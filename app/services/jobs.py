"""백그라운드 스레드 — 매일 자동 백업, 정규장 종가·NXT 애프터마켓 종가 캐싱.

요청 처리와 완전히 다른 생명주기를 가진 코드다. 요청 컨텍스트가 없으므로
`session`·`request`·`current_app` 을 쓸 수 없고, 로거도 `app.logger` 대신
모듈 로거를 써야 한다. 라우트와 같은 파일에 있으면 그 제약이 보이지 않아
"요청 핸들러에서 되던 코드"를 그대로 옮겨 오는 사고가 난다.

`start_all()` 은 서버 프로세스가 직접 실행될 때만 호출한다 (backend_app 하단).
"""

import logging
import os
import sqlite3
import threading
import time
import zipfile
import json
from datetime import datetime, timedelta, timezone

from app.services import accounts
import config
from app.services import prices
from app.services.backups import verify_backup_zip
from app.database.db import db_conn, get_db
from app.services.users import user_dir

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

        # ⭐️ 사용자별 ZIP 보다 먼저, DB 파일 자체를 떠 둔다. ZIP 에는 기록·첨부·계좌
        #    매핑만 들어가고 계정(비밀번호 해시)·API 키·환경설정은 없어서, journal.db 가
        #    손상되면 기록은 되살려도 계정은 전부 다시 만들어야 했다.
        try:
            snapshot_database()
        except Exception as e:
            log.error(f"❌ DB 스냅샷 실패: {e}")

        try:
            log.info("🔄 일일 자동 백업을 시작합니다.")
            with db_conn() as conn:
                users = conn.execute("SELECT username FROM users").fetchall()
        except Exception as e:
            log.error(f"❌ 자동 백업 중 오류 발생(계정 목록 조회): {e}")
            continue

        # ⭐️ 사용자마다 따로 감싼다. 예전에는 try 하나가 루프 전체를 감싸서, 한
        #    사용자에서 예외가 나면(깨진 첨부 파일, 권한 오류 등) 그 뒤 사용자 전원의
        #    백업과 7일 정리가 조용히 건너뛰어졌다. 로그에는 한 줄만 남는다.
        failed = []
        for user in users:
            username = user['username']
            try:
                _backup_user(username)
            except Exception as e:
                failed.append(username)
                log.error(f"  └ ❌ 자동 백업 실패: {username} - {e}")

        if failed:
            log.error(f"⚠️ 일일 자동 백업이 일부 실패했습니다 ({len(failed)}/{len(users)}명: "
                      f"{', '.join(failed)})")
        else:
            log.info("✅ 일일 자동 백업이 완료되었습니다.")


def _backup_user(username):
    """한 사용자의 기록·첨부·계좌 매핑을 ZIP 으로 백업하고 7일 지난 파일을 지운다."""
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
        return  # 규칙 이전에 만들어진 이상한 이름 — 파일을 건드리지 않는다
    os.makedirs(user_backup_dir, exist_ok=True)

    current_time_str = time.strftime('%Y%m%d')
    filename = f'TradingJournal_backup_{username}_{current_time_str}.zip'
    filepath = os.path.join(user_backup_dir, filename)

    # ⭐️ 임시 파일에 다 쓴 뒤 이름을 바꾼다. 쓰는 도중 프로세스가 죽어도 반쯤 쓰인
    #    ZIP 이 정식 백업 이름으로 남지 않는다.
    tmp_path = filepath + '.tmp'
    with zipfile.ZipFile(tmp_path, 'w', zipfile.ZIP_DEFLATED) as zf:
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

    os.replace(tmp_path, filepath)

    # ⭐️ 생성된 백업 파일의 무결성을 즉시 검증 (복원 가능 여부 확인)
    ok, detail = verify_backup_zip(filepath, len(rows))
    if not ok:
        # ⭐️ 검증에 실패하면 옛 백업을 정리하지 않는다. 예전에는 결과와 무관하게 7일 지난
        #    파일을 지워서, 백업이 일주일 내내 깨지면 남는 것이 깨진 백업뿐이었다.
        #    실패한 파일은 원인을 볼 수 있게 남기고, 이 사용자를 실패로 집계한다.
        raise RuntimeError(f"백업 검증 실패 - {detail} (파일: {filename}, 옛 백업은 보존)")
    log.info(f"  └ 백업 검증 통과: {username} ({detail})")
    _prune_older_than(user_backup_dir, BACKUP_RETENTION_DAYS)


# 자동 백업 보관 기간 (사용자별 ZIP 과 DB 스냅샷 공통)
BACKUP_RETENTION_DAYS = 7
# ⭐️ DB 스냅샷 폴더. 사용자명은 영문·숫자로 시작해야 하므로(users.USERNAME_RE) '_' 로
#    시작하는 이름은 어떤 계정의 백업 폴더와도 겹치지 않는다.
DB_SNAPSHOT_DIRNAME = '_db'


def _prune_older_than(folder, days, suffix=None):
    """folder 안에서 days 일보다 오래된 파일을 지운다 (suffix 가 있으면 그 확장자만)."""
    cutoff = time.time() - days * 86400
    for f in os.listdir(folder):
        f_path = os.path.join(folder, f)
        if not os.path.isfile(f_path) or (suffix and not f.endswith(suffix)):
            continue
        if os.stat(f_path).st_mtime < cutoff:
            os.remove(f_path)


def snapshot_database():
    """DB 파일의 일관된 사본을 backup/_db/journal_YYYYMMDD.db 로 남긴다. 경로를 돌려준다.

    파일을 그냥 복사하면 WAL 에 남은 변경분이 빠지거나 쓰는 도중의 상태가 찍힌다.
    SQLite 온라인 백업 API 는 사용 중인 DB 에서도 한 시점의 완전한 사본을 만든다.
    사본을 quick_check 로 검증하고, 통과했을 때만 옛 스냅샷을 정리한다.
    """
    target_dir = os.path.join(config.BACKUP_DIR, DB_SNAPSHOT_DIRNAME)
    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(target_dir, f"journal_{time.strftime('%Y%m%d')}.db")
    tmp_path = path + '.tmp'

    src = get_db()
    dst = sqlite3.connect(tmp_path)
    try:
        src.backup(dst)
        result = dst.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        dst.close()
        src.close()
    if result != 'ok':
        os.remove(tmp_path)
        raise RuntimeError(f"스냅샷 무결성 검사 실패: {result} (옛 스냅샷은 보존)")

    os.replace(tmp_path, path)
    _prune_older_than(target_dir, BACKUP_RETENTION_DAYS, suffix='.db')
    log.info(f"  └ DB 스냅샷 생성: {path}")
    return path


# ⭐️ 정규장 종가(KRX_CLOSE)와 NXT 애프터마켓 종가(NXT)를 자동 갱신하는 백그라운드 스레드 함수
#    - KRX_CLOSE: KRX 모드가 장외 시간에 고정 표시하는 값. 분봉 API 가 실패할 때의 폴백.
#    - NXT: AFT 모드가 다음날 프리마켓(08:00~09:00)에 NXT 시세를 못 받았을 때의 폴백.
#    KRX 애프터마켓 현재가는 closePrice 로 오므로 따로 캐싱하지 않는다.
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

            # 평일(월~금) 15:30 ~ 20:30 (NXT 장 종료 20:00 및 마감 직후 시간)에만 캐시 갱신 수행
            if not (0 <= day_of_week <= 4 and 1530 <= time_num <= 2030):
                continue
            # 휴장일에는 NXT 도 열지 않는다 (prices 의 휴장일 목록과 판정을 공유)
            if prices.is_market_holiday(kst_now):
                continue

            log.info("🔄 백그라운드: 정규장 종가·NXT 애프터마켓 종가 자동 캐싱을 시작합니다...")
            conn = get_db()
            try:
                c = conn.cursor()
                c.execute("SELECT DISTINCT stockCode FROM entries WHERE stockCode IS NOT NULL AND stockCode != ''")
                codes = [row['stockCode'].strip().upper() for row in c.fetchall()]

                updated_count = 0
                for code in codes:
                    # 국내 주식(6자리 영숫자) 만 NXT 대상
                    if prices.detect_market(code) != 'KR':
                        continue
                    price_val = prices.fetch_nxt_close(code)
                    if price_val is not None:
                        prices.save_price_cache(conn, code, price_val, 'NXT')
                        updated_count += 1
                    regular_close = prices.fetch_krx_regular_close(code)
                    if regular_close is not None:
                        prices.save_price_cache(conn, code, regular_close, prices.KRX_CLOSE_CACHE_MARKET)
                    # 네이버 서버에 부담을 주지 않기 위해 약간의 지연 시간 추가
                    time.sleep(0.3)
            finally:
                conn.close()
            log.info(f"✅ 백그라운드: NXT 종가 캐싱 완료 (총 {updated_count}개 종목 업데이트 됨)")
        except Exception as e:
            log.error(f"❌ NXT 종가 자동 캐싱 스레드 오류: {e}")
