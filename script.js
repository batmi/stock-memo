// ⭐️ 전역 에러 핸들러 추가 (화면 렌더링 전 발생하는 치명적 에러 감지용)
window.addEventListener('error', function(e) {
    console.error("[Global Error] JS 에러 발생:", e.message, "위치:", e.filename, "라인:", e.lineno);
});
window.addEventListener('unhandledrejection', function(e) {
    console.error("[Unhandled Promise Rejection] 처리되지 않은 비동기 에러:", e.reason);
});

// ⭐️ 모바일 네트워크(셀룰러↔Wi-Fi 전환, 터널 지연 등)에서 fetch가 응답·실패 없이 무한 정지(stall)하면
//    화면이 로딩 상태로 영영 고착된다. AbortController로 타임아웃을 강제해 무한 대기를 방지한다.
function fetchWithTimeout(url, options = {}, timeout = 15000) {
    const controller = new AbortController();
    const id = setTimeout(() => controller.abort(), timeout);
    return fetch(url, { ...options, signal: controller.signal })
        .finally(() => clearTimeout(id));
}

// ⭐️ HTML head 인라인 스크립트(window.__initialFetches)가 문서 파싱 시작 시점에
//    미리 발사해둔 초기 API 요청을 이어받는다. (라이브러리 로딩과 데이터 수신 병렬화)
//    프리페치가 없거나 실패(null)·15초 내 미완료 시 새 요청으로 폴백한다.
function initialFetchOrFresh(key, url) {
    const pre = window.__initialFetches;
    const prefetched = pre && pre[key];
    if (pre) pre[key] = null; // 재시도(visibilitychange 등) 시에는 항상 새 요청 사용
    if (!prefetched) return fetchWithTimeout(url);
    const timeout = new Promise((resolve) => setTimeout(() => resolve(null), 15000));
    return Promise.race([prefetched, timeout]).then(res => res ? res : fetchWithTimeout(url));
}

let cloudEntries = [];
let currentHoldings = [];
let newsInterval = null;
let currentFilterDate = null;
let currentFilterRecordType = 'all'; // ⭐️ 독립 필터 1 (기록/매매)
let currentFilterStock = 'all';      // ⭐️ 독립 필터 2 (종목별)
let currentFilterAccount = 'all';    // ⭐️ 독립 필터 3 (분류별)
let currentFilterBroker = 'all';     // ⭐️ 독립 필터 4 (증권사별)
let currentFilterSubAccount = 'all'; // ⭐️ 독립 필터 5 (계좌별)
let currentFilterKeywords = []; // ⭐️ 다중 키워드 필터용 배열
let isDashboardCollapsed = false;
let showClosedPositions = false; // 청산 종목 보기 상태
let showCurrentPrice = true; // ⭐️ 현재가 및 평가금액 보기 상태 (기본값: 보기)
let currentMarketMode = 'NXT'; // ⭐️ KRX/NXT 토글 상태 (기본값 NXT)
let currentPortfolioArrayForPrice = []; // 현재가 계산용 임시 배열
let showHistoryClosedPositions = true; // ⭐️ 기본적으로 히스토리에 청산 종목을 표시하도록 변경
let currentDashboardBroker = 'all'; // 대시보드 증권사 필터 상태
let currentDashboardSubAccount = 'all'; // 대시보드 계좌 필터 상태
let currentDashboardAccount = 'all'; // 대시보드 투자 분류 필터 상태
let priceUpdateInterval = null; // ⭐️ 타이머 변수 선언 누락 수정
let currentFilteredEntries = [];
let currentRenderPage = 1;
const entriesPerPage = 15;
let lastRenderedMonth = '';
let userPreferences = {};       // ⭐️ 사용자별 설정(포트폴리오 정렬 순서 등) 저장
let portfolioSortable = null;   // ⭐️ SortableJS 드래그 앤 드롭 인스턴스
window.currentPriceCache = {};  // ⭐️ 장 종료 시 이전 가격을 유지하기 위한 전역 캐시
window.monthlyProfitChartInstance = null; // ⭐️ 월별 손익 차트 인스턴스 변수 추가
window.currentChartGranularity = window.currentChartGranularity || 'monthly'; // ⭐️ 차트 집계 단위 (monthly/weekly, 기본 월간)

// ⭐️ 차트 전용 독립 필터 상태 변수
let currentChartStock = 'all';
let currentChartAccount = 'all';
let currentChartBroker = 'all';
let currentChartSubAccount = 'all';

// ⭐️ 공통 스크롤 함수: 스크롤 튐 현상을 막기 위해 window.scrollTo 절대 좌표 사용
window.scrollToFilterBox = function() {
    // "TRADE HISTORY" 타이틀이 포함된 history-header 영역을 찾아 최상단으로 스크롤
    const historyHeader = document.querySelector('.history-header');
    if (!historyHeader) return;
    const y = historyHeader.getBoundingClientRect().top + window.scrollY - 20; // 상단 여백 20px
    window.scrollTo({ top: Math.max(0, y), behavior: 'smooth' });
};

let customModalTimeout = null; // ⭐️ 연속 모달 호출 시 타이머 꼬임 방지용 전역 변수

// ⭐️ 커스텀 공통 모달 (Alert, Confirm, Prompt 대체용)
window.customModal = function({ type = 'alert', title = '알림', message = '', inputPlaceholder = '' }) {
    return new Promise((resolve) => {
        const overlay = document.getElementById('customModalOverlay');
        if (!overlay) return resolve(type === 'prompt' ? null : true); // HTML 로드 전 폴백
        
        // ⭐️ 이전 모달의 닫힘 애니메이션(180ms) 타이머가 새 모달을 닫아버리는 버그 완벽 차단
        if (customModalTimeout) clearTimeout(customModalTimeout);
        overlay.classList.remove('closing');
        
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
            customModalTimeout = setTimeout(() => { overlay.style.display = 'none'; overlay.classList.remove('closing'); }, 180);
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

// ⭐️ 전역 로딩 오버레이 제어 함수 (백업/원복/엑셀 등 긴 작업 시)
let loadingStartTime = 0;
const MIN_LOADING_TIME = 1000; // 최소 노출 시간 설정 (1000ms = 1초)

window.showLoadingOverlay = function(message = '처리 중입니다...') {
    const overlay = document.getElementById('loadingOverlay');
    const textEl = document.getElementById('loadingText');
    if (overlay && textEl) {
        loadingStartTime = Date.now(); // ⭐️ 로딩이 시작된 정확한 시간 기록
        textEl.innerText = message;
        overlay.style.display = 'flex';
    }
};
window.hideLoadingOverlay = function() {
    return new Promise((resolve) => {
        const overlay = document.getElementById('loadingOverlay');
        if (!overlay || overlay.style.display === 'none') return resolve();
        
        const elapsedTime = Date.now() - loadingStartTime;
        if (elapsedTime < MIN_LOADING_TIME) {
            // ⭐️ 작업이 너무 빨리 끝났다면, 남은 시간만큼 기다렸다가 숨김 처리
            setTimeout(() => { 
                overlay.style.display = 'none'; 
                resolve();
            }, MIN_LOADING_TIME - elapsedTime);
        } else {
            // ⭐️ 이미 1초 이상 지났다면 즉시 숨김 처리
            overlay.style.display = 'none';
            resolve();
        }
    });
};

// ⭐️ 세션 만료 관리 — 서버가 로그인 시점에 확정한 절대 만료 시각(expires_at) 기준으로 동작
//   - 로그인 유지 미선택(1시간): 만료 5분 전 연장 팝업 표시, "연장하기" 선택 시 1시간 단위로 반복 연장
//   - 로그인 유지 선택(24시간): 연장 팝업 없이 만료 시각에 자동 로그아웃
let warningTimer;
let logoutTimer;
let countdownInterval;
let sessionExpiresAtMs = window.SESSION_EXPIRES_AT_MS || (Date.now() + 60 * 60 * 1000);
const SESSION_KEEP_LOGGED_IN = window.SESSION_KEEP_LOGGED_IN === true;
const WARNING_BEFORE = 5 * 60 * 1000; // 만료 5분 전 경고 (밀리초)

function scheduleSessionTimers() {
    clearTimeout(warningTimer);
    clearTimeout(logoutTimer);
    clearInterval(countdownInterval);

    const remainingMs = sessionExpiresAtMs - Date.now();
    if (remainingMs <= 0) {
        window.location.href = '/logout?timeout=1';
        return;
    }

    // 로그인 유지(24시간) 세션은 연장 개념이 없으므로 만료 시각에 자동 로그아웃만 예약
    if (SESSION_KEEP_LOGGED_IN) {
        logoutTimer = setTimeout(() => { window.location.href = '/logout?timeout=1'; }, remainingMs);
        return;
    }

    if (remainingMs <= WARNING_BEFORE) {
        showExtensionWarning();
    } else {
        warningTimer = setTimeout(showExtensionWarning, remainingMs - WARNING_BEFORE);
    }
}

function showExtensionWarning() {
    // ⭐️ 브라우저 백그라운드 지연으로 인해 이미 만료된 경우 즉시 로그아웃
    const remainingMs = sessionExpiresAtMs - Date.now();
    if (remainingMs <= 0) {
        window.location.href = '/logout?timeout=1';
        return;
    }

    const extensionModal = document.getElementById('sessionExtensionModalOverlay');
    const countdownEl = document.getElementById('sessionCountdown');
    if (!extensionModal || !countdownEl) return;
    if (extensionModal.style.display === 'flex') return; // 이미 표시 중이면 중복 방지

    extensionModal.style.display = 'flex';

    // ⭐️ 남은 시간 5분을 고정하지 않고 실제 잔여 시간으로 카운트다운
    let timeLeft = Math.floor(remainingMs / 1000);
    countdownEl.innerText = `${Math.floor(timeLeft / 60).toString().padStart(2, '0')}:${(timeLeft % 60).toString().padStart(2, '0')}`;

    countdownInterval = setInterval(() => {
        // ⭐️ 백그라운드 스로틀링으로 인터벌이 밀려도 정확하도록 실제 만료 시각에서 매번 재계산
        timeLeft = Math.floor((sessionExpiresAtMs - Date.now()) / 1000);
        if (timeLeft <= 0) {
            clearInterval(countdownInterval);
            window.location.href = '/logout?timeout=1'; // 타이머가 0이 되면 즉시 이동
        } else {
            countdownEl.innerText = `${Math.floor(timeLeft / 60).toString().padStart(2, '0')}:${(timeLeft % 60).toString().padStart(2, '0')}`;
        }
    }, 1000);

    logoutTimer = setTimeout(() => {
        window.location.href = '/logout?timeout=1';
    }, remainingMs);
}

// ⭐️ 워치독: 절전 모드·백그라운드 스로틀링으로 setTimeout 이 밀리거나 유실되어도
//    30초마다 실제 잔여 시간을 재확인하여 연장 팝업 표시와 자동 로그아웃을 보장
setInterval(() => {
    const remainingMs = sessionExpiresAtMs - Date.now();
    if (remainingMs <= 0) {
        window.location.href = '/logout?timeout=1';
        return;
    }
    if (!SESSION_KEEP_LOGGED_IN && remainingMs <= WARNING_BEFORE) {
        showExtensionWarning(); // 이미 표시 중이면 내부에서 중복 방지됨
    }
}, 30000);

// ⭐️ 브라우저 탭 활성화 시 실제 만료 여부를 확인하여 동기화
document.addEventListener('visibilitychange', async () => {
    if (document.visibilityState === 'visible') {
        // ⭐️ 모바일에서 앱 전환 후 복귀 시, 초기 로딩이 실패해 멈춰 있던 상태면 자동으로 재시도
        if (window.__dataLoadFailed) {
            loadDataFromLocal();
        }

        if (Date.now() >= sessionExpiresAtMs) {
            // 백그라운드에 있는 동안 이미 만료되었다면 즉시 자동 로그아웃 처리
            window.location.href = '/logout?timeout=1';
            return;
        }

        // ⭐️ 서버에 API를 호출하여 실제 세션 만료 여부(또는 타 탭 로그아웃 여부) 체크
        try {
            const res = await fetch('/api/me');
            if (res.status === 401) {
                window.location.href = '/logout?timeout=1';
                return;
            }
        } catch(e) { console.warn("세션 상태 확인 실패", e); }

        // ⭐️ 백그라운드에서 setTimeout 이 지연되었을 수 있으므로 타이머 재정렬 (팝업 표시 중이면 유지)
        const extensionModal = document.getElementById('sessionExtensionModalOverlay');
        if (!extensionModal || extensionModal.style.display !== 'flex') {
            scheduleSessionTimers();
        }
    }
});

scheduleSessionTimers(); // 초기 타이머 시작

const mainApp = document.getElementById('mainApp');
window.addEventListener('DOMContentLoaded', () => {
    console.log("[App Init] DOMContentLoaded 이벤트 시작 - DOM 로드 완료");
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
        if (window.monthlyProfitChartInstance) {
            window.renderMonthlyProfitChart();
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
                const isExpanded = document.getElementById('newsList')?.classList.contains('news-expanded');
                btnToggleNews.style.backgroundColor = isExpanded ? 'transparent' : 'var(--primary-color)';
                btnToggleNews.style.color = isExpanded ? 'var(--primary-color)' : '#fff';
            } else if (btnToggleNews) {
                btnToggleNews.style.display = 'inline-block';
                const isExpanded = document.getElementById('newsList')?.classList.contains('news-expanded');
                btnToggleNews.style.backgroundColor = isExpanded ? 'transparent' : 'var(--primary-color)';
                btnToggleNews.style.color = isExpanded ? 'var(--primary-color)' : '#fff';
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
            }
            if (btnToggleNews) {
                btnToggleNews.innerText = '펼치기 ▼';
                btnToggleNews.style.backgroundColor = 'var(--primary-color)';
                btnToggleNews.style.color = '#fff';
            }
        }
    }

    let resizeTimer;
    window.addEventListener('resize', () => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(applyMobileResponsiveLayout, 150);
    });
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
                    btnToggleNews.style.backgroundColor = isExpanded ? 'transparent' : 'var(--primary-color)';
                    btnToggleNews.style.color = isExpanded ? 'var(--primary-color)' : '#fff';
            if (!isExpanded) {
                newsList.scrollLeft = 0; // 가로 스크롤 원위치
            }
        });
    }

    // ⭐️ 뉴스 수동 새로고침 버튼 이벤트 (HTML에 id="btnRefreshNews" 버튼이 있을 경우 동작)
    const btnRefreshNews = document.getElementById('btnRefreshNews');
    if (btnRefreshNews) {
        btnRefreshNews.addEventListener('click', () => {
            fetchRealtimeNews(true); // 수동 갱신 시 캐시 무시 강제 갱신
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
        btnToggleCurrentPrice.style.backgroundColor = showCurrentPrice ? 'transparent' : 'var(--primary-color)';
        btnToggleCurrentPrice.style.color = showCurrentPrice ? 'var(--primary-color)' : '#fff';

        btnToggleCurrentPrice.addEventListener('click', () => {
            showCurrentPrice = !showCurrentPrice;
            btnToggleCurrentPrice.innerText = showCurrentPrice ? '현재가 숨기기' : '현재가 보기';
            btnToggleCurrentPrice.style.backgroundColor = showCurrentPrice ? 'transparent' : 'var(--primary-color)';
            btnToggleCurrentPrice.style.color = showCurrentPrice ? 'var(--primary-color)' : '#fff';
            userPreferences.showCurrentPrice = showCurrentPrice;
            savePreferences();
            
            updatePortfolioSummary(); // UI 리렌더링 및 현재가 fetch 트리거
            
            // ⭐️ 현재가 보기 상태에 따라 1분(60초) 자동 업데이트 타이머 켜기/끄기
            if (showCurrentPrice) {
                if (priceUpdateInterval !== null) clearInterval(priceUpdateInterval);
                priceUpdateInterval = setInterval(() => {
                    window.fetchCurrentPricesAndUpdateUI(true); // isAuto = true 로 자동 갱신 요청
                }, 60000); // 60초(60000ms) 주기
            } else {
                if (priceUpdateInterval !== null) clearInterval(priceUpdateInterval);
            }
        });
    }

    // ⭐️ KRX/NXT 토글 버튼 이벤트 연결
    const btnToggleMarketMode = document.getElementById('btnToggleMarketMode');
    if (btnToggleMarketMode) {
        btnToggleMarketMode.innerText = currentMarketMode === 'NXT' ? 'NXT' : 'KRX';
        btnToggleMarketMode.style.backgroundColor = currentMarketMode === 'NXT' ? 'transparent' : 'var(--primary-color)';
        btnToggleMarketMode.style.color = currentMarketMode === 'NXT' ? 'var(--primary-color)' : '#fff';

        btnToggleMarketMode.addEventListener('click', () => {
            currentMarketMode = currentMarketMode === 'NXT' ? 'KRX' : 'NXT';
            btnToggleMarketMode.innerText = currentMarketMode === 'NXT' ? 'NXT' : 'KRX';
            btnToggleMarketMode.style.backgroundColor = currentMarketMode === 'NXT' ? 'transparent' : 'var(--primary-color)';
            btnToggleMarketMode.style.color = currentMarketMode === 'NXT' ? 'var(--primary-color)' : '#fff';
            
            userPreferences.currentMarketMode = currentMarketMode;
            savePreferences();
            
            window.fetchCurrentPricesAndUpdateUI(false); // 모드 변경 시 즉시 갱신
        });
    }

    // ⭐️ 대시보드 필터(큰 범위) 조작 시, 종목 등 세부 필터는 해제하여 직관적인 결과 제공
    function handleDashboardFilterChange(type, value) {
        const prevBroker = currentDashboardBroker;
        const prevSubAccount = currentDashboardSubAccount;
        const prevAccount = currentDashboardAccount;

        // 세부 필터를 포함해 완전히 초기화
        clearAllFilters(false);

        let newBroker = prevBroker;
        let newSubAccount = prevSubAccount;
        let newAccount = prevAccount;

        if (type === 'broker') newBroker = value;
        if (type === 'subAccount') newSubAccount = value;
        if (type === 'account') newAccount = value;

        currentDashboardBroker = newBroker;
        currentFilterBroker = newBroker;
        currentDashboardSubAccount = newSubAccount;
        currentFilterSubAccount = newSubAccount;
        currentDashboardAccount = newAccount;
        currentFilterAccount = newAccount;

        const topBroker = document.getElementById('dashboardBrokerFilter');
        if (topBroker) { topBroker.value = newBroker; window.updateDashboardFilterStyle(topBroker); }
        const topSubAccount = document.getElementById('dashboardSubAccountFilter');
        if (topSubAccount) { topSubAccount.value = newSubAccount; window.updateDashboardFilterStyle(topSubAccount); }
        const topAccount = document.getElementById('dashboardAccountFilter');
        if (topAccount) { topAccount.value = newAccount; window.updateDashboardFilterStyle(topAccount); }

        const bottomBroker = document.getElementById('filterBrokerSelect');
        if (bottomBroker) { bottomBroker.value = newBroker; window.updateDashboardFilterStyle(bottomBroker); }
        const bottomSubAccount = document.getElementById('filterSubAccountSelect');
        if (bottomSubAccount) { bottomSubAccount.value = newSubAccount; window.updateDashboardFilterStyle(bottomSubAccount); }
        const bottomAccount = document.getElementById('filterAccountSelect');
        if (bottomAccount) { bottomAccount.value = newAccount; window.updateDashboardFilterStyle(bottomAccount); }

        window.saveFilterPreferences();
        updatePortfolioSummary();
        displayEntries(true);
    }

    // ⭐️ 대시보드 증권사 필터 이벤트 연결
    const dashboardBrokerFilter = document.getElementById('dashboardBrokerFilter');
    if (dashboardBrokerFilter) {
        dashboardBrokerFilter.addEventListener('change', (e) => {
            handleDashboardFilterChange('broker', e.target.value);
        });
    }

    // ⭐️ 대시보드 증권계좌 필터 이벤트 연결
    const dashboardSubAccountFilter = document.getElementById('dashboardSubAccountFilter');
    if (dashboardSubAccountFilter) {
        dashboardSubAccountFilter.addEventListener('change', (e) => {
            handleDashboardFilterChange('subAccount', e.target.value);
        });
    }

    // ⭐️ 대시보드 투자 분류 필터 이벤트 연결
    const dashboardAccountFilter = document.getElementById('dashboardAccountFilter');
    if (dashboardAccountFilter) {
        dashboardAccountFilter.addEventListener('change', (e) => {
            handleDashboardFilterChange('account', e.target.value);
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

    // ⭐️ 5개의 독립된 필터 컨트롤 체인지 이벤트 연결
    const selectors = [
        { id: 'filterRecordTypeSelect', setter: (val) => currentFilterRecordType = val },
        { id: 'filterStockSelect', setter: (val) => currentFilterStock = val },
        { id: 'filterAccountSelect', setter: (val) => {
            currentFilterAccount = val;
            currentDashboardAccount = val; // ⭐️ 상단 필터 동기화
            const topEl = document.getElementById('dashboardAccountFilter');
            if (topEl) { topEl.value = val; window.updateDashboardFilterStyle(topEl); }
            updatePortfolioSummary();
        }},
        { id: 'filterBrokerSelect', setter: (val) => {
            currentFilterBroker = val;
            currentDashboardBroker = val; // ⭐️ 상단 필터 동기화
            const topEl = document.getElementById('dashboardBrokerFilter');
            if (topEl) { topEl.value = val; window.updateDashboardFilterStyle(topEl); }
            updatePortfolioSummary();
        }},
        { id: 'filterSubAccountSelect', setter: (val) => {
            currentFilterSubAccount = val;
            currentDashboardSubAccount = val; // ⭐️ 상단 필터 동기화
            const topEl = document.getElementById('dashboardSubAccountFilter');
            if (topEl) { topEl.value = val; window.updateDashboardFilterStyle(topEl); }
            updatePortfolioSummary();
        }}
    ];

    selectors.forEach(sel => {
        const el = document.getElementById(sel.id);
        if (el) {
            el.addEventListener('change', (e) => {
                sel.setter(e.target.value);
                window.updateDashboardFilterStyle(e.target);
                window.saveFilterPreferences();
                displayEntries(true);
                window.scrollToFilterBox();
            });
        }
    });

    // ⭐️ 캘린더 차트 전용 필터 컨트롤 이벤트 연결
    const chartFilters = [
        { id: 'chartStockFilter', setter: (val) => currentChartStock = val },
        { id: 'chartAccountFilter', setter: (val) => currentChartAccount = val },
        { id: 'chartBrokerFilter', setter: (val) => currentChartBroker = val },
        { id: 'chartSubAccountFilter', setter: (val) => currentChartSubAccount = val }
    ];
    chartFilters.forEach(sel => {
        const el = document.getElementById(sel.id);
        if (el) {
            el.addEventListener('change', (e) => {
                sel.setter(e.target.value);
                window.updateDashboardFilterStyle(e.target);
                window.saveChartFilterPreferences();
                window.renderMonthlyProfitChart(); // ⭐️ 필터 변경 시 차트 즉시 재렌더링
            });
        }
    });

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
                // 백엔드(Flask) 서버의 세션 만료 시각을 현재 기준 1시간 뒤로 재설정
                const res = await fetch('/api/ping', { method: 'POST'});
                const data = await res.json();
                if (data.expires_at) sessionExpiresAtMs = data.expires_at * 1000;
            } catch(e) {}

            extensionModal.classList.add('closing');
            setTimeout(() => {
                extensionModal.style.display = 'none';
                extensionModal.classList.remove('closing');
                scheduleSessionTimers(); // 연장된 만료 시각 기준으로 다음 팝업(만료 5분 전) 재예약
            }, 180);
        });
    }
    if (btnLogoutNow) btnLogoutNow.addEventListener('click', () => window.location.href = '/logout');

    // ⭐️ 커스텀 글자 크기(medium) 추가
    const Size = Quill.import('formats/size');
    Size.whitelist = ['small', false, 'medium', 'large', 'huge'];
    Quill.register(Size, true);

    // ⭐️ Quill 에디터 초기화
    window.quill = new Quill('#editor-container', {
        theme: 'snow',
        modules: {
            imageResize: {
                displaySize: true // 리사이즈 시 이미지 크기 툴팁 표시
            },
            toolbar: [
                [{ 'header': [1, 2, 3, false] }, { 'size': ['small', false, 'medium', 'large', 'huge'] }], // 헤더, 글자 크기
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
                // ⭐️ 툴바 삽입 시에도 커서 위치 동기적 캡처
                window.quill.focus();
                const range = window.quill.getSelection();
                const insertIndex = range ? range.index : window.quill.getLength();
                window.resizeAndInsertImageToQuill(file, insertIndex);
            }
        };
    });

    // ⭐️ 붙여넣기 시 외부 텍스트의 글자색/배경색 서식 강제 제거 (테마 색상 자동 적용)
    window.quill.clipboard.addMatcher(Node.ELEMENT_NODE, function(node, delta) {
        delta.ops.forEach(op => {
            // ⭐️ 텍스트(string)인 경우에만 서식(색상/배경)을 제거하여 이미지 등 임베드 요소 손상 원천 차단
            if (typeof op.insert === 'string' && op.attributes) {
                delete op.attributes.color;
                delete op.attributes.background;
            }
        });
        return delta;
    });

    const Delta = Quill.import('delta');

    // ⭐️ 에디터 본문 드래그 앤 드롭 이미지 삽입 지원
    window.quill.root.addEventListener('drop', function(e) {
        if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
            for (let i = 0; i < e.dataTransfer.files.length; i++) {
                const file = e.dataTransfer.files[i];
                if (file.type.startsWith('image/')) {
                    e.preventDefault();
                    e.stopPropagation();
                    
                    let insertIndex;
                    if (document.caretRangeFromPoint) {
                        const range = document.caretRangeFromPoint(e.clientX, e.clientY);
                        if (range) {
                            const sel = window.getSelection();
                            sel.removeAllRanges();
                            sel.addRange(range);
                            const qRange = window.quill.getSelection();
                            insertIndex = qRange ? qRange.index : window.quill.getLength();
                        }
                    }
                    window.resizeAndInsertImageToQuill(file, insertIndex);
                    break;
                }
            }
        }
    }, true); // ⭐️ capture 플래그 적용: Quill 내부 핸들러보다 우선 실행

    // ⭐️ 에디터 본문 클립보드 이미지 붙여넣기(Ctrl+V) 직접 연결 (충돌 해결)
    window.quill.root.addEventListener('paste', function(e) {
        if (e.clipboardData) {
            const types = e.clipboardData.types;
            
            // 1. 에디터 내부/웹에서 텍스트와 이미지를 함께 복사한 경우 (HTML 처리)
            if (types && Array.from(types).indexOf('text/html') !== -1) {
                let html = e.clipboardData.getData('text/html');
                
                // ⭐️ 거대한 Base64 이미지가 포함된 경우, 브라우저 DOM 파서가 개입하기 전 순수 문자열 상태에서 공백/줄바꿈을 즉각 제거 (투명화 버그 완벽 차단)
                if (html && html.includes('data:image/')) {
                    e.preventDefault();
                    e.stopPropagation();
                    
                    html = html.replace(/src\s*=\s*(['"])(data:image\/[^'"]+)\1/gi, function(match, quote, src) {
                        return `src=${quote}${src.replace(/\s+/g, '')}${quote}`;
                    });
                    
                    window.quill.focus();
                    const range = window.quill.getSelection();
                    const insertIndex = range ? range.index : window.quill.getLength();
                    
                    // 선택된 텍스트 영역이 있다면 덮어쓰기
                    if (range && range.length > 0) {
                        window.quill.deleteText(range.index, range.length, 'user');
                    }
                    
                    // 정제된 HTML을 안전한 Delta로 변환하여 에디터에 삽입
                    const delta = window.quill.clipboard.convert(html);
                    window.quill.updateContents(new Delta().retain(insertIndex).concat(delta), 'user');
                    
                    // 삽입된 콘텐츠 끝으로 커서 자동 이동
                    window.quill.setSelection(insertIndex + delta.length(), 'silent');
                    return;
                }
                // Base64 이미지가 없는 일반 텍스트 HTML이면 Quill 기본 동작에 위임
                return;
            }

            // 2. 스크린샷 등 순수 이미지 파일만 단독으로 붙여넣은 경우
            if (e.clipboardData.items) {
                const items = e.clipboardData.items;
                for (let i = 0; i < items.length; i++) {
                    if (items[i].type.indexOf('image') !== -1) {
                        const file = items[i].getAsFile();
                        if (file) {
                            e.preventDefault(); // 스크린샷 원본 용량 제한 방지
                            e.stopPropagation();
                            
                            window.quill.focus();
                            const range = window.quill.getSelection();
                            const insertIndex = range ? range.index : window.quill.getLength();
                            
                            window.resizeAndInsertImageToQuill(file, insertIndex);
                            return;
                        }
                    }
                }
            }
        }
    }, true); // ⭐️ capture 플래그 적용: Quill 내부 핸들러보다 우선 실행하여 락 걸림 방지

    const imageViewerModal = document.getElementById('imageViewerModal');
    const fullSizeImage = document.getElementById('fullSizeImage');

    if (imageViewerModal && fullSizeImage) {
        // ⭐️ 배경 클릭 시 닫기
        imageViewerModal.addEventListener('click', (e) => {
            if (e.target === imageViewerModal || e.target.id === 'imageViewerWrapper') {
                window.closeImageViewer();
            }
        });

        // ⭐️ 우측 상단 닫기 버튼 이벤트
        const btnImageViewerClose = document.getElementById('btnImageViewerClose');
        if (btnImageViewerClose) {
            btnImageViewerClose.addEventListener('click', (e) => {
                e.stopPropagation();
                window.closeImageViewer();
            });
        }

        // ⭐️ 이미지 한 번 클릭 시 확대/축소 (드래그 직후 클릭은 무시)
        let hasDragged = false;
        fullSizeImage.addEventListener('click', (e) => {
            e.stopPropagation();
            if (hasDragged) {
                hasDragged = false;
                return;
            }
            
            // ⭐️ 모바일 환경(터치 지원 기기)에서는 한 번 터치 시 팝업 닫기
            if ('ontouchstart' in window || navigator.maxTouchPoints > 0) {
                window.closeImageViewer();
                return;
            }
            
            // ⭐️ 데스크탑 환경에서는 한 번 클릭 시 확대/축소 토글
            if (imageZoom > 1) {
                imageZoom = 1;
                imagePanX = 0;
                imagePanY = 0;
            } else {
                imageZoom = 2.5;
            }
            updateImageViewerTransform();
        });

        // ⭐️ 마우스 휠 확대/축소
        imageViewerModal.addEventListener('wheel', (e) => {
            e.preventDefault();
            const delta = e.deltaY > 0 ? -0.15 : 0.15;
            imageZoom += delta;
            if (imageZoom < 1) {
                imageZoom = 1;
                imagePanX = 0;
                imagePanY = 0;
            }
            if (imageZoom > 5) imageZoom = 5;
            updateImageViewerTransform();
        }, { passive: false });

        // ⭐️ 마우스 드래그 (데스크탑 화면 팬 기능)
        fullSizeImage.addEventListener('mousedown', (e) => {
            hasDragged = false;
            if (imageZoom > 1) {
                e.preventDefault();
                imageIsDragging = true;
                imageStartX = e.clientX - imagePanX;
                imageStartY = e.clientY - imagePanY;
                updateImageViewerTransform();
            }
        });

        window.addEventListener('mousemove', (e) => {
            if (!imageIsDragging) return;
            hasDragged = true;
            imagePanX = e.clientX - imageStartX;
            imagePanY = e.clientY - imageStartY;
            updateImageViewerTransform();
        });

        window.addEventListener('mouseup', () => {
            if (imageIsDragging) {
                imageIsDragging = false;
                updateImageViewerTransform();
            }
        });

        // ⭐️ 모바일 터치 (핀치 줌 및 화면 패닝 기능)
        imageViewerModal.addEventListener('touchstart', (e) => {
            hasDragged = false;
            if (e.touches.length === 2) {
                e.preventDefault();
                imageIsPinching = true; // ⭐️ 핀치 줌 시작 플래그 활성화
                initialPinchDistance = Math.hypot(
                    e.touches[0].clientX - e.touches[1].clientX,
                    e.touches[0].clientY - e.touches[1].clientY
                );
                initialPinchZoom = imageZoom;
            } else if (e.touches.length === 1 && imageZoom > 1) {
                imageIsDragging = true;
                imageStartX = e.touches[0].clientX - imagePanX;
                imageStartY = e.touches[0].clientY - imagePanY;
            }
        }, { passive: false });

        imageViewerModal.addEventListener('touchmove', (e) => {
            if (e.touches.length === 2) {
                hasDragged = true;
                e.preventDefault();
                const currentDistance = Math.hypot(
                    e.touches[0].clientX - e.touches[1].clientX,
                    e.touches[0].clientY - e.touches[1].clientY
                );
                imageZoom = initialPinchZoom * (currentDistance / initialPinchDistance);
                if (imageZoom < 1) {
                    imageZoom = 1;
                    imagePanX = 0;
                    imagePanY = 0;
                }
                if (imageZoom > 5) imageZoom = 5;
                updateImageViewerTransform();
            } else if (e.touches.length === 1 && imageIsDragging) {
                hasDragged = true;
                e.preventDefault();
                imagePanX = e.touches[0].clientX - imageStartX;
                imagePanY = e.touches[0].clientY - imageStartY;
                updateImageViewerTransform();
            }
        }, { passive: false });

        imageViewerModal.addEventListener('touchend', (e) => {
            if (e.touches.length < 2) {
                initialPinchDistance = null;
                imageIsPinching = false; // ⭐️ 핀치 줌 종료
            }
            if (e.touches.length === 0 && imageIsDragging) {
                imageIsDragging = false;
            }
            updateImageViewerTransform(); // ⭐️ 상태 해제 후 즉각 렌더링에 반영 (트랜지션 원복)
            
            // ⭐️ 모바일 환경: 상/하단 여백(배경) 터치 시 팝업 닫기
            if (!hasDragged && e.changedTouches.length === 1) {
                if (e.target === imageViewerModal || e.target.id === 'imageViewerWrapper') {
                    if (e.cancelable) e.preventDefault(); // 중복 클릭 이벤트 방지
                    window.closeImageViewer();
                }
            }
        });
    }

    loadDataFromLocal();
});

// ⭐️ 계산 엔진(calc.js) 로드 보장 헬퍼
//    모바일에서 calc.js 가 (네트워크 불안정·캐시·호환성 등으로) 로드되지 않으면
//    전역 applyTradeToHolding 이 없어 displayEntries() 가 "Can't find variable" 로 중단된다.
//    이때 calc.js 를 동적으로 (재)주입하여 복구를 시도한다. 이미 정상 로드돼 있으면 즉시 통과.
function ensureCalcLoaded() {
    return new Promise((resolve, reject) => {
        if (typeof window.applyTradeToHolding === 'function') { resolve(); return; }
        console.warn("[Calc] 계산 엔진(applyTradeToHolding) 전역 누락 감지 - calc.js 동적 재로딩 시도");

        // 기존 calc.js 스크립트 태그의 src(버전 쿼리 포함)를 재사용하고 캐시를 무력화
        let baseSrc = null;
        const scripts = document.getElementsByTagName('script');
        for (let i = 0; i < scripts.length; i++) {
            const s = scripts[i].getAttribute('src') || '';
            if (s.indexOf('calc.js') !== -1) { baseSrc = s.split('&reload=')[0]; break; }
        }
        if (!baseSrc) baseSrc = '/calc.js';
        const url = baseSrc + (baseSrc.indexOf('?') !== -1 ? '&' : '?') + 'reload=' + Date.now();

        const tag = document.createElement('script');
        tag.src = url;
        tag.onload = () => {
            if (typeof window.applyTradeToHolding === 'function') {
                console.log("[Calc] calc.js 동적 재로딩 성공");
                resolve();
            } else {
                reject(new Error("계산 모듈(calc.js)을 불러왔으나 초기화되지 않았습니다 (브라우저 호환성 문제 가능성)"));
            }
        };
        tag.onerror = () => reject(new Error("계산 모듈(calc.js) 파일을 불러오지 못했습니다 (네트워크 연결을 확인해주세요)"));
        document.head.appendChild(tag);
    });
}

async function loadDataFromLocal() {
    console.log("[Data Load] loadDataFromLocal() 시작 - 사용자 데이터 호출 중...");
    try {
        // ⭐️ 초기 데이터 수신 동안 빈 화면(멈춘 것처럼 보이는 현상) 방지용 로딩 안내
        //    (재시도 시 기존 목록이 이미 렌더링돼 있으면 덮어쓰지 않는다)
        if (historyList && historyList.children.length === 0) {
            historyList.innerHTML = '<p style="text-align:center; color:var(--text-muted-color); font-size: 14px; padding: 30px 0;">⏳ 데이터를 불러오는 중입니다...</p>';
        }

        // ⭐️ 계산 엔진(calc.js)이 준비됐는지 먼저 확인·복구 (없으면 displayEntries 에서 크래시)
        await ensureCalcLoaded();

        // ⭐️ 초기 필수 데이터(사용자 정보, 환경설정, 매매기록)를 병렬로 호출하여 로딩 속도 최적화
        //    (HTML head 에서 미리 발사한 프리페치가 있으면 이어받아 대기 시간 단축)
        const [mePromise, prefPromise, dataPromise] = [
            initialFetchOrFresh('me', '/api/me').catch(e => { console.warn("사용자 정보 로드 실패", e); return null; }),
            initialFetchOrFresh('pref', '/api/preferences').catch(e => { console.warn("환경설정 로드 실패", e); return null; }),
            initialFetchOrFresh('data', '/api/data')
        ];

        console.log("[Data Load] 사용자 정보, 환경설정, 매매 기록 병렬 호출 시작...");
        const [meRes, prefRes, response] = await Promise.all([mePromise, prefPromise, dataPromise]);

        // 1. 사용자 정보 처리
        if (meRes && meRes.ok) {
            try {
                const meData = await meRes.json();
                if (meData.username) {
                    const userDisplay = document.getElementById('loggedInUserDisplay');
                    if (userDisplay) {
                        userDisplay.innerHTML = `<span style="font-size:12px;">👤</span> ${meData.username}`;
                        userDisplay.style.display = 'flex';
                    }
                    if (meData.is_admin) {
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
            } catch(e) { console.warn("사용자 정보 파싱 실패", e); }
        }

        // 2. 환경설정 처리
        if (prefRes && prefRes.ok) {
            try {
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
                        btnCP.style.backgroundColor = showCurrentPrice ? 'transparent' : 'var(--primary-color)';
                        btnCP.style.color = showCurrentPrice ? 'var(--primary-color)' : '#fff';
                    }

                    // ⭐️ 초기 로드 시 현재가 보기가 켜져있다면 1분(60초) 자동 업데이트 시작
                    if (showCurrentPrice) {
                        if (priceUpdateInterval !== null) clearInterval(priceUpdateInterval);
                        priceUpdateInterval = setInterval(() => {
                            window.fetchCurrentPricesAndUpdateUI(true); // isAuto = true 로 자동 갱신 요청
                        }, 60000);
                    }
                }
                
                // ⭐️ KRX/NXT 버튼 상태 복원
                if (typeof userPreferences.currentMarketMode !== 'undefined') {
                    currentMarketMode = userPreferences.currentMarketMode;
                    const btnMM = document.getElementById('btnToggleMarketMode');
                    if (btnMM) {
                        btnMM.innerText = currentMarketMode === 'NXT' ? 'NXT' : 'KRX';
                        btnMM.style.backgroundColor = currentMarketMode === 'NXT' ? 'transparent' : 'var(--primary-color)';
                        btnMM.style.color = currentMarketMode === 'NXT' ? 'var(--primary-color)' : '#fff';
                    }
                }
                
                // ⭐️ 대시보드 및 하단 리스트 필터 상태 복원 (어긋난 상태를 방지하기 위해 강제 동기화)
                if (userPreferences.currentDashboardBroker) { currentDashboardBroker = userPreferences.currentDashboardBroker; currentFilterBroker = currentDashboardBroker; }
                if (userPreferences.currentDashboardSubAccount) { currentDashboardSubAccount = userPreferences.currentDashboardSubAccount; currentFilterSubAccount = currentDashboardSubAccount; }
                if (userPreferences.currentDashboardAccount) { currentDashboardAccount = userPreferences.currentDashboardAccount; currentFilterAccount = currentDashboardAccount; }
                if (userPreferences.currentFilterRecordType) currentFilterRecordType = userPreferences.currentFilterRecordType;
                if (userPreferences.currentFilterStock) currentFilterStock = userPreferences.currentFilterStock;
                
                // 만약 하단 필터 설정이 따로 저장되어 있다면 덮어쓰기하며 대시보드와 동기화
                if (userPreferences.currentFilterAccount) { currentFilterAccount = userPreferences.currentFilterAccount; currentDashboardAccount = currentFilterAccount; }
                if (userPreferences.currentFilterBroker) { currentFilterBroker = userPreferences.currentFilterBroker; currentDashboardBroker = currentFilterBroker; }
                if (userPreferences.currentFilterSubAccount) { currentFilterSubAccount = userPreferences.currentFilterSubAccount; currentDashboardSubAccount = currentFilterSubAccount; }
                
                // ⭐️ 차트 필터 상태 복원
                if (userPreferences.currentChartStock) currentChartStock = userPreferences.currentChartStock;
                if (userPreferences.currentChartAccount) currentChartAccount = userPreferences.currentChartAccount;
                if (userPreferences.currentChartBroker) currentChartBroker = userPreferences.currentChartBroker;
                if (userPreferences.currentChartSubAccount) currentChartSubAccount = userPreferences.currentChartSubAccount;

            } catch (e) {
                console.warn("환경설정 파싱 실패:", e);
            }
        }
        
        // 3. 매매 기록 데이터 처리
        console.log("[Data Load] /api/data 상태 코드:", response.status);
        if (response.status === 401) {
            // ⭐️ 세션 만료 시 로그인 페이지로 이동 (빈 화면 방지)
            console.warn("[Data Load] 세션 만료 감지 - 로그인 페이지로 이동");
            window.location.href = '/login';
            return;
        }
        if (!response.ok) {
            throw new Error(`/api/data 응답 오류: ${response.status}`);
        }
        cloudEntries = await response.json();
        window.__dataLoadFailed = false; // ⭐️ 로딩 성공: 재시도 플래그 해제
        const errBox = document.getElementById('dataLoadErrorBox');
        if (errBox) errBox.style.display = 'none';
        displayEntries();
        console.log("[Data Load] 화면 렌더링(displayEntries) 완료");

        fetchRealtimeNews();
        if (newsInterval) clearInterval(newsInterval);
        newsInterval = setInterval(fetchRealtimeNews, 600000); // 10분 주기로 변경
    } catch (err) {
        // ⭐️ 타임아웃(AbortError)·네트워크 오류 등으로 초기 로딩이 실패하면, 화면이 빈 상태로 굳지 않도록
        //    재시도 버튼을 노출하고 visibilitychange 자동 재시도용 플래그를 세운다.
        console.error("[Data Load Critical Error] 데이터 로딩 중 치명적 에러 발생:", err);
        window.__dataLoadFailed = true;
        showDataLoadError(err);
    }
}

// ⭐️ 초기 데이터 로딩 실패 시 화면 중앙에 안내 + '다시 시도' 버튼을 표시
function showDataLoadError(err) {
    const reason = err && err.name === 'AbortError'
        ? '서버 응답이 지연되어 연결을 종료했습니다 (네트워크 상태를 확인해주세요)'
        : (err && err.message ? err.message : '알 수 없는 오류');

    // ⭐️ 원인 분석용 상세 진단 정보 수집 (HTML 이스케이프 처리)
    const esc = (v) => String(v).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
    const diagLines = [
        '발생 시각      : ' + new Date().toLocaleString(),
        '네트워크 상태  : ' + (navigator.onLine ? '온라인' : '오프라인'),
        '계산엔진(calc) : ' + (typeof window.applyTradeToHolding === 'function' ? '정상 로드됨' : '로드 실패/누락'),
        '에러 종류      : ' + (err && err.name ? err.name : '-'),
        '에러 메시지    : ' + (err && err.message ? err.message : '-'),
        '현재 주소      : ' + location.href,
        'User-Agent     : ' + navigator.userAgent,
    ];
    if (err && err.stack) diagLines.push('', '[스택]', err.stack);
    const diagText = diagLines.join('\n');

    let box = document.getElementById('dataLoadErrorBox');
    if (!box) {
        box = document.createElement('div');
        box.id = 'dataLoadErrorBox';
        box.style.cssText = 'position:fixed; top:0; left:0; width:100%; height:100%; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:14px; background:var(--bg-color, #fff); z-index:99999; padding:24px; text-align:center; box-sizing:border-box;';
        document.body.appendChild(box);
    }
    box.innerHTML = `
        <div style="font-size:15px; color:var(--text-color, #333); line-height:1.6;">데이터를 불러오지 못했습니다.<br><span style="font-size:12px; color:var(--text-muted-color, #888);">(원인: ${esc(reason)})</span></div>
        <button type="button" id="btnRetryDataLoad" style="width:auto; min-width:120px; max-width:200px; padding:8px 20px; background:var(--primary-color, #3b82f6); color:#fff; border:none; border-radius:8px; font-size:13px; cursor:pointer; align-self:center;">다시 시도</button>
        <button type="button" id="btnToggleDiag" style="background:none; border:none; color:var(--text-muted-color, #888); font-size:12px; text-decoration:underline; cursor:pointer; align-self:center;">상세 정보 보기 ▾</button>
        <pre id="dataLoadDiag" style="display:none; max-width:92%; max-height:45vh; overflow:auto; text-align:left; white-space:pre-wrap; word-break:break-all; font-size:11px; line-height:1.5; color:var(--text-muted-color, #666); background:var(--card-bg-color, #f5f5f5); border:1px solid var(--border-color, #ddd); border-radius:8px; padding:12px; margin:0;">${esc(diagText)}</pre>
        <button type="button" id="btnCopyDiag" style="display:none; background:none; border:1px solid var(--border-color, #ccc); color:var(--text-muted-color, #888); font-size:11px; padding:4px 12px; border-radius:6px; cursor:pointer; align-self:center;">진단 정보 복사</button>`;
    box.style.display = 'flex';

    const retryBtn = document.getElementById('btnRetryDataLoad');
    if (retryBtn) {
        retryBtn.onclick = () => {
            box.style.display = 'none';
            loadDataFromLocal();
        };
    }

    const toggleBtn = document.getElementById('btnToggleDiag');
    const diagEl = document.getElementById('dataLoadDiag');
    const copyBtn = document.getElementById('btnCopyDiag');
    if (toggleBtn && diagEl) {
        toggleBtn.onclick = () => {
            const open = diagEl.style.display !== 'none';
            diagEl.style.display = open ? 'none' : 'block';
            if (copyBtn) copyBtn.style.display = open ? 'none' : 'inline-block';
            toggleBtn.textContent = open ? '상세 정보 보기 ▾' : '상세 정보 닫기 ▴';
        };
    }
    if (copyBtn) {
        copyBtn.onclick = async () => {
            try {
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    await navigator.clipboard.writeText(diagText);
                    copyBtn.textContent = '복사됨 ✓';
                    setTimeout(() => { copyBtn.textContent = '진단 정보 복사'; }, 1500);
                }
            } catch (e) { console.warn('진단 정보 복사 실패', e); }
        };
    }
}

// ⭐️ 환경설정(사용자가 정렬한 카드 순서)을 DB에 저장
async function savePreferences() {
    try {
        await fetch('/api/preferences', {
            method: 'POST',
            headers: { 
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(userPreferences)
        });
    } catch (err) {
        console.error("환경설정 저장 실패:", err);
    }
}

// ⭐️ 모든 필터 상태를 DB에 저장
window.saveFilterPreferences = function() {
    userPreferences.currentDashboardBroker = currentDashboardBroker;
    userPreferences.currentDashboardSubAccount = currentDashboardSubAccount;
    userPreferences.currentDashboardAccount = currentDashboardAccount;
    userPreferences.currentFilterRecordType = currentFilterRecordType;
    userPreferences.currentFilterStock = currentFilterStock;
    userPreferences.currentFilterAccount = currentFilterAccount;
    userPreferences.currentFilterBroker = currentFilterBroker;
    userPreferences.currentFilterSubAccount = currentFilterSubAccount;
    savePreferences();
};

// ⭐️ 차트 필터 상태를 DB에 저장
window.saveChartFilterPreferences = function() {
    userPreferences.currentChartStock = currentChartStock;
    userPreferences.currentChartAccount = currentChartAccount;
    userPreferences.currentChartBroker = currentChartBroker;
    userPreferences.currentChartSubAccount = currentChartSubAccount;
    savePreferences();
};

async function fetchRealtimeNews(forceRefresh = false) {
    const newsListEl = document.getElementById('newsList');
    if (!newsListEl) return;
    
    // ⭐️ 전역 변수인 currentHoldings를 활용하여 현재 실제 보유 중인 모든 종목 검색
    const stocksToFetch = currentHoldings;
    
    try {
        newsListEl.innerHTML = '<div style="text-align:center; padding: 20px;">🔄 실시간 뉴스를 불러오는 중...</div>';
        const response = await fetchWithTimeout('/api/news', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ stocks: stocksToFetch, force_refresh: forceRefresh })
        });
        const newsData = await response.json();
        
        // ⭐️ 일주일 이전에 작성된 기사 및 미래 날짜(오기입) 기사 엄격하게 제외
        const now = new Date();
        const oneWeekAgo = new Date();
        oneWeekAgo.setDate(now.getDate() - 7);
        
        const filteredNewsData = newsData.filter(news => {
            if (!news.pubDate) return false; // 작성 시간이 아예 없는 기사 제외
            const pubDate = new Date(news.pubDate);
            // ⭐️ 작성 시간을 정상적으로 파싱할 수 없거나, 일주일이 지난 과거 기사, 그리고 미래 시간(기자 오기입) 기사 모두 제외
            if (isNaN(pubDate.getTime())) return false;
            return pubDate >= oneWeekAgo && pubDate <= now;
        });
        
        if (filteredNewsData.length === 0) {
            newsListEl.innerHTML = '<div style="text-align:center; padding: 20px;">관련 뉴스가 없습니다.</div>';
            return;
        }
        
        filteredNewsData.sort((a, b) => new Date(b.pubDate) - new Date(a.pubDate));

        // ⭐️ 루프 안에서 innerHTML += 를 반복하면 매 회 목록 전체를 재파싱(O(n²))하므로
        //    문자열로 모아 한 번에 대입한다.
        let newsHtml = '';
        filteredNewsData.forEach(news => {
            const dateObj = new Date(news.pubDate);
            const dateStr = !isNaN(dateObj) ? (dateObj.getMonth()+1) + '/' + dateObj.getDate() + ' ' + String(dateObj.getHours()).padStart(2,'0') + ':' + String(dateObj.getMinutes()).padStart(2,'0') : news.pubDate;

            newsHtml += `
                <div class="news-item">
                    <a href="${news.link}" target="_blank">${news.title}</a>
                    <div class="news-meta">
                        <span class="news-stock-tag">${news.stock}</span><span>${dateStr}</span>
                    </div>
                </div>`;
        });
        newsListEl.innerHTML = newsHtml;
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
const btnStats = document.getElementById('btnStats');
const inlineStatsContainer = document.getElementById('inlineStatsContainer');

if (btnStats && inlineStatsContainer) {
    btnStats.addEventListener('click', async () => {
        const monthlyProfitChartContainer = document.getElementById('monthlyProfitChartContainer');
        const chartDetailList = document.getElementById('chartDetailList');

        if (inlineStatsContainer.style.display === 'block') {
            inlineStatsContainer.style.display = 'none';
            btnStats.style.backgroundColor = 'transparent';
            btnStats.style.color = 'var(--primary-color)';
            
            // 기존 차트 다시 보이기
            if (monthlyProfitChartContainer) monthlyProfitChartContainer.style.display = 'block';
            window.renderMonthlyProfitChart();
        } else {
            inlineStatsContainer.style.display = 'block';
            btnStats.style.backgroundColor = 'var(--primary-color)';
            btnStats.style.color = '#fff';
            
            // 차트 영역 및 상세내역 숨기기
            if (monthlyProfitChartContainer) monthlyProfitChartContainer.style.display = 'none';
            if (chartDetailList) chartDetailList.style.display = 'none';
            
            // 기존 차트 타입 버튼들의 강조 효과 제거
            document.querySelectorAll('.chart-type-btn').forEach(btn => {
                btn.style.backgroundColor = 'transparent';
                btn.style.color = 'var(--primary-color)';
            });
            
            await loadTradeStats();
        }
    });
}

// 손익 부호에 따른 색상 클래스 (양수: 빨강/수익, 음수: 파랑/손실 — 국내 관행)
function statsColor(v) {
    if (v > 0) return 'var(--danger-color, #e74c3c)';
    if (v < 0) return 'var(--primary-color, #3b82f6)';
    return 'var(--text-color)';
}
function statsMoney(v) {
    const n = Math.round(Number(v) || 0);
    return (n > 0 ? '+' : '') + n.toLocaleString() + '원';
}

window.loadTradeStats = async function() {
    if (!inlineStatsContainer) return;
    inlineStatsContainer.innerHTML = '<p style="text-align:center; padding: 20px; color: var(--text-muted-color);">불러오는 중...</p>';
    try {
        let entryIds = [];
        cloudEntries.forEach(entry => {
            if (entry.type !== 'trade' || !entry.stockName) return;
            if (currentChartStock !== 'all' && entry.stockName !== currentChartStock) return;
            if (currentChartAccount !== 'all' && (entry.accountName || '') !== currentChartAccount) return;
            if (currentChartBroker !== 'all' && (entry.brokerAccount || '') !== currentChartBroker) return;
            if (currentChartSubAccount !== 'all' && (entry.subAccount || '') !== currentChartSubAccount) return;
            entryIds.push(entry.id);
        });
        
        const res = await fetch('/api/stats', {
            method: 'POST',
            headers: { 
                'Content-Type': 'application/json' 
            },
            body: JSON.stringify({ 
                entry_ids: entryIds,
                granularity: window.currentChartGranularity || 'monthly'
            })
        });
        if (!res.ok) throw new Error('통계를 불러오지 못했습니다.');
        const s = await res.json();
        renderTradeStats(s);
    } catch (e) {
        inlineStatsContainer.innerHTML = '<p style="text-align:center; padding: 20px; color: var(--danger-color);">데이터를 불러오지 못했습니다.</p>';
    }
};

function renderTradeStats(s) {
    if (!inlineStatsContainer) return;

    if (!s || (s.sellCount === 0 && s.buyCount === 0 && s.dividendCount === 0)) {
        inlineStatsContainer.innerHTML = '<p style="text-align:center; padding: 30px; color: var(--text-muted-color);">분석할 매매 기록이 없습니다.<br>매수/매도 기록을 추가해 보세요.</p>';
        return;
    }

    const card = (label, value, color) =>
        `<div style="flex:1 1 22%; min-width:75px; background: var(--bg-color); border:1px solid var(--border-color); border-radius:6px; padding:6px 4px; text-align:center;">
            <div style="font-size:9.5px; color: var(--text-muted-color); margin-bottom:2px; word-break:keep-all;">${label}</div>
            <div style="font-size:11.5px; font-weight:bold; color:${color || 'var(--text-strong-color)'}; word-break:keep-all;">${value}</div>
        </div>`;

    const pf = (s.profitFactor === null || s.profitFactor === undefined) ? '—' : s.profitFactor.toFixed(2);

    // 요약 지표 카드
    let html = '<div style="display:flex; flex-wrap:wrap; gap:8px; margin-bottom:16px;">';
    html += card('총 손익 (실현+배당)', statsMoney(s.totalPnl), statsColor(s.totalPnl));
    html += card('실현 손익', statsMoney(s.totalRealized), statsColor(s.totalRealized));
    html += card('배당 수익', statsMoney(s.totalDividend), statsColor(s.totalDividend));
    html += card('승률', `${s.winRate.toFixed(1)}% (${s.winCount}승 ${s.lossCount}패)`, 'var(--text-strong-color)');
    html += card('손익비 (Profit Factor)', pf, 'var(--text-strong-color)');
    html += card('평균 보유기간', `${s.avgHoldingDays.toFixed(1)}일`, 'var(--text-strong-color)');
    html += card('평균 수익 (이익 거래)', statsMoney(s.avgWin), statsColor(s.avgWin));
    html += card('평균 손실 (손실 거래)', statsMoney(-s.avgLoss), statsColor(-s.avgLoss));
    html += card('최대 단일 수익', statsMoney(s.maxSingleWin), statsColor(s.maxSingleWin));
    html += card('최대 단일 손실', statsMoney(s.maxSingleLoss), statsColor(s.maxSingleLoss));
    html += card('총 매수금', `${Math.round(s.totalBuyAmount).toLocaleString()}원`, 'var(--text-strong-color)');
    html += card('총 매도금', `${Math.round(s.totalSellAmount).toLocaleString()}원`, 'var(--text-strong-color)');
    html += '</div>';

    const thStyle = 'padding:8px; text-align:right; font-weight:bold; color:var(--text-strong-color); border-bottom:2px solid var(--border-color); white-space:nowrap;';
    const tdStyle = 'padding:7px 8px; text-align:right; border-bottom:1px solid var(--border-color); white-space:nowrap;';
    const tdLeft = tdStyle.replace('text-align:right', 'text-align:left');

    // 기간별 실현손익
    if (s.monthly && s.monthly.length) {
        const isWeekly = (window.currentChartGranularity === 'weekly');
        const periodTitle = isWeekly ? '📅 주간 실현손익 (최근 12주)' : '📅 월간 실현손익 (최근 12개월)';
        const periodHeader = isWeekly ? '주간(시작일)' : '월';
        html += `<h4 style="font-size:13px; margin:18px 0 8px; color:var(--text-strong-color);">${periodTitle}</h4>`;
        html += '<div style="overflow-x:auto;"><table style="width:100%; border-collapse:collapse; font-size:12.5px;"><thead><tr>';
        html += `<th style="${thStyle.replace('text-align:right','text-align:left')}">${periodHeader}</th><th style="${thStyle}">실현손익</th><th style="${thStyle}">배당</th><th style="${thStyle}">매도금액</th></tr></thead><tbody>`;
        s.monthly.forEach(m => {
            html += `<tr><td style="${tdLeft}">${m.month}</td>`
                + `<td style="${tdStyle} color:${statsColor(m.realized)};">${statsMoney(m.realized)}</td>`
                + `<td style="${tdStyle} color:${statsColor(m.dividend)};">${statsMoney(m.dividend)}</td>`
                + `<td style="${tdStyle}">${Math.round(m.sellAmount).toLocaleString()}원</td></tr>`;
        });
        html += '</tbody></table></div>';
    }

    // 종목별 실현손익
    if (s.perStock && s.perStock.length) {
        html += '<h4 style="font-size:13px; margin:18px 0 8px; color:var(--text-strong-color);">🏷️ 종목별 실현손익</h4>';
        html += '<div style="overflow-x:auto;"><table style="width:100%; border-collapse:collapse; font-size:12.5px;"><thead><tr>';
        html += `<th style="${thStyle.replace('text-align:right','text-align:left')}">종목</th><th style="${thStyle}">합계(실현+배당)</th><th style="${thStyle}">매도 횟수</th><th style="${thStyle}">승률</th></tr></thead><tbody>`;
        s.perStock.forEach(p => {
            html += `<tr><td style="${tdLeft}">${p.stock}</td>`
                + `<td style="${tdStyle} color:${statsColor(p.total)};">${statsMoney(p.total)}</td>`
                + `<td style="${tdStyle}">${p.sellCount}</td>`
                + `<td style="${tdStyle}">${p.sellCount ? p.winRate.toFixed(0) + '%' : '—'}</td></tr>`;
        });
        html += '</tbody></table></div>';
    }

    html += '<p style="font-size:11px; color:var(--text-muted-color); margin-top:14px; line-height:1.5;">※ 실현손익은 이동평균단가 방식으로 계산되며, 보유기간은 선입선출(FIFO) 기준 추정치입니다. 미실현(평가) 손익은 포함되지 않습니다.</p>';

    inlineStatsContainer.innerHTML = html;
}

let adminUsersData = [];
let currentAdminSort = { key: 'created_at', asc: false };

window.loadAdminUsers = async function() {
    if (!adminUserList) return;
    adminUserList.innerHTML = '<tr><td colspan="5" style="text-align:center; padding: 20px;">불러오는 중...</td></tr>';
    try {
        const res = await fetch('/api/admin/users');
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
    const adminUser = adminUsersData.find(u => u.is_admin);
    const regularUsers = adminUsersData.filter(u => !u.is_admin);

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
        const res = await fetch(`/api/admin/users/${username}/toggle_allow`, { method: 'POST'});
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
            const res = await fetch(`/api/admin/users/${username}/reset_password`, { method: 'POST'});
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
            const res = await fetch(`/api/admin/users/${username}`, { method: 'DELETE'});
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
function getSubAccountOptions() {
    const historySubAccounts = cloudEntries.map(entry => entry.subAccount).filter(Boolean);
    return [...new Set(historySubAccounts)].sort();
}

setupAutocomplete('stockName', 'stockNameList', getStockOptions);
setupAutocomplete('brokerAccount', 'brokerAccountList', getBrokerOptions);
setupAutocomplete('subAccount', 'subAccountList', getSubAccountOptions);

// ⭐️ 종목명 입력 완료(자동완성 선택, 포커스 아웃, 엔터 입력) 시 관련 정보 자동 입력
function autoFillStockInfo(e) {
    const val = (e.type === 'itemSelected' && e.detail) ? e.detail.value : this.value.trim();
    if (!val) return;
    
    // 증권사, 투자분류 등의 정보가 있는 가장 최근의 매매(trade) 기록을 우선 탐색
    let recentEntry = cloudEntries.find(entry => entry.stockName === val && entry.type === 'trade');
    
    // 매매 기록이 없으면 일반 메모 기록이라도 탐색 (종목코드가 있을 수 있으므로)
    if (!recentEntry) {
        recentEntry = cloudEntries.find(entry => entry.stockName === val);
    }

    if (recentEntry) {
        if (recentEntry.stockCode) document.getElementById('stockCode').value = recentEntry.stockCode;
        if (recentEntry.brokerAccount) document.getElementById('brokerAccount').value = recentEntry.brokerAccount;
        if (recentEntry.subAccount) document.getElementById('subAccount').value = recentEntry.subAccount;
        if (recentEntry.accountName) document.getElementById('accountName').value = recentEntry.accountName;
    }
}

const stockNameInput = document.getElementById('stockName');
stockNameInput.addEventListener('itemSelected', autoFillStockInfo);
stockNameInput.addEventListener('blur', autoFillStockInfo); // 포커스 잃을 때
stockNameInput.addEventListener('keydown', function(e) {
    // 엔터 키 입력 시 (한글 조합 중이 아닐 때)
    if (e.key === 'Enter' && !e.isComposing) {
        e.preventDefault(); // ⭐️ 엔터 키 입력 시 폼(Form)이 강제 제출되는 현상 방지
        autoFillStockInfo.call(this, e);
        
        // ⭐️ 엔터 입력 시 빈 칸 또는 다음 주요 입력칸으로 자동 포커스 이동
        const recordType = document.querySelector('input[name="recordType"]:checked');
        if (recordType && recordType.value === 'trade') {
            // 매매 일지: 비어있는 항목을 우선 찾고, 모두 채워졌으면 '매매 단가'로 직행
            if (!document.getElementById('stockCode').value) {
                document.getElementById('stockCode').focus();
            } else if (!document.getElementById('brokerAccount').value) {
                document.getElementById('brokerAccount').focus();
            } else if (!document.getElementById('subAccount').value) {
                document.getElementById('subAccount').focus();
            } else if (!document.getElementById('accountName').value) {
                document.getElementById('accountName').focus();
            } else {
                document.getElementById('price').focus();
            }
        } else {
            // 일반 메모: '메모 제목'으로 직행
            document.getElementById('memoTitle').focus();
        }
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

        // ⭐️ 모달 닫힘 시 다음번을 위해 드래그 위치 초기화
        if (typeof window.resetFormDragPosition === 'function') window.resetFormDragPosition();
    }, 180); // CSS 페이드아웃 애니메이션 시간과 동기화
}

// ⭐️ Flatpickr 초기화 (날짜 및 시간 선택기)
window.tradeDatePicker = flatpickr("#tradeDate", {
    enableTime: true,
    dateFormat: "Y-m-d\\TH:i",
    locale: "ko",
    time_24hr: false,
    // ⭐️ 모바일 네이티브 스크롤 픽커 대신 커스텀 UI를 강제하여 조작 즉시 실시간 반영되도록 처리
    disableMobile: true,
    // ⭐️ 캘린더/시간 변경 시 입력창에 즉각적으로 반영
    onChange: function(selectedDates, dateStr, instance) {
        if (instance.input) instance.input.value = dateStr;
    },
    onValueUpdate: function(selectedDates, dateStr, instance) {
        if (instance.input) instance.input.value = dateStr;
    },
    // ⭐️ 월 또는 연도 변경 시 기존에 선택된 '일(Day)'과 '시간'을 유지하여 즉각 반영되도록 처리
    onMonthChange: function(selectedDates, dateStr, instance) {
        if (selectedDates.length > 0) {
            const cd = selectedDates[0];
            const maxDays = new Date(instance.currentYear, instance.currentMonth + 1, 0).getDate();
            const nd = new Date(instance.currentYear, instance.currentMonth, Math.min(cd.getDate(), maxDays), cd.getHours(), cd.getMinutes());
            instance.setDate(nd, true);
        }
    },
    onYearChange: function(selectedDates, dateStr, instance) {
        if (selectedDates.length > 0) {
            const cd = selectedDates[0];
            const maxDays = new Date(instance.currentYear, instance.currentMonth + 1, 0).getDate();
            const nd = new Date(instance.currentYear, instance.currentMonth, Math.min(cd.getDate(), maxDays), cd.getHours(), cd.getMinutes());
            instance.setDate(nd, true);
        }
    },
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
    const tradeType = document.getElementById('tradeType').value;
    const price = Number(document.getElementById('price').value) || 0;
    const qtyInput = document.getElementById('quantity').value;
    let qty = Number(qtyInput) || 0;
    
    // ⭐️ 배당일 경우 수량이 없으면 1로 계산하여 단가 금액을 총액에 그대로 표시
    if (tradeType === '배당' && (!qtyInput || qty === 0)) {
        qty = 1;
    }

    if (price > 0 && qty > 0) {
        totalWrapper.style.display = 'block';
        const textLabel = tradeType === '배당' ? '총 배당 금액' : '총 매매 금액';
        document.getElementById('totalAmountDisplay').innerText = `${textLabel}: ${(price * qty).toLocaleString()}원`;
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
    document.getElementById('subAccountGroup').style.display = isTrade ? 'block' : 'none';
    
    document.getElementById('stockName').required = isTrade;
    document.getElementById('stockCode').required = isTrade;
    
    const brokerAccountEl = document.getElementById('brokerAccount');
    if (brokerAccountEl) brokerAccountEl.required = isTrade;
    const subAccountEl = document.getElementById('subAccount');
    if (subAccountEl) subAccountEl.required = isTrade;
    const accountNameEl = document.getElementById('accountName');
    if (accountNameEl) accountNameEl.required = isTrade;
    
    const tradeTypeEl = document.getElementById('tradeType');
    const tradeTypeValue = tradeTypeEl ? tradeTypeEl.value : '';
    const isTradeAndNotWatch = isTrade && tradeTypeEl && tradeTypeValue !== '주시' && tradeTypeValue !== '관망';
    const isDividend = tradeTypeValue === '배당';
    const priceEl = document.getElementById('price');
    if (priceEl) priceEl.required = isTradeAndNotWatch;
    const quantityEl = document.getElementById('quantity');
    if (quantityEl) {
        quantityEl.required = isTradeAndNotWatch && !isDividend; // ⭐️ 배당일 때는 수량이 필수값이 아니도록 처리
    }
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

// ⭐️ 매매 포지션(tradeType) 변경 시 단가/수량 필수 여부 동적 업데이트
const tradeTypeSelect = document.getElementById('tradeType');
if (tradeTypeSelect) {
    tradeTypeSelect.addEventListener('change', function() {
        const recordType = document.querySelector('input[name="recordType"]:checked');
        if (recordType) toggleFormUI(recordType.value);
        calcTotalAmount(); // 배당/매매에 따른 텍스트 레이블 변경 반영
    });
}

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
window.resizeAndInsertImageToQuill = function(file, customIndex) {
    // ⭐️ 캡처된 커서 위치(customIndex)가 없으면 현재 위치 사용
    let insertIndex = customIndex;
    if (insertIndex === undefined) {
        window.quill.focus();
        const range = window.quill.getSelection();
        insertIndex = range ? range.index : window.quill.getLength();
    }

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
        const accountName = document.getElementById('accountName').value;
        const tradeType = document.getElementById('tradeType').value;
        const price = document.getElementById('price').value;
        let quantity = document.getElementById('quantity').value;

        // ⭐️ 배당일 때 수량이 입력되지 않았으면 자동으로 1로 보정
        if (tradeType === '배당' && (!quantity || Number(quantity) === 0)) {
            quantity = 1;
        }

        newEntry = {
            id: currentEditingId || Date.now(), type: 'trade', stockName, stockCode, brokerAccount, subAccount, accountName,
            tradeType, price: price ? Number(price) : 0, quantity: quantity ? Number(quantity) : 0, thoughts, date, rawDate: tradeDateRaw, attachedImage: null,
            createdAt, updatedAt: nowIso, tags: currentTags.join(','), attachedFile: '', attachedFileName: ''
        };
    } else {
        const memoTitle = document.getElementById('memoTitle').value;
        newEntry = { id: currentEditingId || Date.now(), type: 'memo', stockName: '', stockCode: '', title: memoTitle, thoughts, date, rawDate: tradeDateRaw, attachedImage: null, createdAt, updatedAt: nowIso, tags: currentTags.join(','), attachedFile: '', attachedFileName: '', brokerAccount: '', subAccount: '' };
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
const btnFullBackup = document.getElementById('btnFullBackup');
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

// ⭐️ SheetJS(약 1MB)는 초기 로딩에서 제외하고 엑셀 내보내기 시점에만 동적 로드
function ensureXlsxLoaded() {
    return new Promise((resolve, reject) => {
        if (window.XLSX) { resolve(); return; }
        const tag = document.createElement('script');
        tag.src = 'https://cdn.sheetjs.com/xlsx-latest/package/dist/xlsx.full.min.js';
        tag.onload = () => window.XLSX ? resolve() : reject(new Error('엑셀 모듈이 초기화되지 않았습니다.'));
        tag.onerror = () => reject(new Error('엑셀 모듈을 불러오지 못했습니다. (네트워크 연결을 확인해주세요)'));
        document.head.appendChild(tag);
    });
}

document.getElementById('btnExportExcel').addEventListener('click', async () => {
    if (await customConfirm('모든 매매 기록을 엑셀 파일(.xlsx)로 \n다운로드하시겠습니까?')) {
        window.showLoadingOverlay('엑셀 파일을 생성 중입니다...\n잠시만 기다려주세요.');

        // ⭐️ UI 스레드가 블록되기 전에 로딩 애니메이션이 화면에 렌더링될 수 있도록 약간의 지연(setTimeout)을 줌
        setTimeout(async () => {
            try {
        await ensureXlsxLoaded();
        const header = ['작성일', '분류', '종목명', '증권사', '증권계좌', '계좌분류', '매매종류', '단가', '수량', '태그', '메모/생각'];
        const rows = cloudEntries.map(e => [
            e.date, (e.type || '').toUpperCase(), e.stockName||'', e.brokerAccount||'', e.subAccount||'', e.accountName||'',
            e.tradeType||'', Number(e.price)||0, Number(e.quantity)||0, 
            e.tags||'', (e.thoughts||'').replace(/<[^>]*>?/gm, '').replace(/&nbsp;/g, ' ') // HTML 태그 제거 후 엑셀 내보내기
        ]);
        
        const worksheet = XLSX.utils.aoa_to_sheet([header, ...rows]);
        
        // 단가, 수량 컬럼 숫자 포맷(천 단위 콤마) 지정
        const range = XLSX.utils.decode_range(worksheet['!ref']);
        for (let R = range.s.r + 1; R <= range.e.r; ++R) {
            const priceCell = worksheet[XLSX.utils.encode_cell({c: 7, r: R})]; // H열 (단가)
            if (priceCell && priceCell.t === 'n') priceCell.z = '#,##0';
            
            const qtyCell = worksheet[XLSX.utils.encode_cell({c: 8, r: R})]; // I열 (수량)
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
            } catch (err) {
                console.error("엑셀 내보내기 실패:", err);
                await customAlert(`엑셀 내보내기에 실패했습니다.\n(${err && err.message ? err.message : '알 수 없는 오류'})`);
            } finally {
                await window.hideLoadingOverlay();
            }
        }, 100);
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

// ⭐️ 정규장 오픈 시간(한국 및 미국)인지 확인하는 함수 (자동 갱신 타이머용)
window.getMarketStatus = function() {
    const now = new Date();
    // 브라우저 지역에 상관없이 KST(한국 표준시) 기준으로 변환하여 일관된 시간 체크
    const utc = now.getTime() + (now.getTimezoneOffset() * 60000);
    const kst = new Date(utc + (9 * 3600000));
    
    const day = kst.getDay(); // 0: 일, 1: 월, 2: 화, 3: 수, 4: 목, 5: 금, 6: 토
    const timeNum = kst.getHours() * 100 + kst.getMinutes();
    
    // 1. 한국 정규장 및 장전/NXT(장후) 시간외 포함: 평일(월~금) 08:00 ~ 20:00
    const isKrOpen = (day >= 1 && day <= 5) && (timeNum >= 800 && timeNum <= 2000);
    
    // 2. 미국 정규장: 평일(월~금) 밤 22:30 ~ 23:59 또는 (화~토) 새벽 00:00 ~ 06:00
    const isUsOpenEvening = (day >= 1 && day <= 5) && (timeNum >= 2230);
    const isUsOpenMorning = (day >= 2 && day <= 6) && (timeNum <= 600);
    
    return {
        kr: isKrOpen,
        us: isUsOpenEvening || isUsOpenMorning
    };
};

// ⭐️ 하위 호환성을 위한 래퍼 함수
window.isMarketOpen = function() {
    const status = window.getMarketStatus();
    return status.kr || status.us;
};

// ⭐️ 백엔드 API를 통해 현재가와 평가금액을 가져와 DOM에 반영하는 함수
window.fetchCurrentPricesAndUpdateUI = async function(isAuto = false) {
    if (!showCurrentPrice || currentPortfolioArrayForPrice.length === 0) return;
    
    const displayMarket = currentMarketMode; // ⭐️ 토글된 시장 모드(KRX 또는 NXT) 사용
    
    const marketStatus = window.getMarketStatus();
    let codesToFetch = [];
    
    currentPortfolioArrayForPrice.forEach(p => {
        if (p.isClosed || !p.stockCode) return;
        
        const codeStr = String(p.stockCode).trim().toUpperCase();
        // 국가 구분 로직 (백엔드와 동일하게 적용)
        const isUS = /^[A-Z\.\-]{1,6}$/.test(codeStr);
        const isKR = (codeStr.length === 6 && /^[0-9A-Z]{6}$/.test(codeStr)) || codeStr === 'KRXGOLD' || codeStr === 'GOLD';
        
        if (isAuto) {
            if (isKR && !marketStatus.kr) return; // 한국장 닫혀있으면 건너뜀
            if (isUS && !marketStatus.us) return; // 미국장 닫혀있으면 건너뜀
            if (!isKR && !isUS && !marketStatus.kr && !marketStatus.us) return; // 기타 종목은 두 시장 모두 닫혀있을 때만 건너뜀
        }
        
        codesToFetch.push(p.stockCode);
    });
    
    codesToFetch = [...new Set(codesToFetch)];
    if (codesToFetch.length === 0) return; // ⭐️ 업데이트할 종목이 없으면 리턴 (평가액 유지를 위해 기존 UI 상태 유지)
    
    try {
        // ⭐️ allow_cached: 60초 자동 폴링(isAuto)만 서버측 단기 캐시를 허용한다.
        //    수동 새로고침은 항상 false → 서버가 외부 API 를 라이브 조회하여 "진짜 현재가"를 보장.
        const res = await fetch('/api/current_price', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ codes: codesToFetch, market_mode: displayMarket, allow_cached: isAuto === true })
        });
        const prices = await res.json();
        
        let totalEval = 0;
        currentPortfolioArrayForPrice.forEach(data => {
            if (data.isClosed) return;
            
            let cp;
            let isFresh = false;
            
            // 이번 요청에 포함된 종목이면 새로 가져온 가격을 캐시에 저장
            if (codesToFetch.includes(data.stockCode)) {
                cp = prices[data.stockCode];
                window.currentPriceCache[data.stockCode] = cp;
                isFresh = true;
            } else {
                // 요청에서 제외된 종목(장 종료)은 캐시된 가격을 사용
                cp = window.currentPriceCache[data.stockCode];
            }
            
            const pEls = document.querySelectorAll(`.cp-price[data-code="${data.stockCode || ''}"]`);
            const eEls = document.querySelectorAll(`.cp-eval[data-code="${data.stockCode || ''}"]`);
            const pfEls = document.querySelectorAll(`.cp-profit[data-code="${data.stockCode || ''}"]`);
            
            if (cp !== undefined && cp !== null) {
                const evalAmount = cp * data.qty;
                const profitAmount = evalAmount - data.totalCost;
                const profitRate = data.totalCost > 0 ? (profitAmount / data.totalCost) * 100 : 0;
                
                if (isFresh) {
                    pEls.forEach(el => el.innerText = cp.toLocaleString());
                    eEls.forEach(el => el.innerText = Math.round(evalAmount).toLocaleString());
                    
                    const pColor = profitAmount > 0 ? 'var(--danger-color)' : (profitAmount < 0 ? 'var(--primary-color)' : 'var(--text-strong-color)');
                    pfEls.forEach(el => el.innerHTML = `<span style="color: ${pColor}; font-weight: bold; text-align: right; display: inline-block;">${profitAmount > 0 ? '+' : ''}${Math.round(profitAmount).toLocaleString()}<br>(${profitRate > 0 ? '+' : ''}${profitRate.toFixed(2)}%)</span>`);
                    
                    // ⭐️ 값이 새로 업데이트될 때만 카드 배경 반짝임(Flash) 애니메이션 적용
                    pEls.forEach(el => {
                        const section = el.closest('.current-price-section');
                        if (section) {
                            section.classList.remove('flash');
                            void section.offsetWidth; // 브라우저 리플로우 강제 발생
                            section.classList.add('flash');
                        }
                    });
                }
                totalEval += evalAmount;
            } else {
                if (isFresh) {
                    pEls.forEach(el => el.innerText = '조회 실패');
                }
                totalEval += data.totalCost; // 조회 실패 시 기본 투자원금으로 임시 합산
            }
        });
        
        const centerEvalEl = document.getElementById('centerTotalEvaluation');
        if (centerEvalEl) {
            animateValue(centerEvalEl, Math.round(totalEval), 1000, false);
        }
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
        
        // ⭐️ 대시보드 증권계좌 필터 적용
        if (currentDashboardSubAccount !== 'all' && (entry.subAccount || '') !== currentDashboardSubAccount) return;
        
        // ⭐️ 대시보드 투자 분류 필터 적용
        if (currentDashboardAccount !== 'all' && (entry.accountName || '') !== currentDashboardAccount) return;

        const stock = entry.stockName;
        const qty = Number(entry.quantity) || 0;
        const price = Number(entry.price) || 0;

        if (!portfolio[stock]) portfolio[stock] = { qty: 0, totalCost: 0, avgPrice: 0, realizedProfit: 0, realizedCost: 0, accountName: '', traded: false, stockCode: '' };
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

        const tt = entry.tradeType;
        if (tt === '매수' || tt === '매도' || tt === '배당') {
            portfolio[stock].traded = true;
            if (isCurrentMonth) {
                if (tt === '매수') monthlyBuyCount++;
                else if (tt === '매도') monthlySellCount++;
            }
            // ⭐️ 공용 계산 엔진(calc.js) — 평균단가/실현손익 단일 소스
            const r = applyTradeToHolding(portfolio[stock], qty, price, tt);
            portfolio[stock].realizedProfit += r.realized + r.dividend;
            portfolio[stock].realizedCost += r.cost;
            totalRealizedProfit += r.realized + r.dividend;
        }
    });

    const portfolioGrid = document.getElementById('portfolioGrid');
    portfolioGrid.innerHTML = '';
    const gridFragment = document.createDocumentFragment();
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
        
        // ⭐️ 현재가 보기 활성화 시에만 종목 실현손익 및 현재가 정보 표시
        if (showCurrentPrice) {
            if (data.realizedProfit !== 0) {
                const profitColor = data.realizedProfit > 0 ? 'var(--danger-color)' : 'var(--primary-color)';
                const profitStr = (data.realizedProfit > 0 ? '+' : '') + Math.round(data.realizedProfit).toLocaleString();
                card.innerHTML += `
                    <div class="stat-row" style="margin-top: 8px; padding-top: 8px; border-top: 1px dashed var(--border-color);">
                        <span>종목 실현손익</span><span style="color:${profitColor}">${profitStr}</span>
                    </div>`;
            }
            
            // ⭐️ 현재 보유 중인 종목만 현재가 영역 추가
            if (!isClosed) {
                card.innerHTML += `
                    <div class="current-price-section" style="margin-top: 8px; padding-top: 8px; border-top: 1px dashed var(--border-color);" title="클릭하여 현재가 갱신">
                        <div class="stat-row" style="align-items: center;"><span>현재가</span><span class="cp-price" data-code="${data.stockCode || ''}">조회 중...</span></div>
                        <div class="stat-row" style="align-items: center;"><span>평가금액</span><span class="cp-eval" data-code="${data.stockCode || ''}">-</span></div>
                        <div class="stat-row" style="align-items: center;"><span>평가손익</span><span class="cp-profit" data-code="${data.stockCode || ''}">-</span></div>
                    </div>
                `;
            }
        }
        
        // ⭐️ 종목 카드 클릭 시 해당 종목 히스토리 필터링 이벤트 연동
        card.title = `${stock} 기록 모아보기`;
        card.addEventListener('click', (e) => {
            // ⭐️ 현재가 영역 클릭 시에는 히스토리 필터링 대신 현재가 즉시 갱신 수행
            if (e.target.closest('.current-price-section')) {
                e.stopPropagation();
                window.fetchCurrentPricesAndUpdateUI();
                return;
            }
            
            // ⭐️ 대시보드에 적용된 필터 상태 임시 저장
            const prevDashboardBroker = currentDashboardBroker;
            const prevDashboardSubAccount = currentDashboardSubAccount;
            const prevDashboardAccount = currentDashboardAccount;

            clearAllFilters(false);

            // ⭐️ 종목 필터 적용 및 저장해둔 대시보드 필터를 하단 필터에도 동기화 유지
            currentFilterStock = stock;
            currentFilterBroker = prevDashboardBroker;
            currentDashboardBroker = prevDashboardBroker;
            currentFilterSubAccount = prevDashboardSubAccount;
            currentDashboardSubAccount = prevDashboardSubAccount;
            currentFilterAccount = prevDashboardAccount;
            currentDashboardAccount = prevDashboardAccount;
            
            window.saveFilterPreferences();
            
            const stockSelect = document.getElementById('filterStockSelect');
            if (stockSelect && stockSelect.querySelector(`option[value="${currentFilterStock.replace(/"/g, '\\"')}"]`)) {
                stockSelect.value = currentFilterStock;
                window.updateDashboardFilterStyle(stockSelect);
            }

            const brokerSelect = document.getElementById('filterBrokerSelect');
            if (brokerSelect && brokerSelect.querySelector(`option[value="${currentFilterBroker.replace(/"/g, '\\"')}"]`)) {
                brokerSelect.value = currentFilterBroker;
                window.updateDashboardFilterStyle(brokerSelect);
            }
            const dashBrokerSelect = document.getElementById('dashboardBrokerFilter');
            if (dashBrokerSelect) {
                dashBrokerSelect.value = currentDashboardBroker;
                window.updateDashboardFilterStyle(dashBrokerSelect);
            }

            const subAccountSelect = document.getElementById('filterSubAccountSelect');
            if (subAccountSelect && subAccountSelect.querySelector(`option[value="${currentFilterSubAccount.replace(/"/g, '\\"')}"]`)) {
                subAccountSelect.value = currentFilterSubAccount;
                window.updateDashboardFilterStyle(subAccountSelect);
            }
            const dashSubAccountSelect = document.getElementById('dashboardSubAccountFilter');
            if (dashSubAccountSelect) {
                dashSubAccountSelect.value = currentDashboardSubAccount;
                window.updateDashboardFilterStyle(dashSubAccountSelect);
            }

            const accountSelect = document.getElementById('filterAccountSelect');
            if (accountSelect && accountSelect.querySelector(`option[value="${currentFilterAccount.replace(/"/g, '\\"')}"]`)) {
                accountSelect.value = currentFilterAccount;
                window.updateDashboardFilterStyle(accountSelect);
            }
            const dashAccountSelect = document.getElementById('dashboardAccountFilter');
            if (dashAccountSelect) {
                dashAccountSelect.value = currentDashboardAccount;
                window.updateDashboardFilterStyle(dashAccountSelect);
            }
            
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

        gridFragment.appendChild(card);
    });
    portfolioGrid.appendChild(gridFragment);
    
    // ⭐️ 필터 결과가 없을 때 빈 화면 대신 안내 메시지 표시
    if (portfolioArray.length === 0) {
        portfolioGrid.innerHTML = '<div style="grid-column: 1 / -1; text-align: center; padding: 40px 20px; color: var(--text-muted-color); font-size: 13px;">해당 조건에 맞는 종목이 없습니다.</div>';
    }

    let toggleBtn = document.getElementById('btnTogglePortfolio');
    // ⭐️ 필터가 적용되어 결과가 없더라도, 전체 매매 기록이 존재하면 대시보드를 숨기지 않음
    const hasAnyTrade = cloudEntries.some(e => e.type === 'trade' && e.stockName);
    const shouldShowDashboard = hasAnyTrade;

    if (toggleBtn) {
        toggleBtn.innerHTML = isDashboardCollapsed ? '▼' : '▲';
        toggleBtn.style.backgroundColor = isDashboardCollapsed ? 'var(--primary-color)' : 'transparent';
        toggleBtn.style.color = isDashboardCollapsed ? '#fff' : 'var(--primary-color)';
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
    
    document.getElementById('portfolioSection').style.display = shouldShowDashboard ? 'block' : 'none';
    
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
            window.fetchCurrentPricesAndUpdateUI();
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
                    // ⭐️ 마우스를 올렸을 때 중앙 텍스트가 툴팁을 가리지 않도록 z-index 조절
                    const centerText = document.getElementById('chartCenterText');
                    if (centerText) {
                        if (elements.length > 0) {
                            centerText.style.zIndex = '5';
                        } else {
                            centerText.style.zIndex = '11';
                        }
                    }
                },
                plugins: { 
                    legend: { 
                        display: false // ⭐️ 기본 캔버스 범례 숨기기 (도넛 크기 고정을 위해)
                    },
                    tooltip: {
                        backgroundColor: 'rgba(0, 0, 0, 0.7)', // ⭐️ 기본값(0.8)보다 약간 더 투명하게 설정
                        callbacks: {
                                title: function(tooltipItems) {
                                    if (isPortfolioEmpty) return '';
                                    return tooltipItems[0].label;
                                },
                            label: function(context) {
                                if (isPortfolioEmpty) return '현재 보유 중인 종목이 없습니다.';
                                let value = context.parsed;
                                let total = context.dataset.data.reduce((a, b) => a + b, 0);
                                let percentage = total > 0 ? ((value / total) * 100).toFixed(1) + '%' : '0%';
                                    return `금액: ${Math.round(value).toLocaleString()}원 (${percentage})`;
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
                        const prevDashboardBroker = currentDashboardBroker;
                        const prevDashboardSubAccount = currentDashboardSubAccount;
                        const prevDashboardAccount = currentDashboardAccount;

                        clearAllFilters(false);

                        currentFilterStock = label;
                        currentFilterBroker = prevDashboardBroker;
                        currentDashboardBroker = prevDashboardBroker;
                        currentFilterSubAccount = prevDashboardSubAccount;
                        currentDashboardSubAccount = prevDashboardSubAccount;
                        currentFilterAccount = prevDashboardAccount;
                        currentDashboardAccount = prevDashboardAccount;
                        
                        window.saveFilterPreferences();
                        
                        const stockSelect = document.getElementById('filterStockSelect');
                        if (stockSelect && stockSelect.querySelector(`option[value="${currentFilterStock.replace(/"/g, '\\"')}"]`)) {
                            stockSelect.value = currentFilterStock;
                            window.updateDashboardFilterStyle(stockSelect);
                        }

                        const brokerSelect = document.getElementById('filterBrokerSelect');
                        if (brokerSelect && brokerSelect.querySelector(`option[value="${currentFilterBroker.replace(/"/g, '\\"')}"]`)) {
                            brokerSelect.value = currentFilterBroker;
                            window.updateDashboardFilterStyle(brokerSelect);
                        }
                        const dashBrokerSelect = document.getElementById('dashboardBrokerFilter');
                        if (dashBrokerSelect) {
                            dashBrokerSelect.value = currentDashboardBroker;
                            window.updateDashboardFilterStyle(dashBrokerSelect);
                        }

                        const subAccountSelect = document.getElementById('filterSubAccountSelect');
                        if (subAccountSelect && subAccountSelect.querySelector(`option[value="${currentFilterSubAccount.replace(/"/g, '\\"')}"]`)) {
                            subAccountSelect.value = currentFilterSubAccount;
                            window.updateDashboardFilterStyle(subAccountSelect);
                        }
                        const dashSubAccountSelect = document.getElementById('dashboardSubAccountFilter');
                        if (dashSubAccountSelect) {
                            dashSubAccountSelect.value = currentDashboardSubAccount;
                            window.updateDashboardFilterStyle(dashSubAccountSelect);
                        }

                        const accountSelect = document.getElementById('filterAccountSelect');
                        if (accountSelect && accountSelect.querySelector(`option[value="${currentFilterAccount.replace(/"/g, '\\"')}"]`)) {
                            accountSelect.value = currentFilterAccount;
                            window.updateDashboardFilterStyle(accountSelect);
                        }
                        const dashAccountSelect = document.getElementById('dashboardAccountFilter');
                        if (dashAccountSelect) {
                            dashAccountSelect.value = currentDashboardAccount;
                            window.updateDashboardFilterStyle(dashAccountSelect);
                        }
                        
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
}

// ⭐️ 대시보드 필터 선택 시 활성화 색상(피드백) 변경 함수
window.updateDashboardFilterStyle = function(element) {
    if (!element) return;
    const wrapper = element.closest('.filter-select-wrapper');
    if (element.value !== 'all') {
        if (wrapper) {
            wrapper.style.backgroundColor = 'var(--primary-color)';
        } else {
            element.style.backgroundColor = 'var(--primary-color)';
        }
        element.style.color = '#fff';
    } else {
        if (wrapper) {
            wrapper.style.backgroundColor = 'transparent';
        } else {
            element.style.backgroundColor = 'transparent';
        }
        element.style.color = 'var(--primary-color)';
    }
};

// ⭐️ 드롭다운 필터에 종목명을 동적으로 추가하는 함수
function updateFilterDropdown() {
    const stockSelect = document.getElementById('filterStockSelect');
    const accountSelect = document.getElementById('filterAccountSelect');
    const brokerSelect = document.getElementById('filterBrokerSelect');
    const subAccountSelect = document.getElementById('filterSubAccountSelect');
    const recordTypeSelect = document.getElementById('filterRecordTypeSelect');
    
    if (recordTypeSelect) {
        recordTypeSelect.value = currentFilterRecordType;
        window.updateDashboardFilterStyle(recordTypeSelect);
    }

    const stocks = [...new Set(cloudEntries.map(e => e.stockName).filter(Boolean))].sort();
    if (stockSelect) {
        let html = '<option value="all">종목별</option>';
        stocks.forEach(stock => {
            html += `<option value="${stock.replace(/"/g, '&quot;')}">${stock}</option>`;
        });
        stockSelect.innerHTML = html;
        if (stockSelect.querySelector(`option[value="${currentFilterStock.replace(/"/g, '\\"')}"]`)) {
            stockSelect.value = currentFilterStock;
        } else {
            stockSelect.value = 'all';
            currentFilterStock = 'all';
        }
        window.updateDashboardFilterStyle(stockSelect);
    }

    const accountSortOrder = { "장기투자": 1, "중기투자": 2, "단기스윙": 3, "단타(스캘핑)": 4, "배당투자": 5, "공모주": 6, "기타": 7 };
    const accounts = [...new Set(cloudEntries.map(e => e.accountName).filter(Boolean))].sort((a, b) => {
        const orderA = accountSortOrder[a] || 99;
        const orderB = accountSortOrder[b] || 99;
        if (orderA !== orderB) return orderA - orderB;
        return a.localeCompare(b);
    });
    if (accountSelect) {
        let html = '<option value="all">분류별</option>';
        accounts.forEach(account => {
            html += `<option value="${account.replace(/"/g, '&quot;')}">${account}</option>`;
        });
        accountSelect.innerHTML = html;
        if (accountSelect.querySelector(`option[value="${currentFilterAccount.replace(/"/g, '\\"')}"]`)) {
            accountSelect.value = currentFilterAccount;
        } else {
            accountSelect.value = 'all';
            currentFilterAccount = 'all';
            currentDashboardAccount = 'all'; // ⭐️ 상단 필터 동기화
        }
        window.updateDashboardFilterStyle(accountSelect);
    }
    
    const brokers = [...new Set(cloudEntries.map(e => e.brokerAccount).filter(Boolean))].sort();
    if (brokerSelect) {
        let html = '<option value="all">증권사별</option>';
        brokers.forEach(broker => {
            html += `<option value="${broker.replace(/"/g, '&quot;')}">${broker}</option>`;
        });
        brokerSelect.innerHTML = html;
        if (brokerSelect.querySelector(`option[value="${currentFilterBroker.replace(/"/g, '\\"')}"]`)) {
            brokerSelect.value = currentFilterBroker;
        } else {
            brokerSelect.value = 'all';
            currentFilterBroker = 'all';
            currentDashboardBroker = 'all'; // ⭐️ 상단 필터 동기화
        }
        window.updateDashboardFilterStyle(brokerSelect);
    }

    const subAccounts = [...new Set(cloudEntries.map(e => e.subAccount).filter(Boolean))].sort();
    if (subAccountSelect) {
        let html = '<option value="all">계좌별</option>';
        subAccounts.forEach(sa => {
            html += `<option value="${sa.replace(/"/g, '&quot;')}">${sa}</option>`;
        });
        subAccountSelect.innerHTML = html;
        if (subAccountSelect.querySelector(`option[value="${currentFilterSubAccount.replace(/"/g, '\\"')}"]`)) {
            subAccountSelect.value = currentFilterSubAccount;
        } else {
            subAccountSelect.value = 'all';
            currentFilterSubAccount = 'all';
            currentDashboardSubAccount = 'all'; // ⭐️ 상단 필터 동기화
        }
        window.updateDashboardFilterStyle(subAccountSelect);
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
        if (dashboardBrokerFilter.querySelector(`option[value="${currentBrokerVal.replace(/"/g, '\\"')}"]`)) {
            dashboardBrokerFilter.value = currentBrokerVal;
        } else {
            dashboardBrokerFilter.value = 'all';
            currentDashboardBroker = 'all';
            currentFilterBroker = 'all'; // ⭐️ 하단 필터 동기화
        }
        window.updateDashboardFilterStyle(dashboardBrokerFilter);
    }
    
    // ⭐️ 대시보드의 증권계좌 필터 옵션도 동적으로 업데이트
    const dashboardSubAccountFilter = document.getElementById('dashboardSubAccountFilter');
    if (dashboardSubAccountFilter) {
        const currentSubAccountVal = currentDashboardSubAccount || 'all';
        let subAccountHtml = `<option value="all">모든 계좌</option>`;
        if (subAccounts.length > 0) {
            subAccounts.forEach(sa => {
                subAccountHtml += `<option value="${sa.replace(/"/g, '&quot;')}">${sa}</option>`;
            });
        }
        dashboardSubAccountFilter.innerHTML = subAccountHtml;
        if (dashboardSubAccountFilter.querySelector(`option[value="${currentSubAccountVal.replace(/"/g, '\\"')}"]`)) {
            dashboardSubAccountFilter.value = currentSubAccountVal;
        } else {
            dashboardSubAccountFilter.value = 'all';
            currentDashboardSubAccount = 'all';
            currentFilterSubAccount = 'all'; // ⭐️ 하단 필터 동기화
        }
        window.updateDashboardFilterStyle(dashboardSubAccountFilter);
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
        if (dashboardAccountFilter.querySelector(`option[value="${currentAccountVal.replace(/"/g, '\\"')}"]`)) {
            dashboardAccountFilter.value = currentAccountVal;
        } else {
            dashboardAccountFilter.value = 'all';
            currentDashboardAccount = 'all';
            currentFilterAccount = 'all'; // ⭐️ 하단 필터 동기화
        }
        window.updateDashboardFilterStyle(dashboardAccountFilter);
    }
    
    // ⭐️ 차트 필터 옵션도 동적으로 업데이트
    const chartStockFilter = document.getElementById('chartStockFilter');
    if (chartStockFilter) {
        const currentStockVal = currentChartStock || 'all';
        let stockHtml = `<option value="all">모든 종목</option>`;
        if (stocks.length > 0) {
            stocks.forEach(stock => {
                stockHtml += `<option value="${stock.replace(/"/g, '&quot;')}">${stock}</option>`;
            });
        }
        chartStockFilter.innerHTML = stockHtml;
        if (chartStockFilter.querySelector(`option[value="${currentStockVal.replace(/"/g, '\\"')}"]`)) {
            chartStockFilter.value = currentStockVal;
        } else {
            chartStockFilter.value = 'all';
            currentChartStock = 'all';
        }
        window.updateDashboardFilterStyle(chartStockFilter);
    }

    const chartAccountFilter = document.getElementById('chartAccountFilter');
    if (chartAccountFilter) {
        const currentAccountVal = currentChartAccount || 'all';
        let accountHtml = `<option value="all">모든 분류</option>`;
        if (accounts.length > 0) {
            accounts.forEach(account => {
                accountHtml += `<option value="${account.replace(/"/g, '&quot;')}">${account}</option>`;
            });
        }
        chartAccountFilter.innerHTML = accountHtml;
        if (chartAccountFilter.querySelector(`option[value="${currentAccountVal.replace(/"/g, '\\"')}"]`)) {
            chartAccountFilter.value = currentAccountVal;
        } else {
            chartAccountFilter.value = 'all';
            currentChartAccount = 'all';
        }
        window.updateDashboardFilterStyle(chartAccountFilter);
    }

    const chartBrokerFilter = document.getElementById('chartBrokerFilter');
    if (chartBrokerFilter) {
        const currentBrokerVal = currentChartBroker || 'all';
        let brokerHtml = `<option value="all">모든 증권사</option>`;
        if (brokers.length > 0) {
            brokers.forEach(broker => {
                brokerHtml += `<option value="${broker.replace(/"/g, '&quot;')}">${broker}</option>`;
            });
        }
        chartBrokerFilter.innerHTML = brokerHtml;
        if (chartBrokerFilter.querySelector(`option[value="${currentBrokerVal.replace(/"/g, '\\"')}"]`)) {
            chartBrokerFilter.value = currentBrokerVal;
        } else {
            chartBrokerFilter.value = 'all';
            currentChartBroker = 'all';
        }
        window.updateDashboardFilterStyle(chartBrokerFilter);
    }

    const chartSubAccountFilter = document.getElementById('chartSubAccountFilter');
    if (chartSubAccountFilter) {
        const currentSubAccountVal = currentChartSubAccount || 'all';
        let subAccountHtml = `<option value="all">모든 계좌</option>`;
        if (subAccounts.length > 0) {
            subAccounts.forEach(sa => {
                subAccountHtml += `<option value="${sa.replace(/"/g, '&quot;')}">${sa}</option>`;
            });
        }
        chartSubAccountFilter.innerHTML = subAccountHtml;
        if (chartSubAccountFilter.querySelector(`option[value="${currentSubAccountVal.replace(/"/g, '\\"')}"]`)) {
            chartSubAccountFilter.value = currentSubAccountVal;
        } else {
            chartSubAccountFilter.value = 'all';
            currentChartSubAccount = 'all';
        }
        window.updateDashboardFilterStyle(chartSubAccountFilter);
    }
}

function displayEntries(isFilterUpdate = false) {
    cloudEntries.sort((a, b) => {
        const timeA = a.rawDate ? new Date(a.rawDate).getTime() : a.id;
        const timeB = b.rawDate ? new Date(b.rawDate).getTime() : b.id;
        return timeB - timeA;
    });

    if (!isFilterUpdate) {
        updateFilterDropdown();
        updatePortfolioSummary();
        renderCalendar();
    }

    // ⭐️ 리스트 갱신 시 순간적인 스크롤 튐(위로 점프) 현상을 방지하기 위해 이전 높이 임시 유지
    const prevHeight = historyList.offsetHeight;
    if (prevHeight > 0) historyList.style.minHeight = prevHeight + 'px';

    historyList.innerHTML = '';
    
    // ⭐️ 청산 종목 수량 계산 + 필터 연관 종목 추출을 단일 순회로 통합
    //   (기존: cloudEntries 를 최대 4회 반복 → 1회로 축소)
    const stockQtys = {};                              // 청산 종목 필터용 보유 수량
    const relatedStocksForAccountFilter = new Set();    // 분류별 모아보기 연관 종목
    const relatedStocksForBrokerFilter = new Set();     // 증권사별 모아보기 연관 종목
    const relatedStocksForSubAccountFilter = new Set(); // 증권계좌별 모아보기 연관 종목
    const needAccount = currentFilterAccount !== 'all';
    const needBroker = currentFilterBroker !== 'all';
    const needSubAccount = currentFilterSubAccount !== 'all';

    cloudEntries.forEach(entry => {
        const entryType = entry.type || 'trade';
        if (entryType !== 'trade' || !entry.stockName) return;
        const stockName = entry.stockName;

        if (entry.tradeType === '매수' || entry.tradeType === '매도') {
            if (stockQtys[stockName] === undefined) stockQtys[stockName] = 0;
            if (entry.tradeType === '매수') stockQtys[stockName] += (Number(entry.quantity) || 0);
            else stockQtys[stockName] -= (Number(entry.quantity) || 0);
        }

        // ⭐️ 분류별/증권사별 모아보기 시 연관된 일반 메모를 함께 보여주기 위해 종목명 추출
        if (needAccount && entry.accountName === currentFilterAccount) relatedStocksForAccountFilter.add(stockName);
        if (needBroker && entry.brokerAccount === currentFilterBroker) relatedStocksForBrokerFilter.add(stockName);
        if (needSubAccount && entry.subAccount === currentFilterSubAccount) relatedStocksForSubAccountFilter.add(stockName);
    });
    
    // ⭐️ 검색창에 입력 중인 텍스트가 있다면 다중 키워드 배열에 자동 등록하고 창 비움
    const pendingKeyword = filterStockInput.value.trim();
    if (pendingKeyword) {
        if (!currentFilterKeywords.includes(pendingKeyword)) {
            currentFilterKeywords.push(pendingKeyword);
        }
        filterStockInput.value = '';
        if (clearFilterBtn) clearFilterBtn.style.display = 'none';
    }

    const filteredEntries = cloudEntries.filter(entry => {
        if (currentFilterKeywords.length > 0) {
            for (const kw of currentFilterKeywords) {
                const lowerKw = kw.toLowerCase();
                const matchStock = entry.stockName && entry.stockName.toLowerCase().includes(lowerKw);
                const matchBroker = entry.brokerAccount && entry.brokerAccount.toLowerCase().includes(lowerKw);
                const matchSubAccount = entry.subAccount && entry.subAccount.toLowerCase().includes(lowerKw);
                const matchTags = entry.tags && entry.tags.toLowerCase().includes(lowerKw);
                const plainThoughts = entry.thoughts ? entry.thoughts.replace(/<[^>]*>?/gm, '').toLowerCase() : '';
                const matchThoughts = plainThoughts.includes(lowerKw);
                const matchTitle = entry.title && entry.title.toLowerCase().includes(lowerKw);
                if (!(matchStock || matchBroker || matchSubAccount || matchTags || matchThoughts || matchTitle)) return false;
            }
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

        if (currentFilterRecordType !== 'all') {
            const entryType = entry.type || 'trade';
            if (entryType !== currentFilterRecordType) return false;
        }
        if (currentFilterStock !== 'all') {
            if ((entry.stockName || '') !== currentFilterStock) return false;
        }
        if (currentFilterAccount !== 'all') {
            const entryType = entry.type || 'trade';
            const isMatchTrade = entryType === 'trade' && (entry.accountName || '') === currentFilterAccount;
            const isMatchMemo = entryType === 'memo' && relatedStocksForAccountFilter.has(entry.stockName);
            if (!isMatchTrade && !isMatchMemo) return false;
        }
        if (currentFilterBroker !== 'all') {
            const entryType = entry.type || 'trade';
            const isMatchTrade = entryType === 'trade' && (entry.brokerAccount || '') === currentFilterBroker;
            const isMatchMemo = entryType === 'memo' && relatedStocksForBrokerFilter.has(entry.stockName);
            if (!isMatchTrade && !isMatchMemo) return false;
        }
        if (currentFilterSubAccount !== 'all') {
            const entryType = entry.type || 'trade';
            const isMatchTrade = entryType === 'trade' && (entry.subAccount || '') === currentFilterSubAccount;
            const isMatchMemo = entryType === 'memo' && relatedStocksForSubAccountFilter.has(entry.stockName);
            if (!isMatchTrade && !isMatchMemo) return false;
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
    const filterBoxContainer = document.getElementById('filterBoxContainer');
    
    const hasDate = currentFilterDate !== null;
    const hasRecordType = currentFilterRecordType !== 'all';
    const hasStock = currentFilterStock !== 'all';
    const hasAccount = currentFilterAccount !== 'all';
    const hasBroker = currentFilterBroker !== 'all';
    const hasSubAccount = currentFilterSubAccount !== 'all';
    const hasKeyword = currentFilterKeywords.length > 0;
    
    const isListView = document.getElementById('btnListView') && document.getElementById('btnListView').classList.contains('active');

    if (isListView && (hasDate || hasRecordType || hasStock || hasAccount || hasBroker || hasSubAccount || hasKeyword)) {
        banner.style.display = 'flex';
        if (filterBoxContainer) filterBoxContainer.classList.add('filter-active');
        
        let chipsHtml = '';
        let activeFilterCount = 0;
        
        if (hasDate) {
            chipsHtml += `<span class="filter-chip">📅 ${currentFilterDate} <span class="chip-close" onclick="clearDateFilter()">&times;</span></span>`;
            activeFilterCount++;
        }
        if (hasRecordType) {
            const typeText = currentFilterRecordType === 'trade' ? '매매 기록' : '일반 메모';
            chipsHtml += `<span class="filter-chip">📑 ${typeText} <span class="chip-close" onclick="clearRecordTypeFilter()">&times;</span></span>`;
            activeFilterCount++;
        }
        if (hasStock) {
            chipsHtml += `<span class="filter-chip">🏢 ${currentFilterStock} <span class="chip-close" onclick="clearStockFilter()">&times;</span></span>`;
            activeFilterCount++;
        }
        if (hasAccount) {
            chipsHtml += `<span class="filter-chip">💼 ${currentFilterAccount} <span class="chip-close" onclick="clearAccountFilter()">&times;</span></span>`;
            activeFilterCount++;
        }
        if (hasBroker) {
            chipsHtml += `<span class="filter-chip">🏦 ${currentFilterBroker} <span class="chip-close" onclick="clearBrokerFilter()">&times;</span></span>`;
            activeFilterCount++;
        }
        if (hasSubAccount) {
            chipsHtml += `<span class="filter-chip">💳 ${currentFilterSubAccount} <span class="chip-close" onclick="clearSubAccountFilter()">&times;</span></span>`;
            activeFilterCount++;
        }
        if (hasKeyword) {
            currentFilterKeywords.forEach((kw, idx) => {
                chipsHtml += `<span class="filter-chip">🔍 '${kw}' <span class="chip-close" onclick="clearKeywordFilter(${idx})" title="검색어 해제">&times;</span></span>`;
                activeFilterCount++;
            });
        }
        
        // 편의 기능: 조건이 2개 이상 섞여 있을 때는 '전체 초기화' 단축 버튼 추가
        if (activeFilterCount >= 2) {
            chipsHtml += `<span onclick="clearAllFilters()" style="font-size: 11px; color: var(--danger-color); cursor: pointer; text-decoration: underline; margin-left: 5px;" title="모든 조건 해제">전체 초기화</span>`;
        }
        
        chipsHtml += `<span style="margin-left: auto; color: var(--danger-color); font-size: 12px; font-weight: bold;">총 ${filteredEntries.length}건</span>`;
        
        banner.innerHTML = chipsHtml;
    } else { 
        banner.style.display = 'none'; 
        if (filterBoxContainer) filterBoxContainer.classList.remove('filter-active');
    }

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
    const keywords = currentFilterKeywords;
    
    function highlight(text, isHtml = false) {
        if (!text || keywords.length === 0) return text || '';
        let result = text;
        keywords.forEach(kw => {
            const safeKw = kw.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
            // 일반 텍스트든 HTML 텍스트든 중복 <mark> 태그 방지를 위해 HTML 태그 내용물 무시 정규식 적용
            const regex = new RegExp(`(${safeKw})(?![^<]*>)`, 'gi');
            result = result.replace(regex, `<mark class="search-highlight">$1</mark>`);
        });
        return result;
    }

    const listFragment = document.createDocumentFragment();

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
            listFragment.appendChild(divider);
            lastRenderedMonth = entryMonth;
        }

        const card = document.createElement('div');
        card.className = 'entry-card';
        const entryType = entry.type || 'trade';
        const imageHtml = entry.attachedImage ? `<div style="margin-top:10px;"><img src="${entry.attachedImage}" class="entry-thumbnail" loading="lazy" decoding="async" onclick="openImageViewer(this.src, event)" title="클릭하여 원본 보기"></div>` : '';

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

        const safeStockName = entry.stockName ? entry.stockName.replace(/'/g, "\\'") : '';
        const displayStockName = highlight(entry.stockName);
        const stockBadge = entry.stockName ? `<span class="cal-badge stock" style="padding:4px 10px; border-radius:12px; font-size:0.95em; font-weight:bold; color:var(--text-strong-color); margin:0;" onclick="filterByStock('${safeStockName}', event)" title="${entry.stockName} 모아보기">🏷️ ${displayStockName}</span>` : '';

        if (entryType === 'memo') {
            card.style.borderLeftColor = 'var(--info-color)';
            const displayBroker = highlight(entry.brokerAccount);
            const displaySubAccount = highlight(entry.subAccount);
            const brokerBadge = entry.brokerAccount ? `<span style="font-size: 0.85em; color: var(--text-muted-color); font-weight: normal; margin:0;">🏦 ${displayBroker}${entry.subAccount ? ` - ${displaySubAccount}` : ''}</span>` : '';
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
            } else if(entry.tradeType === '배당') {
                typeColor = 'var(--warning-color)';
                borderColor = 'var(--warning-color)';
                badgeClass = 'dividend';
            }
            
            card.style.borderLeftColor = borderColor;

            let detailsHtml = '';
            if (entry.tradeType === '배당' && (entry.price > 0 || entry.quantity > 0)) {
                const totalAmount = (entry.price * (entry.quantity || 1)).toLocaleString();
                detailsHtml = `
                    <div class="entry-details">
                        <div class="detail-item">배당금: <span>${totalAmount}</span></div>
                    </div>
                `;
            } else if (entry.tradeType !== '관망' && entry.tradeType !== '주시' && (entry.price > 0 || entry.quantity > 0)) {
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
            const displaySubAccount = highlight(entry.subAccount);
            const brokerBadge = entry.brokerAccount ? `<span style="font-size: 0.85em; color: var(--text-muted-color); font-weight: normal; margin:0;">🏦 ${displayBroker}${entry.subAccount ? ` - ${displaySubAccount}` : ''}</span>` : '';
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
                ${imageHtml}
            `;
        }

        const editBtn = card.querySelector('.btn-edit');
        editBtn.addEventListener('click', () => editEntry(entry));

        const deleteBtn = card.querySelector('.btn-delete');
        deleteBtn.addEventListener('click', () => deleteEntry(entry.id));

        // ⭐️ 에디터 본문 내 이미지 클릭 시 원본 보기 (확대/축소 지원)
        //    + 화면 밖 이미지는 스크롤 시점에 지연 로드하여 초기 렌더링 부담 제거
        const contentImages = card.querySelectorAll('.entry-content img');
        contentImages.forEach(img => {
            img.loading = 'lazy';
            img.decoding = 'async';
            img.addEventListener('click', (e) => {
                window.openImageViewer(img.src, e);
            });
        });

        listFragment.appendChild(card);
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
        listFragment.appendChild(sentinel);
        loadMoreObserver.observe(sentinel);
    }
    historyList.appendChild(listFragment);
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
    document.getElementById('subAccount').value = entry.subAccount || '';
    
    // ⭐️ 과거 하단에 첨부했던 이미지가 있다면 에디터 본문으로 자동 이동(마이그레이션)
    let contentHtml = entry.thoughts || '';
    if (entry.attachedImage && !contentHtml.includes(entry.attachedImage)) {
        contentHtml += `<p><br></p><p><img src="${entry.attachedImage}"></p>`;
    }
    if (window.quill) window.quill.root.innerHTML = contentHtml; // 에디터에 기존 내용 불러오기
    
    currentTags = entry.tags ? entry.tags.split(',').filter(Boolean) : [];
    renderTags();
    calcTotalAmount();
    
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

    // 기존 저장된 매매 포지션(tradeType)에 맞추어 필수 입력 여부 재설정
    toggleFormUI(entry.type || 'trade');

    submitBtn.innerText = "수정";
}

async function deleteEntry(id) {
    if (await customConfirm("정말로 이 기록을 삭제하시겠습니까?\n(삭제 후 로컬 파일에 즉시 반영됩니다)")) {
        try {
            const res = await fetch(`/api/entry/${id}`, {
                method: 'DELETE'
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

let imageZoom = 1;
let imagePanX = 0;
let imagePanY = 0;
let imageIsDragging = false;
let imageIsPinching = false; // ⭐️ 핀치 줌 상태 변수 추가
let imageStartX = 0;
let imageStartY = 0;
let initialPinchDistance = null;
let initialPinchZoom = 1;

function updateImageViewerTransform() {
    const wrapper = document.getElementById('imageViewerWrapper');
    const fullSizeImage = document.getElementById('fullSizeImage');
    const btnClose = document.getElementById('btnImageViewerClose');
    if (!wrapper || !fullSizeImage) return;
    
    // ⭐️ 드래그 중이거나 핀치 줌 중일 때는 transition을 제거하여 즉각(버벅임 없이) 반응하도록 조정
    if (imageIsDragging || imageIsPinching) {
        wrapper.style.transition = 'none';
        if (btnClose) btnClose.style.transition = 'background 0.2s';
    } else {
        wrapper.style.transition = 'transform 0.1s ease-out';
        if (btnClose) btnClose.style.transition = 'background 0.2s, transform 0.1s ease-out';
    }
    
    wrapper.style.transform = `translate(${imagePanX}px, ${imagePanY}px) scale(${imageZoom})`;
    if (btnClose) {
        btnClose.style.transform = `scale(${1 / imageZoom})`;
    }

    if (imageZoom > 1) {
        fullSizeImage.style.cursor = imageIsDragging ? 'grabbing' : 'zoom-out';
    } else {
        fullSizeImage.style.cursor = 'zoom-in';
    }
}

window.openImageViewer = function(src, event) {
    if (event) event.stopPropagation();
    const modal = document.getElementById('imageViewerModal');
    document.getElementById('fullSizeImage').src = src;
    modal.style.display = 'flex';
    document.body.style.overflow = 'hidden'; // ⭐️ 모달 열림 시 배경 스크롤 방지
    
    // ⭐️ 이미지 확대/팬 상태 초기화
    imageZoom = 1;
    imagePanX = 0;
    imagePanY = 0;
    imageIsDragging = false;
    imageIsPinching = false;
    updateImageViewerTransform();
};

window.closeImageViewer = function() {
    const modal = document.getElementById('imageViewerModal');
    if (modal.classList.contains('closing')) return; // ⭐️ 중복 실행 방지
    modal.classList.add('closing');
    setTimeout(() => { 
        modal.style.display = 'none'; 
        modal.classList.remove('closing'); 
        document.body.style.overflow = ''; 
    }, 180);
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
        if (!dailyStats[dateKey].details[stockKey]) dailyStats[dateKey].details[stockKey] = { buyCount: 0, sellCount: 0, watchCount: 0, memoCount: 0, dividendCount: 0 };
        
        if (entry.type === 'trade') {
            if (entry.tradeType === '매수') dailyStats[dateKey].details[stockKey].buyCount++;
            else if (entry.tradeType === '매도') dailyStats[dateKey].details[stockKey].sellCount++;
            else if (entry.tradeType === '주시' || entry.tradeType === '관망') dailyStats[dateKey].details[stockKey].watchCount++;
            else if (entry.tradeType === '배당') dailyStats[dateKey].details[stockKey].dividendCount++;

            if (entry.stockName) {
                const stock = entry.stockName, qty = Number(entry.quantity) || 0, price = Number(entry.price) || 0;
                if (!portfolio[stock]) portfolio[stock] = { qty: 0, totalCost: 0, avgPrice: 0 };

                // ⭐️ 공용 계산 엔진(calc.js) — 일별 실현손익(매도 차익 + 배당)
                const r = applyTradeToHolding(portfolio[stock], qty, price, entry.tradeType);
                dailyStats[dateKey].profit += r.realized + r.dividend;
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
            if (counts.dividendCount > 0) {
                const typeArg = `stock_trade_${safeStock}`;
                badgesHtml += `<div class="cal-badge dividend" onclick="showDetailsForDate('${key}', '${typeArg}', event)">${prefix}배당 ${counts.dividendCount}건</div>`;
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
    
    // ⭐️ 캘린더 렌더링 시 월별 실현 손익 차트 업데이트
    window.renderMonthlyProfitChart();
}

window.showDetailsForDate = function(date, typeArg, event) {
    if (event) event.stopPropagation();
    clearAllFilters(false); // ⭐️ 전체 필터 및 UI 초기화 (렌더링은 중복 방지)
    currentFilterDate = date;
    
    if (typeArg && typeArg.startsWith('stock_trade_')) {
        currentFilterRecordType = 'trade';
        currentFilterStock = typeArg.substring(12);
    } else if (typeArg && typeArg.startsWith('stock_memo_')) {
        currentFilterRecordType = 'memo';
        currentFilterStock = typeArg.substring(11);
    }
    
    window.saveFilterPreferences();
    
    // ⭐️ 새로 설정된 필터 상태를 UI에 동기화
    const typeSelect = document.getElementById('filterRecordTypeSelect');
    if (typeSelect) {
        typeSelect.value = currentFilterRecordType;
        window.updateDashboardFilterStyle(typeSelect);
    }
    const stockSelect = document.getElementById('filterStockSelect');
    if (stockSelect && stockSelect.querySelector(`option[value="${currentFilterStock.replace(/"/g, '\\"')}"]`)) {
        stockSelect.value = currentFilterStock;
        window.updateDashboardFilterStyle(stockSelect);
    }
    
    document.getElementById('btnListView').click();
    displayEntries(true);

    window.scrollToFilterBox();
};

window.filterByStock = function(stockName, event) {
    if (event) event.stopPropagation();
    clearAllFilters(false);
    currentFilterStock = stockName;
    window.saveFilterPreferences();
    
    const stockSelect = document.getElementById('filterStockSelect');
    if (stockSelect && stockSelect.querySelector(`option[value="${currentFilterStock.replace(/"/g, '\\"')}"]`)) {
        stockSelect.value = currentFilterStock;
        window.updateDashboardFilterStyle(stockSelect);
    }
    
    const btnListView = document.getElementById('btnListView');
    if (btnListView && !btnListView.classList.contains('active')) {
        btnListView.click();
    }
    
    displayEntries(true);
    window.scrollToFilterBox();
};

// ⭐️ 캘린더 하단 차트 종류 스위칭 함수
window.setMonthlyChartType = function(type) {
    window.currentMonthlyChartType = type;
    document.querySelectorAll('.chart-type-btn').forEach(btn => {
        if (btn.dataset.type === type) {
            btn.style.backgroundColor = 'var(--primary-color)';
            btn.style.color = 'white';
        } else {
            btn.style.backgroundColor = 'transparent';
            btn.style.color = 'var(--primary-color)';
        }
    });
    
    // 성과분석 뷰 숨기고 차트 뷰 보이기
    const inlineStatsContainer = document.getElementById('inlineStatsContainer');
    const monthlyProfitChartContainer = document.getElementById('monthlyProfitChartContainer');
    const btnStats = document.getElementById('btnStats');
    if (inlineStatsContainer && monthlyProfitChartContainer) {
        inlineStatsContainer.style.display = 'none';
        monthlyProfitChartContainer.style.display = 'block';
        if (btnStats) {
            btnStats.style.backgroundColor = 'transparent';
            btnStats.style.color = 'var(--primary-color)';
        }
    }
    
    window.renderMonthlyProfitChart();
};

// ⭐️ 주간/월간 집계 단위 토글 버튼 표시 동기화 (KRX/NXT 토글과 동일 방식)
window.updateChartGranularityToggle = function() {
    const btn = document.getElementById('btnToggleChartGranularity');
    if (!btn) return;
    const isMonthly = (window.currentChartGranularity || 'monthly') === 'monthly';
    btn.innerText = isMonthly ? '월간' : '주간';
    // ⭐️ 단위와 무관하게 항상 선택 상태(파란 바탕/흰 글씨) 유지
    btn.style.backgroundColor = 'var(--primary-color)';
    btn.style.color = '#fff';
};

// ⭐️ 클릭 시 월간 ↔ 주간 전환 (단일 토글 버튼)
window.toggleChartGranularity = function() {
    window.currentChartGranularity = (window.currentChartGranularity || 'monthly') === 'monthly' ? 'weekly' : 'monthly';
    window.updateChartGranularityToggle();
    // 집계 단위 변경 시 열려있던 상세 내역은 닫아 혼동 방지
    const detailListEl = document.getElementById('chartDetailList');
    if (detailListEl) detailListEl.style.display = 'none';
    
    window.renderMonthlyProfitChart();
};

// ⭐️ 차트 막대 클릭 시 하단에 종목별 상세 내역을 그려주는 함수
window.renderChartDetailList = function(title, breakdown, isProfit) {
    const inlineStatsContainer = document.getElementById('inlineStatsContainer');
    if (inlineStatsContainer) {
        inlineStatsContainer.style.display = 'none';
        const btnStats = document.getElementById('btnStats');
        if (btnStats) {
            btnStats.style.backgroundColor = 'transparent';
            btnStats.style.color = 'var(--primary-color)';
        }
    }
    
    const container = document.getElementById('chartDetailList');
    if (!container) return;
    
    let html = `<div style="font-size: 13px; font-weight: bold; margin-bottom: 10px; color: var(--text-strong-color); display: flex; justify-content: space-between; align-items: center;">
                    <span>📊 ${title}</span>
                    <span style="font-size: 11px; color: var(--text-muted-color); font-weight: normal; cursor: pointer;" onclick="document.getElementById('chartDetailList').style.display='none';">닫기 &times;</span>
                </div>`;
    
    const stocks = Object.keys(breakdown).filter(s => breakdown[s] !== 0);
    stocks.sort((a, b) => breakdown[b] - breakdown[a]); // 금액(손익) 기준 내림차순 정렬
    
    if (stocks.length === 0) {
        html += `<div style="color: var(--text-muted-color); font-size: 12px; text-align: center; padding: 10px 0;">해당 내역이 없습니다.</div>`;
    } else {
        html += `<div style="display: grid; gap: 6px;">`;
        stocks.forEach(s => {
            const val = breakdown[s];
            let color = 'var(--text-strong-color)';
            let prefix = '';
            if (isProfit) {
                if (val > 0) { color = 'var(--danger-color)'; prefix = '+'; }
                else if (val < 0) { color = 'var(--primary-color)'; }
            }
            html += `<div style="display: flex; justify-content: space-between; font-size: 12px; padding: 6px 10px; background: var(--bg-color); border-radius: 6px; border: 1px solid var(--border-light-color);">
                <span style="font-weight: bold; color: var(--text-strong-color);">${s}</span>
                <span style="color: ${color};">${prefix}${Math.round(val).toLocaleString()}원</span>
            </div>`;
        });
        html += `</div>`;
    }
    container.innerHTML = html;
    container.style.display = 'block';
};

// ⭐️ 최근 12개월 월별 실현손익/평가손익/매매금액 바 차트 렌더링 함수 (통합)
window.renderMonthlyProfitChart = function() {
    console.log("[Chart] 월별 실현/평가/매매금액 차트 렌더링 시작...");
    
    // 성과분석 뷰가 열려있다면 필터 갱신에 맞춰 데이터 다시 불러오기
    const inlineStatsContainer = document.getElementById('inlineStatsContainer');
    if (inlineStatsContainer && inlineStatsContainer.style.display === 'block') {
        window.loadTradeStats();
    }
    
    // ⭐️ 차트 필터 초기화 버튼 노출 제어 로직
    const chartClearAllBtnWrapper = document.getElementById('chartClearAllBtnWrapper');
    if (chartClearAllBtnWrapper) {
        let activeFilterCount = 0;
        if (currentChartStock !== 'all') activeFilterCount++;
        if (currentChartAccount !== 'all') activeFilterCount++;
        if (currentChartBroker !== 'all') activeFilterCount++;
        if (currentChartSubAccount !== 'all') activeFilterCount++;

        // 필터가 1개 이상 적용되었을 때 우측 끝에 초기화 버튼 노출
        chartClearAllBtnWrapper.style.display = activeFilterCount >= 1 ? 'flex' : 'none';
    }
    
    const monthlyData = {};
    const now = new Date();
    const labels = [];

    // ⭐️ 집계 단위 (월간/주간). 주간일 경우 해당 주 월요일 날짜(YYYY-MM-DD)를 키로 사용
    const granularity = window.currentChartGranularity || 'monthly';
    const isWeekly = granularity === 'weekly';
    const periodWord = isWeekly ? '주' : '월';
    if (window.updateChartGranularityToggle) window.updateChartGranularityToggle(); // 토글 버튼 표시 동기화

    // 주의 시작(월요일) 날짜 객체 반환
    const getMonday = (input) => {
        const d = new Date(input);
        d.setHours(0, 0, 0, 0);
        const day = d.getDay(); // 0=일 ~ 6=토
        const diff = (day === 0 ? -6 : 1 - day);
        d.setDate(d.getDate() + diff);
        return d;
    };
    const fmtYmd = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    // 기록에서 Date 객체 추출 (rawDate 우선, 없으면 표시용 date 문자열 파싱)
    const getEntryDate = (entry) => {
        if (entry.rawDate) {
            const d = new Date(entry.rawDate);
            if (!isNaN(d)) return d;
        }
        if (entry.date) {
            const parts = entry.date.split('. ').map(p => p.trim()).filter(Boolean);
            if (parts.length >= 3) return new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
            if (parts.length >= 2) return new Date(Number(parts[0]), Number(parts[1]) - 1, 1);
        }
        return null;
    };
    // 기록의 집계 키 산출 (월간: YYYY-MM, 주간: 해당 주 월요일 YYYY-MM-DD)
    const periodKeyOf = (entry) => {
        if (isWeekly) {
            const ed = getEntryDate(entry);
            if (!ed || isNaN(ed)) return '';
            return fmtYmd(getMonday(ed));
        }
        if (entry.rawDate) return entry.rawDate.substring(0, 7);
        if (entry.date) {
            const parts = entry.date.split('. ');
            if (parts.length >= 2) return `${parts[0]}-${parts[1].padStart(2, '0')}`;
        }
        return '';
    };
    const allProfitByMonth = {}; // ⭐️ 전체 기간의 월별 실현손익을 추적하여 누적 계산에 활용
    const allProfitByMonthStock = {}; // ⭐️ 전체 기간의 누적 계산용 '종목별' 실현손익 추적
    const dividendByMonth = {}; // ⭐️ 전체 기간의 월별 배당금 추적
    const dividendByMonthStock = {}; // ⭐️ 전체 기간의 종목별 배당금 추적
    
    // ⭐️ 렌더링 초기화 시 하단 상세 내역 영역 닫기
    const detailListEl = document.getElementById('chartDetailList');
    if (detailListEl) detailListEl.style.display = 'none';
    
    // ⭐️ 최근 12개 기간(월간: 12개월 / 주간: 12주) 라벨 생성
    const thisMonday = getMonday(now);
    for (let i = 11; i >= 0; i--) {
        let key;
        if (isWeekly) {
            const d = new Date(thisMonday);
            d.setDate(d.getDate() - i * 7);
            key = fmtYmd(d);
        } else {
            const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
            key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
        }
        monthlyData[key] = {
            realized: 0, evaluated: 0, buy_volume: 0, sell_volume: 0, dividend: 0,
            realized_breakdown: {}, evaluated_breakdown: {}, buy_volume_breakdown: {}, sell_volume_breakdown: {}, cumulative_breakdown: {}, dividend_breakdown: {}
        };
        labels.push(key);
    }
    
    const chronological = [...cloudEntries].sort((a, b) => {
        const timeA = a.rawDate ? new Date(a.rawDate).getTime() : a.id;
        const timeB = b.rawDate ? new Date(b.rawDate).getTime() : b.id;
        return timeA - timeB;
    });

    const portfolio = {};
    const stockRemainingBuys = {}; // ⭐️ 선입선출(FIFO) 기반 각 매수 건의 잔여 수량 추적
    
    chronological.forEach(entry => {
        if (entry.type !== 'trade' || !entry.stockName) return;
        
        // ⭐️ 차트 전용 필터 적용
        if (currentChartStock !== 'all' && entry.stockName !== currentChartStock) return;
        if (currentChartAccount !== 'all' && (entry.accountName || '') !== currentChartAccount) return;
        if (currentChartBroker !== 'all' && (entry.brokerAccount || '') !== currentChartBroker) return;
        if (currentChartSubAccount !== 'all' && (entry.subAccount || '') !== currentChartSubAccount) return;
        
        const dateKey = periodKeyOf(entry);
        if (!dateKey) return;

        const stock = entry.stockName;
        const qty = Number(entry.quantity) || 0;
        const price = Number(entry.price) || 0;

        if (!portfolio[stock]) portfolio[stock] = { qty: 0, totalCost: 0, avgPrice: 0, stockCode: entry.stockCode || '' };
        if (entry.stockCode) portfolio[stock].stockCode = entry.stockCode; // 최신 종목코드 갱신
        if (!stockRemainingBuys[stock]) stockRemainingBuys[stock] = [];
        
        if (!allProfitByMonth[dateKey]) allProfitByMonth[dateKey] = 0;
        if (!allProfitByMonthStock[dateKey]) allProfitByMonthStock[dateKey] = {};
        if (!allProfitByMonthStock[dateKey][stock]) allProfitByMonthStock[dateKey][stock] = 0;
        
        if (!dividendByMonth[dateKey]) dividendByMonth[dateKey] = 0;
        if (!dividendByMonthStock[dateKey]) dividendByMonthStock[dateKey] = {};
        if (!dividendByMonthStock[dateKey][stock]) dividendByMonthStock[dateKey][stock] = 0;

        if (entry.tradeType === '매수') {
            // ⭐️ 공용 계산 엔진(calc.js) — 평균단가/포지션 갱신
            applyTradeToHolding(portfolio[stock], qty, price, '매수');

            // ⭐️ 잔여 수량 큐에 삽입 및 거래대금(매매금액) 합산
            stockRemainingBuys[stock].push({ dateKey, qty, price });
            if (monthlyData[dateKey]) {
                const vol = price * qty;
                monthlyData[dateKey].buy_volume += vol;
                monthlyData[dateKey].buy_volume_breakdown[stock] = (monthlyData[dateKey].buy_volume_breakdown[stock] || 0) + vol;
            }
            
        } else if (entry.tradeType === '매도') {
            // ⭐️ 공용 계산 엔진(calc.js): 갱신 전 평단으로 실현손익 산출 + 포지션 즉시 갱신
            const profit = applyTradeToHolding(portfolio[stock], qty, price, '매도').realized;
            allProfitByMonth[dateKey] += profit; // ⭐️ 전체 기간 수익 누적용
            allProfitByMonthStock[dateKey][stock] += profit;
            
            if (monthlyData[dateKey]) {
                monthlyData[dateKey].realized += profit;
                monthlyData[dateKey].sell_volume += (price * qty);
                monthlyData[dateKey].realized_breakdown[stock] = (monthlyData[dateKey].realized_breakdown[stock] || 0) + profit;
                monthlyData[dateKey].sell_volume_breakdown[stock] = (monthlyData[dateKey].sell_volume_breakdown[stock] || 0) + (price * qty);
            }
            // (포지션 갱신은 위 applyTradeToHolding 에서 이미 처리됨)

            // ⭐️ 매도 시 과거 매수 기록부터 선입선출(FIFO)로 차감하여 현재 미청산 매수건 파악
            let sellQty = qty;
            while(sellQty > 0 && stockRemainingBuys[stock].length > 0) {
                let firstBuy = stockRemainingBuys[stock][0];
                if (firstBuy.qty <= sellQty) {
                    sellQty -= firstBuy.qty;
                    stockRemainingBuys[stock].shift(); // 전량 청산
                } else {
                    firstBuy.qty -= sellQty;
                    sellQty = 0; // 일부만 청산
                }
            }
        } else if (entry.tradeType === '배당') {
            allProfitByMonth[dateKey] += (price * qty); // ⭐️ 배당금 누적용
            allProfitByMonthStock[dateKey][stock] += (price * qty);
            
            dividendByMonth[dateKey] += (price * qty); // ⭐️ 순수 배당금 누적용
            dividendByMonthStock[dateKey][stock] += (price * qty);
            
            if (monthlyData[dateKey]) {
                monthlyData[dateKey].dividend += (price * qty);
                monthlyData[dateKey].dividend_breakdown[stock] = (monthlyData[dateKey].dividend_breakdown[stock] || 0) + (price * qty);
            }
        }
    });

    // ⭐️ 현재가(Cache)를 바탕으로 현재 청산되지 않고 남은 매수 건들의 평가 손익 계산
    for (const stock in stockRemainingBuys) {
        const stockCode = portfolio[stock]?.stockCode;
        const currentPrice = window.currentPriceCache[stockCode];
        if (currentPrice !== undefined && currentPrice !== null) {
            stockRemainingBuys[stock].forEach(buy => {
                if (monthlyData[buy.dateKey]) {
                    const evalProfit = (currentPrice - buy.price) * buy.qty;
                    monthlyData[buy.dateKey].evaluated += evalProfit;
                    monthlyData[buy.dateKey].evaluated_breakdown[stock] = (monthlyData[buy.dateKey].evaluated_breakdown[stock] || 0) + evalProfit;
                }
            });
        }
    }
    
    // ⭐️ 12개월 라벨별로 과거부터 해당 월까지의 총 누적 수익금 계산 및 상세 내역 생성
    labels.forEach(label => {
        let cum = 0;
        let cumDiv = 0;
        let cumBreakdown = {};
        let cumDivBreakdown = {};
        
        for (const key in allProfitByMonth) {
            if (key <= label) {
                cum += allProfitByMonth[key];
                cumDiv += (dividendByMonth[key] || 0);
                
                if (allProfitByMonthStock[key]) {
                    for (const s in allProfitByMonthStock[key]) {
                        cumBreakdown[s] = (cumBreakdown[s] || 0) + allProfitByMonthStock[key][s];
                    }
                }
                if (dividendByMonthStock[key]) {
                    for (const s in dividendByMonthStock[key]) {
                        cumDivBreakdown[s] = (cumDivBreakdown[s] || 0) + dividendByMonthStock[key][s];
                    }
                }
            }
        }
        monthlyData[label].cumulative = cum;
        monthlyData[label].cumulative_dividend = cumDiv;
        monthlyData[label].cumulative_breakdown = cumBreakdown;
        monthlyData[label].cumulative_div_breakdown = cumDivBreakdown;
    });

    const theme = document.documentElement.getAttribute('data-theme') || 'light';
    const isDark = theme === 'dark';
    
    const type = window.currentMonthlyChartType || 'realized';
    let datasets = [];

    // ⭐️ 막대 최소 높이(px): 값이 너무 작아 막대가 보이지 않는 경우에도 운용자가 인지할 수 있도록 최소 픽셀만큼 그린다.
    //    단, 실제 0(거래 없는 달)은 null 처리하여 막대를 그리지 않으므로 빈 달과 구분된다.
    const MIN_BAR_LENGTH = 2;
    const nz = (v) => (v === 0 ? null : v); // 0 → null (빈 달은 막대 미표시)

    if (type === 'realized') {
        const dataRealized = labels.map(l => monthlyData[l].realized);
        const dataDividend = labels.map(l => monthlyData[l].dividend || 0);
        
        const bgColors = dataRealized.map(val => val > 0 ? (isDark ? 'rgba(163, 78, 78, 0.85)' : 'rgba(231, 76, 60, 0.85)') : (val < 0 ? (isDark ? 'rgba(59, 104, 140, 0.85)' : 'rgba(52, 152, 219, 0.85)') : (isDark ? 'rgba(85, 85, 85, 0.85)' : 'rgba(189, 195, 199, 0.85)')));
        const divBgColor = isDark ? 'rgba(214, 137, 16, 0.85)' : 'rgba(243, 156, 18, 0.85)';
        
        datasets = [
            {
                label: '배당 수익금',
                data: dataDividend.map(nz),
                backgroundColor: divBgColor,
                borderRadius: 4,
                minBarLength: MIN_BAR_LENGTH
            },
            {
                label: '매매 실현손익',
                data: dataRealized.map(nz),
                backgroundColor: bgColors,
                borderRadius: 4,
                minBarLength: MIN_BAR_LENGTH
            }
        ];
    } else if (type === 'evaluated') {
        const data = labels.map(l => monthlyData[l].evaluated);
        const backgroundColors = data.map(val => val > 0 ? (isDark ? 'rgba(163, 78, 78, 0.85)' : 'rgba(231, 76, 60, 0.85)') : (val < 0 ? (isDark ? 'rgba(59, 104, 140, 0.85)' : 'rgba(52, 152, 219, 0.85)') : (isDark ? 'rgba(85, 85, 85, 0.85)' : 'rgba(189, 195, 199, 0.85)')));
        datasets = [{
            label: `해당 ${periodWord} 매수분의 현재 평가 손익`,
            data: data.map(nz),
            backgroundColor: backgroundColors,
            borderRadius: 4,
            minBarLength: MIN_BAR_LENGTH
        }];
    } else if (type === 'volume') {
        datasets = [
            {
                label: '매수 금액 (하단)',
                data: labels.map(l => monthlyData[l].buy_volume).map(nz),
                backgroundColor: isDark ? 'rgba(163, 78, 78, 0.85)' : 'rgba(231, 76, 60, 0.85)',
                borderRadius: { topLeft: 0, topRight: 0, bottomLeft: 4, bottomRight: 4 },
                minBarLength: MIN_BAR_LENGTH
            },
            {
                label: '매도 금액 (상단)',
                data: labels.map(l => monthlyData[l].sell_volume).map(nz),
                backgroundColor: isDark ? 'rgba(59, 104, 140, 0.85)' : 'rgba(52, 152, 219, 0.85)',
                borderRadius: { topLeft: 4, topRight: 4, bottomLeft: 0, bottomRight: 0 },
                minBarLength: MIN_BAR_LENGTH
            }
        ];
    } else if (type === 'cumulative') {
        const data = labels.map(l => monthlyData[l].cumulative);
        const divData = labels.map(l => monthlyData[l].cumulative_dividend || 0);
        
        const lineColor = isDark ? '#3a7a4f' : '#27ae60'; // ⭐️ 자산 우상향을 상징하는 초록색 계열
        const bgColor = isDark ? 'rgba(58, 122, 79, 0.3)' : 'rgba(39, 174, 96, 0.2)';
        
        const divLineColor = isDark ? '#d68910' : '#f39c12'; // ⭐️ 배당을 상징하는 노란색/주황색 계열
        const divBgColor = isDark ? 'rgba(214, 137, 16, 0.3)' : 'rgba(243, 156, 18, 0.2)';
        
        datasets = [
            {
                type: 'line',
                label: '총 누적 수익금',
                data: data,
                borderColor: lineColor,
                backgroundColor: bgColor,
                borderWidth: 2,
                pointBackgroundColor: lineColor,
                pointBorderColor: isDark ? '#1e1e1e' : '#fff',
                fill: true,
                tension: 0.3
            },
            {
                type: 'line',
                label: '누적 배당 수익금',
                data: divData,
                borderColor: divLineColor,
                backgroundColor: divBgColor,
                borderWidth: 2,
                pointBackgroundColor: divLineColor,
                pointBorderColor: isDark ? '#1e1e1e' : '#fff',
                fill: true,
                tension: 0.3
            }
        ];
    }
    
    const displayLabels = isWeekly
        ? labels.map(l => { const p = l.split('-'); return `${parseInt(p[1], 10)}/${parseInt(p[2], 10)}`; }) // 주: 월요일 'M/D'
        : labels.map(l => l.split('-')[1].replace(/^0+/, '') + '월');

    const ctx = document.getElementById('monthlyProfitChart');
    if (!ctx) {
        console.warn("[Chart Error] 'monthlyProfitChart' 캔버스를 찾을 수 없습니다! HTML 파일이 정상적으로 업데이트되었는지 확인해 주세요.");
        return;
    }

    if (window.monthlyProfitChartInstance) window.monthlyProfitChartInstance.destroy();
    
    Chart.defaults.color = isDark ? '#aaaaaa' : '#7f8c8d';

    window.monthlyProfitChartInstance = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: displayLabels,
            datasets: datasets
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            // ⭐️ 정확히 점을 찌르지 않고 근처 영역(초록/노란색)만 터치해도 반응하도록 설정
            interaction: {
                mode: 'nearest',
                intersect: false
            },
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            let value = context.parsed.y;
                            if (type === 'volume') {
                                let prefix = context.datasetIndex === 0 ? '매수: ' : '매도: ';
                                return prefix + Math.round(value).toLocaleString() + '원';
                            }
                            if (type === 'realized' || type === 'cumulative') {
                                const labelStr = context.dataset.label;
                                return labelStr + ': ' + (value > 0 ? '+' : '') + Math.round(value).toLocaleString() + '원';
                            }
                            return (value > 0 ? '+' : '') + Math.round(value).toLocaleString() + '원';
                        }
                    }
                }
            },
            scales: {
                y: {
                    stacked: type === 'volume' || type === 'realized',
                    beginAtZero: true,
                    grid: { color: isDark ? '#333333' : '#eeeeee' },
                    ticks: { callback: function(value) { return value.toLocaleString(); } }
                },
                x: { 
                    stacked: type === 'volume' || type === 'realized',
                    grid: { display: false } 
                }
            },
            // ⭐️ 마우스 호버 시 포인터 변경 (클릭 가능함 암시)
            onHover: (e, elements, chart) => {
                chart.canvas.style.cursor = elements.length ? 'pointer' : 'default';
            },
            // ⭐️ 막대 클릭 이벤트 로직 추가
            onClick: (e, elements, chart) => {
                if (elements.length === 0) return;
                
                const index = elements[0].index;
                const datasetIndex = elements[0].datasetIndex; // 매수(0)/매도(1) 구분용
                const monthLabel = labels[index]; // 예: '2023-10' (월간) / '2023-10-16' (주간)
                const dataObj = monthlyData[monthLabel];
                // ⭐️ 주간은 'M/D 주', 월간은 원본 키를 상세 내역 제목에 사용
                const periodLabel = isWeekly ? `${displayLabels[index]} 주간` : monthLabel;

                let breakdown = {};
                let title = `${periodLabel} 상세 내역`;
                let isProfit = true;

                if (type === 'realized') {
                    // ⭐️ 배열 순서를 바꿨으므로 인덱스 0이 배당수익금
                    if (datasetIndex === 0) {
                        breakdown = dataObj.dividend_breakdown;
                        title = `${periodLabel} 배당 수익 상세`;
                    } else {
                        breakdown = dataObj.realized_breakdown;
                        title = `${periodLabel} 매매 실현손익 상세`;
                    }
                } else if (type === 'evaluated') {
                    breakdown = dataObj.evaluated_breakdown;
                    title = `${periodLabel} 매수분 평가 손익 상세`;
                } else if (type === 'volume') {
                    isProfit = false;
                    if (datasetIndex === 0) { breakdown = dataObj.buy_volume_breakdown; title = `${periodLabel} 매수 금액 상세`; }
                    else { breakdown = dataObj.sell_volume_breakdown; title = `${periodLabel} 매도 금액 상세`; }
                } else if (type === 'cumulative') {
                    if (datasetIndex === 1) {
                        breakdown = dataObj.cumulative_div_breakdown;
                        title = `${periodLabel} 누적 배당 수익금 상세`;
                    } else {
                        breakdown = dataObj.cumulative_breakdown;
                        title = `${periodLabel} 총 누적 수익금 상세`;
                    }
                }
                
                // 계산된 내역을 하단 영역에 렌더링
                window.renderChartDetailList(title, breakdown, isProfit);
            }
        }
    });
};

// ⭐️ 개별 칩(Chip)용 필터 초기화 함수 세트
window.clearDateFilter = function() {
    currentFilterDate = null;
    displayEntries(true);
    window.scrollToFilterBox();
};

window.clearRecordTypeFilter = function() {
    currentFilterRecordType = 'all';
    const el = document.getElementById('filterRecordTypeSelect');
    if (el) { el.value = 'all'; window.updateDashboardFilterStyle(el); }
    window.saveFilterPreferences();
    displayEntries(true);
    window.scrollToFilterBox();
};
window.clearStockFilter = function() {
    currentFilterStock = 'all';
    const el = document.getElementById('filterStockSelect');
    if (el) { el.value = 'all'; window.updateDashboardFilterStyle(el); }
    window.saveFilterPreferences();
    displayEntries(true);
    window.scrollToFilterBox();
};
window.clearAccountFilter = function() {
    currentFilterAccount = 'all';
    currentDashboardAccount = 'all'; // ⭐️ 상단 필터 동기화
    const el = document.getElementById('filterAccountSelect');
    if (el) { el.value = 'all'; window.updateDashboardFilterStyle(el); }
    const topEl = document.getElementById('dashboardAccountFilter');
    if (topEl) { topEl.value = 'all'; window.updateDashboardFilterStyle(topEl); }
    window.saveFilterPreferences();
    updatePortfolioSummary(); // ⭐️ 대시보드 업데이트
    displayEntries(true);
    window.scrollToFilterBox();
};
window.clearBrokerFilter = function() {
    currentFilterBroker = 'all';
    currentDashboardBroker = 'all'; // ⭐️ 상단 필터 동기화
    const el = document.getElementById('filterBrokerSelect');
    if (el) { el.value = 'all'; window.updateDashboardFilterStyle(el); }
    const topEl = document.getElementById('dashboardBrokerFilter');
    if (topEl) { topEl.value = 'all'; window.updateDashboardFilterStyle(topEl); }
    window.saveFilterPreferences();
    updatePortfolioSummary(); // ⭐️ 대시보드 업데이트
    displayEntries(true);
    window.scrollToFilterBox();
};

window.clearSubAccountFilter = function() {
    currentFilterSubAccount = 'all';
    currentDashboardSubAccount = 'all'; // ⭐️ 상단 필터 동기화
    const el = document.getElementById('filterSubAccountSelect');
    if (el) { el.value = 'all'; window.updateDashboardFilterStyle(el); }
    const topEl = document.getElementById('dashboardSubAccountFilter');
    if (topEl) { topEl.value = 'all'; window.updateDashboardFilterStyle(topEl); }
    window.saveFilterPreferences();
    updatePortfolioSummary(); // ⭐️ 대시보드 업데이트
    displayEntries(true);
    window.scrollToFilterBox();
};

window.clearKeywordFilter = function(index) {
    if (typeof index === 'number' && index >= 0) {
        currentFilterKeywords.splice(index, 1);
    }
    displayEntries(true);
    window.scrollToFilterBox();
};

window.clearAllFilters = function(shouldRender = true) {
    currentFilterDate = null;
    currentFilterRecordType = 'all';
    currentFilterStock = 'all';
    currentFilterAccount = 'all';
    currentFilterBroker = 'all';
    currentFilterSubAccount = 'all';
    currentFilterKeywords = [];
    
    currentDashboardAccount = 'all'; // ⭐️ 상단 필터 동기화
    currentDashboardBroker = 'all'; // ⭐️ 상단 필터 동기화
    currentDashboardSubAccount = 'all'; // ⭐️ 상단 필터 동기화
    
    window.saveFilterPreferences();
    
    // ⭐️ 필터 UI 컨트롤(셀렉트 박스)도 명시적으로 모두 초기화
    const selects = ['filterRecordTypeSelect', 'filterStockSelect', 'filterAccountSelect', 'filterBrokerSelect', 'filterSubAccountSelect'];
    selects.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.value = 'all';
            window.updateDashboardFilterStyle(el);
        }
    });
    
    const topSelects = ['dashboardAccountFilter', 'dashboardBrokerFilter', 'dashboardSubAccountFilter'];
    topSelects.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.value = 'all';
            window.updateDashboardFilterStyle(el);
        }
    });
    
    if (filterStockInput) filterStockInput.value = '';
    const clearFilterBtn = document.getElementById('clearFilterBtn');
    if (clearFilterBtn) clearFilterBtn.style.display = 'none';
    
    if (shouldRender !== false) {
        updatePortfolioSummary(); // ⭐️ 대시보드 리렌더링
        displayEntries(true);
        window.scrollToFilterBox();
    }
};

window.clearChartStockFilter = function() {
    currentChartStock = 'all';
    const el = document.getElementById('chartStockFilter');
    if (el) { el.value = 'all'; window.updateDashboardFilterStyle(el); }
    window.saveChartFilterPreferences();
    window.renderMonthlyProfitChart();
};

window.clearChartAccountFilter = function() {
    currentChartAccount = 'all';
    const el = document.getElementById('chartAccountFilter');
    if (el) { el.value = 'all'; window.updateDashboardFilterStyle(el); }
    window.saveChartFilterPreferences();
    window.renderMonthlyProfitChart();
};

window.clearChartBrokerFilter = function() {
    currentChartBroker = 'all';
    const el = document.getElementById('chartBrokerFilter');
    if (el) { el.value = 'all'; window.updateDashboardFilterStyle(el); }
    window.saveChartFilterPreferences();
    window.renderMonthlyProfitChart();
};

window.clearChartSubAccountFilter = function() {
    currentChartSubAccount = 'all';
    const el = document.getElementById('chartSubAccountFilter');
    if (el) { el.value = 'all'; window.updateDashboardFilterStyle(el); }
    window.saveChartFilterPreferences();
    window.renderMonthlyProfitChart();
};

window.clearAllChartFilters = function() {
    currentChartStock = 'all';
    currentChartAccount = 'all';
    currentChartBroker = 'all';
    currentChartSubAccount = 'all';
    
    const selects = ['chartStockFilter', 'chartAccountFilter', 'chartBrokerFilter', 'chartSubAccountFilter'];
    selects.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.value = 'all';
            window.updateDashboardFilterStyle(el);
        }
    });
    
    window.saveChartFilterPreferences();
    window.renderMonthlyProfitChart();
};

document.getElementById('btnListView').addEventListener('click', function() {
    this.classList.add('active'); document.getElementById('btnCalendarView').classList.remove('active');
    const btnChartView = document.getElementById('btnChartView');
    if (btnChartView) btnChartView.classList.remove('active');
    document.getElementById('historyList').style.display = 'flex';
    document.getElementById('calendarViewSection').style.display = 'none';
    const monthlyChartSection = document.getElementById('monthlyChartSection');
    if (monthlyChartSection) monthlyChartSection.style.display = 'none';
    document.getElementById('filterBoxContainer').style.display = 'block';
    const chartFilterBoxContainer = document.getElementById('chartFilterBoxContainer');
    if (chartFilterBoxContainer) chartFilterBoxContainer.style.display = 'none';
    const btnToggleHistoryClosed = document.getElementById('btnToggleHistoryClosed');
    if (btnToggleHistoryClosed) btnToggleHistoryClosed.style.display = 'inline-block';
    
    displayEntries(true); // ⭐️ 리스트 뷰 전환 시 활성화된 필터에 맞춰 배너 다시 표시
});

document.getElementById('btnCalendarView').addEventListener('click', function() {
    this.classList.add('active'); document.getElementById('btnListView').classList.remove('active');
    const btnChartView = document.getElementById('btnChartView');
    if (btnChartView) btnChartView.classList.remove('active');
    document.getElementById('historyList').style.display = 'none';
    document.getElementById('calendarViewSection').style.display = 'block';
    const monthlyChartSection = document.getElementById('monthlyChartSection');
    if (monthlyChartSection) monthlyChartSection.style.display = 'none';
    document.getElementById('filterBoxContainer').style.display = 'none';
    const chartFilterBoxContainer = document.getElementById('chartFilterBoxContainer');
    if (chartFilterBoxContainer) chartFilterBoxContainer.style.display = 'none';
    const btnToggleHistoryClosed = document.getElementById('btnToggleHistoryClosed');
    if (btnToggleHistoryClosed) btnToggleHistoryClosed.style.display = 'none';
    
    const banner = document.getElementById('activeFilterBanner');
    if (banner) banner.style.display = 'none'; // ⭐️ 캘린더 뷰에서는 필터 배너 강제 숨김
    
    renderCalendar();
});

const btnChartViewEl = document.getElementById('btnChartView');
if (btnChartViewEl) {
    btnChartViewEl.addEventListener('click', function() {
        this.classList.add('active');
        document.getElementById('btnListView').classList.remove('active');
        document.getElementById('btnCalendarView').classList.remove('active');
        
        document.getElementById('historyList').style.display = 'none';
        document.getElementById('calendarViewSection').style.display = 'none';
        
        const monthlyChartSection = document.getElementById('monthlyChartSection');
        if (monthlyChartSection) monthlyChartSection.style.display = 'block';
        
        document.getElementById('filterBoxContainer').style.display = 'none';
        const chartFilterBoxContainer = document.getElementById('chartFilterBoxContainer');
        if (chartFilterBoxContainer) chartFilterBoxContainer.style.display = 'block';
        const btnToggleHistoryClosed = document.getElementById('btnToggleHistoryClosed');
        if (btnToggleHistoryClosed) btnToggleHistoryClosed.style.display = 'none';
        
        const banner = document.getElementById('activeFilterBanner');
        if (banner) banner.style.display = 'none';
        
        window.renderMonthlyProfitChart();
    });
}

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
                    'Content-Type': 'application/json'
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