#!/usr/bin/env python3
"""앱 구동·라우트·백그라운드 작업.

도메인 로직과 공통 처리는 각각 별도 모듈이 소유한다.
  config     경로·상수·시크릿 키          applog    로깅 설정
  middleware 보안/캐시 헤더·gzip·예외      schema    DB 스키마
  accounts   계좌 매핑                     prices    시세 조회
  stats      성과 계산                     entry_logic  기록 저장·무결성
  backups    백업 ZIP 검증                 trading_api  봇 연동 API(/api/v1)
"""

import sys
# tmux 등 환경에서 이모지 출력 시 발생하는 UnicodeEncodeError 방지를 위해 표준 출력을 강제로 UTF-8로 지정
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

import os
import shutil
from flask import (Flask)

# ⭐️ 추출된 도메인 모듈 (순수 로직 — 단위 테스트 용이)
import config
import applog
import memcache
import middleware
import prices
import trading_api
import accounts
import images
import schema
import jobs
import auth
import admin
import backup_api
import api
from db import get_db, db_conn

# ⭐️ 정적 자산은 반드시 static/ 안에서만 서빙한다.
#    예전에는 static_folder='.' 로 프로젝트 루트 전체를 정적 경로에 열어 두었는데,
#    이러면 로그인만 한 일반 사용자도 /.secret_key, /db/journal.db, /backup/*.zip,
#    /json/<타인>/account_info.json, /.git/config 를 그대로 내려받을 수 있었다.
#    시크릿 키가 새면 세션 쿠키를 위조해 관리자 계정을 만들어낼 수 있으므로,
#    루트 노출은 단순한 정보 유출이 아니라 권한 상승 경로였다.
app = Flask(__name__)
app.secret_key = config.load_secret_key()
config.apply_to(app)

applog.setup(app)
middleware.register(app)
auth.register(app)
admin.register(app)
trading_api.register(app)
backup_api.register(app)
api.register(app)

# 필요한 폴더들 생성
os.makedirs(config.DB_DIR, exist_ok=True)
os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)
os.makedirs(config.BACKUP_DIR, exist_ok=True)

def _count_users():
    """시작 로그용 계정 수. 실패해도 서버 기동을 막지 않는다."""
    try:
        with db_conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    except Exception as e:
        return f"조회 실패: {e}"


def init_db():
    conn = get_db()
    try:
        # 테이블·컬럼·인덱스는 schema 모듈이 단독으로 소유한다.
        schema.init(conn, app.logger)

        # 값의 의미를 알아야 하는 데이터 이관은 그 도메인을 아는 모듈이 맡는다.
        #   - 평문 API 키 → 해시 저장소 (봇 인증 규칙을 아는 trading_api)
        trading_api.migrate_data(conn)
    finally:
        conn.close()


# ⭐️ 레거시 json/<username>/account_info.json → users.account_mappings 1회성 이관
def migrate_account_mappings():
    try:
        with db_conn() as conn:
            accounts.migrate_json_files(conn, config.JSON_DIR, app.logger)
    except Exception as e:
        # 이관 실패로 서버 기동을 막지 않는다. 실패하면 해당 사용자는 빈 매핑으로
        # 시작하고, 화면에서 다시 등록하면 그 값이 DB 에 저장된다.
        app.logger.error(f"계좌 매핑 이관 중 오류: {e}")


# ⭐️ 기존 기록의 본문 내장 base64 이미지를 파일로 일괄 추출하는 1회성 마이그레이션
#    (서버 시작 시 실행. 실행 전 DB 파일을 backup/ 폴더에 안전 복사한다.)
def migrate_inline_images():
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT id, username, thoughts FROM entries WHERE thoughts LIKE '%data:image%'")
        rows = c.fetchall()
        if not rows:
            return

        # 마이그레이션 직전 DB 원본을 1회 백업 (이미 있으면 생략)
        safety_copy = os.path.join(config.BACKUP_DIR, 'journal_pre_imgmigration.db')
        if not os.path.exists(safety_copy):
            # WAL 에 남은 변경분까지 본 파일에 합친 뒤 복사 (스냅샷 무결성 보장)
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            shutil.copy2(config.DB_FILE, safety_copy)
            app.logger.info(f"🛡️ 이미지 마이그레이션 전 DB 백업 생성: {safety_copy}")

        migrated = 0
        for row in rows:
            username = row['username'] or ''
            new_entry = images.extract_inline_images(username, {'thoughts': row['thoughts']})
            if new_entry['thoughts'] != row['thoughts']:
                c.execute("UPDATE entries SET thoughts = ? WHERE id = ?", (new_entry['thoughts'], row['id']))
                migrated += 1
        conn.commit()
        if migrated:
            app.logger.info(f"🔄 본문 내장 base64 이미지 {migrated}건을 파일로 추출했습니다 (초기 로딩 최적화).")
    except Exception as e:
        app.logger.error(f"❌ 이미지 마이그레이션 중 오류 발생: {e}", exc_info=True)
    finally:
        conn.close()


@app.context_processor
def inject_get_mtime():
    def get_mtime(filename):
        """static/ 안의 자산 수정시각. 템플릿의 ?v= 캐시 버스팅에 쓴다.

        자산이 static/ 으로 옮겨졌으므로 기준 디렉터리도 app.static_folder 다.
        (루트 기준으로 두면 항상 0 이 나와 캐시가 영영 갱신되지 않는다)
        """
        path = os.path.join(app.static_folder, filename)
        if os.path.exists(path):
            return int(os.path.getmtime(path))
        return 0

    def app_scripts():
        """static/js/ 안의 화면 스크립트를 **파일명 순서대로** 돌려준다.

        ⭐️ 예전에는 6,700줄짜리 script.js 한 덩어리였다. 조각으로 나눈 뒤에도
           이들은 ES 모듈이 아니라 전역을 공유하는 클래식 스크립트이므로
           **로드 순서가 곧 실행 순서**다. 그래서 파일명에 01-, 02- 처럼 번호를
           붙이고 정렬해서 넣는다. 템플릿에 <script> 를 손으로 나열하면 새 조각을
           추가할 때 빠뜨리기 쉽고, 그러면 화면 일부만 조용히 죽는다.
        """
        js_dir = os.path.join(app.static_folder, 'js')
        try:
            names = sorted(f for f in os.listdir(js_dir) if f.endswith('.js'))
        except OSError:
            app.logger.error(f"⚠️ 화면 스크립트 폴더를 읽지 못했습니다: {js_dir}")
            return []
        return [(f'js/{n}', int(os.path.getmtime(os.path.join(js_dir, n)))) for n in names]

    return {'get_mtime': get_mtime, 'app_scripts': app_scripts}

# ---------------------------------------------------------------------------
# 기동
# ---------------------------------------------------------------------------

def bootstrap(start_jobs=True):
    """스키마 적용 + 1회성 이관 + 백그라운드 작업 시작. 몇 번 불러도 안전하다.

    ⚠️ 예전에는 이 전부가 __main__ 블록 안에만 있었다. 그래서 gunicorn·uwsgi 처럼
       모듈을 임포트만 하는 WSGI 서버로 띄우면 스키마 적용도, 계좌 매핑 이관도,
       자동 백업 스레드도 **조용히 실행되지 않았다**. 화면은 멀쩡히 뜨는데 백업만
       안 되는 식이라 알아채기 어렵다. 이제 함수로 빼서 wsgi.py 가 호출한다.
    """
    init_db()

    # ⭐️ 본문 내장 base64 이미지 → 파일 추출 (1회성, 이미 완료된 경우 즉시 통과)
    migrate_inline_images()

    # ⭐️ 계좌 매핑을 파일에서 DB 로 이관 (이미 옮겨진 사용자는 즉시 통과)
    migrate_account_mappings()

    if start_jobs:
        jobs.start_all()

    # ⭐️ 어느 DB 를 붙잡고 떴는지 시작 시점에 못박아 둔다. 계정이 통째로 사라진 것처럼
    #    보이는 사고는 대부분 '다른 파일을 보고 있었다' 가 원인이었다.
    app.logger.info(f"📁 사용 중인 DB: {config.DB_FILE} (계정 {_count_users()}개)")

    # ⭐️ 휴장일 판정 상태도 기동 때마다 못박는다. 어느 달력(패키지 버전)으로
    #    판정하고 있는지가 로그에 남아야, 임시공휴일이 반영됐는지를 나중에 되짚을
    #    수 있다. 라이브러리가 없으면 모든 평일이 정규장으로 보이는데 화면·동작은
    #    멀쩡해서 아무도 모른다 — 그 경우는 ERROR 로 올린다.
    coverage, severity = prices.holiday_coverage()
    {'ok': app.logger.info,
     'warn': app.logger.warning,
     'error': app.logger.error}[severity](f"📅 {coverage}")

    # ⭐️ 같은 이유로, 이 프로세스에만 존재하는 상태도 시작 시점에 적어 둔다.
    #    멀티 워커로 띄웠을 때 나타나는 증상(레이트리밋이 헐거워지고, 저장했는데
    #    통계가 안 바뀌고, 비밀번호를 바꿨는데 다른 창이 안 끊기는)은 서로 무관해
    #    보여서 원인을 한참 뒤에야 찾게 된다. 로그에 목록이 있으면 바로 짚인다.
    app.logger.info(
        "🧠 프로세스 메모리에만 있는 상태: "
        + ", ".join(name for name, _why in memcache.PROCESS_LOCAL_STATE)
        + " — 단일 프로세스로 구동해야 의도한 대로 동작합니다 (wsgi.py 참고)")
    return app


# ⭐️ WSGI 서버(gunicorn/uwsgi)용 진입점은 **wsgi.py** 다. 여기서 bootstrap() 을
#    부르지 않는 이유: 이 모듈은 테스트·도구·다른 모듈이 그냥 임포트한다.
#    임포트만으로 스키마가 적용되고 계좌 매핑이 이관되고 자동 백업 스레드가 뜨면,
#    `import backend_app` 한 줄이 사용자의 실제 DB 를 건드리게 된다.
#    임포트에는 부작용이 없어야 한다 — 기동은 항상 명시적으로 부른다.
application = app


if __name__ == '__main__':
    bootstrap()

    port = 5000
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            app.logger.warning(
                f"경고: 잘못된 포트 번호('{sys.argv[1]}')가 입력되어 기본 포트(5000)로 실행합니다.")

    app.logger.info(f"로컬 주식 매매 일지 서버를 시작합니다. (포트: {port})")
    app.logger.info(
        f"웹 브라우저를 열고 http://127.0.0.1:{port} 또는 "
        f"기기의 로컬 IP 주소(예: 192.168.x.x:{port})로 접속해주세요.")

    try:
        from waitress import serve  # type: ignore
        app.logger.info("🚀 Waitress WSGI 프로덕션 서버로 실행 중입니다.")
        # ⭐️ 기본 스레드(4개)로는 시세/뉴스처럼 외부 API 를 수 초씩 점유하는 요청이
        #    겹칠 때 초기 필수 API(/api/data 등)가 큐에서 대기하며 화면이 멈춘 것처럼
        #    보인다. 스레드를 16개로 늘려 초기 병렬 요청이 즉시 처리되도록 한다.
        serve(app, host='0.0.0.0', port=port, threads=16)
    except ImportError:
        app.logger.warning(
            "⚠️ Waitress가 설치되지 않아 Flask 개발 서버로 실행합니다. "
            "(프로덕션 환경 권장: pip install waitress)")
        app.run(host='0.0.0.0', debug=True, port=port)
