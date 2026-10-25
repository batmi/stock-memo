// Editor failure is isolated from read-only screens. Drafts belong to one user and record.
const editorDraftFields = ['stockName', 'stockCode', 'brokerAccount', 'subAccount', 'accountName',
    'tradeClass', 'tradeType', 'entryCurrency', 'price', 'quantity', 'tradeDate', 'memoTitle', 'entryFee', 'entryTax', 'entryPnlBasis'];
let editorDraftBaseline = '';
let editorDraftTimer = null;
let editorDraftOffered = null;
function editorDraftKey(id = editingEntryId) {
    return window.JOURNAL_USERNAME && window.JOURNAL_USER_ID
        ? 'stockMemoDraft:v1:' + JSON.stringify([window.JOURNAL_USERNAME, window.JOURNAL_USER_ID, id || 'new']) : null;
}
function editorDraftSnapshot() {
    return { fields: Object.fromEntries(editorDraftFields.map(id => [id, document.getElementById(id).value])),
        recordType: document.querySelector('input[name="recordType"]:checked').value,
        hidden: document.getElementById('isHidden').checked, tags: [...currentTags],
        html: window.quill?.root.innerHTML || window.pendingEditorHtml || '',
        revision: editingEntryRevision, id: editingEntryId,
        removeFile: document.getElementById('removeAttachedFile').checked };
}
window.clearEditorDraft = function(id = editingEntryId) {
    clearTimeout(editorDraftTimer);
    const key = editorDraftKey(id);
    try { if (key) localStorage.removeItem(key); } catch (_) { /* storage may be disabled */ }
    document.getElementById('editorDraftNotice').hidden = true;
    document.getElementById('editorDraftStatus').textContent = '';
};
window.saveEditorDraft = function() {
    clearTimeout(editorDraftTimer);
    if (formModalOverlay.style.display !== 'flex' || !window.quill || editorDraftOffered) return false;
    const key = editorDraftKey();
    const draft = editorDraftSnapshot();
    if (!key) return false;
    if (JSON.stringify(draft) === editorDraftBaseline && !window.editorPendingWrite) {
        window.clearEditorDraft();
        return true;
    }
    try {
        localStorage.setItem(key, JSON.stringify({ ...draft, pendingWrite: window.editorPendingWrite,
            savedAt: new Date().toISOString() }));
        document.getElementById('editorDraftStatus').textContent = '이 기기에 초안 저장됨';
        return true;
    } catch (_) {
        document.getElementById('editorDraftStatus').textContent = '초안을 저장하지 못했습니다. 작성 내용을 저장해주세요.';
        return false;
    }
};
window.offerEditorDraft = function() {
    window.editorPendingWrite = null;
    clearTimeout(editorDraftTimer);
    editorDraftBaseline = JSON.stringify(editorDraftSnapshot());
    editorDraftOffered = null;
    const notice = document.getElementById('editorDraftNotice');
    notice.hidden = true;
    document.getElementById('editorDraftStatus').textContent = '';
    try {
        const key = editorDraftKey();
        const draft = key && JSON.parse(localStorage.getItem(key) || 'null');
        if (!draft?.fields || typeof draft.html !== 'string' || !Array.isArray(draft.tags) || !Number.isFinite(Date.parse(draft.savedAt))) return;
        if (Date.now() - Date.parse(draft.savedAt) > 30 * 86400000) {
            localStorage.removeItem(key); return;
        }
        editorDraftOffered = draft;
        document.getElementById('editorDraftMessage').textContent =
            `${new Date(draft.savedAt).toLocaleString('ko-KR')}에 저장한 초안이 있습니다. 복구하거나 삭제하면 새 초안을 자동 저장합니다.`;
        notice.hidden = false;
    } catch (_) { /* invalid or disabled storage does not prevent editing */ }
};
function scheduleEditorDraft() {
    clearTimeout(editorDraftTimer);
    editorDraftTimer = setTimeout(window.saveEditorDraft, 500);
}
window.attachEditorDraftEvents = function() {
    window.quill?.on('text-change', (_delta, _old, source) => {
        if (source === 'user') scheduleEditorDraft();
    });
};
journalForm.addEventListener('input', scheduleEditorDraft);
journalForm.addEventListener('change', scheduleEditorDraft);
window.addEventListener('pagehide', window.saveEditorDraft);
document.addEventListener('visibilitychange', () => { if (document.hidden) window.saveEditorDraft(); });
document.getElementById('btnDiscardDraft').addEventListener('click', () => {
    editorDraftOffered = null;
    window.clearEditorDraft();
});
document.getElementById('btnRestoreDraft').addEventListener('click', async () => {
    const draft = editorDraftOffered, generation = formGeneration;
    if (!draft || !window.quill) return;
    if (editingEntryId && draft.revision !== editingEntryRevision) {
        if (!(await customConfirm('초안 저장 이후 기록이 변경되었습니다. 현재 기록 대신 초안 내용을 불러올까요? 저장하기 전에 변경 내용을 확인해주세요.'))) return;
        if (generation !== formGeneration) return;
    }
    editorDraftFields.forEach(id => {
        const value = draft.fields[id];
        if (typeof value === 'string') document.getElementById(id).value = value;
    });
    setEntryCurrency(draft.fields.entryCurrency);
    const type = draft.recordType === 'memo' ? 'memo' : 'trade';
    document.querySelector(`input[name="recordType"][value="${type}"]`).checked = true;
    document.getElementById('isHidden').checked = !!draft.hidden;
    document.getElementById('removeAttachedFile').checked = !!draft.removeFile;
    const pending = draft.pendingWrite;
    if (pending && /^[a-f0-9]{48}$/.test(pending.key) && pending.id === editingEntryId
            && typeof pending.snapshot === 'string' && pending.payload && typeof pending.payload === 'object') {
        window.editorPendingWrite = pending;
    }
    document.getElementById('stockName').dataset.resolvedName = document.getElementById('stockName').value;
    syncJournalAccountSelect(draft.fields.subAccount);
    window.tradeDatePicker?.setDate(draft.fields.tradeDate);
    currentTags = draft.tags.filter(tag => typeof tag === 'string');
    renderTags();
    window.setEditorHtml(sanitizeHtml(draft.html));
    toggleFormUI(type);
    editorDraftOffered = null;
    document.getElementById('editorDraftNotice').hidden = true;
    document.getElementById('editorDraftStatus').textContent = '초안을 복구했습니다. 내용을 확인한 뒤 저장해주세요.';
});

function reloadEditorScript(fragment) {
    const original = [...document.scripts].find(script => script.src.includes(fragment));
    if (!original) return Promise.reject(new Error('편집기 파일을 찾지 못했습니다.'));
    return new Promise((resolve, reject) => {
        const script = document.createElement('script');
        const timer = setTimeout(() => { script.remove(); reject(new Error('편집기 응답이 지연됩니다.')); }, 15000);
        script.src = original.src;
        script.onload = () => { clearTimeout(timer); resolve(); };
        script.onerror = () => { clearTimeout(timer); script.remove(); reject(new Error('편집기를 불러오지 못했습니다.')); };
        document.head.append(script);
    });
}
document.getElementById('btnRetryEditor').addEventListener('click', async event => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
        const quillReloaded = !window.Quill;
        if (quillReloaded) await reloadEditorScript('/quill@');
        // UMD plugins can leave a truthy, incomplete export when their Quill dependency failed.
        if (quillReloaded || !window.TableUp?.TableUp) await reloadEditorScript('/quill-table-up@');
        if (quillReloaded || typeof window.QuillResizeModule !== 'function') await reloadEditorScript('/quill-resize-module@');
        if (!window.marked) await reloadEditorScript('/marked@');
        const container = document.getElementById('editor-container');
        if (!window.quill) {
            if (container.previousElementSibling?.classList.contains('ql-toolbar')) container.previousElementSibling.remove();
            container.innerHTML = '';
            if (!window.initializeJournalEditor()) throw new Error('편집기를 초기화하지 못했습니다.');
            window.initializeMarkdownEditor?.();
        }
    } catch (error) { await customAlert(error.message); }
    finally { button.disabled = false; }
});
