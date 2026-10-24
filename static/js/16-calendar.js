// ═══════════════════════════════════════════════════════════════════
// 16-calendar.js — 캘린더와 월간/주간 손익 차트
//
// ⚠️ 이 파일들은 ES 모듈이 아니라 **순서대로 로드되는 클래식 스크립트**다.
//    최상위 let/const/function 은 전역 렉시컬 환경을 공유하므로, 예전 script.js
//    한 덩어리였을 때와 실행 의미가 완전히 같다. (HTML 의 인라인 onclick 핸들러가
//    전역 함수를 그대로 부르고 있어 모듈로 바꾸면 그것들이 전부 깨진다)
//    → 실행 순서는 파일명 번호 순서다(app/utils/scriptbundle.py 가 그 순서로 이어 붙인다).
// ═══════════════════════════════════════════════════════════════════

let currentDate = new Date();
function renderCalendar() {
    if (!journalMeta) return Promise.resolve();
    const month = journalMonth(currentDate);
    const result = readJournalView('calendar', journalUrl('calendar', { month, currency: displayCurrency }),
        renderCalendarSummary, 'calendarGrid', renderCalendar);
    // 차트는 화면에서 열었을 때만 집계한다.
    if (document.getElementById('btnChartView')?.classList.contains('active')) window.renderMonthlyProfitChart();
    return result;
}

function renderCalendarSummary(dailyStats) {
    const year = currentDate.getFullYear(), month = currentDate.getMonth();
    document.getElementById('calendarMonthTitle').innerText = `${year}년 ${month + 1}월${displayCurrency === 'KRW' ? '' : ` · ${displayCurrency}`}`;
    
    const firstDay = new Date(year, month, 1), lastDay = new Date(year, month + 1, 0);
    const calendarGrid = document.getElementById('calendarGrid');
    calendarGrid.innerHTML = '';
    
    for(let i=0; i<firstDay.getDay(); i++) calendarGrid.innerHTML += `<div style="background:var(--border-light-color); border-radius:4px;"></div>`;
    
    for(let d=1; d<=lastDay.getDate(); d++) {
        const key = `${year}-${String(month+1).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
        
        const dStats = dailyStats[key] || { profit: 0, details: {} };
        
        let profitHtml = '';
        if (dStats.profit > 0) profitHtml = `<div style="color:var(--danger-color); font-size:11px; font-weight:var(--fw-bold, bold); margin-bottom:2px;">+${moneyNumber(dStats.profit)}</div>`;
        else if (dStats.profit < 0) profitHtml = `<div style="color:var(--primary-color); font-size:11px; font-weight:var(--fw-bold, bold); margin-bottom:2px;">${moneyNumber(dStats.profit)}</div>`;
        
        let badgesHtml = '';
        for (const [stock, counts] of Object.entries(dStats.details)) {
            // ⭐️ 표시는 escapeHtml, onclick 안의 JS 문자열은 escapeJsInAttr 로 따로 막는다.
            const prefix = stock ? `${escapeHtml(stock)} ` : '';
            const safeStock = stock ? escapeJsInAttr(stock) : '';
            
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
    
}

// ⭐️ 종목 드롭다운을 현재 필터 값에 맞춘다. 달력 배지·카드가 넘겨 준 이름이 **옛 표기**일 수
//    있는데(같은 종목의 다른 이름), 그럴 때도 드롭다운은 같은 종목의 현재 이름을 가리켜야 한다.
//    - 목록에 없으면 건드리지 않는다: 필터 자체는 동일성으로 대조하니 결과는 이미 맞다.
function syncStockSelectValue() {
    const stockSelect = document.getElementById('filterStockSelect');
    if (!stockSelect) return;
    const options = Array.from(stockSelect.options).map(o => o.value);
    const resolved = resolveStockOptionValue(options, currentFilterStock);
    if (!resolved) return;
    stockSelect.value = resolved;
    currentFilterStock = resolved;
    window.updateDashboardFilterStyle(stockSelect);
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
    syncStockSelectValue();
    
    document.getElementById('btnListView').click();
    displayEntries(true);

    window.scrollToFilterBox();
};

window.filterByStock = function(stockName, event) {
    if (event) event.stopPropagation();
    clearAllFilters(false);
    currentFilterStock = stockName;
    window.saveFilterPreferences();
    
    syncStockSelectValue();
    
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
        syncStatsControls();
        monthlyProfitChartContainer.style.display = 'block';
        if (btnStats) {
            btnStats.style.backgroundColor = 'transparent';
            btnStats.style.color = 'var(--primary-color)';
        }
    }
    
    window.renderMonthlyProfitChart();
};

// ⭐️ 주간/월간 집계 단위 토글 버튼 표시 동기화 (KRX/AFT 토글과 동일 방식)
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
    window.chartWeekOffset = 0;  // 단위 전환 시 항상 최근 기간부터 보여준다
    window.chartMonthOffset = 0;
    window.updateChartGranularityToggle();
    // 집계 단위 변경 시 열려있던 상세 내역은 닫아 혼동 방지
    const detailListEl = document.getElementById('chartDetailList');
    if (detailListEl) detailListEl.style.display = 'none';
    
    window.renderMonthlyProfitChart();
};

// ⭐️ 현재 집계 단위(주간/월간)에 맞는 기간 이동 설정 반환
window.getChartRangeConfig = function() {
    const isWeekly = (window.currentChartGranularity || 'monthly') === 'weekly';
    return isWeekly
        ? { isWeekly: true,  step: CHART_WEEK_WINDOW,  maxOffset: CHART_WEEK_MAX_OFFSET,  offset: Math.max(0, Math.min(CHART_WEEK_MAX_OFFSET, window.chartWeekOffset || 0)),   unit: '주' }
        : { isWeekly: false, step: CHART_MONTH_WINDOW, maxOffset: CHART_MONTH_MAX_OFFSET, offset: Math.max(0, Math.min(CHART_MONTH_MAX_OFFSET, window.chartMonthOffset || 0)), unit: '개월' };
};

// ⭐️ 차트 기간 이동 (dir: -1 = 과거로, +1 = 현재 방향으로 / 주간 12주, 월간 12개월씩)
window.shiftChartRange = function(dir) {
    const cfg = window.getChartRangeConfig();
    const next = Math.max(0, Math.min(cfg.maxOffset, cfg.offset + (dir < 0 ? cfg.step : -cfg.step)));
    if (next === cfg.offset) return;
    if (cfg.isWeekly) window.chartWeekOffset = next;
    else window.chartMonthOffset = next;
    window.renderMonthlyProfitChart();
};

// ⭐️ 차트를 최근 기간(최근 12주 / 최근 12개월)으로 복귀
window.resetChartRange = function() {
    const cfg = window.getChartRangeConfig();
    if (!cfg.offset) return;
    if (cfg.isWeekly) window.chartWeekOffset = 0;
    else window.chartMonthOffset = 0;
    window.renderMonthlyProfitChart();
};

// ⭐️ 기간 이동 내비게이션 표시 동기화 (주간/월간 모두 노출)
window.updateChartRangeNav = function(rangeText) {
    const nav = document.getElementById('chartRangeNav');
    if (!nav) return;
    // 성과분석(분석) 화면이 열려 있으면 차트가 숨겨진 상태이므로 내비게이션도 숨긴다
    const statsEl = document.getElementById('inlineStatsContainer');
    const statsOpen = !!statsEl && statsEl.style.display === 'block';
    nav.style.display = statsOpen ? 'none' : 'flex';
    if (statsOpen) return;

    const cfg = window.getChartRangeConfig();
    const offset = cfg.offset;
    const btnPrev = document.getElementById('btnChartRangePrev');
    const btnNext = document.getElementById('btnChartRangeNext');
    const btnNow = document.getElementById('btnChartRangeNow');
    const labelEl = document.getElementById('chartRangeLabel');

    const setDisabled = (btn, disabled) => {
        if (!btn) return;
        btn.disabled = disabled;
        btn.style.opacity = disabled ? '0.35' : '1';
        btn.style.cursor = disabled ? 'default' : 'pointer';
    };
    setDisabled(btnPrev, offset >= cfg.maxOffset); // 주간 52주 / 월간 60개월까지만 과거 조회
    setDisabled(btnNext, offset <= 0);
    if (btnPrev) btnPrev.title = `이전 ${cfg.step}${cfg.unit}`;
    if (btnNext) btnNext.title = `다음 ${cfg.step}${cfg.unit}`;
    if (btnNow) {
        btnNow.title = `최근 ${cfg.step}${cfg.unit}로 이동`;
        btnNow.style.display = offset > 0 ? 'inline-flex' : 'none';
    }
    if (labelEl) labelEl.innerText = rangeText || '';
};

// ⭐️ 차트 하단 안내문 — 평가손익의 계산 기준과, 현재가를 못 구해 빠진 종목을 알린다.
//    평가손익은 개별 매수건 단가(FIFO), 실현손익은 이동평균단가라 기준이 다르다.
//    월별 귀속을 유지하려면 로트 단위 단가가 필요해 통일할 수 없으므로, 대신 명시한다.
window.updateChartEvalNotice = function(info) {
    const el = document.getElementById('chartEvalNotice');
    if (!el) return;

    const statsEl = document.getElementById('inlineStatsContainer');
    const statsOpen = !!statsEl && statsEl.style.display === 'block';
    const { type, missingStocks = [], offset = 0, periodWord = '월' } = info || {};

    const esc = (t) => String(t).replace(/[&<>"]/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch]));
    const notes = [];

    if (!statsOpen && type === 'evaluated') {
        notes.push(`※ 막대는 <b>해당 ${periodWord}에 매수해 아직 보유 중인 물량</b>을 <b>오늘 현재가</b>로 평가한 값입니다. 이미 매도한 매수분은 표시되지 않습니다.`);
        notes.push('※ 평가손익은 <b>개별 매수건 단가(FIFO)</b> 기준, 실현손익은 <b>이동평균단가</b> 기준이라 서로 기준이 다릅니다.');
        if (offset > 0) notes.push('※ 과거 구간일수록 이미 매도된 물량이 많아 막대가 비어 보일 수 있습니다.');

        if (missingStocks.length) {
            const shown = missingStocks.slice(0, 5).map(esc).join(', ');
            const more = missingStocks.length > 5 ? ` 외 ${missingStocks.length - 5}개` : '';
            notes.push(`⚠️ 현재가를 불러오지 못한 <b>${missingStocks.length}개 종목</b>(${shown}${more})은 평가손익에서 <b>제외</b>됐습니다. 종목코드가 없거나 대시보드 필터에서 빠진 종목일 수 있습니다.`);
        }
    }

    if (!notes.length) {
        el.style.display = 'none';
        el.innerHTML = '';
        return;
    }
    el.innerHTML = notes.map(t => `<div style="margin-top:4px;">${t}</div>`).join('');
    el.style.display = 'block';
};

// ⭐️ 차트 막대 클릭 시 하단에 종목별 상세 내역을 그려주는 함수 (다중 섹션 지원)
window.renderChartDetailList = function(periodLabel, sections, currency = displayCurrency) {
    const inlineStatsContainer = document.getElementById('inlineStatsContainer');
    if (inlineStatsContainer) {
        inlineStatsContainer.style.display = 'none';
        syncStatsControls();
        const btnStats = document.getElementById('btnStats');
        if (btnStats) {
            btnStats.style.backgroundColor = 'transparent';
            btnStats.style.color = 'var(--primary-color)';
        }
    }
    
    const container = document.getElementById('chartDetailList');
    if (!container) return;
    
    let html = `<div style="font-size: 13px; font-weight: bold; margin-bottom: 10px; color: var(--text-strong-color); display: flex; justify-content: space-between; align-items: center;">
                    <span>📊 ${periodLabel} 상세 내역</span>
                    <span style="font-size: 11px; color: var(--text-muted-color); font-weight: normal; cursor: pointer;" onclick="document.getElementById('chartDetailList').style.display='none';">닫기 &times;</span>
                </div>`;
    
    let hasData = false;
    sections.forEach(sec => {
        const { title, breakdown, isProfit, flow } = sec;
        // ⭐️ flow: 매매금액처럼 손익이 아닌 '금액' 항목의 방향 (-1 = 매수, +1 = 매도).
        //    손익이 아니므로 부호는 붙이지 않고, 방향은 색으로만 구분한다.
        //    색은 바로 위 차트의 매수(빨강)·매도(파랑) 막대와 맞춘다.
        const decorate = (v) => {
            if (flow) {
                return {
                    color: flow > 0 ? 'var(--primary-color)' : 'var(--danger-color)',
                    prefix: '',
                    amount: Math.abs(v)
                };
            }
            if (isProfit && v > 0) return { color: 'var(--danger-color)', prefix: '+', amount: v };
            if (isProfit && v < 0) return { color: 'var(--primary-color)', prefix: '', amount: v };
            return { color: 'var(--text-strong-color)', prefix: '', amount: v };
        };
        const stocks = Object.keys(breakdown).filter(s => breakdown[s] !== 0);
        
        if (stocks.length > 0) {
            hasData = true;
            stocks.sort((a, b) => breakdown[b] - breakdown[a]); // 금액(손익) 기준 내림차순 정렬
            
            const sectionTotal = stocks.reduce((acc, s) => acc + breakdown[s], 0);
            const totalDeco = decorate(sectionTotal);
            
            html += `<div style="display: flex; justify-content: space-between; align-items: flex-end; margin-top: 15px; margin-bottom: 8px;">
                        <span style="font-size: 12px; font-weight: bold; color: var(--text-muted-color);">[ ${title} ]</span>
                        <span style="font-size: 13px; font-weight: bold; color: ${totalDeco.color};">${totalDeco.prefix}${moneyText(totalDeco.amount, currency)}</span>
                     </div>`;
            html += `<div style="display: grid; gap: 6px;">`;
            stocks.forEach(s => {
                const { color, prefix, amount } = decorate(breakdown[s]);
                html += `<div style="display: flex; justify-content: space-between; font-size: 12px; padding: 6px 10px; background: var(--bg-color); border-radius: 6px; border: 1px solid var(--border-light-color);">
                    <span style="font-weight: bold; color: var(--text-strong-color);">${escapeHtml(s)}</span>
                    <span style="color: ${color};">${prefix}${moneyText(amount, currency)}</span>
                </div>`;
            });
            html += `</div>`;
        }
    });
    
    if (!hasData) {
        html += `<div style="color: var(--text-muted-color); font-size: 12px; text-align: center; padding: 10px 0; background: var(--bg-color); border-radius: 6px; border: 1px solid var(--border-light-color);">해당 내역이 없습니다.</div>`;
    }
    
    container.innerHTML = html;
    container.style.display = 'block';
};

// ⭐️ 최근 12개월 월별 실현손익/평가손익/매매금액 바 차트 렌더링 함수 (통합)
window.renderMonthlyProfitChart = async function() {
    if (!journalMeta) return;
    const ticket = journalRequests.chart = (journalRequests.chart || 0) + 1;
    const generation = journalGeneration;
    console.log("[Chart] 월별 실현/평가/매매금액 차트 렌더링 시작...");
    
    // ⭐️ 성과분석 뷰가 열려있다면 필터 갱신에 맞춰 다시 불러온다. 단 호출은 아래에서
    //    window.chartPeriodRange(= 지금 화면이 보고 있는 구간)를 확정한 뒤에 한다.
    //    여기서 부르면 직전 렌더의 구간으로 조회돼 차트와 한 박자 어긋난다.
    const inlineStatsContainer = document.getElementById('inlineStatsContainer');
    const statsViewOpen = !!inlineStatsContainer && inlineStatsContainer.style.display === 'block';
    
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
    // ⭐️ 렌더링 초기화 시 하단 상세 내역 영역 닫기
    const detailListEl = document.getElementById('chartDetailList');
    if (detailListEl) detailListEl.style.display = 'none';
    
    // ⭐️ 12개 기간(월간: 12개월 / 주간: 12주) 라벨 생성
    //    주간은 weekOffset 만큼 과거로 밀어서 최대 52주 전까지 조회 가능
    const thisMonday = getMonday(now);
    const rangeCfg = window.getChartRangeConfig();
    const weekOffset = isWeekly ? rangeCfg.offset : 0;
    const monthOffset = isWeekly ? 0 : rangeCfg.offset;
    for (let i = 11; i >= 0; i--) {
        let key;
        if (isWeekly) {
            const d = new Date(thisMonday);
            d.setDate(d.getDate() - (i + weekOffset) * 7);
            key = fmtYmd(d);
        } else {
            const d = new Date(now.getFullYear(), now.getMonth() - i - monthOffset, 1);
            key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
        }
        labels.push(key);
    }
    
    let result;
    try {
        result = await journalJSON(journalUrl('chart', {
            ...journalFilters('chart'), granularity, period: labels,
        }));
    } catch (error) {
        if (ticket === journalRequests.chart && generation === journalGeneration) {
            journalError('monthlyProfitChartContainer', error, window.renderMonthlyProfitChart);
        }
        return;
    }
    if (ticket !== journalRequests.chart || generation !== journalGeneration) return;
    document.getElementById('monthlyProfitChartContainer-load-error')?.remove();
    const currency = result.currency || displayCurrency;
    const monthlyData = result.monthlyData;
    const missing = new Set();
    result.evaluation.forEach(lot => {
        const price = lot.stockCode ? window.currentPriceCache[lot.stockCode] : undefined;
        if (price === undefined || price === null) { missing.add(lot.stock); return; }
        const period = monthlyData[lot.period];
        const profit = price * lot.quantity - lot.cost;
        period.evaluated += profit;
        period.evaluated_breakdown[lot.stock] = (period.evaluated_breakdown[lot.stock] || 0) + profit;
    });
    const evalMissingStocks = [...missing];

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

    // ⭐️ 지금 화면이 보고 있는 구간을 확정한다.
    //    주간: 첫 주 월요일 ~ 마지막 주 일요일 / 월간: 첫 달 1일 ~ 마지막 달 말일
    //    (분석 탭은 자체 기간 선택을 사용한다 — 07-stats.js 참고)
    let rangeText = '';
    window.chartPeriodRange = null;
    if (labels.length) {
        const toYmd = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
        if (isWeekly) {
            const toLocalDate = (ymd) => { const p = ymd.split('-').map(Number); return new Date(p[0], p[1] - 1, p[2]); };
            const start = toLocalDate(labels[0]);
            const end = toLocalDate(labels[labels.length - 1]);
            end.setDate(end.getDate() + 6);
            const md = (d) => `${d.getMonth() + 1}/${d.getDate()}`;
            rangeText = `${start.getFullYear()}. ${md(start)} ~ ${end.getFullYear() !== start.getFullYear() ? end.getFullYear() + '. ' : ''}${md(end)}`;
            window.chartPeriodRange = { start: toYmd(start), end: toYmd(end), text: rangeText };
        } else {
            const [sy, sm] = labels[0].split('-');
            const [ey, em] = labels[labels.length - 1].split('-');
            rangeText = `${sy}. ${parseInt(sm, 10)}월 ~ ${ey !== sy ? ey + '. ' : ''}${parseInt(em, 10)}월`;
            const start = new Date(Number(sy), Number(sm) - 1, 1);
            const end = new Date(Number(ey), Number(em), 0); // 마지막 달의 말일
            window.chartPeriodRange = { start: toYmd(start), end: toYmd(end), text: rangeText };
        }
    }
    if (window.updateChartRangeNav) window.updateChartRangeNav(rangeText);

    // ⭐️ 차트 아래 안내문 (평가손익의 계산 기준 / 현재가 누락 / 과거 구간 주의)
    if (window.updateChartEvalNotice) {
        window.updateChartEvalNotice({
            type,
            missingStocks: evalMissingStocks,
            offset: rangeCfg.offset,
            periodWord
        });
    }

    // 필터와 월간/주간 묶음이 바뀌면 분석도 자체 선택 기간으로 다시 조회한다.
    if (statsViewOpen) window.loadTradeStats();

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
            // ⭐️ x축(해당 월) 기준으로 전체 데이터를 찾아주도록 설정하여 긴 막대 상하단에서도 오류 없이 작동하며 툴팁에 모든 정보를 표시
            interaction: {
                mode: 'index',
                intersect: false
            },
            plugins: {
                legend: { display: false },
                tooltip: {
                    filter: function(tooltipItem) {
                        return tooltipItem.raw !== null;
                    },
                    callbacks: {
                        label: function(context) {
                            let value = context.parsed.y;
                            if (context.raw === null) return null;
                            if (type === 'volume') {
                                let prefix = context.datasetIndex === 0 ? '매수: ' : '매도: ';
                                return prefix + moneyText(value, currency);
                            }
                            if (type === 'realized' || type === 'cumulative') {
                                const labelStr = context.dataset.label;
                                return labelStr + ': ' + (value > 0 ? '+' : '') + moneyText(value, currency);
                            }
                            return (value > 0 ? '+' : '') + moneyText(value, currency);
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
                const monthLabel = labels[index]; // 예: '2023-10' (월간) / '2023-10-16' (주간)
                const dataObj = monthlyData[monthLabel];
                // ⭐️ 주간은 'M/D 주', 월간은 원본 키를 상세 내역 제목에 사용
                const periodLabel = isWeekly ? `${displayLabels[index]} 주간` : monthLabel;

                let sections = [];

                if (type === 'realized') {
                    sections.push({ title: '배당 수익', breakdown: dataObj.dividend_breakdown || {}, isProfit: true });
                    sections.push({ title: '매매 실현손익', breakdown: dataObj.realized_breakdown || {}, isProfit: true });
                } else if (type === 'evaluated') {
                    sections.push({ title: '매수분 평가 손익', breakdown: dataObj.evaluated_breakdown || {}, isProfit: true });
                } else if (type === 'volume') {
                    // ⭐️ 매매금액은 손익이 아니므로 부호 없이 매수(빨강)·매도(파랑) 색으로만 구분
                    sections.push({ title: '매수 금액', breakdown: dataObj.buy_volume_breakdown || {}, isProfit: false, flow: -1 });
                    sections.push({ title: '매도 금액', breakdown: dataObj.sell_volume_breakdown || {}, isProfit: false, flow: 1 });
                } else if (type === 'cumulative') {
                    sections.push({ title: '누적 배당 수익금', breakdown: dataObj.cumulative_div_breakdown || {}, isProfit: true });
                    sections.push({ title: '총 누적 수익금', breakdown: dataObj.cumulative_breakdown || {}, isProfit: true });
                }
                
                // 계산된 내역을 하단 영역에 렌더링
                window.renderChartDetailList(periodLabel, sections, currency);
            }
        }
    });
};

// ⭐️ 개별 칩(Chip)용 필터 초기화 함수 세트
