let cloudEntries = [];
let currentHoldings = [];
let newsInterval = null;
let currentFilterDate = null;
let currentFilterType = null;

const mainApp = document.getElementById('mainApp');
window.addEventListener('DOMContentLoaded', () => {
    const themeToggle = document.getElementById('theme-toggle');

    function applyTheme(theme) {
        document.documentElement.setAttribute('data-theme', theme);
        if (theme === 'dark') {
            themeToggle.checked = true;
        } else {
            themeToggle.checked = false;
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

    loadDataFromLocal();
});

async function loadDataFromLocal() {
    try {
        const response = await fetch('/api/data');
        cloudEntries = await response.json();
        updateStockOptions();
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
const formContainer = document.getElementById('formContainer');
const formModalOverlay = document.getElementById('formModalOverlay');
const btnFab = document.getElementById('btnFab');
const btnCloseForm = document.getElementById('btnCloseForm');
const submitBtn = journalForm.querySelector('button[type="submit"]');
let editingEntryId = null;
let portfolioChartInstance = null;
let currentAttachedImage = null;
let currentSelectedFile = null;

const defaultStocks = [
    "삼성전자", "SK하이닉스", "LG에너지솔루션", "현대차", "기아", "셀트리온", "POSCO홀딩스", "NAVER", "카카오",
    "애플 (AAPL)", "테슬라 (TSLA)", "엔비디아 (NVDA)", "마이크로소프트 (MSFT)", "알파벳 (GOOGL)", "아마존 (AMZN)"
];

btnFab.addEventListener('click', () => {
    formModalOverlay.style.display = 'flex';
});

btnCloseForm.addEventListener('click', resetAndCloseForm);

formModalOverlay.addEventListener('click', (e) => {
    if (e.target === formModalOverlay) resetAndCloseForm();
});

function resetAndCloseForm() {
    formModalOverlay.style.display = 'none';
    journalForm.reset();
    
    currentSelectedFile = null;
    currentAttachedImage = null;
    document.getElementById('imageInput').value = '';
    const previewContainer = document.getElementById('imagePreviewContainer');
    if (previewContainer) previewContainer.style.display = 'none';
    
    editingEntryId = null;
    submitBtn.innerText = "기록 저장하기";
    const tradeRadio = document.querySelector('input[name="recordType"][value="trade"]');
    if(tradeRadio) { tradeRadio.checked = true; tradeRadio.dispatchEvent(new Event('change')); }
    const resetNow = new Date(); resetNow.setMinutes(resetNow.getMinutes() - resetNow.getTimezoneOffset());
    document.getElementById('tradeDate').value = resetNow.toISOString().slice(0,16);
}

function updateStockOptions() {
    const datalist = document.getElementById('stockOptions');
    const historyStocks = cloudEntries.map(entry => entry.stockName).filter(Boolean);
    const allStocks = [...new Set([...defaultStocks, ...historyStocks])].sort();
    
    datalist.innerHTML = '';
    allStocks.forEach(stock => {
        const option = document.createElement('option');
        option.value = stock;
        datalist.appendChild(option);
    });
}

const now = new Date();
now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
document.getElementById('tradeDate').value = now.toISOString().slice(0,16);

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
    });
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

filterStockInput.addEventListener('input', () => { displayEntries(); });

journalForm.addEventListener('submit', async function(e) {
    e.preventDefault();

    const recordType = document.querySelector('input[name="recordType"]:checked').value;
    const stockName = document.getElementById('stockName').value;
    const brokerAccount = document.getElementById('brokerAccount').value;
    const tradeDateRaw = document.getElementById('tradeDate').value;
    const thoughts = document.getElementById('thoughts').value;
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
            createdAt, updatedAt: nowIso
        };
    } else {
        const memoTitle = document.getElementById('memoTitle').value;
        newEntry = { id: editingEntryId || Date.now(), type: 'memo', stockName, title: memoTitle, thoughts, date, rawDate: tradeDateRaw, attachedImage: currentAttachedImage, createdAt, updatedAt: nowIso };
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

document.getElementById('btnImportCSV').addEventListener('click', () => document.getElementById('csvFileInput').click());
document.getElementById('csvFileInput').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const text = await file.text();
    const lines = text.split('\n');
    let importedCount = 0;
    for (let i = 1; i < lines.length; i++) {
        if (!lines[i].trim()) continue;
        const row = lines[i].match(/(".*?"|[^",\s]+)(?=\s*,|\s*$)/g)?.map(str => str.replace(/^"|"$/g, '').replace(/""/g, '"')) || [];
        if (row.length >= 9) {
            let date, type, stockName, brokerAccount, accountName, tradeType, price, quantity, thoughts;
            
            // 이전 버전(ID 포함 10개 컬럼)과 새 버전(ID 제외 9개 컬럼) 호환성 처리
            if (row.length >= 10 && !isNaN(row[0]) && row[0].length >= 10) {
                [ , type, stockName, brokerAccount, accountName, tradeType, price, quantity, thoughts, date] = row;
            } else {
                [date, type, stockName, brokerAccount, accountName, tradeType, price, quantity, thoughts] = row;
            }
            
            // 중복 방지 (같은 날짜와 같은 메모 내용이 있는지 확인)
            if (!cloudEntries.find(e => e.date === date && e.thoughts === thoughts)) {
                const nowIso = new Date().toISOString();
                cloudEntries.push({
                    id: Date.now() + i, type: type === 'memo' ? 'memo' : 'trade',
                    stockName, brokerAccount, accountName, tradeType, price: Number(price) || 0,
                    quantity: Number(quantity) || 0, thoughts, date: date || new Date().toLocaleString(),
                    createdAt: nowIso, updatedAt: nowIso
                });
                importedCount++;
            }
        }
    }
    if (importedCount > 0) {
        alert(`${importedCount}개의 기록을 가져왔습니다.`);
        await saveToLocal(true);
    } else {
        alert('가져올 새로운 기록이 없거나 형식이 잘못되었습니다.');
    }
    e.target.value = '';
});

document.getElementById('btnExportCSV').addEventListener('click', () => {
    const header = ['작성일', '분류', '종목명', '증권사', '계좌분류', '매매종류', '단가', '수량', '메모/생각'];
    const rows = cloudEntries.map(e => [
        e.date, e.type, e.stockName||'', e.brokerAccount||'', e.accountName||'',
        e.tradeType||'', e.price||0, e.quantity||0, 
        (e.thoughts||'').replace(/\n/g, ' ').replace(/"/g, '""')
    ]);
    const csvContent = [header, ...rows].map(e => e.map(item => `"${item}"`).join(',')).join('\n');
    const blob = new Blob(["\uFEFF"+csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.setAttribute('href', url);
    link.setAttribute('download', '주식매매일지_백업.csv');
    document.body.appendChild(link); link.click(); document.body.removeChild(link);
});

function updatePortfolioSummary() {
    const portfolio = {};
    const chartLabels = [];
    const chartData = [];
    let totalRealizedProfit = 0;
    let totalInvestedAmount = 0;
    let holdingsCount = 0;
    let totalBuyCount = 0;
    let totalSellCount = 0;
    const chronologicalEntries = [...cloudEntries].reverse();

    chronologicalEntries.forEach(entry => {
        if (entry.type !== 'trade' || !entry.stockName) return;
        const stock = entry.stockName;
        const qty = Number(entry.quantity) || 0;
        const price = Number(entry.price) || 0;

        if (!portfolio[stock]) portfolio[stock] = { qty: 0, totalCost: 0, avgPrice: 0, realizedProfit: 0 };

        if (entry.tradeType === '매수') {
            totalBuyCount++;
            portfolio[stock].qty += qty;
            portfolio[stock].totalCost += (price * qty);
            if (portfolio[stock].qty > 0) portfolio[stock].avgPrice = portfolio[stock].totalCost / portfolio[stock].qty;
        } else if (entry.tradeType === '매도') {
            totalSellCount++;
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

    const filterValue = filterStockInput.value.trim().toLowerCase();

    for (const stock in portfolio) {
        if (portfolio[stock].qty > 0) {
            totalInvestedAmount += portfolio[stock].totalCost;
            holdingsCount++;
            currentHoldings.push(stock);

            if (filterValue && !stock.toLowerCase().includes(filterValue)) continue;
            hasHoldings = true;
            chartLabels.push(stock);
            chartData.push(portfolio[stock].totalCost);
            const data = portfolio[stock];
            const card = document.createElement('div');
            card.className = 'portfolio-card';
            card.innerHTML = `
                <div class="stock-name">${stock}</div>
                <div class="stat-row"><span>보유 수량</span><span>${data.qty.toLocaleString()}주</span></div>
                <div class="stat-row"><span>평균 단가</span><span>${Math.round(data.avgPrice).toLocaleString()}</span></div>
                <div class="stat-row"><span>총 매수금액</span><span>${Math.round(data.totalCost).toLocaleString()}</span></div>
            `;
            
            if (data.realizedProfit !== 0) {
                const profitColor = data.realizedProfit > 0 ? '#e74c3c' : '#3498db';
                card.innerHTML += `
                    <div class="stat-row" style="margin-top: 8px; padding-top: 8px; border-top: 1px dashed #eee;">
                        <span>종목 실현손익</span><span style="color:${profitColor}">${Math.round(data.realizedProfit).toLocaleString()}</span>
                    </div>`;
            }
            portfolioGrid.appendChild(card);
        }
    }
    
    const dashContainer = document.getElementById('dashboardContainer');
    dashContainer.style.display = (holdingsCount > 0 || totalRealizedProfit !== 0) ? 'flex' : 'none';
    
    const dashProfit = document.getElementById('dashTotalProfit');
    dashProfit.innerText = Math.round(totalRealizedProfit).toLocaleString() + '원';
    dashProfit.style.color = totalRealizedProfit > 0 ? '#e74c3c' : (totalRealizedProfit < 0 ? '#3498db' : '#2c3e50');
    
    document.getElementById('dashTotalInvested').innerText = Math.round(totalInvestedAmount).toLocaleString() + '원';
    document.getElementById('dashHoldingsCount').innerText = holdingsCount + '개';
    document.getElementById('dashTradeStats').innerHTML = `<span style="color:var(--danger-color)">매수 ${totalBuyCount}</span> / <span style="color:var(--primary-color)">매도 ${totalSellCount}</span>`;
    
    const theme = document.documentElement.getAttribute('data-theme') || 'light';
    const legendColor = theme === 'dark' ? '#e0e0e0' : '#2c3e50';

    const chartContainer = document.getElementById('portfolioChartContainer');
    if (hasHoldings) {
        chartContainer.style.display = 'block';
        const ctx = document.getElementById('portfolioChart').getContext('2d');
        if (portfolioChartInstance) portfolioChartInstance.destroy();
        portfolioChartInstance = new Chart(ctx, {
            type: 'doughnut',
            data: { labels: chartLabels, datasets: [{ data: chartData, backgroundColor: ['#3498db', '#e74c3c', '#f1c40f', '#2ecc71', '#9b59b6', '#e67e22', '#1abc9c', '#34495e'] }] },
            options: { 
                responsive: true, 
                plugins: { 
                    legend: { position: 'bottom', labels: { boxWidth: 12, color: legendColor } },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
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

    document.getElementById('portfolioSection').style.display = (hasHoldings || totalRealizedProfit !== 0) ? 'block' : 'none';
}

function displayEntries() {
    cloudEntries.sort((a, b) => {
        const timeA = a.rawDate ? new Date(a.rawDate).getTime() : a.id;
        const timeB = b.rawDate ? new Date(b.rawDate).getTime() : b.id;
        return timeB - timeA;
    });

    updatePortfolioSummary();
    renderCalendar();
    historyList.innerHTML = '';
    
    const filterValue = filterStockInput.value.trim().toLowerCase();
    const filteredEntries = cloudEntries.filter(entry => {
        if (filterValue && !(entry.stockName && entry.stockName.toLowerCase().includes(filterValue))) return false;
        
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
            const entryType = entry.type || 'trade';
            if (entryType !== currentFilterType) return false;
        }
        return true;
    });

    const banner = document.getElementById('activeFilterBanner');
    if (currentFilterDate) {
        banner.style.display = 'flex';
        let typeText = currentFilterType === 'trade' ? '매매 기록' : (currentFilterType === 'memo' ? '일반 메모' : '전체 기록');
        document.getElementById('activeFilterText').innerText = `📅 ${currentFilterDate} 일자의 ${typeText} 모아보기`;
    } else { banner.style.display = 'none'; }

    if (filteredEntries.length === 0) {
        historyList.innerHTML = '<p style="text-align:center; color:var(--text-muted-color); font-size: 16px; padding: 20px;">조건에 맞는 기록이 없습니다.</p>';
        return;
    }

    filteredEntries.forEach(entry => {
        const card = document.createElement('div');
        card.className = 'entry-card';
        const entryType = entry.type || 'trade';
        const imageHtml = entry.attachedImage ? `<div style="margin-top:10px;"><img src="${entry.attachedImage}" class="entry-thumbnail" onclick="openImageViewer(this.src, event)" title="클릭하여 원본 보기"></div>` : '';

        // ⭐️ 최초 작성 및 수정 시간 계산 (기존 데이터의 경우 id값을 통해 생성 시간 유추)
        const createdStr = entry.createdAt ? new Date(entry.createdAt).toLocaleString() : new Date(entry.id).toLocaleString();
        const updatedStr = entry.updatedAt ? new Date(entry.updatedAt).toLocaleString() : '';
        const timeDisplayHtml = `
            <div style="display: flex; flex-direction: column; gap: 3px;">
                <span style="color: var(--text-strong-color); font-weight: var(--fw-bold, bold);">🕒 기록 일시: ${entry.date}</span>
                <span style="font-size: 11px; color: var(--text-muted-color);">최초 작성: ${createdStr}${updatedStr && updatedStr !== createdStr ? ` | 최종 수정: ${updatedStr}` : ''}</span>
            </div>
        `;

        if (entryType === 'memo') {
            card.style.borderLeftColor = '#f39c12';
            const stockBadge = entry.stockName ? `<span class="cal-badge memo" style="padding:3px 8px; border-radius:12px; font-size:0.8em; margin-right:8px; display:inline-block;">🏷️ ${entry.stockName}</span>` : '';
            const brokerBadge = entry.brokerAccount ? `<span class="cal-badge trade" style="padding:3px 8px; border-radius:12px; font-size:0.8em; margin-right:8px; display:inline-block;">🏦 ${entry.brokerAccount}</span>` : '';
            card.innerHTML = `
            <div class="entry-header">
                ${timeDisplayHtml}
                <div class="header-right"><span>📝 일반 메모</span><button class="btn-edit">수정</button><button class="btn-delete">삭제</button></div>
            </div>
                <div class="entry-title">${stockBadge}${brokerBadge}${entry.title}</div>
                <div class="entry-content">${entry.thoughts}</div>
                ${imageHtml}
            `;
        } else {
            let typeColor = '#7f8c8d';
            if(entry.tradeType === '매수') typeColor = '#e74c3c';
            if(entry.tradeType === '매도') typeColor = '#3498db';

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
            const brokerBadge = entry.brokerAccount ? `<span style="font-size: 0.7em; color: var(--text-muted-color); font-weight: normal; margin-left: 8px;">🏦 ${entry.brokerAccount}</span>` : '';
            card.innerHTML = `
            <div class="entry-header">
                ${timeDisplayHtml}
                <div class="header-right"><span>💼 ${entry.accountName}</span><button class="btn-edit">수정</button><button class="btn-delete">삭제</button></div>
            </div>
                <div class="entry-title">${entry.stockName} ${brokerBadge} <span style="color: ${typeColor}; font-size: 0.8em;">[${entry.tradeType}]</span></div>
                ${detailsHtml}
                <div class="entry-content">${entry.thoughts}</div>
                ${imageHtml}
            `;
        }

        const editBtn = card.querySelector('.btn-edit');
        editBtn.addEventListener('click', () => editEntry(entry));

        const deleteBtn = card.querySelector('.btn-delete');
        deleteBtn.addEventListener('click', () => deleteEntry(entry.id));

        historyList.appendChild(card);
    });
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
    document.getElementById('thoughts').value = entry.thoughts || '';
    
    // ⭐️ 기존 수정 시 원래 시간을 불러오던 것을 무시하고 '현재 시간'으로 강제 세팅
    const localNow = new Date();
    localNow.setMinutes(localNow.getMinutes() - localNow.getTimezoneOffset());
    document.getElementById('tradeDate').value = localNow.toISOString().slice(0,16);

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
        if (!dailyStats[dateKey]) dailyStats[dateKey] = { count: 0, profit: 0, tradeCount: 0, memoCount: 0 };
        
        if (entry.type === 'trade' && entry.stockName) {
            dailyStats[dateKey].count++;
            dailyStats[dateKey].tradeCount++;
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
        } else if (entry.type === 'memo') {
            dailyStats[dateKey].count++;
            dailyStats[dateKey].memoCount++;
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
        const dStats = dailyStats[key] || { count: 0, profit: 0, tradeCount: 0, memoCount: 0 };
        
        let profitHtml = '';
        if (dStats.profit > 0) profitHtml = `<div style="color:#e74c3c; font-size:11px; font-weight:var(--fw-bold, bold); margin-bottom:2px;">+${Math.round(dStats.profit).toLocaleString()}</div>`;
        else if (dStats.profit < 0) profitHtml = `<div style="color:#3498db; font-size:11px; font-weight:var(--fw-bold, bold); margin-bottom:2px;">${Math.round(dStats.profit).toLocaleString()}</div>`;
        
        let tradeHtml = dStats.tradeCount > 0 ? `<div class="cal-badge trade" onclick="showDetailsForDate('${key}', 'trade', event)">매매 ${dStats.tradeCount}건</div>` : '';
        let memoHtml = dStats.memoCount > 0 ? `<div class="cal-badge memo" onclick="showDetailsForDate('${key}', 'memo', event)">메모 ${dStats.memoCount}건</div>` : '';
        
        calendarGrid.innerHTML += `<div class="calendar-day" onclick="showDetailsForDate('${key}', 'all', event)" title="클릭하여 상세 보기"><span style="font-size:12px; font-weight:var(--fw-bold, bold); color: var(--text-strong-color);">${d}</span><div style="text-align:right;">${profitHtml}${tradeHtml}${memoHtml}</div></div>`;
    }
}

window.showDetailsForDate = function(date, type, event) {
    if (event) event.stopPropagation();
    currentFilterDate = date;
    currentFilterType = type;
    
    document.getElementById('btnListView').click();
    displayEntries();
};

window.clearDateFilter = function() {
    currentFilterDate = null;
    currentFilterType = null;
    displayEntries();
};

document.getElementById('btnListView').addEventListener('click', function() {
    this.classList.add('active'); document.getElementById('btnCalendarView').classList.remove('active');
    document.getElementById('historyList').style.display = 'flex';
    document.getElementById('calendarViewSection').style.display = 'none';
});

document.getElementById('btnCalendarView').addEventListener('click', function() {
    this.classList.add('active'); document.getElementById('btnListView').classList.remove('active');
    document.getElementById('historyList').style.display = 'none';
    document.getElementById('calendarViewSection').style.display = 'block';
    renderCalendar();
});

document.getElementById('btnPrevMonth').addEventListener('click', () => { currentDate.setMonth(currentDate.getMonth() - 1); renderCalendar(); });
document.getElementById('btnNextMonth').addEventListener('click', () => { currentDate.setMonth(currentDate.getMonth() + 1); renderCalendar(); });