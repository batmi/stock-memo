import pytest
import threading
import os
import tempfile
from werkzeug.serving import make_server
from playwright.sync_api import Page, expect

BASE_URL = "http://127.0.0.1:5001"

class LiveServerThread(threading.Thread):
    def __init__(self, app):
        threading.Thread.__init__(self)
        self.server = make_server('127.0.0.1', 5001, app)
        self.ctx = app.app_context()
        self.ctx.push()

    def run(self):
        self.server.serve_forever()

    def shutdown(self):
        self.server.shutdown()
        self.ctx.pop()

@pytest.fixture(scope="module", autouse=True)
def live_server():
    """테스트 실행 시 백그라운드에서 자동으로 테스트용 Flask 서버를 켜고 끕니다."""
    from backend_app import app as flask_app
    import backend_app
    
    db_fd, db_path = tempfile.mkstemp()
    backend_app.DB_FILE = db_path
    with flask_app.app_context():
        backend_app.init_db()
        
    server = LiveServerThread(flask_app)
    server.start()
    yield
    server.shutdown()
    server.join()
    os.close(db_fd)
    os.unlink(db_path)

def test_login_page_ui(page: Page):
    """
    브라우저를 열고 메인 페이지에 접속했을 때 
    로그인 폼과 각종 UI 요소가 정상적으로 화면에 렌더링되는지 테스트합니다.
    """
    page.goto(BASE_URL)
    
    # 1. 페이지 타이틀 검증
    expect(page).to_have_title("TRADING JOURNAL - 로그인")
    
    # 2. 아이디와 비밀번호 입력 칸이 보이는지 검증
    expect(page.locator('input[name="username"]')).to_be_visible()
    expect(page.locator('input[name="password"]')).to_be_visible()
    
    # 3. 접속하기 버튼이 보이는지 검증
    expect(page.locator('button[type="submit"]')).to_be_visible()

def test_user_login_and_dashboard_render(page: Page):
    """
    실제로 폼에 값을 입력하고 로그인을 수행한 뒤,
    메인 대시보드(stock-memo.html) 화면으로 넘어가는지 테스트합니다.
    """
    page.goto(BASE_URL)
    
    # 1. 관리자 계정 정보 자동 타이핑
    page.fill('input[name="username"]', 'batmi')
    page.fill('input[name="password"]', 'ghkswn96') # 초기 기본 비밀번호
    
    # 2. 로그인 버튼 클릭
    page.click('button[type="submit"]')
    
    # 3. 로그인이 완료되어 메인 대시보드의 특정 요소(예: 백업 버튼)가 뜨는지 확인
    expect(page.locator('#btnFullBackup')).to_be_visible(timeout=5000)