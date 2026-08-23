// ═══════════════════════════════════════════════════════════════════
// 03-session.js — 세션 만료 타이머·연장 경고·탭 복귀 시 재검사
//
// ⚠️ 이 파일들은 ES 모듈이 아니라 **순서대로 로드되는 클래식 스크립트**다.
//    최상위 let/const/function 은 전역 렉시컬 환경을 공유하므로, 예전 script.js
//    한 덩어리였을 때와 실행 의미가 완전히 같다. (HTML 의 인라인 onclick 핸들러가
//    전역 함수를 그대로 부르고 있어 모듈로 바꾸면 그것들이 전부 깨진다)
//    → 로드 순서는 templates/stock-memo.html 의 <script> 순서가 결정한다. 바꾸지 말 것.
// ═══════════════════════════════════════════════════════════════════

let warningTimer;
let logoutTimer;
let countdownInterval;
let sessionExpiresAtMs = window.SESSION_EXPIRES_AT_MS || (Date.now() + 60 * 60 * 1000);
const SESSION_KEEP_LOGGED_IN = window.SESSION_KEEP_LOGGED_IN === true;
const WARNING_BEFORE = 5 * 60 * 1000; // 만료 5분 전 경고 (밀리초)

function scheduleSessionTimers() {
    clearTimeout(warningTimer);
    clearTimeout(logoutTimer);
    clearInterval(countdownInterval);

    const remainingMs = sessionExpiresAtMs - Date.now();
    if (remainingMs <= 0) {
        window.location.href = '/logout?timeout=1';
        return;
    }

    // 로그인 유지(24시간) 세션은 연장 개념이 없으므로 만료 시각에 자동 로그아웃만 예약
    if (SESSION_KEEP_LOGGED_IN) {
        logoutTimer = setTimeout(() => { window.location.href = '/logout?timeout=1'; }, remainingMs);
        return;
    }

    if (remainingMs <= WARNING_BEFORE) {
        showExtensionWarning();
    } else {
        warningTimer = setTimeout(showExtensionWarning, remainingMs - WARNING_BEFORE);
    }
}

function showExtensionWarning() {
    // ⭐️ 브라우저 백그라운드 지연으로 인해 이미 만료된 경우 즉시 로그아웃
    const remainingMs = sessionExpiresAtMs - Date.now();
    if (remainingMs <= 0) {
        window.location.href = '/logout?timeout=1';
        return;
    }

    const extensionModal = document.getElementById('sessionExtensionModalOverlay');
    const countdownEl = document.getElementById('sessionCountdown');
    if (!extensionModal || !countdownEl) return;
    if (extensionModal.style.display === 'flex') return; // 이미 표시 중이면 중복 방지

    extensionModal.style.display = 'flex';

    // ⭐️ 남은 시간 5분을 고정하지 않고 실제 잔여 시간으로 카운트다운
    let timeLeft = Math.floor(remainingMs / 1000);
    countdownEl.innerText = `${Math.floor(timeLeft / 60).toString().padStart(2, '0')}:${(timeLeft % 60).toString().padStart(2, '0')}`;

    countdownInterval = setInterval(() => {
        // ⭐️ 백그라운드 스로틀링으로 인터벌이 밀려도 정확하도록 실제 만료 시각에서 매번 재계산
        timeLeft = Math.floor((sessionExpiresAtMs - Date.now()) / 1000);
        if (timeLeft <= 0) {
            clearInterval(countdownInterval);
            window.location.href = '/logout?timeout=1'; // 타이머가 0이 되면 즉시 이동
        } else {
            countdownEl.innerText = `${Math.floor(timeLeft / 60).toString().padStart(2, '0')}:${(timeLeft % 60).toString().padStart(2, '0')}`;
        }
    }, 1000);

    logoutTimer = setTimeout(() => {
        window.location.href = '/logout?timeout=1';
    }, remainingMs);
}

// ⭐️ 워치독: 절전 모드·백그라운드 스로틀링으로 setTimeout 이 밀리거나 유실되어도
//    30초마다 실제 잔여 시간을 재확인하여 연장 팝업 표시와 자동 로그아웃을 보장
setInterval(() => {
    const remainingMs = sessionExpiresAtMs - Date.now();
    if (remainingMs <= 0) {
        window.location.href = '/logout?timeout=1';
        return;
    }
    if (!SESSION_KEEP_LOGGED_IN && remainingMs <= WARNING_BEFORE) {
        showExtensionWarning(); // 이미 표시 중이면 내부에서 중복 방지됨
    }
}, 30000);

// ⭐️ 브라우저 탭 활성화 시 실제 만료 여부를 확인하여 동기화
document.addEventListener('visibilitychange', async () => {
    if (document.visibilityState === 'visible') {
        // ⭐️ 모바일에서 앱 전환 후 복귀 시, 초기 로딩이 실패해 멈춰 있던 상태면 자동으로 재시도
        if (window.__dataLoadFailed) {
            loadDataFromLocal();
        }

        if (Date.now() >= sessionExpiresAtMs) {
            // 백그라운드에 있는 동안 이미 만료되었다면 즉시 자동 로그아웃 처리
            window.location.href = '/logout?timeout=1';
            return;
        }

        // ⭐️ 서버에 API를 호출하여 실제 세션 만료 여부(또는 타 탭 로그아웃 여부) 체크
        try {
            const res = await fetch('/api/me');
            if (res.status === 401) {
                window.location.href = '/logout?timeout=1';
                return;
            }
        } catch(e) { console.warn("세션 상태 확인 실패", e); }

        // ⭐️ 백그라운드에서 setTimeout 이 지연되었을 수 있으므로 타이머 재정렬 (팝업 표시 중이면 유지)
        const extensionModal = document.getElementById('sessionExtensionModalOverlay');
        if (!extensionModal || extensionModal.style.display !== 'flex') {
            scheduleSessionTimers();
        }
    }
});

scheduleSessionTimers(); // 초기 타이머 시작
