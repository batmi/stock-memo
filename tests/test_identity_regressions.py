"""저장 검증·재구성·조회에서 증권사와 레거시 종목을 같은 기준으로 구분한다."""

import hashlib
import json

import pytest

from app.database.db import db_conn
from app.domain.trades import resolve_trade_identities
from app.services import entries, realized_pnl
from helpers import _buy, _sell, _login
from test_journal_api import seed
from test_trading_api import _trade, api as api
from test_valuation import cache, evaluate, values


@pytest.mark.parametrize('buy_code,sell_code', [('', '005930'), ('005930', '')])
def test_legacy_code_matches_in_validation_all_views_and_historical_prices(client, buy_code, sell_code):
    _login(client)
    buy = _buy(id=1, qty=10, price=100, stockCode=buy_code, rawDate='2026-01-05T09:00')
    entries.create('trader', buy)
    response = client.post('/api/entry', json=_sell(
        id=2, qty=5, price=150, stockCode=sell_code, rawDate='2026-01-06T09:00'))
    assert response.status_code == 200
    sale = client.get('/api/entry/2').json
    assert (sale['realizedPnl'], sale['realizedPnlSource']) == (250, 'reconstructed')
    holding = client.get('/api/journal/portfolio').json['holdings']['005930']
    assert (holding['qty'], holding['totalCost'], holding['realizedProfit']) == (5, 500, 250)
    assert client.get('/api/journal/stats').json['totalRealized'] == 250
    assert client.get('/api/journal/calendar?month=2026-01').json['2026-01-06']['profit'] == 250
    chart = client.get('/api/journal/chart?period=2026-01').json
    assert chart['monthlyData']['2026-01']['realized'] == 250
    assert sum(lot['quantity'] for lot in chart['evaluation']) == 5
    cache({'2026-01-05': 120, '2026-01-06': 140})
    assert values(evaluate(client, '2026-01-05', '2026-01-06')) == [200, 200]
    # 계산용 코드 연결은 사용자가 저장한 원본을 덮어쓰지 않는다.
    assert client.get('/api/entry/1').json['stockCode'] == buy_code
    assert sale['stockCode'] == sell_code
    assert client.put('/api/entry/1', json=dict(buy, quantity=2)).status_code == 409


def test_ambiguous_name_cannot_supply_quantity_to_another_code(client):
    _login(client)
    entries.create('trader', _buy(id=1, qty=10, stockCode='', rawDate='2026-01-01T09:00'))
    entries.create('trader', _buy(id=2, qty=1, stockCode='AAA', rawDate='2026-01-02T09:00'))
    entries.create('trader', _buy(id=3, qty=1, stockCode='BBB', rawDate='2026-01-02T09:00'))
    response = client.post('/api/entry', json=_sell(
        id=4, qty=2, stockCode='AAA', rawDate='2026-01-03T09:00'))
    assert response.status_code == 400
    holdings = client.get('/api/journal/portfolio').json['holdings']
    assert {key: p['qty'] for key, p in holdings.items()} == {'삼성전자': 10, 'AAA': 1, 'BBB': 1}


def test_code_resolution_does_not_cross_broker_account_currency_or_mutate_input():
    base = _buy(stockCode='', brokerAccount='A', subAccount='11-22', currency='KRW')
    rows = [base, dict(base, stockCode='AAA', brokerAccount='B'),
            dict(base, stockCode='BBB', subAccount='other'), dict(base, stockCode='CCC', currency='USD')]
    assert resolve_trade_identities(rows)[0]['stockCode'] == ''
    rows.append(dict(base, stockCode='005930', subAccount='1122'))
    assert resolve_trade_identities(rows)[0]['stockCode'] == '005930'
    assert base['stockCode'] == ''


def test_changing_resolved_legacy_buy_to_another_code_warns_about_existing_sale(client):
    _login(client)
    buy = _buy(id=1, qty=10, stockCode='', rawDate='2026-01-01T09:00')
    entries.create('trader', buy)
    entries.create('trader', _sell(id=2, qty=5, rawDate='2026-01-02T09:00'))
    assert client.put('/api/entry/1', json=dict(buy, stockCode='OTHER')).status_code == 409


def test_brokers_separate_cost_quantity_groups_and_historical_evaluation(client):
    _login(client)
    common = {'subAccount': '12-34', 'accountName': '일반', 'rawDate': '2026-01-05T09:00'}
    entries.create('trader', _buy(id=1, qty=1, price=100, brokerAccount='A', **common))
    entries.create('trader', _buy(id=2, qty=1, price=200, brokerAccount='B', **common))
    entries.create('trader', _sell(id=3, qty=1, price=150, brokerAccount='A', subAccount='1234',
                                  accountName='일반', rawDate='2026-01-06T09:00'))
    assert client.get('/api/entry/3').json['realizedPnl'] == 50
    portfolio = client.get('/api/journal/portfolio').json
    assert portfolio['totalRealizedProfit'] == 50
    assert portfolio['holdings']['005930']['totalCost'] == 200
    # A 계좌를 청산한 뒤 B 잔고를 빌려 추가 매도할 수 없다.
    assert client.post('/api/entry', json=_sell(
        id=4, qty=1, brokerAccount='A', subAccount='1234', rawDate='2026-01-07T09:00')).status_code == 400
    groups = client.get('/api/journal/stats').json['groups']['accounts']
    assert len(groups) == 2 and len({g['key'] for g in groups}) == 2
    group = next(g for g in groups if g['totalPnl'] == 50)
    details = client.get('/api/journal/stats/trades', query_string={
        'metric': 'sell', 'groupBy': 'accountKey', 'groupKey': group['key']}).json
    assert [row['id'] for row in details['items']] == [3]
    cache({'2026-01-05': 150, '2026-01-06': 150})
    assert values(evaluate(client, '2026-01-05', '2026-01-06')) == [0, -50]


def test_mapping_and_exclusion_do_not_cross_brokers(client):
    _login(client)
    client.post('/api/mappings', json={'accounts': {
        '1234': {'broker_name': 'A', 'alias': '제외계좌', 'exclude_from_stats': True}}})
    entries.create('trader', _buy(id=1, qty=1, price=100, brokerAccount='A', subAccount='1234'))
    entries.create('trader', _buy(id=2, qty=1, price=200, brokerAccount='B', subAccount='1234'))
    assert client.get('/api/journal/stats').json['totalBuyAmount'] == 200
    assert client.get('/api/journal/portfolio').json['holdings']['005930']['qty'] == 1


def test_long_valid_account_identity_can_open_trade_details(client):
    _login(client)
    entries.create('trader', _buy(id=1, brokerAccount='증권사' * 60, subAccount='1' * 200))
    group = client.get('/api/journal/stats').json['groups']['accounts'][0]
    response = client.get('/api/journal/stats/trades', query_string={
        'groupBy': 'accountKey', 'groupKey': group['key']})
    assert response.status_code == 200
    assert [row['id'] for row in response.json['items']] == [1]


def test_opening_v2_retries_remain_idempotent_and_new_broker_gets_separate_balance(api):
    client, headers = api['client'], api['headers']
    legacy = 'OPENING:REAL:2026-01-01:005930'
    old_digest = hashlib.sha256(json.dumps(('1234', 'KRW')).encode()).hexdigest()
    original = client.post('/api/v1/trades', headers=headers, json=_trade(
        brokerExecutionId=f'{legacy}:v2:{old_digest}', brokerAccount='A', subAccount='1234',
        executedAt='2026-01-01T00:00:00+09:00')).json
    body = {'asOf': '2026-01-01', 'positions': [
        {'symbol': '005930', 'avgPrice': 100, 'volume': 1, 'brokerAccount': broker, 'subAccount': '12-34'}
        for broker in ('A', 'B')]}
    result = client.post('/api/v1/positions/opening', headers=headers, json=body).json
    assert (result['inserted'], result['skipped']) == (1, 1)
    assert result['results'][0]['id'] == original['id']
    retry = client.post('/api/v1/positions/opening', headers=headers, json=body).json
    assert (retry['inserted'], retry['skipped']) == (0, 2)


def test_upgrade_repairs_reconstructed_profit_once_and_preserves_recorded_profit(client):
    _login(client, 'reader')
    for entry_id, broker, price in [(1, 'A', 100), (2, 'B', 200)]:
        seed(id=entry_id, brokerAccount=broker, subAccount='1234', price=price)
    seed(id=3, brokerAccount='A', subAccount='1234', tradeType='매도', price=150,
         rawDate='2026-01-02T09:00', realizedPnl=0, realizedPnlSource='reconstructed')
    seed(id=4, brokerAccount='B', subAccount='1234', tradeType='매도', price=250,
         rawDate='2026-01-02T09:00', realizedPnl=7, realizedPnlSource='recorded')
    assert realized_pnl.backfill() == 1
    assert client.get('/api/entry/3').json['realizedPnl'] == 50
    assert client.get('/api/entry/4').json['realizedPnl'] == 7
    assert realized_pnl.backfill() == 0


def test_failed_upgrade_rolls_back_values_and_marker(client, monkeypatch):
    _login(client, 'reader')
    seed(id=1)
    seed(id=2, tradeType='매도', price=150, rawDate='2026-01-02T09:00')
    refresh = realized_pnl.refresh
    def fail_after_refresh(*args):
        refresh(*args)
        raise RuntimeError('interrupted migration')
    monkeypatch.setattr(realized_pnl, 'refresh', fail_after_refresh)
    with pytest.raises(RuntimeError):
        realized_pnl.backfill()
    with db_conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM data_migrations').fetchone()[0] == 0
        assert conn.execute('SELECT realizedPnl FROM entries WHERE id=2').fetchone()[0] is None
    monkeypatch.setattr(realized_pnl, 'refresh', refresh)
    assert realized_pnl.backfill() == 1


def test_memo_edits_skip_replay_but_price_edits_refresh_profit(client, monkeypatch):
    _login(client)
    buy = _buy(id=1, qty=2, price=100, rawDate='2026-01-05T09:00')
    entries.create('trader', buy)
    entries.create('trader', _sell(id=2, qty=1, price=150, source='broker', rawDate='2026-01-06T09:00'))
    original = realized_pnl.refresh
    calls = []
    def track(*args):
        calls.append(args[1])
        return original(*args)
    monkeypatch.setattr(realized_pnl, 'refresh', track)
    entries.update('trader', 1, dict(buy, thoughts='메모 정정'))
    entries.patch_bot_trade('trader', 2, {'thoughts': '봇 메모 정정'})
    assert calls == []
    entries.update('trader', 1, dict(buy, price=120))
    assert calls == ['trader']
    assert client.get('/api/entry/2').json['realizedPnl'] == 30
