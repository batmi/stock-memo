"""백업 ZIP 만들기와 무결성 검증.

수동 백업(다운로드)과 매일 자동 백업이 **같은 함수로** ZIP 을 만든다. 예전에는 같은
코드가 backup_api 와 jobs 에 두 벌 있어, 한쪽에만 항목을 더하면 다른 쪽 백업으로는
그 항목이 복원되지 않았다.
"""
import json
import os
import zipfile

import config
from app.database.db import db_conn
from app.services import accounts
from app.services.users import user_dir


def write_user_backup(target, username):
    """한 사용자의 기록·첨부·계좌 매핑을 ZIP 으로 target(경로나 파일 객체)에 쓴다. 기록 수를 돌려준다.

    형식은 복원(backup_api.full_restore)이 읽는 그대로다: data.json, uploads/<파일>,
    계좌 매핑(accounts.BACKUP_ARCNAME, 구버전과 같은 파일명).
    """
    # ⭐️ 기록과 계좌 매핑을 **같은 연결에서** 읽는다. 예전 자동 백업은 닫힌 연결로
    #    accounts.load() 를 불렀고, load() 가 조회 실패를 '매핑 없음'으로 삼켜 자동 백업
    #    ZIP 에서만 account_info.json 이 빠졌다(복원하면 계좌 매핑만 사라졌다).
    with db_conn() as conn:
        rows = [dict(row) for row in conn.execute(
            "SELECT * FROM entries WHERE username = ?", (username,)).fetchall()]
        mappings = accounts.load(conn, username)

    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('data.json', json.dumps(rows, ensure_ascii=False, indent=2))
        user_folder = user_dir(config.UPLOAD_FOLDER, username)
        if user_folder and os.path.exists(user_folder):
            for root, _dirs, files in os.walk(user_folder):
                for file in files:
                    zf.write(os.path.join(root, file), arcname=os.path.join('uploads', file))
        if mappings.get('brokers') or mappings.get('accounts'):
            zf.writestr(accounts.BACKUP_ARCNAME, accounts.dumps(mappings))
    return len(rows)


def verify_backup_zip(filepath, expected_count):
    """백업 ZIP 파일의 무결성을 검증합니다.

    1) ZIP 아카이브가 손상되지 않고 열람 가능한지 (CRC 검사)
    2) data.json 항목이 존재하고 정상적으로 파싱되는지
    3) 복원될 레코드 수가 백업 시점의 레코드 수와 일치하는지

    (성공 여부 bool, 메시지 str) 튜플을 반환합니다.
    """
    try:
        with zipfile.ZipFile(filepath, 'r') as zf:
            corrupt = zf.testzip()
            if corrupt is not None:
                return False, f"손상된 압축 항목: {corrupt}"
            if 'data.json' not in zf.namelist():
                return False, "data.json 항목이 없습니다."
            with zf.open('data.json') as f:
                data = json.loads(f.read().decode('utf-8'))
            if not isinstance(data, list):
                return False, "data.json 형식이 올바르지 않습니다."
            if len(data) != expected_count:
                return False, f"레코드 수 불일치 (기대 {expected_count}건, 실제 {len(data)}건)"
        return True, f"{len(data)}건 검증 통과"
    except Exception as e:
        return False, str(e)
