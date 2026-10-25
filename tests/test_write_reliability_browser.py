"""실제 편집기에서 느린 저장·응답 유실·첨부·통화·CDN 단절을 확인한다."""

import threading
from pathlib import Path

import pytest
from playwright.sync_api import expect
from werkzeug.serving import make_server

import config
from app.database.db import db_conn
from app.services import entries, users
from helpers import _buy, _login


@pytest.fixture
def browser_journal(app, client, page):
    _login(client)
    client.get('/')
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    page.context.add_cookies([{'name': 'session', 'value': client.get_cookie('session').value, 'url': base}])
    page.route('https://**', lambda route: route.abort())
    page.route('**/api/news', lambda route: route.fulfill(json=[]))
    page.route('**/api/current_price', lambda route: route.fulfill(json={}))
    yield page, client, base
    server.shutdown()
    thread.join(timeout=5)


def ready(browser_journal):
    page, _, base = browser_journal
    page.goto(base)
    page.wait_for_function("() => window.quill && typeof journalMeta !== 'undefined' && journalMeta && historyQuery")
    page.evaluate("() => { window.customAlert = async message => {window.lastAlert = message;}; }")
    return page


def new_memo(page):
    page.evaluate("""() => {
        formModalOverlay.style.display = 'flex';
        document.querySelector('input[name=recordType][value=memo]').checked = true;
        toggleFormUI('memo');
        document.getElementById('memoTitle').value = '저장 검증';
        window.offerEditorDraft();
    }""")
    page.locator('#editor-container .ql-editor').fill('첫 번째 내용')


def submit(page):
    page.evaluate("() => document.getElementById('journalForm').requestSubmit()")


def delay_one_save(page):
    page.evaluate("""() => {
        const original = window.fetch;
        let delay = true;
        window.fetch = async (input, options) => {
            if (delay && String(input).startsWith('/api/entry') && options?.method === 'POST') {
                delay = false;
                const response = await original(input, options);
                return new Promise(resolve => { window.releaseSave = () => resolve(response); });
            }
            return original(input, options);
        };
    }""")


def test_edits_during_save_remain_open_and_second_save_updates_same_record(browser_journal):
    page = ready(browser_journal)
    new_memo(page)
    delay_one_save(page)
    submit(page)
    page.wait_for_function('() => !!window.releaseSave')
    page.locator('#editor-container .ql-editor').fill('저장 중 추가한 내용')
    page.evaluate('() => window.releaseSave()')
    expect(page.locator('#editorDraftStatus')).to_contain_text('추가로 작성한 내용')
    expect(page.locator('#formModalOverlay')).to_be_visible()
    expect(page.locator('#editor-container .ql-editor')).to_have_text('저장 중 추가한 내용')
    rows = browser_journal[1].get('/api/data').json
    assert len(rows) == 1 and '첫 번째 내용' in rows[0]['thoughts']
    expect(page.locator('#btnSubmitForm')).to_be_enabled()
    submit(page)
    expect(page.locator('#formModalOverlay')).not_to_be_visible()
    rows = browser_journal[1].get('/api/data').json
    assert len(rows) == 1 and '저장 중 추가한 내용' in rows[0]['thoughts']


def test_lost_response_retry_after_reload_does_not_duplicate(browser_journal):
    page = ready(browser_journal)
    new_memo(page)
    page.evaluate("""() => {
        const original = window.fetch;
        let fail = true;
        window.fetch = async (input, options) => {
            const response = await original(input, options);
            if (fail && input === '/api/entry' && options?.method === 'POST') {
                fail = false; throw new TypeError('response lost');
            }
            return response;
        };
    }""")
    submit(page)
    page.wait_for_function('() => !!window.lastAlert')
    assert len(browser_journal[1].get('/api/data').json) == 1
    page = ready(browser_journal)
    new_memo(page)
    expect(page.locator('#editorDraftNotice')).to_be_visible()
    page.locator('#btnRestoreDraft').click()
    submit(page)
    expect(page.locator('#formModalOverlay')).not_to_be_visible()
    assert len(browser_journal[1].get('/api/data').json) == 1


def test_cancel_pending_save_keeps_draft_and_late_response_cannot_close_new_form(browser_journal):
    page = ready(browser_journal)
    new_memo(page)
    delay_one_save(page)
    submit(page)
    page.wait_for_function('() => !!window.releaseSave')
    page.locator('#editor-container .ql-editor').fill('닫기 전에 추가')
    page.locator('#btnCancelForm').click()
    expect(page.locator('#formModalOverlay')).not_to_be_visible()
    page.wait_for_function('() => editingEntryId === null && !submitBtn.disabled')
    new_memo(page)
    page.locator('#btnRestoreDraft').click()
    expect(page.locator('#editor-container .ql-editor')).to_have_text('닫기 전에 추가')
    page.evaluate('() => window.releaseSave()')
    expect(page.locator('#formModalOverlay')).to_be_visible()
    submit(page)
    expect(page.locator('#editorDraftStatus')).to_contain_text('추가로 작성한 내용')
    expect(page.locator('#btnSubmitForm')).to_be_enabled()
    submit(page)
    expect(page.locator('#formModalOverlay')).not_to_be_visible()
    rows = browser_journal[1].get('/api/data').json
    assert len(rows) == 1 and '닫기 전에 추가' in rows[0]['thoughts']


def test_text_edit_preserves_legacy_attachment_until_explicit_removal(browser_journal):
    folder = Path(users.user_dir(config.UPLOAD_FOLDER, 'trader'))
    folder.mkdir(parents=True, exist_ok=True)
    file = folder / 'report.pdf'
    file.write_bytes(b'%PDF-1.4\nlegacy attachment')
    entries.create('trader', {'id': 42, 'type': 'memo', 'title': '첨부 검증', 'thoughts': '첨부 메모',
                             'attachedFile': '/uploads/trader/report.pdf', 'attachedFileName': '보고서.pdf'})
    page = ready(browser_journal)
    page.evaluate('async () => editEntry(await (await fetch("/api/entry/42")).json())')
    expect(page.locator('#existingFileLink')).to_have_text('보고서.pdf')
    page.locator('#editor-container .ql-editor').fill('본문만 수정')
    submit(page)
    expect(page.locator('#formModalOverlay')).not_to_be_visible()
    assert file.exists() and browser_journal[1].get('/api/entry/42').json['attachedFile'].endswith('/report.pdf')
    page.evaluate('async () => editEntry(await (await fetch("/api/entry/42")).json())')
    page.locator('#removeAttachedFile').check()
    submit(page)
    expect(page.locator('#formModalOverlay')).not_to_be_visible()
    assert not file.exists() and not browser_journal[1].get('/api/entry/42').json['attachedFile']


def test_automatic_currency_cost_edit_and_excel_work_without_cdn(browser_journal):
    common = {'subAccount': '1234', 'brokerAccount': '243', 'accountName': '혼합 계좌',
              'tradeClass': '장기투자', 'thoughts': '거래 메모', 'rawDate': '2026-10-01T10:00'}
    entries.create('trader', _buy(id=1, qty=1, price=10000, currency='KRW', **common))
    entries.create('trader', _buy(id=2, stock='Apple', stockCode='AAPL', qty=2, price=12.5, currency='USD', **common))
    assert browser_journal[1].post('/api/mappings', json={
        'brokers': {'243': '한국투자증권'},
        'accounts': {'1234': {'alias': '혼합 계좌', 'broker_name': '한국투자증권', 'broker_code': '243'}}
    }).status_code == 200
    # 이전 화면에서 선택한 통화가 남아 있어도 숨겨진 필터로 거래를 제외하지 않는다.
    user_id = browser_journal[1].get('/api/me').json['user_id']
    browser_journal[0].add_init_script(f"localStorage.setItem('journalCurrency:{user_id}', 'USD')")
    page = ready(browser_journal)
    expect(page.locator('#journalCurrencySelect')).to_have_count(0)
    expect(page.locator('#centerTotalInvested')).to_have_text('10,000원')
    expect(page.locator('#historyList .entry-card')).to_have_count(2)
    page.locator('#btnChartView').click()
    page.locator('#chartStockFilter').select_option('Apple')
    page.evaluate('() => window.loadTradeStats()')
    expect(page.locator('#inlineStatsContainer')).to_contain_text('25 USD')
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.evaluate('''async () => {
            await ensureExcelLoaded(); window.testSheet = new ExcelJS.Workbook();
            window.testSheet.addWorksheet('내보내기').addRow(['종목', 12.5]);
            window.xlsxBytes = (await Promise.race([window.testSheet.xlsx.writeBuffer(),
                new Promise((_, reject) => setTimeout(() => reject(new Error('XLSX timeout')), 5000))])).byteLength;
        }''')
    except Exception as error:
        raise AssertionError(errors) from error
    assert page.evaluate('() => !!window.testSheet && !!window.Chart && !!window.quill && !!window.flatpickr && !!window.Sortable')
    assert page.evaluate('() => window.xlsxBytes') > 1000
    page.evaluate('async () => editEntry(await (await fetch("/api/entry/2")).json())')
    page.locator('#entryCostFields summary').click()
    page.locator('#entryFee').fill('0')
    page.locator('#entryTax').fill('0.25')
    submit(page)
    expect(page.locator('#formModalOverlay')).not_to_be_visible()
    row = browser_journal[1].get('/api/entry/2').json
    assert row['fee'] == 0 and row['tax'] == 0.25 and row['currency'] == 'USD'
    page.reload()
    expect(page.locator('#journalCurrencySelect')).to_have_count(0)
    expect(page.locator('#centerTotalInvested')).to_have_text('10,000원')


def test_same_user_token_rotation_preserves_open_editor(browser_journal):
    page = ready(browser_journal)
    new_memo(page)
    client = browser_journal[1]
    with client.session_transaction() as session:
        session['csrf_token'] = 'rotated-in-another-tab'
    page.context.add_cookies([{'name': 'session', 'value': client.get_cookie('session').value, 'url': browser_journal[2]}])
    submit(page)
    expect(page.locator('#formModalOverlay')).not_to_be_visible()
    with db_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM entries WHERE username='trader'").fetchone()[0] == 1
    assert page.evaluate('() => window.JOURNAL_CSRF_TOKEN') == 'rotated-in-another-tab'


def test_stalled_save_times_out_and_storage_failure_is_reported(browser_journal):
    page = ready(browser_journal)
    new_memo(page)
    page.evaluate('''() => {
        const originalFetch = window.fetch, originalTimer = window.setTimeout;
        window.setTimeout = (fn, delay, ...args) => originalTimer(fn, delay === 30000 ? 100 : delay, ...args);
        const originalStorage = Storage.prototype.setItem;
        Storage.prototype.setItem = function(key, value) {
            if (key.startsWith('stockMemoDraft:')) throw new DOMException('Full', 'QuotaExceededError');
            return originalStorage.call(this, key, value);
        };
        window.fetch = (input, options) => input === '/api/entry' && options?.method === 'POST'
            ? new Promise((_, reject) => options.signal.addEventListener('abort',
                () => reject(new DOMException('Timeout', 'AbortError')), {once: true}))
            : originalFetch(input, options);
    }''')
    submit(page)
    page.wait_for_function('() => !!window.lastAlert')
    assert '초안도 저장하지 못했습니다' in page.evaluate('() => window.lastAlert')
    expect(page.locator('#btnSubmitForm')).to_be_enabled()
    expect(page.locator('#editor-container .ql-editor')).to_have_text('첫 번째 내용')
    expect(page.locator('#formModalOverlay')).to_be_visible()
    assert browser_journal[1].get('/api/data').json == []


def test_stale_delete_refreshes_the_visible_record_before_retry(browser_journal):
    entries.create('trader', {'id': 42, 'type': 'memo', 'title': '원래 기록', 'thoughts': '처음'})
    page = ready(browser_journal)
    page.wait_for_function('() => cloudEntries.some(row => row.id === 42)')
    revision = page.evaluate('() => cloudEntries.find(row => row.id === 42).revision')
    result = browser_journal[1].put('/api/entry/42', json={'title': '다른 탭에서 수정', 'revision': revision})
    assert result.status_code == 200
    page.evaluate('() => { window.customConfirm = async () => true; }')
    page.evaluate('(revision) => deleteEntry(42, revision)', revision)
    expect(page.locator('#historyList .entry-card')).to_contain_text('다른 탭에서 수정')
    assert page.evaluate('() => cloudEntries.find(row => row.id === 42).revision') == result.json['revision']
    assert '다른 곳에서 변경된 기록' in page.evaluate('() => window.lastAlert')
    assert browser_journal[1].get('/api/entry/42').status_code == 200
