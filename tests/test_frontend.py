import pytest
import threading
import datetime
import os
from werkzeug.serving import make_server
from playwright.sync_api import Page, expect

from app.utils import ratelimit

BASE_URL = "http://127.0.0.1:5001"

# ⭐️ 페이지 전환(가입·로그인·최초 렌더)을 기다리는 예산.
#    이 모듈은 전체 스위트와 함께 돌리면 소요 시간이 크게 흔들린다 — 같은 17개
#    테스트가 28초에 끝나기도 하고 68초가 걸리기도 했다(실측). 그 편차 안에서
#    10초 예산은 종종 모자라는데, 실패는 "가입이 완료되지 않았습니다" / "관리자
#    표에 행이 없습니다" 처럼 **제품 버그의 모습**으로 나타나 원인을 엉뚱한
#    곳에서 찾게 된다. 넉넉히 잡는다 — 통과하는 경우의 실행 시간은 늘지 않는다.
#    (화면 안에서 이미 그려진 요소가 바뀌는 것을 기다리는 단언은 종전 값을 쓴다.
#     그건 서버 응답이 아니라 스크립트 실행을 기다리는 것이라 편차가 작다.)
NAV_TIMEOUT_MS = 30000

# ⭐️ 관리자 계정은 테스트의 '부작용'이 아니라 '전제'다. 아래 live_server 참고.
ADMIN_ID = 'admin'
ADMIN_PW = 'admin123'

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

    # ⭐️ 레이트리밋·잠금 상태는 프로세스 전역인데, 이 모듈은 conftest 의 app
    #    픽스처를 쓰지 않으므로 **아무도 초기화해 주지 않는다.** 그래서 앞 모듈들이
    #    남긴 가입·초기화요청 카운터를 그대로 물려받았고, 이 모듈의 결과가
    #    "직전에 어떤 테스트가 무엇을 했는가"에 좌우됐다.
    #
    #    실제로 이렇게 무너진다 — 가입은 IP 당 5회/시간이다. 잔량이 차 있으면
    #    회원가입이 조용히 거부되고(429 가 아니라 가입 화면 재렌더링), _signup 은
    #    오지 않는 리다이렉트를 기본 30초 동안 기다리다 죽는다. 계정이 만들어지지
    #    않았으므로 뒤따르는 로그인 테스트가 전부 "아이디 또는 비밀번호가 일치하지
    #    않습니다" 로 실패한다 — 제품 버그처럼 보이지만 계정이 아예 없는 것이다.
    #    잔량 5로 채워 재현하면 11개가 실패하고 모듈 실행 시간이 30초 → 95초가 된다.
    ratelimit.reset_all()

    # ⭐️ 관리자 계정을 **여기서** 만든다. 예전에는 test_user_login_and_dashboard_render
    #    가 _signup 으로 만들었고, 그래서 뒤따르는 8개 테스트가 그 테스트 하나의
    #    성공에 매달려 있었다. 순서가 바뀌거나 -k 로 걸러내거나 그 테스트가 어떤
    #    이유로든 실패하면 나머지가 줄줄이 무너지는데, 남는 단서는 로그인 실패
    #    문구뿐이라 원인을 찾을 수 없다. (테스트 하나만 따로 실행해도 실패했다)
    #
    #    비밀번호 해시와 '최초 가입자 = 최고 관리자' 규칙을 테스트가 흉내 내지
    #    않도록 실제 가입 경로를 그대로 태운다.
    signup = flask_app.test_client().post('/signup', data={
        'username': ADMIN_ID, 'password': ADMIN_PW, 'password_confirm': ADMIN_PW})
    assert '최고 관리자' in signup.get_data(as_text=True), (
        "관리자 계정 준비에 실패했습니다 — 이 모듈의 로그인 테스트가 모두 무너집니다. "
        f"(status={signup.status_code})")

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
#       0회였으므로, 그 실패의 원인이 아니다.
#
#    그 실패의 진짜 원인은 계정이 **없었던** 것이고, 원인은 live_server 픽스처의
#    주석에 적어 두었다 (전역 레이트리밋 잔량 + 테스트 간 순서 의존). 화면 문구는
#    계정 없음과 비밀번호 불일치를 일부러 구분해 주지 않으므로, 아래 진단이
#    서버 쪽 사실을 함께 싣는다.
# ─────────────────────────────────────────────────────────────
def _wait_for_login_form(page: Page):
    """로그인 폼이 조작 가능한 상태가 될 때까지 기다린다."""
    expect(page.locator('input[name="username"]')).to_be_visible(timeout=NAV_TIMEOUT_MS)
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

def _login(page: Page, username=ADMIN_ID, password=ADMIN_PW, *, navigate=True):
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
            timeout=NAV_TIMEOUT_MS)
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
    expect(page.locator('input[name="username"]')).to_be_visible(timeout=NAV_TIMEOUT_MS)
    page.fill('input[name="username"]', username)
    page.fill('input[name="password"]', password)
    page.fill('input[name="password_confirm"]', password)
    page.click('button[type="submit"]')

    # ⭐️ 가입이 거부되면(중복 아이디·약한 비밀번호·가입 횟수 제한) 서버는 오류를
    #    담아 가입 화면을 다시 그릴 뿐 리다이렉트하지 않는다. 그대로 두면 기본
    #    30초를 기다리다 "URL 이 안 바뀐다" 로만 죽어, 정작 화면에 적혀 있는
    #    이유가 실패 메시지에 남지 않는다.
    try:
        page.wait_for_url('**/login', timeout=NAV_TIMEOUT_MS)
    except Exception:                                    # noqa: BLE001
        raise AssertionError(
            f"'{username}' 가입이 완료되지 않았습니다 — 화면 문구: "
            f"{page.locator('body').inner_text().strip()[:300]!r}") from None
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
    """폼에 값을 입력해 로그인하면 메인 대시보드로 넘어가는지 테스트합니다.

    계정은 live_server 픽스처가 미리 만들어 둔다. 예전에는 이 테스트가 직접
    가입시켜서, 뒤따르는 로그인 테스트 8개가 이 테스트의 성공에 매달려 있었다.
    (브라우저 가입 폼 자체는 아래 _signup 을 쓰는 테스트들이, '최초 가입자 =
    최고 관리자' 규칙은 tests/test_auth.py 가 검증한다)
    """
    _login(page)

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


def test_chart_excludes_flagged_accounts(page: Page):
    """차트 뷰(실현손익)도 '금액 계산 제외' 계좌를 빼고 그려야 한다."""
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
        # 제외 계좌(연습계좌): 역대급 큰 손실 - 차트에 섞이면 즉시 티가 난다
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
    page.wait_for_url('**/login', timeout=NAV_TIMEOUT_MS)
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
    # ⭐️ 가입도 IP 당 5회/시간이다. 잔량은 모듈 시작 때 한 번만 비워지므로(live_server),
    #    이 모듈에 가입하는 테스트가 하나만 늘어도 여기서 잔량이 바닥난다. 그러면
    #    _signup 이 죽는 게 아니라 계정이 조용히 안 생기고, 실패는 30초 뒤 "관리자
    #    표에 행이 안 뜬다" 로 나타난다 — 원인과 증상이 완전히 떨어져 있다.
    #    아래 초기화 요청 잔량과 같은 이유로, 쓰기 직전에 비운다.
    ratelimit.signups.reset()
    _signup(page, long_name)

    # 초기화 요청을 여러 번 넣어 '×N' 배지까지 붙인 상태로 만든다.
    #
    # ⭐️ 초기화 요청은 IP 당 5회/시간인데, 이 모듈의 요청 테스트 2건 + 여기 3건
    #    = 정확히 5로 여유가 0이었다. 요청을 쓰는 테스트가 하나만 늘어도 여기서
    #    429 를 받아 배지가 조용히 안 붙고, 실패는 엉뚱하게 "표에 '요청' 이 없다"
    #    로 나타난다. 응답을 버리고 있어 원인이 남지도 않았다.
    ratelimit.reset_requests.reset()
    for _ in range(3):
        res = page.request.post(BASE_URL + '/request_password_reset',
                                data={'username': long_name})
        assert res.ok, f"초기화 요청이 거부됐습니다: {res.status} {res.text()}"

    _login(page, navigate=False)

    page.click('.header-action-icon')
    page.click('#btnAdmin')
    # ⭐️ 예전에는 wait_for_function 한 줄이었다. 전체 스위트 실행에서 드물게 여기서
    #    30초를 채우고 죽었는데, 남는 단서가 "함수가 true 를 돌려주지 않았다" 뿐이라
    #    행이 아예 없는 건지, 이 계정만 없는 건지, 열이 모자란 건지 구분되지 않았다.
    #    (11회 재현 시도 중 1회 — 원인 미확정. 다음에 터지면 여기서 갈린다)
    #    세 조건을 따로 세워, 실패한 순간 서버가 뭐라고 답했는지까지 함께 싣는다.
    try:
        expect(page.locator('#adminUserList tr')).not_to_have_count(0, timeout=NAV_TIMEOUT_MS)
        expect(page.locator(f'#adminUserList tr:has-text("{long_name}")')).to_have_count(1)
    except AssertionError:
        listed = page.request.get(BASE_URL + '/api/admin/users')
        raise AssertionError(
            f"관리자 표에 '{long_name}' 행이 뜨지 않았습니다.\n"
            f"  화면 행 수: {page.locator('#adminUserList tr').count()}\n"
            f"  /api/admin/users: {listed.status} {listed.text()[:400]}\n"
            f"  표 본문: {page.locator('#adminUserList').inner_text()[:300]!r}") from None

    columns = page.locator('#adminUserList tr').first.locator('td, th').count()
    assert columns >= 5, f"관리자 표의 열이 {columns}개뿐입니다 — 관리 열이 그려지지 않았습니다."

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
    card = page.locator('#portfolioGrid .portfolio-card[data-id="000101"]')
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
                '#portfolioGrid .portfolio-card[data-id="000101"] .cp-eval'):
        assert 'blur' in filter_of(sel), f'가려지지 않음: {sel}'

    # 2) 종목명·현재가는 남는다 (현재가는 공개 시세라 가릴 이유가 없다)
    assert filter_of('#portfolioGrid .portfolio-card[data-id="000101"] .stock-name') == 'none'
    assert filter_of('#portfolioGrid .portfolio-card[data-id="000101"] .cp-price') == 'none'

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


def test_same_code_different_names_is_one_position(page: Page):
    """종목명 표기가 갈려도 **종목코드가 같으면 한 종목**이다.

    [사고 · 2026-08-24] HTS 봇이 보낸 체결의 종목명은 증권사 정식 명칭
    ('KODEX 삼성그룹')이고, 이미 들고 있던 기록은 사용자가 적어 둔 이름
    ('KODEX 삼성그룹 ETF')이었다. 종목코드는 양쪽 다 102780 인데 화면이 이름으로
    묶는 바람에 1주가 기존 보유 228주에 합쳐지지 않고 별도 카드로 섰다.
    백엔드(stats.stock_identity)는 처음부터 코드 1순위였다 — 화면만 뒤처져 있었다.
    """
    _login(page)

    assert _seed_entries(page, [
        {"type": "trade", "tradeType": "매수", "stockName": "KODEX 삼성그룹 ETF",
         "stockCode": "102780", "price": 20402, "quantity": 228,
         "tradeClass": "중기투자", "rawDate": "2026-04-10T09:25", "id": 900601},
        # 봇이 밀어 넣은 수동 체결 — 이름만 다르고 코드는 같다
        {"type": "trade", "tradeType": "매수", "stockName": "KODEX 삼성그룹",
         "stockCode": "102780", "price": 25500, "quantity": 1,
         "tradeClass": "중기투자", "rawDate": "2026-08-24T12:00", "id": 900602},
    ]) == 'OK'

    page.reload()
    card = page.locator('#portfolioGrid .portfolio-card[data-id="102780"]')
    expect(card).to_have_count(1, timeout=10000)

    # 카드가 하나뿐이어야 한다 — 이름으로 묶이면 여기서 2가 된다
    expect(page.locator('#portfolioGrid .portfolio-card:has-text("KODEX")')).to_have_count(1)

    # 수량은 228 + 1 = 229주로 합쳐진다
    expect(card).to_contain_text('229주')

    # 카드 클릭 → 히스토리 필터. 표기가 다른 기록까지 함께 나와야 한다
    #  (이름으로 거르면 둘 중 하나만 남아 '카드는 합쳐졌는데 목록은 반만' 보인다)
    matched = page.evaluate("""() => cloudEntries.filter(
        e => e.type === 'trade' && identityOf(e) === identityForStockName('KODEX 삼성그룹')
    ).length""")
    assert matched == 2, f"이름이 갈린 기록이 필터에서 빠진다 (matched={matched})"


def test_stock_filter_dropdown_lists_one_row_per_stock(page: Page):
    """종목 드롭다운은 **한 종목당 한 줄**이다 — 표기가 갈려도.

    카드가 코드로 합쳐진 뒤에도 드롭다운은 이름으로 모으고 있었다. 그래서 같은 종목이
    '한화에어로스페이스'와 '한화에어로' 두 줄로 서는데, 필터는 동일성(코드)으로 대조하므로
    **어느 줄을 골라도 결과가 같다** — 사용자는 둘의 차이를 알 수 없고 목록만 길어진다.
    한 줄로 세우되 이름은 가장 최근 체결의 것을 쓴다(백엔드 stats.display_names 와 같은 규칙).
    """
    _login(page)

    assert _seed_entries(page, [
        {"type": "trade", "tradeType": "매수", "stockName": "한화에어로스페이스",
         "stockCode": "012450", "price": 700000, "quantity": 3,
         "tradeClass": "중기투자", "rawDate": "2026-05-02T09:10", "id": 900701},
        # 나중에 들어온 봇 체결 — 코드는 같고 이름 표기만 짧다
        {"type": "trade", "tradeType": "매수", "stockName": "한화에어로",
         "stockCode": "012450", "price": 800000, "quantity": 1,
         "tradeClass": "중기투자", "rawDate": "2026-08-20T13:30", "id": 900702},
    ]) == 'OK'

    page.reload()
    expect(page.locator('#portfolioGrid .portfolio-card[data-id="012450"]')).to_have_count(1, timeout=10000)

    options = page.evaluate(
        "() => Array.from(document.querySelectorAll('#filterStockSelect option')).map(o => o.value)")
    hanwha = [o for o in options if o.startswith('한화')]
    assert hanwha == ['한화에어로'], f"드롭다운이 같은 종목을 여러 줄로 세운다: {hanwha}"

    # 차트 필터 드롭다운도 같은 목록을 쓴다
    chart_options = page.evaluate(
        "() => Array.from(document.querySelectorAll('#chartStockFilter option')).map(o => o.value)")
    assert [o for o in chart_options if o.startswith('한화')] == ['한화에어로']

    # 표시 이름은 화면 어디서나 같은 규칙(가장 최근 체결의 이름)으로 정해진다 —
    #  카드·달력 배지·월별 차트 분해 목록이 이 함수 하나를 본다.
    assert page.evaluate(
        "() => displayNameForEntry({type:'trade', stockName:'한화에어로스페이스', stockCode:'012450'})"
    ) == '한화에어로'
    expect(page.locator('#portfolioGrid .portfolio-card[data-id="012450"]')).to_contain_text('한화에어로')

    # 옛 표기로 저장돼 있던 필터 값도 같은 종목의 현재 이름으로 옮겨 준다(필터가 풀리지 않는다).
    resolved = page.evaluate("""() => {
        const opts = Array.from(document.querySelectorAll('#filterStockSelect option')).map(o => o.value);
        return resolveStockOptionValue(opts, '한화에어로스페이스');
    }""")
    assert resolved == '한화에어로', f"옛 이름이 현재 옵션으로 옮겨지지 않는다: {resolved!r}"


def test_shared_alias_excludes_only_flagged_account(page: Page):
    """별칭이 같은 계좌가 여럿일 때, 제외 표시한 계좌의 기록만 빠져야 한다.

    증권사마다 '일반계좌'가 있는 게 보통이다. 예전에는 계좌 이름으로 한 번 더
    대조하는 단계에서 토스증권 '일반계좌'를 제외하면 한국투자증권 '일반계좌'까지
    함께 빠졌다.
    """
    _login(page)

    assert page.evaluate("""async () => {
        const res = await fetch('/api/mappings', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({brokers: {}, accounts: {
                "189-01-501685": {broker_code: "271", broker_name: "토스증권",
                                  alias: "일반계좌", exclude_from_stats: true},
                "68029263-01": {broker_code: "243", broker_name: "한국투자증권",
                                alias: "일반계좌", exclude_from_stats: false}
            }})
        });
        return res.ok ? 'OK' : 'FAIL';
    }""") == 'OK'

    assert _seed_entries(page, [
        # 한국투자증권 일반계좌(포함): 1,000원 × 10주
        {"type": "trade", "tradeType": "매수", "stockName": "한투종목",
         "stockCode": "000021", "price": 1000, "quantity": 10,
         "rawDate": "2026-07-01T09:00", "id": 900201, "subAccount": "68029263-01"},
        # 토스증권 일반계좌(제외): 50,000원 × 10주 — 합계에 섞이면 즉시 티가 난다
        {"type": "trade", "tradeType": "매수", "stockName": "토스종목",
         "stockCode": "000022", "price": 50000, "quantity": 10,
         "rawDate": "2026-07-02T09:00", "id": 900202, "subAccount": "189-01-501685"},
    ]) == 'OK'

    page.reload()
    #  앞선 테스트가 심어 둔 기록이 같은 계정에 남아 있으므로 개수가 아니라 이 두 장으로 본다.
    expect(page.locator('#portfolioGrid .portfolio-card[data-id="000021"]')).to_have_count(1, timeout=10000)
    #  제외 계좌 카드는 실거래와 섞이지 않도록 키에 접미사가 붙는다 (portfolioKey)
    expect(page.locator('#portfolioGrid .portfolio-card[data-id="000022::제외"]')).to_have_count(1)

    # 판정 함수 자체를 직접 확인한다 — 계좌번호가 특정되면 그 계좌의 설정만 따른다.
    verdicts = page.evaluate("""() => {
        const find = id => cloudEntries.find(e => Number(e.id) === id);
        return {
            kis: isExcludedFromTotals(find(900201)),
            toss: isExcludedFromTotals(find(900202)),
        };
    }""")
    assert verdicts == {'kis': False, 'toss': True}

    # 도넛 차트에도 한국투자증권 기록만 들어가고, 제외한 토스증권 기록은 빠진다
    labels = page.evaluate("() => portfolioChartInstance.data.labels")
    assert '한투종목' in labels
    assert '토스종목' not in labels


def test_saving_accounts_reloads_page(page: Page):
    """계좌 정보를 저장하면 화면이 한 번 새로고침돼 저장된 설정이 곧바로 반영돼야 한다.

    '금액 계산 제외' 같은 설정은 서버가 계산하는 통계 응답까지 바꾸므로, 화면 일부만
    다시 그리면 값이 어긋난다.
    """
    _login(page)
    page.reload()
    page.wait_for_load_state()

    # 새로고침 여부를 판별할 표식 — 리로드되면 사라진다
    page.evaluate("() => { window.__beforeAccountSave = true; }")
    assert page.evaluate("() => window.__beforeAccountSave === true")

    #  ⚙️ 설정 메뉴는 토글이라 실행 순서에 따라 접힘 상태가 달라진다. 여기서 확인할 것은
    #  '저장 후 새로고침'이므로 메뉴 토글은 건너뛰고 계좌 관리 버튼의 핸들러를 직접 태운다.
    page.dispatch_event('#btnAccountManagement', 'click')
    expect(page.locator('#btnSaveAccountMappings')).to_be_visible()
    with page.expect_navigation():
        page.dispatch_event('#btnSaveAccountMappings', 'click')
        expect(page.locator('#btnCustomModalOk')).to_be_visible()
        page.dispatch_event('#btnCustomModalOk', 'click')  # '저장되었습니다' 확인

    page.wait_for_load_state()
    assert page.evaluate("() => window.__beforeAccountSave === undefined")
