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


def test_gear_button_shows_badge_when_admin_has_notifications(page: Page):
    """관리자(M) 버튼에 배지가 생기면 접혀 있는 톱니바퀴에도 배지가 보여야 한다.

    설정 메뉴가 접힌 상태에서는 M 버튼이 숨어 있어, 배지가 M 에만 붙으면
    알림이 있는지 알 수 없다.
    """
    # 승인 대기 사용자를 만들어 관리자에게 알림이 생기게 한다
    page.goto(BASE_URL + '/signup')
    page.fill('input[name="username"]', 'pendinguser')
    page.fill('input[name="password"]', 'Passw0rd!')
    page.fill('input[name="password_confirm"]', 'Passw0rd!')
    page.click('button[type="submit"]')
    page.wait_for_url('**/login')

    page.fill('input[name="username"]', 'admin')
    page.fill('input[name="password"]', 'admin123')
    page.click('button[type="submit"]')
    expect(page.locator('#btnDataManagement')).to_be_visible(timeout=5000)

    gear_badge = page.locator('.header-action-group > .admin-notification-badge')
    expect(gear_badge).to_be_visible(timeout=5000)
    expect(gear_badge).to_have_text('1')

    # 메뉴를 펼치면 M 버튼의 배지가 대신 보이므로 톱니바퀴 배지는 숨는다
    page.click('.header-action-icon')
    expect(gear_badge).to_be_hidden()
    expect(page.locator('#btnAdmin .admin-notification-badge')).to_be_visible()


def test_login_page_links_to_reset_request_page(page: Page):
    """로그인 화면의 '초기화 요청'은 가입하기처럼 별도 페이지로 이동한다."""
    page.goto(BASE_URL + '/login')
    page.click('a:has-text("초기화 요청")')
    page.wait_for_url('**/request_password_reset')

    # 요청 화면에는 로그인 입력칸이 없어야 한다 (화면이 섞이지 않게 분리)
    expect(page.locator('#resetUsername')).to_be_visible()
    expect(page.locator('input[name="password"]')).to_have_count(0)
    expect(page.locator('button:has-text("접속하기")')).to_have_count(0)


def test_reset_request_submits_and_returns_to_login(page: Page):
    """요청을 보내면 안내가 뜨고 로그인 화면으로 되돌아간다."""
    page.goto(BASE_URL + '/request_password_reset')
    page.fill('#resetUsername', 'admin')
    page.fill('#resetNote', '비밀번호를 잊었습니다')
    page.click('#btnSubmitResetRequest')

    expect(page.locator('#resetSuccessBanner')).to_be_visible(timeout=5000)
    expect(page.locator('#resetSuccessBanner')).to_contain_text('접수')

    # 잠시 뒤 로그인 화면으로 복귀 → 아이디/비밀번호 입력칸이 다시 보인다
    page.wait_for_url('**/login', timeout=8000)
    expect(page.locator('input[name="username"]')).to_be_visible()
    expect(page.locator('input[name="password"]')).to_be_visible()


def test_reset_request_response_is_same_for_unknown_account(page: Page):
    """없는 계정으로 요청해도 같은 문구가 나와야 한다 (계정 존재 여부 은닉)."""
    page.goto(BASE_URL + '/request_password_reset')
    page.fill('#resetUsername', 'no_such_user_here')
    page.click('#btnSubmitResetRequest')
    expect(page.locator('#resetSuccessBanner')).to_contain_text('접수', timeout=5000)


def test_admin_table_has_no_horizontal_scroll(page: Page):
    """관리자 표가 가로 스크롤 없이 들어가야 한다.

    '요청 해제' 버튼이 늘면서 관리 열이 잘려 스크롤바가 생겼었다.
    긴 아이디(32자)와 요청 횟수 배지까지 붙은 최악 조건으로 검증한다.
    """
    long_name = 'z' * 32
    page.goto(BASE_URL + '/signup')
    page.fill('input[name="username"]', long_name)
    page.fill('input[name="password"]', 'Passw0rd!')
    page.fill('input[name="password_confirm"]', 'Passw0rd!')
    page.click('button[type="submit"]')
    page.wait_for_url('**/login')

    # 초기화 요청을 여러 번 넣어 '×N' 배지까지 붙인 상태로 만든다
    for _ in range(3):
        page.request.post(BASE_URL + '/request_password_reset',
                          data={'username': long_name})

    page.fill('input[name="username"]', 'admin')
    page.fill('input[name="password"]', 'admin123')
    page.click('button[type="submit"]')
    expect(page.locator('#btnDataManagement')).to_be_visible(timeout=5000)

    page.click('.header-action-icon')
    page.click('#btnAdmin')
    page.wait_for_function(
        "() => document.querySelectorAll('#adminUserList tr').length >= 2"
        " && document.querySelector('#adminUserList tr').children.length >= 5")

    metrics = page.evaluate("""() => {
        const box = document.querySelector('#adminUserList').closest('div');
        const table = document.querySelector('#adminUserList').closest('table');
        return { scrollWidth: table.scrollWidth, clientWidth: box.clientWidth };
    }""")
    assert metrics['scrollWidth'] <= metrics['clientWidth'], (
        f"가로 스크롤 발생: 표 {metrics['scrollWidth']}px > 보이는 폭 {metrics['clientWidth']}px")

    # 관리 열의 버튼이 잘리지 않고 모두 보여야 한다
    # (미승인 계정이라 승인 토글은 '허용'으로 뜬다)
    row = page.locator(f'#adminUserList tr:has-text("{long_name}")')
    for label in ('허용', '비번 초기화', '요청 해제', '삭제'):
        expect(row.locator(f'button:has-text("{label}")')).to_be_visible()

    # 사용자가 남긴 메모가 표에 실제로 보여야 한다 (툴팁으로만 두면 아무도 못 본다)
    expect(row).to_contain_text('요청')
