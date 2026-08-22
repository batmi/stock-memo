import pytest
import io
import time
import zipfile
import json
import os
import backend_app
import entry_logic
import trading_api
from unittest.mock import patch, MagicMock

def test_home_page_redirects_without_auth(client):
    """
    로그인하지 않은 상태에서 메인 페이지(/) 접속 시 
    로그인 페이지로 리다이렉트(302)되는지 테스트합니다.
    """
    response = client.get('/')
    assert response.status_code == 302
    assert '/login' in response.location

def test_home_page_status_with_auth(client):
    """
    세션을 조작하여 로그인한 상태를 만든 후 메인 페이지(/) 접속 시 
    정상적으로 페이지(200)가 로드되는지 테스트합니다.
    """
    client.post('/signup', data={'username': 'admin', 'password': 'Passw0rd!', 'password_confirm': 'Passw0rd!'})
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'testuser'
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)

    response = client.get('/')
    assert response.status_code == 200

def test_backup_endpoint_requires_auth(client):
    """
    README에 명시된 '완벽 백업' 기능 엔드포인트 접근 시, 
    인증(로그인)되지 않은 상태라면 정상 처리(200)되지 않고 
    리다이렉트(302) 또는 에러(401/403)가 발생하는지 확인합니다.
    (※ 백업 엔드포인트 URL이 '/api/backup'이라고 가정한 예시입니다.)
    """
    response = client.get('/api/backup')
    assert response.status_code != 200

def test_logout(client):
    """
    로그아웃(/logout) 호출 시 세션이 안전하게 삭제되고 로그인 페이지로 이동하는지 테스트합니다.
    """
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'testuser'
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)
        
    response = client.get('/logout')
    assert response.status_code == 302
    assert '/login' in response.location
    
    with client.session_transaction() as sess:
        assert 'logged_in' not in sess
        assert 'username' not in sess

def test_get_data_empty(client):
    """
    새로운 사용자의 경우 초기 데이터(/api/data)가 비어있는 리스트([])로 반환되는지 확인합니다.
    """
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'new_user'
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)
        
    response = client.get('/api/data')
    assert response.status_code == 200
    assert response.json == []

def test_create_and_get_entry(client):
    """
    새로운 매매 기록을 등록(POST)하고, 정상적으로 조회(GET)되는지 확인하는 통합 테스트입니다.
    """
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'testuser'
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)
        
    new_entry = {
        "type": "buy",
        "stockName": "삼성전자",
        "stockCode": "005930",
        "price": 80000,
        "quantity": 10,
        "title": "테스트 매수 기록"
    }
    
    # 1. API를 통한 매매 기록 저장 테스트
    post_res = client.post('/api/entry', json=new_entry)
    assert post_res.status_code == 200
    assert post_res.json.get('status') == 'success'
    
    # 2. API를 통해 저장한 데이터가 정상적으로 불러와지는지 확인
    get_res = client.get('/api/data')
    assert get_res.status_code == 200
    data = get_res.json
    
    assert len(data) == 1
    assert data[0]['stockName'] == "삼성전자"
    assert data[0]['type'] == "buy"
    assert data[0]['username'] == "testuser"

def test_signup_process(client):
    """
    회원가입 폼 전송 시 정상 처리 및 중복 아이디 방지가 동작하는지 테스트합니다.
    """
    # 1. 정상 회원가입
    response1 = client.post('/signup', data={
        'username': 'new_test_user',
        'password': 'password123',
        'password_confirm': 'password123'
    })
    response_text1 = response1.get_data(as_text=True)
    assert 'successBanner' in response_text1  # 성공 배너가 화면에 렌더링되어야 함
    
    # 2. 동일한 아이디로 중복 가입 시도
    response2 = client.post('/signup', data={
        'username': 'new_test_user',
        'password': 'password456',
        'password_confirm': 'password456'
    })
    response_text2 = response2.get_data(as_text=True)
    assert 'errorBanner' in response_text2    # 에러 배너가 화면에 렌더링되어야 함

def test_login_process(client):
    """
    실제 /login 라우트에 폼 데이터를 전송하여 로그인 성공/실패 로직을 테스트합니다.
    """
    # 1. 첫 가입 (최고 관리자가 됨)
    client.post('/signup', data={
        'username': 'admin',
        'password': 'Adminpassw0rd!',
        'password_confirm': 'Adminpassw0rd!'
    })

    # 1. 비밀번호를 틀렸을 경우
    response_fail = client.post('/login', data={
        'username': 'admin',
        'password': 'wrongpassword'
    })
    assert 'errorBanner' in response_fail.get_data(as_text=True)
    
    # 2. 정상 로그인 시도
    response_success = client.post('/login', data={
        'username': 'admin',
        'password': 'Adminpassw0rd!'
    })
    assert response_success.status_code == 302
    assert response_success.location == '/'

def test_update_and_delete_entry(client):
    """
    기존 매매 기록을 수정(PUT)하고 삭제(DELETE)하는 과정을 검증합니다.
    """
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'testuser'
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)
        
    # 1. 테스트용 데이터 생성
    client.post('/api/entry', json={
        "type": "buy", "stockName": "카카오", "price": 50000, "quantity": 10
    })
    
    get_res = client.get('/api/data')
    entry_id = get_res.json[0]['id']
    
    # 2. 데이터 수정 (PUT)
    put_res = client.put(f'/api/entry/{entry_id}', json={
        "type": "sell", "stockName": "카카오", "price": 55000, "quantity": 10
    })
    assert put_res.status_code == 200
    
    get_res2 = client.get('/api/data')
    assert get_res2.json[0]['type'] == 'sell'
    assert get_res2.json[0]['price'] == 55000
    
    # 3. 데이터 삭제 (DELETE)
    del_res = client.delete(f'/api/entry/{entry_id}')
    assert del_res.status_code == 200
    
    get_res3 = client.get('/api/data')
    assert len(get_res3.json) == 0  # 삭제 후 데이터가 비어있어야 함

def test_preferences_api(client):
    """
    사용자별 환경 설정(Preferences) 저장 및 조회가 잘 되는지 테스트합니다.
    """
    client.post('/signup', data={'username': 'admin', 'password': 'Passw0rd!', 'password_confirm': 'Passw0rd!'})
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'admin'
        sess['is_admin'] = True
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)
        
    prefs = {"theme": "dark", "chartType": "bar"}
    post_res = client.post('/api/preferences', json=prefs)
    assert post_res.status_code == 200
    
    get_res = client.get('/api/preferences')
    assert get_res.status_code == 200
    assert get_res.json.get("theme") == "dark"

def test_admin_api_access_control(client):
    """
    관리자 전용 API(/api/admin/*)가 일반 유저에게는 403 Forbidden을 반환하는지 확인합니다.
    """
    # 일반 유저 세션
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'normal_user'
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)
        
    res_forbidden = client.get('/api/admin/users')
    assert res_forbidden.status_code == 403
    
    # 최고 관리자 세션
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'admin'
        sess['is_admin'] = True
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)
        
    res_allowed = client.get('/api/admin/users')
    assert res_allowed.status_code == 200
    assert isinstance(res_allowed.json, list)

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

def test_login_lockout(client):
    """
    비밀번호 5회 이상 오입력 시 IP가 차단되는지 테스트합니다.
    """
    client.post('/signup', data={'username': 'admin', 'password': 'Passw0rd!', 'password_confirm': 'Passw0rd!'})
    for _ in range(5):
        res = client.post('/login', data={'username': 'admin', 'password': 'WrongPassw0rd!'})
    
    # 6번째 시도 시 차단 메시지 확인
    res = client.post('/login', data={'username': 'admin', 'password': 'WrongPassw0rd!'})
    assert '차단' in res.get_data(as_text=True)

def test_unallowed_user_login(client):
    """
    관리자 승인이 안 된(is_allowed=0) 계정의 로그인 차단 테스트입니다.
    """
    client.post('/signup', data={'username': 'admin', 'password': 'Passw0rd!', 'password_confirm': 'Passw0rd!'})
    client.post('/signup', data={'username': 'wait_user', 'password': 'Passw0rd1!', 'password_confirm': 'Passw0rd1!'})
    res = client.post('/login', data={'username': 'wait_user', 'password': 'Passw0rd1!'})
    assert '승인이 필요' in res.get_data(as_text=True)

def test_admin_edge_cases(client):
    """
    관리자 기능의 각종 예외 상황(최고 관리자 삭제/변경 방지 등)을 테스트합니다.
    """
    client.post('/signup', data={'username': 'admin', 'password': 'Passw0rd!', 'password_confirm': 'Passw0rd!'})
    client.post('/signup', data={'username': 'user2', 'password': 'Passw0rd!', 'password_confirm': 'Passw0rd!'})
    
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'admin'
        sess['is_admin'] = True
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)
        
    # 최고 관리자 본인 삭제 시도
    res = client.delete('/api/admin/users/admin')
    assert res.status_code == 400
    
    # 최고 관리자 본인 상태 변경 시도
    res = client.post('/api/admin/users/admin/toggle_allow')
    assert res.status_code == 400
    
    # 존재하지 않는 유저 상태 변경 시도
    res = client.post('/api/admin/users/unknown_user/toggle_allow')
    assert res.status_code == 404
    
    # 임시 비밀번호 초기화 기능
    res = client.post('/api/admin/users/user2/reset_password')
    assert res.status_code == 200
    assert 'new_password' in res.json
    
    # 정상 유저 삭제
    res = client.delete('/api/admin/users/user2')
    assert res.status_code == 200

def test_account_deletion_and_change_pw(client):
    """
    회원 탈퇴 및 비밀번호 변경 시 예외(비밀번호 오입력 등) 케이스를 테스트합니다.
    """
    client.post('/signup', data={'username': 'normal', 'password': 'Passw0rd1!', 'password_confirm': 'Passw0rd1!'})
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'admin'
        sess['is_admin'] = True
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)
    client.post('/api/admin/users/normal/toggle_allow')
    
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'normal'
        sess['is_admin'] = False
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)
        
    # 1. 비밀번호 변경 - 현재 비밀번호 오입력
    res = client.post('/api/change_password', json={'current_password': 'Nope0rd!x', 'new_password': 'Brand0New!'})
    assert res.status_code == 400
    
    # 2. 회원 탈퇴 - 비밀번호 오입력
    res = client.delete('/api/account', json={'password': 'wrong'})
    assert res.status_code == 400
    
    # 3. 회원 탈퇴 - 정상
    res = client.delete('/api/account', json={'password': 'Passw0rd1!'})
    assert res.status_code == 200
    
    # 4. 최고 관리자 탈퇴 시도 방어
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'admin'
        sess['is_admin'] = True
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)
    res = client.delete('/api/account', json={'password': 'Passw0rd!'})
    assert res.status_code == 403

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
    monkeypatch.setattr(backend_app, 'MAX_RESTORE_UNCOMPRESSED_BYTES', 1024 * 1024)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('data.json', b'0' * (4 * 1024 * 1024))  # 4MB -> 압축하면 수 KB
    buf.seek(0)
    assert buf.getbuffer().nbytes < 512 * 1024  # 업로드 자체는 작다

    data = {'file': (buf, 'big.zip')}
    res = client.post('/api/restore', data=data, content_type='multipart/form-data')
    assert res.status_code == 413
    assert '압축 해제 크기' in res.get_json()['error']

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

@patch('prices._http_get')
@patch('urllib.request.urlopen')
def test_mock_external_apis(mock_urlopen, mock_http_get, client):
    """
    외부 API(네이버 주가, 구글 뉴스) 통신을 Mocking하여 네트워크 연결 없이 정상 로직을 테스트합니다.
    """
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'testuser'
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)

    mock_res = MagicMock()
    # 국내 주식과 해외 주식 파싱에 모두 통과할 수 있는 다목적 더미 JSON 구조 생성
    mock_res.read.return_value = b'{"closePrice": "80,000", "chart": {"result": [{"meta": {"regularMarketPrice": 150.0}}]}}'
    mock_res.__enter__.return_value = mock_res
    mock_urlopen.return_value = mock_res
    # 시세 조회는 prices._http_get 경로(http.client)를 사용하므로 별도 모킹
    mock_http_get.return_value = b'{"closePrice": "80,000", "chart": {"result": [{"meta": {"regularMarketPrice": 150.0}}]}}'

    # 주가 API 테스트 (국내, 해외, 금)
    res_price = client.post('/api/current_price', json={'codes': ['005930', 'AAPL', 'KRXGOLD']})
    assert res_price.status_code == 200
    
    # 뉴스 API 테스트 (XML RSS 모사)
    mock_res.read.return_value = b'''<?xml version="1.0" encoding="UTF-8"?>
    <rss><channel><item><title>Mock News</title><link>http://link</link><pubDate>Today</pubDate></item></channel></rss>'''
    
    res_news = client.post('/api/news', json={'stocks': []})
    assert res_news.status_code == 200
    assert len(res_news.json) > 0
    assert res_news.json[0]['title'] == 'Mock News'

def test_ping_and_timeout(client):
    """
    세션 연장용 ping 엔드포인트와 타임아웃 파라미터를 테스트합니다.
    """
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'admin'
        sess['is_admin'] = True
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)
        
    res_ping = client.post('/api/ping')
    assert res_ping.status_code == 200
    
    res_me = client.get('/api/me')
    assert res_me.status_code == 200
    
    # 타임아웃 파라미터를 동반한 로그아웃
    res_logout = client.get('/logout?timeout=1')
    assert res_logout.status_code == 302
    assert 'timeout=1' in res_logout.location

def test_json_migration(app, client):
    """기존 JSON 파일에서 SQLite DB로 데이터가 자동 마이그레이션 되는지 테스트합니다."""
    with app.app_context():
        conn = backend_app.get_db()
        conn.execute("DELETE FROM entries")
        conn.execute("DELETE FROM users")
        conn.commit()
        conn.close()
        
    dummy_json = [{
        "id": 9999, "type": "buy", "stockName": "JSON_MIGRATION_TEST", 
        "attachedImage": "http://example.com/img.jpg"
    }]
    with open(backend_app.DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(dummy_json, f)
        
    with app.app_context():
        backend_app.init_db()
        
    client.post('/signup', data={'username': 'admin_mig', 'password': 'Passw0rd!', 'password_confirm': 'Passw0rd!'})
    
    with app.app_context():
        conn = backend_app.get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM entries WHERE stockName='JSON_MIGRATION_TEST'")
        row = c.fetchone()
        assert row is not None
        assert row['attachedImage'] == "http://example.com/img.jpg"
        conn.close()
        
    if os.path.exists(backend_app.DATA_FILE):
        os.remove(backend_app.DATA_FILE)

def test_process_image_edge_cases():
    """process_image 함수의 예외(None 입력, URL 직접 입력) 케이스를 테스트합니다."""
    assert backend_app.process_image(None, 1) is None
    assert backend_app.process_image("http://example.com/test.png", 1) == "http://example.com/test.png"

def test_signup_edge_cases(client):
    """회원가입 시 입력값이 누락되거나 비밀번호가 일치하지 않는 경우를 테스트합니다."""
    res1 = client.post('/signup', data={'username': '', 'password': ''})
    assert '모두 입력해주세요' in res1.get_data(as_text=True)
    
    res2 = client.post('/signup', data={'username': 'testuser', 'password': 'Passw0rd1!', 'password_confirm': 'Passw0rd2!'})
    assert '일치하지 않습니다' in res2.get_data(as_text=True)

def test_preferences_edge_cases(client):
    """환경 설정 API의 인증 안된 접근 및 잘못된 JSON 형식 처리 예외를 테스트합니다."""
    res1 = client.get('/api/preferences')
    assert res1.status_code == 401
    
    res2 = client.post('/api/preferences', json={})
    assert res2.status_code == 401
    
    # 잘못된 JSON 데이터(String)가 DB에 있을 때 빈 딕셔너리로 처리되는지 검증
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'admin'
        sess['is_admin'] = True
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)
        
    conn = backend_app.get_db()
    conn.execute("UPDATE users SET preferences = 'INVALID_JSON_DATA' WHERE username = 'admin'")
    conn.commit()
    conn.close()
    
    res3 = client.get('/api/preferences')
    assert res3.status_code == 200
    assert res3.json == {}

def test_uploaded_file_success(client):
    """정상적으로 권한이 있는 사용자의 파일 다운로드가 동작하는지 테스트합니다."""
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'admin'
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)
        
    user_dir = os.path.join(backend_app.UPLOAD_FOLDER, 'admin')
    os.makedirs(user_dir, exist_ok=True)
    test_file_path = os.path.join(user_dir, 'test_download.txt')
    with open(test_file_path, 'w') as f:
        f.write('file_content_test')
        
    res = client.get('/uploads/admin/test_download.txt')
    assert res.status_code == 200
    assert b'file_content_test' in res.data

@patch('prices._http_get')
def test_current_price_edge_cases(mock_http_get, client, app):
    """현재 주가 API(/api/current_price)의 다양한 파싱 폴백 및 네트워크 에러 처리를 검증합니다."""
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'testuser'
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)

    test_state = {'gold_fail_all': False, 'all_fail': False}

    # prices._http_get(url, headers) 를 모킹: 성공 시 응답 본문(bytes) 반환, 실패 시 예외 발생
    def http_get_side_effect(url, headers=None):
        if 'M04020000' in url:
            if test_state['gold_fail_all']:
                raise Exception("All Fail")
            raise Exception("Naver Gold API Fail")
        elif 'KRX_Gold_Market' in url:
            if test_state['gold_fail_all']:
                raise Exception("All Fail")
            return b"<th>\xed\x98\x84\xec\x9e\xac\xea\xb0\x80</th><td><strong>88,000</strong></td>"
        elif 'siseJson' in url:
            if test_state['all_fail']:
                raise Exception("All APIs Fail")
            return b'var u_js= { "result": { "nowVal": 95000 } };'
        elif '005930' in url:
            if test_state['all_fail']:
                raise Exception("All APIs Fail")
            return b'{"closePrice": "95000"}'
        elif 'ABCDEF' in url:
            return b'{"chart": {"result": [{"meta": {"regularMarketPrice": 250.5}}]}}'
        elif '123456' in url:
            if 'yahoo' in url:
                return b'{"chart": {"result": [{"meta": {"regularMarketPrice": 100.0}}]}}'
            raise Exception("Naver API Fail")
        elif 'AAPL' in url:
            raise Exception("All APIs Fail")
        return b''

    mock_http_get.side_effect = http_get_side_effect
    
    # 1. 금 주가: 기본 API 에러 -> KRX 크롤링 성공 패턴
    res_gold = client.post('/api/current_price', json={'codes': ['KRXGOLD']})
    assert res_gold.json.get('KRXGOLD') == 88000.0
    
    # 2. 금 주가: 모든 API 실패 시 None 반환
    test_state['gold_fail_all'] = True
    res_gold_fail = client.post('/api/current_price', json={'codes': ['KRXGOLD']})
    assert res_gold_fail.json.get('KRXGOLD') is None
    
    # 3. 네이버 주가: 실시간 API 정상 응답
    res_naver = client.post('/api/current_price', json={'codes': ['005930']})
    assert res_naver.json.get('005930') == 95000.0
    
    # 4. 6자리 해외 주식(US): 네이버 API 생략하고 즉시 야후 파이낸스 성공 패턴
    res_us = client.post('/api/current_price', json={'codes': ['ABCDEF']})
    assert res_us.json.get('ABCDEF') == 250.5
    
    # 5. 국내 주식 네이버 API 실패 -> 야후 파이낸스 폴백
    res_fallback = client.post('/api/current_price', json={'codes': ['123456']})
    assert res_fallback.json.get('123456') == 100.0
    
    # 6. 빈 문자열 및 잘못된 코드 무시 처리
    res_empty = client.post('/api/current_price', json={'codes': ['', ' ']})
    assert res_empty.status_code == 200
    assert res_empty.json == {}

    # 캐시 초기화: 이전 테스트(3번)에서 성공한 값이 DB에 캐시되어 있으면, 모든 API가 실패해도 캐시값을 리턴하므로 실패 케이스를 검증하기 위해 비워줍니다.
    with app.app_context():
        conn = backend_app.get_db()
        conn.execute("DELETE FROM price_cache")
        conn.commit()
        conn.close()

    # 7. 일반 주식 모든 API 실패 시 500 에러 없이 안전하게 통과하는지 검증 (Unpacking 버그 회귀 방지)
    test_state['all_fail'] = True
    res_all_fail = client.post('/api/current_price', json={'codes': ['005930', 'AAPL']})
    assert res_all_fail.status_code == 200
    assert res_all_fail.json.get('005930') is None

@patch('urllib.request.urlopen')
def test_news_api_exceptions(mock_urlopen, client):
    """구글 뉴스 API 파싱 시 예외가 발생해도 시스템 중단 없이 빈 배열을 리턴하는지 테스트합니다."""
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'testuser'
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)
        
    mock_urlopen.side_effect = Exception("Network Connection Error")
    
    res = client.post('/api/news', json={'stocks': ['테슬라']})
    assert res.status_code == 200
    assert res.json == []

def test_account_deletion_and_password_exceptions(client):
    """회원 탈퇴 및 비밀번호 변경 시 발생할 수 있는 에러 상황들을 검증합니다."""
    # 인증 전 접근 차단
    res_pw_unauth = client.post('/api/change_password', json={})
    assert res_pw_unauth.status_code == 401
    
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'admin'
        sess['is_admin'] = True
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)
        
    # 비밀번호 변경 - 빈 값 전송
    res_pw_empty = client.post('/api/change_password', json={})
    assert res_pw_empty.status_code == 400
    assert '모든 필드' in res_pw_empty.json['error']
    
    # 최고 관리자는 계정 삭제 API 접근 시 403 에러가 우선 발생하므로 일반 유저로 세션 변경
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'normal_user'
        sess['is_admin'] = False
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)
        
    # 계정 삭제 - 빈 비밀번호 전송
    res_del_empty = client.delete('/api/account', json={})
    assert res_del_empty.status_code == 400
    assert '비밀번호를 입력' in res_del_empty.json['error']

@patch('time.sleep')
def test_auto_backup_job(mock_sleep, client, app, tmp_path, monkeypatch):
    """
    자동 백업 스레드 함수가 실행될 때 ZIP 파일이 잘 생성되는지,
    그리고 7일이 지난 오래된 백업 파일이 정상적으로 삭제되는지(보관 주기) 테스트합니다.
    무한 루프를 탈출하기 위해 두 번째 time.sleep 호출 시 예외를 발생시킵니다.
    """
    # ⭐️ 백업 폴더를 임시 경로로 격리한다. 예전에는 실제 backup/ 에 쓰는 바람에
    #    이전 실행이 남긴 zip 이 다음 실행의 '새 백업 1개' 단언을 깨뜨렸고,
    #    사용자의 백업 폴더에도 테스트 찌꺼기가 계속 쌓였다.
    monkeypatch.setattr(backend_app, 'BACKUP_DIR', str(tmp_path / 'backup'))

    # 1. 테스트 유저 및 매매 기록 생성
    client.post('/signup', data={'username': 'autobackupuser', 'password': 'Passw0rd!', 'password_confirm': 'Passw0rd!'})
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'autobackupuser'
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)
        
    client.post('/api/entry', json={"type": "buy", "stockName": "자동백업테스트", "price": 10000, "quantity": 1})

    backup_dir = os.path.join(backend_app.BACKUP_DIR, 'autobackupuser')
    os.makedirs(backup_dir, exist_ok=True)
    
    # 2. 7일이 지난 가짜 백업 파일 생성 (os.utime으로 수정 시간 조작)
    old_file_path = os.path.join(backup_dir, 'TradingJournal_backup_autobackupuser_old.zip')
    with open(old_file_path, 'w') as f:
        f.write("old data")
        
    # (time 은 모듈 상단에서 이미 import 되어 있다. 여기서 다시 import 하면 함수
    #  전체에서 time 이 지역 변수로 취급돼 위쪽 time.time() 이 UnboundLocalError 가 된다)
    old_time = time.time() - (8 * 86400) # 8일 전 시간
    os.utime(old_file_path, (old_time, old_time))

    # 3. 무한 루프 탈출 설정 (첫 번째 sleep은 통과, 두 번째에서 예외 발생시켜 종료)
    sleep_calls = [0]
    def side_effect(*args):
        sleep_calls[0] += 1
        if sleep_calls[0] > 1:
            raise RuntimeError("Break Loop")
    mock_sleep.side_effect = side_effect

    # 4. 백업 작업 1회 실행
    with app.app_context():
        try:
            backend_app.auto_backup_job()
        except RuntimeError:
            pass

    # 5. 백업 결과 검증
    assert os.path.exists(backup_dir)
    files = os.listdir(backup_dir)
    
    # 8일 전 생성된 가짜 백업 파일이 삭제되었는지 확인
    assert 'TradingJournal_backup_autobackupuser_old.zip' not in files
    
    # 새로 생성된 백업 zip 파일이 존재하는지 확인
    zip_files = [f for f in files if f.endswith('.zip')]
    assert len(zip_files) == 1
    
    # zip 파일 내용(JSON) 검증
    with zipfile.ZipFile(os.path.join(backup_dir, zip_files[0]), 'r') as zf:
        assert 'data.json' in zf.namelist()
        with zf.open('data.json') as f:
            data = json.loads(f.read().decode('utf-8'))
            assert len(data) >= 1
            assert data[0]['stockName'] == "자동백업테스트"
            
    # 테스트 후 폴더 정리
    import shutil
    shutil.rmtree(backup_dir)

# ─────────────────────────────────────────────────────────────
# ⭐️ 데이터 무결성: 매매 기록 검증 테스트
# ─────────────────────────────────────────────────────────────
def _login(client, username='trader'):
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = username
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)
        # before_request 가 절대 만료(expires_at)를 검사하므로 없으면 전부 401 이 된다.
        sess['expires_at'] = time.time() + 3600


def _buy(stock='삼성전자', qty=10, price=80000, **kw):
    e = {"type": "trade", "tradeType": "매수", "stockName": stock,
         "stockCode": "005930", "price": price, "quantity": qty}
    e.update(kw)
    return e


def _sell(stock='삼성전자', qty=10, price=90000, **kw):
    e = {"type": "trade", "tradeType": "매도", "stockName": stock,
         "stockCode": "005930", "price": price, "quantity": qty}
    e.update(kw)
    return e


def test_sell_without_buy_is_rejected(client):
    """매수 보유 기록이 없는 종목의 매도는 400으로 차단되어야 한다."""
    _login(client)
    res = client.post('/api/entry', json=_sell(qty=5))
    assert res.status_code == 400
    assert 'error' in res.json
    # 차단되었으므로 DB에 저장되지 않아야 한다
    assert client.get('/api/data').json == []


def test_oversell_is_rejected(client):
    """보유 수량을 초과하는 매도는 400으로 차단되어야 한다."""
    _login(client)
    assert client.post('/api/entry', json=_buy(qty=10)).status_code == 200
    res = client.post('/api/entry', json=_sell(qty=15))
    assert res.status_code == 400
    assert '초과' in res.json['error']
    # 매수 1건만 남아있어야 한다
    assert len(client.get('/api/data').json) == 1


def test_valid_sell_is_accepted(client):
    """보유 수량 이내의 매도는 정상 저장되어야 한다."""
    _login(client)
    assert client.post('/api/entry', json=_buy(qty=10)).status_code == 200
    assert client.post('/api/entry', json=_sell(qty=10)).status_code == 200
    assert len(client.get('/api/data').json) == 2


def test_update_oversell_is_rejected(client):
    """기록 수정(PUT) 시에도 보유 수량 초과 매도는 차단되어야 한다."""
    _login(client)
    client.post('/api/entry', json=_buy(qty=10))
    sell = _sell(qty=5, id=12345)
    assert client.post('/api/entry', json=sell).status_code == 200
    # 매도 수량을 20으로 늘리는 수정 시도 → 차단
    sell['quantity'] = 20
    res = client.put('/api/entry/12345', json=sell)
    assert res.status_code == 400


def test_dividend_not_blocked(client):
    """배당 기록은 보유 검증 대상이 아니므로 정상 저장되어야 한다."""
    _login(client)
    res = client.post('/api/entry', json={
        "type": "trade", "tradeType": "배당", "stockName": "삼성전자",
        "price": 500, "quantity": 10})
    assert res.status_code == 200


# ─────────────────────────────────────────────────────────────
# ⭐️ 백업 무결성 검증 / 복원 라운드트립 테스트
# ─────────────────────────────────────────────────────────────
def test_verify_backup_zip(tmp_path):
    """verify_backup_zip이 정상/손상/레코드 불일치를 올바르게 판별한다."""
    good = tmp_path / "good.zip"
    with zipfile.ZipFile(good, 'w') as zf:
        zf.writestr('data.json', json.dumps([{"id": 1}, {"id": 2}]))
    ok, _ = backend_app.verify_backup_zip(str(good), 2)
    assert ok is True

    # 레코드 수 불일치
    ok, msg = backend_app.verify_backup_zip(str(good), 5)
    assert ok is False and '불일치' in msg

    # data.json 누락
    nojson = tmp_path / "nojson.zip"
    with zipfile.ZipFile(nojson, 'w') as zf:
        zf.writestr('other.txt', 'hello')
    ok, msg = backend_app.verify_backup_zip(str(nojson), 0)
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


# ─────────────────────────────────────────────────────────────
# ⭐️ 매매 성과 분석(/api/stats) 테스트
# ─────────────────────────────────────────────────────────────
def test_stats_empty(client):
    """기록이 없으면 0값 통계를 반환한다."""
    _login(client, 'statsempty')
    res = client.get('/api/stats')
    assert res.status_code == 200
    s = res.json
    assert s['totalRealized'] == 0
    assert s['sellCount'] == 0
    assert s['monthly'] == []


def test_stats_realized_and_winrate(client):
    """실현손익/승률/손익비가 이동평균단가 기준으로 정확히 계산된다."""
    _login(client, 'statscalc')
    # 100원 10주 매수 → 평단 100
    client.post('/api/entry', json=_buy(stock='A', qty=10, price=100,
                                        rawDate='2024-01-10T09:00', id=1))
    # 120원 5주 매도 → 이익 (120-100)*5 = +100
    client.post('/api/entry', json=_sell(stock='A', qty=5, price=120,
                                         rawDate='2024-02-10T09:00', id=2))
    # 80원 5주 매도 → 손실 (80-100)*5 = -100
    client.post('/api/entry', json=_sell(stock='A', qty=5, price=80,
                                         rawDate='2024-03-10T09:00', id=3))

    s = client.get('/api/stats').json
    assert round(s['totalRealized']) == 0           # +100 -100
    assert s['sellCount'] == 2
    assert s['winCount'] == 1 and s['lossCount'] == 1
    assert round(s['winRate']) == 50
    assert round(s['avgWin']) == 100
    assert round(s['avgLoss']) == 100
    assert round(s['profitFactor'], 2) == 1.0
    # 월별 3개월(매수1, 매도2)이 집계되어야 한다
    assert len(s['monthly']) == 3
    # 종목별 집계
    assert s['perStock'][0]['stock'] == 'A'
    assert s['perStock'][0]['sellCount'] == 2


def test_stats_holding_period_and_dividend(client):
    """평균 보유기간(FIFO)과 배당 수익이 반영된다."""
    _login(client, 'statshold')
    client.post('/api/entry', json=_buy(stock='B', qty=10, price=100,
                                        rawDate='2024-01-01T09:00', id=1))
    # 10일 보유 후 전량 매도
    client.post('/api/entry', json=_sell(stock='B', qty=10, price=110,
                                         rawDate='2024-01-11T09:00', id=2))
    client.post('/api/entry', json={
        "type": "trade", "tradeType": "배당", "stockName": "B",
        "price": 50, "quantity": 10, "rawDate": "2024-02-01T09:00", "id": 3})

    s = client.get('/api/stats').json
    assert round(s['avgHoldingDays']) == 10
    assert round(s['totalDividend']) == 500
    assert round(s['totalPnl']) == round(s['totalRealized'] + 500)


# ─────────────────────────────────────────────────────────────
# ⭐️ 본문(thoughts) 내장 base64 이미지 → 파일 추출 (초기 로딩 최적화)
# ─────────────────────────────────────────────────────────────
def test_extract_inline_images(monkeypatch, tmp_path):
    """base64 이미지가 파일로 저장되고 src 가 /uploads/ URL 로 치환된다."""
    import base64 as b64
    import re
    monkeypatch.setattr(backend_app, 'UPLOAD_FOLDER', str(tmp_path))

    raw = b'\x89PNG-fake-image-bytes'
    encoded = b64.b64encode(raw).decode()
    entry = {'thoughts': f'<p>메모</p><img src="data:image/png;base64,{encoded}"><p>끝</p>'}

    out = backend_app.extract_inline_images('tester', entry)

    assert 'data:image' not in out['thoughts']
    m = re.search(r'src="/uploads/tester/(qimg_\w+\.png)"', out['thoughts'])
    assert m, out['thoughts']
    saved = tmp_path / 'tester' / m.group(1)
    assert saved.read_bytes() == raw
    # 원본 dict 는 변형하지 않는다 (복사본 반환)
    assert 'data:image' in entry['thoughts']


def test_extract_inline_images_edge_cases(monkeypatch, tmp_path):
    """이미지 없음/사용자 없음/손상된 base64 는 원본을 그대로 보존한다."""
    monkeypatch.setattr(backend_app, 'UPLOAD_FOLDER', str(tmp_path))

    no_img = {'thoughts': '<p>이미지 없음</p>'}
    assert backend_app.extract_inline_images('u', no_img) is no_img

    assert backend_app.extract_inline_images('u', {'thoughts': None}) == {'thoughts': None}
    with_img = {'thoughts': '<img src="data:image/png;base64,AAAA">'}
    assert backend_app.extract_inline_images('', with_img) is with_img

    # 패딩이 깨진 base64 → 디코딩 실패 시 원본 유지
    broken = {'thoughts': '<img src="data:image/png;base64,A">'}
    out = backend_app.extract_inline_images('u', broken)
    assert out['thoughts'] == broken['thoughts']


def test_create_entry_extracts_inline_images(client, monkeypatch, tmp_path):
    """POST /api/entry 로 저장된 본문의 base64 이미지가 URL 로 치환되어 조회된다."""
    import base64 as b64
    monkeypatch.setattr(backend_app, 'UPLOAD_FOLDER', str(tmp_path))
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
    monkeypatch.setattr(backend_app, 'UPLOAD_FOLDER', str(tmp_path / 'up'))
    backup_dir = tmp_path / 'bak'
    backup_dir.mkdir()
    monkeypatch.setattr(backend_app, 'BACKUP_DIR', str(backup_dir))

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


# ─────────────────────────────────────────────────────────────
# ⭐️ 모의투자(isSimulated) 분리 테스트
#   모의투자 체결은 기록으로는 남되, 실제 돈을 계산하는 지표에서는 빠져야 한다.
# ─────────────────────────────────────────────────────────────
def _insert_raw(username, **cols):
    """트레이딩 API 를 거치지 않고 기록을 직접 넣는다 (isSimulated 지정용)."""
    cols.setdefault('type', 'trade')
    cols.setdefault('username', username)
    keys = ', '.join(cols)
    marks = ', '.join('?' for _ in cols)
    with backend_app.db_conn() as conn:
        conn.cursor().execute(
            f"INSERT INTO entries ({keys}) VALUES ({marks})", tuple(cols.values()))
        conn.commit()


def test_stats_exclude_simulated_trades(client):
    """모의투자 체결이 실현손익·승률 통계를 오염시키면 안 된다."""
    _login(client, 'simstats')
    # 실거래: 100원 10주 매수 → 120원 10주 매도 = +200
    client.post('/api/entry', json=_buy(stock='A', qty=10, price=100,
                                        rawDate='2024-01-10T09:00', id=1))
    client.post('/api/entry', json=_sell(stock='A', qty=10, price=120,
                                         rawDate='2024-02-10T09:00', id=2))
    # 모의투자: 크게 손실 난 체결 — 통계에 섞이면 총 실현손익이 음수가 된다
    _insert_raw('simstats', id=101, stockName='B', tradeType='매수', price=1000,
                quantity=100, rawDate='2024-01-11T09:00', isSimulated=1)
    _insert_raw('simstats', id=102, stockName='B', tradeType='매도', price=100,
                quantity=100, rawDate='2024-02-11T09:00', isSimulated=1)

    s = client.get('/api/stats').json
    assert round(s['totalRealized']) == 200      # 모의 손실(-90,000)이 섞이지 않았다
    assert s['sellCount'] == 1
    assert s['lossCount'] == 0
    assert [p['stock'] for p in s['perStock']] == ['A']


def test_stats_filtered_request_also_excludes_simulated(client):
    """entry_ids 를 직접 넘겨도(차트 필터 경로) 모의투자는 걸러져야 한다."""
    _login(client, 'simstats2')
    client.post('/api/entry', json=_buy(stock='A', qty=10, price=100,
                                        rawDate='2024-01-10T09:00', id=1))
    client.post('/api/entry', json=_sell(stock='A', qty=10, price=120,
                                         rawDate='2024-02-10T09:00', id=2))
    _insert_raw('simstats2', id=101, stockName='B', tradeType='매수', price=1000,
                quantity=100, rawDate='2024-01-11T09:00', isSimulated=1)
    _insert_raw('simstats2', id=102, stockName='B', tradeType='매도', price=100,
                quantity=100, rawDate='2024-02-11T09:00', isSimulated=1)

    s = client.post('/api/stats', json={'entry_ids': [1, 2, 101, 102]}).json
    assert round(s['totalRealized']) == 200
    assert [p['stock'] for p in s['perStock']] == ['A']


def test_simulated_entries_are_still_returned_to_dashboard(client):
    """카드 슬롯에는 떠야 하므로 목록 조회에서는 빠지면 안 된다."""
    _login(client, 'simlist')
    _insert_raw('simlist', id=101, stockName='B', tradeType='매수', price=1000,
                quantity=100, rawDate='2024-01-11T09:00', isSimulated=1)

    data = client.get('/api/data').json
    assert len(data) == 1
    assert data[0]['stockName'] == 'B'
    assert data[0]['isSimulated'] == 1


@pytest.fixture
def cleanup_user_json():
    """/api/mappings 는 json/<username>/ 에 실제 파일을 남기므로 테스트 후 지운다."""
    names = []
    yield names
    import shutil
    for name in names:
        shutil.rmtree(os.path.join('json', name), ignore_errors=True)


def test_stats_exclude_flagged_account(client, cleanup_user_json):
    """계좌 관리에서 '금액 계산 제외'로 체크한 계좌는 통계에서 빠져야 한다.

    계좌 별칭은 언제든 바꿀 수 있으므로 이름이 아니라 계좌번호(exclude_from_stats)로 판정한다.
    """
    _login(client, 'excacct')
    cleanup_user_json.append('excacct')
    client.post('/api/mappings', json={
        "brokers": {},
        "accounts": {
            "11112222-01": {"broker_code": "243", "broker_name": "한국투자증권",
                            "alias": "실거래계좌"},
            "33334444-01": {"broker_code": "243", "broker_name": "한국투자증권",
                            "alias": "연습계좌", "exclude_from_stats": True},
        }
    })
    # 실거래 계좌: 100원 10주 매수 → 120원 10주 매도 = +200
    _insert_raw('excacct', id=1, stockName='A', tradeType='매수', price=100,
                quantity=10, rawDate='2024-01-10T09:00', subAccount='11112222-01')
    _insert_raw('excacct', id=2, stockName='A', tradeType='매도', price=120,
                quantity=10, rawDate='2024-02-10T09:00', subAccount='11112222-01')
    # 제외 계좌: 큰 손실 — 섞이면 총 실현손익이 음수가 된다. 하이픈 없는 표기로 들어와도 걸러야 한다.
    _insert_raw('excacct', id=101, stockName='B', tradeType='매수', price=1000,
                quantity=100, rawDate='2024-01-11T09:00', subAccount='3333444401')
    _insert_raw('excacct', id=102, stockName='B', tradeType='매도', price=100,
                quantity=100, rawDate='2024-02-11T09:00', subAccount='3333444401')

    s = client.get('/api/stats').json
    assert round(s['totalRealized']) == 200
    assert [p['stock'] for p in s['perStock']] == ['A']

    # 차트 필터 경로(entry_ids 직접 전달)도 동일하게 걸러진다
    s2 = client.post('/api/stats', json={'entry_ids': [1, 2, 101, 102]}).json
    assert round(s2['totalRealized']) == 200

    # 계좌번호 없이 이름만 남은 수기 기록은 별칭으로 대조해 걸러낸다
    _insert_raw('excacct', id=103, stockName='C', tradeType='매수', price=1000,
                quantity=100, rawDate='2024-01-12T09:00', accountName='연습계좌')
    _insert_raw('excacct', id=104, stockName='C', tradeType='매도', price=100,
                quantity=100, rawDate='2024-02-12T09:00', accountName='연습계좌')
    s3 = client.get('/api/stats').json
    assert round(s3['totalRealized']) == 200

    # 체크를 풀면 다시 통계에 잡힌다 (매핑 저장 시 캐시가 무효화돼야 한다)
    client.post('/api/mappings', json={
        "brokers": {},
        "accounts": {
            "33334444-01": {"broker_code": "243", "broker_name": "한국투자증권",
                            "alias": "연습계좌"},
        }
    })
    s4 = client.get('/api/stats').json
    assert round(s4['totalRealized']) < 0


def test_simulated_holdings_do_not_block_real_sell(client):
    """모의 보유가 실거래 매도 검증에 끼어들면 안 된다 (그 반대도 마찬가지)."""
    _login(client, 'simhold')
    # 모의로만 100주 보유
    _insert_raw('simhold', id=101, stockName='C', tradeType='매수', price=1000,
                quantity=100, rawDate='2024-01-11T09:00', isSimulated=1)

    # 실거래 보유는 0 이므로 실거래 매도는 차단되어야 한다
    res = client.post('/api/entry', json=_sell(stock='C', qty=10, price=1200))
    assert res.status_code == 400


# ── 전역 예외 핸들러가 정상 HTTP 응답을 삼키지 않는지 ──────────────
def test_http_errors_keep_their_status_codes(client):
    """404/405/413 이 500 으로 뭉개지면 클라이언트가 원인을 구분할 수 없다."""
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'testuser'
        sess['expires_at'] = time.time() + 3600

    assert client.get('/nope').status_code == 404
    assert client.get('/api/nope').status_code == 404
    # /api/current_price 는 POST 전용
    assert client.delete('/api/data').status_code == 405


def test_oversized_upload_returns_413_not_500(client):
    """MAX_CONTENT_LENGTH 초과는 413 이어야 한다 (예전엔 500 + 스택트레이스)."""
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'testuser'
        sess['expires_at'] = time.time() + 3600

    limit = backend_app.app.config['MAX_CONTENT_LENGTH']
    data = {'file': (io.BytesIO(b'x' * (limit + 1024)), 'big.zip')}
    res = client.post('/api/restore', data=data, content_type='multipart/form-data')
    assert res.status_code == 413


def test_unhandled_exception_does_not_leak_internals(client, monkeypatch):
    """진짜 예외는 500 이되, 내부 메시지(경로·SQL 등)를 밖으로 흘리지 않는다."""
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'testuser'
        sess['expires_at'] = time.time() + 3600

    def boom(_username):
        raise RuntimeError('내부 경로 /var/secret/journal.db')

    monkeypatch.setattr(backend_app, 'get_user_mappings', boom)

    res = client.get('/api/mappings')
    assert res.status_code == 500
    body = res.get_data(as_text=True)
    assert '/var/secret' not in body       # 내부 정보 비노출
    assert res.get_json()['error'] == '서버 오류가 발생했습니다.'


# ── 복원 중 실패해도 기존 첨부파일이 사라지지 않는지 ──────────────
def test_restore_keeps_existing_uploads_when_copy_fails(client, monkeypatch, tmp_path):
    """복사가 도중에 실패해도 원본 첨부파일 폴더는 그대로 남아야 한다.

    예전에는 기존 폴더를 먼저 rmtree 하고 복사해서, 복사가 실패하면 첨부파일이
    영구 소실되고 되돌릴 방법이 없었다.
    """
    monkeypatch.setattr(backend_app, 'UPLOAD_FOLDER', str(tmp_path / 'uploads'))
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'restoreuser'
        sess['expires_at'] = time.time() + 3600

    # 기존 첨부파일을 심어 둔다
    user_folder = os.path.join(backend_app.UPLOAD_FOLDER, 'restoreuser')
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
    monkeypatch.setattr(backend_app, 'UPLOAD_FOLDER', str(tmp_path / 'uploads'))
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'restoreuser2'
        sess['expires_at'] = time.time() + 3600

    user_folder = os.path.join(backend_app.UPLOAD_FOLDER, 'restoreuser2')
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


# ── 큰 응답도 압축되는지 (예전 16MB 상한이 압축을 꺼뜨렸다) ──────────
def test_large_json_response_is_still_compressed(client, monkeypatch):
    """/api/data 가 커져도 gzip 이 꺼지면 안 된다.

    예전에는 16MB 를 넘는 순간 '메모리 보호'로 압축을 건너뛰어, 정작 절감 효과가
    가장 큰 구간에서 수십 MB 가 그대로 전송됐다.
    """
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'compressuser'
        sess['expires_at'] = time.time() + 3600

    # 상한을 낮춰서 '큰 응답' 상황을 작은 데이터로 재현한다
    monkeypatch.setattr(backend_app, 'MAX_COMPRESS_BYTES', 96 * 1024 * 1024)

    body = '<p>' + ('메모 ' * 400) + '</p>'
    with backend_app.db_conn() as conn:
        c = conn.cursor()
        for i in range(200):
            entry_logic.insert_entry(c, 'compressuser', {
                'id': 1700000000000 + i, 'type': 'trade', 'stockName': '종목',
                'stockCode': '000001', 'tradeType': '매수', 'price': 100,
                'quantity': 1, 'rawDate': '2025-01-01T09:00', 'thoughts': body})
        conn.commit()

    plain = client.get('/api/data')
    gz = client.get('/api/data', headers={'Accept-Encoding': 'gzip'})
    assert plain.status_code == gz.status_code == 200
    assert gz.headers.get('Content-Encoding') == 'gzip'
    assert len(gz.get_data()) < len(plain.get_data()) / 2, '압축이 적용되지 않았다'


def test_response_above_cap_is_not_compressed(client, monkeypatch):
    """상한을 넘는 응답은 그대로 보낸다 (메모리 보호는 유지)."""
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'compressuser2'
        sess['expires_at'] = time.time() + 3600

    monkeypatch.setattr(backend_app, 'MAX_COMPRESS_BYTES', 1024)  # 1KB 로 낮춤
    with backend_app.db_conn() as conn:
        c = conn.cursor()
        entry_logic.insert_entry(c, 'compressuser2', {
            'id': 1700000000001, 'type': 'trade', 'stockName': '종목',
            'tradeType': '매수', 'price': 100, 'quantity': 1,
            'rawDate': '2025-01-01T09:00', 'thoughts': '메모 ' * 500})
        conn.commit()

    res = client.get('/api/data', headers={'Accept-Encoding': 'gzip'})
    assert res.headers.get('Content-Encoding') is None


# ══════════════════════════════════════════════════════════════
# 보안: 사용자명 검증 (경로 탈출 차단)
# ══════════════════════════════════════════════════════════════
def test_is_valid_username_rules():
    ok = ['batmi', 'user_1', 'user.name', 'a1b', 'A' * 32]
    bad = ['ab', '', None, '../../etc', 'a/b', 'a\\b', 'a..b', '한글이름',
           'A' * 33, '_lead', '.lead', '-lead', 'has space', 'null\x00byte']
    for n in ok:
        assert backend_app.is_valid_username(n) is True, n
    for n in bad:
        assert backend_app.is_valid_username(n) is False, n


def test_signup_rejects_path_traversal_username(client):
    """'../../../tmp/x' 로 가입하면 안 된다 — 사용자명이 파일 경로에 그대로 쓰인다."""
    res = client.post('/signup', data={
        'username': '../../../tmp/pwned',
        'password': 'Passw0rd!', 'password_confirm': 'Passw0rd!'})
    assert '아이디는' in res.get_data(as_text=True)
    with backend_app.db_conn() as conn:
        rows = conn.execute("SELECT username FROM users").fetchall()
    assert rows == []


def test_user_dir_blocks_escape(tmp_path):
    """경로 조합 헬퍼가 상위 탈출을 막고 None 을 돌려준다."""
    base = str(tmp_path)
    assert backend_app.user_dir(base, 'batmi') == os.path.join(base, 'batmi')
    assert backend_app.user_dir(base, '../../etc') is None
    assert backend_app.user_dir(base, 'a/b') is None
    assert backend_app.user_dir(base, '') is None


def test_backup_job_skips_unsafe_username(client, tmp_path, monkeypatch):
    """규칙 이전에 만들어진 이상한 이름의 계정은 백업 잡이 파일을 건드리지 않는다."""
    monkeypatch.setattr(backend_app, 'BACKUP_DIR', str(tmp_path / 'backup'))
    # 검증을 우회해 DB 에 직접 심는다 (레거시 데이터 상황 재현)
    with backend_app.db_conn() as conn:
        conn.execute("INSERT INTO users (username, password_hash, is_allowed) "
                     "VALUES ('../../../evil', 'x', 1)")
        conn.commit()

    outside = tmp_path / 'evil'
    outside.mkdir(parents=True, exist_ok=True)
    victim = outside / 'important.txt'
    victim.write_text('건드리면 안 됨')
    old = time.time() - 30 * 86400
    os.utime(victim, (old, old))   # 7일보다 오래됨 → 예전 코드면 지워졌다

    calls = [0]
    def side_effect(*a):
        calls[0] += 1
        if calls[0] > 1:
            raise RuntimeError('Break Loop')
    with patch('time.sleep', side_effect=side_effect):
        with backend_app.app.app_context():
            try:
                backend_app.auto_backup_job()
            except RuntimeError:
                pass

    assert victim.exists(), '경로 탈출로 서버 파일이 삭제됐다'


# ══════════════════════════════════════════════════════════════
# 보안: 비밀번호 정책
# ══════════════════════════════════════════════════════════════
def test_validate_password_rules():
    assert backend_app.validate_password('Passw0rd!') is None
    assert backend_app.validate_password('abcd1234') is None          # 소문자+숫자
    assert '8자' in backend_app.validate_password('Ab1!')             # 너무 짧음
    assert '두 종류' in backend_app.validate_password('abcdefghij')   # 소문자만
    assert '너무 깁니다' in backend_app.validate_password('Ab1' + 'x' * 300)
    assert '아이디와' in backend_app.validate_password('Testuser1', 'testuser1')


def test_signup_rejects_weak_password(client):
    res = client.post('/signup', data={
        'username': 'weakuser', 'password': '1234', 'password_confirm': '1234'})
    assert '8자 이상' in res.get_data(as_text=True)
    with backend_app.db_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_change_password_enforces_policy(client):
    client.post('/signup', data={'username': 'polic', 'password': 'Passw0rd!',
                                 'password_confirm': 'Passw0rd!'})
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'polic'
        sess['expires_at'] = time.time() + 3600

    res = client.post('/api/change_password',
                      json={'current_password': 'Passw0rd!', 'new_password': 'abc'})
    assert res.status_code == 400 and '8자 이상' in res.get_json()['error']

    res = client.post('/api/change_password',
                      json={'current_password': 'Passw0rd!', 'new_password': 'Passw0rd!'})
    assert res.status_code == 400 and '같습니다' in res.get_json()['error']


def test_signup_is_rate_limited(client):
    """가입 시도를 IP 당 시간 제한한다 (계정 대량 생성 방지)."""
    made = 0
    for i in range(backend_app.SIGNUP_MAX_PER_HOUR + 2):
        res = client.post('/signup', data={
            'username': f'ratelimit{i}', 'password': 'Passw0rd!',
            'password_confirm': 'Passw0rd!'})
        if '너무 잦' in res.get_data(as_text=True):
            break
        made += 1
    assert made == backend_app.SIGNUP_MAX_PER_HOUR


# ══════════════════════════════════════════════════════════════
# 보안: 비밀번호 변경 시 세션 무효화
# ══════════════════════════════════════════════════════════════
def _signup_and_login(client, username, password):
    client.post('/signup', data={'username': username, 'password': password,
                                 'password_confirm': password})
    with backend_app.db_conn() as conn:
        conn.execute("UPDATE users SET is_allowed = 1 WHERE username = ?", (username,))
        conn.commit()
    return client.post('/login', data={'username': username, 'password': password})


def test_password_change_invalidates_other_sessions(app, client):
    """다른 기기(다른 클라이언트)의 세션이 비밀번호 변경 즉시 끊긴다."""
    _signup_and_login(client, 'sessuser', 'Passw0rd!')

    attacker = app.test_client()
    attacker.post('/login', data={'username': 'sessuser', 'password': 'Passw0rd!'})
    assert attacker.get('/api/data').status_code == 200   # 아직 유효

    res = client.post('/api/change_password',
                      json={'current_password': 'Passw0rd!', 'new_password': 'Br4ndNew!'})
    assert res.status_code == 200
    assert res.get_json()['sessions_invalidated'] is True

    # 침입자 세션은 끊기고, 비밀번호를 바꾼 본인 세션은 유지된다
    assert attacker.get('/api/data').status_code == 401
    assert client.get('/api/data').status_code == 200


def test_password_change_can_revoke_api_keys(client):
    _signup_and_login(client, 'keyuser', 'Passw0rd!')
    trading_api.create_api_key('keyuser')

    res = client.post('/api/change_password',
                      json={'current_password': 'Passw0rd!', 'new_password': 'Br4ndNew!'})
    # 기본값은 유지 — 봇이 조용히 멈추지 않도록
    assert res.get_json()['api_keys_revoked'] == 0
    assert res.get_json()['api_keys_remaining'] == 1

    res = client.post('/api/change_password',
                      json={'current_password': 'Br4ndNew!', 'new_password': 'Th1rdOne!',
                            'revoke_api_keys': True})
    assert res.get_json()['api_keys_revoked'] == 1
    assert res.get_json()['api_keys_remaining'] == 0


# ══════════════════════════════════════════════════════════════
# 보안: 계정 단위 로그인 잠금
# ══════════════════════════════════════════════════════════════
def test_account_lockout_after_repeated_failures(app, client):
    """IP 를 바꿔도 계정 단위로 잠긴다."""
    client.post('/signup', data={'username': 'lockme', 'password': 'Passw0rd!',
                                 'password_confirm': 'Passw0rd!'})
    for i in range(backend_app.USER_LOCKOUT_THRESHOLD):
        c = app.test_client()   # 매번 다른 세션
        c.post('/login', data={'username': 'lockme', 'password': 'Wr0ngPass!'},
               environ_overrides={'REMOTE_ADDR': f'10.0.0.{i + 1}'})

    fresh = app.test_client()
    res = fresh.post('/login', data={'username': 'lockme', 'password': 'Passw0rd!'},
                     environ_overrides={'REMOTE_ADDR': '10.9.9.9'})
    assert '잠겨 있습니다' in res.get_data(as_text=True)


# ══════════════════════════════════════════════════════════════
# 보안: 관리자 임시 비밀번호
# ══════════════════════════════════════════════════════════════
def test_admin_reset_issues_strong_temp_password(client):
    client.post('/signup', data={'username': 'adminx', 'password': 'Passw0rd!',
                                 'password_confirm': 'Passw0rd!'})
    client.post('/signup', data={'username': 'victim', 'password': 'Passw0rd!',
                                 'password_confirm': 'Passw0rd!'})
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'adminx'
        sess['is_admin'] = True
        sess['expires_at'] = time.time() + 3600

    res = client.post('/api/admin/users/victim/reset_password')
    assert res.status_code == 200
    temp = res.get_json()['new_password']
    assert len(temp) == 12                      # 예전 uuid4[:8] 대비 강화
    assert not set(temp) & set('0O1lI')         # 혼동 문자 제외

    with backend_app.db_conn() as conn:
        row = conn.execute("SELECT must_change_password FROM users WHERE username='victim'").fetchone()
    assert row['must_change_password'] == 1     # 다음 로그인에서 변경 강제


def test_generate_temp_password_is_random():
    a = backend_app.generate_temp_password()
    b = backend_app.generate_temp_password()
    assert a != b and len(a) == 12


# ══════════════════════════════════════════════════════════════
# 기능: 비밀번호 재설정 요청 (로그인 화면 → 관리자)
# ══════════════════════════════════════════════════════════════
def test_reset_request_is_public_and_recorded(client):
    client.post('/signup', data={'username': 'forgetful', 'password': 'Passw0rd!',
                                 'password_confirm': 'Passw0rd!'})
    # 로그인하지 않은 상태에서 호출 가능해야 한다
    res = client.post('/request_password_reset',
                      json={'username': 'forgetful', 'note': '휴대폰 바꿨어요'})
    assert res.status_code == 200
    with backend_app.db_conn() as conn:
        row = conn.execute("SELECT * FROM password_reset_requests").fetchone()
    assert row['username'] == 'forgetful'
    assert row['note'] == '휴대폰 바꿨어요'


def test_reset_request_hides_account_existence(client):
    """없는 계정이어도 응답이 같아야 한다 (계정 존재 여부 탐색 방지)."""
    client.post('/signup', data={'username': 'realuser', 'password': 'Passw0rd!',
                                 'password_confirm': 'Passw0rd!'})
    a = client.post('/request_password_reset', json={'username': 'realuser'})
    b = client.post('/request_password_reset', json={'username': 'ghostuser'})
    assert a.status_code == b.status_code == 200
    assert a.get_json() == b.get_json()
    with backend_app.db_conn() as conn:
        names = [r['username'] for r in conn.execute(
            "SELECT username FROM password_reset_requests").fetchall()]
    assert names == ['realuser']   # 없는 계정은 요청함에 쌓이지 않는다


def test_reset_request_is_deduplicated_and_counted(client):
    client.post('/signup', data={'username': 'again', 'password': 'Passw0rd!',
                                 'password_confirm': 'Passw0rd!'})
    for _ in range(3):
        client.post('/request_password_reset', json={'username': 'again'})
    with backend_app.db_conn() as conn:
        rows = conn.execute("SELECT * FROM password_reset_requests").fetchall()
    assert len(rows) == 1 and rows[0]['request_count'] == 3


def test_reset_request_is_rate_limited(client):
    client.post('/signup', data={'username': 'spamtarget', 'password': 'Passw0rd!',
                                 'password_confirm': 'Passw0rd!'})
    codes = [client.post('/request_password_reset', json={'username': 'spamtarget'}).status_code
             for _ in range(backend_app.RESET_REQUEST_MAX_PER_HOUR + 2)]
    assert 429 in codes


def test_admin_sees_reset_requests_and_reset_clears_them(client):
    client.post('/signup', data={'username': 'adminy', 'password': 'Passw0rd!',
                                 'password_confirm': 'Passw0rd!'})
    client.post('/signup', data={'username': 'lostpw', 'password': 'Passw0rd!',
                                 'password_confirm': 'Passw0rd!'})
    client.post('/request_password_reset', json={'username': 'lostpw', 'note': '메모'})

    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'adminy'
        sess['is_admin'] = True
        sess['expires_at'] = time.time() + 3600

    users = {u['username']: u for u in client.get('/api/admin/users').get_json()}
    assert users['lostpw']['reset_requested_at'] is not None
    assert users['lostpw']['reset_note'] == '메모'
    assert client.get('/api/me').get_json()['reset_request_count'] == 1

    client.post('/api/admin/users/lostpw/reset_password')
    with backend_app.db_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM password_reset_requests").fetchone()[0] == 0


def test_admin_can_dismiss_reset_request_without_reset(client):
    client.post('/signup', data={'username': 'adminz', 'password': 'Passw0rd!',
                                 'password_confirm': 'Passw0rd!'})
    client.post('/signup', data={'username': 'mistake', 'password': 'Passw0rd!',
                                 'password_confirm': 'Passw0rd!'})
    client.post('/request_password_reset', json={'username': 'mistake'})

    with backend_app.db_conn() as conn:
        before = conn.execute(
            "SELECT password_hash FROM users WHERE username='mistake'").fetchone()['password_hash']

    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'adminz'
        sess['is_admin'] = True
        sess['expires_at'] = time.time() + 3600

    assert client.delete('/api/admin/password_resets/mistake').status_code == 200
    with backend_app.db_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM password_reset_requests").fetchone()[0] == 0
        after = conn.execute(
            "SELECT password_hash FROM users WHERE username='mistake'").fetchone()['password_hash']
    assert before == after   # 비밀번호는 그대로


def test_reset_request_requires_no_login_but_admin_list_does(client):
    """요청은 공개, 조회/해제는 관리자 전용."""
    assert client.post('/request_password_reset', json={'username': 'x'}).status_code == 200
    assert client.get('/api/admin/users').status_code == 401
    assert client.delete('/api/admin/password_resets/x').status_code == 401


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


# ══════════════════════════════════════════════════════════════
# 초기화는 비밀번호만 바꾼다 (다른 데이터 보존)
# ══════════════════════════════════════════════════════════════
def test_admin_reset_only_touches_credentials(client):
    """비밀번호 초기화가 매매기록·API 키·환경설정을 건드리지 않는다."""
    client.post('/signup', data={'username': 'keepdata', 'password': 'Passw0rd!',
                                 'password_confirm': 'Passw0rd!'})
    client.post('/signup', data={'username': 'adminq', 'password': 'Passw0rd!',
                                 'password_confirm': 'Passw0rd!'})
    with backend_app.db_conn() as conn:
        c = conn.cursor()
        for i in range(3):
            entry_logic.insert_entry(c, 'keepdata', {
                'id': 7000 + i, 'type': 'trade', 'stockName': f'종목{i}',
                'tradeType': '매수', 'price': 100 * (i + 1), 'quantity': 5,
                'rawDate': '2025-03-01T09:00', 'thoughts': f'<p>메모{i}</p>'})
        c.execute("UPDATE users SET preferences = ? WHERE username = ?",
                  ('{"showCurrentPrice": true}', 'keepdata'))
        conn.commit()
    trading_api.create_api_key('keepdata')

    def snapshot():
        with backend_app.db_conn() as conn:
            entries = [tuple(r) for r in conn.execute(
                "SELECT id, stockName, price, quantity, thoughts FROM entries "
                "WHERE username='keepdata' ORDER BY id").fetchall()]
            keys = [tuple(r) for r in conn.execute(
                "SELECT id, key_hash, revoked_at FROM api_keys WHERE username='keepdata'").fetchall()]
            prefs = conn.execute(
                "SELECT preferences FROM users WHERE username='keepdata'").fetchone()['preferences']
        return entries, keys, prefs

    before = snapshot()

    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'adminq'
        sess['is_admin'] = True
        sess['expires_at'] = time.time() + 3600
    assert client.post('/api/admin/users/keepdata/reset_password').status_code == 200

    after = snapshot()
    assert before[0] == after[0], '매매기록이 변경됐다'
    assert before[1] == after[1], 'API 키가 변경됐다'
    assert before[2] == after[2], '환경설정이 변경됐다'


# ══════════════════════════════════════════════════════════════
# 배지: 처리하면 저절로 사라진다
# ══════════════════════════════════════════════════════════════
def test_badge_counts_clear_after_handling(client):
    """승인·초기화를 끝내면 /api/me 의 배지 근거 수치가 0 이 된다."""
    client.post('/signup', data={'username': 'adminb', 'password': 'Passw0rd!',
                                 'password_confirm': 'Passw0rd!'})
    client.post('/signup', data={'username': 'newbie', 'password': 'Passw0rd!',
                                 'password_confirm': 'Passw0rd!'})
    client.post('/request_password_reset', json={'username': 'newbie', 'note': '메모'})

    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'adminb'
        sess['is_admin'] = True
        sess['expires_at'] = time.time() + 3600

    me = client.get('/api/me').get_json()
    assert me['pending_count'] == 1 and me['reset_request_count'] == 1

    client.post('/api/admin/users/newbie/toggle_allow')     # 가입 승인
    client.post('/api/admin/users/newbie/reset_password')   # 초기화 처리

    me = client.get('/api/me').get_json()
    assert me['pending_count'] == 0 and me['reset_request_count'] == 0


def test_admin_list_exposes_reset_note_for_display(client):
    """관리자 표가 메모를 그릴 수 있도록 API 가 메모를 실어 보낸다."""
    client.post('/signup', data={'username': 'adminc', 'password': 'Passw0rd!',
                                 'password_confirm': 'Passw0rd!'})
    client.post('/signup', data={'username': 'noteuser', 'password': 'Passw0rd!',
                                 'password_confirm': 'Passw0rd!'})
    client.post('/request_password_reset',
                json={'username': 'noteuser', 'note': '폰을 바꿔서 비밀번호를 잊었습니다'})

    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'adminc'
        sess['is_admin'] = True
        sess['expires_at'] = time.time() + 3600

    users = {u['username']: u for u in client.get('/api/admin/users').get_json()}
    row = users['noteuser']
    assert row['reset_note'] == '폰을 바꿔서 비밀번호를 잊었습니다'
    assert row['reset_requested_at']
    assert row['reset_request_count'] == 1
