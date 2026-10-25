/* node --test tests/preferences.test.js: 설정 저장 실패와 뒤따르는 저장의 상호작용 */
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
    assert.deepEqual(h.requests[1].body, { defaultView: 'list', showClosedPositions: true });
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
    assert.deepEqual(h.requests[1].body, { defaultView: 'calendar', currentMarketMode: 'AFT' });
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
    assert.deepEqual(h.requests[1].body, { showClosedPositions: false, defaultView: 'calendar' });
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
