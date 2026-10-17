/*
 * calc.js — 화면의 보유 상태 계산 규칙 (종목 동일성 · 청산 판정)
 *
 * 포트폴리오·달력·차트의 평단·실현손익은 서버(app/services/journal.py)가 계산합니다.
 * 화면이 이 파일에서 쓰는 것은 종목 동일성(stockIdentity)과 청산 판정(isFlatQty)이고,
 * applyTradeToHolding 은 서버와 같은 이동평균단가 규칙의 기준 구현으로 남아 있습니다
 * (tests/calc.test.js 가 그 상태전이 규칙을 고정합니다. 05-data.js 의
 * ensureCalcLoaded 는 이 함수가 있는지로 calc.js 로드 여부를 판단합니다).
 *
 * ⭐️ 예전에는 여기에 computeTradeStats() 도 있었습니다. 백엔드
 *    stats.compute_trade_stats() 를 자바스크립트로 옮긴 ~180줄짜리 사본이었는데,
 *    **앱에서는 한 번도 호출되지 않았습니다** — 분석 화면은 서버 통계 API
 *    (지금은 /api/journal/stats)를 씁니다. 성과 지표의 정본은 stats.py 하나입니다.
 *
 *    화면에서 오프라인 통계가 다시 필요해지면, 사본을 만들지 말고 서버 통계 응답을
 *    캐시하는 쪽을 먼저 검토하세요.
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

    /**
     * 종목 동일성 판정 키 — 종목코드가 있으면 코드, 없으면 종목명.
     *
     * ⭐️ 이름으로 묶으면 **같은 종목이 둘로 쪼개진다.** 표기가 갈리기 때문이다:
     *    증권사가 주는 정식 명칭('KODEX 삼성그룹')과 사용자가 손으로 적어 둔 이름
     *    ('KODEX 삼성그룹 ETF')이 다르면, 봇이 밀어 넣은 체결이 기존 보유에 합쳐지지
     *    않고 별도 카드로 선다(2026-08-24 실제 사고, 종목코드는 양쪽 다 102780).
     *    반대로 코드가 다른 동명이인 종목은 한 덩어리가 된다.
     *
     *    백엔드는 이미 코드 1순위다(stats.stock_identity · entry_logic.net_holding_for_stock).
     *    화면만 이름으로 묶고 있었다 — 이 함수가 그 기준을 양쪽에 하나로 맞춘다.
     *    코드가 비어 있는 레거시 수동 기록은 종전대로 이름으로 묶인다.
     */
    function stockIdentity(entry) {
        if (!entry) return '';
        const code = (entry.stockCode == null ? '' : String(entry.stockCode)).trim().toUpperCase();
        if (code) return code;
        return (entry.stockName == null ? '' : String(entry.stockName)).trim();
    }

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


    const api = { applyTradeToHolding, isFlat, stockIdentity, EPS };

    // 브라우저: window 전역 / Node: module.exports
    if (typeof window !== 'undefined') {
        window.StockCalc = api;
        window.applyTradeToHolding = applyTradeToHolding;
        window.isFlatQty = isFlat;
        window.stockIdentity = stockIdentity;
    } else if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    } else {
        root.StockCalc = api;
        root.applyTradeToHolding = applyTradeToHolding;
        root.isFlatQty = isFlat;
        root.stockIdentity = stockIdentity;
    }
})(typeof globalThis !== 'undefined' ? globalThis : this);
