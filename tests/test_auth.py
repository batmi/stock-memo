"""인증 — 로그인/로그아웃·가입·비밀번호 정책·잠금·재설정 요청.

대상: auth.py, users.py 의 자격증명 규칙, ratelimit 의 로그인 방어.
"""

import time


import backend_app
from app.utils import ratelimit
import trading_api
from helpers import _user_id, _ensure_user, _signup_and_login


def test_home_page_redirects_without_auth(client):
    """
    로그인하지 않은 상태에서 메인 페이지(/) 접속 시 
    로그인 페이지로 리다이렉트(302)되는지 테스트합니다.
    """
    response = client.get('/')
    assert response.status_code == 302
    assert '/login' in response.location

def test_home_page_status_with_auth(client):
    """
    세션을 조작하여 로그인한 상태를 만든 후 메인 페이지(/) 접속 시 
    정상적으로 페이지(200)가 로드되는지 테스트합니다.
    """
    client.post('/signup', data={'username': 'admin', 'password': 'Passw0rd!', 'password_confirm': 'Passw0rd!'})
    _ensure_user('testuser')  # 세션은 실제 계정이 있어야 통과한다
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'testuser'
        sess['user_id'] = _user_id(sess['username'])
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)

    response = client.get('/')
    assert response.status_code == 200

def test_logout(client):
    """
    로그아웃(/logout) 호출 시 세션이 안전하게 삭제되고 로그인 페이지로 이동하는지 테스트합니다.
    """
    _ensure_user('testuser')  # 세션은 실제 계정이 있어야 통과한다
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'testuser'
        sess['user_id'] = _user_id(sess['username'])
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)
        
    response = client.post('/logout')
    assert response.status_code == 302
    assert '/login' in response.location
    
    with client.session_transaction() as sess:
        assert 'logged_in' not in sess
        assert 'username' not in sess

def test_signup_process(client):
    """
    회원가입 폼 전송 시 정상 처리 및 중복 아이디 방지가 동작하는지 테스트합니다.
    """
    # 1. 정상 회원가입
    response1 = client.post('/signup', data={
        'username': 'new_test_user',
        'password': 'password123',
        'password_confirm': 'password123'
    })
    response_text1 = response1.get_data(as_text=True)
    assert 'successBanner' in response_text1  # 성공 배너가 화면에 렌더링되어야 함
    
    # 2. 동일한 아이디로 중복 가입 시도
    response2 = client.post('/signup', data={
        'username': 'new_test_user',
        'password': 'password456',
        'password_confirm': 'password456'
    })
    response_text2 = response2.get_data(as_text=True)
    assert 'errorBanner' in response_text2    # 에러 배너가 화면에 렌더링되어야 함

def test_login_process(client):
    """
    실제 /login 라우트에 폼 데이터를 전송하여 로그인 성공/실패 로직을 테스트합니다.
    """
    # 1. 첫 가입 (최고 관리자가 됨)
    client.post('/signup', data={
        'username': 'admin',
        'password': 'Adminpassw0rd!',
        'password_confirm': 'Adminpassw0rd!'
    })

    # 1. 비밀번호를 틀렸을 경우
    response_fail = client.post('/login', data={
        'username': 'admin',
        'password': 'wrongpassword'
    })
    assert 'errorBanner' in response_fail.get_data(as_text=True)
    
    # 2. 정상 로그인 시도
    response_success = client.post('/login', data={
        'username': 'admin',
        'password': 'Adminpassw0rd!'
    })
    assert response_success.status_code == 302
    assert response_success.location == '/'

def test_login_lockout(client):
    """
    비밀번호 5회 이상 오입력 시 IP가 차단되는지 테스트합니다.
    """
    client.post('/signup', data={'username': 'admin', 'password': 'Passw0rd!', 'password_confirm': 'Passw0rd!'})
    for _ in range(5):
        res = client.post('/login', data={'username': 'admin', 'password': 'WrongPassw0rd!'})
    
    # 6번째 시도 시 차단 메시지 확인
    res = client.post('/login', data={'username': 'admin', 'password': 'WrongPassw0rd!'})
    assert '차단' in res.get_data(as_text=True)

def test_unallowed_user_login(client):
    """
    관리자 승인이 안 된(is_allowed=0) 계정의 로그인 차단 테스트입니다.
    """
    client.post('/signup', data={'username': 'admin', 'password': 'Passw0rd!', 'password_confirm': 'Passw0rd!'})
    client.post('/signup', data={'username': 'wait_user', 'password': 'Passw0rd1!', 'password_confirm': 'Passw0rd1!'})
    res = client.post('/login', data={'username': 'wait_user', 'password': 'Passw0rd1!'})
    assert '승인이 필요' in res.get_data(as_text=True)

def test_signup_edge_cases(client):
    """회원가입 시 입력값이 누락되거나 비밀번호가 일치하지 않는 경우를 테스트합니다."""
    res1 = client.post('/signup', data={'username': '', 'password': ''})
    assert '모두 입력해주세요' in res1.get_data(as_text=True)
    
    res2 = client.post('/signup', data={'username': 'testuser', 'password': 'Passw0rd1!', 'password_confirm': 'Passw0rd2!'})
    assert '일치하지 않습니다' in res2.get_data(as_text=True)

def test_account_deletion_and_change_pw(client):
    """
    회원 탈퇴 및 비밀번호 변경 시 예외(비밀번호 오입력 등) 케이스를 테스트합니다.
    """
    client.post('/signup', data={'username': 'normal', 'password': 'Passw0rd1!', 'password_confirm': 'Passw0rd1!'})
    _ensure_user('admin')  # 세션은 실제 계정이 있어야 통과한다
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'admin'
        sess['user_id'] = _user_id(sess['username'])
        sess['is_admin'] = True
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)
    client.post('/api/admin/users/normal/toggle_allow')
    
    _ensure_user('normal')  # 세션은 실제 계정이 있어야 통과한다
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'normal'
        sess['user_id'] = _user_id(sess['username'])
        sess['is_admin'] = False
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)
        
    # 1. 비밀번호 변경 - 현재 비밀번호 오입력
    res = client.post('/api/change_password', json={'current_password': 'Nope0rd!x', 'new_password': 'Brand0New!'})
    assert res.status_code == 400
    
    # 2. 회원 탈퇴 - 비밀번호 오입력
    res = client.delete('/api/account', json={'password': 'wrong'})
    assert res.status_code == 400
    
    # 3. 회원 탈퇴 - 정상
    res = client.delete('/api/account', json={'password': 'Passw0rd1!'})
    assert res.status_code == 200
    
    # 4. 최고 관리자 탈퇴 시도 방어
    _ensure_user('admin')  # 세션은 실제 계정이 있어야 통과한다
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'admin'
        sess['user_id'] = _user_id(sess['username'])
        sess['is_admin'] = True
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)
    res = client.delete('/api/account', json={'password': 'Passw0rd!'})
    assert res.status_code == 403

def test_account_deletion_and_password_exceptions(client):
    """회원 탈퇴 및 비밀번호 변경 시 발생할 수 있는 에러 상황들을 검증합니다."""
    # 인증 전 접근 차단
    res_pw_unauth = client.post('/api/change_password', json={})
    assert res_pw_unauth.status_code == 401
    
    _ensure_user('admin')  # 세션은 실제 계정이 있어야 통과한다
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'admin'
        sess['user_id'] = _user_id(sess['username'])
        sess['is_admin'] = True
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)
        
    # 비밀번호 변경 - 빈 값 전송
    res_pw_empty = client.post('/api/change_password', json={})
    assert res_pw_empty.status_code == 400
    assert '모든 필드' in res_pw_empty.json['error']
    
    # 최고 관리자는 계정 삭제 API 접근 시 403 에러가 우선 발생하므로 일반 유저로 세션 변경
    _ensure_user('normal_user')  # 세션은 실제 계정이 있어야 통과한다
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'normal_user'
        sess['user_id'] = _user_id(sess['username'])
        sess['is_admin'] = False
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)
        
    # 계정 삭제 - 빈 비밀번호 전송
    res_del_empty = client.delete('/api/account', json={})
    assert res_del_empty.status_code == 400
    assert '비밀번호를 입력' in res_del_empty.json['error']

def test_signup_rejects_path_traversal_username(client):
    """'../../../tmp/x' 로 가입하면 안 된다 — 사용자명이 파일 경로에 그대로 쓰인다."""
    res = client.post('/signup', data={
        'username': '../../../tmp/pwned',
        'password': 'Passw0rd!', 'password_confirm': 'Passw0rd!'})
    assert '아이디는' in res.get_data(as_text=True)
    with backend_app.db_conn() as conn:
        rows = conn.execute("SELECT username FROM users").fetchall()
    assert rows == []

def test_signup_rejects_weak_password(client):
    res = client.post('/signup', data={
        'username': 'weakuser', 'password': '1234', 'password_confirm': '1234'})
    assert '8자 이상' in res.get_data(as_text=True)
    with backend_app.db_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0

def test_change_password_enforces_policy(client):
    client.post('/signup', data={'username': 'polic', 'password': 'Passw0rd!',
                                 'password_confirm': 'Passw0rd!'})
    _ensure_user('polic')  # 세션은 실제 계정이 있어야 통과한다
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'polic'
        sess['user_id'] = _user_id(sess['username'])
        sess['expires_at'] = time.time() + 3600

    res = client.post('/api/change_password',
                      json={'current_password': 'Passw0rd!', 'new_password': 'abc'})
    assert res.status_code == 400 and '8자 이상' in res.get_json()['error']

    res = client.post('/api/change_password',
                      json={'current_password': 'Passw0rd!', 'new_password': 'Passw0rd!'})
    assert res.status_code == 400 and '같습니다' in res.get_json()['error']

def test_signup_is_rate_limited(client):
    """가입 시도를 IP 당 시간 제한한다 (계정 대량 생성 방지)."""
    made = 0
    for i in range(ratelimit.SIGNUP_MAX_PER_HOUR + 2):
        res = client.post('/signup', data={
            'username': f'ratelimit{i}', 'password': 'Passw0rd!',
            'password_confirm': 'Passw0rd!'})
        if '너무 잦' in res.get_data(as_text=True):
            break
        made += 1
    assert made == ratelimit.SIGNUP_MAX_PER_HOUR

def test_password_change_invalidates_other_sessions(app, client):
    """다른 기기(다른 클라이언트)의 세션이 비밀번호 변경 즉시 끊긴다."""
    _signup_and_login(client, 'sessuser', 'Passw0rd!')

    attacker = app.test_client()
    attacker.post('/login', data={'username': 'sessuser', 'password': 'Passw0rd!'})
    assert attacker.get('/api/data').status_code == 200   # 아직 유효

    res = client.post('/api/change_password',
                      json={'current_password': 'Passw0rd!', 'new_password': 'Br4ndNew!'})
    assert res.status_code == 200
    assert res.get_json()['sessions_invalidated'] is True

    # 침입자 세션은 끊기고, 비밀번호를 바꾼 본인 세션은 유지된다
    assert attacker.get('/api/data').status_code == 401
    assert client.get('/api/data').status_code == 200

def test_password_change_can_revoke_api_keys(client):
    _signup_and_login(client, 'keyuser', 'Passw0rd!')
    trading_api.create_api_key('keyuser')

    res = client.post('/api/change_password',
                      json={'current_password': 'Passw0rd!', 'new_password': 'Br4ndNew!'})
    # 기본값은 유지 — 봇이 조용히 멈추지 않도록
    assert res.get_json()['api_keys_revoked'] == 0
    assert res.get_json()['api_keys_remaining'] == 1

    res = client.post('/api/change_password',
                      json={'current_password': 'Br4ndNew!', 'new_password': 'Th1rdOne!',
                            'revoke_api_keys': True})
    assert res.get_json()['api_keys_revoked'] == 1
    assert res.get_json()['api_keys_remaining'] == 0

# ══════════════════════════════════════════════════════════════
# 보안: 계정 단위 로그인 잠금
# ══════════════════════════════════════════════════════════════
def test_account_lockout_after_repeated_failures(app, client):
    """IP 를 바꿔도 계정 단위로 잠긴다."""
    client.post('/signup', data={'username': 'lockme', 'password': 'Passw0rd!',
                                 'password_confirm': 'Passw0rd!'})
    for i in range(ratelimit.USER_LOCKOUT_THRESHOLD):
        c = app.test_client()   # 매번 다른 세션
        c.post('/login', data={'username': 'lockme', 'password': 'Wr0ngPass!'},
               environ_overrides={'REMOTE_ADDR': f'10.0.0.{i + 1}'})

    fresh = app.test_client()
    res = fresh.post('/login', data={'username': 'lockme', 'password': 'Passw0rd!'},
                     environ_overrides={'REMOTE_ADDR': '10.9.9.9'})
    assert '잠겨 있습니다' in res.get_data(as_text=True)

# ══════════════════════════════════════════════════════════════
# 기능: 비밀번호 재설정 요청 (로그인 화면 → 관리자)
# ══════════════════════════════════════════════════════════════
def test_reset_request_is_public_and_recorded(client):
    client.post('/signup', data={'username': 'forgetful', 'password': 'Passw0rd!',
                                 'password_confirm': 'Passw0rd!'})
    # 로그인하지 않은 상태에서 호출 가능해야 한다
    res = client.post('/request_password_reset',
                      json={'username': 'forgetful', 'note': '휴대폰 바꿨어요'})
    assert res.status_code == 200
    with backend_app.db_conn() as conn:
        row = conn.execute("SELECT * FROM password_reset_requests").fetchone()
    assert row['username'] == 'forgetful'
    assert row['note'] == '휴대폰 바꿨어요'

def test_reset_request_hides_account_existence(client):
    """없는 계정이어도 응답이 같아야 한다 (계정 존재 여부 탐색 방지)."""
    client.post('/signup', data={'username': 'realuser', 'password': 'Passw0rd!',
                                 'password_confirm': 'Passw0rd!'})
    a = client.post('/request_password_reset', json={'username': 'realuser'})
    b = client.post('/request_password_reset', json={'username': 'ghostuser'})
    assert a.status_code == b.status_code == 200
    assert a.get_json() == b.get_json()
    with backend_app.db_conn() as conn:
        names = [r['username'] for r in conn.execute(
            "SELECT username FROM password_reset_requests").fetchall()]
    assert names == ['realuser']   # 없는 계정은 요청함에 쌓이지 않는다

def test_reset_request_is_deduplicated_and_counted(client):
    client.post('/signup', data={'username': 'again', 'password': 'Passw0rd!',
                                 'password_confirm': 'Passw0rd!'})
    for _ in range(3):
        client.post('/request_password_reset', json={'username': 'again'})
    with backend_app.db_conn() as conn:
        rows = conn.execute("SELECT * FROM password_reset_requests").fetchall()
    assert len(rows) == 1 and rows[0]['request_count'] == 3

def test_reset_request_is_rate_limited(client):
    client.post('/signup', data={'username': 'spamtarget', 'password': 'Passw0rd!',
                                 'password_confirm': 'Passw0rd!'})
    codes = [client.post('/request_password_reset', json={'username': 'spamtarget'}).status_code
             for _ in range(ratelimit.RESET_REQUEST_MAX_PER_HOUR + 2)]
    assert 429 in codes

def test_reset_request_requires_no_login_but_admin_list_does(client):
    """요청은 공개, 조회/해제는 관리자 전용."""
    assert client.post('/request_password_reset', json={'username': 'x'}).status_code == 200
    assert client.get('/api/admin/users').status_code == 401
    assert client.delete('/api/admin/password_resets/x').status_code == 401


def _session_cookie(response):
    return next(h for h in response.headers.getlist('Set-Cookie') if h.startswith('session='))


def test_session_cookie_is_secure_only_for_https_requests(app, monkeypatch):
    """외부(HTTPS)와 집 안(http)에서 같은 서버에 접속한다. 요청이 HTTPS 일 때만 Secure 를 붙인다.

    SESSION_COOKIE_SECURE 를 켜면 http 접속의 로그인이 풀리고, 끄면 외부 쿠키도 평문 요청에 실린다.
    """
    from werkzeug.middleware.proxy_fix import ProxyFix

    _signup_and_login(app.test_client(), 'cookieuser', 'Cookie1234!')

    def login(**kwargs):
        return app.test_client().post(
            '/login', data={'username': 'cookieuser', 'password': 'Cookie1234!'}, **kwargs)

    assert 'Secure' not in _session_cookie(login())
    assert 'Secure' in _session_cookie(login(base_url='https://localhost'))

    # 프록시가 HTTPS 를 받아 http 로 넘기는 경우 — waitress 가 아닌 서버의 ProxyFix 경로
    # (waitress 경로는 test_waitress_trusts_proxy_headers_only_from_the_proxy 가 본다)
    monkeypatch.setattr(app, 'wsgi_app', ProxyFix(app.wsgi_app, x_for=1, x_proto=1))
    assert 'Secure' in _session_cookie(login(headers={'X-Forwarded-Proto': 'https'}))
    assert 'Secure' not in _session_cookie(login(headers={'X-Forwarded-Proto': 'http'}))

    # HTTPS 로만 쓰는 운영은 예전처럼 항상 Secure 로 고정할 수 있다
    monkeypatch.setitem(app.config, 'SESSION_COOKIE_SECURE', True)
    assert 'Secure' in _session_cookie(login(headers={'X-Forwarded-Proto': 'http'}))


def _waitress(app, **options):
    """실제 waitress 서버를 띄운다. Flask 테스트 클라이언트는 waitress 를 거치지 않아
    waitress 가 프록시 헤더를 지우는 동작을 재현하지 못한다."""
    import threading
    from waitress import wasyncore
    from waitress.server import create_server

    server = create_server(app, host='127.0.0.1', port=0, threads=2, **options)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    def stop():
        # 이벤트 루프 스레드 안에서 소켓을 닫아야 select 가 닫힌 소켓을 보지 않는다.
        server.task_dispatcher.shutdown()
        server.trigger.pull_trigger(lambda: wasyncore.close_all(server._map))
        thread.join(5)

    return stop, f'http://127.0.0.1:{server.effective_port}'


def _post_login(base, username, password, **headers):
    from http.cookies import SimpleCookie
    import re
    import urllib.parse
    import urllib.request

    with urllib.request.urlopen(urllib.request.Request(base + '/login', headers=headers)) as form:
        token = re.search(r'name="csrf_token" value="([^"]+)"', form.read().decode())[1]
        cookie = SimpleCookie(form.headers['Set-Cookie'])
    # 프록시 HTTPS를 HTTP 소켓으로 흉내내므로 Secure 쿠키도 직접 전달한다.
    request = urllib.request.Request(
        base + '/login', data=urllib.parse.urlencode({
            'username': username, 'password': password, 'csrf_token': token}).encode(),
        headers={**headers, 'Cookie': f"session={cookie['session'].value}"})
    opener = urllib.request.build_opener(type('NoRedirect', (urllib.request.HTTPRedirectHandler,), {
        'redirect_request': lambda *a, **k: None}))
    try:
        response = opener.open(request)
    except urllib.error.HTTPError as redirect:  # 로그인 성공은 302 다
        response = redirect
    return response, response.read().decode()


def test_waitress_trusts_proxy_headers_only_from_the_proxy(app, monkeypatch):
    """Cloudflare Tunnel(같은 기기의 cloudflared → 127.0.0.1) 뒤에서 접속자를 구분한다.

    waitress 3.x 는 신뢰할 프록시를 지정하지 않으면 X-Forwarded-* 를 앱에 넘기기 전에 지운다.
    앱의 ProxyFix 만 걸어 두었을 때는 외부 접속이 모두 127.0.0.1 로 보여, 한 사람이 로그인을
    5번 틀리면 전원이 잠겼고 HTTPS 도 알아보지 못했다(HSTS·쿠키 Secure 가 붙지 않았다).
    """
    _signup_and_login(app.test_client(), 'proxyuser', 'Proxy1234!')
    a = {'X-Forwarded-For': '203.0.113.10', 'X-Forwarded-Proto': 'https'}
    b = {'X-Forwarded-For': '203.0.113.11', 'X-Forwarded-Proto': 'https'}

    monkeypatch.setenv('TRUSTED_PROXY_COUNT', '1')
    stop, base = _waitress(app, **backend_app.waitress_proxy_options())
    try:
        assert '실패 횟수: 1/5' in _post_login(base, 'nobody1', 'x', **a)[1]
        assert '실패 횟수: 2/5' in _post_login(base, 'nobody1', 'x', **a)[1]
        response, body = _post_login(base, 'nobody2', 'x', **b)
        assert '실패 횟수: 1/5' in body  # 다른 접속자는 따로 센다
        assert 'Strict-Transport-Security' in response.headers
        ok, _ = _post_login(base, 'proxyuser', 'Proxy1234!', **a)
        assert ok.status == 302 and 'Secure' in ok.headers['Set-Cookie']
    finally:
        stop()

    # 프록시가 아닌 상대(여기서는 127.0.0.1 이 프록시가 아니라고 지정)가 보낸 헤더는 믿지 않는다.
    ratelimit.reset_all()
    monkeypatch.setenv('TRUSTED_PROXY_ADDR', '10.0.0.9')
    stop, base = _waitress(app, **backend_app.waitress_proxy_options())
    try:
        assert '실패 횟수: 1/5' in _post_login(base, 'nobody3', 'x', **a)[1]
        response, body = _post_login(base, 'nobody4', 'x', **b)
        assert '실패 횟수: 2/5' in body  # 헤더를 꾸며도 같은 상대로 센다
        assert 'Strict-Transport-Security' not in response.headers
    finally:
        stop()


def test_waitress_proxy_options_are_off_without_trusted_proxy_count(monkeypatch):
    monkeypatch.delenv('TRUSTED_PROXY_COUNT', raising=False)
    assert backend_app.waitress_proxy_options() == {}
    monkeypatch.setenv('TRUSTED_PROXY_COUNT', '1')
    assert backend_app.waitress_proxy_options()['trusted_proxy'] == '127.0.0.1'
