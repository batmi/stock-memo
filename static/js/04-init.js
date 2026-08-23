// ═══════════════════════════════════════════════════════════════════
// 04-init.js — DOMContentLoaded 초기화 — 테마, 증권사 드롭다운, 헤더/모달 배선
//
// ⚠️ 이 파일들은 ES 모듈이 아니라 **순서대로 로드되는 클래식 스크립트**다.
//    최상위 let/const/function 은 전역 렉시컬 환경을 공유하므로, 예전 script.js
//    한 덩어리였을 때와 실행 의미가 완전히 같다. (HTML 의 인라인 onclick 핸들러가
//    전역 함수를 그대로 부르고 있어 모듈로 바꾸면 그것들이 전부 깨진다)
//    → 로드 순서는 templates/stock-memo.html 의 <script> 순서가 결정한다. 바꾸지 말 것.
// ═══════════════════════════════════════════════════════════════════


const mainApp = document.getElementById('mainApp');
// ⭐️ 계좌 등록 화면의 증권사 드롭다운을 BROKER_CHOICES 하나로 채운다.
//    HTML 에 <option> 을 하드코딩하면 JS 매핑과 어긋날 수 있다 (예전에 그랬다).
function populateBrokerSelect() {
    const select = document.getElementById('unifiedBrokerCode');
    if (!select) return;
    // 이미 채워져 있으면(재호출) 중복 추가하지 않는다
    if (select.querySelector('option[value="264"]')) return;
    const frag = document.createDocumentFragment();
    for (const { code, name } of BROKER_CHOICES) {
        const opt = document.createElement('option');
        opt.value = code;
        opt.dataset.name = name;
        opt.textContent = name;   // textContent 라 이름에 특수문자가 있어도 안전하다
        frag.appendChild(opt);
    }
    select.appendChild(frag);
}

window.addEventListener('DOMContentLoaded', () => {
    console.log("[App Init] DOMContentLoaded 이벤트 시작 - DOM 로드 완료");

    populateBrokerSelect();

    // ⭐️ 휴장일 목록을 먼저 받아둔다. (실패해도 평일 판정으로 동작하므로 await 하지 않는다)
    window.loadMarketCalendar();

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
                btnToggleNews.style.backgroundColor = isExpanded ? 'var(--primary-color)' : 'transparent';
                btnToggleNews.style.color = isExpanded ? '#fff' : 'var(--primary-color)';
            } else if (btnToggleNews) {
                btnToggleNews.style.display = 'inline-block';
                const isExpanded = document.getElementById('newsList')?.classList.contains('news-expanded');
                btnToggleNews.style.backgroundColor = isExpanded ? 'var(--primary-color)' : 'transparent';
                btnToggleNews.style.color = isExpanded ? '#fff' : 'var(--primary-color)';
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
                btnToggleNews.style.backgroundColor = 'transparent';
                btnToggleNews.style.color = 'var(--primary-color)';
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
                    btnToggleNews.style.backgroundColor = isExpanded ? 'var(--primary-color)' : 'transparent';
                    btnToggleNews.style.color = isExpanded ? '#fff' : 'var(--primary-color)';
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

    // 청산종목 보기 토글 버튼 이벤트 연결
    const btnToggleClosed = document.getElementById('btnToggleClosed');
    if (btnToggleClosed) {
        // 초기 버튼 상태 동기화 (두 버튼을 함께 갱신)
        syncClosedPositionsButtons();

        btnToggleClosed.addEventListener('click', () => {
            // ⭐️ 히스토리 버튼과 상태를 함께 전환하고 양쪽 화면을 모두 다시 그린다.
            setClosedPositionsVisible(!showClosedPositions);
            updatePortfolioSummary();
            displayEntries(true);
        });
    }

    // ⭐️ 금액 가리기(프라이버시) 토글 버튼 이벤트 연결
    //    화면공유·어깨너머 시선을 가리기 위한 '표시 전용' 기능이다. 현재가 조회와
    //    자동 갱신은 그대로 돌아가므로 껐다 켜는 데 드는 비용이 없고, 다시 켜면
    //    화면이 즉시 원래대로 돌아온다. (예전 '현재가 숨기기'는 조회 자체를 끊어
    //    평가금액·평가손익·분석 탭까지 함께 죽었고, 그래서 아무도 쓰지 않았다)
    const btnToggleAmountMask = document.getElementById('btnToggleAmountMask');
    if (btnToggleAmountMask) {
        syncAmountMaskButton();

        btnToggleAmountMask.addEventListener('click', () => {
            setAmountMasked(!isAmountMasked);
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

    // 히스토리 청산종목 숨김/보기 토글 버튼 이벤트 연결
    const btnToggleHistoryClosed = document.getElementById('btnToggleHistoryClosed');
    if (btnToggleHistoryClosed) {
        // 초기 버튼 상태 동기화 (두 버튼을 함께 갱신)
        syncClosedPositionsButtons();

        btnToggleHistoryClosed.addEventListener('click', () => {
            // ⭐️ 포트폴리오 버튼과 상태를 함께 전환하고 양쪽 화면을 모두 다시 그린다.
            setClosedPositionsVisible(!showHistoryClosedPositions);
            displayEntries(true);
            updatePortfolioSummary();

            // 필터 변경 시 히스토리 상단으로 부드럽게 스크롤
            window.scrollToFilterBox();
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
