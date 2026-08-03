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
import json
import re
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

# ── 봇 하트비트 ────────────────────────────────────────────────────────
# HTS 는 BOT_PING_INTERVAL_SECONDS 마다 상태를 보고하고(응답의 nextPingSeconds 로도 안내),
# 서버는 BOT_MISSED_PINGS_ALLOWED 회 연속 누락되면 '통신단절'로 판정한다.
# 여유(GRACE)는 네트워크 지연·스케줄러 흔들림으로 정상 가동 중에 깜빡이는 것을 막는 완충값이다.
BOT_PING_INTERVAL_SECONDS = 10
BOT_MISSED_PINGS_ALLOWED = 3
BOT_PING_GRACE_SECONDS = 5
BOT_OFFLINE_AFTER_SECONDS = (BOT_PING_INTERVAL_SECONDS * BOT_MISSED_PINGS_ALLOWED
                             + BOT_PING_GRACE_SECONDS)  # 35초

# ── 봇 명령 (Ping 응답에 실어 보내는 유일한 하행 채널) ──────────────────
# 봇은 대개 가정용 네트워크 뒤에 있어 서버가 먼저 접속할 수 없다. 웹에서 누른
# 지시는 다음 Ping 응답에 실려 전달되므로 최대 BOT_PING_INTERVAL_SECONDS 만큼 늦는다.
#
# 봇이 ack 를 돌려줄 때까지 같은 명령을 반복해서 내려보낸다 — 재실행은 멱등하므로
# 안전하고, 응답이 유실돼도 결국 전달된다. 다만 봇이 영영 응답하지 않는 경우까지
# 무한히 붙들 수는 없어 만료를 둔다. 만료된 명령은 웹 화면에 '미처리'로 표시된다.
BOT_COMMAND_TTL_SECONDS = 3600

# 서버가 내려보낼 수 있는 명령. 스펙의 enum 에는 pause/resume 도 있지만 여기 없다 —
# 웹서버가 매매봇을 멈추는 것은 재확인·자동만료 같은 안전장치를 갖춘 별도 설계가
# 필요한 일이라, 재동기화와 같은 취급을 해서는 안 된다.
SUPPORTED_BOT_COMMANDS = ('resync',)

# ── 봇 식별 ────────────────────────────────────────────────────────────
# 하트비트·명령의 스코프는 API 키가 아니라 **사용자**다(키는 인증만 하고 곧바로
# username 으로 바뀐다). 그래서 HTS 를 여러 대 돌리면 키를 따로 발급해도 상태가
# 한 칸에 겹쳐 쓰이고, 실전봇이 죽어도 모의봇 Ping 이 화면을 '정상'으로 유지한다.
# botId 는 그 겹침을 푸는 봇 인스턴스 식별자다 — HTS 가 스스로 정해서 보낸다.
#
# botId 를 보내지 않는 구버전 HTS 는 이 값으로 묶는다. 한 대만 쓰던 기존 사용자는
# 그대로 동작하고, 두 대째가 붙는 순간부터 각자의 botId 로 갈라진다.
LEGACY_BOT_ID = 'default'
BOT_ID_MAX_LEN = 64

# 화면 대표 상태를 고를 때의 우선순위 — **나쁜 쪽이 이긴다.**
# 여러 봇 중 하나라도 죽었으면 그것이 보여야 한다. '하나라도 살아 있으면 초록'은
# 정확히 이 기능이 막으려는 오표시(실전봇 사망을 모의봇 Ping 이 가리는 것)다.
_BOT_STATE_SEVERITY = {'never': 0, 'running': 1, 'stopped': 2, 'offline': 3, 'error': 4}

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
    # ⭐️ 시스템 트레이딩이 낸 주문인가. **DEFAULT 를 두지 않는다** — 0/1 만으로는
    #    '시스템이 아니다'와 '봇이 알려주지 않았다'가 구분되지 않는데, 분류 폴백이
    #    바로 그 구분에 걸려 있다. 모르면 NULL 로 남아야 한다.
    ('isSystem', 'INTEGER'),
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

    # 웹에서 누른 지시를 봇이 가져갈 때까지 보관하는 큐.
    #  이벤트가 아니라 '행'으로 남겨야 한다 — 버튼을 누른 순간 봇이 꺼져 있어도
    #  다시 켜졌을 때 전달되어야 하고, 처리 결과를 웹에 보여줘야 하기 때문이다.
    c.execute('''
        CREATE TABLE IF NOT EXISTS bot_commands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            command TEXT NOT NULL,
            params_json TEXT,
            requested_at TEXT NOT NULL,
            delivered_at TEXT,
            acked_at TEXT,
            result TEXT,
            result_count INTEGER,
            result_message TEXT
        )
    ''')
    # ⭐️ 명령을 받을 봇. NULL 은 '봇을 지정하지 않은 구버전 요청'이라 봇이 한 대일
    #    때만 전달한다 (_take_pending_command 참고). 여러 대가 붙어 있는데 대상을
    #    모르는 명령을 아무 봇에게나 주면, 엉뚱한 계좌가 재동기화되고 그 봇이 ack 까지
    #    보내 웹에는 '완료'로 뜬다 — 운용자가 알아챌 수 없는 실패다.
    try:
        c.execute("ALTER TABLE bot_commands ADD COLUMN bot_id TEXT")
    except sqlite3.OperationalError:
        pass  # 이미 존재

    # 봇 인스턴스별 하트비트. users.bot_status 는 사용자당 한 칸뿐이라 봇이 여러
    # 대면 마지막에 Ping 한 놈이 앞의 상태를 덮어썼다.
    c.execute('''
        CREATE TABLE IF NOT EXISTS bots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            bot_id TEXT NOT NULL,
            label TEXT,
            status TEXT,
            last_seen TEXT,
            is_simulated INTEGER DEFAULT 0,
            message TEXT,
            first_seen TEXT,
            UNIQUE(username, bot_id)
        )
    ''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_bots_user ON bots(username)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_bot_commands_pending "
              "ON bot_commands(username, acked_at, id)")
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


def _now_iso():
    """오프셋을 포함한 ISO 8601 문자열 (예: 2026-08-01T20:08:04+09:00).

    봇 하트비트처럼 '경과 시간'을 계산해야 하는 값은 오프셋 없는 KST 문자열로
    저장하면 안 된다. 브라우저나 다른 타임존의 서버가 이를 로컬 시각으로 읽어
    몇 시간씩 어긋난 경과 시간을 만들어내기 때문이다.
    """
    return _now_kst().isoformat(timespec='seconds')


def _parse_stored_dt(value):
    """DB 에 저장된 시각 문자열을 tz-aware datetime 으로 되돌린다.

    ISO 8601(오프셋 포함)이 표준이지만, 이전 버전이 남긴 오프셋 없는
    'YYYY-MM-DD HH:MM:SS' 값도 KST 로 간주해 함께 받아준다.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).strip().replace(' ', 'T', 1))
    except ValueError:
        return None
    return dt.replace(tzinfo=KST) if dt.tzinfo is None else dt


def evaluate_bot_state(bot_status, bot_last_seen, now=None):
    """봇 표시 상태를 서버에서 확정한다.

    만료 판정을 클라이언트에 맡기면 브라우저 시계 오차와 타임존 해석 차이가
    그대로 오판이 된다. 화면은 여기서 내려준 state 를 그리기만 하면 된다.

    반환: (state, elapsed_seconds)
      never   — 연동 기록 없음
      running — 정상 가동중
      stopped — HTS 가 정상 종료를 알림
      error   — HTS 가 오류를 알림
      offline — Ping 이 BOT_MISSED_PINGS_ALLOWED 회 연속 누락됨(통신단절)
    """
    if not bot_status:
        return 'never', None

    last_seen = _parse_stored_dt(bot_last_seen)
    if last_seen is None:
        return 'never', None

    elapsed = ((now or _now_kst()) - last_seen).total_seconds()

    # 마지막 보고가 'stopped'/'error' 면 오래됐든 아니든 그 사유가 통신단절보다 정확하다.
    status = str(bot_status).strip().lower()
    if status in ('stopped', 'error'):
        return status, elapsed
    if elapsed > BOT_OFFLINE_AFTER_SECONDS:
        return 'offline', elapsed
    return 'running', elapsed


def _normalize_bot_id(value):
    """봇 식별자 정규화. 비었으면 구버전 취급(LEGACY_BOT_ID)."""
    text = str(value or '').strip()[:BOT_ID_MAX_LEN]
    return text or LEGACY_BOT_ID


def _upsert_bot(c, username, bot_id, status, now, *, label=None,
                is_simulated=False, message=None):
    """봇 하트비트를 인스턴스 단위로 기록한다.

    label 은 봇이 보낼 때만 갱신한다 — 매 Ping 마다 덮으면, 라벨을 안 보내는
    구버전으로 잠깐 되돌렸을 때 화면에서 이름이 사라진다.
    """
    c.execute(
        "INSERT INTO bots (username, bot_id, label, status, last_seen, "
        "                  is_simulated, message, first_seen) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(username, bot_id) DO UPDATE SET "
        "  status = excluded.status, last_seen = excluded.last_seen, "
        "  is_simulated = excluded.is_simulated, message = excluded.message, "
        "  label = COALESCE(excluded.label, bots.label)",
        (username, bot_id, label, status, now, 1 if is_simulated else 0, message, now))


def list_bots(username, now=None):
    """이 사용자의 봇 인스턴스 목록. 각 행에 서버가 확정한 state 를 붙여 돌려준다."""
    with _db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT bot_id, label, status, last_seen, is_simulated, message, first_seen "
            "FROM bots WHERE username = ? ORDER BY bot_id", (username,))
        rows = [dict(r) for r in c.fetchall()]

    items = []
    for row in rows:
        state, elapsed = evaluate_bot_state(row['status'], row['last_seen'], now=now)
        items.append({
            'botId': row['bot_id'],
            'label': row['label'] or row['bot_id'],
            'status': row['status'],
            'state': state,
            'lastSeen': row['last_seen'],
            'elapsedSeconds': round(elapsed, 1) if elapsed is not None else None,
            'isSimulated': bool(row['is_simulated']),
            'message': row['message'] or None,
        })
    return items


def delete_bot(username, bot_id):
    """봇 등록을 지운다. 지워졌으면 True.

    필요한 이유: 봇 식별자가 바뀌거나(모드별 구분자 도입 등) 기기를 폐기하면 옛 행이
    남는데, 대표 상태는 **가장 나쁜 봇**을 따르므로 그 유령 행 하나가 표시등을 영구히
    '통신단절'로 굳혀 버린다. 그러면 진짜 장애를 알리는 신호가 죽는다.

    살아 있는 봇을 지워도 다음 Ping 에 다시 등록되므로 파괴적인 동작은 아니다.
    받을 봇이 없어진 대기 명령은 함께 지운다.
    """
    with _db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM bots WHERE username = ? AND bot_id = ?", (username, bot_id))
        removed = c.rowcount > 0
        if removed:
            c.execute("DELETE FROM bot_commands "
                      "WHERE username = ? AND bot_id = ? AND acked_at IS NULL",
                      (username, bot_id))
        conn.commit()
    return removed


def summarize_bot_states(bots):
    """봇 목록에서 화면 대표 상태 하나를 고른다. (state, elapsed, botId)

    **가장 나쁜 상태가 이긴다.** 하나라도 살아 있으면 초록으로 칠하는 방식은
    실전봇이 죽은 것을 모의봇 Ping 이 가려 버린다 — 이 기능이 막으려는 그 오표시다.
    """
    if not bots:
        return 'never', None, None
    worst = max(bots, key=lambda b: _BOT_STATE_SEVERITY.get(b['state'], 0))
    return worst['state'], worst['elapsedSeconds'], worst['botId']


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


def _normalize_trade_class(value, *, is_system=None, fallback=''):
    """매매 분류를 확정한다.

    **비어 있다고 '시스템'으로 채우지 않는다.** 예전에는 그렇게 했는데, HTS 는
    자기 계좌에서 일어난 체결을 전부 보고한다 — 토스 앱이나 증권사 HTS 에서 사람이
    직접 낸 주문까지 포함해서다. 그것들이 전부 '시스템'으로 찍혀 실제 자동매매 성과와
    수동 매매가 한 덩어리가 됐다.

    is_system: 봇이 알려준 '시스템 트레이딩이 낸 주문인가'. True 면 분류를 '시스템'으로
      확정한다. False/None 이면 아래 폴백으로 내려간다.
    fallback: 분류를 못 정했을 때 쓸 값 (보통 같은 종목의 직전 기록에서 상속한 분류).
    """
    if is_system:
        return '시스템'
    if value is None or value == '' or isinstance(value, bool):
        return fallback
    # 숫자 코드 또는 숫자 문자열 → 이름으로 치환 (v1 하위 호환)
    try:
        code = int(value)
        return _TRADE_CLASS_MAP.get(code, '기타')
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text if text in _VALID_TRADE_CLASSES else text or fallback


def _inherit_trade_class(c, username, symbol):
    """같은 사용자·종목의 직전 기록에서 분류를 물려받는다. 없으면 빈 문자열.

    **'시스템'은 물려받지 않는다.** 예전 버전이 HTS 발 기록을 전부 '시스템'으로
    저장해 둬서, 그대로 상속하면 새로 들어오는 외부 체결까지 계속 '시스템'이 된다
    — 고치려던 오염을 상속으로 영구화하는 셈이다. 상속은 사람이 실제로 뜻을 담아
    골랐을 법한 분류(장기투자·배당투자 등)에만 걸린다.
    """
    if not symbol:
        return ''
    c.execute(
        "SELECT tradeClass FROM entries "
        "WHERE username = ? AND stockCode = ? "
        "  AND tradeClass IS NOT NULL AND tradeClass != '' AND tradeClass != '시스템' "
        "ORDER BY id DESC LIMIT 1",
        (username, symbol))
    row = c.fetchone()
    return (row['tradeClass'] or '') if row else ''


def _normalize_enum(value, valid, default, field):
    if value is None or value == '':
        return default
    text = str(value).strip().upper()
    if text not in valid:
        raise ValidationError('INVALID_FIELD',
                              f"{field} 값이 올바르지 않습니다: {value!r} "
                              f"(허용: {', '.join(sorted(valid))})", field)
    return text


def _account_key(value):
    """계좌번호 비교용 정규화 키. 하이픈·공백은 표기 차이일 뿐이므로 모두 지운다.

    등록은 '44048158-01' 로 해 두고 HTS 는 '4404815801' 로 보내는(또는 그 반대의)
    경우가 흔하다. 양쪽을 같은 규칙으로 접어서 비교해야 매핑이 어긋나지 않는다.
    """
    return re.sub(r'[\s-]', '', str(value or ''))


def _find_account_mapping(accounts, raw_sub):
    """등록된 계좌 매핑에서 계좌번호를 찾아 (등록키, 매핑값) 을 돌려줍니다.

    정확히 일치하는 키를 먼저 보고, 없으면 하이픈을 무시한 키로 다시 찾는다.
    """
    if not isinstance(accounts, dict) or not raw_sub:
        return None, None
    if raw_sub in accounts:
        return raw_sub, accounts[raw_sub]
    target = _account_key(raw_sub)
    if not target:
        return None, None
    for key, info in accounts.items():
        if _account_key(key) == target:
            return key, info
    return None, None


def _resolve_account(username, data, mappings):
    """계좌 코드/번호를 등록된 매핑 정보로 치환합니다."""
    raw_broker = _text(data.get('brokerAccount'), 'brokerAccount', 50)
    raw_sub = _text(data.get('subAccount'), 'subAccount', 50)

    accounts = mappings.get('accounts') or {}
    matched_key, acc_info = _find_account_mapping(accounts, raw_sub)

    # ⭐️ 매핑이 잡히면 사용자가 등록한 표기(하이픈 포함)를 그대로 저장한다.
    #    HTS 가 보낸 표기를 그대로 두면 같은 계좌가 두 가지 번호로 쌓인다.
    #    매핑이 없을 때만 기존과 동일하게 하이픈을 제거한 형태로 남긴다.
    sub_account = matched_key if matched_key else raw_sub.replace('-', '')

    if isinstance(acc_info, dict):
        broker = acc_info.get('broker_name') or raw_broker
        account_name = acc_info.get('alias') or _text(data.get('accountName'), 'accountName', 100)
    else:
        broker = (mappings.get('brokers') or {}).get(raw_broker, raw_broker)
        account_name = acc_info if isinstance(acc_info, str) and acc_info else \
            _text(data.get('accountName'), 'accountName', 100)
    return broker, sub_account, account_name


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

    # ⭐️ isSystem 은 3상태다 — True(자동매매), False(사람이 낸 주문), None(봇이 모름).
    #    None 과 False 를 뭉개면 분류 폴백이 무너지므로 여기서 구분해 둔다.
    is_system = data.get('isSystem')
    is_system = None if is_system is None else bool(is_system)

    trade_class = _normalize_trade_class(data.get('tradeClass'), is_system=is_system)
    if not trade_class and not is_system:
        trade_class = _inherit_trade_class(c, username, symbol)

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
        # None 을 그대로 저장해 '모른다'를 남긴다 (0 으로 눕히면 False 와 섞인다).
        'isSystem': None if is_system is None else (1 if is_system else 0),
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
        'isSystem': None if row.get('isSystem') is None else bool(row.get('isSystem')),
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


# ══════════════════════════════════════════════════════════════════════
# 봇 명령 큐
# ══════════════════════════════════════════════════════════════════════

def _command_expiry_cutoff():
    return (_now_kst() - timedelta(seconds=BOT_COMMAND_TTL_SECONDS)).isoformat()


def _take_pending_command(c, username, bot_id=None):
    """이 봇에 내려보낼 명령을 하나 집는다. 없으면 None.

    **한 번만 전달한다(at-most-once).** ack 를 받을 때까지 반복 전달하면 명령이
    반드시 실행되는 대신, 봇이 명령을 받고 ack 를 보내기 전에 재시작할 때 같은
    재동기화가 한 번 더 돈다. 서버 데이터로 보면 멱등하지만 **운용자의 의도로 보면
    멱등하지 않다** — 두 실행 사이에 운용자가 일부러 지운 기록이 되살아난다.
    지운 기록을 마음대로 되살리는 것보다, 전달이 유실됐을 때 버튼을 한 번 더 누르게
    하는 편이 훨씬 낫다.

    전달만 되고 ack 가 오지 않으면 만료될 때까지 '처리 중'으로 남았다가 '미처리'로
    바뀐다. 운용자는 그것을 보고 다시 누르면 된다.

    **대상이 지정되지 않은 명령(bot_id IS NULL)은 봇이 한 대일 때만 전달한다.**
    at-most-once 라서 여러 대가 붙어 있으면 먼저 Ping 한 아무 봇이나 채가는데,
    그 봇은 자기 로컬 DB 만 재전송하고 ack 까지 보낸다 — 정작 복구하려던 계좌는
    아무 일도 일어나지 않았는데 웹에는 '완료'로 뜨는 조용한 실패가 된다.
    전달되지 않은 명령은 '미처리'로 남아 운용자가 다시 누를 수 있다.
    """
    bot_id = _normalize_bot_id(bot_id)
    c.execute("SELECT COUNT(*) AS cnt FROM bots WHERE username = ?", (username,))
    solo = (c.fetchone()['cnt'] or 0) <= 1

    scope = "(bot_id = ? OR bot_id IS NULL)" if solo else "bot_id = ?"
    c.execute(
        "SELECT id, command, params_json FROM bot_commands "
        f"WHERE username = ? AND {scope} "
        "  AND acked_at IS NULL AND delivered_at IS NULL AND requested_at >= ? "
        "ORDER BY id LIMIT 1",
        (username, bot_id, _command_expiry_cutoff()))
    row = c.fetchone()
    if row is None:
        return None

    c.execute("UPDATE bot_commands SET delivered_at = ? WHERE id = ?",
              (_now_iso(), row['id']))
    try:
        params = json.loads(row['params_json']) if row['params_json'] else None
    except (TypeError, ValueError):
        params = None
    return {'id': row['id'], 'command': row['command'], 'params': params}


def _apply_command_ack(c, username, ack, bot_id=None):
    """봇이 보고한 처리 결과를 반영한다. 형식이 어긋나면 조용히 무시한다.

    ack 가 잘못됐다고 Ping 자체를 400 으로 되돌리면 안 된다 — 하트비트가 끊겨
    웹 화면이 '통신단절'로 바뀐다. 상태 보고가 ack 보다 중요하다.

    bot_id 를 주면 그 봇이 실제로 받아 간 명령만 마감한다. 봇이 여러 대일 때
    엉뚱한 봇의 ack 가 남의 명령을 '완료'로 덮는 것을 막는다.
    """
    if not isinstance(ack, dict):
        return
    try:
        command_id = int(ack.get('id'))
    except (TypeError, ValueError):
        return

    result = str(ack.get('result') or 'queued')[:20]
    try:
        count = int(ack.get('count') or 0)
    except (TypeError, ValueError):
        count = 0
    message = str(ack.get('message') or '')[:500]

    scope = "AND (bot_id = ? OR bot_id IS NULL)" if bot_id else ""
    args = [_now_iso(), result, count, message, command_id, username]
    if bot_id:
        args.append(bot_id)
    c.execute(
        "UPDATE bot_commands SET acked_at = ?, result = ?, result_count = ?, "
        f"result_message = ? WHERE id = ? AND username = ? AND acked_at IS NULL {scope}",
        args)


def request_bot_command(username, command, params=None, bot_id=None):
    """웹 세션에서 호출 — 봇에 내려보낼 명령을 큐에 넣는다. (명령 id)

    같은 명령이 이미 대기 중이면 새로 만들지 않고 그것을 돌려준다. 버튼을 여러 번
    눌렀다고 재동기화가 여러 번 돌 이유가 없다. **봇이 여럿이면 대상별로 따로 센다**
    — 실전봇 재동기화가 대기 중이라고 모의봇 요청까지 삼키면 안 된다.

    bot_id=None 은 '대상 미지정'이다. 봇이 한 대뿐일 때만 전달된다.
    """
    if command not in SUPPORTED_BOT_COMMANDS:
        raise ValueError(f'지원하지 않는 명령입니다: {command}')

    bot_id = str(bot_id).strip()[:BOT_ID_MAX_LEN] if bot_id else None

    with _db() as conn:
        c = conn.cursor()
        scope = "bot_id = ?" if bot_id else "bot_id IS NULL"
        args = [username, command]
        if bot_id:
            args.append(bot_id)
        c.execute(
            f"SELECT id FROM bot_commands WHERE username = ? AND command = ? AND {scope} "
            "AND acked_at IS NULL AND requested_at >= ? ORDER BY id LIMIT 1",
            (*args, _command_expiry_cutoff()))
        existing = c.fetchone()
        if existing is not None:
            return existing['id']

        c.execute(
            "INSERT INTO bot_commands (username, command, params_json, requested_at, bot_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (username, command,
             json.dumps(params, ensure_ascii=False) if params else None,
             _now_iso(), bot_id))
        conn.commit()
        return c.lastrowid


def latest_bot_command(username, command=None, bot_id=None):
    """웹 화면 표시용 — 가장 최근 명령의 상태. 없으면 None."""
    with _db() as conn:
        c = conn.cursor()
        sql = ("SELECT id, command, params_json, requested_at, delivered_at, "
               "acked_at, result, result_count, result_message, bot_id "
               "FROM bot_commands WHERE username = ?")
        params = [username]
        if command:
            sql += " AND command = ?"
            params.append(command)
        if bot_id:
            sql += " AND bot_id = ?"
            params.append(bot_id)
        c.execute(sql + " ORDER BY id DESC LIMIT 1", params)
        row = c.fetchone()

    if row is None:
        return None

    item = dict(row)
    item['botId'] = item.pop('bot_id', None)
    try:
        item['params'] = json.loads(item.pop('params_json') or 'null')
    except (TypeError, ValueError):
        item['params'] = None

    if item['acked_at']:
        item['state'] = 'done'
    elif item['requested_at'] < _command_expiry_cutoff():
        # 봇이 만료될 때까지 가져가지 않았다 — 대개 봇이 꺼져 있었던 것이다.
        item['state'] = 'expired'
    elif item['delivered_at']:
        item['state'] = 'running'
    else:
        item['state'] = 'pending'
    return item


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

    # ⭐️ 부가 필드는 검증하지 않고 잘라서 받는다. 라벨이 길다고 하트비트를 400 으로
    #    되돌리면 화면이 '통신단절'로 바뀐다 — 상태 보고가 라벨보다 중요하다.
    bot_id = _normalize_bot_id(data.get('botId'))
    label = str(data.get('label') or '').strip()[:60] or None
    message = str(data.get('message') or '').strip()[:500] or None
    is_simulated = bool(data.get('isSimulated'))

    # ⭐️ 오프셋 포함 ISO 8601 로 저장한다. 만료 판정(마지막 Ping 이후 경과 시간)에
    #    쓰이는 값이라 타임존이 빠지면 읽는 쪽에서 몇 시간씩 어긋난다.
    now = _now_iso()
    with _db() as conn:
        c = conn.cursor()
        _upsert_bot(c, username, bot_id, status_value, now,
                    label=label, is_simulated=is_simulated, message=message)

        # ⭐️ users 의 단일 칸은 하위호환으로만 유지한다. 봇이 여러 대면 마지막에
        #    Ping 한 놈으로 덮이므로 **화면 판정에는 쓰지 않는다** (bots 테이블이 원본).
        c.execute("UPDATE users SET bot_status = ?, bot_last_seen = ? WHERE username = ?",
                  (status_value, now, username))

        # ack 를 먼저 반영해야 방금 끝낸 명령을 같은 응답에서 또 내려보내지 않는다.
        _apply_command_ack(c, username, data.get('commandAck'), bot_id)

        # 봇이 멈추는 중이면 새 일감을 주지 않는다 — 받아도 처리하지 못한다.
        pending = (_take_pending_command(c, username, bot_id)
                   if status_value == 'running' else None)
        conn.commit()

    body = {
        'status': 'success',
        'updatedAt': now,
        'botId': bot_id,
        'nextPingSeconds': BOT_PING_INTERVAL_SECONDS,
        'command': pending['command'] if pending else 'none',
    }
    if pending:
        body['commandId'] = pending['id']
        body['commandParams'] = pending['params']
    return jsonify(body), 200


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
                # 기초잔고는 시스템 트레이딩이 낸 체결이 아니라 '연동 이전부터 들고
                # 있던 것'이다. 예전에는 분류가 비면 '시스템'으로 채워져 자동매매
                # 성과에 섞여 들어갔다.
                'isSystem': False,
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
