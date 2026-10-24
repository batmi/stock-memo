"""증권사 식별·분할 정정·비용 계약·시세 계획을 실제 저장/조회 경로로 검증한다."""

from datetime import date
import json
import sqlite3

import pytest

from app.database import entry_logic, schema
from app.database.db import db_conn
from app.domain.trades import split_scope
from app.services import accounts, entries, historical_prices as history, historical_provider as provider
from app.services import journal, realized_pnl, valuation
from app.utils import statscache
from helpers import _buy, _sell, _login
from test_trading_api import api as api_fixture, _trade
from test_valuation import cache, evaluate, values


MAPPING = {'brokers': {'243': '한국투자증권'}, 'accounts': {
    '1234': {'broker_code': '243', 'broker_name': '한국투자증권', 'alias': '일반'}}}
api = api_fixture


def save_mapping(username='bot'):
    with db_conn(write=True) as conn:
        accounts.save(conn, username, MAPPING)
        conn.commit()


def test_web_buy_and_bot_sell_share_canonical_broker(api):
    save_mapping()
    entries.create('bot', _buy(id=1, qty=10, price=100, brokerAccount='243', subAccount='1234',
                               rawDate='2026-08-01T09:00:00'))
    response = api['client'].post('/api/v1/trades', headers=api['headers'], json=_trade(
        side='SELL', price=110, volume=10, brokerAccount='243', subAccount='1234'))
    assert response.status_code == 201
    assert response.json['brokerAccount'] == '한국투자증권'  # 표시 원문 보존
    assert not response.json['needsReview'] and response.json['realizedPnl'] == 100
    model = journal.snapshot('bot')
    result = journal.portfolio(model, journal.filters({}), '2026-08')
    assert result['holdings']['005930']['qty'] == 0
    assert result['totalRealizedProfit'] == 100
    for broker in ('243', '한국투자증권'):
        query = journal.filters({'brokers': json.dumps([broker])})
        assert journal.portfolio(model, query, '2026-08')['totalRealizedProfit'] == 100
    assert journal.metadata(model)['brokers'] == ['243']


def test_canonical_backfill_preserves_supplied_pnl(client):
    _login(client)
    with db_conn(write=True) as conn:
        entry_logic.insert_entry(conn.cursor(), 'trader', _buy(id=1, qty=10, price=100,
            brokerAccount='243', subAccount='1234', rawDate='2026-01-05T09:00'))
        entry_logic.insert_entry(conn.cursor(), 'trader', _sell(id=2, qty=5, price=110,
            brokerAccount='한국투자증권', subAccount='1234', rawDate='2026-01-06T09:00', realizedPnl=7))
        entry_logic.insert_entry(conn.cursor(), 'trader', _sell(id=3, qty=5, price=120,
            brokerAccount='한국투자증권', subAccount='1234', rawDate='2026-01-07T09:00'))
        accounts.save(conn, 'trader', MAPPING)
        conn.commit()
    assert client.get('/api/entry/2').json['realizedPnl'] == 7
    assert client.get('/api/entry/3').json['realizedPnl'] == 100
    assert {r['brokerIdentity'] for r in journal.snapshot('trader')['rows']} == {'243'}
    assert accounts.broker_identity('공통', {'brokers': {'1': '공통', '2': '공통'}}) == '공통'


def test_web_date_edit_validates_final_trade_day_and_flags_after_force(client):
    _login(client)
    cache({}, splits={'2026-01-07': 2})
    entries.create('trader', _buy(id=1, qty=10, price=100, rawDate='2026-01-05T09:00:00',
        exchange='KRX', tradeDate='2026-01-05', executedAtUtc='2026-01-05T00:00:00Z', source='bot'))
    entries.create('trader', _sell(id=2, qty=15, price=60, rawDate='2026-01-08T09:00:00'))
    old = client.get('/api/entry/1').json
    edit = dict(old, rawDate='2026-01-07T09:00:00')
    assert client.put('/api/entry/1', json=edit).status_code == 409
    assert client.get('/api/entry/1').json['tradeDate'] == '2026-01-05'
    assert client.put('/api/entry/1?force=1', json=edit).status_code == 200
    sale = client.get('/api/entry/2').json
    assert sale['needsReview'] == 1 and sale['realizedPnlSource'] == 'unavailable'


def test_exchange_only_edit_rechecks_final_local_trade_day(client):
    _login(client)
    cache({}, code='AAPL', currency='USD', splits={'2026-01-07': 2})
    entries.create('trader', _buy(id=1, stockCode='AAPL', currency='USD', qty=10, price=100,
        rawDate='2026-01-07T03:00:00', exchange='NASDAQ', tradeDate='2026-01-06',
        executedAtUtc='2026-01-06T18:00:00Z', source='bot'))
    entries.create('trader', _sell(id=2, stockCode='AAPL', currency='USD', qty=15, price=60,
        rawDate='2026-01-08T09:00'))
    old = client.get('/api/entry/1').json
    assert client.put('/api/entry/1', json=dict(old, exchange='KRX')).status_code == 409
    assert client.get('/api/entry/2').json['realizedPnl'] == 150


def test_scoped_split_preview_is_read_only_and_valuation_matches(client):
    _login(client)
    cache({'2026-01-07': 60}, splits={'2026-01-07': 2})
    for entry_id, account in enumerate(('A', 'B'), 1):
        entries.create('trader', _buy(id=entry_id, qty=10, price=100,
            subAccount=account, rawDate='2026-01-05T09:00'))
    change = {'code': '005930', 'currency': 'KRW', 'day': '2026-01-07', 'mode': 'recorded',
                  'scope': split_scope({'subAccount': 'A'})}
    preview = client.post('/api/journal/split-rule/preview', json=change)
    assert preview.status_code == 200
    assert [(x['account'], x['beforeQuantity'], x['afterQuantity']) for x in preview.json['holdings']] == [('A', 20, 10)]
    assert values(evaluate(client, '2026-01-07', '2026-01-07')) == [400]
    assert client.put('/api/journal/split-rule', json=dict(change, previewToken=preview.json['previewToken'])).status_code == 200
    assert values(evaluate(client, '2026-01-07', '2026-01-07')) == [-200]
    assert client.put('/api/journal/split-rule', json=dict(change, scope='["","other"]')).status_code == 400


def test_split_preview_rejects_changes_after_review(client):
    _login(client)
    cache({}, splits={'2026-01-07': 2})
    entries.create('trader', _buy(id=1, qty=10, rawDate='2026-01-05T09:00'))
    change = {'code': '005930', 'currency': 'KRW', 'day': '2026-01-07', 'mode': 'recorded'}
    preview = client.post('/api/journal/split-rule/preview', json=change).json
    entries.create('trader', _buy(id=2, qty=1, rawDate='2026-01-08T09:00'))
    assert client.put('/api/journal/split-rule', json=dict(change, previewToken=preview['previewToken'])).status_code == 409
    with db_conn() as conn:
        assert conn.execute('SELECT count(*) FROM split_rules').fetchone()[0] == 0


def test_old_global_split_rules_survive_schema_upgrade(app):
    with db_conn() as conn:
        conn.execute('DROP TABLE split_rules')
        conn.execute('''CREATE TABLE split_rules (username TEXT,code TEXT,currency TEXT,day TEXT,mode TEXT,
            PRIMARY KEY(username,code,currency,day))''')
        conn.execute("INSERT INTO split_rules VALUES ('u','AAPL','USD','2020-08-31','recorded')")
        conn.commit()
        schema.init(conn)
        assert dict(conn.execute('SELECT * FROM split_rules').fetchone())['scope'] == ''
        schema.init(conn)
        assert conn.execute('SELECT count(*) FROM split_rules').fetchone()[0] == 1


@pytest.mark.parametrize('failure', ['copy', 'drop'])
def test_split_upgrade_rolls_back_ddl_and_preserves_rules_for_retry(app, failure):
    with db_conn() as conn:
        conn.execute('DROP TABLE split_rules')
        conn.execute('''CREATE TABLE split_rules (username TEXT,code TEXT,currency TEXT,day TEXT,mode TEXT,
            PRIMARY KEY(username,code,currency,day))''')
        conn.execute("INSERT INTO split_rules VALUES ('u','AAPL','USD','2020-08-31','recorded')")
        conn.commit()
        def deny_step(action, table, _column, _database, _trigger):
            blocked = ((failure == 'copy' and action == sqlite3.SQLITE_INSERT and table == 'split_rules')
                       or (failure == 'drop' and action == sqlite3.SQLITE_DROP_TABLE and table == 'split_rules_legacy'))
            return sqlite3.SQLITE_DENY if blocked else sqlite3.SQLITE_OK
        conn.set_authorizer(deny_step)
        with pytest.raises(sqlite3.DatabaseError):
            schema.init(conn)
        conn.set_authorizer(None)
        conn.rollback()
    # 별도 연결은 부분 DDL이 이미 커밋되는 회귀도 잡는다.
    with db_conn() as conn:
        assert 'scope' not in {row[1] for row in conn.execute('PRAGMA table_info(split_rules)')}
        assert tuple(conn.execute('SELECT * FROM split_rules').fetchone()) == ('u', 'AAPL', 'USD', '2020-08-31', 'recorded')
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='split_rules_legacy'").fetchone() is None
        schema.init(conn)
        assert dict(conn.execute('SELECT * FROM split_rules').fetchone())['scope'] == ''
        schema.init(conn)
        assert conn.execute('SELECT count(*) FROM split_rules').fetchone()[0] == 1


@pytest.mark.parametrize('basis,expected', [('gross', 45), ('net', 50), ('unknown', None)])
def test_saved_profit_cost_basis_prevents_double_subtraction(client, basis, expected):
    _login(client)
    entries.create('trader', _buy(id=1, qty=1, price=100, rawDate='2026-01-05T09:00'))
    entries.create('trader', _sell(id=2, qty=1, price=160, realizedPnl=50, realizedPnlBasis=basis,
        fee=3, tax=2, rawDate='2026-01-06T09:00'))
    result = client.get('/api/journal/stats').json
    assert result['totalRealized'] == 50 and result['netPnl'] == expected
    assert client.get('/api/entry/2').json['realizedPnlBasis'] == basis


def test_bot_cost_basis_is_validated_and_patchable(api):
    body = _trade(side='SELL', realizedPnl=50, realizedPnlBasis='net', fee=5)
    response = api['client'].post('/api/v1/trades', headers=api['headers'], json=body)
    assert response.status_code == 201 and response.json['realizedPnlBasis'] == 'net'
    path = '/api/v1/trades/' + response.json['id']
    changed = api['client'].patch(path, headers=api['headers'], json={'realizedPnlBasis': 'gross'})
    assert changed.status_code == 200 and changed.json['realizedPnl'] == 50
    assert changed.json['realizedPnlBasis'] == 'gross'
    assert api['client'].patch(path, headers=api['headers'], json={'realizedPnlBasis': 'bad'}).status_code == 400


def test_held_assets_collect_splits_without_valuation_subscription(client, monkeypatch):
    _login(client)
    entries.create('trader', _buy(id=1, qty=10, price=100, rawDate='2026-01-05T09:00'))
    monkeypatch.setattr(provider, 'fetch_asset', lambda *args: {
        'symbol': '005930.KS', 'timezone': 'Asia/Seoul', 'splits': {'2026-01-07': 2}})
    monkeypatch.setattr(provider, 'fetch_closes', lambda *args: pytest.fail('메타데이터 수집에서 종가 요청 금지'))
    history.schedule_held_assets()
    history.schedule_held_assets()
    with db_conn() as conn:
        assert conn.execute('SELECT count(*) FROM historical_watchers').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM historical_jobs').fetchone()[0] == 1
    assert history.run_one()
    assert journal.snapshot('trader')['rows'][0]['quantity'] == 20
    history.schedule_held_assets()
    assert not history.run_one()


def test_missing_split_oversell_is_collected_even_when_balance_is_zero(client, monkeypatch):
    _login(client)
    entries.create('trader', _buy(id=1, qty=10, price=100, rawDate='2026-01-05T09:00'))
    with db_conn(write=True) as conn:
        entry_logic.insert_entry(conn.cursor(), 'trader', _sell(id=2, qty=20, price=60,
            rawDate='2026-01-08T09:00', needsReview=1))
        realized_pnl.refresh(conn, 'trader')
        conn.commit()
    statscache.invalidate('trader')
    before = journal.portfolio(journal.snapshot('trader'), journal.filters({}), '2026-01')
    assert before['holdings']['005930']['qty'] == 0
    assert client.get('/api/entry/2').json['realizedPnlSource'] == 'unavailable'
    monkeypatch.setattr(provider, 'fetch_asset', lambda *args: {
        'symbol': '005930.KS', 'timezone': 'Asia/Seoul', 'splits': {'2026-01-07': 2}})
    monkeypatch.setattr(provider, 'fetch_closes', lambda *args: pytest.fail('종가 수집 불필요'))
    history.schedule_held_assets()
    assert history.run_one()
    sale = client.get('/api/entry/2').json
    assert sale['realizedPnl'] == 200 and sale['realizedPnlSource'] == 'reconstructed'


def test_unchanged_history_plan_only_revisits_recent_window(client, monkeypatch):
    _login(client)
    entries.create('trader', _buy(id=1, qty=10, rawDate='2024-01-05T09:00'))
    history.watch('trader')
    history._plans.clear()
    monkeypatch.setattr(provider, 'completed_day', lambda *args: date(2026, 1, 9))
    calls = []
    monkeypatch.setattr(valuation, 'report', lambda model, query, start, end, **kw: calls.append((start, end)))
    history.schedule_watchers()
    assert len(calls) >= 2 and calls[0][0] == '2024-01-05'
    calls.clear()
    history.schedule_watchers()
    assert calls == [('2026-01-02', '2026-01-09')]
    statscache.invalidate('trader')
    calls.clear()
    history.schedule_watchers()
    assert calls[0][0] == '2024-01-05'


def test_analysis_cache_reuses_work_and_invalidates_after_mutation(client, monkeypatch):
    _login(client)
    entries.create('trader', _buy(id=1, qty=10, rawDate='2026-01-05T09:00'))
    original = journal.analysis.report
    calls = []
    def report(*args, **kw):
        calls.append(1)
        return original(*args, **kw)
    monkeypatch.setattr(journal.analysis, 'report', report)
    assert client.get('/api/journal/stats').status_code == 200
    assert client.get('/api/journal/stats').status_code == 200
    assert len(calls) == 1
    entries.create('trader', _buy(id=2, qty=1, rawDate='2026-01-06T09:00'))
    assert client.get('/api/journal/stats').status_code == 200
    assert len(calls) == 2
