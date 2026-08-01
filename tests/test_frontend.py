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
    # 1. 먼저 회원가입 (최고 관리자 생성)
    page.goto(BASE_URL + '/signup')
    page.fill('input[name="username"]', 'admin')
    page.fill('input[name="password"]', 'admin123')
    page.fill('input[name="password_confirm"]', 'admin123')
    page.click('button[type="submit"]')
    
    # 로그인 화면으로 자동 이동될 때까지 대기
    page.wait_for_url('**/login')
    
    # 2. 관리자 계정 정보 자동 타이핑
    page.fill('input[name="username"]', 'admin')
    page.fill('input[name="password"]', 'admin123')
    
    # 3. 로그인 버튼 클릭
    page.click('button[type="submit"]')
    
    # 4. 로그인이 완료되어 메인 대시보드의 특정 요소(예: 백업 버튼)가 뜨는지 확인
    expect(page.locator('#btnFullBackup')).to_be_visible(timeout=5000)

def _seed_entries(page: Page, entries):
    """로그인된 세션으로 기록을 심는다 (브라우저 fetch 사용)."""
    return page.evaluate("""async (entries) => {
        for (const e of entries) {
            const res = await fetch('/api/entry', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(e)
            });
            if (!res.ok) return 'FAIL ' + res.status + ' ' + (await res.text());
        }
        return 'OK';
    }""", entries)


def test_simulated_trades_show_as_card_but_excluded_from_totals(page: Page):
    """모의투자는 카드로만 보이고, 도넛·총 투자금액·보유 종목 수에서는 빠져야 한다."""
    page.goto(BASE_URL + '/login')
    page.fill('input[name="username"]', 'admin')
    page.fill('input[name="password"]', 'admin123')
    page.click('button[type="submit"]')
    expect(page.locator('#btnFullBackup')).to_be_visible(timeout=5000)

    assert _seed_entries(page, [
        # 실거래: 1,000원 × 10주 = 10,000원
        {"type": "trade", "tradeType": "매수", "stockName": "리얼종목",
         "stockCode": "000001", "price": 1000, "quantity": 10,
         "tradeClass": "장기투자", "rawDate": "2026-07-01T09:00", "id": 900001},
        # 모의투자: 50,000원 × 10주 = 500,000원 — 합계에 섞이면 즉시 티가 난다
        {"type": "trade", "tradeType": "매수", "stockName": "모의종목",
         "stockCode": "000002", "price": 50000, "quantity": 10,
         "tradeClass": "시스템", "rawDate": "2026-07-02T09:00", "id": 900002,
         "isSimulated": 1},
    ]) == 'OK'

    page.reload()
    expect(page.locator('#portfolioGrid .portfolio-card')).to_have_count(2, timeout=10000)

    # 1) 모의투자도 카드로는 보인다 (+ '모의' 배지)
    sim_card = page.locator('#portfolioGrid .portfolio-card[data-id="모의종목::모의"]')
    expect(sim_card).to_have_count(1)
    expect(sim_card).to_contain_text('모의')

    # 2) 총 투자금액은 실거래 10,000원만 (모의 500,000원 제외)
    expect(page.locator('#centerTotalInvested')).to_have_text('10,000원', timeout=5000)

    # 3) 보유 종목 수도 실거래만 센다
    expect(page.locator('#centerHoldingsCount')).to_have_text('1종목 보유')

    # 4) 도넛 차트 데이터에 모의 종목이 없다
    labels = page.evaluate("() => portfolioChartInstance.data.labels")
    assert labels == ['리얼종목']


def test_simulated_trade_does_not_pollute_real_average_price(page: Page):
    """같은 종목을 실거래·모의로 함께 들고 있어도 카드가 합쳐지면 안 된다."""
    page.goto(BASE_URL + '/login')
    page.fill('input[name="username"]', 'admin')
    page.fill('input[name="password"]', 'admin123')
    page.click('button[type="submit"]')
    expect(page.locator('#btnFullBackup')).to_be_visible(timeout=5000)

    assert _seed_entries(page, [
        # 동일 종목명 '겹침종목' 을 실거래 100원, 모의 9,900원에 매수
        {"type": "trade", "tradeType": "매수", "stockName": "겹침종목",
         "stockCode": "000003", "price": 100, "quantity": 10,
         "tradeClass": "장기투자", "rawDate": "2026-07-03T09:00", "id": 900003},
        {"type": "trade", "tradeType": "매수", "stockName": "겹침종목",
         "stockCode": "000003", "price": 9900, "quantity": 10,
         "tradeClass": "시스템", "rawDate": "2026-07-04T09:00", "id": 900004,
         "isSimulated": 1},
    ]) == 'OK'

    page.reload()
    real_card = page.locator('#portfolioGrid .portfolio-card[data-id="겹침종목"]')
    sim_card = page.locator('#portfolioGrid .portfolio-card[data-id="겹침종목::모의"]')
    expect(real_card).to_have_count(1, timeout=10000)
    expect(sim_card).to_have_count(1)

    # 실거래 카드의 평균 단가는 모의 매수(9,900원)에 오염되지 않은 100원이어야 한다
    expect(real_card).to_contain_text('100')
    expect(sim_card).to_contain_text('9,900')
