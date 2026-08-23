// ═══════════════════════════════════════════════════════════════════
// 15-imageviewer.js — 첨부 이미지 뷰어 (확대·이동·핀치줌)
//
// ⚠️ 이 파일들은 ES 모듈이 아니라 **순서대로 로드되는 클래식 스크립트**다.
//    최상위 let/const/function 은 전역 렉시컬 환경을 공유하므로, 예전 script.js
//    한 덩어리였을 때와 실행 의미가 완전히 같다. (HTML 의 인라인 onclick 핸들러가
//    전역 함수를 그대로 부르고 있어 모듈로 바꾸면 그것들이 전부 깨진다)
//    → 로드 순서는 templates/stock-memo.html 의 <script> 순서가 결정한다. 바꾸지 말 것.
// ═══════════════════════════════════════════════════════════════════

let imageZoom = 1;
let imagePanX = 0;
let imagePanY = 0;
let imageIsDragging = false;
let imageIsPinching = false; // ⭐️ 핀치 줌 상태 변수 추가
let imageStartX = 0;
let imageStartY = 0;
let initialPinchDistance = null;
let initialPinchZoom = 1;

function updateImageViewerTransform() {
    const wrapper = document.getElementById('imageViewerWrapper');
    const fullSizeImage = document.getElementById('fullSizeImage');
    const btnClose = document.getElementById('btnImageViewerClose');
    if (!wrapper || !fullSizeImage) return;
    
    // ⭐️ 드래그 중이거나 핀치 줌 중일 때는 transition을 제거하여 즉각(버벅임 없이) 반응하도록 조정
    if (imageIsDragging || imageIsPinching) {
        wrapper.style.transition = 'none';
        if (btnClose) btnClose.style.transition = 'background 0.2s';
    } else {
        wrapper.style.transition = 'transform 0.1s ease-out';
        if (btnClose) btnClose.style.transition = 'background 0.2s, transform 0.1s ease-out';
    }
    
    wrapper.style.transform = `translate(${imagePanX}px, ${imagePanY}px) scale(${imageZoom})`;
    if (btnClose) {
        btnClose.style.transform = `scale(${1 / imageZoom})`;
    }

    if (imageZoom > 1) {
        fullSizeImage.style.cursor = imageIsDragging ? 'grabbing' : 'zoom-out';
    } else {
        fullSizeImage.style.cursor = 'zoom-in';
    }
}

window.openImageViewer = function(src, event) {
    if (event) event.stopPropagation();
    const modal = document.getElementById('imageViewerModal');
    document.getElementById('fullSizeImage').src = src;
    modal.style.display = 'flex';
    document.body.style.overflow = 'hidden'; // ⭐️ 모달 열림 시 배경 스크롤 방지
    
    // ⭐️ 이미지 확대/팬 상태 초기화
    imageZoom = 1;
    imagePanX = 0;
    imagePanY = 0;
    imageIsDragging = false;
    imageIsPinching = false;
    updateImageViewerTransform();
};

window.closeImageViewer = function() {
    const modal = document.getElementById('imageViewerModal');
    if (modal.classList.contains('closing')) return; // ⭐️ 중복 실행 방지
    modal.classList.add('closing');
    setTimeout(() => { 
        modal.style.display = 'none'; 
        modal.classList.remove('closing'); 
        document.body.style.overflow = ''; 
    }, 180);
};

