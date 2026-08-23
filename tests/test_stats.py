"""stats.py — 성과 지표 계산의 회귀 고정.

⭐️ 예전 이름은 test_stats_parity.py 였고, 같은 픽스처를 stats.py 와 calc.js 양쪽에
   통과시켜 값을 비교했다. calc.js 의 computeTradeStats 는 앱에서 호출되지 않는
   사본이었으므로 삭제했고(성과 지표의 정본은 stats.py 하나), 그 자리를 **골든
   스냅샷**이 대신한다.

   fixtures/stats_expected.json 은 parity 가 통과하던 시점의 stats.py 출력이다.
   즉 예전 calc.js 와 값이 일치함이 확인된 결과이며, 앞으로 어떤 지표든 값이
   바뀌면 여기서 걸린다. 계산식을 의도적으로 고쳤다면 스냅샷을 함께 갱신한다:

       python -c "import json,sys; sys.path.insert(0,'.'); import stats; \
         fx=json.load(open('tests/fixtures/parity_fixtures.json',encoding='utf-8')); \
         json.dump({k:stats.compute_trade_stats(v) for k,v in fx.items()}, \
           open('tests/fixtures/stats_expected.json','w',encoding='utf-8'), \
           ensure_ascii=False, indent=2, sort_keys=True)"

   그 아래 명시적 테스트들은 '왜 그 값이어야 하는가'를 문장으로 남긴 것이다.
   스냅샷만 있으면 잘못된 값도 그대로 굳으므로 둘 다 둔다.
"""
import os
import sys
import json

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, ROOT)

import stats  # noqa: E402

FIXTURE_PATH = os.path.join(ROOT, 'tests', 'fixtures', 'parity_fixtures.json')
EXPECTED_PATH = os.path.join(ROOT, 'tests', 'fixtures', 'stats_expected.json')

with open(FIXTURE_PATH, encoding='utf-8') as f:
    FIXTURES = json.load(f)
with open(EXPECTED_PATH, encoding='utf-8') as f:
    EXPECTED = json.load(f)

# 스냅샷으로 고정하는 스칼라 지표
SCALAR_KEYS = [
    'totalRealized', 'totalDividend', 'totalPnl',
    'buyCount', 'sellCount', 'dividendCount',
    'winCount', 'lossCount', 'winRate',
    'avgWin', 'avgLoss', 'profitFactor', 'avgHoldingDays',
    'maxDrawdown', 'maxSingleWin', 'maxSingleLoss',
    'totalBuyAmount', 'totalSellAmount',
]


def _close(a, b):
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) < 1e-6


@pytest.mark.parametrize('name', sorted(FIXTURES))
@pytest.mark.parametrize('key', SCALAR_KEYS)
def test_scalar_matches_snapshot(name, key):
    got = stats.compute_trade_stats(FIXTURES[name])
    assert key in got, f"stats.py 에 {key} 가 없습니다"
    assert _close(got[key], EXPECTED[name][key]), (
        f"[{name}] {key} 가 스냅샷과 다릅니다 — 지금={got[key]!r} / 스냅샷={EXPECTED[name][key]!r}")


@pytest.mark.parametrize('name', sorted(FIXTURES))
def test_monthly_matches_snapshot(name):
    got = stats.compute_trade_stats(FIXTURES[name])['monthly']
    want = EXPECTED[name]['monthly']
    assert [m['month'] for m in got] == [m['month'] for m in want], f"[{name}] monthly 구간 불일치"
    for gm, wm in zip(got, want, strict=True):
        for k in ('realized', 'dividend', 'buyAmount', 'sellAmount'):
            assert _close(gm[k], wm[k]), f"[{name}] monthly[{gm['month']}].{k} 불일치"


@pytest.mark.parametrize('name', sorted(FIXTURES))
def test_per_stock_matches_snapshot(name):
    got = stats.compute_trade_stats(FIXTURES[name])['perStock']
    want = EXPECTED[name]['perStock']
    assert [p['stock'] for p in got] == [p['stock'] for p in want], f"[{name}] perStock 종목/정렬 불일치"
    for gp, wp in zip(got, want, strict=True):
        assert gp['stockCode'] == wp['stockCode'], f"[{name}] perStock 종목코드 불일치"
        for k in ('realized', 'dividend', 'total', 'sellCount', 'winCount', 'lossCount', 'winRate'):
            assert _close(gp[k], wp[k]), f"[{name}] perStock[{gp['stock']}].{k} 불일치"


# ── 정의를 문장으로 못 박는다 (스냅샷만으로는 "왜"가 남지 않는다) ──
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
