"""실제 브라우저의 쿠키·Origin과 비밀번호 변경/로그아웃 폼을 검증한다."""

from html import escape
import threading

from playwright.sync_api import expect
import pytest
from werkzeug.serving import make_server

from app.database.db import db_conn
from helpers import _ensure_user, _signup_and_login


@pytest.fixture
def security_server(app):
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def _login_without_dashboard(page, base, username, password):
    page.goto(base + '/login')
    response = page.request.post(base + '/login', max_redirects=0, form={
        'username': username, 'password': password,
        'csrf_token': page.locator('input[name="csrf_token"]').input_value()})
    assert response.status == 302
    page.goto(base + '/logout')
    return page.locator('input[name="csrf_token"]').input_value()


@pytest.mark.parametrize('attacker', ['http://attacker.invalid/', 'http://127.0.0.1:1/'])
def test_browser_rejects_cross_origin_login_and_admin_forms(app, page, security_server, attacker):
    _signup_and_login(app.test_client(), 'victimadmin', 'VictimPassword123!')
    _signup_and_login(app.test_client(), 'attackuser', 'AttackPassword123!')
    _ensure_user('targetuser')
    token = _login_without_dashboard(page, security_server, 'victimadmin', 'VictimPassword123!')

    for path, fields in [('/login', {'username': 'attackuser', 'password': 'AttackPassword123!'}),
                         ('/api/admin/users/targetuser/toggle_allow', {})]:
        inputs = ''.join(f'<input name="{escape(k)}" value="{escape(v)}">'
                         for k, v in {**fields, 'csrf_token': token}.items())
        body = f'<form method="post" action="{security_server + path}">{inputs}<button>Send</button></form>'
        page.route(attacker, lambda route, *, body=body: route.fulfill(content_type='text/html', body=body))
        page.goto(attacker)
        with page.expect_response(lambda r, path=path: r.url == security_server + path
                                  and r.request.method == 'POST') as sent:
            page.locator('button').click()
        expected = 401 if attacker == 'http://attacker.invalid/' and path.startswith('/api/') else 403
        assert sent.value.status == expected
        page.unroute(attacker)
        assert page.request.get(security_server + '/api/me').json()['username'] == 'victimadmin'
        with db_conn() as conn:
            assert conn.execute("SELECT is_allowed FROM users WHERE username='targetuser'").fetchone()[0] == 1


def test_browser_replaces_temporary_password_and_logs_out(app, page, security_server):
    admin, user = app.test_client(), app.test_client()
    _signup_and_login(admin, 'resetadmin', 'AdminPassword123!')
    _signup_and_login(user, 'resetuser', 'UserPassword123!')
    temporary = admin.post('/api/admin/users/resetuser/reset_password').json['new_password']
    page.goto(security_server + '/login')
    page.locator('input[name="username"]').fill('resetuser')
    page.locator('input[name="password"]').fill(temporary)
    page.locator('button[type="submit"]').click()
    expect(page).to_have_url(security_server + '/change-required')
    assert page.request.get(security_server + '/api/data').status == 403
    page.locator('input[name="current_password"]').fill(temporary)
    page.locator('input[name="new_password"]').fill('ChangedPassword123!')
    page.locator('input[name="confirm_password"]').fill('ChangedPassword123!')
    page.locator('#changeRequiredForm button').click()
    expect(page).to_have_url(security_server + '/')
    assert page.request.get(security_server + '/api/data').status == 200
    page.wait_for_function("() => typeof window.logoutCurrentSession === 'function'")
    page.evaluate('window.logoutCurrentSession()')
    expect(page).to_have_url(security_server + '/login')
    assert page.request.get(security_server + '/api/me').status == 401
