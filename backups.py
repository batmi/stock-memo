"""백업 무결성 검증 로직 (DB 비의존 순수 함수)."""
import json
import zipfile


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
