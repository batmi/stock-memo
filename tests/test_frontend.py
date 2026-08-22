import pytest
import threading
import datetime
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
    expect(page.locator('#btnDataManagement')).to_be_visible(timeout=5000)

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
    expect(page.locator('#btnDataManagement')).to_be_visible(timeout=5000)

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
    expect(page.locator('#btnDataManagement')).to_be_visible(timeout=5000)

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


def _chart_realized(page: Page, month_label):
    """차트 뷰의 '실현손익' 데이터셋에서 특정 월(예: '9월') 값을 읽는다."""
    return page.evaluate("""(monthLabel) => {
        window.currentChartType = 'profit';
        window.renderMonthlyProfitChart();
        const chart = window.monthlyProfitChartInstance;
        const idx = chart.data.labels.indexOf(monthLabel);
        if (idx < 0) return null;
        const ds = chart.data.datasets.find(d => d.label === '매매 실현손익');
        return ds ? ds.data[idx] : null;
    }""", month_label)


@pytest.fixture
def cleanup_admin_mappings():
    """/api/mappings 는 json/<username>/ 에 실제 파일을 남기므로 테스트 후 지운다."""
    yield
    import shutil
    shutil.rmtree(os.path.join('json', 'admin'), ignore_errors=True)


def test_chart_excludes_simulated_and_flagged_accounts(page: Page, cleanup_admin_mappings):
    """차트 뷰(실현손익)도 모의투자·'금액 계산 제외' 계좌를 빼고 그려야 한다."""
    page.goto(BASE_URL + '/login')
    page.fill('input[name="username"]', 'admin')
    page.fill('input[name="password"]', 'admin123')
    page.click('button[type="submit"]')
    expect(page.locator('#btnDataManagement')).to_be_visible(timeout=5000)

    # '연습계좌'(99998888-01)를 금액 계산 제외로 등록한다
    assert page.evaluate("""async () => {
        const res = await fetch('/api/mappings', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({brokers: {}, accounts: {
                "99998888-01": {broker_code: "243", broker_name: "한국투자증권",
                                alias: "연습계좌", exclude_from_stats: true}
            }})
        });
        return res.ok ? 'OK' : 'FAIL';
    }""") == 'OK'

    # ⭐️ 차트는 '최근 12개월'만 그리므로 실행 시점 기준 이번 달로 기록을 심는다.
    today = datetime.date.today()
    day = f"{today.year}-{today.month:02d}-{{:02d}}T09:00"

    assert _seed_entries(page, [
        # 실거래: 1,000 → 1,200 × 10주 = +2,000원
        {"type": "trade", "tradeType": "매수", "stockName": "차트리얼",
         "stockCode": "000011", "price": 1000, "quantity": 10,
         "rawDate": day.format(1), "id": 900101},
        {"type": "trade", "tradeType": "매도", "stockName": "차트리얼",
         "stockCode": "000011", "price": 1200, "quantity": 10,
         "rawDate": day.format(5), "id": 900102},
        # 모의투자: 큰 손실 — 차트에 섞이면 즉시 티가 난다
        {"type": "trade", "tradeType": "매수", "stockName": "차트모의",
         "stockCode": "000012", "price": 10000, "quantity": 10,
         "rawDate": day.format(2), "id": 900103, "isSimulated": 1},
        {"type": "trade", "tradeType": "매도", "stockName": "차트모의",
         "stockCode": "000012", "price": 1000, "quantity": 10,
         "rawDate": day.format(6), "id": 900104, "isSimulated": 1},
        # 제외 계좌(연습계좌): 역시 큰 손실
        {"type": "trade", "tradeType": "매수", "stockName": "차트연습",
         "stockCode": "000013", "price": 10000, "quantity": 10,
         "rawDate": day.format(3), "id": 900105, "subAccount": "99998888-01"},
        {"type": "trade", "tradeType": "매도", "stockName": "차트연습",
         "stockCode": "000013", "price": 1000, "quantity": 10,
         "rawDate": day.format(7), "id": 900106, "subAccount": "99998888-01"},
    ]) == 'OK'

    page.reload()
    expect(page.locator('#portfolioGrid')).to_be_visible(timeout=10000)

    # 이번 달 실현손익은 실거래 +2,000원뿐이어야 한다
    assert _chart_realized(page, f'{today.month}월') == 2000

    # 제외 계좌는 차트 계좌 필터 선택지에도 나오지 않는다
    options = page.evaluate(
        "() => [...document.querySelectorAll('#chartSubAccountFilter option')].map(o => o.value)")
    assert '연습계좌' not in options


def test_account_form_switches_to_edit_mode(page: Page, cleanup_admin_mappings):
    """'수정'을 누르면 폼이 펼쳐지고 문구가 '계좌 수정 / 수정하기'로 바뀌어야 한다."""
    page.goto(BASE_URL + '/login')
    page.fill('input[name="username"]', 'admin')
    page.fill('input[name="password"]', 'admin123')
    page.click('button[type="submit"]')
    expect(page.locator('#btnDataManagement')).to_be_visible(timeout=5000)

    assert page.evaluate("""async () => {
        const res = await fetch('/api/mappings', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({brokers: {}, accounts: {
                "77776666-01": {broker_code: "243", broker_name: "한국투자증권",
                                alias: "수정테스트계좌", exclude_from_stats: true}
            }})
        });
        return res.ok ? 'OK' : 'FAIL';
    }""") == 'OK'

    page.reload()
    # ⭐️ 헤더의 설정 버튼들은 '⚙️ 설정 메뉴' 아이콘을 눌러야(.active) 펼쳐진다.
    #    펼치지 않고 바로 클릭하면 접힌 상태의 다른 버튼이 포인터를 가로챈다.
    page.click('.header-action-icon')
    page.click('#btnAccountManagement')
    form = page.locator('#newAccountFormContainer')
    title = page.locator('#btnToggleNewAccountForm')
    submit = page.locator('#btnAddUnifiedMapping')

    # 모달을 열면 항상 접힌 '신규 계좌 등록' 상태
    expect(form).to_be_hidden()
    expect(title).to_have_text('신규 계좌 등록')

    page.click('#unifiedMappingList button:has-text("수정")')
    expect(form).to_be_visible()
    expect(title).to_have_text('계좌 수정')
    expect(submit).to_have_text('수정하기')
    expect(page.locator('#unifiedAccountCode')).to_have_value('77776666-01')
    # 저장돼 있던 '금액 계산에서 제외' 체크 상태도 그대로 불러온다
    expect(page.locator('#unifiedAccountExcludeStats')).to_be_checked()

    # 취소하면 신규 등록 상태로 되돌아간다
    page.click('#btnCancelNewAccountForm')
    expect(form).to_be_hidden()
    expect(title).to_have_text('신규 계좌 등록')
    expect(submit).to_have_text('추가하기')
    expect(page.locator('#unifiedAccountCode')).to_have_value('')


def test_account_list_escapes_quotes_and_html(page: Page, cleanup_admin_mappings):
    """별칭에 따옴표·HTML 이 들어가도 수정/삭제 버튼이 살아있고 마크업이 깨지지 않는다.

    예전에는 값을 그대로 innerHTML 과 onclick 에 끼워 넣어서, 별칭에 작은따옴표가
    하나만 있어도 핸들러 문자열이 끊겨 버튼이 먹통이 됐다.
    """
    page.goto(BASE_URL + '/login')
    page.fill('input[name="username"]', 'admin')
    page.fill('input[name="password"]', 'admin123')
    page.click('button[type="submit"]')
    expect(page.locator('#btnDataManagement')).to_be_visible(timeout=5000)

    tricky_alias = "John's <img src=x onerror=window.__xss=1> 계좌"
    assert page.evaluate("""async (alias) => {
        const res = await fetch('/api/mappings', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({brokers: {}, accounts: {
                "12345678-01": {broker_code: "243", broker_name: "한국투자증권",
                                alias: alias, exclude_from_stats: false}
            }})
        });
        return res.ok ? 'OK' : 'FAIL';
    }""", tricky_alias) == 'OK'

    page.reload()
    page.click('.header-action-icon')
    page.click('#btnAccountManagement')

    # 1) 별칭이 '텍스트로' 그대로 보인다 (HTML 로 해석되지 않는다)
    expect(page.locator('#unifiedMappingList')).to_contain_text(tricky_alias)
    assert page.evaluate("() => document.querySelectorAll('#unifiedMappingList img').length") == 0
    assert page.evaluate("() => window.__xss === undefined")

    # 2) 따옴표가 있어도 '수정' 버튼이 정상 동작해 편집 모드로 들어간다
    page.click('#unifiedMappingList button:has-text("수정")')
    expect(page.locator('#newAccountFormContainer')).to_be_visible()
    expect(page.locator('#unifiedAccountCode')).to_have_value('12345678-01')
    expect(page.locator('#unifiedAccountName')).to_have_value(tricky_alias)
