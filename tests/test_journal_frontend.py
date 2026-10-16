"""첫 화면의 제한된 전송, 상세 지연 조회, 페이지·필터 경쟁을 실제 브라우저로 본다."""

import json

import pytest
from playwright.sync_api import expect

from app.database.db import db_conn
from app.utils import statscache
from test_editor_table import USER_ID, _login, live_server as live_server


@pytest.fixture(autouse=True)
def journal_data(live_server, page):
    with db_conn() as conn:
        conn.execute('DELETE FROM entries WHERE username = ?', (USER_ID,))
        conn.execute('UPDATE users SET preferences = NULL, account_mappings = NULL WHERE username = ?', (USER_ID,))
        body = '<p>' + '긴 본문 ' * 500 + ' 오래된검색어</p><table><tr><td>표 원문</td></tr></table>'
        conn.executemany(
            "INSERT INTO entries (id, username, type, title, thoughts, rawDate) VALUES (?, ?, 'memo', ?, ?, ?)",
            [(10000 + i, USER_ID, f'기록 {i}', body if i == 1 else f'<p>본문 {i}</p>',
              f'2026-10-{1 + i // 24:02d}T{i % 24:02d}:00') for i in range(1, 62)])
        conn.commit()
    statscache.invalidate(USER_ID)
    # 시세·뉴스의 외부 연결 지연은 기록 읽기 검증과 관계없다.
    page.route('**/api/news', lambda route: route.fulfill(json=[]))
    page.route('**/api/current_price', lambda route: route.fulfill(json={}))


def ready(page):
    _login(page)
    expect(page.locator('#historyList .entry-card')).to_have_count(15)


def test_first_page_is_small_and_search_opens_old_body_on_demand(page):
    requests = []
    page.on('request', lambda req: requests.append(req.url))
    ready(page)
    assert not any('/api/data' in url or '/api/entry/' in url for url in requests)
    assert page.evaluate('() => cloudEntries.length') == 15
    assert page.evaluate('() => cloudEntries.every(e => e.thoughts === undefined)')

    page.locator('#filterStock').fill('오래된검색어')
    page.locator('#filterStock').press('Enter')
    card = page.locator('#historyList .entry-card')
    expect(card).to_have_count(1)
    expect(card).to_contain_text('기록 1')
    expect(card.locator('table')).to_have_count(0)
    card.locator('.btn-entry-detail').click()
    expect(card.locator('table')).to_contain_text('표 원문')
    assert any('/api/entry/10001' in url for url in requests)
    card.locator('.btn-entry-detail').click()
    expect(card.locator('table')).to_have_count(0)
    card.locator('.btn-edit').click()
    expect(page.locator('#editor-container .ql-editor table')).to_contain_text('표 원문')


def test_cursor_pages_are_unique_and_all_records_can_be_read(page):
    ready(page)
    # 실제 화면의 감시자가 다음 페이지를 요청하는 경로를 같은 함수로 태운다.
    page.evaluate('''async () => {
        while (historyCursor) await loadNextHistoryPage();
    }''')
    expect(page.locator('#historyList .entry-card')).to_have_count(61)
    ids = page.evaluate('() => cloudEntries.map(e => e.id)')
    assert len(set(ids)) == 61
    assert ids[0] == 10061 and ids[-1] == 10001


def test_older_filter_response_cannot_replace_newer_filter(page):
    ready(page)
    result = page.evaluate('''async () => {
        const original = fetchWithTimeout;
        window.fetchWithTimeout = async (...args) => {
            const result = await original(...args);
            if (String(args[0]).includes(encodeURIComponent('기록 61'))) {
                await new Promise(resolve => setTimeout(resolve, 200));
            }
            return result;
        };
        currentFilterKeywords = ['기록 61'];
        const older = displayEntries(true);
        currentFilterKeywords = ['기록 60'];
        const newer = displayEntries(true);
        await Promise.all([older, newer]);
        window.fetchWithTimeout = original;
        return cloudEntries.map(e => e.id);
    }''')
    assert result == [10060]
    expect(page.locator('#historyList .entry-card')).to_contain_text('기록 60')


def test_changed_cursor_reloads_summary_and_first_page(page):
    ready(page)
    page.evaluate('''async () => {
        await fetch('/api/entry', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({type:'memo', title:'다른 기기의 새 기록', thoughts:'내용', rawDate:'2026-10-10T09:00'})
        });
        await loadNextHistoryPage();
    }''')
    expect(page.locator('#historyList .entry-card').first).to_contain_text('다른 기기의 새 기록')
    assert page.evaluate('() => journalMeta.total') == 62
    assert page.evaluate('() => cloudEntries.length') == 15


def test_failed_page_can_retry_without_losing_existing_records(page):
    ready(page)
    failed = []

    def fail_once(route):
        if 'cursor=' in route.request.url and not failed:
            failed.append(True)
            route.fulfill(status=503, json={'error': '일시적인 조회 실패'})
        else:
            route.continue_()

    page.route('**/api/journal/entries?*', fail_once)
    page.evaluate('() => loadNextHistoryPage()')
    expect(page.locator('#historyList .entry-card')).to_have_count(15)
    notice = page.locator('#historyList-load-error')
    expect(notice).to_contain_text('일시적인 조회 실패')
    notice.locator('button').click()
    expect(page.locator('#historyList .entry-card')).to_have_count(30)
    expect(notice).to_have_count(0)


def test_summary_and_chart_use_buy_that_is_not_in_first_page(page):
    with db_conn() as conn:
        conn.executemany(
            "INSERT INTO entries (id, username, type, stockName, stockCode, tradeType, quantity, price, rawDate) "
            "VALUES (?, ?, 'trade', '과거매수종목', 'OLD', ?, ?, ?, ?)", [
                (1, USER_ID, '매수', 10, 100, '2024-01-01T09:00'),
                (2, USER_ID, '매도', 4, 150, '2026-10-01T09:00')])
        conn.commit()
    statscache.invalidate(USER_ID)
    ready(page)
    assert page.evaluate('() => cloudEntries.every(e => e.id > 2)')
    card = page.locator('#portfolioGrid .portfolio-card[data-id="OLD"]')
    expect(card).to_contain_text('6주')
    page.evaluate('''async () => {
        currentDate = new Date(2026, 9, 1);
        await renderCalendar();
        currentChartStock = '과거매수종목';
        await window.renderMonthlyProfitChart();
        await window.loadTradeStats();
    }''')
    chart = page.evaluate("() => window.monthlyProfitChartInstance.data.datasets.find(d => d.label === '매매 실현손익').data")
    assert 200 in chart
    stats = page.locator('#inlineStatsContainer')
    expect(stats).to_contain_text('200')
    expect(page.locator('#calendarGrid')).to_contain_text('+200')


def test_export_reads_all_records_only_after_user_requests_it(page):
    ready(page)
    page.evaluate('''() => {
        window.ExcelJS = {Workbook: class {
            constructor() { this.rows = []; this.xlsx = {writeBuffer: async () => {
                window.exportedRows = this.rows; return new Uint8Array();
            }}; }
            addWorksheet() {
                return { columns: [], getRow: () => ({}), addRow: row => {
                    this.rows.push(row); return {getCell: () => ({})};
                }};
            }
        }};
    }''')
    page.dispatch_event('#btnModalExportExcel', 'click')
    page.locator('#btnCustomModalOk').click()
    page.wait_for_function('() => window.exportedRows !== undefined')
    rows = page.evaluate('() => window.exportedRows')
    assert len(rows) == 62  # 머리글 + 전체 61건 (첫 페이지 15건에 제한되지 않는다)
    assert '오래된검색어' in json.dumps(rows, ensure_ascii=False)
