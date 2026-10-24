// ═══════════════════════════════════════════════════════════════════
// 02-overlay.js — 모달(alert/confirm/prompt)과 로딩 오버레이 — 화면 공통 UI
//
// ⚠️ 이 파일들은 ES 모듈이 아니라 **순서대로 로드되는 클래식 스크립트**다.
//    최상위 let/const/function 은 전역 렉시컬 환경을 공유하므로, 예전 script.js
//    한 덩어리였을 때와 실행 의미가 완전히 같다. (HTML 의 인라인 onclick 핸들러가
//    전역 함수를 그대로 부르고 있어 모듈로 바꾸면 그것들이 전부 깨진다)
//    → 실행 순서는 파일명 번호 순서다(app/utils/scriptbundle.py 가 그 순서로 이어 붙인다).
// ═══════════════════════════════════════════════════════════════════

window.scrollToFilterBox = function() {
    // "TRADE HISTORY" 타이틀이 포함된 history-header 영역을 찾아 최상단으로 스크롤
    const historyHeader = document.querySelector('.history-header');
    if (!historyHeader) return;
    const y = historyHeader.getBoundingClientRect().top + window.scrollY - 20; // 상단 여백 20px
    window.scrollTo({ top: Math.max(0, y), behavior: 'smooth' });
};

let customModalTimeout = null; // ⭐️ 연속 모달 호출 시 타이머 꼬임 방지용 전역 변수
const customModalQueue = [];
let customModalActive = false;

// ⭐️ 커스텀 공통 모달 (Alert, Confirm, Prompt 대체용)
window.customModal = function({ type = 'alert', title = '알림', message = '', inputPlaceholder = '' }) {
    return new Promise((resolve) => {
        customModalQueue.push({ type, title, message, inputPlaceholder, resolve });
        showNextCustomModal();
    });
};
function showNextCustomModal() {
        if (customModalActive || !customModalQueue.length) return;
        const { type, title, message, inputPlaceholder, resolve } = customModalQueue.shift();
        const overlay = document.getElementById('customModalOverlay');
        if (!overlay) {
            resolve(type === 'prompt' ? null : type === 'alert');
            showNextCustomModal();
            return;
        }
        customModalActive = true;
        const previousFocus = document.activeElement;
        let settled = false;
        
        // ⭐️ 이전 모달의 닫힘 애니메이션(180ms) 타이머가 새 모달을 닫아버리는 버그 완벽 차단
        if (customModalTimeout) clearTimeout(customModalTimeout);
        overlay.classList.remove('closing');
        
        const titleEl = document.getElementById('customModalTitle');
        const messageEl = document.getElementById('customModalMessage');
        const promptContainer = document.getElementById('customModalPromptContainer');
        const inputEl = document.getElementById('customModalInput');
        const btnCancel = document.getElementById('btnCustomModalCancel');
        const btnOk = document.getElementById('btnCustomModalOk');

        titleEl.innerText = title;
        messageEl.innerText = message;
        promptContainer.style.display = type === 'prompt' ? 'block' : 'none';
        if (type === 'prompt') { inputEl.value = ''; inputEl.placeholder = inputPlaceholder; }
        btnCancel.style.display = (type === 'confirm' || type === 'prompt') ? 'block' : 'none';

        overlay.style.display = 'flex';
        if (type === 'prompt') inputEl.focus();
        else btnOk.focus();

        const cleanup = () => {
            overlay.classList.add('closing');
            customModalTimeout = setTimeout(() => { overlay.style.display = 'none'; overlay.classList.remove('closing'); }, 180);
            btnOk.removeEventListener('click', onOk);
            btnCancel.removeEventListener('click', onCancel);
            inputEl.removeEventListener('keydown', onInputKeydown);
            document.removeEventListener('keydown', onDocKeydown);
        };
        const finish = value => {
            if (settled) return;
            settled = true;
            cleanup();
            customModalActive = false;
            resolve(value);
            if (customModalQueue.length) showNextCustomModal();
            else if (previousFocus?.isConnected) previousFocus.focus();
        };
        const onOk = () => finish(type === 'prompt' ? inputEl.value : true);
        const onCancel = () => finish(type === 'prompt' ? null : false);
        const onInputKeydown = (e) => { if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); onOk(); } };
        const onDocKeydown = (e) => { if (e.key === 'Escape' && overlay.style.display === 'flex') { e.preventDefault(); e.stopPropagation(); onCancel(); } };

        btnOk.addEventListener('click', onOk);
        btnCancel.addEventListener('click', onCancel);
        if (type === 'prompt') inputEl.addEventListener('keydown', onInputKeydown);
        document.addEventListener('keydown', onDocKeydown);
}
window.customAlert = (message, title = '알림') => window.customModal({ type: 'alert', title, message });
window.customConfirm = (message, title = '확인') => window.customModal({ type: 'confirm', title, message });
window.customPrompt = (message, title = '입력', placeholder = '') => window.customModal({ type: 'prompt', title, message, inputPlaceholder: placeholder });

// ⭐️ 전역 로딩 오버레이 제어 함수 (백업/원복/엑셀 등 긴 작업 시)
let loadingStartTime = 0;
const MIN_LOADING_TIME = 1000; // 최소 노출 시간 설정 (1000ms = 1초)

window.showLoadingOverlay = function(message = '처리 중입니다...') {
    const overlay = document.getElementById('loadingOverlay');
    const textEl = document.getElementById('loadingText');
    if (overlay && textEl) {
        loadingStartTime = Date.now(); // ⭐️ 로딩이 시작된 정확한 시간 기록
        textEl.innerText = message;
        overlay.style.display = 'flex';
    }
};
window.hideLoadingOverlay = function() {
    return new Promise((resolve) => {
        const overlay = document.getElementById('loadingOverlay');
        if (!overlay || overlay.style.display === 'none') return resolve();
        
        const elapsedTime = Date.now() - loadingStartTime;
        if (elapsedTime < MIN_LOADING_TIME) {
            // ⭐️ 작업이 너무 빨리 끝났다면, 남은 시간만큼 기다렸다가 숨김 처리
            setTimeout(() => { 
                overlay.style.display = 'none'; 
                resolve();
            }, MIN_LOADING_TIME - elapsedTime);
        } else {
            // ⭐️ 이미 1초 이상 지났다면 즉시 숨김 처리
            overlay.style.display = 'none';
            resolve();
        }
    });
};

// ⭐️ 세션 만료 관리 — 서버가 로그인 시점에 확정한 절대 만료 시각(expires_at) 기준으로 동작
//   - 로그인 유지 미선택(1시간): 만료 5분 전 연장 팝업 표시, "연장하기" 선택 시 1시간 단위로 반복 연장
//   - 로그인 유지 선택(24시간): 연장 팝업 없이 만료 시각에 자동 로그아웃
