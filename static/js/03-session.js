// ═══════════════════════════════════════════════════════════════════
// 03-session.js — 세션 만료 타이머·연장 경고·탭 복귀 시 재검사
//
// ⚠️ 이 파일들은 ES 모듈이 아니라 **순서대로 로드되는 클래식 스크립트**다.
//    최상위 let/const/function 은 전역 렉시컬 환경을 공유하므로, 예전 script.js
//    한 덩어리였을 때와 실행 의미가 완전히 같다. (HTML 의 인라인 onclick 핸들러가
//    전역 함수를 그대로 부르고 있어 모듈로 바꾸면 그것들이 전부 깨진다)
//    → 실행 순서는 파일명 번호 순서다(app/utils/scriptbundle.py 가 그 순서로 이어 붙인다).
// ═══════════════════════════════════════════════════════════════════

let warningTimer;
let logoutTimer;
let countdownInterval;
let sessionExpiresAtMs = window.SESSION_EXPIRES_AT_MS || (Date.now() + 60 * 60 * 1000);
let sessionKeepLoggedIn = window.SESSION_KEEP_LOGGED_IN === true;
let sessionExtensionRequest = null;
let sessionSyncRequest = null;
let sessionStateVersion = 0;
const SESSION_PAGE_USERNAME = window.JOURNAL_USERNAME;
const SESSION_PAGE_USER_ID = window.JOURNAL_USER_ID;
const WARNING_BEFORE = 5 * 60 * 1000;

window.getSessionState = function() {
    return { expiresAtMs: sessionExpiresAtMs, keepLoggedIn: sessionKeepLoggedIn };
};

function sessionError(message, code) {
    return Object.assign(new Error(message), { code });
}

window.getSessionLogoutUrl = function(timedOut = false) {
    return `/logout?${timedOut ? 'timeout=1&' : ''}user_id=${encodeURIComponent(SESSION_PAGE_USER_ID)}`;
};

function rejectExpiredSession() {
    window.location.href = window.getSessionLogoutUrl(true);
    return sessionError('로그인 시간이 만료되었습니다. 다시 로그인해주세요.', 'SESSION_EXPIRED');
}

function closeSessionWarning() {
    const modal = document.getElementById('sessionExtensionModalOverlay');
    if (modal) {
        modal.style.display = 'none';
        modal.classList.remove('closing');
    }
    clearInterval(countdownInterval);
}

function applyServerSession(data) {
    // 다른 탭에서 계정이 바뀌었으면 현재 화면의 데이터와 섞지 않는다.
    if (!data || typeof data.username !== 'string' || !Number.isSafeInteger(data.user_id)
            || data.user_id <= 0 || typeof data.expires_at !== 'number'
            || !Number.isFinite(data.expires_at) || data.expires_at <= 0
            || data.expires_at * 1000 > Date.now() + (data.keep_logged_in ? 24 : 1) * 60 * 60 * 1000 + WARNING_BEFORE
            || typeof data.keep_logged_in !== 'boolean') {
        throw sessionError('세션 응답을 확인하지 못했습니다. 잠시 후 다시 시도해주세요.', 'INVALID_SESSION');
    }
    if (data.username !== SESSION_PAGE_USERNAME || data.user_id !== SESSION_PAGE_USER_ID) {
        // 새 계정의 쿠키를 로그아웃시키지 않고, 그 계정에 맞는 화면을 다시 연다.
        window.location.href = '/';
        throw sessionError('로그인 계정이 변경되어 화면을 다시 불러옵니다.', 'ACCOUNT_CHANGED');
    }
    if (data.expires_at * 1000 <= Date.now()) throw rejectExpiredSession();
    sessionExpiresAtMs = data.expires_at * 1000;
    sessionKeepLoggedIn = data.keep_logged_in;
    closeSessionWarning();
    scheduleSessionTimers();
    window.dispatchEvent(new CustomEvent('session-state-changed', { detail: window.getSessionState() }));
    return window.getSessionState();
}

async function sessionResponse(response) {
    if (response.status === 401) throw rejectExpiredSession();
    if (!response.ok) {
        let data;
        try { data = await response.json(); } catch (_) { /* 오류 본문이 없을 수 있다. */ }
        if (response.status === 409 && (data?.errorCode === 'ACCOUNT_CHANGED'
                || response.headers?.get('X-Journal-Account-Changed') === '1')) {
            window.location.href = '/';
            throw sessionError('로그인 계정이 변경되어 화면을 다시 불러옵니다.', 'ACCOUNT_CHANGED');
        }
        throw sessionError('세션을 확인하지 못했습니다. 잠시 후 다시 시도해주세요.', 'SESSION_REQUEST_FAILED');
    }
    try { return await response.json(); }
    catch (_) { throw sessionError('세션 응답을 확인하지 못했습니다. 잠시 후 다시 시도해주세요.', 'INVALID_SESSION'); }
}

async function requestSession(url, options) {
    try { return await fetchWithTimeout(url, options); }
    catch (error) {
        if (error.code === 'ACCOUNT_CHANGED') throw error;
        throw sessionError('서버에 연결하지 못했습니다. 연결 상태를 확인한 후 다시 시도해주세요.', 'SESSION_REQUEST_FAILED');
    }
}

async function requestCurrentSession(url, options, isCurrent = () => true) {
    const response = await requestSession(url, options);
    // 비밀번호 변경/다른 탭 로그인 전에 시작된 요청의 늦은 401일 수 있다.
    // 브라우저에 현재 저장된 쿠키로 같은 요청을 한 번만 다시 확인한다.
    if (response.status === 401 && isCurrent()) return requestSession(url, options);
    return response;
}

window.extendCurrentSession = function() {
    if (sessionExtensionRequest) return sessionExtensionRequest;
    if (sessionKeepLoggedIn) {
        return Promise.reject(sessionError('로그인 유지 세션은 24시간 후 만료되며 연장할 수 없습니다.', 'SESSION_NOT_EXTENDABLE'));
    }
    // 연장 전에 시작된 /api/me 응답이 뒤늦게 와도 새 만료 시각을 되돌리지 않는다.
    sessionStateVersion += 1;
    sessionExtensionRequest = (async () => {
        const response = await requestCurrentSession('/api/ping', { method: 'POST', cache: 'no-store' });
        const data = await sessionResponse(response);
        if (data?.status !== 'success') throw sessionError('로그인 시간을 연장하지 못했습니다.', 'INVALID_SESSION');
        const state = applyServerSession(data);
        if (state.keepLoggedIn) throw sessionError('로그인 유지 세션은 연장할 수 없습니다.', 'SESSION_NOT_EXTENDABLE');
        return state;
    })().finally(() => { sessionExtensionRequest = null; });
    return sessionExtensionRequest;
};

function syncCurrentSession() {
    if (sessionExtensionRequest) return sessionExtensionRequest;
    if (sessionSyncRequest) return sessionSyncRequest;
    const version = sessionStateVersion;
    sessionSyncRequest = (async () => {
        const response = await requestCurrentSession('/api/me', { cache: 'no-store' }, () => version === sessionStateVersion);
        if (version !== sessionStateVersion) return window.getSessionState();
        const data = await sessionResponse(response);
        if (version !== sessionStateVersion) return window.getSessionState();
        return applyServerSession(data);
    })().finally(() => { sessionSyncRequest = null; });
    return sessionSyncRequest;
}

function checkSessionDeadline() {
    // 숨겨진 탭의 옛 타이머로 다른 탭에서 연장한 공용 쿠키를 지우지 않는다.
    if (document.visibilityState === 'hidden') return;
    syncCurrentSession().catch(error => {
        if (!['ACCOUNT_CHANGED', 'SESSION_EXPIRED'].includes(error.code)) console.warn('세션 상태 확인 실패', error);
    });
}

function scheduleSessionTimers() {
    clearTimeout(warningTimer);
    clearTimeout(logoutTimer);
    clearInterval(countdownInterval);
    const remainingMs = sessionExpiresAtMs - Date.now();
    if (remainingMs <= 0) {
        checkSessionDeadline();
        return;
    }
    logoutTimer = setTimeout(checkSessionDeadline, remainingMs);
    if (sessionKeepLoggedIn) return;
    if (remainingMs <= WARNING_BEFORE) showExtensionWarning();
    else warningTimer = setTimeout(showExtensionWarning, remainingMs - WARNING_BEFORE);
}

function showExtensionWarning() {
    if (sessionKeepLoggedIn) return;
    const remainingMs = sessionExpiresAtMs - Date.now();
    if (remainingMs <= 0) {
        checkSessionDeadline();
        return;
    }
    const modal = document.getElementById('sessionExtensionModalOverlay');
    const countdownEl = document.getElementById('sessionCountdown');
    if (!modal || !countdownEl) return;
    modal.style.display = 'flex';
    clearInterval(countdownInterval);
    const updateCountdown = () => {
        const seconds = Math.max(0, Math.floor((sessionExpiresAtMs - Date.now()) / 1000));
        countdownEl.innerText = `${Math.floor(seconds / 60).toString().padStart(2, '0')}:${(seconds % 60).toString().padStart(2, '0')}`;
        if (seconds <= 0) {
            clearInterval(countdownInterval);
            checkSessionDeadline();
        }
    };
    updateCountdown();
    countdownInterval = setInterval(updateCountdown, 1000);
}

setInterval(() => {
    if (document.visibilityState === 'hidden') return;
    const remainingMs = sessionExpiresAtMs - Date.now();
    if (remainingMs <= 0) checkSessionDeadline();
    else if (!sessionKeepLoggedIn && remainingMs <= WARNING_BEFORE) showExtensionWarning();
}, 30000);

document.addEventListener('visibilitychange', async () => {
    if (document.visibilityState !== 'visible') return;
    try {
        // 로컬 시각이 지났어도 먼저 확인한다. 다른 탭에서 이미 연장했을 수 있다.
        await syncCurrentSession();
        if (window.__dataLoadFailed) loadDataFromLocal();
    } catch (error) {
        if (!['ACCOUNT_CHANGED', 'SESSION_EXPIRED'].includes(error.code)) console.warn('세션 상태 확인 실패', error);
    }
});

scheduleSessionTimers();
