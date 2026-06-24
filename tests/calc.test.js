/*
 * calc.js 단위 테스트 (node --test 로 실행)
 *
 * 백엔드 tests/test_backend_app.py 의 통계 테스트와 "동일한 픽스처/기대값"을
 * 사용하여, 프론트엔드 계산 엔진(calc.js)이 백엔드(stats.py)와 같은 결과를
 * 내는지(parity) 검증합니다. 이로써 손익 계산 이중화로 인한 수치 불일치를
 * 회귀 테스트로 방지합니다.
 */
const test = require('node:test');
const assert = require('node:assert');
const { computeTradeStats, applyTradeToHolding } = require('../calc.js');

function buy({ stock = 'A', qty, price, rawDate, id }) {
    return { type: 'trade', tradeType: '매수', stockName: stock, quantity: qty, price, rawDate, id };
}
function sell({ stock = 'A', qty, price, rawDate, id }) {
    return { type: 'trade', tradeType: '매도', stockName: stock, quantity: qty, price, rawDate, id };
}

test('빈 기록 → 0값 통계', () => {
    const s = computeTradeStats([]);
    assert.strictEqual(s.totalRealized, 0);
    assert.strictEqual(s.sellCount, 0);
    assert.deepStrictEqual(s.monthly, []);
});

test('실현손익/승률/손익비 (이동평균단가) — 백엔드 test_stats_realized_and_winrate 와 동일', () => {
    const rows = [
        buy({ stock: 'A', qty: 10, price: 100, rawDate: '2024-01-10T09:00', id: 1 }),
        sell({ stock: 'A', qty: 5, price: 120, rawDate: '2024-02-10T09:00', id: 2 }),
        sell({ stock: 'A', qty: 5, price: 80, rawDate: '2024-03-10T09:00', id: 3 }),
    ];
    const s = computeTradeStats(rows);
    assert.strictEqual(Math.round(s.totalRealized), 0);
    assert.strictEqual(s.sellCount, 2);
    assert.strictEqual(s.winCount, 1);
    assert.strictEqual(s.lossCount, 1);
    assert.strictEqual(Math.round(s.winRate), 50);
    assert.strictEqual(Math.round(s.avgWin), 100);
    assert.strictEqual(Math.round(s.avgLoss), 100);
    assert.strictEqual(Number(s.profitFactor.toFixed(2)), 1.0);
    assert.strictEqual(s.monthly.length, 3);
    assert.strictEqual(s.perStock[0].stock, 'A');
    assert.strictEqual(s.perStock[0].sellCount, 2);
});

test('평균 보유기간(FIFO)·배당 — 백엔드 test_stats_holding_period_and_dividend 와 동일', () => {
    const rows = [
        buy({ stock: 'B', qty: 10, price: 100, rawDate: '2024-01-01T09:00', id: 1 }),
        sell({ stock: 'B', qty: 10, price: 110, rawDate: '2024-01-11T09:00', id: 2 }),
        { type: 'trade', tradeType: '배당', stockName: 'B', price: 50, quantity: 10, rawDate: '2024-02-01T09:00', id: 3 },
    ];
    const s = computeTradeStats(rows);
    assert.strictEqual(Math.round(s.avgHoldingDays), 10);
    assert.strictEqual(Math.round(s.totalDividend), 500);
    assert.strictEqual(Math.round(s.totalPnl), Math.round(s.totalRealized + 500));
});

test('applyTradeToHolding 핵심 상태전이', () => {
    const h = { qty: 0, totalCost: 0, avgPrice: 0 };
    applyTradeToHolding(h, 10, 100, '매수');
    assert.strictEqual(h.qty, 10);
    assert.strictEqual(h.avgPrice, 100);

    applyTradeToHolding(h, 10, 200, '매수'); // 평단 = (1000+2000)/20 = 150
    assert.strictEqual(h.qty, 20);
    assert.strictEqual(h.avgPrice, 150);

    const r = applyTradeToHolding(h, 5, 180, '매도'); // 실현 = (180-150)*5 = 150
    assert.strictEqual(r.realized, 150);
    assert.strictEqual(h.qty, 15);
    assert.strictEqual(h.avgPrice, 150);

    const d = applyTradeToHolding(h, 15, 10, '배당'); // 배당 = 150
    assert.strictEqual(d.dividend, 150);

    // 전량 매도 → 청산 초기화
    applyTradeToHolding(h, 15, 160, '매도');
    assert.strictEqual(h.qty, 0);
    assert.strictEqual(h.totalCost, 0);
    assert.strictEqual(h.avgPrice, 0);
});
