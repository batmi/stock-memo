"""백그라운드 작업 — 자동 백업.

대상: jobs.py.
"""

import json
import os
import time
import zipfile
from unittest.mock import patch


import accounts
import backend_app
import config
import jobs


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
    monkeypatch.setattr(config, 'BACKUP_DIR', str(tmp_path / 'backup'))

    # 1. 테스트 유저 및 매매 기록 생성
    client.post('/signup', data={'username': 'autobackupuser', 'password': 'Passw0rd!', 'password_confirm': 'Passw0rd!'})
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'autobackupuser'
        sess['expires_at'] = time.time() + 3600  # 세션 절대 만료 시각(check_login 이 요구)
        
    client.post('/api/entry', json={"type": "buy", "stockName": "자동백업테스트", "price": 10000, "quantity": 1})

    backup_dir = os.path.join(config.BACKUP_DIR, 'autobackupuser')
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
            jobs.auto_backup_job()
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

@patch('time.sleep')
def test_auto_backup_includes_account_mappings(mock_sleep, client, app, tmp_path, monkeypatch):
    """자동 백업 ZIP 에도 계좌 매핑(account_info.json)이 들어가야 한다.

    ⭐️ 예전에는 기록을 읽은 연결을 곧바로 닫아 놓고, ZIP 을 쓰는 아래쪽에서 그
       **닫힌 연결**로 accounts.load(conn, ...) 를 불렀다. load() 는 조회 실패를
       '매핑 없음' 으로 삼키므로(그게 맞는 설계다) 예외도 로그도 없이 빈 매핑이
       돌아왔고, 자동 백업 ZIP 에서만 account_info.json 이 조용히 빠졌다.
       수동 백업은 멀쩡했기 때문에 "복원했더니 계좌 매핑만 사라졌다" 로 나타난다.
    """
    monkeypatch.setattr(config, 'BACKUP_DIR', str(tmp_path / 'backup'))

    client.post('/signup', data={'username': 'mapbackup', 'password': 'Passw0rd!',
                                 'password_confirm': 'Passw0rd!'})
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'mapbackup'
        sess['expires_at'] = time.time() + 3600

    mappings = {'brokers': {'KI': '한국투자'},
                'accounts': {'12345678-01': {'broker_name': '한국투자', 'alias': '주력'}}}
    with backend_app.db_conn() as conn:
        accounts.save(conn, 'mapbackup', mappings)
        conn.commit()
    client.post('/api/entry', json={"type": "buy", "stockName": "매핑백업", "price": 1, "quantity": 1})

    sleep_calls = [0]

    def side_effect(*args):
        sleep_calls[0] += 1
        if sleep_calls[0] > 1:
            raise RuntimeError("Break Loop")
    mock_sleep.side_effect = side_effect

    with app.app_context():
        try:
            jobs.auto_backup_job()
        except RuntimeError:
            pass

    backup_dir = os.path.join(config.BACKUP_DIR, 'mapbackup')
    zip_files = [f for f in os.listdir(backup_dir) if f.endswith('.zip')]
    assert len(zip_files) == 1
    with zipfile.ZipFile(os.path.join(backup_dir, zip_files[0])) as zf:
        assert accounts.BACKUP_ARCNAME in zf.namelist(), (
            f"자동 백업에 계좌 매핑이 빠졌습니다: {zf.namelist()}")
        saved = json.loads(zf.read(accounts.BACKUP_ARCNAME).decode('utf-8'))
    assert saved['brokers'] == {'KI': '한국투자'}
    assert '12345678-01' in saved['accounts']


def test_backup_job_skips_unsafe_username(client, tmp_path, monkeypatch):
    """규칙 이전에 만들어진 이상한 이름의 계정은 백업 잡이 파일을 건드리지 않는다."""
    monkeypatch.setattr(config, 'BACKUP_DIR', str(tmp_path / 'backup'))
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
                jobs.auto_backup_job()
            except RuntimeError:
                pass

    assert victim.exists(), '경로 탈출로 서버 파일이 삭제됐다'


@patch('threading.Thread.start')
def test_start_all(mock_start):
    jobs.start_all()
    assert mock_start.call_count == 2


@patch('time.sleep')
def test_auto_backup_job_with_uploads_and_errors(mock_sleep, client, app, tmp_path, monkeypatch):
    """
    업로드 파일이 있을 때 백업에 포함되는지 확인하고,
    예외 상황(검증 실패, 내부 에러 등)에 대한 분기를 커버합니다.
    """
    monkeypatch.setattr(config, 'BACKUP_DIR', str(tmp_path / 'backup'))
    monkeypatch.setattr(config, 'UPLOAD_FOLDER', str(tmp_path / 'uploads'))
    
    client.post('/signup', data={'username': 'fullbackupuser', 'password': 'Passw0rd!', 'password_confirm': 'Passw0rd!'})
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'fullbackupuser'
        sess['expires_at'] = time.time() + 3600
        
    client.post('/api/entry', json={"type": "buy", "stockName": "풀업테스트", "price": 10000, "quantity": 1})
    
    upload_dir = os.path.join(config.UPLOAD_FOLDER, 'fullbackupuser')
    os.makedirs(upload_dir, exist_ok=True)
    with open(os.path.join(upload_dir, 'test_image.jpg'), 'w') as f:
        f.write("fake image data")

    sleep_calls = [0]
    def side_effect(*args):
        sleep_calls[0] += 1
        if sleep_calls[0] > 1:
            raise RuntimeError("Break Loop")
    mock_sleep.side_effect = side_effect

    # 검증 실패 상황을 위해 verify_backup_zip 모킹
    with patch('jobs.verify_backup_zip', return_value=(False, "Verification failed test")):
        with app.app_context():
            try:
                jobs.auto_backup_job()
            except RuntimeError:
                pass
                
    backup_dir = os.path.join(config.BACKUP_DIR, 'fullbackupuser')
    zip_files = [f for f in os.listdir(backup_dir) if f.endswith('.zip')]
    assert len(zip_files) == 1
    with zipfile.ZipFile(os.path.join(backup_dir, zip_files[0]), 'r') as zf:
        assert 'uploads/test_image.jpg' in zf.namelist()

    # 에러 블록 커버를 위해 DB 연결 오류 모킹
    sleep_calls[0] = 0
    with patch('jobs.db_conn', side_effect=Exception("DB Error test")):
        with app.app_context():
            try:
                jobs.auto_backup_job()
            except RuntimeError:
                pass


@patch('jobs.datetime')
@patch('time.sleep')
def test_auto_fetch_nxt_close_job(mock_sleep, mock_datetime, client, app):
    """
    시간외 단일가 수집 스레드가 평일 동작 시간대/비동작 시간대에 따라 
    각각 어떻게 동작하는지 테스트합니다.
    """
    from datetime import datetime, timezone
    
    # 1. 동작 시간대: UTC 7:00 => KST 16:00 (월요일 평일)
    mock_datetime.now.return_value = datetime(2023, 1, 2, 7, 0, tzinfo=timezone.utc)
    mock_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
    
    sleep_calls = [0]
    def side_effect_sleep(*args):
        sleep_calls[0] += 1
        if sleep_calls[0] > 1:
            raise RuntimeError("Break")
    mock_sleep.side_effect = side_effect_sleep
    
    client.post('/signup', data={'username': 'nxt_user', 'password': 'Passw0rd!', 'password_confirm': 'Passw0rd!'})
    client.post('/api/entry', json={"type": "buy", "stockName": "삼성전자", "stockCode": "005930", "price": 10000, "quantity": 1})
    
    with patch('prices.fetch_nxt_close', return_value=11000) as mock_fetch:
        with patch('prices.is_market_holiday', return_value=False):
            with app.app_context():
                try:
                    jobs.auto_fetch_nxt_close_job()
                except RuntimeError:
                    pass
        assert mock_fetch.called

    # 2. 비동작 시간대: KST 19:00
    mock_datetime.now.return_value = datetime(2023, 1, 2, 10, 0, tzinfo=timezone.utc)
    sleep_calls[0] = 0
    with app.app_context():
        try:
            jobs.auto_fetch_nxt_close_job()
        except RuntimeError:
            pass

    # Exception catch 커버리지
    sleep_calls[0] = 0
    mock_datetime.now.side_effect = Exception("Time Error Test")
    with app.app_context():
        try:
            jobs.auto_fetch_nxt_close_job()
        except RuntimeError:
            pass
