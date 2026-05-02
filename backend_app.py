#!/usr/bin/env python3
import sys
# tmux 등 환경에서 이모지 출력 시 발생하는 UnicodeEncodeError 방지를 위해 표준 출력을 강제로 UTF-8로 지정
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

import json
import uuid
import os
import sqlite3
import base64
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
import time
import zipfile
import io
import tempfile
import shutil
import re
from datetime import timedelta
from flask import Flask, jsonify, request, send_from_directory, session, redirect, url_for, render_template_string, send_file
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__, static_folder='.', static_url_path='')
app.secret_key = 'stock_memo_secret_key' # 세션 유지를 위한 시크릿 키 설정

# ⭐️ 세션(쿠키) 보안 설정 강화
app.config['SESSION_COOKIE_HTTPONLY'] = True  # 자바스크립트(XSS)로 쿠키 접근 원천 차단
app.config['SESSION_COOKIE_SECURE'] = False   # ⭐️ 로컬(HTTP) 환경 접속 시 로그인 갱신 오류 방지를 위해 비활성화
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax' # CSRF(크로스 사이트 요청 위조) 공격 방어
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=1) # ⭐️ 세션 유효 기간 1시간으로 설정

DATA_FILE = 'my_stock_trading_journal.json'
DB_DIR = 'db'
DB_FILE = os.path.join(DB_DIR, 'journal.db')
UPLOAD_FOLDER = 'uploads'

# 필요한 폴더들 생성
os.makedirs(DB_DIR, exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

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
        
    # 다중 사용자 격리를 위한 컬럼 추가
    try:
        c.execute("ALTER TABLE entries ADD COLUMN username TEXT")
    except sqlite3.OperationalError:
        pass
    
    c.execute("UPDATE entries SET username = 'batmi' WHERE username IS NULL")
        
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
    c.execute("UPDATE users SET is_allowed = 1 WHERE username = 'batmi'")
    conn.commit()
    
    # ⭐️ 기본 사용자(batmi) 계정이 없으면 암호화하여 DB에 생성
    c.execute("SELECT COUNT(*) FROM users WHERE username = 'batmi'")
    if c.fetchone()[0] == 0:
        default_hash = generate_password_hash('ghkswn96')
        current_time = time.strftime('%Y-%m-%d %H:%M:%S')
        c.execute("INSERT INTO users (username, password_hash, is_allowed, created_at) VALUES (?, ?, 1, ?)", ('batmi', default_hash, current_time))
        conn.commit()
        print("🔒 기본 관리자 계정(batmi)이 DB에 안전하게 암호화되어 생성되었습니다.")
    
    # 기존 JSON 파일이 있고 DB가 비어있다면 자동 마이그레이션 수행
    c.execute("SELECT COUNT(*) FROM entries")
    if c.fetchone()[0] == 0 and os.path.exists(DATA_FILE):
        print("🔄 기존 JSON 데이터를 SQLite 데이터베이스로 마이그레이션 합니다...")
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            try:
                old_data = json.load(f)
                for entry in old_data:
                    img_url = process_image(entry.get('attachedImage'), entry.get('id'))
                    c.execute('''
                        INSERT INTO entries 
                        (id, username, type, stockName, stockCode, title, thoughts, date, rawDate, attachedImage, brokerAccount, accountName, tradeType, price, quantity, createdAt, updatedAt, tags, attachedFile, attachedFileName)
                        VALUES (?, 'batmi', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        entry.get('id'), entry.get('type'), entry.get('stockName'), entry.get('stockCode', ''), entry.get('title'),
                        entry.get('thoughts'), entry.get('date'), entry.get('rawDate'), img_url,
                        entry.get('brokerAccount'), entry.get('accountName'), entry.get('tradeType'),
                        entry.get('price', 0), entry.get('quantity', 0),
                        entry.get('createdAt'), entry.get('updatedAt'), entry.get('tags', ''),
                        entry.get('attachedFile', ''), entry.get('attachedFileName', '')
                    ))
                conn.commit()
                print("✅ 데이터 마이그레이션 완료! (이제부터 db/journal.db와 uploads 폴더를 사용합니다)")
            except Exception as e:
                print(f"❌ 마이그레이션 중 오류 발생: {e}")
    conn.close()

@app.before_request
def check_login():
    # 로그인 및 회원가입 처리를 수행하는 라우트는 검사에서 제외
    if request.endpoint not in ['login', 'signup']:
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
            c.execute("SELECT password_hash, is_allowed FROM users WHERE username = ?", (username,))
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
                    padding: 12px 24px;
                    border-radius: 8px;
                    box-shadow: 0 4px 15px rgba(231, 76, 60, 0.4);
                    font-size: 14px;
                    font-weight: bold;
                    z-index: 1000;
                    backdrop-filter: blur(5px);
                    -webkit-backdrop-filter: blur(5px);
                    opacity: 0;
                    pointer-events: none;
                    animation: slideDownFadeOut 3.5s ease-in-out forwards;
                }
                @keyframes slideDownFadeOut {
                    0% { top: -20px; opacity: 0; }
                    10% { top: 20px; opacity: 1; }
                    80% { top: 20px; opacity: 1; }
                    100% { top: -20px; opacity: 0; }
                }
            </style>
        </head>
        <body>
            {% if error_message %}
            <div class="error-banner">⚠️ {{ error_message }}</div>
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
        </body>
        </html>
    ''', error_message=error_message)

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
            c.execute("SELECT id FROM users WHERE username = ?", (username,))
            if c.fetchone():
                error_message = "이미 존재하는 아이디입니다."
            else:
                hashed_pw = generate_password_hash(password)
                current_time = time.strftime('%Y-%m-%d %H:%M:%S')
                c.execute("INSERT INTO users (username, password_hash, is_allowed, created_at) VALUES (?, ?, 0, ?)", (username, hashed_pw, current_time))
                conn.commit()
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
                .error-banner { position: fixed; top: 20px; left: 50%; transform: translateX(-50%); background: rgba(231, 76, 60, 0.95); color: white; padding: 12px 24px; border-radius: 8px; box-shadow: 0 4px 15px rgba(231, 76, 60, 0.4); font-size: 14px; font-weight: bold; z-index: 1000; backdrop-filter: blur(5px); -webkit-backdrop-filter: blur(5px); opacity: 0; pointer-events: none; animation: slideDownFadeOut 3.5s ease-in-out forwards; }
                @keyframes slideDownFadeOut { 0% { top: -20px; opacity: 0; } 10% { top: 20px; opacity: 1; } 80% { top: 20px; opacity: 1; } 100% { top: -20px; opacity: 0; } }
            </style>
        </head>
        <body>
            {% if error_message %}
            <div class="error-banner">⚠️ {{ error_message }}</div>
            {% endif %}
            {% if success_message %}
            <div class="error-banner" style="background: rgba(39, 174, 96, 0.95); box-shadow: 0 4px 15px rgba(39, 174, 96, 0.4); animation: none; opacity: 1; top: 20px;">✅ {{ success_message }}</div>
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
    return redirect(url_for('login'))

@app.route('/')
def index():
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
    if username == 'batmi':
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM users WHERE is_allowed = 0")
        pending_count = c.fetchone()[0]
        conn.close()
    return jsonify({"username": username, "pending_count": pending_count})

@app.route('/api/account', methods=['DELETE'])
def delete_account():
    username = session.get('username')
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
    return session.get('username') == 'batmi'

@app.route('/api/admin/users', methods=['GET'])
def admin_get_users():
    if not is_admin():
        return jsonify({"error": "Unauthorized"}), 403
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        SELECT u.username, u.is_allowed, u.created_at, u.last_login_at, COUNT(e.id) as entry_count
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
    if target_username == 'batmi':
        return jsonify({"error": "최고 관리자는 삭제할 수 없습니다."}), 400
        
    conn = get_db()
    c = conn.cursor()
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
    if target_username == 'batmi':
        return jsonify({"error": "최고 관리자의 상태는 변경할 수 없습니다."}), 400
        
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT is_allowed FROM users WHERE username = ?", (target_username,))
    user = c.fetchone()
    if not user:
        conn.close()
        return jsonify({"error": "사용자를 찾을 수 없습니다."}), 404
        
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
    return send_from_directory(user_folder, filename)

@app.route('/api/upload', methods=['POST'])
def upload_file():
    username = session.get('username')
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    filename = os.path.basename(file.filename)
    safe_name = f"{int(time.time())}_{uuid.uuid4().hex[:6]}_{filename.replace(' ', '_')}"
    
    user_folder = os.path.join(UPLOAD_FOLDER, username)
    os.makedirs(user_folder, exist_ok=True)
    filepath = os.path.join(user_folder, safe_name)
    file.save(filepath)
    return jsonify({'url': f'/uploads/{username}/{safe_name}', 'filename': file.filename})

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
        (id, username, type, stockName, stockCode, title, thoughts, date, rawDate, attachedImage, brokerAccount, accountName, tradeType, price, quantity, createdAt, updatedAt, tags, attachedFile, attachedFileName)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        entry.get('id'), username, entry.get('type'), entry.get('stockName'), entry.get('stockCode', ''), entry.get('title'),
        entry.get('thoughts'), entry.get('date'), entry.get('rawDate'), entry.get('attachedImage'),
        entry.get('brokerAccount'), entry.get('accountName'), entry.get('tradeType'),
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
        type=?, stockName=?, stockCode=?, title=?, thoughts=?, date=?, rawDate=?, attachedImage=?, brokerAccount=?, accountName=?, tradeType=?, price=?, quantity=?, updatedAt=?, tags=?, attachedFile=?, attachedFileName=?
        WHERE id=? AND username=?
    ''', (
        entry.get('type'), entry.get('stockName'), entry.get('stockCode', ''), entry.get('title'),
        entry.get('thoughts'), entry.get('date'), entry.get('rawDate'), entry.get('attachedImage'),
        entry.get('brokerAccount'), entry.get('accountName'), entry.get('tradeType'),
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
    c.execute("SELECT attachedFile FROM entries WHERE id=? AND username=?", (entry_id, username))
    row = c.fetchone()
    if row and row['attachedFile'] and row['attachedFile'].startswith(f'/uploads/{username}/'):
        filepath = os.path.join(UPLOAD_FOLDER, username, os.path.basename(row['attachedFile']))
        if os.path.exists(filepath):
            os.remove(filepath)
    c.execute("DELETE FROM entries WHERE id=? AND username=?", (entry_id, username))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

@app.route('/api/current_price', methods=['POST'])
def get_current_price():
    data = request.json or {}
    codes = data.get('codes', [])
    prices = {}
    for code in codes:
        code_str = str(code).strip().upper()
        if not code_str: continue
        try:
            # ⭐️ KRX 금현물(1g) 전용 처리
            if code_str in ['KRXGOLD', 'GOLD']:
                price_found = False

                try:
                    # 1순위: 네이버 증권의 새로운 금 시세 API (가장 안정적)
                    new_naver_gold_url = "https://api.stock.naver.com/marketindex/metals/M04020000"
                    req = urllib.request.Request(new_naver_gold_url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req, timeout=3) as response:
                        res_data = json.loads(response.read())
                        price_str = res_data.get('closePrice', '')
                        if price_str:
                            prices[code] = float(price_str.replace(',', ''))
                            price_found = True
                except Exception:
                    pass

                if not price_found:
                    try:
                        # 2순위: 한국거래소(KRX) 공식 웹사이트 직접 스크래핑 (백업)
                        krx_url = "https://www.krx.co.kr/contents/COM/Finance/KRX_Gold_Market.jsp"
                        krx_req = urllib.request.Request(krx_url, headers={'User-Agent': 'Mozilla/5.0'})
                        with urllib.request.urlopen(krx_req, timeout=3) as krx_res:
                            html = krx_res.read().decode('utf-8', errors='ignore')
                            match = re.search(r'현재가</th>\s*<td[^>]*>\s*<strong>([\d,]+)</strong>', html)
                            if match:
                                prices[code] = float(match.group(1).replace(',', ''))
                                price_found = True
                    except Exception:
                        pass

                if not price_found:
                    prices[code] = None

                continue

            if code_str.isdigit():
                # 국내 주식 (네이버 금융 API)
                url = f"https://m.stock.naver.com/api/stock/{code_str}/basic"
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=3) as response:
                    res_data = json.loads(response.read())
                    price_str = res_data.get('closePrice', '')
                    
                if price_str and price_str != '0':
                    prices[code] = float(price_str.replace(',', ''))
                else:
                    # ⭐️ 일반 API에서 누락되는 종목을 위한 실시간 Polling API(폴백) 처리
                    fallback_url = f"https://polling.finance.naver.com/api/realtime?query=SERVICE_ITEM:{code_str}"
                    req2 = urllib.request.Request(fallback_url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req2, timeout=3) as response2:
                        res_data2 = json.loads(response2.read())
                        areas2 = res_data2.get('result', {}).get('areas', [])
                        if areas2 and areas2[0].get('datas'):
                            prices[code] = float(areas2[0]['datas'][0].get('nv', 0))
                        else:
                            prices[code] = None
            else:
                # 해외 주식 (야후 파이낸스 API)
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{code_str}"
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=3) as response:
                    res_data = json.loads(response.read())
                    price = res_data['chart']['result'][0]['meta']['regularMarketPrice']
                    prices[code] = float(price)
        except Exception: prices[code] = None
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

@app.route('/api/news', methods=['POST'])
def get_news():
    data = request.json or {}
    stocks = data.get('stocks', [])
    
    # 보유 종목이 없을 경우 기본 검색어 사용
    if not stocks:
        stocks = ['국내 증시']
        
    all_news = []
    for stock in stocks:
        try:
            # 최신 뉴스를 우선적으로 가져오기 위해 최근 7일(when:7d) 조건 추가
            query = urllib.parse.quote(f"{stock} when:7d")
            url = f"https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=3) as response:
                xml_data = response.read()
                root = ET.fromstring(xml_data)
                channel = root.find('channel')
                if channel:
                    for idx, item in enumerate(channel.findall('item')):
                        if idx >= 3:  # 종목당 최대 3개의 주요 뉴스만 가져오기
                            break
                        all_news.append({
                            'stock': stock,
                            'title': item.find('title').text,
                            'link': item.find('link').text,
                            'pubDate': item.find('pubDate').text
                        })
        except Exception as e:
            print(f"Error fetching news for {stock}: {e}")
            
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
                (id, username, type, stockName, stockCode, title, thoughts, date, rawDate, attachedImage, brokerAccount, accountName, tradeType, price, quantity, createdAt, updatedAt, tags, attachedFile, attachedFileName)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                entry.get('id'), username, entry.get('type'), entry.get('stockName'), entry.get('stockCode', ''), entry.get('title'),
                entry.get('thoughts'), entry.get('date'), entry.get('rawDate'), entry.get('attachedImage'),
                entry.get('brokerAccount'), entry.get('accountName'), entry.get('tradeType'),
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
    
    port = 5000
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            print(f"⚠️ 경고: 잘못된 포트 번호('{sys.argv[1]}')가 입력되어 기본 포트(5000)로 실행합니다.")
            
    print(f"🚀 로컬 주식 매매 일지 서버를 시작합니다. (포트: {port})")
    print(f"👉 웹 브라우저를 열고 http://127.0.0.1:{port} 또는 기기의 로컬 IP 주소(예: 192.168.x.x:{port})로 접속해주세요.")
    app.run(host='0.0.0.0', debug=True, port=port)
