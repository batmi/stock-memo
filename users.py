"""계정 신원 규칙 — 사용자명 검증, 비밀번호 정책, 세션 epoch, 사용자별 폴더 경로.

로그인 라우트(`auth.py`)와 관리자 라우트(`admin.py`), 백그라운드 백업 잡이 모두
같은 규칙을 써야 한다. 규칙이 라우트 파일 안에 있으면 그중 하나만 고쳐지고
나머지는 옛 규칙으로 남는다 — 특히 사용자명 검증은 그렇게 어긋나면 곧바로
경로 탈출로 이어진다.
"""

import logging
import os
import re
import secrets
import string
import threading

from db import db_conn

log = logging.getLogger('users')

# ⭐️ 사용자명은 그대로 파일 경로에 들어간다(uploads/<username>, backup/<username> 등).
#    예전에는 아무 검증이 없어 '../../../tmp/x' 같은 이름으로 가입할 수 있었고,
#    자동 백업 잡이 그 경로에 폴더를 만들고 7일 지난 파일을 지웠다.
#    (가입은 로그인 없이 가능하므로 인증 없이 서버 파일이 삭제될 수 있었다)
USERNAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{2,31}$')

# ⭐️ 비밀번호 최소 정책. scrypt 로 해시해도 '1234' 같은 값은 막지 못한다.
#    tools/reset_password.py 가 이미 8자를 강제하고 있어 웹만 무정책이던 것을 맞춘다.
PASSWORD_MIN_LENGTH = 8

# 경로 구분자·상위 참조는 규칙에서 이미 걸리지만, 이름이 바뀌어도 안전하도록 따로 막는다.
_UNSAFE_NAME_PARTS = ('..', '/', '\\', '\x00')


def is_valid_username(name):
    """가입 가능한 사용자명인지. 영문/숫자로 시작하는 3~32자."""
    if not name or not isinstance(name, str):
        return False
    if any(bad in name for bad in _UNSAFE_NAME_PARTS):
        return False
    return bool(USERNAME_RE.fullmatch(name))


def validate_password(password, username=None):
    """비밀번호 정책 위반 시 한국어 사유(str), 통과하면 None."""
    if not password or len(password) < PASSWORD_MIN_LENGTH:
        return f"비밀번호는 {PASSWORD_MIN_LENGTH}자 이상이어야 합니다."
    if len(password) > 256:
        return "비밀번호가 너무 깁니다. (최대 256자)"
    kinds = sum([
        any(ch.islower() for ch in password),
        any(ch.isupper() for ch in password),
        any(ch.isdigit() for ch in password),
        any(not ch.isalnum() for ch in password),
    ])
    if kinds < 2:
        return "비밀번호는 영문·숫자·기호 중 두 종류 이상을 섞어주세요."
    if username and password.lower() == str(username).lower():
        return "비밀번호를 아이디와 같게 설정할 수 없습니다."
    return None


def generate_temp_password(length=12):
    """관리자 초기화용 임시 비밀번호. 혼동하기 쉬운 글자(0/O, 1/l/I)는 뺀다.

    ⭐️ 예전에는 uuid4().hex[:8](32비트)를 썼다. 같은 저장소의
       tools/reset_password.py 는 이미 secrets 를 쓰고 있어 기준을 맞춘다.
    """
    alphabet = ''.join(c for c in string.ascii_letters + string.digits if c not in '0O1lI')
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def user_dir(base, username):
    """base/<username> 경로를 안전하게 조합한다. 이름이 수상하면 None.

    ⭐️ 규칙 도입 이전에 만들어진 계정이 DB 에 남아 있을 수 있으므로, 경로를 쓰는
       쪽에서도 한 번 더 막는다. (호출부는 None 이면 건너뛴다)
    """
    if not is_valid_username(username):
        log.warning(f"⚠️ 경로에 쓸 수 없는 사용자명이라 건너뜁니다: {username!r}")
        return None
    path = os.path.normpath(os.path.join(base, username))
    if os.path.commonpath([os.path.abspath(base), os.path.abspath(path)]) != os.path.abspath(base):
        log.error(f"⚠️ 경로 탈출 시도 차단: {username!r}")
        return None
    return path


# ---------------------------------------------------------------------------
# 세션 무효화용 epoch
# ---------------------------------------------------------------------------
#
# ⭐️ 비밀번호를 바꿔도 예전 세션 쿠키가 만료 시각까지(최대 24시간) 그대로 살아
#    있어서, '유출이 의심돼 비밀번호를 바꿨는데 침입자가 그대로 로그인 상태'인
#    상황이 가능했다. 로그인 시 세션에 epoch 을 심고 매 요청 대조한다.
#    요청마다 DB 를 읽지 않도록 메모리에 캐시하고, 값이 바뀌는 지점에서만 갱신한다.

_session_epochs = {}
_session_epoch_lock = threading.Lock()


def current_session_epoch(username):
    with _session_epoch_lock:
        if username in _session_epochs:
            return _session_epochs[username]
    epoch = 0
    try:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT session_epoch FROM users WHERE username = ?", (username,)).fetchone()
            if row and row['session_epoch'] is not None:
                epoch = int(row['session_epoch'])
    except Exception:
        return 0
    with _session_epoch_lock:
        _session_epochs[username] = epoch
    return epoch


def bump_session_epoch(c, username):
    """이 사용자의 다른 모든 세션을 즉시 무효화한다.

    (커서를 받아 호출자와 같은 트랜잭션에서 수행한다 — 비밀번호 변경과 세션
     무효화가 따로 커밋되면 그 사이에 옛 쿠키가 통과하는 창이 생긴다)
    """
    c.execute("UPDATE users SET session_epoch = COALESCE(session_epoch, 0) + 1 "
              "WHERE username = ?", (username,))
    row = c.execute("SELECT session_epoch FROM users WHERE username = ?",
                    (username,)).fetchone()
    epoch = int(row['session_epoch']) if row and row['session_epoch'] is not None else 0
    with _session_epoch_lock:
        _session_epochs[username] = epoch
    return epoch


def clear_epoch_cache():
    """테스트용 — 앞 테스트의 캐시가 다음 테스트로 새지 않도록."""
    with _session_epoch_lock:
        _session_epochs.clear()
