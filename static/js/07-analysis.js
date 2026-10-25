// 분석의 기간 선택·추가 지표·근거 거래. 본문은 거래를 펼칠 때만 조회한다.
let statsPeriod = { from: '', to: '' };
let statsLastQuery = null;
let statsLastReport = null;
let statsDetailTicket = 0;

function syncStatsControls() {
    const form = document.getElementById('statsPeriodForm');
    if (form) form.hidden = document.getElementById('inlineStatsContainer')?.style.display !== 'block';
}

function statsPresetBounds(preset) {
    const today = new Date().toLocaleDateString('sv-SE', { timeZone: 'Asia/Seoul' });
    if (preset === 'all') return { from: '', to: '' };
    if (preset === 'month') return { from: today.slice(0, 7) + '-01', to: today };
    if (preset === 'year') return { from: today.slice(0, 4) + '-01-01', to: today };
    const end = new Date(today + 'T00:00:00Z');
    const start = new Date(Date.UTC(end.getUTCFullYear(), end.getUTCMonth() - 3, 1));
    const lastDay = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth() + 1, 0)).getUTCDate();
    start.setUTCDate(Math.min(end.getUTCDate(), lastDay) + 1);
    return { from: start.toISOString().slice(0, 10), to: today };
}

document.getElementById('statsPeriodPreset')?.addEventListener('change', event => {
    const custom = event.target.value === 'custom';
    document.getElementById('statsCustomPeriod').hidden = !custom;
    document.getElementById('statsPeriodError').textContent = '';
    if (custom) {
        const fallback = statsPresetBounds('month');
        document.getElementById('statsFrom').value = statsPeriod.from || fallback.from;
        document.getElementById('statsTo').value = statsPeriod.to || fallback.to;
    } else {
        statsPeriod = statsPresetBounds(event.target.value);
        window.loadTradeStats();
    }
});

document.getElementById('statsPeriodForm')?.addEventListener('submit', event => {
    event.preventDefault();
    const from = document.getElementById('statsFrom').value, to = document.getElementById('statsTo').value;
    const error = document.getElementById('statsPeriodError');
    if (!from || !to || from > to) {
        error.textContent = '시작일과 종료일을 올바른 순서로 입력해주세요.';
        return;
    }
    error.textContent = '';
    statsPeriod = { from, to };
    window.loadTradeStats();
});

function statsDetailButton(label, options = {}, title = '') {
    const attr = value => escapeAttr(escapeHtml(value));
    return `<button type="button" class="stats-link" data-stats-detail="${attr(JSON.stringify(options))}"`
        + ` data-stats-title="${attr(title || label)}">${escapeHtml(label)}</button>`;
}

function statsPeriodSummary(s) {
    const current = s.period?.from ? `${s.period.from} ~ ${s.period.to}` : '전체 기간';
    return `<p class="stats-caption">${escapeHtml(current)} · 매도 체결 기준`
        + ' <span class="stats-muted">(분할 체결은 각각 1건)</span></p>';
}

function renderStatsComparison(s) {
    const p = s.comparison;
    if (!p) return '';
    const money = v => moneyText(v, s.currency);
    const rate = v => v == null ? '—' : v.toFixed(1) + '%';
    const winRate = v => v.winCount + v.lossCount ? v.winRate : null;
    const rows = [
        ['손익 (실현+배당)', s.totalPnl, p.metrics.totalPnl, money, 'money'],
        ['승률', winRate(s), winRate(p.metrics), rate, 'rate'],
        ['매도당 기대값', s.expectancy, p.metrics.expectancy, money, 'money'],
        ['실현 수익률', s.realizedReturnRate, p.metrics.realizedReturnRate, rate, 'rate'],
        ['매도 건수', s.sellCount, p.metrics.sellCount, v => v.toLocaleString() + '건', 'count'],
    ];
    return `<details class="stats-section" open><summary>직전 기간과 비교</summary>`
        + `<p class="stats-muted">이전: ${p.from} ~ ${p.to} · 같은 일수와 통화로 비교</p>`
        + (!p.tradeCount ? '<p class="stats-muted">이전 기간에 해당하는 거래가 없습니다. 금액·건수는 0, 정의되지 않는 비율은 —로 표시합니다.</p>' : '')
        + '<div class="stats-table-wrap"><table class="stats-table"><thead><tr><th>지표</th><th>선택 기간</th><th>이전 기간</th><th>차이</th></tr></thead><tbody>'
        + rows.map(([label, current, previous, format, kind]) => {
            const delta = current == null || previous == null ? null : current - previous;
            const deltaText = delta == null ? '—' : (delta > 0 ? '+' : '')
                + (kind === 'rate' ? delta.toFixed(1) + '%p' : format(delta));
            return `<tr><th>${label}</th><td>${current == null ? '—' : format(current)}</td>`
                + `<td>${previous == null ? '—' : format(previous)}</td><td>${deltaText}</td></tr>`;
        }).join('') + '</tbody></table></div></details>';
}

function renderStatsQuality(s) {
    const q = s.quality;
    if (!q) return '';
    const items = [statsDetailButton(`대상 ${q.tradeCount}건`, { metric: 'all' })];
    if (q.reviewCount) items.push(statsDetailButton(`검토 필요 ${q.reviewCount}건`, { metric: 'review' }));
    if (q.missingBuyCount) items.push(statsDetailButton(`매수 이력 부족 ${q.missingBuyCount}건`, { metric: 'missing-buy' }));
    const review = q.reviewCount ? ` · 검토 필요 ${q.reviewCount}건` : '';
    return `<details class="stats-section" id="statsQuality"><summary>데이터 상태 · ${q.tradeCount}건${review}</summary>`
        + `<div class="stats-actions">${items.join('')}</div>`
        + `<p>수수료·세금 모두 입력: ${q.completeCostCount}/${q.tradeCount}건`
        + `${q.costCoverage == null ? '' : ` (${q.costCoverage.toFixed(1)}%)`}</p>`
        + `<p>일부만 입력: ${q.partialCostCount}건 · 모두 미입력: ${q.missingCostCount}건 `
        + (q.partialCostCount + q.missingCostCount ? statsDetailButton('비용 미완료 거래', { metric: 'missing-cost' }) : '') + '</p>'
        + '<p class="stats-muted">미입력 비용은 0원으로 확인된 비용과 구분합니다. 비용 차감 후로 저장된 매도 손익에서는 같은 매도 비용을 다시 빼지 않습니다. 저장 손익의 비용 기준을 모르면 순손익을 확정하지 않습니다.</p>'
        + `<p>체결 이력으로 재구성한 손익: ${q.reconstructedRealizedCount || 0}건</p>`
        + (q.missingRealizedCount ? `<p class="stats-muted">손익 미확정 ${q.missingRealizedCount}건은 실현손익 합계에서 제외됩니다. 매수 이력을 보완하거나 확정 손익을 동기화해주세요.</p>` : '')
        + (q.missingBuyCount || q.zeroBasisCount ? `<p class="stats-muted">매수 이력이 부족해도 저장된 손익이 있으면 합산합니다. 매도 원가가 0인 ${q.zeroBasisCount}건은 수익률에서 제외됩니다.</p>` : '')
        + '</details>';
}

function renderStatsGroups(s) {
    if (!s.groups) return '';
    const table = (label, rows, groupBy) => `<h4>${label}</h4><div class="stats-table-wrap"><table class="stats-table">`
        + '<thead><tr><th>구분</th><th>손익 (실현+배당)</th><th>매도</th><th>승률</th><th>실현 수익률</th><th>평균 보유</th><th>근거</th></tr></thead><tbody>'
        + rows.map(g => `<tr><th>${escapeHtml(g.label)}${groupBy === 'accountKey' && (g.detail || g.key) && (g.detail || g.key) !== g.label ? `<small>${escapeHtml(g.detail || g.key)}</small>` : ''}</th>`
            + `<td>${statsMoney(g.totalPnl, g.currency)}</td><td>${g.sellCount}</td>`
            + `<td>${g.winRate == null ? '—' : g.winRate.toFixed(1) + '%'}</td><td>${statsRate(g.returnRate)}</td>`
            + `<td>${statsDays(g.avgHoldingDays)}</td><td>${statsDetailButton('거래 보기', { groupBy, groupKey: g.key, currency: g.currency }, label + ' · ' + g.label)}</td></tr>`).join('')
        + '</tbody></table></div>';
    return '<details class="stats-section" id="statsGroups"><summary>분류·계좌 비교</summary>'
        + '<p class="stats-muted">현재 종목·분류·계좌 조건에 해당하는 거래입니다. 분류는 매도 기록 기준이며, 서로 다른 통화는 별도로 계산합니다.</p>'
        + table('투자 분류', s.groups.classes, 'tradeClass')
        + table('계좌', s.groups.accounts, 'accountKey') + '</details>';
}

function renderStatsDistribution(s) {
    const d = s.distribution;
    if (!d?.sellCount) return '';
    const maxCount = Math.max(...d.bins.map(b => b.count));
    // 구간 경계가 소수이면 원화도 정수로 반올림하지 않아 구간을 구별할 수 있게 한다.
    const boundary = v => Number(v).toLocaleString(undefined, { maximumSignificantDigits: 8 })
        + (s.currency === 'KRW' ? '원' : ` ${s.currency}`);
    return '<details class="stats-section" id="statsDistribution"><summary>손익 분포·수익 집중도</summary>'
        + `<div class="stats-facts"><span>매도 손익 중앙값 <b>${statsMoney(d.median, s.currency)}</b></span>`
        + `<span>총이익 중 상위 3건 비중 <b>${d.top3WinShare == null ? '—' : d.top3WinShare.toFixed(1) + '%'}</b></span>`
        + `<span>최대 수익 1건을 제외한 실현손익 <b>${statsMoney(d.withoutBestWin, s.currency)}</b></span></div>`
        + '<p class="stats-muted">매도 체결별 실현손익 기준입니다. 배당과 수수료·세금은 분포에 포함하지 않습니다.</p>'
        + '<div class="stats-histogram" role="img" aria-label="매도 실현손익 구간별 거래 건수">'
        + d.bins.map(b => `<div class="stats-bin"><span>${boundary(b.from)}${b.from === b.to ? '' : ` ~ ${boundary(b.to)}`}</span>`
            + `<span class="stats-bar-track"><span class="stats-bar" style="width:${b.count / maxCount * 100}%"></span></span><b>${b.count}건</b></div>`).join('')
        + '</div><p class="stats-muted">각 구간은 하한 포함·상한 미포함이며 마지막 구간은 상한도 포함합니다.</p></details>';
}

function renderStatsRecovery(s) {
    const r = s.recovery;
    if (!r?.asOf) return '';
    const recovered = !s.maxDrawdown ? '낙폭 없음' : r.maxRecoveryDate
        ? `${r.maxRecoveryDate} (${r.maxRecoveryDays}일)` : '분석 종료일까지 미회복';
    return '<details class="stats-section" id="statsRecovery"><summary>낙폭 회복 분석</summary>'
        + `<div class="stats-facts"><span>최대 낙폭 저점 → 회복 <b>${recovered}</b></span>`
        + `<span>종료 시점 미회복 금액 <b>${moneyText(r.currentDrawdown, s.currency)}</b></span>`
        + `<span>최근 고점 이후 미회복 기간 <b>${r.currentUnderwaterDays}일</b></span></div>`
        + `<p class="stats-muted">${r.asOf} 기준 · 배당 포함·비용 차감 전 누적 실현손익의 낙폭입니다. 계좌 전체 자산의 낙폭은 아닙니다.</p></details>`;
}

async function openStatsTrades(options, title) {
    if (!statsLastReport || !statsLastQuery) return;
    const target = document.getElementById('statsTradeDetails');
    if (!target) return;
    const ticket = ++statsDetailTicket, generation = journalGeneration;
    const query = { ...statsLastQuery, currency: statsLastReport.currency, ...options,
        version: statsLastReport.version, limit: 30 };
    let nextOffset = 0, items = [];
    target.hidden = false;
    target.innerHTML = '<p>거래를 불러오는 중...</p>';
    target.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    const current = () => ticket === statsDetailTicket && generation === journalGeneration && target.isConnected;
    async function loadPage() {
        const nextButton = target.querySelector('[data-stats-next]');
        if (nextButton) nextButton.disabled = true;
        try {
            const data = await journalJSON(journalUrl('stats/trades', { ...query, offset: nextOffset }));
            if (!current()) return;
            items.push(...data.items);
            nextOffset = data.nextOffset;
            target.innerHTML = `<div class="stats-detail-heading"><h4>${escapeHtml(title)} · ${data.total}건</h4>`
                + '<button type="button" data-stats-close>닫기</button></div>'
                + (items.length ? '<div class="stats-table-wrap"><table class="stats-table"><thead><tr><th>거래일 / 종목</th><th>계좌 / 분류</th><th>거래 / 수량</th><th>단가</th><th>매도 원가</th><th>손익</th><th>수수료 / 세금</th><th>보유기간</th><th>기록</th></tr></thead><tbody>'
                    + items.map(f => `<tr><th>${escapeHtml(f.date || '미상')}<small>${escapeHtml(f.stock)}</small></th>`
                        + `<td>${escapeHtml(f.account)}<small>${escapeHtml(f.tradeClass || '미분류')}</small></td>`
                        + `<td>${escapeHtml(f.tradeType)} ${f.quantity.toLocaleString()}주</td><td>${moneyText(f.price, f.currency)}</td>`
                        + `<td>${f.tradeType === '매도' ? moneyText(f.basis, f.currency) : '—'}</td>`
                        + `<td>${f.tradeType === '매수' ? '—' : f.realizedSource === 'unavailable' ? '미확정' : statsMoney(f.realized + f.dividend, f.currency)}${f.realizedSource === 'reconstructed' ? '<small>체결 이력 재구성</small>' : f.realizedSource === 'recorded' ? '<small>저장 손익</small>' : ''}${f.missingBuyQuantity > 1e-9 ? '<small>매수 이력 부족</small>' : ''}${f.needsReview ? '<small>검토 필요</small>' : ''}</td>`
                        + `<td>${f.fee == null ? '미입력' : moneyText(f.fee, f.currency)}<small>${f.tax == null ? '미입력' : moneyText(f.tax, f.currency)}</small></td>`
                        + `<td>${statsDays(f.holdingDays)}</td><td><button type="button" class="stats-link" data-stats-entry="${Number(f.id)}">메모 보기</button></td></tr>`
                        + `<tr class="stats-note-row" hidden data-stats-note="${Number(f.id)}"><td colspan="9"></td></tr>`).join('')
                    + '</tbody></table></div>' : '<p class="stats-muted">해당 거래가 없습니다.</p>')
                + (nextOffset !== null ? '<button type="button" data-stats-next>더 보기</button>' : '');
            target.querySelector('[data-stats-close]').onclick = () => { ++statsDetailTicket; target.hidden = true; };
            const next = target.querySelector('[data-stats-next]');
            if (next) next.onclick = loadPage;
        } catch (error) {
            if (!current()) return;
            if (!items.length) target.innerHTML = '';
            target.querySelector('.stats-detail-error')?.remove();
            const message = document.createElement('p');
            message.className = 'stats-detail-error';
            message.setAttribute('role', 'alert');
            message.textContent = error.message + ' ';
            const retry = document.createElement('button');
            retry.type = 'button';
            retry.textContent = error.status === 409 ? '분석 새로고침' : '다시 시도';
            retry.onclick = error.status === 409 ? () => window.loadTradeStats() : loadPage;
            message.append(retry);
            target.append(message);
            if (nextButton) nextButton.disabled = false;
        }
    }
    await loadPage();
}

document.getElementById('inlineStatsContainer')?.addEventListener('click', async event => {
    const link = event.target.closest('[data-stats-detail]');
    if (link) {
        await openStatsTrades(JSON.parse(link.dataset.statsDetail), link.dataset.statsTitle);
        return;
    }
    const noteButton = event.target.closest('[data-stats-entry]');
    if (!noteButton) return;
    const note = document.querySelector(`[data-stats-note="${Number(noteButton.dataset.statsEntry)}"]`);
    note.hidden = !note.hidden;
    noteButton.textContent = note.hidden ? '메모 보기' : '메모 닫기';
    if (note.hidden) return;
    // 표 오른쪽의 버튼을 누른 뒤 왼쪽에서 시작하는 메모를 바로 읽을 수 있게 한다.
    const tableWrap = note.closest('.stats-table-wrap');
    if (tableWrap) tableWrap.scrollLeft = 0;
    if (note.dataset.loaded) return;
    const body = note.firstElementChild;
    body.textContent = '메모를 불러오는 중...';
    try {
        const entry = await journalJSON(`/api/entry/${Number(noteButton.dataset.statsEntry)}`, false);
        if (!note.isConnected) return;
        body.innerHTML = entry.thoughts ? sanitizeHtml(entry.thoughts) : '작성한 메모가 없습니다.';
        note.dataset.loaded = '1';
    } catch (error) {
        if (note.isConnected) body.textContent = error.message + ' 닫았다가 다시 열면 재시도합니다.';
    }
});
