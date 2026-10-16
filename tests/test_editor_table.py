"""본문 편집기(Quill 2 + quill-table-up) — 표 붙여넣기·저장·다시 편집, 예전 본문 호환 (브라우저 E2E).

Quill 1 에는 표 서식이 없어, 웹에서 복사한 표가 셀마다 한 줄로 흩어졌다.
Quill 2 로 올리면서 확인할 것은 셋이다.
  1) 웹 표를 붙여넣으면 머리글 칸(<th>)·병합 셀(colspan/rowspan)이 살아 있다.
  2) 저장 → 기록 화면 → 다시 편집을 거쳐도 표 구조가 그대로다.
     (저장 전 정화로 편집용 data-* 속성·아이콘 SVG 를 걷어내도 표는 구조만으로 복원된다)
  3) Quill 1 시절 본문(<ul><li> 목록, 직접 지정한 글자색)이 깨지지 않는다.

뒤쪽은 마크다운 붙여넣기(21-markdown-paste.js)다. 자동 판정은 하지 않으므로
일반 붙여넣기는 원문 그대로, Ctrl/Cmd+Shift+V 나 MD 버튼을 거친 붙여넣기만 서식으로 바뀐다.
"""
import os
import re
import threading

import pytest
from playwright.sync_api import Page, expect
from werkzeug.serving import make_server

from app.utils import ratelimit, statscache

PORT = 5003
BASE_URL = f"http://127.0.0.1:{PORT}"
USER_ID, USER_PW = 'editor', 'Editor1234!'

WEB_TABLE = (
    '<meta charset="utf-8"><table class="tb_type1"><caption>시세</caption>'
    '<thead><tr><th scope="col">종목</th><th scope="col" colspan="2">가격</th></tr></thead>'
    '<tbody><tr><td>삼성전자</td><td>72,000</td><td>+1.2%</td></tr>'
    '<tr><td rowspan="2">SK하이닉스</td><td>180,000</td><td>-0.5%</td></tr>'
    '<tr><td>1<br>2</td><td>x</td></tr></tbody></table><p>표 뒤 문단</p>')


@pytest.fixture(scope="module", autouse=True)
def live_server(tmp_path_factory):
    from backend_app import app as flask_app
    import backend_app
    import config

    sandbox = tmp_path_factory.mktemp('editor-e2e')
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
    statscache.clear_all()
    flask_app.test_client().post('/signup', data={
        'username': USER_ID, 'password': USER_PW, 'password_confirm': USER_PW})

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
    page.fill('input[name="username"]', USER_ID)
    page.fill('input[name="password"]', USER_PW)
    page.click('button[type="submit"]')
    page.wait_for_selector('#btnDataManagement', timeout=30000)
    page.wait_for_function("() => window.quill && typeof window.setEditorHtml === 'function'", timeout=30000)


def _insert_memo(entry_id, thoughts, title='메모'):
    import backend_app
    with backend_app.db_conn() as conn:
        conn.execute("INSERT INTO entries (id, username, type, title, thoughts, rawDate, date) "
                     "VALUES (?, ?, 'memo', ?, ?, '2026-10-01T09:00', '2026-10-01')",
                     (entry_id, USER_ID, title, thoughts))
        conn.commit()
    statscache.invalidate(USER_ID)


def _thoughts(title):
    import backend_app
    with backend_app.db_conn() as conn:
        row = conn.execute("SELECT thoughts FROM entries WHERE title = ?", (title,)).fetchone()
        return row[0] if row else None


def _open_edit(page: Page, entry_id):
    page.evaluate(f"() => editEntry(cloudEntries.find(e => e.id === {entry_id}))")
    page.wait_for_function("() => document.getElementById('formModalOverlay').style.display === 'flex'")


def test_editor_loads_quill2_with_table_tool(page: Page):
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    _login(page)
    assert page.evaluate("() => Quill.version").startswith('2.')
    assert page.locator('#journalForm .ql-toolbar .ql-picker.ql-table-up').count() == 1
    assert errors == []


def test_pasted_web_table_survives_save_view_and_reedit(page: Page):
    _login(page)
    page.evaluate("""() => {
        formModalOverlay.style.display = 'flex';
        const memo = document.querySelector('input[name="recordType"][value="memo"]');
        memo.checked = true; memo.dispatchEvent(new Event('change', { bubbles: true }));
        document.getElementById('memoTitle').value = '표 메모';
    }""")
    page.evaluate("""(html) => {
        const q = window.quill; q.focus(); q.setSelection(0);
        const dt = new DataTransfer(); dt.setData('text/html', html); dt.setData('text/plain', 'x');
        q.root.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
    }""", WEB_TABLE)

    editor = page.locator('#editor-container .ql-editor')
    expect(editor.locator('table th')).to_have_count(2)
    expect(editor.locator('th[colspan="2"]')).to_have_count(1)
    expect(editor.locator('td[rowspan="2"]')).to_have_count(1)

    page.evaluate("() => document.getElementById('journalForm').requestSubmit()")
    page.wait_for_function("() => cloudEntries.some(e => e.title === '표 메모')", timeout=10000)

    # 저장본: 표 구조는 있고, 편집용 표식(data-*·SVG·contenteditable)은 없다.
    saved = _thoughts('표 메모')
    assert '<table' in saved and '<th' in saved and 'colspan="2"' in saved and 'rowspan="2"' in saved
    assert 'data-table-id' not in saved and '<svg' not in saved and 'contenteditable' not in saved

    # 기록 화면: 표로 그려진다.
    page.evaluate("() => displayEntries(true)")
    card = page.locator('#historyList .entry-card', has_text='표 메모')
    card.locator('.btn-entry-detail').click()
    expect(card.locator('table th')).to_have_count(2)
    expect(card.locator('td', has_text='SK하이닉스')).to_have_attribute('rowspan', '2')

    # 다시 편집: 정화된 저장본에서도 표가 되살아난다.
    entry_id = page.evaluate("() => cloudEntries.find(e => e.title === '표 메모').id")
    _open_edit(page, entry_id)
    expect(editor.locator('th[colspan="2"]')).to_have_count(1)
    expect(editor.locator('td[rowspan="2"]')).to_have_count(1)
    expect(editor.locator('td', has_text='SK하이닉스')).to_have_count(1)


def test_legacy_quill1_lists_render_and_convert(page: Page):
    _insert_memo(9001, '<ul><li>글머리</li></ul><ol><li>번호</li></ol>'
                       '<p><span style="color: rgb(230, 0, 0);">빨강</span></p>', title='옛 메모')
    _login(page)
    page.evaluate("() => displayEntries(true)")
    card = page.locator('#historyList .entry-card', has_text='옛 메모')
    card.locator('.btn-entry-detail').click()
    expect(card.locator('li', has_text='글머리')).to_have_css('list-style-type', 'disc')
    expect(card.locator('li', has_text='번호')).to_have_css('list-style-type', 'decimal')

    # 편집기로 불러오면 Quill 2 목록으로 바뀌고, 직접 지정했던 글자색은 지워지지 않는다.
    _open_edit(page, 9001)
    editor = page.locator('#editor-container .ql-editor')
    expect(editor.locator('li[data-list="bullet"]', has_text='글머리')).to_have_count(1)
    expect(editor.locator('li[data-list="ordered"]', has_text='번호')).to_have_count(1)
    expect(editor.locator('span[style*="color"]', has_text='빨강')).to_have_count(1)


def test_pasted_text_still_drops_foreign_colors(page: Page):
    """불러오기 예외를 두면서 붙여넣기의 색 제거가 같이 꺼지면 안 된다."""
    _login(page)
    page.evaluate("""() => {
        const q = window.quill; window.setEditorHtml(''); q.focus(); q.setSelection(0);
        const dt = new DataTransfer();
        dt.setData('text/html', '<p><span style="color: rgb(0, 0, 255); background-color: yellow;">외부</span></p>');
        dt.setData('text/plain', '외부');
        q.root.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
    }""")
    editor = page.locator('#editor-container .ql-editor')
    expect(editor).to_contain_text('외부')
    assert editor.locator('span[style*="color"]').count() == 0


def test_sanitize_keeps_table_structure_and_column_width(page: Page):
    _login(page)
    out = page.evaluate("""() => sanitizeHtml(
        '<table data-table-id="t"><colgroup><col width="120px" data-col-id="c" onclick="x()"></colgroup>'
        + '<tbody><tr><td colspan="2" rowspan="1" data-row-id="r">a</td></tr></tbody></table>')""")
    assert '<col width="120px">' in out and 'colspan="2"' in out
    assert 'data-' not in out and 'onclick' not in out


# ---------------------------------------------------------------------------
# 마크다운 붙여넣기
# ---------------------------------------------------------------------------

MARKDOWN = (
    '## 매수 근거\n\n'
    '**실적** 개선과 ~~단기~~ 수급.\n\n'
    '- 첫째\n- 둘째\n  - 하위\n\n'
    '1. 하나\n2. 둘\n\n'
    '| 종목 | 가격 |\n|---|---:|\n| 삼성전자 | 72,000 |\n\n'
    '```\nprint(1)\n```\n')

_PASTE_TEXT = """(text) => {
    const q = window.quill; window.setEditorHtml(''); q.focus(); q.setSelection(0);
    const dt = new DataTransfer(); dt.setData('text/plain', text);
    q.root.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
}"""

_SHORTCUT = """() => window.quill.root.dispatchEvent(new KeyboardEvent('keydown',
    { key: 'V', code: 'KeyV', ctrlKey: true, shiftKey: true, bubbles: true, cancelable: true }))"""


def _expect_markdown_converted(page: Page):
    editor = page.locator('#editor-container .ql-editor')
    expect(editor.locator('h2', has_text='매수 근거')).to_have_count(1)
    expect(editor.locator('strong', has_text='실적')).to_have_count(1)
    expect(editor.locator('s', has_text='단기')).to_have_count(1)
    expect(editor.locator('li[data-list="bullet"]')).to_have_count(3)
    expect(editor.locator('li[data-list="bullet"].ql-indent-1', has_text='하위')).to_have_count(1)
    expect(editor.locator('li[data-list="ordered"]')).to_have_count(2)
    expect(editor.locator('table th', has_text='종목')).to_have_count(1)
    expect(editor.locator('table td', has_text='72,000')).to_have_count(1)
    expect(editor.locator('.ql-code-block', has_text='print(1)')).to_have_count(1)
    assert '##' not in editor.inner_text() and '**' not in editor.inner_text()


def test_plain_paste_keeps_markdown_as_text(page: Page):
    _login(page)
    page.evaluate(_PASTE_TEXT, MARKDOWN)
    editor = page.locator('#editor-container .ql-editor')
    expect(editor).to_contain_text('## 매수 근거')
    assert editor.locator('h2').count() == 0


def test_shortcut_paste_converts_markdown(page: Page):
    _login(page)
    page.evaluate(_SHORTCUT)
    page.evaluate(_PASTE_TEXT, MARKDOWN)
    _expect_markdown_converted(page)
    # 한 번만 적용된다 — 다음 일반 붙여넣기는 원문 그대로
    page.evaluate("""() => { const q = window.quill; q.setSelection(q.getLength() - 1);
        const dt = new DataTransfer(); dt.setData('text/plain', '## 그대로');
        q.root.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true })); }""")
    expect(page.locator('#editor-container .ql-editor')).to_contain_text('## 그대로')


def test_button_arms_next_paste_when_clipboard_api_is_unavailable(page: Page):
    _login(page)
    page.evaluate("() => { Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });"
                  " formModalOverlay.style.display = 'flex'; }")
    btn = page.locator('#journalForm .ql-toolbar button.ql-markdown')
    expect(btn).to_have_text('MD')
    btn.click()
    expect(btn).to_have_class(re.compile(r'\bmd-armed\b'))
    page.evaluate(_PASTE_TEXT, MARKDOWN)
    _expect_markdown_converted(page)
    expect(btn).not_to_have_class(re.compile(r'\bmd-armed\b'))


def test_button_cancel_and_escape_disarm(page: Page):
    _login(page)
    page.evaluate("() => { Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });"
                  " formModalOverlay.style.display = 'flex'; }")
    btn = page.locator('#journalForm .ql-toolbar button.ql-markdown')
    btn.click()
    btn.click()
    expect(btn).not_to_have_class(re.compile(r'\bmd-armed\b'))
    btn.click()
    page.evaluate("() => window.quill.root.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))")
    expect(btn).not_to_have_class(re.compile(r'\bmd-armed\b'))
    page.evaluate(_PASTE_TEXT, '## 원문')
    expect(page.locator('#editor-container .ql-editor')).to_contain_text('## 원문')


def test_markdown_raw_html_is_not_executed(page: Page):
    _login(page)
    page.evaluate(_SHORTCUT)
    page.evaluate(_PASTE_TEXT, '안녕 <img src=x onerror="window.__xss=1"> [링크](javascript:window.__xss=1)\n')
    page.wait_for_timeout(300)
    assert page.evaluate("() => window.__xss") is None
    html = page.evaluate("() => window.quill.root.innerHTML")
    assert 'onerror' not in html and 'javascript:' not in html


def test_shortcut_without_paste_event_falls_back_to_waiting(page: Page):
    """Safari 는 Cmd+Shift+V 에 붙여넣기 이벤트가 없다 — 대기로 넘어가 다음 Cmd+V 를 받는다."""
    _login(page)
    page.evaluate("() => { formModalOverlay.style.display = 'flex'; }")
    page.evaluate(_SHORTCUT)
    btn = page.locator('#journalForm .ql-toolbar button.ql-markdown')
    expect(btn).to_have_class(re.compile(r'\bmd-armed\b'), timeout=3000)
    page.evaluate(_PASTE_TEXT, MARKDOWN)
    _expect_markdown_converted(page)


def test_button_reads_clipboard_directly_when_permitted(page: Page, context):
    context.grant_permissions(['clipboard-read', 'clipboard-write'], origin=BASE_URL)
    _login(page)
    page.evaluate("(t) => navigator.clipboard.writeText(t)", MARKDOWN)
    page.evaluate("() => { formModalOverlay.style.display = 'flex'; window.setEditorHtml(''); }")
    page.locator('#journalForm .ql-toolbar button.ql-markdown').click()
    _expect_markdown_converted(page)
