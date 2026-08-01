"""시스템 트레이딩 봇 연동 REST API — Universal Trading History API v2.

UniversalTradingHistoryAPI.json 의 계약을 그대로 구현합니다.

설계 원칙
---------
1. **유실 금지**: 봇이 보낸 체결은 무결성 검증에 걸려도 저장한다. 400 으로 되돌리면
   봇은 재시도해도 계속 실패하고 그 체결은 영구히 사라진다. 대신 needsReview 로
   표시해 웹에서 사람이 확인하게 한다. (웹 UI 직접 입력은 종전대로 차단)
2. **멱등**: brokerExecutionId 에 UNIQUE 제약을 걸고 INSERT OR IGNORE 로 경합을 막는다.
   중복 재전송은 200 + 기존 기록 반환. 그래서 타임아웃 시 무조건 재전송해도 안전하다.
3. **모의/실거래 분리**: isSimulated 를 별도 컬럼으로 두고 기본 조회에서 제외한다.
4. **최소 권한**: API 키는 해시로만 저장하고 스코프를 부여한다. 키를 폐기하면 그 키로
   발급된 토큰도 즉시 무효가 된다(토큰에 key id 를 심고 매 요청 DB 대조).

backend_app 에 직접 의존하지 않고 init_app() 으로 주입받은 공급자만 사용합니다.
"""

import hashlib
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Blueprint, jsonify, request

import entry_logic

try:  # 표준 tz 데이터베이스 (거래소 현지 거래일 산출용)
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.8 이하
    ZoneInfo = None


bp = Blueprint('trading_api', __name__, url_prefix='/api/v1')

API_VERSION = '2.0.0'
TOKEN_TTL_SECONDS = 86400
MAX_BATCH_ITEMS = 500
KST = timezone(timedelta(hours=9))

# ── 스코프 ─────────────────────────────────────────────────────────────
SCOPE_TRADES_WRITE = 'trades:write'
SCOPE_TRADES_READ = 'trades:read'
SCOPE_BOT_WRITE = 'bot:write'
ALL_SCOPES = (SCOPE_TRADES_WRITE, SCOPE_TRADES_READ, SCOPE_BOT_WRITE)
DEFAULT_SCOPES = ' '.join(ALL_SCOPES)

API_KEY_PREFIX = 'skm_'

# ── 주입 의존성 ────────────────────────────────────────────────────────
_deps = {
    'db_conn': None,          # contextmanager -> sqlite3.Connection
    'get_user_mappings': None,  # (username) -> dict
    'invalidate_cache': None,   # (username) -> None
    'logger': None,
}


def init_app(app, *, db_conn, get_user_mappings, invalidate_cache, logger=None):
    """블루프린트를 앱에 등록하고 외부 의존성을 주입합니다."""
    _deps['db_conn'] = db_conn
    _deps['get_user_mappings'] = get_user_mappings
    _deps['invalidate_cache'] = invalidate_cache
    _deps['logger'] = logger or app.logger
    app.register_blueprint(bp)


def _db():
    return _deps['db_conn']()


def _log():
    return _deps['logger']


# ══════════════════════════════════════════════════════════════════════
# 스키마
# ══════════════════════════════════════════════════════════════════════

# entries 확장 컬럼 (컬럼명 -> 타입). init_db 에서 ALTER TABLE 로 안전 추가.
ENTRY_COLUMN_DDL = [
    ('isSimulated', 'INTEGER DEFAULT 0'),
    ('tradeStatus', 'TEXT'),
    ('confidence', 'TEXT'),
    ('orderOrigin', 'TEXT'),
    ('source', 'TEXT'),
    ('orderId', 'TEXT'),
    ('originalOrderId', 'TEXT'),
    ('realizedPnl', 'REAL'),
    ('realizedPnlRate', 'REAL'),
    ('fee', 'REAL'),
    ('tax', 'REAL'),
    ('strategyScore', 'REAL'),
    ('stopLossRate', 'REAL'),
    ('executedAtUtc', 'TEXT'),
    ('tradeDate', 'TEXT'),
    ('needsReview', 'INTEGER DEFAULT 0'),
]


def migrate_schema(conn):
    """entries 확장 컬럼 / api_keys 테이블 / 멱등 UNIQUE 인덱스를 준비합니다.

    backend_app.init_db() 에서 호출합니다. 이미 적용된 경우 아무 일도 하지 않습니다.
    """
    c = conn.cursor()

    for name, coltype in ENTRY_COLUMN_DDL:
        try:
            c.execute(f"ALTER TABLE entries ADD COLUMN {name} {coltype}")
        except sqlite3.OperationalError:
            pass  # 이미 존재

    c.execute('''
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            key_hash TEXT NOT NULL UNIQUE,
            key_prefix TEXT NOT NULL,
            label TEXT,
            scopes TEXT NOT NULL,
            created_at TEXT,
            last_used_at TEXT,
            revoked_at TEXT
        )
    ''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(username)")
    conn.commit()

    # ⭐️ 멱등키 UNIQUE 제약. 기존 idx_entries_exec_id 는 비유니크라 동시 요청이
    #    check-then-insert 사이를 파고들면 중복이 그대로 들어갔다.
    #    빈 문자열/NULL(수동 입력 기록)은 제약 대상에서 제외한다.
    try:
        c.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_exec_unique "
            "ON entries(username, brokerExecutionId) "
            "WHERE brokerExecutionId IS NOT NULL AND brokerExecutionId != ''"
        )
    except sqlite3.OperationalError as e:
        # 이미 중복 데이터가 있어 UNIQUE 를 걸 수 없는 경우 — 정리 후 재시도
        _dedupe_execution_ids(conn)
        try:
            c.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_exec_unique "
                "ON entries(username, brokerExecutionId) "
                "WHERE brokerExecutionId IS NOT NULL AND brokerExecutionId != ''"
            )
        except sqlite3.OperationalError:
            if _deps['logger']:
                _deps['logger'].error(f"⚠️ brokerExecutionId UNIQUE 인덱스 생성 실패: {e}")

    # 확장 컬럼 조회 성능
    c.execute("CREATE INDEX IF NOT EXISTS idx_entries_user_src ON entries(username, source)")
    conn.commit()

    _migrate_legacy_api_keys(conn)


def _dedupe_execution_ids(conn):
    """UNIQUE 인덱스 적용 전에 남아 있던 중복 멱등키를 정리합니다 (가장 오래된 1건만 보존)."""
    c = conn.cursor()
    c.execute('''
        DELETE FROM entries WHERE id IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY username, brokerExecutionId ORDER BY id
                ) AS rn
                FROM entries
                WHERE brokerExecutionId IS NOT NULL AND brokerExecutionId != ''
            ) WHERE rn > 1
        )
    ''')
    removed = c.rowcount
    conn.commit()
    if removed and _deps['logger']:
        _deps['logger'].info(f"🔄 중복 brokerExecutionId 기록 {removed}건을 정리했습니다.")


def _migrate_legacy_api_keys(conn):
    """users.api_key 에 평문으로 저장돼 있던 기존 키를 api_keys 테이블로 이관합니다.

    이관 후 평문은 즉시 지웁니다. 사용자는 기존 키를 그대로 계속 쓸 수 있습니다.
    """
    c = conn.cursor()
    try:
        c.execute("SELECT username, api_key FROM users WHERE api_key IS NOT NULL AND api_key != ''")
    except sqlite3.OperationalError:
        return
    rows = c.fetchall()
    if not rows:
        return

    now = _now_kst_str()
    migrated = 0
    for row in rows:
        raw = row['api_key']
        try:
            c.execute(
                "INSERT OR IGNORE INTO api_keys "
                "(username, key_hash, key_prefix, label, scopes, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (row['username'], _hash_key(raw), _display_prefix(raw),
                 '기존 키(자동 이관)', DEFAULT_SCOPES, now),
            )
            migrated += 1
        except sqlite3.Error:
            continue
    c.execute("UPDATE users SET api_key = NULL")
    conn.commit()
    if migrated and _deps['logger']:
        _deps['logger'].info(
            f"🔐 기존 API 키 {migrated}건을 해시 저장소로 이관하고 평문을 삭제했습니다.")


# ══════════════════════════════════════════════════════════════════════
# 공통 유틸
# ══════════════════════════════════════════════════════════════════════

def _now_kst():
    return datetime.now(KST)


def _now_kst_str():
    return _now_kst().strftime('%Y-%m-%d %H:%M:%S')


def _err(status, code, message, **details):
    body = {'error': message, 'errorCode': code}
    if details:
        body['details'] = details
    return jsonify(body), status


def _hash_key(raw):
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _display_prefix(raw):
    """키 식별용 앞자리. 전체 키는 저장하지 않으므로 목록에서 이걸로 구분한다."""
    return raw[:12] if len(raw) > 12 else raw


def _generate_api_key():
    return API_KEY_PREFIX + secrets.token_urlsafe(32)


# ── 레이트 리밋 (프로세스 내 슬라이딩 윈도우) ─────────────────────────
class _RateLimiter:
    """단일 프로세스(waitress) 기준 슬라이딩 윈도우 카운터.

    다중 프로세스로 확장할 때는 Redis 등 공유 저장소로 교체해야 합니다.
    """

    def __init__(self):
        self._hits = {}
        self._lock = threading.Lock()

    def check(self, key, limit, window):
        """(허용여부, 남은횟수, 재시도까지 초)"""
        now = time.time()
        with self._lock:
            bucket = [t for t in self._hits.get(key, []) if now - t < window]
            if len(bucket) >= limit:
                self._hits[key] = bucket
                retry_after = int(window - (now - bucket[0])) + 1
                return False, 0, retry_after
            bucket.append(now)
            self._hits[key] = bucket
            # 오래된 키 정리 (메모리 누수 방지)
            if len(self._hits) > 5000:
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] > window]:
                    self._hits.pop(k, None)
            return True, limit - len(bucket), 0

    def reset(self):
        with self._lock:
            self._hits.clear()


_limiter = _RateLimiter()

TOKEN_RATE_LIMIT = (10, 300)   # IP 당 5분에 10회 — API 키 무차별 대입 차단
API_RATE_LIMIT = (600, 60)     # 키 당 1분에 600회


def _client_ip():
    fwd = request.headers.get('X-Forwarded-For')
    if fwd:
        return fwd.split(',')[0].strip()
    return request.remote_addr or 'unknown'


# ── 인증 ──────────────────────────────────────────────────────────────
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

            with _db() as conn:
                c = conn.cursor()
                c.execute(
                    "SELECT scopes, revoked_at FROM api_keys WHERE id = ? AND username = ?",
                    (key_id, username))
                key_row = c.fetchone()
                if key_row is None or key_row['revoked_at']:
                    return _err(401, 'TOKEN_REVOKED',
                                '이 토큰의 API 키가 폐기되었습니다. 새 키로 다시 발급받으세요.')
                scopes = set((key_row['scopes'] or '').split())
                c.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?",
                          (_now_kst_str(), key_id))
                conn.commit()

            missing = [s for s in required_scopes if s not in scopes]
            if missing:
                return _err(403, 'INSUFFICIENT_SCOPE',
                            f"이 API 키에는 필요한 권한이 없습니다: {', '.join(missing)}",
                            required=list(required_scopes), granted=sorted(scopes))

            allowed, remaining, retry_after = _limiter.check(
                f'api:{key_id}', API_RATE_LIMIT[0], API_RATE_LIMIT[1])
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


# ══════════════════════════════════════════════════════════════════════
# 값 정규화
# ══════════════════════════════════════════════════════════════════════

# 거래소 -> 현지 시간대. 거래일(tradeDate) 귀속에 쓴다.
# 미국은 프리(04:00)~애프터(20:00)가 모두 같은 ET 날짜이므로 ET 기준 날짜가 곧 거래일이다.
_EXCHANGE_TZ = {
    'KRX': 'Asia/Seoul', 'NXT': 'Asia/Seoul', 'KOSPI': 'Asia/Seoul',
    'KOSDAQ': 'Asia/Seoul', 'KONEX': 'Asia/Seoul',
    'NASDAQ': 'America/New_York', 'NAS': 'America/New_York',
    'NYSE': 'America/New_York', 'NYS': 'America/New_York',
    'AMEX': 'America/New_York', 'AMS': 'America/New_York',
    'BAQ': 'America/New_York', 'BAY': 'America/New_York', 'BAA': 'America/New_York',
    'TSE': 'Asia/Tokyo', 'HKEX': 'Asia/Hong_Kong', 'SEHK': 'Asia/Hong_Kong',
    'SSE': 'Asia/Shanghai', 'SZSE': 'Asia/Shanghai',
}

_TRADE_CLASS_MAP = {
    1: '장기투자', 2: '중기투자', 3: '단기스윙', 4: '단타(스캘핑)',
    5: '배당투자', 6: '공모주', 7: '시스템', 8: '기타',
}
_VALID_TRADE_CLASSES = set(_TRADE_CLASS_MAP.values())

_VALID_STATUS = {'FILLED', 'PARTIALLY_FILLED', 'CANCELED', 'SUBMITTED'}
_VALID_CONFIDENCE = {'CONFIRMED', 'ESTIMATED'}
_VALID_ORIGIN = {'AUTO', 'MANUAL', 'RESERVED', 'EXTERNAL', 'BACKFILL'}


class ValidationError(Exception):
    def __init__(self, code, message, field=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field


def _parse_executed_at(value):
    """체결 시각 문자열을 파싱해 (aware datetime, 원본오프셋여부) 를 돌려줍니다.

    오프셋이 없으면 KST 로 간주합니다. 해외 체결에 오프셋을 빠뜨리면 거래일이
    어긋나므로 클라이언트는 반드시 오프셋을 넣어야 합니다.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValidationError('MISSING_FIELD', 'executedAt 이 필요합니다.', 'executedAt')

    raw = value.strip().replace('Z', '+00:00').replace(' ', 'T', 1)
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M', '%Y-%m-%d'):
            try:
                dt = datetime.strptime(raw[:len(datetime.now().strftime(fmt))], fmt)
                break
            except ValueError:
                continue
        else:
            raise ValidationError(
                'INVALID_FIELD',
                f"executedAt 형식이 올바르지 않습니다: {value!r} "
                "(예: 2026-08-01T09:30:00+09:00)", 'executedAt')

    has_offset = dt.tzinfo is not None
    if not has_offset:
        dt = dt.replace(tzinfo=KST)
    return dt, has_offset


def _trade_date_for(dt_aware, exchange):
    """거래소 현지 기준 거래일(YYYY-MM-DD)."""
    tz_name = _EXCHANGE_TZ.get((exchange or '').strip().upper())
    if tz_name and ZoneInfo is not None:
        try:
            return dt_aware.astimezone(ZoneInfo(tz_name)).strftime('%Y-%m-%d')
        except Exception:
            pass
    # tz 데이터가 없거나 미등록 거래소면 요청에 실려 온 오프셋의 현지 날짜를 쓴다.
    return dt_aware.strftime('%Y-%m-%d')


def _num(value, field, *, allow_none=True, minimum=None, exclusive_min=None):
    if value is None or value == '':
        if allow_none:
            return None
        raise ValidationError('MISSING_FIELD', f'{field} 이(가) 필요합니다.', field)
    if isinstance(value, bool):
        raise ValidationError('INVALID_FIELD', f'{field} 은(는) 숫자여야 합니다.', field)
    try:
        num = float(value)
    except (TypeError, ValueError):
        raise ValidationError('INVALID_FIELD', f'{field} 은(는) 숫자여야 합니다.', field)
    if num != num or num in (float('inf'), float('-inf')):
        raise ValidationError('INVALID_FIELD', f'{field} 값이 유효하지 않습니다.', field)
    if minimum is not None and num < minimum:
        raise ValidationError('INVALID_FIELD', f'{field} 은(는) {minimum} 이상이어야 합니다.', field)
    if exclusive_min is not None and num <= exclusive_min:
        raise ValidationError('INVALID_FIELD', f'{field} 은(는) {exclusive_min} 보다 커야 합니다.', field)
    return num


def _text(value, field, max_length, default=''):
    if value is None:
        return default
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    if len(value) > max_length:
        raise ValidationError('INVALID_FIELD',
                              f'{field} 길이가 최대 {max_length}자를 초과했습니다.', field)
    return value


def _normalize_trade_class(value):
    if value is None or value == '':
        return '시스템'
    if isinstance(value, bool):
        return '시스템'
    # 숫자 코드 또는 숫자 문자열 → 이름으로 치환 (v1 하위 호환)
    try:
        code = int(value)
        return _TRADE_CLASS_MAP.get(code, '기타')
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text if text in _VALID_TRADE_CLASSES else text or '시스템'


def _normalize_enum(value, valid, default, field):
    if value is None or value == '':
        return default
    text = str(value).strip().upper()
    if text not in valid:
        raise ValidationError('INVALID_FIELD',
                              f"{field} 값이 올바르지 않습니다: {value!r} "
                              f"(허용: {', '.join(sorted(valid))})", field)
    return text


def _resolve_account(username, data, mappings):
    """계좌 코드/번호를 등록된 매핑 정보로 치환합니다."""
    raw_broker = _text(data.get('brokerAccount'), 'brokerAccount', 50)
    raw_sub = _text(data.get('subAccount'), 'subAccount', 50).replace('-', '')

    acc_info = (mappings.get('accounts') or {}).get(raw_sub)
    if isinstance(acc_info, dict):
        broker = acc_info.get('broker_name') or raw_broker
        account_name = acc_info.get('alias') or _text(data.get('accountName'), 'accountName', 100)
    else:
        broker = (mappings.get('brokers') or {}).get(raw_broker, raw_broker)
        mapped = (mappings.get('accounts') or {}).get(raw_sub)
        account_name = mapped if isinstance(mapped, str) and mapped else \
            _text(data.get('accountName'), 'accountName', 100)
    return broker, raw_sub, account_name


def _lookup_stock_name(c, username, symbol):
    """종목명 미제공 시 **같은 사용자의** 기존 기록에서 조회.

    (v1 은 username 조건이 없어 다른 사용자의 종목명이 새어 나왔다.)
    """
    c.execute(
        "SELECT stockName FROM entries "
        "WHERE username = ? AND stockCode = ? AND stockName IS NOT NULL AND stockName != '' "
        "ORDER BY id DESC LIMIT 1",
        (username, symbol))
    row = c.fetchone()
    return row['stockName'] if row else symbol


def build_entry(c, username, data, mappings, *, default_source=None):
    """API 입력(dict)을 entries 행(dict)으로 정규화합니다.

    검증 실패 시 ValidationError 를 던집니다. (매도 무결성은 여기서 보지 않음)
    """
    if not isinstance(data, dict):
        raise ValidationError('INVALID_REQUEST', '각 항목은 JSON 객체여야 합니다.')

    for field in ('symbol', 'side', 'price', 'volume', 'executedAt'):
        if data.get(field) is None or data.get(field) == '':
            raise ValidationError('MISSING_FIELD', f'필수 파라미터가 누락되었습니다: {field}', field)

    symbol = _text(data['symbol'], 'symbol', 32)
    if not symbol:
        raise ValidationError('MISSING_FIELD', '필수 파라미터가 누락되었습니다: symbol', 'symbol')

    side = str(data['side']).strip().upper()
    if side not in ('BUY', 'SELL'):
        raise ValidationError('INVALID_FIELD', "side 는 BUY 또는 SELL 이어야 합니다.", 'side')

    price = _num(data['price'], 'price', allow_none=False, minimum=0)
    volume = _num(data['volume'], 'volume', allow_none=False, exclusive_min=0)

    executed_dt, _ = _parse_executed_at(data['executedAt'])
    exchange = _text(data.get('exchange'), 'exchange', 20)

    # rawDate/date 는 웹 UI 의 표시·정렬 기준(KST 로컬, 오프셋 없음)을 유지한다.
    # 오프셋이 섞이면 문자열 MAX/정렬이 깨지므로 UTC 는 별도 컬럼에 둔다.
    local_kst = executed_dt.astimezone(KST)
    raw_date = local_kst.strftime('%Y-%m-%dT%H:%M:%S')

    trade_status = _normalize_enum(data.get('status'), _VALID_STATUS, 'FILLED', 'status')
    confidence = _normalize_enum(data.get('confidence'), _VALID_CONFIDENCE, 'CONFIRMED', 'confidence')
    order_origin = _normalize_enum(data.get('orderOrigin'), _VALID_ORIGIN, '', 'orderOrigin') \
        if data.get('orderOrigin') else ''

    trade_class = _normalize_trade_class(data.get('tradeClass'))

    tags = data.get('tags') or []
    if not isinstance(tags, list):
        raise ValidationError('INVALID_FIELD', 'tags 는 문자열 배열이어야 합니다.', 'tags')
    if len(tags) > 30:
        raise ValidationError('INVALID_FIELD', 'tags 는 최대 30개까지 허용됩니다.', 'tags')
    tags = [_text(t, 'tags', 50) for t in tags]
    if trade_class and trade_class not in tags:
        tags.append(trade_class)
    if confidence == 'ESTIMATED' and '추정체결' not in tags:
        tags.append('추정체결')

    stock_name = _text(data.get('name'), 'name', 100)
    if not stock_name:
        stock_name = _lookup_stock_name(c, username, symbol)

    broker, sub_account, account_name = _resolve_account(username, data, mappings)

    is_simulated = 1 if data.get('isSimulated') else 0
    source = _text(data.get('source'), 'source', 100) or _text(default_source, 'source', 100)

    now = _now_kst_str()
    return {
        'type': 'trade',
        'stockName': stock_name,
        'stockCode': symbol,
        'title': '',
        'thoughts': _text(data.get('memo'), 'memo', 5000),
        'date': local_kst.strftime('%Y-%m-%d'),
        'rawDate': raw_date,
        'brokerAccount': broker,
        'subAccount': sub_account,
        'accountName': account_name,
        'tradeClass': trade_class,
        'tradeType': '매수' if side == 'BUY' else '매도',
        'price': price,
        'quantity': volume,
        'tags': ','.join(tags),
        'createdAt': now,
        'updatedAt': now,
        'brokerExecutionId': _text(data.get('brokerExecutionId'), 'brokerExecutionId', 200),
        'currency': _text(data.get('currency'), 'currency', 10) or 'KRW',
        'exchange': exchange,
        'assetType': _text(data.get('assetType'), 'assetType', 20),
        # ── 확장 컬럼 ──
        'isSimulated': is_simulated,
        'tradeStatus': trade_status,
        'confidence': confidence,
        'orderOrigin': order_origin,
        'source': source,
        'orderId': _text(data.get('orderId'), 'orderId', 100),
        'originalOrderId': _text(data.get('originalOrderId'), 'originalOrderId', 100),
        'realizedPnl': _num(data.get('realizedPnl'), 'realizedPnl'),
        'realizedPnlRate': _num(data.get('realizedPnlRate'), 'realizedPnlRate'),
        'fee': _num(data.get('fee'), 'fee', minimum=0),
        'tax': _num(data.get('tax'), 'tax', minimum=0),
        'strategyScore': _num(data.get('strategyScore'), 'strategyScore'),
        'stopLossRate': _num(data.get('stopLossRate'), 'stopLossRate'),
        'executedAtUtc': executed_dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'tradeDate': _trade_date_for(executed_dt, exchange),
        'needsReview': 0,
    }


def entry_to_response(row):
    """DB 행을 API 응답(TradeRecord)으로 변환합니다."""
    row = dict(row)
    tags = [t for t in (row.get('tags') or '').split(',') if t]
    return {
        'id': str(row.get('id')),
        'symbol': row.get('stockCode') or '',
        'name': row.get('stockName') or '',
        'side': 'BUY' if row.get('tradeType') == '매수' else 'SELL',
        'price': row.get('price'),
        'volume': row.get('quantity'),
        'executedAt': row.get('executedAtUtc') or row.get('rawDate'),
        'tradeDate': row.get('tradeDate'),
        'brokerExecutionId': row.get('brokerExecutionId') or None,
        'isSimulated': bool(row.get('isSimulated')),
        'status': row.get('tradeStatus') or 'FILLED',
        'confidence': row.get('confidence') or 'CONFIRMED',
        'orderOrigin': row.get('orderOrigin') or None,
        'source': row.get('source') or None,
        'orderId': row.get('orderId') or None,
        'originalOrderId': row.get('originalOrderId') or None,
        'realizedPnl': row.get('realizedPnl'),
        'realizedPnlRate': row.get('realizedPnlRate'),
        'fee': row.get('fee'),
        'tax': row.get('tax'),
        'strategyScore': row.get('strategyScore'),
        'stopLossRate': row.get('stopLossRate'),
        'memo': row.get('thoughts') or '',
        'tradeClass': row.get('tradeClass') or '',
        'brokerAccount': row.get('brokerAccount') or '',
        'subAccount': row.get('subAccount') or '',
        'accountName': row.get('accountName') or '',
        'currency': row.get('currency') or 'KRW',
        'exchange': row.get('exchange') or '',
        'assetType': row.get('assetType') or '',
        'tags': tags,
        'needsReview': bool(row.get('needsReview')),
        'createdAt': row.get('createdAt'),
        'updatedAt': row.get('updatedAt'),
    }


def _fetch_by_exec_id(c, username, exec_id):
    c.execute("SELECT * FROM entries WHERE username = ? AND brokerExecutionId = ?",
              (username, exec_id))
    return c.fetchone()


def _insert_trade(c, username, entry):
    """멱등 INSERT. (기록 id, 신규여부, 경고목록) 을 반환합니다.

    매도 무결성 위반은 차단하지 않고 needsReview 로 표시만 합니다 —
    봇 체결을 400 으로 되돌리면 재시도해도 계속 실패해 그 기록이 영구 유실됩니다.
    """
    warnings = []
    integrity = entry_logic.check_sell_integrity(c, username, entry)
    if integrity:
        warnings.append(integrity[1])
        entry['needsReview'] = 1

    exec_id = entry.get('brokerExecutionId') or ''
    if exec_id:
        existing = _fetch_by_exec_id(c, username, exec_id)
        if existing is not None:
            return existing['id'], False, warnings

    try:
        entry_logic.insert_entry(c, username, entry)
    except sqlite3.IntegrityError:
        # UNIQUE 경합 — 다른 요청이 방금 같은 멱등키를 넣었다.
        if exec_id:
            existing = _fetch_by_exec_id(c, username, exec_id)
            if existing is not None:
                return existing['id'], False, warnings
        raise
    return c.lastrowid, True, warnings


# ══════════════════════════════════════════════════════════════════════
# API 키 관리 (웹 세션에서 호출 — backend_app 이 얇게 위임)
# ══════════════════════════════════════════════════════════════════════

def list_api_keys(username):
    with _db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT id, key_prefix, label, scopes, created_at, last_used_at, revoked_at "
            "FROM api_keys WHERE username = ? ORDER BY id DESC", (username,))
        return [dict(r) for r in c.fetchall()]


def create_api_key(username, label=None, scopes=None):
    """새 키를 발급합니다. **평문은 이 반환값에서 단 한 번만 볼 수 있습니다.**"""
    raw = _generate_api_key()
    scope_str = ' '.join(scopes) if scopes else DEFAULT_SCOPES
    with _db() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO api_keys (username, key_hash, key_prefix, label, scopes, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (username, _hash_key(raw), _display_prefix(raw),
             label or 'HTS 연동 키', scope_str, _now_kst_str()))
        key_id = c.lastrowid
        conn.commit()
    return {'id': key_id, 'api_key': raw, 'key_prefix': _display_prefix(raw),
            'scopes': scope_str.split(), 'label': label or 'HTS 연동 키'}


def revoke_api_key(username, key_id):
    with _db() as conn:
        c = conn.cursor()
        c.execute("UPDATE api_keys SET revoked_at = ? WHERE id = ? AND username = ? "
                  "AND revoked_at IS NULL", (_now_kst_str(), key_id, username))
        changed = c.rowcount
        conn.commit()
    return changed > 0


def revoke_all_api_keys(username):
    with _db() as conn:
        c = conn.cursor()
        c.execute("UPDATE api_keys SET revoked_at = ? WHERE username = ? AND revoked_at IS NULL",
                  (_now_kst_str(), username))
        conn.commit()


# ══════════════════════════════════════════════════════════════════════
# 엔드포인트
# ══════════════════════════════════════════════════════════════════════

@bp.route('/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'ok',
        'serverTime': _now_kst().strftime('%Y-%m-%dT%H:%M:%S%z'),
        'apiVersion': API_VERSION,
    }), 200


@bp.route('/auth/token', methods=['POST'])
def auth_token():
    allowed, _, retry_after = _limiter.check(
        f'token:{_client_ip()}', TOKEN_RATE_LIMIT[0], TOKEN_RATE_LIMIT[1])
    if not allowed:
        resp, status = _err(429, 'RATE_LIMITED',
                            '토큰 발급 요청이 너무 잦습니다. 잠시 후 다시 시도하세요.')
        resp.headers['Retry-After'] = str(retry_after)
        return resp, status

    api_key = request.headers.get('X-API-KEY')
    if not api_key:
        body = request.get_json(silent=True)
        if isinstance(body, dict):
            api_key = body.get('api_key')
    if not api_key or not isinstance(api_key, str):
        return _err(400, 'MISSING_API_KEY', 'API 키가 누락되었습니다.')

    with _db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT id, username, scopes, revoked_at FROM api_keys WHERE key_hash = ?",
            (_hash_key(api_key.strip()),))
        row = c.fetchone()

    if row is None or row['revoked_at']:
        return _err(401, 'INVALID_API_KEY', '유효하지 않은 API 키입니다.')

    token = _serializer().dumps({'u': row['username'], 'k': row['id']})
    return jsonify({
        'access_token': token,
        'token_type': 'Bearer',
        'expires_in': TOKEN_TTL_SECONDS,
        'scopes': (row['scopes'] or '').split(),
    }), 200


@bp.route('/bot/status', methods=['POST'])
@require_token(SCOPE_BOT_WRITE)
def bot_status(username, scopes):
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or 'status' not in data:
        return _err(400, 'MISSING_FIELD', 'status 필드가 필요합니다.', field='status')

    status_value = str(data['status']).strip().lower()
    if status_value not in ('running', 'stopped', 'error'):
        return _err(400, 'INVALID_FIELD',
                    'status 는 running/stopped/error 중 하나여야 합니다.', field='status')

    now = _now_kst_str()
    with _db() as conn:
        c = conn.cursor()
        c.execute("UPDATE users SET bot_status = ?, bot_last_seen = ? WHERE username = ?",
                  (status_value, now, username))
        conn.commit()

    return jsonify({
        'status': 'success',
        'updatedAt': now,
        'nextPingSeconds': 60,
        'command': 'none',
    }), 200


@bp.route('/trades', methods=['POST'])
@require_token(SCOPE_TRADES_WRITE)
def create_trade(username, scopes):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _err(400, 'INVALID_REQUEST', '요청 본문이 JSON 객체가 아닙니다.')

    # brokerExecutionId 가 없으면 Idempotency-Key 헤더를 대체 멱등키로 쓴다.
    if not data.get('brokerExecutionId'):
        idem = request.headers.get('Idempotency-Key')
        if idem:
            data = dict(data, brokerExecutionId=idem.strip()[:200])

    with _db() as conn:
        c = conn.cursor()
        mappings = _deps['get_user_mappings'](username)
        try:
            entry = build_entry(c, username, data, mappings)
        except ValidationError as e:
            return _err(400, e.code, e.message, field=e.field)

        try:
            entry_id, created, warnings = _insert_trade(c, username, entry)
        except sqlite3.Error as e:
            _log().error(f"[trading_api] 매매 기록 저장 실패: {e}")
            return _err(500, 'INTERNAL_ERROR', '기록 저장 중 오류가 발생했습니다.')
        conn.commit()

        c.execute("SELECT * FROM entries WHERE id = ?", (entry_id,))
        row = c.fetchone()

    if created:
        _deps['invalidate_cache'](username)

    body = entry_to_response(row)
    if warnings:
        body['warnings'] = warnings
    return jsonify(body), (201 if created else 200)


@bp.route('/trades/batch', methods=['POST'])
@require_token(SCOPE_TRADES_WRITE)
def create_trades_batch(username, scopes):
    payload = request.get_json(silent=True)
    default_source = None
    if isinstance(payload, dict):
        trades = payload.get('trades')
        default_source = payload.get('source')
    else:
        trades = payload  # 레거시 v1: 최상위 배열

    if not isinstance(trades, list):
        return _err(400, 'INVALID_REQUEST',
                    "요청 본문은 배열 또는 {\"trades\": [...]} 형태여야 합니다.")
    if not trades:
        return jsonify({'status': 'success', 'inserted': 0, 'skipped': 0,
                        'failed': 0, 'results': [], 'errors': None}), 200
    if len(trades) > MAX_BATCH_ITEMS:
        return _err(413, 'PAYLOAD_TOO_LARGE',
                    f'한 번에 최대 {MAX_BATCH_ITEMS}건까지 전송할 수 있습니다.',
                    received=len(trades), maximum=MAX_BATCH_ITEMS)

    results = []
    inserted = skipped = failed = 0
    legacy_errors = []
    seen_exec_ids = set()  # 같은 배치 안의 중복도 걸러낸다

    with _db() as conn:
        c = conn.cursor()
        mappings = _deps['get_user_mappings'](username)

        for index, item in enumerate(trades):
            try:
                entry = build_entry(c, username, item, mappings,
                                    default_source=default_source)
            except ValidationError as e:
                failed += 1
                legacy_errors.append(e.message)
                results.append({'index': index, 'status': 'failed', 'id': None,
                                'brokerExecutionId': (item or {}).get('brokerExecutionId')
                                if isinstance(item, dict) else None,
                                'errorCode': e.code, 'error': e.message})
                continue

            exec_id = entry.get('brokerExecutionId') or ''
            if exec_id and exec_id in seen_exec_ids:
                skipped += 1
                results.append({'index': index, 'status': 'duplicate', 'id': None,
                                'brokerExecutionId': exec_id,
                                'errorCode': None, 'error': None})
                continue

            try:
                entry_id, created, warnings = _insert_trade(c, username, entry)
            except sqlite3.Error as e:
                failed += 1
                message = f'저장 실패: {e}'
                legacy_errors.append(message)
                results.append({'index': index, 'status': 'failed', 'id': None,
                                'brokerExecutionId': exec_id or None,
                                'errorCode': 'INTERNAL_ERROR', 'error': message})
                continue

            if exec_id:
                seen_exec_ids.add(exec_id)
            if created:
                inserted += 1
            else:
                skipped += 1
            row = {'index': index, 'status': 'created' if created else 'duplicate',
                   'id': str(entry_id), 'brokerExecutionId': exec_id or None,
                   'errorCode': None, 'error': None}
            if warnings:
                row['warnings'] = warnings
            results.append(row)

        conn.commit()

    if inserted:
        _deps['invalidate_cache'](username)

    return jsonify({
        'status': 'success' if not failed else 'partial',
        'inserted': inserted,
        'skipped': skipped,
        'failed': failed,
        'results': results,
        'errors': legacy_errors or None,
    }), (201 if inserted else 200)


@bp.route('/trades', methods=['GET'])
@require_token(SCOPE_TRADES_READ)
def list_trades(username, scopes):
    args = request.args
    conditions = ["username = ?", "type = 'trade'"]
    params = [username]

    from_val = args.get('from')
    to_val = args.get('to')
    if from_val:
        conditions.append("COALESCE(executedAtUtc, rawDate) >= ?")
        params.append(from_val)
    if to_val:
        # 날짜만 주면 그날 끝까지 포함
        conditions.append("COALESCE(executedAtUtc, rawDate) <= ?")
        params.append(to_val if len(to_val) > 10 else to_val + 'T23:59:59Z')
    if args.get('source'):
        conditions.append("source = ?")
        params.append(args.get('source'))

    sim_arg = args.get('isSimulated')
    if sim_arg is None:
        conditions.append("COALESCE(isSimulated, 0) = 0")
    elif sim_arg.lower() not in ('all', '*'):
        conditions.append("COALESCE(isSimulated, 0) = ?")
        params.append(1 if sim_arg.lower() in ('1', 'true', 'yes') else 0)

    cursor = args.get('cursor')
    if cursor:
        try:
            conditions.append("id < ?")
            params.append(int(cursor))
        except (TypeError, ValueError):
            return _err(400, 'INVALID_FIELD', 'cursor 값이 올바르지 않습니다.', field='cursor')

    try:
        limit = min(max(int(args.get('limit', 100)), 1), 500)
    except (TypeError, ValueError):
        limit = 100

    with _db() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM entries WHERE " + " AND ".join(conditions)
                  + " ORDER BY id DESC LIMIT ?", params + [limit + 1])
        rows = c.fetchall()

    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = str(rows[-1]['id']) if has_more and rows else None

    return jsonify({
        'trades': [entry_to_response(r) for r in rows],
        'nextCursor': next_cursor,
    }), 200


@bp.route('/trades/last-sync', methods=['GET'])
@require_token(SCOPE_TRADES_READ)
def last_sync(username, scopes):
    args = request.args
    conditions = ["username = ?", "type = 'trade'"]
    params = [username]

    if args.get('source'):
        conditions.append("source = ?")
        params.append(args.get('source'))
    if args.get('account'):
        conditions.append("REPLACE(subAccount, '-', '') = ?")
        params.append(args.get('account').replace('-', ''))

    sim_arg = (args.get('isSimulated') or 'false').lower()
    conditions.append("COALESCE(isSimulated, 0) = ?")
    params.append(1 if sim_arg in ('1', 'true', 'yes') else 0)

    where = " AND ".join(conditions)
    with _db() as conn:
        c = conn.cursor()
        # executedAtUtc 는 오프셋이 통일된 UTC 문자열이라 사전순 비교가 곧 시간순이다.
        c.execute(f"SELECT COUNT(*) AS cnt FROM entries WHERE {where}", params)
        count = c.fetchone()['cnt']
        c.execute(
            f"SELECT executedAtUtc, rawDate, brokerExecutionId FROM entries WHERE {where} "
            "ORDER BY COALESCE(executedAtUtc, rawDate) DESC, id DESC LIMIT 1", params)
        row = c.fetchone()

    return jsonify({
        'lastExecutedAt': (row['executedAtUtc'] or row['rawDate']) if row else None,
        'lastBrokerExecutionId': (row['brokerExecutionId'] or None) if row else None,
        'count': count,
    }), 200


@bp.route('/trades/by-exec-id/<path:broker_execution_id>', methods=['GET'])
@require_token(SCOPE_TRADES_READ)
def get_trade_by_exec_id(username, scopes, broker_execution_id):
    with _db() as conn:
        c = conn.cursor()
        row = _fetch_by_exec_id(c, username, broker_execution_id)
    if row is None:
        return _err(404, 'NOT_FOUND', '해당 멱등키의 기록이 없습니다.')
    return jsonify(entry_to_response(row)), 200


# PATCH 로 갱신 가능한 API 필드 -> DB 컬럼
_PATCHABLE = {
    'price': 'price',
    'volume': 'quantity',
    'status': 'tradeStatus',
    'confidence': 'confidence',
    'realizedPnl': 'realizedPnl',
    'realizedPnlRate': 'realizedPnlRate',
    'fee': 'fee',
    'tax': 'tax',
    'strategyScore': 'strategyScore',
    'stopLossRate': 'stopLossRate',
    'memo': 'thoughts',
    'name': 'stockName',
}


def _load_bot_entry(c, username, trade_id):
    """봇이 만든 기록만 반환합니다 (웹 UI 수동 입력은 API 로 건드리지 않는다)."""
    c.execute("SELECT * FROM entries WHERE id = ? AND username = ?", (trade_id, username))
    row = c.fetchone()
    if row is None:
        return None
    if not (row['brokerExecutionId'] or row['source']):
        return None
    return row


@bp.route('/trades/<trade_id>', methods=['PATCH'])
@require_token(SCOPE_TRADES_WRITE)
def patch_trade(username, scopes, trade_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not data:
        return _err(400, 'INVALID_REQUEST', '갱신할 필드를 1개 이상 보내야 합니다.')

    updates, params = [], []
    try:
        for field, value in data.items():
            column = _PATCHABLE.get(field)
            if column is None:
                continue
            if field == 'price':
                value = _num(value, 'price', allow_none=False, minimum=0)
            elif field == 'volume':
                value = _num(value, 'volume', allow_none=False, exclusive_min=0)
            elif field in ('realizedPnl', 'realizedPnlRate', 'strategyScore', 'stopLossRate'):
                value = _num(value, field)
            elif field in ('fee', 'tax'):
                value = _num(value, field, minimum=0)
            elif field == 'status':
                value = _normalize_enum(value, _VALID_STATUS, 'FILLED', 'status')
            elif field == 'confidence':
                value = _normalize_enum(value, _VALID_CONFIDENCE, 'CONFIRMED', 'confidence')
            elif field == 'memo':
                value = _text(value, 'memo', 5000)
            elif field == 'name':
                value = _text(value, 'name', 100)
            updates.append(f"{column} = ?")
            params.append(value)

        if 'executedAt' in data:
            executed_dt, _ = _parse_executed_at(data['executedAt'])
            local_kst = executed_dt.astimezone(KST)
            updates.extend(["rawDate = ?", "date = ?", "executedAtUtc = ?"])
            params.extend([local_kst.strftime('%Y-%m-%dT%H:%M:%S'),
                           local_kst.strftime('%Y-%m-%d'),
                           executed_dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')])

        if 'tags' in data:
            tags = data['tags']
            if not isinstance(tags, list):
                raise ValidationError('INVALID_FIELD', 'tags 는 문자열 배열이어야 합니다.', 'tags')
            updates.append("tags = ?")
            params.append(','.join(_text(t, 'tags', 50) for t in tags[:30]))
    except ValidationError as e:
        return _err(400, e.code, e.message, field=e.field)

    if not updates:
        return _err(400, 'INVALID_REQUEST', '갱신 가능한 필드가 없습니다.',
                    allowed=sorted(list(_PATCHABLE) + ['executedAt', 'tags']))

    updates.append("updatedAt = ?")
    params.append(_now_kst_str())

    with _db() as conn:
        c = conn.cursor()
        if _load_bot_entry(c, username, trade_id) is None:
            return _err(404, 'NOT_FOUND', '해당 기록이 없거나 API 로 수정할 수 없는 기록입니다.')
        c.execute(f"UPDATE entries SET {', '.join(updates)} WHERE id = ? AND username = ?",
                  params + [trade_id, username])
        conn.commit()
        c.execute("SELECT * FROM entries WHERE id = ? AND username = ?", (trade_id, username))
        row = c.fetchone()

    _deps['invalidate_cache'](username)
    return jsonify(entry_to_response(row)), 200


@bp.route('/trades/<trade_id>', methods=['DELETE'])
@require_token(SCOPE_TRADES_WRITE)
def delete_trade(username, scopes, trade_id):
    with _db() as conn:
        c = conn.cursor()
        if _load_bot_entry(c, username, trade_id) is None:
            return _err(404, 'NOT_FOUND', '해당 기록이 없거나 API 로 삭제할 수 없는 기록입니다.')
        c.execute("DELETE FROM entries WHERE id = ? AND username = ?", (trade_id, username))
        conn.commit()

    _deps['invalidate_cache'](username)
    return '', 204


@bp.route('/positions/opening', methods=['POST'])
@require_token(SCOPE_TRADES_WRITE)
def create_opening_positions(username, scopes):
    """연동 시작 시점의 보유 잔고를 매수 기록으로 등록합니다.

    이 단계가 없으면 연동 이전부터 들고 있던 종목의 첫 매도가 전부
    '매수 기록 없음' 경고를 달고 보유 수량 집계가 음수로 내려갑니다.
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _err(400, 'INVALID_REQUEST', '요청 본문이 JSON 객체가 아닙니다.')

    as_of = payload.get('asOf')
    positions = payload.get('positions')
    if not as_of or not isinstance(positions, list):
        return _err(400, 'MISSING_FIELD', 'asOf 와 positions 가 필요합니다.')
    if len(positions) > MAX_BATCH_ITEMS:
        return _err(413, 'PAYLOAD_TOO_LARGE',
                    f'한 번에 최대 {MAX_BATCH_ITEMS}건까지 등록할 수 있습니다.')

    is_simulated = bool(payload.get('isSimulated'))
    env = 'SIM' if is_simulated else 'REAL'
    source = payload.get('source') or 'opening-balance'

    results = []
    inserted = skipped = failed = 0
    with _db() as conn:
        c = conn.cursor()
        mappings = _deps['get_user_mappings'](username)

        for index, pos in enumerate(positions):
            if not isinstance(pos, dict):
                failed += 1
                results.append({'index': index, 'status': 'failed', 'id': None,
                                'errorCode': 'INVALID_REQUEST',
                                'error': '각 항목은 JSON 객체여야 합니다.'})
                continue
            symbol = str(pos.get('symbol') or '').strip()
            trade_input = {
                'symbol': symbol,
                'side': 'BUY',
                'price': pos.get('avgPrice'),
                'volume': pos.get('volume'),
                'executedAt': f'{as_of}T00:00:00+09:00',
                'brokerExecutionId': f'OPENING:{env}:{as_of}:{symbol}',
                'isSimulated': is_simulated,
                'orderOrigin': 'BACKFILL',
                'source': source,
                'name': pos.get('name'),
                'currency': pos.get('currency'),
                'exchange': pos.get('exchange'),
                'assetType': pos.get('assetType'),
                'brokerAccount': pos.get('brokerAccount'),
                'subAccount': pos.get('subAccount'),
                'memo': pos.get('memo') or f'연동 시작 기초잔고 ({as_of} 기준)',
                'tags': ['기초잔고'],
            }
            try:
                entry = build_entry(c, username, trade_input, mappings)
                entry_id, created, _warn = _insert_trade(c, username, entry)
            except ValidationError as e:
                failed += 1
                results.append({'index': index, 'status': 'failed', 'id': None,
                                'errorCode': e.code, 'error': e.message})
                continue
            except sqlite3.Error as e:
                failed += 1
                results.append({'index': index, 'status': 'failed', 'id': None,
                                'errorCode': 'INTERNAL_ERROR', 'error': str(e)})
                continue

            if created:
                inserted += 1
            else:
                skipped += 1
            results.append({'index': index,
                            'status': 'created' if created else 'duplicate',
                            'id': str(entry_id), 'errorCode': None, 'error': None})
        conn.commit()

    if inserted:
        _deps['invalidate_cache'](username)

    return jsonify({
        'status': 'success' if not failed else 'partial',
        'inserted': inserted, 'skipped': skipped, 'failed': failed,
        'results': results, 'errors': None,
    }), (201 if inserted else 200)
