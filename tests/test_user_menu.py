"""내 계정 메뉴의 키보드 접근, 기존 동작, 화면 설정과 세션 연동을 검증한다."""

import json
import re
import time

from playwright.sync_api import expect

from app.database.db import db_conn
from app.services import entries
from helpers import _buy
from test_editor_table import BASE_URL, USER_ID, USER_PW, _login
from test_journal_frontend import journal_data as journal_data, live_server as live_server


def _ready(page):
    _login(page)
    page.wait_for_function('() => preferencesLoaded')
    expect(page.locator('#loggedInUserDisplay')).to_be_visible()


def _open(page):
    page.locator('#loggedInUserDisplay').click()
    expect(page.locator('#userAccountPanel')).to_be_visible()


def test_account_menu_is_nonmodal_and_keyboard_dismissible(page):
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    _ready(page)
    trigger = page.locator('#loggedInUserDisplay')
    panel = page.locator('#userAccountPanel')
    assert trigger.evaluate('(element) => element.tagName') == 'BUTTON'
    expect(trigger).to_have_attribute('aria-controls', 'userAccountPanel')
    expect(trigger).to_have_attribute('aria-expanded', 'false')
    expect(panel).not_to_be_visible()
    trigger.focus()
    trigger.press('Enter')
    expect(panel).to_be_visible()
    expect(panel).to_have_attribute('role', 'dialog')
    expect(panel).to_have_attribute('aria-label', '내 계정')
    assert panel.get_attribute('aria-modal') != 'true'
    expect(trigger).to_have_attribute('aria-expanded', 'true')
    expect(page.locator('#accountMenuUsername')).to_have_text(USER_ID)
    expect(page.locator('#accountMenuRole')).to_have_text(re.compile('관리자|일반 사용자'))
    page.keyboard.press('Escape')
    expect(panel).not_to_be_visible()
    expect(trigger).to_be_focused()
    expect(trigger).to_have_attribute('aria-expanded', 'false')

    _open(page)
    page.locator('#btnListView').click()
    expect(panel).not_to_be_visible()
    expect(page.locator('#btnListView')).to_be_focused()
    _open(page)
    panel.locator('button:visible, select:visible, input:visible, a[href]:visible').last.focus()
    page.keyboard.press('Tab')
    expect(panel).not_to_be_visible()
    assert not page.evaluate("document.getElementById('userAccountPanel').contains(document.activeElement)")
    assert errors == []


def test_password_action_and_logout_confirmation_keep_existing_flows(page):
    _ready(page)
    _open(page)
    expect(page.locator('#userAccountPanel #btnChangePassword')).to_have_count(1)
    expect(page.locator('#userAccountPanel #btnLogout')).to_have_count(1)
    page.locator('#btnChangePassword').click()
    expect(page.locator('#passwordModalOverlay')).to_be_visible()
    expect(page.locator('#userAccountPanel')).not_to_be_visible()
    assert page.evaluate('document.body.style.overflow') == 'hidden'
    page.locator('#btnClosePasswordModal').click()
    expect(page.locator('#passwordModalOverlay')).not_to_be_visible()

    # 임시 비밀번호 로그인은 닫힌 메뉴의 버튼을 프로그램으로 호출한다.
    page.evaluate("document.getElementById('btnChangePassword').click()")
    expect(page.locator('#passwordModalOverlay')).to_be_visible()
    page.locator('#btnClosePasswordModal').click()
    expect(page.locator('#passwordModalOverlay')).not_to_be_visible()
    _open(page)
    page.locator('#btnLogout').click()
    expect(page.locator('#customModalMessage')).to_have_text('로그아웃 하시겠습니까?')
    expect(page.locator('#userAccountPanel')).not_to_be_visible()
    page.locator('#btnCustomModalCancel').click()
    expect(page.locator('#loggedInUserDisplay')).to_be_visible()
    assert page.url == BASE_URL + '/'


def test_account_actions_remain_available_when_me_request_fails(page):
    page.route('**/api/me', lambda route: route.fulfill(status=503, json={'error': 'temporary'}))
    _ready(page)
    _open(page)
    expect(page.locator('#accountMenuUsername')).to_have_text(USER_ID)
    page.locator('#btnChangePassword').click()
    expect(page.locator('#passwordModalOverlay')).to_be_visible()
    page.locator('#btnClosePasswordModal').click()
    expect(page.locator('#passwordModalOverlay')).not_to_be_visible()
    _open(page)
    page.locator('#btnLogout').click()
    expect(page.locator('#customModalMessage')).to_have_text('로그아웃 하시겠습니까?')
    page.locator('#btnCustomModalCancel').click()


def test_profile_theme_and_portfolio_amount_controls_survive_reload(page):
    entries.create(USER_ID, _buy(id=1, qty=2, price=10000))
    _ready(page)
    _open(page)
    expect(page.locator('#accountMenuHideAmounts')).to_have_count(0)
    expect(page.locator('#theme-toggle, .theme-switch-wrapper')).to_have_count(0)
    expect(page.locator('#newsSidebar')).not_to_contain_text('화면 테마')
    page.locator('#accountMenuTheme').select_option('light')
    expect(page.locator('html')).to_have_attribute('data-theme', 'light')
    expect(page.locator('#flatpickr-dark-theme')).to_have_attribute('disabled', 'disabled')
    page.keyboard.press('Escape')

    # 금액 표시는 기존 포트폴리오 버튼에서만 조절한다.
    for identifier in ('btnToggleAmountMask', 'btnToggleClosed', 'btnToggleMarketMode', 'btnTogglePortfolio'):
        expect(page.locator(f'#portfolioSection .portfolio-header #{identifier}')).to_be_visible()
        expect(page.locator(f'#userAccountPanel #{identifier}')).to_have_count(0)
    page.locator('#btnToggleAmountMask').click()
    expect(page.locator('#btnToggleAmountMask')).to_have_text('금액 보이기')
    page.reload()
    expect(page.locator('#loggedInUserDisplay')).to_be_visible()
    expect(page.locator('html')).to_have_attribute('data-theme', 'light')
    expect(page.locator('html')).to_have_class(re.compile(r'\bamount-masked\b'))
    _open(page)
    expect(page.locator('#accountMenuTheme')).to_have_value('light')
    page.locator('#accountMenuTheme').select_option('dark')
    expect(page.locator('html')).to_have_attribute('data-theme', 'dark')
    assert page.locator('#flatpickr-dark-theme').get_attribute('disabled') is None
    page.keyboard.press('Escape')
    page.locator('#btnToggleAmountMask').click()
    expect(page.locator('#btnToggleAmountMask')).to_have_text('금액 가리기')
    page.reload()
    expect(page.locator('html')).to_have_attribute('data-theme', 'dark')
    expect(page.locator('html')).not_to_have_class(re.compile(r'\bamount-masked\b'))


def test_last_screen_and_fixed_start_screen_are_distinct(page):
    _ready(page)
    _open(page)
    expect(page.locator('#accountMenuDefaultView')).to_have_value('last')
    page.keyboard.press('Escape')
    page.locator('#btnCalendarView').click()
    page.reload()
    expect(page.locator('#btnCalendarView')).to_have_class(re.compile(r'\bactive\b'))
    _open(page)
    with page.expect_response(lambda response: '/api/preferences' in response.url
                              and response.request.method == 'PATCH') as save:
        page.locator('#accountMenuDefaultView').select_option('chart')
    assert save.value.ok
    page.keyboard.press('Escape')
    page.locator('#btnListView').click()
    page.reload()
    expect(page.locator('#btnChartView')).to_have_class(re.compile(r'\bactive\b'))
    expect(page.locator('#monthlyChartSection')).to_be_visible()
    _open(page)
    expect(page.locator('#accountMenuDefaultView')).to_have_value('chart')
    with page.expect_response(lambda response: '/api/preferences' in response.url
                              and response.request.method == 'PATCH'):
        page.locator('#accountMenuDefaultView').select_option('last')
    page.keyboard.press('Escape')
    # 고정 시작 화면으로 자동 복원한 차트도 마지막으로 본 화면이어야 한다.
    # 복원 전에 수동 선택했던 리스트로 되돌아가면 안 된다.
    page.reload()
    expect(page.locator('#btnChartView')).to_have_class(re.compile(r'\bactive\b'))
    expect(page.locator('#monthlyChartSection')).to_be_visible()
    page.locator('#btnCalendarView').click()
    page.reload()
    expect(page.locator('#btnCalendarView')).to_have_class(re.compile(r'\bactive\b'))


def test_start_screen_setting_preserves_other_preferences(page):
    with db_conn() as conn:
        conn.execute('UPDATE users SET preferences = ? WHERE username = ?',
                     (json.dumps({'isDashboardCollapsed': True, 'portfolioOrder': ['preserve-me']}), USER_ID))
        conn.commit()
    _ready(page)
    _open(page)
    with page.expect_response(lambda response: '/api/preferences' in response.url
                              and response.request.method == 'PATCH') as save:
        page.locator('#accountMenuDefaultView').select_option('calendar')
    assert save.value.ok
    saved = page.request.get(BASE_URL + '/api/preferences').json()
    assert saved['isDashboardCollapsed'] is True
    assert saved['portfolioOrder'] == ['preserve-me']
    page.reload()
    expect(page.locator('#btnCalendarView')).to_have_class(re.compile(r'\bactive\b'))


def test_last_screen_is_not_inherited_from_another_account_on_same_device(page):
    with db_conn() as conn:
        user_id = conn.execute('SELECT id FROM users WHERE username = ?', (USER_ID,)).fetchone()['id']
    page.add_init_script(f"localStorage.setItem('journalLastView:{user_id + 1}', 'chart')")
    _ready(page)
    expect(page.locator('#btnListView')).to_have_class(re.compile(r'\bactive\b'))
    page.locator('#btnCalendarView').click()
    assert page.evaluate('(key) => localStorage.getItem(key)', f'journalLastView:{user_id}') == 'calendar'
    assert page.evaluate('(key) => localStorage.getItem(key)', f'journalLastView:{user_id + 1}') == 'chart'


def test_start_screen_cannot_overwrite_unavailable_preferences(page):
    requests = []
    def unavailable(route):
        requests.append(route.request.method)
        route.fulfill(status=503, json={'error': 'temporary'})
    page.route('**/api/preferences', unavailable)
    _login(page)
    expect(page.locator('#loggedInUserDisplay')).to_be_visible()
    expect(page.locator('#historyList .entry-card')).to_have_count(15)
    _open(page)
    expect(page.locator('#accountMenuDefaultView')).to_be_disabled()
    assert not {'POST', 'PATCH'} & set(requests)


def test_start_screen_save_failure_restores_selection_and_allows_retry(page):
    _ready(page)
    _open(page)
    def reject_write(route):
        if route.request.method in ('POST', 'PATCH'):
            route.fulfill(status=503, json={'error': 'temporary'})
        else:
            route.continue_()
    page.route('**/api/preferences', reject_write)
    select = page.locator('#accountMenuDefaultView')
    select.select_option('chart')
    expect(select).to_have_value('last')
    expect(select).to_be_enabled()
    expect(page.locator('#accountMenuStatus')).not_to_be_empty()
    page.unroute('**/api/preferences', reject_write)
    with page.expect_response(lambda response: '/api/preferences' in response.url
                              and response.request.method == 'PATCH') as save:
        select.select_option('calendar')
    assert save.value.ok
    expect(select).to_have_value('calendar')
    page.reload()
    expect(page.locator('#btnCalendarView')).to_have_class(re.compile(r'\bactive\b'))


def test_different_preferences_changed_in_two_tabs_are_both_preserved(page):
    entries.create(USER_ID, _buy(id=1, qty=2, price=10000))
    _ready(page)
    other = page.context.new_page()
    try:
        other.route('**/api/news', lambda route: route.fulfill(json=[]))
        other.route('**/api/current_price', lambda route: route.fulfill(json={}))
        other.goto(BASE_URL + '/')
        other.wait_for_function('() => preferencesLoaded')
        _open(page)
        with page.expect_response(lambda r: '/api/preferences' in r.url and r.request.method == 'PATCH'):
            page.locator('#accountMenuDefaultView').select_option('chart')
        with other.expect_response(lambda r: '/api/preferences' in r.url and r.request.method == 'PATCH'):
            other.locator('#btnToggleClosed').click()
        saved = page.request.get(BASE_URL + '/api/preferences').json()
        assert saved['defaultView'] == 'chart'
        assert saved['showClosedPositions'] is True
    finally:
        other.close()


def test_old_account_screen_cannot_save_into_newly_logged_in_account(page):
    from werkzeug.security import generate_password_hash

    other_user = 'menuother'
    original = {'defaultView': 'list', 'preserveOtherAccount': True}
    with db_conn() as conn:
        conn.execute('INSERT INTO users (username,password_hash,is_allowed,preferences) VALUES (?,?,1,?)',
                     (other_user, generate_password_hash(USER_PW), json.dumps(original)))
        conn.commit()
    _ready(page)
    _open(page)
    # 별도 창에서 로그인한 것처럼 공용 쿠키만 바꾸고 기존 화면은 그대로 둔다.
    response = page.request.post(BASE_URL + '/login', form={
        'username': other_user, 'password': USER_PW,
        'csrf_token': page.evaluate('window.JOURNAL_CSRF_TOKEN')})
    assert response.ok
    with page.expect_response(lambda r: '/api/preferences' in r.url and r.request.method == 'PATCH') as save:
        page.locator('#accountMenuDefaultView').select_option('chart')
    assert save.value.status == 409
    assert save.value.headers.get('x-journal-account-changed') == '1'
    expect(page.locator('#loggedInUserDisplay')).to_contain_text(other_user)
    with db_conn() as conn:
        actual = conn.execute('SELECT preferences FROM users WHERE username=?', (other_user,)).fetchone()[0]
        assert json.loads(actual) == original
        assert conn.execute('SELECT preferences FROM users WHERE username=?', (USER_ID,)).fetchone()[0] is None


def test_session_extension_updates_menu_and_failure_is_retryable(page):
    _ready(page)
    identity = page.evaluate('({username:JOURNAL_USERNAME,user_id:JOURNAL_USER_ID})')
    original_expiry = int(time.time()) + 600
    page.evaluate('data => applyServerSession(data)', {**identity, 'expires_at': original_expiry, 'keep_logged_in': False})
    _open(page)
    remaining = page.locator('#accountMenuSessionRemaining')
    expect(remaining).not_to_be_empty()
    before = remaining.inner_text()
    button = page.locator('#btnAccountExtendSession')
    expect(button).to_be_enabled()
    page.route('**/api/ping', lambda route: route.fulfill(status=503, json={'error': 'temporary'}))
    button.click()
    expect(button).to_be_enabled()
    assert page.evaluate('getSessionState().expiresAtMs') == original_expiry * 1000
    expect(page.locator('#accountMenuStatus')).not_to_be_empty()
    new_expiry = int(time.time()) + 3595
    page.route('**/api/ping', lambda route: route.fulfill(json={**identity,
        'status': 'success', 'expires_at': new_expiry, 'keep_logged_in': False}))
    button.click()
    page.wait_for_function('(expiry) => getSessionState().expiresAtMs === expiry', arg=new_expiry * 1000)
    expect(remaining).not_to_have_text(before)
    expect(button).to_be_enabled()


def test_keep_logged_in_session_cannot_offer_extension(page):
    _ready(page)
    page.evaluate('''() => applyServerSession({username:JOURNAL_USERNAME,user_id:JOURNAL_USER_ID,
        expires_at:Math.floor(Date.now()/1000)+23*3600,keep_logged_in:true})''')
    _open(page)
    button = page.locator('#btnAccountExtendSession')
    assert not button.is_visible() or button.is_disabled()
    expect(page.locator('#accountMenuSessionPolicy')).to_contain_text('24시간')
    expect(page.locator('#accountMenuSessionRemaining')).not_to_be_empty()


def test_account_menu_fits_mobile_screen_and_preserves_settings_actions(page):
    page.set_viewport_size({'width': 360, 'height': 640})
    _ready(page)
    _open(page)
    bounds = page.locator('#userAccountPanel').bounding_box()
    assert bounds is not None
    assert bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= 360
    assert bounds['y'] >= 0 and bounds['y'] + bounds['height'] <= 640
    for identifier in ('btnAccountManagement', 'btnHtsIntegration', 'btnDataManagement', 'btnAdmin'):
        expect(page.locator(f'.header-action-group #{identifier}')).to_have_count(1)
    expect(page.locator('.header-action-group #btnChangePassword')).to_have_count(0)

    # 테마 영역이 없어도 모바일 뉴스 배치·펼치기와 데스크톱 복귀가 동작한다.
    page.keyboard.press('Escape')
    expect(page.locator('#mainApp > #newsSidebar')).to_have_count(1)
    page.locator('#btnToggleNews').click()
    expect(page.locator('#newsList')).to_have_class(re.compile(r'\bnews-expanded\b'))
    page.locator('#btnToggleNews').click()
    expect(page.locator('#newsList')).not_to_have_class(re.compile(r'\bnews-expanded\b'))
    page.set_viewport_size({'width': 1280, 'height': 720})
    expect(page.locator('.main-layout > #newsSidebar')).to_have_count(1)
    expect(page.locator('#btnToggleNews')).not_to_be_visible()
