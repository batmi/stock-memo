"""백업 ZIP 생성·검증·복원.

대상: backup_api.py, backups.py.
"""

import io
import json
import os
import time
import zipfile


import backend_app
import backups
import config
import entry_logic
from .helpers import _buy, _ensure_user, _login, _sell


def test_backup_endpoint_requires_auth(client):
    """
    README에 명시된 '완벽 백업' 기능 엔드포인트 접근 시, 
    인증(로그인)되지 않은 상태라면 정상 처리(200)되지 않고 
    리다이렉트(302) 또는 에러(401/403)가 발생하는지 확인합니다.
    (※ 백업 엔드포인트 URL이 '/api/backup'이라고 가정한 예시입니다.)
    """
    response = client.get('/api/backup')
    assert response.status_code != 200

def test_backup_and_restore_workflow(client):
    """
    백업(GET /api/backup)으로 ZIP 파일을 다운로드하고,
    해당 ZIP 파일을 다시 복구(POST /api/restore)하여 
    정상적으로 데이터가 복원되는지 확인하는 통합 테스트입니다.
    """
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'testuser'
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)

    # 1. 백업할 원본 테스트 데이터 생성
    client.post('/api/entry', json={
        "type": "buy", "stockName": "애플", "price": 150000, "quantity": 5
    })

    # 2. 백업 파일 다운로드 (GET /api/backup)
    backup_res = client.get('/api/backup')
    assert backup_res.status_code == 200
    assert backup_res.mimetype == 'application/zip'

    # 다운로드된 ZIP 파일 데이터를 메모리에 로드하여 검증
    zip_data = io.BytesIO(backup_res.data)
    with zipfile.ZipFile(zip_data, 'r') as zf:
        assert 'data.json' in zf.namelist() # ZIP 내부 파일 목록에 data.json이 있어야 함
        with zf.open('data.json') as f:
            json_data = json.loads(f.read().decode('utf-8'))
            assert len(json_data) >= 1
            assert any(item.get('stockName') == '애플' for item in json_data)

    # 3. 새로운 데이터로 복구용 가상 ZIP 파일 생성 (기존 '애플' 대신 '테슬라'만 존재하도록 조작)
    restore_zip_buffer = io.BytesIO()
    with zipfile.ZipFile(restore_zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        fake_entries = [{"id": 999, "username": "testuser", "type": "buy", "stockName": "테슬라", "price": 300000, "quantity": 2}]
        zf.writestr('data.json', json.dumps(fake_entries, ensure_ascii=False))
        zf.writestr('uploads/dummy.txt', 'This is a test file for mocking image uploads')
    restore_zip_buffer.seek(0)

    # 4. 복구 API 호출 (POST /api/restore, multipart/form-data 파일 업로드 모사)
    restore_res = client.post('/api/restore', data={'file': (restore_zip_buffer, 'backup.zip')}, content_type='multipart/form-data')
    assert restore_res.status_code == 200
    assert restore_res.json.get('status') == 'success'

    # 5. 복구된 데이터 검증 (기존 '애플'이 삭제되고 복원한 '테슬라'만 조회되어야 함)
    get_res = client.get('/api/data')
    assert get_res.status_code == 200
    restored_data = get_res.json
    assert len(restored_data) == 1
    assert restored_data[0]['stockName'] == '테슬라'

def test_restore_exceptions(client):
    """
    백업 복원 시 발생할 수 있는 에러 상황(파일 누락, 잘못된 형식 등)을 테스트합니다.
    """
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'testuser'
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)
        
    # 1. 파일이 없을 때
    res = client.post('/api/restore')
    assert res.status_code == 400
    
    # 2. 확장자가 zip이 아닐 때
    data = {'file': (io.BytesIO(b"dummy"), 'test.txt')}
    res = client.post('/api/restore', data=data, content_type='multipart/form-data')
    assert res.status_code == 400
    
    # 3. data.json이 없는 잘못된 zip 파일일 때
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('wrong.txt', 'hello')
    buf.seek(0)
    data = {'file': (buf, 'test.zip')}
    res = client.post('/api/restore', data=data, content_type='multipart/form-data')
    assert res.status_code == 400

    # 4. data.json 이 기록 목록(list) 형식이 아닐 때 — 500 이 아니라 원인을 알려준다
    for bad in (b'{"entries": []}', b'"just a string"', b'[1, 2, 3]'):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('data.json', bad)
        buf.seek(0)
        data = {'file': (buf, 'test.zip')}
        res = client.post('/api/restore', data=data, content_type='multipart/form-data')
        assert res.status_code == 400, f"{bad!r} -> {res.status_code}"
        assert '기록 목록' in res.get_json()['error']

def test_restore_rejects_oversized_uncompressed_zip(client, monkeypatch):
    """압축 해제 후 크기가 상한을 넘으면 디스크를 채우기 전에 막는다.

    업로드 크기 제한(MAX_CONTENT_LENGTH)만으로는 막을 수 없다 — ZIP 은 압축률이
    높아 작은 업로드가 해제 시 수백 MB 가 될 수 있다.
    """
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'testuser'
        sess['expires_at'] = time.time() + 3600

    # 상한을 낮춰, 압축이 잘 되는 큰 데이터를 작은 업로드로 재현한다.
    monkeypatch.setattr(config, 'MAX_RESTORE_UNCOMPRESSED_BYTES', 1024 * 1024)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('data.json', b'0' * (4 * 1024 * 1024))  # 4MB -> 압축하면 수 KB
    buf.seek(0)
    assert buf.getbuffer().nbytes < 512 * 1024  # 업로드 자체는 작다

    data = {'file': (buf, 'big.zip')}
    res = client.post('/api/restore', data=data, content_type='multipart/form-data')
    assert res.status_code == 413
    assert '압축 해제 크기' in res.get_json()['error']

# ─────────────────────────────────────────────────────────────
# ⭐️ 백업 무결성 검증 / 복원 라운드트립 테스트
# ─────────────────────────────────────────────────────────────
def test_verify_backup_zip(tmp_path):
    """verify_backup_zip이 정상/손상/레코드 불일치를 올바르게 판별한다."""
    good = tmp_path / "good.zip"
    with zipfile.ZipFile(good, 'w') as zf:
        zf.writestr('data.json', json.dumps([{"id": 1}, {"id": 2}]))
    ok, _ = backups.verify_backup_zip(str(good), 2)
    assert ok is True

    # 레코드 수 불일치
    ok, msg = backups.verify_backup_zip(str(good), 5)
    assert ok is False and '불일치' in msg

    # data.json 누락
    nojson = tmp_path / "nojson.zip"
    with zipfile.ZipFile(nojson, 'w') as zf:
        zf.writestr('other.txt', 'hello')
    ok, msg = backups.verify_backup_zip(str(nojson), 0)
    assert ok is False

def test_backup_restore_round_trip(client):
    """백업 → 복원 후 데이터가 동일하게 보존되는지(라운드트립) 검증한다."""
    _login(client, 'roundtrip')
    client.post('/api/entry', json=_buy(stock='삼성전자', qty=10, id=1001))
    client.post('/api/entry', json=_sell(stock='삼성전자', qty=4, id=1002))

    before = client.get('/api/data').json

    # 1. 백업 다운로드
    backup_res = client.get('/api/backup')
    assert backup_res.status_code == 200
    backup_bytes = backup_res.data

    # 2. 데이터 전부 삭제
    for e in before:
        client.delete(f"/api/entry/{e['id']}")
    assert client.get('/api/data').json == []

    # 3. 백업 파일로 복원
    data = {'file': (io.BytesIO(backup_bytes), 'backup.zip')}
    restore_res = client.post('/api/restore', data=data, content_type='multipart/form-data')
    assert restore_res.status_code == 200
    assert restore_res.json.get('status') == 'success'

    # 4. 복원된 데이터가 원본과 동일한지 확인
    after = client.get('/api/data').json
    assert len(after) == len(before)
    assert {e['id'] for e in after} == {e['id'] for e in before}
    by_id = {e['id']: e for e in after}
    for e in before:
        assert by_id[e['id']]['stockName'] == e['stockName']
        assert by_id[e['id']]['quantity'] == e['quantity']

# ── 복원 중 실패해도 기존 첨부파일이 사라지지 않는지 ──────────────
def test_restore_keeps_existing_uploads_when_copy_fails(client, monkeypatch, tmp_path):
    """복사가 도중에 실패해도 원본 첨부파일 폴더는 그대로 남아야 한다.

    예전에는 기존 폴더를 먼저 rmtree 하고 복사해서, 복사가 실패하면 첨부파일이
    영구 소실되고 되돌릴 방법이 없었다.
    """
    monkeypatch.setattr(config, 'UPLOAD_FOLDER', str(tmp_path / 'uploads'))
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'restoreuser'
        sess['expires_at'] = time.time() + 3600

    # 기존 첨부파일을 심어 둔다
    user_folder = os.path.join(config.UPLOAD_FOLDER, 'restoreuser')
    os.makedirs(user_folder, exist_ok=True)
    keep = os.path.join(user_folder, 'important.png')
    with open(keep, 'w') as f:
        f.write('원본 이미지')

    # 백업 ZIP (첨부파일 1개 포함)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('data.json', json.dumps([]))
        zf.writestr('uploads/new.png', 'new')
    buf.seek(0)

    # 복사 단계에서 디스크가 찬 상황을 흉내낸다
    def boom(*a, **k):
        raise OSError('No space left on device')
    monkeypatch.setattr(backend_app.shutil, 'copy2', boom)

    res = client.post('/api/restore', data={'file': (buf, 'b.zip')},
                      content_type='multipart/form-data')
    assert res.status_code == 500

    # 핵심: 원본이 살아 있어야 한다
    assert os.path.exists(keep), '복원 실패로 기존 첨부파일이 사라졌다'
    with open(keep) as f:
        assert f.read() == '원본 이미지'
    # 작업용 폴더는 남지 않는다
    assert not os.path.exists(user_folder + '.restoring')

def test_restore_replaces_uploads_on_success(client, monkeypatch, tmp_path):
    """정상 복원 시에는 첨부파일 폴더가 백업 내용으로 교체된다."""
    monkeypatch.setattr(config, 'UPLOAD_FOLDER', str(tmp_path / 'uploads'))
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'restoreuser2'
        sess['expires_at'] = time.time() + 3600

    user_folder = os.path.join(config.UPLOAD_FOLDER, 'restoreuser2')
    os.makedirs(user_folder, exist_ok=True)
    with open(os.path.join(user_folder, 'old.png'), 'w') as f:
        f.write('old')

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('data.json', json.dumps([]))
        zf.writestr('uploads/new.png', 'new')
    buf.seek(0)

    res = client.post('/api/restore', data={'file': (buf, 'b.zip')},
                      content_type='multipart/form-data')
    assert res.status_code == 200
    assert sorted(os.listdir(user_folder)) == ['new.png']
    assert not os.path.exists(user_folder + '.old')
    assert not os.path.exists(user_folder + '.restoring')

# ══════════════════════════════════════════════════════════════
# 복원: 다른 계정의 기록과 id 가 겹쳐도 실패하지 않는다
# ══════════════════════════════════════════════════════════════
def test_restore_survives_id_collision_with_other_user(client):
    """entries.id 는 전역 PK 라 다른 계정의 id 와 겹치면 복원이 통째로 실패했다.

    (예: test 계정으로 batmi 의 백업을 복원 → UNIQUE constraint failed: entries.id)
    """
    # 다른 사용자가 id 1000, 1001 을 이미 쓰고 있다
    with backend_app.db_conn() as conn:
        c = conn.cursor()
        for eid in (1000, 1001):
            entry_logic.insert_entry(c, 'someone_else', {
                'id': eid, 'type': 'trade', 'stockName': '남의기록',
                'tradeType': '매수', 'price': 100, 'quantity': 1,
                'rawDate': '2025-01-01T09:00'})
        conn.commit()

    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'restorer'
        sess['expires_at'] = time.time() + 3600

    # 같은 id 를 담은 백업을 복원한다
    payload = [
        {'id': 1000, 'type': 'trade', 'stockName': '내기록A', 'tradeType': '매수',
         'price': 500, 'quantity': 2, 'rawDate': '2025-02-01T09:00'},
        {'id': 1001, 'type': 'trade', 'stockName': '내기록B', 'tradeType': '매도',
         'price': 600, 'quantity': 2, 'rawDate': '2025-02-02T09:00'},
        {'id': 2000, 'type': 'trade', 'stockName': '내기록C', 'tradeType': '매수',
         'price': 700, 'quantity': 1, 'rawDate': '2025-02-03T09:00'},
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('data.json', json.dumps(payload, ensure_ascii=False))
    buf.seek(0)

    res = client.post('/api/restore', data={'file': (buf, 'b.zip')},
                      content_type='multipart/form-data')
    assert res.status_code == 200, res.get_data(as_text=True)

    with backend_app.db_conn() as conn:
        mine = conn.execute(
            "SELECT stockName FROM entries WHERE username='restorer' ORDER BY stockName").fetchall()
        theirs = conn.execute(
            "SELECT id FROM entries WHERE username='someone_else' ORDER BY id").fetchall()
        dups = conn.execute("SELECT COUNT(*) - COUNT(DISTINCT id) FROM entries").fetchone()[0]

    assert [r['stockName'] for r in mine] == ['내기록A', '내기록B', '내기록C']
    assert [r['id'] for r in theirs] == [1000, 1001], '남의 기록이 훼손됐다'
    assert dups == 0
    # 겹치지 않은 id(2000)는 그대로 살아 있어야 한다
    with backend_app.db_conn() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM entries WHERE username='restorer' AND id=2000"
        ).fetchone()[0] == 1

def test_restore_same_account_keeps_original_ids(client):
    """같은 계정의 백업을 되돌릴 때는 id 가 그대로 유지된다 (불필요한 재배정 금지)."""
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'selfrestore'
        sess['expires_at'] = time.time() + 3600

    payload = [{'id': 555, 'type': 'trade', 'stockName': 'A', 'tradeType': '매수',
                'price': 1, 'quantity': 1, 'rawDate': '2025-01-01T09:00'}]
    for _ in range(2):   # 두 번 연속 복원해도 id 가 안 밀려야 한다
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('data.json', json.dumps(payload, ensure_ascii=False))
        buf.seek(0)
        assert client.post('/api/restore', data={'file': (buf, 'b.zip')},
                           content_type='multipart/form-data').status_code == 200

    with backend_app.db_conn() as conn:
        ids = [r['id'] for r in conn.execute(
            "SELECT id FROM entries WHERE username='selfrestore'").fetchall()]
    assert ids == [555]

def test_backup_roundtrip_carries_account_mappings(client, tmp_path, monkeypatch):
    """백업 ZIP 은 DB 의 매핑을 담고, 복원은 그것을 DB 로 되돌려야 한다."""
    monkeypatch.setattr(config, 'UPLOAD_FOLDER', str(tmp_path / 'uploads'))
    _login(client, 'bkuser')
    _ensure_user('bkuser')

    payload = {"brokers": {}, "accounts": {"9999-8888": {"alias": "연습계좌",
                                                         "exclude_from_stats": True}}}
    client.post('/api/mappings', json=payload)

    zip_bytes = client.get('/api/backup').data
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        # 구버전 백업과 같은 파일명을 유지해야 예전 ZIP 도 계속 복원된다
        assert 'account_info.json' in zf.namelist()
        assert json.loads(zf.read('account_info.json')) == payload

    # 매핑을 지운 뒤 복원하면 되살아나야 한다
    client.post('/api/mappings', json={"brokers": {}, "accounts": {}})
    assert client.get('/api/mappings').json['accounts'] == {}

    res = client.post('/api/restore',
                      data={'file': (io.BytesIO(zip_bytes), 'b.zip')},
                      content_type='multipart/form-data')
    assert res.status_code == 200
    assert client.get('/api/mappings').json == payload

def test_restore_accepts_legacy_zip_with_account_info(client, tmp_path, monkeypatch):
    """파일 저장 시절에 만들어진 ZIP 도 그대로 복원돼야 한다."""
    monkeypatch.setattr(config, 'UPLOAD_FOLDER', str(tmp_path / 'uploads'))
    _login(client, 'legacyuser')
    _ensure_user('legacyuser')

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('data.json', '[]')
        zf.writestr('account_info.json',
                    json.dumps({"brokers": {}, "accounts": {"7777": {"alias": "옛계좌"}}}))
    buf.seek(0)

    res = client.post('/api/restore', data={'file': (buf, 'legacy.zip')},
                      content_type='multipart/form-data')
    assert res.status_code == 200
    assert client.get('/api/mappings').json['accounts'] == {"7777": {"alias": "옛계좌"}}


def test_verify_backup_zip_exceptions(tmp_path):
    """verify_backup_zip 의 예외 및 손상 상황을 검증한다."""
    import backups
    import zipfile
    
    # 1. 파일 자체가 zip 파일이 아니거나 손상된 경우 -> Exception 발생
    bad_zip = tmp_path / "bad.zip"
    bad_zip.write_text("not a zip file")
    ok, msg = backups.verify_backup_zip(str(bad_zip), 0)
    assert ok is False
    assert "File is not a zip file" in msg or "zipfile" in msg.lower() or "bad magic number" in msg.lower() or "not a zip" in msg.lower() or "file is not a zip" in msg.lower()

    # 2. testzip() 이 에러를 리턴하는 경우
    from unittest.mock import patch, MagicMock
    with patch('zipfile.ZipFile') as mock_zip:
        mock_instance = MagicMock()
        mock_instance.__enter__.return_value = mock_instance
        mock_instance.testzip.return_value = "corrupted_file.txt"
        mock_zip.return_value = mock_instance
        
        ok, msg = backups.verify_backup_zip("dummy.zip", 0)
        assert ok is False
        assert "손상된 압축 항목: corrupted_file.txt" in msg

    # 3. data.json 형식이 리스트가 아닌 경우
    wrong_type_zip = tmp_path / "wrong_type.zip"
    import json
    with zipfile.ZipFile(wrong_type_zip, 'w') as zf:
        zf.writestr('data.json', json.dumps({"not_a_list": True}))
    ok, msg = backups.verify_backup_zip(str(wrong_type_zip), 0)
    assert ok is False
    assert "data.json 형식이 올바르지 않습니다." in msg
