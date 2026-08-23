"""첨부 이미지 — 업로드·접근 권한·본문 내장 base64 추출.

대상: images.py 와 그 1회성 마이그레이션.
"""

import json
import re
import time


import backend_app
import config
import images


def test_image_upload_and_access(client):
    """
    Base64 이미지 업로드 처리와 사용자 간 격리된 첨부파일 접근 제어를 테스트합니다.
    """
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'imguser'
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)
        
    # 1px 짜리 투명 PNG 더미 파일
    b64_image = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    
    res = client.post('/api/entry', json={"type": "buy", "stockName": "ImageTest", "attachedImage": b64_image})
    assert res.status_code == 200
    
    # 이미지 경로 추출
    img_url = client.get('/api/data').json[0]['attachedImage']
    filename = img_url.split('/')[-1]
    
    # 다른 유저 세션으로 타인의 파일 접근 시도 시 403 에러 발생 확인
    with client.session_transaction() as sess:
        sess['username'] = 'otheruser'
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)
    res_file_unauth = client.get(f"/uploads/imguser/{filename}")
    assert res_file_unauth.status_code == 403

def test_process_image_edge_cases():
    """process_image 함수의 예외(None 입력, URL 직접 입력) 케이스를 테스트합니다."""
    assert images.process_image(None, 1) is None
    assert images.process_image("http://example.com/test.png", 1) == "http://example.com/test.png"

# ─────────────────────────────────────────────────────────────
# ⭐️ 본문(thoughts) 내장 base64 이미지 → 파일 추출 (초기 로딩 최적화)
# ─────────────────────────────────────────────────────────────
def test_extract_inline_images(monkeypatch, tmp_path):
    """base64 이미지가 파일로 저장되고 src 가 /uploads/ URL 로 치환된다."""
    import base64 as b64
    import re
    monkeypatch.setattr(config, 'UPLOAD_FOLDER', str(tmp_path))

    raw = b'\x89PNG-fake-image-bytes'
    encoded = b64.b64encode(raw).decode()
    entry = {'thoughts': f'<p>메모</p><img src="data:image/png;base64,{encoded}"><p>끝</p>'}

    out = images.extract_inline_images('tester', entry)

    assert 'data:image' not in out['thoughts']
    m = re.search(r'src="/uploads/tester/(qimg_\w+\.png)"', out['thoughts'])
    assert m, out['thoughts']
    saved = tmp_path / 'tester' / m.group(1)
    assert saved.read_bytes() == raw
    # 원본 dict 는 변형하지 않는다 (복사본 반환)
    assert 'data:image' in entry['thoughts']

def test_extract_inline_images_edge_cases(monkeypatch, tmp_path):
    """이미지 없음/사용자 없음/손상된 base64 는 원본을 그대로 보존한다."""
    monkeypatch.setattr(config, 'UPLOAD_FOLDER', str(tmp_path))

    no_img = {'thoughts': '<p>이미지 없음</p>'}
    assert images.extract_inline_images('u', no_img) is no_img

    assert images.extract_inline_images('u', {'thoughts': None}) == {'thoughts': None}
    with_img = {'thoughts': '<img src="data:image/png;base64,AAAA">'}
    assert images.extract_inline_images('', with_img) is with_img

    # 패딩이 깨진 base64 → 디코딩 실패 시 원본 유지
    broken = {'thoughts': '<img src="data:image/png;base64,A">'}
    out = images.extract_inline_images('u', broken)
    assert out['thoughts'] == broken['thoughts']

def test_create_entry_extracts_inline_images(client, monkeypatch, tmp_path):
    """POST /api/entry 로 저장된 본문의 base64 이미지가 URL 로 치환되어 조회된다."""
    import base64 as b64
    monkeypatch.setattr(config, 'UPLOAD_FOLDER', str(tmp_path))
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'imgentry'
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)

    encoded = b64.b64encode(b'img-bytes').decode()
    res = client.post('/api/entry', json={
        "type": "memo", "title": "이미지 메모", "id": 1,
        "thoughts": f'<p>본문</p><img src="data:image/jpeg;base64,{encoded}">',
    })
    assert res.status_code == 200

    data = client.get('/api/data').json
    assert len(data) == 1
    assert 'data:image' not in data[0]['thoughts']
    assert '/uploads/imgentry/qimg_' in data[0]['thoughts']

def test_migrate_inline_images(app, monkeypatch, tmp_path):
    """기존 DB 의 base64 본문이 일괄 추출되고, 사전 DB 백업본이 생성된다."""
    import base64 as b64
    monkeypatch.setattr(config, 'UPLOAD_FOLDER', str(tmp_path / 'up'))
    backup_dir = tmp_path / 'bak'
    backup_dir.mkdir()
    monkeypatch.setattr(config, 'BACKUP_DIR', str(backup_dir))

    encoded = b64.b64encode(b'legacy-image').decode()
    conn = backend_app.get_db()
    conn.execute(
        "INSERT INTO entries (id, username, type, thoughts) VALUES (?, ?, ?, ?)",
        (1, 'legacy', 'memo', f'<img src="data:image/png;base64,{encoded}">'))
    conn.execute(
        "INSERT INTO entries (id, username, type, thoughts) VALUES (?, ?, ?, ?)",
        (2, 'legacy', 'memo', '<p>이미지 없는 기록</p>'))
    conn.commit()
    conn.close()

    backend_app.migrate_inline_images()

    conn = backend_app.get_db()
    rows = {r['id']: r['thoughts'] for r in conn.execute("SELECT id, thoughts FROM entries")}
    conn.close()
    assert 'data:image' not in rows[1]
    assert '/uploads/legacy/qimg_' in rows[1]
    assert rows[2] == '<p>이미지 없는 기록</p>'
    assert (backup_dir / 'journal_pre_imgmigration.db').exists()

    # 재실행 시 아무 것도 변경하지 않고 통과 (멱등성)
    backend_app.migrate_inline_images()
