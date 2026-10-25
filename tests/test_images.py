"""첨부 이미지 — 업로드·접근 권한·본문 내장 base64 추출.

대상: images.py 와 그 1회성 마이그레이션.
"""

from app.services.users import user_dir as account_storage_dir
import re
import time


import backend_app
import config
from app.services import images, migrations
from helpers import _user_id, _ensure_user


def test_image_upload_and_access(client):
    """
    Base64 이미지 업로드 처리와 사용자 간 격리된 첨부파일 접근 제어를 테스트합니다.
    """
    _ensure_user('imguser')  # 세션은 실제 계정이 있어야 통과한다
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'imguser'
        sess['user_id'] = _user_id(sess['username'])
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)
        
    # 1px 짜리 투명 PNG 더미 파일
    b64_image = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    
    res = client.post('/api/entry', json={"type": "buy", "stockName": "ImageTest", "price": 1,
                                           "quantity": 1, "attachedImage": b64_image})
    assert res.status_code == 200
    
    # 이미지 경로 추출
    img_url = client.get('/api/data').json[0]['attachedImage']
    filename = img_url.split('/')[-1]
    
    # 다른 유저 세션으로 타인의 파일 접근 시도 시 403 에러 발생 확인
    _ensure_user('otheruser')  # 세션은 실제 계정이 있어야 통과한다
    with client.session_transaction() as sess:
        sess['username'] = 'otheruser'
        sess['user_id'] = _user_id(sess['username'])
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)
    res_file_unauth = client.get(f"/uploads/imguser/{filename}")
    assert res_file_unauth.status_code == 403

def test_process_image_edge_cases():
    """process_image 함수의 예외(None 입력, URL 직접 입력) 케이스를 테스트합니다."""
    assert images.process_image('trader', None, 1) is None
    assert images.process_image('trader', "http://example.com/test.png", 1) == "http://example.com/test.png"

# ─────────────────────────────────────────────────────────────
# ⭐️ 본문(thoughts) 내장 base64 이미지 → 파일 추출 (초기 로딩 최적화)
# ─────────────────────────────────────────────────────────────
def test_extract_inline_images(app, monkeypatch, tmp_path):
    """base64 이미지가 파일로 저장되고 src 가 /uploads/ URL 로 치환된다."""
    import base64 as b64
    monkeypatch.setattr(config, 'UPLOAD_FOLDER', str(tmp_path))
    _ensure_user('tester')

    raw = b'\x89PNG-fake-image-bytes'
    encoded = b64.b64encode(raw).decode()
    entry = {'thoughts': f'<p>메모</p><img src="data:image/png;base64,{encoded}"><p>끝</p>'}

    out = images.extract_inline_images('tester', entry)

    assert 'data:image' not in out['thoughts']
    m = re.search(r'src="/uploads/tester/(qimg_\w+\.png)"', out['thoughts'])
    assert m, out['thoughts']
    from pathlib import Path
    saved = Path(account_storage_dir(config.UPLOAD_FOLDER, 'tester')) / m.group(1)
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
    _ensure_user('imgentry')  # 세션은 실제 계정이 있어야 통과한다
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'imgentry'
        sess['user_id'] = _user_id(sess['username'])
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
    _ensure_user('legacy')
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

    migrations.migrate_inline_images()

    conn = backend_app.get_db()
    rows = {r['id']: r['thoughts'] for r in conn.execute("SELECT id, thoughts FROM entries")}
    conn.close()
    assert 'data:image' not in rows[1]
    assert '/uploads/legacy/qimg_' in rows[1]
    assert rows[2] == '<p>이미지 없는 기록</p>'
    assert (backup_dir / 'journal_pre_imgmigration.db').exists()

    # 재실행 시 아무 것도 변경하지 않고 통과 (멱등성)
    migrations.migrate_inline_images()


def test_extract_inline_images_exception(monkeypatch, tmp_path):
    from unittest.mock import patch
    monkeypatch.setattr(config, 'UPLOAD_FOLDER', str(tmp_path))
    with patch('base64.b64decode', side_effect=Exception("Decode Error")):
        broken = {'thoughts': '<img src="data:image/png;base64,AAAA">'}
        out = images.extract_inline_images('u', broken)
        assert out['thoughts'] == broken['thoughts']


def test_process_image_base64(app, monkeypatch, tmp_path):
    from pathlib import Path
    _ensure_user('trader')
    monkeypatch.setattr(config, 'UPLOAD_FOLDER', str(tmp_path))
    
    import base64
    raw = b'fake-image'
    encoded = base64.b64encode(raw).decode()
    
    # Test png
    # 첨부 라우트(/uploads/<user>/<file>)가 서빙할 수 있도록 사용자 폴더에 저장한다.
    res = images.process_image('trader', f"data:image/png;base64,{encoded}", 99)
    assert res == "/uploads/trader/img_99.png"
    assert (Path(account_storage_dir(config.UPLOAD_FOLDER, 'trader')) / 'img_99.png').read_bytes() == raw

    # Test jpg
    res2 = images.process_image('trader', f"data:image/jpeg;base64,{encoded}", 100)
    assert res2 == "/uploads/trader/img_100.jpg"
    assert (Path(account_storage_dir(config.UPLOAD_FOLDER, 'trader')) / 'img_100.jpg').read_bytes() == raw

    # 경로 문자가 섞인 id 는 걸러져 사용자 폴더를 벗어나지 않는다.
    res3 = images.process_image('trader', f"data:image/png;base64,{encoded}", '../../x')
    assert res3 == "/uploads/trader/img_x.png"


# ─────────────────────────────────────────────────────────────
# ⭐️ 아무 기록도 쓰지 않게 된 첨부 파일 정리
# ─────────────────────────────────────────────────────────────
_PNG = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQ"
        "DwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def _upload_files(username):
    import os
    folder = account_storage_dir(config.UPLOAD_FOLDER, username)
    return sorted(os.listdir(folder)) if os.path.isdir(folder) else []


def _memo(thoughts, **kw):
    e = {"type": "memo", "title": "메모", "thoughts": thoughts}
    e.update(kw)
    return e


def _login_as(client, username):
    from helpers import _login
    _login(client, username)


def test_rejected_entry_leaves_no_image_file(client):
    """검증에서 거부된 저장(400)은 이미지 파일을 남기지 않는다."""
    from helpers import _sell
    _login_as(client, 'imgreject')
    res = client.post('/api/entry', json=_sell(thoughts=f'<img src="{_PNG}">'))
    assert res.status_code == 400
    assert _upload_files('imgreject') == []


def test_oversell_confirm_then_force_stores_image_once(client):
    """409 확인 후 force 로 다시 보내도 이미지는 한 벌만 남는다."""
    from helpers import _buy, _sell
    _login_as(client, 'imgforce')
    buy_id = client.post('/api/entry', json=_buy(qty=10)).json['id']
    client.post('/api/entry', json=_sell(qty=10))

    body = _buy(qty=1, thoughts=f'<img src="{_PNG}">')
    assert client.put(f'/api/entry/{buy_id}', json=body).status_code == 409
    assert _upload_files('imgforce') == []
    assert client.put(f'/api/entry/{buy_id}?force=1', json=body).status_code == 200
    assert len(_upload_files('imgforce')) == 1


def test_delete_entry_removes_its_image(client):
    _login_as(client, 'imgdel')
    entry_id = client.post('/api/entry', json=_memo(f'<img src="{_PNG}">')).json['id']
    assert len(_upload_files('imgdel')) == 1
    assert client.delete(f'/api/entry/{entry_id}').status_code == 200
    assert _upload_files('imgdel') == []


def test_shared_image_kept_until_last_reference_is_gone(client):
    """기록 복사처럼 같은 파일을 여러 기록이 쓰면, 마지막 참조가 사라질 때만 지운다."""
    _login_as(client, 'imgshare')
    first = client.post('/api/entry', json=_memo(f'<img src="{_PNG}">')).json['id']
    thoughts = next(e['thoughts'] for e in client.get('/api/data').json if e['id'] == first)
    second = client.post('/api/entry', json=_memo(thoughts)).json['id']
    assert len(_upload_files('imgshare')) == 1

    client.delete(f'/api/entry/{first}')
    assert len(_upload_files('imgshare')) == 1      # 두 번째 기록이 아직 쓴다
    client.delete(f'/api/entry/{second}')
    assert _upload_files('imgshare') == []


def test_update_removing_image_deletes_file(client):
    _login_as(client, 'imgedit')
    entry_id = client.post('/api/entry', json=_memo(f'<p>a</p><img src="{_PNG}">')).json['id']
    assert len(_upload_files('imgedit')) == 1
    res = client.put(f'/api/entry/{entry_id}', json=_memo('<p>이미지 뺌</p>'))
    assert res.status_code == 200
    assert _upload_files('imgedit') == []


def test_update_keeping_image_keeps_file(client):
    _login_as(client, 'imgkeep')
    entry_id = client.post('/api/entry', json=_memo(f'<img src="{_PNG}">')).json['id']
    thoughts = client.get('/api/data').json[0]['thoughts']
    client.put(f'/api/entry/{entry_id}', json=_memo(thoughts + '<p>추가</p>'))
    assert len(_upload_files('imgkeep')) == 1


def test_entry_uploads_only_counts_own_folder():
    entry = {'thoughts': '<img src="/uploads/me/a.png"><img src="http://h:1/uploads/me/b.jpg">'
                         '<img src="/uploads/other/c.png">',
             'attachedImage': '/uploads/me/legacy.png'}
    assert images.entry_uploads('me', entry) == {'a.png', 'b.jpg', 'legacy.png'}


def test_bot_delete_and_patch_release_unused_images(client):
    """봇 API 의 삭제·메모 정정도 같은 규칙으로 쓰지 않는 첨부를 정리한다."""
    import os
    from app.services import entries
    from helpers import _insert_raw

    _login_as(client, 'imgbot')
    folder = account_storage_dir(config.UPLOAD_FOLDER, 'imgbot')
    os.makedirs(folder, exist_ok=True)
    for name in ('a.png', 'b.png'):
        open(os.path.join(folder, name), 'wb').close()
    _insert_raw('imgbot', id=1, source='bot', brokerExecutionId='E1', tradeType='매수',
                quantity=1, thoughts='<img src="/uploads/imgbot/a.png">')
    _insert_raw('imgbot', id=2, source='bot', brokerExecutionId='E2', tradeType='매수',
                quantity=1, thoughts='<img src="/uploads/imgbot/b.png">')

    entries.patch_bot_trade('imgbot', 1, {'thoughts': '메모만 남김'})
    assert _upload_files('imgbot') == ['b.png']
    entries.delete_bot_trade('imgbot', 2)
    assert _upload_files('imgbot') == []
