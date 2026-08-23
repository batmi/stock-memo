// ═══════════════════════════════════════════════════════════════════
// 12-market.js — 장 운영시간 판정(KRX/NXT/미국장)·현재가 조회
//
// ⚠️ 이 파일들은 ES 모듈이 아니라 **순서대로 로드되는 클래식 스크립트**다.
//    최상위 let/const/function 은 전역 렉시컬 환경을 공유하므로, 예전 script.js
//    한 덩어리였을 때와 실행 의미가 완전히 같다. (HTML 의 인라인 onclick 핸들러가
//    전역 함수를 그대로 부르고 있어 모듈로 바꾸면 그것들이 전부 깨진다)
//    → 로드 순서는 templates/stock-memo.html 의 <script> 순서가 결정한다. 바꾸지 말 것.
// ═══════════════════════════════════════════════════════════════════

let krxHolidaySet = new Set();

window.loadMarketCalendar = async function() {
    try {
        const res = await fetchWithTimeout('/api/market_calendar', {}, 8000);
        if (!res.ok) return;
        const data = await res.json();
        if (Array.isArray(data.holidays)) krxHolidaySet = new Set(data.holidays);
        // 목록이 만료된 해에는 휴장일 판정이 무의미해지므로 콘솔에 남긴다.
        const thisYear = new Date().getFullYear();
        if (data.maxYear && thisYear > data.maxYear) {
            console.warn(`[MarketCalendar] 휴장일 목록이 ${data.maxYear}년까지만 등록되어 있습니다.`);
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
    
    // 1. 한국 정규장 및 장전/NXT(장후) 시간외 포함: 평일(월~금) 08:00 ~ 20:00 (휴장일 제외)
    const isKrOpen = !isKrHoliday && (day >= 1 && day <= 5) && (timeNum >= 800 && timeNum <= 2000);
    
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
        us: isUsMarketOpen(now)
    };
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

// ⭐️ 백엔드 API를 통해 현재가와 평가금액을 가져와 DOM에 반영하는 함수
window.fetchCurrentPricesAndUpdateUI = async function(isAuto = false) {
    if (currentPortfolioArrayForPrice.length === 0) return;
    
    const displayMarket = currentMarketMode; // ⭐️ 토글된 시장 모드(KRX 또는 NXT) 사용
    
    const marketStatus = window.getMarketStatus();
    let codesToFetch = [];
    
    currentPortfolioArrayForPrice.forEach(p => {
        if (p.isClosed || !p.stockCode) return;
        
        const codeStr = String(p.stockCode).trim().toUpperCase();
        // 국가 구분 로직 (백엔드와 동일하게 적용)
        const isUS = /^[A-Z\.\-]{1,6}$/.test(codeStr);
        const isKR = (codeStr.length === 6 && /^[0-9A-Z]{6}$/.test(codeStr)) || codeStr === 'KRXGOLD' || codeStr === 'GOLD';
        
        if (isAuto) {
            if (isKR && !marketStatus.kr) return; // 한국장 닫혀있으면 건너뜀
            if (isUS && !marketStatus.us) return; // 미국장 닫혀있으면 건너뜀
            if (!isKR && !isUS && !marketStatus.kr && !marketStatus.us) return; // 기타 종목은 두 시장 모두 닫혀있을 때만 건너뜀
        }
        
        codesToFetch.push(p.stockCode);
    });
    
    codesToFetch = [...new Set(codesToFetch)];
    if (codesToFetch.length === 0) return; // ⭐️ 업데이트할 종목이 없으면 리턴 (평가액 유지를 위해 기존 UI 상태 유지)
    const requestedCodes = new Set(codesToFetch); // 아래 루프에서 종목마다 조회하므로 Set 으로 둔다
    
    try {
        // ⭐️ allow_cached: 60초 자동 폴링(isAuto)만 서버측 단기 캐시를 허용한다.
        //    수동 새로고침은 항상 false → 서버가 외부 API 를 라이브 조회하여 "진짜 현재가"를 보장.
        // ⭐️ 서버가 다단계 폴백을 도는 동안 응답이 늦어지면 60초 폴링이 겹쳐 쌓인다.
        //    다른 요청들과 동일하게 fetchWithTimeout 으로 상한을 둔다.
        const res = await fetchWithTimeout('/api/current_price', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ codes: codesToFetch, market_mode: displayMarket, allow_cached: isAuto === true })
        }, 20000);
        const prices = await res.json();
        
        let totalEval = 0;
        currentPortfolioArrayForPrice.forEach(data => {
            if (data.isClosed) return;
            
            let cp;
            let isFresh = false;
            
            // 이번 요청에 포함된 종목이면 새로 가져온 가격을 캐시에 저장
            if (requestedCodes.has(data.stockCode)) {
                cp = prices[data.stockCode];
                // ⭐️ 조회 실패(null)로 캐시를 덮지 않는다. 서버가 null 을 준다는 건 DB
                //    캐시까지 전부 실패했다는 뜻이지만, 이 화면은 직전 폴링에서 받은
                //    정상 가격을 이미 갖고 있다. 그걸 지우면 차트 탭 평가손익이 해당
                //    종목을 '빠진 종목'으로 빼버려, 일시적 네트워크 오류 한 번에
                //    멀쩡하던 손익이 사라진다.
                //    카드 자체는 cp 가 null 인 채로 '조회 실패'를 그대로 보여준다 —
                //    캐시된 옛 가격을 현재가인 척 띄우지는 않는다.
                if (Number.isFinite(cp)) window.currentPriceCache[data.stockCode] = cp;
                isFresh = true;
            } else {
                // 요청에서 제외된 종목(장 종료)은 캐시된 가격을 사용
                cp = window.currentPriceCache[data.stockCode];
            }
            
            // ⭐️ 카드 단위 식별자로 찾는다. 같은 종목의 실거래·모의 카드가 함께 있을 때
            //    data-code 로 찾으면 두 카드가 서로의 평가금액·손익을 덮어쓴다.
            const sel = `[data-pkey="${escapeAttrSelector(data.key)}"]`;
            const pEls = document.querySelectorAll(`.cp-price${sel}`);
            const eEls = document.querySelectorAll(`.cp-eval${sel}`);
            const pfEls = document.querySelectorAll(`.cp-profit${sel}`);

            if (cp !== undefined && cp !== null) {
                const evalAmount = cp * data.qty;
                const profitAmount = evalAmount - data.totalCost;
                const profitRate = data.totalCost > 0 ? (profitAmount / data.totalCost) * 100 : 0;
                
                if (isFresh) {
                    pEls.forEach(el => el.innerText = cp.toLocaleString());
                    eEls.forEach(el => el.innerText = Math.round(evalAmount).toLocaleString());
                    
                    const pColor = profitAmount > 0 ? 'var(--danger-color)' : (profitAmount < 0 ? 'var(--primary-color)' : 'var(--text-strong-color)');
                    pfEls.forEach(el => el.innerHTML = `<span style="color: ${pColor}; font-weight: bold; text-align: right; display: inline-block;"><span class="masked-amount">${profitAmount > 0 ? '+' : ''}${Math.round(profitAmount).toLocaleString()}</span><br>(${profitRate > 0 ? '+' : ''}${profitRate.toFixed(2)}%)</span>`);
                    
                    // ⭐️ 값이 새로 업데이트될 때만 카드 배경 반짝임(Flash) 애니메이션 적용
                    pEls.forEach(el => {
                        const section = el.closest('.current-price-section');
                        if (section) {
                            section.classList.remove('flash');
                            void section.offsetWidth; // 브라우저 리플로우 강제 발생
                            section.classList.add('flash');
                        }
                    });
                }
                // ⭐️ 모의투자 카드에도 현재가·평가손익은 보여주되(시세는 실제 시세다),
                //    총 평가금액 합계에는 넣지 않는다.
                if (!data.isSim) totalEval += evalAmount;
            } else {
                if (isFresh) {
                    pEls.forEach(el => el.innerText = '조회 실패');
                }
                if (!data.isSim) totalEval += data.totalCost; // 조회 실패 시 기본 투자원금으로 임시 합산
            }
        });
        
        const centerEvalEl = document.getElementById('centerTotalEvaluation');
        if (centerEvalEl) {
            animateValue(centerEvalEl, Math.round(totalEval), 1000, false);
        }
    } catch(e) { console.error("현재가 가져오기 실패", e); }
};

