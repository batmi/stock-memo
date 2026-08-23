// ═══════════════════════════════════════════════════════════════════
// 20-account-map.js — 계좌 매핑 관리 — 등록·수정·삭제·저장
//
// ⚠️ 이 파일들은 ES 모듈이 아니라 **순서대로 로드되는 클래식 스크립트**다.
//    최상위 let/const/function 은 전역 렉시컬 환경을 공유하므로, 예전 script.js
//    한 덩어리였을 때와 실행 의미가 완전히 같다. (HTML 의 인라인 onclick 핸들러가
//    전역 함수를 그대로 부르고 있어 모듈로 바꾸면 그것들이 전부 깨진다)
//    → 로드 순서는 templates/stock-memo.html 의 <script> 순서가 결정한다. 바꾸지 말 것.
// ═══════════════════════════════════════════════════════════════════

const accountMappingModalOverlay = document.getElementById('accountMappingModalOverlay');
const btnCloseAccountMappingModal = document.getElementById('btnCloseAccountMappingModal');
const btnAccountManagement = document.getElementById('btnAccountManagement');

if (btnAccountManagement && accountMappingModalOverlay) {
    btnAccountManagement.addEventListener('click', async () => {
        try {
            const res = await fetch('/api/mappings');
            if (res.ok) {
                currentAccountMappings = await res.json();
                renderAccountMappings();
                // ⭐️ 지난번에 '계좌 수정' 상태로 닫았을 수 있으므로 항상 접힌 신규 등록 폼으로 연다.
                const container = document.getElementById('newAccountFormContainer');
                if (container) container.style.display = 'none';
                resetAccountForm();
                accountMappingModalOverlay.style.display = 'flex';
                document.body.style.overflow = 'hidden';
            }
        } catch(e) {
            console.error('매핑 정보 로드 실패', e);
        }
    });
}

if (btnCloseAccountMappingModal) {
    btnCloseAccountMappingModal.addEventListener('click', async () => {
        accountMappingModalOverlay.style.display = 'none';
        document.body.style.overflow = '';
        // ⭐️ 저장하지 않고 닫은 변경은 화면에 반영되면 안 된다. (취소 버튼과 동일)
        await revertAccountMappings();
    });
}

function renderAccountMappings() {
    const list = document.getElementById('unifiedMappingList');
    if (!list) return;
    
    list.innerHTML = Object.entries(currentAccountMappings.accounts || {}).map(([accCode, info]) => {
        if (typeof info === 'string') {
            // legacy handling if any
            return `
                <div style="display: flex; justify-content: space-between; font-size: 11px; padding: 6px; border-bottom: 1px solid var(--border-light-color); align-items: center;">
                    <span style="flex: 1; word-break: break-all; margin-right: 10px;">(이전형식) ${escapeHtml(accCode)} &rarr; ${escapeHtml(info)}</span>
                    <button type="button" onclick="removeMapping('accounts', '${escapeJsInAttr(accCode)}')" style="background:none; border:none; color:var(--danger-color); cursor:pointer; font-size:11px; width:auto; padding:0; margin:0; box-shadow:none; flex: 0 0 auto;">삭제</button>
                </div>
            `;
        }
        // ⭐️ '금액 계산 제외' 계좌는 목록에서도 한눈에 구분돼야 한다.
        const excludeBadge = info.exclude_from_stats
            ? `<span style="font-size: 10px; background: var(--warning-color); color: #fff; padding: 1px 4px; border-radius: 3px; margin-left: 4px;" title="도넛 차트·총액·실현손익·차트·통계에서 제외됩니다.">금액 제외</span>`
            : '';
        return `
            <div style="display: flex; justify-content: space-between; align-items: center; font-size: 11.5px; padding: 8px 4px; border-bottom: 1px solid var(--border-light-color);">
                <span style="flex: 1; word-break: break-word; margin-right: 10px; line-height: 1.4;"><strong style="color:var(--text-strong-color);">[${escapeHtml(info.broker_name)}]</strong><br>${escapeHtml(accCode)} &rarr; ${escapeHtml(info.alias)}${excludeBadge}</span>
                <div style="display: flex; gap: 8px; flex: 0 0 auto;">
                    <button type="button" onclick="editMapping('${escapeJsInAttr(accCode)}')" style="background:none; border:none; color:var(--primary-color); cursor:pointer; font-size:11px; width:auto; padding:0; margin:0; box-shadow:none;">수정</button>
                    <button type="button" onclick="removeMapping('accounts', '${escapeJsInAttr(accCode)}')" style="background:none; border:none; color:var(--danger-color); cursor:pointer; font-size:11px; width:auto; padding:0; margin:0; box-shadow:none;">삭제</button>
                </div>
            </div>
        `;
    }).join('') || '<div style="font-size:11px; color:var(--text-muted-color); padding: 8px 4px;">등록된 계좌가 없습니다.</div>';
}

// ⭐️ 같은 폼을 '신규 등록'과 '수정'에 함께 쓰므로, 지금 무엇을 하는 중인지 문구로 알려준다.
function setAccountFormMode(mode) {
    const title = document.getElementById('btnToggleNewAccountForm');
    const submit = document.getElementById('btnAddUnifiedMapping');
    const isEdit = mode === 'edit';
    if (title) title.innerText = isEdit ? '계좌 수정' : '신규 계좌 등록';
    if (submit) submit.innerText = isEdit ? '수정하기' : '추가하기';
    if (title) title.dataset.mode = isEdit ? 'edit' : 'new';
}

// 입력칸을 비우고 '신규 계좌 등록' 상태로 되돌린다.
function resetAccountForm() {
    const select = document.getElementById('unifiedBrokerCode');
    if (select) select.value = '';
    document.getElementById('unifiedAccountCode').value = '';
    document.getElementById('unifiedAccountName').value = '';
    const excludeCheckbox = document.getElementById('unifiedAccountExcludeStats');
    if (excludeCheckbox) excludeCheckbox.checked = false;
    setAccountFormMode('new');
}

window.removeMapping = function(type, code) {
    if (currentAccountMappings[type] && currentAccountMappings[type][code]) {
        delete currentAccountMappings[type][code];
        renderAccountMappings();
    }
};

window.editMapping = function(code) {
    const info = currentAccountMappings.accounts[code];
    if (info && typeof info === 'object') {
        const select = document.getElementById('unifiedBrokerCode');
        for (let i = 0; i < select.options.length; i++) {
            if (select.options[i].dataset.name === info.broker_name || select.options[i].text === info.broker_name) {
                select.selectedIndex = i;
                break;
            }
        }
        document.getElementById('unifiedAccountCode').value = code;
        document.getElementById('unifiedAccountName').value = info.alias;
        const excludeCheckbox = document.getElementById('unifiedAccountExcludeStats');
        if (excludeCheckbox) excludeCheckbox.checked = !!info.exclude_from_stats;

        // ⭐️ 입력 폼이 접혀 있으면 값만 채워지고 화면에는 아무 변화가 없어 '수정이 안 된다'고 느낀다.
        //    반드시 펼치고, 폼이 보이는 위치까지 스크롤한다.
        setAccountFormMode('edit');
        const container = document.getElementById('newAccountFormContainer');
        if (container) {
            container.style.display = 'flex';
            if (typeof container.scrollIntoView === 'function') {
                container.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            }
        }
        document.getElementById('unifiedAccountCode').focus();
    }
};

const btnToggleNewAccountForm = document.getElementById('btnToggleNewAccountForm');
if (btnToggleNewAccountForm) {
    btnToggleNewAccountForm.addEventListener('click', () => {
        const container = document.getElementById('newAccountFormContainer');
        if (container.style.display === 'none') {
            // ⭐️ 수정 중이던 값이 남은 채로 '신규 계좌 등록'이 열리면 다른 계좌를 덮어쓰게 된다.
            //    (신규 입력 중 접었다 편 경우에는 적던 값을 그대로 살린다)
            if (btnToggleNewAccountForm.dataset.mode === 'edit') resetAccountForm();
            container.style.display = 'flex';
        } else {
            container.style.display = 'none';
        }
    });
}

const btnCancelNewAccountForm = document.getElementById('btnCancelNewAccountForm');
if (btnCancelNewAccountForm) {
    btnCancelNewAccountForm.addEventListener('click', () => {
        document.getElementById('newAccountFormContainer').style.display = 'none';
        resetAccountForm();
    });
}

const btnAddUnifiedMapping = document.getElementById('btnAddUnifiedMapping');
if (btnAddUnifiedMapping) {
    btnAddUnifiedMapping.addEventListener('click', () => {
        const select = document.getElementById('unifiedBrokerCode');
        const broker_code = select.value;
        const broker_name = select.options[select.selectedIndex]?.getAttribute('data-name');
        const acc_code = document.getElementById('unifiedAccountCode').value.trim();
        const alias = document.getElementById('unifiedAccountName').value.trim();
        const excludeCheckbox = document.getElementById('unifiedAccountExcludeStats');
        const excludeFromStats = !!(excludeCheckbox && excludeCheckbox.checked);

        if (broker_code && acc_code && alias) {
            if (!currentAccountMappings.accounts) currentAccountMappings.accounts = {};
            currentAccountMappings.accounts[acc_code] = {
                broker_code: broker_code,
                broker_name: broker_name,
                alias: alias,
                // ⭐️ 도넛·총액·실현손익·차트·통계 등 금액을 합산하는 모든 곳에서 뺄 계좌인지
                exclude_from_stats: excludeFromStats
            };

            // UI 초기화 ('계좌 수정' 상태였다면 문구도 신규 등록으로 되돌린다)
            resetAccountForm();
            document.getElementById('newAccountFormContainer').style.display = 'none';
            renderAccountMappings();
        } else {
            customAlert("모든 입력값을 채워주세요.");
        }
    });

    const handleEnter = (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            btnAddUnifiedMapping.click();
        }
    };
    document.getElementById('unifiedAccountCode').addEventListener('keydown', handleEnter);
    document.getElementById('unifiedAccountName').addEventListener('keydown', handleEnter);
}

const btnSaveAccountMappings = document.getElementById('btnSaveAccountMappings');
if (btnSaveAccountMappings) {
    btnSaveAccountMappings.addEventListener('click', async () => {
        try {
            btnSaveAccountMappings.innerText = '저장 중...';
            const res = await fetch('/api/mappings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(currentAccountMappings)
            });
            if (res.ok) {
                await customAlert('계좌정보가 저장되었습니다.');
                accountMappingModalOverlay.style.display = 'none';
                document.body.style.overflow = '';
                // ⭐️ 새로 등록·수정한 별칭이 새로고침 없이 카드·필터에 바로 반영되도록 다시 그린다.
                if (typeof renderPage === 'function') renderPage();
            } else {
                await customAlert('저장 실패');
            }
        } catch(e) {
            await customAlert('오류 발생');
        } finally {
            btnSaveAccountMappings.innerText = '저장';
        }
    });
}

const btnCancelAccountMappings = document.getElementById('btnCancelAccountMappings');
if (btnCancelAccountMappings) {
    btnCancelAccountMappings.addEventListener('click', async () => {
        accountMappingModalOverlay.style.display = 'none';
        document.body.style.overflow = '';
        // ⭐️ 취소는 '저장하지 않겠다'는 뜻이다. 메모리에만 남은 수정(특히 '금액 계산 제외' 체크)이
        //    다음 렌더링에 반영되지 않도록 서버 값으로 되돌린다.
        await revertAccountMappings();
    });
}

// 저장하지 않은 계좌 정보 변경을 서버 값으로 되돌린다.
async function revertAccountMappings() {
    try {
        const res = await fetch('/api/mappings');
        if (res.ok) {
            currentAccountMappings = await res.json();
            renderAccountMappings();
        }
    } catch (e) {
        console.error('계좌 정보 되돌리기 실패', e);
    }
}