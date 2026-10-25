"""내 계정 세션 표시와 연장이 서버의 절대 만료 정책을 따른다."""

from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest
from flask import request

from helpers import _buy, _login, _signup_and_login, _user_id
from app.database.db import db_conn
from app.routes import api, auth, ownership


@pytest.mark.parametrize('keep_logged_in', [False, True])
def test_me_returns_current_session_policy_without_extending_it(client, keep_logged_in):
    _login(client, 'sessionuser')
    expires = time.time() + (86400 if keep_logged_in else 120)
    with client.session_transaction() as session:
        session['expires_at'] = expires
        session['keep_logged_in'] = keep_logged_in
    response = client.get('/api/me')
    assert response.status_code == 200
    assert response.json['username'] == 'sessionuser'
    assert response.json['user_id'] == _user_id('sessionuser')
    assert response.json['expires_at'] == expires
    assert response.json['keep_logged_in'] is keep_logged_in
    assert response.headers['Cache-Control'] == 'no-store'
    assert not {'epoch', 'password_hash', 'secret_key'} & response.json.keys()
    with client.session_transaction() as session:
        assert session['expires_at'] == expires


@pytest.mark.parametrize('keep_logged_in', [False, True])
def test_ping_only_extends_regular_sessions(client, keep_logged_in):
    _login(client, 'sessionuser')
    expires = time.time() + (86400 if keep_logged_in else 120)
    with client.session_transaction() as session:
        session['expires_at'] = expires
        session['keep_logged_in'] = keep_logged_in
    before = time.time()
    response = client.post('/api/ping')
    assert response.status_code == 200
    assert response.json['status'] == 'success'
    assert response.json['user_id'] == _user_id('sessionuser')
    assert response.json['username'] == 'sessionuser'
    assert response.json['keep_logged_in'] is keep_logged_in
    assert response.headers['Cache-Control'] == 'no-store'
    if keep_logged_in:
        assert response.json['expires_at'] == expires
    else:
        assert before + 3600 <= response.json['expires_at'] <= time.time() + 3600
    with client.session_transaction() as session:
        assert session['expires_at'] == response.json['expires_at']


@pytest.mark.parametrize('path,method', [('/api/me', 'GET'), ('/api/ping', 'POST')])
def test_expired_session_cannot_be_read_or_revived(client, path, method):
    _login(client, 'sessionuser')
    with client.session_transaction() as session:
        session['expires_at'] = time.time() - 1
    response = client.open(path, method=method)
    assert response.status_code == 401
    # 늦은 응답이 새 로그인 쿠키를 지우지 않는다. 남은 만료 쿠키로 재요청해도 거절한다.
    assert 'Set-Cookie' not in response.headers
    assert client.open(path, method=method).status_code == 401


@pytest.mark.parametrize('keep_logged_in', [False, True])
def test_unchanged_me_does_not_reissue_session_cookie(client, keep_logged_in):
    _login(client, 'sessionuser')
    with client.session_transaction() as session:
        session.permanent = keep_logged_in
        session['keep_logged_in'] = keep_logged_in
    response = client.get('/api/me')
    assert response.status_code == 200
    assert 'Set-Cookie' not in response.headers


def test_me_still_updates_changed_admin_permission(client):
    _login(client, 'sessionuser')
    with db_conn() as conn:
        conn.execute("UPDATE users SET is_admin=1 WHERE username='sessionuser'")
        conn.commit()
    response = client.get('/api/me')
    assert response.status_code == 200 and response.json['is_admin'] is True
    assert 'Set-Cookie' in response.headers
    with client.session_transaction() as session:
        assert session['is_admin'] is True
    assert 'Set-Cookie' not in client.get('/api/me').headers


def test_late_read_response_cannot_undo_successful_session_extension(app, client, monkeypatch):
    _login(client, 'sessionuser')
    old_expiry = time.time() + 120
    with client.session_transaction() as session:
        session['expires_at'] = old_expiry
    reading = app.test_client()
    reading.set_cookie('session', client.get_cookie('session').value)
    entered, release = threading.Event(), threading.Event()

    def slow_prices(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return {}, False

    monkeypatch.setattr(api.prices, 'get_prices', slow_prices)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(reading.post, '/api/current_price', json={'codes': ['005930']})
        assert entered.wait(2)
        try:
            extended = client.post('/api/ping')
            assert extended.status_code == 200
            assert extended.json['expires_at'] > old_expiry + 3000
        finally:
            release.set()
        late = pending.result(timeout=2)
    assert late.status_code == 200
    assert 'Set-Cookie' not in late.headers
    assert client.get('/api/me').json['expires_at'] == extended.json['expires_at']


@pytest.mark.parametrize('path', ['/api/ping', '/api/preferences'])
def test_request_expired_while_waiting_cannot_extend_or_write(client, monkeypatch, path):
    _login(client, 'sessionuser')
    base = time.time()
    current = [base]
    with client.session_transaction() as session:
        session['expires_at'] = base + 5
    monkeypatch.setattr(auth.time, 'time', lambda: current[0])
    original_lock = ownership.lock_request

    def lock_after_expiry(username):
        original_lock(username)
        current[0] = base + 10

    monkeypatch.setattr(ownership, 'lock_request', lock_after_expiry)
    response = client.post(path, json={'defaultView': 'chart'})
    assert response.status_code == 401
    assert 'Set-Cookie' not in response.headers
    with db_conn() as conn:
        assert conn.execute("SELECT preferences FROM users WHERE username='sessionuser'").fetchone()[0] is None


@pytest.mark.parametrize('path,method,body', [
    ('/api/me', 'GET', None),
    ('/api/data', 'POST', _buy(id=81002, rawDate='2026-10-25T10:00:00')),
    ('/api/preferences', 'POST', {'defaultView': 'chart'}),
    ('/api/preferences', 'PATCH', {'defaultView': 'chart'}),
    ('/api/mappings', 'POST', {'brokers': {'264': 'old page'}, 'accounts': {}}),
    ('/api/me/api-key', 'POST', {'label': 'old page'}),
])
def test_old_page_cannot_read_or_write_another_tabs_account(client, path, method, body):
    _login(client, 'pagealpha')
    old_page_id = _user_id('pagealpha')
    _login(client, 'pagebeta')
    active_key = client.post('/api/me/api-key', json={'label': 'beta'}).json['id']
    current_cookie = client.get_cookie('session').value
    with db_conn() as conn:
        previous_user = dict(conn.execute("SELECT * FROM users WHERE username='pagebeta'").fetchone())
    response = client.open(path, method=method, json=body,
                           headers={'X-Journal-User-ID': str(old_page_id)})
    assert response.status_code == 409
    assert response.json['errorCode'] == 'ACCOUNT_CHANGED'
    assert response.headers['X-Journal-Account-Changed'] == '1'
    assert response.headers['Cache-Control'] == 'no-store'
    assert 'Set-Cookie' not in response.headers
    assert client.get_cookie('session').value == current_cookie
    with db_conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM entries').fetchone()[0] == 0
        assert dict(conn.execute("SELECT * FROM users WHERE username='pagebeta'").fetchone()) == previous_user
        assert conn.execute('SELECT COUNT(*) FROM api_keys').fetchone()[0] == 1
        assert conn.execute('SELECT revoked_at FROM api_keys WHERE id=?', (active_key,)).fetchone()[0] is None


@pytest.mark.parametrize('with_header', [False, True])
def test_matching_page_and_legacy_clients_remain_compatible(client, with_header):
    _login(client, 'sessionuser')
    headers = {'X-Journal-User-ID': str(_user_id('sessionuser'))} if with_header else {}
    assert client.get('/api/me', headers=headers).status_code == 200
    assert client.post('/api/preferences', json={'defaultView': 'chart'}, headers=headers).status_code == 200


def test_web_account_header_does_not_affect_bot_key_authentication(client):
    _login(client, 'sessionuser')
    key = client.post('/api/me/api-key', json={'label': 'bot'}).json['api_key']
    response = client.post('/api/v1/auth/token', headers={'X-API-KEY': key, 'X-Journal-User-ID': '999999'})
    assert response.status_code == 200


@pytest.mark.parametrize('failure', ['expired', 'blocked', 'epoch', 'not_logged_in'])
def test_api_rejection_clears_request_session_without_deleting_browser_cookie(client, monkeypatch, failure):
    _login(client, 'sessionuser')
    if failure == 'expired':
        with client.session_transaction() as session:
            session['expires_at'] = time.time() - 1
    elif failure == 'not_logged_in':
        with client.session_transaction() as session:
            session['logged_in'] = False
    else:
        with db_conn() as conn:
            column = 'is_allowed=0' if failure == 'blocked' else 'session_epoch=1'
            conn.execute(f"UPDATE users SET {column} WHERE username='sessionuser'")
            conn.commit()
    saved = []
    original_save = auth._SessionInterface.save_session

    def observe_save(interface, app, session, response):
        saved.append(dict(session))
        return original_save(interface, app, session, response)

    monkeypatch.setattr(auth._SessionInterface, 'save_session', observe_save)
    response = client.get('/api/me')
    assert response.status_code == 401
    assert saved == [{}]
    assert 'Set-Cookie' not in response.headers
    assert response.headers['Cache-Control'] == 'no-store'


def test_late_unauthorized_read_cannot_delete_password_change_session(app, client, monkeypatch):
    _signup_and_login(client, 'sessionuser', 'Original123!')
    reading = app.test_client()
    reading.set_cookie('session', client.get_cookie('session').value)
    entered, release = threading.Event(), threading.Event()
    original_validate = ownership.validate_owner

    def delayed_validate(conn):
        if request.path == '/api/me':
            entered.set()
            assert release.wait(5)
        original_validate(conn)

    monkeypatch.setattr(ownership, 'validate_owner', delayed_validate)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(reading.get, '/api/me')
        assert entered.wait(2)
        try:
            changed = client.post('/api/change_password', json={
                'current_password': 'Original123!', 'new_password': 'Changed456!',
            })
            assert changed.status_code == 200
            new_cookie = client.get_cookie('session').value
        finally:
            release.set()
        late = pending.result(timeout=2)
    assert late.status_code == 401
    assert 'Set-Cookie' not in late.headers
    assert client.get_cookie('session').value == new_cookie
    assert client.get('/api/me').status_code == 200


@pytest.mark.parametrize('timed_out', [False, True])
def test_old_page_logout_preserves_other_tabs_login(client, timed_out):
    _login(client, 'pagealpha')
    old_page_id = _user_id('pagealpha')
    _login(client, 'pagebeta')
    cookie = client.get_cookie('session').value
    response = client.get('/logout', query_string={'user_id': old_page_id, **({'timeout': 1} if timed_out else {})})
    assert response.status_code == 302 and response.location == '/'
    assert 'Set-Cookie' not in response.headers
    assert client.get_cookie('session').value == cookie
    assert client.get('/api/me').json['username'] == 'pagebeta'


def test_old_timeout_preserves_already_extended_session(client):
    _login(client, 'sessionuser')
    extended = client.post('/api/ping').json['expires_at']
    response = client.get('/logout', query_string={'timeout': 1, 'user_id': _user_id('sessionuser')})
    assert response.status_code == 302 and response.location == '/'
    assert 'Set-Cookie' not in response.headers
    assert client.get('/api/me').json['expires_at'] == extended


def test_timeout_rejects_expired_session_without_emitting_a_late_deletion_cookie(client):
    _login(client, 'sessionuser')
    with client.session_transaction() as session:
        session['expires_at'] = time.time() - 1
    response = client.get('/logout', query_string={'timeout': 1, 'user_id': _user_id('sessionuser')})
    assert response.status_code == 302 and response.location == '/login?timeout=1'
    assert response.headers['Cache-Control'] == 'no-store'
    assert 'Set-Cookie' not in response.headers
    assert client.get('/api/me').status_code == 401


def test_explicit_logout_of_current_page_still_removes_browser_session(client):
    _login(client, 'sessionuser')
    response = client.post('/logout', query_string={'user_id': _user_id('sessionuser')})
    assert response.status_code == 302 and response.location == '/login'
    assert 'Max-Age=0' in response.headers['Set-Cookie']
    assert client.get_cookie('session') is None
