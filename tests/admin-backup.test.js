/* node --test tests/admin-backup.test.js: 백업 시작·자동 상태 조회의 경계를 검증한다. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const tick = () => new Promise(resolve => setImmediate(resolve));

function setup() {
    const requests = [], timers = new Map(), alerts = [];
    const button = { disabled: false, addEventListener: (_, handler) => { button.click = handler; } };
    const status = { textContent: '' };
    const modal = { style: { display: 'flex' }, classList: { contains: () => false } };
    const elements = { adminBackupRun: button, adminBackupStatus: status, adminModalOverlay: modal };
    const context = vm.createContext({
        location: { href: 'https://journal.example/', origin: 'https://journal.example' },
        document: {
            getElementById: id => elements[id] || null,
            documentElement: { classList: { contains: () => false } },
        },
        addEventListener() {}, console, URL, Request, Response, Headers, AbortController,
        setTimeout(callback, delay) { const id = {}; timers.set(id, { callback, delay }); return id; },
        clearTimeout(id) { timers.delete(id); },
        fetch(url, options) {
            return new Promise((resolve, reject) => requests.push({ url, options, resolve, reject }));
        },
        customAlert: async message => { alerts.push(message); },
    });
    context.window = context;
    for (const file of ['01-core.js', '08-admin.js']) {
        vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/js', file), 'utf8'), context);
    }
    const respond = async (request, body, code = 200) => {
        request.resolve(new Response(JSON.stringify(body), { status: code }));
        await tick();
    };
    const poll = async () => {
        const entry = [...timers].find(([, timer]) => timer.delay < 15000);
        assert.ok(entry, '자동 상태 조회가 예약되어야 한다');
        timers.delete(entry[0]);
        entry[1].callback();
        await tick();
    };
    return { context, button, status, modal, requests, timers, alerts, respond, poll,
        click: () => button.click({ currentTarget: button }) };
}

const snapshot = (state, extra = {}) => ({
    state, last_success: '2026-10-25T13:29:32+09:00', next_run: '2026-10-26T03:00:00+09:00', ...extra,
});

for (const [state, label] of [['success', '성공'], ['failed', '실패'], ['interrupted', '이전 작업 중단']]) {
    test(`백업을 한 번 시작하고 ${label} 결과까지 자동 조회한 뒤 중지한다`, async () => {
        const h = setup();
        const click = h.click();
        await h.click();
        assert.equal(h.requests.length, 1);
        assert.equal(h.button.disabled, true);
        await h.respond(h.requests[0], { status: 'started' }, 202);
        await h.respond(h.requests[1], snapshot('running'));
        await click;
        assert.match(h.status.textContent, /실행 중/);
        assert.equal(h.button.disabled, true);
        await h.poll();
        await h.respond(h.requests[2], snapshot(state, { database_ok: true, failed_users: 1, total_users: 2 }));
        assert.ok(h.status.textContent.includes(`상태: ${label}`));
        assert.match(h.status.textContent, /마지막 성공:.*2026.*다음 예약:/);
        if (state === 'failed') assert.match(h.status.textContent, /사용자 ZIP 실패: 1\/2/);
        assert.equal(h.button.disabled, false);
        assert.equal(h.timers.size, 0);
        assert.equal(h.requests.filter(r => r.options.method === 'POST').length, 1);
    });
}

test('시작 전의 늦은 조회 응답이 진행 중인 백업 버튼을 활성화하지 않는다', async () => {
    const h = setup();
    const initial = h.context.refreshBackupStatus();
    const old = h.requests[0];
    const click = h.click();
    assert.equal(old.options.signal.aborted, true);
    await h.context.refreshBackupStatus();
    assert.equal(h.requests.length, 2);
    await h.respond(old, snapshot('success'));
    await initial;
    assert.equal(h.button.disabled, true);
    assert.match(h.status.textContent, /시작하는 중/);
    await h.respond(h.requests[1], { status: 'started' }, 202);
    await h.respond(h.requests[2], snapshot('success'));
    await click;
    assert.equal(h.button.disabled, false);
    assert.equal(h.timers.size, 0);
});

test('상태 조회 통신 오류는 조회만 재시도하고 완료 후 버튼을 활성화한다', async () => {
    const h = setup();
    const refresh = h.context.refreshBackupStatus();
    await h.respond(h.requests[0], snapshot('running'));
    await refresh;
    await h.poll();
    h.requests[1].reject(new Error('network offline'));
    await tick();
    assert.match(h.status.textContent, /자동으로 다시 확인/);
    assert.equal(h.button.disabled, true);
    await h.poll();
    await h.respond(h.requests[2], snapshot('success'));
    assert.equal(h.button.disabled, false);
    assert.equal(h.requests.filter(r => r.options.method === 'POST').length, 0);
    assert.equal(h.timers.size, 0);
});

test('창을 닫으면 조회를 취소하고 늦은 응답을 무시하며 다시 열면 조회를 재개한다', async () => {
    const h = setup();
    const refresh = h.context.refreshBackupStatus();
    await h.respond(h.requests[0], snapshot('running'));
    await refresh;
    h.context.stopBackupStatusPolling();
    assert.equal(h.timers.size, 0);
    const pending = h.context.refreshBackupStatus();
    h.context.stopBackupStatusPolling();
    h.modal.style.display = 'none';
    assert.equal(h.requests[1].options.signal.aborted, true);
    await h.respond(h.requests[1], snapshot('success'));
    await pending;
    assert.match(h.status.textContent, /실행 중/);
    assert.equal(h.timers.size, 0);
    await h.context.refreshBackupStatus();
    assert.equal(h.requests.length, 2);
    h.modal.style.display = 'flex';
    const reopened = h.context.refreshBackupStatus();
    await h.respond(h.requests[2], snapshot('success'));
    await reopened;
    assert.equal(h.button.disabled, false);
});

test('시작 응답이 유실되어도 백업을 재실행하지 않고 실제 서버 상태를 조회한다', async () => {
    const h = setup();
    const click = h.click();
    h.requests[0].reject(new Error('응답 유실'));
    await tick();
    await h.respond(h.requests[1], snapshot('running'));
    await click;
    assert.deepEqual(h.alerts, ['응답 유실']);
    assert.equal(h.button.disabled, true);
    await h.poll();
    await h.respond(h.requests[2], snapshot('success'));
    assert.equal(h.button.disabled, false);
    assert.equal(h.requests.filter(r => r.options.method === 'POST').length, 1);
});

for (const code of [401, 403]) {
    test(`인증·권한 오류(${code})에는 조회를 반복하지 않는다`, async () => {
        const h = setup();
        const refresh = h.context.refreshBackupStatus();
        await h.respond(h.requests[0], { error: '접근 불가' }, code);
        await refresh;
        assert.equal(h.button.disabled, true);
        assert.equal(h.timers.size, 0);
        assert.match(h.status.textContent, /불러오지 못했습니다/);
    });
}
