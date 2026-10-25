"""쿠키 인증 요청에 세션 토큰과 동일 출처 검증을 적용한다."""

import secrets
from urllib.parse import urlsplit

from flask import jsonify, request, session


def csrf_token():
    if not session.get('csrf_token'):
        session['csrf_token'] = secrets.token_urlsafe(32)
    return session['csrf_token']


def _origin(url):
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            return None
        return parsed.scheme, parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == 'https' else 80)
    except ValueError:
        return None


def check_csrf():
    if request.method in ('GET', 'HEAD', 'OPTIONS') or request.blueprint == 'trading_api':
        return None
    # 토큰이 없거나 다른 출처에서 온 요청은 본문/DB를 처리하기 전에 거부한다.
    source = request.headers.get('Origin')
    if source is None:
        source = request.headers.get('Referer')
    foreign = source is not None and _origin(source) != _origin(request.host_url)
    foreign = foreign or request.headers.get('Sec-Fetch-Site') == 'cross-site'
    if source is None and request.headers.get('Sec-Fetch-Site') == 'same-site':
        foreign = True
    supplied = request.headers.get('X-CSRF-Token')
    if supplied is None and request.blueprint == 'auth':
        supplied = request.form.get('csrf_token')
    expected = session.get('csrf_token')
    valid = (isinstance(expected, str) and isinstance(supplied, str) and len(supplied) <= 128
             and secrets.compare_digest(expected.encode(), supplied.encode()))
    if foreign or not valid:
        response = jsonify(error='요청을 확인할 수 없습니다. 화면을 새로고침한 후 다시 시도해주세요.',
                           errorCode='CSRF_INVALID')
        response.status_code = 403
        response.headers['Cache-Control'] = 'no-store'
        return response
    return None


def register(app):
    app.before_request(check_csrf)
    app.context_processor(lambda: {'csrf_token': csrf_token})

    @app.after_request
    def private_forms(response):
        if response.mimetype == 'text/html':
            response.headers['Cache-Control'] = 'no-store'
        return response
