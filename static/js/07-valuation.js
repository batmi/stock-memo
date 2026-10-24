// 실현손익/과거 평가손익 선택. 시세 수집은 서버 큐에서 진행하고 화면은 상태만 조회한다.
let statsCurveMode = 'realized';
let statsCurveTicket = 0;
let statsCurveTimer = null;

function resetStatsCurveRequest() {
    ++statsCurveTicket;
    clearTimeout(statsCurveTimer);
    statsCurveTimer = null;
}

function statsCurveSection() {
    return '<section class="stats-curve-section">'
        + '<div class="stats-curve-header"><h4 id="statsCurveTitle"></h4>'
        + '<div class="stats-curve-switch" role="group" aria-label="손익 그래프 선택">'
        + '<button type="button" class="stats-curve-button" data-stats-curve="realized">누적 실현손익</button>'
        + '<button type="button" class="stats-curve-button" data-stats-curve="evaluated">평가손익</button></div></div>'
        + '<p id="statsCurveStatus" class="stats-muted" role="status"></p>'
        + '<p id="statsCurveNote" class="stats-muted"></p>'
        + '<button type="button" class="stats-curve-button" id="statsCurveRetry" hidden>다시 확인</button>'
        + '<div id="statsCurvePlot" class="stats-curve-plot"><canvas id="statsEquityChart"></canvas></div>'
        + '</section>';
}

function statsCurveVisible() {
    return !document.hidden && document.getElementById('statsCurveTitle')?.offsetParent !== null
        && inlineStatsContainer?.style.display === 'block';
}

function showStatsCurve(s, retry = false) {
    resetStatsCurveRequest();
    const title = document.getElementById('statsCurveTitle');
    if (!title) return;
    const evaluated = statsCurveMode === 'evaluated';
    title.textContent = evaluated ? '📈 평가손익 (일별 종가 기준)' : '📈 누적 실현손익 (배당 포함)';
    document.querySelectorAll('[data-stats-curve]').forEach(button => {
        button.setAttribute('aria-pressed', String(button.dataset.statsCurve === statsCurveMode));
    });
    const status = document.getElementById('statsCurveStatus');
    const note = document.getElementById('statsCurveNote');
    const plot = document.getElementById('statsCurvePlot');
    const retryButton = document.getElementById('statsCurveRetry');
    retryButton.hidden = true;
    retryButton.onclick = () => showStatsCurve(statsLastReport, true);
    window.statsEquityChartInstance?.destroy();
    window.statsEquityChartInstance = null;
    if (!evaluated) {
        const curve = s.equityCurve || [];
        status.textContent = curve.length ? '' : '선택한 기간에 실현손익 기록이 없습니다.';
        note.textContent = s.maxDrawdownTroughDate ? '━ 최대 낙폭 구간' : '';
        note.classList.toggle('stats-drawdown-note', !!s.maxDrawdownTroughDate);
        plot.hidden = !curve.length;
        if (curve.length) renderStatsEquityChart(s);
        return;
    }
    note.classList.remove('stats-drawdown-note');
    note.textContent = '계좌·종목별 (해당일 종가 − 해당일 보유분의 평균 매입단가) × 보유 수량을 합산합니다. '
        + '평균 매입단가는 이전 날짜의 매수를 포함한 매매 이력을 반영합니다. '
        + '배당·실현손익·비용은 합산하지 않습니다. 휴장일은 직전 거래일 종가를 사용하고, 시세가 누락된 날은 선을 끊어 표시합니다.';
    status.textContent = '저장된 과거 시세를 확인하고 있습니다…';
    plot.hidden = true;
    const ticket = statsCurveTicket, generation = journalGeneration;
    const query = { ...statsLastQuery, currency: s.currency, version: s.version };
    const current = () => ticket === statsCurveTicket && generation === journalGeneration && s === statsLastReport;
    const poll = async (first = false) => {
        if (!current() || !statsCurveVisible()) return;
        try {
            const url = journalUrl('stats/evaluation', { ...query, ...(first && retry ? { retry: '1' } : {}) });
            const response = await fetchWithTimeout(url, { method: first ? 'POST' : 'GET' });
            const result = await response.json();
            if (!current() || !statsCurveVisible()) return;
            if (!response.ok) {
                const error = new Error(result.error || '과거 평가손익을 불러오지 못했습니다.');
                error.status = response.status;
                throw error;
            }
            const messages = [`종가 기준: ${result.period.from} ~ ${result.period.to}`];
            if (result.pending) messages.push(`시세 수집 중 · ${result.progress.ready}/${result.progress.total} 종목·거래일 확인`);
            if (!result.hasHoldings) messages.push('선택 기간에 해당하는 보유 물량이 없습니다.');
            if (result.missingDays) {
                messages.push(`시세 미확인 ${result.missingDays}일 · ${result.missingStocks.slice(0, 5).join(', ')}`
                    + (result.missingStocks.length > 5 ? ` 외 ${result.missingStocks.length - 5}개` : ''));
            }
            if (result.progress.failed) messages.push('일부 시세 수집에 실패했습니다. 자동으로 재시도하며 다시 확인할 수 있습니다.');
            messages.push(...result.notes);
            status.textContent = messages.join(' · ');
            retryButton.hidden = result.pending || !result.missingDays;
            plot.hidden = !result.hasHoldings || !result.curve.some(point => point.value !== null);
            if (!plot.hidden) renderStatsEquityChart({ equityCurve: result.curve, currency: result.currency }, true);
            else {
                window.statsEquityChartInstance?.destroy();
                window.statsEquityChartInstance = null;
            }
            if (result.pending) statsCurveTimer = setTimeout(() => poll(), 2500);
        } catch (error) {
            if (!current() || !statsCurveVisible()) return;
            status.textContent = error.message;
            retryButton.hidden = false;
            retryButton.onclick = error.status === 409 ? () => {
                journalReads.clear();
                window.loadTradeStats();
            } : () => showStatsCurve(s, true);
        }
    };
    poll(true);
}

document.getElementById('inlineStatsContainer')?.addEventListener('click', event => {
    const button = event.target.closest('[data-stats-curve]');
    if (!button || !statsLastReport) return;
    statsCurveMode = button.dataset.statsCurve;
    showStatsCurve(statsLastReport);
});

document.addEventListener('visibilitychange', () => {
    if (document.hidden) resetStatsCurveRequest();
    else if (statsCurveMode === 'evaluated' && statsLastReport && statsCurveVisible()) showStatsCurve(statsLastReport);
});
