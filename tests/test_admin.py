"""관리자 기능 — 권한 검사 구조와 개별 라우트의 동작.

앞부분은 구조를 지킨다: **새 관리자 라우트를 추가할 때 `@admin_required` 를
빠뜨리면 실패한다.** 눈으로 확인하는 규칙은 언젠가 어긋나기 때문이다.
뒷부분은 승인·삭제·비밀번호 재설정·재설정 요청 배지의 실제 동작을 본다.
"""

import time

import pytest

import auth
import backend_app
import entry_logic
import trading_api
import users


def _admin_routes(app):
    """admin 블루프린트에 등록된 (규칙, 뷰 함수) 목록."""
    return [(r.rule, app.view_functions[r.endpoint])
            for r in app.url_map.iter_rules()
            if r.endpoint.startswith('admin.')]


def test_admin_blueprint_has_routes(app):
    assert _admin_routes(app), "admin 블루프린트에 라우트가 하나도 없다"


def test_every_admin_route_is_permission_checked(app):
    """비관리자 세션으로 모든 admin 라우트를 두드려 403 인지 확인한다.

    `@admin_required` 데코레이터의 존재 여부를 소스로 추측하지 않고, 실제 응답으로
    확인한다. 데코레이터 순서를 잘못 놓아 무력화된 경우까지 잡힌다.
    """
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'ordinary'
        sess['expires_at'] = time.time() + 3600
        sess['is_admin'] = False

    checked = 0
    for rule in app.url_map.iter_rules():
        if not rule.endpoint.startswith('admin.'):
            continue
        # <target_username> 같은 자리표시자를 아무 값으로 채운다
        path = rule.rule
        for arg in rule.arguments:
            path = path.replace(f'<{arg}>', 'victim').replace(f'<int:{arg}>', '1')
            path = path.replace(f'<path:{arg}>', 'victim')

        for method in sorted(rule.methods - {'HEAD', 'OPTIONS'}):
            res = client.open(path, method=method)
            assert res.status_code == 403, (
                f"{method} {path} 가 비관리자에게 {res.status_code} 를 돌려줬다 "
                f"(@admin_required 누락 의심)")
            checked += 1

    assert checked > 0


def test_admin_routes_reject_anonymous(app):
    """로그인 자체가 없으면 세션 검사 단계에서 먼저 막힌다."""
    client = app.test_client()
    res = client.get('/api/admin/users')
    assert res.status_code in (401, 302)


def test_is_admin_reads_session(app):
    with app.test_request_context('/'):
        from flask import session
        assert auth.is_admin() is False
        session['is_admin'] = True
        assert auth.is_admin() is True


@pytest.mark.parametrize('endpoint_prefix', ['admin.'])
def test_admin_routes_live_under_api_admin(app, endpoint_prefix):
    """관리자 API 는 /api/admin/ 아래에 모아 둔다 (프런트 권한 분기와 맞추기 위해)."""
    for rule in app.url_map.iter_rules():
        if rule.endpoint.startswith(endpoint_prefix):
            assert rule.rule.startswith('/api/admin/'), rule.rule
def test_admin_api_access_control(client):
    """
    관리자 전용 API(/api/admin/*)가 일반 유저에게는 403 Forbidden을 반환하는지 확인합니다.
    """
    # 일반 유저 세션
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'normal_user'
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)
        
    res_forbidden = client.get('/api/admin/users')
    assert res_forbidden.status_code == 403
    
    # 최고 관리자 세션
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'admin'
        sess['is_admin'] = True
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)
        
    res_allowed = client.get('/api/admin/users')
    assert res_allowed.status_code == 200
    assert isinstance(res_allowed.json, list)

def test_admin_edge_cases(client):
    """
    관리자 기능의 각종 예외 상황(최고 관리자 삭제/변경 방지 등)을 테스트합니다.
    """
    client.post('/signup', data={'username': 'admin', 'password': 'Passw0rd!', 'password_confirm': 'Passw0rd!'})
    client.post('/signup', data={'username': 'user2', 'password': 'Passw0rd!', 'password_confirm': 'Passw0rd!'})
    
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'admin'
        sess['is_admin'] = True
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)
        
    # 최고 관리자 본인 삭제 시도
    res = client.delete('/api/admin/users/admin')
    assert res.status_code == 400
    
    # 최고 관리자 본인 상태 변경 시도
    res = client.post('/api/admin/users/admin/toggle_allow')
    assert res.status_code == 400
    
    # 존재하지 않는 유저 상태 변경 시도
    res = client.post('/api/admin/users/unknown_user/toggle_allow')
    assert res.status_code == 404
    
    # 임시 비밀번호 초기화 기능
    res = client.post('/api/admin/users/user2/reset_password')
    assert res.status_code == 200
    assert 'new_password' in res.json
    
    # 정상 유저 삭제
    res = client.delete('/api/admin/users/user2')
    assert res.status_code == 200

def test_admin_sees_reset_requests_and_reset_clears_them(client):
    client.post('/signup', data={'username': 'adminy', 'password': 'Passw0rd!',
                                 'password_confirm': 'Passw0rd!'})
    client.post('/signup', data={'username': 'lostpw', 'password': 'Passw0rd!',
                                 'password_confirm': 'Passw0rd!'})
    client.post('/request_password_reset', json={'username': 'lostpw', 'note': '메모'})

    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'adminy'
        sess['is_admin'] = True
        sess['expires_at'] = time.time() + 3600

    users = {u['username']: u for u in client.get('/api/admin/users').get_json()}
    assert users['lostpw']['reset_requested_at'] is not None
    assert users['lostpw']['reset_note'] == '메모'
    assert client.get('/api/me').get_json()['reset_request_count'] == 1

    client.post('/api/admin/users/lostpw/reset_password')
    with backend_app.db_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM password_reset_requests").fetchone()[0] == 0

def test_admin_can_dismiss_reset_request_without_reset(client):
    client.post('/signup', data={'username': 'adminz', 'password': 'Passw0rd!',
                                 'password_confirm': 'Passw0rd!'})
    client.post('/signup', data={'username': 'mistake', 'password': 'Passw0rd!',
                                 'password_confirm': 'Passw0rd!'})
    client.post('/request_password_reset', json={'username': 'mistake'})

    with backend_app.db_conn() as conn:
        before = conn.execute(
            "SELECT password_hash FROM users WHERE username='mistake'").fetchone()['password_hash']

    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'adminz'
        sess['is_admin'] = True
        sess['expires_at'] = time.time() + 3600

    assert client.delete('/api/admin/password_resets/mistake').status_code == 200
    with backend_app.db_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM password_reset_requests").fetchone()[0] == 0
        after = conn.execute(
            "SELECT password_hash FROM users WHERE username='mistake'").fetchone()['password_hash']
    assert before == after   # 비밀번호는 그대로

# ══════════════════════════════════════════════════════════════
# 보안: 관리자 임시 비밀번호
# ══════════════════════════════════════════════════════════════
def test_admin_reset_issues_strong_temp_password(client):
    client.post('/signup', data={'username': 'adminx', 'password': 'Passw0rd!',
                                 'password_confirm': 'Passw0rd!'})
    client.post('/signup', data={'username': 'victim', 'password': 'Passw0rd!',
                                 'password_confirm': 'Passw0rd!'})
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'adminx'
        sess['is_admin'] = True
        sess['expires_at'] = time.time() + 3600

    res = client.post('/api/admin/users/victim/reset_password')
    assert res.status_code == 200
    temp = res.get_json()['new_password']
    assert len(temp) == 12                      # 예전 uuid4[:8] 대비 강화
    assert not set(temp) & set('0O1lI')         # 혼동 문자 제외

    with backend_app.db_conn() as conn:
        row = conn.execute("SELECT must_change_password FROM users WHERE username='victim'").fetchone()
    assert row['must_change_password'] == 1     # 다음 로그인에서 변경 강제

# ══════════════════════════════════════════════════════════════
# 초기화는 비밀번호만 바꾼다 (다른 데이터 보존)
# ══════════════════════════════════════════════════════════════
def test_admin_reset_only_touches_credentials(client):
    """비밀번호 초기화가 매매기록·API 키·환경설정을 건드리지 않는다."""
    client.post('/signup', data={'username': 'keepdata', 'password': 'Passw0rd!',
                                 'password_confirm': 'Passw0rd!'})
    client.post('/signup', data={'username': 'adminq', 'password': 'Passw0rd!',
                                 'password_confirm': 'Passw0rd!'})
    with backend_app.db_conn() as conn:
        c = conn.cursor()
        for i in range(3):
            entry_logic.insert_entry(c, 'keepdata', {
                'id': 7000 + i, 'type': 'trade', 'stockName': f'종목{i}',
                'tradeType': '매수', 'price': 100 * (i + 1), 'quantity': 5,
                'rawDate': '2025-03-01T09:00', 'thoughts': f'<p>메모{i}</p>'})
        c.execute("UPDATE users SET preferences = ? WHERE username = ?",
                  ('{"showCurrentPrice": true}', 'keepdata'))
        conn.commit()
    trading_api.create_api_key('keepdata')

    def snapshot():
        with backend_app.db_conn() as conn:
            entries = [tuple(r) for r in conn.execute(
                "SELECT id, stockName, price, quantity, thoughts FROM entries "
                "WHERE username='keepdata' ORDER BY id").fetchall()]
            keys = [tuple(r) for r in conn.execute(
                "SELECT id, key_hash, revoked_at FROM api_keys WHERE username='keepdata'").fetchall()]
            prefs = conn.execute(
                "SELECT preferences FROM users WHERE username='keepdata'").fetchone()['preferences']
        return entries, keys, prefs

    before = snapshot()

    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'adminq'
        sess['is_admin'] = True
        sess['expires_at'] = time.time() + 3600
    assert client.post('/api/admin/users/keepdata/reset_password').status_code == 200

    after = snapshot()
    assert before[0] == after[0], '매매기록이 변경됐다'
    assert before[1] == after[1], 'API 키가 변경됐다'
    assert before[2] == after[2], '환경설정이 변경됐다'

# ══════════════════════════════════════════════════════════════
# 배지: 처리하면 저절로 사라진다
# ══════════════════════════════════════════════════════════════
def test_badge_counts_clear_after_handling(client):
    """승인·초기화를 끝내면 /api/me 의 배지 근거 수치가 0 이 된다."""
    client.post('/signup', data={'username': 'adminb', 'password': 'Passw0rd!',
                                 'password_confirm': 'Passw0rd!'})
    client.post('/signup', data={'username': 'newbie', 'password': 'Passw0rd!',
                                 'password_confirm': 'Passw0rd!'})
    client.post('/request_password_reset', json={'username': 'newbie', 'note': '메모'})

    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'adminb'
        sess['is_admin'] = True
        sess['expires_at'] = time.time() + 3600

    me = client.get('/api/me').get_json()
    assert me['pending_count'] == 1 and me['reset_request_count'] == 1

    client.post('/api/admin/users/newbie/toggle_allow')     # 가입 승인
    client.post('/api/admin/users/newbie/reset_password')   # 초기화 처리

    me = client.get('/api/me').get_json()
    assert me['pending_count'] == 0 and me['reset_request_count'] == 0

def test_admin_list_exposes_reset_note_for_display(client):
    """관리자 표가 메모를 그릴 수 있도록 API 가 메모를 실어 보낸다."""
    client.post('/signup', data={'username': 'adminc', 'password': 'Passw0rd!',
                                 'password_confirm': 'Passw0rd!'})
    client.post('/signup', data={'username': 'noteuser', 'password': 'Passw0rd!',
                                 'password_confirm': 'Passw0rd!'})
    client.post('/request_password_reset',
                json={'username': 'noteuser', 'note': '폰을 바꿔서 비밀번호를 잊었습니다'})

    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'adminc'
        sess['is_admin'] = True
        sess['expires_at'] = time.time() + 3600

    users = {u['username']: u for u in client.get('/api/admin/users').get_json()}
    row = users['noteuser']
    assert row['reset_note'] == '폰을 바꿔서 비밀번호를 잊었습니다'
    assert row['reset_requested_at']
    assert row['reset_request_count'] == 1


# ---------------------------------------------------------------------------
# 정적 서빙 격리 (static_folder='.' 로 루트 전체가 열려 있던 회귀 방지)
# ---------------------------------------------------------------------------

SENSITIVE_PATHS = [
    '/.secret_key',
    '/db/journal.db',
    '/backend_app.py',
    '/trading_api.py',
    '/logs/backend_app.log',
    '/json/someuser/account_info.json',
    '/.git/config',
    '/templates/login.html',
    '/tests/conftest.py',
    '/backup/journal.db',
    '/README.md',
]
