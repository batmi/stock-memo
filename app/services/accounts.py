"""계좌 매핑 도메인 모듈 — 정규화 규칙 + 사용자별 매핑 저장소.

이 모듈이 생긴 이유는 두 가지다.

1) **정규화 규칙의 소유자를 정한다.**
   계좌번호를 접어서 비교하는 규칙(`account_key`)은 웹 UI·봇 API(/api/v1)·통계가
   모두 같은 값을 내야 한다. 예전에는 이 규칙이 `trading_api._account_key` 라는
   **비공개 함수**로 있었고 `backend_app` 이 그걸 직접 꺼내 썼다. 봇 연동 모듈이
   웹 화면 통계의 의존 대상이 되는 건 계층이 뒤집힌 것이라, 공용 도메인 규칙은
   여기로 올린다.

2) **저장 위치를 DB 로 통일한다.**
   예전에는 `json/<username>/account_info.json` 파일 하나였다. 다른 데이터는 전부
   SQLite 인데 이것만 파일이라 네 가지 문제가 한꺼번에 따라왔다.
     - 쓰기에 락이 없어 동시 저장 시 한쪽이 통째로 사라진다
     - 백업/복원이 "DB + 파일" 두 갈래로 갈라진다
     - 통계 요청마다 파일 I/O 가 발생한다
     - 정적 서빙 경로에 그대로 노출됐다 (`/json/<타인>/account_info.json`)
   이제 `users.account_mappings` 컬럼(JSON TEXT)에 저장한다. `users.preferences`
   와 같은 방식이라 백업/복원도 DB 한 갈래로 끝난다.

   백업 ZIP 안의 `account_info.json` 항목은 **그대로 유지한다.** 예전 백업을
   복원할 수 있어야 하고, 파일 하나만 꺼내 읽는 사용자도 있기 때문이다.
   (DB → ZIP 으로 내보내고, ZIP → DB 로 되돌린다)
"""

import json
import os
import re

from app.database.db import db_conn

EMPTY_MAPPINGS = {"brokers": {}, "accounts": {}}

# 백업 ZIP 안에서 계좌 매핑이 담기는 파일명 (구버전 백업과의 호환을 위해 고정)
BACKUP_ARCNAME = 'account_info.json'


def empty_mappings():
    """매번 새 딕셔너리를 돌려준다 (호출자가 수정해도 상수가 오염되지 않도록)."""
    return {"brokers": {}, "accounts": {}}


def account_key(value):
    """계좌번호 비교용 정규화 키. 하이픈·공백은 표기 차이일 뿐이므로 모두 지운다.

    등록은 '44048158-01' 로 해 두고 HTS 는 '4404815801' 로 보내는(또는 그 반대의)
    경우가 흔하다. 양쪽을 같은 규칙으로 접어서 비교해야 매핑이 어긋나지 않는다.
    프런트엔드 `static/script.js` 의 `accountKey()` 가 같은 규칙을 구현한다.
    """
    return re.sub(r'[\s-]', '', str(value or ''))


def find_account_mapping(accounts, raw_sub):
    """등록된 계좌 매핑에서 계좌번호를 찾아 (등록키, 매핑값) 을 돌려준다.

    정확히 일치하는 키를 먼저 보고, 없으면 하이픈을 무시한 키로 다시 찾는다.
    """
    if not isinstance(accounts, dict) or not raw_sub:
        return None, None
    if raw_sub in accounts:
        return raw_sub, accounts[raw_sub]
    target = account_key(raw_sub)
    if not target:
        return None, None
    for key, info in accounts.items():
        if account_key(key) == target:
            return key, info
    return None, None


def normalize(data):
    """어떤 입력이 들어와도 {'brokers': dict, 'accounts': dict} 형태로 맞춘다.

    손상된 JSON·구버전 형식 때문에 화면 전체가 죽는 일을 막는 최소 방어선이다.
    """
    if not isinstance(data, dict):
        return empty_mappings()
    brokers = data.get('brokers')
    accounts = data.get('accounts')
    return {
        "brokers": brokers if isinstance(brokers, dict) else {},
        "accounts": accounts if isinstance(accounts, dict) else {},
    }


# ---------------------------------------------------------------------------
# 저장소 (users.account_mappings)
# ---------------------------------------------------------------------------

def load(conn, username):
    """사용자의 계좌 매핑을 DB 에서 읽는다. 없거나 깨졌으면 빈 매핑."""
    if not username:
        return empty_mappings()
    try:
        row = conn.execute(
            "SELECT account_mappings FROM users WHERE username = ?", (username,)
        ).fetchone()
    except Exception:
        return empty_mappings()
    if not row or not row[0]:
        return empty_mappings()
    try:
        return normalize(json.loads(row[0]))
    except (ValueError, TypeError):
        return empty_mappings()


def load_for(username):
    """연결까지 직접 열어 사용자 매핑을 읽는다.

    ⭐️ 예전에는 이 3줄이 api.py 안에 있었고, 봇 API(trading_api)는 그걸 쓰려고
       backend_app 에서 함수를 **주입**받았다. 라우트 모듈에 도메인 조회가 있으니
       다른 도메인 모듈이 그걸 참조할 방법이 주입밖에 없었던 것이다. 매핑을 아는
       모듈이 갖고 있으면 양쪽 다 그냥 임포트하면 된다.
    """
    with db_conn() as conn:
        return load(conn, username)


class UnknownUserError(LookupError):
    """계정 행이 없어 매핑을 저장할 곳이 없을 때."""


def save(conn, username, data):
    """사용자의 계좌 매핑을 DB 에 저장한다. commit 은 호출자 몫.

    저장된 정규화 결과를 돌려주므로, 호출자가 응답에 그대로 쓸 수 있다.

    계정 행이 없으면 UPDATE 가 조용히 0건 처리되고 사용자는 "저장됐다"고 믿게
    되므로, 그 경우는 예외로 알린다. (파일 저장 시절에는 계정 존재 여부와 무관하게
    파일이 만들어져 이 불일치가 드러나지 않았다)
    """
    normalized = normalize(data)
    cur = conn.execute(
        "UPDATE users SET account_mappings = ? WHERE username = ?",
        (json.dumps(normalized, ensure_ascii=False), username),
    )
    if cur.rowcount == 0:
        raise UnknownUserError(username)
    return normalized


def dumps(mappings):
    """백업 ZIP 에 넣을 JSON 문자열 (구버전 account_info.json 과 같은 모양)."""
    return json.dumps(normalize(mappings), ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# '금액 계산 제외' 판정
# ---------------------------------------------------------------------------

def _alias_of(info):
    """매핑값에서 별칭만 꺼낸다 (구버전은 문자열 하나가 곧 별칭이었다)."""
    if isinstance(info, dict):
        return (info.get('alias') or '').strip()
    return str(info).strip() if info else ''


def known_account_keys(mappings):
    """등록된 모든 계좌의 정규화된 계좌번호 집합 (제외 여부와 무관)."""
    accounts = normalize(mappings).get('accounts') or {}
    return {key for key in (account_key(code) for code in accounts) if key}


def excluded_accounts(mappings):
    """'금액 계산 제외'로 체크한 계좌의 (정규화된 계좌번호 집합, 별칭 집합).

    계좌 별칭은 언제든 바꿀 수 있으므로 이름이 아니라 등록된 계좌번호로 판정하는
    것이 기본이다. 다만 계좌번호 없이 이름만 적힌 수기 기록도 있어 별칭도 함께 본다.

    ⭐️ 단, 같은 별칭이 제외 계좌와 포함 계좌에 함께 쓰이면(증권사마다 '일반계좌'
    처럼) 이름만으로는 어느 계좌인지 구별할 수 없다. 그 별칭은 이름 대조 대상에서
    빼고 계좌번호로만 판정한다 — 그러지 않으면 토스증권 '일반계좌' 하나를 제외했을
    때 한국투자증권 '일반계좌'까지 함께 빠진다.
    """
    accounts = normalize(mappings).get('accounts') or {}
    codes, aliases, kept_aliases = set(), set(), set()
    for code, info in accounts.items():
        alias = _alias_of(info)
        if isinstance(info, dict) and info.get('exclude_from_stats'):
            key = account_key(code)
            if key:
                codes.add(key)
            if alias:
                aliases.add(alias)
        elif alias:
            kept_aliases.add(alias)
    return codes, aliases - kept_aliases


def is_excluded_row(row, codes, aliases, known_codes=None):
    """기록 한 건이 '금액 계산 제외' 계좌에 속하는지.

    `known_codes`(등록된 모든 계좌번호)를 주면, 계좌번호로 계좌가 특정되는 기록은
    그 계좌의 설정만 따른다. 이름 대조는 계좌번호가 없거나 등록되지 않은 기록
    (수기 입력 등)에만 쓰는 마지막 수단이다.
    """
    sub = account_key(row.get('subAccount'))
    if sub and sub in codes:
        return True
    if sub and known_codes and sub in known_codes:
        return False
    name = (row.get('accountName') or '').strip()
    return bool(name and name in aliases)


# ---------------------------------------------------------------------------
# 1회성 이관 (json/<username>/account_info.json → users.account_mappings)
# ---------------------------------------------------------------------------

def migrate_json_files(conn, json_dir, logger=None):
    """레거시 JSON 파일에 남은 매핑을 DB 로 옮긴다.

    - DB 에 이미 값이 있는 사용자는 건드리지 않는다 (DB 가 항상 우선)
    - 파일은 지우지 않고 `.migrated` 로 이름만 바꿔 둔다. 이관 결과가 이상할 때
      되돌릴 수 있어야 하고, 지워 버리면 확인할 방법이 없다.

    옮긴 사용자 수를 돌려준다.
    """
    if not os.path.isdir(json_dir):
        return 0

    moved = 0
    for username in sorted(os.listdir(json_dir)):
        user_path = os.path.join(json_dir, username)
        src = os.path.join(user_path, BACKUP_ARCNAME)
        if not os.path.isfile(src):
            continue

        row = conn.execute(
            "SELECT account_mappings FROM users WHERE username = ?", (username,)
        ).fetchone()
        if row is None:
            continue  # 계정이 없는 잔재 폴더 — 그대로 둔다
        if row[0]:
            continue  # 이미 DB 에 값이 있다

        try:
            with open(src, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            if logger:
                logger.warning(f"계좌 매핑 이관 건너뜀({username}): 파일을 읽지 못함 - {e}")
            continue

        save(conn, username, data)
        moved += 1
        try:
            os.replace(src, src + '.migrated')
        except OSError as e:
            if logger:
                logger.warning(f"계좌 매핑 원본 파일 정리 실패({username}): {e}")

    if moved:
        conn.commit()
        if logger:
            logger.info(f"🔄 계좌 매핑 {moved}건을 JSON 파일에서 DB 로 이관했습니다.")
    return moved
