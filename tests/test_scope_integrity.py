"""계좌별 기초잔고와 필터를 적용한 원가·통화 집계의 회귀 검사."""

import pytest

from app.database.db import db_conn
from app.services import entries
from helpers import _buy, _login, _sell
from test_trading_api import _trade, api as api


def test_opening_balances_are_idempotent_per_account_and_currency(api):
    client, headers = api['client'], api['headers']
    body = {'asOf': '2026-01-01', 'positions': [
        {'symbol': 'AAPL', 'name': 'Apple', 'avgPrice': 100, 'volume': 1, 'subAccount': '11-22', 'currency': 'KRW'},
        {'symbol': 'AAPL', 'name': 'Apple', 'avgPrice': 200, 'volume': 2, 'subAccount': '33-44', 'currency': 'KRW'},
        {'symbol': 'AAPL', 'name': 'Apple', 'avgPrice': 3, 'volume': 3, 'subAccount': '11-22', 'currency': 'USD'},
    ]}
    first = client.post('/api/v1/positions/opening', headers=headers, json=body).json
    assert (first['inserted'], first['skipped']) == (3, 0)
    for position in body['positions']:
        position['subAccount'] = position['subAccount'].replace('-', '')
        position['symbol'] = 'aapl'
        position['currency'] = position['currency'].lower()
    second = client.post('/api/v1/positions/opening', headers=headers, json=body).json
    assert (second['inserted'], second['skipped']) == (0, 3)
    with db_conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM entries').fetchone()[0] == 3


def test_legacy_opening_key_dedupes_only_the_original_account(api):
    client, headers = api['client'], api['headers']
    key = 'OPENING:REAL:2026-01-01:005930'
    original = client.post('/api/v1/trades', headers=headers, json=_trade(
        brokerExecutionId=key, subAccount='11-22', currency='KRW',
        executedAt='2026-01-01T00:00:00+09:00')).json
    body = {'asOf': '2026-01-01', 'positions': [
        {'symbol': '005930', 'avgPrice': 100, 'volume': 1, 'subAccount': '1122'},
        {'symbol': '005930', 'avgPrice': 200, 'volume': 2, 'subAccount': '3344'},
    ]}
    result = client.post('/api/v1/positions/opening', headers=headers, json=body).json
    assert (result['inserted'], result['skipped']) == (1, 1)
    assert result['results'][0]['id'] == original['id']
    retry = client.post('/api/v1/positions/opening', headers=headers, json=body).json
    assert (retry['inserted'], retry['skipped']) == (0, 2)
    with db_conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM entries').fetchone()[0] == 2
        assert conn.execute('SELECT id FROM entries WHERE brokerExecutionId=?', (key,)).fetchone()[0] == int(original['id'])


def test_filtered_profit_keeps_full_cost_and_filtered_holdings_consume_later_sells(client):
    _login(client)
    entries.create('trader', _buy(id=1, price=100, qty=10, tradeClass='장기투자', rawDate='2026-01-01T09:00'))
    entries.create('trader', _sell(id=2, price=120, qty=4, tradeClass='단기스윙', rawDate='2026-01-11T09:00'))
    query = {'tradeClass': '단기스윙'}
    stats = client.get('/api/journal/stats', query_string=query).json
    assert (stats['totalRealized'], stats['buyCount'], stats['sellCount']) == (80, 0, 1)
    assert stats['realizedReturnRate'] == 20
    assert stats['avgHoldingDays'] == 10
    assert stats['totalBuyAmount'] == 0
    chart = client.get('/api/journal/chart', query_string=dict(query, period='2026-01')).json
    assert chart['monthlyData']['2026-01']['realized'] == 80
    assert chart['evaluation'] == []
    portfolio = client.get('/api/journal/portfolio', query_string=query).json
    assert portfolio['totalRealizedProfit'] == 80
    assert portfolio['holdings']['005930']['qty'] == 0

    long_query = {'tradeClass': '장기투자'}
    holding = client.get('/api/journal/portfolio', query_string=long_query).json['holdings']['005930']
    assert (holding['qty'], holding['totalCost'], holding['realizedProfit']) == (6, 600, 0)
    chart = client.get('/api/journal/chart', query_string=dict(long_query, period='2026-01')).json
    assert sum(lot['quantity'] for lot in chart['evaluation']) == 6


def test_filtered_sell_uses_all_purchases_and_fifo_holding_period(client):
    _login(client)
    entries.create('trader', _buy(id=1, qty=1, price=100, tradeClass='장기투자', rawDate='2026-01-01T09:00'))
    entries.create('trader', _buy(id=2, qty=1, price=200, tradeClass='단기스윙', rawDate='2026-01-02T09:00'))
    entries.create('trader', _sell(id=3, qty=1, price=180, tradeClass='단기스윙', rawDate='2026-01-03T09:00'))
    query = {'tradeClass': '단기스윙'}
    stats = client.get('/api/journal/stats', query_string=query).json
    assert stats['totalRealized'] == 30
    assert stats['avgHoldingDays'] == 2
    holding = client.get('/api/journal/portfolio', query_string=query).json['holdings']['005930']
    assert (holding['qty'], holding['avgPrice'], holding['totalCost']) == (1, 150, 150)


@pytest.mark.parametrize('query', [{'stock': 'AAPL'}, {'subAccount': '미국계좌'}, {'brokers': '["US"]'}])
def test_currency_is_resolved_from_requested_stock_or_account(client, query):
    _login(client)
    entries.create('trader', _buy(id=1, price=10000, qty=1, currency='KRW', subAccount='A', accountName='국내계좌'))
    entries.create('trader', _buy(id=2, stock='Apple', stockCode='AAPL', price=12.5, qty=2,
                                currency='USD', subAccount='B', accountName='미국계좌', brokerAccount='US'))
    stats = client.get('/api/journal/stats', query_string=query).json
    assert (stats['currency'], stats['totalBuyAmount']) == ('USD', 25)
    portfolio = client.get('/api/journal/portfolio', query_string=query).json
    assert portfolio['currency'] == 'USD'
    assert portfolio['holdings']['AAPL']['totalCost'] == 25
    chart = client.get('/api/journal/chart', query_string=dict(query, period='2026-01')).json
    assert chart['currency'] == 'USD'
    explicit = client.get('/api/journal/stats', query_string=dict(query, currency='KRW')).json
    assert (explicit['currency'], explicit['totalBuyAmount']) == ('KRW', 0)
