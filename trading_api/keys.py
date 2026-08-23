"""API 키 저장소 — 해시 보관, 발급/폐기, 레거시 평문 키 이관.

⭐️ 키 원문은 발급 순간에만 존재하고 DB 에는 해시와 표시용 앞자리만 남는다.
   DB 가 통째로 새더라도 그 키로 API 를 부를 수는 없어야 하기 때문이다.
"""

import hashlib
import secrets
import sqlite3

from db import db_conn

from .common import (
    API_KEY_PREFIX, DEFAULT_SCOPES, LAST_USED_WRITE_INTERVAL_SECONDS,
    _log, _now_kst, _now_kst_str, _parse_stored_dt,
)

def _should_touch_last_used(stored_value, now=None):
    """last_used_at 을 지금 갱신해야 하는지 여부.

    저장된 값이 없거나 읽을 수 없으면 갱신한다(첫 사용 기록은 남겨야 한다).
    """
    last = _parse_stored_dt(stored_value)
    if last is None:
        return True
    elapsed = ((now or _now_kst()) - last).total_seconds()
    # 시계가 뒤로 간 경우(elapsed < 0)에도 갱신해 값이 미래에 굳는 것을 막는다.
    return not (0 <= elapsed < LAST_USED_WRITE_INTERVAL_SECONDS)


def _hash_key(raw):
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _display_prefix(raw):
    """키 식별용 앞자리. 전체 키는 저장하지 않으므로 목록에서 이걸로 구분한다."""
    return raw[:12] if len(raw) > 12 else raw


def _generate_api_key():
    return API_KEY_PREFIX + secrets.token_urlsafe(32)


def list_api_keys(username):
    with db_conn() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT id, key_prefix, label, scopes, created_at, last_used_at, revoked_at "
            "FROM api_keys WHERE username = ? ORDER BY id DESC", (username,))
        return [dict(r) for r in c.fetchall()]


def create_api_key(username, label=None, scopes=None):
    """새 키를 발급합니다. **평문은 이 반환값에서 단 한 번만 볼 수 있습니다.**"""
    raw = _generate_api_key()
    scope_str = ' '.join(scopes) if scopes else DEFAULT_SCOPES
    with db_conn() as conn:
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
    with db_conn() as conn:
        c = conn.cursor()
        c.execute("UPDATE api_keys SET revoked_at = ? WHERE id = ? AND username = ? "
                  "AND revoked_at IS NULL", (_now_kst_str(), key_id, username))
        changed = c.rowcount
        conn.commit()
    return changed > 0


def revoke_all_api_keys(username):
    with db_conn() as conn:
        c = conn.cursor()
        c.execute("UPDATE api_keys SET revoked_at = ? WHERE username = ? AND revoked_at IS NULL",
                  (_now_kst_str(), username))
        conn.commit()


# ══════════════════════════════════════════════════════════════════════
# 데이터 이관
# ══════════════════════════════════════════════════════════════════════
#
# ⭐️ 테이블·컬럼·인덱스 정의는 이 패키지가 갖지 않는다. `schema.py` 가 단독으로
#    소유한다. 여기에는 값의 의미를 알아야만 할 수 있는 **데이터** 이관만 남긴다.

def migrate_data(conn):
    """봇 도메인 지식이 필요한 데이터 이관. init_db() 가 스키마 적용 뒤 호출한다."""
    _migrate_legacy_api_keys(conn)


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
    if migrated:
        _log().info(
            f"🔐 기존 API 키 {migrated}건을 해시 저장소로 이관하고 평문을 삭제했습니다.")

