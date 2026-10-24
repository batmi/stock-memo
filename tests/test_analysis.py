"""분석 기간·통화·원가와 근거 거래가 같은 계산 결과를 사용하는지 검증한다."""

import pytest

from app.services import entries
from app.utils import statscache
from helpers import _buy, _insert_raw, _login, _sell


def get(client, endpoint='stats', **query):
    return client.get('/api/journal/' + endpoint, query_string=query)


def seed(entry_id, side, price, qty, day, **values):
    _insert_raw('trader', id=entry_id, tradeType=side, price=price, quantity=qty,
                rawDate=day + 'T09:00', stockName=values.pop('stockName', '종목'),
                stockCode=values.pop('stockCode', 'A'), **values)


def test_period_comparison_retains_prior_cost_and_uses_equal_days(client):
    _login(client)
    seed(1, '매수', 100, 10, '2026-01-01', tradeClass='장기투자')
    seed(2, '매도', 120, 2, '2026-02-10', tradeClass='단기스윙')
    seed(3, '매도', 150, 3, '2026-03-31', tradeClass='단기스윙')
    seed(4, '매도', 1000, 1, '2026-04-01', tradeClass='단기스윙')
    query = {'from': '2026-03-01', 'to': '2026-03-31', 'tradeClass': '단기스윙'}
    result = get(client, **query).json
    assert (result['totalRealized'], result['buyCount'], result['sellCount']) == (150, 0, 1)
    assert result['realizedReturnRate'] == 50
    previous = result['comparison']
    assert (previous['from'], previous['to']) == ('2026-01-29', '2026-02-28')
    assert previous['metrics']['totalPnl'] == 40
    detail = get(client, 'stats/trades', **query, metric='sell').json
    assert len(detail['items']) == 1
    assert (detail['items'][0]['id'], detail['items'][0]['basis']) == (3, 300)
    assert detail['items'][0]['holdingDays'] == 89


@pytest.mark.parametrize('query', [
    {'from': '2026-01-01'}, {'to': '2026-01-01'},
    {'from': '2026-02-30', 'to': '2026-03-01'},
    {'from': '2026-02-02', 'to': '2026-02-01'},
    {'from': '2026-1-1', 'to': '2026-01-02'},
    {'from': '0001-01-01', 'to': '0001-01-02'},
    {'from': '9999-12-01', 'to': '9999-12-31'},
])
def test_invalid_period_is_rejected_for_summary_and_details(client, query):
    _login(client)
    assert get(client, **query).status_code == 400
    assert get(client, 'stats/trades', **query).status_code == 400


def test_all_period_has_no_comparison_and_empty_previous_ratios_are_undefined(client):
    _login(client)
    seed(1, '매수', 100, 1, '2024-02-29')
    assert get(client).json['comparison'] is None
    result = get(client, **{'from': '2024-02-29', 'to': '2024-03-01'}).json
    previous = result['comparison']
    assert (previous['from'], previous['to'], previous['tradeCount']) == ('2024-02-27', '2024-02-28', 0)
    assert previous['metrics']['expectancy'] is None
    assert previous['metrics']['realizedReturnRate'] is None


def test_groups_share_full_cost_and_separate_accounts_with_same_alias_and_currencies(client):
    _login(client)
    seed(1, '매수', 100, 4, '2026-01-01', subAccount='11-22', accountName='일반', tradeClass='장기투자')
    seed(2, '매도', 130, 2, '2026-01-10', subAccount='1122', accountName='일반', tradeClass='단기스윙')
    seed(3, '매수', 200, 1, '2026-01-01', subAccount='3344', accountName='일반', tradeClass='단기스윙')
    seed(4, '매도', 180, 1, '2026-01-10', subAccount='3344', accountName='일반', tradeClass='단기스윙')
    seed(5, '매수', 10, 1, '2026-01-01', subAccount='US', currency='USD', tradeClass='단기스윙')
    seed(6, '매도', 11, 1, '2026-01-10', subAccount='US', currency='USD', tradeClass='단기스윙')
    result = get(client).json
    assert (result['currency'], result['totalPnl']) == ('KRW', 40)
    accounts = {g['key']: g for g in result['groups']['accounts']}
    assert accounts['1122']['totalPnl'] == 60
    assert accounts['3344']['totalPnl'] == -20
    assert accounts['US']['currency'] == 'USD' and accounts['US']['totalPnl'] == 1
    classes = {(g['currency'], g['key']): g for g in result['groups']['classes']}
    assert classes['KRW', '단기스윙']['returnRate'] == 10
    assert classes['KRW', '단기스윙']['winRate'] == 50
    assert classes['KRW', '단기스윙']['avgHoldingDays'] == 9
    detail = get(client, 'stats/trades', currency='USD', groupBy='accountKey', groupKey='US').json
    assert [f['id'] for f in detail['items']] == [6, 5]
    explicit = get(client, currency='USD').json
    assert {g['currency'] for g in explicit['groups']['classes']} == {'USD'}


def test_quality_reports_partial_costs_and_unmatched_buys_without_counting_inactive_orders(client):
    _login(client)
    seed(1, '매수', 100, 1, '2026-01-01', fee=0, tax=0)
    seed(2, '매도', 120, 2, '2026-01-02', fee=1, needsReview=1)
    seed(3, '매도', 120, 1, '2026-01-03')
    seed(4, '매수', 100, 100, '2026-01-04', tradeStatus='CANCELED', needsReview=1)
    result = get(client).json
    assert result['quality'] == {
        'tradeCount': 3, 'reviewCount': 1, 'missingBuyCount': 2, 'zeroBasisCount': 1,
        'completeCostCount': 1, 'partialCostCount': 1, 'missingCostCount': 1,
        'costCoverage': pytest.approx(100 / 3),
    }
    detail = get(client, 'stats/trades', metric='missing-buy').json['items']
    assert [f['id'] for f in detail] == [3, 2]
    assert detail[1]['missingBuyQuantity'] == 1
    assert detail[1]['basis'] == 200  # 요약과 같은 기존 이동평균 원가 규칙
    assert [f['id'] for f in get(client, 'stats/trades', metric='missing-cost').json['items']] == [3, 2]


def test_distribution_handles_zero_and_profit_concentration(client):
    _login(client)
    seed(1, '매수', 100, 4, '2026-01-01')
    for i, price in enumerate((80, 100, 110, 200), 2):
        seed(i, '매도', price, 1, f'2026-01-{i:02d}')
    result = get(client).json['distribution']
    assert (result['sellCount'], result['median'], result['withoutBestWin']) == (4, 5, -10)
    assert result['top3WinShare'] == 100
    assert sum(b['count'] for b in result['bins']) == 4
    assert [f['id'] for f in get(client, 'stats/trades', metric='best').json['items']] == [5]
    assert [f['id'] for f in get(client, 'stats/trades', metric='worst').json['items']] == [2]
    assert get(client, 'stats/trades', metric='wins').json['total'] == 2


def test_recovery_uses_only_selected_period_and_tracks_later_unrecovered_episode(client):
    _login(client)
    seed(1, '매수', 100, 10, '2026-01-01')
    for i, (day, price) in enumerate(((2, 110), (4, 80), (5, 90), (10, 140), (12, 90)), 2):
        seed(i, '매도', price, 1, f'2026-01-{day:02d}')
    result = get(client, **{'from': '2026-01-01', 'to': '2026-01-30'}).json
    assert result['maxDrawdown'] == 30
    assert result['recovery'] == {
        'asOf': '2026-01-30', 'maxRecoveryDate': '2026-01-10', 'maxRecoveryDays': 5,
        'currentDrawdown': 10, 'currentPeakDate': '2026-01-10', 'currentUnderwaterDays': 20,
    }
    past = get(client, **{'from': '2026-01-01', 'to': '2026-01-07'}).json['recovery']
    assert past['maxRecoveryDate'] is None
    assert (past['currentDrawdown'], past['currentUnderwaterDays']) == (30, 5)


def test_initial_loss_recovers_at_zero_and_equal_profit_bins_work(client):
    _login(client)
    seed(1, '매수', 100, 2, '2026-01-01')
    seed(2, '매도', 90, 1, '2026-01-02')
    seed(3, '매도', 110, 1, '2026-01-04')
    result = get(client).json
    assert result['recovery']['maxRecoveryDays'] == 2
    assert result['recovery']['currentDrawdown'] == 0
    only_loss = get(client, **{'from': '2026-01-02', 'to': '2026-01-02'}).json['distribution']
    assert only_loss['bins'] == [{'from': -10, 'to': -10, 'count': 1}]
    assert only_loss['top3WinShare'] is None
    assert only_loss['withoutBestWin'] == -10


def test_detail_pagination_is_small_and_stale_version_requires_refresh(client):
    _login(client)
    seed(1, '매수', 100, 40, '2026-01-01')
    for i in range(2, 37):
        seed(i, '매도', 101, 1, '2026-01-02', thoughts='긴 본문' * 10000, attachedImage='사진')
    summary = get(client).json
    assert 'items' not in summary and 'thoughts' not in str(summary)
    first = get(client, 'stats/trades', metric='sell', version=summary['version']).json
    second = get(client, 'stats/trades', metric='sell', version=summary['version'], offset=first['nextOffset']).json
    assert (len(first['items']), len(second['items']), second['nextOffset']) == (30, 5, None)
    assert len({f['id'] for f in first['items'] + second['items']}) == 35
    assert all('thoughts' not in f and 'attachedImage' not in f for f in first['items'])
    statscache.invalidate('trader')
    assert get(client, 'stats/trades', version=summary['version']).status_code == 409


def test_detail_is_user_scoped_and_requires_login(client):
    assert get(client, 'stats/trades').status_code == 401
    _login(client)
    entries.create('trader', _buy(id=1, qty=1, price=100, rawDate='2026-01-01'))
    entries.create('trader', _sell(id=2, qty=1, price=200, rawDate='2026-01-02'))
    _login(client, 'another')
    assert get(client, 'stats/trades').json['total'] == 0


def test_detail_period_and_holding_bucket_restrict_same_computed_facts(client):
    _login(client)
    seed(1, '매수', 100, 3, '2026-01-01')
    seed(2, '매도', 120, 1, '2026-01-10')
    seed(3, '매도', 130, 1, '2026-02-02')
    seed(4, '배당', 10, 1, '2026-02-03')
    assert [f['id'] for f in get(client, 'stats/trades', bucket='1개월 이내').json['items']] == [2]
    assert [f['id'] for f in get(client, 'stats/trades', granularity='weekly',
                                periodKey='2026-02-02').json['items']] == [4, 3]
    stock = get(client, 'stats/trades', metric='pnl', identity='A').json['items']
    assert sum(f['realized'] + f['dividend'] for f in stock) == get(client).json['totalPnl']


def test_previous_period_keeps_selected_currency_when_only_other_currency_has_sales(client):
    _login(client)
    seed(1, '매수', 100, 1, '2026-02-01', currency='KRW')
    seed(2, '매도', 200, 1, '2026-02-15', currency='KRW')
    seed(3, '매수', 10, 1, '2026-03-01', currency='USD')
    seed(4, '매도', 11, 1, '2026-03-10', currency='USD')
    result = get(client, currency='USD', **{'from': '2026-03-01', 'to': '2026-03-31'}).json
    assert result['totalPnl'] == 1
    assert result['comparison']['currency'] == 'USD'
    assert result['comparison']['metrics']['totalPnl'] == 0
    assert result['comparison']['tradeCount'] == 0


@pytest.mark.parametrize('query', [{'metric': 'nope'}, {'bucket': 'nope'}, {'limit': '101'},
                                 {'offset': '-1'}, {'offset': 'NaN'}, {'groupBy': 'username'}])
def test_detail_rejects_invalid_filters(client, query):
    _login(client)
    assert get(client, 'stats/trades', **query).status_code == 400
