#!/usr/bin/env python3
import json
import os
import sqlite3
import base64
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__, static_folder='.', static_url_path='')

DATA_FILE = 'my_stock_trading_journal.json'
DB_DIR = 'db'
DB_FILE = os.path.join(DB_DIR, 'journal.db')
UPLOAD_FOLDER = 'uploads'

# 필요한 폴더들 생성
os.makedirs(DB_DIR, exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

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

if __name__ == '__main__':
    init_db()
    print("🚀 로컬 주식 매매 일지 서버를 시작합니다.")
    print("👉 웹 브라우저를 열고 http://127.0.0.1:5000 주소로 접속해주세요.")
    app.run(debug=True, port=5000)
