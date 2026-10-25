// ═══════════════════════════════════════════════════════════════════
// 08-admin.js — 관리자 화면 — 계정 목록·승인·초기화·삭제
//
// ⚠️ 이 파일들은 ES 모듈이 아니라 **순서대로 로드되는 클래식 스크립트**다.
//    최상위 let/const/function 은 전역 렉시컬 환경을 공유하므로, 예전 script.js
//    한 덩어리였을 때와 실행 의미가 완전히 같다. (HTML 의 인라인 onclick 핸들러가
//    전역 함수를 그대로 부르고 있어 모듈로 바꾸면 그것들이 전부 깨진다)
//    → 실행 순서는 파일명 번호 순서다(app/utils/scriptbundle.py 가 그 순서로 이어 붙인다).
// ═══════════════════════════════════════════════════════════════════

let adminUsersData = [];
let currentAdminSort = { key: 'created_at', asc: false };

window.loadAdminUsers = async function() {
    if (!adminUserList) return;
    refreshBackupStatus();
    adminUserList.innerHTML = '<tr><td colspan="5" style="text-align:center; padding: 20px;">불러오는 중...</td></tr>';
    try {
        const res = await fetch('/api/admin/users');
        if (!res.ok) throw new Error("권한이 없습니다.");
        adminUsersData = await res.json();
        renderAdminUsers();
        // 목록을 새로 받을 때마다 '남은 할 일' 기준으로 배지를 다시 계산한다.
        window.refreshAdminBadges();
    } catch(e) {
        adminUserList.innerHTML = '<tr><td colspan="5" style="text-align:center; padding: 20px; color: var(--danger-color);">데이터를 불러오지 못했습니다.</td></tr>';
    }
};

window.sortAdminUsers = function(key) {
    if (currentAdminSort.key === key) {
        currentAdminSort.asc = !currentAdminSort.asc;
    } else {
        currentAdminSort.key = key;
        currentAdminSort.asc = false; // 새로운 정렬 기준 선택 시 기본 내림차순(최신순/많은순)
        if (key === 'username') currentAdminSort.asc = true; // 이름은 오름차순(가나다순)이 기본
    }
    renderAdminUsers();
};

window.renderAdminUsers = function() {
    if (!adminUserList) return;

    // 정렬 아이콘 업데이트
    ['username', 'created_at', 'last_login_at', 'entry_count'].forEach(key => {
        const iconEl = document.getElementById('sortIcon_' + key);
        if (iconEl) {
            iconEl.innerText = (currentAdminSort.key === key) ? (currentAdminSort.asc ? '▲' : '▼') : '↕';
            iconEl.style.color = (currentAdminSort.key === key) ? 'var(--primary-color)' : 'var(--text-muted-color)';
        }
    });

    // 관리자와 일반 사용자 분리
    const adminUser = adminUsersData.find(u => u.is_admin);
    const regularUsers = adminUsersData.filter(u => !u.is_admin);

    // 데이터 정렬 (일반 사용자만)
    const sortedData = [...regularUsers].sort((a, b) => {
        let valA = a[currentAdminSort.key];
        let valB = b[currentAdminSort.key];

        if (currentAdminSort.key === 'entry_count') {
            valA = parseInt(valA) || 0;
            valB = parseInt(valB) || 0;
        } else if (currentAdminSort.key === 'created_at' || currentAdminSort.key === 'last_login_at') {
            valA = valA ? new Date(valA.replace(' ', 'T')).getTime() : 0;
            valB = valB ? new Date(valB.replace(' ', 'T')).getTime() : 0;
        } else {
            valA = (valA || '').toString().toLowerCase();
            valB = (valB || '').toString().toLowerCase();
        }

        if (valA < valB) return currentAdminSort.asc ? -1 : 1;
        if (valA > valB) return currentAdminSort.asc ? 1 : -1;
        return 0;
    });

    adminUserList.innerHTML = '';

    // 최고 관리자 먼저 렌더링 (별도 배경색 및 굵은 하단 테두리로 시각적 분리)
    if (adminUser) {
        const createdStr = adminUser.created_at || '-';
        const lastLoginStr = adminUser.last_login_at || '-';
        const tr = document.createElement('tr');
        tr.style.backgroundColor = 'var(--bg-color)';
        tr.style.borderBottom = '2px solid var(--primary-color)';
        tr.innerHTML = `
            <td data-label="사용자명" style="padding: 10px; font-weight: bold; color: var(--primary-color); white-space: nowrap;">
                <div class="user-badge-wrapper">
                    <span class="user-name-text">${adminUser.username}</span>
                    <span style="font-size:11px; font-weight:normal; color:var(--text-muted-color);">👑 (최고 관리자)</span>
                </div>
            </td>
            <td data-label="가입 일시" style="padding: 10px; color: var(--text-muted-color); font-size: 12px; white-space: nowrap;">${createdStr}</td>
            <td data-label="최근 로그인" style="padding: 10px; color: var(--text-muted-color); font-size: 12px; white-space: nowrap;">${lastLoginStr}</td>
            <td data-label="기록 수" style="padding: 10px; font-weight: bold; white-space: nowrap;">${adminUser.entry_count}건</td>
            <td data-label="관리" style="padding: 10px; text-align: right; white-space: nowrap;">
                <div class="admin-action-container">
                    <button onclick="resetUserPassword('${adminUser.username}')" class="admin-action-btn" style="border: 1px solid var(--warning-color); color: var(--warning-color);">비번 초기화</button>
                </div>
            </td>
        `;
        adminUserList.appendChild(tr);
    }

    // 일반 사용자 목록 렌더링
    if (sortedData.length > 0) {
        sortedData.forEach(u => {
            const isAllowed = u.is_allowed;
            const allowBtnText = isAllowed ? '제한' : '허용';
            const allowBtnBg = isAllowed ? 'var(--neutral-color)' : 'var(--success-color)';
            const createdStr = u.created_at || '-';
            const lastLoginStr = u.last_login_at || '-';

            // ⭐️ 로그인 화면에서 접수된 비밀번호 재설정 요청을 한눈에 보이게 한다.
            const hasReset = !!u.reset_requested_at;
            const resetBadge = hasReset
                ? `<span style="font-size: 10px; background: var(--warning-color); color: #fff; padding: 1px 5px; border-radius: 3px; white-space: nowrap;">🔑 초기화 요청${u.reset_request_count > 1 ? ' ×' + u.reset_request_count : ''}</span>`
                : '';
            // ⭐️ 사용자가 남긴 메모는 툴팁으로만 두면 아무도 보지 못한다. 표에 펼쳐 보여준다.
            const resetDetail = hasReset
                ? `<div style="margin-top: 4px; font-weight: normal; font-size: 11px; color: var(--text-muted-color); line-height: 1.4; white-space: normal;">`
                  + `<span style="color: var(--warning-color);">요청 ${escapeHtml(u.reset_requested_at)}</span>`
                  + (u.reset_note ? `<br>💬 ${escapeHtml(u.reset_note)}` : '<br><span style="opacity:.7;">(남긴 메모 없음)</span>')
                  + `</div>`
                : '';
            // 임시 비밀번호를 아직 안 바꾼 계정도 표시한다.
            const mustChangeBadge = u.must_change_password
                ? `<span title="임시 비밀번호 상태 — 다음 로그인에서 변경이 강제됩니다" style="font-size: 10px; background: var(--neutral-color); color: #fff; padding: 1px 5px; border-radius: 3px; white-space: nowrap;">임시 비번</span>`
                : '';

            const tr = document.createElement('tr');
            tr.style.borderBottom = '1px solid var(--border-light-color)';
            if (hasReset) tr.style.background = 'rgba(243, 156, 18, 0.08)';
            tr.innerHTML = `
                <td data-label="사용자명" style="padding: 10px; font-weight: bold; color: var(--text-strong-color); word-break: break-all; min-width: 140px;">
                    <div class="user-badge-wrapper">
                        <span class="user-name-text">${escapeHtml(u.username)}</span>
                        ${resetBadge}
                        ${mustChangeBadge}
                    </div>
                    ${resetDetail}
                </td>
                <td data-label="가입 일시" style="padding: 10px; color: var(--text-muted-color); font-size: 12px; white-space: nowrap;">${createdStr}</td>
                <td data-label="최근 로그인" style="padding: 10px; color: var(--text-muted-color); font-size: 12px; white-space: nowrap;">${lastLoginStr}</td>
                <td data-label="기록 수" style="padding: 10px; white-space: nowrap;">${u.entry_count}건</td>
                <td data-label="관리" style="padding: 10px; text-align: right; white-space: nowrap;">
                    <div class="admin-action-container">
                        <button onclick="toggleUserAllow('${escapeJsInAttr(u.username)}')" class="admin-action-btn" style="border: 1px solid ${allowBtnBg}; color: ${allowBtnBg};">${allowBtnText}</button>
                        <button onclick="resetUserPassword('${escapeJsInAttr(u.username)}')" class="admin-action-btn" style="border: 1px solid var(--warning-color); color: var(--warning-color);">비번 초기화</button>
                        ${hasReset ? `<button onclick="dismissResetRequest('${escapeJsInAttr(u.username)}')" title="비밀번호를 바꾸지 않고 요청만 내립니다" class="admin-action-btn" style="border: 1px solid var(--neutral-color); color: var(--neutral-color);">요청 해제</button>` : ''}
                        <button onclick="deleteUserAccount('${escapeJsInAttr(u.username)}')" class="admin-action-btn" style="border: 1px solid var(--danger-color); color: var(--danger-color);">삭제</button>
                    </div>
                </td>
            `;
            adminUserList.appendChild(tr);
        });
    } else {
        const tr = document.createElement('tr');
        tr.innerHTML = '<td colspan="5" style="padding: 20px; text-align: center; color: var(--text-muted-color);">가입한 일반 사용자가 없습니다.</td>';
        adminUserList.appendChild(tr);
    }
};

// ⭐️ 관리자 알림 배지(가입 승인 대기 + 비밀번호 초기화 요청)를 화면에 반영한다.
//    M 버튼과 톱니바퀴(⚙️) 두 곳에 같은 값을 붙이고, 0 이면 양쪽 모두 지운다.
//    (접힌 상태에서는 M 이 보이지 않고, 펼친 상태에서는 톱니바퀴가 사라지기 때문)
window.applyAdminBadges = function(count) {
    const btnAdmin = document.getElementById('btnAdmin');
    const group = document.querySelector('.header-action-group');

    [btnAdmin, group].forEach(host => {
        if (!host) return;
        const existing = host.querySelector(':scope > .admin-notification-badge');
        if (!count) { if (existing) existing.remove(); return; }
        host.style.position = 'relative';
        if (existing) { existing.innerText = count; return; }
        const badge = document.createElement('span');
        badge.className = 'admin-notification-badge';
        badge.innerText = count;
        host.appendChild(badge);
    });
};

// ⭐️ 처리해야 할 일이 남았는지 서버에 다시 물어 배지를 갱신한다.
//    승인/초기화/요청 해제/삭제 후 호출해, 할 일이 없어지면 배지가 저절로 사라진다.
window.refreshAdminBadges = async function() {
    try {
        const res = await fetch('/api/me');
        if (!res.ok) return;
        const me = await res.json();
        if (!me.is_admin) { window.applyAdminBadges(0); return; }
        window.applyAdminBadges((me.pending_count || 0) + (me.reset_request_count || 0));
    } catch (e) { /* 배지 갱신 실패는 조용히 넘긴다 */ }
};

// ⭐️ 비밀번호를 바꾸지 않고 요청만 내린다 (본인이 다시 기억해낸 경우 등).
window.dismissResetRequest = async function(username) {
    if (!await customConfirm(`'${username}' 의 비밀번호 초기화 요청을 해제할까요?\n(비밀번호는 바뀌지 않습니다)`)) return;
    try {
        const res = await fetch(`/api/admin/password_resets/${encodeURIComponent(username)}`, { method: 'DELETE' });
        if (res.ok) { loadAdminUsers(); }
        else { await customAlert('요청 해제에 실패했습니다.'); }
    } catch(e) { await customAlert('통신 오류가 발생했습니다.'); }
};

window.toggleUserAllow = async function(username) {
    try {
        const res = await fetch(`/api/admin/users/${username}/toggle_allow`, { method: 'POST'});
        if (res.ok) {
            loadAdminUsers();
        } else {
            const data = await res.json();
                await customAlert(data.error || "상태 변경에 실패했습니다.");
        }
        } catch(e) { await customAlert("통신 오류가 발생했습니다."); }
};

window.resetUserPassword = async function(username) {
    if (await customConfirm(`'${username}' 사용자의 비밀번호를 초기화하시겠습니까?\n기존 로그인과 모든 API 키가 폐기되며, 연동된 봇은 새 키로 다시 연결해야 합니다.`)) {
        try {
            const res = await fetch(`/api/admin/users/${username}/reset_password`, { method: 'POST'});
            if (res.ok) {
                const data = await res.json();
                await customAlert(`'${username}' 계정의 비밀번호가 [ ${data.new_password} ] 로 초기화되었습니다.\n사용자에게 이 임시 비밀번호를 전달해 주세요.\n\n기존 로그인과 API 키 ${data.api_keys_revoked}개를 폐기했습니다. 새 비밀번호를 설정한 후 봇 연동 키를 다시 발급해주세요.`);
                // ⭐️ 처리한 요청이 목록·배지에서 바로 사라지도록 다시 불러온다.
                loadAdminUsers();
            } else {
                await customAlert("초기화에 실패했습니다.");
            }
        } catch(e) { await customAlert("통신 오류가 발생했습니다."); }
    }
};

window.deleteUserAccount = async function(username) {
    const confirmName = await customPrompt(`경고: '${username}' 사용자와 관련된 모든 기록과 첨부파일이 영구적으로 삭제됩니다.\n\n계속하시려면 삭제할 아이디('${username}')를 아래에 정확히 입력해주세요.`, '입력');
    if (confirmName === username) {
        try {
            const res = await fetch(`/api/admin/users/${username}`, { method: 'DELETE'});
            if (res.ok) {
                await customAlert(`'${username}' 계정이 삭제되었습니다.`);
                loadAdminUsers();
            } else {
                await customAlert("삭제에 실패했습니다.");
            }
        } catch(e) { await customAlert("통신 오류가 발생했습니다."); }
    } else if (confirmName !== null) {
        await customAlert("입력한 아이디가 일치하지 않아 삭제가 취소되었습니다.");
    }
};



async function refreshBackupStatus() {
    const status = document.getElementById('adminBackupStatus');
    const button = document.getElementById('adminBackupRun');
    if (!status || !button) return;
    try {
        const response = await fetchWithTimeout('/api/admin/backup');
        if (!response.ok) throw new Error('백업 상태를 불러오지 못했습니다.');
        const data = await response.json();
        const stamp = value => value ? new Date(value).toLocaleString('ko-KR', {timeZone: 'Asia/Seoul'}) + ' (한국 시간)' : '없음';
        const state = {never: '실행 이력 없음', running: '실행 중', success: '성공', failed: '실패', interrupted: '이전 작업 중단'}[data.state] || data.state;
        status.textContent = `상태: ${state} · 마지막 성공: ${stamp(data.last_success)} · 다음 예약: ${stamp(data.next_run)}`
            + (data.state === 'failed' ? ` · DB: ${data.database_ok ? '성공' : '실패'}, 사용자 ZIP 실패: ${data.failed_users}/${data.total_users}. 서버 로그를 확인해주세요.` : '');
        button.disabled = data.state === 'running';
    } catch (error) { status.textContent = error.message; button.disabled = false; }
}
document.getElementById('adminBackupRefresh')?.addEventListener('click', refreshBackupStatus);
document.getElementById('adminBackupRun')?.addEventListener('click', async event => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
        const response = await fetchWithTimeout('/api/admin/backup', {method: 'POST'});
        if (!response.ok) throw new Error((await response.json()).error || '백업을 시작하지 못했습니다.');
        document.getElementById('adminBackupStatus').textContent = '백업을 시작했습니다. 잠시 후 상태 새로고침으로 결과를 확인해주세요.';
    } catch (error) { await customAlert(error.message); button.disabled = false; }
});
