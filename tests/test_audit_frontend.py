"""전체 코드 감사(2026-09-24) — 화면 XSS·기능 보완 회귀 방지 (브라우저 E2E).

감사 때 두 경로 모두 실제로 스크립트가 실행됐다(AUDIT-11·12). test_audit.py 와
마찬가지로 xfail 로 재현한 뒤, 수정과 함께 표시를 걷어냈다.

재현 방식: 스크립트가 실행되면 window.__xss 를 세우는 페이로드를 심고, 화면을
그린 뒤 그 값이 비어 있는지 본다. (alert 대신 플래그라 헤드리스에서 확실히 잡힌다)

뒤쪽의 기능 보완 테스트는 '검토 필요' 배지·모아 보기·검토 완료와, 매수를 지워 초과
매도가 생길 때의 확인 창을 본다 (서버 쪽 규칙은 test_audit_functional.py).
"""
import os
import threading
import time

import pytest
from playwright.sync_api import Page, expect
from werkzeug.serving import make_server

from app.utils import ratelimit

PORT = 5002
BASE_URL = f"http://127.0.0.1:{PORT}"
ADMIN_ID, ADMIN_PW = 'auditor', 'Audit1234!'
PAYLOAD = '<img src=x onerror="window.__xss=1">'


@pytest.fixture(scope="module", autouse=True)
def live_server(tmp_path_factory):
    from backend_app import app as flask_app
    import backend_app
    import config

    sandbox = tmp_path_factory.mktemp('audit-e2e')
    names = ('DB_FILE', 'UPLOAD_FOLDER', 'BACKUP_DIR', 'JSON_DIR', 'DATA_FILE')
    original = {n: getattr(config, n) for n in names}
    config.DB_FILE = str(sandbox / 'journal.db')
    config.UPLOAD_FOLDER = str(sandbox / 'uploads')
    config.BACKUP_DIR = str(sandbox / 'backup')
    config.JSON_DIR = str(sandbox / 'json')
    config.DATA_FILE = str(sandbox / 'legacy.json')
    for p in (config.UPLOAD_FOLDER, config.BACKUP_DIR, config.JSON_DIR):
        os.makedirs(p, exist_ok=True)
    with flask_app.app_context():
        backend_app.init_db()
    ratelimit.reset_all()
    flask_app.test_client().post('/signup', data={
        'username': ADMIN_ID, 'password': ADMIN_PW, 'password_confirm': ADMIN_PW})

    server = make_server('127.0.0.1', PORT, flask_app, threaded=True)
    ctx = flask_app.app_context()
    ctx.push()
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield
    server.shutdown()
    ctx.pop()
    for n, v in original.items():
        setattr(config, n, v)


def _login(page: Page):
    page.goto(BASE_URL + '/login')
    page.fill('input[name="username"]', ADMIN_ID)
    page.fill('input[name="password"]', ADMIN_PW)
    page.click('button[type="submit"]')
    page.wait_for_selector('#btnDataManagement', timeout=30000)
    # 버튼은 정적 HTML 에 있어 스크립트(defer)보다 먼저 보인다. 전역 함수가 서기를 기다린다.
    page.wait_for_function("() => typeof sanitizeHtml === 'function'", timeout=30000)


# AUDIT-11
def test_bot_supplied_stock_name_is_not_executed(page: Page):
    import trading_api
    raw = trading_api.create_api_key(ADMIN_ID)['api_key']
    from backend_app import app as flask_app
    c = flask_app.test_client()
    token = c.post('/api/v1/auth/token', headers={'X-API-KEY': raw}).get_json()['access_token']
    res = c.post('/api/v1/trades', headers={'Authorization': f'Bearer {token}'}, json={
        'symbol': '005930', 'side': 'BUY', 'price': 1, 'volume': 1,
        'executedAt': '2026-09-01T10:00:00+09:00', 'brokerExecutionId': 'audit-xss-1',
        'name': PAYLOAD, 'memo': PAYLOAD})
    assert res.status_code in (200, 201)

    _login(page)
    # 기록 목록 탭을 그리게 한다 (함수가 전역이다)
    page.evaluate("() => { try { displayEntries(true); } catch (e) {} }")
    time.sleep(1.5)
    assert page.evaluate("() => window.__xss") is None
    # 막는 방식이 '지우기'가 아니라 '글자로 보여 주기'여야 한다 — 종목명이 그대로 읽혀야 한다.
    assert PAYLOAD in page.locator('#historyList').inner_text()


# AUDIT-12
def test_news_title_is_not_executed(page: Page, monkeypatch):
    from app.services import news
    now = time.strftime('%a, %d %b %Y %H:%M:%S +0900')
    monkeypatch.setattr(news, 'fetch_many', lambda stocks, force_refresh=False: [
        {'stock': 'x', 'title': PAYLOAD, 'link': 'https://example.com', 'pubDate': now}])

    _login(page)
    # 뉴스는 데이터 로딩 직후 자동으로 불린다. 새로고침 버튼으로 한 번 더 확실히 그린다.
    page.evaluate("() => document.getElementById('btnRefreshNews')?.click()")
    time.sleep(1.5)
    assert page.evaluate("() => window.__xss") is None
    assert PAYLOAD in page.locator('#newsList').inner_text()


def test_sanitize_html_keeps_formatting_and_drops_handlers(page: Page):
    """정화는 편집기 서식(굵게·정렬·첨부 이미지)을 살리고 실행 가능한 것만 걷어낸다."""
    _login(page)
    out = page.evaluate("""() => sanitizeHtml(
        '<p class="ql-align-center"><strong>b</strong>'
        + '<img src="/uploads/a/x.png" onerror="window.__xss=1">'
        + '<a href="javascript:window.__xss=1">l</a><script>window.__xss=1</script></p>')""")
    assert '<strong>b</strong>' in out and 'ql-align-center' in out and '/uploads/a/x.png' in out
    assert 'onerror' not in out and 'javascript:' not in out and '<script' not in out


# ---------------------------------------------------------------------------
# 기능 보완 — '검토 필요' 표시와 매수 삭제 확인 창
# ---------------------------------------------------------------------------

def _insert(**cols):
    import backend_app
    cols.setdefault('type', 'trade')
    cols.setdefault('username', ADMIN_ID)
    keys = ', '.join(cols)
    with backend_app.db_conn() as conn:
        conn.execute(f"INSERT INTO entries ({keys}) VALUES ({', '.join('?' * len(cols))})",
                     tuple(cols.values()))
        conn.commit()


def _entry(entry_id):
    import backend_app
    with backend_app.db_conn() as conn:
        row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        return dict(row) if row else None


def test_needs_review_badge_filter_and_resolve(page: Page):
    _insert(id=110, tradeType='매수', stockName='리뷰종목', stockCode='111110', quantity=10,
            price=1, rawDate='2026-01-01T09:00', date='2026-01-01')
    _insert(id=111, tradeType='매도', stockName='리뷰종목', stockCode='111110', quantity=3,
            price=1, rawDate='2026-01-02T09:00', date='2026-01-02',
            needsReview=1, reviewReason='테스트 사유입니다')

    _login(page)
    page.evaluate("() => displayEntries(true)")
    bar = page.locator('#reviewNoticeBar')
    expect(bar).to_be_visible()
    expect(bar).to_contain_text('1건')

    bar.locator('.review-notice-btn').click()
    expect(page.locator('#historyList .entry-card')).to_have_count(1)
    note = page.locator('#historyList .review-note')
    expect(note).to_contain_text('테스트 사유입니다')

    note.locator('.btn-review-done').click()
    expect(page.locator('#historyList .review-note')).to_have_count(0)
    expect(bar).to_be_hidden()
    assert _entry(111)['needsReview'] == 0


def test_deleting_buy_under_sold_quantity_confirms_then_flags(page: Page):
    _insert(id=120, tradeType='매수', stockName='확인종목', stockCode='222220', quantity=10,
            price=1, rawDate='2026-02-01T09:00', date='2026-02-01')
    _insert(id=121, tradeType='매도', stockName='확인종목', stockCode='222220', quantity=5,
            price=1, rawDate='2026-02-02T09:00', date='2026-02-02')

    _login(page)
    page.evaluate("() => { deleteEntry(120); }")
    ok = page.locator('#btnCustomModalOk')
    expect(page.locator('#customModalMessage')).to_contain_text('삭제하시겠습니까')
    ok.click()
    expect(page.locator('#customModalTitle')).to_have_text('보유 수량 초과')
    expect(page.locator('#customModalMessage')).to_contain_text('5주 초과')
    ok.click()

    page.wait_for_function("() => !cloudEntries.some(e => e.id === 120)", timeout=10000)
    assert _entry(120) is None
    assert _entry(121)['needsReview'] == 1
