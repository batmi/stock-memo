"""stats.py — 성과 지표 계산의 회귀 고정.

⭐️ 예전 이름은 test_stats_parity.py 였고, 같은 픽스처를 stats.py 와 calc.js 양쪽에
   통과시켜 값을 비교했다. calc.js 의 computeTradeStats 는 앱에서 호출되지 않는
   사본이었으므로 삭제했고(성과 지표의 정본은 stats.py 하나), 그 자리를 **골든
   스냅샷**이 대신한다.

   fixtures/stats_expected.json 은 parity 가 통과하던 시점의 stats.py 출력이다.
   즉 예전 calc.js 와 값이 일치함이 확인된 결과이며, 앞으로 어떤 지표든 값이
   바뀌면 여기서 걸린다. 계산식을 의도적으로 고쳤다면 스냅샷을 함께 갱신한다:

       python -c "import json,sys; sys.path.insert(0,'.'); \
         from app.services import stats; \
         fx=json.load(open('tests/fixtures/parity_fixtures.json',encoding='utf-8')); \
         json.dump({k:stats.compute_trade_stats(v) for k,v in fx.items()}, \
           open('tests/fixtures/stats_expected.json','w',encoding='utf-8'), \
           ensure_ascii=False, indent=2, sort_keys=True)"

   (모듈이 app/services/ 로 옮겨졌으므로 `import stats` 로는 더 이상 돌지 않는다.)

   그 아래 명시적 테스트들은 '왜 그 값이어야 하는가'를 문장으로 남긴 것이다.
   스냅샷만 있으면 잘못된 값도 그대로 굳으므로 둘 다 둔다.
"""
import os
import sys
import json

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, ROOT)

from app.services import stats  # noqa: E402

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
    'realizedReturnRate', 'avgReturnRate', 'payoffRatio', 'expectancy',
    'avgHoldingDaysWin', 'avgHoldingDaysLoss', 'maxWinStreak', 'maxLossStreak',
    'maxDrawdownPeakDate', 'maxDrawdownTroughDate',
    'totalFee', 'totalTax', 'totalCost', 'costRecordCount', 'netPnl',
]


def _close(a, b):
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, str) or isinstance(b, str):
        return a == b
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
        for k in ('realized', 'dividend', 'buyAmount', 'sellAmount', 'sellCount', 'winRate'):
            assert _close(gm[k], wm[k]), f"[{name}] monthly[{gm['month']}].{k} 불일치"


@pytest.mark.parametrize('name', sorted(FIXTURES))
def test_per_stock_matches_snapshot(name):
    got = stats.compute_trade_stats(FIXTURES[name])['perStock']
    want = EXPECTED[name]['perStock']
    assert [p['stock'] for p in got] == [p['stock'] for p in want], f"[{name}] perStock 종목/정렬 불일치"
    for gp, wp in zip(got, want, strict=True):
        assert gp['stockCode'] == wp['stockCode'], f"[{name}] perStock 종목코드 불일치"
        for k in ('realized', 'dividend', 'total', 'sellCount', 'winCount', 'lossCount', 'winRate',
                  'returnRate', 'avgHoldingDays'):
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


def test_same_timestamp_trades_are_ordered_by_id():
    """같은 시각의 체결은 id 가 큰 쪽(나중에 들어온 쪽)이 뒤에 온다.

    ⭐️ 예전에는 정렬 키가 시각 하나뿐이었다. 파이썬 정렬이 안정적이라 동점의 순서가
       'SQL 이 돌려준 순서'(api.py 의 조회에는 ORDER BY 가 없다)로 결정됐는데, 그
       순서가 표시 이름(가장 최근 표기가 이긴다)을 좌우한다. 화면(14-history.js)은
       동점을 id 로 가르므로, 백엔드가 DB 반환 순서에 기대면 같은 종목을 화면과
       백엔드가 서로 다른 이름으로 부를 수 있다.
    """
    same_time = '2026-08-01T10:00'
    rows = [
        {'id': 1, 'type': 'trade', 'tradeType': '매수', 'stockName': '옛표기',
         'stockCode': '005930', 'price': 100, 'quantity': 2, 'rawDate': '2026-08-01T09:00'},
        # ↓ 시각이 같은 두 매도. id 가 큰 쪽의 표기가 이겨야 한다.
        {'id': 3, 'type': 'trade', 'tradeType': '매도', 'stockName': '나중표기',
         'stockCode': '005930', 'price': 120, 'quantity': 1, 'rawDate': same_time},
        {'id': 2, 'type': 'trade', 'tradeType': '매도', 'stockName': '먼저표기',
         'stockCode': '005930', 'price': 120, 'quantity': 1, 'rawDate': same_time},
    ]
    # DB 반환 순서가 어떻든 결과가 같아야 한다.
    for ordered in (rows, list(reversed(rows))):
        result = stats.compute_trade_stats(ordered)
        assert len(result['perStock']) == 1
        assert result['perStock'][0]['stock'] == '나중표기'


# ── 분석 화면 확장 지표 (수익률·기대값·손익비·보유 습관·연승연패·비용·낙폭 곡선) ──
def _trade(entry_id, trade_type, price, qty, raw_date, code='A', **extra):
    return {'id': entry_id, 'type': 'trade', 'tradeType': trade_type, 'stockName': code,
            'stockCode': code, 'price': price, 'quantity': qty, 'rawDate': raw_date, **extra}


def test_payoff_ratio_differs_from_profit_factor():
    """손익비는 평균수익/평균손실, Profit Factor 는 총이익/총손실이다. 건수가 다르면 값이 갈린다."""
    rows = [
        _trade(1, '매수', 100, 3, '2026-01-01T09:00'),
        _trade(2, '매도', 110, 1, '2026-01-02T09:00'),   # +10
        _trade(3, '매도', 110, 1, '2026-01-03T09:00'),   # +10
        _trade(4, '매도', 85, 1, '2026-01-04T09:00'),    # -15
    ]
    s = stats.compute_trade_stats(rows)
    assert s['profitFactor'] == pytest.approx(20 / 15)
    assert s['payoffRatio'] == pytest.approx(10 / 15)
    assert s['expectancy'] == pytest.approx(5 / 3)       # 실현 5 ÷ 매도 3건


def test_payoff_ratio_and_expectancy_are_none_when_undefined():
    s = stats.compute_trade_stats([_trade(1, '매수', 100, 1, '2026-01-01T09:00')])
    assert s['payoffRatio'] is None and s['expectancy'] is None
    assert s['realizedReturnRate'] is None and s['avgReturnRate'] is None


def test_return_rate_uses_cost_basis_and_skips_sells_without_buys():
    """수익률은 매도분 평단 원금 대비. 매수 기록 없는 매도(평단 0)는 수익률에서 뺀다."""
    rows = [
        _trade(1, '매수', 100, 10, '2026-01-01T09:00'),
        _trade(2, '매도', 120, 5, '2026-01-02T09:00'),               # +100 / 원금 500 → +20%
        _trade(3, '매도', 90, 5, '2026-01-03T09:00'),                # -50 / 원금 500 → -10%
        _trade(4, '매도', 1000, 1, '2026-01-04T09:00', code='B'),    # 매수 기록 없음
    ]
    s = stats.compute_trade_stats(rows)
    assert s['totalRealized'] == pytest.approx(1050)                # 금액 집계는 종전대로 포함
    assert s['realizedReturnRate'] == pytest.approx(50 / 1000 * 100)
    assert s['avgReturnRate'] == pytest.approx((20 - 10) / 2)
    by_code = {p['stockCode']: p for p in s['perStock']}
    assert by_code['A']['returnRate'] == pytest.approx(5.0)
    assert by_code['B']['returnRate'] is None


def test_holding_days_split_by_outcome_and_bucketed():
    rows = [
        _trade(1, '매수', 100, 1, '2026-01-01T09:00', code='W'),
        _trade(2, '매도', 110, 1, '2026-01-01T15:00', code='W'),    # 0.25일, 이익
        _trade(3, '매수', 100, 1, '2026-01-01T09:00', code='L'),
        _trade(4, '매도', 90, 1, '2026-01-21T09:00', code='L'),     # 20일, 손실
    ]
    s = stats.compute_trade_stats(rows)
    assert s['avgHoldingDaysWin'] == pytest.approx(0.25)
    assert s['avgHoldingDaysLoss'] == pytest.approx(20)
    assert [(b['label'], b['sellCount'], b['winRate']) for b in s['holdingBuckets']] == [
        ('1일 미만', 1, 100.0), ('1개월 이내', 1, 0.0)]
    by_code = {p['stockCode']: p for p in s['perStock']}
    assert by_code['L']['avgHoldingDays'] == pytest.approx(20)


def test_holding_bucket_boundaries():
    assert [stats._holding_bucket(d) for d in (0, 0.99, 1, 7, 7.01, 30, 90, 90.5)] == [0, 0, 1, 1, 2, 2, 3, 4]


def test_streaks_ignore_break_even_sells():
    """손익 0 매도는 승률처럼 승도 패도 아니므로 연승을 끊지 않는다."""
    prices = [110, 100, 110, 90, 80, 120]   # 승, 0, 승, 패, 패, 승
    rows = [_trade(1, '매수', 100, len(prices), '2026-01-01T09:00')]
    rows += [_trade(10 + i, '매도', p, 1, f'2026-01-{i + 2:02d}T09:00') for i, p in enumerate(prices)]
    s = stats.compute_trade_stats(rows)
    assert (s['maxWinStreak'], s['maxLossStreak']) == (2, 2)


def test_fee_and_tax_count_only_recorded_rows():
    rows = [
        _trade(1, '매수', 100, 10, '2026-01-01T09:00', fee=15),
        _trade(2, '매도', 120, 10, '2026-01-02T09:00', fee=18, tax=30),
        _trade(3, '매수', 100, 1, '2026-01-03T09:00', code='B'),      # 수기 기록: 비용 없음
    ]
    s = stats.compute_trade_stats(rows)
    assert (s['totalFee'], s['totalTax'], s['totalCost']) == (33, 30, 63)
    assert s['costRecordCount'] == 2
    assert s['netPnl'] == pytest.approx(200 - 63)
    none = stats.compute_trade_stats(rows[2:])
    assert none['costRecordCount'] == 0 and none['netPnl'] == none['totalPnl']


def test_canceled_order_fee_is_not_counted():
    rows = [_trade(1, '매수', 100, 1, '2026-01-01T09:00', fee=5, tradeStatus='CANCELED')]
    assert stats.compute_trade_stats(rows)['costRecordCount'] == 0


def test_equity_curve_and_drawdown_dates():
    rows = [
        _trade(1, '매수', 100, 3, '2026-01-01T09:00'),
        _trade(2, '매도', 200, 1, '2026-01-02T09:00'),   # +100 → 고점
        _trade(3, '매도', 50, 1, '2026-01-05T09:00'),    # -50 → 50
        _trade(4, '매도', 70, 1, '2026-01-05T10:00'),    # -30 → 20 (같은 날, 저점)
    ]
    s = stats.compute_trade_stats(rows)
    assert s['equityCurve'] == [{'date': '2026-01-02', 'value': 100}, {'date': '2026-01-05', 'value': 20}]
    assert s['maxDrawdown'] == pytest.approx(80)
    assert (s['maxDrawdownPeakDate'], s['maxDrawdownTroughDate']) == ('2026-01-02', '2026-01-05')


def test_drawdown_from_start_has_no_peak_date():
    rows = [_trade(1, '매수', 100, 1, '2026-01-01T09:00'), _trade(2, '매도', 90, 1, '2026-01-02T09:00')]
    s = stats.compute_trade_stats(rows)
    assert s['maxDrawdownPeakDate'] is None and s['maxDrawdownTroughDate'] == '2026-01-02'


def test_period_rows_have_sell_count_and_win_rate():
    rows = [
        _trade(1, '매수', 100, 3, '2026-01-01T09:00'),
        _trade(2, '매도', 110, 1, '2026-01-02T09:00'),
        _trade(3, '매도', 90, 1, '2026-01-03T09:00'),
        _trade(4, '매도', 120, 1, '2026-02-03T09:00'),
        _trade(5, '매수', 100, 1, '2026-03-03T09:00'),
    ]
    got = {m['month']: (m['sellCount'], m['winRate']) for m in stats.compute_trade_stats(rows)['monthly']}
    assert got == {'2026-01': (2, 50.0), '2026-02': (1, 100.0), '2026-03': (0, None)}


@pytest.mark.parametrize('name', sorted(FIXTURES))
def test_curve_and_buckets_match_snapshot(name):
    got = stats.compute_trade_stats(FIXTURES[name])
    want = EXPECTED[name]
    assert [p['date'] for p in got['equityCurve']] == [p['date'] for p in want['equityCurve']]
    for gp, wp in zip(got['equityCurve'], want['equityCurve'], strict=True):
        assert _close(gp['value'], wp['value']), f"[{name}] equityCurve[{gp['date']}] 불일치"
    assert [b['label'] for b in got['holdingBuckets']] == [b['label'] for b in want['holdingBuckets']]
    for gb, wb in zip(got['holdingBuckets'], want['holdingBuckets'], strict=True):
        for k in ('sellCount', 'winRate', 'realized', 'returnRate'):
            assert _close(gb[k], wb[k]), f"[{name}] holdingBuckets[{gb['label']}].{k} 불일치"
