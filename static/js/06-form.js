// ═══════════════════════════════════════════════════════════════════
// 06-form.js — 기록 입력 폼 요소·계좌 선택 동기화·자동완성·내 계정 삭제
//
// ⚠️ 이 파일들은 ES 모듈이 아니라 **순서대로 로드되는 클래식 스크립트**다.
//    최상위 let/const/function 은 전역 렉시컬 환경을 공유하므로, 예전 script.js
//    한 덩어리였을 때와 실행 의미가 완전히 같다. (HTML 의 인라인 onclick 핸들러가
//    전역 함수를 그대로 부르고 있어 모듈로 바꾸면 그것들이 전부 깨진다)
//    → 로드 순서는 templates/stock-memo.html 의 <script> 순서가 결정한다. 바꾸지 말 것.
// ═══════════════════════════════════════════════════════════════════

const journalForm = document.getElementById('journalForm');
const historyList = document.getElementById('historyList');
const filterStockInput = document.getElementById('filterStock');
const clearFilterBtn = document.getElementById('clearFilterBtn');
const formContainer = document.getElementById('formContainer');
const formModalOverlay = document.getElementById('formModalOverlay');
const btnFab = document.getElementById('btnFab');
const btnCloseForm = document.getElementById('btnCloseForm');
const submitBtn = journalForm.querySelector('button[type="submit"]');
let editingEntryId = null;
let portfolioChartInstance = null;
let currentTags = [];

// ⭐️ 모바일 환경 폼 스크롤 보정 (키보드 팝업 시 폼 높이 동적 조절 및 커서 중앙 배치)
if (formContainer) {
    // ⭐️ 모바일에서 가상 키보드가 올라오거나 터치로 커서를 변경할 때 해당 위치를 중앙으로 자동 스크롤
    function scrollToActiveElement() {
        if (!window.matchMedia("(max-width: 768px)").matches) return;
        const active = document.activeElement;
        if (active && (['INPUT', 'TEXTAREA'].includes(active.tagName) || active.isContentEditable)) {
            setTimeout(() => {
                if (active.isContentEditable && window.getSelection) {
                    // 에디터(ContentEditable) 내부일 경우, 실제 커서가 위치한 텍스트 노드의 부모를 찾아 스크롤
                    const selection = window.getSelection();
                    if (selection.rangeCount > 0) {
                        let targetNode = selection.focusNode;
                        if (targetNode && targetNode.nodeType === Node.TEXT_NODE) {
                            targetNode = targetNode.parentNode;
                        }
                        if (targetNode && targetNode.scrollIntoView) {
                            targetNode.scrollIntoView({ behavior: 'smooth', block: 'center' });
                            return;
                        }
                    }
                }
                // 일반 입력창인 경우
                active.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }, 300); // 가상 키보드가 올라오거나 UI가 재배치될 시간 확보
        }
    }

    // ⭐️ 폼 모달 높이 동적 업데이트 함수
    window.updateFormContainerHeight = function() {
        if (formModalOverlay && formModalOverlay.style.display === 'flex' && formContainer) {
            if (window.visualViewport) {
                if (window.matchMedia("(max-width: 768px)").matches) {
                    formContainer.style.maxHeight = `${window.visualViewport.height}px`;
                    formContainer.style.height = `${window.visualViewport.height}px`;
                    // ⭐️ 스마트폰에서 키보드 팝업 시 화면(Layout Viewport)이 위로 밀려 올라가는 오차를 정확히 계산하여 보정
                    formContainer.style.marginTop = `${window.visualViewport.offsetTop}px`;
                } else {
                    formContainer.style.maxHeight = `${window.visualViewport.height * 0.9}px`;
                    formContainer.style.height = 'auto';
                    formContainer.style.marginTop = '0px';
                }
            }
        }
    };

    if (window.visualViewport) {
        let prevViewportHeight = window.visualViewport.height;
        // ⭐️ 스크롤 시에도 offsetTop 값을 지속적으로 동기화하여 키보드 위로 UI가 항상 밀착되도록 유지
        window.visualViewport.addEventListener('scroll', () => {
            if (typeof window.updateFormContainerHeight === 'function') window.updateFormContainerHeight();
        });
        window.visualViewport.addEventListener('resize', () => {
            // ⭐️ iOS 등 모바일 환경에서 가상 키보드가 올라올 때 모달이 가려지지 않도록 팝업 최대 높이를 실제 뷰포트에 맞게 동적 보정
            if (typeof window.updateFormContainerHeight === 'function') window.updateFormContainerHeight();

            // ⭐️ 화면 높이가 줄어들었을 때(가상 키보드가 올라올 때)만 중앙 정렬 스크롤 실행
            if (window.visualViewport.height < prevViewportHeight) {
                scrollToActiveElement();
            }
            prevViewportHeight = window.visualViewport.height;
        });
    }
}

const defaultStocks = [
    "삼성전자", "SK하이닉스", "LG에너지솔루션", "현대차", "기아", "셀트리온", "POSCO홀딩스", "NAVER", "카카오",
    "애플 (AAPL)", "테슬라 (TSLA)", "엔비디아 (NVDA)", "마이크로소프트 (MSFT)", "알파벳 (GOOGL)", "아마존 (AMZN)"
];

async function ensureAccountMapping() {
    try {
        const res = await fetch('/api/mappings');
        if (res.ok) {
            currentAccountMappings = await res.json();
            if (typeof renderAccountMappings === 'function') renderAccountMappings();
        }
    } catch(e) {}
    
    const select = document.getElementById('journalAccountSelect');
    if (select) {
        select.innerHTML = '';
        const accounts = currentAccountMappings.accounts || {};
        
        if (Object.keys(accounts).length === 0) {
            select.innerHTML = '<option value="" disabled selected>계좌 없음 (먼저 등록하세요)</option>';
        } else {
            let options = '<option value="" disabled selected>계좌를 선택하세요</option>';
            for (const [code, info] of Object.entries(accounts)) {
                if (typeof info === 'string') continue;
                options += `<option value="${code}">[${info.broker_name}] ${info.alias} (${code})</option>`;
            }
            select.innerHTML = options;
        }
    }
    
    if (Object.keys(currentAccountMappings.accounts || {}).length === 0) {
        if (await customConfirm("등록된 계좌 매핑 정보가 없습니다.\n매매 기록을 작성하려면 계좌를 먼저 등록해야 합니다.\n지금 등록하시겠습니까?")) {
            const overlay = document.getElementById('accountMappingModalOverlay');
            if (overlay) {
                overlay.style.display = 'flex';
                document.body.style.overflow = 'hidden';
            }
        }
        return false;
    }
    return true;
}

const journalAccountSelect = document.getElementById('journalAccountSelect');
if (journalAccountSelect) {
    journalAccountSelect.addEventListener('change', () => {
        const val = journalAccountSelect.value;
        const brokerInput = document.getElementById('brokerAccount');
        const subInput = document.getElementById('subAccount');
        const nameInput = document.getElementById('accountName');
        const classInput = document.getElementById('tradeClass');
        const accounts = currentAccountMappings.accounts || {};
        if (val && accounts[val]) {
            brokerInput.value = accounts[val].broker_code;
            subInput.value = val;
            nameInput.value = accounts[val].alias;
        } else {
            brokerInput.value = '';
            subInput.value = '';
            nameInput.value = '';
        }
    });
}

// ⭐️ 저장된 계좌번호(subAccount)에 맞춰 '매매 계좌' 드롭다운 선택값을 동기화
//    (등록 목록에 없는 이전 형식 계좌는 임시 옵션을 만들어 표시)
function syncJournalAccountSelect(rawVal) {
    const select = document.getElementById('journalAccountSelect');
    if (!select) return false;
    const val = (rawVal || '').trim();
    if (!val) return false;

    let targetOpt = Array.from(select.options).find(opt => opt.value.replace(/-/g, '') === val.replace(/-/g, ''));
    if (!targetOpt) {
        targetOpt = document.createElement('option');
        targetOpt.value = val;
        targetOpt.text = `[미등록/이전형식] ${val}`;
        select.appendChild(targetOpt);
    }
    select.value = targetOpt.value;
    return true;
}

btnFab.addEventListener('click', async () => {
    const isTrade = document.querySelector('input[name="recordType"]:checked')?.value === 'trade';
    if (isTrade || !document.querySelector('input[name="recordType"]:checked')) {
        const canOpen = await ensureAccountMapping();
        if (!canOpen) return;
    }
    
    formModalOverlay.style.display = 'flex';
    document.body.style.overflow = 'hidden'; // ⭐️ 모달 열림 시 배경 스크롤 방지
    
    // ⭐️ 팝업 열릴 때 실제 화면 높이에 맞게 사이즈 조정 (키보드 대응)
    if (typeof window.updateFormContainerHeight === 'function') window.updateFormContainerHeight();
    
    // ⭐️ 새 글 작성 시 기록 일시를 현재 시간으로 리프레시
    const currentNow = new Date();
    currentNow.setMinutes(currentNow.getMinutes() - currentNow.getTimezoneOffset());
    if (window.tradeDatePicker) {
        window.tradeDatePicker.setDate(currentNow.toISOString().slice(0,16));
    } else {
        document.getElementById('tradeDate').value = currentNow.toISOString().slice(0,16);
    }
});

// ⭐️ 화살표로 감싸 **클릭 시점에** 이름을 찾게 한다.
//    resetAndCloseForm 은 09-form-fields.js 에 있다. 예전 script.js 한 덩어리에서는
//    함수 선언이 파일 전체로 호이스팅돼 그냥 참조해도 됐지만, 파일이 나뉘면
//    호이스팅 범위도 파일 단위라 로드 시점 참조는 ReferenceError 가 된다.
btnCloseForm.addEventListener('click', () => resetAndCloseForm());
const btnCancelForm = document.getElementById('btnCancelForm');
if (btnCancelForm) btnCancelForm.addEventListener('click', () => resetAndCloseForm());

    // ⭐️ 폼 모달창 드래그 이동 로직 (데스크탑 전용)
    const formHeader = document.querySelector('#formContainer .form-header-container');
    
    let formDragX = 0;
    let formDragY = 0;
    
    window.resetFormDragPosition = function() {
        formDragX = 0;
        formDragY = 0;
        if (formContainer) {
            formContainer.style.transform = '';
            formContainer.style.animation = ''; // ⭐️ 다음 팝업 시 등장 애니메이션 정상 동작을 위해 초기화
        }
    };

    if (formHeader && formContainer) {
        let isDragging = false;
        let startX, startY;

        formHeader.style.cursor = 'grab';
        formHeader.style.userSelect = 'none';

        formHeader.addEventListener('mousedown', (e) => {
            if (window.innerWidth <= 768) return; // 모바일 환경에서는 화면에 고정
            if (e.target.closest('.btn-close')) return; // 닫기 버튼 클릭 시 드래그 방지
            
            e.preventDefault(); // ⭐️ 브라우저 기본 텍스트 선택 및 드래그 앤 드롭 동작 차단
            formContainer.style.animation = 'none'; // ⭐️ CSS 등장 애니메이션(forwards)의 transform 잠금 강제 해제
            
            isDragging = true;
            formHeader.style.cursor = 'grabbing';
            startX = e.clientX - formDragX;
            startY = e.clientY - formDragY;
            
            document.body.style.userSelect = 'none'; // 드래그 중 텍스트 선택 방지
        });

        document.addEventListener('mousemove', (e) => {
            if (!isDragging) return;
            e.preventDefault();
            formDragX = e.clientX - startX;
            formDragY = e.clientY - startY;
            formContainer.style.transform = `translate(${formDragX}px, ${formDragY}px)`;
        });

        document.addEventListener('mouseup', () => {
            if (!isDragging) return;
            isDragging = false;
            formHeader.style.cursor = 'grab';
            document.body.style.userSelect = '';
        });
    }

// ⭐️ Esc 키로 모달 닫기
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        const customModal = document.getElementById('customModalOverlay');
        if (customModal && customModal.style.display === 'flex') return;
        
        const imageViewerModal = document.getElementById('imageViewerModal');
        if (imageViewerModal && imageViewerModal.style.display === 'flex') {
            window.closeImageViewer();
            return;
        }
        if (formModalOverlay.style.display === 'flex') {
            const openLists = document.querySelectorAll('.autocomplete-list[style*="display: block"]');
            if (openLists.length > 0) return; // 드롭다운이 열려있을 땐 모달 닫기 방지
            resetAndCloseForm();
        } else if (typeof passwordModalOverlay !== 'undefined' && passwordModalOverlay && passwordModalOverlay.style.display === 'flex') {
            document.getElementById('btnClosePasswordModal').click();
        } else if (typeof htsIntegrationModalOverlay !== 'undefined' && htsIntegrationModalOverlay && htsIntegrationModalOverlay.style.display === 'flex') {
            document.getElementById('btnCloseHtsIntegrationModal').click();
        } else if (typeof adminModalOverlay !== 'undefined' && adminModalOverlay && adminModalOverlay.style.display === 'flex') {
            document.getElementById('btnCloseAdminModal').click();
        } else if (typeof statsModalOverlay !== 'undefined' && statsModalOverlay && statsModalOverlay.style.display === 'flex') {
            document.getElementById('btnCloseStatsModal').click();
        } else if (typeof deleteAccountModalOverlay !== 'undefined' && deleteAccountModalOverlay && deleteAccountModalOverlay.style.display === 'flex') {
            document.getElementById('btnCloseDeleteAccountModal').click();
        }
    }
});

// ⭐️ 커스텀 자동완성(Autocomplete) 드롭다운 로직
function setupAutocomplete(inputId, listId, getOptions) {
    const input = document.getElementById(inputId);
    const list = document.getElementById(listId);
    let currentFocus = -1;
    let lastVal = input.value;
    
    // ⭐️ 공통 항목 선택 로직 (중복 실행 방지)
    function selectOption(opt) {
        if (!opt) return;
        if (input.value === opt && list.style.display === 'none') return;
        input.value = opt;
        lastVal = opt;
        list.style.display = 'none';
        input.dispatchEvent(new Event('input'));
        input.dispatchEvent(new CustomEvent('itemSelected', { detail: { value: opt } }));
    }

    // ⭐️ 핵심 1: 마우스를 누르는(mousedown) 즉시 항목을 선택하여 click 이벤트가 무시되는(씹히는) 현상 완벽 해결
    list.addEventListener('mousedown', function(e) {
        e.preventDefault(); // 스크롤바 조작 등 빈 공간 클릭 시 input의 포커스 유실 원천 차단
        const item = e.target.closest('.autocomplete-item');
        if (item) {
            e.stopPropagation();
            selectOption(item.getAttribute('data-val'));
        }
    });
    
    // ⭐️ 핵심 2: 한글 한 글자 입력(조합 중) 시, OS/브라우저가 한글 완성을 위해 첫 mousedown 이벤트를 강제로 삼켜버리는 현상 완벽 대응
    list.addEventListener('mouseup', function(e) {
        const item = e.target.closest('.autocomplete-item');
        if (item) {
            e.stopPropagation();
            selectOption(item.getAttribute('data-val'));
        }
    });
    
    // ⭐️ 핵심 3: 키보드 방향키 이동 후 엔터(Enter) 조작으로 item.click()이 코드상에서 강제 호출될 때를 대비한 폴백(Fallback)
    list.addEventListener('click', function(e) {
        const item = e.target.closest('.autocomplete-item');
        if (item) {
            e.stopPropagation();
            selectOption(item.getAttribute('data-val'));
        }
    });

    function triggerInput(e) {
        const val = input.value;
        
        // 한글 타이핑 중 방향키 조작 시 발생하는 의미 없는 input 이벤트 무시 (초기화 방지)
        if (e && e.type === 'input' && val === lastVal) return;
        lastVal = val;
        
        list.innerHTML = '';
        currentFocus = -1;
        const options = getOptions();
        const matched = val ? options.filter(opt => opt.toLowerCase().includes(val.toLowerCase())) : options;
        
        if (matched.length === 0) {
            list.style.display = 'none';
            return;
        }
        
        list.style.display = 'block';
        matched.forEach(opt => {
            const item = document.createElement('div');
            item.className = 'autocomplete-item';
            item.setAttribute('data-val', opt); // ⭐️ 클릭 이벤트를 위한 데이터 저장
            
            if (val) {
                // ⭐️ 특수문자 에러 방지 및 클릭 타겟 충돌을 막기 위해 span에 pointer-events: none 추가
                const safeVal = val.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
                const regex = new RegExp(`(${safeVal})`, 'gi');
                item.innerHTML = opt.replace(regex, "<span style='color:var(--danger-color); font-weight:var(--fw-bold, bold); pointer-events: none;'>$1</span>");
            } else {
                item.innerText = opt;
            }
            
            list.appendChild(item);
        });
    }

    input.addEventListener('input', triggerInput);
    input.addEventListener('focus', triggerInput);
    input.addEventListener('click', triggerInput);
    
    // ⭐️ 입력창 밖을 클릭했을 때 이벤트가 안전하게 처리될 수 있도록 닫힘 지연(150ms) 추가
    input.addEventListener('blur', function() {
        setTimeout(function() {
            list.style.display = 'none';
        }, 150);
    });

    input.addEventListener('keydown', function(e) {
        const items = list.getElementsByClassName('autocomplete-item');
        if (list.style.display === 'none') return;
        if (e.key === 'ArrowDown' || e.keyCode === 40) {
            currentFocus++; addActive(items); e.preventDefault();
        } else if (e.key === 'ArrowUp' || e.keyCode === 38) {
            currentFocus--; addActive(items); e.preventDefault();
        } else if (e.key === 'Enter' || e.keyCode === 13) {
            // 커서가 이동된 상태(currentFocus > -1)라면 한글 조합 중이더라도 항목 선택을 우선함
            if (currentFocus > -1 && items.length > 0) { 
                e.preventDefault(); 
                items[currentFocus].click(); 
            } else if (e.isComposing) {
                // 커서 이동 없이 단순 타이핑 중 엔터인 경우, 글자 조합만 완료하고 무시
                return;
            }
        } else if (e.key === 'Escape' || e.keyCode === 27) {
            list.style.display = 'none';
        }
    });

    function addActive(items) {
        if (!items || items.length === 0) return;
        for (let i = 0; i < items.length; i++) items[i].classList.remove('active');
        if (currentFocus >= items.length) currentFocus = 0;
        if (currentFocus < 0) currentFocus = items.length - 1;
        items[currentFocus].classList.add('active');
        items[currentFocus].scrollIntoView({ block: 'nearest' });
    }

    document.addEventListener('click', function(e) {
        if (e.target !== input && e.target !== list) list.style.display = 'none';
    });
}

// ⭐️ 회원 탈퇴 로직
const btnDeleteAccount = document.getElementById('btnDeleteAccount');
const deleteAccountModalOverlay = document.getElementById('deleteAccountModalOverlay');
const btnCloseDeleteAccountModal = document.getElementById('btnCloseDeleteAccountModal');
const deleteAccountForm = document.getElementById('deleteAccountForm');

if (btnDeleteAccount && deleteAccountModalOverlay) {
    btnDeleteAccount.addEventListener('click', () => {
        const pwOverlay = document.getElementById('passwordModalOverlay');
        if (pwOverlay) pwOverlay.style.display = 'none'; // 비번 변경 모달 숨기기
        deleteAccountModalOverlay.style.display = 'flex';
        document.body.style.overflow = 'hidden';
    });

    const closeDeleteModal = () => {
        deleteAccountModalOverlay.classList.add('closing');
        setTimeout(() => {
            deleteAccountModalOverlay.style.display = 'none';
            deleteAccountModalOverlay.classList.remove('closing');
            document.body.style.overflow = '';
            if(deleteAccountForm) deleteAccountForm.reset();
        }, 180);
    };

    if (btnCloseDeleteAccountModal) btnCloseDeleteAccountModal.addEventListener('click', closeDeleteModal);

    if (deleteAccountForm) deleteAccountForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const pw = document.getElementById('deleteAccountPassword').value;
        if (!pw) return;

        if (await customConfirm("정말로 탈퇴하시겠습니까?\n이 작업은 되돌릴 수 없습니다!")) {
            try {
                const submitBtn = deleteAccountForm.querySelector('button[type="submit"]');
                const origText = submitBtn.innerText;
                submitBtn.innerText = '탈퇴 중...';
                submitBtn.disabled = true;

                const res = await fetch('/api/account', {
                    method: 'DELETE',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ password: pw })
                });
                const data = await res.json();
                if (res.ok && data.status === 'success') {
                    await customAlert("계정이 성공적으로 삭제되었습니다. 이용해 주셔서 감사합니다.");
                    window.location.href = '/login';
                } else {
                    submitBtn.innerText = origText;
                    submitBtn.disabled = false;
                    // 최고 관리자 계정 탈퇴 차단 시 별도 알림 후 모달창 닫기
                    if (res.status === 403 && data.error === "최고 관리자 계정은 탈퇴할 수 없습니다.") {
                        await customAlert(data.error);
                        closeDeleteModal();
                    } else {
                        await customAlert("탈퇴 실패: " + (data.error || "알 수 없는 오류"));
                    }
                }
            } catch (e) {
                const submitBtn = deleteAccountForm.querySelector('button[type="submit"]');
                submitBtn.innerText = '탈퇴하기';
                submitBtn.disabled = false;
                await customAlert("탈퇴 처리 중 오류가 발생했습니다.");
            }
        }
    });
}

// ⭐️ 관리자 대시보드 로직
const btnAdmin = document.getElementById('btnAdmin');
const adminModalOverlay = document.getElementById('adminModalOverlay');
const btnCloseAdminModal = document.getElementById('btnCloseAdminModal');
const adminUserList = document.getElementById('adminUserList');

if (btnAdmin && adminModalOverlay) {
    btnAdmin.addEventListener('click', async () => {
        adminModalOverlay.style.display = 'flex';
        document.body.style.overflow = 'hidden';
        await loadAdminUsers();
    });
    
    const closeAdminModal = () => {
        adminModalOverlay.classList.add('closing');
        setTimeout(() => {
            adminModalOverlay.style.display = 'none';
            adminModalOverlay.classList.remove('closing');
            document.body.style.overflow = '';
        }, 180);
    };
    
    if (btnCloseAdminModal) btnCloseAdminModal.addEventListener('click', closeAdminModal);
}

// ─────────────────────────────────────────────────────────────
// ⭐️ 매매 성과 분석(통계) 모달
// ─────────────────────────────────────────────────────────────
