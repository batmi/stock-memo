// ═══════════════════════════════════════════════════════════════════
// 07-stats.js — 통계 패널 (/api/stats 결과 렌더링)
//
// ⚠️ 이 파일들은 ES 모듈이 아니라 **순서대로 로드되는 클래식 스크립트**다.
//    최상위 let/const/function 은 전역 렉시컬 환경을 공유하므로, 예전 script.js
//    한 덩어리였을 때와 실행 의미가 완전히 같다. (HTML 의 인라인 onclick 핸들러가
//    전역 함수를 그대로 부르고 있어 모듈로 바꾸면 그것들이 전부 깨진다)
//    → 로드 순서는 templates/stock-memo.html 의 <script> 순서가 결정한다. 바꾸지 말 것.
// ═══════════════════════════════════════════════════════════════════

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
function statsMoney(v) {
    const n = Math.round(Number(v) || 0);
    return (n > 0 ? '+' : '') + n.toLocaleString() + '원';
}

window.loadTradeStats = async function() {
    if (!inlineStatsContainer) return;
    inlineStatsContainer.innerHTML = '<p style="text-align:center; padding: 20px; color: var(--text-muted-color);">불러오는 중...</p>';
    try {
        let entryIds = [];
        //  종목 필터는 이름으로 지정되지만 대조는 동일성(코드)으로 한다 — 루프 밖에서 한 번만.
        const chartStockIdentity = currentChartStock === 'all'
            ? null : identityForStockName(currentChartStock);
        cloudEntries.forEach(entry => {
            if (entry.type !== 'trade' || !entry.stockName) return;
            // ⭐️ 모의투자·'금액 계산 제외' 계좌는 실제 성과가 아니므로 통계에서 제외한다.
            //    (모의투자는 백엔드에서도 한 번 더 거른다)
            if (isExcludedFromTotals(entry)) return;
            if (currentChartStock !== 'all' && identityOf(entry) !== chartStockIdentity) return;
            if (currentChartAccount !== 'all' && (entry.tradeClass || '') !== currentChartAccount) return;
            if (currentChartBroker !== 'all' && getMappedBroker(entry.brokerAccount) !== currentChartBroker) return;
            if (currentChartSubAccount !== 'all' && getMappedSubAccount(entry.subAccount, entry.accountName) !== currentChartSubAccount) return;
            entryIds.push(entry.id);
        });
        
        // ⭐️ 분석은 차트 기간과 무관하게 '전체 기간'을 집계한다.
        //    (종목·계좌 등 필터는 그대로 적용되고, 기간만 제한하지 않는다)
        const res = await fetch('/api/stats', {
            method: 'POST',
            headers: { 
                'Content-Type': 'application/json' 
            },
            body: JSON.stringify({ 
                entry_ids: entryIds,
                granularity: window.currentChartGranularity || 'monthly',
                period_start: null,
                period_end: null
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

    // ⭐️ 분석은 차트 기간과 무관하게 전체 기록을 집계한다.
    //    (월간/주간 선택은 아래 기간별 표의 묶음 단위로만 반영된다)
    let html = '';

    // 요약 지표 카드
    html += '<div style="display:flex; flex-wrap:wrap; gap:8px; margin-bottom:16px;">';
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
    // ⭐️ 매수는 빨강, 매도는 파랑 (국내 호가창 관행)
    html += card('총 매수금', `${Math.round(s.totalBuyAmount).toLocaleString()}원`, 'var(--danger-color, #e74c3c)');
    html += card('총 매도금', `${Math.round(s.totalSellAmount).toLocaleString()}원`, 'var(--primary-color, #3b82f6)');
    html += '</div>';

    const thStyle = 'padding:8px; text-align:right; font-weight:bold; color:var(--text-strong-color); border-bottom:2px solid var(--border-color); white-space:nowrap;';
    const tdStyle = 'padding:7px 8px; text-align:right; border-bottom:1px solid var(--border-color); white-space:nowrap;';
    const tdLeft = tdStyle.replace('text-align:right', 'text-align:left');
    // ⭐️ 행이 길어지면 표 안에서만 스크롤되므로(아래 tableWrap) 열 이름은 고정한다.
    //    밑줄은 스크롤 시에도 남는 inset 그림자로만 그린다
    //    (border-bottom 을 같이 두면 줄이 두 개로 보인다)
    const thSticky = thStyle.replace('border-bottom:2px solid var(--border-color);', '')
        + ' position:sticky; top:0; z-index:1; background:var(--card-bg-color); box-shadow: inset 0 -2px 0 var(--border-color);';
    const tableWrap = '<div style="overflow:auto; max-height:340px;"><table style="width:100%; border-collapse:collapse; font-size:12.5px;"><thead><tr>';

    // 기간별 실현손익
    if (s.monthly && s.monthly.length) {
        const isWeekly = (window.currentChartGranularity === 'weekly');
        const periodTitle = isWeekly ? '📅 주간 실현손익' : '📅 월간 실현손익';
        const periodHeader = isWeekly ? '주간(시작일)' : '월';
        html += `<h4 style="font-size:13px; margin:18px 0 8px; color:var(--text-strong-color);">${periodTitle}</h4>`;
        html += tableWrap;
        html += `<th style="${thSticky.replace('text-align:right','text-align:left')}">${periodHeader}</th><th style="${thSticky}">실현손익</th><th style="${thSticky}">배당</th><th style="${thSticky}">매도금액</th></tr></thead><tbody>`;
        // ⭐️ 최근 기간이 위로 오도록 뒤집어 보여준다 (서버는 과거→최근 순으로 준다)
        s.monthly.slice().reverse().forEach(m => {
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
        html += tableWrap;
        html += `<th style="${thSticky.replace('text-align:right','text-align:left')}">종목</th><th style="${thSticky}">합계(실현+배당)</th><th style="${thSticky}">매도 횟수</th><th style="${thSticky}">승률</th></tr></thead><tbody>`;
        s.perStock.forEach(p => {
            html += `<tr><td style="${tdLeft}">${p.stock}</td>`
                + `<td style="${tdStyle} color:${statsColor(p.total)};">${statsMoney(p.total)}</td>`
                + `<td style="${tdStyle}">${p.sellCount}</td>`
                + `<td style="${tdStyle}">${p.sellCount ? p.winRate.toFixed(0) + '%' : '—'}</td></tr>`;
        });
        html += '</tbody></table></div>';
    }

    html += '<p style="font-size:11px; color:var(--text-muted-color); margin-top:14px; line-height:1.5;">'
        + '※ 실현손익은 이동평균단가 방식으로 계산되며, 보유기간은 선입선출(FIFO) 기준 추정치입니다. 미실현(평가) 손익은 포함되지 않습니다.'
        + '</p>';

    inlineStatsContainer.innerHTML = html;
}

