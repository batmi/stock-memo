import os
import shutil
import sys

import pytest

# 테스트 실행 시 backend_app 모듈을 찾을 수 있도록 시스템 경로 추가
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import auth  # noqa: E402
import backend_app  # noqa: E402
import config  # noqa: E402
import ratelimit  # noqa: E402
import statscache  # noqa: E402
import users  # noqa: E402
from backend_app import app as flask_app  # noqa: E402

# ⭐️ 테스트가 건드리면 안 되는 실제 데이터 경로. app 픽스처가 전부 임시 폴더로 돌린다.
_SANDBOXED_PATHS = ('DB_FILE', 'UPLOAD_FOLDER', 'BACKUP_DIR', 'JSON_DIR', 'DATA_FILE')

# 실제 데이터 폴더 (세션 시작 시점의 값 — 흔적 검사에 쓴다)
_REAL_DIRS = (config.UPLOAD_FOLDER, config.JSON_DIR, config.BACKUP_DIR)


@pytest.fixture
def app(tmp_path):
    """각 테스트마다 독립적인 Flask 애플리케이션 인스턴스와 임시 데이터 폴더를 제공합니다.

    ⭐️ DB 뿐 아니라 **모든 데이터 경로**를 임시 폴더로 돌린다.
       예전에는 DB_FILE 만 바꿔서, 첨부파일·백업·계좌 매핑을 쓰는 테스트가 실제
       uploads/ 와 json/ 폴더에 흔적을 남겼다. 실제로 uploads/restorer,
       uploads/roundtrip, uploads/admin/test_download.txt 같은 잔재가 운영
       데이터 폴더에 쌓여 있었다. 개별 테스트가 monkeypatch 로 막는 방식은
       한 곳만 빠뜨려도 다시 새므로, 기본값 자체를 안전하게 둔다.
    """
    original = {name: getattr(config, name) for name in _SANDBOXED_PATHS}

    config.DB_FILE = str(tmp_path / 'journal.db')
    config.UPLOAD_FOLDER = str(tmp_path / 'uploads')
    config.BACKUP_DIR = str(tmp_path / 'backup')
    config.JSON_DIR = str(tmp_path / 'json')
    config.DATA_FILE = str(tmp_path / 'legacy.json')
    for path in (config.UPLOAD_FOLDER, config.BACKUP_DIR, config.JSON_DIR):
        os.makedirs(path, exist_ok=True)

    # 테스트 간 전역 상태(로그인 차단, 레이트리밋, 세션 epoch, 통계 캐시)가
    # 겹치지 않도록 초기화한다. 모두 모듈 전역이라 앞 테스트의 상태가 새어 들어간다.
    auth.login_attempts.clear()
    ratelimit.reset_all()
    users.clear_epoch_cache()
    statscache.clear_all()

    with flask_app.app_context():
        backend_app.init_db()

    flask_app.config.update({"TESTING": True, "DATABASE": config.DB_FILE})

    yield flask_app

    for name, value in original.items():
        setattr(config, name, value)


@pytest.fixture
def client(app):
    """테스트용 HTTP 클라이언트를 제공하여 직접 브라우저 없이 라우트를 테스트합니다."""
    return app.test_client()


@pytest.fixture(scope='session', autouse=True)
def _fail_if_tests_touch_real_data_dirs():
    """테스트가 끝난 뒤 실제 데이터 폴더에 새 흔적이 생겼는지 확인한다.

    위 샌드박스를 우회하는 경로가 새로 생기면 여기서 드러난다. 조용히 운영
    폴더를 더럽히는 것보다 테스트가 실패해서 알려주는 편이 낫다.
    """
    def snapshot():
        return {d: set(os.listdir(d)) for d in _REAL_DIRS if os.path.isdir(d)}

    before = snapshot()
    yield
    after = snapshot()

    leaked = {
        base: sorted(names - before.get(base, set()))
        for base, names in after.items()
        if names - before.get(base, set())
    }
    # 테스트가 만든 것만 지우고(운영 데이터는 건드리지 않는다) 실패로 알린다.
    for base, names in leaked.items():
        for name in names:
            target = os.path.join(base, name)
            shutil.rmtree(target, ignore_errors=True)
            if os.path.exists(target):
                os.remove(target)
    assert not leaked, f"테스트가 실제 데이터 폴더에 흔적을 남겼습니다(정리함): {leaked}"
