/*
 * calc.js — 매매 계산 단일 소스(Single Source of Truth)
 *
 * 이동평균단가(average-cost) 기반 손익 계산 로직을 한 곳에 모은 모듈입니다.
 * 기존에 프론트엔드 3곳(포트폴리오 대시보드 / 캘린더 / 월별 차트)과 백엔드
 * (stats.py)에 흩어져 있던 동일 알고리즘의 중복을 제거하기 위한 공용 엔진입니다.
 *
 * computeTradeStats() 는 백엔드 stats.compute_trade_stats() 와 동일한 결과를
 * 내도록 작성되어 있으며, tests/calc.test.js 가 백엔드 테스트와 동일한 픽스처로
 * 양쪽 일치(parity)를 검증합니다.
 *
 * 브라우저에서는 window 전역으로, Node(테스트)에서는 module.exports 로 노출됩니다.
 */
(function (root) {
    'use strict';

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
            if (holding.qty <= 0) {
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
        const EPS = 1e-9;

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

        function mget(k) {
            if (!monthly[k]) monthly[k] = { realized: 0, dividend: 0, buyAmount: 0, sellAmount: 0 };
            return monthly[k];
        }
        function sget(s) {
            if (!perStock[s]) perStock[s] = { realized: 0, dividend: 0, sellCount: 0, winCount: 0 };
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
            } else if (ttype === '매도') {
                sellCount++;
                const avg = p.avgPrice;
                const profit = (price - avg) * qty;

                totalRealized += profit;
                mget(mkey).realized += profit;
                mget(mkey).sellAmount += price * qty;
                sget(stock).realized += profit;
                sget(stock).sellCount += 1;
                if (profit > 0) {
                    winCount++;
                    grossProfit += profit;
                    sget(stock).winCount += 1;
                } else if (profit < 0) {
                    lossCount++;
                    grossLoss += -profit;
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
                if (p.qty <= EPS) {
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
        for (const [, amount] of realizedEvents) {
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

        const monthlyList = Object.keys(monthly)
            .filter(k => k !== '미상')
            .sort()
            .map(k => Object.assign({ month: k }, monthly[k]))
            .slice(-12);

        const perStockList = Object.keys(perStock).map(stock => {
            const v = perStock[stock];
            const sc = v.sellCount;
            return {
                stock,
                realized: v.realized,
                dividend: v.dividend,
                total: v.realized + v.dividend,
                sellCount: sc,
                winCount: v.winCount,
                winRate: sc ? (v.winCount / sc * 100) : 0,
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
            monthly: monthlyList,
            perStock: perStockList,
        };
    }

    const api = { applyTradeToHolding, parseEntryDt, monthKey, computeTradeStats };

    // 브라우저: window 전역 / Node: module.exports
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    } else {
        root.StockCalc = api;
        root.applyTradeToHolding = applyTradeToHolding;
        root.computeTradeStats = computeTradeStats;
    }
})(typeof globalThis !== 'undefined' ? globalThis : this);
