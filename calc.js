/*
 * calc.js — 매매 계산 단일 소스(Single Source of Truth)
 *
 * 이동평균단가(average-cost) 기반 손익 계산 로직을 한 곳에 모은 모듈입니다.
 * 기존에 프론트엔드 3곳(포트폴리오 대시보드 / 캘린더 / 월별 차트)과 백엔드
 * (stats.py)에 흩어져 있던 동일 알고리즘의 중복을 제거하기 위한 공용 엔진입니다.
 *
 * computeTradeStats() 는 백엔드 stats.compute_trade_stats() 를 '전체 기간 + 월간
 * (granularity='monthly', 기간 필터 없음)' 조건에서 그대로 옮긴 것입니다. 백엔드에만
 * 있는 주간 집계(weekly)와 기간 필터(period_start/period_end)는 다루지 않으므로,
 * 그 두 기능이 필요하면 /api/stats 를 호출해야 합니다.
 * tests/calc.test.js 와 tests/test_stats_parity.py 가 같은 픽스처로 양쪽 일치를
 * 검증합니다. (승률·손익비의 경계값처럼 정의가 갈리기 쉬운 구간을 포함합니다)
 *
 * ⚠️ 현재 앱에서 실제로 호출되는 것은 applyTradeToHolding 뿐이고 computeTradeStats
 *    는 분석 화면이 /api/stats 를 쓰기 때문에 호출되지 않습니다. 계산식을 고칠 때는
 *    stats.py 와 함께 고쳐 parity 를 유지하세요.
 *
 * 브라우저에서는 window 전역으로, Node(테스트)에서는 module.exports 로 노출됩니다.
 */
(function (root) {
    'use strict';

    /**
     * 수량 비교용 허용 오차.
     *
     * ⭐️ 해외주식 소수점 거래에서 0.1 을 세 번 사고 0.3 을 팔면 부동소수점 잔여가
     *    5.55e-17 만큼 남는다. `qty <= 0` 으로 청산을 판정하면 이 잔여가 살아남아
     *    청산한 종목의 카드·평단·원가가 그대로 남고, 이후 재매수 시 평단이 오염된다.
     *    백엔드(stats.py)는 처음부터 EPS 로 눕히고 있어 화면과 분석 탭이 어긋났다.
     */
    const EPS = 1e-9;

    /** 사실상 청산된 수량인지 여부. 화면의 isClosed 판정도 이 함수를 쓴다. */
    function isFlat(qty) {
        return !(Number(qty) > EPS);
    }

    /**
     * 단일 거래를 보유 상태(holding)에 적용하는 핵심 상태전이 함수.
     * holding = { qty, totalCost, avgPrice } (숫자) — 이 객체를 직접 변경합니다.
     * 반환: { realized, cost, dividend }
     *   - realized : 매도 실현손익 (매수/배당 시 0)
     *   - cost     : 매도된 수량의 원가(평단×수량) (매수/배당 시 0)
     *   - dividend : 배당 수익 (매수/매도 시 0)
     *
     * 호출자는 반환값을 각자의 누적 변수(realizedProfit 등)에 더해 사용합니다.
     */
    function applyTradeToHolding(holding, qty, price, tradeType) {
        let realized = 0, cost = 0, dividend = 0;
        qty = Number(qty) || 0;
        price = Number(price) || 0;

        if (tradeType === '매수') {
            holding.qty += qty;
            holding.totalCost += price * qty;
            if (holding.qty > 0) holding.avgPrice = holding.totalCost / holding.qty;
        } else if (tradeType === '매도') {
            const avg = holding.avgPrice;
            realized = (price - avg) * qty;
            cost = avg * qty;
            holding.qty -= qty;
            holding.totalCost -= avg * qty;
            if (isFlat(holding.qty)) {
                holding.qty = 0;
                holding.totalCost = 0;
                holding.avgPrice = 0;
            }
        } else if (tradeType === '배당') {
            dividend = price * qty;
        }
        return { realized, cost, dividend };
    }

    /**
     * 기록의 거래 일시를 Date 로 파싱. rawDate 우선, 실패 시 id(밀리초)로 대체.
     * (백엔드 stats.parse_entry_dt 와 동일한 우선순위)
     */
    function parseEntryDt(entry) {
        if (entry.rawDate) {
            const d = new Date(entry.rawDate);
            if (!isNaN(d)) return d;
        }
        if (entry.id !== undefined && entry.id !== null) {
            const d = new Date(Number(entry.id));
            if (!isNaN(d)) return d;
        }
        return null;
    }

    function monthKey(dt) {
        return dt.getFullYear() + '-' + String(dt.getMonth() + 1).padStart(2, '0');
    }

    /**
     * 매매 기록 리스트로부터 성과 분석 지표를 계산.
     * 백엔드 stats.compute_trade_stats() 와 동일한 형태/값을 반환합니다.
     */
    function computeTradeStats(rows) {
        const trades = rows
            .filter(r => r.type === 'trade' && (r.stockName || '').trim())
            .slice();
        // 시간순 정렬 (날짜 없는 항목은 가장 과거로)
        trades.sort((a, b) => {
            const da = parseEntryDt(a), db = parseEntryDt(b);
            const ta = da ? da.getTime() : -Infinity;
            const tb = db ? db.getTime() : -Infinity;
            return ta - tb;
        });

        const portfolio = {};       // stock -> {qty,totalCost,avgPrice,lots:[[dt,qty]]}
        const monthly = {};         // key -> {realized,dividend,buyAmount,sellAmount}
        const perStock = {};        // stock -> {realized,dividend,sellCount,winCount}
        const realizedEvents = [];  // [dt, amount]

        let totalRealized = 0, totalDividend = 0;
        let buyCount = 0, sellCount = 0, dividendCount = 0;
        let winCount = 0, lossCount = 0;
        let grossProfit = 0, grossLoss = 0;
        let holdingDaysWeighted = 0, holdingQtyTotal = 0;
        let maxSingleWin = 0, maxSingleLoss = 0;
        let totalBuyAmount = 0, totalSellAmount = 0;

        function mget(k) {
            if (!monthly[k]) monthly[k] = { realized: 0, dividend: 0, buyAmount: 0, sellAmount: 0 };
            return monthly[k];
        }
        function sget(s) {
            if (!perStock[s]) perStock[s] = { realized: 0, dividend: 0, sellCount: 0, winCount: 0, lossCount: 0 };
            return perStock[s];
        }

        for (const t of trades) {
            const stock = t.stockName.trim();
            const qty = Number(t.quantity) || 0;
            const price = Number(t.price) || 0;
            const ttype = t.tradeType;
            const dt = parseEntryDt(t);
            const mkey = dt ? monthKey(dt) : '미상';

            if (!portfolio[stock]) portfolio[stock] = { qty: 0, totalCost: 0, avgPrice: 0, lots: [] };
            const p = portfolio[stock];

            if (ttype === '매수') {
                buyCount++;
                p.qty += qty;
                p.totalCost += price * qty;
                if (p.qty > 0) p.avgPrice = p.totalCost / p.qty;
                p.lots.push([dt, qty]);
                mget(mkey).buyAmount += price * qty;
                totalBuyAmount += price * qty;
            } else if (ttype === '매도') {
                sellCount++;
                const avg = p.avgPrice;
                const profit = (price - avg) * qty;

                totalRealized += profit;
                mget(mkey).realized += profit;
                mget(mkey).sellAmount += price * qty;
                totalSellAmount += price * qty;
                sget(stock).realized += profit;
                sget(stock).sellCount += 1;
                if (profit > maxSingleWin) maxSingleWin = profit;
                if (profit < maxSingleLoss) maxSingleLoss = profit;
                if (profit > 0) {
                    winCount++;
                    grossProfit += profit;
                    sget(stock).winCount += 1;
                } else if (profit < 0) {
                    lossCount++;
                    grossLoss += -profit;
                    sget(stock).lossCount += 1;
                }
                if (dt) realizedEvents.push([dt, profit]);

                // FIFO 로트 매칭으로 보유기간(일) 가중 합산
                let remaining = qty;
                while (remaining > EPS && p.lots.length) {
                    const lot = p.lots[0];
                    const lotDt = lot[0], lotQty = lot[1];
                    const matched = Math.min(remaining, lotQty);
                    if (lotDt && dt) {
                        holdingDaysWeighted += (dt - lotDt) / 86400000 * matched;
                        holdingQtyTotal += matched;
                    }
                    lot[1] -= matched;
                    remaining -= matched;
                    if (lot[1] <= EPS) p.lots.shift();
                }

                p.qty -= qty;
                p.totalCost -= avg * qty;
                if (isFlat(p.qty)) {
                    p.qty = 0; p.totalCost = 0; p.avgPrice = 0; p.lots = [];
                }
            } else if (ttype === '배당') {
                dividendCount++;
                const amount = price * qty;
                totalDividend += amount;
                mget(mkey).dividend += amount;
                sget(stock).dividend += amount;
                if (dt) realizedEvents.push([dt, amount]);
            }
        }

        // 누적 실현손익 곡선 및 최대 낙폭(MDD)
        realizedEvents.sort((a, b) => a[0] - b[0]);
        let cumulative = 0, peak = 0, maxDrawdown = 0;
        for (const event of realizedEvents) {
            const amount = event[1];
            cumulative += amount;
            if (cumulative > peak) peak = cumulative;
            const drawdown = peak - cumulative;
            if (drawdown > maxDrawdown) maxDrawdown = drawdown;
        }

        const decided = winCount + lossCount;
        const winRate = decided ? (winCount / decided * 100) : 0;
        const avgWin = winCount ? (grossProfit / winCount) : 0;
        const avgLoss = lossCount ? (grossLoss / lossCount) : 0;
        const profitFactor = grossLoss > 0 ? (grossProfit / grossLoss) : null;
        const avgHoldingDays = holdingQtyTotal > 0 ? (holdingDaysWeighted / holdingQtyTotal) : 0;

        // ⭐️ 백엔드와 동일하게 전체 기간을 시간순으로 반환한다. 예전에는 여기서만
        //    .slice(-12) 로 최근 12개월을 잘라내 13개월 이상이면 결과가 갈렸다.
        //    표시 구간을 자르는 일은 화면 쪽 책임이다.
        const monthlyList = Object.keys(monthly)
            .filter(k => k !== '미상')
            .sort()
            .map(k => Object.assign({ month: k }, monthly[k]));

        const perStockList = Object.keys(perStock).map(stock => {
            const v = perStock[stock];
            const sc = v.sellCount;
            const decidedS = v.winCount + v.lossCount;
            return {
                stock,
                realized: v.realized,
                dividend: v.dividend,
                total: v.realized + v.dividend,
                sellCount: sc,
                winCount: v.winCount,
                lossCount: v.lossCount,
                // 전체 승률과 같은 정의(승/(승+패)) — 손익 0 매도는 분모에서 뺀다.
                winRate: decidedS ? (v.winCount / decidedS * 100) : 0,
            };
        });
        perStockList.sort((a, b) => b.total - a.total);

        return {
            totalRealized,
            totalDividend,
            totalPnl: totalRealized + totalDividend,
            buyCount,
            sellCount,
            dividendCount,
            winCount,
            lossCount,
            winRate,
            avgWin,
            avgLoss,
            profitFactor,
            avgHoldingDays,
            maxDrawdown,
            maxSingleWin,
            maxSingleLoss,
            totalBuyAmount,
            totalSellAmount,
            monthly: monthlyList,
            perStock: perStockList,
        };
    }

    const api = { applyTradeToHolding, parseEntryDt, monthKey, computeTradeStats, isFlat, EPS };

    // 브라우저: window 전역 / Node: module.exports
    if (typeof window !== 'undefined') {
        window.StockCalc = api;
        window.applyTradeToHolding = applyTradeToHolding;
        window.computeTradeStats = computeTradeStats;
        window.isFlatQty = isFlat;
    } else if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    } else {
        root.StockCalc = api;
        root.applyTradeToHolding = applyTradeToHolding;
        root.computeTradeStats = computeTradeStats;
        root.isFlatQty = isFlat;
    }
})(typeof globalThis !== 'undefined' ? globalThis : this);
