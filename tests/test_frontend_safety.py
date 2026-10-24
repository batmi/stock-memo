"""실제 화면의 요청 경합, 입력 보존, 복구와 권한 선택을 검증한다."""

import json

from playwright.sync_api import expect

from app.services import entries
from helpers import _buy
from test_editor_table import BASE_URL, USER_ID, USER_PW
from test_journal_frontend import ready, journal_data as journal_data, live_server as live_server


def test_bot_identity_is_text_in_every_attribute(page):
    ready(page)
    payload = 'bot" onmouseover="window.__xss=1'
    page.route('**/api/me', lambda route: route.fulfill(json={
        'username': USER_ID, 'bots': [
            {'botId': payload, 'label': '테스트 봇', 'state': 'stopped', 'elapsedSeconds': 1},
            {'botId': 'second', 'label': '두 번째', 'state': 'running', 'elapsedSeconds': 1}],
        'bot_state': 'stopped', 'bot_last_seen': None}))
    page.evaluate("document.getElementById('btnHtsIntegration').click()")
    expect(page.locator('#botInstanceList .bot-forget')).to_have_count(1)
    button = page.locator('#botInstanceList .bot-forget')
    assert button.get_attribute('data-bot-id') == payload
    button.hover()
    expect(page.locator('#botInstanceList [onmouseover]')).to_have_count(0)
    assert page.evaluate('window.__xss') is None
    assert page.locator('#resyncBotSelect option').first.get_attribute('value') == payload


def test_pending_alert_cannot_approve_delete_confirmation(page):
    ready(page)
    page.evaluate('''() => {
        window.modalResults = [];
        customConfirm('기록을 삭제할까요?').then(value => modalResults.push(['delete', value]));
        customAlert('본문 조회 실패').then(value => modalResults.push(['alert', value]));
    }''')
    expect(page.locator('#customModalMessage')).to_have_text('기록을 삭제할까요?')
    page.locator('#btnCustomModalCancel').click()
    expect(page.locator('#customModalMessage')).to_have_text('본문 조회 실패')
    page.locator('#btnCustomModalOk').click()
    assert page.evaluate('modalResults') == [['delete', False], ['alert', True]]


def test_stock_autofill_preserves_edited_account_and_resets_old_code(page):
    ready(page)
    values = page.evaluate('''() => {
        editingEntryId = 99;
        editingEntry = {stockName: '기존종목'};
        journalMeta.recentEntries['기존종목'] = {stockCode:'005930', subAccount:'new-account', brokerAccount:'238'};
        const name = document.getElementById('stockName');
        name.value = '기존종목';
        document.getElementById('stockCode').value = '005930';
        document.getElementById('subAccount').value = 'original-account';
        document.getElementById('brokerAccount').value = '264';
        autoFillStockInfo.call(name, {type:'blur'});
        const account = document.getElementById('subAccount').value;
        name.value = '다른신규종목';
        autoFillStockInfo.call(name, {type:'blur'});
        return {account, code:document.getElementById('stockCode').value};
    }''')
    assert values == {'account': 'original-account', 'code': ''}


def test_quote_mode_race_and_failed_refresh_keep_one_valuation(page):
    ready(page)
    page.evaluate('''async () => {
        const original = fetchAssetQuotes;
        const pending = [];
        fetchAssetQuotes = assets => new Promise(resolve => pending.push(value => resolve(assets.map(asset => ({...asset, price:value, asOf:'2026-10-01T00:00:00Z', market:currentMarketMode, stale:false, status:value == null ? 'unavailable' : 'ok'})))));
        currentPortfolioArrayForPrice = [{key:'test', stockCode:'005930', currency:'KRW', qty:10, totalCost:1000}];
        document.getElementById('portfolioGrid').innerHTML = '<span class="cp-price" data-pkey="test"></span><span class="cp-eval" data-pkey="test"></span><span class="cp-profit" data-pkey="test"></span>';
        try {
            currentMarketMode = 'AFT';
            const older = fetchCurrentPricesAndUpdateUI();
            currentMarketMode = 'KRX';
            const newer = fetchCurrentPricesAndUpdateUI();
            pending[1](300); await newer;
            pending[0](200); await older;
            const failed = fetchCurrentPricesAndUpdateUI();
            pending[2](null); await failed;
        } finally { fetchAssetQuotes = original; }
    }''')
    expect(page.locator('#portfolioGrid .cp-price')).to_have_text('300원')
    expect(page.locator('#portfolioGrid .cp-eval')).to_have_text('3,000원')
    expect(page.locator('#centerTotalEvaluation')).to_have_text('3,000원')
    expect(page.locator('#portfolioQuoteStatus')).to_contain_text('이전 시세 1종목')
    assert page.evaluate("cachedQuote({code:'005930',currency:'USD',exchange:''})") is None


def test_unavailable_quotes_never_use_invested_cost_as_market_value(page):
    ready(page)
    page.evaluate('''() => {
        currentPortfolioArrayForPrice = [{key:'missing', stockCode:'NOQUOTE', currency:'USD', qty:10, totalCost:1234}];
        renderPortfolioQuotes();
    }''')
    expect(page.locator('#centerTotalEvaluation')).to_have_text('확인 필요')
    expect(page.locator('#portfolioQuoteStatus')).to_contain_text('시세 미확인 1종목')


def test_chart_fetches_its_own_foreign_quote_outside_dashboard_filter(page):
    entries.create(USER_ID, _buy(id=1, qty=1, price=100, currency='KRW', rawDate='2026-10-01T09:00'))
    entries.create(USER_ID, _buy(id=2, stock='Apple', stockCode='AAPL', currency='USD', exchange='NASDAQ',
                                qty=2, price=10, rawDate='2026-10-02T09:00'))
    requested = []
    def quotes(route):
        assets = route.request.post_data_json['assets']
        requested.extend(assets)
        route.fulfill(json={'quotes': [{**asset, 'price': 25 if asset['currency'] == 'USD' else 100,
            'market': asset.get('exchange') or 'KRX', 'asOf': '2026-10-25T00:00:00Z',
            'status': 'ok', 'stale': False} for asset in assets]})
    page.route('**/api/current_price', quotes)
    ready(page)
    expect(page.locator('#centerTotalInvested')).to_have_text('100원')
    page.locator('#btnChartView').click()
    page.locator('#chartStockFilter').select_option('Apple')
    page.locator('.chart-type-btn[data-type="evaluated"]').click()
    page.wait_for_function("() => window.monthlyProfitChartInstance?.data.datasets[0].data.includes(30)")
    assert {'code': 'AAPL', 'currency': 'USD', 'exchange': 'NASDAQ'} in requested
    expect(page.locator('#centerTotalInvested')).to_have_text('100원')


def test_slow_account_lookup_cannot_open_an_older_edit(page):
    ready(page)
    result = page.evaluate('''async () => {
        const oldJSON = journalJSON, oldMapping = ensureAccountMapping;
        const pending = [];
        journalJSON = async url => { const id=Number(url.split('/').pop());return {id,revision:1,type:'trade',stockName:'종목'+id,stockCode:'005930',rawDate:'2026-10-01T09:00',thoughts:'<p>내용</p>'}; };
        ensureAccountMapping = () => new Promise(resolve => pending.push(resolve));
        try {
            const a = editEntry({id:1}); await Promise.resolve();
            const b = editEntry({id:2}); await Promise.resolve();
            pending[1](true); await b;
            pending[0](true); await a;
            return {id:editingEntryId, name:document.getElementById('stockName').value};
        } finally {journalJSON=oldJSON;ensureAccountMapping=oldMapping;}
    }''')
    assert result == {'id': 2, 'name': '종목2'}


def test_calendar_navigation_does_not_skip_february(page):
    ready(page)
    page.evaluate('currentDate = new Date(2026, 2, 31); shiftCalendarMonth(-1)')
    expect(page.locator('#calendarMonthTitle')).to_have_text('2026년 2월')
    page.evaluate('currentDate = new Date(2026, 0, 31); shiftCalendarMonth(1)')
    expect(page.locator('#calendarMonthTitle')).to_have_text('2026년 2월')


def test_old_restore_preview_cannot_replace_latest_selected_file(page):
    ready(page)
    page.evaluate('''() => {
        const original = fetchWithTimeout;
        window.restoreReleases = [];
        fetchWithTimeout = (url, ...args) => url === '/api/restore/preview'
            ? new Promise(resolve => restoreReleases.push(count => resolve(new Response(JSON.stringify({beforeCount:1,afterCount:count,beforeAccounts:0,afterAccounts:0,holdings:[],previewToken:'test'})))))
            : original(url, ...args);
    }''')
    upload = page.locator('#restoreFileInput')
    upload.set_input_files({'name': 'older-A.zip', 'mimeType': 'application/zip', 'buffer': b'A'})
    upload.set_input_files({'name': 'latest-B.zip', 'mimeType': 'application/zip', 'buffer': b'B'})
    page.evaluate('restoreReleases[1](7)')
    expect(page.locator('#dataAuditContent')).to_contain_text('latest-B.zip')
    page.evaluate('restoreReleases[0](3)')
    expect(page.locator('#dataAuditContent')).to_contain_text('백업 기록 7건')
    expect(page.locator('#dataAuditContent')).not_to_contain_text('older-A.zip')
    page.locator('#dataAuditClose').click()


def test_editor_failure_keeps_history_and_allows_retry(page):
    diagnostics = []
    page.on('console', lambda message: diagnostics.append(message.text) if '편집기 초기화 실패' in message.text else None)
    page.route('**/quill@*/dist/quill.js', lambda route: route.abort())
    page.goto(BASE_URL + '/login')
    page.fill('input[name="username"]', USER_ID)
    page.fill('input[name="password"]', USER_PW)
    page.click('button[type="submit"]')
    expect(page.locator('#historyList .entry-card')).to_have_count(15)
    assert page.evaluate('window.quill == null')
    page.unroute('**/quill@*/dist/quill.js')
    page.evaluate("document.getElementById('btnRetryEditor').click()")
    page.wait_for_function("() => !!window.quill || document.getElementById('customModalOverlay').style.display === 'flex'")
    assert page.evaluate('!!window.quill'), '\n'.join(diagnostics)
    assert page.evaluate("document.getElementById('editorLoadNotice').hidden")


def test_editor_draft_survives_reload_and_is_cleared_after_save(page):
    ready(page)
    page.locator('#historyList .btn-edit').first.click()
    page.locator('#editor-container .ql-editor').fill('초안으로 보존할 문장')
    page.evaluate('saveEditorDraft()')
    page.reload()
    page.wait_for_function('() => !!window.quill && journalMeta !== null')
    page.locator('#historyList .btn-edit').first.click()
    expect(page.locator('#editorDraftNotice')).to_be_visible()
    page.locator('#btnRestoreDraft').click()
    expect(page.locator('#editor-container .ql-editor')).to_have_text('초안으로 보존할 문장')
    page.locator('#btnSubmitForm').click()
    expect(page.locator('#formModalOverlay')).not_to_be_visible()
    page.locator('#historyList .btn-edit').first.click()
    expect(page.locator('#editorDraftNotice')).to_be_hidden()


def test_draft_does_not_cross_recreated_account_identity(page):
    ready(page)
    page.locator('#historyList .btn-edit').first.click()
    page.locator('#editor-container .ql-editor').fill('이전 계정의 초안')
    page.evaluate('saveEditorDraft()')
    keys = page.evaluate('''() => {
        const before = editorDraftKey();
        window.JOURNAL_USER_ID = 'replacement-user';
        offerEditorDraft();
        return {before, after: editorDraftKey()};
    }''')
    assert keys['before'] != keys['after']
    expect(page.locator('#editorDraftNotice')).to_be_hidden()


def test_key_generation_sends_selected_scopes(page):
    ready(page)
    received = []
    def issue(route):
        if route.request.method != 'POST':
            route.fulfill(json={'keys': [], 'api_key': None})
            return
        received.append(route.request.post_data_json)
        route.fulfill(json={'api_key': 'test-only-key'})
    page.route('**/api/me/api-key', issue)
    page.evaluate("document.getElementById('btnHtsIntegration').click()")
    page.locator('#apiKeyScopes input[value="trades:write"]').uncheck()
    page.locator('#apiKeyScopes input[value="bot:write"]').uncheck()
    page.locator('#btnGenerateApiKey').click()
    page.locator('#btnCustomModalOk').click()
    expect(page.locator('#apiKeyValue')).to_have_value('test-only-key')
    assert received[0]['scopes'] == ['trades:read']
    page.locator('#btnCustomModalOk').click()


def test_export_preserves_currency_stored_pnl_and_fractional_shares(page):
    ready(page)
    row = page.evaluate('''() => journalExportRow({type:'trade', stockName:'Apple',stockCode:'AAPL',exchange:'NASDAQ',currency:'USD',price:12.3456,quantity:0.123456,realizedPnl:0,realizedPnlSource:'recorded',realizedPnlBasis:'net',fee:0,tax:null,thoughts:'<p>메모</p>'})''')
    assert row[3:6] == ['AAPL', 'NASDAQ', 'USD']
    assert row[12:19] == [12.3456, 0.123456, 0, '저장 손익', '비용 차감 후', 0, None]


def test_split_rule_preview_is_shown_before_scoped_update(page):
    ready(page)
    scope = json.dumps(['키움증권', '1234'], ensure_ascii=False, separators=(',', ':'))
    page.route('**/api/journal/integrity', lambda route: route.fulfill(json={
        'total': 1, 'issueCount': 0, 'issues': [], 'splits': [
            {'code': '005930', 'currency': 'KRW', 'stock': '삼성전자', 'day': '2026-10-01',
             'ratio': 2, 'mode': 'apply', 'scope': scope, 'broker': '키움증권', 'account': '1234'}]}))
    page.route('**/api/journal/split-rule/preview', lambda route: route.fulfill(json={
        'holdings': [], 'beforeRealized': 100, 'afterRealized': 50, 'currency': 'KRW', 'scope': scope, 'previewToken': 'preview-signature'}))
    updates = []
    def update(route):
        updates.append(route.request.post_data_json)
        route.fulfill(status=400, json={'error': '테스트 저장 차단'})
    page.route('**/api/journal/split-rule', update)
    page.evaluate('showDataIntegrity()')
    select = page.locator('#dataAuditContent select')
    select.select_option('recorded')
    expect(page.locator('#customModalMessage')).to_contain_text('100원 → 50원')
    assert not updates
    page.locator('#btnCustomModalOk').click()
    expect(page.locator('#customModalMessage')).to_have_text('테스트 저장 차단')
    assert updates[0]['scope'] == scope and updates[0]['mode'] == 'recorded'
    assert updates[0]['previewToken'] == 'preview-signature'
    page.locator('#btnCustomModalOk').click()
