// ═══════════════════════════════════════════════════════════════════
// 09-form-fields.js — 폼 입력 동작 — 종목 자동입력, 금액 계산, 태그, 이미지 삽입
//
// ⚠️ 이 파일들은 ES 모듈이 아니라 **순서대로 로드되는 클래식 스크립트**다.
//    최상위 let/const/function 은 전역 렉시컬 환경을 공유하므로, 예전 script.js
//    한 덩어리였을 때와 실행 의미가 완전히 같다. (HTML 의 인라인 onclick 핸들러가
//    전역 함수를 그대로 부르고 있어 모듈로 바꾸면 그것들이 전부 깨진다)
//    → 로드 순서는 templates/stock-memo.html 의 <script> 순서가 결정한다. 바꾸지 말 것.
// ═══════════════════════════════════════════════════════════════════

function getStockOptions() {
    const historyStocks = cloudEntries.map(entry => entry.stockName).filter(Boolean);
    return [...new Set([...defaultStocks, ...historyStocks])].sort();
}
function getBrokerOptions() {
    const historyBrokers = cloudEntries.map(entry => entry.brokerAccount).filter(Boolean);
    return [...new Set([...defaultBrokers, ...historyBrokers])].sort();
}
function getSubAccountOptions() {
    const historySubAccounts = cloudEntries.map(entry => entry.subAccount).filter(Boolean);
    return [...new Set(historySubAccounts)].sort();
}

setupAutocomplete('stockName', 'stockNameList', getStockOptions);

// ⭐️ 종목명 입력 완료(자동완성 선택, 포커스 아웃, 엔터 입력) 시 관련 정보 자동 입력
function autoFillStockInfo(e) {
    const val = (e.type === 'itemSelected' && e.detail) ? e.detail.value : this.value.trim();
    if (!val) return;

    // ⭐️ 이미 숨김 처리된 종목이면 체크박스를 켜둔 상태로 시작한다.
    //    (기록을 하나 추가했다는 이유만으로 숨김이 풀리는 것을 방지)
    syncHiddenCheckbox(val);

    // 증권사, 투자분류 등의 정보가 있는 가장 최근의 매매(trade) 기록을 우선 탐색
    let recentEntry = cloudEntries.find(entry => entry.stockName === val && entry.type === 'trade');
    
    // 매매 기록이 없으면 일반 메모 기록이라도 탐색 (종목코드가 있을 수 있으므로)
    if (!recentEntry) {
        recentEntry = cloudEntries.find(entry => entry.stockName === val);
    }

    if (recentEntry) {
        if (recentEntry.stockCode) document.getElementById('stockCode').value = recentEntry.stockCode;
        if (recentEntry.brokerAccount) document.getElementById('brokerAccount').value = recentEntry.brokerAccount;
        if (recentEntry.subAccount) document.getElementById('subAccount').value = recentEntry.subAccount;
        if (recentEntry.accountName) document.getElementById('accountName').value = recentEntry.accountName;

        // ⭐️ 계좌는 화면상 '매매 계좌' 드롭다운으로 입력되므로 숨김 필드와 함께 선택값도 맞춰준다.
        syncJournalAccountSelect(recentEntry.subAccount);

        // ⭐️ 투자 분류도 직전 기록 값으로 자동 선택 (목록에 없는 이전 값은 건드리지 않음)
        const classSelect = document.getElementById('tradeClass');
        if (classSelect && recentEntry.tradeClass) {
            const hasOption = Array.from(classSelect.options).some(opt => opt.value === recentEntry.tradeClass);
            if (hasOption) classSelect.value = recentEntry.tradeClass;
        }
    }
}

const stockNameInput = document.getElementById('stockName');
stockNameInput.addEventListener('itemSelected', autoFillStockInfo);
stockNameInput.addEventListener('blur', autoFillStockInfo); // 포커스 잃을 때
stockNameInput.addEventListener('keydown', function(e) {
    // 엔터 키 입력 시 (한글 조합 중이 아닐 때)
    if (e.key === 'Enter' && !e.isComposing) {
        e.preventDefault(); // ⭐️ 엔터 키 입력 시 폼(Form)이 강제 제출되는 현상 방지
        autoFillStockInfo.call(this, e);
        
        // ⭐️ 엔터 입력 시 빈 칸 또는 다음 주요 입력칸으로 자동 포커스 이동
        const recordType = document.querySelector('input[name="recordType"]:checked');
        if (recordType && recordType.value === 'trade') {
            // 매매 일지: 비어있는 항목을 우선 찾고, 모두 채워졌으면 '매매 단가'로 직행
            if (!document.getElementById('stockCode').value) {
                document.getElementById('stockCode').focus();
            } else if (!document.getElementById('brokerAccount').value) {
                document.getElementById('brokerAccount').focus();
            } else if (!document.getElementById('subAccount').value) {
                document.getElementById('subAccount').focus();
            } else if (!document.getElementById('accountName').value) {
                document.getElementById('accountName').focus();
            } else {
                document.getElementById('price').focus();
            }
        } else {
            // 일반 메모: '메모 제목'으로 직행
            document.getElementById('memoTitle').focus();
        }
    }
});

function resetAndCloseForm() {
    formModalOverlay.classList.add('closing');
    setTimeout(() => {
        formModalOverlay.style.display = 'none';
        formModalOverlay.classList.remove('closing');
        document.body.style.overflow = ''; // ⭐️ 모달 닫힘 시 배경 스크롤 복구
        
        journalForm.reset();
        currentTags = [];
        renderTags();
        calcTotalAmount();
        
        if (window.quill) window.quill.setContents([]); // 에디터 초기화
        editingEntryId = null;
        submitBtn.innerText = "기록";
        const tradeRadio = document.querySelector('input[name="recordType"][value="trade"]');
        if(tradeRadio) { tradeRadio.checked = true; toggleFormUI('trade'); }
        const resetNow = new Date(); resetNow.setMinutes(resetNow.getMinutes() - resetNow.getTimezoneOffset());
        if (window.tradeDatePicker) {
            window.tradeDatePicker.setDate(resetNow.toISOString().slice(0,16));
        } else {
            document.getElementById('tradeDate').value = resetNow.toISOString().slice(0,16);
        }

        // ⭐️ 모달 닫힘 시 다음번을 위해 드래그 위치 초기화
        if (typeof window.resetFormDragPosition === 'function') window.resetFormDragPosition();
    }, 180); // CSS 페이드아웃 애니메이션 시간과 동기화
}

// ⭐️ Flatpickr 초기화 (날짜 및 시간 선택기)
window.tradeDatePicker = flatpickr("#tradeDate", {
    enableTime: true,
    dateFormat: "Y-m-d\\TH:i",
    locale: "ko",
    time_24hr: false,
    // ⭐️ 모바일 네이티브 스크롤 픽커 대신 커스텀 UI를 강제하여 조작 즉시 실시간 반영되도록 처리
    disableMobile: true,
    // ⭐️ 캘린더/시간 변경 시 입력창에 즉각적으로 반영
    onChange: function(selectedDates, dateStr, instance) {
        if (instance.input) instance.input.value = dateStr;
    },
    onValueUpdate: function(selectedDates, dateStr, instance) {
        if (instance.input) instance.input.value = dateStr;
    },
    // ⭐️ 월 또는 연도 변경 시 기존에 선택된 '일(Day)'과 '시간'을 유지하여 즉각 반영되도록 처리
    onMonthChange: function(selectedDates, dateStr, instance) {
        if (selectedDates.length > 0) {
            const cd = selectedDates[0];
            const maxDays = new Date(instance.currentYear, instance.currentMonth + 1, 0).getDate();
            const nd = new Date(instance.currentYear, instance.currentMonth, Math.min(cd.getDate(), maxDays), cd.getHours(), cd.getMinutes());
            instance.setDate(nd, true);
        }
    },
    onYearChange: function(selectedDates, dateStr, instance) {
        if (selectedDates.length > 0) {
            const cd = selectedDates[0];
            const maxDays = new Date(instance.currentYear, instance.currentMonth + 1, 0).getDate();
            const nd = new Date(instance.currentYear, instance.currentMonth, Math.min(cd.getDate(), maxDays), cd.getHours(), cd.getMinutes());
            instance.setDate(nd, true);
        }
    },
    onReady: function(selectedDates, dateStr, instance) {
        const nowBtn = document.createElement('button');
        nowBtn.type = 'button';
        nowBtn.textContent = '🕒';
        nowBtn.title = '현재 시간으로 설정';
        nowBtn.style.cssText = 'background: transparent; border: none; color: var(--primary-color); cursor: pointer; font-weight: bold; padding: 0 10px; width: auto; margin: 0; box-shadow: none; height: auto; outline: none; display: flex; align-items: center;';
        
        nowBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            const currentNow = new Date();
            currentNow.setMinutes(currentNow.getMinutes() - currentNow.getTimezoneOffset());
            instance.setDate(currentNow.toISOString().slice(0,16), true);
        });
        
        if (instance.timeContainer) {
            instance.timeContainer.appendChild(nowBtn);
        }
    }
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
    const tradeType = document.getElementById('tradeType').value;
    const price = Number(document.getElementById('price').value) || 0;
    const qtyInput = document.getElementById('quantity').value;
    let qty = Number(qtyInput) || 0;
    
    // ⭐️ 배당일 경우 수량이 없으면 1로 계산하여 단가 금액을 총액에 그대로 표시
    if (tradeType === '배당' && (!qtyInput || qty === 0)) {
        qty = 1;
    }

    if (price > 0 && qty > 0) {
        totalWrapper.style.display = 'block';
        const textLabel = tradeType === '배당' ? '총 배당 금액' : '총 매매 금액';
        document.getElementById('totalAmountDisplay').innerText = `${textLabel}: ${(price * qty).toLocaleString()}원`;
    } else { totalWrapper.style.display = 'none'; }
}
document.getElementById('price').addEventListener('input', calcTotalAmount);
document.getElementById('quantity').addEventListener('input', calcTotalAmount);

// ⭐️ 기록 유형(매매/메모)에 따른 폼 UI 전환 함수
function toggleFormUI(recordType) {
    const isTrade = recordType === 'trade';
    document.getElementById('tradeRow0').style.display = isTrade ? 'flex' : 'none';
    document.getElementById('tradeRow1').style.display = isTrade ? 'flex' : 'none';
    document.getElementById('tradeRow2').style.display = isTrade ? 'flex' : 'none';
    document.getElementById('memoTitleGroup').style.display = isTrade ? 'none' : 'block';
    const journalGroup = document.getElementById('journalAccountSelectGroup');
    if (journalGroup) journalGroup.style.display = isTrade ? 'block' : 'none';
    
    document.getElementById('stockName').required = isTrade;
    document.getElementById('stockCode').required = isTrade;
    
    const select = document.getElementById('journalAccountSelect');
    if (select) select.required = isTrade;
    const tradeClassEl = document.getElementById('tradeClass');
    if (tradeClassEl) tradeClassEl.required = isTrade;
    
    const tradeTypeEl = document.getElementById('tradeType');
    const tradeTypeValue = tradeTypeEl ? tradeTypeEl.value : '';
    const isTradeAndNotWatch = isTrade && tradeTypeEl && tradeTypeValue !== '주시' && tradeTypeValue !== '관망';
    const isDividend = tradeTypeValue === '배당';
    const priceEl = document.getElementById('price');
    if (priceEl) priceEl.required = isTradeAndNotWatch;
    const quantityEl = document.getElementById('quantity');
    if (quantityEl) {
        quantityEl.required = isTradeAndNotWatch && !isDividend; // ⭐️ 배당일 때는 수량이 필수값이 아니도록 처리
    }
    const memoTitleEl = document.getElementById('memoTitle');
    if (memoTitleEl) memoTitleEl.required = !isTrade;
    
    document.getElementById('thoughtsLabel').innerText = isTrade ? '생각의 흐름 / 계획' : '메모 내용';
    calcTotalAmount();
}

const typeRadios = document.querySelectorAll('input[name="recordType"]');
typeRadios.forEach(radio => {
    radio.addEventListener('change', function() {
        toggleFormUI(this.value);
    });
});

// ⭐️ 매매 포지션(tradeType) 변경 시 단가/수량 필수 여부 동적 업데이트
const tradeTypeSelect = document.getElementById('tradeType');
if (tradeTypeSelect) {
    tradeTypeSelect.addEventListener('change', function() {
        const recordType = document.querySelector('input[name="recordType"]:checked');
        if (recordType) toggleFormUI(recordType.value);
        calcTotalAmount(); // 배당/매매에 따른 텍스트 레이블 변경 반영
    });
}

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

// ⭐️ 에디터 내부에 이미지를 압축하여 삽입하는 함수
window.resizeAndInsertImageToQuill = function(file, customIndex) {
    // ⭐️ 캡처된 커서 위치(customIndex)가 없으면 현재 위치 사용
    let insertIndex = customIndex;
    if (insertIndex === undefined) {
        window.quill.focus();
        const range = window.quill.getSelection();
        insertIndex = range ? range.index : window.quill.getLength();
    }

    const reader = new FileReader();
    reader.onload = function(event) {
        const img = new Image();
        img.onload = function() {
            const canvas = document.createElement('canvas');
            const MAX_WIDTH = 1200; // 본문 삽입용 최대 해상도 제한
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
            
            // jpeg 포맷으로 85% 최적화 압축 (Base64)
            const dataUrl = canvas.toDataURL('image/jpeg', 0.85);
            
            window.quill.insertEmbed(insertIndex, 'image', dataUrl);
            window.quill.setSelection(insertIndex + 1);
            window.quill.focus(); // ⭐️ 포커스 명시적 유지
            
            // ⭐️ 이미지가 DOM에 렌더링된 후, 삽입된 커서 위치로 부드럽게 스크롤 보정
            setTimeout(() => {
                const selection = window.getSelection();
                if (selection && selection.rangeCount > 0) {
                    let targetNode = selection.focusNode;
                    if (targetNode && targetNode.nodeType === Node.TEXT_NODE) {
                        targetNode = targetNode.parentNode;
                    }
                    if (targetNode && targetNode.scrollIntoView) {
                        targetNode.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    }
                }
            }, 150);
        };
        img.src = event.target.result;
    };
    reader.readAsDataURL(file);
};

