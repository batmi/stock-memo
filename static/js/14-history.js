// ═══════════════════════════════════════════════════════════════════
// 14-history.js — 히스토리 목록 렌더링·숨김 종목·기록 편집/삭제
//
// ⚠️ 이 파일들은 ES 모듈이 아니라 **순서대로 로드되는 클래식 스크립트**다.
//    최상위 let/const/function 은 전역 렉시컬 환경을 공유하므로, 예전 script.js
//    한 덩어리였을 때와 실행 의미가 완전히 같다. (HTML 의 인라인 onclick 핸들러가
//    전역 함수를 그대로 부르고 있어 모듈로 바꾸면 그것들이 전부 깨진다)
//    → 실행 순서는 파일명 번호 순서다(app/utils/scriptbundle.py 가 그 순서로 이어 붙인다).
// ═══════════════════════════════════════════════════════════════════

function recomputeHiddenStocks() {
    if (!journalMeta) return;
    stockIdentityByName = journalMeta.stockIdentityByName;
    stockNameByIdentity = journalMeta.stockNameByIdentity;
    hiddenStocks = new Set(journalMeta.hiddenStocks);
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

async function displayEntries(isFilterUpdate = false, refreshed = false) {
    if (!journalMeta) return;
    if (!isFilterUpdate) updateFilterDropdown();
    const otherViews = isFilterUpdate ? [] : [updatePortfolioSummary(), renderCalendar()];
    const pendingKeyword = filterStockInput.value.trim();
    if (pendingKeyword && !currentFilterKeywords.includes(pendingKeyword)) currentFilterKeywords.push(pendingKeyword);
    filterStockInput.value = '';
    if (clearFilterBtn) clearFilterBtn.style.display = 'none';
    historyQuery = {
        ...journalFilters('history'), date: currentFilterDate || '', type: currentFilterRecordType,
        keywords: [...currentFilterKeywords], review: currentFilterNeedsReview ? '1' : '0',
        showClosed: showHistoryClosedPositions ? '1' : '0', limit: entriesPerPage,
    };
    historyCursor = null;
    historyLoading = false;
    const ticket = journalRequests.history = (journalRequests.history || 0) + 1;
    const generation = journalGeneration;
    try {
        const data = await journalJSON(journalUrl('entries', historyQuery), false);
        if (ticket !== journalRequests.history || generation !== journalGeneration) return;
        if (!refreshed && data.version !== journalMeta.version) {
            await refreshEntriesFromServer();
            return displayEntries(false, true);
        }
        document.getElementById('historyList-load-error')?.remove();
        historyCursor = data.nextCursor;
        cloudEntries = data.items;
        renderHistoryResult(data);
    } catch (error) {
        if (ticket !== journalRequests.history || generation !== journalGeneration) return;
        if (error.status === 409 && !refreshed) {
            try {
                await refreshEntriesFromServer();
                return displayEntries(false, true);
            } catch (refreshError) { error = refreshError; }
        }
        journalError('historyList', error, () => displayEntries(true));
    }
    await Promise.all(otherViews);
}

async function loadNextHistoryPage() {
    if (historyLoading || !historyCursor) return;
    historyLoading = true;
    const ticket = journalRequests.history, generation = journalGeneration;
    try {
        const data = await journalJSON(journalUrl('entries', { ...historyQuery, cursor: historyCursor }), false);
        if (ticket !== journalRequests.history || generation !== journalGeneration) return;
        document.getElementById('historyList-load-error')?.remove();
        historyCursor = data.nextCursor;
        cloudEntries.push(...data.items);
        renderPage(data.items);
    } catch (error) {
        if (ticket !== journalRequests.history || generation !== journalGeneration) return;
        if (error.status === 409) {
            try {
                await refreshEntriesFromServer();
                await displayEntries(false, true);
            } catch (refreshError) {
                journalError('historyList', refreshError, loadNextHistoryPage);
            }
        } else {
            journalError('historyList', error, loadNextHistoryPage);
        }
    } finally {
        if (ticket === journalRequests.history) historyLoading = false;
    }
}

function renderHistoryResult(data) {
    recomputeHiddenStocks();
    const filteredEntries = data.items;
    const prevHeight = historyList.offsetHeight;
    if (prevHeight > 0) historyList.style.minHeight = prevHeight + 'px';
    loadMoreObserver.disconnect();
    historyList.innerHTML = '';
    const banner = document.getElementById('activeFilterBanner');
    const filterBoxContainer = document.getElementById('filterBoxContainer');
    
    const hasDate = currentFilterDate !== null;
    const hasRecordType = currentFilterRecordType !== 'all';
    const hasStock = currentFilterStock !== 'all';
    const hasAccount = currentFilterAccount !== 'all';
    const hasBroker = currentFilterBroker !== 'all';
    const hasSubAccount = currentFilterSubAccount !== 'all';
    const hasKeyword = currentFilterKeywords.length > 0;
    const hasReview = currentFilterNeedsReview;

    renderReviewNotice();

    const isListView = document.getElementById('btnListView') && document.getElementById('btnListView').classList.contains('active');

    if (isListView && (hasDate || hasRecordType || hasStock || hasAccount || hasBroker || hasSubAccount || hasKeyword || hasReview)) {
        banner.style.display = 'flex';
        if (filterBoxContainer) filterBoxContainer.classList.add('filter-active');
        
        let chipsHtml = '';
        let activeFilterCount = 0;
        
        if (hasReview) {
            chipsHtml += `<span class="filter-chip">⚠️ 검토 필요만 <span class="chip-close" onclick="clearReviewFilter()">&times;</span></span>`;
            activeFilterCount++;
        }
        if (hasDate) {
            chipsHtml += `<span class="filter-chip">📅 ${currentFilterDate}${displayCurrency === 'KRW' ? '' : ` · ${escapeHtml(displayCurrency)}`} <span class="chip-close" onclick="clearDateFilter()">&times;</span></span>`;
            activeFilterCount++;
        }
        if (hasRecordType) {
            const typeText = currentFilterRecordType === 'trade' ? '매매 기록' : '일반 메모';
            chipsHtml += `<span class="filter-chip">📑 ${typeText} <span class="chip-close" onclick="clearRecordTypeFilter()">&times;</span></span>`;
            activeFilterCount++;
        }
        if (hasStock) {
            chipsHtml += `<span class="filter-chip">🏢 ${escapeHtml(currentFilterStock)} <span class="chip-close" onclick="clearStockFilter()">&times;</span></span>`;
            activeFilterCount++;
        }
        if (hasAccount) {
            chipsHtml += `<span class="filter-chip">💼 ${escapeHtml(currentFilterAccount)} <span class="chip-close" onclick="clearAccountFilter()">&times;</span></span>`;
            activeFilterCount++;
        }
        if (hasBroker) {
            chipsHtml += `<span class="filter-chip">🏦 ${escapeHtml(currentFilterBroker)} <span class="chip-close" onclick="clearBrokerFilter()">&times;</span></span>`;
            activeFilterCount++;
        }
        if (hasSubAccount) {
            chipsHtml += `<span class="filter-chip">💳 ${escapeHtml(currentFilterSubAccount)} <span class="chip-close" onclick="clearSubAccountFilter()">&times;</span></span>`;
            activeFilterCount++;
        }
        if (hasKeyword) {
            currentFilterKeywords.forEach((kw, idx) => {
                chipsHtml += `<span class="filter-chip">🔍 '${escapeHtml(kw)}' <span class="chip-close" onclick="clearKeywordFilter(${idx})" title="검색어 해제">&times;</span></span>`;
                activeFilterCount++;
            });
        }
        
        // 편의 기능: 조건이 2개 이상 섞여 있을 때는 '전체 초기화' 단축 버튼 추가
        if (activeFilterCount >= 2) {
            chipsHtml += `<span onclick="clearAllFilters()" style="font-size: 11px; color: var(--danger-color); cursor: pointer; text-decoration: underline; margin-left: 5px;" title="모든 조건 해제">전체 초기화</span>`;
        }
        
        chipsHtml += `<span style="margin-left: auto; color: var(--danger-color); font-size: 12px; font-weight: bold;">총 ${data.total}건</span>`;
        
        banner.innerHTML = chipsHtml;
    } else { 
        banner.style.display = 'none'; 
        if (filterBoxContainer) filterBoxContainer.classList.remove('filter-active');
    }

    lastRenderedMonth = '';

    if (filteredEntries.length === 0) {
        historyList.innerHTML = '<p style="text-align:center; color:var(--text-muted-color); font-size: 16px; padding: 20px;">조건에 맞는 기록이 없습니다.</p>';
        // 높이 고정 해제
        requestAnimationFrame(() => { historyList.style.minHeight = ''; });
        return;
    }

    renderPage(filteredEntries);

    // 리렌더링 완료 후 높이 고정 해제 (부드러운 전환을 위해 브라우저 페인트 타이밍에 맞춤)
    requestAnimationFrame(() => { historyList.style.minHeight = ''; });
}

// ⭐️ '검토 필요' 기록이 있으면 목록 위에 건수 안내를 띄운다.
//    봇 체결이 보유 수량을 넘거나(서버가 거부 대신 표시만 한다) 매수를 고쳐 초과 매도가
//    생기면 붙는 표시인데, 예전에는 화면 어디에도 나오지 않아 쌓이기만 했다.
function renderReviewNotice() {
    if (!historyList || !historyList.parentNode) return;
    let bar = document.getElementById('reviewNoticeBar');
    if (!bar) {
        bar = document.createElement('div');
        bar.id = 'reviewNoticeBar';
        bar.className = 'review-notice-bar';
        historyList.parentNode.insertBefore(bar, historyList);
    }
    const count = journalMeta?.reviewCount || 0;
    if (!count || currentFilterNeedsReview) {
        bar.style.display = 'none';
        return;
    }
    bar.style.display = 'flex';
    bar.innerHTML = `<span>⚠️ 확인이 필요한 매매 기록이 <b>${count}건</b> 있습니다.</span>`
        + `<button type="button" class="review-notice-btn" onclick="showNeedsReviewEntries()">모아 보기</button>`;
}

// ⭐️ '검토 완료' — 서버의 표시를 내리고 화면 목록에도 반영한다.
async function resolveEntryReview(entry) {
    try {
        const res = await fetch(`/api/entry/${entry.id}/review`, { method: 'POST' });
        if (!res.ok) {
            let msg = '검토 완료 처리에 실패했습니다.';
            try { const d = await res.json(); if (d && d.error) msg = d.error; } catch (_) { /* 기본 문구 */ }
            await customAlert(msg);
            return;
        }
        await refreshEntriesFromServer();
        if (currentFilterNeedsReview && !journalMeta.reviewCount) currentFilterNeedsReview = false;
        await displayEntries(true);
    } catch (e) {
        await customAlert('검토 완료 처리 중 오류가 발생했습니다.');
    }
}

function renderPage(pageEntries) {
    const existingSentinel = document.getElementById('scroll-sentinel');
    if (existingSentinel) {
        loadMoreObserver.unobserve(existingSentinel);
        existingSentinel.remove();
    }

    // ⭐️ 검색어 하이라이팅을 위한 정규식 준비
    const keywords = currentFilterKeywords;
    
    // ⭐️ 반환값은 그대로 innerHTML 에 들어간다. 그래서 강조 전에 먼저 안전하게 만든다 —
    //    평문 필드는 이스케이프, 본문(HTML)은 허용 목록으로 정화. 예전에는 원문을 그대로
    //    넣어, 봇 API·백업 복원으로 들어온 종목명·본문의 HTML 이 로그인 세션에서 실행됐다.
    function highlight(text, isHtml = false) {
        const safe = isHtml ? sanitizeHtml(text) : escapeHtml(text);
        if (!safe || keywords.length === 0) return safe;
        let result = safe;
        keywords.forEach(kw => {
            // 본문이 이스케이프된 상태이므로 검색어도 같은 형태로 맞춰 찾는다.
            const safeKw = escapeHtml(kw).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
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
        if (/^\d{4}-\d{2}-\d{2}$/.test(entry.tradeDate || '')) {
            entryMonth = `${entry.tradeDate.slice(0, 4)}년 ${Number(entry.tradeDate.slice(5, 7))}월`;
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
        card.dataset.entryId = entry.id;
        const imageSrc = ''; // 첨부는 본문을 펼칠 때 단건 상세에서 받는다.
        const imageHtml = imageSrc ? `<div style="margin-top:10px;"><img src="${escapeAttr(imageSrc)}" class="entry-thumbnail" loading="lazy" decoding="async" onclick="openImageViewer(this.src, event)" title="클릭하여 원본 보기"></div>` : '';

        const createdStr = entry.createdAt ? new Date(entry.createdAt).toLocaleString() : new Date(entry.id).toLocaleString();
        const updatedStr = entry.updatedAt ? new Date(entry.updatedAt).toLocaleString() : '';
        const timeDisplayHtml = `
            <div style="display: flex; flex-direction: column; gap: 3px;">
                <span style="color: var(--text-strong-color); font-weight: var(--fw-bold, bold);">🕒 기록 일시: ${escapeHtml(entry.date)}</span>
                ${entry.tradeDate ? `<span>거래일: ${escapeHtml(entry.tradeDate)}${entry.exchange ? ` (${escapeHtml(entry.exchange)})` : ''}</span>` : ''}
                <span style="font-size: 11px; color: var(--text-muted-color);">최초 작성: ${createdStr}${updatedStr && updatedStr !== createdStr ? ` | 최종 수정: ${updatedStr}` : ''}</span>
            </div>
        `;
        const tagsArr = entry.tags ? entry.tags.split(',').filter(Boolean) : [];
        const tagsHtml = tagsArr.length > 0 ? `<div style="margin-top: 8px;">` + tagsArr.map(t => `<span class="history-tag">#${highlight(t)}</span>`).join('') + `</div>` : '';

        // ⭐️ onclick 안의 JS 문자열은 escapeJsInAttr 로 막는다. 작은따옴표만 막으면
        //    큰따옴표 하나로 속성을 벗어나 임의 핸들러를 붙일 수 있었다.
        const safeStockName = escapeJsInAttr(entry.stockName || '');
        const displayStockName = highlight(entry.stockName);
        const stockBadge = entry.stockName ? `<span class="cal-badge stock" style="padding:4px 10px; border-radius:12px; font-size:0.95em; font-weight:bold; color:var(--text-strong-color); margin:0;" onclick="filterByStock('${safeStockName}', event)" title="${escapeAttr(entry.stockName)} 모아보기">🏷️ ${displayStockName}</span>` : '';

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
            const displayThoughts = highlight(entry.preview || '');
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
            const cPre = '';
            const cSuf = !entry.currency || entry.currency === 'KRW' ? '원' : ` ${escapeHtml(entry.currency)}`;
            
            if (entry.tradeType === '배당' && (entry.price > 0 || entry.quantity > 0)) {
                const totalAmount = escapeHtml((entry.price * (entry.quantity || 1)).toLocaleString());
                detailsHtml = `
                    <div class="entry-details">
                        <div class="detail-item">배당금: <span class="masked-amount">${cPre}${totalAmount}${cSuf}</span></div>
                    </div>
                `;
            } else if (entry.tradeType !== '관망' && entry.tradeType !== '주시' && (entry.price > 0 || entry.quantity > 0)) {
                // 숫자로 바꿔서 쓴다 — 복원 ZIP 등에서 문자열이 오면 toLocaleString 이
                // 원문을 그대로 돌려줘 HTML 이 섞여 들어갈 수 있다.
                const priceStr = entry.price ? escapeHtml(Number(entry.price).toLocaleString()) : '0';
                const qtyStr = entry.quantity ? escapeHtml(Number(entry.quantity).toLocaleString()) : '0';
                const totalAmount = escapeHtml((entry.price * entry.quantity).toLocaleString());
                detailsHtml = `
                    <div class="entry-details">
                        <div class="detail-item">단가: <span>${cPre}${priceStr}${cSuf}</span></div>
                        <div class="detail-item">수량: <span class="masked-amount">${qtyStr}주</span></div>
                        <div class="detail-item">총액: <span class="masked-amount">${cPre}${totalAmount}${cSuf}</span></div>
                    </div>
                `;
            }
            const tradeBadge = `<span style="background-color: ${typeColor}; color: white; padding:4px 8px; border-radius:12px; font-size:0.85em; font-weight:bold; margin:0;">${escapeHtml(entry.tradeType)}</span>`;
            // ⭐️ 제외 계좌 체결은 기록으로는 남기되, 합계·통계에 안 잡힌다는 걸 목록에서도 알 수 있게 한다.
            const simBadge = isExcludedFromTotals(entry)
                ? `<span style="background-color: var(--warning-color); color: white; padding:4px 8px; border-radius:12px; font-size:0.85em; font-weight:bold; margin:0;" title="총 투자금액·평가금액·실현손익·도넛 차트·통계에는 반영되지 않는 기록입니다.">${exclusionBadgeLabel(entry)}</span>`
                : '';
            // 취소·접수 주문도 이력으로는 남지만 보유 수량·손익·통계에는 넣지 않는다(서버 entry_logic.is_settled_trade).
            const statusLabel = { CANCELED: '취소된 주문', SUBMITTED: '미체결 접수' }[entry.tradeStatus];
            const statusBadge = statusLabel
                ? `<span style="background-color: var(--text-muted-color); color: white; padding:4px 8px; border-radius:12px; font-size:0.85em; font-weight:bold; margin:0;" title="보유 수량·실현손익·달력·차트·통계에는 반영되지 않는 기록입니다.">${statusLabel}</span>`
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
            const displayThoughts = highlight(entry.preview || '');
            const reviewHtml = entry.needsReview
                ? `<div class="review-note"><span>⚠️ <b>검토 필요</b> — ${escapeHtml(entry.reviewReason || '보유 수량과 맞지 않는 매도 체결입니다.')}</span>`
                  + `<button type="button" class="btn-review-done">검토 완료</button></div>`
                : '';
            if (entry.needsReview) card.classList.add('needs-review');
            card.innerHTML = `
            <div class="entry-header">
                ${timeDisplayHtml}
                <div class="header-right"><span>💼 ${escapeHtml(entry.tradeClass)}</span><button class="btn-edit">수정</button><button class="btn-delete">삭제</button></div>
            </div>
                <div class="entry-title" style="display: flex; align-items: center; flex-wrap: wrap; gap: 8px;">${stockBadge}${tradeBadge}${statusBadge}${simBadge}${brokerBadge}</div>
                ${reviewHtml}
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

        const reviewBtn = card.querySelector('.btn-review-done');
        if (reviewBtn) reviewBtn.addEventListener('click', () => resolveEntryReview(entry));

        const content = card.querySelector('.entry-content');
        if (entry.hasDetail) {
            const expand = document.createElement('button');
            expand.type = 'button';
            expand.className = 'btn-entry-detail';
            expand.textContent = '본문 펼치기';
            expand.setAttribute('aria-expanded', 'false');
            content.after(expand);
            expand.addEventListener('click', async () => {
                if (expand.disabled) return;
                if (expand.getAttribute('aria-expanded') === 'true') {
                    content.querySelector('.ql-editor').innerHTML = highlight(entry.preview || '');
                    expand.textContent = '본문 펼치기';
                    expand.setAttribute('aria-expanded', 'false');
                    return;
                }
                expand.disabled = true;
                try {
                    const detail = await journalJSON(`/api/entry/${entry.id}`, false);
                    if (!card.isConnected) return;
                    const body = content.querySelector('.ql-editor');
                    body.innerHTML = highlight(detail.thoughts || '', true);
                    const attachment = safeUrl(detail.attachedImage, { allowDataImage: true });
                    if (attachment && !(detail.thoughts || '').includes(detail.attachedImage)) {
                        const image = document.createElement('img');
                        image.src = attachment;
                        image.className = 'entry-thumbnail';
                        body.appendChild(image);
                    }
                    body.querySelectorAll('img').forEach(img => {
                        img.loading = 'lazy';
                        img.decoding = 'async';
                        img.addEventListener('click', event => window.openImageViewer(img.src, event));
                    });
                    expand.textContent = '본문 접기';
                    expand.setAttribute('aria-expanded', 'true');
                } catch (error) {
                    await customAlert(error.message);
                } finally { expand.disabled = false; }
            });
        }

        listFragment.appendChild(card);
    });

    // 스크롤 감지용 투명 요소(Sentinel) 추가
    if (historyCursor) {
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
    if (!entry) return;
    const ticket = journalRequests.edit = (journalRequests.edit || 0) + 1;
    try { entry = await journalJSON(`/api/entry/${entry.id}`, false); }
    catch (error) { await customAlert(error.message); return; }
    if (ticket !== journalRequests.edit) return;
    editingEntry = entry;
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
    setEntryCurrency(entry.currency);
    document.getElementById('accountName').value = entry.accountName || '';

    if (entry.type === 'trade') {
        syncJournalAccountSelect(entry.subAccount);
    }

    // ⭐️ 숨김 체크박스는 이 기록 자신의 값이 아니라 '해당 종목의 현재 숨김 상태'로 채운다.
    //    (숨김은 기록 단위가 아닌 종목 단위 속성이므로, 무관한 수정이 상태를 뒤집지 않게 한다)
    syncHiddenCheckbox(entry.stockName);
    
    // ⭐️ 과거 하단에 첨부했던 이미지가 있다면 에디터 본문으로 자동 이동(마이그레이션)
    let contentHtml = entry.thoughts || '';
    const legacyImage = safeUrl(entry.attachedImage, { allowDataImage: true });
    if (legacyImage && !contentHtml.includes(entry.attachedImage)) {
        contentHtml += `<p><br></p><p><img src="${escapeAttr(legacyImage)}"></p>`;
    }
    // ⭐️ 편집기 본문도 살아 있는 문서라 정화 없이 넣으면 그 자리에서 실행된다.
    if (window.quill) window.setEditorHtml(sanitizeHtml(contentHtml)); // 에디터에 기존 내용 불러오기
    
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
            const res = await fetchWithOversellConfirm(`/api/entry/${id}`, {
                method: 'DELETE'
            });
            if (res === null) return;   // 보유 초과 경고에서 취소
            if (res.ok) {
                await refreshEntriesFromServer();
                await displayEntries();
            } else { await customAlert("삭제에 실패했습니다."); }
        } catch(e) { await customAlert("삭제 중 오류가 발생했습니다."); }
    }
}

