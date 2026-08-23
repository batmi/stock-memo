"""경로·상수·시크릿 키 — 앱 설정의 단일 소스.

**왜 별도 모듈인가.** 예전에는 이 값들이 `backend_app` 모듈 전역이었고, 라우트도
전부 같은 모듈에 있었기 때문에 그냥 전역 변수를 읽으면 됐다. 라우트를 블루프린트로
쪼개면 그 전제가 깨진다 — 각 모듈이 `from config import UPLOAD_FOLDER` 로 값을
복사해 가면, 테스트가 경로를 바꿔치기해도 복사본은 그대로라 **테스트가 통과하는데
실제로는 아무것도 검증하지 않는** 상태가 된다.

그래서 규칙을 하나 둔다: **경로는 항상 `config.<이름>` 으로, 쓰는 순간에 읽는다.**
`from config import UPLOAD_FOLDER` 처럼 이름만 떼어 오지 않는다. 그러면 테스트가
`monkeypatch.setattr(config, 'UPLOAD_FOLDER', tmp)` 한 번으로 모든 모듈에 적용된다.
"""

import os
from datetime import timedelta

# ⭐️ 모든 데이터 경로의 기준점. 상대 경로로 두면 프로세스의 실행 위치(cwd)에 따라
#    엉뚱한 곳에 빈 DB·빈 시크릿키가 새로 생겨 "비밀번호가 틀렸다"·"갑자기 로그아웃"
#    으로 나타난다. run.sh 는 cd 를 하지만 systemd·cron·다른 터미널로 띄우면 그 보호가
#    사라지므로, 실행 방식과 무관하게 항상 소스 파일 위치를 기준으로 고정한다.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 레거시 단일 JSON 저장소 (최초 관리자 가입 시 1회 이관에만 쓰인다)
DATA_FILE = os.path.join(BASE_DIR, 'my_stock_trading_journal.json')

DB_DIR = os.path.join(BASE_DIR, 'db')
DB_FILE = os.path.join(DB_DIR, 'journal.db')
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
BACKUP_DIR = os.path.join(BASE_DIR, 'backup')
LOG_DIR = os.path.join(BASE_DIR, 'logs')
# 계좌 매핑을 DB 로 옮기기 전의 레거시 저장소. 기동 시 1회 이관에만 쓴다.
JSON_DIR = os.path.join(BASE_DIR, 'json')

SECRET_KEY_FILE = os.path.join(BASE_DIR, '.secret_key')

# 업로드 본문 상한 (초과 시 413)
MAX_CONTENT_LENGTH = 16 * 1024 * 1024

# ⭐️ 복원 ZIP 의 '압축 해제 후' 크기 상한 — zip bomb 방어.
MAX_RESTORE_UNCOMPRESSED_BYTES = 512 * 1024 * 1024

# 세션 쿠키
SESSION_LIFETIME = timedelta(hours=24)


def apply_to(app):
    """Flask 앱에 세션·업로드 관련 설정을 적용한다."""
    app.config['SESSION_COOKIE_HTTPONLY'] = True   # 자바스크립트(XSS)로 쿠키 접근 원천 차단
    # ⭐️ 기본은 False (로컬 HTTP 접속에서 로그인이 풀리지 않도록). HTTPS 로 외부에
    #    공개할 때는 SESSION_COOKIE_SECURE=1 로 켜서 쿠키가 평문 경로로 나가지 않게 한다.
    app.config['SESSION_COOKIE_SECURE'] = (
        os.environ.get('SESSION_COOKIE_SECURE', '').strip().lower()
        in ('1', 'true', 'yes', 'on'))
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # CSRF(크로스 사이트 요청 위조) 방어
    # ⭐️ "로그인 유지" 선택 시 쿠키 수명 24시간
    #    (실제 만료는 서버의 expires_at 검사로 강제한다)
    app.config['PERMANENT_SESSION_LIFETIME'] = SESSION_LIFETIME
    # ⭐️ 만료 시각은 로그인 시점에 확정되므로 요청마다 쿠키 수명을 연장하지 않는다
    app.config['SESSION_REFRESH_EACH_REQUEST'] = False
    app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH


# ---------------------------------------------------------------------------
# 시크릿 키
# ---------------------------------------------------------------------------

def _harden_key_permissions(key_path):
    """.secret_key 를 소유자만 읽을 수 있게(0600) 조인다.

    ⭐️ 이 키가 유출되면 임의 사용자의 세션 쿠키를 위조할 수 있다(로그인 우회).
       예전에는 umask 기본값(0644)으로 만들어져 같은 머신의 다른 계정·프로세스가
       읽을 수 있었으므로, 새로 만들 때뿐 아니라 기존 파일도 함께 조인다.
    """
    try:
        if os.name != 'nt' and (os.stat(key_path).st_mode & 0o077):
            os.chmod(key_path, 0o600)
    except Exception:
        pass  # 권한 변경 실패가 기동을 막아서는 안 된다


def load_secret_key():
    """환경변수가 우선. 없으면 파일에 영속화하여 재시작 시에도 세션을 유지한다."""
    env_key = os.environ.get('SECRET_KEY')
    if env_key:
        return env_key
    key_path = SECRET_KEY_FILE
    try:
        if os.path.exists(key_path):
            _harden_key_permissions(key_path)
            with open(key_path, 'r') as f:
                saved = f.read().strip()
                if saved:
                    return saved
        new_key = os.urandom(24).hex()
        # ⭐️ 0600 으로 '만들면서' 연다. 먼저 만들고 chmod 하면 그 사이 짧게나마
        #    누구나 읽을 수 있는 창이 생긴다.
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(new_key)
        _harden_key_permissions(key_path)  # 이미 있던 파일을 덮어쓴 경우 대비
        return new_key
    except Exception:
        # 파일 접근 불가 환경에서는 임시 난수 키로 폴백 (재시작 시 세션 무효화)
        return os.urandom(24).hex()
