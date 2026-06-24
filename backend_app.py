#!/usr/bin/env python3
import sys
# tmux 등 환경에서 이모지 출력 시 발생하는 UnicodeEncodeError 방지를 위해 표준 출력을 강제로 UTF-8로 지정
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

import json
import uuid
import os
import sqlite3
import base64
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
import time
import concurrent.futures
import zipfile
import io
import tempfile
import shutil
import re
import logging
import threading
from functools import wraps
from contextlib import contextmanager
from logging.handlers import TimedRotatingFileHandler
from datetime import timedelta, datetime
from flask import (Flask, jsonify, request, send_from_directory, session, redirect,
                   url_for, render_template, send_file)
from werkzeug.security import generate_password_hash, check_password_hash

# ⭐️ 추출된 도메인 모듈 (순수 로직 — 단위 테스트 용이)
import prices
import stats
import entry_logic
import backups

# 하위 호환 및 기존 참조 유지를 위한 재노출
from prices import KRX_HOLIDAYS
from stats import compute_trade_stats, parse_entry_dt
from backups import verify_backup_zip
from entry_logic import validate_trade_entry

app = Flask(__name__, static_folder='.', static_url_path='')


# ⭐️ 1. 시크릿 키: 환경변수가 우선. 없으면 파일에 영속화하여 재시작 시에도 세션 유지
def _load_secret_key():
    env_key = os.environ.get('SECRET_KEY')
    if env_key:
        return env_key
    key_path = '.secret_key'
    try:
        if os.path.exists(key_path):
            with open(key_path, 'r') as f:
                saved = f.read().strip()
                if saved:
                    return saved
        new_key = os.urandom(24).hex()
        with open(key_path, 'w') as f:
            f.write(new_key)
        return new_key
    except Exception:
        # 파일 접근 불가 환경에서는 임시 난수 키로 폴백 (재시작 시 세션 무효화)
        return os.urandom(24).hex()


app.secret_key = _load_secret_key()


# ⭐️ 로깅(Logging) 설정
LOG_DIR = 'logs'
os.makedirs(LOG_DIR, exist_ok=True)
log_file = os.path.join(LOG_DIR, 'backend_app.log')


# ⭐️ 백업 로그 파일명을 backend_app_YYYYMMDD.log 형태로 저장하기 위한 커스텀 핸들러
class CustomDailyRotatingFileHandler(TimedRotatingFileHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.suffix = "%Y%m%d"
        self.extMatch = re.compile(r"^\d{8}$")

    def rotation_filename(self, default_name):
        # 기본적으로 'backend_app.log.20231027' 로 생성되는 이름을 'backend_app_20231027.log' 로 변경
        return default_name.replace('.log.', '_') + '.log'

    def getFilesToDelete(self):
        dirName, baseName = os.path.split(self.baseFilename)
        fileNames = os.listdir(dirName)
        result = []
        prefix = baseName.replace('.log', '_')
        for fileName in fileNames:
            if fileName.startswith(prefix) and fileName.endswith('.log'):
                suffix = fileName[len(prefix):-4]
                if self.extMatch.match(suffix):
                    result.append(os.path.join(dirName, fileName))
        if len(result) < self.backupCount:
            return []
        else:
            result.sort()
            return result[:len(result) - self.backupCount]


# ⭐️ 모든 로그(Traceback 등 포함)를 개행 없이 한 줄로 출력하기 위한 커스텀 포매터
class SingleLineFormatter(logging.Formatter):
    def format(self, record):
        msg = super().format(record)
        # 개행 문자를 공백으로 대체하여 여러 줄의 로그를 한 줄로 결합
        return msg.replace('\n', ' ').replace('\r', '')


# 파일 핸들러 설정 (매일 자정에 갱신, 30일(약 1달)간 로그 보관, UTF-8 인코딩)
file_handler = CustomDailyRotatingFileHandler(log_file, when='midnight', interval=1, backupCount=30, encoding='utf-8')
log_formatter = SingleLineFormatter('%(asctime)s.%(msecs)03d [%(levelname)s] [%(funcName)s] %(filename)s:%(lineno)d - %(message)s', datefmt='%H:%M:%S')
file_handler.setFormatter(log_formatter)
file_handler.setLevel(logging.DEBUG)

# ⭐️ 화면(콘솔) 출력용 핸들러 설정
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
console_handler.setLevel(logging.DEBUG)


# ⭐️ 특정 백그라운드 작업 로그는 화면(콘솔)에 출력하지 않고 파일에만 남기도록 필터 추가
class ConsoleFilter(logging.Filter):
    def filter(self, record):
        if record.funcName == 'auto_fetch_nxt_close_job':
            return False
        return True


console_handler.addFilter(ConsoleFilter())

# ⭐️ 기본 Flask 로거 초기화 및 중복 출력 방지 (시간이 없는 기본 화면 로그 차단)
app.logger.handlers.clear()
app.logger.propagate = False

# 로거에 파일 및 콘솔 핸들러 추가
app.logger.addHandler(file_handler)
app.logger.addHandler(console_handler)
app.logger.setLevel(logging.DEBUG)

# ⭐️ Werkzeug 기본 Access 로그 비활성화
werkzeug_logger = logging.getLogger('werkzeug')
werkzeug_logger.setLevel(logging.ERROR)


# ⭐️ Flask 요청/응답 라이프사이클 내에서 직접 Access 로그를 기록
@app.after_request
def log_request_info(response):
    username = session.get('username') or "Guest"
    app.logger.info(f"[{username}] {request.method} {request.path} {response.status_code}")
    return response


# ⭐️ 3. 전역 HTTP 보안 헤더 적용 (보안 강화)
@app.after_request
def add_security_headers(response):
    # 클릭재킹(Clickjacking) 방지: 다른 사이트의 iframe 내부에서 이 앱이 렌더링되지 않도록 차단
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    # MIME 스니핑 방지: 브라우저가 파일 형식을 추측하지 않도록 강제
    response.headers['X-Content-Type-Options'] = 'nosniff'
    # XSS 필터링 활성화 (구형 브라우저 지원용)
    response.headers['X-XSS-Protection'] = '1; mode=block'
    # HTTPS 환경일 경우 브라우저에 HTTPS 접속만 허용하도록 강제 (개발 환경에서는 주석 처리 가능)
    # if request.is_secure:
    #     response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response


# ⭐️ 정적 자산 캐시 헤더: 정적 파일(js/css/이미지)에 단기 캐시 부여로 재방문 속도 개선
@app.after_request
def add_cache_headers(response):
    if request.endpoint == 'static' or request.path.startswith('/uploads/'):
        response.headers.setdefault('Cache-Control', 'public, max-age=3600')
    return response


# ⭐️ 전역 서버 에러 핸들러 추가 (서버 중단/500 에러 발생 시 상세 로그 기록)
@app.errorhandler(Exception)
def handle_exception(e):
    app.logger.error(f"Unhandled Exception: {e}", exc_info=True)
    return jsonify(error=str(e)), 500


# ⭐️ 세션(쿠키) 보안 설정 강화
app.config['SESSION_COOKIE_HTTPONLY'] = True  # 자바스크립트(XSS)로 쿠키 접근 원천 차단
app.config['SESSION_COOKIE_SECURE'] = False   # ⭐️ 로컬(HTTP) 환경 접속 시 로그인 갱신 오류 방지를 위해 비활성화
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # CSRF(크로스 사이트 요청 위조) 공격 방어
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=1)  # ⭐️ 세션 유효 기간 1시간으로 설정
app.config['SESSION_REFRESH_EACH_REQUEST'] = False  # ⭐️ 백그라운드 자동 갱신 시 세션 무한 연장 방지

# ⭐️ 2. 악의적인 대용량 파일 업로드 방어 (Payload 제한: 16MB)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

DATA_FILE = 'my_stock_trading_journal.json'
DB_DIR = 'db'
DB_FILE = os.path.join(DB_DIR, 'journal.db')
UPLOAD_FOLDER = 'uploads'
BACKUP_DIR = 'backup'

# 필요한 폴더들 생성
os.makedirs(DB_DIR, exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

# 로그인 시도 횟수 및 차단 시간 관리를 위한 전역 변수 (IP 기준)
login_attempts = {}


def get_db():
    # 모듈 전역 DB_FILE 을 동적으로 참조 (테스트가 경로를 교체할 수 있도록)
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row  # 결과를 dict 형태로 접근할 수 있게 함
    # journal_mode=WAL 은 DB 파일에 영속되므로 init_db() 에서 1회만 설정한다.
    # 여기서는 비용이 거의 없는 연결별 설정만 적용한다.
    #   - synchronous=NORMAL : WAL 과 함께 쓸 때 안전하면서 쓰기 성능 향상
    #   - busy_timeout       : 시세 병렬 스레드와의 잠금 경합 시 즉시 실패 대신 대기
    conn.execute('PRAGMA synchronous=NORMAL;')
    conn.execute('PRAGMA busy_timeout=5000;')
    return conn


@contextmanager
def db_conn():
    """요청 핸들러용 DB 연결 컨텍스트 매니저.

    예외/조기 반환과 무관하게 연결을 반드시 닫아 누수를 방지합니다.
    commit 은 호출자가 명시적으로 수행합니다.
    """
    conn = get_db()
    try:
        yield conn
    finally:
        conn.close()


# ⭐️ 시세 모듈에 DB 연결 공급자 주입 (순환 임포트 회피)
prices.set_db_provider(get_db)


def process_image(image_data, entry_id):
    """Base64 이미지를 파일로 저장하고 URL 경로를 반환"""
    if not image_data:
        return None
    if image_data.startswith('data:image'):
        header, encoded = image_data.split(',', 1)
        ext = 'jpg'
        if 'png' in header:
            ext = 'png'

        filename = f"img_{entry_id}.{ext}"
        filepath = os.path.join(UPLOAD_FOLDER, filename)

        with open(filepath, 'wb') as f:
            f.write(base64.b64decode(encoded))

        return f"/uploads/{filename}"
    return image_data  # 이미 URL 형식인 경우 그대로 반환


def init_db():
    conn = get_db()
    # WAL 모드는 DB 파일에 영속되므로 초기화 시 1회만 설정한다. (요청마다 재설정 방지)
    conn.execute('PRAGMA journal_mode=WAL;')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY,
            username TEXT,
            type TEXT,
            stockName TEXT,
            stockCode TEXT,
            title TEXT,
            thoughts TEXT,
            date TEXT,
            rawDate TEXT,
            attachedImage TEXT,
            brokerAccount TEXT,
            subAccount TEXT,
            accountName TEXT,
            tradeType TEXT,
            price REAL,
            quantity REAL,
            createdAt TEXT,
            updatedAt TEXT,
            tags TEXT,
            attachedFile TEXT,
            attachedFileName TEXT
        )
    ''')

    # 사용자 계정 관리를 위한 테이블 생성
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        )
    ''')

    # 기존 DB 호환성을 위해 새 컬럼을 안전하게 추가
    for ddl in (
        "ALTER TABLE entries ADD COLUMN createdAt TEXT",
        "ALTER TABLE entries ADD COLUMN updatedAt TEXT",
        "ALTER TABLE entries ADD COLUMN stockCode TEXT",
        "ALTER TABLE entries ADD COLUMN tags TEXT",
        "ALTER TABLE entries ADD COLUMN attachedFile TEXT",
        "ALTER TABLE entries ADD COLUMN attachedFileName TEXT",
        "ALTER TABLE entries ADD COLUMN subAccount TEXT",
        "ALTER TABLE entries ADD COLUMN username TEXT",
        "ALTER TABLE users ADD COLUMN preferences TEXT",
        "ALTER TABLE users ADD COLUMN created_at TEXT",
        "ALTER TABLE users ADD COLUMN last_login_at TEXT",
        "ALTER TABLE users ADD COLUMN is_allowed INTEGER DEFAULT 1",
        "ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0",
    ):
        try:
            c.execute(ddl)
        except sqlite3.OperationalError:
            pass

    # ⭐️ 시간외 단일가(NXT) 전일 종가 유지를 위한 캐시 테이블 (KRX/NXT 분리 저장)
    # 기존 단일 키(code) 테이블에서 복합 키(code, market_type)로 마이그레이션
    try:
        c.execute("SELECT market_type FROM price_cache LIMIT 1")
    except sqlite3.OperationalError:
        # 기존 스키마(market_type 컬럼 없음) → 테이블 재생성
        c.execute("DROP TABLE IF EXISTS price_cache")
        app.logger.info("🔄 price_cache 테이블을 KRX/NXT 분리 저장 스키마로 마이그레이션합니다.")
    c.execute('''
        CREATE TABLE IF NOT EXISTS price_cache (
            code TEXT,
            market_type TEXT DEFAULT 'KRX',
            price REAL,
            updated_at TEXT,
            PRIMARY KEY (code, market_type)
        )
    ''')
    conn.commit()

    # ⭐️ 쿼리 성능 최적화를 위한 인덱스 생성 (통계/필터/정렬 가속)
    for idx in (
        "CREATE INDEX IF NOT EXISTS idx_entries_username ON entries(username)",
        "CREATE INDEX IF NOT EXISTS idx_entries_user_type ON entries(username, type)",
        "CREATE INDEX IF NOT EXISTS idx_entries_user_stock ON entries(username, stockName)",
    ):
        try:
            c.execute(idx)
        except sqlite3.OperationalError:
            pass
    conn.commit()

    # 기존 레거시 데이터 호환을 위한 처리
    try:
        c.execute("UPDATE entries SET username = (SELECT username FROM users WHERE is_admin = 1 LIMIT 1) WHERE username IS NULL")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()


# ⭐️ 자동 백업 스레드 함수
def auto_backup_job():
    while True:
        now = datetime.now()
        # 다음 자정 시간 계산
        next_midnight = datetime(now.year, now.month, now.day) + timedelta(days=1)
        # 자정까지 대기
        time_to_sleep = (next_midnight - now).total_seconds()
        time.sleep(time_to_sleep)

        try:
            app.logger.info("🔄 일일 자동 백업을 시작합니다.")
            conn = get_db()
            c = conn.cursor()
            c.execute("SELECT username FROM users")
            users = c.fetchall()
            conn.close()

            for user in users:
                username = user['username']

                conn = get_db()
                c = conn.cursor()
                c.execute("SELECT * FROM entries WHERE username = ?", (username,))
                rows = [dict(row) for row in c.fetchall()]
                conn.close()

                user_backup_dir = os.path.join(BACKUP_DIR, username)
                os.makedirs(user_backup_dir, exist_ok=True)

                current_time_str = time.strftime('%Y%m%d')
                filename = f'TradingJournal_backup_{username}_{current_time_str}.zip'
                filepath = os.path.join(user_backup_dir, filename)

                with zipfile.ZipFile(filepath, 'w', zipfile.ZIP_DEFLATED) as zf:
                    json_data = json.dumps(rows, ensure_ascii=False, indent=2)
                    zf.writestr('data.json', json_data)

                    user_folder = os.path.join(UPLOAD_FOLDER, username)
                    if os.path.exists(user_folder):
                        for root, dirs, files in os.walk(user_folder):
                            for file in files:
                                file_path = os.path.join(root, file)
                                arcname = os.path.join('uploads', file)
                                zf.write(file_path, arcname=arcname)

                # ⭐️ 생성된 백업 파일의 무결성을 즉시 검증 (복원 가능 여부 확인)
                ok, detail = verify_backup_zip(filepath, len(rows))
                if ok:
                    app.logger.info(f"  └ 백업 검증 통과: {username} ({detail})")
                else:
                    app.logger.error(f"  └ ⚠️ 백업 검증 실패: {username} - {detail} (파일: {filename})")

                # 7일 지난 백업 파일 삭제 (7일 = 604800초)
                current_time_sec = time.time()
                for f in os.listdir(user_backup_dir):
                    f_path = os.path.join(user_backup_dir, f)
                    if os.path.isfile(f_path):
                        if os.stat(f_path).st_mtime < current_time_sec - 7 * 86400:
                            os.remove(f_path)

            app.logger.info("✅ 일일 자동 백업이 완료되었습니다.")
        except Exception as e:
            app.logger.error(f"❌ 자동 백업 중 오류 발생: {e}")


# ⭐️ 시간외 단일가(NXT) 종가를 자동 갱신하는 백그라운드 스레드 함수
def auto_fetch_nxt_close_job():
    while True:
        try:
            # 10분(600초) 단위로 동작
            time.sleep(600)

            # 한국 시간(KST) 기준 시간 계산
            import datetime as dt
            kst_now = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=9)
            time_num = kst_now.hour * 100 + kst_now.minute
            day_of_week = kst_now.weekday()  # 0: 월, 1: 화, ..., 4: 금, 5: 토, 6: 일

            # 평일(월~금) 15:30 ~ 18:30 (장 종료 후 시간외 단일가 운영 및 마감 직후 시간)에만 캐시 갱신 수행
            if 0 <= day_of_week <= 4 and 1530 <= time_num <= 1830:
                app.logger.info("🔄 백그라운드: 시간외 단일가(NXT) 자동 캐싱을 시작합니다...")
                conn = get_db()
                c = conn.cursor()
                c.execute("SELECT DISTINCT stockCode FROM entries WHERE stockCode IS NOT NULL AND stockCode != ''")
                codes = [row['stockCode'].strip().upper() for row in c.fetchall()]

                api_headers = {
                    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko)',
                    'Accept': 'application/json, text/plain, */*',
                    'Referer': 'https://m.stock.naver.com/'
                }

                updated_count = 0
                for code in codes:
                    # 국내 주식(6자리 영숫자) 여부 간단 체크
                    if len(code) == 6 and code.isalnum():
                        try:
                            ts = int(time.time() * 1000)
                            url = f"https://m.stock.naver.com/api/stock/{code}/basic?_={ts}"
                            req = urllib.request.Request(url, headers=api_headers)
                            with urllib.request.urlopen(req, timeout=3) as response:
                                res_data = json.loads(response.read())
                                over_info = res_data.get('overMarketPriceInfo', {})
                                if isinstance(over_info, dict) and over_info.get('overPrice'):
                                    price_str = str(over_info.get('overPrice'))
                                    price_val = float(price_str.replace(',', ''))

                                    now_str = dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                                    c.execute("REPLACE INTO price_cache (code, market_type, price, updated_at) VALUES (?, ?, ?, ?)", (code, 'NXT', price_val, now_str))
                                    updated_count += 1
                        except Exception:
                            pass
                        # 네이버 서버에 부담을 주지 않기 위해 약간의 지연 시간 추가
                        time.sleep(0.3)

                conn.commit()
                conn.close()
                app.logger.info(f"✅ 백그라운드: 시간외 단일가 캐싱 완료 (총 {updated_count}개 종목 업데이트 됨)")
        except Exception as e:
            app.logger.error(f"❌ 시간외 단일가 자동 캐싱 스레드 오류: {e}")


@app.before_request
def check_login():
    # 로그인 및 회원가입 처리를 수행하는 라우트는 검사에서 제외
    if request.endpoint not in ['login', 'signup', 'logout']:
        # 세션에 로그인 상태가 없으면 차단
        if not session.get('logged_in'):
            # 백엔드 API 요청인 경우 401 인증 에러 반환
            if request.path.startswith('/api/'):
                return jsonify({"error": "Unauthorized"}), 401
            # 일반 페이지 접근은 로그인 화면으로 리다이렉트
            return redirect(url_for('login'))

        # ⭐️ 자동 폴링되는 API는 세션 갱신에서 제외하여 무한 로그인 유지 현상 방지
        if request.path not in ['/api/current_price', '/api/news']:
            session.modified = True


# ⭐️ 관리자 권한 필요 라우트용 데코레이터
def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not is_admin():
            return jsonify({"error": "Unauthorized"}), 403
        return f(*args, **kwargs)
    return wrapper


def is_admin():
    return session.get('is_admin', False)


@app.route('/login', methods=['GET', 'POST'])
def login():
    client_ip = request.remote_addr
    current_time = time.time()

    # 접속 IP별 시도 횟수 및 차단 시간 초기화
    if client_ip not in login_attempts:
        login_attempts[client_ip] = {'count': 0, 'lockout_until': 0}

    record = login_attempts[client_ip]
    error_message = None
    timeout_message = None

    if request.method == 'GET' and request.args.get('timeout'):
        timeout_message = "보안을 위해 장시간 활동이 없어 자동으로 로그아웃 되었습니다."

    if request.method == 'POST':
        # 현재 차단된 상태인지 확인
        if current_time < record['lockout_until']:
            remaining = int(record['lockout_until'] - current_time)
            error_message = f"로그인 5회 실패로 차단되었습니다. {remaining}초 후에 다시 시도해주세요."
        else:
            username = request.form.get('username')
            password = request.form.get('password') or ""

            # DB에서 입력한 아이디와 일치하는 암호화된 비밀번호 조회
            with db_conn() as conn:
                c = conn.cursor()
                c.execute("SELECT password_hash, is_allowed, is_admin FROM users WHERE username = ?", (username,))
                user_record = c.fetchone()

            # 계정이 존재하고, 입력한 비밀번호와 DB의 해시값이 일치하는지 검증
            if user_record and check_password_hash(user_record['password_hash'], password):
                if not user_record['is_allowed']:
                    error_message = "관리자의 승인이 필요하거나 로그인이 제한된 계정입니다."
                else:
                    # 로그인 성공 시 최근 로그인 일시 업데이트
                    with db_conn() as conn:
                        c = conn.cursor()
                        current_time_str = time.strftime('%Y-%m-%d %H:%M:%S')
                        c.execute("UPDATE users SET last_login_at = ? WHERE username = ?", (current_time_str, username))
                        conn.commit()

                    record['count'] = 0
                    record['lockout_until'] = 0
                    session.permanent = True  # ⭐️ 브라우저 종료 여부와 상관없이 설정된 시간(1시간) 후 세션 만료
                    session['logged_in'] = True
                    session['username'] = username  # ⭐️ 계정별 설정 저장을 위해 세션에 저장
                    session['is_admin'] = bool(user_record['is_admin'])
                    return redirect(url_for('index'))
            else:
                record['count'] += 1
                if record['count'] >= 5:
                    record['lockout_until'] = current_time + 60
                    error_message = "비밀번호 5회 연속 실패! 1분 동안 로그인이 차단됩니다."
                else:
                    error_message = f"아이디 또는 비밀번호가 일치하지 않습니다. (실패 횟수: {record['count']}/5)"

    return render_template('login.html', error_message=error_message, timeout_message=timeout_message)


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    error_message = None
    success_message = None

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        password_confirm = request.form.get('password_confirm')

        if not username or not password:
            error_message = "아이디와 비밀번호를 모두 입력해주세요."
        elif password != password_confirm:
            error_message = "비밀번호가 일치하지 않습니다."
        else:
            conn = get_db()
            c = conn.cursor()

            c.execute("SELECT COUNT(*) FROM users")
            user_count = c.fetchone()[0]

            c.execute("SELECT id FROM users WHERE username = ?", (username,))
            if c.fetchone():
                error_message = "이미 존재하는 아이디입니다."
            else:
                hashed_pw = generate_password_hash(password)
                current_time = time.strftime('%Y-%m-%d %H:%M:%S')

                # ⭐️ 가장 먼저 가입하는 사용자를 자동으로 최고 관리자로 설정
                is_admin_flag = 1 if user_count == 0 else 0
                is_allowed = 1 if user_count == 0 else 0

                c.execute("INSERT INTO users (username, password_hash, is_allowed, is_admin, created_at) VALUES (?, ?, ?, ?, ?)", (username, hashed_pw, is_allowed, is_admin_flag, current_time))
                conn.commit()

                if is_admin_flag:
                    success_message = "최초 회원가입이 완료되어 자동으로 최고 관리자로 지정되었습니다. 잠시 후 로그인 화면으로 이동합니다."

                    # ⭐️ 첫 관리자 가입 시 기존 JSON 파일이 있다면 자동 마이그레이션 수행
                    c.execute("SELECT COUNT(*) FROM entries")
                    if c.fetchone()[0] == 0 and os.path.exists(DATA_FILE):
                        app.logger.info("🔄 기존 JSON 데이터를 SQLite 데이터베이스로 마이그레이션 합니다...")
                        try:
                            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                                old_data = json.load(f)
                                for entry in old_data:
                                    img_url = process_image(entry.get('attachedImage'), entry.get('id'))
                                    entry_logic.insert_entry(c, username, entry, attached_image=img_url)
                                conn.commit()
                                app.logger.info("✅ 데이터 마이그레이션 완료! (이제부터 db/journal.db와 uploads 폴더를 사용합니다)")
                        except Exception as e:
                            app.logger.error(f"❌ 마이그레이션 중 오류 발생: {e}")
                else:
                    success_message = "회원가입이 완료되었습니다! 관리자의 승인 후 로그인할 수 있습니다. 잠시 후 로그인 화면으로 이동합니다."
            conn.close()

    return render_template('signup.html', error_message=error_message, success_message=success_message)


@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    session.pop('username', None)  # ⭐️ 로그아웃 시 계정 정보 완벽 파기
    session.pop('is_admin', None)
    if request.args.get('timeout'):
        return redirect(url_for('login', timeout=1))
    return redirect(url_for('login'))


@app.context_processor
def inject_get_mtime():
    def get_mtime(filename):
        path = os.path.join(app.root_path, filename)
        if os.path.exists(path):
            return int(os.path.getmtime(path))
        return 0
    return dict(get_mtime=get_mtime)

@app.route('/')
def index():
    app.logger.debug("index() route 호출됨: templates/stock-memo.html 파일을 렌더링합니다.")
    return render_template('stock-memo.html')


@app.route('/api/ping', methods=['POST'])
def ping():
    # 세션 갱신을 위한 엔드포인트. 요청이 오면 세션 수명 1시간이 다시 연장됨
    session.modified = True
    return jsonify({"status": "success"})


@app.route('/api/me', methods=['GET'])
def get_me():
    username = session.get('username')
    pending_count = 0
    admin_flag = False

    if username:
        with db_conn() as conn:
            c = conn.cursor()
            # ⭐️ 매 요청 시마다 DB에서 최신 관리자 권한을 조회하여 세션 동기화
            c.execute("SELECT is_admin FROM users WHERE username = ?", (username,))
            user = c.fetchone()
            if user:
                admin_flag = bool(user['is_admin'])
                session['is_admin'] = admin_flag  # 브라우저 세션에 즉각 갱신 반영

            if admin_flag:
                c.execute("SELECT COUNT(*) FROM users WHERE is_allowed = 0")
                pending_count = c.fetchone()[0]

    return jsonify({"username": username, "is_admin": admin_flag, "pending_count": pending_count})


@app.route('/api/account', methods=['DELETE'])
def delete_account():
    username = session.get('username')
    if not username:
        return jsonify({"error": "로그인이 필요합니다."}), 401
    is_admin_flag = session.get('is_admin', False)
    # ⭐️ 최고 관리자 계정은 탈퇴할 수 없도록 보호
    if is_admin_flag:
        return jsonify({"error": "최고 관리자 계정은 탈퇴할 수 없습니다."}), 403

    data = request.json or {}
    password = data.get('password')
    if not password:
        return jsonify({"error": "비밀번호를 입력해주세요."}), 400

    with db_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT password_hash FROM users WHERE username = ?", (username,))
        user_record = c.fetchone()

        if not user_record or not check_password_hash(user_record['password_hash'], password):
            return jsonify({"error": "비밀번호가 일치하지 않습니다."}), 400

        # 사용자 데이터 및 계정 삭제
        c.execute("DELETE FROM entries WHERE username = ?", (username,))
        c.execute("DELETE FROM users WHERE username = ?", (username,))
        conn.commit()

    invalidate_stats_cache(username)

    # 사용자 전용 업로드 폴더 삭제
    user_folder = os.path.join(UPLOAD_FOLDER, username)
    if os.path.exists(user_folder):
        shutil.rmtree(user_folder)

    session.pop('logged_in', None)
    session.pop('username', None)
    return jsonify({"status": "success"})


@app.route('/api/admin/users', methods=['GET'])
@admin_required
def admin_get_users():
    with db_conn() as conn:
        c = conn.cursor()
        c.execute('''
            SELECT u.username, u.is_allowed, u.is_admin, u.created_at, u.last_login_at, COUNT(e.id) as entry_count
            FROM users u
            LEFT JOIN entries e ON u.username = e.username
            GROUP BY u.username
        ''')
        users = [dict(row) for row in c.fetchall()]
    return jsonify(users)


@app.route('/api/admin/users/<target_username>', methods=['DELETE'])
@admin_required
def admin_delete_user(target_username):
    with db_conn() as conn:
        c = conn.cursor()

        c.execute("SELECT is_admin FROM users WHERE username = ?", (target_username,))
        target_user = c.fetchone()
        if target_user and target_user['is_admin']:
            return jsonify({"error": "최고 관리자는 삭제할 수 없습니다."}), 400

        c.execute("DELETE FROM entries WHERE username = ?", (target_username,))
        c.execute("DELETE FROM users WHERE username = ?", (target_username,))
        conn.commit()

    invalidate_stats_cache(target_username)

    user_folder = os.path.join(UPLOAD_FOLDER, target_username)
    if os.path.exists(user_folder):
        shutil.rmtree(user_folder)

    return jsonify({"status": "success"})


@app.route('/api/admin/users/<target_username>/toggle_allow', methods=['POST'])
@admin_required
def admin_toggle_allow(target_username):
    with db_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT is_allowed, is_admin FROM users WHERE username = ?", (target_username,))
        user = c.fetchone()
        if not user:
            return jsonify({"error": "사용자를 찾을 수 없습니다."}), 404

        if user['is_admin']:
            return jsonify({"error": "최고 관리자의 상태는 변경할 수 없습니다."}), 400

        new_status = 0 if user['is_allowed'] else 1
        c.execute("UPDATE users SET is_allowed = ? WHERE username = ?", (new_status, target_username))
        conn.commit()

    return jsonify({"status": "success", "is_allowed": new_status})


@app.route('/api/admin/users/<target_username>/reset_password', methods=['POST'])
@admin_required
def admin_reset_password(target_username):
    # 8자리의 무작위 영문+숫자 임시 비밀번호 생성
    new_password = uuid.uuid4().hex[:8]
    hashed_pw = generate_password_hash(new_password)

    with db_conn() as conn:
        c = conn.cursor()
        c.execute("UPDATE users SET password_hash = ? WHERE username = ?", (hashed_pw, target_username))
        conn.commit()

    return jsonify({"status": "success", "new_password": new_password})


@app.route('/uploads/<req_username>/<filename>')
def uploaded_file(req_username, filename):
    # 사용자 격리된 파일 접근 제어
    if req_username != session.get('username'):
        return jsonify({"error": "Unauthorized"}), 403
    user_folder = os.path.join(UPLOAD_FOLDER, req_username)
    # ⭐️ 브라우저(특히 Safari)가 다운로드된 ZIP 파일을 강제로 자동 압축 해제하지 않도록 attachment로 전송
    return send_from_directory(user_folder, filename, as_attachment=True)


@app.route('/api/data', methods=['GET'])
def get_data():
    username = session.get('username')
    with db_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM entries WHERE username = ? ORDER BY id DESC", (username,))
        data = [dict(row) for row in c.fetchall()]
    return jsonify(data)


@app.route('/api/entry', methods=['POST'])
def create_entry():
    username = session.get('username')
    entry = request.json
    with db_conn() as conn:
        c = conn.cursor()

        # ⭐️ 데이터 무결성 검증 (매도 수량/보유 여부)
        validation_error = entry_logic.validate_trade_entry(c, username, entry)
        if validation_error:
            return jsonify({"error": validation_error}), 400

        entry_logic.insert_entry(c, username, entry)
        conn.commit()
    invalidate_stats_cache(username)
    return jsonify({"status": "success"})


@app.route('/api/entry/<int:entry_id>', methods=['PUT'])
def update_entry(entry_id):
    username = session.get('username')
    entry = request.json
    with db_conn() as conn:
        c = conn.cursor()

        # ⭐️ 데이터 무결성 검증 (수정 중인 기록 자신은 집계에서 제외)
        validation_error = entry_logic.validate_trade_entry(c, username, entry, exclude_id=entry_id)
        if validation_error:
            return jsonify({"error": validation_error}), 400

        entry_logic.update_entry_row(c, entry_id, username, entry)
        conn.commit()
    invalidate_stats_cache(username)
    return jsonify({"status": "success"})


@app.route('/api/entry/<int:entry_id>', methods=['DELETE'])
def delete_entry(entry_id):
    username = session.get('username')
    with db_conn() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM entries WHERE id=? AND username=?", (entry_id, username))
        conn.commit()
    invalidate_stats_cache(username)
    return jsonify({"status": "success"})


# ⭐️ 매매 통계(전체 집계) 결과 캐시 — (username, granularity) -> 결과 dict
#   전체 통계는 대시보드 진입 시 자주 호출되며 SELECT * 전체 로드 + Python 재계산
#   비용이 크다. 기록이 변경될 때만 무효화하여 반복 계산을 제거한다.
#   (특정 entry_ids 로 필터링된 POST 요청은 케이스가 다양해 캐싱하지 않는다.)
_stats_cache = {}
_stats_cache_lock = threading.Lock()


def invalidate_stats_cache(username):
    """해당 사용자의 통계 캐시를 무효화한다 (기록 추가/수정/삭제 시 호출)."""
    if not username:
        return
    with _stats_cache_lock:
        for key in [k for k in _stats_cache if k[0] == username]:
            _stats_cache.pop(key, None)


@app.route('/api/stats', methods=['GET', 'POST'])
def get_stats():
    """로그인한 사용자의 매매 성과 분석 지표를 반환합니다.
    POST 요청 시 JSON 바디에 entry_ids 리스트를 전달하면 해당 항목들로만 통계를 계산합니다."""
    username = session.get('username')
    entry_ids = None
    granularity = 'monthly'
    if request.method == 'POST':
        data = request.json or {}
        entry_ids = data.get('entry_ids')
        granularity = data.get('granularity', 'monthly')

    # ⭐️ 전체 통계 요청은 캐시 우선 조회 (필터링 요청은 캐시 대상 아님)
    if entry_ids is None:
        cache_key = (username, granularity)
        with _stats_cache_lock:
            cached = _stats_cache.get(cache_key)
        if cached is not None:
            return jsonify(cached)

    with db_conn() as conn:
        c = conn.cursor()
        if entry_ids is not None:
            if not entry_ids:
                rows = []
            else:
                chunk_size = 900
                rows = []
                for i in range(0, len(entry_ids), chunk_size):
                    chunk = entry_ids[i:i+chunk_size]
                    placeholders = ','.join('?' for _ in chunk)
                    c.execute(f"SELECT * FROM entries WHERE username = ? AND id IN ({placeholders})", (username, *chunk))
                    rows.extend([dict(row) for row in c.fetchall()])
        else:
            c.execute("SELECT * FROM entries WHERE username = ?", (username,))
            rows = [dict(row) for row in c.fetchall()]

    result = stats.compute_trade_stats(rows, granularity=granularity)

    if entry_ids is None:
        with _stats_cache_lock:
            _stats_cache[(username, granularity)] = result

    return jsonify(result)


@app.route('/api/current_price', methods=['POST'])
def get_current_price():
    data = request.json or {}
    codes = data.get('codes', [])
    market_mode = data.get('market_mode', 'AUTO')
    return jsonify(prices.get_prices(codes, market_mode))


@app.route('/api/change_password', methods=['POST'])
def change_password():
    username = session.get('username')
    if not username:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json
    current_password = data.get('current_password')
    new_password = data.get('new_password')

    if not current_password or not new_password:
        return jsonify({"error": "모든 필드를 입력해주세요."}), 400

    with db_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT password_hash FROM users WHERE username = ?", (username,))
        user_record = c.fetchone()

        if not user_record or not check_password_hash(user_record['password_hash'], current_password):
            return jsonify({"error": "현재 비밀번호가 일치하지 않습니다."}), 400

        hashed_pw = generate_password_hash(new_password)
        c.execute("UPDATE users SET password_hash = ? WHERE username = ?", (hashed_pw, username))
        conn.commit()

    return jsonify({"status": "success"})


@app.route('/api/preferences', methods=['GET'])
def get_preferences():
    username = session.get('username')
    if not username:
        return jsonify({}), 401
    with db_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT preferences FROM users WHERE username = ?", (username,))
        row = c.fetchone()
    prefs = {}
    if row and row['preferences']:
        try:
            prefs = json.loads(row['preferences'])
        except Exception:
            pass
    return jsonify(prefs)


@app.route('/api/preferences', methods=['POST'])
def save_preferences():
    username = session.get('username')
    if not username:
        return jsonify({"error": "Unauthorized"}), 401
    prefs = request.json
    with db_conn() as conn:
        c = conn.cursor()
        c.execute("UPDATE users SET preferences = ? WHERE username = ?", (json.dumps(prefs), username))
        conn.commit()
    return jsonify({"status": "success"})


# ⭐️ 뉴스 검색 결과를 임시 보관할 캐시 딕셔너리와 유효 시간(초) 설정
news_cache = {}
NEWS_CACHE_TTL = 600  # 10분(600초) 동안 캐시 유지


@app.route('/api/news', methods=['POST'])
def get_news():
    data = request.json or {}
    stocks = data.get('stocks', [])
    force_refresh = data.get('force_refresh', False)

    # 보유 종목이 없을 경우 기본 검색어 사용
    if not stocks:
        stocks = ['국내 증시']

    def fetch_news_for_stock(stock):
        current_time = time.time()

        # ⭐️ 1. 수동 새로고침이 아니고, 캐시에 데이터가 유효 시간(10분) 내에 있다면 구글에 요청하지 않고 캐시 반환
        if not force_refresh and stock in news_cache:
            cached_data, timestamp = news_cache[stock]
            if current_time - timestamp < NEWS_CACHE_TTL:
                return cached_data

        news_list = []
        try:
            # ⭐️ 네이버 RSS 서비스 전면 종료(404)에 따라 안정적인 구글 뉴스 RSS로 복귀
            query = urllib.parse.quote(f"{stock} when:7d")
            ts = int(time.time() * 1000)
            url = f"https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko&_={ts}"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=3) as response:
                xml_data = response.read()
                root = ET.fromstring(xml_data)
                for idx, item in enumerate(root.findall('.//item')):
                    if idx >= 5:
                        break

                    title_elem = item.find('title')
                    link_elem = item.find('link')
                    pub_elem = item.find('pubDate')

                    news_list.append({
                        'stock': stock,
                        'title': title_elem.text if title_elem is not None else '',
                        'link': link_elem.text if link_elem is not None else '',
                        'pubDate': pub_elem.text if pub_elem is not None else ''
                    })
        except Exception as e:
            app.logger.error(f"Error fetching Google news for {stock}: {e}")

        # ⭐️ 2. 새로 가져온 뉴스 데이터를 현재 시간과 함께 캐시에 저장
        news_cache[stock] = (news_list, current_time)

        return news_list

    all_news = []
    # ⭐️ 보유 종목이 많을 경우를 대비해 스레드 풀을 활용한 병렬(비동기) 처리 (최대 10개 동시 요청)
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        results = executor.map(fetch_news_for_stock, stocks)
        for res_list in results:
            all_news.extend(res_list)

    return jsonify(all_news)


@app.route('/api/backup', methods=['GET'])
def full_backup():
    """DB와 업로드 이미지를 포함한 전체 폴더를 압축하여 다운로드 제공"""
    username = session.get('username')
    if not username:
        return jsonify({"error": "로그인이 필요합니다."}), 401
    with db_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM entries WHERE username = ?", (username,))
        rows = [dict(row) for row in c.fetchall()]

    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
        # 1. 사용자 데이터를 JSON으로 백업
        json_data = json.dumps(rows, ensure_ascii=False, indent=2)
        zf.writestr('data.json', json_data)

        # 2. 사용자 이미지 폴더 백업
        user_folder = os.path.join(UPLOAD_FOLDER, username)
        if os.path.exists(user_folder):
            for root, dirs, files in os.walk(user_folder):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.join('uploads', file)
                    zf.write(file_path, arcname=arcname)

    memory_file.seek(0)

    # 파일명에 현재 날짜와 시간 추가 (예: TradingJournal_backup_20231027_153000.zip)
    current_time = time.strftime('%Y%m%d_%H%M%S')
    filename = f'TradingJournal_backup_{username}_{current_time}.zip'

    response = send_file(memory_file, mimetype='application/zip', download_name=filename, as_attachment=True)
    response.headers["Access-Control-Expose-Headers"] = "Content-Disposition"
    return response


@app.route('/api/restore', methods=['POST'])
def full_restore():
    """백업 받은 ZIP 파일을 해제하여 DB 및 업로드 이미지를 완벽 원복"""
    username = session.get('username')
    if not username:
        return jsonify({"error": "로그인이 필요합니다."}), 401
    if 'file' not in request.files:
        return jsonify({'error': '업로드된 파일이 없습니다.'}), 400

    file = request.files['file']
    if not file.filename or not file.filename.endswith('.zip'):
        return jsonify({'error': '유효하지 않은 파일입니다. .zip 백업 파일을 업로드해주세요.'}), 400

    temp_dir = tempfile.mkdtemp()
    try:
        with zipfile.ZipFile(file.stream, 'r') as zf:
            zf.extractall(temp_dir)

        json_path = os.path.join(temp_dir, 'data.json')
        if not os.path.exists(json_path):
            return jsonify({'error': '손상된 백업 파일입니다. (data.json을 찾을 수 없습니다)'}), 400

        with open(json_path, 'r', encoding='utf-8') as f:
            entries = json.load(f)

        with db_conn() as conn:
            c = conn.cursor()

            # 1. 기존 사용자의 데이터만 삭제
            c.execute("DELETE FROM entries WHERE username = ?", (username,))

            # 2. 복원할 데이터 삽입
            for entry in entries:
                entry_logic.insert_entry(c, username, entry)
            conn.commit()

        invalidate_stats_cache(username)

        # 3. 사용자 첨부파일 폴더 덮어쓰기
        user_folder = os.path.join(UPLOAD_FOLDER, username)
        if os.path.exists(user_folder):
            shutil.rmtree(user_folder)
        os.makedirs(user_folder, exist_ok=True)

        temp_uploads = os.path.join(temp_dir, 'uploads')
        if os.path.exists(temp_uploads):
            for f in os.listdir(temp_uploads):
                src_path = os.path.join(temp_uploads, f)
                if os.path.isfile(src_path):
                    shutil.copy2(src_path, os.path.join(user_folder, f))

        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        shutil.rmtree(temp_dir)


if __name__ == '__main__':
    init_db()

    # 자동 백업 스레드 시작
    backup_thread = threading.Thread(target=auto_backup_job, daemon=True)
    backup_thread.start()

    # ⭐️ NXT 종가 자동 캐싱 스레드 시작
    nxt_thread = threading.Thread(target=auto_fetch_nxt_close_job, daemon=True)
    nxt_thread.start()

    port = 5000
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            app.logger.warning(f"경고: 잘못된 포트 번호('{sys.argv[1]}')가 입력되어 기본 포트(5000)로 실행합니다.")

    app.logger.info(f"로컬 주식 매매 일지 서버를 시작합니다. (포트: {port})")
    app.logger.info(f"웹 브라우저를 열고 http://127.0.0.1:{port} 또는 기기의 로컬 IP 주소(예: 192.168.x.x:{port})로 접속해주세요.")

    try:
        from waitress import serve
        app.logger.info("🚀 Waitress WSGI 프로덕션 서버로 실행 중입니다.")
        serve(app, host='0.0.0.0', port=port)
    except ImportError:
        app.logger.warning("⚠️ Waitress가 설치되지 않아 Flask 개발 서버로 실행합니다. (프로덕션 환경 권장: pip install waitress)")
        app.run(host='0.0.0.0', debug=True, port=port)
