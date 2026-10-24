"""경합·복원·분할·편집 충돌이 실제 API와 계산 결과에 미치는 영향을 검증한다."""

import io
import json
import threading
import zipfile

import pytest

from app.database import entry_logic
from app.database.db import db_conn
from app.services import accounts, entries, journal, realized_pnl
from app.utils import statscache
from helpers import _buy, _sell, _login, _ensure_user
from test_valuation import cache, evaluate, values


def archive(rows, mappings=None, rules=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as z:
        z.writestr('data.json', json.dumps(rows))
        if mappings is not None:
            z.writestr('account_info.json', json.dumps(mappings))
        if rules is not None:
            z.writestr('split_rules.json', json.dumps(rules))
    return stream.getvalue()


def upload(client, path, data, **fields):
    return client.post(path, data={'file': (io.BytesIO(data), 'backup.zip'), **fields})


def record(entry_id, **extra):
    return _buy(id=entry_id, qty=10, price=100, rawDate='2026-01-05T09:00:00', **extra)


def test_restore_preserves_same_time_trade_order_despite_id_collision(client):
    _login(client)
    _ensure_user('other')
    entries.create('other', record(1))
    rows = [record(1), _sell(id=2, qty=5, price=150, rawDate='2026-01-05T09:00:00')]
    assert upload(client, '/api/restore', archive(rows)).status_code == 200
    model = journal.snapshot('trader')
    assert [r['tradeType'] for r in model['rows']] == ['매수', '매도']
    assert model['rows'][1]['realizedPnl'] == 250
    assert model['rows'][0]['id'] != 1
    with db_conn() as conn:
        assert conn.execute('SELECT quantity FROM entries WHERE username=?', ('other',)).fetchone()[0] == 10


def test_restore_preserves_legacy_id_based_date(client):
    _login(client)
    _ensure_user('other')
    entry = record(1767574800000)
    entry.pop('rawDate')
    entries.create('other', entry)
    assert upload(client, '/api/restore', archive([entry])).status_code == 200
    model = journal.snapshot('trader')
    assert model['rows'][0]['_day'].year == 2026
    assert model['rows'][0]['_day'].month == 1


@pytest.mark.parametrize('legacy', [False, True])
def test_empty_account_settings_are_restored_including_legacy_backup(client, legacy):
    _login(client)
    data = archive([]) if legacy else client.get('/api/backup').data
    assert client.post('/api/mappings', json={'accounts': {'A': {'exclude_from_stats': True}}}).status_code == 200
    assert upload(client, '/api/restore', data).status_code == 200
    assert client.get('/api/mappings').json == accounts.empty_mappings()


def test_restore_preview_is_read_only_and_requires_matching_current_state(client):
    _login(client)
    entries.create('trader', record(1))
    data = archive([dict(record(1), quantity=30)])
    response = upload(client, '/api/restore/preview', data)
    assert response.status_code == 200
    assert response.json['holdings'][0]['beforeQuantity'] == 10
    assert response.json['holdings'][0]['afterQuantity'] == 30
    assert client.get('/api/entry/1').json['quantity'] == 10
    entries.create('trader', record(2))
    assert upload(client, '/api/restore', data, previewToken=response.json['previewToken']).status_code == 409
    assert client.get('/api/entry/2').status_code == 200
    token = upload(client, '/api/restore/preview', data).json['previewToken']
    assert upload(client, '/api/restore', archive([]), previewToken=token).status_code == 409
    assert upload(client, '/api/restore', data, previewToken=token).status_code == 200
    assert client.get('/api/entry/1').json['quantity'] == 30
    assert upload(client, '/api/restore', data, previewToken=token).status_code == 409


def test_stale_edit_cannot_overwrite_bot_correction_or_restored_entry(client):
    _login(client)
    entries.create('trader', record(1))
    old = client.get('/api/entry/1').json
    with db_conn(write=True) as conn:
        entry_logic.update_columns(conn.cursor(), 1, 'trader', {'quantity': 20})
        conn.commit()
    response = client.put('/api/entry/1?force=1', json=dict(old, title='옛 편집창'))
    assert response.status_code == 409 and response.json['errorCode'] == 'STALE_ENTRY'
    assert client.get('/api/entry/1').json['quantity'] == 20
    current = client.get('/api/entry/1').json
    assert client.put('/api/entry/1', json=dict(current, title='새 편집창')).status_code == 200
    old = client.get('/api/entry/1').json
    backup = client.get('/api/backup').data
    assert upload(client, '/api/restore', backup).status_code == 200
    assert client.put('/api/entry/1', json=dict(old, title='복원 전 편집창')).status_code == 409


def test_split_consistent_across_holdings_realized_and_daily_valuation(client):
    _login(client)
    cache({'2026-01-05': 100, '2026-01-06': 100, '2026-01-07': 60}, splits={'2026-01-07': 2})
    entries.create('trader', record(1))
    entries.create('trader', _sell(id=2, qty=10, price=60, rawDate='2026-01-07T09:00:00'))
    model = journal.snapshot('trader')
    assert model['rows'][0]['quantity'] == 20
    assert model['rows'][1]['realizedPnl'] == 100
    assert values(evaluate(client, '2026-01-05', '2026-01-07')) == [0, 0, 100]
    with db_conn() as conn:
        assert entry_logic.net_holding_for_stock(conn.cursor(), 'trader', '삼성전자', stock_code='005930') == 10
    assert client.get('/api/entry/1').json['quantity'] == 10  # 원본 불변
    data = {'code': '005930', 'currency': 'KRW', 'day': '2026-01-07', 'mode': 'recorded'}
    assert client.put('/api/journal/split-rule', json=data).status_code == 200
    assert values(evaluate(client, '2026-01-05', '2026-01-07')) == [0, 0, 0]
    assert journal.snapshot('trader')['rows'][1]['realizedPnl'] == -400
    backup = client.get('/api/backup').data
    assert client.put('/api/journal/split-rule', json=dict(data, mode='apply')).status_code == 200
    assert upload(client, '/api/restore', backup).status_code == 200
    assert client.get('/api/journal/integrity').json['splits'][0]['mode'] == 'recorded'


def test_split_sell_validation_and_supplied_profit_remain_authoritative(client):
    _login(client)
    cache({}, splits={'2026-01-07': 2, '2099-01-01': 10})
    entries.create('trader', record(1))
    assert client.post('/api/entry', json=_sell(id=2, qty=15, price=60, realizedPnl=7,
                                               rawDate='2026-01-07T09:00')).status_code == 200
    assert client.get('/api/entry/2').json['realizedPnl'] == 7
    assert client.post('/api/entry', json=_sell(id=3, qty=6, price=60,
                                               rawDate='2026-01-08T09:00')).status_code == 400


def test_initial_split_collection_refreshes_cached_balance_and_reconstructed_profit(client, monkeypatch):
    from app.services import historical_prices, historical_provider
    _login(client)
    entries.create('trader', record(1))
    entries.create('trader', _sell(id=2, qty=5, price=60, rawDate='2026-01-07T09:00'))
    assert journal.snapshot('trader')['rows'][1]['realizedPnl'] == -200
    monkeypatch.setattr(historical_provider, 'fetch_asset', lambda *args: {
        'symbol': '005930.KS', 'timezone': 'Asia/Seoul', 'splits': {'2026-01-07': 2}})
    monkeypatch.setattr(historical_provider, 'fetch_closes', lambda *args: {'2026-01-07': 60})
    historical_prices.enqueue({('005930', 'KRW'): {'2026-01-07'}})
    assert historical_prices.run_one()
    assert journal.snapshot('trader')['rows'][0]['quantity'] == 20
    assert client.get('/api/entry/2').json['realizedPnl'] == 50


def test_scoped_account_mapping_and_exclusion_do_not_cross_brokers(client):
    _login(client)
    mappings = {'accounts': {
        'A/1234': {'number': '12-34', 'broker_name': 'A', 'alias': '공통', 'exclude_from_stats': True},
        'B/1234': {'number': '1234', 'broker_name': 'B', 'alias': '공통', 'exclude_from_stats': False}}}
    assert client.post('/api/mappings', json=mappings).status_code == 200
    entries.create('trader', record(1, brokerAccount='A', subAccount='1234'))
    entries.create('trader', record(2, brokerAccount='B', subAccount='12-34'))
    rows = journal.snapshot('trader')['rows']
    assert [r['_excluded'] for r in rows] == [True, False]
    assert accounts.find_account_mapping(mappings['accounts'], '1234') == (None, None)
    assert accounts.find_account_mapping(mappings['accounts'], '12-34', 'B')[0] == 'B/1234'


def test_integrity_and_change_history_are_user_scoped_and_paginated(client):
    _login(client)
    entries.create('trader', record(1))
    with db_conn(write=True) as conn:
        for qty in range(11, 66):
            conn.execute('UPDATE entries SET quantity=? WHERE id=1', (qty,))
        entry_logic.insert_entry(conn.cursor(), 'trader', _sell(id=2, qty=100, price=100,
                                 rawDate='2026-01-06T09:00'))
        realized_pnl.refresh(conn, 'trader')
        conn.commit()
    statscache.invalidate('trader')
    result = client.get('/api/journal/integrity').json
    assert result['issueCount'] == 1
    assert '매도 수량' in ' '.join(result['issues'][0]['reasons'])
    page = client.get('/api/journal/changes').json
    assert len(page['items']) == 50 and page['nextCursor']
    next_page = client.get('/api/journal/changes', query_string={'before': page['nextCursor']}).json
    assert next_page['items'] and not ({x['id'] for x in page['items']} & {x['id'] for x in next_page['items']})
    _login(client, 'other')
    assert not client.get('/api/journal/changes').json['items']
    assert not client.get('/api/journal/integrity').json['issues']


def test_deleted_and_recreated_username_rejects_old_session(client):
    _login(client)
    assert client.get('/api/journal/meta').status_code == 200
    from app.services.users import purge_account
    with db_conn(write=True) as conn:
        purge_account(conn.cursor(), 'trader')
        conn.commit()
    _ensure_user('trader')
    assert client.post('/api/entry', json=record(1)).status_code == 401
    with db_conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM entries').fetchone()[0] == 0


def test_delete_waits_for_inflight_entry_write(app, monkeypatch):
    from app.services import images
    writer, admin = app.test_client(), app.test_client()
    _login(writer)
    _login(admin, 'manager')
    with admin.session_transaction() as session:
        session['is_admin'] = True
    with db_conn(write=True) as conn:
        conn.execute("UPDATE users SET is_admin=1 WHERE username='manager'")
        conn.commit()
    reached, release, deletion_started = threading.Event(), threading.Event(), threading.Event()
    original = images.store_inline_images
    def pause(*args, **kwargs):
        reached.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(images, 'store_inline_images', pause)
    results = {}
    def write():
        results['write'] = writer.post('/api/entry', json=record(1)).status_code
    def delete():
        deletion_started.set()
        results['delete'] = admin.delete('/api/admin/users/trader').status_code
    first = threading.Thread(target=write)
    second = threading.Thread(target=delete)
    first.start()
    try:
        assert reached.wait(5)
        second.start()
        assert deletion_started.wait(5)
    finally:
        release.set()
        first.join(5)
        if second.ident:
            second.join(5)
    assert results == {'write': 200, 'delete': 200}
    with db_conn() as conn:
        for table in ('users', 'entries', 'entry_changes'):
            assert conn.execute(f'SELECT COUNT(*) FROM {table} WHERE username=?', ('trader',)).fetchone()[0] == 0


def test_reverse_split_uses_new_units_without_changing_trade_amount(client):
    _login(client)
    cache({}, splits={'2026-01-07': 0.1})
    entries.create('trader', record(1))
    entries.create('trader', _sell(id=2, qty=0.5, price=1200, rawDate='2026-01-07T09:00'))
    rows = journal.snapshot('trader')['rows']
    assert rows[0]['quantity'] == 1 and rows[0]['price'] == 1000
    assert rows[1]['realizedPnl'] == 100
    with db_conn() as conn:
        assert entry_logic.net_holding_for_stock(conn.cursor(), 'trader', '삼성전자', stock_code='005930') == 0.5


@pytest.mark.parametrize('version,with_settings', [(99, True), (2, False)])
def test_invalid_manifest_is_rejected_before_data_changes(client, version, with_settings):
    _login(client)
    entries.create('trader', record(1))
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as z:
        z.writestr('data.json', '[]')
        z.writestr('manifest.json', json.dumps({'formatVersion': version}))
        if with_settings:
            z.writestr('account_info.json', '{}')
            z.writestr('split_rules.json', '[]')
    assert upload(client, '/api/restore/preview', stream.getvalue()).status_code == 400
    assert upload(client, '/api/restore', stream.getvalue()).status_code == 400
    assert client.get('/api/entry/1').json['quantity'] == 10


def test_preview_token_expiration_and_other_user_are_rejected(client, monkeypatch):
    _login(client)
    data = archive([])
    token = upload(client, '/api/restore/preview', data).json['previewToken']
    _login(client, 'other')
    assert upload(client, '/api/restore', data, previewToken=token).status_code == 409
    _login(client)
    import itsdangerous.timed
    timestamp = itsdangerous.timed.TimestampSigner.get_timestamp
    monkeypatch.setattr(itsdangerous.timed.TimestampSigner, 'get_timestamp', lambda self: timestamp(self) + 601)
    assert upload(client, '/api/restore', data, previewToken=token).status_code == 409


def test_legacy_cookie_without_persistent_user_id_cannot_attach_to_recreated_account(client):
    from app.services.users import purge_account
    _login(client)
    with client.session_transaction() as session:
        session.pop('user_id')
    with db_conn(write=True) as conn:
        purge_account(conn.cursor(), 'trader')
        conn.commit()
    _ensure_user('trader')
    assert client.post('/api/entry', json=record(1)).status_code == 401
