/* node --test tests/preferences.test.js: 여러 탭·저장 큐·조회 응답 경합 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = name => fs.readFileSync(path.join(__dirname, '../static/js/', name), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));

function setup({ preferences = { defaultView: 'list' }, loaded = true } = {}) {
    const nodes = new Map(), listeners = new Map(), requests = [];
    function element(id) {
        if (!nodes.has(id)) nodes.set(id, {
            hidden: true, disabled: false, value: '', textContent: '', style: {},
            parentElement: { contains: () => true, addEventListener() {} },
            addEventListener(name, fn) { listeners.set(`${id}:${name}`, fn); },
            setAttribute() {}, focus() {},
        });
        return nodes.get(id);
    }
    const context = vm.createContext({
        window: { JOURNAL_USER_ID: 7, addEventListener() {} },
        document: {
            getElementById: element, addEventListener() {},
            documentElement: { getAttribute: () => 'dark', classList: { contains: () => false } },
        },
        userPreferences: { ...preferences }, preferencesLoaded: loaded,
        console: { warn() {}, error() {} },
        fetchWithTimeout(url, options) {
            let resolve, reject;
            const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
            requests.push({ url, options, body: JSON.parse(options.body), resolve, reject });
            return promise;
        },
    });
    // 함수 사본 없이 실제 전체 스크립트를 실행한다. 네트워크·DOM 입출력만 격리한다.
    vm.runInContext(source('05-data.js'), context);
    vm.runInContext(source('24-user-menu.js'), context);
    return {
        context, requests, element,
        save: changes => context.savePreferences(changes),
        choose(view) {
            element('accountMenuDefaultView').value = view;
            return listeners.get('accountMenuDefaultView:change')();
        },
        respond(index, status = 200) { requests[index].resolve({ ok: status >= 200 && status < 300, status }); },
    };
}

test('시작 화면 실패 뒤 필터 저장이 성공해도 실패한 시작 화면을 다시 저장하지 않는다', async () => {
    const h = setup();
    const first = h.choose('chart');
    await tick();
    assert.equal(h.element('accountMenuDefaultView').disabled, true);
    h.context.userPreferences.showClosedPositions = true;
    const second = h.save();
    assert.equal(h.requests.length, 1);
    h.respond(0, 500);
    await tick();
    assert.deepEqual(h.requests[0].body, { defaultView: 'chart' });
    assert.equal(h.requests[1].options.method, 'PATCH');
    assert.deepEqual(h.requests[1].body, { showClosedPositions: true });
    h.respond(1);
    await first;
    assert.equal(await second, true);
    assert.deepEqual(h.context.userPreferences, { defaultView: 'list', showClosedPositions: true });
    assert.equal(h.element('accountMenuDefaultView').value, 'list');
    assert.match(h.element('accountMenuStatus').textContent, /저장하지 못했습니다/);
});

test('시작 화면 성공 뒤 대기 중인 필터 저장이 새 시작 화면을 보존한다', async () => {
    const h = setup();
    const first = h.choose('calendar');
    await tick();
    h.context.userPreferences.currentMarketMode = 'AFT';
    const second = h.save();
    h.respond(0);
    await tick();
    assert.deepEqual(h.requests[1].body, { currentMarketMode: 'AFT' });
    h.respond(1);
    await first;
    assert.equal(await second, true);
    assert.equal(h.element('accountMenuDefaultView').value, 'calendar');
    assert.match(h.element('accountMenuStatus').textContent, /다음에 접속/);
    assert.equal(h.element('accountMenuDefaultView').disabled, false);
});

test('환경설정을 읽지 못한 동안 저장 요청과 로컬 패치를 모두 건너뛴다', async () => {
    const h = setup({ loaded: false, preferences: { portfolioOrder: ['005930'] } });
    assert.equal(await h.save({ defaultView: 'chart' }), false);
    await h.choose('calendar');
    assert.deepEqual(h.context.userPreferences, { portfolioOrder: ['005930'] });
    assert.equal(h.requests.length, 0);
});

test('네트워크 실패는 없던 설정 키를 복구하고 다음 재시도를 막지 않는다', async () => {
    const h = setup({ preferences: { showClosedPositions: false } });
    const first = h.choose('chart');
    await tick();
    h.requests[0].reject(new Error('network failure'));
    await first;
    assert.equal(Object.hasOwn(h.context.userPreferences, 'defaultView'), false);
    assert.equal(h.element('accountMenuDefaultView').value, 'last');
    const retry = h.choose('calendar');
    await tick();
    assert.deepEqual(h.requests[1].body, { defaultView: 'calendar' });
    h.respond(1);
    await retry;
    assert.equal(h.context.userPreferences.defaultView, 'calendar');
});

test('동일 필드의 후속 패치는 앞선 패치 실패 이후에도 순서대로 반영한다', async () => {
    const h = setup();
    const first = h.save({ defaultView: 'chart' });
    await tick();
    const second = h.save({ defaultView: 'calendar' });
    h.respond(0, 503);
    await tick();
    assert.equal(await first, false);
    assert.deepEqual(h.requests[1].body, { defaultView: 'calendar' });
    h.respond(1);
    assert.equal(await second, true);
    assert.equal(h.context.userPreferences.defaultView, 'calendar');
});

test('다른 탭의 오래된 설정으로 필터를 바꿔도 첫 탭의 시작 화면을 덮어쓰지 않는다', async () => {
    const initial = { defaultView: 'list', showClosedPositions: false, extension: { enabled: true } };
    const first = setup({ preferences: initial }), second = setup({ preferences: initial });
    const a = first.save({ defaultView: 'chart' });
    await tick();
    first.respond(0);
    await a;
    second.context.userPreferences.showClosedPositions = true;
    const b = second.save();
    await tick();
    assert.deepEqual(second.requests[0].body, { showClosedPositions: true });
    const server = { ...initial, ...first.requests[0].body, ...second.requests[0].body };
    assert.deepEqual(server, { defaultView: 'chart', showClosedPositions: true, extension: { enabled: true } });
    second.respond(0);
    await b;
});

test('연속된 동일 필드 저장이 모두 실패하면 마지막 확인된 서버값으로 복구한다', async () => {
    const h = setup();
    const first = h.save({ defaultView: 'chart' });
    const second = h.save({ defaultView: 'calendar' });
    await tick();
    h.respond(0, 503);
    await tick();
    assert.equal(h.context.userPreferences.defaultView, 'calendar');
    h.respond(1, 503);
    assert.equal(await first, false);
    assert.equal(await second, false);
    assert.equal(h.context.userPreferences.defaultView, 'list');
    assert.equal(await h.save(), true);
    assert.equal(h.requests.length, 2);
});

test('같은 필드의 앞선 저장이 성공했다면 다음 저장 실패는 그 성공값으로 복구한다', async () => {
    const h = setup();
    const first = h.save({ defaultView: 'chart' });
    const second = h.save({ defaultView: 'calendar' });
    await tick();
    h.respond(0);
    await tick();
    assert.equal(await first, true);
    assert.equal(h.context.userPreferences.defaultView, 'calendar');
    h.respond(1, 500);
    assert.equal(await second, false);
    assert.equal(h.context.userPreferences.defaultView, 'chart');
});

test('재조회 시작 뒤 저장을 완료해도 늦게 받은 GET이 성공한 설정을 되돌리지 않는다', async () => {
    const h = setup();
    let deliverPreferences;
    const staleGet = new Promise(resolve => { deliverPreferences = resolve; });
    Object.assign(h.context, {
        historyList: null, priceUpdateInterval: null, newsInterval: null,
        Event: class { constructor(type) { this.type = type; } },
        setInterval: () => 1, clearInterval() {},
        ensureCalcLoaded: async () => {},
        initialFetchOrFresh(name) {
            return name === 'pref' ? staleGet : Promise.resolve({ ok: true, status: 200, json: async () => ({}) });
        },
        setJournalMeta() {}, beginInitialViews() {}, endInitialViews() {},
        displayEntries: async () => {}, fetchRealtimeNews() {},
    });
    h.context.console.log = () => {};
    h.context.window.dispatchEvent = () => {};
    h.context.window.restoreAccountStartView = () => {};
    const load = h.context.loadDataFromLocal();
    await tick();
    const save = h.save({ defaultView: 'chart' });
    await tick();
    h.respond(0);
    assert.equal(await save, true);
    deliverPreferences({ ok: true, json: async () => ({ defaultView: 'list', extension: 'from server' }) });
    await load;
    assert.equal(h.context.window.__dataLoadFailed, false);
    assert.equal(h.context.userPreferences.defaultView, 'chart');
    assert.equal(h.context.userPreferences.extension, 'from server');
    h.context.userPreferences.showClosedPositions = true;
    const filter = h.save();
    await tick();
    assert.deepEqual(h.requests[1].body, { showClosedPositions: true });
    h.respond(1);
    await filter;
});

test('저장 중 시작한 GET은 저장 완료 뒤 도착하더라도 해당 키를 보호한다', async () => {
    const h = setup();
    const save = h.save({ defaultView: 'chart' });
    await tick();
    const read = h.context.preferenceReadContext();
    h.respond(0);
    await save;
    h.context.applyLoadedPreferences({ defaultView: 'list', extension: 3 }, read);
    assert.equal(h.context.userPreferences.defaultView, 'chart');
    assert.equal(h.context.userPreferences.extension, 3);
});

test('재조회 도중 진행 중인 저장이 실패하면 조회 전 확인값으로 복구한다', async () => {
    const h = setup();
    const save = h.save({ defaultView: 'chart' });
    await tick();
    h.context.applyLoadedPreferences({ defaultView: 'list', extension: 3 }, h.context.preferenceReadContext());
    assert.equal(h.context.userPreferences.defaultView, 'chart');
    h.respond(0, 500);
    assert.equal(await save, false);
    assert.equal(h.context.userPreferences.defaultView, 'list');
    assert.equal(h.context.userPreferences.extension, 3);
});

test('잘못된 응답은 미로드 가드를 유지하고 손상된 개별 설정만 제외한다', async () => {
    const h = setup({ loaded: false, preferences: {} });
    assert.throws(() => h.context.applyLoadedPreferences([]), /응답 형식/);
    assert.equal(await h.save({ defaultView: 'chart' }), false);
    h.context.applyLoadedPreferences({ portfolioOrder: {}, defaultView: 'wrong', showClosedPositions: 'false', currentFilterStock: [], extension: { value: 1 } });
    assert.deepEqual(JSON.parse(JSON.stringify(h.context.userPreferences)), { extension: { value: 1 } });
    const save = h.save({ defaultView: 'chart' });
    await tick();
    assert.deepEqual(h.requests[0].body, { defaultView: 'chart' });
    h.respond(0);
    assert.equal(await save, true);
});

test('키 삭제는 null 패치로 보내고 실패하면 원래 키를 복구한다', async () => {
    const h = setup({ preferences: { portfolioOrder: ['005930'], extension: { value: 1 } } });
    delete h.context.userPreferences.portfolioOrder;
    const save = h.save();
    await tick();
    assert.deepEqual(h.requests[0].body, { portfolioOrder: null });
    h.respond(0, 500);
    assert.equal(await save, false);
    assert.deepEqual(Array.from(h.context.userPreferences.portfolioOrder), ['005930']);
    assert.equal(h.context.userPreferences.extension.value, 1);
});
