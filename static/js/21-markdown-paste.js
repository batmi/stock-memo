// ═══════════════════════════════════════════════════════════════════
// 21-markdown-paste.js — 마크다운 원문 붙여넣기 → 편집기 서식(제목·목록·표·코드)으로 변환
//
// ⚠️ 이 파일들은 ES 모듈이 아니라 **순서대로 로드되는 클래식 스크립트**다.
//    최상위 let/const/function 은 전역 렉시컬 환경을 공유하므로, 예전 script.js
//    한 덩어리였을 때와 실행 의미가 완전히 같다. (HTML 의 인라인 onclick 핸들러가
//    전역 함수를 그대로 부르고 있어 모듈로 바꾸면 그것들이 전부 깨진다)
//    → 로드 순서는 templates/stock-memo.html 의 <script> 순서가 결정한다. 바꾸지 말 것.
// ═══════════════════════════════════════════════════════════════════

// ⭐️ 자동 판정은 하지 않는다. '*' 나 '-' 로 시작하는 평범한 글이 멋대로 목록이 되면 안 되므로,
//    일반 붙여넣기(Ctrl/Cmd+V)는 그대로 두고 사용자가 고를 때만 변환한다.
//    - Ctrl/Cmd+Shift+V: 브라우저의 '서식 없이 붙여넣기' 키. 이 키로 들어온 붙여넣기를 마크다운으로 읽는다.
//      (Safari 는 이 키에 붙여넣기가 없어 이벤트가 오지 않는다 → 아래 '대기'로 넘겨 다음 Cmd+V 를 받는다)
//    - 툴바 MD 버튼: 클립보드 읽기 API 가 되면 곧바로 넣고, 안 되면(http 접속·권한 거부)
//      '다음 붙여넣기 1회'를 마크다운으로 받도록 대기한다. 버튼을 다시 누르거나 Esc 로 취소.

const MD_SHORTCUT_WINDOW_MS = 1500; // 단축키를 누른 뒤 붙여넣기 이벤트를 기다리는 시간
const MD_ARMED_TIMEOUT_MS = 60000;  // 버튼 대기가 저절로 풀리는 시간
let mdShortcutAt = 0;
let mdArmed = false;
let mdArmedTimer = null;

function markdownToHtml(md) {
    // marked 는 원문 속 HTML 을 그대로 통과시키므로 편집기에 넣기 전에 정화한다.
    return sanitizeHtml(window.marked.parse(md, { gfm: true, breaks: false }));
}

window.insertMarkdownToQuill = function(md) {
    const q = window.quill;
    const Delta = Quill.import('delta');
    const range = q.getSelection(true);
    const index = range ? range.index : q.getLength();
    if (range && range.length > 0) q.deleteText(range.index, range.length, 'user');
    const delta = q.clipboard.convert({ html: markdownToHtml(md) });
    q.updateContents(new Delta().retain(index).concat(delta), 'user');
    q.setSelection(index + delta.length(), 'silent');
};

function setMarkdownArmed(on) {
    mdArmed = on;
    clearTimeout(mdArmedTimer);
    if (on) mdArmedTimer = setTimeout(() => setMarkdownArmed(false), MD_ARMED_TIMEOUT_MS);
    const btn = document.querySelector('#journalForm .ql-toolbar button.ql-markdown');
    if (btn) {
        btn.classList.toggle('md-armed', on);
        btn.title = on ? '붙여넣기(Ctrl/Cmd+V)를 기다리는 중 — 다시 누르면 취소'
                       : '마크다운으로 붙여넣기 (Ctrl/Cmd+Shift+V)';
    }
}

window.onMarkdownPasteButton = async function() {
    if (mdArmed) { setMarkdownArmed(false); return; }
    if (navigator.clipboard && navigator.clipboard.readText) {
        try {
            const text = await navigator.clipboard.readText();
            if (text) { window.insertMarkdownToQuill(text); return; }
        } catch (e) { /* 권한 거부 등 — 대기로 넘어간다 */ }
    }
    setMarkdownArmed(true);
    window.quill.focus();
};

window.addEventListener('DOMContentLoaded', () => {
    // 04-init.js 의 DOMContentLoaded 가 먼저 등록돼 있어 이 시점엔 편집기가 만들어져 있다.
    const q = window.quill;
    const container = document.getElementById('editor-container');
    if (!q || !container || !window.marked) return;

    const btn = document.querySelector('#journalForm .ql-toolbar button.ql-markdown');
    if (btn) { btn.textContent = 'MD'; btn.setAttribute('aria-label', '마크다운으로 붙여넣기'); }
    setMarkdownArmed(false);

    // marked 는 취소선을 <del> 로 낸다. Quill 은 <s>·<strike> 만 알아보므로 이어 준다.
    const Delta = Quill.import('delta');
    q.clipboard.addMatcher('del', (node, delta) =>
        delta.compose(new Delta().retain(delta.length(), { strike: true })));

    q.root.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.code === 'KeyV') {
            mdShortcutAt = Date.now();
            // 붙여넣기 이벤트가 따라오지 않는 브라우저(Safari)는 다음 붙여넣기를 기다린다.
            setTimeout(() => {
                if (mdShortcutAt && Date.now() - mdShortcutAt >= MD_SHORTCUT_WINDOW_MS - 50) {
                    mdShortcutAt = 0;
                    setMarkdownArmed(true);
                }
            }, MD_SHORTCUT_WINDOW_MS);
        } else if (e.key === 'Escape' && mdArmed) {
            setMarkdownArmed(false);
        }
    }, true);

    // 편집기 본문(root)보다 바깥에서 캡처로 받아야 04-init 의 붙여넣기 처리·Quill 기본 처리보다 먼저 돈다.
    container.addEventListener('paste', (e) => {
        const viaShortcut = mdShortcutAt && Date.now() - mdShortcutAt < MD_SHORTCUT_WINDOW_MS;
        if (!viaShortcut && !mdArmed) return;
        mdShortcutAt = 0;
        setMarkdownArmed(false);
        const text = e.clipboardData && e.clipboardData.getData('text/plain');
        if (!text) return; // 이미지만 있는 클립보드 등은 평소 경로로 넘긴다
        e.preventDefault();
        e.stopPropagation();
        window.insertMarkdownToQuill(text);
    }, true);
});
