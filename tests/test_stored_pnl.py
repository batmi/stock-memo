"""체결에 저장된 확정 손익을 모든 조회 화면에서 일관되게 집계한다."""

import io

import pytest

from app.domain.trades import apply_trade, empty_holding
from app.database.db import db_conn
from app.services import entries, realized_pnl
from helpers import _buy, _sell, _login
from test_journal_api import seed
from test_trading_api import api as api, _trade


@pytest.mark.parametrize('field', ['realizedPnl', 'realizedPnlRate', 'fee', 'tax', 'strategyScore', 'stopLossRate'])
@pytest.mark.parametrize('bad', ['not-a-number', True, 'NaN', 'Infinity', [], {}])
def test_web_rejects_invalid_optional_numbers_without_poisoning_reads(client, field, bad):
    _login(client)
    entries.create('trader', _buy(id=1, qty=2, price=100, rawDate='2026-01-05T09:00'))
    response = client.post('/api/entry', json=_sell(
        id=2, qty=1, price=150, rawDate='2026-01-06T09:00', **{field: bad}))
    assert response.status_code == 400
    assert field in response.json['error']
    assert client.get('/api/entry/2').status_code == 404
    assert client.get('/api/journal/portfolio').status_code == 200
    assert client.get('/api/journal/stats').status_code == 200


@pytest.mark.parametrize('stored,expected', [('0', 0), ('-7.5', -7.5), (None, 50), ('', 50)])
def test_web_normalizes_optional_pnl_and_owns_its_source(client, stored, expected):
    _login(client)
    entries.create('trader', _buy(id=1, qty=2, price=100, rawDate='2026-01-05T09:00'))
    response = client.post('/api/entry', json=_sell(id=2, qty=1, price=150, realizedPnl=stored,
        realizedPnlSource='reconstructed', fee='0', tax=None, rawDate='2026-01-06T09:00'))
    assert response.status_code == 200
    sale = client.get('/api/entry/2').json
    assert sale['realizedPnl'] == expected
    assert sale['realizedPnlSource'] == ('recorded' if stored not in (None, '') else 'reconstructed')
    assert (sale['fee'], sale['tax']) == (0, None)


@pytest.mark.parametrize('field,value', [('fee', -1), ('tax', -1), ('realizedPnlSource', []),
                                        ('realizedPnlSource', 'forged')])
def test_web_rejects_negative_costs_and_invalid_pnl_source(client, field, value):
    _login(client)
    assert client.post('/api/entry', json=_buy(**{field: value})).status_code == 400


@pytest.mark.parametrize('stored', [0, -35, 47.5])
def test_recorded_profit_overrides_calculation_without_changing_remaining_cost(stored):
    holding = empty_holding()
    apply_trade(holding, {'tradeType': '매수', 'quantity': 10, 'price': 100})
    profit, cost, dividend = apply_trade(holding, {
        'tradeType': '매도', 'quantity': 2, 'price': 150, 'realizedPnl': stored})
    assert (profit, cost, dividend) == (stored, 200, 0)
    assert holding == {'qty': 8, 'totalCost': 800, 'avgPrice': 100}


def test_stored_pnl_reaches_portfolio_calendar_charts_and_analysis(client):
    _login(client, 'reader')
    seed(id=1, quantity=10, price=100, rawDate='2026-01-05T09:00')
    seed(id=2, tradeType='매도', quantity=2, price=150, realizedPnl=-35,
         source='broker', rawDate='2026-01-06T09:00')
    seed(id=3, tradeType='매도', quantity=2, price=170, realizedPnl=0,
         rawDate='2026-01-07T09:00')
    seed(id=4, tradeType='배당', quantity=1, price=5, rawDate='2026-01-08T09:00')
    # 같은 종목의 나중 매수는 이전 체결의 저장 손익을 바꾸지 않는다.
    seed(id=5, quantity=2, price=200, rawDate='2026-02-01T09:00')
    portfolio = client.get('/api/journal/portfolio').json
    assert portfolio['totalRealizedProfit'] == -30
    assert portfolio['holdings']['005930']['realizedProfit'] == -30
    calendar = client.get('/api/journal/calendar?month=2026-01').json
    assert calendar['2026-01-06']['profit'] == -35
    assert calendar['2026-01-07']['profit'] == 0
    chart = client.get('/api/journal/chart?period=2026-01').json
    assert chart['monthlyData']['2026-01']['realized'] == -35
    assert chart['monthlyData']['2026-01']['cumulative'] == -30
    stats = client.get('/api/journal/stats').json
    assert (stats['totalRealized'], stats['totalPnl'], stats['winCount'], stats['lossCount']) == (-35, -30, 0, 1)
    assert stats['maxDrawdown'] == 35
    assert stats['monthly'][0]['realized'] == -35
    assert stats['perStock'][0]['realized'] == -35
    details = client.get('/api/journal/stats/trades?metric=sell').json
    assert {f['id']: f['realized'] for f in details['items']} == {2: -35, 3: 0}
    entries.patch_bot_trade('reader', 2, {'realizedPnl': 25})
    assert client.get('/api/journal/stats').json['totalRealized'] == 25
    assert client.get('/api/journal/chart?period=2026-01').json['monthlyData']['2026-01']['realized'] == 25


def stored_rows():
    with db_conn() as conn:
        return {r['id']: (r['realizedPnl'], r['realizedPnlSource']) for r in conn.execute(
            'SELECT id,realizedPnl,realizedPnlSource FROM entries')}


def test_backfill_preserves_recorded_zero_and_never_uses_later_purchase(client):
    _login(client, 'reader')
    seed(id=1, quantity=10, price=100, rawDate='2026-01-05T09:00')
    seed(id=2, tradeType='매도', quantity=2, price=150, rawDate='2026-01-06T09:00')
    seed(id=3, tradeType='매도', quantity=2, price=150, realizedPnl=0, rawDate='2026-01-07T09:00')
    seed(id=4, quantity=10, price=1000, rawDate='2026-01-08T09:00')
    assert realized_pnl.backfill() == 1
    assert stored_rows()[2] == (100, 'reconstructed')
    assert stored_rows()[3] == (0, 'recorded')
    assert realized_pnl.backfill() == 0
    assert client.get('/api/journal/stats').json['totalRealized'] == 100


def test_write_correct_delete_and_external_reconciliation_update_only_reconstructed_pnl(client):
    _login(client, 'reader')
    buy = _buy(id=1, qty=10, price=100, rawDate='2026-01-05T09:00')
    entries.create('reader', buy)
    entries.create('reader', _sell(id=2, qty=2, price=150, rawDate='2026-01-06T09:00'))
    entries.create('reader', _sell(id=3, qty=2, price=150, realizedPnl=7, source='broker',
                                   rawDate='2026-01-07T09:00'))
    assert stored_rows()[2] == (100, 'reconstructed')
    entries.update('reader', 1, dict(buy, price=120))
    assert stored_rows()[2] == (60, 'reconstructed')
    assert stored_rows()[3] == (7, 'recorded')
    # 실제 금액을 받으면 재구성 값을 대체하고, 이후 매수가 바뀌어도 확정 금액을 유지한다.
    entries.patch_bot_trade('reader', 3, {'realizedPnl': 0})
    entries.update('reader', 1, dict(buy, price=130))
    assert stored_rows()[2] == (40, 'reconstructed')
    assert stored_rows()[3] == (0, 'recorded')
    entries.delete('reader', 1, force=True)
    assert stored_rows()[2] == (None, 'unavailable')
    assert stored_rows()[3] == (0, 'recorded')
    stats = client.get('/api/journal/stats').json
    assert stats['totalRealized'] == 0
    assert stats['quality']['missingRealizedCount'] == 1
    facts = client.get('/api/journal/stats/trades?metric=sell').json['items']
    assert {f['id']: f['realizedSource'] for f in facts} == {2: 'unavailable', 3: 'recorded'}


def test_reconstruction_separates_accounts_currencies_and_inactive_orders(client):
    _login(client, 'reader')
    seed(id=1, quantity=10, price=100, subAccount='A', rawDate='2026-01-05T09:00')
    seed(id=2, quantity=10, price=200, subAccount='B', rawDate='2026-01-05T09:00')
    seed(id=3, quantity=10, price=10, subAccount='A', currency='USD', rawDate='2026-01-05T09:00')
    seed(id=4, quantity=10, price=1, subAccount='A', tradeStatus='CANCELED', rawDate='2026-01-05T10:00')
    seed(id=5, tradeType='매도', quantity=2, price=150, subAccount='A', rawDate='2026-01-06T09:00')
    seed(id=6, tradeType='매도', quantity=2, price=150, subAccount='B', rawDate='2026-01-06T09:00')
    seed(id=7, tradeType='매도', quantity=2, price=15, subAccount='A', currency='USD', rawDate='2026-01-06T09:00')
    realized_pnl.backfill()
    rows = stored_rows()
    assert [rows[i] for i in (5, 6, 7)] == [(100, 'reconstructed'), (-100, 'reconstructed'), (10, 'reconstructed')]


def test_late_purchase_reconstructs_unavailable_sell_without_fabricating_original_pnl(client):
    _login(client, 'reader')
    seed(id=2, tradeType='매도', quantity=2, price=150, source='broker', rawDate='2026-01-06T09:00')
    realized_pnl.backfill()
    assert stored_rows()[2] == (None, 'unavailable')
    stats = client.get('/api/journal/stats').json
    assert stats['totalRealized'] == 0
    assert stats['expectancy'] is None and stats['realizedReturnRate'] is None
    assert stats['distribution']['sellCount'] == 0
    assert stats['equityCurve'] == []
    entries.create('reader', _buy(id=1, qty=2, price=100, rawDate='2026-01-05T09:00'))
    assert stored_rows()[2] == (100, 'reconstructed')
    entries.patch_bot_trade('reader', 2, {'tradeStatus': 'CANCELED'})
    assert stored_rows()[2] == (None, None)
    assert client.get('/api/journal/stats').json['sellCount'] == 0


def test_bot_batch_reconstructs_after_all_fills_and_patch_replaces_estimate(api):
    client, headers = api['client'], api['headers']
    sell = _trade(side='SELL', price=120, volume=1, brokerExecutionId='sell',
                  executedAt='2026-08-02T09:30:00+09:00')
    buy = _trade(price=100, volume=2, brokerExecutionId='buy')
    response = client.post('/api/v1/trades/batch', headers=headers, json=[sell, buy])
    assert response.status_code == 201 and response.json['inserted'] == 2
    sell_id = int(response.json['results'][0]['id'])
    assert stored_rows()[sell_id] == (20, 'reconstructed')
    patched = client.patch(f'/api/v1/trades/{sell_id}', headers=headers, json={'realizedPnl': -7})
    assert patched.status_code == 200
    assert stored_rows()[sell_id] == (-7, 'recorded')
    buy_id = response.json['results'][1]['id']
    assert client.patch(f'/api/v1/trades/{buy_id}', headers=headers, json={'price': 110}).status_code == 200
    assert stored_rows()[sell_id] == (-7, 'recorded')
    duplicate = client.post('/api/v1/trades', headers=headers, json=sell)
    assert duplicate.status_code == 200
    assert duplicate.json['realizedPnl'] == -7 and duplicate.json['realizedPnlSource'] == 'recorded'


def test_backup_restore_preserves_pnl_provenance(client):
    _login(client, 'reader')
    buy = _buy(id=1, qty=4, price=100, rawDate='2026-01-05T09:00')
    entries.create('reader', buy)
    entries.create('reader', _sell(id=2, qty=1, price=120, rawDate='2026-01-06T09:00'))
    entries.create('reader', _sell(id=3, qty=1, price=120, realizedPnl=7, rawDate='2026-01-07T09:00'))
    backup = client.get('/api/backup')
    assert backup.status_code == 200
    entries.update('reader', 1, dict(buy, price=110))
    assert stored_rows()[2] == (10, 'reconstructed')
    result = client.post('/api/restore', data={'file': (io.BytesIO(backup.data), 'backup.zip')},
                         content_type='multipart/form-data')
    assert result.status_code == 200
    assert stored_rows()[2] == (20, 'reconstructed')
    assert stored_rows()[3] == (7, 'recorded')
