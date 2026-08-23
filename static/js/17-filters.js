// ═══════════════════════════════════════════════════════════════════
// 17-filters.js — 필터 해제 버튼들 (히스토리·차트)·차트 뷰 전환
//
// ⚠️ 이 파일들은 ES 모듈이 아니라 **순서대로 로드되는 클래식 스크립트**다.
//    최상위 let/const/function 은 전역 렉시컬 환경을 공유하므로, 예전 script.js
//    한 덩어리였을 때와 실행 의미가 완전히 같다. (HTML 의 인라인 onclick 핸들러가
//    전역 함수를 그대로 부르고 있어 모듈로 바꾸면 그것들이 전부 깨진다)
//    → 로드 순서는 templates/stock-memo.html 의 <script> 순서가 결정한다. 바꾸지 말 것.
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
