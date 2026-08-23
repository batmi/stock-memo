// ═══════════════════════════════════════════════════════════════════
// 19-account-menu.js — 비밀번호 변경·HTS 연동(API 키·봇) 모달
//
// ⚠️ 이 파일들은 ES 모듈이 아니라 **순서대로 로드되는 클래식 스크립트**다.
//    최상위 let/const/function 은 전역 렉시컬 환경을 공유하므로, 예전 script.js
//    한 덩어리였을 때와 실행 의미가 완전히 같다. (HTML 의 인라인 onclick 핸들러가
//    전역 함수를 그대로 부르고 있어 모듈로 바꾸면 그것들이 전부 깨진다)
//    → 로드 순서는 templates/stock-memo.html 의 <script> 순서가 결정한다. 바꾸지 말 것.
// ═══════════════════════════════════════════════════════════════════

const passwordModalOverlay = document.getElementById('passwordModalOverlay');
const btnChangePassword = document.getElementById('btnChangePassword');
const btnClosePasswordModal = document.getElementById('btnClosePasswordModal');
const passwordForm = document.getElementById('passwordForm');
const htsIntegrationModalOverlay = document.getElementById('htsIntegrationModalOverlay');
const btnHtsIntegration = document.getElementById('btnHtsIntegration');
const btnCloseHtsIntegrationModal = document.getElementById('btnCloseHtsIntegrationModal');

if (btnChangePassword && passwordModalOverlay) {
    // ⭐️ 상태 표시등은 모달이 열려 있는 동안만 주기적으로 갱신한다.
    //    (예전에는 모달을 열 때 딱 한 번만 조회해서, 열어둔 채로 HTS 를 꺼도
    //     표시가 '정상 가동중'에서 영영 바뀌지 않았다)
    const BOT_STATUS_POLL_MS = 5000;
    let botStatusPollTimer = null;

    function startBotStatusPolling() {
        stopBotStatusPolling();
        botStatusPollTimer = setInterval(fetchBotStatusOnly, BOT_STATUS_POLL_MS);
    }

    function stopBotStatusPolling() {
        if (botStatusPollTimer) {
            clearInterval(botStatusPollTimer);
            botStatusPollTimer = null;
        }
    }

    // 폴링 중에는 상태만 다시 읽는다 — 키 목록까지 매번 받아올 이유가 없고,
    // 방금 발급해 화면에 떠 있는 키 원문을 건드릴 위험도 없다.
    async function fetchBotStatusOnly() {
        // 모달이 닫혔는데 타이머만 살아있는 경우를 방어
        if (htsIntegrationModalOverlay && (htsIntegrationModalOverlay.style.display === 'none' || !htsIntegrationModalOverlay.style.display)) {
            stopBotStatusPolling();
            return;
        }
        try {
            const res = await fetch('/api/me');
            if (res.ok) renderBotStatus(await res.json());
        } catch(e) { /* 일시적 통신 오류는 다음 주기에 회복된다 */ }
    }

    // ⭐️ 상태 판정은 서버(/api/me)가 확정해 내려주는 bot_state 를 그대로 그린다.
    //    브라우저 시계·타임존에 따라 표시가 달라지지 않게 하기 위함이다.
    function renderBotStatus(meData) {
        const indicator = document.getElementById('botStatusIndicator');
        const lastSeenText = document.getElementById('botLastSeenText');
        if (!indicator || !lastSeenText) return;

        const STATE_UI = {
            never:   { text: '연결 기록 없음',   color: 'var(--text-muted-color)' },
            running: { text: '🟢 정상 가동중',   color: 'var(--success-color)' },
            stopped: { text: '🟡 정지됨',        color: 'var(--warning-color)' },
            error:   { text: '🔴 오류',          color: 'var(--danger-color)' },
            offline: { text: '🔴 통신단절',      color: 'var(--danger-color)' }
        };
        const ui = STATE_UI[meData.bot_state] || STATE_UI.never;
        indicator.innerText = ui.text;
        indicator.style.color = ui.color;

        // 봇 목록은 대표 상태가 'never' 여도 그린다 — 연결 기록 없는 봇도 목록에는 남는다.
        knownBots = Array.isArray(meData.bots) ? meData.bots : [];
        renderBotInstances(knownBots);
        syncResyncBotPicker(knownBots);

        if (meData.bot_state === 'never' || !meData.bot_last_seen) {
            lastSeenText.innerText = '-';
            return;
        }

        lastSeenText.innerText = formatBotTimestamp(meData.bot_last_seen)
            + formatElapsed(meData.bot_elapsed_seconds);
    }

    function formatElapsed(elapsed) {
        if (elapsed === null || elapsed === undefined) return '';
        if (elapsed < 60) return ` (${Math.max(0, Math.round(elapsed))}초 전)`;
        if (elapsed < 3600) return ` (${Math.floor(elapsed / 60)}분 전)`;
        return ` (${Math.floor(elapsed / 3600)}시간 전)`;
    }

    // ⭐️ HTS 를 여러 대 돌리면 위의 대표 표시등은 '가장 나쁜 봇'을 가리킨다.
    //    어느 봇이 그런지 알 수 없으면 그 표시는 쓸모가 없으므로 목록을 함께 그린다.
    //    한 대뿐이어도 그린다 — '정상 가동중'만으로는 어느 HTS 가 붙어 있는지 알 수 없다.
    let knownBots = [];

    function renderBotInstances(bots) {
        const box = document.getElementById('botInstanceList');
        if (!box) return;
        if (!Array.isArray(bots) || bots.length === 0) {
            box.style.display = 'none';
            box.innerHTML = '';
            return;
        }
        const DOT = {
            running: ['🟢', 'var(--success-color)'], stopped: ['🟡', 'var(--warning-color)'],
            error:   ['🔴', 'var(--danger-color)'],  offline: ['🔴', 'var(--danger-color)'],
            never:   ['⚪', 'var(--text-muted-color)']
        };
        box.style.display = 'block';
        box.innerHTML = bots.map(b => {
            const [dot, color] = DOT[b.state] || DOT.never;
            const name = b.label || b.botId;
            // 라벨이 이미 '모의'를 말하고 있으면 배지를 겹쳐 붙이지 않는다.
            const sim = (b.isSimulated && !name.includes('모의'))
                ? ' <span style="opacity: .6;">모의</span>' : '';
            const ago = formatElapsed(b.elapsedSeconds).trim() || '-';
            // 죽은 봇만 지울 수 있게 한다 — 살아 있는 봇은 지워도 10초 뒤 되살아나
            // 버튼이 아무 일도 안 한 것처럼 보인다.
            const removable = b.state !== 'running'
                ? `<button type="button" class="bot-forget" data-bot-id="${escapeHtml(b.botId)}"`
                  + ` title="목록에서 지우기 (가동 중이면 다시 등록됩니다)"`
                  + ` style="margin: 0; padding: 0 4px; width: auto; background: none; border: none;`
                  + ` box-shadow: none; color: var(--text-muted-color); font-size: 11px; cursor: pointer;">✕</button>`
                : '';
            // 계좌가 둘인 봇(한투 실전 = 거래계좌 + 자동매매계좌)은 라벨이 '·'로 이어져
            // 온다. 한 줄에 밀어 넣으면 뒤쪽 계좌가 말줄임으로 잘려 정작 자동매매가 도는
            // 계좌를 못 읽는다. 줄을 나누되 표시등은 첫 줄에만 둔다 — 봇은 하나다.
            const [head, ...rest] = name.split('·').map(part => part.trim()).filter(Boolean);
            // ⭐️ 라벨만 띄우면 식별자가 겹쳐 두 봇이 한 줄에 포개진 것을 알아챌 수 없다
            //    (겹치면 라벨이 Ping 마다 뒤집힌다). botId 를 툴팁으로 노출해 확인 가능하게 한다.
            const extra = rest.map(part =>
                `<div style="color: ${color}; padding-left: 1.4em; overflow: hidden;`
                + ` text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(part)}</div>`).join('');
            return `<div title="${escapeHtml(b.botId)}">`
                 + '<div style="display: flex; justify-content: space-between; gap: 8px; align-items: center;">'
                 + `<span style="color: ${color}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">`
                 + `${dot} ${escapeHtml(head || name)}${sim}</span>`
                 + '<span style="color: var(--text-muted-color); white-space: nowrap;">'
                 + `${escapeHtml(ago)}${removable}</span></div>`
                 + `${extra}</div>`;
        }).join('');

        box.querySelectorAll('.bot-forget').forEach(btn => {
            btn.addEventListener('click', () => forgetBot(btn.dataset.botId));
        });
    }

    async function forgetBot(botId) {
        if (!(await customConfirm(
            `'${botId}' 봇을 목록에서 지웁니다.\n\n`
            + '매매 기록은 지워지지 않습니다. 봇이 아직 돌고 있다면 다음 연결에 다시 나타납니다.\n계속하시겠습니까?'))) return;
        try {
            const res = await fetch(`/api/me/bot/registration/${encodeURIComponent(botId)}`,
                                    { method: 'DELETE' });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                await customAlert(err.error || '봇을 지우지 못했습니다.');
                return;
            }
            await fetchBotStatusOnly();
        } catch (e) {
            await customAlert('오류가 발생했습니다.');
        }
    }

    // 봇이 둘 이상이면 재동기화 대상을 반드시 골라야 한다 — 서버가 대상 없는 요청을
    // 400 으로 막는다(아무 봇이나 채가면 엉뚱한 계좌가 재동기화되고 화면엔 '완료'로 뜬다).
    function syncResyncBotPicker(bots) {
        const picker = document.getElementById('resyncBotPicker');
        const select = document.getElementById('resyncBotSelect');
        if (!picker || !select) return;
        if (!Array.isArray(bots) || bots.length < 2) {
            picker.style.display = 'none';
            return;
        }
        const previous = select.value;
        const options = bots.map(b =>
            `<option value="${escapeHtml(b.botId)}">${escapeHtml(b.label || b.botId)}</option>`).join('');
        // 갱신 때마다 통째로 다시 그리면 사용자가 고르던 항목이 풀린다 — 목록이
        // 실제로 바뀌었을 때만 교체하고, 선택은 살려 둔다.
        if (select.innerHTML !== options) {
            select.innerHTML = options;
            if (bots.some(b => b.botId === previous)) select.value = previous;
        }
        picker.style.display = 'block';
    }

    // 서버는 오프셋 포함 ISO 8601 로 내려주지만, 이전 버전이 남긴 오프셋 없는
    // 'YYYY-MM-DD HH:MM:SS' 값도 그대로 보여줄 수 있어야 한다.
    function formatBotTimestamp(raw) {
        const dt = new Date(raw);
        if (isNaN(dt.getTime())) return raw;
        const p = n => String(n).padStart(2, '0');
        return `${dt.getFullYear()}-${p(dt.getMonth() + 1)}-${p(dt.getDate())} `
             + `${p(dt.getHours())}:${p(dt.getMinutes())}:${p(dt.getSeconds())}`;
    }

    btnChangePassword.addEventListener('click', () => {
        passwordModalOverlay.style.display = 'flex';
        document.body.style.overflow = 'hidden';
    });

    if (btnHtsIntegration && htsIntegrationModalOverlay) {
        btnHtsIntegration.addEventListener('click', () => {
            htsIntegrationModalOverlay.style.display = 'flex';
            document.body.style.overflow = 'hidden';
            fetchApiKeyStatus();
            startBotStatusPolling();
        });
    }

    // ⭐️ HTS 연동 API 키 발급 및 복사 로직 추가
    async function fetchApiKeyStatus() {
        try {
            const [keyRes, meRes] = await Promise.all([
                fetch('/api/me/api-key'),
                fetch('/api/me')
            ]);
            
            if (keyRes.ok) {
                const data = await keyRes.json();
                // ⭐️ 서버는 키를 해시로만 보관하므로 원문(api_key)은 항상 null 이다.
                //    발급 직후 화면에 떠 있는 값은 지우지 않고 목록만 갱신한다.
                updateApiKeyUI(data.api_key);
                renderApiKeyList(data.keys || []);
            }

            if (meRes.ok) {
                const meData = await meRes.json();
                renderBotStatus(meData);
            }
        } catch(e) { console.error('API Key/Status 로드 실패', e); }
    }
    
    function updateApiKeyUI(apiKey) {
        const input = document.getElementById('apiKeyValue');
        const container = document.getElementById('newApiKeyContainer');
        if (!input) return;
        // 원문이 없으면(재조회 시) 이미 표시 중인 값을 지우지 않는다 —
        // 방금 발급받은 키를 사용자가 복사하기 전에 사라지면 영영 볼 수 없다.
        if (apiKey) {
            input.value = apiKey;
            if (container) container.style.display = 'flex';
        }
    }

    // ⭐️ 발급된 키 목록. 원문은 없고 식별용 앞자리·사용 이력만 보여준다.
    function renderApiKeyList(keys) {
        const box = document.getElementById('apiKeyList');
        if (!box) return;

        const active = keys.filter(k => !k.revoked_at);
        if (!active.length) {
            box.innerHTML = '<span style="opacity:0.7;">발급된 키가 없습니다.</span>';
            return;
        }

        box.innerHTML = active.map(k => `
            <div style="display:flex; justify-content:space-between; align-items:center; gap:6px; padding:3px 0;">
                <div style="display:flex; flex-direction:column; overflow:hidden;">
                    <span style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
                        <code>${escapeHtml(k.key_prefix)}…</code>
                        ${escapeHtml(k.label || '')}
                    </span>
                    <span style="opacity:0.6; font-size:9.5px; margin-top:2px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">
                        최근 사용: ${escapeHtml(k.last_used_at || '없음')}
                    </span>
                </div>
                <button type="button" data-key-id="${k.id}" class="btnRevokeApiKey"
                        style="margin:0; padding:2px 6px; font-size:10px; width:auto; background:transparent;
                               border:1px solid var(--danger-color); color:var(--danger-color); border-radius:4px; flex-shrink:0; white-space:nowrap;">폐기</button>
            </div>`).join('');

        box.querySelectorAll('.btnRevokeApiKey').forEach(btn => {
            btn.addEventListener('click', async () => {
                if (!(await customConfirm("이 키를 폐기하시겠습니까?\n이 키로 발급된 접속 토큰도 즉시 무효화됩니다."))) return;
                const res = await fetch(`/api/me/api-key/${btn.dataset.keyId}`, { method: 'DELETE' });
                if (res.ok) {
                    const container = document.getElementById('newApiKeyContainer');
                    if (container) container.style.display = 'none';
                    document.getElementById('apiKeyValue').value = '';
                    fetchApiKeyStatus();
                } else {
                    await customAlert("키 폐기에 실패했습니다.");
                }
            });
        });
    }

    const btnGenerateApiKey = document.getElementById('btnGenerateApiKey');
    if (btnGenerateApiKey) {
        btnGenerateApiKey.addEventListener('click', async () => {
            if (!(await customConfirm("새 API 키를 발급하시겠습니까?\n기존 키를 사용하던 모든 연결이 즉시 끊어집니다."))) return;
            try {
                const res = await fetch('/api/me/api-key', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ label: 'HTS 연동 키' })
                });
                if(res.ok) {
                    const data = await res.json();
                    updateApiKeyUI(data.api_key);
                    renderApiKeyList([]);
                    await customAlert("새 API 키가 발급되었습니다.\n\n⚠️ 이 키는 지금 화면에 표시된 것이 마지막입니다.\n창을 닫기 전에 반드시 복사해 두세요.");
                    fetchApiKeyStatus();
                } else {
                    await customAlert("API 키 발급에 실패했습니다.");
                }
            } catch(e) {
                await customAlert("오류가 발생했습니다.");
            }
        });
    }
    
    // ── 재동기화 ───────────────────────────────────────────────────────
    //  웹에서 지운 기록을 봇에서 다시 받아온다. 봇은 대개 가정용 네트워크 뒤에
    //  있어 서버가 먼저 접속할 수 없으므로, 요청을 큐에 쌓아 두면 봇이 다음
    //  Ping(최대 10초) 때 가져간다. 그래서 버튼을 눌러도 결과는 조금 뒤에 나온다.
    let resyncPollTimer = null;

    function renderResyncStatus(cmd) {
        const box = document.getElementById('resyncResult');
        if (!box) return;
        if (!cmd) { box.innerHTML = ''; return; }

        const period = (cmd.params && cmd.params.from)
            ? `${cmd.params.from} ~ ${cmd.params.to || '현재'}` : '전체';
        const pending = {
            pending: `<span style="color: var(--warning-color);">⏳ 요청됨</span> — 봇이 받아가기를 기다리는 중 (${escapeHtml(period)})`,
            running: `<span style="color: var(--info-color);">🔄 처리 중</span> — 봇이 재동기화하고 있습니다 (${escapeHtml(period)})`,
            expired: `<span style="color: var(--danger-color);">⚠️ 미처리</span> — 봇이 가져가지 않았습니다. 봇이 꺼져 있지 않은지 확인 후 다시 시도하세요.`,
        };
        if (cmd.state !== 'done') {
            box.innerHTML = pending[cmd.state] || '';
            return;
        }

        // 완료 — 복구된 건수와 이미 있던 건수를 나눠 보여준다. 이 두 숫자가 곧
        // '무엇이 얼마나 지워져 있었는지'에 대한 답이 된다.
        const n = cmd.result_count || 0;
        if (cmd.result === 'failed') {
            box.innerHTML = `<span style="color: var(--danger-color);">❌ 실패</span> — ${escapeHtml(cmd.result_message || '사유 미상')}`;
        } else if (n > 0) {
            box.innerHTML = `<span style="color: var(--success-color);">✅ 완료</span> — ${n}건을 다시 전송했습니다. `
                + `<span style="color: var(--text-muted-color);">${escapeHtml(cmd.result_message || '')}</span>`;
        } else {
            box.innerHTML = `<span style="color: var(--success-color);">✅ 완료</span> — 빠진 기록이 없었습니다.`;
        }
    }

    // 대상 봇이 선택돼 있으면 그 봇의 명령만 본다 — 다른 봇의 재동기화 결과를
    // 이 패널에 그리면 방금 누른 요청이 이미 끝난 것처럼 보인다.
    function selectedResyncBotId() {
        const picker = document.getElementById('resyncBotPicker');
        const select = document.getElementById('resyncBotSelect');
        if (!picker || picker.style.display === 'none' || !select) return null;
        return select.value || null;
    }

    async function pollResyncStatus() {
        try {
            const botId = selectedResyncBotId();
            const url = botId ? `/api/me/bot/resync?botId=${encodeURIComponent(botId)}`
                              : '/api/me/bot/resync';
            const res = await fetch(url);
            if (!res.ok) return;
            const cmd = (await res.json()).command;
            renderResyncStatus(cmd);
            // 끝났으면 폴링을 멈춘다 — 계정 설정 창은 오래 열어두는 화면이다.
            if (!cmd || cmd.state === 'done' || cmd.state === 'expired') {
                if (resyncPollTimer) { clearInterval(resyncPollTimer); resyncPollTimer = null; }
                // 실제로 복구된 게 있을 때만 목록을 다시 읽는다 — 빠진 게 없었는데
                // 전체 재조회를 걸면 느린 회선에서 화면이 괜히 한 번 멈춘다.
                if (cmd && cmd.state === 'done' && (cmd.result_count || 0) > 0) loadDataFromLocal();
            }
        } catch (e) { /* 폴링 실패는 다음 주기에 다시 시도한다 */ }
    }

    async function requestResync(payload, label) {
        if (!(await customConfirm(
            `${label} 기간의 매매 기록을 봇에서 다시 받아옵니다.\n\n`
            + '이미 있는 기록은 건너뛰므로 중복이 생기지 않습니다.\n계속하시겠습니까?'))) return;
        try {
            const res = await fetch('/api/me/bot/resync', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                await customAlert(err.error || '재동기화 요청에 실패했습니다.');
                return;
            }
            renderResyncStatus((await res.json()).command);
            if (resyncPollTimer) clearInterval(resyncPollTimer);
            resyncPollTimer = setInterval(pollResyncStatus, 3000);
        } catch (e) {
            await customAlert('오류가 발생했습니다.');
        }
    }

    const btnResync = document.getElementById('btnResync');
    if (btnResync) {
        btnResync.addEventListener('click', () => {
            const panel = document.getElementById('resyncPanel');
            const opening = panel.style.display === 'none';
            panel.style.display = opening ? 'block' : 'none';
            if (opening) pollResyncStatus();
        });
    }

    document.querySelectorAll('.resync-preset').forEach(btn => {
        const labels = { quarter: '최근 분기(90일)', half: '최근 반기(180일)', year: '최근 1년(365일)' };
        btn.addEventListener('click', () => {
            const botId = selectedResyncBotId();
            const bot = knownBots.find(b => b.botId === botId);
            // 봇을 고른 경우엔 확인 문구에 그 이름을 넣는다 — 어느 계좌를 되돌리는지
            // 모른 채 누르면 엉뚱한 계좌의 기록이 되살아난다.
            const label = labels[btn.dataset.preset]
                + (bot ? ` · ${bot.label || bot.botId}` : '');
            requestResync({ preset: btn.dataset.preset, botId }, label);
        });
    });

    const btnCopyApiKey = document.getElementById('btnCopyApiKey');
    if (btnCopyApiKey) {
        btnCopyApiKey.addEventListener('click', async () => {
            const input = document.getElementById('apiKeyValue');
            if (!input.value) {
                await customAlert("발급된 키가 없습니다.");
                return;
            }
            input.select();
            input.setSelectionRange(0, 99999);
            try {
                await navigator.clipboard.writeText(input.value);
                await customAlert("API 키가 복사되었습니다.");
            } catch(e) {
                await customAlert("복사 실패. 수동으로 복사해주세요.");
            }
        });
    }
    
    const closePwModal = () => {
        passwordModalOverlay.classList.add('closing');
        setTimeout(() => {
            passwordModalOverlay.style.display = 'none';
            passwordModalOverlay.classList.remove('closing');
            document.body.style.overflow = '';
            if(passwordForm) passwordForm.reset();
        }, 180);
    };
    
    if (btnClosePasswordModal) btnClosePasswordModal.addEventListener('click', closePwModal);

    const closeHtsModal = () => {
        stopBotStatusPolling(); // 닫힌 모달을 위해 계속 /api/me 를 두드리지 않는다
        htsIntegrationModalOverlay.classList.add('closing');
        setTimeout(() => {
            htsIntegrationModalOverlay.style.display = 'none';
            htsIntegrationModalOverlay.classList.remove('closing');
            document.body.style.overflow = '';
            const container = document.getElementById('newApiKeyContainer');
            if(container) container.style.display = 'none';
            const apiKeyValue = document.getElementById('apiKeyValue');
            if(apiKeyValue) apiKeyValue.value = '';
        }, 180);
    };
    
    if (btnCloseHtsIntegrationModal) btnCloseHtsIntegrationModal.addEventListener('click', closeHtsModal);
    
    if(passwordForm) passwordForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const current_password = document.getElementById('currentPassword').value;
        const new_password = document.getElementById('newPassword').value;
        const new_password_confirm = document.getElementById('newPasswordConfirm').value;
        
        if (new_password !== new_password_confirm) {
            await customAlert("새 비밀번호가 일치하지 않습니다.");
            return;
        }
        
        try {
            const submitBtn = passwordForm.querySelector('button[type="submit"]');
            const origText = submitBtn.innerText;
            submitBtn.innerText = '변경 중...';
            submitBtn.disabled = true;
            
            const res = await fetch('/api/change_password', {
                method: 'POST',
                headers: { 
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    current_password, new_password,
                    revoke_api_keys: !!(document.getElementById('revokeApiKeysOnChange') || {}).checked
                })
            });
            
            const data = await res.json();
            if (res.ok && data.status === 'success') {
                let extra = '';
                if (data.api_keys_revoked) extra = `\nAPI 키 ${data.api_keys_revoked}개를 폐기했습니다.`;
                else if (data.api_keys_remaining) extra = `\n※ API 키 ${data.api_keys_remaining}개는 그대로 유효합니다. 유출이 의심되면 HTS 연동 메뉴에서 폐기하세요.`;
                await customAlert("비밀번호가 성공적으로 변경되었습니다.\n다른 기기의 로그인은 모두 해제되었습니다." + extra + "\n\n새로운 비밀번호로 다시 로그인해주세요.");
                window.location.href = '/logout'; // 로그아웃 처리하여 새 비번으로 로그인 유도
            } else {
                submitBtn.innerText = origText;
                submitBtn.disabled = false;
                await customAlert("변경 실패: " + (data.error || "알 수 없는 오류가 발생했습니다."));
            }
        } catch(err) {
            const submitBtn = passwordForm.querySelector('button[type="submit"]');
            submitBtn.innerText = '변경하기';
            submitBtn.disabled = false;
            await customAlert("비밀번호 변경 중 오류가 발생했습니다.");
        }
    });
}

// ⭐️ 계좌 관리(매핑) 모달 로직
