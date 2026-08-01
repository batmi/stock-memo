import sqlite3
import os
import json

def migrate():
    # 1. 파일 경로 설정
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db_file = os.path.join(base_dir, 'db', 'journal.db')
    
    if not os.path.exists(db_file):
        print(f"[-] 데이터베이스 파일을 찾을 수 없습니다: {db_file}")
        return

    # 2. 모든 사용자의 매핑 정보 로드
    users_dir = os.path.join(base_dir, 'json')
    user_mappings = {}
    if os.path.exists(users_dir):
        for user in os.listdir(users_dir):
            acc_info_file = os.path.join(users_dir, user, 'account_info.json')
            if os.path.exists(acc_info_file):
                try:
                    with open(acc_info_file, 'r', encoding='utf-8') as f:
                        user_mappings[user] = json.load(f)
                except Exception as e:
                    print(f"[-] {user}의 매핑 정보를 읽는 중 에러: {e}")

    # 3. DB 연결 및 데이터 조회
    print(f"[+] 데이터베이스 연결: {db_file}")
    conn = sqlite3.connect(db_file)
    c = conn.cursor()

    try:
        c.execute("SELECT id, username, brokerAccount, subAccount, accountName, tradeClass FROM entries")
        rows = c.fetchall()
    except sqlite3.OperationalError as e:
        print(f"[-] 데이터 조회 실패 (tradeClass 컬럼이 추가되었는지 확인하세요): {e}")
        return

    broker_conversion = {'1': '264', '2': '238', '3': '247', '4': '243', '5': '240', '6': '271'}
    target_classes = ['장기투자', '중기투자', '단기스윙', '단타(스캘핑)', '배당투자', '공모주', '시스템', '기타']
    
    updates = []
    
    for row in rows:
        r_id, username, broker, sub_acc, acc_name, trade_class = row
        needs_update = False
        
        new_broker = broker
        new_trade_class = trade_class
        new_acc_name = acc_name
        
        # A. 증권사 코드 변환 (1~6 -> KFTC 코드)
        if broker in broker_conversion:
            new_broker = broker_conversion[broker]
            needs_update = True
            
        # B. 투자 분류(tradeClass)와 계좌별칭(accountName) 분리 마이그레이션
        if not trade_class and acc_name in target_classes:
            new_trade_class = acc_name
            # 계좌별칭(accountName) 복원: 서브계좌(subAccount)를 매핑 정보에서 조회
            # 서브계좌에서 '-' 제거 후 검색
            clean_sub = sub_acc.replace('-', '') if sub_acc else ''
            
            mappings = user_mappings.get(username, {}).get('accounts', {})
            acc_info = mappings.get(clean_sub, {})
            
            if isinstance(acc_info, dict):
                new_acc_name = acc_info.get('alias', '')
            else:
                new_acc_name = mappings.get(clean_sub, '')
                
            needs_update = True
            
        if needs_update:
            updates.append((new_broker, new_acc_name, new_trade_class, r_id))
            
    if updates:
        print(f"[+] 총 {len(updates)}건의 마이그레이션 업데이트를 진행합니다...")
        c.executemany("UPDATE entries SET brokerAccount = ?, accountName = ?, tradeClass = ? WHERE id = ?", updates)
        conn.commit()
        print("[+] 마이그레이션 성공!")
    else:
        print("[+] 업데이트가 필요한 데이터가 없습니다 (이미 마이그레이션 되었거나 기존 데이터 없음).")
        
    conn.close()

if __name__ == '__main__':
    migrate()
