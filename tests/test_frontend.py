import pytest
import threading
import datetime
import os
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
def live_server(tmp_path_factory):
    """테스트 실행 시 백그라운드에서 자동으로 테스트용 Flask 서버를 켜고 끕니다.

    ⭐️ DB 뿐 아니라 첨부·백업·계좌 매핑 경로까지 전부 임시 폴더로 돌린다.
       예전에는 DB 만 임시로 두어, 브라우저 테스트가 실제 uploads/ 와 json/ 에
       계정 폴더를 만들고 그대로 남겼다. (conftest 의 app 픽스처와 같은 이유)
    """
    from backend_app import app as flask_app
    import backend_app
    import config

    sandbox = tmp_path_factory.mktemp('e2e')
    original = {name: getattr(config, name)
                for name in ('DB_FILE', 'UPLOAD_FOLDER', 'BACKUP_DIR', 'JSON_DIR', 'DATA_FILE')}
    config.DB_FILE = str(sandbox / 'journal.db')
    config.UPLOAD_FOLDER = str(sandbox / 'uploads')
    config.BACKUP_DIR = str(sandbox / 'backup')
    config.JSON_DIR = str(sandbox / 'json')
    config.DATA_FILE = str(sandbox / 'legacy.json')
    for path in (config.UPLOAD_FOLDER, config.BACKUP_DIR, config.JSON_DIR):
        os.makedirs(path, exist_ok=True)

    with flask_app.app_context():
        backend_app.init_db()

    server = LiveServerThread(flask_app)
    server.start()
    yield
    server.shutdown()
    server.join()
    for name, value in original.items():
        setattr(config, name, value)

# ─────────────────────────────────────────────────────────────
# 로그인·가입 조작 헬퍼
#
# ⭐️ 예전에는 12곳이 각자 `fill → fill → click → expect(대시보드)` 를 적어 두었다.
#    한 번은 그중 하나(admin 표 테스트)가 전체 스위트 직후 실행에서 실패했는데,
#    남은 단서가 "아이디 또는 비밀번호가 일치하지 않습니다" 뿐이라 원인을 좁힐 수
#    없었다. 로그인이 왜 거부됐는지(계정 없음/비번 불일치/IP 차단/계정 잠금)는
#    화면 문구에 다 나와 있었는데, 단언이 "#btnDataManagement 가 안 보인다" 로만
#    끝나서 그 문구가 실패 메시지에 담기지 않았던 것이다.
#
#    그래서 두 가지를 한다.
#      1. 입력 전에 폼이 실제로 조작 가능한지 기다린다 (리다이렉트 직후 대비).
#      2. 로그인이 실패하면 **화면의 오류 문구를 그대로 실패 메시지에 싣는다.**
#    검증 대상 자체는 그대로다.
#
#    ⚠️ 1번은 예방적 조치다. 폼이 준비되지 않는 경우를 30회 반복 계측했으나
#       0회였으므로, 그 실패의 원인이라고 단정하지는 않는다.
# ─────────────────────────────────────────────────────────────
def _wait_for_login_form(page: Page):
    """로그인 폼이 조작 가능한 상태가 될 때까지 기다린다."""
    expect(page.locator('input[name="username"]')).to_be_visible(timeout=10000)
    expect(page.locator('input[name="password"]')).to_be_visible()



def _server_account_state(username):
    """로그인이 거부됐을 때 서버 쪽 사실을 함께 보고한다.

    ⭐️ 화면 문구는 계정 존재 여부를 일부러 감춘다(보안). 그래서 "아이디 또는
       비밀번호가 일치하지 않습니다" 만으로는 **계정이 없는 것**과 **비밀번호가
       다른 것**을 구분할 수 없고, 간헐적 실패의 원인도 좁힐 수 없다.
       테스트 프로세스는 라이브 서버와 같은 프로세스이므로 DB 를 직접 볼 수 있다.
    """
    import sqlite3

    import config
    try:
        conn = sqlite3.connect(config.DB_FILE)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT username, is_allowed FROM users ORDER BY username").fetchall()
        conn.close()
    except Exception as e:                                   # noqa: BLE001
        return f"    [서버] DB 조회 실패: {e!r} (DB={config.DB_FILE})"

    me = [r for r in rows if r['username'] == username]
    return (f"    [서버] DB={config.DB_FILE}\n"
            f"    [서버] 계정 {len(rows)}개: {[r['username'] for r in rows]}\n"
            f"    [서버] '{username}' 존재={bool(me)}"
            + (f", is_allowed={me[0]['is_allowed']}" if me else " ← 계정이 없다"))

def _login(page: Page, username='admin', password='admin123', *, navigate=True):
    """로그인해서 대시보드가 뜰 때까지 기다린다.

    navigate=False 는 이미 로그인 화면에 와 있을 때(가입 직후 리다이렉트 등).
    """
    if navigate:
        page.goto(BASE_URL + '/login')
    _wait_for_login_form(page)
    page.fill('input[name="username"]', username)
    page.fill('input[name="password"]', password)
    page.click('button[type="submit"]')

    # 대시보드가 뜨거나, 로그인 화면에 오류 배너가 뜨거나 — 둘 중 하나는 반드시 온다.
    dashboard = page.locator('#btnDataManagement')
    banner = page.locator('#errorBanner')
    try:
        page.wait_for_function(
            "() => document.querySelector('#btnDataManagement')"
            " || document.querySelector('#errorBanner')",
            timeout=10000)
    except Exception:                                    # noqa: BLE001
        pass                                             # 아래 단언이 상황을 설명한다
    if banner.count() and not dashboard.count():
        raise AssertionError(
            f"'{username}' 로그인이 거부되었습니다 — 화면 문구: "
            f"{banner.inner_text().strip()!r}\n{_server_account_state(username)}")
    expect(dashboard).to_be_visible(timeout=5000)


def _signup(page: Page, username, password='Passw0rd!'):
    """가입하고 로그인 화면으로 돌아올 때까지 기다린다."""
    page.goto(BASE_URL + '/signup')
    expect(page.locator('input[name="username"]')).to_be_visible(timeout=10000)
    page.fill('input[name="username"]', username)
    page.fill('input[name="password"]', password)
    page.fill('input[name="password_confirm"]', password)
    page.click('button[type="submit"]')
    page.wait_for_url('**/login')
    _wait_for_login_form(page)


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
    # 1. 먼저 회원가입 (최고 관리자 생성) → 로그인 화면으로 자동 이동
    _signup(page, 'admin', 'admin123')

    # 2. 그 계정으로 로그인하면 대시보드(백업 버튼)가 떠야 한다
    _login(page, navigate=False)

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
    _login(page)

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
    _login(page)

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


def test_chart_excludes_simulated_and_flagged_accounts(page: Page):
    """차트 뷰(실현손익)도 모의투자·'금액 계산 제외' 계좌를 빼고 그려야 한다."""
    _login(page)

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


def test_account_form_switches_to_edit_mode(page: Page):
    """'수정'을 누르면 폼이 펼쳐지고 문구가 '계좌 수정 / 수정하기'로 바뀌어야 한다."""
    _login(page)

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


def test_account_list_escapes_quotes_and_html(page: Page):
    """별칭에 따옴표·HTML 이 들어가도 수정/삭제 버튼이 살아있고 마크업이 깨지지 않는다.

    예전에는 값을 그대로 innerHTML 과 onclick 에 끼워 넣어서, 별칭에 작은따옴표가
    하나만 있어도 핸들러 문자열이 끊겨 버튼이 먹통이 됐다.
    """
    _login(page)

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
    _signup(page, 'pendinguser')

    _login(page, navigate=False)

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
    _signup(page, long_name)

    # 초기화 요청을 여러 번 넣어 '×N' 배지까지 붙인 상태로 만든다
    for _ in range(3):
        page.request.post(BASE_URL + '/request_password_reset',
                          data={'username': long_name})

    _login(page, navigate=False)

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


def test_amount_mask_hides_money_but_keeps_prices_flowing(page: Page):
    """'금액 가리기'는 표시만 가려야 한다.

    예전 '현재가 숨기기'는 조회 자체를 끊어 평가금액·평가손익·분석 탭까지 함께
    죽었다. 대체 기능은 그러면 안 된다 — 값은 그대로 계산되고 화면에만 블러가
    걸려야 하며, 종목명·현재가·손익률은 남아 상황 판단이 계속 가능해야 한다.
    """
    _login(page)

    assert _seed_entries(page, [
        {"type": "trade", "tradeType": "매수", "stockName": "가림종목",
         "stockCode": "000101", "price": 1000, "quantity": 7,
         "tradeClass": "장기투자", "rawDate": "2026-07-01T09:00", "id": 900501},
    ]) == 'OK'

    page.reload()
    card = page.locator('#portfolioGrid .portfolio-card[data-id="가림종목"]')
    expect(card).to_have_count(1, timeout=10000)

    btn = page.locator('#btnToggleAmountMask')
    expect(btn).to_have_text('금액 가리기')

    def filter_of(selector):
        return page.evaluate(
            "sel => { const el = document.querySelector(sel);"
            "         return el ? getComputedStyle(el).filter : 'MISSING'; }",
            selector)

    # 기본 상태: 아무것도 가려져 있지 않다
    assert filter_of('#centerTotalInvested') == 'none'

    btn.click()
    expect(btn).to_have_text('금액 보이기')

    # 1) 자산 규모가 드러나는 값은 흐려진다
    for sel in ('#centerTotalInvested', '#centerTotalProfit',
                '#portfolioGrid .portfolio-card[data-id="가림종목"] .cp-eval'):
        assert 'blur' in filter_of(sel), f'가려지지 않음: {sel}'

    # 2) 종목명·현재가는 남는다 (현재가는 공개 시세라 가릴 이유가 없다)
    assert filter_of('#portfolioGrid .portfolio-card[data-id="가림종목"] .stock-name') == 'none'
    assert filter_of('#portfolioGrid .portfolio-card[data-id="가림종목"] .cp-price') == 'none'

    # 3) 핵심: 가려도 값은 계속 계산된다 (예전 토글은 여기서 조회가 끊겼다)
    #    앞선 테스트가 심어 둔 기록도 함께 잡히므로 합계 대신 이 종목 카드로 확인한다.
    assert '7,000' in card.inner_text()
    assert page.locator('#centerTotalInvested').inner_text().endswith('원')
    assert page.evaluate("() => typeof window.currentPriceCache === 'object'")

    # 4) 아래 히스토리 목록의 수량·총액도 함께 가려진다.
    #    대시보드만 가리고 바로 아래 기록에 금액이 그대로 남으면 가리는 의미가 없다.
    hist_amount = page.locator('#historyList .entry-details .masked-amount').first
    expect(hist_amount).to_have_count(1)
    assert 'blur' in page.evaluate(
        "() => getComputedStyle(document.querySelector('#historyList .entry-details .masked-amount')).filter")

    # 5) 새로고침해도 가림 상태가 유지되고, 첫 페인트부터 가려져 있어야 한다.
    #    나중에 걸면 금액이 한 번 노출됐다가 가려지므로 가리는 의미가 없다.
    page.reload()
    expect(page.locator('#btnToggleAmountMask')).to_have_text('금액 보이기', timeout=10000)
    assert page.evaluate(
        "() => document.documentElement.classList.contains('amount-masked')")
    assert 'blur' in filter_of('#centerTotalInvested')

    # 6) 다시 누르면 원래대로 — 되돌리는 비용이 없어야 실제로 쓰인다
    page.locator('#btnToggleAmountMask').click()
    expect(page.locator('#btnToggleAmountMask')).to_have_text('금액 가리기')
    assert filter_of('#centerTotalInvested') == 'none'
    page.reload()
    expect(page.locator('#btnToggleAmountMask')).to_have_text('금액 가리기', timeout=10000)


def test_broker_dropdown_is_built_from_the_single_source(page: Page):
    """증권사 목록이 JS 의 BROKER_NAMES 하나에서 만들어져야 한다.

    예전에는 같은 매핑이 script.js 네 곳 + HTML <option> 에 복붙돼 있었다.
    증권사를 추가할 때 한 곳을 빠뜨리면 화면에 코드('243')가 그대로 노출된다.
    """
    _login(page)
    # ⭐️ script.js 는 defer 라 대시보드 요소가 보인 뒤에야 실행이 끝난다.
    #    전역이 준비되기 전에 evaluate 하면 ReferenceError 로 깨진다.
    page.wait_for_function("() => window.BROKER_CHOICES !== undefined", timeout=5000)

    result = page.evaluate("""() => {
        const opts = [...document.querySelectorAll('#unifiedBrokerCode option')]
            .filter(o => o.value);
        return {
            options: opts.map(o => ({code: o.value, name: o.textContent, data: o.dataset.name})),
            choices: window.BROKER_CHOICES.map(b => ({code: b.code, name: b.name})),
            // 표시 이름 조회가 드롭다운과 같은 소스를 보는지
            mapped: opts.map(o => getMappedBroker(o.value)),
        };
    }""")

    assert result['options'], "증권사 드롭다운이 비어 있다"
    assert [o['code'] for o in result['options']] == [c['code'] for c in result['choices']]
    assert [o['name'] for o in result['options']] == [c['name'] for c in result['choices']]
    # data-name 속성(계좌 등록 시 별칭 기본값으로 쓰인다)도 같은 값이어야 한다
    assert [o['data'] for o in result['options']] == [c['name'] for c in result['choices']]
    # getMappedBroker 가 드롭다운의 모든 코드를 이름으로 바꿀 수 있어야 한다
    assert result['mapped'] == [c['name'] for c in result['choices']]


def test_broker_map_accepts_both_hts_code_forms(page: Page):
    """HTS 는 증권사 코드를 표준 3자리로도, 축약 1자리로도 보낸다. 둘 다 같은 이름."""
    _login(page)
    page.wait_for_function("() => window.BROKER_CHOICES !== undefined", timeout=5000)

    pairs = page.evaluate("""() => ([
        [getMappedBroker('264'), getMappedBroker('1')],
        [getMappedBroker('243'), getMappedBroker('4')],
        [getMappedBroker('271'), getMappedBroker('6')],
    ])""")
    for standard, short in pairs:
        assert standard == short, f"{standard} != {short}"
    # 모르는 코드는 그대로 돌려준다 (임의 문자열을 삼키지 않는다)
    assert page.evaluate("() => getMappedBroker('999')") == '999'
    assert page.evaluate("() => getMappedBroker('')") == ''
