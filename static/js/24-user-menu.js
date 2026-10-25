// 계정 배지의 비모달 팝업. 계정 정보·로그인 상태·테마와 시작 화면을 관리한다.
(() => {
    const trigger = document.getElementById('loggedInUserDisplay');
    const panel = document.getElementById('userAccountPanel');
    if (!trigger || !panel) return;
    const wrapper = trigger.parentElement;
    const remaining = document.getElementById('accountMenuSessionRemaining');
    const extendButton = document.getElementById('btnAccountExtendSession');
    const policy = document.getElementById('accountMenuSessionPolicy');
    const status = document.getElementById('accountMenuStatus');
    const theme = document.getElementById('accountMenuTheme');
    const defaultView = document.getElementById('accountMenuDefaultView');
    const views = ['list', 'calendar', 'chart'];
    const lastViewKey = `journalLastView:${window.JOURNAL_USER_ID || window.JOURNAL_USERNAME}`;
    let countdown = null;
    let extending = false;
    let savingView = false;
    let startViewApplied = false;
    let preferencesResolved = false;
    const preferencesError = '시작 화면 설정을 불러오지 못했습니다. 새로고침 후 다시 시도해 주세요.';

    function positionPanel() {
        if (panel.hidden) return;
        const bounds = trigger.getBoundingClientRect();
        const top = Math.max(12, Math.min(bounds.bottom + 8, window.innerHeight - 80));
        panel.style.left = `${Math.max(12, Math.min(bounds.right - panel.offsetWidth, window.innerWidth - panel.offsetWidth - 12))}px`;
        panel.style.top = `${top}px`;
        panel.style.maxHeight = `${Math.max(60, window.innerHeight - top - 12)}px`;
    }

    function syncSession() {
        const state = window.getSessionState();
        const seconds = Math.max(0, Math.ceil((state.expiresAtMs - Date.now()) / 1000));
        const hours = Math.floor(seconds / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        remaining.textContent = seconds ? `${hours ? `${hours}시간 ` : ''}${minutes}분 ${seconds % 60}초` : '만료됨';
        extendButton.hidden = state.keepLoggedIn;
        extendButton.disabled = extending || seconds <= 0;
        policy.textContent = state.keepLoggedIn ? '로그인 유지: 로그인 후 최대 24시간 (연장 불가)' : '연장하면 지금부터 1시간 동안 유지됩니다.';
    }

    function syncDisplaySettings() {
        theme.value = document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
        defaultView.disabled = !preferencesLoaded || savingView;
        if (!savingView) defaultView.value = views.includes(userPreferences.defaultView) ? userPreferences.defaultView : 'last';
    }

    function close(returnFocus = false) {
        if (panel.hidden) return;
        panel.hidden = true;
        trigger.setAttribute('aria-expanded', 'false');
        clearInterval(countdown);
        countdown = null;
        if (returnFocus) trigger.focus();
    }

    function open() {
        document.querySelector('.header-action-group')?.classList.remove('active');
        panel.hidden = false;
        trigger.setAttribute('aria-expanded', 'true');
        status.textContent = preferencesLoaded ? '' : (preferencesResolved ? preferencesError : '시작 화면 설정을 불러오는 중…');
        syncDisplaySettings();
        syncSession();
        positionPanel();
        clearInterval(countdown);
        countdown = setInterval(syncSession, 1000);
        panel.querySelector('button:not([hidden]):not(:disabled), select:not(:disabled)')?.focus();
    }

    window.updateAccountMenuUser = user => {
        if (!user || typeof user.username !== 'string') return;
        trigger.textContent = `👤 ${user.username}`;
        trigger.setAttribute('aria-label', `${user.username} 내 계정`);
        trigger.hidden = false;
        document.getElementById('accountMenuUsername').textContent = user.username;
        document.getElementById('accountMenuRole').textContent = user.is_admin ? '관리자' : '일반 사용자';
    };

    window.restoreAccountStartView = () => {
        if (startViewApplied) return;
        startViewApplied = true;
        let view = userPreferences.defaultView;
        if (!views.includes(view)) {
            try { view = localStorage.getItem(lastViewKey); } catch (e) { }
        }
        window.setJournalView(views.includes(view) ? view : 'list', { render: false, remember: false });
    };

    trigger.addEventListener('click', () => panel.hidden ? open() : close());
    trigger.addEventListener('keydown', event => {
        if (event.key === 'ArrowDown') { event.preventDefault(); open(); }
    });
    document.addEventListener('click', event => {
        if (!wrapper.contains(event.target)) close();
    }, true);
    wrapper.addEventListener('keydown', event => {
        if (event.key === 'Escape' && !panel.hidden) {
            event.preventDefault();
            event.stopPropagation();
            close(true);
        }
    });
    document.addEventListener('focusin', event => {
        // 클릭 중의 임시 blur나 요청 중 버튼 비활성화로는 닫지 않는다.
        if (!wrapper.contains(event.target)) close();
    });
    // 기존 핸들러보다 먼저 닫아 확인 모달을 닫은 뒤 보이는 배지로 포커스가 복귀하게 한다.
    panel.addEventListener('click', event => {
        if (event.target.closest('#btnChangePassword, #btnLogout')) close(true);
    }, true);
    document.getElementById('btnChangePassword').addEventListener('click', () => {
        document.getElementById('currentPassword')?.focus();
    });
    window.addEventListener('resize', positionPanel);
    window.addEventListener('scroll', () => close(), { passive: true });
    window.addEventListener('session-state-changed', () => { if (!panel.hidden) syncSession(); });
    window.addEventListener('display-settings-changed', syncDisplaySettings);
    window.addEventListener('user-preferences-loaded', () => {
        preferencesResolved = true;
        syncDisplaySettings();
        if (!panel.hidden && !savingView && !extending) status.textContent = preferencesLoaded ? '' : preferencesError;
    });
    window.addEventListener('journal-view-changed', event => {
        if (!views.includes(event.detail?.view)) return;
        startViewApplied = true; // 초기 데이터가 늦게 도착해도 사용자가 고른 화면을 되돌리지 않는다.
        // 계정 ID를 포함해 같은 기기의 다른 사용자와 마지막 화면을 공유하지 않는다.
        try { localStorage.setItem(lastViewKey, event.detail.view); } catch (e) { }
    });

    extendButton.addEventListener('click', async () => {
        if (extending) return;
        extending = true;
        extendButton.textContent = '연장 중…';
        status.textContent = '';
        syncSession();
        try {
            await window.extendCurrentSession();
            status.textContent = '로그인 시간이 연장되었습니다.';
        } catch (error) {
            status.textContent = error.message || '로그인을 연장하지 못했습니다. 다시 시도해 주세요.';
        } finally {
            extending = false;
            extendButton.textContent = '로그인 연장';
            syncSession();
        }
    });
    theme.addEventListener('change', () => window.setDisplayTheme(theme.value));
    defaultView.addEventListener('change', async () => {
        if (!preferencesLoaded || savingView) return;
        const value = defaultView.value;
        savingView = true;
        defaultView.disabled = true;
        status.textContent = '시작 화면 저장 중…';
        const saved = await savePreferences({ defaultView: value });
        savingView = false;
        syncDisplaySettings();
        status.textContent = saved ? '다음에 접속할 때 선택한 화면으로 시작합니다.' : '시작 화면을 저장하지 못했습니다. 다시 선택해 주세요.';
    });
})();
