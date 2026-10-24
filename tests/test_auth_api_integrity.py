"""인증 세대·읽기 소유자·키 교체·봇 시각 계약의 경합과 실패 경계를 검증한다."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import threading

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

import config
from app.database.db import db_conn
from app.routes import api as web_api, auth, ownership
from app.services import users
from app.utils import statscache
from helpers import _login, _signup_and_login, _user_id
from test_trading_api import api as api, _trade
import trading_api
from trading_api import keys


def _change_password(username='victim'):
    with db_conn() as conn:
        conn.execute('UPDATE users SET password_hash=? WHERE username=?',
                     (generate_password_hash('Changed123!'), username))
        users.bump_session_epoch(conn.cursor(), username)
        conn.commit()


def _replace_account(username):
    with db_conn() as conn:
        users.purge_account(conn.cursor(), username)
        conn.execute("INSERT INTO users (username,password_hash,is_allowed) VALUES (?, 'new', 1)",
                     (username,))
        conn.execute("INSERT INTO entries (username,type,title) VALUES (?, 'memo', 'new owner private memo')",
                     (username,))
        conn.commit()
    statscache.invalidate(username)


def test_login_uses_epoch_of_verified_password(client, monkeypatch):
    _signup_and_login(client, 'victim', 'Original123!')
    client.get('/logout')

    def verify_then_change(password_hash, password):
        valid = check_password_hash(password_hash, password)
        _change_password()
        return valid

    monkeypatch.setattr(auth, 'check_password_hash', verify_then_change)
    client.post('/login', data={'username': 'victim', 'password': 'Original123!'})
    assert client.get('/api/me/api-key').status_code == 401


def test_waiting_web_write_rechecks_password_epoch(client, monkeypatch):
    _signup_and_login(client, 'victim', 'Original123!')
    original = ownership.lock_request

    def change_before_lock(username):
        _change_password(username)
        original(username)

    monkeypatch.setattr(ownership, 'lock_request', change_before_lock)
    assert client.post('/api/me/api-key', json={'label': 'old session'}).status_code == 401
    with db_conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM api_keys').fetchone()[0] == 0


def test_epoch_read_cannot_poison_later_authentication(client, monkeypatch):
    _signup_and_login(client, 'victim', 'Original123!')
    original = users.db_conn
    changed = []

    @contextmanager
    def read_then_change(**kwargs):
        with original(**kwargs) as conn:
            yield conn
        if not changed:
            changed.append(True)
            _change_password()

    monkeypatch.setattr(users, 'db_conn', read_then_change)
    assert users.current_session_epoch('victim') == 0
    assert users.current_session_epoch('victim') == 1
    assert client.get('/api/me/api-key').status_code == 401


def test_rolled_back_password_change_does_not_invalidate_session(client):
    _signup_and_login(client, 'victim', 'Original123!')
    with db_conn() as conn:
        users.bump_session_epoch(conn.cursor(), 'victim')
        conn.rollback()
    assert client.get('/api/me/api-key').status_code == 200


def test_pending_restore_blocks_web_and_bot_with_actionable_json(api, monkeypatch):
    from app.services import restore_recovery
    _login(api['client'], 'bot')
    monkeypatch.setattr(restore_recovery, '_blocked_accounts', {(config.DB_FILE, 'bot')})
    for endpoint, headers in [('/api/preferences', {}), ('/api/v1/trades', api['headers'])]:
        response = api['client'].get(endpoint, headers=headers)
        assert response.status_code == 503
        assert '서버를 재시작' in response.json['error']


def test_get_rejects_recreated_owner_before_data_read(client, monkeypatch):
    _login(client, 'victim')
    old_id = _user_id('victim')
    original = statscache.data_etag
    changed = []

    def replace_after_auth(username):
        if not changed:
            changed.append(True)
            _replace_account(username)
        return original(username)

    monkeypatch.setattr(statscache, 'data_etag', replace_after_auth)
    response = client.get('/api/data')
    assert _user_id('victim') != old_id
    assert response.status_code == 401
    assert 'new owner private memo' not in response.get_data(as_text=True)


def test_owner_validation_and_read_share_database_snapshot(client, monkeypatch):
    _login(client, 'victim')
    with db_conn() as conn:
        conn.execute("INSERT INTO entries (username,type,title) VALUES ('victim','memo','old owner memo')")
        conn.commit()
    original = ownership.validate_owner
    changed = []

    def replace_after_snapshot(conn):
        original(conn)
        if not changed:
            changed.append(True)
            _replace_account('victim')

    monkeypatch.setattr(ownership, 'validate_owner', replace_after_snapshot)
    response = client.get('/api/data')
    assert response.status_code == 200
    assert [row['title'] for row in response.json] == ['old owner memo']


@pytest.mark.parametrize('body', [[], {'label': ['bad']}, {'label': 3},
                                 {'scopes': []}, {'scopes': ['unknown']},
                                 {'scopes': None}, {'keep_existing': 'false'}])
def test_invalid_key_rotation_preserves_existing_key(client, body):
    _login(client, 'victim')
    existing = trading_api.create_api_key('victim')
    assert client.post('/api/me/api-key', json=body).status_code == 400
    assert client.post('/api/v1/auth/token', headers={'X-API-KEY': existing['api_key']}).status_code == 200


def test_key_rotation_storage_failure_rolls_back_revocation(client, monkeypatch):
    _login(client, 'victim')
    existing = trading_api.create_api_key('victim')
    monkeypatch.setattr(keys, '_generate_api_key', lambda: existing['api_key'])
    assert client.post('/api/me/api-key', json={'label': 'replacement'}).status_code == 500
    assert trading_api.list_api_keys('victim')[0]['revoked_at'] is None
    assert client.post('/api/v1/auth/token', headers={'X-API-KEY': existing['api_key']}).status_code == 200


def test_web_issues_and_enforces_read_only_api_key(client):
    _login(client, 'victim')
    issued = client.post('/api/me/api-key', json={'scopes': ['trades:read']})
    assert issued.status_code == 200 and issued.json['scopes'] == ['trades:read']
    token = client.post('/api/v1/auth/token', headers={'X-API-KEY': issued.json['api_key']}).json['access_token']
    headers = {'Authorization': f'Bearer {token}'}
    assert client.get('/api/v1/trades', headers=headers).status_code == 200
    assert client.post('/api/v1/trades', headers=headers, json=_trade()).status_code == 403
    assert client.post('/api/v1/bot/status', headers=headers, json={'status': 'running'}).status_code == 403


@pytest.mark.parametrize('keep_existing', [False, True])
def test_successful_rotation_preserves_scope_defaults_and_selected_keys(client, keep_existing):
    _login(client, 'victim')
    existing = trading_api.create_api_key('victim')
    issued = client.post('/api/me/api-key', json={'keep_existing': keep_existing})
    assert issued.status_code == 200
    assert set(issued.json['scopes']) == set(trading_api.ALL_SCOPES)
    assert client.post('/api/v1/auth/token', headers={'X-API-KEY': issued.json['api_key']}).status_code == 200
    assert client.post('/api/v1/auth/token', headers={'X-API-KEY': existing['api_key']}).status_code == (
        200 if keep_existing else 401)


@pytest.mark.parametrize('value', ['2026-08-01T25:99:00Z', '2026-08-01T09:30:00+09:99',
                                 '2026-08-01garbage', '2026-08-01T09:30:00Ztrailing',
                                 '0001-01-01T00:00:00+09:00'])
def test_invalid_execution_time_is_not_silently_truncated(api, value):
    response = api['client'].post('/api/v1/trades', headers=api['headers'], json=_trade(executedAt=value))
    assert response.status_code == 400
    assert response.json['errorCode'] == 'INVALID_FIELD'
    with db_conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM entries').fetchone()[0] == 0


def test_trade_filter_compares_equivalent_offsets_and_legacy_times(api):
    client, headers = api['client'], api['headers']
    assert client.post('/api/v1/trades', headers=headers, json=_trade()).status_code == 201
    with db_conn() as conn:
        conn.execute("INSERT INTO entries (username,type,tradeType,rawDate,brokerExecutionId) "
                     "VALUES ('bot','trade','매수','2026-08-01T09:15:00','legacy')")
        conn.commit()
    queries = [
        {'from': '2026-08-01T00:00:00Z', 'to': '2026-08-01T00:30:00Z'},
        {'from': '2026-08-01T09:00:00+09:00', 'to': '2026-08-01T09:30:00+09:00'},
    ]
    results = [client.get('/api/v1/trades', headers=headers, query_string=q).json['trades'] for q in queries]
    assert len(results[0]) == 2 and results[0] == results[1]
    latest = client.get('/api/v1/trades/last-sync', headers=headers).json
    assert latest['lastExecutedAt'] == '2026-08-01T00:30:00Z'
    assert latest['lastBrokerExecutionId'] != 'legacy'


@pytest.mark.parametrize('query', [{'from': '2026-08-01bad'},
                                  {'from': '2026-08-02', 'to': '2026-08-01'}])
def test_invalid_trade_query_is_rejected(api, query):
    assert api['client'].get('/api/v1/trades', headers=api['headers'], query_string=query).status_code == 400


def test_date_filter_does_not_include_next_utc_day(api):
    client, headers = api['client'], api['headers']
    for index, stamp in enumerate(['2026-08-01T23:59:59Z', '2026-08-02T00:00:00Z']):
        assert client.post('/api/v1/trades', headers=headers,
                           json=_trade(executedAt=stamp, brokerExecutionId=f'date:{index}')).status_code == 201
    with db_conn() as conn:
        conn.execute("INSERT INTO entries (username,type,tradeType,rawDate,brokerExecutionId) "
                     "VALUES ('bot','trade','매수','2026-08-01T23:59:59.250000Z','date:fraction')")
        conn.commit()
    response = client.get('/api/v1/trades', headers=headers,
                          query_string={'from': '2026-08-01', 'to': '2026-08-01'})
    assert {row['brokerExecutionId'] for row in response.json['trades']} == {'date:0', 'date:fraction'}


@pytest.mark.parametrize('raw_date', ['2026-08-01T00:15:00Z', '2026-08-01T09:15:00+09:00',
                                     '2026-08-01T09:15:00+0900', '2026-07-31T20:15:00-04:00'])
def test_legacy_trade_offsets_are_applied_once(api, raw_date):
    with db_conn() as conn:
        conn.execute("INSERT INTO entries (username,type,tradeType,rawDate,brokerExecutionId) "
                     "VALUES ('bot','trade','매수',?,'legacy-offset')", (raw_date,))
        conn.commit()
    response = api['client'].get('/api/v1/trades', headers=api['headers'], query_string={
        'from': '2026-08-01T00:00:00Z', 'to': '2026-08-01T00:30:00Z'})
    assert len(response.json['trades']) == 1
    latest = api['client'].get('/api/v1/trades/last-sync', headers=api['headers'])
    assert latest.json['lastExecutedAt'] == '2026-08-01T00:15:00Z'


@pytest.mark.parametrize('endpoint', ['price', 'news'])
def test_external_lookup_does_not_block_same_user_writes(app, monkeypatch, endpoint):
    reading, writing = app.test_client(), app.test_client()
    _login(reading, 'victim')
    _login(writing, 'victim')
    entered, release = threading.Event(), threading.Event()

    def slow_lookup(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return ({}, False) if endpoint == 'price' else {}

    if endpoint == 'price':
        monkeypatch.setattr(web_api.prices, 'get_prices', slow_lookup)
        path, body = '/api/current_price', {'codes': ['005930']}
    else:
        monkeypatch.setattr(web_api.news, 'fetch_many', slow_lookup)
        path, body = '/api/news', {'stocks': ['삼성전자']}
    with ThreadPoolExecutor(max_workers=2) as pool:
        lookup = pool.submit(reading.post, path, json=body)
        assert entered.wait(2)
        update = pool.submit(writing.post, '/api/preferences', json={'hide_amounts': True})
        try:
            assert update.result(timeout=2).status_code == 200
        finally:
            release.set()
        assert lookup.result(timeout=2).status_code == 200
