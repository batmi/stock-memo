"""관리자 블루프린트 — 모든 라우트가 권한 검사를 거치는지.

개별 라우트의 동작은 tests/test_backend_app.py 가 검증한다. 여기서는 구조를
지킨다: **새 관리자 라우트를 추가할 때 `@admin_required` 를 빠뜨리면 실패한다.**
눈으로 확인하는 규칙은 언젠가 어긋나기 때문이다.
"""

import time

import pytest

import admin
import auth


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
