/* node --test tests/request-context.test.js: 페이지 계정과 fetch 요청의 연결을 검증한다. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../static/js/01-core.js'), 'utf8');
const origin = 'https://journal.example';

function setup(respond = () => new Response('ok')) {
    const calls = [], redirects = [], timers = new Map();
    const context = vm.createContext({
        JOURNAL_USER_ID: 7, JOURNAL_USERNAME: 'alice', JOURNAL_CSRF_TOKEN: 'session-bound-token',
        location: { href: origin + '/', origin, replace: value => redirects.push(value) },
        document: { documentElement: { classList: { contains: () => false } } },
        addEventListener() {}, console, URL, Request, Response, Headers, AbortController,
        setTimeout(fn) { const id = {}; timers.set(id, fn); return id; },
        clearTimeout(id) { timers.delete(id); },
        fetch(input, options) {
            const target = input instanceof Request ? input : new URL(input, origin);
            const request = new Request(target, options);
            calls.push({ input, options, request });
            return Promise.resolve(respond(request));
        },
    });
    context.window = context;
    vm.runInContext(source, context);
    return { context, calls, redirects, timers };
}

test('문자열·URL·Request의 내부 API 요청에 페이지 계정을 넣고 기존 헤더를 보존한다', async () => {
    const h = setup();
    const headers = { 'Content-Type': 'application/json', 'X-Journal-User-ID': '999' };
    await h.context.fetch('/api/preferences', { method: 'POST', body: '{"defaultView":"chart"}', headers });
    await h.context.fetch(new URL(origin + '/api/me'));
    await h.context.fetch(new Request(origin + '/api/journal/meta', { headers: { 'X-Custom': 'retained' } }));
    for (const { request } of h.calls) assert.equal(request.headers.get('X-Journal-User-ID'), '7');
    assert.equal(h.calls[0].request.headers.get('Content-Type'), 'application/json');
    assert.equal(await h.calls[0].request.text(), '{"defaultView":"chart"}');
    assert.equal(h.calls[2].request.headers.get('X-Custom'), 'retained');
    assert.equal(headers['X-Journal-User-ID'], '999');
});

test('CSRF 토큰은 같은 출처 웹 API의 변경 요청에만 붙인다', async () => {
    const h = setup();
    await h.context.fetch('/api/preferences', { method: 'PATCH', body: '{}' });
    await h.context.fetch(new Request(origin + '/api/account', { method: 'DELETE' }));
    await h.context.fetch(new Request(origin + '/api/ping'), { method: 'POST' });
    await h.context.fetch('/api/me');
    await h.context.fetch('https://other.example/api/entry', { method: 'POST' });
    await h.context.fetch('/api/v1/auth/token', { method: 'POST' });
    for (const { request } of h.calls.slice(0, 3)) {
        assert.equal(request.headers.get('X-CSRF-Token'), 'session-bound-token');
    }
    for (const { request } of h.calls.slice(3)) assert.equal(request.headers.get('X-CSRF-Token'), null);
});

test('Request 본문·메서드·credentials·signal·keepalive는 변경하지 않는다', async () => {
    const h = setup();
    const controller = new AbortController();
    const input = new Request(origin + '/api/entry', {
        method: 'POST', body: 'payload', credentials: 'include', keepalive: true,
        signal: controller.signal, headers: { 'X-Original': 'present' },
    });
    await h.context.fetch(input);
    const { request } = h.calls[0];
    assert.equal(h.calls[0].input, input);
    assert.equal(request.method, 'POST');
    assert.equal(request.credentials, 'include');
    assert.equal(request.keepalive, true);
    assert.equal(await request.text(), 'payload');
    controller.abort();
    assert.equal(request.signal.aborted, true);
    assert.equal(input.headers.has('X-Journal-User-ID'), false);
});

test('init의 명시적 헤더·signal·body 우선순위와 Headers 원본을 유지한다', async () => {
    const h = setup();
    const original = new AbortController(), override = new AbortController();
    const input = new Request(origin + '/api/preferences', {
        method: 'POST', body: 'old', signal: original.signal, headers: { 'X-Original': 'old' },
    });
    const headers = new Headers({ 'X-Override': 'new' });
    const options = { method: 'PUT', body: 'new', signal: override.signal, headers, keepalive: true };
    await h.context.fetch(input, options);
    const { request } = h.calls[0];
    assert.equal(request.headers.has('X-Original'), false);
    assert.equal(request.headers.get('X-Override'), 'new');
    assert.equal(request.headers.get('X-Journal-User-ID'), '7');
    assert.equal(request.method, 'PUT');
    assert.equal(await request.text(), 'new');
    assert.equal(request.keepalive, true);
    original.abort();
    assert.equal(request.signal.aborted, false);
    override.abort();
    assert.equal(request.signal.aborted, true);
    assert.equal(headers.has('X-Journal-User-ID'), false);
    assert.equal(options.headers, headers);
});

test('외부 origin·정적 파일·v1 API에는 페이지 계정을 첨부하지 않는다', async () => {
    const h = setup();
    for (const url of ['https://outside.example/api/me', '//outside.example/api/preferences',
        'http://journal.example/api/me', '/static/calc.js', '/api/v1', '/api/v1/trades']) {
        await h.context.fetch(url);
    }
    for (const { request } of h.calls) assert.equal(request.headers.has('X-Journal-User-ID'), false);
    await h.context.fetch('/api/v10/example');
    assert.equal(h.calls.at(-1).request.headers.get('X-Journal-User-ID'), '7');
});

test('계정 변경 응답은 본문을 사용하기 전에 새 페이지로 이동하고 오류를 반환한다', async () => {
    const response = new Response('{"owner":"bob"}', {
        status: 409, headers: { 'X-Journal-Account-Changed': '1' },
    });
    const h = setup(() => response);
    await assert.rejects(h.context.fetch('/api/journal/meta'), error => error.code === 'ACCOUNT_CHANGED' && error.status === 409);
    assert.deepEqual(h.redirects, ['/']);
    assert.equal(response.bodyUsed, false);
});

test('일반 409나 외부 서비스의 같은 이름 헤더로 계정 이동을 하지 않는다', async () => {
    const normal = setup(() => new Response('stale revision', { status: 409 }));
    assert.equal((await normal.context.fetch('/api/preferences')).status, 409);
    assert.deepEqual(normal.redirects, []);
    const external = setup(() => new Response('external', { status: 409, headers: { 'X-Journal-Account-Changed': '1' } }));
    assert.equal((await external.context.fetch('https://outside.example/api/me')).status, 409);
    assert.deepEqual(external.redirects, []);
});

test('초기 프리페치도 계정 변경 응답을 데이터 로더에 전달하지 않는다', async () => {
    const h = setup();
    h.context.__initialFetches = { data: Promise.resolve(new Response('changed', {
        status: 409, headers: { 'X-Journal-Account-Changed': '1' },
    })) };
    await assert.rejects(h.context.initialFetchOrFresh('data', '/api/journal/meta'), error => error.code === 'ACCOUNT_CHANGED');
    assert.deepEqual(h.redirects, ['/']);
    assert.equal(h.calls.length, 0);
    assert.equal(h.context.__initialFetches.data, null);
});

test('실패한 프리페치의 새 요청에도 계정 헤더를 붙인다', async () => {
    const h = setup();
    h.context.__initialFetches = { data: Promise.resolve(null) };
    await h.context.initialFetchOrFresh('data', '/api/journal/meta');
    assert.equal(h.calls[0].request.headers.get('X-Journal-User-ID'), '7');
});

test('공통 wrapper를 거쳐도 요청 중 취소가 전달되고 타임아웃이 해제된다', async () => {
    const h = setup(request => new Promise((resolve, reject) => {
        request.signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' })));
    }));
    const controller = new AbortController();
    const pending = h.context.fetchWithTimeout('/api/preferences', { signal: controller.signal, keepalive: true });
    assert.equal(h.calls[0].request.keepalive, true);
    assert.equal(h.calls[0].request.headers.get('X-Journal-User-ID'), '7');
    controller.abort();
    await assert.rejects(pending, { name: 'AbortError' });
    assert.equal(h.timers.size, 0);
});

test('파일 업로드 FormData의 본문과 multipart Content-Type을 보존한다', async () => {
    const h = setup();
    const body = new FormData();
    body.set('file', new Blob(['zip contents']), 'backup.zip');
    body.set('previewToken', 'token-value');
    await h.context.fetch('/api/restore/preview', { method: 'POST', body });
    const { request } = h.calls[0];
    assert.match(request.headers.get('Content-Type'), /^multipart\/form-data; boundary=/);
    const received = await request.formData();
    assert.equal(received.get('file').name, 'backup.zip');
    assert.equal(await received.get('file').text(), 'zip contents');
    assert.equal(received.get('previewToken'), 'token-value');
});

test('head의 모든 초기 요청은 bundle 로드 전부터 페이지 계정 헤더를 사용한다', () => {
    const html = fs.readFileSync(path.join(__dirname, '../templates/stock-memo.html'), 'utf8');
    let initial = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)]
        .map(match => match[1]).find(script => script.includes('window.__initialFetches ='));
    initial = initial.replace("{{ session.get('username', '') | tojson }}", '"alice"')
        .replace("{{ session.get('user_id', '') | tojson }}", '7')
        .replace('{{ csrf_token() | tojson }}', '"session-bound-token"');
    const calls = [];
    const context = vm.createContext({
        localStorage: { getItem: () => JSON.stringify(['/api/journal/entries?limit=15']) },
        fetch(url, options) { calls.push({ url, options }); return Promise.resolve(new Response('ok')); },
    });
    context.window = context;
    vm.runInContext(initial, context);
    assert.equal(context.JOURNAL_USER_ID, 7);
    assert.equal(calls.length, 5);
    for (const call of calls) assert.equal(call.options.headers['X-Journal-User-ID'], '7');
});

test('같은 계정의 CSRF 만료는 Request 본문과 멱등키를 보존해 한 번 재시도한다', async () => {
    const bodies = [];
    const h = setup(async request => {
        if (new URL(request.url).pathname === '/api/session/csrf') {
            return Response.json({user_id: 7, csrf_token: 'renewed'});
        }
        bodies.push(await request.text());
        return request.headers.get('X-CSRF-Token') === 'renewed'
            ? Response.json({status: 'success'}) : Response.json({errorCode: 'CSRF_INVALID'}, {status: 403});
    });
    const result = await h.context.fetch(new Request(origin + '/api/entry', {
        method: 'POST', body: '{"thoughts":"draft"}', headers: {'Idempotency-Key': 'stable-key'}
    }));
    assert.equal(result.status, 200);
    assert.deepEqual(bodies, ['{"thoughts":"draft"}', '{"thoughts":"draft"}']);
    assert.equal(h.calls.length, 3);
    assert.equal(h.calls[2].request.headers.get('Idempotency-Key'), 'stable-key');
    assert.equal(h.context.JOURNAL_CSRF_TOKEN, 'renewed');
});

test('CSRF 갱신도 계정 변경을 검사하며 다른 계정 토큰을 사용하지 않는다', async () => {
    const h = setup(request => new URL(request.url).pathname === '/api/session/csrf'
        ? Response.json({user_id: 8, csrf_token: 'foreign'})
        : Response.json({errorCode: 'CSRF_INVALID'}, {status: 403}));
    assert.equal((await h.context.fetch('/api/entry', {method: 'POST', body: '{}'})).status, 403);
    assert.equal(h.calls.length, 2);
    assert.equal(h.context.JOURNAL_CSRF_TOKEN, 'session-bound-token');
});

test('토큰 갱신 후 재거부와 일반 403은 무한 재시도하지 않는다', async () => {
    const h = setup(request => new URL(request.url).pathname === '/api/session/csrf'
        ? Response.json({user_id: 7, csrf_token: 'new'})
        : Response.json({errorCode: 'CSRF_INVALID'}, {status: 403}));
    assert.equal((await h.context.fetch('/api/entry', {method: 'POST', body: '{}'})).status, 403);
    assert.equal(h.calls.length, 3);
    const denied = setup(() => Response.json({error: 'forbidden'}, {status: 403}));
    await denied.context.fetch('/api/admin/users');
    assert.equal(denied.calls.length, 1);
});
