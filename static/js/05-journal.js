// 서버 집계와 페이지 조회. 캐시는 사용자 데이터 버전이 바뀌면 모두 버린다.
// 빠르게 필터·월을 바꿨을 때 늦게 도착한 응답이 새 화면을 덮지 않게 한다.
let journalMeta = null;
let journalGeneration = 0;
const journalReads = new Map();
const journalRequests = {};
let historyCursor = null;
let historyQuery = null;
let historyLoading = false;
let editingEntry = null;

function setJournalMeta(meta) {
    journalMeta = meta;
    journalGeneration++;
    journalReads.clear();
    stockIdentityByName = meta.stockIdentityByName;
    stockNameByIdentity = meta.stockNameByIdentity;
    hiddenStocks = new Set(meta.hiddenStocks);
}

function journalFilters(scope) {
    const history = scope === 'history', chart = scope === 'chart';
    const stock = history ? currentFilterStock : chart ? currentChartStock : 'all';
    const broker = history ? currentFilterBroker : chart ? currentChartBroker : currentDashboardBroker;
    return {
        stock: stock === 'all' ? '' : identityForStockName(stock),
        tradeClass: history ? currentFilterAccount : chart ? currentChartAccount : currentDashboardAccount,
        subAccount: history ? currentFilterSubAccount : chart ? currentChartSubAccount : currentDashboardSubAccount,
        brokers: broker === 'all' ? [] : [broker, ...Object.keys(BROKER_NAMES).filter(k => BROKER_NAMES[k] === broker)],
    };
}

function journalUrl(endpoint, params = {}) {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
        if (key === 'period') value.forEach(v => query.append(key, v));
        else if (value !== null && value !== undefined) query.set(key, Array.isArray(value) ? JSON.stringify(value) : value);
    });
    return `/api/journal/${endpoint}?${query}`;
}

async function journalJSON(url, cache = true) {
    let pending = cache && journalReads.get(url);
    if (!pending) {
        pending = (async () => {
            const response = await fetchWithTimeout(url);
            if (response.status === 401) window.location.href = '/login';
            const data = await response.json();
            if (!response.ok) {
                const error = new Error(data.error || '데이터를 불러오지 못했습니다.');
                error.status = response.status;
                throw error;
            }
            return data;
        })();
        if (cache) {
            if (journalReads.size >= 48) journalReads.delete(journalReads.keys().next().value);
            journalReads.set(url, pending);
            pending.catch(() => { if (journalReads.get(url) === pending) journalReads.delete(url); });
        }
    }
    // 차트는 현재가로 평가손익을 더하므로 캐시 원본을 변경하지 않게 복제한다.
    return JSON.parse(JSON.stringify(await pending));
}

function journalError(targetId, error, retry) {
    const target = document.getElementById(targetId);
    if (!target) return;
    const noticeId = `${targetId}-load-error`;
    document.getElementById(noticeId)?.remove();
    const notice = document.createElement('div');
    notice.id = noticeId;
    notice.className = 'journal-load-error';
    const text = document.createElement('span');
    text.textContent = error.message + ' ';
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = '다시 시도';
    button.onclick = retry;
    notice.append(text, button);
    target.before(notice);
}

async function readJournalView(slot, url, render, targetId, retry) {
    const ticket = journalRequests[slot] = (journalRequests[slot] || 0) + 1;
    const generation = journalGeneration;
    try {
        const result = await journalJSON(url);
        if (ticket !== journalRequests[slot] || generation !== journalGeneration) return;
        document.getElementById(`${targetId}-load-error`)?.remove();
        render(result);
        return result;
    } catch (error) {
        if (ticket === journalRequests[slot] && generation === journalGeneration) {
            journalError(targetId, error, retry);
        }
    }
}

function journalMonth(date = new Date()) {
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
}
