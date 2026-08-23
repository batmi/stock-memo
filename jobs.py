"""백그라운드 스레드 — 매일 자동 백업, 시간외 단일가(NXT) 종가 캐싱.

요청 처리와 완전히 다른 생명주기를 가진 코드다. 요청 컨텍스트가 없으므로
`session`·`request`·`current_app` 을 쓸 수 없고, 로거도 `app.logger` 대신
모듈 로거를 써야 한다. 라우트와 같은 파일에 있으면 그 제약이 보이지 않아
"요청 핸들러에서 되던 코드"를 그대로 옮겨 오는 사고가 난다.

`start_all()` 은 서버 프로세스가 직접 실행될 때만 호출한다 (backend_app 하단).
"""

import logging
import os
import threading
import time
import zipfile
import json
from datetime import datetime, timedelta, timezone

import accounts
import config
import prices
from backups import verify_backup_zip
from db import db_conn, get_db
from users import user_dir

log = logging.getLogger('jobs')


def start_all():
    """데몬 스레드로 백그라운드 작업을 띄운다. 프로세스 종료를 막지 않는다."""
    threading.Thread(target=auto_backup_job, daemon=True, name='auto-backup').start()
    threading.Thread(target=auto_fetch_nxt_close_job, daemon=True, name='nxt-close').start()


# ⭐️ 자동 백업 스레드 함수
def auto_backup_job():
    while True:
        now = datetime.now()
        # ⭐️ 다음 새벽 3시 계산 — 자정에는 로그 로테이션과 겹쳐 라즈베리파이의
        #    CPU/SD카드 I/O 부하가 집중되므로 사용이 없는 새벽 시간대로 분산한다.
        next_run = datetime(now.year, now.month, now.day, 3, 0)
        if next_run <= now:
            next_run += timedelta(days=1)
        time_to_sleep = (next_run - now).total_seconds()
        time.sleep(time_to_sleep)

        try:
            log.info("🔄 일일 자동 백업을 시작합니다.")
            with db_conn() as conn:
                users = conn.execute("SELECT username FROM users").fetchall()

            for user in users:
                username = user['username']

                # ⭐️ 기록과 계좌 매핑을 **같은 연결에서** 읽는다.
                #    예전에는 기록만 읽고 곧바로 conn.close() 한 뒤, ZIP 을 쓰는
                #    한참 아래에서 그 닫힌 연결로 accounts.load(conn, ...) 를 불렀다.
                #    load() 는 조회 실패를 '매핑 없음'으로 삼키도록 되어 있어(그게
                #    맞는 설계다) 아무 소리 없이 빈 매핑이 돌아왔고, 결과적으로
                #    **자동 백업 ZIP 에서만 account_info.json 이 통째로 빠졌다.**
                #    수동 백업(backup_api)은 with db_conn() 을 써서 멀쩡했기 때문에
                #    "백업은 되는데 복원하면 계좌 매핑만 사라지는" 형태로 나타난다.
                with db_conn() as conn:
                    rows = [dict(row) for row in conn.execute(
                        "SELECT * FROM entries WHERE username = ?", (username,)).fetchall()]
                    mappings = accounts.load(conn, username)

                user_backup_dir = user_dir(config.BACKUP_DIR, username)
                if user_backup_dir is None:
                    continue  # 규칙 이전에 만들어진 이상한 이름 — 파일을 건드리지 않는다
                os.makedirs(user_backup_dir, exist_ok=True)

                current_time_str = time.strftime('%Y%m%d')
                filename = f'TradingJournal_backup_{username}_{current_time_str}.zip'
                filepath = os.path.join(user_backup_dir, filename)

                with zipfile.ZipFile(filepath, 'w', zipfile.ZIP_DEFLATED) as zf:
                    json_data = json.dumps(rows, ensure_ascii=False, indent=2)
                    zf.writestr('data.json', json_data)

                    user_folder = user_dir(config.UPLOAD_FOLDER, username)
                    if user_folder and os.path.exists(user_folder):
                        for root, _dirs, files in os.walk(user_folder):
                            for file in files:
                                file_path = os.path.join(root, file)
                                arcname = os.path.join('uploads', file)
                                zf.write(file_path, arcname=arcname)
                                
                    # 계좌 매핑은 DB 에 있지만, 백업 ZIP 안에서는 구버전과 같은
                    # account_info.json 이름을 유지한다 (예전 백업과 호환).
                    if mappings.get('brokers') or mappings.get('accounts'):
                        zf.writestr(accounts.BACKUP_ARCNAME, accounts.dumps(mappings))

                # ⭐️ 생성된 백업 파일의 무결성을 즉시 검증 (복원 가능 여부 확인)
                ok, detail = verify_backup_zip(filepath, len(rows))
                if ok:
                    log.info(f"  └ 백업 검증 통과: {username} ({detail})")
                else:
                    log.error(f"  └ ⚠️ 백업 검증 실패: {username} - {detail} (파일: {filename})")

                # 7일 지난 백업 파일 삭제 (7일 = 604800초)
                current_time_sec = time.time()
                for f in os.listdir(user_backup_dir):
                    f_path = os.path.join(user_backup_dir, f)
                    if os.path.isfile(f_path):
                        if os.stat(f_path).st_mtime < current_time_sec - 7 * 86400:
                            os.remove(f_path)

            log.info("✅ 일일 자동 백업이 완료되었습니다.")
        except Exception as e:
            log.error(f"❌ 자동 백업 중 오류 발생: {e}")


# ⭐️ 시간외 단일가(NXT) 종가를 자동 갱신하는 백그라운드 스레드 함수
#    시세 조회 자체는 prices 모듈에 위임한다. 예전에는 이 함수가 네이버 모바일
#    API 를 urllib 로 따로 호출해 헤더·타임아웃·파싱이 prices.py 와 이중으로
#    존재했고, 네이버 응답 스펙이 바뀌면 두 곳을 모두 고쳐야 했다.
def auto_fetch_nxt_close_job():
    while True:
        try:
            # 10분(600초) 단위로 동작
            time.sleep(600)

            # 한국 시간(KST) 기준 시간 계산
            kst_now = datetime.now(timezone.utc) + timedelta(hours=9)
            time_num = kst_now.hour * 100 + kst_now.minute
            day_of_week = kst_now.weekday()  # 0: 월, 1: 화, ..., 4: 금, 5: 토, 6: 일

            # 평일(월~금) 15:30 ~ 18:30 (장 종료 후 시간외 단일가 운영 및 마감 직후 시간)에만 캐시 갱신 수행
            if not (0 <= day_of_week <= 4 and 1530 <= time_num <= 1830):
                continue
            # 휴장일에는 시간외 단일가도 없다 (prices 의 휴장일 목록과 판정을 공유)
            if (kst_now.year, kst_now.month, kst_now.day) in prices.holidays():
                continue

            log.info("🔄 백그라운드: 시간외 단일가(NXT) 자동 캐싱을 시작합니다...")
            conn = get_db()
            try:
                c = conn.cursor()
                c.execute("SELECT DISTINCT stockCode FROM entries WHERE stockCode IS NOT NULL AND stockCode != ''")
                codes = [row['stockCode'].strip().upper() for row in c.fetchall()]

                updated_count = 0
                for code in codes:
                    # 국내 주식(6자리 영숫자) 만 시간외 단일가 대상
                    if prices.detect_market(code) != 'KR':
                        continue
                    price_val = prices.fetch_nxt_close(code)
                    if price_val is not None:
                        prices.save_price_cache(conn, code, price_val, 'NXT')
                        updated_count += 1
                    # 네이버 서버에 부담을 주지 않기 위해 약간의 지연 시간 추가
                    time.sleep(0.3)
            finally:
                conn.close()
            log.info(f"✅ 백그라운드: 시간외 단일가 캐싱 완료 (총 {updated_count}개 종목 업데이트 됨)")
        except Exception as e:
            log.error(f"❌ 시간외 단일가 자동 캐싱 스레드 오류: {e}")
