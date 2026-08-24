"""공통 응답 처리 — 예외 핸들러·gzip·정적 자산 노출 범위.

대상: middleware.py 와 backend_app 의 정적 자산 라우팅.
"""

import io
import os
import re
import time

import pytest

from app.routes import api
import backend_app
import config
from app.database import entry_logic
from app.routes import middleware


# ── 전역 예외 핸들러가 정상 HTTP 응답을 삼키지 않는지 ──────────────
def test_http_errors_keep_their_status_codes(client):
    """404/405/413 이 500 으로 뭉개지면 클라이언트가 원인을 구분할 수 없다."""
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'testuser'
        sess['expires_at'] = time.time() + 3600

    assert client.get('/nope').status_code == 404
    assert client.get('/api/nope').status_code == 404
    # /api/current_price 는 POST 전용
    assert client.delete('/api/data').status_code == 405

def test_oversized_upload_returns_413_not_500(client):
    """MAX_CONTENT_LENGTH 초과는 413 이어야 한다 (예전엔 500 + 스택트레이스)."""
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'testuser'
        sess['expires_at'] = time.time() + 3600

    limit = backend_app.app.config['MAX_CONTENT_LENGTH']
    data = {'file': (io.BytesIO(b'x' * (limit + 1024)), 'big.zip')}
    res = client.post('/api/restore', data=data, content_type='multipart/form-data')
    assert res.status_code == 413

def test_unhandled_exception_does_not_leak_internals(client, monkeypatch):
    """진짜 예외는 500 이되, 내부 메시지(경로·SQL 등)를 밖으로 흘리지 않는다."""
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'testuser'
        sess['expires_at'] = time.time() + 3600

    def boom(_username):
        raise RuntimeError('내부 경로 /var/secret/journal.db')

    monkeypatch.setattr(api, 'get_user_mappings', boom)

    res = client.get('/api/mappings')
    assert res.status_code == 500
    body = res.get_data(as_text=True)
    assert '/var/secret' not in body       # 내부 정보 비노출
    assert res.get_json()['error'] == '서버 오류가 발생했습니다.'

# ── 큰 응답도 압축되는지 (예전 16MB 상한이 압축을 꺼뜨렸다) ──────────
def test_large_json_response_is_still_compressed(client, monkeypatch):
    """/api/data 가 커져도 gzip 이 꺼지면 안 된다.

    예전에는 16MB 를 넘는 순간 '메모리 보호'로 압축을 건너뛰어, 정작 절감 효과가
    가장 큰 구간에서 수십 MB 가 그대로 전송됐다.
    """
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'compressuser'
        sess['expires_at'] = time.time() + 3600

    # 상한을 낮춰서 '큰 응답' 상황을 작은 데이터로 재현한다
    monkeypatch.setattr(middleware, 'MAX_COMPRESS_BYTES', 96 * 1024 * 1024)

    body = '<p>' + ('메모 ' * 400) + '</p>'
    with backend_app.db_conn() as conn:
        c = conn.cursor()
        for i in range(200):
            entry_logic.insert_entry(c, 'compressuser', {
                'id': 1700000000000 + i, 'type': 'trade', 'stockName': '종목',
                'stockCode': '000001', 'tradeType': '매수', 'price': 100,
                'quantity': 1, 'rawDate': '2025-01-01T09:00', 'thoughts': body})
        conn.commit()

    plain = client.get('/api/data')
    gz = client.get('/api/data', headers={'Accept-Encoding': 'gzip'})
    assert plain.status_code == gz.status_code == 200
    assert gz.headers.get('Content-Encoding') == 'gzip'
    assert len(gz.get_data()) < len(plain.get_data()) / 2, '압축이 적용되지 않았다'

def test_response_above_cap_is_not_compressed(client, monkeypatch):
    """상한을 넘는 응답은 그대로 보낸다 (메모리 보호는 유지)."""
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'compressuser2'
        sess['expires_at'] = time.time() + 3600

    monkeypatch.setattr(middleware, 'MAX_COMPRESS_BYTES', 1024)  # 1KB 로 낮춤
    with backend_app.db_conn() as conn:
        c = conn.cursor()
        entry_logic.insert_entry(c, 'compressuser2', {
            'id': 1700000000001, 'type': 'trade', 'stockName': '종목',
            'tradeType': '매수', 'price': 100, 'quantity': 1,
            'rawDate': '2025-01-01T09:00', 'thoughts': '메모 ' * 500})
        conn.commit()

    res = client.get('/api/data', headers={'Accept-Encoding': 'gzip'})
    assert res.headers.get('Content-Encoding') is None

# ⭐️ 로그인한 일반 사용자라도 절대 내려받을 수 없어야 하는 경로들.
#    static_folder='.' 로 프로젝트 루트를 열어 두던 시절엔 이 전부가 그대로 받아졌다.
#    (시크릿 키가 새면 세션 쿠키를 위조할 수 있어 단순 정보 유출이 아니라 권한 상승이다)
SENSITIVE_PATHS = [
    '/.secret_key',
    '/db/journal.db',
    '/backend_app.py',
    '/trading_api/__init__.py',
    '/logs/backend_app.log',
    '/json/someuser/account_info.json',
    '/.git/config',
    '/templates/login.html',
    '/tests/conftest.py',
    '/backup/journal.db',
    '/README.md',
]


@pytest.mark.parametrize('path', SENSITIVE_PATHS)
def test_sensitive_files_are_not_served(client, path):
    """로그인한 사용자라도 프로젝트 파일을 정적 경로로 내려받을 수 없어야 한다.

    예전에는 static_folder='.' 라서 .secret_key(→세션 위조로 관리자 사칭)와
    db/journal.db(→전 사용자 기록·비밀번호 해시)가 그대로 노출됐다.
    """
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'testuser'
        sess['expires_at'] = time.time() + 3600

    assert client.get(path).status_code != 200

def test_static_assets_are_served(client):
    """반대로 static/ 안의 프런트 자산은 정상 서빙돼야 한다."""
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'testuser'
        sess['expires_at'] = time.time() + 3600

    for path in ('/static/calc.js', '/static/style.css',
                 '/static/js/01-core.js'):
        assert client.get(path).status_code == 200, path

def test_every_app_script_is_listed_and_served(client, app):
    """static/js 의 모든 조각이 페이지에 실리고 실제로 내려와야 한다.

    조각들은 ES 모듈이 아니라 전역을 공유하는 클래식 스크립트다. 하나가 빠지면
    화면 일부만 조용히 죽으므로(에러 하나 없이 버튼이 반응하지 않는다) 목록을
    손으로 관리하지 않고 폴더에서 만든다 — 그 자동 생성이 도는지 확인한다.
    """
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'testuser'
        sess['expires_at'] = time.time() + 3600

    js_dir = os.path.join(app.static_folder, 'js')
    on_disk = sorted(f for f in os.listdir(js_dir) if f.endswith('.js'))
    assert on_disk, 'static/js 에 스크립트가 없다'

    html = client.get('/').get_data(as_text=True)
    listed = re.findall(r'/static/js/([^?"]+)', html)
    assert listed == on_disk, f"페이지에 실린 순서/목록이 폴더와 다르다\n{listed}\n{on_disk}"

    for name in on_disk:
        assert client.get(f'/static/js/{name}').status_code == 200, name

def test_app_scripts_are_loaded_in_filename_order(app):
    """번호 접두사 순서가 곧 실행 순서다 — 정렬이 깨지면 전역 참조가 어긋난다."""
    with app.test_request_context('/'):
        scripts = backend_app.inject_get_mtime()['app_scripts']()
    paths = [p for p, _ in scripts]
    assert paths == sorted(paths)
    assert all(p.startswith('js/') for p in paths)
    # 모든 조각의 mtime 이 실제 값이어야 캐시 버스팅이 동작한다
    assert all(m > 0 for _, m in scripts)

def test_get_mtime_resolves_static_assets(app):
    """템플릿의 ?v= 캐시 버스팅이 실제 mtime 을 읽어야 한다 (0 이면 캐시가 안 깨진다)."""
    with app.test_request_context('/'):
        get_mtime = backend_app.inject_get_mtime()['get_mtime']
        assert get_mtime('calc.js') > 0
        assert get_mtime('style.css') > 0
        assert get_mtime('js/01-core.js') > 0
        assert get_mtime('없는파일.js') == 0
