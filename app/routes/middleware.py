"""응답 미들웨어 — Access 로그, 보안 헤더, 캐시 헤더, gzip 압축, 전역 예외 처리.

라우트와 섞여 있으면 "요청 하나가 어떤 처리를 거치는가"를 파악하려고 2,000줄짜리
파일을 훑어야 했다. 모든 응답에 공통으로 걸리는 것만 여기 모은다.

`register(app)` 한 번으로 전부 붙는다. 등록 순서가 곧 실행 순서의 역순이라
(Flask 의 after_request 는 나중에 등록된 것이 먼저 실행된다) 순서를 바꾸지 않는다.
"""

import gzip
import io
import os
import threading

from flask import jsonify, request, session
from werkzeug.exceptions import HTTPException

from app.utils import applog

# ⭐️ 텍스트 응답 gzip 압축: 초기 로딩 전송량 절감
#    (/api/data 수 MB JSON, script.js 350KB→60KB 등)
#    이미지/ZIP 등 이미 압축된 형식은 Content-Type 으로 제외한다.
COMPRESSIBLE_TYPES = ('application/json', 'text/html', 'text/css',
                      'application/javascript', 'text/javascript', 'text/plain')

# ⭐️ 지나치게 큰 응답만 압축을 건너뛴다.
#    예전 상한(16MB)은 정작 압축이 가장 필요한 구간을 잘라냈다. 기록이 쌓여
#    /api/data 가 16MB 를 넘는 순간 압축이 꺼져 17MB 가 통째로 전송됐다.
#    실측: 15.4MB → 75KB(99.5% 절감), 압축 비용 27ms. 전송량 절감이 압도적이다.
#    (jsonify 가 이미 전체 바이트를 메모리에 들고 있으므로 압축은 버퍼 하나를
#     더 쓰는 정도다. 상한은 그 '한 벌 더'가 부담되는 크기에 둔다)
MAX_COMPRESS_BYTES = 96 * 1024 * 1024

# ⭐️ 정적 자산(js/css) 압축 결과 메모리 캐시 — 라즈베리파이의 느린 CPU 에서
#    script.js 를 요청마다 매번 gzip 압축하면 요청당 수십 ms 를 소모한다.
#    파일 mtime 을 키에 포함해, 파일이 바뀌지 않는 한 최초 1회만 압축한다.
_static_gzip_cache = {}  # request.path -> (mtime, compressed_bytes | None)
_static_gzip_lock = threading.Lock()


# ⭐️ 콘텐츠 보안 정책(CSP).
#    화면 코드에 인라인 핸들러(onclick=)와 인라인 <script> 가 남아 있어 script-src 에
#    'unsafe-inline' 이 필요하다. 그래도 CSP 를 거는 값어치는 충분하다 —
#    스크립트 출처를 우리 서버와 명시된 CDN 으로 못박고, <base> 조작·플러그인
#    삽입·타 사이트 iframe 임베드를 차단한다.
#    (인라인 핸들러를 걷어내면 'unsafe-inline' 을 떼고 nonce 로 넘어갈 수 있다)
_CSP_DIRECTIVES = [
    "default-src 'self'",
    # Chart.js / Sortable / flatpickr / Quill / ExcelJS
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdn.quilljs.com https://cdnjs.cloudflare.com",
    # flatpickr 테마 + 화면 곳곳의 style="" 속성
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdn.quilljs.com",
    # 첨부 이미지는 /uploads/ (self), Quill 편집 중 이미지는 data:/blob:
    "img-src 'self' data: blob: https://ssl.gstatic.com",
    "font-src 'self' data:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'self'",
]
CONTENT_SECURITY_POLICY = '; '.join(_CSP_DIRECTIVES)


def register(app):
    """모든 미들웨어를 앱에 등록한다."""

    # ⭐️ Flask 요청/응답 라이프사이클 내에서 직접 Access 로그를 기록
    @app.after_request
    def log_request_info(response):
        username = session.get('username') or "Guest"
        # ⭐️ 실패는 절대 숨기지 않는다. 하트비트가 401/500 으로 돌아서는 순간이
        #    바로 '봇 연동이 끊겼다'는 신호라, 그때는 콘솔에 보여야 한다.
        quiet = (request.path in applog.QUIET_CONSOLE_PATHS
                 and response.status_code < 400)
        app.logger.info(
            f"[{username}] {request.method} {request.path} {response.status_code}",
            extra={'quiet_console': quiet})
        return response

    @app.after_request
    def add_security_headers(response):
        # 클릭재킹 방지: 다른 사이트의 iframe 내부에서 렌더링되지 않도록 차단
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        # MIME 스니핑 방지: 브라우저가 파일 형식을 추측하지 않도록 강제
        response.headers['X-Content-Type-Options'] = 'nosniff'
        # XSS 필터링 활성화 (구형 브라우저 지원용)
        response.headers['X-XSS-Protection'] = '1; mode=block'
        # 외부로 나가는 링크에 전체 URL(쿼리 포함)을 넘기지 않는다
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers.setdefault('Content-Security-Policy', CONTENT_SECURITY_POLICY)
        # HTTPS 로 들어온 요청에만 HSTS 를 건다.
        # (로컬 http 접속에 걸면 그 브라우저는 이후 http 로 못 붙는다)
        if request.is_secure:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        return response

    # ⭐️ 정적 자산 캐시 헤더: 정적 파일(js/css/이미지)에 단기 캐시 부여
    @app.after_request
    def add_cache_headers(response):
        if request.endpoint == 'static' or request.path.startswith('/uploads/'):
            # ⭐️ 200 정상 응답에만 캐시를 부여한다.
            #    비로그인 상태에서 정적 파일을 요청하면 before_request 가
            #    302(→/login) 를 반환하는데, 이때도 endpoint 는 여전히 'static' 이다.
            #    이 302 리다이렉트에 장기 캐시가 붙으면, 이후 로그인해도 브라우저가
            #    캐시된 "로그인 리다이렉트"를 calc.js 대신 돌려줘 로그인 HTML 을 JS 로
            #    실행하게 되고(Can't find variable: applyTradeToHolding) 데이터
            #    렌더링이 영구히 막힌다. 따라서 리다이렉트/에러 응답은 캐시하지 않는다.
            if response.status_code == 200:
                # setdefault 는 Flask 가 정적 파일에 기본으로 넣는 'no-cache' 때문에
                # 항상 무시되어 캐시가 전혀 적용되지 않았다. 명시적으로 덮어쓴다.
                # (js/css 는 ?v=수정시각 쿼리로 버전 관리되므로 1시간 캐시가 안전하다)
                response.headers['Cache-Control'] = 'public, max-age=3600'
            else:
                response.headers['Cache-Control'] = 'no-store'
        return response

    @app.after_request
    def compress_response(response):
        return _compress(app, response)

    @app.errorhandler(Exception)
    def handle_exception(e):
        # ⭐️ 404/405/413 같은 정상적인 HTTP 응답까지 삼키지 않는다.
        #    이 핸들러는 Exception 을 받으므로 werkzeug 의 HTTPException 도 걸려들어,
        #    예전에는 "없는 페이지"가 404 대신 500 으로 나가고 업로드 크기 초과가
        #    413 대신 500 이 됐다. 클라이언트가 '잘못된 요청'과 '서버 장애'를 구분할 수
        #    없었고, 평범한 404 한 건마다 스택 트레이스가 ERROR 로 쌓였다.
        if isinstance(e, HTTPException):
            return e

        app.logger.error(f"Unhandled Exception: {e}", exc_info=True)
        # ⭐️ 예외 메시지를 그대로 내보내면 DB 경로·SQL 등 내부 정보가 새어 나간다.
        #    원인은 서버 로그(위 exc_info)에만 남기고, 클라이언트에는 일반 문구를 준다.
        return jsonify(error="서버 오류가 발생했습니다."), 500


# ---------------------------------------------------------------------------
# gzip
# ---------------------------------------------------------------------------

def _gzip_if_smaller(data):
    """압축이 실제로 이득일 때만 압축 결과를 돌려준다 (아니면 None)."""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode='wb', compresslevel=6, mtime=0) as gz:
        gz.write(data)
    compressed = buf.getvalue()
    return compressed if len(compressed) < len(data) else None


def _compress(app, response):
    if (response.status_code != 200
            or request.method == 'HEAD'
            or response.headers.get('Content-Encoding')
            or 'gzip' not in (request.headers.get('Accept-Encoding') or '').lower()):
        return response
    content_type = (response.content_type or '').split(';')[0].strip().lower()
    if content_type not in COMPRESSIBLE_TYPES:
        return response
    if response.content_length is not None and response.content_length > MAX_COMPRESS_BYTES:
        return response

    # ⭐️ 정적 파일은 mtime 기반 캐시를 먼저 조회 (파일 읽기·압축 모두 생략)
    static_mtime = None
    if request.endpoint == 'static':
        # 경로가 아니라 라우트가 넘겨준 filename 으로 실제 파일을 찾는다.
        # (request.path 를 잘라 쓰면 static_url_path 를 바꾸는 순간 조용히 어긋난다)
        try:
            static_mtime = os.path.getmtime(
                os.path.join(app.static_folder, (request.view_args or {})['filename']))
        except (OSError, KeyError):
            static_mtime = None
        if static_mtime is not None:
            with _static_gzip_lock:
                cached = _static_gzip_cache.get(request.path)
            if cached is not None and cached[0] == static_mtime:
                compressed = cached[1]
                if compressed is None:
                    return response  # 이전에 "압축 이득 없음"으로 판정된 자산
                response.direct_passthrough = False
                _apply_gzip(response, compressed)
                return response

    try:
        # 정적 파일(js/css)은 파일 스트리밍(direct_passthrough) 모드라 그대로는
        # get_data() 가 실패한다. 텍스트 자산은 크기가 작으므로 버퍼로 전환해 압축한다.
        response.direct_passthrough = False
        data = response.get_data()
    except Exception:
        return response
    compressed = _gzip_if_smaller(data)

    if static_mtime is not None:
        with _static_gzip_lock:
            _static_gzip_cache[request.path] = (static_mtime, compressed)

    if compressed is None:
        return response
    _apply_gzip(response, compressed)
    return response


def _apply_gzip(response, compressed):
    response.set_data(compressed)
    response.headers['Content-Encoding'] = 'gzip'
    response.headers['Content-Length'] = str(len(compressed))
    # 프록시/브라우저 캐시가 인코딩별로 응답을 구분하도록 Vary 지정
    existing_vary = response.headers.get('Vary', '')
    if 'accept-encoding' not in existing_vary.lower():
        response.headers['Vary'] = (
            (existing_vary + ', Accept-Encoding') if existing_vary else 'Accept-Encoding')
