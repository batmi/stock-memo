// 데이터 관리: 정합성 검사·변경 이력·복원 미리보기.
const auditOverlay = document.getElementById('dataAuditOverlay');
const auditContent = document.getElementById('dataAuditContent');
const auditActions = document.getElementById('dataAuditActions');
let auditCancel = null;
let auditGeneration = 0;
function closeDataAudit() {
    auditGeneration++;
    auditOverlay.style.display = 'none';
    if (auditCancel) { auditCancel(false); auditCancel = null; }
}
document.getElementById('dataAuditClose')?.addEventListener('click', closeDataAudit);
auditOverlay?.addEventListener('keydown', event => {
    if (event.key === 'Escape') closeDataAudit();
});
function openDataAudit(title) {
    closeDataAudit();
    document.getElementById('dataAuditTitle').textContent = title;
    auditContent.textContent = '확인 중입니다…';
    auditActions.replaceChildren();
    auditOverlay.style.display = 'flex';
    document.getElementById('dataAuditClose').focus();
    return auditGeneration;
}
function auditButton(label, action) {
    const button = document.createElement('button');
    button.type = 'button'; button.textContent = label;
    button.addEventListener('click', action);
    return button;
}
async function auditFetch(url, options) {
    const response = await fetch(url, options);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || '데이터를 불러오지 못했습니다.');
    return result;
}
const auditNumber = value => Number(value).toLocaleString('ko-KR', { maximumFractionDigits: 6 });
function auditTable(headers, rows) {
    return `<div class="data-audit-scroll"><table><thead><tr>${headers.map(x => `<th>${escapeHtml(x)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${row.map(x => `<td>${escapeHtml(String(x ?? ''))}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}
function showRestorePreview(data, filename = '') {
    openDataAudit('복원 미리보기');
    auditContent.innerHTML = `<p>${escapeHtml(filename)}</p><p>현재 기록 ${data.beforeCount}건 → 백업 기록 ${data.afterCount}건<br>계좌 설정 ${data.beforeAccounts}개 → ${data.afterAccounts}개</p>
        <p>현재 기록·첨부·계좌 설정·분할 설정을 백업 내용으로 교체합니다. 변경 이력은 유지합니다.</p>
        ${data.legacy ? '<p>이전 형식의 백업입니다. 계좌·분할 설정 파일이 없으면 해당 설정을 비웁니다.</p>' : ''}
        <p>보유 수량·매수 원가가 달라지는 항목</p>` +
        (data.holdings.length ? auditTable(['종목 / 통화', '증권사 / 계좌', '수량 (현재 → 복원)', '원가 (현재 → 복원)'], data.holdings.map(x => [
            `${x.stock} / ${x.currency}`, `${x.broker} / ${x.account}`,
            `${auditNumber(x.beforeQuantity)} → ${auditNumber(x.afterQuantity)}`,
            `${auditNumber(x.beforeCost)} → ${auditNumber(x.afterCost)}`])) : '<p>보유 수량·매수 원가의 변화가 없습니다.</p>');
    return new Promise(resolve => {
        auditCancel = resolve;
        auditActions.append(auditButton('취소', closeDataAudit), auditButton('이 내용으로 복원', () => {
            auditCancel = null; closeDataAudit(); resolve(true);
        }));
    });
}
async function showDataIntegrity() {
    const generation = openDataAudit('데이터 정합성 검사');
    try {
        const result = await auditFetch('/api/journal/integrity');
        if (generation !== auditGeneration) return;
        auditContent.innerHTML = `<p>기록 ${result.total}건 중 확인이 필요한 거래 ${result.issueCount}건입니다.</p>` +
            (result.issues.length ? auditTable(['거래', '종목', '확인 사항'], result.issues.map(x => [x.id, x.stock, x.reasons.join(' ')])) : '<p>저장된 거래에서 수량·시세 연결·손익 재구성 문제를 발견하지 못했습니다.</p>') +
            (result.truncated ? '<p>앞의 200건을 표시합니다. 거래 목록의 검토 필요 필터도 확인해주세요.</p>' : '') +
            '<p>분할 적용 설정: 자동 적용하면 분할 전 수량과 단가를 함께 환산합니다. 분할을 수기로 반영한 계좌는 자동 적용을 끌 수 있습니다. 계좌별 설정은 전체 계좌 기본값보다 우선하며, 기본값을 바꿔도 유지됩니다. 적용 전에 수량·원가·손익 변화를 확인합니다.</p>';
        for (const split of result.splits) {
            const row = document.createElement('div'); row.className = 'data-audit-split';
            const label = document.createElement('label');
            label.textContent = `${split.stock} · ${split.day} · ${split.ratio}배 · ${split.scope ? `${split.broker || ''} / ${split.account || ''}` : '전체 계좌 기본값'} `;
            const select = document.createElement('select');
            select.add(new Option('자동 적용', 'apply'));
            select.add(new Option('수기 반영 완료 (자동 적용 안 함)', 'recorded'));
            select.value = split.mode; label.append(select); row.append(label);
            select.addEventListener('change', async () => {
                const mode = select.value;
                select.disabled = true;
                try {
                    const preview = await auditFetch('/api/journal/split-rule/preview', { method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ ...split, mode }) });
                    if (generation !== auditGeneration) return;
                    const changes = preview.holdings.map(item => `${item.stock} · ${item.broker || ''} / ${item.account || ''}\n수량 ${auditNumber(item.beforeQuantity)} → ${auditNumber(item.afterQuantity)}\n매수 원가 ${moneyText(item.beforeCost, item.currency)} → ${moneyText(item.afterCost, item.currency)}`).join('\n\n');
                    const message = `${changes || '보유 수량과 매수 원가의 변화가 없습니다.'}\n\n실현손익 ${moneyText(preview.beforeRealized, preview.currency)} → ${moneyText(preview.afterRealized, preview.currency)}\n\n이 내용으로 적용할까요?`;
                    if (!(await customConfirm(message, '분할 적용 미리보기')) || generation !== auditGeneration) {
                        select.value = split.mode; return;
                    }
                    await auditFetch('/api/journal/split-rule', {method: 'PUT', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({...split, mode, previewToken: preview.previewToken})});
                    window.location.reload();
                } catch (error) { select.value = split.mode; await customAlert(error.message); }
                finally { select.disabled = false; }
            });
            auditContent.append(row);
        }
        if (!result.splits.length) auditContent.insertAdjacentHTML('beforeend', '<p>수집된 분할 정보가 없습니다.</p>');
    } catch (error) {
        if (generation === auditGeneration) auditContent.textContent = error.message;
    }
    if (generation === auditGeneration) auditActions.append(auditButton('다시 검사', showDataIntegrity));
}
const auditFieldNames = {quantity: '수량', price: '단가', realizedPnl: '실현손익', realizedPnlSource: '손익 출처', realizedPnlBasis: '손익 비용 기준',
    stockName: '종목명', stockCode: '종목코드', brokerAccount: '증권사', subAccount: '계좌번호', accountName: '계좌명',
    rawDate: '거래 일시', tradeDate: '거래일', tradeType: '매매 구분', title: '제목', thoughts: '내용',
    needsReview: '검토 필요', reviewReason: '검토 사유', mode: '분할 적용', code: '종목코드', day: '분할일',
    currency: '통화', tradeSequence: '거래 순서', fee: '수수료', tax: '세금', type: '기록 종류', tags: '태그',
    tradeStatus: '체결 상태', tradeClass: '매매 분류', recordCount: '기록 수',
    accountCount: '계좌 설정 수', splitRuleCount: '분할 설정 수'};
async function showDataChanges() {
    const generation = openDataAudit('변경 이력');
    auditContent.textContent = '이 기능 적용 이후의 추가·수정·삭제 이력입니다. 복원한 기록은 삭제·추가로 표시합니다.';
    const load = async cursor => {
        auditActions.replaceChildren();
        try {
            const result = await auditFetch('/api/journal/changes' + (cursor ? `?before=${cursor}` : ''));
            if (generation !== auditGeneration) return;
            if (!result.items.length && !cursor) auditContent.insertAdjacentHTML('beforeend', '<p>변경 이력이 없습니다.</p>');
            for (const item of result.items) {
                const detail = document.createElement('details');
                const summary = document.createElement('summary');
                const action = {insert: '추가', update: '수정', delete: '삭제', split_rule: '분할 설정', restore: '백업 복원'}[item.action] || item.action;
                summary.textContent = `${new Date(item.changedAt).toLocaleString('ko-KR')} · ${action} · ${item.stock || ''} ${item.entryId ?? ''} · ${{web: '화면', bot: '자동 연동', system: '시스템'}[item.origin] || item.origin}`;
                detail.append(summary);
                detail.insertAdjacentHTML('beforeend', auditTable(['항목', '변경 전', '변경 후'], Object.entries(item.fields).map(([key, pair]) => [auditFieldNames[key] || key, ...pair])));
                auditContent.append(detail);
            }
            if (result.nextCursor) auditActions.append(auditButton('이전 이력 더 보기', () => load(result.nextCursor)));
        } catch (error) {
            if (generation !== auditGeneration) return;
            await customAlert(error.message);
            auditActions.append(auditButton('다시 불러오기', () => load(cursor)));
        }
    };
    await load(null);
}
