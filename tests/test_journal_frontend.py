"""첫 화면의 제한된 전송, 상세 지연 조회, 페이지·필터 경쟁을 실제 브라우저로 본다."""

import json

import pytest
from playwright.sync_api import expect

from app.database.db import db_conn
from app.services import accounts, entries
from app.utils import statscache
from test_editor_table import USER_ID, _login, live_server as live_server
from helpers import _buy, _sell


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


def test_currency_switches_automatically_without_main_screen_control(page):
    entries.create(USER_ID, _buy(id=2, stock='Apple', stockCode='AAPL', qty=2, price=12.5,
                                currency='USD', rawDate='2026-10-01T10:00', subAccount='1234',
                                brokerAccount='243', accountName='미국계좌', tradeClass='장기투자',
                                brokerExecutionId='usd-exec', source='broker', exchange='NASDAQ'))
    with db_conn() as conn:
        accounts.save(conn, USER_ID, {'brokers': {'243': '한국투자증권'},
                                    'accounts': {'1234': {'alias': '미국계좌', 'broker': '243'}}})
        conn.commit()
    ready(page)
    # 통화 선택 UI 없이 외화만 있는 기록은 해당 통화로 표시한다.
    expect(page.locator('#displayCurrency')).to_have_count(0)
    card = page.locator('#portfolioGrid .portfolio-card[data-id="AAPL"]')
    expect(card).to_contain_text('12.5 USD')
    expect(card).to_contain_text('25 USD')
    expect(page.locator('#portfolioGrid')).not_to_contain_text('원화종목')
    page.evaluate('() => window.loadTradeStats()')
    expect(page.locator('#inlineStatsContainer')).to_contain_text('25 USD')
    page.evaluate("() => showDetailsForDate('2026-10-01', 'stock_trade_Apple')")
    expect(page.locator('#historyList .entry-card')).to_have_count(1)
    page.locator('#historyList .btn-edit').click()
    expect(page.locator('#entryCurrency')).to_have_value('USD')
    page.locator('#editor-container .ql-editor').fill('메모만 수정')
    page.locator('#journalForm button[type="submit"]').click()
    expect(page.locator('#formModalOverlay')).not_to_be_visible()
    with db_conn() as conn:
        row = conn.execute('SELECT * FROM entries WHERE id=2').fetchone()
        assert row['currency'] == 'USD'
        assert row['brokerExecutionId'] == 'usd-exec'
        assert row['exchange'] == 'NASDAQ'
        assert '메모만 수정' in row['thoughts']


def test_mixed_currency_filters_keep_dashboard_chart_and_stats_independent(page):
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    entries.create(USER_ID, _buy(id=1, qty=1, price=10000, currency='KRW', subAccount='KR',
                                accountName='국내계좌', rawDate='2026-10-01T09:00'))
    usd = {'stock': 'Apple', 'stockCode': 'AAPL', 'currency': 'USD', 'subAccount': 'US', 'accountName': '미국계좌'}
    entries.create(USER_ID, _buy(id=2, qty=2, price=12.5, rawDate='2026-10-01T09:00', **usd))
    entries.create(USER_ID, _sell(id=3, qty=1, price=15, rawDate='2026-10-02T09:00', **usd))
    page.route('**/api/current_price', lambda route: route.fulfill(json={'005930': 11000, 'AAPL': 16.25}))
    ready(page)
    expect(page.locator('#centerTotalInvested')).to_have_text('10,000원')
    page.locator('#btnChartView').click()
    page.locator('#chartStockFilter').select_option('Apple')
    page.wait_for_function("() => window.monthlyProfitChartInstance?.data.datasets.some(d => d.data.includes(2.5))")
    tooltip = page.evaluate('''() => {
        const chart = window.monthlyProfitChartInstance;
        const dataset = chart.data.datasets.find(d => d.label === '매매 실현손익');
        const index = dataset.data.indexOf(2.5);
        chart.options.onClick(null, [{index}], chart);
        return chart.options.plugins.tooltip.callbacks.label({parsed: {y: 2.5}, raw: 2.5, dataset});
    }''')
    assert '2.5 USD' in tooltip
    expect(page.locator('#chartDetailList')).to_contain_text('2.5 USD')
    page.locator('#btnStats').click()
    expect(page.locator('#inlineStatsContainer')).to_contain_text('25 USD')
    expect(page.locator('#inlineStatsContainer')).to_contain_text('+2.5 USD')
    expect(page.locator('#centerTotalInvested')).to_have_text('10,000원')

    page.locator('#dashboardSubAccountFilter').select_option('미국계좌')
    card = page.locator('#portfolioGrid .portfolio-card[data-id="AAPL"]')
    expect(card).to_contain_text('12.5 USD')
    expect(card.locator('.cp-price')).to_have_text('16.25 USD')
    expect(page.locator('#centerTotalInvested')).to_have_text('12.5 USD')
    expect(page.locator('#centerTotalEvaluation')).to_have_text('16.25 USD')
    expect(page.locator('#centerTotalProfit')).to_have_text('+2.5 USD')
    page.locator('#dashboardSubAccountFilter').select_option('all')
    expect(page.locator('#centerTotalInvested')).to_have_text('10,000원')
    expect(page.locator('#inlineStatsContainer')).to_contain_text('25 USD')
    expect(page.locator('#displayCurrency')).to_have_count(0)
    assert errors == []


def test_prices_from_previous_account_cannot_overwrite_new_currency(page):
    entries.create(USER_ID, _buy(id=1, qty=1, price=10000, currency='KRW', subAccount='KR',
                                accountName='국내계좌', rawDate='2026-10-01T09:00'))
    entries.create(USER_ID, _buy(id=2, qty=2, price=12.5, currency='USD', subAccount='US',
                                stock='Apple', stockCode='AAPL', accountName='미국계좌', rawDate='2026-10-01T09:00'))
    ready(page)
    page.evaluate('''async () => {
        const original = fetchWithTimeout;
        let release;
        const gate = new Promise(resolve => { release = resolve; });
        window.fetchWithTimeout = async (url, ...args) => {
            if (url !== '/api/current_price') return original(url, ...args);
            const {codes} = JSON.parse(args[0].body);
            if (codes.includes('005930')) {
                await gate;
                return new Response(JSON.stringify({'005930': 11000}));
            }
            return new Response(JSON.stringify({AAPL: 16.25}));
        };
        try {
            const older = window.fetchCurrentPricesAndUpdateUI();
            currentDashboardSubAccount = '미국계좌';
            await updatePortfolioSummary();
            await window.fetchCurrentPricesAndUpdateUI();
            release();
            await older;
        } finally {
            release();
            window.fetchWithTimeout = original;
        }
    }''')
    expect(page.locator('#centerTotalEvaluation')).to_have_text('32.5 USD')
    expect(page.locator('#portfolioGrid .cp-price')).to_have_text('16.25 USD')


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


def _entries_requests(page):
    return page.evaluate('''() => performance.getEntriesByType('resource')
        .filter(r => r.name.includes('/api/journal/entries'))
        .map(r => ({ start: r.startTime, url: r.name.replace(location.origin, '') }))''')


def _initial_load_done(page):
    # 첫 화면의 조회 주소는 포트폴리오·달력까지 받은 뒤에 저장한다.
    page.wait_for_function('() => initialViewUrls === null')


def test_reload_prefetches_last_first_screen_views_once(page):
    ready(page)
    _initial_load_done(page)
    saved = page.evaluate("() => JSON.parse(localStorage.getItem('journalInitialViews'))")
    assert {url.split('?')[0] for url in saved} >= {
        '/api/journal/entries', '/api/journal/portfolio', '/api/journal/calendar'}

    page.reload()
    expect(page.locator('#historyList .entry-card')).to_have_count(15)
    dcl = page.evaluate("() => performance.getEntriesByType('navigation')[0].domContentLoadedEventStart")
    requests = _entries_requests(page)
    # 스크립트가 실행되기 전에 head 에서 한 번만 요청하고, 화면은 그 응답을 이어받는다.
    assert len(requests) == 1 and requests[0]['start'] < dcl, requests
    assert page.evaluate('''() => Object.keys(window.__initialFetches)
        .filter(k => k.startsWith('view:')).every(k => window.__initialFetches[k] === null)''')


def test_mismatched_saved_view_is_ignored(page):
    ready(page)
    _initial_load_done(page)
    page.evaluate('''() => localStorage.setItem('journalInitialViews', JSON.stringify(
        ['/api/journal/entries?type=memo&limit=1', 'https://example.com/x']))''')
    # 요청을 보낸 순서로 모은다 (Resource Timing 은 응답이 끝나야 항목이 생긴다).
    urls = []
    page.on('request', lambda req: urls.append(req.url))
    page.reload()
    expect(page.locator('#historyList .entry-card')).to_have_count(15)
    entries = [u for u in urls if '/api/journal/entries' in u]
    assert entries[0].endswith('/api/journal/entries?type=memo&limit=1')
    assert len(entries) == 2 and 'limit=15' in entries[1], entries
    assert not any('example.com' in u for u in urls)


def test_auto_price_refresh_waits_while_tab_is_hidden(page):
    ready(page)
    result = page.evaluate('''() => {
        let hidden = true;
        Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
        Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => hidden ? 'hidden' : 'visible' });
        let statusChecks = 0;
        const original = window.getMarketStatus;
        window.getMarketStatus = () => { statusChecks++; return original(); };
        window.fetchCurrentPricesAndUpdateUI(true);
        const whileHidden = statusChecks;
        const calls = [];
        const refresh = window.fetchCurrentPricesAndUpdateUI;
        window.fetchCurrentPricesAndUpdateUI = (isAuto) => { calls.push(isAuto); };
        hidden = false;
        document.dispatchEvent(new Event('visibilitychange'));
        document.dispatchEvent(new Event('visibilitychange'));  // 한 번만 따라잡는다
        window.fetchCurrentPricesAndUpdateUI = refresh;
        return { whileHidden, calls };
    }''')
    assert result == {'whileHidden': 0, 'calls': [True]}


def test_canceled_order_card_shows_it_is_not_counted(page):
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO entries (id, username, type, stockName, stockCode, tradeType, price, quantity, rawDate,"
            " tradeStatus) VALUES (20000, ?, 'trade', '취소종목', '000001', '매수', 100, 1, '2026-12-01T09:00',"
            " 'CANCELED')", (USER_ID,))
        conn.commit()
    statscache.invalidate(USER_ID)
    _login(page)
    card = page.locator('#historyList .entry-card[data-entry-id="20000"]')
    expect(card).to_contain_text('취소된 주문')
    # 보유 수량에 들어가지 않으므로 포트폴리오 카드가 생기지 않는다(포트폴리오 응답까지 기다린 뒤 본다).
    _initial_load_done(page)
    expect(page.locator('#portfolioGrid')).not_to_contain_text('취소종목')


def test_stats_panel_shows_grouped_metrics_and_equity_curve(page):
    with db_conn() as conn:
        conn.executemany(
            "INSERT INTO entries (id, username, type, stockName, stockCode, tradeType, quantity, price, rawDate, fee, tax) "
            "VALUES (?, ?, 'trade', '분석종목', 'STAT', ?, ?, ?, ?, ?, ?)", [
                (1, USER_ID, '매수', 10, 100, '2026-01-02T09:00', 15, None),
                (2, USER_ID, '매도', 5, 150, '2026-01-10T09:00', 8, 20),    # +250, 8일 보유
                (3, USER_ID, '매도', 5, 80, '2026-03-02T09:00', 4, 10)])    # -100, 59일 보유
        conn.commit()
    statscache.invalidate(USER_ID)
    ready(page)
    page.locator('#btnChartView').click()
    page.locator('#btnStats').click()
    stats = page.locator('#inlineStatsContainer')
    expect(stats.get_by_text('수수료·세금', exact=True)).to_have_count(0)
    for text in ('💰 손익', '🎯 매매 효율', '⚠️ 위험', '⏱️ 보유 습관', '⏳ 보유기간별 성과',
                 '+93원', '손익비 (평균수익÷평균손실)', '2.50', '1연승 / 1연패',
                 '최대 낙폭 (MDD)', '-100원', '+15.0%', '51.0일 더 오래'):
        expect(stats).to_contain_text(text)
    page.wait_for_function('() => window.statsEquityChartInstance?.data.datasets.length === 2')
    curve = page.evaluate('() => window.statsEquityChartInstance.data.datasets[0].data')
    assert curve == [250, 150]


def test_historical_evaluation_is_separate_from_current_price_chart(page):
    from test_valuation import cache
    entries.create(USER_ID, _buy(id=1, qty=2, price=100, rawDate='2026-01-05T09:00'))
    entries.create(USER_ID, _sell(id=2, qty=1, price=130, rawDate='2026-01-07T09:00'))
    entries.create(USER_ID, _sell(id=3, qty=1, price=120, rawDate='2026-02-05T09:00'))
    cache({'2026-01-05': 110, '2026-01-06': None, '2026-01-07': 140, '2026-01-30': 150})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    ready(page)
    page.locator('#btnChartView').click()
    page.locator('#btnStats').click()
    _select_analysis_period(page, '2026-01-05', '2026-01-07')
    page.locator('[data-stats-curve="evaluated"]').click()
    expect(page.locator('#statsCurveStatus')).to_contain_text('시세 미확인 1일')
    page.wait_for_function('() => window.statsEquityChartInstance?.data.datasets[0].label === "평가손익"')
    assert page.evaluate('() => window.statsEquityChartInstance.data.datasets[0].data') == [20, None, 40]
    # 빈 종가를 0으로 채우거나 앞뒤 점을 연결하지 않는다.
    assert page.evaluate('() => window.statsEquityChartInstance.data.datasets[0].spanGaps') is False
    # 선으로 연결할 수 없는 양쪽의 유효한 값도 실제 캔버스 점으로 표시한다.
    points = page.evaluate('''() => window.statsEquityChartInstance.getDatasetMeta(0).data.map(
        point => ({skip: point.skip, radius: point.options.radius}))''')
    assert not points[0]['skip'] and points[0]['radius'] > 0
    assert points[1]['skip']
    assert not points[2]['skip'] and points[2]['radius'] > 0
    page.locator('[data-stats-curve="realized"]').click()
    assert page.evaluate('() => window.statsEquityChartInstance.data.datasets[0].data') == [30]
    page.locator('[data-stats-curve="evaluated"]').click()
    _select_analysis_period(page, '2026-01-30', '2026-01-30')  # 매매가 없는 날에도 보유분 평가
    page.wait_for_function('() => window.statsEquityChartInstance?.data.datasets[0].data[0] === 50')
    page.set_viewport_size({'width': 390, 'height': 844})
    page.wait_for_function('''() => {
        const el = document.getElementById('monthlyChartSection');
        return el.scrollWidth <= el.clientWidth + 1;
    }''')
    evaluation_requests = []
    page.on('request', lambda request: evaluation_requests.append(request.url)
            if '/stats/evaluation' in request.url else None)
    page.locator('.chart-type-btn[data-type="evaluated"]').click()
    page.wait_for_function('''() => window.monthlyProfitChartInstance?.data.datasets[0].label
        === '해당 월 매수분의 현재 평가 손익' ''')
    expect(page.locator('#chartEvalNotice')).to_contain_text('(현재가 − 실제 매수단가) × 지금 남아 있는 수량')
    # 과거 분석에서는 50원이지만 현재는 전량 청산했으므로 매수분 막대가 없다.
    assert page.evaluate('() => window.monthlyProfitChartInstance.data.datasets[0].data.every(v => v === null)')
    assert not evaluation_requests
    expect(page.locator('#chartEvalRetry')).to_have_count(0)
    assert not errors


def test_monthly_and_weekly_evaluation_use_current_quotes_and_fifo_lots(page):
    from datetime import datetime, timezone
    page.clock.set_fixed_time(datetime(2026, 10, 24, 3, tzinfo=timezone.utc))
    entries.create(USER_ID, _buy(id=1, qty=5, price=100, rawDate='2026-01-15T09:00'))
    entries.create(USER_ID, _buy(id=2, qty=5, price=200, rawDate='2026-02-16T09:00'))
    entries.create(USER_ID, _sell(id=3, qty=4, price=300, rawDate='2026-03-02T09:00'))
    entries.create(USER_ID, _buy(id=4, stock='KRX 금현물', stockCode='KRXGOLD', qty=1,
                                price=50, rawDate='2026-01-15T10:00'))
    page.route('**/api/current_price', lambda route: route.fulfill(json={'005930': 250, 'KRXGOLD': 80}))
    evaluation_requests = []
    page.on('request', lambda request: evaluation_requests.append(request.url)
            if '/stats/evaluation' in request.url else None)
    ready(page)
    page.evaluate("() => { window.currentPriceCache = {'005930': 250, 'KRXGOLD': 80}; }")
    page.locator('#btnChartView').click()
    page.locator('.chart-type-btn[data-type="evaluated"]').click()
    page.wait_for_function('''() => {
        const dataset = window.monthlyProfitChartInstance?.data.datasets[0];
        return dataset?.label === '해당 월 매수분의 현재 평가 손익' && dataset.data.includes(180);
    }''')
    assert page.evaluate('() => window.monthlyProfitChartInstance.data.datasets[0].data.filter(v => v !== null)') == [180, 250]
    expect(page.locator('#chartEvalNotice')).to_contain_text('먼저 산 물량부터 판 것으로 계산')
    example = page.locator('#chartEvalNotice details')
    expect(example).not_to_have_attribute('open', '')
    example.locator('summary').click()
    expect(example.get_by_text('−20,000원', exact=True)).to_be_visible()
    expect(example).to_contain_text('2월 매수분은 20주, 3월 매수분은 100주')
    page.evaluate('''() => {
        const chart = window.monthlyProfitChartInstance;
        chart.options.onClick({}, [{index: chart.data.datasets[0].data.indexOf(180), datasetIndex: 0}], chart);
    }''')
    expect(page.locator('#chartDetailList')).to_contain_text('매수분 평가 손익')
    expect(page.locator('#chartDetailList')).to_contain_text('KRX 금현물')
    expect(page.locator('#chartDetailList')).to_contain_text('+30원')
    page.evaluate('''() => {
        window.currentChartGranularity = 'weekly';
        window.chartWeekOffset = 30;
        return window.renderMonthlyProfitChart();
    }''')
    expect(page.locator('#chartEvalNotice')).to_contain_text('해당 주에 산 물량 중 지금도 보유 중인 물량')
    expect(page.locator('#chartEvalNotice details')).to_contain_text('첫째 주 매수분은 20주, 둘째 주 매수분은 100주')
    assert page.evaluate('() => window.monthlyProfitChartInstance.data.datasets[0].data.filter(v => v !== null)') == [180, 250]
    assert not evaluation_requests


def test_historical_evaluation_polls_and_discards_late_response(page):
    _analysis_trades()
    ready(page)
    page.locator('#btnChartView').click()
    page.locator('#btnStats').click()
    page.wait_for_function('() => window.statsEquityChartInstance?.data.datasets[0].data.length === 2')
    page.evaluate('''() => {
        window.originalEvaluationFetch = fetchWithTimeout;
        window.fetchWithTimeout = (url, ...args) => {
            if (!url.includes('/stats/evaluation')) return originalEvaluationFetch(url, ...args);
            return new Promise(resolve => { window.releaseEvaluation = () => resolve(new Response('{}')); });
        };
    }''')
    page.locator('[data-stats-curve="evaluated"]').click()
    page.wait_for_function('() => typeof window.releaseEvaluation === "function"')
    page.locator('[data-stats-curve="realized"]').click()
    page.evaluate('''async () => {
        window.releaseEvaluation();
        window.fetchWithTimeout = window.originalEvaluationFetch;
        await new Promise(resolve => requestAnimationFrame(resolve));
    }''')
    assert page.evaluate('() => window.statsEquityChartInstance.data.datasets[0].data') == [40, -20]
    calls = []
    def respond(route):
        calls.append(route.request.method)
        pending = len(calls) == 1
        route.fulfill(json={
            'curve': [{'date': '2026-01-05', 'value': None if pending else 22}],
            'currency': 'KRW', 'period': {'from': '2026-01-05', 'to': '2026-01-05'},
            'progress': {'ready': 0 if pending else 1, 'total': 1, 'pending': int(pending), 'failed': 0},
            'pending': pending, 'missingDays': int(pending), 'missingStocks': [], 'notes': [], 'hasHoldings': True})
    page.route('**/api/journal/stats/evaluation?*', respond)
    page.locator('[data-stats-curve="evaluated"]').click()
    expect(page.locator('#statsCurveStatus')).to_contain_text('시세 수집 중')
    page.wait_for_function('() => window.statsEquityChartInstance?.data.datasets[0].data[0] === 22')
    assert calls == ['POST', 'GET']


def test_historical_evaluation_refreshes_changed_summary(page):
    from test_valuation import cache
    entries.create(USER_ID, _buy(id=1, qty=2, price=100, rawDate='2026-01-05T09:00'))
    cache({'2026-01-05': 110})
    ready(page)
    page.locator('#btnChartView').click()
    page.locator('#btnStats').click()
    _select_analysis_period(page, '2026-01-05', '2026-01-05')
    entries.create(USER_ID, _buy(id=2, qty=1, price=100, rawDate='2026-01-05T10:00'))
    page.locator('[data-stats-curve="evaluated"]').click()
    expect(page.locator('#statsCurveStatus')).to_contain_text('기록이 변경되었습니다')
    page.locator('#statsCurveRetry').click()
    page.wait_for_function('() => window.statsEquityChartInstance?.data.datasets[0].data[0] === 30')


def test_realized_chart_uses_saved_pnl_and_shows_provenance(page):
    entries.create(USER_ID, _buy(id=1, qty=10, price=100, rawDate='2026-01-05T09:00'))
    entries.create(USER_ID, _sell(id=2, qty=2, price=150, realizedPnl=-35, rawDate='2026-01-06T09:00'))
    entries.create(USER_ID, _sell(id=3, qty=1, price=120, rawDate='2026-01-07T09:00'))
    ready(page)
    page.locator('#btnChartView').click()
    page.locator('#btnStats').click()
    page.wait_for_function('() => window.statsEquityChartInstance?.data.datasets[0].data.length === 2')
    assert page.evaluate('() => window.statsEquityChartInstance.data.datasets[0].data') == [-35, -15]
    stats = page.locator('#inlineStatsContainer')
    expect(stats).to_contain_text('매도 거래에 저장된 금액을 합산')
    stats.locator('[data-stats-title="실현 손익"]').click()
    details = page.locator('#statsTradeDetails')
    expect(details).to_contain_text('저장 손익')
    expect(details).to_contain_text('체결 이력 재구성')
    expect(details).to_contain_text('-35원')
    expect(details).to_contain_text('+20원')
    page.locator('.chart-type-btn[data-type="evaluated"]').click()
    expect(page.locator('#chartEvalNotice')).to_contain_text('매도 거래에 저장된 손익')
    expect(page.locator('#chartEvalNotice')).not_to_contain_text('실현손익은 이동평균단가')


def _analysis_trades():
    entries.create(USER_ID, _buy(id=1, stock='분석종목', stockCode='STAT', qty=10, price=100,
                                rawDate='2026-01-01T09:00', tradeClass='장기투자', accountName='일반계좌'))
    entries.create(USER_ID, _sell(id=2, stock='분석종목', stockCode='STAT', qty=2, price=120,
                                 rawDate='2026-02-10T09:00', tradeClass='단기스윙', accountName='일반계좌'))
    entries.create(USER_ID, _sell(id=3, stock='분석종목', stockCode='STAT', qty=3, price=80,
                                 rawDate='2026-03-10T09:00', tradeClass='단기스윙', accountName='일반계좌',
                                 thoughts='<p>손절 판단을 다시 확인</p>'))


def _select_analysis_period(page, start, end):
    page.locator('#statsPeriodPreset').select_option('custom')
    page.locator('#statsFrom').fill(start)
    page.locator('#statsTo').fill(end)
    page.locator('#statsPeriodForm button[type="submit"]').click()
    expect(page.locator('.stats-caption')).to_contain_text(f'{start} ~ {end}')


def test_analysis_period_comparison_and_trade_note_are_connected(page):
    _analysis_trades()
    requested = []
    page.on('request', lambda request: requested.append(request.url))
    ready(page)
    page.locator('#btnChartView').click()
    page.locator('#btnStats').click()
    _select_analysis_period(page, '2026-03-01', '2026-03-31')
    stats = page.locator('#inlineStatsContainer')
    expect(stats).to_contain_text('2026-01-29 ~ 2026-02-28')
    expect(stats).to_contain_text('-60원')
    expect(stats.locator('details').first).to_contain_text('40원')
    assert not any('/api/entry/' in url or '/stats/trades' in url for url in requested)
    stats.locator('[data-stats-title="최대 단일 손실"]').click()
    details = page.locator('#statsTradeDetails')
    expect(details).to_contain_text('최대 단일 손실 · 1건')
    expect(details).to_contain_text('300원')  # 기간 밖 매수 원가
    expect(details).not_to_contain_text('손절 판단')
    details.get_by_role('button', name='메모 보기').click()
    expect(details).to_contain_text('손절 판단을 다시 확인')
    assert any('/api/entry/3' in url for url in requested)
    page.locator('#statsQuality summary').click()
    expect(page.locator('#statsQuality')).to_contain_text('모두 미입력: 1건')
    page.locator('#statsRecovery summary').click()
    expect(page.locator('#statsRecovery')).to_contain_text('미회복')
    page.locator('[data-stats-close]').click()
    expect(details).not_to_be_visible()
    page.locator('#statsPeriodPreset').select_option('all')
    expect(page.locator('.stats-caption')).to_contain_text('전체 기간')
    expect(stats).not_to_contain_text('직전 기간과 비교')
    page.locator('.chart-type-btn[data-type="realized"]').click()
    expect(page.locator('#statsPeriodForm')).not_to_be_visible()


def test_analysis_groups_open_foreign_currency_and_escape_stock_names(page):
    _analysis_trades()
    stock = 'Apple "A&B" <종목>'
    entries.create(USER_ID, _buy(id=4, stock=stock, stockCode='AAPL', qty=2, price=12.5,
                                currency='USD', subAccount='US', accountName='미국계좌', rawDate='2026-03-01T09:00'))
    entries.create(USER_ID, _sell(id=5, stock=stock, stockCode='AAPL', qty=1, price=15,
                                 currency='USD', subAccount='US', accountName='미국계좌', rawDate='2026-03-02T09:00'))
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    ready(page)
    page.locator('#btnChartView').click()
    page.locator('#btnStats').click()
    page.locator('#statsGroups summary').click()
    row = page.locator('#statsGroups tr').filter(has_text='미국계좌')
    expect(row).to_contain_text('+2.5 USD')
    row.get_by_role('button', name='거래 보기').click()
    details = page.locator('#statsTradeDetails')
    expect(details).to_contain_text(stock)
    expect(details).to_contain_text('12.5 USD')
    page.locator('#chartStockFilter').select_option(stock)
    page.wait_for_function("() => statsLastReport?.currency === 'USD'")
    page.locator('#inlineStatsContainer [data-stats-title]').filter(has_text=stock).click()
    expect(page.locator('#statsTradeDetails')).to_contain_text(stock + ' · 1건')
    assert errors == []


def test_analysis_discards_late_period_and_detail_responses(page):
    _analysis_trades()
    ready(page)
    page.locator('#btnChartView').click()
    page.locator('#btnStats').click()
    page.wait_for_function('() => statsLastReport !== null')
    page.evaluate('''async () => {
        const original = fetchWithTimeout;
        window.fetchWithTimeout = async (...args) => {
            const response = await original(...args);
            const url = String(args[0]);
            if (url.includes('/stats/trades') || url.includes('from=2026-02')) {
                await new Promise(resolve => setTimeout(resolve, 150));
            }
            return response;
        };
        try {
            const oldDetail = openStatsTrades({metric: 'sell'}, '오래된 상세');
            statsPeriod = {from: '2026-02-01', to: '2026-02-28'};
            const older = loadTradeStats();
            statsPeriod = {from: '2026-03-01', to: '2026-03-31'};
            const newer = loadTradeStats();
            await Promise.all([oldDetail, older, newer]);
        } finally { window.fetchWithTimeout = original; }
    }''')
    expect(page.locator('.stats-caption')).to_contain_text('2026-03-01 ~ 2026-03-31')
    expect(page.locator('#statsTradeDetails')).not_to_be_visible()
    assert page.evaluate('() => statsLastReport.totalRealized') == -60


def test_analysis_empty_period_and_invalid_dates_keep_controls_available(page):
    _analysis_trades()
    ready(page)
    page.locator('#btnChartView').click()
    page.locator('#btnStats').click()
    _select_analysis_period(page, '2026-04-01', '2026-04-30')
    expect(page.locator('.stats-empty')).to_contain_text('분석할 매매 기록이 없습니다')
    expect(page.locator('#statsPeriodForm')).to_be_visible()
    expect(page.locator('#inlineStatsContainer')).to_contain_text('-60원')  # 이전 기간 비교는 유지
    page.locator('#statsFrom').fill('2026-05-01')
    page.locator('#statsPeriodForm button[type="submit"]').click()
    expect(page.locator('#statsPeriodError')).to_contain_text('올바른 순서')
    expect(page.locator('.stats-caption')).to_contain_text('2026-04-01 ~ 2026-04-30')


def test_analysis_mobile_layout_and_sections(page):
    _analysis_trades()
    page.set_viewport_size({'width': 390, 'height': 844})
    ready(page)
    page.locator('#btnChartView').click()
    page.locator('#btnStats').click()
    _select_analysis_period(page, '2026-03-01', '2026-03-31')
    page.locator('#statsDistribution summary').click()
    expect(page.locator('.stats-bin')).to_have_count(1)
    page.locator('#statsGroups summary').click()
    assert page.evaluate('''() => {
        const root = document.getElementById('monthlyChartSection');
        return root.scrollWidth <= root.clientWidth + 1;
    }''')
    page.locator('#statsPeriodForm').scroll_into_view_if_needed()
    page.set_viewport_size({'width': 1280, 'height': 1000})
    page.locator('#statsQuality summary').click()
    page.locator('#statsRecovery summary').click()
    page.locator('[data-stats-title="최대 단일 손실"]').click()
    details = page.locator('#statsTradeDetails')
    expect(details).to_contain_text('수수료 / 세금')
    details.get_by_role('button', name='메모 보기').click()
    expect(details).to_contain_text('손절 판단을 다시 확인')


def test_analysis_presets_use_kst_dates_and_clamp_short_months(page):
    ready(page)
    def bounds(now):
        return page.evaluate('''now => {
            const Original = Date;
            window.Date = class extends Original {
                constructor(...args) { super(...(args.length ? args : [now])); }
            };
            try { return ['all', 'month', 'quarter', 'year'].map(statsPresetBounds); }
            finally { window.Date = Original; }
        }''', now)
    assert bounds('2024-05-31T03:00:00Z') == [
        {'from': '', 'to': ''}, {'from': '2024-05-01', 'to': '2024-05-31'},
        {'from': '2024-03-01', 'to': '2024-05-31'}, {'from': '2024-01-01', 'to': '2024-05-31'},
    ]
    assert bounds('2024-05-31T15:30:00Z')[1] == {'from': '2024-06-01', 'to': '2024-06-01'}


def test_data_inspection_history_and_restore_preview_flow(page):
    import io
    import zipfile
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    ready(page)
    page.click('.header-action-icon')
    page.click('#btnDataManagement')
    expect(page.locator('#btnDataIntegrity, #btnDataChanges')).to_have_count(0)
    expect(page.locator('#dataManagementModalOverlay .modal-outline-btn')).to_have_count(3)
    # 메뉴 진입 버튼은 제거해도 기존 검사·이력 조회와 복원 기능은 유지한다.
    page.evaluate('showDataIntegrity()')
    expect(page.locator('#dataAuditContent')).to_contain_text('확인이 필요한 거래 0건')
    page.click('#dataAuditClose')
    page.evaluate('showDataChanges()')
    expect(page.locator('#dataAuditContent details').first).to_be_visible()
    page.locator('#dataAuditContent summary').first.click()
    expect(page.locator('#dataAuditContent table').first).to_be_visible()
    page.click('#dataAuditClose')
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as z:
        z.writestr('data.json', '[]')
    payload = {'name': 'empty.zip', 'mimeType': 'application/zip', 'buffer': stream.getvalue()}
    page.locator('#restoreFileInput').set_input_files(payload)
    expect(page.locator('#dataAuditTitle')).to_have_text('복원 미리보기')
    expect(page.locator('#dataAuditContent')).to_contain_text('현재 기록 61건 → 백업 기록 0건')
    page.locator('#dataAuditActions button', has_text='취소').click()
    with db_conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM entries WHERE username=?', (USER_ID,)).fetchone()[0] == 61
    page.locator('#restoreFileInput').set_input_files(payload)
    page.locator('#dataAuditActions button', has_text='이 내용으로 복원').click()
    expect(page.locator('#customModalMessage')).to_contain_text('성공적으로 원복')
    page.click('#btnCustomModalOk')
    expect(page.locator('#historyList .entry-card')).to_have_count(0)
    assert not errors


def test_stale_editor_preserves_draft_and_latest_database_record(page):
    ready(page)
    page.locator('#historyList .btn-edit').first.click()
    expect(page.locator('#formModalOverlay')).to_be_visible()
    entry_id = page.evaluate('editingEntryId')
    page.locator('#editor-container .ql-editor').fill('작성 중인 내용')
    with db_conn(write=True) as conn:
        conn.execute('UPDATE entries SET title=? WHERE id=?', ('다른 곳에서 정정', entry_id))
        conn.commit()
    page.locator('#journalForm button[type="submit"]').click()
    expect(page.locator('#customModalMessage')).to_contain_text('다른 곳에서 변경된 기록')
    page.click('#btnCustomModalOk')
    expect(page.locator('#formModalOverlay')).to_be_visible()
    expect(page.locator('#editor-container .ql-editor')).to_have_text('작성 중인 내용')
    with db_conn() as conn:
        assert conn.execute('SELECT title FROM entries WHERE id=?', (entry_id,)).fetchone()[0] == '다른 곳에서 정정'


def test_same_number_accounts_can_be_added_selected_and_renamed(page):
    ready(page)
    page.click('.header-action-icon')
    page.click('#btnAccountManagement')
    for broker, alias in [('264', '첫 계좌'), ('238', '둘째 계좌')]:
        page.click('#btnToggleNewAccountForm')
        page.select_option('#unifiedBrokerCode', broker)
        page.fill('#unifiedAccountCode', '12-34')
        page.fill('#unifiedAccountName', alias)
        page.click('#btnAddUnifiedMapping')
    expect(page.locator('#unifiedMappingList button', has_text='수정')).to_have_count(2)
    page.locator('#unifiedMappingList button', has_text='수정').first.click()
    page.fill('#unifiedAccountCode', '56-78')
    page.click('#btnAddUnifiedMapping')
    page.click('#btnSaveAccountMappings')
    expect(page.locator('#customModalMessage')).to_contain_text('계좌정보가 저장')
    with page.expect_navigation():
        page.click('#btnCustomModalOk')
    page.wait_for_function('typeof ensureAccountMapping === "function"')
    page.evaluate('ensureAccountMapping()')
    choices = page.locator('#journalAccountSelect option').evaluate_all('(options) => options.filter(x => !x.disabled).map(x => x.value)')
    assert len(choices) == 2
    values = []
    for choice in choices:
        page.select_option('#journalAccountSelect', choice, force=True)
        values.append(page.evaluate("[document.getElementById('brokerAccount').value, document.getElementById('subAccount').value]"))
    assert sorted(values) == [['238', '12-34'], ['264', '56-78']]
    with db_conn() as conn:
        saved = accounts.load(conn, USER_ID)['accounts']
        assert len(saved) == 2
        assert sorted(value['number'] for value in saved.values()) == ['12-34', '56-78']
