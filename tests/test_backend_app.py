import pytest
import io
import zipfile
import json
import os
import backend_app
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
    client.post('/signup', data={'username': 'admin', 'password': 'pw', 'password_confirm': 'pw'})
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'testuser'

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
        'password': 'adminpassword',
        'password_confirm': 'adminpassword'
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
        'password': 'adminpassword'
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
    client.post('/signup', data={'username': 'admin', 'password': 'pw', 'password_confirm': 'pw'})
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'admin'
        sess['is_admin'] = True
        
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
        
    res_forbidden = client.get('/api/admin/users')
    assert res_forbidden.status_code == 403
    
    # 최고 관리자 세션
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'admin'
        sess['is_admin'] = True
        
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
    client.post('/signup', data={'username': 'admin', 'password': 'pw', 'password_confirm': 'pw'})
    for _ in range(5):
        res = client.post('/login', data={'username': 'admin', 'password': 'wrong_password'})
    
    # 6번째 시도 시 차단 메시지 확인
    res = client.post('/login', data={'username': 'admin', 'password': 'wrong_password'})
    assert '차단' in res.get_data(as_text=True)

def test_unallowed_user_login(client):
    """
    관리자 승인이 안 된(is_allowed=0) 계정의 로그인 차단 테스트입니다.
    """
    client.post('/signup', data={'username': 'admin', 'password': 'pw', 'password_confirm': 'pw'})
    client.post('/signup', data={'username': 'wait_user', 'password': 'pw1', 'password_confirm': 'pw1'})
    res = client.post('/login', data={'username': 'wait_user', 'password': 'pw1'})
    assert '승인이 필요' in res.get_data(as_text=True)

def test_admin_edge_cases(client):
    """
    관리자 기능의 각종 예외 상황(최고 관리자 삭제/변경 방지 등)을 테스트합니다.
    """
    client.post('/signup', data={'username': 'admin', 'password': 'pw', 'password_confirm': 'pw'})
    client.post('/signup', data={'username': 'user2', 'password': 'pw', 'password_confirm': 'pw'})
    
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'admin'
        sess['is_admin'] = True
        
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
    client.post('/signup', data={'username': 'normal', 'password': 'pw1', 'password_confirm': 'pw1'})
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'admin'
        sess['is_admin'] = True
    client.post('/api/admin/users/normal/toggle_allow')
    
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'normal'
        sess['is_admin'] = False
        
    # 1. 비밀번호 변경 - 현재 비밀번호 오입력
    res = client.post('/api/change_password', json={'current_password': 'pw2', 'new_password': 'pw3'})
    assert res.status_code == 400
    
    # 2. 회원 탈퇴 - 비밀번호 오입력
    res = client.delete('/api/account', json={'password': 'wrong'})
    assert res.status_code == 400
    
    # 3. 회원 탈퇴 - 정상
    res = client.delete('/api/account', json={'password': 'pw1'})
    assert res.status_code == 200
    
    # 4. 최고 관리자 탈퇴 시도 방어
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'admin'
        sess['is_admin'] = True
    res = client.delete('/api/account', json={'password': 'pw'})
    assert res.status_code == 403

def test_restore_exceptions(client):
    """
    백업 복원 시 발생할 수 있는 에러 상황(파일 누락, 잘못된 형식 등)을 테스트합니다.
    """
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'testuser'
        
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

def test_image_upload_and_access(client):
    """
    Base64 이미지 업로드 처리와 사용자 간 격리된 첨부파일 접근 제어를 테스트합니다.
    """
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'imguser'
        
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
    res_file_unauth = client.get(f"/uploads/imguser/{filename}")
    assert res_file_unauth.status_code == 403

@patch('urllib.request.urlopen')
def test_mock_external_apis(mock_urlopen, client):
    """
    외부 API(네이버 주가, 구글 뉴스) 통신을 Mocking하여 네트워크 연결 없이 정상 로직을 테스트합니다.
    """
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'testuser'
        
    mock_res = MagicMock()
    # 국내 주식과 해외 주식 파싱에 모두 통과할 수 있는 다목적 더미 JSON 구조 생성
    mock_res.read.return_value = b'{"closePrice": "80,000", "chart": {"result": [{"meta": {"regularMarketPrice": 150.0}}]}}'
    mock_res.__enter__.return_value = mock_res
    mock_urlopen.return_value = mock_res
    
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
        
    client.post('/signup', data={'username': 'admin_mig', 'password': 'pw', 'password_confirm': 'pw'})
    
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
    
    res2 = client.post('/signup', data={'username': 'testuser', 'password': 'pw1', 'password_confirm': 'pw2'})
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
        
    user_dir = os.path.join(backend_app.UPLOAD_FOLDER, 'admin')
    os.makedirs(user_dir, exist_ok=True)
    test_file_path = os.path.join(user_dir, 'test_download.txt')
    with open(test_file_path, 'w') as f:
        f.write('file_content_test')
        
    res = client.get('/uploads/admin/test_download.txt')
    assert res.status_code == 200
    assert b'file_content_test' in res.data

@patch('urllib.request.urlopen')
def test_current_price_edge_cases(mock_urlopen, client):
    """현재 주가 API(/api/current_price)의 다양한 파싱 폴백 및 네트워크 에러 처리를 검증합니다."""
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'testuser'
        
    def create_mock_res(content):
        mock_res = MagicMock()
        mock_res.read.return_value = content
        mock_res.__enter__.return_value = mock_res
        return mock_res

    def urlopen_side_effect(req, timeout=3):
        url = req.full_url if hasattr(req, 'full_url') else req
        
        if 'M04020000' in url:
            if getattr(urlopen_side_effect, 'gold_fail_all', False):
                raise Exception("All Fail")
            raise Exception("Naver Gold API Fail")
        elif 'KRX_Gold_Market' in url:
            if getattr(urlopen_side_effect, 'gold_fail_all', False):
                raise Exception("All Fail")
            return create_mock_res(b"<th>\xed\x98\x84\xec\x9e\xac\xea\xb0\x80</th><td><strong>88,000</strong></td>")
        elif '005930' in url:
            if getattr(urlopen_side_effect, 'all_fail', False):
                raise Exception("All APIs Fail")
            if 'polling' in url:
                return create_mock_res(b'{"result": {"areas": [{"datas": [{"nv": 95000}]}]}}')
            return create_mock_res(b'{"closePrice": "0"}')
        elif 'ABCDEF' in url:
            return create_mock_res(b'{"chart": {"result": [{"meta": {"regularMarketPrice": 250.5}}]}}')
        elif '123456' in url:
            if 'yahoo' in url:
                return create_mock_res(b'{"chart": {"result": [{"meta": {"regularMarketPrice": 100.0}}]}}')
            raise Exception("Naver API Fail")
        elif 'AAPL' in url:
            raise Exception("All APIs Fail")
        return create_mock_res(b'')

    mock_urlopen.side_effect = urlopen_side_effect
    
    # 1. 금 주가: 기본 API 에러 -> KRX 크롤링 성공 패턴
    res_gold = client.post('/api/current_price', json={'codes': ['KRXGOLD']})
    assert res_gold.json.get('KRXGOLD') == 88000.0
    
    # 2. 금 주가: 모든 API 실패 시 None 반환
    urlopen_side_effect.gold_fail_all = True
    res_gold_fail = client.post('/api/current_price', json={'codes': ['KRXGOLD']})
    assert res_gold_fail.json.get('KRXGOLD') is None
    
    # 3. 네이버 주가: 기본 API 데이터 0 -> Polling API 우회 성공 패턴
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

    # 7. 일반 주식 모든 API 실패 시 500 에러 없이 안전하게 통과하는지 검증 (Unpacking 버그 회귀 방지)
    urlopen_side_effect.all_fail = True
    res_all_fail = client.post('/api/current_price', json={'codes': ['005930', 'AAPL']})
    assert res_all_fail.status_code == 200
    assert res_all_fail.json.get('005930') is None

@patch('urllib.request.urlopen')
def test_news_api_exceptions(mock_urlopen, client):
    """구글 뉴스 API 파싱 시 예외가 발생해도 시스템 중단 없이 빈 배열을 리턴하는지 테스트합니다."""
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'testuser'
        
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
        
    # 비밀번호 변경 - 빈 값 전송
    res_pw_empty = client.post('/api/change_password', json={})
    assert res_pw_empty.status_code == 400
    assert '모든 필드' in res_pw_empty.json['error']
    
    # 최고 관리자는 계정 삭제 API 접근 시 403 에러가 우선 발생하므로 일반 유저로 세션 변경
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'normal_user'
        sess['is_admin'] = False
        
    # 계정 삭제 - 빈 비밀번호 전송
    res_del_empty = client.delete('/api/account', json={})
    assert res_del_empty.status_code == 400
    assert '비밀번호를 입력' in res_del_empty.json['error']

@patch('time.sleep')
def test_auto_backup_job(mock_sleep, client, app):
    """
    자동 백업 스레드 함수가 실행될 때 ZIP 파일이 잘 생성되는지,
    그리고 7일이 지난 오래된 백업 파일이 정상적으로 삭제되는지(보관 주기) 테스트합니다.
    무한 루프를 탈출하기 위해 두 번째 time.sleep 호출 시 예외를 발생시킵니다.
    """
    # 1. 테스트 유저 및 매매 기록 생성
    client.post('/signup', data={'username': 'autobackupuser', 'password': 'pw', 'password_confirm': 'pw'})
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'autobackupuser'
        
    client.post('/api/entry', json={"type": "buy", "stockName": "자동백업테스트", "price": 10000, "quantity": 1})

    backup_dir = os.path.join(backend_app.BACKUP_DIR, 'autobackupuser')
    os.makedirs(backup_dir, exist_ok=True)
    
    # 2. 7일이 지난 가짜 백업 파일 생성 (os.utime으로 수정 시간 조작)
    old_file_path = os.path.join(backup_dir, 'TradingJournal_backup_autobackupuser_old.zip')
    with open(old_file_path, 'w') as f:
        f.write("old data")
        
    import time
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