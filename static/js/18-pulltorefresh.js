// ═══════════════════════════════════════════════════════════════════
// 18-pulltorefresh.js — 모바일 당겨서 새로고침
//
// ⚠️ 이 파일들은 ES 모듈이 아니라 **순서대로 로드되는 클래식 스크립트**다.
//    최상위 let/const/function 은 전역 렉시컬 환경을 공유하므로, 예전 script.js
//    한 덩어리였을 때와 실행 의미가 완전히 같다. (HTML 의 인라인 onclick 핸들러가
//    전역 함수를 그대로 부르고 있어 모듈로 바꾸면 그것들이 전부 깨진다)
//    → 로드 순서는 templates/stock-memo.html 의 <script> 순서가 결정한다. 바꾸지 말 것.
// ═══════════════════════════════════════════════════════════════════

let ptrStartY = 0;
let ptrCurrentY = 0;
let isPulling = false;
const ptrThreshold = 150; // ⭐️ 당겨야 하는 기준 픽셀 (기존 80에서 증가시켜 민감도 대폭 낮춤)

window.addEventListener('touchstart', (e) => {
    // ⭐️ 모달(입력창 팝업 등)이 열려있을 때는 당겨서 새로고침 동작 방지
    if (document.body.style.overflow === 'hidden') return;

    if (window.scrollY <= 0) {
        ptrStartY = e.touches[0].clientY;
        ptrCurrentY = ptrStartY;
        isPulling = true;
        const ptrIndicator = document.getElementById('ptrIndicator');
        if (ptrIndicator) ptrIndicator.style.transition = 'none';
    }
}, { passive: true });

window.addEventListener('touchmove', (e) => {
    if (!isPulling) return;
    ptrCurrentY = e.touches[0].clientY;
    const distance = ptrCurrentY - ptrStartY;

    // 화면 맨 위에서 아래로 당길 때만 작동
    if (distance > 0 && window.scrollY <= 0) {
        const ptrIndicator = document.getElementById('ptrIndicator');
        const ptrSpinner = document.getElementById('ptrSpinner');
        const ptrText = document.getElementById('ptrText');
        if (ptrIndicator && ptrSpinner && ptrText) {
            ptrIndicator.style.opacity = Math.min(distance / 60, 1).toString();
            // 화면에 더 묵직하게 당겨지도록 distance / 2.5 로 계산
            ptrIndicator.style.top = `${Math.min((distance / 2.5) - 50, 0)}px`;
            ptrSpinner.style.transform = `rotate(${distance * 1.5}deg)`;
            ptrText.innerText = distance > ptrThreshold ? '손을 놓아서 새로고침' : '아래로 당겨서 새로고침';
        }
    }
}, { passive: true });

window.addEventListener('touchend', () => {
    if (!isPulling) return;
    isPulling = false;
    const distance = ptrCurrentY - ptrStartY;
    const ptrIndicator = document.getElementById('ptrIndicator');
    const ptrSpinner = document.getElementById('ptrSpinner');
    const ptrText = document.getElementById('ptrText');

    if (ptrIndicator && ptrSpinner && ptrText) {
        ptrIndicator.style.transition = 'top 0.3s ease, opacity 0.3s ease';
        if (distance > ptrThreshold && window.scrollY <= 0) {
            ptrIndicator.style.top = '0px';
            ptrText.innerText = '화면을 새로고침합니다...';
            ptrSpinner.classList.add('spinning');
            setTimeout(() => { window.location.reload(); }, 400);
        } else {
            ptrIndicator.style.top = '-50px';
            ptrIndicator.style.opacity = '0';
            setTimeout(() => {
                ptrSpinner.style.transform = 'rotate(0deg)';
                ptrSpinner.classList.remove('spinning');
            }, 300);
        }
    }
});

// ⭐️ 비밀번호 변경 모달 이벤트 연결
