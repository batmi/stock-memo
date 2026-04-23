let cloudEntries = [];
let currentHoldings = [];
let newsInterval = null;
let currentFilterDate = null;
let currentFilterType = null;
let isDashboardCollapsed = false;
let showClosedPositions = false; // 청산 종목 보기 상태
let showHistoryClosedPositions = true; // 히스토리 청산 종목 포함 상태
let currentDashboardBroker = 'all'; // 대시보드 증권사 필터 상태
let currentFilteredEntries = [];
let currentRenderPage = 1;
const entriesPerPage = 15;
let lastRenderedMonth = '';

const mainApp = document.getElementById('mainApp');
window.addEventListener('DOMContentLoaded', () => {
    const themeToggle = document.getElementById('theme-toggle');

    function applyTheme(theme) {
        document.documentElement.setAttribute('data-theme', theme);
        const fpDark = document.getElementById('flatpickr-dark-theme');
        if (theme === 'dark') {
            themeToggle.checked = true;
            if (fpDark) fpDark.removeAttribute('disabled');
        } else {
            themeToggle.checked = false;
            if (fpDark) fpDark.setAttribute('disabled', 'disabled');
        }
        // 차트가 이미 생성되었다면 색상을 업데이트하기 위해 다시 렌더링
        if (portfolioChartInstance) {
            updatePortfolioSummary();
        }
    }

    themeToggle.addEventListener('change', () => {
        const theme = themeToggle.checked ? 'dark' : 'light';
        localStorage.setItem('theme', theme);
        applyTheme(theme);
    });

    // 페이지 로드 시 저장된 테마 적용
    const savedTheme = localStorage.getItem('theme') || 'dark';
    applyTheme(savedTheme);

    // 대시보드 접기/펴기 버튼 이벤트 연결
    const btnTogglePortfolio = document.getElementById('btnTogglePortfolio');
    if (btnTogglePortfolio) {
        btnTogglePortfolio.addEventListener('click', () => {
            isDashboardCollapsed = !isDashboardCollapsed;
            updatePortfolioSummary();
        });
    }

    // 청산 종목 보기 토글 버튼 이벤트 연결
    const btnToggleClosed = document.getElementById('btnToggleClosed');
    if (btnToggleClosed) {
        btnToggleClosed.addEventListener('click', () => {
            showClosedPositions = !showClosedPositions;
            btnToggleClosed.innerText = showClosedPositions ? '청산 종목 숨기기' : '청산 종목 보기';
            btnToggleClosed.style.backgroundColor = showClosedPositions ? 'var(--primary-color)' : 'transparent';
            btnToggleClosed.style.color = showClosedPositions ? '#fff' : 'var(--primary-color)';
            updatePortfolioSummary();
        });
    }

    // ⭐️ 대시보드 증권사 필터 이벤트 연결
    const dashboardBrokerFilter = document.getElementById('dashboardBrokerFilter');
    if (dashboardBrokerFilter) {
        dashboardBrokerFilter.addEventListener('change', (e) => {
            currentDashboardBroker = e.target.value;
            updatePortfolioSummary();
        });
    }

    // 히스토리 청산 종목 숨기기/보기 토글 버튼 이벤트 연결
    const btnToggleHistoryClosed = document.getElementById('btnToggleHistoryClosed');
    if (btnToggleHistoryClosed) {
        btnToggleHistoryClosed.addEventListener('click', () => {
            showHistoryClosedPositions = !showHistoryClosedPositions;
            btnToggleHistoryClosed.innerText = showHistoryClosedPositions ? '청산 종목 숨기기' : '청산 종목 보기';
            btnToggleHistoryClosed.style.backgroundColor = showHistoryClosedPositions ? 'transparent' : 'var(--primary-color)';
            btnToggleHistoryClosed.style.color = showHistoryClosedPositions ? 'var(--primary-color)' : '#fff';
            displayEntries(true);
        });
    }

    // [3안] 필터 타입(유형) 선택 드롭다운 이벤트
    const filterTypeSelect = document.getElementById('filterTypeSelect');
    if (filterTypeSelect) {
        filterTypeSelect.addEventListener('change', (e) => {
            currentFilterType = e.target.value === 'all' ? null : e.target.value;
            displayEntries(true);
        });
    }

    // ⭐️ 로그아웃 버튼 이벤트 연결
    const btnLogout = document.getElementById('btnLogout');
    if (btnLogout) {
        btnLogout.addEventListener('click', () => {
            if (confirm("로그아웃 하시겠습니까?")) {
                window.location.href = '/logout';
            }
        });
    }

    // ⭐️ Quill 에디터 초기화
    window.quill = new Quill('#editor-container', {
        theme: 'snow',
        modules: {
            toolbar: [
                [{ 'header': [1, 2, 3, false] }, { 'size': ['small', false, 'large', 'huge'] }], // 헤더, 글자 크기
                ['bold', 'italic', 'underline', 'strike'],       // 텍스트 강조
                [{ 'color': [] }, { 'background': [] }],         // 글자/배경 색상
                [{ 'align': [] }],                               // 정렬
                [{ 'list': 'ordered'}, { 'list': 'bullet' }],    // 리스트
                ['blockquote', 'code-block'],                    // 인용, 코드 블록
                ['clean']                                        // 서식 초기화
            ]
        },
        placeholder: '현재 시장 상황, 매매 이유, 향후 대응 계획 등을 자유롭게 기록하세요.'
    });

    // ⭐️ 붙여넣기 시 외부 텍스트의 글자색/배경색 서식 강제 제거 (테마 색상 자동 적용)
    window.quill.clipboard.addMatcher(Node.ELEMENT_NODE, function(node, delta) {
        delta.ops.forEach(op => {
            if (op.attributes) {
                delete op.attributes.color;
                delete op.attributes.background;
            }
        });
        return delta;
    });

    loadDataFromLocal();
});

async function loadDataFromLocal() {
    try {
        const response = await fetch('/api/data');
        cloudEntries = await response.json();
        displayEntries();
        
        fetchRealtimeNews();
        if (newsInterval) clearInterval(newsInterval);
        newsInterval = setInterval(fetchRealtimeNews, 300000);
    } catch (err) {
        console.error(err);
        alert("로컬 데이터를 불러오지 못했습니다.\n서버가 실행 중인지 확인하세요.");
    }
}

async function saveToLocal(reload = false) {
    try {
        await fetch('/api/data', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(cloudEntries)
        });
        if (reload) {
            window.location.reload();
        } else {
            await loadDataFromLocal();
        }
    } catch (err) {
        console.error("저장 실패:", err);
        alert("데이터베이스에 저장하는 중 오류가 발생했습니다.");
    }
}

async function fetchRealtimeNews() {
    const newsListEl = document.getElementById('newsList');
    if (!newsListEl) return;
    
    const portfolioQty = {};
    cloudEntries.forEach(entry => {
        if (entry.type === 'trade' && entry.stockName) {
            if (portfolioQty[entry.stockName] === undefined) portfolioQty[entry.stockName] = 0;
            if (entry.tradeType === '매수') portfolioQty[entry.stockName] += (Number(entry.quantity) || 0);
            else if (entry.tradeType === '매도') portfolioQty[entry.stockName] -= (Number(entry.quantity) || 0);
        }
    });

    const validStocks = new Set();
    for (const entry of cloudEntries) {
        const stock = entry.stockName;
        if (!stock) continue;
        if (portfolioQty[stock] !== undefined && portfolioQty[stock] <= 0) continue;
        
        validStocks.add(stock);
        if (validStocks.size >= 5) break;
    }
    const stocksToFetch = Array.from(validStocks);
    
    try {
        newsListEl.innerHTML = '<div style="text-align:center; padding: 20px;">🔄 실시간 뉴스를 불러오는 중...</div>';
        const response = await fetch('/api/news', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ stocks: stocksToFetch })
        });
        const newsData = await response.json();
        
        if (newsData.length === 0) {
            newsListEl.innerHTML = '<div style="text-align:center; padding: 20px;">관련 뉴스가 없습니다.</div>';
            return;
        }
        
        newsData.sort((a, b) => new Date(b.pubDate) - new Date(a.pubDate));

        newsListEl.innerHTML = '';
        newsData.forEach(news => {
            const dateObj = new Date(news.pubDate);
            const dateStr = !isNaN(dateObj) ? (dateObj.getMonth()+1) + '/' + dateObj.getDate() + ' ' + String(dateObj.getHours()).padStart(2,'0') + ':' + String(dateObj.getMinutes()).padStart(2,'0') : news.pubDate;
            
            newsListEl.innerHTML += `
                <div class="news-item">
                    <a href="${news.link}" target="_blank">${news.title}</a>
                    <div class="news-meta">
                        <span class="news-stock-tag">${news.stock}</span><span>${dateStr}</span>
                    </div>
                </div>`;
        });
    } catch (err) {
        console.error("뉴스 로딩 실패:", err);
            newsListEl.innerHTML = '<div style="text-align:center; padding: 20px; color:var(--danger-color);">뉴스를 불러오지 못했습니다.</div>';
    }
}

const journalForm = document.getElementById('journalForm');
const historyList = document.getElementById('historyList');
const filterStockInput = document.getElementById('filterStock');
const clearFilterBtn = document.getElementById('clearFilterBtn');
const formContainer = document.getElementById('formContainer');
const formModalOverlay = document.getElementById('formModalOverlay');
const btnFab = document.getElementById('btnFab');
const btnCloseForm = document.getElementById('btnCloseForm');
const submitBtn = journalForm.querySelector('button[type="submit"]');
let editingEntryId = null;
let portfolioChartInstance = null;
let currentAttachedImage = null;
let currentSelectedFile = null;
let currentTags = [];

const defaultStocks = [
    "삼성전자", "SK하이닉스", "LG에너지솔루션", "현대차", "기아", "셀트리온", "POSCO홀딩스", "NAVER", "카카오",
    "애플 (AAPL)", "테슬라 (TSLA)", "엔비디아 (NVDA)", "마이크로소프트 (MSFT)", "알파벳 (GOOGL)", "아마존 (AMZN)"
];

btnFab.addEventListener('click', () => {
    formModalOverlay.style.display = 'flex';
});

btnCloseForm.addEventListener('click', resetAndCloseForm);

// ⭐️ Esc 키로 모달 닫기
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && formModalOverlay.style.display === 'flex') {
        const openLists = document.querySelectorAll('.autocomplete-list[style*="display: block"]');
        if (openLists.length > 0) return; // 드롭다운이 열려있을 땐 모달 닫기 방지
        resetAndCloseForm();
    }
});

// ⭐️ 커스텀 자동완성(Autocomplete) 드롭다운 로직
function setupAutocomplete(inputId, listId, getOptions) {
    const input = document.getElementById(inputId);
    const list = document.getElementById(listId);
    let currentFocus = -1;
    let lastVal = input.value;

    function triggerInput(e) {
        const val = input.value;
        
        // 한글 타이핑 중 방향키 조작 시 발생하는 의미 없는 input 이벤트 무시 (초기화 방지)
        if (e && e.type === 'input' && val === lastVal) return;
        lastVal = val;
        
        list.innerHTML = '';
        currentFocus = -1;
        const options = getOptions();
        const matched = val ? options.filter(opt => opt.toLowerCase().includes(val.toLowerCase())) : options;
        
        if (matched.length === 0) {
            list.style.display = 'none';
            return;
        }
        
        list.style.display = 'block';
        matched.forEach(opt => {
            const item = document.createElement('div');
            item.className = 'autocomplete-item';
            if (val) {
                const regex = new RegExp(`(${val})`, 'gi');
                item.innerHTML = opt.replace(regex, "<span style='color:var(--danger-color); font-weight:var(--fw-bold, bold);'>$1</span>");
            } else {
                item.innerText = opt;
            }
            // ⭐️ 포커스 유실 방지 (첫 클릭 무시 및 한글 입력기 충돌 해결)
            item.addEventListener('mousedown', function(ev) {
                ev.preventDefault(); 
            });
            // ⭐️ 한 번의 마우스 클릭만으로 즉시 자동 입력되도록 동작 분리
            item.addEventListener('click', function(ev) {
                ev.stopPropagation();
                input.value = opt;
                lastVal = opt;
                list.style.display = 'none';
                input.dispatchEvent(new Event('input')); 
            });
            list.appendChild(item);
        });
    }

    input.addEventListener('input', triggerInput);
    input.addEventListener('focus', triggerInput);
    input.addEventListener('click', triggerInput);

    input.addEventListener('keydown', function(e) {
        const items = list.getElementsByClassName('autocomplete-item');
        if (list.style.display === 'none') return;
        if (e.key === 'ArrowDown' || e.keyCode === 40) {
            currentFocus++; addActive(items); e.preventDefault();
        } else if (e.key === 'ArrowUp' || e.keyCode === 38) {
            currentFocus--; addActive(items); e.preventDefault();
        } else if (e.key === 'Enter' || e.keyCode === 13) {
            // 커서가 이동된 상태(currentFocus > -1)라면 한글 조합 중이더라도 항목 선택을 우선함
            if (currentFocus > -1 && items.length > 0) { 
                e.preventDefault(); 
                items[currentFocus].click(); 
            } else if (e.isComposing) {
                // 커서 이동 없이 단순 타이핑 중 엔터인 경우, 글자 조합만 완료하고 무시
                return;
            }
        } else if (e.key === 'Escape' || e.keyCode === 27) {
            list.style.display = 'none';
        }
    });

    function addActive(items) {
        if (!items || items.length === 0) return;
        for (let i = 0; i < items.length; i++) items[i].classList.remove('active');
        if (currentFocus >= items.length) currentFocus = 0;
        if (currentFocus < 0) currentFocus = items.length - 1;
        items[currentFocus].classList.add('active');
        items[currentFocus].scrollIntoView({ block: 'nearest' });
    }

    document.addEventListener('click', function(e) {
        if (e.target !== input && e.target !== list) list.style.display = 'none';
    });
}

const defaultBrokers = ["키움증권", "미래에셋증권", "NH투자증권", "한국투자증권", "삼성증권", "토스증권"];
function getStockOptions() {
    const historyStocks = cloudEntries.map(entry => entry.stockName).filter(Boolean);
    return [...new Set([...defaultStocks, ...historyStocks])].sort();
}
function getBrokerOptions() {
    const historyBrokers = cloudEntries.map(entry => entry.brokerAccount).filter(Boolean);
    return [...new Set([...defaultBrokers, ...historyBrokers])].sort();
}

setupAutocomplete('stockName', 'stockNameList', getStockOptions);
setupAutocomplete('brokerAccount', 'brokerAccountList', getBrokerOptions);
setupAutocomplete('filterStock', 'filterStockList', getStockOptions);

function resetAndCloseForm() {
    formModalOverlay.style.display = 'none';
    journalForm.reset();
    
    currentTags = [];
    renderTags();
    calcTotalAmount();
    currentSelectedFile = null;
    currentAttachedImage = null;
    document.getElementById('imageInput').value = '';
    const previewContainer = document.getElementById('imagePreviewContainer');
    if (previewContainer) previewContainer.style.display = 'none';
    
    if (window.quill) window.quill.setContents([]); // 에디터 초기화
    editingEntryId = null;
    submitBtn.innerText = "기록 저장하기";
    const tradeRadio = document.querySelector('input[name="recordType"][value="trade"]');
    if(tradeRadio) { tradeRadio.checked = true; tradeRadio.dispatchEvent(new Event('change')); }
    const resetNow = new Date(); resetNow.setMinutes(resetNow.getMinutes() - resetNow.getTimezoneOffset());
    if (window.tradeDatePicker) {
        window.tradeDatePicker.setDate(resetNow.toISOString().slice(0,16));
    } else {
        document.getElementById('tradeDate').value = resetNow.toISOString().slice(0,16);
    }
}

// ⭐️ Flatpickr 초기화 (날짜 및 시간 선택기)
window.tradeDatePicker = flatpickr("#tradeDate", {
    enableTime: true,
    dateFormat: "Y-m-d\\TH:i",
    locale: "ko",
    time_24hr: false
});

const now = new Date();
now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
window.tradeDatePicker.setDate(now.toISOString().slice(0,16));

// ⭐️ 총 금액 자동 계산 로직
function calcTotalAmount() {
    const recordType = document.querySelector('input[name="recordType"]:checked');
    const totalWrapper = document.getElementById('totalAmountWrapper');
    if (!recordType || recordType.value !== 'trade') {
        totalWrapper.style.display = 'none'; return;
    }
    const price = Number(document.getElementById('price').value) || 0;
    const qty = Number(document.getElementById('quantity').value) || 0;
    if (price > 0 && qty > 0) {
        totalWrapper.style.display = 'block';
        document.getElementById('totalAmountDisplay').innerText = `총 금액: ${(price * qty).toLocaleString()}원`;
    } else { totalWrapper.style.display = 'none'; }
}
document.getElementById('price').addEventListener('input', calcTotalAmount);
document.getElementById('quantity').addEventListener('input', calcTotalAmount);

const typeRadios = document.querySelectorAll('input[name="recordType"]');
typeRadios.forEach(radio => {
    radio.addEventListener('change', function() {
        const isTrade = this.value === 'trade';
        document.getElementById('tradeRow1').style.display = isTrade ? 'flex' : 'none';
        document.getElementById('tradeRow2').style.display = isTrade ? 'flex' : 'none';
        document.getElementById('memoTitleGroup').style.display = isTrade ? 'none' : 'block';
        document.getElementById('brokerAccountGroup').style.display = isTrade ? 'block' : 'none';
        
        document.getElementById('stockName').required = isTrade;
        document.getElementById('stockName').placeholder = isTrade ? "검색 또는 직접 입력 (예: 삼성전자)" : "종목명 (메모 시 생략 가능)";
        document.getElementById('accountName').required = isTrade;
        document.getElementById('memoTitle').required = !isTrade;
        document.getElementById('thoughtsLabel').innerText = isTrade ? '생각의 흐름 / 계획' : '메모 내용';
        calcTotalAmount();
    });
});

// ⭐️ 해시태그 입력 로직
function renderTags() {
    const tagList = document.getElementById('tagList');
    tagList.innerHTML = '';
    currentTags.forEach((tag, index) => {
        const badge = document.createElement('span');
        badge.className = 'tag-badge';
        badge.innerHTML = `#${tag} <span class="remove-tag" onclick="removeTag(${index})">&times;</span>`;
        tagList.appendChild(badge);
    });
}
window.removeTag = function(index) { currentTags.splice(index, 1); renderTags(); };
document.getElementById('tagInput').addEventListener('keydown', function(e) {
    if (e.key === 'Enter' || e.key === ',' || e.key === ' ') {
        e.preventDefault();
        let val = this.value.trim().replace(/^#+/, '').replace(/,/g, '');
        if (val && !currentTags.includes(val)) {
            currentTags.push(val);
            renderTags();
        }
        this.value = '';
    }
});

function processImageFile() {
    if (!currentSelectedFile) {
        currentAttachedImage = null;
        const previewContainer = document.getElementById('imagePreviewContainer');
        if (previewContainer) previewContainer.style.display = 'none';
        return;
    }
    const reader = new FileReader();
    reader.onload = function(event) {
        const img = new Image();
        img.onload = function() {
            const canvas = document.createElement('canvas');
            const MAX_WIDTH = 1920; // 최대 해상도 제한
            let width = img.width;
            let height = img.height;
            if (width > MAX_WIDTH) {
                height = height * (MAX_WIDTH / width);
                width = MAX_WIDTH;
            }
            canvas.width = width;
            canvas.height = height;
            const ctx = canvas.getContext('2d');
            ctx.drawImage(img, 0, 0, width, height);
            currentAttachedImage = canvas.toDataURL('image/jpeg', 0.9);
            
            document.getElementById('imagePreview').src = currentAttachedImage;
            const container = document.getElementById('imagePreviewContainer');
            container.style.width = 'auto'; // 크기 제한 초기화
            container.style.display = 'block';
        };
        img.src = event.target.result;
    };
    reader.readAsDataURL(currentSelectedFile);
}

document.getElementById('imageInput').addEventListener('change', function(e) { currentSelectedFile = e.target.files[0]; processImageFile(); });

// 클립보드 이미지 붙여넣기 이벤트 처리
document.addEventListener('paste', function(e) {
    const formOverlay = document.getElementById('formModalOverlay');
    if (formOverlay.style.display === 'flex') {
        const items = e.clipboardData.items;
        for (let i = 0; i < items.length; i++) {
            if (items[i].type.indexOf('image') !== -1) {
                const file = items[i].getAsFile();
                currentSelectedFile = file;
                
                const dataTransfer = new DataTransfer();
                dataTransfer.items.add(file);
                document.getElementById('imageInput').files = dataTransfer.files;
                
                processImageFile();
                break;
            }
        }
    }
});

const btnRemoveImage = document.getElementById('btnRemoveImage');
if (btnRemoveImage) {
    btnRemoveImage.addEventListener('click', () => {
        currentSelectedFile = null;
        currentAttachedImage = null;
        document.getElementById('imageInput').value = '';
        document.getElementById('imagePreviewContainer').style.display = 'none';
    });
}

const loadMoreObserver = new IntersectionObserver((entries) => {
    if (entries[0].isIntersecting) {
        currentRenderPage++;
        renderPage();
    }
}, { rootMargin: '300px' }); // 스크롤이 바닥에 닿기 300px 전에 미리 다음 페이지 로딩 시작

filterStockInput.addEventListener('input', () => { 
    clearFilterBtn.style.display = filterStockInput.value ? 'block' : 'none';
    displayEntries(true); 
});
clearFilterBtn.addEventListener('click', () => {
    filterStockInput.value = '';
    clearFilterBtn.style.display = 'none';
    document.getElementById('filterStockList').style.display = 'none';
    displayEntries(true);
});

journalForm.addEventListener('submit', async function(e) {
    e.preventDefault();

    const recordType = document.querySelector('input[name="recordType"]:checked').value;
    const stockName = document.getElementById('stockName').value;
    const brokerAccount = document.getElementById('brokerAccount').value;
    const tradeDateRaw = document.getElementById('tradeDate').value;
    
    // ⭐️ 에디터에서 작성한 내용 가져오기 및 필수 입력 검증
    const thoughtsHTML = window.quill.root.innerHTML;
    const thoughtsText = window.quill.getText().trim();
    if (!thoughtsText && !thoughtsHTML.includes('<img')) {
        alert("내용을 입력해주세요."); return;
    }
    const thoughts = thoughtsHTML === '<p><br></p>' ? '' : thoughtsHTML;
    const date = tradeDateRaw ? new Date(tradeDateRaw).toLocaleString() : new Date().toLocaleString();
    
    let newEntry;
    const nowIso = new Date().toISOString();
    let createdAt = nowIso;
    
    if (editingEntryId) {
        const oldEntry = cloudEntries.find(e => e.id === editingEntryId);
        if (oldEntry) createdAt = oldEntry.createdAt || new Date(oldEntry.id).toISOString(); // 기존 시간 유지
    }

    if (recordType === 'trade') {
        const accountName = document.getElementById('accountName').value;
        const tradeType = document.getElementById('tradeType').value;
        const price = document.getElementById('price').value;
        const quantity = document.getElementById('quantity').value;

        newEntry = {
            id: editingEntryId || Date.now(), type: 'trade', stockName, brokerAccount, accountName,
            tradeType, price: price ? Number(price) : 0, quantity: quantity ? Number(quantity) : 0, thoughts, date, rawDate: tradeDateRaw, attachedImage: currentAttachedImage,
            createdAt, updatedAt: nowIso, tags: currentTags.join(',')
        };
    } else {
        const memoTitle = document.getElementById('memoTitle').value;
        newEntry = { id: editingEntryId || Date.now(), type: 'memo', stockName, title: memoTitle, thoughts, date, rawDate: tradeDateRaw, attachedImage: currentAttachedImage, createdAt, updatedAt: nowIso, tags: currentTags.join(',') };
    }

    if (editingEntryId) {
        const index = cloudEntries.findIndex(e => e.id === editingEntryId);
        if (index > -1) cloudEntries[index] = newEntry;
        editingEntryId = null;
        submitBtn.innerText = "기록 저장하기";
    } else {
        cloudEntries.unshift(newEntry);
    }
    
    resetAndCloseForm();
    
    await saveToLocal(true); // 저장 후 화면 전체를 새로고침하여 최신 상태 반영
});

document.getElementById('btnImportExcel').addEventListener('click', () => document.getElementById('excelFileInput').click());
document.getElementById('excelFileInput').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = async function(event) {
        const data = new Uint8Array(event.target.result);
        const workbook = XLSX.read(data, {type: 'array'});
        const firstSheetName = workbook.SheetNames[0];
        const worksheet = workbook.Sheets[firstSheetName];
        const rows = XLSX.utils.sheet_to_json(worksheet, {header: 1});

        let importedCount = 0;
        for (let i = 1; i < rows.length; i++) {
            const row = rows[i];
            if (!row || row.length === 0) continue;

            const date = row[0] || '';
            const type = row[1] || 'trade';
            const stockName = row[2] || '';
            const brokerAccount = row[3] || '';
            const accountName = row[4] || '';
            const tradeType = row[5] || '';
            const price = Number(row[6]) || 0;
            const quantity = Number(row[7]) || 0;
            const thoughts = row[8] || '';
            const tags = row[9] || '';

            // 중복 방지 (같은 날짜와 같은 메모 내용이 있는지 확인)
            if (!cloudEntries.find(e => e.date === date && e.thoughts === thoughts)) {
                const nowIso = new Date().toISOString();
                cloudEntries.push({
                    id: Date.now() + i, type: type === 'memo' ? 'memo' : 'trade',
                    stockName, brokerAccount, accountName, tradeType, price,
                    quantity, thoughts, date: date || new Date().toLocaleString(),
                    createdAt: nowIso, updatedAt: nowIso, tags
                });
                importedCount++;
            }
        }
        if (importedCount > 0) {
            alert(`${importedCount}개의 기록을 Excel에서 가져왔습니다.`);
            await saveToLocal(true);
        } else {
            alert('가져올 새로운 기록이 없거나 형식이 잘못되었습니다.');
        }
    };
    reader.readAsArrayBuffer(file);
    e.target.value = '';
});

document.getElementById('btnExportExcel').addEventListener('click', () => {
    const header = ['작성일', '분류', '종목명', '증권사', '계좌분류', '매매종류', '단가', '수량', '메모/생각', '태그'];
    const rows = cloudEntries.map(e => [
        e.date, e.type, e.stockName||'', e.brokerAccount||'', e.accountName||'',
        e.tradeType||'', Number(e.price)||0, Number(e.quantity)||0, 
        (e.thoughts||'').replace(/<[^>]*>?/gm, '').replace(/&nbsp;/g, ' '), e.tags||'' // HTML 태그 제거 후 엑셀 내보내기
    ]);
    
    const worksheet = XLSX.utils.aoa_to_sheet([header, ...rows]);
    const workbook = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(workbook, worksheet, "매매일지");
    XLSX.writeFile(workbook, "주식매매일지_백업.xlsx");
});

function updatePortfolioSummary() {
    const portfolio = {};
    const chartLabels = [];
    const chartData = [];
    let totalRealizedProfit = 0;
    let totalInvestedAmount = 0;
    let holdingsCount = 0;
    let monthlyBuyCount = 0;
    let monthlySellCount = 0;
    
    const nowDt = new Date();
    const curYear = nowDt.getFullYear();
    const curMonth = nowDt.getMonth();
    
    const chronologicalEntries = [...cloudEntries].reverse();

    chronologicalEntries.forEach(entry => {
        if (entry.type !== 'trade' || !entry.stockName) return;
        
        // ⭐️ 대시보드 증권사 필터 적용
        if (currentDashboardBroker !== 'all' && (entry.brokerAccount || '') !== currentDashboardBroker) return;

        const stock = entry.stockName;
        const qty = Number(entry.quantity) || 0;
        const price = Number(entry.price) || 0;

        if (!portfolio[stock]) portfolio[stock] = { qty: 0, totalCost: 0, avgPrice: 0, realizedProfit: 0, accountName: '', traded: false };
        if (entry.accountName) portfolio[stock].accountName = entry.accountName; // 가장 최근 거래의 투자 분류 기록

        // 이번 달 거래인지 확인
        let isCurrentMonth = false;
        let entryDate = null;
        if (entry.rawDate) entryDate = new Date(entry.rawDate);
        else if (entry.id) entryDate = new Date(entry.id);
        
        if (entryDate && !isNaN(entryDate) && entryDate.getFullYear() === curYear && entryDate.getMonth() === curMonth) {
            isCurrentMonth = true;
        }

        if (entry.tradeType === '매수') {
            if (isCurrentMonth) monthlyBuyCount++;
            portfolio[stock].traded = true;
            portfolio[stock].qty += qty;
            portfolio[stock].totalCost += (price * qty);
            if (portfolio[stock].qty > 0) portfolio[stock].avgPrice = portfolio[stock].totalCost / portfolio[stock].qty;
        } else if (entry.tradeType === '매도') {
            if (isCurrentMonth) monthlySellCount++;
            portfolio[stock].traded = true;
            const currentAvgPrice = portfolio[stock].avgPrice;
            const profit = (price - currentAvgPrice) * qty;
            portfolio[stock].realizedProfit += profit;
            totalRealizedProfit += profit;

            portfolio[stock].qty -= qty;
            portfolio[stock].totalCost -= (currentAvgPrice * qty);
            if (portfolio[stock].qty <= 0) { portfolio[stock].qty = 0; portfolio[stock].totalCost = 0; portfolio[stock].avgPrice = 0; }
        }
    });

    const portfolioGrid = document.getElementById('portfolioGrid');
    portfolioGrid.innerHTML = '';
    let hasHoldings = false;
    currentHoldings = [];

    // ⭐️ 포트폴리오를 배열로 변환하고 투자 분류에 따라 정렬
    const portfolioArray = [];
    for (const stock in portfolio) {
        const p = portfolio[stock];
        const isClosed = p.qty <= 0;
        if (!isClosed) {
            portfolioArray.push({ stock, ...p, isClosed: false });
        } else if (showClosedPositions && p.traded) {
            portfolioArray.push({ stock, ...p, isClosed: true }); // 청산 종목 포함
        }
    }

    const sortOrder = { "장기투자": 1, "중기투자": 2, "단기스윙": 3, "단타(스캘핑)": 4, "배당투자": 5, "공모주": 6, "기타": 7 };
    portfolioArray.sort((a, b) => {
        const orderA = sortOrder[a.accountName] || 99;
        const orderB = sortOrder[b.accountName] || 99;
        if (orderA !== orderB) return orderA - orderB;
        return a.stock.localeCompare(b.stock); // 분류가 같으면 종목명 가나다순 정렬
    });

    portfolioArray.forEach(data => {
        const stock = data.stock;
        const isClosed = data.isClosed;
        
        // 현재 보유 중인 종목만 차트 및 상단 요약 수치에 반영
        if (!isClosed) {
            totalInvestedAmount += data.totalCost;
            holdingsCount++;
            currentHoldings.push(stock);
            hasHoldings = true;
            chartLabels.push(stock);
            chartData.push(data.totalCost);
        }
        
        const card = document.createElement('div');
        card.className = 'portfolio-card';
        if (isClosed) {
            card.style.opacity = '0.6'; // 청산 종목은 반투명하게 표시
            card.style.borderLeftColor = 'var(--text-muted-color)';
        }
        const statusBadge = isClosed ? `<span style="font-size: 10px; background: var(--border-color); color: var(--card-bg-color); padding: 1px 4px; border-radius: 3px; margin-left: 4px;">청산완료</span>` : '';
        card.innerHTML = `
            <div class="stock-name">${stock} <span style="font-size: 11px; color: var(--text-muted-color); font-weight: normal;">${data.accountName ? `(${data.accountName})` : ''}</span>${statusBadge}</div>
            <div class="stat-row"><span>보유 수량</span><span>${data.qty.toLocaleString()}주</span></div>
            <div class="stat-row"><span>평균 단가</span><span>${Math.round(data.avgPrice).toLocaleString()}</span></div>
            <div class="stat-row"><span>총 매수금액</span><span>${Math.round(data.totalCost).toLocaleString()}</span></div>
        `;
        
        if (data.realizedProfit !== 0) {
            const profitColor = data.realizedProfit > 0 ? 'var(--danger-color)' : 'var(--primary-color)';
            const profitStr = (data.realizedProfit > 0 ? '+' : '') + Math.round(data.realizedProfit).toLocaleString();
            card.innerHTML += `
                <div class="stat-row" style="margin-top: 8px; padding-top: 8px; border-top: 1px dashed var(--border-color);">
                    <span>종목 실현손익</span><span style="color:${profitColor}">${profitStr}</span>
                </div>`;
        }
        
        // ⭐️ 종목 카드 클릭 시 해당 종목 히스토리 필터링 이벤트 연동
        card.title = `${stock} 기록 모아보기`;
        card.addEventListener('click', () => {
            currentFilterDate = null; // 날짜 필터 초기화
            currentFilterType = 'stock_' + stock; // 드롭다운 필터값 설정
            
            // 캘린더 뷰인 경우 리스트 뷰로 자동 전환
            const btnListView = document.getElementById('btnListView');
            if (btnListView && !btnListView.classList.contains('active')) {
                btnListView.click();
            }
            
            displayEntries(true); // 필터링 반영
            
            // 사용자 편의를 위해 필터/히스토리 영역으로 부드럽게 스크롤
            const filterBox = document.getElementById('filterBoxContainer');
            if (filterBox) filterBox.scrollIntoView({ behavior: 'smooth', block: 'start' });
        });

        portfolioGrid.appendChild(card);
    });
    
    let toggleBtn = document.getElementById('btnTogglePortfolio');
    const shouldShowDashboard = (hasHoldings || totalRealizedProfit !== 0 || portfolioArray.length > 0);

    if (toggleBtn) {
        toggleBtn.innerHTML = isDashboardCollapsed ? '펼치기 ▼' : '접기 ▲';
        toggleBtn.style.display = shouldShowDashboard ? 'inline-block' : 'none';
    }

    if (portfolioGrid) portfolioGrid.style.display = isDashboardCollapsed ? 'none' : '';
    
    const brokerFilterEl = document.getElementById('dashboardBrokerFilter');
    if (brokerFilterEl && brokerFilterEl.parentElement) {
        brokerFilterEl.parentElement.style.display = isDashboardCollapsed ? 'none' : 'flex';
    }
    
    const theme = document.documentElement.getAttribute('data-theme') || 'light';
    const legendColor = theme === 'dark' ? '#e0e0e0' : '#2c3e50';
    const chartColors = theme === 'dark' 
        ? ['#2a5298', '#c0392b', '#d68910', '#1e8449', '#76448a', '#ca6f1e', '#117a65', '#283747'] // 다크모드용 차분한 색상
        : ['#3498db', '#e74c3c', '#f1c40f', '#2ecc71', '#9b59b6', '#e67e22', '#1abc9c', '#34495e']; // 라이트모드용 기본 색상

    const chartContainer = document.getElementById('portfolioChartContainer');
    if (shouldShowDashboard && !isDashboardCollapsed) {
        chartContainer.style.display = 'block';
        
        // 차트 중앙 텍스트 업데이트
        const investedStr = Math.round(totalInvestedAmount).toLocaleString() + '원';
        const elInvested = document.getElementById('centerTotalInvested');
        elInvested.innerText = investedStr;
        elInvested.style.fontSize = investedStr.length > 13 ? '13px' : (investedStr.length > 10 ? '15px' : '17px');

        const centerProfit = document.getElementById('centerTotalProfit');
        const profitStr = (totalRealizedProfit > 0 ? '+' : '') + Math.round(totalRealizedProfit).toLocaleString() + '원';
        centerProfit.innerText = profitStr;
        centerProfit.style.fontSize = profitStr.length > 13 ? '12px' : (profitStr.length > 10 ? '13px' : '15px');
        centerProfit.style.color = totalRealizedProfit > 0 ? 'var(--danger-color)' : (totalRealizedProfit < 0 ? 'var(--primary-color)' : 'var(--text-strong-color)');
        document.getElementById('centerHoldingsCount').innerText = holdingsCount + '종목 보유';
        document.getElementById('centerTradeStats').innerText = `월간 매수 ${monthlyBuyCount} / 매도 ${monthlySellCount}`;

        // 보유 종목이 없을 때(전량 매도) 보여줄 '빈 고리' 더미 데이터 처리
        const isPortfolioEmpty = totalInvestedAmount === 0;
        const finalLabels = isPortfolioEmpty ? ['보유 종목 없음'] : chartLabels;
        const finalData = isPortfolioEmpty ? [1] : chartData;
        const finalColors = isPortfolioEmpty ? [theme === 'dark' ? '#2c2c2c' : '#f0f0f0'] : chartColors;

        const ctx = document.getElementById('portfolioChart').getContext('2d');
        if (portfolioChartInstance) portfolioChartInstance.destroy();
        portfolioChartInstance = new Chart(ctx, {
            type: 'doughnut',
            data: { labels: finalLabels, datasets: [{ data: finalData, backgroundColor: finalColors, borderColor: theme === 'dark' ? '#1e1e1e' : '#fff' }] },
            options: { 
                responsive: true,
                cutout: '72%', // 중앙 구멍 크기 확장
                // ⭐️ 도넛 차트 클릭 시 필터링 및 초기화 이벤트 연동
                onClick: (e, elements, chart) => {
                    if (isPortfolioEmpty) return;
                    
                    if (elements.length > 0) {
                        // 차트의 특정 조각(종목) 클릭 시
                        const idx = elements[0].index;
                        const stock = chart.data.labels[idx];
                        currentFilterDate = null;
                        currentFilterType = 'stock_' + stock;
                    } else {
                        // 차트의 중앙(빈 공간) 클릭 시 필터 초기화(전체 보기)
                        currentFilterDate = null;
                        currentFilterType = null;
                    }
                    
                    const btnListView = document.getElementById('btnListView');
                    if (btnListView && !btnListView.classList.contains('active')) {
                        btnListView.click();
                    }
                    displayEntries(true);
                    
                    const filterBox = document.getElementById('filterBoxContainer');
                    if (filterBox) filterBox.scrollIntoView({ behavior: 'smooth', block: 'start' });
                },
                onHover: (e, elements, chart) => {
                    chart.canvas.style.cursor = isPortfolioEmpty ? 'default' : 'pointer';
                },
                plugins: { 
                    legend: { position: 'bottom', labels: { boxWidth: 12, color: legendColor } },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                if (isPortfolioEmpty) return '현재 보유 중인 종목이 없습니다.';
                                let value = context.parsed;
                                let total = context.dataset.data.reduce((a, b) => a + b, 0);
                                let percentage = total > 0 ? ((value / total) * 100).toFixed(1) + '%' : '0%';
                                return `${context.label}: ${Math.round(value).toLocaleString()}원 (${percentage})`;
                            }
                        }
                    }
                } 
            }
        });
    } else { chartContainer.style.display = 'none'; }

    document.getElementById('portfolioSection').style.display = shouldShowDashboard ? 'block' : 'none';
}

// ⭐️ 드롭다운 필터에 종목명을 동적으로 추가하는 함수
function updateFilterDropdown() {
    const select = document.getElementById('filterTypeSelect');
    if (!select) return;
    
    const currentVal = currentFilterType || 'all';
    let html = `<option value="all">전체 보기</option>
                <option value="trade">매매 기록만</option>
                <option value="memo">일반 메모만</option>`;
    
    const stocks = [...new Set(cloudEntries.map(e => e.stockName).filter(Boolean))].sort();
    if (stocks.length > 0) {
        html += `<optgroup label="종목별 모아보기">`;
        stocks.forEach(stock => {
            html += `<option value="stock_${stock.replace(/"/g, '&quot;')}">${stock}</option>`;
        });
        html += `</optgroup>`;
    }
    
    const accountSortOrder = { "장기투자": 1, "중기투자": 2, "단기스윙": 3, "단타(스캘핑)": 4, "배당투자": 5, "공모주": 6, "기타": 7 };
    const accounts = [...new Set(cloudEntries.map(e => e.accountName).filter(Boolean))].sort((a, b) => {
        const orderA = accountSortOrder[a] || 99;
        const orderB = accountSortOrder[b] || 99;
        if (orderA !== orderB) return orderA - orderB;
        return a.localeCompare(b);
    });
    if (accounts.length > 0) {
        html += `<optgroup label="분류별 모아보기">`;
        accounts.forEach(account => {
            html += `<option value="account_${account.replace(/"/g, '&quot;')}">${account}</option>`;
        });
        html += `</optgroup>`;
    }
    
    const brokers = [...new Set(cloudEntries.map(e => e.brokerAccount).filter(Boolean))].sort();
    if (brokers.length > 0) {
        html += `<optgroup label="증권사별 모아보기">`;
        brokers.forEach(broker => {
            html += `<option value="broker_${broker.replace(/"/g, '&quot;')}">${broker}</option>`;
        });
        html += `</optgroup>`;
    }
    
    // ⭐️ 대시보드의 증권사 필터 옵션도 동적으로 업데이트
    const dashboardBrokerFilter = document.getElementById('dashboardBrokerFilter');
    if (dashboardBrokerFilter) {
        const currentBrokerVal = currentDashboardBroker || 'all';
        let brokerHtml = `<option value="all">모든 증권사</option>`;
        if (brokers.length > 0) {
            brokers.forEach(broker => {
                brokerHtml += `<option value="${broker.replace(/"/g, '&quot;')}">${broker}</option>`;
            });
        }
        dashboardBrokerFilter.innerHTML = brokerHtml;
        if (dashboardBrokerFilter.querySelector(`option[value="${currentBrokerVal}"]`)) {
            dashboardBrokerFilter.value = currentBrokerVal;
        } else {
            dashboardBrokerFilter.value = 'all';
            currentDashboardBroker = 'all';
        }
    }

    select.innerHTML = html;
    
    if (select.querySelector(`option[value="${currentVal}"]`)) {
        select.value = currentVal;
    } else {
        select.value = 'all';
        currentFilterType = null;
    }
}

function displayEntries(isFilterUpdate = false) {
    cloudEntries.sort((a, b) => {
        const timeA = a.rawDate ? new Date(a.rawDate).getTime() : a.id;
        const timeB = b.rawDate ? new Date(b.rawDate).getTime() : b.id;
        return timeB - timeA;
    });

    // [3안] 리스트 뷰 필터링 상태와 HTML Select 동기화
    const filterTypeSelect = document.getElementById('filterTypeSelect');
    if (filterTypeSelect) {
        let selectVal = currentFilterType || 'all';
        if (selectVal.startsWith('stock_trade_')) selectVal = 'stock_' + selectVal.substring(12);
        else if (selectVal.startsWith('stock_memo_')) selectVal = 'stock_' + selectVal.substring(11);
        
        if (filterTypeSelect.querySelector(`option[value="${selectVal}"]`)) {
            filterTypeSelect.value = selectVal;
        } else {
            filterTypeSelect.value = 'all';
        }
    }

    if (!isFilterUpdate) {
        updateFilterDropdown();
        updatePortfolioSummary();
        renderCalendar();
    }

    historyList.innerHTML = '';
    
    // 청산 종목 필터링을 위한 현재 보유 수량 계산
    const stockQtys = {};
    cloudEntries.forEach(entry => {
        if (entry.type === 'trade' && entry.stockName) {
            if (stockQtys[entry.stockName] === undefined) stockQtys[entry.stockName] = 0;
            if (entry.tradeType === '매수') stockQtys[entry.stockName] += (Number(entry.quantity) || 0);
            else if (entry.tradeType === '매도') stockQtys[entry.stockName] -= (Number(entry.quantity) || 0);
        }
    });

    // ⭐️ 분류별/증권사별 모아보기 시 연관된 일반 메모를 함께 보여주기 위해 종목명 추출
    const relatedStocksForFilter = new Set();
    if (currentFilterType && currentFilterType.startsWith('account_')) {
        const targetAccount = currentFilterType.substring(8);
        cloudEntries.forEach(e => { if ((e.type || 'trade') === 'trade' && e.accountName === targetAccount && e.stockName) relatedStocksForFilter.add(e.stockName); });
    } else if (currentFilterType && currentFilterType.startsWith('broker_')) {
        const targetBroker = currentFilterType.substring(7);
        cloudEntries.forEach(e => { if ((e.type || 'trade') === 'trade' && e.brokerAccount === targetBroker && e.stockName) relatedStocksForFilter.add(e.stockName); });
    }

    const filterValue = filterStockInput.value.trim().toLowerCase();
    const filteredEntries = cloudEntries.filter(entry => {
        if (filterValue) {
            const matchStock = entry.stockName && entry.stockName.toLowerCase().includes(filterValue);
            const matchBroker = entry.brokerAccount && entry.brokerAccount.toLowerCase().includes(filterValue);
            const matchTags = entry.tags && entry.tags.toLowerCase().includes(filterValue);
            const plainThoughts = entry.thoughts ? entry.thoughts.replace(/<[^>]*>?/gm, '').toLowerCase() : '';
            const matchThoughts = plainThoughts.includes(filterValue);
            const matchTitle = entry.title && entry.title.toLowerCase().includes(filterValue);
            if (!(matchStock || matchBroker || matchTags || matchThoughts || matchTitle)) return false;
        }
        
        if (currentFilterDate) {
            let entryDateKey = '';
            if (entry.rawDate) { entryDateKey = entry.rawDate.split('T')[0]; } 
            else if (entry.date) {
                const parts = entry.date.split('. ');
                if (parts.length >= 3) entryDateKey = `${parts[0]}-${parts[1].padStart(2,'0')}-${parts[2].split('.')[0].padStart(2,'0')}`;
            }
            if (entryDateKey !== currentFilterDate) return false;
        }

        if (currentFilterType && currentFilterType !== 'all') {
            if (currentFilterType.startsWith('stock_trade_')) {
                const targetStock = currentFilterType.substring(12);
                if ((entry.stockName || '') !== targetStock || (entry.type || 'trade') !== 'trade') return false;
            } else if (currentFilterType.startsWith('stock_memo_')) {
                const targetStock = currentFilterType.substring(11);
                if ((entry.stockName || '') !== targetStock || (entry.type || 'trade') !== 'memo') return false;
            } else if (currentFilterType.startsWith('stock_')) {
                const targetStock = currentFilterType.substring(6);
                if ((entry.stockName || '') !== targetStock) return false;
            } else if (currentFilterType.startsWith('account_')) {
                const targetAccount = currentFilterType.substring(8);
                const entryType = entry.type || 'trade';
                const isMatchTrade = entryType === 'trade' && (entry.accountName || '') === targetAccount;
                const isMatchMemo = entryType === 'memo' && relatedStocksForFilter.has(entry.stockName);
                if (!isMatchTrade && !isMatchMemo) return false;
            } else if (currentFilterType.startsWith('broker_')) {
                const targetBroker = currentFilterType.substring(7);
                const entryType = entry.type || 'trade';
                const isMatchTrade = entryType === 'trade' && (entry.brokerAccount || '') === targetBroker;
                const isMatchMemo = entryType === 'memo' && relatedStocksForFilter.has(entry.stockName);
                if (!isMatchTrade && !isMatchMemo) return false;
            } else {
                const entryType = entry.type || 'trade';
                if (entryType !== currentFilterType) return false;
            }
        }
        
        // ⭐️ 청산 종목 숨기기 상태일 때 (보유 수량이 0인 종목을 검색 및 필터에서 제외)
        if (!showHistoryClosedPositions) {
            if (entry.stockName && stockQtys[entry.stockName] !== undefined && stockQtys[entry.stockName] <= 0) return false; 
        }
        
        return true;
    });

    const banner = document.getElementById('activeFilterBanner');
    if (currentFilterDate) {
        banner.style.display = 'flex';
        let typeText = '전체 기록';
        if (currentFilterType === 'trade') typeText = '매매 기록';
        else if (currentFilterType === 'memo') typeText = '일반 메모';
        else if (currentFilterType && currentFilterType.startsWith('stock_trade_')) {
            const st = currentFilterType.substring(12);
            typeText = st ? st + ' 매매 기록' : '종목 미지정 매매 기록';
        } else if (currentFilterType && currentFilterType.startsWith('stock_memo_')) {
            const st = currentFilterType.substring(11);
            typeText = st ? st + ' 일반 메모' : '종목 미지정 일반 메모';
        } else if (currentFilterType && currentFilterType.startsWith('stock_')) {
            const st = currentFilterType.substring(6);
            typeText = st ? st + ' 기록' : '종목 미지정 기록';
        } else if (currentFilterType && currentFilterType.startsWith('account_')) {
            const acc = currentFilterType.substring(8);
            typeText = acc ? acc + ' 기록' : '분류 미지정 기록';
        } else if (currentFilterType && currentFilterType.startsWith('broker_')) {
            const brk = currentFilterType.substring(7);
            typeText = brk ? brk + ' 기록' : '증권사 미지정 기록';
        }
        document.getElementById('activeFilterText').innerText = `📅 ${currentFilterDate} 일자의 ${typeText} 모아보기`;
    } else { banner.style.display = 'none'; }

    currentFilteredEntries = filteredEntries;
    currentRenderPage = 1;
    lastRenderedMonth = '';

    if (filteredEntries.length === 0) {
        historyList.innerHTML = '<p style="text-align:center; color:var(--text-muted-color); font-size: 16px; padding: 20px;">조건에 맞는 기록이 없습니다.</p>';
        return;
    }

    renderPage();
}

function renderPage() {
    const existingSentinel = document.getElementById('scroll-sentinel');
    if (existingSentinel) {
        loadMoreObserver.unobserve(existingSentinel);
        existingSentinel.remove();
    }

    const start = (currentRenderPage - 1) * entriesPerPage;
    const end = start + entriesPerPage;
    const pageEntries = currentFilteredEntries.slice(start, end);

    pageEntries.forEach(entry => {
        // ⭐️ 월별 타임라인 구분선 로직
        let entryMonth = '';
        let parsedDate = null;
        if (entry.rawDate) parsedDate = new Date(entry.rawDate);
        else if (entry.id) parsedDate = new Date(entry.id);
        
        if (parsedDate && !isNaN(parsedDate)) {
            entryMonth = `${parsedDate.getFullYear()}년 ${parsedDate.getMonth() + 1}월`;
        }

        if (entryMonth && entryMonth !== lastRenderedMonth) {
            const divider = document.createElement('div');
            divider.className = 'timeline-divider';
            divider.innerText = entryMonth;
            historyList.appendChild(divider);
            lastRenderedMonth = entryMonth;
        }

        const card = document.createElement('div');
        card.className = 'entry-card';
        const entryType = entry.type || 'trade';
        const imageHtml = entry.attachedImage ? `<div style="margin-top:10px;"><img src="${entry.attachedImage}" class="entry-thumbnail" onclick="openImageViewer(this.src, event)" title="클릭하여 원본 보기"></div>` : '';

        const createdStr = entry.createdAt ? new Date(entry.createdAt).toLocaleString() : new Date(entry.id).toLocaleString();
        const updatedStr = entry.updatedAt ? new Date(entry.updatedAt).toLocaleString() : '';
        const timeDisplayHtml = `
            <div style="display: flex; flex-direction: column; gap: 3px;">
                <span style="color: var(--text-strong-color); font-weight: var(--fw-bold, bold);">🕒 기록 일시: ${entry.date}</span>
                <span style="font-size: 11px; color: var(--text-muted-color);">최초 작성: ${createdStr}${updatedStr && updatedStr !== createdStr ? ` | 최종 수정: ${updatedStr}` : ''}</span>
            </div>
        `;
        const tagsArr = entry.tags ? entry.tags.split(',').filter(Boolean) : [];
        const tagsHtml = tagsArr.length > 0 ? `<div style="margin-top: 8px;">` + tagsArr.map(t => `<span class="history-tag">#${t}</span>`).join('') + `</div>` : '';

        if (entryType === 'memo') {
            card.style.borderLeftColor = 'var(--primary-color)';
            const stockBadge = entry.stockName ? `<span class="cal-badge memo" style="padding:3px 8px; border-radius:12px; font-size:0.8em; margin-right:8px; display:inline-block;">🏷️ ${entry.stockName}</span>` : '';
            const brokerBadge = entry.brokerAccount ? `<span style="font-size: 0.8em; color: var(--text-muted-color); font-weight: normal; margin-left: 8px;">🏦 ${entry.brokerAccount}</span>` : '';
            card.innerHTML = `
            <div class="entry-header">
                ${timeDisplayHtml}
                <div class="header-right"><span>📝 일반 메모</span><button class="btn-edit">수정</button><button class="btn-delete">삭제</button></div>
            </div>
                <div class="entry-title">${stockBadge}${entry.title}${brokerBadge}</div>
                <div class="entry-content ql-snow" style="border:none; padding:0;"><div class="ql-editor" style="padding:0; min-height:auto; font-family:inherit; font-size:inherit;">${entry.thoughts}</div></div>
                ${tagsHtml}
                ${imageHtml}
            `;
        } else {
            let typeColor = 'var(--text-muted-color)';
            if(entry.tradeType === '매수') typeColor = 'var(--danger-color)';
            if(entry.tradeType === '매도') typeColor = 'var(--primary-color)';

            let detailsHtml = '';
            if (entry.tradeType !== '관망' && (entry.price > 0 || entry.quantity > 0)) {
                const priceStr = entry.price ? entry.price.toLocaleString() : '0';
                const qtyStr = entry.quantity ? entry.quantity.toLocaleString() : '0';
                const totalAmount = (entry.price * entry.quantity).toLocaleString();
                detailsHtml = `
                    <div class="entry-details">
                        <div class="detail-item">단가: <span>${priceStr}</span></div>
                        <div class="detail-item">수량: <span>${qtyStr}주</span></div>
                        <div class="detail-item">총액: <span>${totalAmount}</span></div>
                    </div>
                `;
            }
            const stockBadge = entry.stockName ? `<span class="cal-badge trade" style="padding:3px 8px; border-radius:12px; font-size:0.8em; margin-right:8px; display:inline-block;">🏷️ ${entry.stockName}</span>` : '';
            const tradeBadge = `<span style="background-color: ${typeColor}; color: white; padding:3px 8px; border-radius:12px; font-size:0.8em; margin-right:8px; display:inline-block;">${entry.tradeType}</span>`;
            const brokerBadge = entry.brokerAccount ? `<span style="font-size: 0.8em; color: var(--text-muted-color); font-weight: normal; margin-left: 8px;">🏦 ${entry.brokerAccount}</span>` : '';
            card.innerHTML = `
            <div class="entry-header">
                ${timeDisplayHtml}
                <div class="header-right"><span>💼 ${entry.accountName}</span><button class="btn-edit">수정</button><button class="btn-delete">삭제</button></div>
            </div>
                <div class="entry-title">${stockBadge}${tradeBadge}${brokerBadge}</div>
                ${detailsHtml}
                <div class="entry-content ql-snow" style="border:none; padding:0;"><div class="ql-editor" style="padding:0; min-height:auto; font-family:inherit; font-size:inherit;">${entry.thoughts}</div></div>
                ${tagsHtml}
                ${imageHtml}
            `;
        }

        const editBtn = card.querySelector('.btn-edit');
        editBtn.addEventListener('click', () => editEntry(entry));

        const deleteBtn = card.querySelector('.btn-delete');
        deleteBtn.addEventListener('click', () => deleteEntry(entry.id));

        historyList.appendChild(card);
    });

    // 스크롤 감지용 투명 요소(Sentinel) 추가
    if (end < currentFilteredEntries.length) {
        const sentinel = document.createElement('div');
        sentinel.id = 'scroll-sentinel';
        sentinel.style.padding = '20px';
        sentinel.style.textAlign = 'center';
        sentinel.style.color = 'var(--text-muted-color)';
        sentinel.style.fontSize = '12px';
        sentinel.innerHTML = '<span>⬇️ 스크롤하여 과거 기록 불러오는 중...</span>';
        historyList.appendChild(sentinel);
        loadMoreObserver.observe(sentinel);
    }
}

function editEntry(entry) {
    editingEntryId = entry.id;

    formModalOverlay.style.display = 'flex';
    
    const typeRadio = document.querySelector(`input[name="recordType"][value="${entry.type || 'trade'}"]`);
    if (typeRadio) {
        typeRadio.checked = true;
        typeRadio.dispatchEvent(new Event('change'));
    }

    document.getElementById('stockName').value = entry.stockName || '';
    document.getElementById('brokerAccount').value = entry.brokerAccount || '';
    if (window.quill) window.quill.root.innerHTML = entry.thoughts || ''; // 에디터에 기존 내용 불러오기
    
    currentTags = entry.tags ? entry.tags.split(',').filter(Boolean) : [];
    renderTags();
    calcTotalAmount();
    
    // ⭐️ 수정 시 기존에 기록된 '기록 일시'를 가져와서 설정
    if (window.tradeDatePicker) {
        const originalDate = entry.rawDate || new Date(entry.id).toISOString().slice(0, 16);
        window.tradeDatePicker.setDate(originalDate);
    } else {
        document.getElementById('tradeDate').value = entry.rawDate || new Date(entry.id).toISOString().slice(0, 16);
    }

    currentAttachedImage = entry.attachedImage || null;
    document.getElementById('imageInput').value = '';
    const preview = document.getElementById('imagePreview');
    const previewContainer2 = document.getElementById('imagePreviewContainer');
    currentSelectedFile = null;

    if (currentAttachedImage) { 
        preview.src = currentAttachedImage; 
        if(previewContainer2) {
            previewContainer2.style.width = 'auto';
            previewContainer2.style.display = 'block'; 
        }
    }
    else { if(previewContainer2) previewContainer2.style.display = 'none'; }

    if (entry.type === 'memo') {
        document.getElementById('memoTitle').value = entry.title || '';
    } else {
        document.getElementById('accountName').value = entry.accountName || '';
        document.getElementById('tradeType').value = entry.tradeType || '매수';
        document.getElementById('price').value = entry.price || '';
        document.getElementById('quantity').value = entry.quantity || '';
    }

    submitBtn.innerText = "기록 수정하기";
}

async function deleteEntry(id) {
    if (confirm("정말로 이 기록을 삭제하시겠습니까?\n(삭제 후 로컬 파일에 즉시 반영됩니다)")) {
        cloudEntries = cloudEntries.filter(e => e.id !== id);
        await saveToLocal(true);
    }
}

window.openImageViewer = function(src, event) {
    if (event) event.stopPropagation();
    const modal = document.getElementById('imageViewerModal');
    document.getElementById('fullSizeImage').src = src;
    modal.style.display = 'flex';
};

let currentDate = new Date();
function renderCalendar() {
    const dailyStats = {};
    const portfolio = {};
    const chronological = [...cloudEntries].reverse();
    
    chronological.forEach(entry => {
        let dateKey = '';
        if (entry.rawDate) { dateKey = entry.rawDate.split('T')[0]; } 
        else if (entry.date) {
            const parts = entry.date.split('. ');
            if (parts.length >= 3) dateKey = `${parts[0]}-${parts[1].padStart(2,'0')}-${parts[2].split('.')[0].padStart(2,'0')}`;
        }
        if (!dateKey) return;
        
        if (!dailyStats[dateKey]) dailyStats[dateKey] = { profit: 0, details: {} };
        
        const stockKey = entry.stockName || '';
        if (!dailyStats[dateKey].details[stockKey]) dailyStats[dateKey].details[stockKey] = { tradeCount: 0, memoCount: 0 };
        
        if (entry.type === 'trade') {
            dailyStats[dateKey].details[stockKey].tradeCount++;
            if (entry.stockName) {
                const stock = entry.stockName, qty = Number(entry.quantity) || 0, price = Number(entry.price) || 0;
                if (!portfolio[stock]) portfolio[stock] = { qty: 0, totalCost: 0, avgPrice: 0 };
                
                if (entry.tradeType === '매수') {
                    portfolio[stock].qty += qty; portfolio[stock].totalCost += (price * qty);
                    portfolio[stock].avgPrice = portfolio[stock].totalCost / portfolio[stock].qty;
                } else if (entry.tradeType === '매도') {
                    dailyStats[dateKey].profit += (price - portfolio[stock].avgPrice) * qty;
                    portfolio[stock].qty -= qty; portfolio[stock].totalCost -= (portfolio[stock].avgPrice * qty);
                    if (portfolio[stock].qty <= 0) { portfolio[stock].qty = 0; portfolio[stock].totalCost = 0; portfolio[stock].avgPrice = 0; }
                }
            }
        } else if (entry.type === 'memo') {
            dailyStats[dateKey].details[stockKey].memoCount++;
        }
    });

    const year = currentDate.getFullYear(), month = currentDate.getMonth();
    document.getElementById('calendarMonthTitle').innerText = `${year}년 ${month + 1}월`;
    
    const firstDay = new Date(year, month, 1), lastDay = new Date(year, month + 1, 0);
    const calendarGrid = document.getElementById('calendarGrid');
    calendarGrid.innerHTML = '';
    
    for(let i=0; i<firstDay.getDay(); i++) calendarGrid.innerHTML += `<div style="background:var(--border-light-color); border-radius:4px;"></div>`;
    
    for(let d=1; d<=lastDay.getDate(); d++) {
        const key = `${year}-${String(month+1).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
        
        const dStats = dailyStats[key] || { profit: 0, details: {} };
        
        let profitHtml = '';
        if (dStats.profit > 0) profitHtml = `<div style="color:var(--danger-color); font-size:11px; font-weight:var(--fw-bold, bold); margin-bottom:2px;">+${Math.round(dStats.profit).toLocaleString()}</div>`;
        else if (dStats.profit < 0) profitHtml = `<div style="color:var(--primary-color); font-size:11px; font-weight:var(--fw-bold, bold); margin-bottom:2px;">${Math.round(dStats.profit).toLocaleString()}</div>`;
        
        let badgesHtml = '';
        for (const [stock, counts] of Object.entries(dStats.details)) {
            const prefix = stock ? `${stock} ` : '';
            const safeStock = stock ? stock.replace(/'/g, "\\'") : '';
            
            if (counts.tradeCount > 0) {
                const typeArg = `stock_trade_${safeStock}`;
                badgesHtml += `<div class="cal-badge trade" onclick="showDetailsForDate('${key}', '${typeArg}', event)">${prefix}매매 ${counts.tradeCount}건</div>`;
            }
            if (counts.memoCount > 0) {
                const typeArg = `stock_memo_${safeStock}`;
                badgesHtml += `<div class="cal-badge memo" onclick="showDetailsForDate('${key}', '${typeArg}', event)">${prefix}메모 ${counts.memoCount}건</div>`;
            }
        }
        
        calendarGrid.innerHTML += `<div class="calendar-day" onclick="showDetailsForDate('${key}', 'all', event)" title="클릭하여 상세 보기"><span style="font-size:12px; font-weight:var(--fw-bold, bold); color: var(--text-strong-color);">${d}</span><div style="text-align:right;">${profitHtml}${badgesHtml}</div></div>`;
    }
}

window.showDetailsForDate = function(date, type, event) {
    if (event) event.stopPropagation();
    currentFilterDate = date;
    currentFilterType = type;
    
    document.getElementById('btnListView').click();
    displayEntries(true);
};

window.clearDateFilter = function() {
    currentFilterDate = null;
    currentFilterType = null;
    displayEntries(true);
};

document.getElementById('btnListView').addEventListener('click', function() {
    this.classList.add('active'); document.getElementById('btnCalendarView').classList.remove('active');
    document.getElementById('historyList').style.display = 'flex';
    document.getElementById('calendarViewSection').style.display = 'none';
    document.getElementById('filterBoxContainer').style.display = 'block';
});

document.getElementById('btnCalendarView').addEventListener('click', function() {
    this.classList.add('active'); document.getElementById('btnListView').classList.remove('active');
    document.getElementById('historyList').style.display = 'none';
    document.getElementById('calendarViewSection').style.display = 'block';
    document.getElementById('filterBoxContainer').style.display = 'none';
    renderCalendar();
});

document.getElementById('btnPrevMonth').addEventListener('click', () => { currentDate.setMonth(currentDate.getMonth() - 1); renderCalendar(); });
document.getElementById('btnNextMonth').addEventListener('click', () => { currentDate.setMonth(currentDate.getMonth() + 1); renderCalendar(); });