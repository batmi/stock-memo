"""백그라운드 작업 — 자동 백업.

대상: jobs.py.
"""

import json
import os
import time
import zipfile
from unittest.mock import patch


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
