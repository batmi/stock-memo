"""1회성 데이터 이관 — 옛 저장 형식을 지금 형식으로 한 번 옮기고 나면 다시 할 일이 없다.

예전에는 이 셋이 흩어져 있었다. 둘은 진입점(backend_app)에, 하나는 **회원가입
핸들러 안**(auth.signup)에 들어 있었다. 인증 라우트가 파일을 읽고 이미지를
추출하고 기록을 넣고 있으면, 가입 규칙을 고치는 사람이 이관 코드를 지나쳐야 하고
이관을 찾는 사람은 그것이 가입 화면에 있으리라 짐작하기 어렵다.

모두 **실패해도 서버를 세우지 않는다.** 이관이 실패하면 옛 데이터가 그대로 남아
다음 기회에 다시 시도되거나, 사용자가 화면에서 다시 등록할 수 있다.

값의 의미를 아는 이관 두 가지(API 키 해시화, 종목코드 대문자화)는 그 규칙을 아는
모듈(trading_api.keys, entry_logic)이 갖는다. 여기 있는 것은 옛 **저장소**에서
옮겨 오는 일들이다.
"""

import json
import logging
import os
import shutil

import config
from app.database import entry_logic
from app.database.db import db_conn, get_db
from app.services import accounts, images

log = logging.getLogger('migrations')


def import_legacy_json(conn, username):
    """단일 JSON 파일 시절의 기록을 첫 관리자 계정으로 옮긴다. 옮긴 건수를 돌려준다.

    DB 에 기록이 하나도 없을 때만 한다 — 이미 쓰고 있는 DB 에 옛 파일을 덧붙이면
    같은 기록이 두 벌이 된다. 중간에 실패하면 아무것도 넣지 않은 상태로 되돌린다.
    """
    c = conn.cursor()
    if not os.path.exists(config.DATA_FILE):
        return 0
    if c.execute("SELECT COUNT(*) FROM entries").fetchone()[0] != 0:
        return 0

    log.info("🔄 기존 JSON 데이터를 SQLite 데이터베이스로 마이그레이션 합니다...")
    try:
        with open(config.DATA_FILE, 'r', encoding='utf-8') as f:
            old_data = json.load(f)
        for entry in old_data:
            img_url = images.process_image(username, entry.get('attachedImage'), entry.get('id'))
            entry = images.extract_inline_images(username, entry)
            entry_logic.insert_entry(c, username, entry, attached_image=img_url)
        conn.commit()
    except Exception as e:
        conn.rollback()
        log.error(f"❌ 마이그레이션 중 오류 발생: {e}")
        return 0
    log.info("✅ 데이터 마이그레이션 완료! (이제부터 db/journal.db와 uploads 폴더를 사용합니다)")
    return len(old_data)


def migrate_account_mappings():
    """레거시 json/<username>/account_info.json → users.account_mappings."""
    try:
        with db_conn() as conn:
            accounts.migrate_json_files(conn, config.JSON_DIR, log)
    except Exception as e:
        # 실패하면 해당 사용자는 빈 매핑으로 시작하고, 화면에서 다시 등록하면
        # 그 값이 DB 에 저장된다.
        log.error(f"계좌 매핑 이관 중 오류: {e}")


def migrate_inline_images():
    """기록 본문에 내장된 base64 이미지를 파일로 추출한다(초기 로딩 응답 크기 유지).

    실행 전 DB 원본을 backup/ 에 한 번 복사해 둔다. 이미 끝났다면 즉시 통과한다.
    """
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT id, username, thoughts FROM entries WHERE thoughts LIKE '%data:image%'")
        rows = c.fetchall()
        if not rows:
            return

        # 마이그레이션 직전 DB 원본을 1회 백업 (이미 있으면 생략)
        safety_copy = os.path.join(config.BACKUP_DIR, 'journal_pre_imgmigration.db')
        if not os.path.exists(safety_copy):
            # WAL 에 남은 변경분까지 본 파일에 합친 뒤 복사 (스냅샷 무결성 보장)
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            shutil.copy2(config.DB_FILE, safety_copy)
            log.info(f"🛡️ 이미지 마이그레이션 전 DB 백업 생성: {safety_copy}")

        migrated = 0
        for row in rows:
            username = row['username'] or ''
            new_entry = images.extract_inline_images(username, {'thoughts': row['thoughts']})
            if new_entry['thoughts'] != row['thoughts']:
                c.execute("UPDATE entries SET thoughts = ? WHERE id = ?", (new_entry['thoughts'], row['id']))
                migrated += 1
        conn.commit()
        if migrated:
            log.info(f"🔄 본문 내장 base64 이미지 {migrated}건을 파일로 추출했습니다 (초기 로딩 최적화).")
    except Exception as e:
        log.error(f"❌ 이미지 마이그레이션 중 오류 발생: {e}", exc_info=True)
    finally:
        conn.close()
