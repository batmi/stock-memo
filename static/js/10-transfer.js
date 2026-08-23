// ═══════════════════════════════════════════════════════════════════
// 10-transfer.js — 히스토리 무한스크롤·백업/복원·데이터 관리 모달
//
// ⚠️ 이 파일들은 ES 모듈이 아니라 **순서대로 로드되는 클래식 스크립트**다.
//    최상위 let/const/function 은 전역 렉시컬 환경을 공유하므로, 예전 script.js
//    한 덩어리였을 때와 실행 의미가 완전히 같다. (HTML 의 인라인 onclick 핸들러가
//    전역 함수를 그대로 부르고 있어 모듈로 바꾸면 그것들이 전부 깨진다)
//    → 로드 순서는 templates/stock-memo.html 의 <script> 순서가 결정한다. 바꾸지 말 것.
// ═══════════════════════════════════════════════════════════════════

const loadMoreObserver = new IntersectionObserver((entries) => {
    if (entries[0].isIntersecting) {
        currentRenderPage++;
        renderPage();
    }
}, { rootMargin: '300px' }); // 스크롤이 바닥에 닿기 300px 전에 미리 다음 페이지 로딩 시작

filterStockInput.addEventListener('input', () => { 
    clearFilterBtn.style.display = filterStockInput.value ? 'block' : 'none';
    // 검색어 타이핑 중에는 화면 요동을 방지하기 위해 실시간 필터링을 수행하지 않음
});
filterStockInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.isComposing) {
        filterStockInput.blur(); // ⭐️ 모바일 키보드 숨김(포커스 해제) 처리
        displayEntries(true);
        // ⭐️ 키보드가 닫히고 화면 크기가 복구된 뒤 안정적으로 스크롤되도록 지연 이동
        setTimeout(() => window.scrollToFilterBox(), 150);
    }
});
clearFilterBtn.addEventListener('click', () => {
    filterStockInput.value = '';
    clearFilterBtn.style.display = 'none';
    displayEntries(true);

    // 필터 초기화 시 히스토리 상단으로 부드럽게 스크롤
    window.scrollToFilterBox();
});

journalForm.addEventListener('submit', async function(e) {
    e.preventDefault();

    // ⭐️ 중복 제출 방지 (더블 클릭 등으로 인한 동일 데이터 복제 현상 해결)
    if (submitBtn.disabled) return;
    submitBtn.disabled = true;
    const origBtnText = submitBtn.innerText;
    submitBtn.innerText = "처리 중...";

    const recordType = document.querySelector('input[name="recordType"]:checked').value;
    const stockName = document.getElementById('stockName').value;
    const stockCode = document.getElementById('stockCode').value;
    const brokerAccount = document.getElementById('brokerAccount').value;
    const subAccount = document.getElementById('subAccount').value;
    const tradeDateRaw = document.getElementById('tradeDate').value;
    
    // ⭐️ 에디터에서 작성한 내용 가져오기 및 필수 입력 검증
    const thoughtsHTML = window.quill.root.innerHTML;
    const thoughtsText = window.quill.getText().trim();
    if (!thoughtsText && !thoughtsHTML.includes('<img')) {
        submitBtn.disabled = false;
        submitBtn.innerText = origBtnText;
        await customAlert("내용을 입력해주세요."); return;
    }
    const thoughts = thoughtsHTML === '<p><br></p>' ? '' : thoughtsHTML;
    const date = tradeDateRaw ? new Date(tradeDateRaw).toLocaleString() : new Date().toLocaleString();
    
    let newEntry;
    const nowIso = new Date().toISOString();
    let createdAt = nowIso;
    
    // ⭐️ 비동기 요청 중 전역 변수(editingEntryId)가 변경될 가능성을 대비하여 지역 변수로 캡처
    const currentEditingId = editingEntryId;
    
    if (currentEditingId) {
        const oldEntry = cloudEntries.find(e => e.id === currentEditingId);
        if (oldEntry) {
            createdAt = oldEntry.createdAt || new Date(oldEntry.id).toISOString(); // 기존 시간 유지
        }
    }

    if (recordType === 'trade') {
        const tradeClass = document.getElementById('tradeClass').value;
        const accountName = document.getElementById('accountName').value;
        const tradeType = document.getElementById('tradeType').value;
        const price = document.getElementById('price').value;
        let quantity = document.getElementById('quantity').value;

        // ⭐️ 배당일 때 수량이 입력되지 않았으면 자동으로 1로 보정
        if (tradeType === '배당' && (!quantity || Number(quantity) === 0)) {
            quantity = 1;
        }

        // ⭐️ 종목 숨김 플래그 — 이 종목의 '최신 기록'이 가진 값이 숨김 여부를 결정한다.
        const isHiddenEl = document.getElementById('isHidden');
        const isHidden = isHiddenEl && isHiddenEl.checked ? 1 : 0;

        newEntry = {
            id: currentEditingId || Date.now(), type: 'trade', stockName, stockCode, brokerAccount, subAccount, accountName, tradeClass,
            tradeType, price: price ? Number(price) : 0, quantity: quantity ? Number(quantity) : 0, thoughts, date, rawDate: tradeDateRaw, attachedImage: null,
            createdAt, updatedAt: nowIso, tags: currentTags.join(','), attachedFile: '', attachedFileName: '', isHidden
        };
    } else {
        const memoTitle = document.getElementById('memoTitle').value;
        newEntry = { id: currentEditingId || Date.now(), type: 'memo', stockName: '', stockCode: '', title: memoTitle, thoughts, date, rawDate: tradeDateRaw, attachedImage: null, createdAt, updatedAt: nowIso, tags: currentTags.join(','), attachedFile: '', attachedFileName: '', brokerAccount: '', subAccount: '', isHidden: 0 };
    }

    const method = currentEditingId ? 'PUT' : 'POST';
    const url = currentEditingId ? `/api/entry/${currentEditingId}` : '/api/entry';

    try {
        const res = await fetch(url, {
            method: method,
            headers: { 
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(newEntry)
        });
        
        if (res.ok) {
            if (currentEditingId) {
                const index = cloudEntries.findIndex(e => e.id === currentEditingId);
                if (index > -1) cloudEntries[index] = newEntry;
            } else {
                cloudEntries.unshift(newEntry);
            }
            
            editingEntryId = null;
            resetAndCloseForm();
            displayEntries(true);
            updatePortfolioSummary();
            renderCalendar();
        } else {
            // ⭐️ 서버 측 데이터 무결성 검증 오류 등 구체적인 메시지 표시
            let errMsg = "저장에 실패했습니다.";
            try {
                const errData = await res.json();
                if (errData && errData.error) errMsg = errData.error;
            } catch (_) { /* JSON 파싱 실패 시 기본 메시지 사용 */ }
            await customAlert(errMsg);
        }
    } catch(err) {
        console.error(err);
        await customAlert("데이터 저장 중 오류가 발생했습니다.");
    } finally {
        // ⭐️ 요청 완료 후 버튼 상태 원복 (UI 안정성 확보)
        submitBtn.disabled = false;
        if (editingEntryId !== null) {
            submitBtn.innerText = origBtnText;
        } else {
            submitBtn.innerText = "기록";
        }
    }
});

// ⭐️ 전체 데이터 백업 및 원복 이벤트 연결
const btnFullBackup = document.getElementById('btnModalFullBackup');
if (btnFullBackup) {
    btnFullBackup.addEventListener('click', async () => {
        if (await customConfirm('에디터 서식(폰트 등) 및 첨부 이미지를 포함한 \n모든 데이터를 완벽하게 백업합니다.\n\n다운로드를 진행하시겠습니까?')) {
            document.body.style.cursor = 'wait';
            window.showLoadingOverlay('데이터를 백업 중입니다...\n완료될 때까지 잠시만 기다려주세요.');
            fetch('/api/backup')
                .then(response => {
                    if (!response.ok) throw new Error('Network response was not ok');
                    let filename = 'TradingJournal_backup.zip';
                    const disposition = response.headers.get('content-disposition');
                    if (disposition && disposition.includes('attachment')) {
                        const matches = /filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/.exec(disposition);
                        if (matches != null && matches[1]) { 
                            filename = matches[1].replace(/['"]/g, '');
                        }
                    }
                    return response.blob().then(blob => ({ blob, filename }));
                })
                .then(({ blob, filename }) => {
                    const url = window.URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.style.display = 'none';
                    a.href = url;
                    a.download = filename;
                    document.body.appendChild(a);
                    a.click();
                    window.URL.revokeObjectURL(url);
                    a.remove();
                })
                .catch(async err => {
                    console.error(err);
                    document.body.style.cursor = 'default';
                    await window.hideLoadingOverlay();
                    await customAlert('백업 파일 다운로드 중 오류가 발생했습니다.');
                })
                .finally(async () => {
                    document.body.style.cursor = 'default';
                    await window.hideLoadingOverlay();
                });
        }
    });
}

const btnFullRestore = document.getElementById('btnModalFullRestore');
const restoreFileInput = document.getElementById('restoreFileInput');
if (btnFullRestore && restoreFileInput) {
    btnFullRestore.addEventListener('click', () => restoreFileInput.click());
    restoreFileInput.addEventListener('change', async (e) => {
        const file = e.target.files[0];
        if (!file) return;

        if (!(await customConfirm('원복을 진행하면 현재 작성된 모든 기록과 이미지가 \n백업 파일(.zip)의 내용으로 "완전히 덮어씌워"집니다.\n\n정말로 복구를 진행하시겠습니까?'))) {
            e.target.value = '';
            return;
        }

        const formData = new FormData();
        formData.append('file', file);

        try {
            document.body.style.cursor = 'wait'; // 로딩 커서
            window.showLoadingOverlay('데이터를 원복하고 있습니다...\n진행 중 창을 닫거나 새로고침하지 마세요.');
            const response = await fetch('/api/restore', {
                method: 'POST',
                body: formData
            });
            
            const result = await response.json();
            
            // ⭐️ 알림창(모달)이 뜨기 전에 즉시 마우스 커서를 정상으로 복구
            document.body.style.cursor = 'default';
            await window.hideLoadingOverlay(); // ⭐️ 로딩 애니메이션(최소 1초)이 완전히 끝날 때까지 대기
            
            if (response.ok && result.status === 'success') {
                // ⭐️ 재동기화는 여기서 걸지 않는다. 요청 창구는 계정 설정의
                //    재동기화 버튼 한 곳뿐이어야, 기간을 정하고 진행 상태를
                //    확인하는 흐름이 갈라지지 않는다.
                await customAlert('데이터가 성공적으로 원복되었습니다.\n화면을 새로고침 합니다.');
                // ⭐️ 모달 닫힘 애니메이션(180ms)이 끝난 후 안전하게 페이지 새로고침
                setTimeout(() => {
                    window.location.reload();
                }, 200);
            } else {
                await customAlert('원복 실패: ' + (result.error || '알 수 없는 오류가 발생했습니다.'));
            }
        } catch (err) {
            console.error(err);
            document.body.style.cursor = 'default';
            await window.hideLoadingOverlay();
            await customAlert('서버와 통신 중 오류가 발생했습니다.');
        } finally {
            document.body.style.cursor = 'default';
            await window.hideLoadingOverlay();
            e.target.value = '';
        }
    });
}
// ⭐️ 데이터 관리 모달 관련 로직
const btnDataManagement = document.getElementById('btnDataManagement');
const dataManagementModalOverlay = document.getElementById('dataManagementModalOverlay');
const btnDataManagementClose = document.getElementById('btnDataManagementClose');

if (btnDataManagement && dataManagementModalOverlay && btnDataManagementClose) {
    btnDataManagement.addEventListener('click', () => {
        dataManagementModalOverlay.style.display = 'flex';
    });
    btnDataManagementClose.addEventListener('click', () => {
        dataManagementModalOverlay.style.display = 'none';
    });
    
    // 모달 내 각 버튼 클릭 시 팝업 닫기
    const modalButtons = ['btnModalFullBackup', 'btnModalFullRestore', 'btnModalExportExcel'];
    modalButtons.forEach(id => {
        const btn = document.getElementById(id);
        if (btn) {
            btn.addEventListener('click', () => {
                dataManagementModalOverlay.style.display = 'none';
            });
        }
    });
}

// ⭐️ ExcelJS(약 800KB)는 초기 로딩에서 제외하고 엑셀 내보내기 시점에만 동적 로드
