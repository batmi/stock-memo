"""schema 모듈 — 스키마 단일 소스 검증.

가장 중요한 것은 **"새로 만든 DB"와 "옛 DB 를 마이그레이션한 DB"가 같은 모양**이
되는가다. 이게 어긋나면 개발 PC 에서는 되는데 운영 DB 에서만 터지는, 재현이
가장 어려운 종류의 사고가 난다.
"""

import sqlite3

import pytest

import schema


def _dump(conn):
    tables = {}
    for (t,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ):
        tables[t] = sorted(r[1] for r in conn.execute(f"PRAGMA table_info({t})"))
    indexes = sorted(
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
        )
    )
    return tables, indexes


@pytest.fixture
def fresh():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    schema.init(conn)
    yield conn
    conn.close()


def test_init_creates_every_table(fresh):
    tables, _ = _dump(fresh)
    assert set(tables) == {
        'entries', 'users', 'password_reset_requests', 'price_cache',
        'api_keys', 'bot_commands', 'bots',
    }


def test_init_is_idempotent(fresh):
    before = _dump(fresh)
    schema.init(fresh)
    schema.init(fresh)
    assert _dump(fresh) == before


def test_create_table_and_added_columns_agree(fresh):
    """ADDED_COLUMNS 에만 적고 CREATE TABLE 에 빠뜨리면, 새 DB 와 옛 DB 의
    컬럼 순서·기본값이 갈린다. 두 목록이 서로를 덮는지 확인한다."""
    tables, _ = _dump(fresh)
    for table, column, _type in schema.ADDED_COLUMNS:
        assert column in tables[table], f"{table}.{column} 이 CREATE TABLE 에 없다"


def test_migrated_legacy_db_matches_fresh_db(fresh):
    """멀티유저·봇 연동 이전의 최소 스키마에서 출발해도 같은 결과가 나와야 한다."""
    legacy = sqlite3.connect(':memory:')
    legacy.execute('''
        CREATE TABLE entries (
            id INTEGER PRIMARY KEY, type TEXT, stockName TEXT, title TEXT,
            thoughts TEXT, date TEXT, rawDate TEXT, attachedImage TEXT,
            brokerAccount TEXT, accountName TEXT, tradeType TEXT,
            price REAL, quantity REAL
        )
    ''')
    legacy.execute('''
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        )
    ''')
    # 단일 키 시절의 price_cache — 복합 키로 갈아엎어야 한다
    legacy.execute("CREATE TABLE price_cache (code TEXT PRIMARY KEY, price REAL, updated_at TEXT)")
    legacy.commit()

    schema.init(legacy)
    assert _dump(legacy) == _dump(fresh)
    legacy.close()


def test_legacy_entries_get_assigned_to_admin(fresh):
    """멀티유저 이전에 쌓인 주인 없는 기록이 사라지면 안 된다."""
    fresh.execute("INSERT INTO users (username, password_hash, is_admin) VALUES ('boss','x',1)")
    fresh.execute("INSERT INTO entries (id, username, stockName) VALUES (1, NULL, 'A')")
    fresh.commit()

    schema.init(fresh)
    assert fresh.execute("SELECT username FROM entries WHERE id=1").fetchone()[0] == 'boss'


# ── 멱등키 UNIQUE 인덱스 ────────────────────────────────────────

def test_exec_id_unique_blocks_duplicates(fresh):
    fresh.execute("INSERT INTO entries (id, username, brokerExecutionId) VALUES (1,'u','E1')")
    fresh.commit()
    with pytest.raises(sqlite3.IntegrityError):
        fresh.execute("INSERT INTO entries (id, username, brokerExecutionId) VALUES (2,'u','E1')")


def test_exec_id_unique_allows_blank_and_null(fresh):
    """수동 입력 기록은 멱등키가 없다 — 제약 대상에서 빠져야 한다."""
    fresh.execute("INSERT INTO entries (id, username, brokerExecutionId) VALUES (1,'u','')")
    fresh.execute("INSERT INTO entries (id, username, brokerExecutionId) VALUES (2,'u','')")
    fresh.execute("INSERT INTO entries (id, username, brokerExecutionId) VALUES (3,'u',NULL)")
    fresh.execute("INSERT INTO entries (id, username, brokerExecutionId) VALUES (4,'u',NULL)")
    fresh.commit()


def test_exec_id_unique_allows_same_key_across_users(fresh):
    """다른 사용자가 같은 체결번호를 가질 수 있다 (증권사별로 번호가 겹친다)."""
    fresh.execute("INSERT INTO entries (id, username, brokerExecutionId) VALUES (1,'a','E1')")
    fresh.execute("INSERT INTO entries (id, username, brokerExecutionId) VALUES (2,'b','E1')")
    fresh.commit()


def test_init_dedupes_before_applying_unique_index():
    """이미 중복이 쌓인 DB 도 기동돼야 한다 — 가장 오래된 1건만 남긴다."""
    conn = sqlite3.connect(':memory:')
    conn.execute("CREATE TABLE entries (id INTEGER PRIMARY KEY, username TEXT, brokerExecutionId TEXT)")
    for i in (1, 2, 3):
        conn.execute("INSERT INTO entries VALUES (?, 'u', 'DUP')", (i,))
    conn.commit()

    schema.init(conn)

    rows = conn.execute("SELECT id FROM entries WHERE brokerExecutionId='DUP'").fetchall()
    assert [r[0] for r in rows] == [1]
    conn.close()


def test_dedupe_returns_removed_count(fresh):
    # UNIQUE 인덱스가 걸린 뒤에는 중복을 넣을 수 없으므로 인덱스를 잠시 내린다
    fresh.execute("DROP INDEX idx_entries_exec_unique")
    for i in (1, 2, 3):
        fresh.execute("INSERT INTO entries (id, username, brokerExecutionId) "
                      "VALUES (?, 'u', 'X')", (i,))
    fresh.commit()

    assert schema.dedupe_execution_ids(fresh) == 2   # 가장 오래된 1건만 남는다
    assert [r[0] for r in fresh.execute("SELECT id FROM entries")] == [1]


# ── price_cache 키 구조 변경 ────────────────────────────────────

def test_price_cache_rebuilt_from_single_key_schema():
    conn = sqlite3.connect(':memory:')
    conn.execute("CREATE TABLE price_cache (code TEXT PRIMARY KEY, price REAL, updated_at TEXT)")
    conn.execute("INSERT INTO price_cache VALUES ('005930', 70000, 'x')")
    conn.commit()

    schema.init(conn)

    cols = [r[1] for r in conn.execute("PRAGMA table_info(price_cache)")]
    assert 'market_type' in cols
    # 같은 종목을 KRX/NXT 로 나눠 담을 수 있어야 한다
    conn.execute("INSERT INTO price_cache VALUES ('005930','KRX',70000,'t')")
    conn.execute("INSERT INTO price_cache VALUES ('005930','NXT',70100,'t')")
    conn.commit()
    conn.close()


def test_price_cache_kept_when_already_migrated(fresh):
    """이미 복합 키인 DB 를 다시 초기화해도 캐시가 날아가면 안 된다."""
    fresh.execute("INSERT INTO price_cache VALUES ('005930','KRX',70000,'t')")
    fresh.commit()
    schema.init(fresh)
    assert fresh.execute("SELECT COUNT(*) FROM price_cache").fetchone()[0] == 1
