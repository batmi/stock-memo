// ═══════════════════════════════════════════════════════════════════
// 01-core.js — 전역 상태·매핑 헬퍼·이스케이프·제외 규칙 — 다른 모든 조각이 여기에 의존한다
//
// ⚠️ 이 파일들은 ES 모듈이 아니라 **순서대로 로드되는 클래식 스크립트**다.
//    최상위 let/const/function 은 전역 렉시컬 환경을 공유하므로, 예전 script.js
//    한 덩어리였을 때와 실행 의미가 완전히 같다. (HTML 의 인라인 onclick 핸들러가
//    전역 함수를 그대로 부르고 있어 모듈로 바꾸면 그것들이 전부 깨진다)
//    → 로드 순서는 templates/stock-memo.html 의 <script> 순서가 결정한다. 바꾸지 말 것.
// ═══════════════════════════════════════════════════════════════════

// ⭐️ 전역 에러 핸들러 추가 (화면 렌더링 전 발생하는 치명적 에러 감지용)
window.addEventListener('error', function(e) {
    console.error("[Global Error] JS 에러 발생:", e.message, "위치:", e.filename, "라인:", e.lineno);
});
window.addEventListener('unhandledrejection', function(e) {
    console.error("[Unhandled Promise Rejection] 처리되지 않은 비동기 에러:", e.reason);
});

// ⭐️ 모바일 네트워크(셀룰러↔Wi-Fi 전환, 터널 지연 등)에서 fetch가 응답·실패 없이 무한 정지(stall)하면
//    화면이 로딩 상태로 영영 고착된다. AbortController로 타임아웃을 강제해 무한 대기를 방지한다.
function fetchWithTimeout(url, options = {}, timeout = 15000) {
    const controller = new AbortController();
    const id = setTimeout(() => controller.abort(), timeout);
    return fetch(url, { ...options, signal: controller.signal })
        .finally(() => clearTimeout(id));
}

// ⭐️ HTML head 인라인 스크립트(window.__initialFetches)가 문서 파싱 시작 시점에
//    미리 발사해둔 초기 API 요청을 이어받는다. (라이브러리 로딩과 데이터 수신 병렬화)
//    프리페치가 없거나 실패(null)·15초 내 미완료 시 새 요청으로 폴백한다.
function initialFetchOrFresh(key, url) {
    const pre = window.__initialFetches;
    const prefetched = pre && pre[key];
    if (pre) pre[key] = null; // 재시도(visibilitychange 등) 시에는 항상 새 요청 사용
    if (!prefetched) return fetchWithTimeout(url);
    const timeout = new Promise((resolve) => setTimeout(() => resolve(null), 15000));
    return Promise.race([prefetched, timeout]).then(res => res ? res : fetchWithTimeout(url));
}

let cloudEntries = [];

// ⭐️ 등록된 계좌 매핑(별칭·증권사). 페이지 로드 시 /api/mappings 로 채운다.
//    아래 매핑 헬퍼들이 참조하므로 헬퍼보다 먼저 선언해 둔다.
let currentAccountMappings = { brokers: {}, accounts: {} };

// ⭐️ 증권사 코드 → 표시 이름. **이 앱 안의 유일한 정의다.**
//    예전에는 같은 리터럴이 이 파일 네 곳에 복붙되어 있고 HTML 의 <option> 에도
//    또 하드코딩돼 있었다. 증권사 하나를 추가하려면 다섯 곳을 고쳐야 했고,
//    한 곳을 빠뜨리면 화면 어딘가에서만 코드가 그대로 노출됐다.
//    HTS 가 보내는 코드는 증권사마다 두 갈래(표준 3자리 / 축약 1자리)라 둘 다 받는다.
const BROKER_NAMES = Object.freeze({
    '264': '키움증권',       '1': '키움증권',
    '238': '미래에셋증권',   '2': '미래에셋증권',
    '247': 'NH투자증권',     '3': 'NH투자증권',
    '243': '한국투자증권',   '4': '한국투자증권',
    '240': '삼성증권',       '5': '삼성증권',
    '271': '토스증권',       '6': '토스증권',
    '218': 'KB증권',
    '278': '신한투자증권',
});

// ⭐️ 계좌 등록 드롭다운에 넣을 (코드, 이름) 목록 — 표준 코드만, 등록 순서 그대로.
//    BROKER_NAMES 에서 파생시켜 목록이 따로 늙지 않게 한다.
const BROKER_CHOICES = Object.freeze(
    ['264', '238', '247', '243', '240', '271', '218', '278']
        .map(code => Object.freeze({ code, name: BROKER_NAMES[code] }))
);

// ⭐️ 자동완성 후보로 쓸 증권사 이름 (중복 없이)
const defaultBrokers = Object.freeze([...new Set(BROKER_CHOICES.map(b => b.name))]);

// ⭐️ 클래식 스크립트의 최상위 const 는 window 에 붙지 않는다. 인라인 핸들러와
//    브라우저 테스트가 같은 정의를 볼 수 있도록 명시적으로 노출한다.
window.BROKER_NAMES = BROKER_NAMES;
window.BROKER_CHOICES = BROKER_CHOICES;

// ⭐️ 글로벌 매핑 헬퍼 함수
function getMappedBroker(rawBroker) {
    if (!rawBroker) return '';
    return BROKER_NAMES[rawBroker] || rawBroker;
}

// ⭐️ 계좌번호 비교용 정규화 키. 하이픈·공백은 표기 차이일 뿐이다.
//    등록은 '44048158-01' 로 해 두고 HTS 는 '4404815801' 로 보내는 경우가 흔해서,
//    양쪽을 같은 규칙으로 접어야 매핑이 어긋나지 않는다. (백엔드 _account_key 와 동일 규칙)
function accountKey(value) {
    return String(value || '').replace(/[\s-]/g, '');
}

// ⭐️ 등록된 계좌 매핑에서 계좌번호로 매핑 정보를 찾는다.
//    정확히 일치하는 키를 먼저 보고, 없으면 하이픈을 무시하고 다시 찾는다.
function findAccountMapping(rawSubAccount) {
    const accounts = (currentAccountMappings && currentAccountMappings.accounts) || {};
    if (!rawSubAccount) return null;
    if (accounts[rawSubAccount]) return accounts[rawSubAccount];
    const target = accountKey(rawSubAccount);
    if (!target) return null;
    const hit = Object.keys(accounts).find(key => accountKey(key) === target);
    return hit ? accounts[hit] : null;
}

function getMappedSubAccount(rawSubAccount, accountName) {
    if (accountName) return accountName;
    if (!rawSubAccount) return '';
    const accInfo = findAccountMapping(rawSubAccount);
    if (accInfo && accInfo.alias) return accInfo.alias;
    return rawSubAccount;
}

let currentHoldings = [];
let newsInterval = null;
let currentFilterDate = null;
let currentFilterRecordType = 'all'; // ⭐️ 독립 필터 1 (기록/매매)
let currentFilterStock = 'all';      // ⭐️ 독립 필터 2 (종목별)
let currentFilterAccount = 'all';    // ⭐️ 독립 필터 3 (분류별)
let currentFilterBroker = 'all';     // ⭐️ 독립 필터 4 (증권사별)
let currentFilterSubAccount = 'all'; // ⭐️ 독립 필터 5 (계좌별)
let currentFilterKeywords = []; // ⭐️ 다중 키워드 필터용 배열
let isDashboardCollapsed = false;
let showClosedPositions = false; // 청산종목 보기 상태
// ⭐️ 금액 가리기(프라이버시) 모드. 초기값은 head 의 FOUC 방지 스크립트가 이미 적용해 둔
//    클래스에서 읽어 온다 — localStorage 를 두 곳에서 따로 읽으면 언젠가 어긋난다.
let isAmountMasked = document.documentElement.classList.contains('amount-masked');
let currentMarketMode = 'NXT'; // ⭐️ KRX/NXT 토글 상태 (기본값 NXT)
let currentPortfolioArrayForPrice = []; // 현재가 계산용 임시 배열
let showHistoryClosedPositions = false; // ⭐️ 히스토리도 포트폴리오와 동일하게 청산·숨김 종목을 기본 숨김 처리
let stockIdentityByName = {};  // ⭐️ 종목명 → 동일성(코드) 표. recomputeHiddenStocks 가 채운다
let stockNameByIdentity = {};  // ⭐️ 동일성(코드) → 표시 이름(가장 최근 기록의 이름). 같은 표가 함께 만들어진다
let hiddenStocks = new Set();  // ⭐️ 숨김 처리된 종목명 집합 — 각 종목의 최신 기록(updatedAt→createdAt→id)의 isHidden 으로 판정

const EXCLUDED_KEY_SUFFIX = '::제외';

// ⭐️ 이름만으로 '제외 계좌'라고 단정할 수 있는 별칭 집합.
//    같은 별칭이 제외 계좌와 포함 계좌에 함께 쓰이면(증권사마다 '일반계좌'처럼) 이름만으로는
//    어느 계좌인지 구별할 수 없으므로 대조 대상에서 뺀다. 그러지 않으면 토스증권 '일반계좌'
//    하나만 제외했는데 한국투자증권 '일반계좌'까지 함께 빠진다.
//    (백엔드 app/services/accounts.py 의 excluded_accounts 와 같은 규칙)
function excludedAccountAliases() {
    const accounts = (currentAccountMappings && currentAccountMappings.accounts) || {};
    const excluded = new Set(), kept = new Set();
    Object.values(accounts).forEach(acc => {
        const isObj = acc && typeof acc === 'object';
        const alias = String((isObj ? acc.alias : acc) || '').trim();
        if (!alias) return;
        if (isObj && acc.exclude_from_stats) excluded.add(alias);
        else kept.add(alias);
    });
    kept.forEach(alias => excluded.delete(alias));
    return excluded;
}

// ⭐️ 계좌 관리에서 '금액 계산 제외'로 체크한 계좌(exclude_from_stats)의 기록인지 판정한다.
//    별칭(계좌 이름)은 언제든 바꿀 수 있으므로 이름을 하드코딩하지 않고 등록된 계좌번호로 본다.
//    다만 HTS 없이 손으로 적은 기록은 계좌번호 없이 이름만 있을 수 있어, 별칭도 함께 대조한다.
function isExcludedAccountEntry(entry) {
    if (!entry) return false;
    const info = findAccountMapping(entry.subAccount);
    // 계좌번호로 계좌가 특정되면 그 계좌의 설정이 정답이다. 여기서 이름 대조로 넘어가면
    // 별칭이 같은 다른 증권사 계좌의 기록까지 함께 제외된다.
    if (info) return !!(typeof info === 'object' && info.exclude_from_stats);

    // 등록된 계좌번호로 찾지 못한 기록(수기 입력 등)만 화면에 보이는 계좌 이름으로 대조한다.
    const label = String(getMappedSubAccount(entry.subAccount, entry.accountName) || '').trim();
    if (!label) return false;
    return excludedAccountAliases().has(label);
}

// ⭐️ 금액을 합산하는 모든 지표(도넛·총액·실현손익·차트·통계)에서 빼야 하는 기록인지.
//    사용자가 '제외'로 체크한 계좌의 기록이 해당된다.
function isExcludedFromTotals(entry) {
    return isExcludedAccountEntry(entry);
}

// 제외 사유에 따라 카드·목록에 붙일 배지 문구
function exclusionBadgeLabel(entry) {
    return '제외';
}

function portfolioKey(stockName, isExcluded) {
    return isExcluded ? stockName + EXCLUDED_KEY_SUFFIX : stockName;
}

// ⭐️ 종목 동일성 키 — 코드가 있으면 코드, 없으면 이름 (calc.js 가 정본, 백엔드
//    stats.stock_identity 와 같은 기준). 화면 전역에서 '같은 종목인가'는 이걸로 판정한다.
function identityOf(entry) {
    return window.stockIdentity ? window.stockIdentity(entry) : ((entry && entry.stockName) || '');
}

// 보유 칸 키 = 종목 동일성 + 실거래/모의·제외 구분.
//  같은 종목이라도 모의·제외 계좌 물량은 실거래 평단·실현손익에 섞이면 안 되므로 칸을 나눈다.
function portfolioKeyFor(entry) {
    return portfolioKey(identityOf(entry), isExcludedFromTotals(entry));
}

// ⭐️ 화면에 남아 있는 '이름으로 지정된 것들'(카드 클릭·필터 드롭다운·달력 링크·레거시 메모)을
//    동일성으로 옮긴다. 이름만 비교하면 표기가 갈린 같은 종목의 기록이 필터에서 빠진다.
//    표는 recomputeHiddenStocks 가 목록을 훑을 때 함께 만든다(여기서 다시 훑지 않는다).
function identityForStockName(name) {
    const target = (name == null ? '' : String(name)).trim();
    if (!target) return '';
    if (stockIdentityByName[target]) return stockIdentityByName[target];
    //  표가 아직 안 만들어진 시점(첫 렌더 순서에 따라)에도 답은 나와야 한다 — 그때만 훑는다.
    const entries = (typeof cloudEntries !== 'undefined' && cloudEntries) ? cloudEntries : [];
    const hit = entries.find(e => e && (e.type || 'trade') === 'trade'
        && (e.stockName || '').trim() === target);
    return hit ? identityOf(hit) : target;
}

// ⭐️ 동일성 → 화면에 보여 줄 종목명. 표기가 갈린 같은 종목은 **가장 최근 기록의 이름**
//    하나로 부른다 — 백엔드 stats.display_names 와 같은 규칙이다.
//    표가 아직 없으면(첫 렌더 순서) 동일성 자체를 돌려준다. 호출부가 원래 이름으로 받아 준다.
function displayNameForIdentity(identity) {
    const key = (identity == null ? '' : String(identity)).trim();
    if (!key) return '';
    return stockNameByIdentity[key] || key;
}

// 기록 하나를 화면에 부를 이름. 이름이 갈려도 카드·달력·차트가 같은 이름으로 부르게 한다.
function displayNameForEntry(entry) {
    if (!entry) return '';
    //  표가 아직 없으면(첫 렌더 순서) 그 기록의 이름을 그대로 쓴다 — 코드가 이름 자리에
    //  튀어나오지 않게 한다.
    return stockNameByIdentity[identityOf(entry)] || (entry.stockName || '');
}

// ⭐️ 종목 드롭다운(<select>)에 세울 값 고르기. 저장된 필터 값이 **옛 표기**일 수 있으므로
//    이름이 그대로 없으면 동일성이 같은 옵션으로 옮겨 준다. 없으면 빈 문자열.
//    (필터 자체는 동일성으로 대조하니 결과는 이미 맞다 — 어긋나는 건 드롭다운 표시뿐이다)
function resolveStockOptionValue(options, value) {
    const target = (value == null ? '' : String(value)).trim();
    if (!target || target === 'all') return '';
    if (options.indexOf(target) !== -1) return target;
    const ident = identityForStockName(target);
    if (!ident) return '';
    return options.find(name => identityForStockName(name) === ident) || '';
}

// HTML 속성값·속성 선택자에 문자열을 안전하게 넣기 위한 최소 이스케이프
function escapeAttr(value) {
    return String(value == null ? '' : value).replace(/"/g, '&quot;');
}
function escapeAttrSelector(value) {
    return String(value == null ? '' : value).replace(/["\\]/g, '\\$&');
}

// 텍스트를 HTML 본문에 안전하게 넣기 위한 이스케이프.
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text == null ? '' : String(text);
    return div.innerHTML;
}

// ⭐️ onclick="fn('...')" 처럼 'HTML 속성 안의 JS 문자열 리터럴'에 값을 넣기 위한 이스케이프.
//    브라우저는 속성값을 HTML 디코딩한 뒤 JS 로 평가하므로 JS → HTML 순서로 두 번 막아야 한다.
//    (계좌 별칭에 작은따옴표가 하나만 들어가도 핸들러가 깨져 수정·삭제 버튼이 먹통이 됐다)
function escapeJsInAttr(value) {
    const js = String(value == null ? '' : value)
        .replace(/\\/g, '\\\\')
        .replace(/'/g, "\\'")
        .replace(/[\r\n]/g, '');
    return js.replace(/&/g, '&amp;').replace(/</g, '&lt;')
             .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ⭐️ 포트폴리오와 히스토리의 '청산종목 보기' 버튼 UI 를 현재 상태로 함께 갱신한다.
function syncClosedPositionsButtons() {
    const label = showClosedPositions ? '청산종목 숨김' : '청산종목 보기';
    const bg = showClosedPositions ? 'var(--primary-color)' : 'transparent';
    const fg = showClosedPositions ? '#fff' : 'var(--primary-color)';
    ['btnToggleClosed', 'btnToggleHistoryClosed'].forEach(id => {
        const btn = document.getElementById(id);
        if (!btn) return;
        btn.innerText = label;
        btn.style.backgroundColor = bg;
        btn.style.color = fg;
    });
}

// ⭐️ 청산·숨김 종목 노출 상태를 포트폴리오와 히스토리 양쪽에 동일하게 적용한다.
//    두 상태가 어긋나면 청산·숨김 종목 카드를 눌렀을 때 히스토리가 비어 보이므로 항상 함께 움직인다.
function setClosedPositionsVisible(next, { persist = true } = {}) {
    showClosedPositions = next;
    showHistoryClosedPositions = next;
    syncClosedPositionsButtons();
    if (persist) {
        userPreferences.showClosedPositions = next;
        userPreferences.showHistoryClosedPositions = next;
        savePreferences();
    }
}
let currentDashboardBroker = 'all'; // 대시보드 증권사 필터 상태
let currentDashboardSubAccount = 'all'; // 대시보드 계좌 필터 상태
let currentDashboardAccount = 'all'; // 대시보드 투자 분류 필터 상태
let priceUpdateInterval = null; // ⭐️ 타이머 변수 선언 누락 수정
let currentFilteredEntries = [];
let currentRenderPage = 1;
const entriesPerPage = 15;
let lastRenderedMonth = '';
let userPreferences = {};       // ⭐️ 사용자별 설정(포트폴리오 정렬 순서 등) 저장
let preferencesLoaded = false;  // ⭐️ 서버 환경설정 수신 성공 여부 — 실패 상태로 저장하면 빈 설정이 DB를 덮어써 기존 설정이 유실되므로 가드로 사용
let portfolioSortable = null;   // ⭐️ SortableJS 드래그 앤 드롭 인스턴스
window.currentPriceCache = {};  // ⭐️ 장 종료 시 이전 가격을 유지하기 위한 전역 캐시
window.monthlyProfitChartInstance = null; // ⭐️ 월별 손익 차트 인스턴스 변수 추가
window.currentChartGranularity = window.currentChartGranularity || 'monthly'; // ⭐️ 차트 집계 단위 (monthly/weekly, 기본 월간)

// ⭐️ 차트 과거 기간 탐색 상태 (주간/월간 공통)
//    CHART_WEEK_WINDOW: 한 화면에 표시할 주 개수(12주)
//    CHART_WEEK_MAX_BACK: 과거로 볼 수 있는 최대 기간(현재 주 포함 52주)
//    chartWeekOffset: 화면을 과거로 밀어낸 주 수 (0 = 현재 주 포함 최근 12주)
const CHART_WEEK_WINDOW = 12;
const CHART_WEEK_MAX_BACK = 52;
const CHART_WEEK_MAX_OFFSET = CHART_WEEK_MAX_BACK - CHART_WEEK_WINDOW; // 40주
window.chartWeekOffset = 0;

//    CHART_MONTH_WINDOW: 한 화면에 표시할 개월 수(12개월)
//    CHART_MONTH_MAX_BACK: 과거로 볼 수 있는 최대 기간(이번 달 포함 60개월 = 5년)
//    chartMonthOffset: 화면을 과거로 밀어낸 개월 수 (0 = 이번 달 포함 최근 12개월)
const CHART_MONTH_WINDOW = 12;
const CHART_MONTH_MAX_BACK = 60;
const CHART_MONTH_MAX_OFFSET = CHART_MONTH_MAX_BACK - CHART_MONTH_WINDOW; // 48개월
window.chartMonthOffset = 0;

// ⭐️ 차트 전용 독립 필터 상태 변수
let currentChartStock = 'all';
let currentChartAccount = 'all';
let currentChartBroker = 'all';
let currentChartSubAccount = 'all';

// ⭐️ 공통 스크롤 함수: 스크롤 튐 현상을 막기 위해 window.scrollTo 절대 좌표 사용
