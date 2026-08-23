"""웹 화면용 REST API — 기록 CRUD·설정·계좌 매핑·시세/뉴스 위임.

대상: api.py.
"""

import json
import os
import time
from unittest.mock import MagicMock, patch


import backend_app
import config
from helpers import _ensure_user, _login


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

def test_uploaded_file_success(client):
    """정상적으로 권한이 있는 사용자의 파일 다운로드가 동작하는지 테스트합니다."""
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'admin'
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)
        
    user_dir = os.path.join(config.UPLOAD_FOLDER, 'admin')
    os.makedirs(user_dir, exist_ok=True)
    test_file_path = os.path.join(user_dir, 'test_download.txt')
    with open(test_file_path, 'w') as f:
        f.write('file_content_test')
        
    res = client.get('/uploads/admin/test_download.txt')
    assert res.status_code == 200
    assert b'file_content_test' in res.data

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
    with open(config.DATA_FILE, 'w', encoding='utf-8') as f:
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
        
    if os.path.exists(config.DATA_FILE):
        os.remove(config.DATA_FILE)

# ---------------------------------------------------------------------------
# 계좌 매핑 DB 이관 (예전에는 json/<username>/account_info.json 파일이었다)
# ---------------------------------------------------------------------------

def test_mappings_persist_in_db_not_files(client, tmp_path, monkeypatch):
    """매핑 저장이 json/ 폴더에 파일을 남기지 않아야 한다."""
    monkeypatch.setattr(config, 'JSON_DIR', str(tmp_path))
    _login(client, 'mapuser')
    _ensure_user('mapuser')

    payload = {"brokers": {"243": "한국투자증권"},
               "accounts": {"1111-2222": {"alias": "주계좌"}}}
    assert client.post('/api/mappings', json=payload).status_code == 200

    assert client.get('/api/mappings').json == payload
    assert not os.path.exists(os.path.join(str(tmp_path), 'mapuser'))

def test_mappings_rejects_unknown_account(client):
    """계정 행이 없으면 저장된 척하지 말고 404 로 알려야 한다."""
    _login(client, 'ghostuser')
    res = client.post('/api/mappings', json={"brokers": {}, "accounts": {}})
    assert res.status_code == 404
