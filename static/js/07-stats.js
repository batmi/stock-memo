// ═══════════════════════════════════════════════════════════════════
// 07-stats.js — 통계 패널 (/api/journal/stats 결과 렌더링)
//
// ⚠️ 이 파일들은 ES 모듈이 아니라 **순서대로 로드되는 클래식 스크립트**다.
//    최상위 let/const/function 은 전역 렉시컬 환경을 공유하므로, 예전 script.js
//    한 덩어리였을 때와 실행 의미가 완전히 같다. (HTML 의 인라인 onclick 핸들러가
//    전역 함수를 그대로 부르고 있어 모듈로 바꾸면 그것들이 전부 깨진다)
//    → 실행 순서는 파일명 번호 순서다(app/utils/scriptbundle.py 가 그 순서로 이어 붙인다).
// ═══════════════════════════════════════════════════════════════════

const btnStats = document.getElementById('btnStats');
const inlineStatsContainer = document.getElementById('inlineStatsContainer');

if (btnStats && inlineStatsContainer) {
    btnStats.addEventListener('click', async () => {
        const monthlyProfitChartContainer = document.getElementById('monthlyProfitChartContainer');
        const chartDetailList = document.getElementById('chartDetailList');

        if (inlineStatsContainer.style.display === 'block') {
            inlineStatsContainer.style.display = 'none';
            syncStatsControls();
            btnStats.style.backgroundColor = 'transparent';
            btnStats.style.color = 'var(--primary-color)';
            
            // 기존 차트 다시 보이기
            if (monthlyProfitChartContainer) monthlyProfitChartContainer.style.display = 'block';
            window.renderMonthlyProfitChart();
        } else {
            inlineStatsContainer.style.display = 'block';
            btnStats.style.backgroundColor = 'var(--primary-color)';
            btnStats.style.color = '#fff';
            
            // 차트 영역 및 상세내역 숨기기 (주간 기간 이동 내비게이션도 함께 숨김)
            if (monthlyProfitChartContainer) monthlyProfitChartContainer.style.display = 'none';
            if (chartDetailList) chartDetailList.style.display = 'none';
            const chartRangeNav = document.getElementById('chartRangeNav');
            if (chartRangeNav) chartRangeNav.style.display = 'none';
            const chartEvalNotice = document.getElementById('chartEvalNotice');
            if (chartEvalNotice) chartEvalNotice.style.display = 'none';
            
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
function statsMoney(v, currency = displayCurrency) {
    const n = Number(v) || 0;
    return (n > 0 ? '+' : '') + moneyText(n, currency);
}

window.loadTradeStats = async function() {
    if (!inlineStatsContainer) return;
    syncStatsControls();
    ++statsDetailTicket;
    statsLastQuery = statsLastReport = null;
    if (window.statsEquityChartInstance) {
        window.statsEquityChartInstance.destroy();
        window.statsEquityChartInstance = null;
    }
    inlineStatsContainer.innerHTML = '<p style="text-align:center; padding: 20px; color: var(--text-muted-color);">불러오는 중...</p>';
    const ticket = journalRequests.stats = (journalRequests.stats || 0) + 1;
    const generation = journalGeneration;
    const query = { ...journalFilters('chart'), ...statsPeriod,
        granularity: window.currentChartGranularity || 'monthly' };
    try {
        const s = await journalJSON(journalUrl('stats', query));
        if (ticket !== journalRequests.stats || generation !== journalGeneration) return;
        statsLastQuery = query;
        statsLastReport = s;
        renderTradeStats(s);
    } catch (e) {
        if (ticket !== journalRequests.stats || generation !== journalGeneration) return;
        inlineStatsContainer.innerHTML = `<p role="alert" class="stats-detail-error">${escapeHtml(e.message)}</p><button type="button" id="statsRetry">다시 시도</button>`;
        document.getElementById('statsRetry').onclick = () => window.loadTradeStats();
    }
};

function statsRate(v) {
    if (v === null || v === undefined) return '—';
    return (v > 0 ? '+' : '') + v.toFixed(1) + '%';
}
function statsDays(v) {
    return (v === null || v === undefined) ? '—' : `${v.toFixed(1)}일`;
}
function statsRatio(v) {
    return (v === null || v === undefined) ? '—' : v.toFixed(2);
}

function renderTradeStats(s) {
    if (!inlineStatsContainer) return;
    if (window.statsEquityChartInstance) {
        window.statsEquityChartInstance.destroy();
        window.statsEquityChartInstance = null;
    }

    if (!s || (s.sellCount === 0 && s.buyCount === 0 && s.dividendCount === 0)) {
        inlineStatsContainer.innerHTML = (s ? statsPeriodSummary(s) + renderStatsComparison(s) + renderStatsGroups(s) : '')
            + '<p class="stats-empty">선택한 조건에 분석할 매매 기록이 없습니다.</p>'
            + '<section id="statsTradeDetails" class="stats-section" hidden aria-live="polite"></section>';
        return;
    }

    const currency = s.currency || displayCurrency;
    const strong = 'var(--text-strong-color)';
    const card = (label, value, color, sub, detail) =>
        `<div style="background: var(--bg-color); border:1px solid var(--border-color); border-radius:6px; padding:6px 4px; text-align:center;">
            <div style="font-size:9.5px; color: var(--text-muted-color); margin-bottom:2px; word-break:keep-all;">${label}</div>
            <div style="font-size:11.5px; font-weight:bold; color:${color || strong}; word-break:keep-all;">${detail ? statsDetailButton(value, { metric: detail }, label) : value}</div>
            ${sub ? `<div style="font-size:9px; color: var(--text-muted-color); margin-top:2px; word-break:keep-all;">${sub}</div>` : ''}
        </div>`;
    // ⭐️ 카드가 많아져 성격별로 묶는다 (손익 → 효율 → 위험 → 보유 습관)
    //    flex-wrap 은 줄 끝에 남은 카드가 한 줄 전체로 늘어나므로, 열 너비가 고정되는 grid 로 놓는다
    const section = (title, cards, note) =>
        `<div style="font-size:11.5px; font-weight:bold; color:${strong}; margin:14px 0 6px;">${title}</div>`
        + `<div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(88px, 1fr)); gap:8px;">${cards.join('')}</div>`
        + (note ? `<p style="font-size:11px; color:var(--text-muted-color); margin:6px 0 0; line-height:1.5;">${note}</p>` : '');
    const shortDate = d => d ? d.slice(2).replace(/-/g, '.') : '시작';

    // 분석 기간은 차트 이동과 독립적이며, 월간/주간은 표의 묶음 단위다.
    let html = statsPeriodSummary(s) + renderStatsComparison(s);

    // 손익 — 수수료·세금은 기록된 건만 더하므로, 기록이 없으면 0원이 아니라 '기록 없음'이다
    const hasCost = s.costRecordCount > 0;
    const pnlCards = [
        card('총 손익 (실현+배당)', statsMoney(s.totalPnl, currency), statsColor(s.totalPnl), '', 'pnl'),
        card('실현 손익', statsMoney(s.totalRealized, currency), statsColor(s.totalRealized), '', 'sell'),
        card('배당 수익', statsMoney(s.totalDividend, currency), statsColor(s.totalDividend), '', 'dividend'),
        card('실현 수익률 (원금 대비)', statsRate(s.realizedReturnRate), statsColor(s.realizedReturnRate || 0)),
        card('수수료·세금', hasCost ? statsMoney(-s.totalCost, currency) : '기록 없음',
            hasCost ? statsColor(-s.totalCost) : 'var(--text-muted-color)',
            hasCost ? `${s.costRecordCount.toLocaleString()}건 기준` : ''),
    ];
    if (hasCost) pnlCards.push(card('순손익 (비용 차감)', statsMoney(s.netPnl, currency), statsColor(s.netPnl)));
    // ⭐️ 매수는 빨강, 매도는 파랑 (국내 호가창 관행)
    pnlCards.push(card('총 매수금', `${moneyText(s.totalBuyAmount, currency)}`, 'var(--danger-color, #e74c3c)'));
    pnlCards.push(card('총 매도금', `${moneyText(s.totalSellAmount, currency)}`, 'var(--primary-color, #3b82f6)'));
    html += section('💰 손익', pnlCards);

    html += section('🎯 매매 효율', [
        card('승률', `${s.winRate.toFixed(1)}% (${s.winCount}승 ${s.lossCount}패)`),
        card('손익비 (평균수익÷평균손실)', statsRatio(s.payoffRatio)),
        card('Profit Factor (총이익÷총손실)', statsRatio(s.profitFactor)),
        card('거래당 기대값', s.expectancy === null ? '—' : statsMoney(s.expectancy, currency), statsColor(s.expectancy || 0)),
        card('거래당 평균 수익률', statsRate(s.avgReturnRate), statsColor(s.avgReturnRate || 0)),
        card('평균 수익 (이익 거래)', statsMoney(s.avgWin, currency), statsColor(s.avgWin), '', 'wins'),
        card('평균 손실 (손실 거래)', statsMoney(-s.avgLoss, currency), statsColor(-s.avgLoss), '', 'losses'),
    ]);

    const mddSub = s.maxDrawdownTroughDate
        ? `${shortDate(s.maxDrawdownPeakDate)} → ${shortDate(s.maxDrawdownTroughDate)}` : '';
    html += section('⚠️ 위험', [
        card('최대 낙폭 (MDD)', statsMoney(-s.maxDrawdown, currency), statsColor(-s.maxDrawdown), mddSub),
        card('최대 단일 수익', statsMoney(s.maxSingleWin, currency), statsColor(s.maxSingleWin), '', 'best'),
        card('최대 단일 손실', statsMoney(s.maxSingleLoss, currency), statsColor(s.maxSingleLoss), '', 'worst'),
        card('최대 연승 / 연패', `${s.maxWinStreak}연승 / ${s.maxLossStreak}연패`),
    ]);

    // 손실 거래를 더 오래 들고 있었다면 한 줄로 짚어 준다 (흔한 '손절 지연' 습관)
    let holdNote = '';
    if (s.avgHoldingDaysWin !== null && s.avgHoldingDaysLoss !== null && s.avgHoldingDaysLoss > s.avgHoldingDaysWin) {
        holdNote = `손실 거래를 이익 거래보다 평균 <b>${(s.avgHoldingDaysLoss - s.avgHoldingDaysWin).toFixed(1)}일</b> 더 오래 보유했습니다.`;
    }
    html += section('⏱️ 보유 습관', [
        card('평균 보유기간', statsDays(s.avgHoldingDays)),
        card('이익 거래 보유기간', statsDays(s.avgHoldingDaysWin), 'var(--danger-color, #e74c3c)'),
        card('손실 거래 보유기간', statsDays(s.avgHoldingDaysLoss), 'var(--primary-color, #3b82f6)'),
    ], holdNote);

    // 누적 실현손익 곡선 — 날짜가 이틀 이상이어야 선이 된다
    const curve = s.equityCurve || [];
    if (curve.length >= 2) {
        html += '<h4 style="font-size:13px; margin:18px 0 4px; color:var(--text-strong-color);">📈 누적 실현손익 (배당 포함)</h4>';
        if (s.maxDrawdownTroughDate) {
            html += '<div style="font-size:11px; color:var(--text-muted-color); margin-bottom:6px;"><span style="color:var(--warning-color, #f39c12); font-weight:bold;">━</span> 최대 낙폭 구간</div>';
        }
        html += '<div style="position:relative; height:180px;"><canvas id="statsEquityChart"></canvas></div>';
    }

    const thStyle = 'padding:8px; text-align:right; font-weight:bold; color:var(--text-strong-color); border-bottom:2px solid var(--border-color); white-space:nowrap;';
    const tdStyle = 'padding:7px 8px; text-align:right; border-bottom:1px solid var(--border-color); white-space:nowrap;';
    const tdLeft = tdStyle.replace('text-align:right', 'text-align:left');
    // ⭐️ 행이 길어지면 표 안에서만 스크롤되므로(아래 tableWrap) 열 이름은 고정한다.
    //    밑줄은 스크롤 시에도 남는 inset 그림자로만 그린다
    //    (border-bottom 을 같이 두면 줄이 두 개로 보인다)
    const thSticky = thStyle.replace('border-bottom:2px solid var(--border-color);', '')
        + ' position:sticky; top:0; z-index:1; background:var(--card-bg-color); box-shadow: inset 0 -2px 0 var(--border-color);';
    const thLeft = thSticky.replace('text-align:right', 'text-align:left');
    const tableWrap = '<div style="overflow:auto; max-height:340px;"><table style="width:100%; border-collapse:collapse; font-size:12.5px;"><thead><tr>';
    const winRateCell = v => (v === null || v === undefined) ? '—' : v.toFixed(0) + '%';

    // 보유기간별 성과
    if (s.holdingBuckets && s.holdingBuckets.length) {
        html += '<h4 style="font-size:13px; margin:18px 0 8px; color:var(--text-strong-color);">⏳ 보유기간별 성과</h4>';
        html += tableWrap;
        html += `<th style="${thLeft}">보유기간</th><th style="${thSticky}">매도</th><th style="${thSticky}">승률</th><th style="${thSticky}">실현손익</th><th style="${thSticky}">수익률</th></tr></thead><tbody>`;
        s.holdingBuckets.forEach(b => {
            html += `<tr><td style="${tdLeft}">${statsDetailButton(b.label, { bucket: b.label })}</td>`
                + `<td style="${tdStyle}">${b.sellCount}</td>`
                + `<td style="${tdStyle}">${winRateCell(b.winRate)}</td>`
                + `<td style="${tdStyle} color:${statsColor(b.realized)};">${statsMoney(b.realized, currency)}</td>`
                + `<td style="${tdStyle} color:${statsColor(b.returnRate || 0)};">${statsRate(b.returnRate)}</td></tr>`;
        });
        html += '</tbody></table></div>';
    }

    // 기간별 실현손익
    if (s.monthly && s.monthly.length) {
        const isWeekly = (window.currentChartGranularity === 'weekly');
        const periodTitle = isWeekly ? '📅 주간 실현손익' : '📅 월간 실현손익';
        const periodHeader = isWeekly ? '주간(시작일)' : '월';
        html += `<h4 style="font-size:13px; margin:18px 0 8px; color:var(--text-strong-color);">${periodTitle}</h4>`;
        html += tableWrap;
        html += `<th style="${thLeft}">${periodHeader}</th><th style="${thSticky}">실현손익</th><th style="${thSticky}">배당</th><th style="${thSticky}">매도</th><th style="${thSticky}">승률</th><th style="${thSticky}">매도금액</th></tr></thead><tbody>`;
        // ⭐️ 최근 기간이 위로 오도록 뒤집어 보여준다 (서버는 과거→최근 순으로 준다)
        s.monthly.slice().reverse().forEach(m => {
            html += `<tr><td style="${tdLeft}">${statsDetailButton(m.month, { periodKey: m.month })}</td>`
                + `<td style="${tdStyle} color:${statsColor(m.realized)};">${statsMoney(m.realized, currency)}</td>`
                + `<td style="${tdStyle} color:${statsColor(m.dividend)};">${statsMoney(m.dividend, currency)}</td>`
                + `<td style="${tdStyle}">${m.sellCount}</td>`
                + `<td style="${tdStyle}">${winRateCell(m.winRate)}</td>`
                + `<td style="${tdStyle}">${moneyText(m.sellAmount, currency)}</td></tr>`;
        });
        html += '</tbody></table></div>';
    }

    // 종목별 실현손익
    if (s.perStock && s.perStock.length) {
        html += '<h4 style="font-size:13px; margin:18px 0 8px; color:var(--text-strong-color);">🏷️ 종목별 실현손익</h4>';
        html += tableWrap;
        html += `<th style="${thLeft}">종목</th><th style="${thSticky}">합계(실현+배당)</th><th style="${thSticky}">수익률</th><th style="${thSticky}">매도</th><th style="${thSticky}">승률</th><th style="${thSticky}">평균 보유</th></tr></thead><tbody>`;
        s.perStock.forEach(p => {
            html += `<tr><td style="${tdLeft}">${statsDetailButton(p.stock, { identity: p.stockCode || p.stock, metric: 'pnl' })}</td>`
                + `<td style="${tdStyle} color:${statsColor(p.total)};">${statsMoney(p.total, currency)}</td>`
                + `<td style="${tdStyle} color:${statsColor(p.returnRate || 0)};">${statsRate(p.returnRate)}</td>`
                + `<td style="${tdStyle}">${p.sellCount}</td>`
                + `<td style="${tdStyle}">${p.sellCount ? p.winRate.toFixed(0) + '%' : '—'}</td>`
                + `<td style="${tdStyle}">${statsDays(p.avgHoldingDays)}</td></tr>`;
        });
        html += '</tbody></table></div>';
    }

    html += renderStatsQuality(s) + renderStatsGroups(s) + renderStatsDistribution(s) + renderStatsRecovery(s);
    html += '<section id="statsTradeDetails" class="stats-section" hidden aria-live="polite"></section>';
    html += '<p style="font-size:11px; color:var(--text-muted-color); margin-top:14px; line-height:1.5;">'
        + '※ 실현손익은 이동평균단가 방식으로 계산되며, 보유기간은 선입선출(FIFO) 기준 추정치입니다. 미실현(평가) 손익은 포함되지 않습니다.<br>'
        + '※ 수익률은 매도 수량의 평단 원금 대비이며, 매수 기록이 없는 매도는 수익률 계산에서 제외됩니다.<br>'
        + '※ 수수료·세금은 기록에 입력된 건만 합산합니다. 최대 낙폭은 누적 실현손익(배당 포함)이 고점에서 내려간 최대 금액입니다.'
        + '</p>';

    inlineStatsContainer.innerHTML = html;
    if (curve.length >= 2) renderStatsEquityChart(s);
}

// 누적 실현손익 곡선. 0원 위는 빨강, 아래는 파랑으로 칠하고 최대 낙폭 구간을 주황 선으로 겹친다.
function renderStatsEquityChart(s) {
    const canvas = document.getElementById('statsEquityChart');
    if (!canvas || typeof Chart === 'undefined') return;

    const css = getComputedStyle(document.documentElement);
    const up = css.getPropertyValue('--danger-color').trim() || '#e74c3c';
    const down = css.getPropertyValue('--primary-color').trim() || '#3b82f6';
    const warn = css.getPropertyValue('--warning-color').trim() || '#f39c12';
    const isDark = document.documentElement.getAttribute('data-theme') === 'dark';

    const curve = s.equityCurve;
    const labels = curve.map(p => p.date);
    const datasets = [{
        label: '누적 실현손익',
        data: curve.map(p => p.value),
        borderColor: up,
        borderWidth: 2,
        pointRadius: 0,
        tension: 0,
        segment: { borderColor: ctx => (ctx.p1.parsed.y < 0 ? down : up) },
    }];
    if (s.maxDrawdownTroughDate) {
        // 고점이 없으면(처음부터 내려간 경우) 곡선의 시작이 고점이다
        const from = s.maxDrawdownPeakDate || labels[0];
        const to = s.maxDrawdownTroughDate;
        datasets.push({
            label: '최대 낙폭 구간',
            data: curve.map(p => (p.date >= from && p.date <= to ? p.value : null)),
            borderColor: warn,
            borderWidth: 3,
            pointRadius: 2,
            pointBackgroundColor: warn,
            tension: 0,
            spanGaps: false,
        });
    }

    window.statsEquityChartInstance = new Chart(canvas, {
        type: 'line',
        data: { labels, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { display: false },
                tooltip: {
                    // 낙폭 선은 같은 값을 겹쳐 그린 것이라 툴팁에서는 본선만 보여준다
                    filter: item => item.datasetIndex === 0,
                    callbacks: {
                        label: ctx => `${ctx.dataset.label}: ${statsMoney(ctx.parsed.y, s.currency || displayCurrency)}`,
                    },
                },
            },
            scales: {
                y: {
                    grid: { color: isDark ? '#333333' : '#eeeeee' },
                    ticks: { callback: v => v.toLocaleString() },
                },
                x: {
                    grid: { display: false },
                    ticks: { maxTicksLimit: 6, maxRotation: 0, callback: function(v) { return this.getLabelForValue(v).slice(2, 7).replace('-', '.'); } },
                },
            },
        },
    });
}
