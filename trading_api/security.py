"""봇 API 의 인증 계층 — 토큰 서명/검증, 스코프 검사, 레이트리밋.

키를 폐기하면 그 키로 이미 발급된 토큰도 즉시 죽어야 하므로, 토큰에는 key id 를
심고 **매 요청 DB 를 대조**한다. 서명만 믿으면 폐기가 TTL 만큼 늦게 듣는다.
"""

from functools import wraps

from flask import request

import ratelimit
from db import db_conn

from .common import TOKEN_TTL_SECONDS, _err
from .keys import _now_kst_str, _should_touch_last_used

# ⭐️ 슬라이딩 윈도우 구현은 ratelimit 모듈이 단독으로 소유한다. 예전에는 이 파일에
#    같은 알고리즘을 한 벌 더 두었는데, 그러면 "메모리 상한을 몇으로 두는가",
#    "거부된 요청도 창을 소비하는가" 같은 정책이 두 곳에서 따로 굳는다.
TOKEN_RATE_LIMIT = ratelimit.TOKEN_RATE_LIMIT
API_RATE_LIMIT = ratelimit.API_RATE_LIMIT


def _client_ip():
    fwd = request.headers.get('X-Forwarded-For')
    if fwd:
        return fwd.split(',')[0].strip()
    return request.remote_addr or 'unknown'


def _serializer():
    from itsdangerous import URLSafeTimedSerializer
    from flask import current_app
    return URLSafeTimedSerializer(current_app.secret_key, salt='trading-api-v1')


def require_token(*required_scopes):
    """Bearer 토큰을 검증하고 스코프를 확인하는 데코레이터.

    토큰 안의 key id 를 매 요청 DB 와 대조하므로, 키를 폐기하면 이미 발급된
    토큰도 즉시 무효가 됩니다. (서명만 검증하면 폐기해도 24시간 살아남습니다)
    """
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            from itsdangerous import BadSignature, SignatureExpired

            auth_header = request.headers.get('Authorization', '')
            if not auth_header.startswith('Bearer '):
                return _err(401, 'TOKEN_MISSING',
                            '토큰이 누락되었거나 형식이 잘못되었습니다 (Bearer <TOKEN>).')

            token = auth_header[7:].strip()
            try:
                payload = _serializer().loads(token, max_age=TOKEN_TTL_SECONDS)
            except SignatureExpired:
                return _err(401, 'TOKEN_EXPIRED',
                            '토큰이 만료되었습니다. API 키로 새 토큰을 발급받으세요.')
            except BadSignature:
                return _err(401, 'TOKEN_INVALID', '유효하지 않은 토큰입니다.')

            username = payload.get('u') if isinstance(payload, dict) else None
            key_id = payload.get('k') if isinstance(payload, dict) else None
            if not username or not key_id:
                return _err(401, 'TOKEN_INVALID', '토큰 페이로드가 유효하지 않습니다.')

            with db_conn() as conn:
                c = conn.cursor()
                c.execute(
                    "SELECT scopes, revoked_at, last_used_at FROM api_keys "
                    "WHERE id = ? AND username = ?",
                    (key_id, username))
                key_row = c.fetchone()
                if key_row is None or key_row['revoked_at']:
                    return _err(401, 'TOKEN_REVOKED',
                                '이 토큰의 API 키가 폐기되었습니다. 새 키로 다시 발급받으세요.')
                scopes = set((key_row['scopes'] or '').split())
                # ⭐️ 위 SELECT 에 last_used_at 을 함께 담았으므로 추가 쿼리 없이 판정한다.
                if _should_touch_last_used(key_row['last_used_at']):
                    c.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?",
                              (_now_kst_str(), key_id))
                    conn.commit()

            missing = [s for s in required_scopes if s not in scopes]
            if missing:
                return _err(403, 'INSUFFICIENT_SCOPE',
                            f"이 API 키에는 필요한 권한이 없습니다: {', '.join(missing)}",
                            required=list(required_scopes), granted=sorted(scopes))

            allowed, remaining, retry_after = ratelimit.api_calls.check(key_id)
            if not allowed:
                resp, status = _err(429, 'RATE_LIMITED',
                                    '요청이 너무 잦습니다. 잠시 후 다시 시도하세요.')
                resp.headers['Retry-After'] = str(retry_after)
                return resp, status

            result = f(*args, username=username, scopes=scopes, **kwargs)
            return _with_ratelimit_headers(result, remaining)

        return decorated
    return decorator


def _with_ratelimit_headers(result, remaining):
    """(response, status) 또는 response 에 레이트리밋 헤더를 붙입니다."""
    from flask import make_response
    response = make_response(result)
    response.headers['X-RateLimit-Limit'] = str(API_RATE_LIMIT[0])
    response.headers['X-RateLimit-Remaining'] = str(remaining)
    response.headers['X-RateLimit-Reset'] = str(API_RATE_LIMIT[1])
    return response

