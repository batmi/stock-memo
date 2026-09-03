// ═══════════════════════════════════════════════════════════════════
// 13-portfolio.js — 포트폴리오 카드·대시보드 요약·필터 드롭다운
//
// ⚠️ 이 파일들은 ES 모듈이 아니라 **순서대로 로드되는 클래식 스크립트**다.
//    최상위 let/const/function 은 전역 렉시컬 환경을 공유하므로, 예전 script.js
//    한 덩어리였을 때와 실행 의미가 완전히 같다. (HTML 의 인라인 onclick 핸들러가
//    전역 함수를 그대로 부르고 있어 모듈로 바꾸면 그것들이 전부 깨진다)
//    → 로드 순서는 templates/stock-memo.html 의 <script> 순서가 결정한다. 바꾸지 말 것.
// ═══════════════════════════════════════════════════════════════════

function updatePortfolioSummary() {
    // ⭐️ displayEntries 를 거치지 않고 직접 호출되는 경로(필터 변경 등)가 많으므로 여기서도 재계산
    recomputeHiddenStocks();

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
        if (currentDashboardBroker !== 'all' && getMappedBroker(entry.brokerAccount) !== currentDashboardBroker) return;
        
        // ⭐️ 대시보드 증권계좌 필터 적용
        if (currentDashboardSubAccount !== 'all' && getMappedSubAccount(entry.subAccount, entry.accountName) !== currentDashboardSubAccount) return;
        
        // ⭐️ 대시보드 투자 분류 필터 적용
        if (currentDashboardAccount !== 'all' && (entry.tradeClass || '') !== currentDashboardAccount) return;

        // 표시 이름은 표기가 갈린 같은 종목을 **한 이름**으로 부르기 위한 공용 규칙을 따른다
        //  (가장 최근 체결의 이름 — 드롭다운·달력·백엔드 stats.display_names 와 같다).
        const stock = displayNameForEntry(entry);
        const qty = Number(entry.quantity) || 0;
        const price = Number(entry.price) || 0;

        // ⭐️ '금액 계산 제외' 계좌는 실거래와 다른 칸에 쌓는다. 같은 종목이라도 합치면
        //    제외 대상 매수가 실거래 평균단가·실현손익을 오염시킨다.
        const isExcluded = isExcludedFromTotals(entry);
        const excludeLabel = isExcluded ? exclusionBadgeLabel(entry) : '';

        // ⭐️ 칸은 **종목코드**로 가른다(identityOf). 이름으로 가르면 표기가 갈린 같은 종목이
        //    카드 두 장으로 쪼개진다 — 봇이 보내는 증권사 정식 명칭과 손으로 적어 둔 이름이
        //    다를 때가 그렇다(2026-08-24: 'KODEX 삼성그룹' vs 'KODEX 삼성그룹 ETF', 코드는 둘 다 102780).
        const identity = identityOf(entry);
        const key = portfolioKeyFor(entry);

        if (!portfolio[key]) portfolio[key] = { stock, identity, isExcluded, excludeLabel, qty: 0, totalCost: 0, avgPrice: 0, realizedProfit: 0, realizedCost: 0, accountName: '', tradeClass: '', traded: false, stockCode: '' };
        if (stock) portfolio[key].stock = stock;
        if (entry.tradeClass) portfolio[key].tradeClass = entry.tradeClass; // 가장 최근 거래의 투자 분류 기록
        if (entry.stockCode) portfolio[key].stockCode = entry.stockCode; // 종목코드 기록

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
            portfolio[key].traded = true;
            // ⭐️ 월간 매매 건수는 실거래(합계 반영 대상)만 센다.
            if (isCurrentMonth && !isExcluded) {
                if (tt === '매수') monthlyBuyCount++;
                else if (tt === '매도') monthlySellCount++;
            }
            // ⭐️ 공용 계산 엔진(calc.js) — 평균단가/실현손익 단일 소스
            const r = applyTradeToHolding(portfolio[key], qty, price, tt);
            portfolio[key].realizedProfit += r.realized + r.dividend;
            portfolio[key].realizedCost += r.cost;
            // ⭐️ 카드에 표시할 종목별 손익은 모의도 계산하되, 누적 실현 손익 합계에는 넣지 않는다.
            if (!isExcluded) totalRealizedProfit += r.realized + r.dividend;
        }
    });

    const portfolioGrid = document.getElementById('portfolioGrid');
    portfolioGrid.innerHTML = '';
    const gridFragment = document.createDocumentFragment();
    let hasHoldings = false;
    currentHoldings = [];

    // ⭐️ 포트폴리오를 배열로 변환하고 투자 분류에 따라 정렬
    const portfolioArray = [];
    for (const key in portfolio) {
        const p = portfolio[key];
        const stock = p.stock;
        // ⭐️ 소수점 거래의 부동소수점 잔여(5e-17 등)를 청산으로 인정한다. calc.js 의
        //    applyTradeToHolding 과 같은 기준을 써야 '청산했는데 카드가 남는' 일이 없다.
        const isClosed = window.isFlatQty ? window.isFlatQty(p.qty) : (p.qty <= 1e-9);
        // ⭐️ 보유 중인 숨김 종목은 카드 노출과 무관하게 배열에 항상 담는다.
        //    총 투자금액·총 평가금액·누적 실현 손익은 숨김 여부와 관계없이 계속 반영해야 하고,
        //    현재가 조회 대상(currentPortfolioArrayForPrice)에도 포함돼야 하기 때문이다.
        //    실제 카드 노출 여부는 아래 렌더 루프에서 걸러낸다.
        const isHiddenStock = hiddenStocks.has(p.identity || stock);
        if (!isClosed) {
            portfolioArray.push({ key, ...p, isClosed: false, isHiddenStock });
        } else if (showClosedPositions && (p.traded || isHiddenStock)) {
            portfolioArray.push({ key, ...p, isClosed: true, isHiddenStock }); // 청산 종목 포함
        }
    }

    currentPortfolioArrayForPrice = portfolioArray;
    const sortOrder = { "장기투자": 1, "중기투자": 2, "단기스윙": 3, "단타(스캘핑)": 4, "배당투자": 5, "공모주": 6, "시스템": 7, "기타": 8 };
    portfolioArray.sort((a, b) => {
        // ⭐️ 청산·숨김 종목과 제외 계좌 종목을 가장 하단으로 정렬
        //    (실제 돈이 들어간 종목이 위쪽에 모여 있어야 한눈에 읽힌다)
        const aBottom = a.isClosed || a.isHiddenStock || a.isExcluded;
        const bBottom = b.isClosed || b.isHiddenStock || b.isExcluded;
        if (aBottom !== bBottom) {
            return aBottom ? 1 : -1;
        }

        // ⭐️ 사용자가 드래그 앤 드롭으로 설정한 커스텀 순서가 있다면 최우선 적용
        if (userPreferences.portfolioOrder) {
            const idxA = userPreferences.portfolioOrder.indexOf(a.key);
            const idxB = userPreferences.portfolioOrder.indexOf(b.key);
            
            if (idxA !== -1 && idxB !== -1) return idxA - idxB;
            if (idxA !== -1) return -1;
            if (idxB !== -1) return 1;
        }
        
        const orderA = sortOrder[a.tradeClass] || 99;
        const orderB = sortOrder[b.tradeClass] || 99;
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
        "시스템": "시스템",
        "기타": "기타"
    };

    const badgeClassMap = {
        "장기투자": "badge-long",
        "중기투자": "badge-mid",
        "단기스윙": "badge-swing",
        "단타(스캘핑)": "badge-scalp",
        "배당투자": "badge-dividend",
        "공모주": "badge-ipo",
        "시스템": "badge-system",
        "기타": "badge-etc"
    };

    portfolioArray.forEach(data => {
        const stock = data.stock;
        const isClosed = data.isClosed;
        const isExcluded = !!data.isExcluded;

        // ⭐️ 숨김 종목이라도 보유 중이면 총 투자금액·보유 종목 수에 그대로 반영한다.
        //    (종목만 가리는 것이지 수치를 빼는 것이 아니다. 총 평가금액은 현재가 조회 쪽에서
        //     currentPortfolioArrayForPrice 를 그대로 합산하므로 함께 반영되고,
        //     누적 실현 손익은 위쪽 기록 순회에서 이미 전 종목을 집계한다.)
        // ⭐️ 단, '금액 계산 제외' 계좌는 어떤 합계에도 넣지 않는다. 카드만 보여준다.
        //    (판정 근거는 계좌 설정 하나뿐이다 — isSimulated 는 표시용 컬럼일 뿐
        //     합계를 가르지 않는다. isExcludedFromTotals 가 단독 소유한다.)
        if (!isClosed && !isExcluded) {
            totalInvestedAmount += data.totalCost;
            holdingsCount++;
            hasHoldings = true;
        }

        // ⭐️ 숨김 종목은 '청산종목 보기'가 꺼져 있으면 종목명이 드러나는 표현
        //    (카드·도넛 차트·뉴스)에서 제외한다. 수치 반영은 위에서 이미 끝났다.
        const hideThisStock = data.isHiddenStock && !showClosedPositions;

        // ⭐️ 도넛 차트는 합계에 잡히는 물량만 그린다 — 제외 계좌가 섞이면 비중이 왜곡된다.
        if (!isClosed && !hideThisStock && !isExcluded) {
            currentHoldings.push(stock);
            chartLabels.push(stock);
            chartData.push(data.totalCost);
        }

        if (hideThisStock) return;

        const shortAccountName = data.tradeClass ? (shortAccountNameMap[data.tradeClass] || data.tradeClass.substring(0, 2)) : '';
        const badgeClass = badgeClassMap[data.tradeClass] || 'badge-etc';
        const cardBorderClass = badgeClass.replace('badge-', 'card-border-');
        
        const card = document.createElement('div');
        card.className = `portfolio-card ${cardBorderClass}`;
        card.setAttribute('data-id', data.key); // ⭐️ 드래그 앤 드롭 정렬을 위한 식별자 (모의는 실거래와 별개 카드)
        // ⭐️ 흐리게 처리는 청산 종목에만 적용한다. 숨김 종목은 노출 규칙만 청산과 같을 뿐
        //    실제로는 보유 중일 수 있으므로 일반 종목과 동일한 농도로 보여준다.
        if (isClosed) {
            card.style.opacity = '0.6'; // 청산 종목은 반투명하게 표시
            card.style.borderLeftColor = 'var(--text-muted-color)';
        }
        // ⭐️ 합계에 잡히지 않는 카드(제외 계좌)는 그 사실이 한눈에 보여야 오해가 없다.
        //    다만 테두리는 실거래 카드와 동일한 실선·분류색을 그대로 쓴다. 점선/주황 고정색은
        //    '시스템' 같은 분류색을 가려 버려서, 구분은 아래 배지와 농도로만 준다.
        if (isExcluded) {
            card.style.opacity = isClosed ? '0.5' : '0.75';
        }
        const closedBadge = isClosed ? `<span style="font-size: 10px; background: var(--border-color); color: var(--card-bg-color); padding: 1px 4px; border-radius: 3px;">청산완료</span>` : '';
        const hiddenBadge = data.isHiddenStock ? `<span style="font-size: 10px; background: var(--text-muted-color); color: var(--card-bg-color); padding: 1px 4px; border-radius: 3px; margin-left: 2px;">숨김</span>` : '';
        const simBadge = isExcluded ? `<span style="font-size: 10px; background: var(--warning-color); color: #fff; padding: 1px 4px; border-radius: 3px; margin-left: 2px;" title="총 투자금액·평가금액·실현손익·도넛 차트·통계에는 반영되지 않는 기록입니다.">${data.excludeLabel || '모의'}</span>` : '';
        const statusBadge = `${closedBadge}${hiddenBadge}${simBadge}`;
        const accountBadgeHtml = shortAccountName ? `<span class="account-badge ${badgeClass}">${shortAccountName}</span>` : '';
        card.innerHTML = `
            <div class="stock-name" style="margin-bottom: 2px;">${stock}</div>
            <div style="margin-bottom: 8px; display: flex; align-items: center; min-height: 16px;">${accountBadgeHtml}${statusBadge}</div>
            <div class="stat-row"><span>보유 수량</span><span class="masked-amount">${data.qty.toLocaleString()}주</span></div>
            <div class="stat-row"><span>평균 단가</span><span>${Math.round(data.avgPrice).toLocaleString()}</span></div>
            <div class="stat-row"><span>총 매수금액</span><span class="masked-amount">${Math.round(data.totalCost).toLocaleString()}</span></div>
        `;
        
        if (data.realizedProfit !== 0) {
            const profitColor = data.realizedProfit > 0 ? 'var(--danger-color)' : 'var(--primary-color)';
            const profitStr = (data.realizedProfit > 0 ? '+' : '') + Math.round(data.realizedProfit).toLocaleString();
            card.innerHTML += `
                <div class="stat-row" style="margin-top: 8px; padding-top: 8px; border-top: 1px dashed var(--border-color);">
                    <span>종목 실현손익</span><span class="masked-amount" style="color:${profitColor}">${profitStr}</span>
                </div>`;
        }

        // ⭐️ 현재 보유 중인 종목만 현재가 영역 추가
        //    data-pkey 는 카드 단위 식별자다. 같은 종목을 실거래·모의로 함께 들고 있으면
        //    data-code 만으로는 두 카드가 구분되지 않아 서로의 평가금액을 덮어쓴다.
        if (!isClosed) {
            const pkey = escapeAttr(data.key);
            const code = escapeAttr(data.stockCode || '');
            card.innerHTML += `
                <div class="current-price-section" style="margin-top: 8px; padding-top: 8px; border-top: 1px dashed var(--border-color);" title="클릭하여 현재가 갱신">
                    <div class="stat-row" style="align-items: center;"><span>현재가</span><span class="cp-price" data-pkey="${pkey}" data-code="${code}">조회 중...</span></div>
                    <div class="stat-row" style="align-items: center;"><span>평가금액</span><span class="cp-eval masked-amount" data-pkey="${pkey}" data-code="${code}">-</span></div>
                    <div class="stat-row" style="align-items: center;"><span>평가손익</span><span class="cp-profit" data-pkey="${pkey}" data-code="${code}">-</span></div>
                </div>
            `;
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
            
            // ⭐️ 사용자 설정에 상태 저장 (두 키는 항상 같은 값으로 유지)
            userPreferences.showClosedPositions = showClosedPositions;
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

        // ⭐️ 도넛 중앙의 총 평가금액은 실거래 합계이므로 실거래가 없으면 감춘다.
        if (!isPortfolioEmpty) {
            document.getElementById('centerTotalEvaluationContainer').style.display = 'block';
        } else {
            document.getElementById('centerTotalEvaluationContainer').style.display = 'none';
        }

        // ⭐️ 현재가 조회는 위 도넛 표시 여부와 분리한다.
        //    isPortfolioEmpty 는 실거래 투자금액(totalInvestedAmount) 기준인데, 제외 계좌는
        //    합계에 잡히지 않으므로 '모의투자계좌'로 필터하면 보유 종목이 있어도 참이 된다.
        //    여기에 묶어 두면 그 화면의 카드가 영영 '조회 중...' 에서 멈춘다.
        window.fetchCurrentPricesAndUpdateUI();

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
                                    // ⭐️ 툴팁은 canvas 안이라 CSS 로 가릴 수 없다. 금액 가리기 모드에서는 비중만 남긴다.
                                    if (isAmountMasked) return `비중: ${percentage}`;
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

// ⭐️ 종목 드롭다운에 세울 이름 목록 — **동일성(코드) 하나당 한 줄**, 이름은 가장 최근 기록의 것.
//    (2026-08-24: 봇이 보낸 'KODEX 삼성그룹'과 손으로 적은 'KODEX 삼성그룹 ETF'가 코드는 같은데
//     두 줄로 섰다.) 표시 이름 규칙은 카드·백엔드(stats.display_names)와 같다.
function stockFilterOptions() {
    const latest = {}; // identity -> { name, ts, id }
    cloudEntries.forEach(entry => {
        const name = (entry.stockName || '').trim();
        if (!name) return;
        // 메모는 종목코드를 안 갖는 경우가 많아 이름 → 동일성 표를 거쳐 묶는다.
        const ident = (entry.type || 'trade') === 'trade' ? identityOf(entry) : identityForStockName(name);
        if (!ident) return;
        const ts = entry.rawDate ? (new Date(entry.rawDate).getTime() || 0) : 0;
        const id = Number(entry.id) || 0;
        const prev = latest[ident];
        if (!prev || ts > prev.ts || (ts === prev.ts && id > prev.id)) latest[ident] = { name, ts, id };
    });
    return Object.values(latest).map(v => v.name).sort();
}

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

    // ⭐️ 종목 목록은 이름이 아니라 **동일성(코드)** 하나당 한 줄이다. 이름으로 모으면 표기가
    //    갈린 같은 종목이 두 줄로 서는데, 필터는 코드로 대조하므로 어느 줄을 골라도 결과가
    //    같다 — 목록만 어지럽고 사용자는 둘 중 뭐가 다른지 알 수 없다.
    const stocks = stockFilterOptions();
    if (stockSelect) {
        let html = '<option value="all">종목별</option>';
        stocks.forEach(stock => {
            html += `<option value="${stock.replace(/"/g, '&quot;')}">${stock}</option>`;
        });
        stockSelect.innerHTML = html;
        // 저장해 둔 필터 값이 옛 표기여도 같은 종목의 현재 이름으로 옮겨 준다(값이 살아남는다).
        const resolvedStock = resolveStockOptionValue(stocks, currentFilterStock);
        if (resolvedStock) {
            stockSelect.value = resolvedStock;
            currentFilterStock = resolvedStock;
        } else {
            stockSelect.value = 'all';
            currentFilterStock = 'all';
        }
        window.updateDashboardFilterStyle(stockSelect);
    }

    const accountSortOrder = { "장기투자": 1, "중기투자": 2, "단기스윙": 3, "단타(스캘핑)": 4, "배당투자": 5, "공모주": 6, "시스템": 7, "기타": 8 };
    const accounts = [...new Set(cloudEntries.map(e => e.tradeClass).filter(Boolean))].sort((a, b) => {
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
    
    const brokers = [...new Set(cloudEntries.map(e => getMappedBroker(e.brokerAccount)).filter(Boolean))].sort();
    if (brokerSelect) {
        let html = '<option value="all">증권사별</option>';
        brokers.forEach(broker => {
            const displayBroker = broker;
            html += `<option value="${broker.replace(/"/g, '&quot;')}">${displayBroker}</option>`;
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

    const subAccounts = [...new Set(cloudEntries.map(e => getMappedSubAccount(e.subAccount, e.accountName)).filter(Boolean))].sort();
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
                const displayBroker = broker;
                brokerHtml += `<option value="${broker.replace(/"/g, '&quot;')}">${displayBroker}</option>`;
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
        const resolvedChartStock = resolveStockOptionValue(stocks, currentStockVal);
        if (resolvedChartStock) {
            chartStockFilter.value = resolvedChartStock;
            currentChartStock = resolvedChartStock;
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
                const displayBroker = broker;
                brokerHtml += `<option value="${broker.replace(/"/g, '&quot;')}">${displayBroker}</option>`;
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
        // ⭐️ 금액 계산에서 빠지는 기록(제외 계좌)만 들어있는 계좌는 차트가 항상 비므로
        //    아예 선택지에서 뺀다. (대시보드 카드 필터는 카드 확인용이라 그대로 유지)
        const chartSubAccounts = subAccounts.filter(sa => cloudEntries.some(e =>
            getMappedSubAccount(e.subAccount, e.accountName) === sa && !isExcludedFromTotals(e)
        ));
        if (chartSubAccounts.length > 0) {
            chartSubAccounts.forEach(sa => {
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

// ⭐️ 종목별 '가장 최근에 기록된 의도'의 isHidden 플래그로 숨김 종목 집합(hiddenStocks)을 재계산한다.
//    판정 기준은 updatedAt → createdAt → id 순서.
//    - rawDate(기록 일시)는 사용자가 과거 날짜로 지정할 수 있어 숨김 의도와 어긋나므로 쓰지 않는다.
//    - updatedAt 을 최우선으로 두어야 오래된 기록을 수정해 숨김을 걸/풀 때도 즉시 반영된다.
//      (수정 시 폼은 '종목의 현재 숨김 상태'로 프리필되므로 무관한 수정이 상태를 뒤집지 않는다.)
