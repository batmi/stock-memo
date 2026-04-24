#!/usr/bin/env python3
import json
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
from flask import Flask, jsonify, request, send_from_directory, session, redirect, url_for, render_template_string, send_file
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__, static_folder='.', static_url_path='')
app.secret_key = 'stock_memo_secret_key' # 세션 유지를 위한 시크릿 키 설정

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
            type TEXT,
            stockName TEXT,
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
            tags TEXT
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
        c.execute("ALTER TABLE entries ADD COLUMN tags TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    
    # ⭐️ 기본 사용자(batmi) 계정이 없으면 암호화하여 DB에 생성
    c.execute("SELECT COUNT(*) FROM users WHERE username = 'batmi'")
    if c.fetchone()[0] == 0:
        default_hash = generate_password_hash('ghkswn96')
        c.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", ('batmi', default_hash))
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
                        (id, type, stockName, title, thoughts, date, rawDate, attachedImage, brokerAccount, accountName, tradeType, price, quantity, createdAt, updatedAt, tags)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        entry.get('id'), entry.get('type'), entry.get('stockName'), entry.get('title'),
                        entry.get('thoughts'), entry.get('date'), entry.get('rawDate'), img_url,
                        entry.get('brokerAccount'), entry.get('accountName'), entry.get('tradeType'),
                        entry.get('price', 0), entry.get('quantity', 0),
                        entry.get('createdAt'), entry.get('updatedAt'), entry.get('tags', '')
                    ))
                conn.commit()
                print("✅ 데이터 마이그레이션 완료! (이제부터 db/journal.db와 uploads 폴더를 사용합니다)")
            except Exception as e:
                print(f"❌ 마이그레이션 중 오류 발생: {e}")
    conn.close()

@app.before_request
def check_login():
    # 로그인 처리를 수행하는 라우트는 검사에서 제외
    if request.endpoint != 'login':
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
    
    if request.method == 'POST':
        # 현재 차단된 상태인지 확인
        if current_time < record['lockout_until']:
            remaining = int(record['lockout_until'] - current_time)
            return render_template_string('''
                <script>
                    alert("로그인 5회 실패로 차단되었습니다.\\n{{ remaining }}초 후에 다시 시도해주세요.");
                    window.location.href = "{{ url_for('login') }}";
                </script>
            ''', remaining=remaining)
            
        username = request.form.get('username')
        password = request.form.get('password')
        
        # DB에서 입력한 아이디와 일치하는 암호화된 비밀번호 조회
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT password_hash FROM users WHERE username = ?", (username,))
        user_record = c.fetchone()
        conn.close()
        
        # 계정이 존재하고, 입력한 비밀번호와 DB의 해시값이 일치하는지 검증
        if user_record and check_password_hash(user_record['password_hash'], password):
            record['count'] = 0
            record['lockout_until'] = 0
            session['logged_in'] = True
            return redirect(url_for('index'))
        else:
            record['count'] += 1
            if record['count'] >= 5:
                record['lockout_until'] = current_time + 60
                return render_template_string('''
                    <script>
                        alert("비밀번호 5회 연속 실패! 1분 동안 로그인이 차단됩니다.");
                        window.location.href = "{{ url_for('login') }}";
                    </script>
                ''')
                
            return render_template_string('''
                <script>
                    alert("아이디 또는 비밀번호가 일치하지 않습니다. (실패 횟수: {{ count }}/5)");
                    window.location.href = "{{ url_for('login') }}";
                </script>
            ''', count=record['count'])
    
    return render_template_string('''
        <!DOCTYPE html>
        <html lang="ko">
        <head>
            <meta charset="UTF-8">
            <title>주식 매매 일지 - 로그인</title>
            <link rel="shortcut icon" href="https://ssl.gstatic.com/finance/favicon/finance_496x496.png">
            <link rel="icon" type="image/png" href="https://ssl.gstatic.com/finance/favicon/finance_496x496.png">
            <link rel="apple-touch-icon" sizes="180x180" href="https://ssl.gstatic.com/finance/favicon/finance_496x496.png">
            <style>
                body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; background-color: #121212; margin: 0; color: #ccc; }
                .login-container { background: #1e1e1e; padding: 40px; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.5); text-align: center; width: 300px; }
                h2 { margin-top: 0; color: #cccccc; }
                input[type="text"],
                input[type="password"] {
                    width: 100%; 
                    box-sizing: border-box; 
                    padding: 12px; 
                    margin: 10px 0 20px 0; 
                    border: 1px solid #333; 
                    border-radius: 6px; 
                    font-size: 15px; 
                    background-color: #121212; 
                    color: #ccc;
                    transition: border-color 0.2s, box-shadow 0.2s;
                }
                input[type="text"]::placeholder,
                input[type="password"]::placeholder {
                    color: #777;
                }
                input[type="text"]:focus,
                input[type="password"]:focus {
                    border-color: #3b688c;
                    outline: none;
                    box-shadow: 0 0 0 2px rgba(59, 104, 140, 0.5);
                }
                button { 
                    width: 100%; padding: 14px; background-color: #3b688c; color: white; border: none; border-radius: 8px; font-size: 15px; font-weight: bold; cursor: pointer; 
                    transition: transform 0.2s, background-color 0.2s, box-shadow 0.2s; 
                    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.5);
                }
                button:hover { 
                    background-color: #3498db; 
                    transform: translateY(-2px);
                    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.7);
                }
            </style>
        </head>
        <body>
            <div class="login-container">
                <h2>🔒 주식 매매 일지</h2>
                <form method="post">
                    <input type="text" name="username" placeholder="아이디를 입력하세요" required autofocus>
                    <input type="password" name="password" placeholder="비밀번호를 입력하세요" required autofocus>
                    <button type="submit">접속하기</button>
                </form>
            </div>
        </body>
        </html>
    ''')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

@app.route('/')
def index():
    return send_from_directory('.', 'stock-memo.html')

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    # 분리되어 저장된 이미지 파일을 브라우저에 제공
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/api/data', methods=['GET'])
def get_data():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM entries")
    rows = c.fetchall()
    data = [dict(row) for row in rows]
    conn.close()
    return jsonify(data)

@app.route('/api/data', methods=['POST'])
def save_data():
    entries = request.json
    incoming_ids = [entry['id'] for entry in entries]
    
    conn = get_db()
    c = conn.cursor()
    
    # 삭제된 항목 처리 (DB에는 있는데 클라이언트가 안 보낸 ID 삭제)
    c.execute("SELECT id, attachedImage FROM entries")
    existing_entries = c.fetchall()
    
    for row in existing_entries:
        if row['id'] not in incoming_ids:
            # 연결된 이미지 파일도 함께 삭제
            if row['attachedImage'] and row['attachedImage'].startswith('/uploads/'):
                filepath = os.path.join(UPLOAD_FOLDER, os.path.basename(row['attachedImage']))
                if os.path.exists(filepath):
                    os.remove(filepath)
            c.execute("DELETE FROM entries WHERE id=?", (row['id'],))

    # 새로 추가되거나 수정된 항목 저장 (Upsert)
    for entry in entries:
        img_url = process_image(entry.get('attachedImage'), entry.get('id'))
        c.execute('''
            INSERT OR REPLACE INTO entries 
            (id, type, stockName, title, thoughts, date, rawDate, attachedImage, brokerAccount, accountName, tradeType, price, quantity, createdAt, updatedAt, tags)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            entry.get('id'), entry.get('type'), entry.get('stockName'), entry.get('title'),
            entry.get('thoughts'), entry.get('date'), entry.get('rawDate'), img_url,
            entry.get('brokerAccount'), entry.get('accountName'), entry.get('tradeType'),
            entry.get('price', 0), entry.get('quantity', 0),
            entry.get('createdAt'), entry.get('updatedAt'), entry.get('tags', '')
        ))
        
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
    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
        # 1. DB 백업
        db_path = os.path.join(DB_DIR, 'journal.db')
        if os.path.exists(db_path):
            zf.write(db_path, arcname='db/journal.db')
        
        # 2. 이미지 폴더 백업
        for root, dirs, files in os.walk(UPLOAD_FOLDER):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, start='.')
                zf.write(file_path, arcname=arcname)
                
    memory_file.seek(0)
    
    # 파일명에 현재 날짜와 시간 추가 (예: TradingJournal_backup_20231027_153000.zip)
    current_time = time.strftime('%Y%m%d_%H%M%S')
    filename = f'TradingJournal_backup_{current_time}.zip'
    
    response = send_file(memory_file, mimetype='application/zip', download_name=filename, as_attachment=True)
    response.headers["Access-Control-Expose-Headers"] = "Content-Disposition"
    return response

@app.route('/api/restore', methods=['POST'])
def full_restore():
    """백업 받은 ZIP 파일을 해제하여 DB 및 업로드 이미지를 완벽 원복"""
    if 'file' not in request.files:
        return jsonify({'error': '업로드된 파일이 없습니다.'}), 400
        
    file = request.files['file']
    if file.filename == '' or not file.filename.endswith('.zip'):
        return jsonify({'error': '유효하지 않은 파일입니다. .zip 백업 파일을 업로드해주세요.'}), 400
        
    temp_dir = tempfile.mkdtemp()
    try:
        with zipfile.ZipFile(file, 'r') as zf:
            zf.extractall(temp_dir)
            
        temp_db = os.path.join(temp_dir, 'db', 'journal.db')
        if not os.path.exists(temp_db):
            return jsonify({'error': '손상된 백업 파일입니다. (DB 파일을 찾을 수 없습니다)'}), 400
            
        # 1. 기존 DB 덮어쓰기
        shutil.copy2(temp_db, os.path.join(DB_DIR, 'journal.db'))
        
        # 2. 기존 이미지 폴더 날리고 덮어쓰기
        temp_uploads = os.path.join(temp_dir, 'uploads')
        for f in os.listdir(UPLOAD_FOLDER):
            file_path = os.path.join(UPLOAD_FOLDER, f)
            if os.path.isfile(file_path):
                os.remove(file_path)
                
        if os.path.exists(temp_uploads):
            for f in os.listdir(temp_uploads):
                src_path = os.path.join(temp_uploads, f)
                if os.path.isfile(src_path):
                    shutil.copy2(src_path, os.path.join(UPLOAD_FOLDER, f))
                    
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        shutil.rmtree(temp_dir)

if __name__ == '__main__':
    init_db()
    print("🚀 로컬 주식 매매 일지 서버를 시작합니다.")
    print("👉 웹 브라우저를 열고 http://127.0.0.1:5000 또는 기기의 로컬 IP 주소(예: 192.168.x.x:5000)로 접속해주세요.")
    app.run(host='0.0.0.0', debug=True, port=5000)
