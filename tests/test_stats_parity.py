"""stats.py ↔ calc.js 결과 일치(parity) 교차 검증.

기존 tests/calc.test.js 는 calc.js 만 실행하고 기대값을 하드코딩해 두었기 때문에,
백엔드와 정의가 갈려도(승률 분모, 손익비 정의불가 구간, monthly 길이) 통과했다.
여기서는 같은 픽스처를 양쪽 엔진에 실제로 통과시켜 값을 직접 비교한다.

node 가 없는 환경에서는 skip 한다.
"""
import os
import sys
import json
import shutil
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, ROOT)

import stats  # noqa: E402

FIXTURE_PATH = os.path.join(ROOT, 'tests', 'fixtures', 'parity_fixtures.json')

with open(FIXTURE_PATH, encoding='utf-8') as f:
    FIXTURES = json.load(f)

# 양쪽이 모두 산출하며 정의가 일치해야 하는 지표
SCALAR_KEYS = [
    'totalRealized', 'totalDividend', 'totalPnl',
    'buyCount', 'sellCount', 'dividendCount',
    'winCount', 'lossCount', 'winRate',
    'avgWin', 'avgLoss', 'profitFactor', 'avgHoldingDays',
    'maxDrawdown', 'maxSingleWin', 'maxSingleLoss',
    'totalBuyAmount', 'totalSellAmount',
]

_NODE = shutil.which('node')
pytestmark = pytest.mark.skipif(_NODE is None, reason="node 실행 파일이 없어 parity 검증을 건너뜁니다")

_RUNNER = r"""
const { computeTradeStats } = require(process.argv[1]);
const fixtures = require(process.argv[2]);
const out = {};
for (const [name, rows] of Object.entries(fixtures)) out[name] = computeTradeStats(rows);
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope='module')
def js_results():
    proc = subprocess.run(
        [_NODE, '-e', _RUNNER, os.path.join(ROOT, 'calc.js'), FIXTURE_PATH],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"calc.js 실행 실패: {proc.stderr}"
    return json.loads(proc.stdout)


def _close(a, b):
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) < 1e-6


@pytest.mark.parametrize('name', sorted(FIXTURES))
@pytest.mark.parametrize('key', SCALAR_KEYS)
def test_scalar_parity(js_results, name, key):
    py = stats.compute_trade_stats(FIXTURES[name])
    js = js_results[name]
    assert key in py, f"stats.py 에 {key} 가 없습니다"
    assert key in js, f"calc.js 에 {key} 가 없습니다"
    assert _close(py[key], js[key]), (
        f"[{name}] {key} 불일치 — stats.py={py[key]!r} / calc.js={js[key]!r}")


@pytest.mark.parametrize('name', sorted(FIXTURES))
def test_monthly_parity(js_results, name):
    py = stats.compute_trade_stats(FIXTURES[name])['monthly']
    js = js_results[name]['monthly']
    assert [m['month'] for m in py] == [m['month'] for m in js], f"[{name}] monthly 구간 불일치"
    for pm, jm in zip(py, js):
        for k in ('realized', 'dividend', 'buyAmount', 'sellAmount'):
            assert _close(pm[k], jm[k]), f"[{name}] monthly[{pm['month']}].{k} 불일치"


@pytest.mark.parametrize('name', sorted(FIXTURES))
def test_per_stock_parity(js_results, name):
    py = stats.compute_trade_stats(FIXTURES[name])['perStock']
    js = js_results[name]['perStock']
    assert [p['stock'] for p in py] == [j['stock'] for j in js], f"[{name}] perStock 종목/정렬 불일치"
    for pp, jj in zip(py, js):
        assert pp['stockCode'] == jj['stockCode'], f"[{name}] perStock 종목코드 불일치"
        for k in ('realized', 'dividend', 'total', 'sellCount', 'winCount', 'lossCount', 'winRate'):
            assert _close(pp[k], jj[k]), f"[{name}] perStock[{pp['stock']}].{k} 불일치"


# ── 회귀 방지: 이번에 고친 정의를 명시적으로 못 박는다 ──────────────
def test_win_rate_excludes_break_even_sells():
    """손익 0 매도는 승률 분모에서 빠진다. (예전엔 승/전체매도건수 라 '50% (1승 0패)')"""
    s = stats.compute_trade_stats(FIXTURES['손익0_매도_포함'])
    assert s['winCount'] == 1 and s['lossCount'] == 0
    assert s['sellCount'] == 2          # 매도 건수 자체는 2건
    assert s['winRate'] == 100.0        # 승부가 난 건 1건뿐 → 100%


def test_profit_factor_is_none_when_no_loss():
    """손실이 없으면 손익비는 정의할 수 없다. 금액을 대신 돌려주지 않는다."""
    s = stats.compute_trade_stats(FIXTURES['손실0건_손익비_정의불가'])
    assert s['profitFactor'] is None


def test_fractional_full_sell_leaves_no_residual():
    """소수점 전량 매도 후 잔여 수량이 평단을 오염시키지 않는다."""
    s = stats.compute_trade_stats(FIXTURES['소수점_전량매도_잔여없음'])
    # 0.3 을 평단 100 에 사서 120 에 전량 매도 → 6, 이어서 1주를 200 에 사서 210 에 매도 → 10
    assert abs(s['totalRealized'] - 16.0) < 1e-6


# ── 종목 동일성: 코드 우선, 코드 없으면 이름 ──────────────────────
def test_same_code_different_names_are_one_stock():
    """표기가 갈려도 종목코드가 같으면 한 종목으로 묶인다."""
    s = stats.compute_trade_stats(FIXTURES['같은코드_다른표기'])
    assert len(s['perStock']) == 1
    row = s['perStock'][0]
    assert row['stockCode'] == '005930'
    assert row['sellCount'] == 1
    # 평단 150 에 20주를 250 에 매도 → 2,000
    assert abs(row['realized'] - 2000.0) < 1e-6
    # 표시 이름은 가장 최근 기록의 표기를 쓴다
    assert row['stock'] == '삼성전자'


def test_same_name_different_codes_are_separate_stocks():
    """이름이 같아도 종목코드가 다르면 서로 다른 종목이다."""
    s = stats.compute_trade_stats(FIXTURES['같은이름_다른코드'])
    assert len(s['perStock']) == 2
    codes = sorted(r['stockCode'] for r in s['perStock'])
    assert codes == ['316140', '999999']
    # 한쪽은 +500, 다른 쪽은 -500 → 합계 0, 1승 1패
    assert abs(s['totalRealized']) < 1e-6
    assert (s['winCount'], s['lossCount']) == (1, 1)


def test_legacy_rows_without_code_group_by_name():
    """코드가 비어 있는 레거시 기록은 종전대로 이름으로 묶인다."""
    s = stats.compute_trade_stats(FIXTURES['코드없는_레거시'])
    assert len(s['perStock']) == 1
    assert s['perStock'][0]['stock'] == '옛날종목'
    assert abs(s['perStock'][0]['realized'] - 300.0) < 1e-6


def test_stock_code_matching_is_case_insensitive():
    """영문 혼합 코드(0162Z0 등)의 대소문자 차이는 같은 종목이다."""
    s = stats.compute_trade_stats(FIXTURES['코드_대소문자'])
    assert len(s['perStock']) == 1
    assert s['perStock'][0]['stockCode'] == '0162Z0'


def test_stock_identity_prefers_code():
    assert stats.stock_identity({'stockCode': ' 005930 ', 'stockName': '삼성전자'}) == '005930'
    assert stats.stock_identity({'stockCode': '', 'stockName': ' 옛날종목 '}) == '옛날종목'
    assert stats.stock_identity({'stockName': '옛날종목'}) == '옛날종목'
    assert stats.stock_identity({'stockCode': '0162z0', 'stockName': 'x'}) == '0162Z0'
