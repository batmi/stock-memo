import os
import tempfile
import pytest
import sys

# 테스트 실행 시 backend_app 모듈을 찾을 수 있도록 시스템 경로 추가
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend_app import app as flask_app
import backend_app

@pytest.fixture
def app():
    """각 테스트마다 독립적인 Flask 애플리케이션 인스턴스와 임시 DB를 제공합니다."""
    db_fd, db_path = tempfile.mkstemp()
    
    # 테스트 간 전역 변수(로그인 차단 상태 등)가 겹치지 않도록 초기화
    backend_app.login_attempts.clear()
    
    # ⭐️ 실제 DB 파일 대신 임시 DB 경로를 사용하도록 덮어쓰기하여 원본 데이터를 보호합니다.
    backend_app.DB_FILE = db_path
    
    # 임시 DB에 테이블 구조(스키마) 초기화
    with flask_app.app_context():
        backend_app.init_db()
        
    flask_app.config.update({
        "TESTING": True,
        "DATABASE": db_path,  # 실제 backend_app.py 구현에 맞춰 환경 변수나 설정 키를 수정하세요.
    })

    yield flask_app

    os.close(db_fd)
    os.unlink(db_path)

@pytest.fixture
def client(app):
    """테스트용 HTTP 클라이언트를 제공하여 직접 브라우저 없이 라우트를 테스트합니다."""
    return app.test_client()