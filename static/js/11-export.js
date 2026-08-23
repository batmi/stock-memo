// ═══════════════════════════════════════════════════════════════════
// 11-export.js — 엑셀 내보내기·금액 가리기·숫자 애니메이션
//
// ⚠️ 이 파일들은 ES 모듈이 아니라 **순서대로 로드되는 클래식 스크립트**다.
//    최상위 let/const/function 은 전역 렉시컬 환경을 공유하므로, 예전 script.js
//    한 덩어리였을 때와 실행 의미가 완전히 같다. (HTML 의 인라인 onclick 핸들러가
//    전역 함수를 그대로 부르고 있어 모듈로 바꾸면 그것들이 전부 깨진다)
//    → 로드 순서는 templates/stock-memo.html 의 <script> 순서가 결정한다. 바꾸지 말 것.
// ═══════════════════════════════════════════════════════════════════

function ensureExcelLoaded() {
    return new Promise((resolve, reject) => {
        if (window.ExcelJS) { resolve(); return; }
        const tag = document.createElement('script');
        tag.src = 'https://cdnjs.cloudflare.com/ajax/libs/exceljs/4.3.0/exceljs.min.js';
        tag.onload = () => window.ExcelJS ? resolve() : reject(new Error('엑셀 모듈이 초기화되지 않았습니다.'));
        tag.onerror = () => reject(new Error('엑셀 모듈을 불러오지 못했습니다. (네트워크 연결을 확인해주세요)'));
        document.head.appendChild(tag);
    });
}

document.getElementById('btnModalExportExcel').addEventListener('click', async () => {
    if (await customConfirm('모든 매매 기록을 엑셀 파일(.xlsx)로 \n다운로드하시겠습니까?')) {
        window.showLoadingOverlay('엑셀 파일을 생성 중입니다...\n잠시만 기다려주세요.');

        // ⭐️ UI 스레드가 블록되기 전에 로딩 애니메이션이 화면에 렌더링될 수 있도록 약간의 지연(setTimeout)을 줌
        setTimeout(async () => {
            try {
                await ensureExcelLoaded();
                
                const workbook = new ExcelJS.Workbook();
                const worksheet = workbook.addWorksheet('매매일지', {
                    views: [{ state: 'frozen', xSplit: 0, ySplit: 1, topLeftCell: 'A2' }]
                });
                
                const header = ['작성일', '분류', '종목명', '증권사', '증권계좌', '계좌분류', '매매종류', '단가', '수량', '태그', '메모/생각'];
                worksheet.addRow(header);
                
                // 1행 틀고정 및 헤더 스타일/필터 적용
                worksheet.autoFilter = 'A1:K1';
                const headerRow = worksheet.getRow(1);
                headerRow.font = { bold: true };
                headerRow.fill = {
                    type: 'pattern',
                    pattern: 'solid',
                    fgColor: { argb: 'FFEAEAEA' }
                };
                
                // 데이터 추가
                cloudEntries.forEach(e => {
                    const rowData = [
                        e.date, (e.type || '').toUpperCase(), e.stockName||'', getMappedBroker(e.brokerAccount)||'', e.subAccount||'', e.accountName||'',
                        e.tradeType||'', Number(e.price)||0, Number(e.quantity)||0, 
                        e.tags||'', (e.thoughts||'').replace(/<[^>]*>?/gm, '').replace(/&nbsp;/g, ' ')
                    ];
                    const addedRow = worksheet.addRow(rowData);
                    
                    // 단가, 수량 컬럼 숫자 포맷 지정
                    addedRow.getCell(8).numFmt = '#,##0'; // H열 (단가)
                    addedRow.getCell(9).numFmt = '#,##0'; // I열 (수량)
                });
                
                // 내용에 맞게 열 너비 자동 조절
                worksheet.columns.forEach((column, colIdx) => {
                    let maxLen = header[colIdx].length * 2;
                    column.eachCell({ includeEmpty: true }, (cell, rowNumber) => {
                        if (rowNumber > 1) {
                            const val = cell.value != null ? cell.value.toString() : '';
                            let len = 0;
                            for (let i = 0; i < val.length; i++) len += val.charCodeAt(i) > 255 ? 2 : 1.1;
                            if (len > maxLen) maxLen = len;
                        }
                    });
                    column.width = Math.min(Math.max(Math.ceil(maxLen), 10), 100);
                });
                
                const now = new Date();
                const yyyy = now.getFullYear();
                const mm = String(now.getMonth() + 1).padStart(2, '0');
                const dd = String(now.getDate()).padStart(2, '0');
                const hh = String(now.getHours()).padStart(2, '0');
                const min = String(now.getMinutes()).padStart(2, '0');
                const ss = String(now.getSeconds()).padStart(2, '0');
                const filename = `TradingJournal_export_${yyyy}${mm}${dd}_${hh}${min}${ss}.xlsx`;
                
                // 다운로드 처리
                const buffer = await workbook.xlsx.writeBuffer();
                const blob = new Blob([buffer], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
                const link = document.createElement('a');
                link.href = window.URL.createObjectURL(blob);
                link.download = filename;
                document.body.appendChild(link);
                link.click();
                document.body.removeChild(link);
                window.URL.revokeObjectURL(link.href);
            } catch (err) {
                console.error("엑셀 내보내기 실패:", err);
                await customAlert(`엑셀 내보내기에 실패했습니다.\n(${err && err.message ? err.message : '알 수 없는 오류'})`);
            } finally {
                await window.hideLoadingOverlay();
            }
        }, 100);
    }
});

// ⭐️ 금액 가리기 모드 반영.
//    실제 마스킹은 CSS(.amount-masked .masked-amount)가 하므로 값을 다시
//    그릴 필요가 없다 — 갱신 애니메이션이나 현재가 폴링과 부딪히지 않는다.
//    다만 도넛 툴팁만은 canvas 라 CSS 가 닿지 않아 따로 처리한다(아래 label 콜백).
//    값 자체는 DOM 에 그대로 남으므로 보안 기능이 아니라 시선 차단용이다.
function syncAmountMaskButton() {
    const btn = document.getElementById('btnToggleAmountMask');
    if (!btn) return;
    btn.innerText = isAmountMasked ? '금액 보이기' : '금액 가리기';
    btn.style.backgroundColor = isAmountMasked ? 'var(--primary-color)' : 'transparent';
    btn.style.color = isAmountMasked ? '#fff' : 'var(--primary-color)';
}

function setAmountMasked(masked) {
    isAmountMasked = !!masked;
    document.documentElement.classList.toggle('amount-masked', isAmountMasked);
    // 기기별 화면 설정이라 서버 설정이 아닌 localStorage 에 저장한다(테마와 동일).
    try {
        localStorage.setItem('amountMasked', isAmountMasked ? '1' : '0');
    } catch (e) { /* 시크릿 모드 등 저장이 막혀도 이번 세션 동작은 유지한다 */ }
    syncAmountMaskButton();
}

// ⭐️ 숫자 카운트업 애니메이션 함수 (차트 중앙 텍스트용)
function animateValue(element, endValue, duration, isProfit = false) {
    let startValue = parseInt(element.getAttribute('data-val')) || 0;
    if (startValue === endValue) {
        const prefix = isProfit && endValue > 0 ? '+' : '';
        element.innerText = prefix + endValue.toLocaleString() + '원';
        return;
    }
    if (element.dataset.animId) cancelAnimationFrame(element.dataset.animId);

    let startTime = null;
    const step = (timestamp) => {
        if (!startTime) startTime = timestamp;
        const progress = Math.min((timestamp - startTime) / duration, 1);
        // easeOutExpo (부드럽게 감속)
        const ease = progress === 1 ? 1 : 1 - Math.pow(2, -10 * progress);
        const current = Math.floor(startValue + (endValue - startValue) * ease);
        
        const prefix = isProfit && current > 0 ? '+' : '';
        element.innerText = prefix + current.toLocaleString() + '원';
        
        if (progress < 1) {
            element.dataset.animId = requestAnimationFrame(step);
        } else {
            const finalPrefix = isProfit && endValue > 0 ? '+' : '';
            element.innerText = finalPrefix + endValue.toLocaleString() + '원';
            element.setAttribute('data-val', endValue);
        }
    };
    element.dataset.animId = requestAnimationFrame(step);
}

// ⭐️ 정규장 오픈 시간(한국 및 미국)인지 확인하는 함수 (자동 갱신 타이머용)
// ⭐️ KRX 휴장일 집합. 서버(prices.KRX_HOLIDAYS)에서 한 번 받아와 캐시한다.
//    비어 있으면(미수신/실패) 기존과 동일하게 평일 여부만으로 판정한다.
