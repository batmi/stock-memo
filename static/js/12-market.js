// ═══════════════════════════════════════════════════════════════════
// 12-market.js — 장 운영시간 판정(KRX/AFT/미국장)·현재가 조회
//
// ⚠️ 이 파일들은 ES 모듈이 아니라 **순서대로 로드되는 클래식 스크립트**다.
//    최상위 let/const/function 은 전역 렉시컬 환경을 공유하므로, 예전 script.js
//    한 덩어리였을 때와 실행 의미가 완전히 같다. (HTML 의 인라인 onclick 핸들러가
//    전역 함수를 그대로 부르고 있어 모듈로 바꾸면 그것들이 전부 깨진다)
//    → 실행 순서는 파일명 번호 순서다(app/utils/scriptbundle.py 가 그 순서로 이어 붙인다).
// ═══════════════════════════════════════════════════════════════════

let krxHolidaySet = new Set();

window.loadMarketCalendar = async function() {
    try {
        const res = await fetchWithTimeout('/api/market_calendar', {}, 8000);
        if (!res.ok) return;
        const data = await res.json();
        if (Array.isArray(data.holidays)) krxHolidaySet = new Set(data.holidays);
        // ⭐️ 서버가 휴장일을 계산하지 못하면(holidays 패키지 미설치 등) 공휴일에도
        //    '장중'으로 보이고 60초마다 시세를 폴링한다. 조용히 그러면 안 되므로 남긴다.
        //    (예전에는 '목록이 몇 년까지 등록됐는가'를 봤는데, 손으로 관리하던 시절의
        //     축이라 지금은 서버가 임의의 연도를 계산한다)
        if (data.available === false) {
            console.warn('[MarketCalendar] 서버가 휴장일을 계산하지 못했습니다 — 휴장일 판정이 비활성화됩니다.');
        }
    } catch (e) {
        console.warn('[MarketCalendar] 휴장일 목록을 가져오지 못했습니다:', e);
    }
};

window.getMarketStatus = function() {
    const now = new Date();
    // 브라우저 지역에 상관없이 KST(한국 표준시) 기준으로 변환하여 일관된 시간 체크
    const utc = now.getTime() + (now.getTimezoneOffset() * 60000);
    const kst = new Date(utc + (9 * 3600000));
    
    const day = kst.getDay(); // 0: 일, 1: 월, 2: 화, 3: 수, 4: 목, 5: 금, 6: 토
    const timeNum = kst.getHours() * 100 + kst.getMinutes();
    
    // ⭐️ KST 기준 YYYY-MM-DD (휴장일 대조용). toISOString 은 UTC 로 되돌리므로 쓰지 않는다.
    const kstDate = `${kst.getFullYear()}-${String(kst.getMonth() + 1).padStart(2, '0')}-${String(kst.getDate()).padStart(2, '0')}`;
    const isKrHoliday = krxHolidaySet.has(kstDate);
    
    // 1. 한국 정규장 및 프리마켓(NXT)/애프터마켓(KRX·NXT) 포함: 평일(월~금) 08:00 ~ 20:00 (휴장일 제외)
    const isKrWeekday = !isKrHoliday && (day >= 1 && day <= 5);
    const isKrOpen = isKrWeekday && (timeNum >= 800 && timeNum <= 2000);
    // ⭐️ 프리마켓(08:00~09:00)은 KRX 가 열지 않아 NXT 시세를 쓴다 — 백엔드 is_kr_pre_market 과 같은 창.
    const isKrPreMarket = isKrWeekday && (timeNum >= 800 && timeNum < 900);
    // ⭐️ 정규장~애프터마켓 개장 전(09:00~16:00). AFT 모드라도 이 동안은 KRX 시세와 같으므로
    //    버튼 라벨을 KRX 로 보여 준다. 16:00 애프터마켓이 열리면 다시 AFT.
    const isKrRegular = isKrWeekday && (timeNum >= 900 && timeNum < 1600);
    
    // 2. 미국 정규장: 뉴욕 현지 시각으로 직접 판정한다.
    //    ⭐️ 예전에는 KST 22:30~06:00 으로 고정했는데, 미국 서머타임(EDT/EST) 때문에
    //       실제 개장 시각이 KST 기준 한 시간씩 움직인다.
    //         - 서머타임(EDT): 22:30 ~ 05:00
    //         - 표준시(EST)  : 23:30 ~ 06:00
    //       고정 창은 두 경우를 모두 덮으려고 넓게 잡은 탓에, 겨울에는 22:30~23:30
    //       한 시간 동안 장이 열리지도 않았는데 60초마다 시세를 조회했다.
    //       DST 규칙을 직접 구현하는 대신 브라우저의 타임존 데이터에 맡긴다.
    return {
        kr: isKrOpen,
        krPreMarket: isKrPreMarket,
        krRegular: isKrRegular,
        us: isUsMarketOpen(now)
    };
};

// ⭐️ 저장된 토글 값을 현재 모드 이름으로 정규화한다. 'NXT' 는 KRX 애프터마켓
//    개장(2026-09-14) 전의 이름이므로 AFT 로 읽는다. 그 밖의 값은 KRX.
window.normalizeMarketMode = function(mode) {
    const m = String(mode || '').trim().toUpperCase();
    return (m === 'AFT' || m === 'NXT') ? 'AFT' : 'KRX';
};

// ⭐️ KRX/AFT 토글 버튼 표시 동기화. AFT 모드의 라벨은 그 시간대에 실제로 쓰이는 시세를
//    따른다 — 프리마켓(08~09시) NXT, 정규장~애프터 개장 전(09~16시) KRX, 그 밖에는 AFT.
//    60초 폴링마다 다시 불러 시간대에 맞춘다. 모드 값(currentMarketMode)은 바뀌지 않는다.
window.renderMarketModeButton = function() {
    const btn = document.getElementById('btnToggleMarketMode');
    if (!btn) return;
    const isAft = currentMarketMode === 'AFT';
    const status = window.getMarketStatus();
    const label = !isAft ? 'KRX'
        : status.krPreMarket ? 'NXT'
        : status.krRegular ? 'KRX'
        : 'AFT';
    btn.innerText = label;
    btn.style.backgroundColor = isAft ? 'transparent' : 'var(--primary-color)';
    btn.style.color = isAft ? 'var(--primary-color)' : '#fff';
};

// ⭐️ 뉴욕 현지 시각/요일을 구한다. Intl 타임존을 못 쓰는 환경이면 null.
function getNewYorkTime(date) {
    try {
        const parts = new Intl.DateTimeFormat('en-US', {
            timeZone: 'America/New_York', hour12: false,
            weekday: 'short', hour: '2-digit', minute: '2-digit'
        }).formatToParts(date).reduce((acc, p) => (acc[p.type] = p.value, acc), {});
        const hour = parseInt(parts.hour, 10) % 24;  // 자정을 24 로 주는 구현 대비
        const minute = parseInt(parts.minute, 10);
        if (isNaN(hour) || isNaN(minute)) return null;
        return { weekday: parts.weekday, timeNum: hour * 100 + minute };
    } catch (e) {
        return null;
    }
}

// 미국 정규장(뉴욕 평일 09:30~16:00) 여부.
// ※ 미국 공휴일(추수감사절 등)은 아직 반영하지 않는다 — 그날은 조회가 실패하고
//   서버가 직전 종가 캐시로 답한다.
function isUsMarketOpen(date) {
    const ny = getNewYorkTime(date || new Date());
    if (!ny) {
        // 타임존 데이터를 못 쓰면 예전처럼 넓은 창으로 폴백한다 (조회를 놓치지 않는 쪽)
        const utc = (date || new Date());
        const kst = new Date(utc.getTime() + (utc.getTimezoneOffset() * 60000) + (9 * 3600000));
        const d = kst.getDay(), t = kst.getHours() * 100 + kst.getMinutes();
        return ((d >= 1 && d <= 5) && t >= 2230) || ((d >= 2 && d <= 6) && t <= 600);
    }
    if (['Sat', 'Sun'].indexOf(ny.weekday) !== -1) return false;
    return ny.timeNum >= 930 && ny.timeNum < 1600;
}
window.isUsMarketOpen = isUsMarketOpen;
window.getNewYorkTime = getNewYorkTime;

// ⭐️ 하위 호환성을 위한 래퍼 함수
window.isMarketOpen = function() {
    const status = window.getMarketStatus();
    return status.kr || status.us;
};

// ⭐️ 탭이 가려진 동안에는 1분 자동 갱신을 건너뛴다. 탭을 열어 둔 채로 두면 장중 내내
//    서버가 외부 시세를 부르고 휴대폰은 배터리를 쓴다. 다시 보일 때 한 번 갱신한다.
let pricesSkippedWhileHidden = false;
document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && pricesSkippedWhileHidden) {
        pricesSkippedWhileHidden = false;
        window.fetchCurrentPricesAndUpdateUI(true);
    }
});

// Quotes are keyed by exchange, currency, code and requested session, never by code alone.
window.currentQuoteCache = new Map();
function quoteAsset(entry, currency = portfolioCurrency) {
    return { code: String(entry.stockCode || entry.code || '').trim().toUpperCase(),
        currency: String(entry.currency || currency || 'KRW').trim().toUpperCase(),
        exchange: String(entry.exchange || '').trim().toUpperCase() };
}
function quoteAssetKey(asset) {
    return JSON.stringify([asset.exchange || '', asset.currency || 'KRW', asset.code]);
}
function quoteCacheKey(asset, mode = currentMarketMode) {
    return JSON.stringify([mode, quoteAssetKey(asset)]);
}
function cachedQuote(asset, mode = currentMarketMode) {
    return window.currentQuoteCache.get(quoteCacheKey(asset, mode)) || null;
}
function rememberQuotes(quotes, mode) {
    quotes.forEach(quote => {
        const key = quoteCacheKey(quote, mode);
        const old = window.currentQuoteCache.get(key);
        // A transient failure preserves the last verified price and marks it explicitly stale.
        const value = !Number.isFinite(quote.price) && Number.isFinite(old?.price)
            ? { ...old, stale: true, status: 'stale' } : quote;
        window.currentQuoteCache.delete(key);
        window.currentQuoteCache.set(key, value);
        if (window.currentQuoteCache.size > 1000) window.currentQuoteCache.delete(window.currentQuoteCache.keys().next().value);
        // Kept for older integrations; all application calculations use the scoped quote cache.
        if (Number.isFinite(value.price)) window.currentPriceCache[value.code] = value.price;
    });
}
async function fetchAssetQuotes(assets, { mode = currentMarketMode, signal, allowCached = true } = {}) {
    const unique = [...new Map(assets.filter(asset => asset.code).map(asset => [quoteAssetKey(asset), asset])).values()];
    const result = [];
    for (let i = 0; i < unique.length; i += 100) {
        const batch = unique.slice(i, i + 100);
        const response = await fetchWithTimeout('/api/current_price', {
            method: 'POST', signal, headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ assets: batch, codes: batch.map(asset => asset.code), market_mode: mode, allow_cached: allowCached })
        }, 20000);
        if (!response.ok) throw new Error('현재가를 확인하지 못했습니다. 잠시 후 다시 확인해주세요.');
        const data = await response.json();
        batch.forEach(asset => {
            const supplied = Array.isArray(data.quotes)
                ? data.quotes.find(item => quoteAssetKey(item) === quoteAssetKey(asset)) : null;
            // Compatibility with an older server while deploying. Foreign numeric codes must not be guessed.
            const legacyAllowed = asset.currency === 'USD' && /^[A-Z.\-]{1,15}$/.test(asset.code)
                || asset.currency === 'KRW' && (/^[0-9A-Z]{6}$/.test(asset.code) || ['KRXGOLD', 'GOLD'].includes(asset.code));
            const legacy = !Array.isArray(data.quotes) && legacyAllowed ? data[asset.code] : null;
            const quote = supplied || { ...asset, price: Number.isFinite(legacy) ? legacy : null,
                asOf: null, market: mode, stale: true, status: Number.isFinite(legacy) ? 'stale' : 'unavailable' };
            result.push({ ...quote, ...asset, price: Number.isFinite(quote.price) && quote.price > 0 ? quote.price : null });
        });
    }
    return result;
}
function quoteStatusText(quote) {
    if (!Number.isFinite(quote?.price)) return '시세 미확인';
    const when = quote.asOf && !isNaN(Date.parse(quote.asOf))
        ? new Date(quote.asOf).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '확인 시각 미상';
    return `${quote.stale ? '이전 시세 · ' : ''}${quote.market || ''} · 시세 확인 ${when}`;
}
function renderPortfolioQuotes() {
    let totalEval = 0, missing = 0, stale = 0;
    currentPortfolioArrayForPrice.forEach(data => {
        if (data.isClosed) return;
        const asset = quoteAsset(data), quote = cachedQuote(asset);
        const select = `[data-pkey="${escapeAttrSelector(data.key)}"]`;
        const prices = document.querySelectorAll(`.cp-price${select}`);
        const evaluations = document.querySelectorAll(`.cp-eval${select}`);
        const profits = document.querySelectorAll(`.cp-profit${select}`);
        document.querySelectorAll(`.cp-status${select}`).forEach(element => { element.textContent = quoteStatusText(quote); });
        if (!Number.isFinite(quote?.price)) {
            prices.forEach(element => { element.textContent = '시세 미확인'; });
            [...evaluations, ...profits].forEach(element => { element.textContent = '—'; });
            if (!data.isExcluded) missing++;
            return;
        }
        const value = quote.price * data.qty, profit = value - data.totalCost;
        const rate = data.totalCost > 0 ? profit / data.totalCost * 100 : 0;
        prices.forEach(element => { element.textContent = moneyText(quote.price, asset.currency); });
        evaluations.forEach(element => { element.textContent = moneyText(value, asset.currency); });
        const color = profit > 0 ? 'var(--danger-color)' : profit < 0 ? 'var(--primary-color)' : 'var(--text-strong-color)';
        profits.forEach(element => { element.innerHTML = `<span style="color:${color};font-weight:bold;text-align:right;display:inline-block"><span class="masked-amount">${profit > 0 ? '+' : ''}${moneyText(profit, asset.currency)}</span><br>(${rate > 0 ? '+' : ''}${rate.toFixed(2)}%)</span>`; });
        if (!data.isExcluded) {
            totalEval += value;
            if (quote.stale) stale++;
        }
    });
    const total = document.getElementById('centerTotalEvaluation');
    if (total) {
        if (missing) {
            if (total.dataset.animId) cancelAnimationFrame(total.dataset.animId);
            total.textContent = '확인 필요';
        } else animateValue(total, totalEval, 1000, false, portfolioCurrency);
    }
    const status = document.getElementById('portfolioQuoteStatus');
    if (status) status.textContent = [missing ? `시세 미확인 ${missing}종목 · 총 평가금액을 확정하지 못했습니다.` : '',
        stale ? `이전 시세 ${stale}종목 포함` : ''].filter(Boolean).join(' ');
}
window.fetchCurrentPricesAndUpdateUI = async function(isAuto = false) {
    if (isAuto && document.hidden) { pricesSkippedWhileHidden = true; return; }
    const mode = currentMarketMode, portfolio = currentPortfolioArrayForPrice, generation = journalGeneration;
    const market = window.getMarketStatus();
    window.renderMarketModeButton();
    const assets = portfolio.filter(position => !position.isClosed && position.stockCode)
        .map(position => quoteAsset(position))
        .filter(asset => !isAuto || (asset.currency === 'KRW' ? market.kr : asset.currency === 'USD' ? market.us : true));
    if (!assets.length) { renderPortfolioQuotes(); return; }
    const request = beginUIRequest('portfolio-quotes');
    const current = () => request.current() && mode === currentMarketMode
        && portfolio === currentPortfolioArrayForPrice && generation === journalGeneration;
    try {
        const quotes = await fetchAssetQuotes(assets, { mode, signal: request.signal, allowCached: isAuto });
        if (!current()) return;
        rememberQuotes(quotes, mode);
    } catch (error) {
        if (!current()) return;
        rememberQuotes(assets.map(asset => ({ ...asset, price: null, asOf: null, market: mode, stale: false, status: 'unavailable' })), mode);
        console.warn('현재가 조회 실패', error);
    }
    if (!current()) return;
    renderPortfolioQuotes();
    if (document.getElementById('btnChartView')?.classList.contains('active')
            && inlineStatsContainer?.style.display !== 'block' && window.currentMonthlyChartType === 'evaluated') {
        window.renderMonthlyProfitChart();
    }
};
