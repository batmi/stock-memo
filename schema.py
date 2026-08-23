"""DB 스키마 단일 소스 — 모든 테이블·컬럼·인덱스를 여기서만 정의한다.

**왜 모았나.** 예전에는 같은 `entries` 테이블을 두 곳이 각자 손댔다.
`backend_app.init_db()` 가 기본 컬럼과 `idx_entries_*` 인덱스를,
`trading_api.migrate_schema()` 가 봇 연동용 확장 컬럼과 `idx_entries_user_src`,
멱등키 UNIQUE 인덱스를 만들었다. 그래서 "새 컬럼을 어디에 추가해야 하는가"가
규칙이 아니라 관례로만 남아 있었고, 스키마 전체를 한눈에 볼 방법이 없었다.

**경계.** 이 모듈은 스키마의 *모양*(테이블/컬럼/인덱스)만 책임진다.
값의 의미를 알아야 하는 *데이터* 이관 — 평문 API 키를 해시로 바꾸는 일,
계좌 매핑을 파일에서 옮기는 일 — 은 그 도메인을 아는 모듈이 계속 갖는다.
(`trading_api.migrate_data`, `accounts.migrate_json_files`)

**규칙.** 이미 배포된 DB 가 있으므로 테이블을 다시 만들 수는 없다. 새 컬럼은
`ADDED_COLUMNS` 에 한 줄 추가하고 `CREATE TABLE` 문에도 함께 적는다. 앞의 것은
기존 DB 를, 뒤의 것은 새 DB 를 위한 것이라 둘 다 필요하다.
"""

import sqlite3

# ---------------------------------------------------------------------------
# 테이블 (새 DB 용 — 기존 DB 에는 IF NOT EXISTS 로 아무 영향이 없다)
# ---------------------------------------------------------------------------

CREATE_TABLES = [
    # 매매 기록·메모 본체. 웹 화면과 봇(/api/v1)이 같은 테이블에 쓴다.
    '''
    CREATE TABLE IF NOT EXISTS entries (
        id INTEGER PRIMARY KEY,
        username TEXT,
        type TEXT,
        stockName TEXT,
        stockCode TEXT,
        title TEXT,
        thoughts TEXT,
        date TEXT,
        rawDate TEXT,
        attachedImage TEXT,
        brokerAccount TEXT,
        subAccount TEXT,
        accountName TEXT,
        tradeType TEXT,
        price REAL,
        quantity REAL,
        createdAt TEXT,
        updatedAt TEXT,
        tags TEXT,
        attachedFile TEXT,
        attachedFileName TEXT,
        isHidden INTEGER DEFAULT 0,
        brokerExecutionId TEXT,
        currency TEXT,
        exchange TEXT,
        assetType TEXT,
        tradeClass TEXT DEFAULT '',
        isSimulated INTEGER DEFAULT 0,
        tradeStatus TEXT,
        confidence TEXT,
        orderOrigin TEXT,
        source TEXT,
        orderId TEXT,
        originalOrderId TEXT,
        realizedPnl REAL,
        realizedPnlRate REAL,
        fee REAL,
        tax REAL,
        strategyScore REAL,
        stopLossRate REAL,
        executedAtUtc TEXT,
        tradeDate TEXT,
        needsReview INTEGER DEFAULT 0,
        isSystem INTEGER
    )
    ''',

    '''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        preferences TEXT,
        account_mappings TEXT,
        created_at TEXT,
        last_login_at TEXT,
        is_allowed INTEGER DEFAULT 1,
        is_admin INTEGER DEFAULT 0,
        api_key TEXT,
        bot_status TEXT,
        bot_last_seen TEXT,
        session_epoch INTEGER DEFAULT 0,
        must_change_password INTEGER DEFAULT 0
    )
    ''',

    # ⭐️ 비밀번호 재설정 요청함. 로그인 화면에서 누구나 넣을 수 있으므로
    #    username 을 기본키로 두어 같은 계정이 여러 번 눌러도 한 줄만 쌓이게 한다.
    #    (무한 증가 방지 — 별도 정리 작업이 필요 없다)
    '''
    CREATE TABLE IF NOT EXISTS password_reset_requests (
        username TEXT PRIMARY KEY,
        requested_at TEXT NOT NULL,
        note TEXT,
        request_count INTEGER NOT NULL DEFAULT 1
    )
    ''',

    # ⭐️ 시간외 단일가(NXT) 전일 종가 유지를 위한 캐시 (KRX/NXT 분리 저장)
    '''
    CREATE TABLE IF NOT EXISTS price_cache (
        code TEXT,
        market_type TEXT DEFAULT 'KRX',
        price REAL,
        updated_at TEXT,
        PRIMARY KEY (code, market_type)
    )
    ''',

    '''
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
    ''',

    # 웹에서 누른 지시를 봇이 가져갈 때까지 보관하는 큐.
    #  이벤트가 아니라 '행'으로 남겨야 한다 — 버튼을 누른 순간 봇이 꺼져 있어도
    #  다시 켜졌을 때 전달되어야 하고, 처리 결과를 웹에 보여줘야 하기 때문이다.
    #
    #  bot_id 가 NULL 이면 '봇을 지정하지 않은 구버전 요청'이라 봇이 한 대일 때만
    #  전달한다 (_take_pending_command 참고). 여러 대가 붙어 있는데 대상을 모르는
    #  명령을 아무 봇에게나 주면, 엉뚱한 계좌가 재동기화되고 그 봇이 ack 까지 보내
    #  웹에는 '완료'로 뜬다 — 운용자가 알아챌 수 없는 실패다.
    '''
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
        result_message TEXT,
        bot_id TEXT
    )
    ''',

    # 봇 인스턴스별 하트비트. users.bot_status 는 사용자당 한 칸뿐이라 봇이 여러
    # 대면 마지막에 Ping 한 놈이 앞의 상태를 덮어썼다.
    '''
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
    ''',
]


# ---------------------------------------------------------------------------
# 기존 DB 에 뒤늦게 추가된 컬럼 (ALTER TABLE — 이미 있으면 조용히 건너뛴다)
# ---------------------------------------------------------------------------

ADDED_COLUMNS = [
    # (table, column, type)
    ('entries', 'createdAt', 'TEXT'),
    ('entries', 'updatedAt', 'TEXT'),
    ('entries', 'stockCode', 'TEXT'),
    ('entries', 'tags', 'TEXT'),
    ('entries', 'attachedFile', 'TEXT'),
    ('entries', 'attachedFileName', 'TEXT'),
    ('entries', 'subAccount', 'TEXT'),
    ('entries', 'username', 'TEXT'),
    ('entries', 'isHidden', 'INTEGER DEFAULT 0'),
    ('entries', 'brokerExecutionId', 'TEXT'),
    ('entries', 'currency', 'TEXT'),
    ('entries', 'exchange', 'TEXT'),
    ('entries', 'assetType', 'TEXT'),
    ('entries', 'tradeClass', 'TEXT'),

    # ── 시스템 트레이딩(/api/v1) 연동으로 추가된 확장 컬럼 ──
    ('entries', 'isSimulated', 'INTEGER DEFAULT 0'),
    ('entries', 'tradeStatus', 'TEXT'),
    ('entries', 'confidence', 'TEXT'),
    ('entries', 'orderOrigin', 'TEXT'),
    ('entries', 'source', 'TEXT'),
    ('entries', 'orderId', 'TEXT'),
    ('entries', 'originalOrderId', 'TEXT'),
    ('entries', 'realizedPnl', 'REAL'),
    ('entries', 'realizedPnlRate', 'REAL'),
    ('entries', 'fee', 'REAL'),
    ('entries', 'tax', 'REAL'),
    ('entries', 'strategyScore', 'REAL'),
    ('entries', 'stopLossRate', 'REAL'),
    ('entries', 'executedAtUtc', 'TEXT'),
    ('entries', 'tradeDate', 'TEXT'),
    ('entries', 'needsReview', 'INTEGER DEFAULT 0'),
    # ⭐️ 시스템 트레이딩이 낸 주문인가. **DEFAULT 를 두지 않는다** — 0/1 만으로는
    #    '시스템이 아니다'와 '봇이 알려주지 않았다'가 구분되지 않는데, 분류 폴백이
    #    바로 그 구분에 걸려 있다. 모르면 NULL 로 남아야 한다.
    ('entries', 'isSystem', 'INTEGER'),

    ('users', 'preferences', 'TEXT'),
    ('users', 'account_mappings', 'TEXT'),
    ('users', 'created_at', 'TEXT'),
    ('users', 'last_login_at', 'TEXT'),
    ('users', 'is_allowed', 'INTEGER DEFAULT 1'),
    ('users', 'is_admin', 'INTEGER DEFAULT 0'),
    ('users', 'api_key', 'TEXT'),
    ('users', 'bot_status', 'TEXT'),
    ('users', 'bot_last_seen', 'TEXT'),
    # ⭐️ 비밀번호를 바꾸면 이 값을 올려 기존 세션을 한꺼번에 무효화한다.
    ('users', 'session_epoch', 'INTEGER DEFAULT 0'),
    # ⭐️ 관리자가 임시 비밀번호로 초기화하면 1 — 다음 로그인에서 변경을 강제한다.
    ('users', 'must_change_password', 'INTEGER DEFAULT 0'),

    ('bot_commands', 'bot_id', 'TEXT'),
]


# ---------------------------------------------------------------------------
# 인덱스 (통계·필터·정렬 가속)
# ---------------------------------------------------------------------------

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_entries_username ON entries(username)",
    "CREATE INDEX IF NOT EXISTS idx_entries_user_type ON entries(username, type)",
    "CREATE INDEX IF NOT EXISTS idx_entries_user_stock ON entries(username, stockName)",
    "CREATE INDEX IF NOT EXISTS idx_entries_exec_id ON entries(brokerExecutionId)",
    "CREATE INDEX IF NOT EXISTS idx_entries_user_src ON entries(username, source)",
    "CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(username)",
    "CREATE INDEX IF NOT EXISTS idx_bots_user ON bots(username)",
    "CREATE INDEX IF NOT EXISTS idx_bot_commands_pending ON bot_commands(username, acked_at, id)",
]

# ⭐️ 멱등키 UNIQUE 제약. 비유니크 idx_entries_exec_id 만으로는 동시 요청이
#    check-then-insert 사이를 파고들면 중복이 그대로 들어갔다.
#    빈 문자열/NULL(수동 입력 기록)은 제약 대상에서 제외한다.
EXEC_ID_UNIQUE_INDEX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_exec_unique "
    "ON entries(username, brokerExecutionId) "
    "WHERE brokerExecutionId IS NOT NULL AND brokerExecutionId != ''"
)


# ---------------------------------------------------------------------------
# 적용
# ---------------------------------------------------------------------------

def init(conn, logger=None):
    """스키마를 현재 정의에 맞춘다. 몇 번 호출해도 안전하다(멱등)."""
    conn.execute('PRAGMA journal_mode=WAL;')  # DB 파일에 영속 — 여기서 1회만
    c = conn.cursor()

    # price_cache 는 컬럼 추가로는 못 고치는 키 구조 변경이라 먼저 처리한다.
    _rebuild_price_cache_if_legacy(c, logger)

    for ddl in CREATE_TABLES:
        c.execute(ddl)

    for table, column, coltype in ADDED_COLUMNS:
        try:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
        except sqlite3.OperationalError:
            pass  # 이미 존재

    for ddl in INDEXES:
        try:
            c.execute(ddl)
        except sqlite3.OperationalError:
            pass

    conn.commit()

    _create_exec_id_unique_index(conn, logger)
    _backfill_owner_username(conn)
    conn.commit()


def _rebuild_price_cache_if_legacy(c, logger):
    """단일 키(code) 시절의 price_cache 를 복합 키(code, market_type)로 갈아엎는다.

    캐시라 버려도 되는 데이터이므로 DROP 후 재생성이 가장 단순하고 안전하다.
    """
    try:
        c.execute("SELECT market_type FROM price_cache LIMIT 1")
    except sqlite3.OperationalError:
        c.execute("DROP TABLE IF EXISTS price_cache")
        if logger:
            logger.info("🔄 price_cache 테이블을 KRX/NXT 분리 저장 스키마로 마이그레이션합니다.")


def _create_exec_id_unique_index(conn, logger):
    """멱등키 UNIQUE 인덱스를 건다. 중복이 남아 있으면 정리하고 한 번 더 시도한다.

    ⚠️ 여기서 잡아야 하는 예외는 IntegrityError 다. 중복 데이터 때문에 UNIQUE
    인덱스를 못 만들 때 SQLite 가 던지는 것이 그것이다. 예전 코드는
    OperationalError 만 잡고 있어서 '정리 후 재시도' 경로가 한 번도 실행되지
    않았고, 중복이 쌓인 DB 는 서버 기동 자체가 실패했다.
    """
    c = conn.cursor()
    try:
        c.execute(EXEC_ID_UNIQUE_INDEX)
        conn.commit()
        return
    except sqlite3.DatabaseError as e:
        first_error = e

    conn.rollback()
    dedupe_execution_ids(conn, logger)
    try:
        c.execute(EXEC_ID_UNIQUE_INDEX)
        conn.commit()
    except sqlite3.DatabaseError:
        conn.rollback()
        if logger:
            logger.error(f"⚠️ brokerExecutionId UNIQUE 인덱스 생성 실패: {first_error}")


def dedupe_execution_ids(conn, logger=None):
    """중복 멱등키를 정리한다 (가장 오래된 1건만 보존). 지운 건수를 돌려준다."""
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
    if removed and logger:
        logger.info(f"🔄 중복 brokerExecutionId 기록 {removed}건을 정리했습니다.")
    return removed


def _backfill_owner_username(conn):
    """멀티유저 이전에 쌓인 주인 없는 기록을 최고 관리자 소유로 붙인다."""
    try:
        conn.execute(
            "UPDATE entries SET username = "
            "(SELECT username FROM users WHERE is_admin = 1 LIMIT 1) "
            "WHERE username IS NULL"
        )
    except sqlite3.OperationalError:
        pass
