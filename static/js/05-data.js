// ═══════════════════════════════════════════════════════════════════
// 05-data.js — 서버 데이터 로드(/api/data)·환경설정 저장·실시간 뉴스
//
// ⚠️ 이 파일들은 ES 모듈이 아니라 **순서대로 로드되는 클래식 스크립트**다.
//    최상위 let/const/function 은 전역 렉시컬 환경을 공유하므로, 예전 script.js
//    한 덩어리였을 때와 실행 의미가 완전히 같다. (HTML 의 인라인 onclick 핸들러가
//    전역 함수를 그대로 부르고 있어 모듈로 바꾸면 그것들이 전부 깨진다)
//    → 로드 순서는 templates/stock-memo.html 의 <script> 순서가 결정한다. 바꾸지 말 것.
// ═══════════════════════════════════════════════════════════════════

function ensureCalcLoaded() {
    return new Promise((resolve, reject) => {
        if (typeof window.applyTradeToHolding === 'function') { resolve(); return; }
        console.warn("[Calc] 계산 엔진(applyTradeToHolding) 전역 누락 감지 - calc.js 동적 재로딩 시도");

        // 기존 calc.js 스크립트 태그의 src(버전 쿼리 포함)를 재사용하고 캐시를 무력화
        let baseSrc = null;
        const scripts = document.getElementsByTagName('script');
        for (let i = 0; i < scripts.length; i++) {
            const s = scripts[i].getAttribute('src') || '';
            if (s.indexOf('calc.js') !== -1) { baseSrc = s.split('&reload=')[0]; break; }
        }
        if (!baseSrc) baseSrc = '/static/calc.js';
        const url = baseSrc + (baseSrc.indexOf('?') !== -1 ? '&' : '?') + 'reload=' + Date.now();

        const tag = document.createElement('script');
        tag.src = url;
        tag.onload = () => {
            if (typeof window.applyTradeToHolding === 'function') {
                console.log("[Calc] calc.js 동적 재로딩 성공");
                resolve();
            } else {
                reject(new Error("계산 모듈(calc.js)을 불러왔으나 초기화되지 않았습니다 (브라우저 호환성 문제 가능성)"));
            }
        };
        tag.onerror = () => reject(new Error("계산 모듈(calc.js) 파일을 불러오지 못했습니다 (네트워크 연결을 확인해주세요)"));
        document.head.appendChild(tag);
    });
}

async function loadDataFromLocal() {
    console.log("[Data Load] loadDataFromLocal() 시작 - 사용자 데이터 호출 중...");
    try {
        // ⭐️ 초기 데이터 수신 동안 빈 화면(멈춘 것처럼 보이는 현상) 방지용 로딩 안내
        //    (재시도 시 기존 목록이 이미 렌더링돼 있으면 덮어쓰지 않는다)
        if (historyList && historyList.children.length === 0) {
            historyList.innerHTML = '<p style="text-align:center; color:var(--text-muted-color); font-size: 14px; padding: 30px 0;">⏳ 데이터를 불러오는 중입니다...</p>';
        }

        // ⭐️ 계산 엔진(calc.js)이 준비됐는지 먼저 확인·복구 (없으면 displayEntries 에서 크래시)
        await ensureCalcLoaded();

        // ⭐️ 초기 필수 데이터(사용자 정보, 환경설정, 매매기록)를 병렬로 호출하여 로딩 속도 최적화
        //    (HTML head 에서 미리 발사한 프리페치가 있으면 이어받아 대기 시간 단축)
        // ⭐️ 계좌 매핑도 이 단계에서 함께 받아야 한다. 예전에는 '계좌 관리' 모달을 열거나
        //    매매 기록을 쓸 때만 채워져서, 첫 렌더링 때는 매핑이 비어 있었다. 그 탓에
        //    accountName 이 없는 기록(HTS/봇 수신분)은 별칭 대신 계좌번호가 그대로 노출됐다.
        const [mePromise, prefPromise, dataPromise, mappingPromise] = [
            initialFetchOrFresh('me', '/api/me').catch(e => { console.warn("사용자 정보 로드 실패", e); return null; }),
            initialFetchOrFresh('pref', '/api/preferences').catch(e => { console.warn("환경설정 로드 실패", e); return null; }),
            initialFetchOrFresh('data', '/api/data'),
            initialFetchOrFresh('mappings', '/api/mappings').catch(e => { console.warn("계좌 매핑 로드 실패", e); return null; })
        ];

        console.log("[Data Load] 사용자 정보, 환경설정, 매매 기록, 계좌 매핑 병렬 호출 시작...");
        const [meRes, prefRes, response, mappingRes] = await Promise.all([mePromise, prefPromise, dataPromise, mappingPromise]);

        // 0. 계좌 매핑 처리 — 아래 렌더링이 별칭을 쓰므로 가장 먼저 반영한다.
        if (mappingRes && mappingRes.ok) {
            try {
                const mappingData = await mappingRes.json();
                if (mappingData && typeof mappingData === 'object') {
                    currentAccountMappings = {
                        brokers: mappingData.brokers || {},
                        accounts: mappingData.accounts || {}
                    };
                }
            } catch (e) { console.warn("계좌 매핑 파싱 실패", e); }
        }

        // 1. 사용자 정보 처리
        if (meRes && meRes.ok) {
            try {
                const meData = await meRes.json();
                if (meData.username) {
                    const userDisplay = document.getElementById('loggedInUserDisplay');
                    if (userDisplay) {
                        userDisplay.innerHTML = `<span style="font-size:12px;">👤</span> ${meData.username}`;
                        userDisplay.style.display = 'flex';
                    }
                    if (meData.is_admin) {
                        const btnAdmin = document.getElementById('btnAdmin');
                        if (btnAdmin) {
                            btnAdmin.style.display = 'flex';
                            // ⭐️ 가입 승인 대기 + 비밀번호 초기화 요청을 함께 알린다.
                            const resetCount = meData.reset_request_count || 0;
                            const totalBadge = (meData.pending_count || 0) + resetCount;
                            window.applyAdminBadges(totalBadge);
                            if (totalBadge > 0) {
                                const lines = [];
                                if (meData.pending_count > 0) lines.push(`가입 승인 대기 중인 신규 사용자가 ${meData.pending_count}명 있습니다.`);
                                if (resetCount > 0) lines.push(`비밀번호 초기화를 요청한 사용자가 ${resetCount}명 있습니다.`);
                                setTimeout(async () => { await customAlert(lines.join('\n') + '\n상단 어드민 메뉴에서 확인해주세요.'); }, 500);
                            }
                        }
                    }

                    // ⭐️ 관리자가 임시 비밀번호로 초기화한 계정이면, 다른 작업 전에
                    //    비밀번호부터 바꾸게 한다. (임시 비밀번호가 계속 유효한 채로
                    //    남는 것을 막는다)
                    if (meData.must_change_password) {
                        setTimeout(async () => {
                            await customAlert('임시 비밀번호로 로그인하셨습니다.\n보안을 위해 새 비밀번호를 설정해 주세요.');
                            const btnPw = document.getElementById('btnChangePassword');
                            if (btnPw) btnPw.click();
                        }, 300);
                    }
                }
            } catch(e) { console.warn("사용자 정보 파싱 실패", e); }
        }

        // 2. 환경설정 처리
        // ⭐️ 환경설정 로드가 실패한 채로 진행하면 필터가 모두 풀려 보일 뿐 아니라,
        //    이후 첫 저장 때 빈 userPreferences 가 DB 를 통째로 덮어써 기존 설정이 영구 유실된다.
        //    네트워크 오류·5xx 실패 시 최대 2회 재시도하고, 끝내 실패하면 preferencesLoaded 를
        //    세우지 않아 savePreferences() 가 이번 세션의 서버 저장을 건너뛰게 한다. (401 은 재시도 무의미)
        let prefResFinal = (prefRes && (prefRes.ok || prefRes.status === 401)) ? prefRes : null;
        for (let retry = 0; !prefResFinal && retry < 2; retry++) {
            try {
                console.warn(`[Data Load] 환경설정 로드 실패 - 재시도 ${retry + 1}회차`);
                const r = await fetchWithTimeout('/api/preferences');
                if (r.ok || r.status === 401) prefResFinal = r;
            } catch (e) {
                console.warn(`[Data Load] 환경설정 재시도 ${retry + 1}회차 실패`, e);
            }
        }
        if (prefResFinal && prefResFinal.ok) {
            try {
                userPreferences = await prefResFinal.json();
                preferencesLoaded = true; // ⭐️ 이 시점부터 서버 저장 허용 (기존 설정 덮어쓰기 방지 가드 해제)

                // ⭐️ DB에서 불러온 환경설정을 UI(청산 종목 토글, 접기/펴기 등)에 반영
                if (typeof userPreferences.isDashboardCollapsed !== 'undefined') {
                    isDashboardCollapsed = userPreferences.isDashboardCollapsed;
                }
                // ⭐️ 청산종목 보기 상태 복원 — 포트폴리오/히스토리 두 버튼은 항상 같은 값을 쓴다.
                //    구버전 환경설정에는 두 키가 따로 저장돼 있을 수 있어 포트폴리오 값을 우선하고,
                //    없으면 히스토리 값으로 대체한다. (복원 시점에는 저장을 다시 트리거하지 않는다)
                const savedClosedVisible = (typeof userPreferences.showClosedPositions !== 'undefined')
                    ? userPreferences.showClosedPositions
                    : userPreferences.showHistoryClosedPositions;
                if (typeof savedClosedVisible !== 'undefined') {
                    setClosedPositionsVisible(!!savedClosedVisible, { persist: false });
                }
                // ⭐️ 현재가 1분(60초) 자동 갱신 시작.
                //    예전에는 저장된 '현재가 보기' 설정이 있을 때만 타이머를 걸어서,
                //    설정을 한 번도 건드린 적 없는 신규 계정은 자동 갱신이 아예
                //    돌지 않았다. 토글을 없앤 김에 조건 없이 켠다.
                if (priceUpdateInterval !== null) clearInterval(priceUpdateInterval);
                priceUpdateInterval = setInterval(() => {
                    window.fetchCurrentPricesAndUpdateUI(true); // isAuto = true 로 자동 갱신 요청
                }, 60000);


                // ⭐️ KRX/NXT 버튼 상태 복원
                if (typeof userPreferences.currentMarketMode !== 'undefined') {
                    currentMarketMode = userPreferences.currentMarketMode;
                    const btnMM = document.getElementById('btnToggleMarketMode');
                    if (btnMM) {
                        btnMM.innerText = currentMarketMode === 'NXT' ? 'NXT' : 'KRX';
                        btnMM.style.backgroundColor = currentMarketMode === 'NXT' ? 'transparent' : 'var(--primary-color)';
                        btnMM.style.color = currentMarketMode === 'NXT' ? 'var(--primary-color)' : '#fff';
                    }
                }
                
                // ⭐️ 대시보드 및 하단 리스트 필터 상태 복원 (어긋난 상태를 방지하기 위해 강제 동기화)
                if (userPreferences.currentDashboardBroker) { currentDashboardBroker = userPreferences.currentDashboardBroker; currentFilterBroker = currentDashboardBroker; }
                if (userPreferences.currentDashboardSubAccount) { currentDashboardSubAccount = userPreferences.currentDashboardSubAccount; currentFilterSubAccount = currentDashboardSubAccount; }
                if (userPreferences.currentDashboardAccount) { currentDashboardAccount = userPreferences.currentDashboardAccount; currentFilterAccount = currentDashboardAccount; }
                if (userPreferences.currentFilterRecordType) currentFilterRecordType = userPreferences.currentFilterRecordType;
                if (userPreferences.currentFilterStock) currentFilterStock = userPreferences.currentFilterStock;
                
                // 만약 하단 필터 설정이 따로 저장되어 있다면 덮어쓰기하며 대시보드와 동기화
                if (userPreferences.currentFilterAccount) { currentFilterAccount = userPreferences.currentFilterAccount; currentDashboardAccount = currentFilterAccount; }
                if (userPreferences.currentFilterBroker) { currentFilterBroker = userPreferences.currentFilterBroker; currentDashboardBroker = currentFilterBroker; }
                if (userPreferences.currentFilterSubAccount) { currentFilterSubAccount = userPreferences.currentFilterSubAccount; currentDashboardSubAccount = currentFilterSubAccount; }
                
                // ⭐️ 차트 필터 상태 복원
                if (userPreferences.currentChartStock) currentChartStock = userPreferences.currentChartStock;
                if (userPreferences.currentChartAccount) currentChartAccount = userPreferences.currentChartAccount;
                if (userPreferences.currentChartBroker) currentChartBroker = userPreferences.currentChartBroker;
                if (userPreferences.currentChartSubAccount) currentChartSubAccount = userPreferences.currentChartSubAccount;

            } catch (e) {
                console.warn("환경설정 파싱 실패:", e);
            }
        }
        
        // 3. 매매 기록 데이터 처리
        console.log("[Data Load] /api/data 상태 코드:", response.status);
        if (response.status === 401) {
            // ⭐️ 세션 만료 시 로그인 페이지로 이동 (빈 화면 방지)
            console.warn("[Data Load] 세션 만료 감지 - 로그인 페이지로 이동");
            window.location.href = '/login';
            return;
        }
        if (!response.ok) {
            throw new Error(`/api/data 응답 오류: ${response.status}`);
        }
        cloudEntries = await response.json();
        window.__dataLoadFailed = false; // ⭐️ 로딩 성공: 재시도 플래그 해제
        const errBox = document.getElementById('dataLoadErrorBox');
        if (errBox) errBox.style.display = 'none';
        displayEntries();
        console.log("[Data Load] 화면 렌더링(displayEntries) 완료");

        fetchRealtimeNews();
        if (newsInterval) clearInterval(newsInterval);
        newsInterval = setInterval(fetchRealtimeNews, 600000); // 10분 주기로 변경
    } catch (err) {
        // ⭐️ 타임아웃(AbortError)·네트워크 오류 등으로 초기 로딩이 실패하면, 화면이 빈 상태로 굳지 않도록
        //    재시도 버튼을 노출하고 visibilitychange 자동 재시도용 플래그를 세운다.
        console.error("[Data Load Critical Error] 데이터 로딩 중 치명적 에러 발생:", err);
        window.__dataLoadFailed = true;
        showDataLoadError(err);
    }
}

// ⭐️ 초기 데이터 로딩 실패 시 화면 중앙에 안내 + '다시 시도' 버튼을 표시
function showDataLoadError(err) {
    const reason = err && err.name === 'AbortError'
        ? '서버 응답이 지연되어 연결을 종료했습니다 (네트워크 상태를 확인해주세요)'
        : (err && err.message ? err.message : '알 수 없는 오류');

    // ⭐️ 원인 분석용 상세 진단 정보 수집 (HTML 이스케이프 처리)
    const esc = (v) => String(v).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
    const diagLines = [
        '발생 시각      : ' + new Date().toLocaleString(),
        '네트워크 상태  : ' + (navigator.onLine ? '온라인' : '오프라인'),
        '계산엔진(calc) : ' + (typeof window.applyTradeToHolding === 'function' ? '정상 로드됨' : '로드 실패/누락'),
        '에러 종류      : ' + (err && err.name ? err.name : '-'),
        '에러 메시지    : ' + (err && err.message ? err.message : '-'),
        '현재 주소      : ' + location.href,
        'User-Agent     : ' + navigator.userAgent,
    ];
    if (err && err.stack) diagLines.push('', '[스택]', err.stack);
    const diagText = diagLines.join('\n');

    let box = document.getElementById('dataLoadErrorBox');
    if (!box) {
        box = document.createElement('div');
        box.id = 'dataLoadErrorBox';
        box.style.cssText = 'position:fixed; top:0; left:0; width:100%; height:100%; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:14px; background:var(--bg-color, #fff); z-index:99999; padding:24px; text-align:center; box-sizing:border-box;';
        document.body.appendChild(box);
    }
    box.innerHTML = `
        <div style="font-size:15px; color:var(--text-color, #333); line-height:1.6;">데이터를 불러오지 못했습니다.<br><span style="font-size:12px; color:var(--text-muted-color, #888);">(원인: ${esc(reason)})</span></div>
        <button type="button" id="btnRetryDataLoad" style="width:auto; min-width:120px; max-width:200px; padding:8px 20px; background:var(--primary-color, #3b82f6); color:#fff; border:none; border-radius:8px; font-size:13px; cursor:pointer; align-self:center;">다시 시도</button>
        <button type="button" id="btnToggleDiag" style="background:none; border:none; color:var(--text-muted-color, #888); font-size:12px; text-decoration:underline; cursor:pointer; align-self:center;">상세 정보 보기 ▾</button>
        <pre id="dataLoadDiag" style="display:none; max-width:92%; max-height:45vh; overflow:auto; text-align:left; white-space:pre-wrap; word-break:break-all; font-size:11px; line-height:1.5; color:var(--text-muted-color, #666); background:var(--card-bg-color, #f5f5f5); border:1px solid var(--border-color, #ddd); border-radius:8px; padding:12px; margin:0;">${esc(diagText)}</pre>
        <button type="button" id="btnCopyDiag" style="display:none; background:none; border:1px solid var(--border-color, #ccc); color:var(--text-muted-color, #888); font-size:11px; padding:4px 12px; border-radius:6px; cursor:pointer; align-self:center;">진단 정보 복사</button>`;
    box.style.display = 'flex';

    const retryBtn = document.getElementById('btnRetryDataLoad');
    if (retryBtn) {
        retryBtn.onclick = () => {
            box.style.display = 'none';
            loadDataFromLocal();
        };
    }

    const toggleBtn = document.getElementById('btnToggleDiag');
    const diagEl = document.getElementById('dataLoadDiag');
    const copyBtn = document.getElementById('btnCopyDiag');
    if (toggleBtn && diagEl) {
        toggleBtn.onclick = () => {
            const open = diagEl.style.display !== 'none';
            diagEl.style.display = open ? 'none' : 'block';
            if (copyBtn) copyBtn.style.display = open ? 'none' : 'inline-block';
            toggleBtn.textContent = open ? '상세 정보 보기 ▾' : '상세 정보 닫기 ▴';
        };
    }
    if (copyBtn) {
        copyBtn.onclick = async () => {
            try {
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    await navigator.clipboard.writeText(diagText);
                    copyBtn.textContent = '복사됨 ✓';
                    setTimeout(() => { copyBtn.textContent = '진단 정보 복사'; }, 1500);
                }
            } catch (e) { console.warn('진단 정보 복사 실패', e); }
        };
    }
}

// ⭐️ 환경설정(사용자가 정렬한 카드 순서)을 DB에 저장
async function savePreferences() {
    // ⭐️ 서버 환경설정을 아직 받아오지 못한 상태(빈 객체)로 저장하면
    //    기존에 저장된 필터·카드 순서 등 설정 전체가 덮어써져 유실되므로 건너뛴다.
    if (!preferencesLoaded) {
        console.warn("환경설정 미로드 상태 - 서버 저장 건너뜀 (기존 설정 보호)");
        return;
    }
    try {
        await fetch('/api/preferences', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            // ⭐️ keepalive: 필터 변경 직후 새로고침·앱 전환으로 페이지가 내려가는 중에도 저장 요청이 완료되도록 보장
            keepalive: true,
            body: JSON.stringify(userPreferences)
        });
    } catch (err) {
        console.error("환경설정 저장 실패:", err);
    }
}

// ⭐️ 모든 필터 상태를 DB에 저장
window.saveFilterPreferences = function() {
    userPreferences.currentDashboardBroker = currentDashboardBroker;
    userPreferences.currentDashboardSubAccount = currentDashboardSubAccount;
    userPreferences.currentDashboardAccount = currentDashboardAccount;
    userPreferences.currentFilterRecordType = currentFilterRecordType;
    userPreferences.currentFilterStock = currentFilterStock;
    userPreferences.currentFilterAccount = currentFilterAccount;
    userPreferences.currentFilterBroker = currentFilterBroker;
    userPreferences.currentFilterSubAccount = currentFilterSubAccount;
    savePreferences();
};

// ⭐️ 차트 필터 상태를 DB에 저장
window.saveChartFilterPreferences = function() {
    userPreferences.currentChartStock = currentChartStock;
    userPreferences.currentChartAccount = currentChartAccount;
    userPreferences.currentChartBroker = currentChartBroker;
    userPreferences.currentChartSubAccount = currentChartSubAccount;
    savePreferences();
};

async function fetchRealtimeNews(forceRefresh = false) {
    const newsListEl = document.getElementById('newsList');
    if (!newsListEl) return;
    
    // ⭐️ 전역 변수인 currentHoldings를 활용하여 현재 실제 보유 중인 모든 종목 검색
    const stocksToFetch = currentHoldings;
    
    try {
        newsListEl.innerHTML = '<div style="text-align:center; padding: 20px;">🔄 실시간 뉴스를 불러오는 중...</div>';
        const response = await fetchWithTimeout('/api/news', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ stocks: stocksToFetch, force_refresh: forceRefresh })
        });
        const newsData = await response.json();
        
        // ⭐️ 일주일 이전에 작성된 기사 및 미래 날짜(오기입) 기사 엄격하게 제외
        const now = new Date();
        const oneWeekAgo = new Date();
        oneWeekAgo.setDate(now.getDate() - 7);
        
        const filteredNewsData = newsData.filter(news => {
            if (!news.pubDate) return false; // 작성 시간이 아예 없는 기사 제외
            const pubDate = new Date(news.pubDate);
            // ⭐️ 작성 시간을 정상적으로 파싱할 수 없거나, 일주일이 지난 과거 기사, 그리고 미래 시간(기자 오기입) 기사 모두 제외
            if (isNaN(pubDate.getTime())) return false;
            return pubDate >= oneWeekAgo && pubDate <= now;
        });
        
        if (filteredNewsData.length === 0) {
            newsListEl.innerHTML = '<div style="text-align:center; padding: 20px;">관련 뉴스가 없습니다.</div>';
            return;
        }
        
        filteredNewsData.sort((a, b) => new Date(b.pubDate) - new Date(a.pubDate));

        // ⭐️ 루프 안에서 innerHTML += 를 반복하면 매 회 목록 전체를 재파싱(O(n²))하므로
        //    문자열로 모아 한 번에 대입한다.
        let newsHtml = '';
        filteredNewsData.forEach(news => {
            const dateObj = new Date(news.pubDate);
            const dateStr = !isNaN(dateObj) ? (dateObj.getMonth()+1) + '/' + dateObj.getDate() + ' ' + String(dateObj.getHours()).padStart(2,'0') + ':' + String(dateObj.getMinutes()).padStart(2,'0') : news.pubDate;

            newsHtml += `
                <div class="news-item">
                    <a href="${news.link}" target="_blank">${news.title}</a>
                    <div class="news-meta">
                        <span class="news-stock-tag">${news.stock}</span><span>${dateStr}</span>
                    </div>
                </div>`;
        });
        newsListEl.innerHTML = newsHtml;
    } catch (err) {
        console.error("뉴스 로딩 실패:", err);
            newsListEl.innerHTML = '<div style="text-align:center; padding: 20px; color:var(--danger-color);">뉴스를 불러오지 못했습니다.</div>';
    }
}

