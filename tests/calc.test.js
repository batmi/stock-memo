/*
 * calc.js 단위 테스트 (node --test 로 실행)
 *
 * calc.js 는 화면이 보유 상태(평단·원가·실현손익)를 굴리는 유일한 경로이므로,
 * 그 상태전이 규칙을 여기서 고정한다.
 *
 * ⭐️ 예전에는 이 파일이 computeTradeStats() 도 검증했다. 그 함수는 stats.py 를
 *    자바스크립트로 옮긴 사본이었고 앱에서는 호출되지 않았으므로 삭제했다.
 *    성과 지표(승률·손익비·월별 집계)의 정본은 stats.py 하나이고,
 *    그 회귀는 tests/test_stats.py 가 본다.
 */
const test = require('node:test');
const assert = require('node:assert');
const { applyTradeToHolding, isFlat, stockIdentity, EPS } = require('../static/calc.js');

test('매수는 이동평균단가를 갱신한다', () => {
    const h = { qty: 0, totalCost: 0, avgPrice: 0 };
    applyTradeToHolding(h, 10, 100, '매수');
    assert.strictEqual(h.qty, 10);
    assert.strictEqual(h.avgPrice, 100);

    applyTradeToHolding(h, 10, 200, '매수'); // 평단 = (1000+2000)/20 = 150
    assert.strictEqual(h.qty, 20);
    assert.strictEqual(h.avgPrice, 150);
});

test('매도는 평단 기준으로 실현손익을 내고 평단은 유지한다', () => {
    const h = { qty: 20, totalCost: 3000, avgPrice: 150 };
    const r = applyTradeToHolding(h, 5, 180, '매도'); // 실현 = (180-150)*5 = 150
    assert.strictEqual(r.realized, 150);
    assert.strictEqual(h.qty, 15);
    assert.strictEqual(h.avgPrice, 150);
});

test('배당은 보유 수량을 건드리지 않는다', () => {
    const h = { qty: 15, totalCost: 2250, avgPrice: 150 };
    const d = applyTradeToHolding(h, 15, 10, '배당'); // 배당 = 150
    assert.strictEqual(d.dividend, 150);
    assert.strictEqual(h.qty, 15);
});

test('전량 매도는 보유 상태를 초기화한다', () => {
    const h = { qty: 15, totalCost: 2250, avgPrice: 150 };
    applyTradeToHolding(h, 15, 160, '매도');
    assert.strictEqual(h.qty, 0);
    assert.strictEqual(h.totalCost, 0);
    assert.strictEqual(h.avgPrice, 0);
});

test('소수점 전량 매도 후 잔여 수량이 평단을 오염시키지 않는다', () => {
    const h = { qty: 0, totalCost: 0, avgPrice: 0 };
    applyTradeToHolding(h, 0.1, 100, '매수');
    applyTradeToHolding(h, 0.1, 100, '매수');
    applyTradeToHolding(h, 0.1, 100, '매수');   // qty = 0.30000000000000004
    applyTradeToHolding(h, 0.3, 120, '매도');
    assert.strictEqual(h.qty, 0);
    assert.strictEqual(h.avgPrice, 0);
    assert.strictEqual(h.totalCost, 0);
});

test('isFlat 은 부동소수점 잔여를 청산으로 본다', () => {
    assert.strictEqual(isFlat(0), true);
    assert.strictEqual(isFlat(EPS / 2), true);
    assert.strictEqual(isFlat(0.001), false);
});


/*
 * 종목 동일성 — 화면이 '같은 종목인가'를 판정하는 단일 기준.
 *
 * [사고 · 2026-08-24] 봇이 밀어 넣은 체결의 종목명은 증권사 정식 명칭('KODEX 삼성그룹'),
 *  기존 보유 기록은 손으로 적은 이름('KODEX 삼성그룹 ETF')이었다. 종목코드는 양쪽 다
 *  102780 인데 화면이 이름으로 묶는 바람에 카드가 두 장으로 갈라졌다.
 *  백엔드(stats.stock_identity)는 처음부터 코드 1순위였다 — 화면만 뒤처져 있었다.
 */
test('종목코드가 같으면 이름이 달라도 같은 종목이다', () => {
    const bot = { stockName: 'KODEX 삼성그룹', stockCode: '102780' };
    const manual = { stockName: 'KODEX 삼성그룹 ETF', stockCode: '102780' };
    assert.strictEqual(stockIdentity(bot), stockIdentity(manual));
});

test('종목코드가 다르면 이름이 같아도 다른 종목이다', () => {
    assert.notStrictEqual(
        stockIdentity({ stockName: '동일이름', stockCode: '000001' }),
        stockIdentity({ stockName: '동일이름', stockCode: '000002' }));
});

test('코드가 없는 레거시 기록은 종전대로 이름으로 묶인다', () => {
    assert.strictEqual(stockIdentity({ stockName: '삼성전자', stockCode: '' }), '삼성전자');
    assert.strictEqual(stockIdentity({ stockName: ' 삼성전자 ' }), '삼성전자');
});

test('해외 티커는 대소문자를 가리지 않는다', () => {
    assert.strictEqual(stockIdentity({ stockName: 'Apple', stockCode: 'aapl' }),
                       stockIdentity({ stockName: 'APPLE', stockCode: 'AAPL' }));
});

test('빈 기록은 빈 키를 준다 (없는 종목을 하나로 뭉치지 않게 호출부가 거른다)', () => {
    assert.strictEqual(stockIdentity(null), '');
    assert.strictEqual(stockIdentity({}), '');
});
