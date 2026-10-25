"""내 계정 세션 표시와 연장이 서버의 절대 만료 정책을 따른다."""

import time

import pytest

from helpers import _login, _user_id


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
    assert client.open(path, method=method).status_code == 401
    with client.session_transaction() as session:
        assert not session.get('logged_in')
