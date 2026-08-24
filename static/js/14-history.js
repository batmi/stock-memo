// ═══════════════════════════════════════════════════════════════════
// 14-history.js — 히스토리 목록 렌더링·숨김 종목·기록 편집/삭제
//
// ⚠️ 이 파일들은 ES 모듈이 아니라 **순서대로 로드되는 클래식 스크립트**다.
//    최상위 let/const/function 은 전역 렉시컬 환경을 공유하므로, 예전 script.js
//    한 덩어리였을 때와 실행 의미가 완전히 같다. (HTML 의 인라인 onclick 핸들러가
//    전역 함수를 그대로 부르고 있어 모듈로 바꾸면 그것들이 전부 깨진다)
//    → 로드 순서는 templates/stock-memo.html 의 <script> 순서가 결정한다. 바꾸지 말 것.
// ═══════════════════════════════════════════════════════════════════

function recomputeHiddenStocks() {
    // ⭐️ 키는 종목명이 아니라 **종목 동일성**(코드 우선)이다. 이름으로 두면 봇이 보내는
    //    증권사 정식 명칭과 손으로 적은 이름이 갈릴 때 숨김이 저절로 풀린다 —
    //    새 이름의 기록이 '숨김 이력이 없는 별개 종목'으로 잡히기 때문이다.
    const latest = {}; // identity -> { ts, id, isHidden }
    const byName = {};  // 종목명 → 동일성 (이름으로 지정된 필터·레거시 메모를 옮길 때 쓴다)
    // ⭐️ 동일성 → 표시 이름. 표기가 갈린 같은 종목을 화면 어디서나 **한 이름**으로 부르기 위한 표다.
    //    규칙은 백엔드(stats.display_names)와 같다 — 가장 최근 체결의 이름이 이긴다.
    const nameOfIdentity = {}; // identity -> { name, ts, id }
    cloudEntries.forEach(entry => {
        const stockName = entry.stockName;
        if (!stockName || (entry.type || 'trade') !== 'trade') return;
        byName[stockName.trim()] = identityOf(entry);

        // 표시 이름은 **거래 시각** 기준으로 고른다(숨김 판정의 updatedAt 기준과 다르다 —
        //  이름은 '언제 산 종목이냐'의 문제이고, 숨김은 '언제 그렇게 정했냐'의 문제다).
        const identForName = identityOf(entry);
        const nameTs = entry.rawDate ? (new Date(entry.rawDate).getTime() || 0) : 0;
        const nameId = Number(entry.id) || 0;
        const prevName = nameOfIdentity[identForName];
        if (!prevName || nameTs > prevName.ts || (nameTs === prevName.ts && nameId > prevName.id)) {
            nameOfIdentity[identForName] = { name: stockName.trim(), ts: nameTs, id: nameId };
        }
        // ⭐️ 숨김은 사용자가 실거래 종목에 대해 정한 의도다. 봇이 밀어 넣는 모의투자 기록은
        //    항상 isHidden=0 이고 가장 최신이라, 포함시키면 숨김이 저절로 풀려버린다.
        if (isSimulatedEntry(entry)) return;

        const stamp = entry.updatedAt || entry.createdAt;
        const ts = stamp ? (new Date(stamp).getTime() || 0) : 0;
        const id = Number(entry.id) || 0;
        const ident = identityOf(entry);
        const prev = latest[ident];
        if (!prev || ts > prev.ts || (ts === prev.ts && id > prev.id)) {
            latest[ident] = { ts, id, isHidden: !!entry.isHidden };
        }
    });

    stockIdentityByName = byName;
    stockNameByIdentity = Object.fromEntries(
        Object.entries(nameOfIdentity).map(([ident, v]) => [ident, v.name]));
    hiddenStocks = new Set(Object.keys(latest).filter(k => latest[k].isHidden));
}

// ⭐️ 특정 종목이 현재 숨김 상태인지 조회 (입력 폼 프리필용)
//    폼은 이름만 알고 있으므로 이름 → 동일성으로 옮겨 조회한다.
function isStockHidden(stockName) {
    return !!stockName && hiddenStocks.has(identityForStockName(stockName));
}

// ⭐️ 입력 폼의 숨김 체크박스를 해당 종목의 현재 숨김 상태로 동기화한다.
//    이걸 빼먹으면 숨긴 종목에 새 기록을 하나 추가했을 뿐인데 숨김이 풀려버린다.
function syncHiddenCheckbox(stockName) {
    const el = document.getElementById('isHidden');
    if (!el) return;
    recomputeHiddenStocks(); // 폼을 여는 시점의 최신 상태 보장
    el.checked = isStockHidden(stockName);
}

function displayEntries(isFilterUpdate = false) {
    recomputeHiddenStocks();
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
    const stockQtys = {};                              // 청산 종목 필터용 보유 수량 (portfolioKey 기준)
    // ⭐️ 연관 종목 집합의 키는 이름이 아니라 **동일성(코드)** 이다. 이름으로 담으면 메모에
    //    적힌 표기와 체결의 표기가 갈릴 때 그 메모가 모아보기에서 빠진다.
    const relatedStocksForAccountFilter = new Set();    // 분류별 모아보기 연관 종목
    const relatedStocksForBrokerFilter = new Set();     // 증권사별 모아보기 연관 종목
    const relatedStocksForSubAccountFilter = new Set(); // 증권계좌별 모아보기 연관 종목
    const needAccount = currentFilterAccount !== 'all';
    const needBroker = currentFilterBroker !== 'all';
    const needSubAccount = currentFilterSubAccount !== 'all';

    cloudEntries.forEach(entry => {
        const entryType = entry.type || 'trade';
        if (entryType !== 'trade' || !entry.stockName) return;

        // ⭐️ 청산 판정 수량은 포트폴리오와 똑같이 (종목 + 모의/제외 여부)별로 나눠 쌓는다.
        //    한 칸에 합치면 모의·제외 계좌 물량이 실거래 잔량을 오염시키고,
        //    반대로 실거래만 세면 모의 전용 종목은 수량이 아예 안 잡혀 청산 판정에서 빠진다.
        if (entry.tradeType === '매수' || entry.tradeType === '매도') {
            const qtyKey = portfolioKeyFor(entry);   // 포트폴리오와 같은 기준(코드 우선)
            if (stockQtys[qtyKey] === undefined) stockQtys[qtyKey] = 0;
            if (entry.tradeType === '매수') stockQtys[qtyKey] += (Number(entry.quantity) || 0);
            else stockQtys[qtyKey] -= (Number(entry.quantity) || 0);
        }

        // ⭐️ 분류별/증권사별 모아보기 시 연관된 일반 메모를 함께 보여주기 위해 종목 동일성 추출
        const relatedIdentity = identityOf(entry);
        if (needAccount && entry.tradeClass === currentFilterAccount) relatedStocksForAccountFilter.add(relatedIdentity);
        if (needBroker && getMappedBroker(entry.brokerAccount) === currentFilterBroker) relatedStocksForBrokerFilter.add(relatedIdentity);
        if (needSubAccount && getMappedSubAccount(entry.subAccount, entry.accountName) === currentFilterSubAccount) relatedStocksForSubAccountFilter.add(relatedIdentity);
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

    // ⭐️ 이름으로 지정된 종목 필터를 동일성으로 옮겨 둔다(루프 밖에서 한 번만).
    //    카드 클릭·드롭다운은 이름을 넘기는데, 표기가 갈린 같은 종목의 기록이 빠지면
    //    '카드는 합쳐졌는데 목록은 반만 나오는' 상태가 된다.
    const filterStockIdentity = currentFilterStock === 'all'
        ? null : identityForStockName(currentFilterStock);
    //  메모는 종목코드 없이 이름만 달고 있을 수 있다(레거시). 이름표로 동일성을 찾아 준다.
    const identityOfEntry = (entry) => ((entry.type || 'trade') === 'trade'
        ? identityOf(entry) : identityForStockName(entry.stockName));

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
            if (identityOfEntry(entry) !== filterStockIdentity) return false;
        }
        if (currentFilterAccount !== 'all') {
            const entryType = entry.type || 'trade';
            const isMatchTrade = entryType === 'trade' && (entry.tradeClass || '') === currentFilterAccount;
            const isMatchMemo = entryType === 'memo' && relatedStocksForAccountFilter.has(identityForStockName(entry.stockName));
            if (!isMatchTrade && !isMatchMemo) return false;
        }
        if (currentFilterBroker !== 'all') {
            const entryType = entry.type || 'trade';
            const isMatchTrade = entryType === 'trade' && getMappedBroker(entry.brokerAccount) === currentFilterBroker;
            const isMatchMemo = entryType === 'memo' && relatedStocksForBrokerFilter.has(identityForStockName(entry.stockName));
            if (!isMatchTrade && !isMatchMemo) return false;
        }
        if (currentFilterSubAccount !== 'all') {
            const entryType = entry.type || 'trade';
            const isMatchTrade = entryType === 'trade' && getMappedSubAccount(entry.subAccount, entry.accountName) === currentFilterSubAccount;
            const isMatchMemo = entryType === 'memo' && relatedStocksForSubAccountFilter.has(identityForStockName(entry.stockName));
            if (!isMatchTrade && !isMatchMemo) return false;
        }
        
        // ⭐️ 청산종목 숨김 상태일 때 (보유 수량이 0인 종목과 숨김 종목을 검색 및 필터에서 제외)
        if (!showHistoryClosedPositions && entry.stockName) {
            if (hiddenStocks.has(identityOfEntry(entry))) return false;

            // 매매 기록은 자기 칸(실거래/모의·제외)의 잔량으로만 판정한다.
            // 일반 메모는 어느 칸에 속하는지 알 수 없으므로, 그 종목의 칸이 모두 청산됐을 때만 숨긴다.
            const entryType = entry.type || 'trade';
            const memoIdentity = identityForStockName(entry.stockName);
            const qtyKeys = entryType === 'trade'
                ? [portfolioKeyFor(entry)]
                : [memoIdentity, portfolioKey(memoIdentity, true)];
            const knownQtys = qtyKeys
                .map(k => stockQtys[k])
                .filter(q => q !== undefined);
            if (knownQtys.length > 0 && knownQtys.every(q => q <= 0)) {
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
                    const actualBroker = BROKER_NAMES[entry.brokerAccount] || entry.brokerAccount;
            const displayBroker = highlight(actualBroker);
            let actualSub = entry.accountName || entry.subAccount;
            if (entry.subAccount && !entry.accountName) {
                const accInfo = findAccountMapping(entry.subAccount);
                if (accInfo && accInfo.alias) actualSub = accInfo.alias;
            }
            const displaySubAccount = highlight(actualSub);
            const brokerBadge = entry.brokerAccount ? `<span style="font-size: 0.85em; color: var(--text-muted-color); font-weight: normal; margin:0;">🏦 ${displayBroker}${actualSub ? ` - ${displaySubAccount}` : ''}</span>` : '';
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
            const cPre = entry.currency === 'USD' ? '$' : '';
            const cSuf = entry.currency === 'USD' ? '' : ''; // 원화는 원래 생략되어 있었음
            
            if (entry.tradeType === '배당' && (entry.price > 0 || entry.quantity > 0)) {
                const totalAmount = (entry.price * (entry.quantity || 1)).toLocaleString();
                detailsHtml = `
                    <div class="entry-details">
                        <div class="detail-item">배당금: <span class="masked-amount">${cPre}${totalAmount}${cSuf}</span></div>
                    </div>
                `;
            } else if (entry.tradeType !== '관망' && entry.tradeType !== '주시' && (entry.price > 0 || entry.quantity > 0)) {
                const priceStr = entry.price ? entry.price.toLocaleString() : '0';
                const qtyStr = entry.quantity ? entry.quantity.toLocaleString() : '0';
                const totalAmount = (entry.price * entry.quantity).toLocaleString();
                detailsHtml = `
                    <div class="entry-details">
                        <div class="detail-item">단가: <span>${cPre}${priceStr}${cSuf}</span></div>
                        <div class="detail-item">수량: <span class="masked-amount">${qtyStr}주</span></div>
                        <div class="detail-item">총액: <span class="masked-amount">${cPre}${totalAmount}${cSuf}</span></div>
                    </div>
                `;
            }
            const tradeBadge = `<span style="background-color: ${typeColor}; color: white; padding:4px 8px; border-radius:12px; font-size:0.85em; font-weight:bold; margin:0;">${entry.tradeType}</span>`;
            // ⭐️ 모의투자·제외 계좌 체결은 기록으로는 남기되, 합계·통계에 안 잡힌다는 걸 목록에서도 알 수 있게 한다.
            const simBadge = isExcludedFromTotals(entry)
                ? `<span style="background-color: var(--warning-color); color: white; padding:4px 8px; border-radius:12px; font-size:0.85em; font-weight:bold; margin:0;" title="총 투자금액·평가금액·실현손익·도넛 차트·통계에는 반영되지 않는 기록입니다.">${exclusionBadgeLabel(entry)}</span>`
                : '';
                    const actualBroker = BROKER_NAMES[entry.brokerAccount] || entry.brokerAccount;
            const displayBroker = highlight(actualBroker);
            let actualSub = entry.accountName || entry.subAccount;
            if (entry.subAccount && !entry.accountName) {
                const accInfo = findAccountMapping(entry.subAccount);
                if (accInfo && accInfo.alias) actualSub = accInfo.alias;
            }
            const displaySubAccount = highlight(actualSub);
            const brokerBadge = entry.brokerAccount ? `<span style="font-size: 0.85em; color: var(--text-muted-color); font-weight: normal; margin:0;">🏦 ${displayBroker}${actualSub ? ` - ${displaySubAccount}` : ''}</span>` : '';
            const displayThoughts = highlight(entry.thoughts, true);
            card.innerHTML = `
            <div class="entry-header">
                ${timeDisplayHtml}
                <div class="header-right"><span>💼 ${entry.tradeClass}</span><button class="btn-edit">수정</button><button class="btn-delete">삭제</button></div>
            </div>
                <div class="entry-title" style="display: flex; align-items: center; flex-wrap: wrap; gap: 8px;">${stockBadge}${tradeBadge}${simBadge}${brokerBadge}</div>
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

window.editEntry = async function(entry) {
    if (entry.type === 'trade') {
        const canOpen = await ensureAccountMapping();
        if (!canOpen) return;
    }
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
    document.getElementById('tradeClass').value = entry.tradeClass || '';
    document.getElementById('accountName').value = entry.accountName || '';

    if (entry.type === 'trade') {
        syncJournalAccountSelect(entry.subAccount);
    }

    // ⭐️ 숨김 체크박스는 이 기록 자신의 값이 아니라 '해당 종목의 현재 숨김 상태'로 채운다.
    //    (숨김은 기록 단위가 아닌 종목 단위 속성이므로, 무관한 수정이 상태를 뒤집지 않게 한다)
    syncHiddenCheckbox(entry.stockName);
    
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
        document.getElementById('tradeClass').value = entry.tradeClass || '';
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

