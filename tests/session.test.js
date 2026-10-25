/* node --test tests/session.test.js: 서버 세션 동기화와 중복·지연 응답의 경계 검사 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../static/js/03-session.js'), 'utf8');

function deferred() {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
}

function setup({ keep = false, remaining = 120000 } = {}) {
    const clock = { now: 1800000000000 };
    const listeners = {}, requests = [], events = [], timers = new Map();
    let timerId = 0;
    const modal = { style: { display: 'none' }, classList: { remove() {} } };
    const document = {
        visibilityState: 'visible',
        getElementById: id => id === 'sessionExtensionModalOverlay' ? modal : { innerText: '' },
        addEventListener: (name, handler) => { listeners[name] = handler; },
    };
    const window = {
        SESSION_EXPIRES_AT_MS: clock.now + remaining, SESSION_KEEP_LOGGED_IN: keep,
        JOURNAL_USERNAME: 'trader', JOURNAL_USER_ID: 7, location: { href: '' },
        dispatchEvent: event => events.push(event),
    };
    const context = vm.createContext({
        window, document, Date: { now: () => clock.now },
        console: { warn() {} },
        CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
        setTimeout: (callback, delay) => { timers.set(++timerId, { callback, delay }); return timerId; },
        clearTimeout: id => timers.delete(id), setInterval: () => ++timerId, clearInterval() {},
        fetchWithTimeout: (url, options) => {
            const item = { url, options, ...deferred() };
            requests.push(item);
            return item.promise;
        },
        loadDataFromLocal() {},
    });
    vm.runInContext(source, context);
    const payload = (extra = {}) => ({ username: 'trader', user_id: 7, expires_at: (clock.now + 3600000) / 1000,
        keep_logged_in: keep, ...extra });
    const respond = (index, body, status = 200) => requests[index].resolve({
        status, ok: status >= 200 && status < 300, json: async () => body,
    });
    return { window, document, clock, requests, events, timers, modal, payload, respond,
        visible: () => listeners.visibilitychange() };
}

test('연장은 요청을 공유하고 서버 시각·타이머·경고창·메뉴 이벤트를 함께 갱신한다', async () => {
    const h = setup();
    assert.equal(h.modal.style.display, 'flex');
    const first = h.window.extendCurrentSession();
    assert.equal(first, h.window.extendCurrentSession());
    assert.equal(h.requests.length, 1);
    assert.equal(h.requests[0].url, '/api/ping');
    h.respond(0, h.payload({ status: 'success' }));
    const state = await first;
    assert.equal(state.expiresAtMs, h.clock.now + 3600000);
    assert.equal(h.window.getSessionState().expiresAtMs, state.expiresAtMs);
    assert.equal(h.modal.style.display, 'none');
    assert.equal(h.events.at(-1).type, 'session-state-changed');
    assert.equal(h.events.at(-1).detail.expiresAtMs, state.expiresAtMs);
    assert.ok([...h.timers.values()].some(timer => timer.delay === 3600000));
});

test('로그인 유지 세션은 연장을 요청하지 않는다', async () => {
    const h = setup({ keep: true, remaining: 86400000 });
    await assert.rejects(h.window.extendCurrentSession(), { code: 'SESSION_NOT_EXTENDABLE' });
    assert.equal(h.requests.length, 0);
    assert.equal(h.modal.style.display, 'none');
});

test('연장 실패는 기존 만료 시각과 경고창을 유지하고 재시도를 허용한다', async () => {
    const h = setup();
    const oldExpiry = h.window.getSessionState().expiresAtMs;
    const attempt = h.window.extendCurrentSession();
    h.respond(0, { error: 'unavailable' }, 503);
    await assert.rejects(attempt, { code: 'SESSION_REQUEST_FAILED' });
    assert.equal(h.window.getSessionState().expiresAtMs, oldExpiry);
    assert.equal(h.modal.style.display, 'flex');
    const retry = h.window.extendCurrentSession();
    assert.equal(h.requests.length, 2);
    h.respond(1, h.payload({ status: 'success' }));
    await retry;
    assert.equal(h.modal.style.display, 'none');
});

test('네트워크 실패도 상태를 유지하고 사용자가 재시도할 수 있게 전달한다', async () => {
    const h = setup();
    const expiry = h.window.getSessionState().expiresAtMs;
    const attempt = h.window.extendCurrentSession();
    h.requests[0].reject(new Error('Network failed'));
    await assert.rejects(attempt, error => error.code === 'SESSION_REQUEST_FAILED' && error.message.includes('연결 상태'));
    assert.equal(h.window.getSessionState().expiresAtMs, expiry);
    assert.equal(h.modal.style.display, 'flex');
});

test('401은 만료 안내 로그인 경로로 이동한다', async () => {
    const h = setup();
    const attempt = h.window.extendCurrentSession();
    h.respond(0, { error: 'Unauthorized' }, 401);
    await assert.rejects(attempt, { code: 'SESSION_EXPIRED' });
    assert.equal(h.window.location.href, '/logout?timeout=1');
});

for (const bad of [null, { status: 'success' }, { user_id: '7' }, { expires_at: '1800000000' },
    { expires_at: NaN }, { expires_at: 1e15 }, { expires_at: (1800000000000 + 86400000) / 1000 },
    { keep_logged_in: 1 }]) {
    test(`잘못된 연장 응답은 상태에 적용하지 않는다: ${JSON.stringify(bad)}`, async () => {
        const h = setup();
        const oldExpiry = h.window.getSessionState().expiresAtMs;
        const attempt = h.window.extendCurrentSession();
        h.respond(0, bad === null ? null : (bad.status ? bad : h.payload({ status: 'success', ...bad })));
        await assert.rejects(attempt, { code: 'INVALID_SESSION' });
        assert.equal(h.window.getSessionState().expiresAtMs, oldExpiry);
        assert.equal(h.events.length, 0);
        assert.equal(h.modal.style.display, 'flex');
    });
}

test('로그인 유지 세션도 24시간 만료되면 자동으로 로그인 화면으로 이동한다', async () => {
    const h = setup({ keep: true, remaining: 86400000 });
    const deadline = [...h.timers.values()].find(timer => timer.delay === 86400000);
    h.clock.now += 86400001;
    deadline.callback();
    assert.equal(h.requests[0].url, '/api/me');
    h.respond(0, { error: 'Unauthorized' }, 401);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(h.window.location.href, '/logout?timeout=1');
});

test('탭 복귀는 로컬 만료가 지났어도 다른 탭에서 연장된 서버 만료를 먼저 확인한다', async () => {
    const h = setup();
    h.clock.now += 180000;
    const visible = h.visible();
    assert.equal(h.requests[0].url, '/api/me');
    assert.equal(h.window.location.href, '');
    h.respond(0, h.payload());
    await visible;
    assert.equal(h.window.location.href, '');
    assert.equal(h.window.getSessionState().expiresAtMs, h.clock.now + 3600000);
});

test('탭 복귀의 잘못된 응답은 현재 상태를 유지한다', async () => {
    const h = setup();
    const expiry = h.window.getSessionState().expiresAtMs;
    const visible = h.visible();
    h.respond(0, { username: 'trader' });
    await visible;
    assert.equal(h.window.getSessionState().expiresAtMs, expiry);
    assert.equal(h.events.length, 0);
    assert.equal(h.window.location.href, '');
});

test('동일 이름이어도 사용자 ID가 다르면 새 화면으로 이동하고 기존 상태와 섞지 않는다', async () => {
    const h = setup();
    const expiry = h.window.getSessionState().expiresAtMs;
    const visible = h.visible();
    h.respond(0, h.payload({ user_id: 8 }));
    await visible;
    assert.equal(h.window.location.href, '/');
    assert.equal(h.window.getSessionState().expiresAtMs, expiry);
    assert.equal(h.events.length, 0);
});

test('연장 전에 시작한 응답은 JSON 수신이 늦어도 연장 결과를 덮지 않는다', async () => {
    const h = setup();
    const visible = h.visible();
    const body = deferred();
    h.requests[0].resolve({ status: 200, ok: true, json: () => body.promise });
    await new Promise(resolve => setImmediate(resolve));
    const extension = h.window.extendCurrentSession();
    h.respond(1, h.payload({ status: 'success' }));
    await extension;
    body.resolve(h.payload({ expires_at: (h.clock.now + 100000) / 1000 }));
    await visible;
    assert.equal(h.window.getSessionState().expiresAtMs, h.clock.now + 3600000);
});
