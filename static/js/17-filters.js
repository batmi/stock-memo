// ═══════════════════════════════════════════════════════════════════
// 17-filters.js — 필터 해제 버튼들 (히스토리·차트)·차트 뷰 전환
//
// ⚠️ 이 파일들은 ES 모듈이 아니라 **순서대로 로드되는 클래식 스크립트**다.
//    최상위 let/const/function 은 전역 렉시컬 환경을 공유하므로, 예전 script.js
//    한 덩어리였을 때와 실행 의미가 완전히 같다. (HTML 의 인라인 onclick 핸들러가
//    전역 함수를 그대로 부르고 있어 모듈로 바꾸면 그것들이 전부 깨진다)
//    → 실행 순서는 파일명 번호 순서다(app/utils/scriptbundle.py 가 그 순서로 이어 붙인다).
// ═══════════════════════════════════════════════════════════════════

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

window.clearReviewFilter = function() {
    currentFilterNeedsReview = false;
    displayEntries(true);
    window.scrollToFilterBox();
};

// ⭐️ 상단 '검토 필요 N건' 안내를 누르면 그 기록만 모아 본다.
window.showNeedsReviewEntries = function() {
    currentFilterNeedsReview = true;
    window.setJournalView('list');
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
    currentFilterNeedsReview = false;
    
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

// 수동 전환과 시작 화면 복원이 같은 표시 규칙을 사용한다.
window.setJournalView = function(view, { render = true, remember = true } = {}) {
    if (!['list', 'calendar', 'chart'].includes(view)) return;
    const isList = view === 'list', isCalendar = view === 'calendar';
    for (const [name, id] of [['list', 'btnListView'], ['calendar', 'btnCalendarView'], ['chart', 'btnChartView']]) {
        document.getElementById(id)?.classList.toggle('active', view === name);
    }
    document.getElementById('historyList').style.display = isList ? 'flex' : 'none';
    document.getElementById('calendarViewSection').style.display = isCalendar ? 'block' : 'none';
    document.getElementById('monthlyChartSection').style.display = view === 'chart' ? 'block' : 'none';
    document.getElementById('filterBoxContainer').style.display = isList ? 'block' : 'none';
    document.getElementById('chartFilterBoxContainer').style.display = view === 'chart' ? 'block' : 'none';
    document.getElementById('btnToggleHistoryClosed').style.display = isList ? 'inline-block' : 'none';
    if (!isList) {
        const banner = document.getElementById('activeFilterBanner');
        if (banner) banner.style.display = 'none';
    }
    if (remember) window.dispatchEvent(new CustomEvent('journal-view-changed', { detail: { view } }));
    if (!render) return;
    if (isList) displayEntries(true);
    else if (isCalendar) renderCalendar();
    else window.renderMonthlyProfitChart();
};

for (const [view, id] of [['list', 'btnListView'], ['calendar', 'btnCalendarView'], ['chart', 'btnChartView']]) {
    document.getElementById(id)?.addEventListener('click', () => window.setJournalView(view));
}

function shiftCalendarMonth(delta) {
    currentDate = new Date(currentDate.getFullYear(), currentDate.getMonth() + delta, 1);
    renderCalendar();
}
document.getElementById('btnPrevMonth').addEventListener('click', () => shiftCalendarMonth(-1));
document.getElementById('btnNextMonth').addEventListener('click', () => shiftCalendarMonth(1));

// ⭐️ 모바일 당겨서 새로고침 (Pull-to-Refresh) 기능
