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
from logging.handlers import TimedRotatingFileHandler
from datetime import timedelta, datetime
from flask import Flask, jsonify, request, send_from_directory, session, redirect, url_for, render_template_string, send_file, has_request_context
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__, static_folder='.', static_url_path='')
app.secret_key = 'stock_memo_secret_key' # 세션 유지를 위한 시크릿 키 설정

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

# ⭐️ 전역 서버 에러 핸들러 추가 (서버 중단/500 에러 발생 시 상세 로그 기록)
@app.errorhandler(Exception)
def handle_exception(e):
    app.logger.error(f"Unhandled Exception: {e}", exc_info=True)
    return jsonify(error=str(e)), 500

# ⭐️ 세션(쿠키) 보안 설정 강화
app.config['SESSION_COOKIE_HTTPONLY'] = True  # 자바스크립트(XSS)로 쿠키 접근 원천 차단
app.config['SESSION_COOKIE_SECURE'] = False   # ⭐️ 로컬(HTTP) 환경 접속 시 로그인 갱신 오류 방지를 위해 비활성화
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax' # CSRF(크로스 사이트 요청 위조) 공격 방어
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=1) # ⭐️ 세션 유효 기간 1시간으로 설정

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
    conn = sqlite3.connect(DB_FILE)
    conn.execute('PRAGMA journal_mode=WAL;')
    conn.row_factory = sqlite3.Row # 결과를 dict 형태로 접근할 수 있게 함
    return conn

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
    return image_data # 이미 URL 형식인 경우 그대로 반환

def init_db():
    conn = get_db()
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
    try:
        c.execute("ALTER TABLE entries ADD COLUMN createdAt TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE entries ADD COLUMN updatedAt TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE entries ADD COLUMN stockCode TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE entries ADD COLUMN tags TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE entries ADD COLUMN attachedFile TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE entries ADD COLUMN attachedFileName TEXT")
    except sqlite3.OperationalError:
        pass
        
    try:
        c.execute("ALTER TABLE entries ADD COLUMN subAccount TEXT")
    except sqlite3.OperationalError:
        pass
        
    # 다중 사용자 격리를 위한 컬럼 추가
    try:
        c.execute("ALTER TABLE entries ADD COLUMN username TEXT")
    except sqlite3.OperationalError:
        pass

    # 사용자별 환경 설정(카드 순서 등) 저장을 위한 컬럼 추가
    try:
        c.execute("ALTER TABLE users ADD COLUMN preferences TEXT")
    except sqlite3.OperationalError:
        pass
        
    # 사용자 가입 일시 컬럼 추가
    try:
        c.execute("ALTER TABLE users ADD COLUMN created_at TEXT")
    except sqlite3.OperationalError:
        pass
        
    # 사용자 최근 로그인 일시 컬럼 추가
    try:
        c.execute("ALTER TABLE users ADD COLUMN last_login_at TEXT")
    except sqlite3.OperationalError:
        pass
    # 사용자 로그인 허용 여부를 위한 컬럼 추가 (기존 가입자는 1로 기본 설정)
    try:
        c.execute("ALTER TABLE users ADD COLUMN is_allowed INTEGER DEFAULT 1")
    except sqlite3.OperationalError:
        pass
        
    # 사용자 관리자 여부 컬럼 추가
    try:
        c.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
        
    # ⭐️ 시간외 단일가(NXT) 전일 종가 유지를 위한 캐시 테이블 생성
    c.execute('''
        CREATE TABLE IF NOT EXISTS price_cache (
            code TEXT PRIMARY KEY,
            price REAL,
            updated_at TEXT
        )
    ''')
    conn.commit()
    
    # ⭐️ 쿼리 성능 최적화를 위한 인덱스 생성
    try:
        c.execute("CREATE INDEX IF NOT EXISTS idx_entries_username ON entries(username)")
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
            
            # 15:30 ~ 18:30 (장 종료 후 시간외 단일가 운영 및 마감 직후 시간)에만 캐시 갱신 수행
            if 1530 <= time_num <= 1830:
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
                                    c.execute("REPLACE INTO price_cache (code, price, updated_at) VALUES (?, ?, ?)", (code, price_val, now_str))
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
            password = request.form.get('password')
            
            # DB에서 입력한 아이디와 일치하는 암호화된 비밀번호 조회
            conn = get_db()
            c = conn.cursor()
            c.execute("SELECT password_hash, is_allowed, is_admin FROM users WHERE username = ?", (username,))
            user_record = c.fetchone()
            conn.close()
            
            # 계정이 존재하고, 입력한 비밀번호와 DB의 해시값이 일치하는지 검증
            if user_record and check_password_hash(user_record['password_hash'], password):
                if not user_record['is_allowed']:
                    error_message = "관리자의 승인이 필요하거나 로그인이 제한된 계정입니다."
                else:
                    # 로그인 성공 시 최근 로그인 일시 업데이트
                    conn = get_db()
                    c = conn.cursor()
                    current_time_str = time.strftime('%Y-%m-%d %H:%M:%S')
                    c.execute("UPDATE users SET last_login_at = ? WHERE username = ?", (current_time_str, username))
                    conn.commit()
                    conn.close()
                    
                    record['count'] = 0
                    record['lockout_until'] = 0
                    session.permanent = True # ⭐️ 브라우저 종료 여부와 상관없이 설정된 시간(1시간) 후 세션 만료
                    session['logged_in'] = True
                    session['username'] = username # ⭐️ 계정별 설정 저장을 위해 세션에 저장
                    session['is_admin'] = bool(user_record['is_admin'])
                    return redirect(url_for('index'))
            else:
                record['count'] += 1
                if record['count'] >= 5:
                    record['lockout_until'] = current_time + 60
                    error_message = "비밀번호 5회 연속 실패! 1분 동안 로그인이 차단됩니다."
                else:
                    error_message = f"아이디 또는 비밀번호가 일치하지 않습니다. (실패 횟수: {record['count']}/5)"
    
    return render_template_string('''
        <!DOCTYPE html>
        <html lang="ko">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=0">
            <title>TRADING JOURNAL - 로그인</title>
            <meta name="apple-mobile-web-app-capable" content="yes">
            <meta name="apple-mobile-web-app-title" content="TRADING JOURNAL">
            <meta name="theme-color" content="#121212">
            <link rel="shortcut icon" href="https://ssl.gstatic.com/finance/favicon/finance_496x496.png">
            <link rel="icon" type="image/png" href="https://ssl.gstatic.com/finance/favicon/finance_496x496.png">
            <link rel="apple-touch-icon" sizes="180x180" href="https://ssl.gstatic.com/finance/favicon/finance_496x496.png">
            <style>
                body { font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, Helvetica, Arial, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; background: linear-gradient(135deg, #121212 0%, #1a1a2e 100%); margin: 0; color: #e0e0e0; }
                .login-container { background: rgba(30, 30, 30, 0.85); backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px); padding: 30px 20px; border-radius: 16px; border: 1px solid rgba(255, 255, 255, 0.05); box-shadow: 0 10px 30px rgba(0,0,0,0.5); text-align: center; width: 260px; }
                .logo-text { font-size: 22px; font-weight: 900; font-style: italic; background: linear-gradient(135deg, #b388ff 0%, #8a2be2 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; letter-spacing: -1px; margin-bottom: 20px; display: flex; align-items: center; justify-content: center; }
                input[type="text"],
                input[type="password"] {
                    width: 100%; 
                    box-sizing: border-box; 
                    padding: 10px; 
                    margin: 0 0 12px 0; 
                    border: 1px solid #333; 
                    border-radius: 8px; 
                    font-size: 16px; 
                    background-color: rgba(18, 18, 18, 0.8); 
                    color: #fff;
                    transition: all 0.3s ease;
                }
                input[type="text"]::placeholder,
                input[type="password"]::placeholder {
                    color: #666;
                }
                input[type="text"]:focus,
                input[type="password"]:focus {
                    border-color: #8a2be2;
                    outline: none;
                    box-shadow: 0 0 0 3px rgba(138, 43, 226, 0.3);
                    background-color: #121212;
                }
                button { 
                    width: 100%; padding: 10px; margin-top: 5px; background: linear-gradient(135deg, #9d4edd 0%, #7b2cbf 100%); color: white; border: none; border-radius: 8px; font-size: 14px; font-weight: bold; cursor: pointer; 
                    transition: all 0.3s ease; 
                    box-shadow: 0 4px 15px rgba(0, 0, 0, 0.3);
                }
                button:hover { 
                    transform: translateY(-2px);
                    box-shadow: 0 6px 20px rgba(123, 44, 191, 0.5);
                    background: linear-gradient(135deg, #b388ff 0%, #8a2be2 100%); 
                }
                .error-banner {
                    position: fixed;
                    top: 20px;
                    left: 50%;
                    transform: translateX(-50%);
                    background: rgba(231, 76, 60, 0.95);
                    color: white;
                    padding: 12px 40px 12px 24px;
                    border-radius: 8px;
                    box-shadow: 0 4px 15px rgba(231, 76, 60, 0.4);
                    font-size: 14px;
                    font-weight: bold;
                    z-index: 1000;
                    backdrop-filter: blur(5px);
                    -webkit-backdrop-filter: blur(5px);
                    opacity: 1;
                    pointer-events: auto;
                    animation: slideDown 0.3s ease-out forwards;
                }
                @keyframes slideDown {
                    0% { top: -20px; opacity: 0; }
                    100% { top: 20px; opacity: 1; }
                }
            </style>
        </head>
        <body>
            {% if timeout_message %}
            <!-- ⭐️ 타임아웃 메시지는 화면에 영구 고정되도록 설정 (주황색 테마) -->
            <div class="error-banner" id="timeoutBanner" style="background: rgba(243, 156, 18, 0.95); box-shadow: 0 4px 15px rgba(243, 156, 18, 0.4);">
                ⚠️ {{ timeout_message }}
                <span onclick="document.getElementById('timeoutBanner').style.display='none'; const url = new URL(window.location); url.searchParams.delete('timeout'); window.history.replaceState({}, document.title, url);" style="position: absolute; right: 8px; top: 50%; transform: translateY(-50%); cursor: pointer; font-size: 24px; padding: 5px; line-height: 1;">&times;</span>
            </div>
            {% endif %}
            {% if error_message %}
            <div class="error-banner" id="errorBanner">
                ⚠️ {{ error_message }}
                <span onclick="document.getElementById('errorBanner').style.display='none'" style="position: absolute; right: 8px; top: 50%; transform: translateY(-50%); cursor: pointer; font-size: 24px; padding: 5px; line-height: 1;">&times;</span>
            </div>
            {% endif %}
            <div class="login-container">
                <div class="logo-text">
                    TRADING JOURNAL
                </div>
                <form method="post">
                    <input type="text" name="username" placeholder="아이디를 입력하세요" required autofocus>
                    <input type="password" name="password" placeholder="비밀번호를 입력하세요" required>
                    <button type="submit">접속하기</button>
                </form>
                <div style="margin-top: 15px; font-size: 13px;">
                    <span style="color: #888;">계정이 없으신가요?</span> 
                    <a href="{{ url_for('signup') }}" style="color: #b388ff; text-decoration: none; font-weight: bold;">새 계정 가입하기</a>
                </div>
            </div>
            <script>
                document.addEventListener('DOMContentLoaded', function() {
                    const usernameInput = document.querySelector('input[name="username"]');
                    const savedUsername = localStorage.getItem('last_username');
                    if (savedUsername && usernameInput) {
                        usernameInput.value = savedUsername;
                        document.querySelector('input[name="password"]').focus(); // 아이디가 있으면 비밀번호 칸으로 포커스 이동
                    }
                    document.querySelector('form').addEventListener('submit', function() {
                        if (usernameInput.value) localStorage.setItem('last_username', usernameInput.value);
                    });
                });
            </script>
        </body>
        </html>
    ''', error_message=error_message, timeout_message=timeout_message)

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
                is_admin = 1 if user_count == 0 else 0
                is_allowed = 1 if user_count == 0 else 0
                
                c.execute("INSERT INTO users (username, password_hash, is_allowed, is_admin, created_at) VALUES (?, ?, ?, ?, ?)", (username, hashed_pw, is_allowed, is_admin, current_time))
                conn.commit()
                
                if is_admin:
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
                                    c.execute('''
                                        INSERT INTO entries 
                                        (id, username, type, stockName, stockCode, title, thoughts, date, rawDate, attachedImage, brokerAccount, subAccount, accountName, tradeType, price, quantity, createdAt, updatedAt, tags, attachedFile, attachedFileName)
                                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                    ''', (
                                        entry.get('id'), username, entry.get('type'), entry.get('stockName'), entry.get('stockCode', ''), entry.get('title'),
                                        entry.get('thoughts'), entry.get('date'), entry.get('rawDate'), img_url,
                                        entry.get('brokerAccount'), entry.get('subAccount', ''), entry.get('accountName'), entry.get('tradeType'),
                                        entry.get('price', 0), entry.get('quantity', 0),
                                        entry.get('createdAt'), entry.get('updatedAt'), entry.get('tags', ''),
                                        entry.get('attachedFile', ''), entry.get('attachedFileName', '')
                                    ))
                                conn.commit()
                                app.logger.info("✅ 데이터 마이그레이션 완료! (이제부터 db/journal.db와 uploads 폴더를 사용합니다)")
                        except Exception as e:
                            app.logger.error(f"❌ 마이그레이션 중 오류 발생: {e}")
                else:
                    success_message = "회원가입이 완료되었습니다! 관리자의 승인 후 로그인할 수 있습니다. 잠시 후 로그인 화면으로 이동합니다."
            conn.close()
            
    return render_template_string('''
        <!DOCTYPE html>
        <html lang="ko">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=0">
            <title>TRADING JOURNAL - 회원가입</title>
            <meta name="apple-mobile-web-app-capable" content="yes">
            <meta name="apple-mobile-web-app-title" content="TRADING JOURNAL">
            <meta name="theme-color" content="#121212">
            <link rel="shortcut icon" href="https://ssl.gstatic.com/finance/favicon/finance_496x496.png">
            <link rel="icon" type="image/png" href="https://ssl.gstatic.com/finance/favicon/finance_496x496.png">
            <link rel="apple-touch-icon" sizes="180x180" href="https://ssl.gstatic.com/finance/favicon/finance_496x496.png">
            <style>
                body { font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, Helvetica, Arial, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; background: linear-gradient(135deg, #121212 0%, #1a1a2e 100%); margin: 0; color: #e0e0e0; }
                .login-container { background: rgba(30, 30, 30, 0.85); backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px); padding: 30px 20px; border-radius: 16px; border: 1px solid rgba(255, 255, 255, 0.05); box-shadow: 0 10px 30px rgba(0,0,0,0.5); text-align: center; width: 260px; }
                .logo-text { font-size: 22px; font-weight: 900; font-style: italic; background: linear-gradient(135deg, #b388ff 0%, #8a2be2 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; letter-spacing: -1px; margin-bottom: 20px; display: flex; align-items: center; justify-content: center; }
                input[type="text"], input[type="password"] { width: 100%; box-sizing: border-box; padding: 10px; margin: 0 0 12px 0; border: 1px solid #333; border-radius: 8px; font-size: 16px; background-color: rgba(18, 18, 18, 0.8); color: #fff; transition: all 0.3s ease; }
                input[type="text"]::placeholder, input[type="password"]::placeholder { color: #666; }
                input[type="text"]:focus, input[type="password"]:focus { border-color: #8a2be2; outline: none; box-shadow: 0 0 0 3px rgba(138, 43, 226, 0.3); background-color: #121212; }
                button { width: 100%; padding: 10px; margin-top: 5px; background: linear-gradient(135deg, #9d4edd 0%, #7b2cbf 100%); color: white; border: none; border-radius: 8px; font-size: 14px; font-weight: bold; cursor: pointer; transition: all 0.3s ease; box-shadow: 0 4px 15px rgba(0, 0, 0, 0.3); }
                button:hover { transform: translateY(-2px); box-shadow: 0 6px 20px rgba(123, 44, 191, 0.5); background: linear-gradient(135deg, #b388ff 0%, #8a2be2 100%); }
                .error-banner { position: fixed; top: 20px; left: 50%; transform: translateX(-50%); background: rgba(231, 76, 60, 0.95); color: white; padding: 12px 40px 12px 24px; border-radius: 8px; box-shadow: 0 4px 15px rgba(231, 76, 60, 0.4); font-size: 14px; font-weight: bold; z-index: 1000; backdrop-filter: blur(5px); -webkit-backdrop-filter: blur(5px); opacity: 1; pointer-events: auto; animation: slideDown 0.3s ease-out forwards; }
                @keyframes slideDown { 0% { top: -20px; opacity: 0; } 100% { top: 20px; opacity: 1; } }
            </style>
        </head>
        <body>
            {% if error_message %}
            <div class="error-banner" id="errorBanner">
                ⚠️ {{ error_message }}
                <span onclick="document.getElementById('errorBanner').style.display='none'" style="position: absolute; right: 8px; top: 50%; transform: translateY(-50%); cursor: pointer; font-size: 24px; padding: 5px; line-height: 1;">&times;</span>
            </div>
            {% endif %}
            {% if success_message %}
            <div class="error-banner" id="successBanner" style="background: rgba(39, 174, 96, 0.95); box-shadow: 0 4px 15px rgba(39, 174, 96, 0.4);">
                ✅ {{ success_message }}
                <span onclick="document.getElementById('successBanner').style.display='none'" style="position: absolute; right: 8px; top: 50%; transform: translateY(-50%); cursor: pointer; font-size: 24px; padding: 5px; line-height: 1;">&times;</span>
            </div>
            <script> setTimeout(function() { window.location.href = "{{ url_for('login') }}"; }, 1500); </script>
            {% endif %}
            <div class="login-container">
                <div class="logo-text">TRADING JOURNAL</div>
                <form method="post">
                    <input type="text" name="username" placeholder="사용할 아이디" required autofocus>
                    <input type="password" name="password" placeholder="비밀번호" required>
                    <input type="password" name="password_confirm" placeholder="비밀번호 확인" required>
                    <button type="submit">가입하기</button>
                </form>
                <div style="margin-top: 15px; font-size: 13px;">
                    <span style="color: #888;">이미 계정이 있으신가요?</span> 
                    <a href="{{ url_for('login') }}" style="color: #b388ff; text-decoration: none; font-weight: bold;">로그인</a>
                </div>
            </div>
        </body>
        </html>
    ''', error_message=error_message, success_message=success_message)

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    session.pop('username', None) # ⭐️ 로그아웃 시 계정 정보 완벽 파기
    session.pop('is_admin', None)
    if request.args.get('timeout'):
        return redirect(url_for('login', timeout=1))
    return redirect(url_for('login'))

@app.route('/')
def index():
    app.logger.debug("index() route 호출됨: stock-memo.html 파일을 반환합니다.")
    return send_from_directory('.', 'stock-memo.html')

@app.route('/api/ping', methods=['POST'])
def ping():
    # 세션 갱신을 위한 엔드포인트. 요청이 오면 세션 수명 1시간이 다시 연장됨
    session.modified = True
    return jsonify({"status": "success"})

@app.route('/api/me', methods=['GET'])
def get_me():
    username = session.get('username')
    pending_count = 0
    is_admin = False
    
    if username:
        conn = get_db()
        c = conn.cursor()
        # ⭐️ 매 요청 시마다 DB에서 최신 관리자 권한을 조회하여 세션 동기화
        c.execute("SELECT is_admin FROM users WHERE username = ?", (username,))
        user = c.fetchone()
        if user:
            is_admin = bool(user['is_admin'])
            session['is_admin'] = is_admin # 브라우저 세션에 즉각 갱신 반영
            
        if is_admin:
            c.execute("SELECT COUNT(*) FROM users WHERE is_allowed = 0")
            pending_count = c.fetchone()[0]
        conn.close()
        
    return jsonify({"username": username, "is_admin": is_admin, "pending_count": pending_count})

@app.route('/api/account', methods=['DELETE'])
def delete_account():
    username = session.get('username')
    is_admin_flag = session.get('is_admin', False)
    # ⭐️ 최고 관리자 계정은 탈퇴할 수 없도록 보호
    if is_admin_flag:
        return jsonify({"error": "최고 관리자 계정은 탈퇴할 수 없습니다."}), 403

    data = request.json or {}
    password = data.get('password')
    if not password:
        return jsonify({"error": "비밀번호를 입력해주세요."}), 400

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT password_hash FROM users WHERE username = ?", (username,))
    user_record = c.fetchone()
    
    if not user_record or not check_password_hash(user_record['password_hash'], password):
        conn.close()
        return jsonify({"error": "비밀번호가 일치하지 않습니다."}), 400
        
    # 사용자 데이터 및 계정 삭제
    c.execute("DELETE FROM entries WHERE username = ?", (username,))
    c.execute("DELETE FROM users WHERE username = ?", (username,))
    conn.commit()
    conn.close()
    
    # 사용자 전용 업로드 폴더 삭제
    user_folder = os.path.join(UPLOAD_FOLDER, username)
    if os.path.exists(user_folder):
        shutil.rmtree(user_folder)
        
    session.pop('logged_in', None)
    session.pop('username', None)
    return jsonify({"status": "success"})

def is_admin():
    return session.get('is_admin', False)

@app.route('/api/admin/users', methods=['GET'])
def admin_get_users():
    if not is_admin():
        return jsonify({"error": "Unauthorized"}), 403
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        SELECT u.username, u.is_allowed, u.is_admin, u.created_at, u.last_login_at, COUNT(e.id) as entry_count
        FROM users u
        LEFT JOIN entries e ON u.username = e.username
        GROUP BY u.username
    ''')
    users = [dict(row) for row in c.fetchall()]
    conn.close()
    return jsonify(users)

@app.route('/api/admin/users/<target_username>', methods=['DELETE'])
def admin_delete_user(target_username):
    if not is_admin():
        return jsonify({"error": "Unauthorized"}), 403
        
    conn = get_db()
    c = conn.cursor()
    
    c.execute("SELECT is_admin FROM users WHERE username = ?", (target_username,))
    target_user = c.fetchone()
    if target_user and target_user['is_admin']:
        conn.close()
        return jsonify({"error": "최고 관리자는 삭제할 수 없습니다."}), 400
        
    c.execute("DELETE FROM entries WHERE username = ?", (target_username,))
    c.execute("DELETE FROM users WHERE username = ?", (target_username,))
    conn.commit()
    conn.close()
    
    user_folder = os.path.join(UPLOAD_FOLDER, target_username)
    if os.path.exists(user_folder):
        shutil.rmtree(user_folder)
        
    return jsonify({"status": "success"})

@app.route('/api/admin/users/<target_username>/toggle_allow', methods=['POST'])
def admin_toggle_allow(target_username):
    if not is_admin():
        return jsonify({"error": "Unauthorized"}), 403
        
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT is_allowed, is_admin FROM users WHERE username = ?", (target_username,))
    user = c.fetchone()
    if not user:
        conn.close()
        return jsonify({"error": "사용자를 찾을 수 없습니다."}), 404
        
    if user['is_admin']:
        conn.close()
        return jsonify({"error": "최고 관리자의 상태는 변경할 수 없습니다."}), 400
        
    new_status = 0 if user['is_allowed'] else 1
    c.execute("UPDATE users SET is_allowed = ? WHERE username = ?", (new_status, target_username))
    conn.commit()
    conn.close()
    
    return jsonify({"status": "success", "is_allowed": new_status})

@app.route('/api/admin/users/<target_username>/reset_password', methods=['POST'])
def admin_reset_password(target_username):
    if not is_admin():
        return jsonify({"error": "Unauthorized"}), 403
        
    # 8자리의 무작위 영문+숫자 임시 비밀번호 생성
    new_password = uuid.uuid4().hex[:8]
    hashed_pw = generate_password_hash(new_password)
    
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE users SET password_hash = ? WHERE username = ?", (hashed_pw, target_username))
    conn.commit()
    conn.close()
    
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
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM entries WHERE username = ? ORDER BY id DESC", (username,))
    rows = c.fetchall()
    data = [dict(row) for row in rows]
    conn.close()
    return jsonify(data)

@app.route('/api/entry', methods=['POST'])
def create_entry():
    username = session.get('username')
    entry = request.json
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        INSERT INTO entries 
        (id, username, type, stockName, stockCode, title, thoughts, date, rawDate, attachedImage, brokerAccount, subAccount, accountName, tradeType, price, quantity, createdAt, updatedAt, tags, attachedFile, attachedFileName)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        entry.get('id'), username, entry.get('type'), entry.get('stockName'), entry.get('stockCode', ''), entry.get('title'),
        entry.get('thoughts'), entry.get('date'), entry.get('rawDate'), entry.get('attachedImage'),
        entry.get('brokerAccount'), entry.get('subAccount', ''), entry.get('accountName'), entry.get('tradeType'),
        entry.get('price', 0), entry.get('quantity', 0),
        entry.get('createdAt'), entry.get('updatedAt'), entry.get('tags', ''),
        entry.get('attachedFile', ''), entry.get('attachedFileName', '')
    ))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

@app.route('/api/entry/<int:entry_id>', methods=['PUT'])
def update_entry(entry_id):
    username = session.get('username')
    entry = request.json
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        UPDATE entries SET
        type=?, stockName=?, stockCode=?, title=?, thoughts=?, date=?, rawDate=?, attachedImage=?, brokerAccount=?, subAccount=?, accountName=?, tradeType=?, price=?, quantity=?, updatedAt=?, tags=?, attachedFile=?, attachedFileName=?
        WHERE id=? AND username=?
    ''', (
        entry.get('type'), entry.get('stockName'), entry.get('stockCode', ''), entry.get('title'),
        entry.get('thoughts'), entry.get('date'), entry.get('rawDate'), entry.get('attachedImage'),
        entry.get('brokerAccount'), entry.get('subAccount', ''), entry.get('accountName'), entry.get('tradeType'),
        entry.get('price', 0), entry.get('quantity', 0),
        entry.get('updatedAt'), entry.get('tags', ''),
        entry.get('attachedFile', ''), entry.get('attachedFileName', ''),
        entry_id, username
    ))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

@app.route('/api/entry/<int:entry_id>', methods=['DELETE'])
def delete_entry(entry_id):
    username = session.get('username')
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM entries WHERE id=? AND username=?", (entry_id, username))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

@app.route('/api/current_price', methods=['POST'])
def get_current_price():
    data = request.json or {}
    codes = data.get('codes', [])
    market_mode = data.get('market_mode', 'AUTO')
    prices = {}
    
    # ⭐️ 시간외 단일가 유지를 위한 DB 캐시 저장/조회 헬퍼 함수
    def save_price_cache(code_val, price_val):
        try:
            conn = get_db()
            c = conn.cursor()
            now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            c.execute("REPLACE INTO price_cache (code, price, updated_at) VALUES (?, ?, ?)", (code_val, price_val, now_str))
            conn.commit()
            conn.close()
        except Exception:
            pass
            
    def load_price_cache(code_val):
        try:
            conn = get_db()
            c = conn.cursor()
            c.execute("SELECT price FROM price_cache WHERE code = ?", (code_val,))
            row = c.fetchone()
            conn.close()
            if row:
                return row['price']
        except Exception:
            pass
        return None

    def fetch_price(code):
        code_str = str(code).strip().upper()
        if not code_str: return None, None
        try:
            # ⭐️ KRX 금현물(1g) 전용 처리
            if code_str in ['KRXGOLD', 'GOLD']:
                try:
                    # 1순위: 네이버 증권의 새로운 금 시세 API (가장 안정적)
                    new_naver_gold_url = "https://api.stock.naver.com/marketindex/metals/M04020000"
                    req = urllib.request.Request(new_naver_gold_url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req, timeout=3) as response:
                        res_data = json.loads(response.read())
                        price_str = res_data.get('closePrice', '')
                        if price_str:
                            return code, float(price_str.replace(',', ''))
                except Exception:
                    pass

                try:
                    # 2순위: 한국거래소(KRX) 공식 웹사이트 직접 스크래핑 (백업)
                    krx_url = "https://www.krx.co.kr/contents/COM/Finance/KRX_Gold_Market.jsp"
                    krx_req = urllib.request.Request(krx_url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(krx_req, timeout=3) as krx_res:
                        html = krx_res.read().decode('utf-8', errors='ignore')
                        match = re.search(r'현재가</th>\s*<td[^>]*>\s*<strong>([\d,]+)</strong>', html)
                        if match:
                            return code, float(match.group(1).replace(',', ''))
                except Exception:
                    pass

                return code, None

            # ⭐️ 제공해주신 종목코드/티커 국가 구분 로직 적용
            market_type = "UNKNOWN"
            if re.fullmatch(r'^[A-Z\.\-]{1,6}$', code_str):
                market_type = "US"
            elif len(code_str) == 6 and re.fullmatch(r'^\d{5}[0-9A-Z]$', code_str):
                market_type = "KR"
            elif len(code_str) == 6 and code_str.isalnum():
                market_type = "KR" # 예외: 0162Z0 등 영문 혼합 국내 신주인수권/ETN 포괄용
            elif re.fullmatch(r'^\d+$', code_str):
                market_type = "OTHER_ASIAN"

            if market_type == "KR":
                import datetime
                kst_now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)
                time_num = kst_now.hour * 100 + kst_now.minute
                
                # ⭐️ 사용자가 선택한 시장 모드(KRX/NXT)에 따라 장외 시간 적용 강제/해제
                is_out_of_hours = not (900 <= time_num < 1530)
                if market_mode == 'KRX':
                    is_out_of_hours = False
                elif market_mode == 'NXT':
                    is_out_of_hours = True

                # ⭐️ 모바일 API 전용 위장 헤더 (PC 스크래핑을 제거하여 봇 차단 원천 방지)
                api_headers = {
                    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1',
                    'Accept': 'application/json, text/plain, */*',
                    'Referer': 'https://m.stock.naver.com/'
                }

                try:
                    # ⭐️ 정규장 실시간 시세: 모바일 API의 CDN 지연(1~3분)을 완벽히 우회하기 위해 PC용 실시간 siseJson API 최우선 호출
                    if not is_out_of_hours:
                        try:
                            sise_url = f"https://api.finance.naver.com/siseJson.naver?symbol={code_str}&requestType=1"
                            sise_req = urllib.request.Request(sise_url, headers={'User-Agent': 'Mozilla/5.0'})
                            with urllib.request.urlopen(sise_req, timeout=3) as sise_res:
                                sise_data = sise_res.read().decode('euc-kr', errors='ignore')
                                # 정규식을 통해 nowVal 추출 (캐시 없는 실시간 현재가)
                                match = re.search(r'"nowVal"\s*:\s*(\d+)', sise_data)
                                if match:
                                    realtime_price = float(match.group(1))
                                    if realtime_price > 0:
                                        return code, realtime_price
                        except Exception:
                            pass

                    # 국내 주식 (네이버 증권 최신 API 적용 및 캐시 방지 파라미터 추가)
                    ts = int(time.time() * 1000)
                    url = f"https://m.stock.naver.com/api/stock/{code_str}/basic?_={ts}"
                    req = urllib.request.Request(url, headers=api_headers)
                    with urllib.request.urlopen(req, timeout=3) as response:
                        res_data = json.loads(response.read())
                        price_str = str(res_data.get('closePrice', ''))
                        
                        # ⭐️ 정규장 외 시간(혹은 NXT 수동 모드)일 경우 overPrice 적용
                        if is_out_of_hours and price_str and price_str != '0':
                            over_info = res_data.get('overMarketPriceInfo', {})
                            if isinstance(over_info, dict) and over_info.get('overPrice'):
                                price_str = str(over_info.get('overPrice'))
                                # ⭐️ 정상적인 시간외 종가를 가져왔다면 다음 날 아침을 대비해 DB 캐시에 저장
                                save_price_cache(code_str, float(price_str.replace(',', '')))
                            else:
                                # ⭐️ 자정 이후 등 API 데이터가 비워졌을 경우, 최우선적으로 DB 캐시에서 전일 시간외 종가 획득 시도
                                cached_price = load_price_cache(code_str)
                                if cached_price:
                                    return code, cached_price
                                
                                # 캐시가 없을 경우 PC 웹 크롤링으로 획득 시도 (최후의 보루)
                                try:
                                    pc_url = f"https://finance.naver.com/item/main.naver?code={code_str}"
                                    pc_req = urllib.request.Request(pc_url, headers={'User-Agent': 'Mozilla/5.0'})
                                    with urllib.request.urlopen(pc_req, timeout=3) as pc_res:
                                        html = pc_res.read().decode('euc-kr', errors='ignore')
                                        # 네이버 금융 PC 버전의 '시간외단일가' 영역 파싱
                                        match = re.search(r'시간외단일가.*?<span class="blind">([\d,]+)</span>', html, re.DOTALL)
                                        if match:
                                            price_str = match.group(1)
                                            save_price_cache(code_str, float(price_str.replace(',', '')))
                                except Exception:
                                    pass
                        
                    if price_str and price_str != '0':
                        return code, float(price_str.replace(',', ''))
                except Exception:
                    # ⭐️ 네이버 API 조회에 완전히 실패(통신 에러 등)했을 경우에도 장외 시간이라면 DB 캐시를 최후의 보루로 사용
                    if is_out_of_hours:
                        cached_price = load_price_cache(code_str)
                        if cached_price:
                            return code, cached_price

            # ⭐️ US, OTHER_ASIAN, UNKNOWN 이거나 국내 API에서 조회 실패한 경우 야후 파이낸스 호출
            try:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{code_str}"
                req = urllib.request.Request(url, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
                })
                with urllib.request.urlopen(req, timeout=3) as response:
                    res_data = json.loads(response.read())
                    price = res_data['chart']['result'][0]['meta']['regularMarketPrice']
                    return code, float(price)
            except Exception:
                pass
                
            return code, None
            
        except Exception: 
            return code, None
            
    # ⭐️ 스레드 풀을 활용한 병렬(비동기) 처리로 다수 종목 조회 속도 대폭 개선
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        results = executor.map(fetch_price, codes)
        for code, price in results:
            if code is not None:
                prices[code] = price
                
    return jsonify(prices)

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
        
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT password_hash FROM users WHERE username = ?", (username,))
    user_record = c.fetchone()
    
    if not user_record or not check_password_hash(user_record['password_hash'], current_password):
        conn.close()
        return jsonify({"error": "현재 비밀번호가 일치하지 않습니다."}), 400
        
    hashed_pw = generate_password_hash(new_password)
    c.execute("UPDATE users SET password_hash = ? WHERE username = ?", (hashed_pw, username))
    conn.commit()
    conn.close()
    
    return jsonify({"status": "success"})

@app.route('/api/preferences', methods=['GET'])
def get_preferences():
    username = session.get('username')
    if not username:
        return jsonify({}), 401
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT preferences FROM users WHERE username = ?", (username,))
    row = c.fetchone()
    conn.close()
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
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE users SET preferences = ? WHERE username = ?", (json.dumps(prefs), username))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

# ⭐️ 뉴스 검색 결과를 임시 보관할 캐시 딕셔너리와 유효 시간(초) 설정
news_cache = {}
NEWS_CACHE_TTL = 600  # 10분(600초) 동안 캐시 유지

@app.route('/api/news', methods=['POST'])
def get_news():
    data = request.json or {}
    stocks = data.get('stocks', [])
    
    # 보유 종목이 없을 경우 기본 검색어 사용
    if not stocks:
        stocks = ['국내 증시']
        
    def fetch_news_for_stock(stock):
        current_time = time.time()
        
        # ⭐️ 1. 캐시에 데이터가 있고, 유효 시간(10분)이 지나지 않았다면 구글에 요청하지 않고 캐시 반환
        if stock in news_cache:
            cached_data, timestamp = news_cache[stock]
            if current_time - timestamp < NEWS_CACHE_TTL:
                return cached_data
                
        news_list = []
        try:
            # ⭐️ 네이버 RSS 서비스 전면 종료(404)에 따라 안정적인 구글 뉴스 RSS로 복귀
            query = urllib.parse.quote(f"{stock} when:7d")
            url = f"https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=3) as response:
                xml_data = response.read()
                root = ET.fromstring(xml_data)
                for idx, item in enumerate(root.findall('.//item')):
                    if idx >= 3: break
                    
                    news_list.append({
                        'stock': stock,
                        'title': item.find('title').text,
                        'link': item.find('link').text,
                        'pubDate': item.find('pubDate').text
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
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM entries WHERE username = ?", (username,))
    rows = [dict(row) for row in c.fetchall()]
    conn.close()
    
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
    if 'file' not in request.files:
        return jsonify({'error': '업로드된 파일이 없습니다.'}), 400
        
    file = request.files['file']
    if file.filename == '' or not file.filename.endswith('.zip'):
        return jsonify({'error': '유효하지 않은 파일입니다. .zip 백업 파일을 업로드해주세요.'}), 400
        
    temp_dir = tempfile.mkdtemp()
    try:
        with zipfile.ZipFile(file, 'r') as zf:
            zf.extractall(temp_dir)
            
        json_path = os.path.join(temp_dir, 'data.json')
        if not os.path.exists(json_path):
            return jsonify({'error': '손상된 백업 파일입니다. (data.json을 찾을 수 없습니다)'}), 400
            
        with open(json_path, 'r', encoding='utf-8') as f:
            entries = json.load(f)
            
        conn = get_db()
        c = conn.cursor()
        
        # 1. 기존 사용자의 데이터만 삭제
        c.execute("DELETE FROM entries WHERE username = ?", (username,))
        
        # 2. 복원할 데이터 삽입
        for entry in entries:
            c.execute('''
                INSERT INTO entries 
                (id, username, type, stockName, stockCode, title, thoughts, date, rawDate, attachedImage, brokerAccount, subAccount, accountName, tradeType, price, quantity, createdAt, updatedAt, tags, attachedFile, attachedFileName)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                entry.get('id'), username, entry.get('type'), entry.get('stockName'), entry.get('stockCode', ''), entry.get('title'),
                entry.get('thoughts'), entry.get('date'), entry.get('rawDate'), entry.get('attachedImage'),
                entry.get('brokerAccount'), entry.get('subAccount', ''), entry.get('accountName'), entry.get('tradeType'),
                entry.get('price', 0), entry.get('quantity', 0),
                entry.get('createdAt'), entry.get('updatedAt'), entry.get('tags', ''),
                entry.get('attachedFile', ''), entry.get('attachedFileName', '')
            ))
        conn.commit()
        conn.close()
        
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
