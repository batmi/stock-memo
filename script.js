let cloudEntries = [];
let currentHoldings = [];
let newsInterval = null;
let currentFilterDate = null;
let currentFilterType = null;
let isDashboardCollapsed = false;
let showClosedPositions = false; // 청산 종목 보기 상태
let showCurrentPrice = false; // ⭐️ 현재가 및 평가금액 보기 상태
let currentPortfolioArrayForPrice = []; // 현재가 계산용 임시 배열
let showHistoryClosedPositions = true; // ⭐️ 기본적으로 히스토리에 청산 종목을 표시하도록 변경
let currentDashboardBroker = 'all'; // 대시보드 증권사 필터 상태
let currentDashboardAccount = 'all'; // 대시보드 투자 분류 필터 상태
let currentFilteredEntries = [];
let currentRenderPage = 1;
const entriesPerPage = 15;
let lastRenderedMonth = '';
let currentAttachedFile = null;
let currentAttachedFileName = '';
let pendingUploadFile = null;
let userPreferences = {};       // ⭐️ 사용자별 설정(포트폴리오 정렬 순서 등) 저장
let portfolioSortable = null;   // ⭐️ SortableJS 드래그 앤 드롭 인스턴스

// ⭐️ 공통 스크롤 함수: 스크롤 튐 현상을 막기 위해 window.scrollTo 절대 좌표 사용
window.scrollToFilterBox = function() {
    // "TRADE HISTORY" 타이틀이 포함된 history-header 영역을 찾아 최상단으로 스크롤
    const historyHeader = document.querySelector('.history-header');
    if (!historyHeader) return;
    const y = historyHeader.getBoundingClientRect().top + window.scrollY - 20; // 상단 여백 20px
    window.scrollTo({ top: Math.max(0, y), behavior: 'smooth' });
};

// ⭐️ 커스텀 공통 모달 (Alert, Confirm, Prompt 대체용)
window.customModal = function({ type = 'alert', title = '알림', message = '', inputPlaceholder = '' }) {
    return new Promise((resolve) => {
        const overlay = document.getElementById('customModalOverlay');
        if (!overlay) return resolve(type === 'prompt' ? null : true); // HTML 로드 전 폴백
        
        const titleEl = document.getElementById('customModalTitle');
        const messageEl = document.getElementById('customModalMessage');
        const promptContainer = document.getElementById('customModalPromptContainer');
        const inputEl = document.getElementById('customModalInput');
        const btnCancel = document.getElementById('btnCustomModalCancel');
        const btnOk = document.getElementById('btnCustomModalOk');

        titleEl.innerText = title;
        messageEl.innerText = message;
        promptContainer.style.display = type === 'prompt' ? 'block' : 'none';
        if (type === 'prompt') { inputEl.value = ''; inputEl.placeholder = inputPlaceholder; }
        btnCancel.style.display = (type === 'confirm' || type === 'prompt') ? 'block' : 'none';

        overlay.style.display = 'flex';
        if (type === 'prompt') inputEl.focus();
        else btnOk.focus();

        const cleanup = () => {
            overlay.classList.add('closing');
            setTimeout(() => { overlay.style.display = 'none'; overlay.classList.remove('closing'); }, 180);
            btnOk.removeEventListener('click', onOk);
            btnCancel.removeEventListener('click', onCancel);
            inputEl.removeEventListener('keydown', onInputKeydown);
            document.removeEventListener('keydown', onDocKeydown);
        };
        const onOk = () => { cleanup(); resolve(type === 'prompt' ? inputEl.value : true); };
        const onCancel = () => { cleanup(); resolve(type === 'prompt' ? null : false); };
        const onInputKeydown = (e) => { if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); onOk(); } };
        const onDocKeydown = (e) => { if (e.key === 'Escape' && overlay.style.display === 'flex') { e.preventDefault(); e.stopPropagation(); onCancel(); } };

        btnOk.addEventListener('click', onOk);
        btnCancel.addEventListener('click', onCancel);
        if (type === 'prompt') inputEl.addEventListener('keydown', onInputKeydown);
        document.addEventListener('keydown', onDocKeydown);
    });
};
window.customAlert = (message, title = '알림') => window.customModal({ type: 'alert', title, message });
window.customConfirm = (message, title = '확인') => window.customModal({ type: 'confirm', title, message });
window.customPrompt = (message, title = '입력', placeholder = '') => window.customModal({ type: 'prompt', title, message, inputPlaceholder: placeholder });

// ⭐️ 1시간 동안 아무런 동작이 없으면 자동 로그아웃 (5분 전 경고 팝업 표시)
let warningTimer;
let logoutTimer;
let countdownInterval;
const TOTAL_SESSION_LIMIT = 60 * 60 * 1000; // 1시간 (밀리초)
const WARNING_BEFORE = 5 * 60 * 1000;       // 5분 전 (밀리초)
const WARNING_TRIGGER_TIME = TOTAL_SESSION_LIMIT - WARNING_BEFORE; // 55분

function resetInactivityTimers() {
    // 경고 팝업이 떠 있는 상태에서는 배경 클릭/스크롤 등으로 연장되지 않도록 방지
    const extensionModal = document.getElementById('sessionExtensionModalOverlay');
    if (extensionModal && extensionModal.style.display === 'flex') return;

    clearTimeout(warningTimer);
    clearTimeout(logoutTimer);
    clearInterval(countdownInterval);

    warningTimer = setTimeout(showExtensionWarning, WARNING_TRIGGER_TIME);
}

function showExtensionWarning() {
    const extensionModal = document.getElementById('sessionExtensionModalOverlay');
    const countdownEl = document.getElementById('sessionCountdown');
    if (!extensionModal || !countdownEl) return;

    extensionModal.style.display = 'flex';
    let timeLeft = Math.floor(WARNING_BEFORE / 1000);
    countdownEl.innerText = `${Math.floor(timeLeft / 60).toString().padStart(2, '0')}:${(timeLeft % 60).toString().padStart(2, '0')}`;

    countdownInterval = setInterval(() => {
        timeLeft -= 1;
        if (timeLeft <= 0) clearInterval(countdownInterval);
        else countdownEl.innerText = `${Math.floor(timeLeft / 60).toString().padStart(2, '0')}:${(timeLeft % 60).toString().padStart(2, '0')}`;
    }, 1000);

    logoutTimer = setTimeout(() => {
        window.location.href = '/logout?timeout=1';
    }, WARNING_BEFORE);
}

['mousedown', 'mousemove', 'keydown', 'scroll', 'touchstart'].forEach(evt => {
    document.addEventListener(evt, resetInactivityTimers, { passive: true });
});
resetInactivityTimers(); // 초기 타이머 시작

const mainApp = document.getElementById('mainApp');
window.addEventListener('DOMContentLoaded', () => {
    const themeToggle = document.getElementById('theme-toggle');

    function applyTheme(theme) {
        document.documentElement.setAttribute('data-theme', theme);
        const fpDark = document.getElementById('flatpickr-dark-theme');
        if (theme === 'dark') {
            themeToggle.checked = true;
            if (fpDark) fpDark.removeAttribute('disabled');
        } else {
            themeToggle.checked = false;
            if (fpDark) fpDark.setAttribute('disabled', 'disabled');
        }
        // 차트가 이미 생성되었다면 색상을 업데이트하기 위해 다시 렌더링
        if (portfolioChartInstance) {
            updatePortfolioSummary();
        }
    }

    themeToggle.addEventListener('change', () => {
        const theme = themeToggle.checked ? 'dark' : 'light';
        localStorage.setItem('theme', theme);
        applyTheme(theme);
    });

    // 페이지 로드 시 저장된 테마 적용
    const savedTheme = localStorage.getItem('theme') || 'dark';
    applyTheme(savedTheme);

    // ⭐️ 모바일/데스크탑 레이아웃 동적 전환 (뉴스 영역 및 접기 버튼 위치 이동)
    function applyMobileResponsiveLayout() {
        const isMobile = window.innerWidth <= 900;
        const mainApp = document.getElementById('mainApp');
        const portfolioSection = document.getElementById('portfolioSection');
        const historyHeader = document.querySelector('.history-header');
        const newsSidebar = document.getElementById('newsSidebar');
        const mainLayout = document.querySelector('.main-layout');
        
        const themeSwitchWrapper = document.querySelector('.theme-switch-wrapper');
        const themeSwitchOuter = themeSwitchWrapper ? themeSwitchWrapper.parentElement.parentElement : null;
        const btnTogglePortfolio = document.getElementById('btnTogglePortfolio');
        const portfolioHeaderGroup = document.querySelector('.portfolio-header > div');
        const btnToggleNews = document.getElementById('btnToggleNews');
        const newsHeaderGroup = newsSidebar ? newsSidebar.querySelector('.section-title').nextElementSibling : null;

        if (!mainApp || !portfolioSection || !historyHeader || !newsSidebar || !mainLayout || !themeSwitchOuter || !btnTogglePortfolio || !portfolioHeaderGroup) return;

        if (isMobile) {
            // 1. 뉴스 영역을 포트폴리오와 히스토리 사이로 이동
            if (newsSidebar.parentElement !== mainApp) {
                mainApp.insertBefore(newsSidebar, historyHeader);
            }
            // 2. 포트폴리오 접기/펼치기 버튼을 원래 위치로 유지 (이전 이동 복구)
            if (btnTogglePortfolio.parentElement !== portfolioHeaderGroup) {
                portfolioHeaderGroup.appendChild(btnTogglePortfolio);
            }
            // 3. 뉴스 접기/펼치기 버튼을 테마 변경 컨테이너 옆(우측)으로 이동
            if (btnToggleNews && btnToggleNews.parentElement !== themeSwitchOuter) {
                themeSwitchOuter.appendChild(btnToggleNews);
                btnToggleNews.style.display = 'inline-block';
            } else if (btnToggleNews) {
                btnToggleNews.style.display = 'inline-block';
            }
        } else {
            // 데스크탑 레이아웃 원복
            if (newsSidebar.parentElement !== mainLayout) {
                mainLayout.appendChild(newsSidebar);
            }
            if (btnTogglePortfolio.parentElement !== portfolioHeaderGroup) {
                portfolioHeaderGroup.appendChild(btnTogglePortfolio);
            }
            if (btnToggleNews && newsHeaderGroup && btnToggleNews.parentElement !== newsHeaderGroup) {
                newsHeaderGroup.insertBefore(btnToggleNews, newsHeaderGroup.firstChild);
            }
            if (btnToggleNews) btnToggleNews.style.display = 'none';
            const newsList = document.getElementById('newsList');
            if (newsList && newsList.classList.contains('news-expanded')) {
                newsList.classList.remove('news-expanded');
                if (btnToggleNews) btnToggleNews.innerText = '펼치기 ▼';
            }
        }
    }

    window.addEventListener('resize', applyMobileResponsiveLayout);
    applyMobileResponsiveLayout(); // 초기 로드 시 1회 실행

    // ⭐️ 모바일 환경을 위한 설정 메뉴(톱니바퀴) 터치 토글 로직
    const headerActionGroup = document.querySelector('.header-action-group');
    const headerActionIcon = document.querySelector('.header-action-icon');
    if (headerActionGroup && headerActionIcon) {
        headerActionIcon.addEventListener('click', (e) => {
            e.stopPropagation(); // 클릭 이벤트가 문서 전체로 전파되는 것 방지
            headerActionGroup.classList.toggle('active');
        });
        
        // ⭐️ 메뉴 안의 동작 버튼을 클릭했을 때도 메뉴가 닫히며 톱니바퀴로 복귀하도록 처리
        const actionBtns = headerActionGroup.querySelectorAll('.header-action-btn');
        actionBtns.forEach(btn => btn.addEventListener('click', () => headerActionGroup.classList.remove('active')));
        
        // 화면의 다른 빈 공간을 터치(클릭)하면 열려있는 메뉴 닫기
        document.addEventListener('click', (e) => {
            if (!headerActionGroup.contains(e.target)) {
                headerActionGroup.classList.remove('active');
            }
        });
    }

    // ⭐️ 모바일 뉴스 영역 접기/펼치기 버튼 이벤트
    const btnToggleNews = document.getElementById('btnToggleNews');
    if (btnToggleNews) {
        btnToggleNews.addEventListener('click', () => {
            const newsList = document.getElementById('newsList');
            if (!newsList) return;
            const isExpanded = newsList.classList.toggle('news-expanded');
            btnToggleNews.innerText = isExpanded ? '접기 ▲' : '펼치기 ▼';
            if (!isExpanded) {
                newsList.scrollLeft = 0; // 가로 스크롤 원위치
            }
        });
    }

    // 대시보드 접기/펴기 버튼 이벤트 연결
    const btnTogglePortfolio = document.getElementById('btnTogglePortfolio');
    if (btnTogglePortfolio) {
        btnTogglePortfolio.addEventListener('click', () => {
            isDashboardCollapsed = !isDashboardCollapsed;
            updatePortfolioSummary();
            
            // ⭐️ 사용자 설정에 상태 저장
            userPreferences.isDashboardCollapsed = isDashboardCollapsed;
            savePreferences();
        });
    }

    // 청산 종목 보기 토글 버튼 이벤트 연결
    const btnToggleClosed = document.getElementById('btnToggleClosed');
    if (btnToggleClosed) {
        // 초기 버튼 상태 동기화
        btnToggleClosed.innerText = showClosedPositions ? '청산 종목 숨기기' : '청산 종목 보기';
        btnToggleClosed.style.backgroundColor = showClosedPositions ? 'var(--primary-color)' : 'transparent';
        btnToggleClosed.style.color = showClosedPositions ? '#fff' : 'var(--primary-color)';

        btnToggleClosed.addEventListener('click', () => {
            showClosedPositions = !showClosedPositions;
            btnToggleClosed.innerText = showClosedPositions ? '청산 종목 숨기기' : '청산 종목 보기';
            btnToggleClosed.style.backgroundColor = showClosedPositions ? 'var(--primary-color)' : 'transparent';
            btnToggleClosed.style.color = showClosedPositions ? '#fff' : 'var(--primary-color)';
            updatePortfolioSummary();
            
            // ⭐️ 사용자 설정에 상태 저장 (정상 위치로 복구)
            userPreferences.showClosedPositions = showClosedPositions;
            savePreferences();
        });
    }

    // ⭐️ 현재가 보기 토글 버튼 이벤트 연결
    const btnToggleCurrentPrice = document.getElementById('btnToggleCurrentPrice');
    if (btnToggleCurrentPrice) {
        btnToggleCurrentPrice.innerText = showCurrentPrice ? '현재가 숨기기' : '현재가 보기';
        btnToggleCurrentPrice.style.backgroundColor = showCurrentPrice ? 'var(--primary-color)' : 'transparent';
        btnToggleCurrentPrice.style.color = showCurrentPrice ? '#fff' : 'var(--primary-color)';

        btnToggleCurrentPrice.addEventListener('click', () => {
            showCurrentPrice = !showCurrentPrice;
            btnToggleCurrentPrice.innerText = showCurrentPrice ? '현재가 숨기기' : '현재가 보기';
            btnToggleCurrentPrice.style.backgroundColor = showCurrentPrice ? 'var(--primary-color)' : 'transparent';
            btnToggleCurrentPrice.style.color = showCurrentPrice ? '#fff' : 'var(--primary-color)';
            userPreferences.showCurrentPrice = showCurrentPrice;
            savePreferences();
            updatePortfolioSummary(); // UI 리렌더링 및 현재가 fetch 트리거
        });
    }

    // ⭐️ 대시보드 증권사 필터 이벤트 연결
    const dashboardBrokerFilter = document.getElementById('dashboardBrokerFilter');
    if (dashboardBrokerFilter) {
        dashboardBrokerFilter.addEventListener('change', (e) => {
            currentDashboardBroker = e.target.value;
            updatePortfolioSummary();
        });
    }

    // ⭐️ 대시보드 투자 분류 필터 이벤트 연결
    const dashboardAccountFilter = document.getElementById('dashboardAccountFilter');
    if (dashboardAccountFilter) {
        dashboardAccountFilter.addEventListener('change', (e) => {
            currentDashboardAccount = e.target.value;
            updatePortfolioSummary();
        });
    }

    // 히스토리 청산 종목 숨기기/보기 토글 버튼 이벤트 연결
    const btnToggleHistoryClosed = document.getElementById('btnToggleHistoryClosed');
    if (btnToggleHistoryClosed) {
        // 초기 버튼 상태 동기화
        btnToggleHistoryClosed.innerText = showHistoryClosedPositions ? '청산 종목 숨기기' : '청산 종목 보기';
        btnToggleHistoryClosed.style.backgroundColor = showHistoryClosedPositions ? 'transparent' : 'var(--primary-color)';
        btnToggleHistoryClosed.style.color = showHistoryClosedPositions ? 'var(--primary-color)' : '#fff';

        btnToggleHistoryClosed.addEventListener('click', () => {
            showHistoryClosedPositions = !showHistoryClosedPositions;
            btnToggleHistoryClosed.innerText = showHistoryClosedPositions ? '청산 종목 숨기기' : '청산 종목 보기';
            btnToggleHistoryClosed.style.backgroundColor = showHistoryClosedPositions ? 'transparent' : 'var(--primary-color)';
            btnToggleHistoryClosed.style.color = showHistoryClosedPositions ? 'var(--primary-color)' : '#fff';
            displayEntries(true);

            // 필터 변경 시 히스토리 상단으로 부드럽게 스크롤
            window.scrollToFilterBox();
            
            // ⭐️ 사용자 설정에 상태 저장 (추가)
            userPreferences.showHistoryClosedPositions = showHistoryClosedPositions;
            savePreferences();
        });
    }

    // [3안] 필터 타입(유형) 선택 드롭다운 이벤트
    const filterTypeSelect = document.getElementById('filterTypeSelect');
    const clearTypeFilterBtn = document.getElementById('clearTypeFilterBtn');
    if (filterTypeSelect) {
        filterTypeSelect.addEventListener('change', (e) => {
            currentFilterType = e.target.value === 'all' ? null : e.target.value;
            displayEntries(true);

            // 필터 변경 시 히스토리 상단으로 부드럽게 스크롤
            window.scrollToFilterBox();
            
            if (clearTypeFilterBtn) {
                clearTypeFilterBtn.style.display = currentFilterType ? 'inline-block' : 'none';
            }
        });
    }
    if (clearTypeFilterBtn) {
        clearTypeFilterBtn.addEventListener('click', () => {
            if (filterTypeSelect) {
                filterTypeSelect.value = 'all';
                currentFilterType = null;
                displayEntries(true);
                window.scrollToFilterBox();
            }
        });
    }

    // ⭐️ 로그아웃 버튼 이벤트 연결
    const btnLogout = document.getElementById('btnLogout');
    if (btnLogout) {
        btnLogout.addEventListener('click', async () => {
            if (await customConfirm("로그아웃 하시겠습니까?")) {
                window.location.href = '/logout';
            }
        });
    }
    
    // ⭐️ 자동 로그아웃 연장 팝업 이벤트 연결
    const btnExtendSession = document.getElementById('btnExtendSession');
    const btnLogoutNow = document.getElementById('btnLogoutNow');
    const extensionModal = document.getElementById('sessionExtensionModalOverlay');

    if (btnExtendSession && extensionModal) {
        btnExtendSession.addEventListener('click', async () => {
            try {
                // 백엔드(Flask) 서버의 세션 만료 시간도 동기화하여 갱신
                await fetch('/api/ping', { method: 'POST', headers: { 'ngrok-skip-browser-warning': 'true' }});
            } catch(e) {}
            
            extensionModal.classList.add('closing');
            setTimeout(() => {
                extensionModal.style.display = 'none';
                extensionModal.classList.remove('closing');
                resetInactivityTimers();
            }, 180);
        });
    }
    if (btnLogoutNow) btnLogoutNow.addEventListener('click', () => window.location.href = '/logout');

    // ⭐️ Quill 에디터 초기화
    window.quill = new Quill('#editor-container', {
        theme: 'snow',
        modules: {
            imageResize: {
                displaySize: true // 리사이즈 시 이미지 크기 툴팁 표시
            },
            toolbar: [
                [{ 'header': [1, 2, 3, false] }, { 'size': ['small', false, 'large', 'huge'] }], // 헤더, 글자 크기
                ['bold', 'italic', 'underline', 'strike'],       // 텍스트 강조
                [{ 'color': [] }, { 'background': [] }],         // 글자/배경 색상
                [{ 'align': [] }],                               // 정렬
                [{ 'list': 'ordered'}, { 'list': 'bullet' }],    // 리스트
                ['blockquote', 'code-block'],                    // 인용, 코드 블록
                ['image'],                                       // ⭐️ 이미지 삽입 툴바 버튼 추가
                ['clean']                                        // 서식 초기화
            ]
        },
        placeholder: '현재 시장 상황, 매매 이유, 향후 대응 계획 등을 자유롭게 기록하세요.'
    });

    // ⭐️ 에디터 내 이미지 삽입 커스텀 핸들러 연결 (원본 대신 리사이징 적용)
    window.quill.getModule('toolbar').addHandler('image', function() {
        const input = document.createElement('input');
        input.setAttribute('type', 'file');
        input.setAttribute('accept', 'image/*');
        input.click();
        input.onchange = function() {
            const file = input.files[0];
            if (file) {
                window.resizeAndInsertImageToQuill(file);
            }
        };
    });

    // ⭐️ 붙여넣기 시 외부 텍스트의 글자색/배경색 서식 강제 제거 (테마 색상 자동 적용)
    window.quill.clipboard.addMatcher(Node.ELEMENT_NODE, function(node, delta) {
        delta.ops.forEach(op => {
            if (op.attributes) {
                delete op.attributes.color;
                delete op.attributes.background;
            }
        });
        return delta;
    });

    // ⭐️ 에디터 본문 드래그 앤 드롭 이미지 삽입 지원
    window.quill.root.addEventListener('drop', function(e) {
        if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
            for (let i = 0; i < e.dataTransfer.files.length; i++) {
                const file = e.dataTransfer.files[i];
                if (file.type.startsWith('image/')) {
                    e.preventDefault();
                    e.stopPropagation();
                    window.resizeAndInsertImageToQuill(file);
                    break;
                }
            }
        }
    });

    // ⭐️ 에디터 본문 클립보드 이미지 붙여넣기(Ctrl+V) 직접 연결 (충돌 해결)
    window.quill.root.addEventListener('paste', function(e) {
        if (e.clipboardData && e.clipboardData.items) {
            const items = e.clipboardData.items;
            for (let i = 0; i < items.length; i++) {
                if (items[i].type.indexOf('image') !== -1) {
                    const file = items[i].getAsFile();
                    if (file) {
                        e.preventDefault(); // 기본 붙여넣기(원본 용량) 방지
                        e.stopPropagation();
                        window.resizeAndInsertImageToQuill(file);
                        break;
                    }
                }
            }
        }
    });

    // ⭐️ 일반 첨부파일 이벤트 연결
    const btnAttachFile = document.getElementById('btnAttachFile');
    const generalFileInput = document.getElementById('generalFileInput');
    const attachedFileNameDisplay = document.getElementById('attachedFileNameDisplay');
    const btnRemoveAttachedFile = document.getElementById('btnRemoveAttachedFile');

    if (btnAttachFile && generalFileInput) {
        btnAttachFile.addEventListener('click', () => generalFileInput.click());
        generalFileInput.addEventListener('change', function(e) {
            const file = e.target.files[0];
            if (file) {
                pendingUploadFile = file;
                currentAttachedFileName = file.name;
                attachedFileNameDisplay.innerText = file.name;
                btnRemoveAttachedFile.style.display = 'inline-block';
            }
        });
    }

    if (btnRemoveAttachedFile) {
        btnRemoveAttachedFile.addEventListener('click', () => {
            pendingUploadFile = null;
            currentAttachedFile = null;
            currentAttachedFileName = '';
            if (generalFileInput) generalFileInput.value = '';
            attachedFileNameDisplay.innerText = '선택된 파일 없음';
            btnRemoveAttachedFile.style.display = 'none';
        });
    }

    loadDataFromLocal();
});

async function loadDataFromLocal() {
    try {
        // ⭐️ 사용자 환경설정 먼저 불러오기
        try {
            // ⭐️ 사용자 정보 및 관리자 여부 확인
            try {
                const meRes = await fetch('/api/me', { headers: { 'ngrok-skip-browser-warning': 'true' }});
                if (meRes.ok) {
                    const meData = await meRes.json();
                    if (meData.username) {
                        const userDisplay = document.getElementById('loggedInUserDisplay');
                        if (userDisplay) {
                            userDisplay.innerHTML = `<span style="font-size:12px;">👤</span> ${meData.username}`;
                            userDisplay.style.display = 'flex';
                        }
                        if (meData.username === 'batmi') {
                            const btnAdmin = document.getElementById('btnAdmin');
                            if (btnAdmin) {
                                btnAdmin.style.display = 'flex';
                                if (meData.pending_count > 0) {
                                    btnAdmin.style.position = 'relative';
                                    btnAdmin.innerHTML += `<span class="admin-notification-badge">${meData.pending_count}</span>`;
                                    setTimeout(async () => { await customAlert(`가입 승인 대기 중인 신규 사용자가 ${meData.pending_count}명 있습니다.\n상단 어드민 메뉴에서 확인해주세요.`); }, 500);
                                }
                            }
                        }
                    }
                }
            } catch(e) { console.warn("사용자 정보 로드 실패"); }

            const prefRes = await fetch('/api/preferences', {
                headers: { 'ngrok-skip-browser-warning': 'true' }
            });
            if (prefRes.ok) {
                userPreferences = await prefRes.json();
                
                // ⭐️ DB에서 불러온 환경설정을 UI(청산 종목 토글, 접기/펴기 등)에 반영
                if (typeof userPreferences.isDashboardCollapsed !== 'undefined') {
                    isDashboardCollapsed = userPreferences.isDashboardCollapsed;
                }
                if (typeof userPreferences.showClosedPositions !== 'undefined') {
                    showClosedPositions = userPreferences.showClosedPositions;
                    const btn1 = document.getElementById('btnToggleClosed');
                    if (btn1) {
                        btn1.innerText = showClosedPositions ? '청산 종목 숨기기' : '청산 종목 보기';
                        btn1.style.backgroundColor = showClosedPositions ? 'var(--primary-color)' : 'transparent';
                        btn1.style.color = showClosedPositions ? '#fff' : 'var(--primary-color)';
                    }
                }
                if (typeof userPreferences.showHistoryClosedPositions !== 'undefined') {
                    showHistoryClosedPositions = userPreferences.showHistoryClosedPositions;
                    const btn2 = document.getElementById('btnToggleHistoryClosed');
                    if (btn2) {
                        btn2.innerText = showHistoryClosedPositions ? '청산 종목 숨기기' : '청산 종목 보기';
                        btn2.style.backgroundColor = showHistoryClosedPositions ? 'transparent' : 'var(--primary-color)';
                        btn2.style.color = showHistoryClosedPositions ? 'var(--primary-color)' : '#fff';
                    }
                }
                if (typeof userPreferences.showCurrentPrice !== 'undefined') {
                    showCurrentPrice = userPreferences.showCurrentPrice;
                    const btnCP = document.getElementById('btnToggleCurrentPrice');
                    if (btnCP) {
                        btnCP.innerText = showCurrentPrice ? '현재가 숨기기' : '현재가 보기';
                        btnCP.style.backgroundColor = showCurrentPrice ? 'var(--primary-color)' : 'transparent';
                        btnCP.style.color = showCurrentPrice ? '#fff' : 'var(--primary-color)';
                    }
                }
            }
        } catch (e) {
            console.warn("환경설정 로드 실패:", e);
        }
        
        const response = await fetch('/api/data', {
            headers: { 'ngrok-skip-browser-warning': 'true' }
        });
        cloudEntries = await response.json();
        displayEntries();
        
        fetchRealtimeNews();
        if (newsInterval) clearInterval(newsInterval);
        newsInterval = setInterval(fetchRealtimeNews, 300000);
    } catch (err) {
        console.error(err);
        await customAlert("로컬 데이터를 불러오지 못했습니다.\n서버가 실행 중인지 확인하세요.");
    }
}

// ⭐️ 환경설정(사용자가 정렬한 카드 순서)을 DB에 저장
async function savePreferences() {
    try {
        await fetch('/api/preferences', {
            method: 'POST',
            headers: { 
                'Content-Type': 'application/json',
                'ngrok-skip-browser-warning': 'true'
            },
            body: JSON.stringify(userPreferences)
        });
    } catch (err) {
        console.error("환경설정 저장 실패:", err);
    }
}

async function fetchRealtimeNews() {
    const newsListEl = document.getElementById('newsList');
    if (!newsListEl) return;
    
    const portfolioQty = {};
    cloudEntries.forEach(entry => {
        if (entry.type === 'trade' && entry.stockName) {
            if (entry.tradeType === '매수' || entry.tradeType === '매도') {
                if (portfolioQty[entry.stockName] === undefined) portfolioQty[entry.stockName] = 0;
                if (entry.tradeType === '매수') portfolioQty[entry.stockName] += (Number(entry.quantity) || 0);
                else if (entry.tradeType === '매도') portfolioQty[entry.stockName] -= (Number(entry.quantity) || 0);
            }
        }
    });

    const validStocks = new Set();
    for (const entry of cloudEntries) {
        const stock = entry.stockName;
        if (!stock) continue;
        if (portfolioQty[stock] !== undefined && portfolioQty[stock] <= 0) continue;
        
        validStocks.add(stock);
        if (validStocks.size >= 5) break;
    }
    const stocksToFetch = Array.from(validStocks);
    
    try {
        newsListEl.innerHTML = '<div style="text-align:center; padding: 20px;">🔄 실시간 뉴스를 불러오는 중...</div>';
        const response = await fetch('/api/news', {
            method: 'POST',
            headers: { 
                'Content-Type': 'application/json',
                'ngrok-skip-browser-warning': 'true'
            },
            body: JSON.stringify({ stocks: stocksToFetch })
        });
        const newsData = await response.json();
        
        if (newsData.length === 0) {
            newsListEl.innerHTML = '<div style="text-align:center; padding: 20px;">관련 뉴스가 없습니다.</div>';
            return;
        }
        
        newsData.sort((a, b) => new Date(b.pubDate) - new Date(a.pubDate));

        newsListEl.innerHTML = '';
        newsData.forEach(news => {
            const dateObj = new Date(news.pubDate);
            const dateStr = !isNaN(dateObj) ? (dateObj.getMonth()+1) + '/' + dateObj.getDate() + ' ' + String(dateObj.getHours()).padStart(2,'0') + ':' + String(dateObj.getMinutes()).padStart(2,'0') : news.pubDate;
            
            newsListEl.innerHTML += `
                <div class="news-item">
                    <a href="${news.link}" target="_blank">${news.title}</a>
                    <div class="news-meta">
                        <span class="news-stock-tag">${news.stock}</span><span>${dateStr}</span>
                    </div>
                </div>`;
        });
    } catch (err) {
        console.error("뉴스 로딩 실패:", err);
            newsListEl.innerHTML = '<div style="text-align:center; padding: 20px; color:var(--danger-color);">뉴스를 불러오지 못했습니다.</div>';
    }
}

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

btnFab.addEventListener('click', () => {
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

btnCloseForm.addEventListener('click', resetAndCloseForm);
const btnCancelForm = document.getElementById('btnCancelForm');
if (btnCancelForm) btnCancelForm.addEventListener('click', resetAndCloseForm);

// ⭐️ Esc 키로 모달 닫기
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        const customModal = document.getElementById('customModalOverlay');
        if (customModal && customModal.style.display === 'flex') return;
        if (formModalOverlay.style.display === 'flex') {
            const openLists = document.querySelectorAll('.autocomplete-list[style*="display: block"]');
            if (openLists.length > 0) return; // 드롭다운이 열려있을 땐 모달 닫기 방지
            resetAndCloseForm();
        } else if (typeof passwordModalOverlay !== 'undefined' && passwordModalOverlay && passwordModalOverlay.style.display === 'flex') {
            document.getElementById('btnClosePasswordModal').click();
        } else if (typeof adminModalOverlay !== 'undefined' && adminModalOverlay && adminModalOverlay.style.display === 'flex') {
            document.getElementById('btnCloseAdminModal').click();
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
            if (val) {
                const regex = new RegExp(`(${val})`, 'gi');
                item.innerHTML = opt.replace(regex, "<span style='color:var(--danger-color); font-weight:var(--fw-bold, bold);'>$1</span>");
            } else {
                item.innerText = opt;
            }
            // ⭐️ 포커스 유실 방지 (첫 클릭 무시 및 한글 입력기 충돌 해결)
            item.addEventListener('mousedown', function(ev) {
                ev.preventDefault(); 
            });
            // ⭐️ 한 번의 마우스 클릭만으로 즉시 자동 입력되도록 동작 분리
            item.addEventListener('click', function(ev) {
                ev.stopPropagation();
                input.value = opt;
                lastVal = opt;
                list.style.display = 'none';
                input.dispatchEvent(new Event('input'));
                input.dispatchEvent(new CustomEvent('itemSelected', { detail: { value: opt } })); // 자동완성 클릭 시 명시적 이벤트 발생
            });
            list.appendChild(item);
        });
    }

    input.addEventListener('input', triggerInput);
    input.addEventListener('focus', triggerInput);
    input.addEventListener('click', triggerInput);

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
                    headers: { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': 'true' },
                    body: JSON.stringify({ password: pw })
                });
                const data = await res.json();
                if (res.ok && data.status === 'success') {
                    await customAlert("계정이 성공적으로 삭제되었습니다. 이용해 주셔서 감사합니다.");
                    window.location.href = '/login';
                } else {
                    submitBtn.innerText = origText;
                    submitBtn.disabled = false;
                    await customAlert("탈퇴 실패: " + (data.error || "알 수 없는 오류"));
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

let adminUsersData = [];
let currentAdminSort = { key: 'created_at', asc: false };

window.loadAdminUsers = async function() {
    if (!adminUserList) return;
    adminUserList.innerHTML = '<tr><td colspan="5" style="text-align:center; padding: 20px;">불러오는 중...</td></tr>';
    try {
        const res = await fetch('/api/admin/users', { headers: { 'ngrok-skip-browser-warning': 'true' }});
        if (!res.ok) throw new Error("권한이 없습니다.");
        adminUsersData = await res.json();
        renderAdminUsers();
    } catch(e) {
        adminUserList.innerHTML = '<tr><td colspan="5" style="text-align:center; padding: 20px; color: var(--danger-color);">데이터를 불러오지 못했습니다.</td></tr>';
    }
};

window.sortAdminUsers = function(key) {
    if (currentAdminSort.key === key) {
        currentAdminSort.asc = !currentAdminSort.asc;
    } else {
        currentAdminSort.key = key;
        currentAdminSort.asc = false; // 새로운 정렬 기준 선택 시 기본 내림차순(최신순/많은순)
        if (key === 'username') currentAdminSort.asc = true; // 이름은 오름차순(가나다순)이 기본
    }
    renderAdminUsers();
};

window.renderAdminUsers = function() {
    if (!adminUserList) return;

    // 정렬 아이콘 업데이트
    ['username', 'created_at', 'last_login_at', 'entry_count'].forEach(key => {
        const iconEl = document.getElementById('sortIcon_' + key);
        if (iconEl) {
            iconEl.innerText = (currentAdminSort.key === key) ? (currentAdminSort.asc ? '▲' : '▼') : '↕';
            iconEl.style.color = (currentAdminSort.key === key) ? 'var(--primary-color)' : 'var(--text-muted-color)';
        }
    });

    // 관리자와 일반 사용자 분리
    const adminUser = adminUsersData.find(u => u.username === 'batmi');
    const regularUsers = adminUsersData.filter(u => u.username !== 'batmi');

    // 데이터 정렬 (일반 사용자만)
    const sortedData = [...regularUsers].sort((a, b) => {
        let valA = a[currentAdminSort.key];
        let valB = b[currentAdminSort.key];

        if (currentAdminSort.key === 'entry_count') {
            valA = parseInt(valA) || 0;
            valB = parseInt(valB) || 0;
        } else if (currentAdminSort.key === 'created_at' || currentAdminSort.key === 'last_login_at') {
            valA = valA ? new Date(valA.replace(' ', 'T')).getTime() : 0;
            valB = valB ? new Date(valB.replace(' ', 'T')).getTime() : 0;
        } else {
            valA = (valA || '').toString().toLowerCase();
            valB = (valB || '').toString().toLowerCase();
        }

        if (valA < valB) return currentAdminSort.asc ? -1 : 1;
        if (valA > valB) return currentAdminSort.asc ? 1 : -1;
        return 0;
    });

    adminUserList.innerHTML = '';

    // 최고 관리자 먼저 렌더링 (별도 배경색 및 굵은 하단 테두리로 시각적 분리)
    if (adminUser) {
        const createdStr = adminUser.created_at || '-';
        const lastLoginStr = adminUser.last_login_at || '-';
        const tr = document.createElement('tr');
        tr.style.backgroundColor = 'var(--bg-color)';
        tr.style.borderBottom = '2px solid var(--primary-color)';
        tr.innerHTML = `
            <td style="padding: 10px; font-weight: bold; color: var(--primary-color); white-space: nowrap;">${adminUser.username} 👑 <span style="font-size:11px; font-weight:normal; color:var(--text-muted-color);">(최고 관리자)</span></td>
            <td style="padding: 10px; color: var(--text-muted-color); font-size: 12px; white-space: nowrap;">${createdStr}</td>
            <td style="padding: 10px; color: var(--text-muted-color); font-size: 12px; white-space: nowrap;">${lastLoginStr}</td>
            <td style="padding: 10px; font-weight: bold; white-space: nowrap;">${adminUser.entry_count}건</td>
            <td style="padding: 10px; text-align: right; white-space: nowrap;">
                <button onclick="resetUserPassword('${adminUser.username}')" style="background: var(--warning-color); color: white; border: none; padding: 4px 8px; border-radius: 4px; font-size: 11px; cursor: pointer; width: auto; margin: 0; box-shadow: none;">비번 초기화</button>
            </td>
        `;
        adminUserList.appendChild(tr);
    }

    // 일반 사용자 목록 렌더링
    if (sortedData.length > 0) {
        sortedData.forEach(u => {
            const isAllowed = u.is_allowed;
            const allowBtnText = isAllowed ? '제한' : '허용';
            const allowBtnBg = isAllowed ? 'var(--neutral-color)' : 'var(--success-color)';
            const createdStr = u.created_at || '-';
            const lastLoginStr = u.last_login_at || '-';
            
            const tr = document.createElement('tr');
            tr.style.borderBottom = '1px solid var(--border-light-color)';
            tr.innerHTML = `
                <td style="padding: 10px; font-weight: bold; color: var(--text-strong-color); white-space: nowrap;">${u.username}</td>
                <td style="padding: 10px; color: var(--text-muted-color); font-size: 12px; white-space: nowrap;">${createdStr}</td>
                <td style="padding: 10px; color: var(--text-muted-color); font-size: 12px; white-space: nowrap;">${lastLoginStr}</td>
                <td style="padding: 10px; white-space: nowrap;">${u.entry_count}건</td>
                <td style="padding: 10px; text-align: right; white-space: nowrap;">
                    <button onclick="toggleUserAllow('${u.username}')" style="background: ${allowBtnBg}; color: white; border: none; padding: 4px 8px; border-radius: 4px; font-size: 11px; cursor: pointer; width: auto; margin: 0 4px 0 0; box-shadow: none;">${allowBtnText}</button>
                    <button onclick="resetUserPassword('${u.username}')" style="background: var(--warning-color); color: white; border: none; padding: 4px 8px; border-radius: 4px; font-size: 11px; cursor: pointer; width: auto; margin: 0 4px 0 0; box-shadow: none;">비번 초기화</button>
                    <button onclick="deleteUserAccount('${u.username}')" style="background: var(--danger-color); color: white; border: none; padding: 4px 8px; border-radius: 4px; font-size: 11px; cursor: pointer; width: auto; margin: 0; box-shadow: none;">삭제</button>
                </td>
            `;
            adminUserList.appendChild(tr);
        });
    } else {
        const tr = document.createElement('tr');
        tr.innerHTML = '<td colspan="5" style="padding: 20px; text-align: center; color: var(--text-muted-color);">가입한 일반 사용자가 없습니다.</td>';
        adminUserList.appendChild(tr);
    }
};

window.toggleUserAllow = async function(username) {
    try {
        const res = await fetch(`/api/admin/users/${username}/toggle_allow`, { method: 'POST', headers: { 'ngrok-skip-browser-warning': 'true' }});
        if (res.ok) {
            loadAdminUsers();
        } else {
            const data = await res.json();
                await customAlert(data.error || "상태 변경에 실패했습니다.");
        }
        } catch(e) { await customAlert("통신 오류가 발생했습니다."); }
};

window.resetUserPassword = async function(username) {
    if (await customConfirm(`'${username}' 사용자의 비밀번호를 안전한 무작위 문자열로 초기화하시겠습니까?`)) {
        try {
            const res = await fetch(`/api/admin/users/${username}/reset_password`, { method: 'POST', headers: { 'ngrok-skip-browser-warning': 'true' }});
            if (res.ok) {
                const data = await res.json();
                await customAlert(`'${username}' 계정의 비밀번호가 [ ${data.new_password} ] 로 초기화되었습니다.\n사용자에게 이 임시 비밀번호를 전달해 주세요.`);
            } else {
                await customAlert("초기화에 실패했습니다.");
            }
        } catch(e) { await customAlert("통신 오류가 발생했습니다."); }
    }
};

window.deleteUserAccount = async function(username) {
    const confirmName = await customPrompt(`경고: '${username}' 사용자와 관련된 모든 기록과 첨부파일이 영구적으로 삭제됩니다.\n\n계속하시려면 삭제할 아이디('${username}')를 아래에 정확히 입력해주세요.`, '입력');
    if (confirmName === username) {
        try {
            const res = await fetch(`/api/admin/users/${username}`, { method: 'DELETE', headers: { 'ngrok-skip-browser-warning': 'true' }});
            if (res.ok) {
                await customAlert(`'${username}' 계정이 삭제되었습니다.`);
                loadAdminUsers();
            } else {
                await customAlert("삭제에 실패했습니다.");
            }
        } catch(e) { await customAlert("통신 오류가 발생했습니다."); }
    } else if (confirmName !== null) {
        await customAlert("입력한 아이디가 일치하지 않아 삭제가 취소되었습니다.");
    }
};

const defaultBrokers = ["키움증권", "미래에셋증권", "NH투자증권", "한국투자증권", "삼성증권", "토스증권"];
function getStockOptions() {
    const historyStocks = cloudEntries.map(entry => entry.stockName).filter(Boolean);
    return [...new Set([...defaultStocks, ...historyStocks])].sort();
}
function getBrokerOptions() {
    const historyBrokers = cloudEntries.map(entry => entry.brokerAccount).filter(Boolean);
    return [...new Set([...defaultBrokers, ...historyBrokers])].sort();
}

setupAutocomplete('stockName', 'stockNameList', getStockOptions);
setupAutocomplete('brokerAccount', 'brokerAccountList', getBrokerOptions);

// ⭐️ 종목명 자동완성 시 종목코드 자동 입력
document.getElementById('stockName').addEventListener('itemSelected', function(e) {
    const val = e.detail ? e.detail.value : this.value;
    const recentEntry = cloudEntries.find(entry => entry.stockName === val && entry.stockCode);
    if (recentEntry) {
        document.getElementById('stockCode').value = recentEntry.stockCode;
    }
});

function resetAndCloseForm() {
    formModalOverlay.classList.add('closing');
    setTimeout(() => {
        formModalOverlay.style.display = 'none';
        formModalOverlay.classList.remove('closing');
        document.body.style.overflow = ''; // ⭐️ 모달 닫힘 시 배경 스크롤 복구
        
        journalForm.reset();
        currentTags = [];
        renderTags();
        calcTotalAmount();
        
        currentAttachedFile = null;
        currentAttachedFileName = '';
        pendingUploadFile = null;
        if (document.getElementById('generalFileInput')) document.getElementById('generalFileInput').value = '';
        if (document.getElementById('attachedFileNameDisplay')) document.getElementById('attachedFileNameDisplay').innerText = '선택된 파일 없음';
        if (document.getElementById('btnRemoveAttachedFile')) document.getElementById('btnRemoveAttachedFile').style.display = 'none';

        if (window.quill) window.quill.setContents([]); // 에디터 초기화
        editingEntryId = null;
        submitBtn.innerText = "기록";
        const tradeRadio = document.querySelector('input[name="recordType"][value="trade"]');
        if(tradeRadio) { tradeRadio.checked = true; toggleFormUI('trade'); }
        const resetNow = new Date(); resetNow.setMinutes(resetNow.getMinutes() - resetNow.getTimezoneOffset());
        if (window.tradeDatePicker) {
            window.tradeDatePicker.setDate(resetNow.toISOString().slice(0,16));
        } else {
            document.getElementById('tradeDate').value = resetNow.toISOString().slice(0,16);
        }
    }, 180); // CSS 페이드아웃 애니메이션 시간과 동기화
}

// ⭐️ Flatpickr 초기화 (날짜 및 시간 선택기)
window.tradeDatePicker = flatpickr("#tradeDate", {
    enableTime: true,
    dateFormat: "Y-m-d\\TH:i",
    locale: "ko",
    time_24hr: false,
    onReady: function(selectedDates, dateStr, instance) {
        const nowBtn = document.createElement('button');
        nowBtn.type = 'button';
        nowBtn.textContent = '🕒';
        nowBtn.title = '현재 시간으로 설정';
        nowBtn.style.cssText = 'background: transparent; border: none; color: var(--primary-color); cursor: pointer; font-weight: bold; padding: 0 10px; width: auto; margin: 0; box-shadow: none; height: auto; outline: none; display: flex; align-items: center;';
        
        nowBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            const currentNow = new Date();
            currentNow.setMinutes(currentNow.getMinutes() - currentNow.getTimezoneOffset());
            instance.setDate(currentNow.toISOString().slice(0,16), true);
        });
        
        if (instance.timeContainer) {
            instance.timeContainer.appendChild(nowBtn);
        }
    }
});

const now = new Date();
now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
window.tradeDatePicker.setDate(now.toISOString().slice(0,16));

// ⭐️ 총 금액 자동 계산 로직
function calcTotalAmount() {
    const recordType = document.querySelector('input[name="recordType"]:checked');
    const totalWrapper = document.getElementById('totalAmountWrapper');
    if (!recordType || recordType.value !== 'trade') {
        totalWrapper.style.display = 'none'; return;
    }
    const price = Number(document.getElementById('price').value) || 0;
    const qty = Number(document.getElementById('quantity').value) || 0;
    if (price > 0 && qty > 0) {
        totalWrapper.style.display = 'block';
            document.getElementById('totalAmountDisplay').innerText = `총 매매 금액: ${(price * qty).toLocaleString()}원`;
    } else { totalWrapper.style.display = 'none'; }
}
document.getElementById('price').addEventListener('input', calcTotalAmount);
document.getElementById('quantity').addEventListener('input', calcTotalAmount);

// ⭐️ 기록 유형(매매/메모)에 따른 폼 UI 전환 함수
function toggleFormUI(recordType) {
    const isTrade = recordType === 'trade';
    document.getElementById('tradeRow0').style.display = isTrade ? 'flex' : 'none';
    document.getElementById('tradeRow1').style.display = isTrade ? 'flex' : 'none';
    document.getElementById('tradeRow2').style.display = isTrade ? 'flex' : 'none';
    document.getElementById('memoTitleGroup').style.display = isTrade ? 'none' : 'block';
    document.getElementById('brokerAccountGroup').style.display = isTrade ? 'block' : 'none';
    
    document.getElementById('stockName').required = isTrade;
    document.getElementById('stockCode').required = isTrade;
    
    const accountNameEl = document.getElementById('accountName');
    if (accountNameEl) accountNameEl.required = isTrade;
    const memoTitleEl = document.getElementById('memoTitle');
    if (memoTitleEl) memoTitleEl.required = !isTrade;
    
    document.getElementById('thoughtsLabel').innerText = isTrade ? '생각의 흐름 / 계획' : '메모 내용';
    calcTotalAmount();
}

const typeRadios = document.querySelectorAll('input[name="recordType"]');
typeRadios.forEach(radio => {
    radio.addEventListener('change', function() {
        toggleFormUI(this.value);
    });
});

// ⭐️ 해시태그 입력 로직
function renderTags() {
    const tagList = document.getElementById('tagList');
    tagList.innerHTML = '';
    currentTags.forEach((tag, index) => {
        const badge = document.createElement('span');
        badge.className = 'tag-badge';
        badge.innerHTML = `#${tag} <span class="remove-tag" onclick="removeTag(${index})">&times;</span>`;
        tagList.appendChild(badge);
    });
}
window.removeTag = function(index) { currentTags.splice(index, 1); renderTags(); };
document.getElementById('tagInput').addEventListener('keydown', function(e) {
    if (e.key === 'Enter' || e.key === ',' || e.key === ' ') {
        e.preventDefault();
        let val = this.value.trim().replace(/^#+/, '').replace(/,/g, '');
        if (val && !currentTags.includes(val)) {
            currentTags.push(val);
            renderTags();
        }
        this.value = '';
    }
});

// ⭐️ 에디터 내부에 이미지를 압축하여 삽입하는 함수
window.resizeAndInsertImageToQuill = function(file) {
    // ⭐️ 비동기 파일 읽기가 시작되기 전에 현재 커서 위치를 미리 캡처 (상단으로 튕기는 현상 방지)
    window.quill.focus();
    const range = window.quill.getSelection();
    const insertIndex = range ? range.index : window.quill.getLength();

    const reader = new FileReader();
    reader.onload = function(event) {
        const img = new Image();
        img.onload = function() {
            const canvas = document.createElement('canvas');
            const MAX_WIDTH = 1200; // 본문 삽입용 최대 해상도 제한
            let width = img.width;
            let height = img.height;
            if (width > MAX_WIDTH) {
                height = height * (MAX_WIDTH / width);
                width = MAX_WIDTH;
            }
            canvas.width = width;
            canvas.height = height;
            const ctx = canvas.getContext('2d');
            ctx.drawImage(img, 0, 0, width, height);
            
            // jpeg 포맷으로 85% 최적화 압축 (Base64)
            const dataUrl = canvas.toDataURL('image/jpeg', 0.85);
            
            window.quill.insertEmbed(insertIndex, 'image', dataUrl);
            window.quill.setSelection(insertIndex + 1);
            window.quill.focus(); // ⭐️ 포커스 명시적 유지
            
            // ⭐️ 이미지가 DOM에 렌더링된 후, 삽입된 커서 위치로 부드럽게 스크롤 보정
            setTimeout(() => {
                const selection = window.getSelection();
                if (selection && selection.rangeCount > 0) {
                    let targetNode = selection.focusNode;
                    if (targetNode && targetNode.nodeType === Node.TEXT_NODE) {
                        targetNode = targetNode.parentNode;
                    }
                    if (targetNode && targetNode.scrollIntoView) {
                        targetNode.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    }
                }
            }, 150);
        };
        img.src = event.target.result;
    };
    reader.readAsDataURL(file);
};

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

    const recordType = document.querySelector('input[name="recordType"]:checked').value;
    const stockName = document.getElementById('stockName').value;
    const stockCode = document.getElementById('stockCode').value;
    const brokerAccount = document.getElementById('brokerAccount').value;
    const tradeDateRaw = document.getElementById('tradeDate').value;
    
    // ⭐️ 에디터에서 작성한 내용 가져오기 및 필수 입력 검증
    const thoughtsHTML = window.quill.root.innerHTML;
    const thoughtsText = window.quill.getText().trim();
    if (!thoughtsText && !thoughtsHTML.includes('<img')) {
        await customAlert("내용을 입력해주세요."); return;
    }
    const thoughts = thoughtsHTML === '<p><br></p>' ? '' : thoughtsHTML;
    const date = tradeDateRaw ? new Date(tradeDateRaw).toLocaleString() : new Date().toLocaleString();
    
    // ⭐️ 첨부파일 업로드 처리
    let finalAttachedFile = currentAttachedFile;
    let finalAttachedFileName = currentAttachedFileName;
    
    if (pendingUploadFile) {
        const formData = new FormData();
        formData.append('file', pendingUploadFile);
        try {
            document.body.style.cursor = 'wait';
            submitBtn.innerText = "업로드 중...";
            const uploadRes = await fetch('/api/upload', {
                method: 'POST',
                headers: { 'ngrok-skip-browser-warning': 'true' },
                body: formData
            });
            if (uploadRes.ok) {
                const uploadData = await uploadRes.json();
                finalAttachedFile = uploadData.url;
                finalAttachedFileName = uploadData.filename;
            } else {
                await customAlert("파일 첨부 실패");
                document.body.style.cursor = 'default';
                submitBtn.innerText = editingEntryId ? "수정" : "기록";
                return;
            }
        } catch (e) {
            console.error(e);
            await customAlert("파일 첨부 중 오류 발생");
            document.body.style.cursor = 'default';
            submitBtn.innerText = editingEntryId ? "수정" : "기록";
            return;
        } finally {
            document.body.style.cursor = 'default';
        }
    }

    let newEntry;
    const nowIso = new Date().toISOString();
    let createdAt = nowIso;
    
    if (editingEntryId) {
        const oldEntry = cloudEntries.find(e => e.id === editingEntryId);
        if (oldEntry) {
            createdAt = oldEntry.createdAt || new Date(oldEntry.id).toISOString(); // 기존 시간 유지
        }
    }

    if (recordType === 'trade') {
        const accountName = document.getElementById('accountName').value;
        const tradeType = document.getElementById('tradeType').value;
        const price = document.getElementById('price').value;
        const quantity = document.getElementById('quantity').value;

        newEntry = {
            id: editingEntryId || Date.now(), type: 'trade', stockName, stockCode, brokerAccount, accountName,
            tradeType, price: price ? Number(price) : 0, quantity: quantity ? Number(quantity) : 0, thoughts, date, rawDate: tradeDateRaw, attachedImage: null,
            createdAt, updatedAt: nowIso, tags: currentTags.join(','), attachedFile: finalAttachedFile, attachedFileName: finalAttachedFileName
        };
    } else {
        const memoTitle = document.getElementById('memoTitle').value;
        newEntry = { id: editingEntryId || Date.now(), type: 'memo', stockName: '', stockCode: '', title: memoTitle, thoughts, date, rawDate: tradeDateRaw, attachedImage: null, createdAt, updatedAt: nowIso, tags: currentTags.join(','), attachedFile: finalAttachedFile, attachedFileName: finalAttachedFileName };
    }

    const method = editingEntryId ? 'PUT' : 'POST';
    const url = editingEntryId ? `/api/entry/${editingEntryId}` : '/api/entry';

    try {
        const res = await fetch(url, {
            method: method,
            headers: { 
                'Content-Type': 'application/json',
                'ngrok-skip-browser-warning': 'true'
            },
            body: JSON.stringify(newEntry)
        });
        
        if (res.ok) {
            if (editingEntryId) {
                const index = cloudEntries.findIndex(e => e.id === editingEntryId);
                if (index > -1) cloudEntries[index] = newEntry;
            } else {
                cloudEntries.unshift(newEntry);
            }
            
            editingEntryId = null;
            submitBtn.innerText = "기록";
            resetAndCloseForm();
            displayEntries(true);
            updatePortfolioSummary();
            renderCalendar();
        } else {
            await customAlert("저장에 실패했습니다.");
        }
    } catch(err) {
        console.error(err);
        await customAlert("데이터 저장 중 오류가 발생했습니다.");
    }
});

// ⭐️ 전체 데이터 백업 및 원복 이벤트 연결
const btnFullBackup = document.getElementById('btnFullBackup');
if (btnFullBackup) {
    btnFullBackup.addEventListener('click', async () => {
        if (await customConfirm('에디터 서식(폰트 등) 및 첨부 이미지를 포함한 \n모든 데이터를 완벽하게 백업합니다.\n\n다운로드를 진행하시겠습니까?')) {
            document.body.style.cursor = 'wait';
            fetch('/api/backup', {
                headers: { 'ngrok-skip-browser-warning': 'true' }
            })
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
                    await customAlert('백업 파일 다운로드 중 오류가 발생했습니다.');
                })
                .finally(() => {
                    document.body.style.cursor = 'default';
                });
        }
    });
}

const btnFullRestore = document.getElementById('btnFullRestore');
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
            const response = await fetch('/api/restore', {
                method: 'POST',
                headers: { 'ngrok-skip-browser-warning': 'true' },
                body: formData
            });
            
            const result = await response.json();
            if (response.ok && result.status === 'success') {
                await customAlert('데이터가 성공적으로 원복되었습니다.\n화면을 새로고침 합니다.');
                window.location.reload();
            } else {
                await customAlert('원복 실패: ' + (result.error || '알 수 없는 오류가 발생했습니다.'));
            }
        } catch (err) {
            console.error(err);
            await customAlert('서버와 통신 중 오류가 발생했습니다.');
        } finally {
            document.body.style.cursor = 'default';
            e.target.value = '';
        }
    });
}

document.getElementById('btnExportExcel').addEventListener('click', async () => {
    if (await customConfirm('모든 매매 기록을 엑셀 파일(.xlsx)로 \n다운로드하시겠습니까?')) {
        const header = ['작성일', '분류', '종목명', '증권사', '계좌분류', '매매종류', '단가', '수량', '태그', '메모/생각'];
        const rows = cloudEntries.map(e => [
            e.date, (e.type || '').toUpperCase(), e.stockName||'', e.brokerAccount||'', e.accountName||'',
            e.tradeType||'', Number(e.price)||0, Number(e.quantity)||0, 
            e.tags||'', (e.thoughts||'').replace(/<[^>]*>?/gm, '').replace(/&nbsp;/g, ' ') // HTML 태그 제거 후 엑셀 내보내기
        ]);
        
        const worksheet = XLSX.utils.aoa_to_sheet([header, ...rows]);
        
        // 단가, 수량 컬럼 숫자 포맷(천 단위 콤마) 지정
        const range = XLSX.utils.decode_range(worksheet['!ref']);
        for (let R = range.s.r + 1; R <= range.e.r; ++R) {
            const priceCell = worksheet[XLSX.utils.encode_cell({c: 6, r: R})]; // G열 (단가)
            if (priceCell && priceCell.t === 'n') priceCell.z = '#,##0';
            
            const qtyCell = worksheet[XLSX.utils.encode_cell({c: 7, r: R})]; // H열 (수량)
            if (qtyCell && qtyCell.t === 'n') qtyCell.z = '#,##0';
        }

        // 내용에 맞게 열 너비 자동 조절
        const colWidths = header.map((h, colIdx) => {
            let maxLen = h.length * 2; // 헤더 한글 너비 고려
            rows.forEach(row => {
                const val = row[colIdx] != null ? row[colIdx].toString() : '';
                let len = 0;
                for (let i = 0; i < val.length; i++) len += val.charCodeAt(i) > 255 ? 2 : 1.1; // 한글은 2, 영문/숫자는 1.1 비율
                if (len > maxLen) maxLen = len;
            });
            return { wch: Math.min(Math.max(Math.ceil(maxLen), 10), 100) }; // 최소 10, 최대 100 제한
        });
        worksheet['!cols'] = colWidths;

        const workbook = XLSX.utils.book_new();
        XLSX.utils.book_append_sheet(workbook, worksheet, "매매일지");
        
        const now = new Date();
        const yyyy = now.getFullYear();
        const mm = String(now.getMonth() + 1).padStart(2, '0');
        const dd = String(now.getDate()).padStart(2, '0');
        const hh = String(now.getHours()).padStart(2, '0');
        const min = String(now.getMinutes()).padStart(2, '0');
        const ss = String(now.getSeconds()).padStart(2, '0');
        const filename = `TradingJournal_export_${yyyy}${mm}${dd}_${hh}${min}${ss}.xlsx`;
        
        XLSX.writeFile(workbook, filename);
    }
});

// ⭐️ 숫자 카운트업 애니메이션 함수 (차트 중앙 텍스트용)
function animateValue(element, endValue, duration, isProfit = false) {
    let startValue = parseInt(element.getAttribute('data-val')) || 0;
    if (startValue === endValue) {
        const prefix = isProfit && endValue > 0 ? '+' : '';
        element.innerText = prefix + endValue.toLocaleString() + '원';
        return;
    }
    if (element.dataset.animId) cancelAnimationFrame(element.dataset.animId);

    let startTime = null;
    const step = (timestamp) => {
        if (!startTime) startTime = timestamp;
        const progress = Math.min((timestamp - startTime) / duration, 1);
        // easeOutExpo (부드럽게 감속)
        const ease = progress === 1 ? 1 : 1 - Math.pow(2, -10 * progress);
        const current = Math.floor(startValue + (endValue - startValue) * ease);
        
        const prefix = isProfit && current > 0 ? '+' : '';
        element.innerText = prefix + current.toLocaleString() + '원';
        
        if (progress < 1) {
            element.dataset.animId = requestAnimationFrame(step);
        } else {
            const finalPrefix = isProfit && endValue > 0 ? '+' : '';
            element.innerText = finalPrefix + endValue.toLocaleString() + '원';
            element.setAttribute('data-val', endValue);
        }
    };
    element.dataset.animId = requestAnimationFrame(step);
}

// ⭐️ 백엔드 API를 통해 현재가와 평가금액을 가져와 DOM에 반영하는 함수
window.fetchCurrentPricesAndUpdateUI = async function() {
    if (!showCurrentPrice || currentPortfolioArrayForPrice.length === 0) return;
    
    const codes = [...new Set(currentPortfolioArrayForPrice.filter(p => !p.isClosed && p.stockCode).map(p => p.stockCode))];
    if (codes.length === 0) return;
    
    try {
        const res = await fetch('/api/current_price', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': 'true' },
            body: JSON.stringify({ codes })
        });
        const prices = await res.json();
        
        let totalEval = 0;
        currentPortfolioArrayForPrice.forEach(data => {
            if (data.isClosed) return;
            const cp = prices[data.stockCode];
            const pEls = document.querySelectorAll(`.cp-price[data-code="${data.stockCode || ''}"]`);
            const eEls = document.querySelectorAll(`.cp-eval[data-code="${data.stockCode || ''}"]`);
            const pfEls = document.querySelectorAll(`.cp-profit[data-code="${data.stockCode || ''}"]`);
            
            if (cp !== undefined && cp !== null) {
                const evalAmount = cp * data.qty;
                const profitAmount = evalAmount - data.totalCost;
                
                pEls.forEach(el => el.innerText = cp.toLocaleString());
                eEls.forEach(el => el.innerText = Math.round(evalAmount).toLocaleString());
                
                const pColor = profitAmount > 0 ? 'var(--danger-color)' : (profitAmount < 0 ? 'var(--primary-color)' : 'var(--text-strong-color)');
                pfEls.forEach(el => el.innerHTML = `<span style="color: ${pColor}; font-weight: bold;">${profitAmount > 0 ? '+' : ''}${Math.round(profitAmount).toLocaleString()}</span>`);
                totalEval += evalAmount;
            } else {
                pEls.forEach(el => el.innerText = '조회 실패');
                totalEval += data.totalCost; // 조회 실패 시 기본 투자원금으로 임시 합산
            }
        });
        
        const centerEvalEl = document.getElementById('centerTotalEvaluation');
        if (centerEvalEl) animateValue(centerEvalEl, Math.round(totalEval), 1000, false);
    } catch(e) { console.error("현재가 가져오기 실패", e); }
};

function updatePortfolioSummary() {
    const portfolio = {};
    const chartLabels = [];
    const chartData = [];
    let totalRealizedProfit = 0;
    let totalInvestedAmount = 0;
    let holdingsCount = 0;
    let monthlyBuyCount = 0;
    let monthlySellCount = 0;
    
    const nowDt = new Date();
    const curYear = nowDt.getFullYear();
    const curMonth = nowDt.getMonth();
    
    const chronologicalEntries = [...cloudEntries].reverse();

    chronologicalEntries.forEach(entry => {
        if (entry.type !== 'trade' || !entry.stockName) return;
        
        // ⭐️ 대시보드 증권사 필터 적용
        if (currentDashboardBroker !== 'all' && (entry.brokerAccount || '') !== currentDashboardBroker) return;
        
        // ⭐️ 대시보드 투자 분류 필터 적용
        if (currentDashboardAccount !== 'all' && (entry.accountName || '') !== currentDashboardAccount) return;

        const stock = entry.stockName;
        const qty = Number(entry.quantity) || 0;
        const price = Number(entry.price) || 0;

        if (!portfolio[stock]) portfolio[stock] = { qty: 0, totalCost: 0, avgPrice: 0, realizedProfit: 0, accountName: '', traded: false, stockCode: '' };
        if (entry.accountName) portfolio[stock].accountName = entry.accountName; // 가장 최근 거래의 투자 분류 기록
        if (entry.stockCode) portfolio[stock].stockCode = entry.stockCode; // 종목코드 기록

        // 이번 달 거래인지 확인
        let isCurrentMonth = false;
        let entryDate = null;
        if (entry.rawDate) entryDate = new Date(entry.rawDate);
        else if (entry.id) entryDate = new Date(entry.id);
        
        if (entryDate && !isNaN(entryDate) && entryDate.getFullYear() === curYear && entryDate.getMonth() === curMonth) {
            isCurrentMonth = true;
        }

        if (entry.tradeType === '매수') {
            if (isCurrentMonth) monthlyBuyCount++;
            portfolio[stock].traded = true;
            portfolio[stock].qty += qty;
            portfolio[stock].totalCost += (price * qty);
            if (portfolio[stock].qty > 0) portfolio[stock].avgPrice = portfolio[stock].totalCost / portfolio[stock].qty;
        } else if (entry.tradeType === '매도') {
            if (isCurrentMonth) monthlySellCount++;
            portfolio[stock].traded = true;
            const currentAvgPrice = portfolio[stock].avgPrice;
            const profit = (price - currentAvgPrice) * qty;
            portfolio[stock].realizedProfit += profit;
            totalRealizedProfit += profit;

            portfolio[stock].qty -= qty;
            portfolio[stock].totalCost -= (currentAvgPrice * qty);
            if (portfolio[stock].qty <= 0) { portfolio[stock].qty = 0; portfolio[stock].totalCost = 0; portfolio[stock].avgPrice = 0; }
        }
    });

    const portfolioGrid = document.getElementById('portfolioGrid');
    portfolioGrid.innerHTML = '';
    let hasHoldings = false;
    currentHoldings = [];

    // ⭐️ 포트폴리오를 배열로 변환하고 투자 분류에 따라 정렬
    const portfolioArray = [];
    for (const stock in portfolio) {
        const p = portfolio[stock];
        const isClosed = p.qty <= 0;
        if (!isClosed) {
            portfolioArray.push({ stock, ...p, isClosed: false });
        } else if (showClosedPositions && p.traded) {
            portfolioArray.push({ stock, ...p, isClosed: true }); // 청산 종목 포함
        }
    }

    currentPortfolioArrayForPrice = portfolioArray;
    const sortOrder = { "장기투자": 1, "중기투자": 2, "단기스윙": 3, "단타(스캘핑)": 4, "배당투자": 5, "공모주": 6, "기타": 7 };
    portfolioArray.sort((a, b) => {
        // ⭐️ 청산 종목을 가장 하단으로 정렬
        if (a.isClosed !== b.isClosed) {
            return a.isClosed ? 1 : -1;
        }
        
        // ⭐️ 사용자가 드래그 앤 드롭으로 설정한 커스텀 순서가 있다면 최우선 적용
        if (userPreferences.portfolioOrder) {
            const idxA = userPreferences.portfolioOrder.indexOf(a.stock);
            const idxB = userPreferences.portfolioOrder.indexOf(b.stock);
            
            if (idxA !== -1 && idxB !== -1) return idxA - idxB;
            if (idxA !== -1) return -1;
            if (idxB !== -1) return 1;
        }
        
        const orderA = sortOrder[a.accountName] || 99;
        const orderB = sortOrder[b.accountName] || 99;
        if (orderA !== orderB) return orderA - orderB;
        return a.stock.localeCompare(b.stock); // 분류가 같으면 종목명 가나다순 정렬
    });

    const shortAccountNameMap = {
        "장기투자": "장기",
        "중기투자": "중기",
        "단기스윙": "스윙",
        "단타(스캘핑)": "단타",
        "배당투자": "배당",
        "공모주": "공모",
        "기타": "기타"
    };

    const badgeClassMap = {
        "장기투자": "badge-long",
        "중기투자": "badge-mid",
        "단기스윙": "badge-swing",
        "단타(스캘핑)": "badge-scalp",
        "배당투자": "badge-dividend",
        "공모주": "badge-ipo",
        "기타": "badge-etc"
    };

    portfolioArray.forEach(data => {
        const stock = data.stock;
        const isClosed = data.isClosed;
        
        // 현재 보유 중인 종목만 차트 및 상단 요약 수치에 반영
        if (!isClosed) {
            totalInvestedAmount += data.totalCost;
            holdingsCount++;
            currentHoldings.push(stock);
            hasHoldings = true;
            chartLabels.push(stock);
            chartData.push(data.totalCost);
        }
        
        const shortAccountName = data.accountName ? (shortAccountNameMap[data.accountName] || data.accountName.substring(0, 2)) : '';
        const badgeClass = badgeClassMap[data.accountName] || 'badge-etc';
        const cardBorderClass = badgeClass.replace('badge-', 'card-border-');
        
        const card = document.createElement('div');
        card.className = `portfolio-card ${cardBorderClass}`;
        card.setAttribute('data-id', stock); // ⭐️ 드래그 앤 드롭 정렬을 위한 식별자 추가
        if (isClosed) {
            card.style.opacity = '0.6'; // 청산 종목은 반투명하게 표시
            card.style.borderLeftColor = 'var(--text-muted-color)';
        }
        const statusBadge = isClosed ? `<span style="font-size: 10px; background: var(--border-color); color: var(--card-bg-color); padding: 1px 4px; border-radius: 3px;">청산완료</span>` : '';
        const accountBadgeHtml = shortAccountName ? `<span class="account-badge ${badgeClass}">${shortAccountName}</span>` : '';
        card.innerHTML = `
            <div class="stock-name" style="margin-bottom: 2px;">${stock}</div>
            <div style="margin-bottom: 8px; display: flex; align-items: center; min-height: 16px;">${accountBadgeHtml}${statusBadge}</div>
            <div class="stat-row"><span>보유 수량</span><span>${data.qty.toLocaleString()}주</span></div>
            <div class="stat-row"><span>평균 단가</span><span>${Math.round(data.avgPrice).toLocaleString()}</span></div>
            <div class="stat-row"><span>총 매수금액</span><span>${Math.round(data.totalCost).toLocaleString()}</span></div>
        `;
        
        if (data.realizedProfit !== 0) {
            const profitColor = data.realizedProfit > 0 ? 'var(--danger-color)' : 'var(--primary-color)';
            const profitStr = (data.realizedProfit > 0 ? '+' : '') + Math.round(data.realizedProfit).toLocaleString();
            card.innerHTML += `
                <div class="stat-row" style="margin-top: 8px; padding-top: 8px; border-top: 1px dashed var(--border-color);">
                    <span>종목 실현손익</span><span style="color:${profitColor}">${profitStr}</span>
                </div>`;
        }
        
        // ⭐️ 현재가 보기 활성화 시 카드 하단에 영역 추가
        if (!isClosed && showCurrentPrice) {
            card.innerHTML += `
                <div class="current-price-section" style="margin-top: 8px; padding-top: 8px; border-top: 1px dashed var(--border-color);">
                    <div class="stat-row"><span>현재가</span><span class="cp-price" data-code="${data.stockCode || ''}">조회 중...</span></div>
                    <div class="stat-row"><span>평가금액</span><span class="cp-eval" data-code="${data.stockCode || ''}">-</span></div>
                    <div class="stat-row"><span>평가손익</span><span class="cp-profit" data-code="${data.stockCode || ''}">-</span></div>
                </div>
            `;
        }
        
        // ⭐️ 종목 카드 클릭 시 해당 종목 히스토리 필터링 이벤트 연동
        card.title = `${stock} 기록 모아보기`;
        card.addEventListener('click', () => {
            currentFilterDate = null; // 날짜 필터 초기화
            currentFilterType = 'stock_' + stock; // 드롭다운 필터값 설정
            
            // 캘린더 뷰인 경우 리스트 뷰로 자동 전환
            const btnListView = document.getElementById('btnListView');
            if (btnListView && !btnListView.classList.contains('active')) {
                btnListView.click();
            }
            
            displayEntries(true); // 필터링 반영
            
            // 사용자 편의를 위해 필터/히스토리 영역으로 부드럽게 스크롤
            window.scrollToFilterBox();
            
            // ⭐️ 사용자 설정에 상태 저장
            userPreferences.showHistoryClosedPositions = showHistoryClosedPositions;
            savePreferences();
        });

        portfolioGrid.appendChild(card);
    });
    
    // ⭐️ 필터 결과가 없을 때 빈 화면 대신 안내 메시지 표시
    if (portfolioArray.length === 0) {
        portfolioGrid.innerHTML = '<div style="grid-column: 1 / -1; text-align: center; padding: 40px 20px; color: var(--text-muted-color); font-size: 13px;">해당 조건에 맞는 종목이 없습니다.</div>';
    }

    let toggleBtn = document.getElementById('btnTogglePortfolio');
    // ⭐️ 필터가 적용되어 결과가 없더라도, 전체 매매 기록이 존재하면 대시보드를 숨기지 않음
    const hasAnyTrade = cloudEntries.some(e => e.type === 'trade' && e.stockName);
    const shouldShowDashboard = hasAnyTrade;

    if (toggleBtn) {
        toggleBtn.innerHTML = isDashboardCollapsed ? '펼치기 ▼' : '접기 ▲';
        toggleBtn.style.display = shouldShowDashboard ? 'inline-block' : 'none';
    }

    if (portfolioGrid) portfolioGrid.style.display = isDashboardCollapsed ? 'none' : '';
    
    const brokerFilterEl = document.getElementById('dashboardBrokerFilter');
    if (brokerFilterEl && brokerFilterEl.parentElement) {
        brokerFilterEl.parentElement.style.display = isDashboardCollapsed ? 'none' : 'flex';
    }
    
    // ⭐️ SortableJS 드래그 앤 드롭 활성화
    if (portfolioSortable) {
        portfolioSortable.destroy();
        portfolioSortable = null;
    }
    
    if (portfolioArray.length > 0 && !isDashboardCollapsed) {
        portfolioSortable = Sortable.create(portfolioGrid, {
            animation: 300, // ⭐️ 카드가 밀려날 때 더 부드럽고 천천히 이동
            easing: "cubic-bezier(0.25, 1, 0.5, 1)", // ⭐️ 부드러운 가감속 효과
            ghostClass: 'sortable-ghost',
            dragClass: 'sortable-drag',
            forceFallback: true, // ⭐️ 모바일 환경에서 카드가 손가락을 정확히 따라오도록 강제 폴백 렌더링
            fallbackClass: 'sortable-fallback',
            fallbackOnBody: true, // ⭐️ 드래그 중인 카드가 그리드 영역에 갇히지 않고 웹처럼 자유롭게 전체 화면을 이동하도록 설정
            delay: 150, // 더 빠르고 직관적인 터치 반응을 위해 딜레이 단축 (0.15초)
            delayOnTouchOnly: true,
            touchStartThreshold: 5, // ⭐️ 5px 이상 터치가 미끄러지면 스크롤로 인식하여 드래그 취소 (모바일 안정성)
            onEnd: function () {
                const newOrder = portfolioSortable.toArray(); // 새로 정렬된 식별자 배열
                let updatedOrder = [...newOrder];
                if (userPreferences.portfolioOrder) {
                    userPreferences.portfolioOrder.forEach(stk => {
                        if (!updatedOrder.includes(stk)) updatedOrder.push(stk);
                    });
                }
                userPreferences.portfolioOrder = updatedOrder;
                savePreferences();
            }
        });
    }
    
    const theme = document.documentElement.getAttribute('data-theme') || 'light';
    const legendColor = theme === 'dark' ? '#e0e0e0' : '#2c3e50';
    
    // 모드별 색상 정의
    const lightColors = ['#3498db', '#e74c3c', '#f1c40f', '#2ecc71', '#9b59b6', '#e67e22', '#1abc9c', '#34495e'];
    const darkColors = ['#2a5298', '#c0392b', '#d68910', '#1e8449', '#76448a', '#ca6f1e', '#117a65', '#283747'];

    const chartColors = theme === 'dark' ? darkColors : lightColors;
    const hoverColors = theme === 'dark' ? lightColors : darkColors; // ⭐️ 호버 시 반대 테마 색상 적용

    const chartContainer = document.getElementById('portfolioChartContainer');
    if (shouldShowDashboard && !isDashboardCollapsed) {
        chartContainer.style.display = 'block';
        
        // 차트 중앙 텍스트 업데이트
        const targetInvested = Math.round(totalInvestedAmount);
        const investedStr = targetInvested.toLocaleString() + '원';
        const elInvested = document.getElementById('centerTotalInvested');
        animateValue(elInvested, targetInvested, 1000, false); // ⭐️ 1초(1000ms) 동안 카운트업 애니메이션
        elInvested.style.fontSize = investedStr.length > 13 ? '13px' : (investedStr.length > 10 ? '15px' : '17px');

        const centerProfit = document.getElementById('centerTotalProfit');
        const targetProfit = Math.round(totalRealizedProfit);
        const profitStr = (targetProfit > 0 ? '+' : '') + targetProfit.toLocaleString() + '원';
        animateValue(centerProfit, targetProfit, 1000, true); // ⭐️ 1초(1000ms) 동안 카운트업 애니메이션
        centerProfit.style.fontSize = profitStr.length > 13 ? '12px' : (profitStr.length > 10 ? '13px' : '15px');
        centerProfit.style.color = totalRealizedProfit > 0 ? 'var(--danger-color)' : (totalRealizedProfit < 0 ? 'var(--primary-color)' : 'var(--text-strong-color)');
        document.getElementById('centerHoldingsCount').innerText = holdingsCount + '종목 보유';
        document.getElementById('centerTradeStats').innerText = `월간 매수 ${monthlyBuyCount} / 매도 ${monthlySellCount}`;

        // 보유 종목이 없을 때(전량 매도) 보여줄 '빈 고리' 더미 데이터 처리
        const isPortfolioEmpty = totalInvestedAmount === 0;
        const finalLabels = isPortfolioEmpty ? ['보유 종목 없음'] : chartLabels;
        const finalData = isPortfolioEmpty ? [1] : chartData;
        const finalColors = isPortfolioEmpty ? [theme === 'dark' ? '#2c2c2c' : '#f0f0f0'] : chartColors;
        const finalHoverColors = isPortfolioEmpty ? [theme === 'dark' ? '#f0f0f0' : '#2c2c2c'] : hoverColors;

        // ⭐️ 현재가 보기 활성화 시 도넛 차트 중앙에 총 평가금액 컨테이너 노출 및 조회 요청
        if (showCurrentPrice && !isPortfolioEmpty) {
            document.getElementById('centerTotalEvaluationContainer').style.display = 'block';
            fetchCurrentPricesAndUpdateUI();
        } else {
            document.getElementById('centerTotalEvaluationContainer').style.display = 'none';
        }

        const ctx = document.getElementById('portfolioChart').getContext('2d');
        if (portfolioChartInstance) portfolioChartInstance.destroy();
        portfolioChartInstance = new Chart(ctx, {
            type: 'doughnut',
            data: { 
                labels: finalLabels, 
                datasets: [{ 
                    data: finalData, 
                    backgroundColor: finalColors, 
                    hoverBackgroundColor: finalHoverColors, // ⭐️ 호버 색상 속성 추가
                    borderColor: theme === 'dark' ? '#1e1e1e' : '#fff',
                    hoverOffset: isPortfolioEmpty ? 0 : 12 // ⭐️ 마우스 오버 시 조각이 커지는 애니메이션 효과 추가
                }] 
            },
            options: { 
                responsive: true,
                cutout: '72%', // 중앙 구멍 크기 확장
                layout: { padding: 15 }, // ⭐️ 도넛 크기를 약간 줄여 주변 여유 공간 확보
                onHover: (e, elements, chart) => {
                    chart.canvas.style.cursor = isPortfolioEmpty ? 'default' : 'pointer';
                },
                plugins: { 
                    legend: { 
                        display: false // ⭐️ 기본 캔버스 범례 숨기기 (도넛 크기 고정을 위해)
                    },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                if (isPortfolioEmpty) return '현재 보유 중인 종목이 없습니다.';
                                let value = context.parsed;
                                let total = context.dataset.data.reduce((a, b) => a + b, 0);
                                let percentage = total > 0 ? ((value / total) * 100).toFixed(1) + '%' : '0%';
                                return `${context.label}: ${Math.round(value).toLocaleString()}원 (${percentage})`;
                            }
                        }
                    }
                } 
            },
            // ⭐️ 커스텀 플러그인: 도넛 차트의 실제 중심 좌표를 찾아 텍스트 위치를 동기화
            plugins: [{
                id: 'centerTextPositioner',
                afterDraw: (chart) => {
                    const centerText = document.getElementById('chartCenterText');
                    const meta = chart.getDatasetMeta(0);
                    if (centerText && meta && meta.data.length > 0) {
                        const arc = meta.data[0];
                        if (arc && typeof arc.x === 'number' && typeof arc.y === 'number') {
                            centerText.style.left = arc.x + 'px';
                            centerText.style.top = arc.y + 'px';
                        }
                    }
                }
            }]
        });

        // ⭐️ 커스텀 HTML 범례 생성 (도넛 크기가 범례 개수에 영향받지 않게 분리)
        const customLegendContainer = document.getElementById('customChartLegend');
        if (customLegendContainer) {
            customLegendContainer.innerHTML = '';
            if (!isPortfolioEmpty) {
                chartLabels.forEach((label, index) => {
                    const color = finalColors[index % finalColors.length];
                    const legendItem = document.createElement('div');
                    legendItem.style.display = 'flex';
                    legendItem.style.alignItems = 'center';
                    legendItem.style.gap = '4px';
                    legendItem.style.fontSize = '11.5px';
                    legendItem.style.color = legendColor;
                    legendItem.style.cursor = 'pointer';
                    legendItem.innerHTML = `<span style="display:inline-block; width:10px; height:10px; background-color:${color}; border-radius:2px;"></span><span>${label}</span>`;
                    
                    legendItem.addEventListener('click', () => {
                        currentFilterDate = null;
                        currentFilterType = 'stock_' + label;
                        const btnListView = document.getElementById('btnListView');
                        if (btnListView && !btnListView.classList.contains('active')) btnListView.click();
                        displayEntries(true);
                        window.scrollToFilterBox();
                    });
                    customLegendContainer.appendChild(legendItem);
                });
            }
        }
    } else { chartContainer.style.display = 'none'; }

    document.getElementById('portfolioSection').style.display = shouldShowDashboard ? 'block' : 'none';
}

// ⭐️ 드롭다운 필터에 종목명을 동적으로 추가하는 함수
function updateFilterDropdown() {
    const select = document.getElementById('filterTypeSelect');
    if (!select) return;
    
    const currentVal = currentFilterType || 'all';
    let html = `<option value="all">전체 보기</option>
                <option value="trade">매매 기록만</option>
                <option value="memo">일반 메모만</option>`;
    
    const stocks = [...new Set(cloudEntries.map(e => e.stockName).filter(Boolean))].sort();
    if (stocks.length > 0) {
        html += `<optgroup label="종목별 모아보기">`;
        stocks.forEach(stock => {
            html += `<option value="stock_${stock.replace(/"/g, '&quot;')}">${stock}</option>`;
        });
        html += `</optgroup>`;
    }
    
    const accountSortOrder = { "장기투자": 1, "중기투자": 2, "단기스윙": 3, "단타(스캘핑)": 4, "배당투자": 5, "공모주": 6, "기타": 7 };
    const accounts = [...new Set(cloudEntries.map(e => e.accountName).filter(Boolean))].sort((a, b) => {
        const orderA = accountSortOrder[a] || 99;
        const orderB = accountSortOrder[b] || 99;
        if (orderA !== orderB) return orderA - orderB;
        return a.localeCompare(b);
    });
    if (accounts.length > 0) {
        html += `<optgroup label="분류별 모아보기">`;
        accounts.forEach(account => {
            html += `<option value="account_${account.replace(/"/g, '&quot;')}">${account}</option>`;
        });
        html += `</optgroup>`;
    }
    
    const brokers = [...new Set(cloudEntries.map(e => e.brokerAccount).filter(Boolean))].sort();
    if (brokers.length > 0) {
        html += `<optgroup label="증권사별 모아보기">`;
        brokers.forEach(broker => {
            html += `<option value="broker_${broker.replace(/"/g, '&quot;')}">${broker}</option>`;
        });
        html += `</optgroup>`;
    }
    
    // ⭐️ 대시보드의 증권사 필터 옵션도 동적으로 업데이트
    const dashboardBrokerFilter = document.getElementById('dashboardBrokerFilter');
    if (dashboardBrokerFilter) {
        const currentBrokerVal = currentDashboardBroker || 'all';
        let brokerHtml = `<option value="all">모든 증권사</option>`;
        if (brokers.length > 0) {
            brokers.forEach(broker => {
                brokerHtml += `<option value="${broker.replace(/"/g, '&quot;')}">${broker}</option>`;
            });
        }
        dashboardBrokerFilter.innerHTML = brokerHtml;
        if (dashboardBrokerFilter.querySelector(`option[value="${currentBrokerVal}"]`)) {
            dashboardBrokerFilter.value = currentBrokerVal;
        } else {
            dashboardBrokerFilter.value = 'all';
            currentDashboardBroker = 'all';
        }
    }

    // ⭐️ 대시보드의 투자 분류 필터 옵션도 동적으로 업데이트
    const dashboardAccountFilter = document.getElementById('dashboardAccountFilter');
    if (dashboardAccountFilter) {
        const currentAccountVal = currentDashboardAccount || 'all';
        let accountHtml = `<option value="all">모든 분류</option>`;
        if (accounts.length > 0) {
            accounts.forEach(account => {
                accountHtml += `<option value="${account.replace(/"/g, '&quot;')}">${account}</option>`;
            });
        }
        dashboardAccountFilter.innerHTML = accountHtml;
        if (dashboardAccountFilter.querySelector(`option[value="${currentAccountVal}"]`)) {
            dashboardAccountFilter.value = currentAccountVal;
        } else {
            dashboardAccountFilter.value = 'all';
            currentDashboardAccount = 'all';
        }
    }

    select.innerHTML = html;
    
    if (select.querySelector(`option[value="${currentVal}"]`)) {
        select.value = currentVal;
    } else {
        select.value = 'all';
        currentFilterType = null;
    }
    
    const clearTypeFilterBtn = document.getElementById('clearTypeFilterBtn');
    if (clearTypeFilterBtn) {
        clearTypeFilterBtn.style.display = (select.value !== 'all') ? 'inline-block' : 'none';
    }
}

function displayEntries(isFilterUpdate = false) {
    cloudEntries.sort((a, b) => {
        const timeA = a.rawDate ? new Date(a.rawDate).getTime() : a.id;
        const timeB = b.rawDate ? new Date(b.rawDate).getTime() : b.id;
        return timeB - timeA;
    });

    // [3안] 리스트 뷰 필터링 상태와 HTML Select 동기화
    const filterTypeSelect = document.getElementById('filterTypeSelect');
    if (filterTypeSelect) {
        let selectVal = currentFilterType || 'all';
        if (selectVal.startsWith('stock_trade_')) selectVal = 'stock_' + selectVal.substring(12);
        else if (selectVal.startsWith('stock_memo_')) selectVal = 'stock_' + selectVal.substring(11);
        
        if (filterTypeSelect.querySelector(`option[value="${selectVal}"]`)) {
            filterTypeSelect.value = selectVal;
        } else {
            filterTypeSelect.value = 'all';
        }
        
        const clearTypeFilterBtn = document.getElementById('clearTypeFilterBtn');
        if (clearTypeFilterBtn) {
            clearTypeFilterBtn.style.display = (filterTypeSelect.value !== 'all') ? 'inline-block' : 'none';
        }
    }

    if (!isFilterUpdate) {
        updateFilterDropdown();
        updatePortfolioSummary();
        renderCalendar();
    }

    // ⭐️ 리스트 갱신 시 순간적인 스크롤 튐(위로 점프) 현상을 방지하기 위해 이전 높이 임시 유지
    const prevHeight = historyList.offsetHeight;
    if (prevHeight > 0) historyList.style.minHeight = prevHeight + 'px';

    historyList.innerHTML = '';
    
    // 청산 종목 필터링을 위한 현재 보유 수량 계산
    const stockQtys = {};
    cloudEntries.forEach(entry => {
        if (entry.type === 'trade' && entry.stockName) {
            if (entry.tradeType === '매수' || entry.tradeType === '매도') {
                if (stockQtys[entry.stockName] === undefined) stockQtys[entry.stockName] = 0;
                if (entry.tradeType === '매수') stockQtys[entry.stockName] += (Number(entry.quantity) || 0);
                else if (entry.tradeType === '매도') stockQtys[entry.stockName] -= (Number(entry.quantity) || 0);
            }
        }
    });

    // ⭐️ 분류별/증권사별 모아보기 시 연관된 일반 메모를 함께 보여주기 위해 종목명 추출
    const relatedStocksForFilter = new Set();
    if (currentFilterType && currentFilterType.startsWith('account_')) {
        const targetAccount = currentFilterType.substring(8);
        cloudEntries.forEach(e => { if ((e.type || 'trade') === 'trade' && e.accountName === targetAccount && e.stockName) relatedStocksForFilter.add(e.stockName); });
    } else if (currentFilterType && currentFilterType.startsWith('broker_')) {
        const targetBroker = currentFilterType.substring(7);
        cloudEntries.forEach(e => { if ((e.type || 'trade') === 'trade' && e.brokerAccount === targetBroker && e.stockName) relatedStocksForFilter.add(e.stockName); });
    }

    const filterValue = filterStockInput.value.trim().toLowerCase();
    const filteredEntries = cloudEntries.filter(entry => {
        if (filterValue) {
            const matchStock = entry.stockName && entry.stockName.toLowerCase().includes(filterValue);
            const matchBroker = entry.brokerAccount && entry.brokerAccount.toLowerCase().includes(filterValue);
            const matchTags = entry.tags && entry.tags.toLowerCase().includes(filterValue);
            const plainThoughts = entry.thoughts ? entry.thoughts.replace(/<[^>]*>?/gm, '').toLowerCase() : '';
            const matchThoughts = plainThoughts.includes(filterValue);
            const matchTitle = entry.title && entry.title.toLowerCase().includes(filterValue);
            if (!(matchStock || matchBroker || matchTags || matchThoughts || matchTitle)) return false;
        }
        
        if (currentFilterDate) {
            let entryDateKey = '';
            if (entry.rawDate) { entryDateKey = entry.rawDate.split('T')[0]; } 
            else if (entry.date) {
                const parts = entry.date.split('. ');
                if (parts.length >= 3) entryDateKey = `${parts[0]}-${parts[1].padStart(2,'0')}-${parts[2].split('.')[0].padStart(2,'0')}`;
            }
            if (entryDateKey !== currentFilterDate) return false;
        }

        if (currentFilterType && currentFilterType !== 'all') {
            if (currentFilterType.startsWith('stock_trade_')) {
                const targetStock = currentFilterType.substring(12);
                if ((entry.stockName || '') !== targetStock || (entry.type || 'trade') !== 'trade') return false;
            } else if (currentFilterType.startsWith('stock_memo_')) {
                const targetStock = currentFilterType.substring(11);
                if ((entry.stockName || '') !== targetStock || (entry.type || 'trade') !== 'memo') return false;
            } else if (currentFilterType.startsWith('stock_')) {
                const targetStock = currentFilterType.substring(6);
                if ((entry.stockName || '') !== targetStock) return false;
            } else if (currentFilterType.startsWith('account_')) {
                const targetAccount = currentFilterType.substring(8);
                const entryType = entry.type || 'trade';
                const isMatchTrade = entryType === 'trade' && (entry.accountName || '') === targetAccount;
                const isMatchMemo = entryType === 'memo' && relatedStocksForFilter.has(entry.stockName);
                if (!isMatchTrade && !isMatchMemo) return false;
            } else if (currentFilterType.startsWith('broker_')) {
                const targetBroker = currentFilterType.substring(7);
                const entryType = entry.type || 'trade';
                const isMatchTrade = entryType === 'trade' && (entry.brokerAccount || '') === targetBroker;
                const isMatchMemo = entryType === 'memo' && relatedStocksForFilter.has(entry.stockName);
                if (!isMatchTrade && !isMatchMemo) return false;
            } else {
                const entryType = entry.type || 'trade';
                if (entryType !== currentFilterType) return false;
            }
        }
        
        // ⭐️ 청산 종목 숨기기 상태일 때 (보유 수량이 0인 종목을 검색 및 필터에서 제외)
        if (!showHistoryClosedPositions) {
            if (entry.stockName && stockQtys[entry.stockName] !== undefined && stockQtys[entry.stockName] <= 0) {
                return false; 
            }
        }
        
        return true;
    });

    const banner = document.getElementById('activeFilterBanner');
    if (currentFilterDate) {
        banner.style.display = 'flex';
        let typeText = '전체 기록';
        if (currentFilterType === 'trade') typeText = '매매 기록';
        else if (currentFilterType === 'memo') typeText = '일반 메모';
        else if (currentFilterType && currentFilterType.startsWith('stock_trade_')) {
            const st = currentFilterType.substring(12);
            typeText = st ? st + ' 매매 기록' : '종목 미지정 매매 기록';
        } else if (currentFilterType && currentFilterType.startsWith('stock_memo_')) {
            const st = currentFilterType.substring(11);
            typeText = st ? st + ' 일반 메모' : '종목 미지정 일반 메모';
        } else if (currentFilterType && currentFilterType.startsWith('stock_')) {
            const st = currentFilterType.substring(6);
            typeText = st ? st + ' 기록' : '종목 미지정 기록';
        } else if (currentFilterType && currentFilterType.startsWith('account_')) {
            const acc = currentFilterType.substring(8);
            typeText = acc ? acc + ' 기록' : '분류 미지정 기록';
        } else if (currentFilterType && currentFilterType.startsWith('broker_')) {
            const brk = currentFilterType.substring(7);
            typeText = brk ? brk + ' 기록' : '증권사 미지정 기록';
        }
        document.getElementById('activeFilterText').innerText = `📅 ${currentFilterDate} 일자의 ${typeText} 모아보기`;
    } else { banner.style.display = 'none'; }

    currentFilteredEntries = filteredEntries;
    currentRenderPage = 1;
    lastRenderedMonth = '';

    if (filteredEntries.length === 0) {
        historyList.innerHTML = '<p style="text-align:center; color:var(--text-muted-color); font-size: 16px; padding: 20px;">조건에 맞는 기록이 없습니다.</p>';
        // 높이 고정 해제
        requestAnimationFrame(() => { historyList.style.minHeight = ''; });
        return;
    }

    renderPage();

    // 리렌더링 완료 후 높이 고정 해제 (부드러운 전환을 위해 브라우저 페인트 타이밍에 맞춤)
    requestAnimationFrame(() => { historyList.style.minHeight = ''; });
}

function renderPage() {
    const existingSentinel = document.getElementById('scroll-sentinel');
    if (existingSentinel) {
        loadMoreObserver.unobserve(existingSentinel);
        existingSentinel.remove();
    }

    const start = (currentRenderPage - 1) * entriesPerPage;
    const end = start + entriesPerPage;
    const pageEntries = currentFilteredEntries.slice(start, end);

    // ⭐️ 검색어 하이라이팅을 위한 정규식 준비
    const keyword = filterStockInput.value.trim();
    const safeKeyword = keyword.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const regexText = safeKeyword ? new RegExp(`(${safeKeyword})`, 'gi') : null;
    const regexHtml = safeKeyword ? new RegExp(`(${safeKeyword})(?![^<]*>)`, 'gi') : null; // HTML 태그 내부 무시
    
    function highlight(text, isHtml = false) {
        if (!safeKeyword || !text) return text || '';
        if (isHtml) return text.replace(regexHtml, `<mark class="search-highlight">$1</mark>`);
        return text.replace(regexText, `<mark class="search-highlight">$1</mark>`);
    }

    pageEntries.forEach(entry => {
        // ⭐️ 월별 타임라인 구분선 로직
        let entryMonth = '';
        let parsedDate = null;
        if (entry.rawDate) parsedDate = new Date(entry.rawDate);
        else if (entry.id) parsedDate = new Date(entry.id);
        
        if (parsedDate && !isNaN(parsedDate)) {
            entryMonth = `${parsedDate.getFullYear()}년 ${parsedDate.getMonth() + 1}월`;
        }

        if (entryMonth && entryMonth !== lastRenderedMonth) {
            const divider = document.createElement('div');
            divider.className = 'timeline-divider';
            divider.innerText = entryMonth;
            historyList.appendChild(divider);
            lastRenderedMonth = entryMonth;
        }

        const card = document.createElement('div');
        card.className = 'entry-card';
        const entryType = entry.type || 'trade';
        const imageHtml = entry.attachedImage ? `<div style="margin-top:10px;"><img src="${entry.attachedImage}" class="entry-thumbnail" onclick="openImageViewer(this.src, event)" title="클릭하여 원본 보기"></div>` : '';

        const createdStr = entry.createdAt ? new Date(entry.createdAt).toLocaleString() : new Date(entry.id).toLocaleString();
        const updatedStr = entry.updatedAt ? new Date(entry.updatedAt).toLocaleString() : '';
        const timeDisplayHtml = `
            <div style="display: flex; flex-direction: column; gap: 3px;">
                <span style="color: var(--text-strong-color); font-weight: var(--fw-bold, bold);">🕒 기록 일시: ${entry.date}</span>
                <span style="font-size: 11px; color: var(--text-muted-color);">최초 작성: ${createdStr}${updatedStr && updatedStr !== createdStr ? ` | 최종 수정: ${updatedStr}` : ''}</span>
            </div>
        `;
        const tagsArr = entry.tags ? entry.tags.split(',').filter(Boolean) : [];
        const tagsHtml = tagsArr.length > 0 ? `<div style="margin-top: 8px;">` + tagsArr.map(t => `<span class="history-tag">#${highlight(t)}</span>`).join('') + `</div>` : '';
        const fileHtml = entry.attachedFile ? `
            <div style="margin-top: 10px; padding: 8px 12px; background: var(--bg-color); border: 1px solid var(--border-color); border-radius: 4px; display: inline-flex; align-items: center; gap: 8px; font-size: 12px;">
                <span>📎</span>
                <a href="${entry.attachedFile}" target="_blank" style="color: var(--primary-color); text-decoration: none; font-weight: bold; word-break: break-all;">${entry.attachedFileName || '첨부파일 다운로드'}</a>
            </div>
        ` : '';

        const safeStockName = entry.stockName ? entry.stockName.replace(/'/g, "\\'") : '';
        const displayStockName = highlight(entry.stockName);
        const stockBadge = entry.stockName ? `<span class="cal-badge stock" style="padding:4px 10px; border-radius:12px; font-size:0.95em; font-weight:bold; color:var(--text-strong-color); margin:0;" onclick="filterByStock('${safeStockName}', event)" title="${entry.stockName} 모아보기">🏷️ ${displayStockName}</span>` : '';

        if (entryType === 'memo') {
            card.style.borderLeftColor = 'var(--info-color)';
            const displayBroker = highlight(entry.brokerAccount);
            const brokerBadge = entry.brokerAccount ? `<span style="font-size: 0.85em; color: var(--text-muted-color); font-weight: normal; margin:0;">🏦 ${displayBroker}</span>` : '';
            const displayTitle = highlight(entry.title);
            const displayThoughts = highlight(entry.thoughts, true);
            card.innerHTML = `
            <div class="entry-header">
                ${timeDisplayHtml}
                <div class="header-right"><span>📝 일반 메모</span><button class="btn-edit">수정</button><button class="btn-delete">삭제</button></div>
            </div>
                <div class="entry-title" style="display: flex; align-items: center; flex-wrap: wrap; gap: 8px;">${stockBadge}<span style="margin:0;">${displayTitle}</span>${brokerBadge}</div>
                <div class="entry-content ql-snow" style="border:none; padding:0;"><div class="ql-editor" style="padding:0; min-height:auto; font-family:inherit; font-size:inherit;">${displayThoughts}</div></div>
                ${tagsHtml}
                ${fileHtml}
                ${imageHtml}
            `;
        } else {
            let typeColor = 'var(--text-muted-color)';
            let borderColor = 'var(--primary-color)';
            let badgeClass = 'trade';

            if(entry.tradeType === '매수') {
                typeColor = 'var(--danger-color)';
                borderColor = 'var(--danger-color)';
                badgeClass = 'buy';
            } else if(entry.tradeType === '매도') {
                typeColor = 'var(--primary-color)';
                borderColor = 'var(--primary-color)';
                badgeClass = 'sell';
            } else if(entry.tradeType === '주시' || entry.tradeType === '관망') {
                typeColor = 'var(--success-color)';
                borderColor = 'var(--success-color)';
                badgeClass = 'watch';
            }
            
            card.style.borderLeftColor = borderColor;

            let detailsHtml = '';
            if (entry.tradeType !== '관망' && entry.tradeType !== '주시' && (entry.price > 0 || entry.quantity > 0)) {
                const priceStr = entry.price ? entry.price.toLocaleString() : '0';
                const qtyStr = entry.quantity ? entry.quantity.toLocaleString() : '0';
                const totalAmount = (entry.price * entry.quantity).toLocaleString();
                detailsHtml = `
                    <div class="entry-details">
                        <div class="detail-item">단가: <span>${priceStr}</span></div>
                        <div class="detail-item">수량: <span>${qtyStr}주</span></div>
                        <div class="detail-item">총액: <span>${totalAmount}</span></div>
                    </div>
                `;
            }
            const tradeBadge = `<span style="background-color: ${typeColor}; color: white; padding:4px 8px; border-radius:12px; font-size:0.85em; font-weight:bold; margin:0;">${entry.tradeType}</span>`;
            const displayBroker = highlight(entry.brokerAccount);
            const brokerBadge = entry.brokerAccount ? `<span style="font-size: 0.85em; color: var(--text-muted-color); font-weight: normal; margin:0;">🏦 ${displayBroker}</span>` : '';
            const displayThoughts = highlight(entry.thoughts, true);
            card.innerHTML = `
            <div class="entry-header">
                ${timeDisplayHtml}
                <div class="header-right"><span>💼 ${entry.accountName}</span><button class="btn-edit">수정</button><button class="btn-delete">삭제</button></div>
            </div>
                <div class="entry-title" style="display: flex; align-items: center; flex-wrap: wrap; gap: 8px;">${stockBadge}${tradeBadge}${brokerBadge}</div>
                ${detailsHtml}
                <div class="entry-content ql-snow" style="border:none; padding:0;"><div class="ql-editor" style="padding:0; min-height:auto; font-family:inherit; font-size:inherit;">${displayThoughts}</div></div>
                ${tagsHtml}
                ${fileHtml}
                ${imageHtml}
            `;
        }

        const editBtn = card.querySelector('.btn-edit');
        editBtn.addEventListener('click', () => editEntry(entry));

        const deleteBtn = card.querySelector('.btn-delete');
        deleteBtn.addEventListener('click', () => deleteEntry(entry.id));

        historyList.appendChild(card);
    });

    // 스크롤 감지용 투명 요소(Sentinel) 추가
    if (end < currentFilteredEntries.length) {
        const sentinel = document.createElement('div');
        sentinel.id = 'scroll-sentinel';
        sentinel.style.padding = '20px';
        sentinel.style.textAlign = 'center';
        sentinel.style.color = 'var(--text-muted-color)';
        sentinel.style.fontSize = '12px';
        sentinel.innerHTML = '<span>⬇️ 스크롤하여 과거 기록 불러오는 중...</span>';
        historyList.appendChild(sentinel);
        loadMoreObserver.observe(sentinel);
    }
}

function editEntry(entry) {
    editingEntryId = entry.id;

    formModalOverlay.style.display = 'flex';
    document.body.style.overflow = 'hidden'; // ⭐️ 모달 열림 시 배경 스크롤 방지
    
    // ⭐️ 팝업 열릴 때 실제 화면 높이에 맞게 사이즈 조정 (키보드 대응)
    if (typeof window.updateFormContainerHeight === 'function') window.updateFormContainerHeight();

    const typeRadio = document.querySelector(`input[name="recordType"][value="${entry.type || 'trade'}"]`);
    if (typeRadio) {
        typeRadio.checked = true;
        toggleFormUI(entry.type || 'trade');
    }

    document.getElementById('stockName').value = entry.stockName || '';
    document.getElementById('stockCode').value = entry.stockCode || '';
    document.getElementById('brokerAccount').value = entry.brokerAccount || '';
    
    // ⭐️ 과거 하단에 첨부했던 이미지가 있다면 에디터 본문으로 자동 이동(마이그레이션)
    let contentHtml = entry.thoughts || '';
    if (entry.attachedImage && !contentHtml.includes(entry.attachedImage)) {
        contentHtml += `<p><br></p><p><img src="${entry.attachedImage}"></p>`;
    }
    if (window.quill) window.quill.root.innerHTML = contentHtml; // 에디터에 기존 내용 불러오기
    
    currentTags = entry.tags ? entry.tags.split(',').filter(Boolean) : [];
    renderTags();
    calcTotalAmount();
    
    // 일반 첨부파일 로드
    currentAttachedFile = entry.attachedFile || null;
    currentAttachedFileName = entry.attachedFileName || '';
    pendingUploadFile = null;
    const generalFileInput = document.getElementById('generalFileInput');
    const attachedFileNameDisplay = document.getElementById('attachedFileNameDisplay');
    const btnRemoveAttachedFile = document.getElementById('btnRemoveAttachedFile');
    if (generalFileInput) generalFileInput.value = '';
    if (currentAttachedFileName && attachedFileNameDisplay && btnRemoveAttachedFile) {
        attachedFileNameDisplay.innerText = currentAttachedFileName;
        btnRemoveAttachedFile.style.display = 'inline-block';
    } else if (attachedFileNameDisplay && btnRemoveAttachedFile) {
        attachedFileNameDisplay.innerText = '선택된 파일 없음';
        btnRemoveAttachedFile.style.display = 'none';
    }

    // ⭐️ 수정 시 기존에 기록된 '기록 일시'를 가져와서 설정
    if (window.tradeDatePicker) {
        const originalDate = entry.rawDate || new Date(entry.id).toISOString().slice(0, 16);
        window.tradeDatePicker.setDate(originalDate);
    } else {
        document.getElementById('tradeDate').value = entry.rawDate || new Date(entry.id).toISOString().slice(0, 16);
    }

    if (entry.type === 'memo') {
        document.getElementById('memoTitle').value = entry.title || '';
    } else {
        document.getElementById('accountName').value = entry.accountName || '';
        document.getElementById('tradeType').value = entry.tradeType || '매수';
        document.getElementById('price').value = entry.price || '';
        document.getElementById('quantity').value = entry.quantity || '';
    }

    submitBtn.innerText = "수정";
}

async function deleteEntry(id) {
    if (await customConfirm("정말로 이 기록을 삭제하시겠습니까?\n(삭제 후 로컬 파일에 즉시 반영됩니다)")) {
        try {
            const res = await fetch(`/api/entry/${id}`, {
                method: 'DELETE',
                headers: { 'ngrok-skip-browser-warning': 'true' }
            });
            if (res.ok) {
                cloudEntries = cloudEntries.filter(e => e.id !== id);
                displayEntries(true);
                updatePortfolioSummary();
                renderCalendar();
            } else { await customAlert("삭제에 실패했습니다."); }
        } catch(e) { await customAlert("삭제 중 오류가 발생했습니다."); }
    }
}

window.openImageViewer = function(src, event) {
    if (event) event.stopPropagation();
    const modal = document.getElementById('imageViewerModal');
    document.getElementById('fullSizeImage').src = src;
    modal.style.display = 'flex';
    document.body.style.overflow = 'hidden'; // ⭐️ 모달 열림 시 배경 스크롤 방지
};

let currentDate = new Date();
function renderCalendar() {
    const dailyStats = {};
    const portfolio = {};
    const chronological = [...cloudEntries].reverse();
    
    chronological.forEach(entry => {
        let dateKey = '';
        if (entry.rawDate) { dateKey = entry.rawDate.split('T')[0]; } 
        else if (entry.date) {
            const parts = entry.date.split('. ');
            if (parts.length >= 3) dateKey = `${parts[0]}-${parts[1].padStart(2,'0')}-${parts[2].split('.')[0].padStart(2,'0')}`;
        }
        if (!dateKey) return;
        
        if (!dailyStats[dateKey]) dailyStats[dateKey] = { profit: 0, details: {} };
        
        const stockKey = entry.stockName || '';
        if (!dailyStats[dateKey].details[stockKey]) dailyStats[dateKey].details[stockKey] = { buyCount: 0, sellCount: 0, watchCount: 0, memoCount: 0 };
        
        if (entry.type === 'trade') {
            if (entry.tradeType === '매수') dailyStats[dateKey].details[stockKey].buyCount++;
            else if (entry.tradeType === '매도') dailyStats[dateKey].details[stockKey].sellCount++;
            else if (entry.tradeType === '주시' || entry.tradeType === '관망') dailyStats[dateKey].details[stockKey].watchCount++;

            if (entry.stockName) {
                const stock = entry.stockName, qty = Number(entry.quantity) || 0, price = Number(entry.price) || 0;
                if (!portfolio[stock]) portfolio[stock] = { qty: 0, totalCost: 0, avgPrice: 0 };
                
                if (entry.tradeType === '매수') {
                    portfolio[stock].qty += qty; portfolio[stock].totalCost += (price * qty);
                    portfolio[stock].avgPrice = portfolio[stock].totalCost / portfolio[stock].qty;
                } else if (entry.tradeType === '매도') {
                    dailyStats[dateKey].profit += (price - portfolio[stock].avgPrice) * qty;
                    portfolio[stock].qty -= qty; portfolio[stock].totalCost -= (portfolio[stock].avgPrice * qty);
                    if (portfolio[stock].qty <= 0) { portfolio[stock].qty = 0; portfolio[stock].totalCost = 0; portfolio[stock].avgPrice = 0; }
                }
            }
        } else if (entry.type === 'memo') {
            dailyStats[dateKey].details[stockKey].memoCount++;
        }
    });

    const year = currentDate.getFullYear(), month = currentDate.getMonth();
    document.getElementById('calendarMonthTitle').innerText = `${year}년 ${month + 1}월`;
    
    const firstDay = new Date(year, month, 1), lastDay = new Date(year, month + 1, 0);
    const calendarGrid = document.getElementById('calendarGrid');
    calendarGrid.innerHTML = '';
    
    for(let i=0; i<firstDay.getDay(); i++) calendarGrid.innerHTML += `<div style="background:var(--border-light-color); border-radius:4px;"></div>`;
    
    for(let d=1; d<=lastDay.getDate(); d++) {
        const key = `${year}-${String(month+1).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
        
        const dStats = dailyStats[key] || { profit: 0, details: {} };
        
        let profitHtml = '';
        if (dStats.profit > 0) profitHtml = `<div style="color:var(--danger-color); font-size:11px; font-weight:var(--fw-bold, bold); margin-bottom:2px;">+${Math.round(dStats.profit).toLocaleString()}</div>`;
        else if (dStats.profit < 0) profitHtml = `<div style="color:var(--primary-color); font-size:11px; font-weight:var(--fw-bold, bold); margin-bottom:2px;">${Math.round(dStats.profit).toLocaleString()}</div>`;
        
        let badgesHtml = '';
        for (const [stock, counts] of Object.entries(dStats.details)) {
            const prefix = stock ? `${stock} ` : '';
            const safeStock = stock ? stock.replace(/'/g, "\\'") : '';
            
            if (counts.buyCount > 0) {
                const typeArg = `stock_trade_${safeStock}`;
                badgesHtml += `<div class="cal-badge buy" onclick="showDetailsForDate('${key}', '${typeArg}', event)">${prefix}매수 ${counts.buyCount}건</div>`;
            }
            if (counts.sellCount > 0) {
                const typeArg = `stock_trade_${safeStock}`;
                badgesHtml += `<div class="cal-badge sell" onclick="showDetailsForDate('${key}', '${typeArg}', event)">${prefix}매도 ${counts.sellCount}건</div>`;
            }
            if (counts.watchCount > 0) {
                const typeArg = `stock_trade_${safeStock}`;
                badgesHtml += `<div class="cal-badge watch" onclick="showDetailsForDate('${key}', '${typeArg}', event)">${prefix}주시 ${counts.watchCount}건</div>`;
            }
            if (counts.memoCount > 0) {
                const typeArg = `stock_memo_${safeStock}`;
                badgesHtml += `<div class="cal-badge memo" onclick="showDetailsForDate('${key}', '${typeArg}', event)">${prefix}메모 ${counts.memoCount}건</div>`;
            }
        }
        
        // ⭐️ 오늘 날짜인지 판별하여 강조 클래스 및 스타일 적용
        const realToday = new Date();
        const isToday = (year === realToday.getFullYear() && month === realToday.getMonth() && d === realToday.getDate());
        const todayClass = isToday ? ' today' : '';
        const daySpanHtml = isToday 
            ? `<span style="background: var(--primary-color); color: white; padding: 1px 6px; border-radius: 10px; font-size: 11px; font-weight: bold; display: inline-block;">${d}</span>` 
            : `<span style="font-size:12px; font-weight:var(--fw-bold, bold); color: var(--text-strong-color);">${d}</span>`;
        
        calendarGrid.innerHTML += `<div class="calendar-day${todayClass}" onclick="showDetailsForDate('${key}', 'all', event)" title="클릭하여 상세 보기">${daySpanHtml}<div style="text-align:right;">${profitHtml}${badgesHtml}</div></div>`;
    }
}

window.showDetailsForDate = function(date, type, event) {
    if (event) event.stopPropagation();
    currentFilterDate = date;
    currentFilterType = type;
    
    document.getElementById('btnListView').click();
    displayEntries(true);

    window.scrollToFilterBox();
};

window.filterByStock = function(stockName, event) {
    if (event) event.stopPropagation();
    currentFilterDate = null;
    currentFilterType = 'stock_' + stockName;
    
    const btnListView = document.getElementById('btnListView');
    if (btnListView && !btnListView.classList.contains('active')) {
        btnListView.click();
    }
    
    displayEntries(true);
    window.scrollToFilterBox();
};

window.clearDateFilter = function() {
    currentFilterDate = null;
    currentFilterType = null;
    displayEntries(true);

    window.scrollToFilterBox();
};

document.getElementById('btnListView').addEventListener('click', function() {
    this.classList.add('active'); document.getElementById('btnCalendarView').classList.remove('active');
    document.getElementById('historyList').style.display = 'flex';
    document.getElementById('calendarViewSection').style.display = 'none';
    document.getElementById('filterBoxContainer').style.display = 'block';
    const btnToggleHistoryClosed = document.getElementById('btnToggleHistoryClosed');
    if (btnToggleHistoryClosed) btnToggleHistoryClosed.style.display = 'inline-block';
});

document.getElementById('btnCalendarView').addEventListener('click', function() {
    this.classList.add('active'); document.getElementById('btnListView').classList.remove('active');
    document.getElementById('historyList').style.display = 'none';
    document.getElementById('calendarViewSection').style.display = 'block';
    document.getElementById('filterBoxContainer').style.display = 'none';
    const btnToggleHistoryClosed = document.getElementById('btnToggleHistoryClosed');
    if (btnToggleHistoryClosed) btnToggleHistoryClosed.style.display = 'none';
    renderCalendar();
});

document.getElementById('btnPrevMonth').addEventListener('click', () => { currentDate.setMonth(currentDate.getMonth() - 1); renderCalendar(); });
document.getElementById('btnNextMonth').addEventListener('click', () => { currentDate.setMonth(currentDate.getMonth() + 1); renderCalendar(); });

// ⭐️ 모바일 당겨서 새로고침 (Pull-to-Refresh) 기능
let ptrStartY = 0;
let ptrCurrentY = 0;
let isPulling = false;
const ptrThreshold = 150; // ⭐️ 당겨야 하는 기준 픽셀 (기존 80에서 증가시켜 민감도 대폭 낮춤)

window.addEventListener('touchstart', (e) => {
    // ⭐️ 모달(입력창 팝업 등)이 열려있을 때는 당겨서 새로고침 동작 방지
    if (document.body.style.overflow === 'hidden') return;

    if (window.scrollY <= 0) {
        ptrStartY = e.touches[0].clientY;
        ptrCurrentY = ptrStartY;
        isPulling = true;
        const ptrIndicator = document.getElementById('ptrIndicator');
        if (ptrIndicator) ptrIndicator.style.transition = 'none';
    }
}, { passive: true });

window.addEventListener('touchmove', (e) => {
    if (!isPulling) return;
    ptrCurrentY = e.touches[0].clientY;
    const distance = ptrCurrentY - ptrStartY;

    // 화면 맨 위에서 아래로 당길 때만 작동
    if (distance > 0 && window.scrollY <= 0) {
        const ptrIndicator = document.getElementById('ptrIndicator');
        const ptrSpinner = document.getElementById('ptrSpinner');
        const ptrText = document.getElementById('ptrText');
        if (ptrIndicator && ptrSpinner && ptrText) {
            ptrIndicator.style.opacity = Math.min(distance / 60, 1).toString();
            // 화면에 더 묵직하게 당겨지도록 distance / 2.5 로 계산
            ptrIndicator.style.top = `${Math.min((distance / 2.5) - 50, 0)}px`;
            ptrSpinner.style.transform = `rotate(${distance * 1.5}deg)`;
            ptrText.innerText = distance > ptrThreshold ? '손을 놓아서 새로고침' : '아래로 당겨서 새로고침';
        }
    }
}, { passive: true });

window.addEventListener('touchend', () => {
    if (!isPulling) return;
    isPulling = false;
    const distance = ptrCurrentY - ptrStartY;
    const ptrIndicator = document.getElementById('ptrIndicator');
    const ptrSpinner = document.getElementById('ptrSpinner');
    const ptrText = document.getElementById('ptrText');

    if (ptrIndicator && ptrSpinner && ptrText) {
        ptrIndicator.style.transition = 'top 0.3s ease, opacity 0.3s ease';
        if (distance > ptrThreshold && window.scrollY <= 0) {
            ptrIndicator.style.top = '0px';
            ptrText.innerText = '화면을 새로고침합니다...';
            ptrSpinner.classList.add('spinning');
            setTimeout(() => { window.location.reload(); }, 400);
        } else {
            ptrIndicator.style.top = '-50px';
            ptrIndicator.style.opacity = '0';
            setTimeout(() => {
                ptrSpinner.style.transform = 'rotate(0deg)';
                ptrSpinner.classList.remove('spinning');
            }, 300);
        }
    }
});

// ⭐️ 비밀번호 변경 모달 이벤트 연결
const passwordModalOverlay = document.getElementById('passwordModalOverlay');
const btnChangePassword = document.getElementById('btnChangePassword');
const btnClosePasswordModal = document.getElementById('btnClosePasswordModal');
const passwordForm = document.getElementById('passwordForm');

if (btnChangePassword && passwordModalOverlay) {
    btnChangePassword.addEventListener('click', () => {
        passwordModalOverlay.style.display = 'flex';
        document.body.style.overflow = 'hidden';
    });
    
    const closePwModal = () => {
        passwordModalOverlay.classList.add('closing');
        setTimeout(() => {
            passwordModalOverlay.style.display = 'none';
            passwordModalOverlay.classList.remove('closing');
            document.body.style.overflow = '';
            if(passwordForm) passwordForm.reset();
        }, 180);
    };
    
    if (btnClosePasswordModal) btnClosePasswordModal.addEventListener('click', closePwModal);
    
    if(passwordForm) passwordForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const current_password = document.getElementById('currentPassword').value;
        const new_password = document.getElementById('newPassword').value;
        const new_password_confirm = document.getElementById('newPasswordConfirm').value;
        
        if (new_password !== new_password_confirm) {
            await customAlert("새 비밀번호가 일치하지 않습니다.");
            return;
        }
        
        try {
            const submitBtn = passwordForm.querySelector('button[type="submit"]');
            const origText = submitBtn.innerText;
            submitBtn.innerText = '변경 중...';
            submitBtn.disabled = true;
            
            const res = await fetch('/api/change_password', {
                method: 'POST',
                headers: { 
                    'Content-Type': 'application/json',
                    'ngrok-skip-browser-warning': 'true'
                },
                body: JSON.stringify({ current_password, new_password })
            });
            
            const data = await res.json();
            if (res.ok && data.status === 'success') {
                await customAlert("비밀번호가 성공적으로 변경되었습니다.\n새로운 비밀번호로 다시 로그인해주세요.");
                window.location.href = '/logout'; // 로그아웃 처리하여 새 비번으로 로그인 유도
            } else {
                submitBtn.innerText = origText;
                submitBtn.disabled = false;
                await customAlert("변경 실패: " + (data.error || "알 수 없는 오류가 발생했습니다."));
            }
        } catch(err) {
            const submitBtn = passwordForm.querySelector('button[type="submit"]');
            submitBtn.innerText = '변경하기';
            submitBtn.disabled = false;
            await customAlert("비밀번호 변경 중 오류가 발생했습니다.");
        }
    });
}