#!/usr/bin/env python3
"""로그인이 막혔을 때 서버에서 직접 비밀번호를 되살리는 복구 도구.

웹의 관리자 초기화(/api/admin/users/<계정>/reset_password)는 '관리자가 이미
로그인해 있을 것'을 전제로 한다. 정작 그 관리자 본인이 잠기면 쓸 수 없어,
남는 수단이 DB 를 손으로 UPDATE 하는 것뿐이었다. 이 도구가 그 자리를 메운다.

  - 웹 엔드포인트를 늘리지 않는다. 터널로 외부에 공개된 환경에서도 공격면이 없다.
    서버 파일에 접근할 수 있다는 사실 자체가 이미 충분한 소유 증명이다.
  - 앱이 떠 있지 않아도, 관리자가 잠겨 있어도 동작한다.
  - 비밀번호는 화면·셸 히스토리·로그 어디에도 남기지 않는다(--random 일 때만
    생성된 값을 한 번 보여준다).

사용 예:
  python3 tools/reset_password.py                 # 계정 목록을 보고 골라서 변경
  python3 tools/reset_password.py --user batmi    # 계정 지정
  python3 tools/reset_password.py --user batmi --random   # 무작위 임시 비밀번호
  python3 tools/reset_password.py --user batmi --unlock   # 잠긴 계정 승인까지
  python3 tools/reset_password.py --list          # 조회만
"""

import argparse
import getpass
import os
import secrets
import sqlite3
import string
import sys
import time
import unicodedata

# ⭐️ backend_app.py 와 같은 규칙으로 경로를 잡는다. 실행 위치(cwd)에 좌우되면
#    엉뚱한 DB 를 고쳐 놓고 "바꿨는데 왜 안 되지" 가 반복된다.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(BASE_DIR, 'db', 'journal.db')
AUDIT_LOG = os.path.join(BASE_DIR, 'logs', 'backend_app.log')

MIN_LENGTH = 8

try:
    from werkzeug.security import generate_password_hash
except ImportError:
    sys.exit("[-] werkzeug 를 찾을 수 없습니다. 가상환경의 파이썬으로 실행하세요.\n"
             "    예: ./.venv/bin/python tools/reset_password.py")


def connect(db_file):
    if not os.path.exists(db_file):
        sys.exit(f"[-] DB 파일이 없습니다: {db_file}\n"
                 "    경로가 맞는지 확인하거나 --db 로 직접 지정하세요.")
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    return conn


def pad(text, width):
    """한글은 터미널에서 두 칸을 차지하므로 글자 수가 아닌 표시 폭으로 맞춘다."""
    shown = sum(2 if unicodedata.east_asian_width(ch) in 'WF' else 1 for ch in text)
    return text + ' ' * max(0, width - shown)


def show_users(conn, db_file):
    rows = conn.execute(
        "SELECT username, is_allowed, is_admin, created_at, last_login_at "
        "FROM users ORDER BY is_admin DESC, username").fetchall()
    print(f"\n📁 DB: {db_file}")
    if not rows:
        print("[-] 계정이 하나도 없습니다. 이 DB 가 맞는지 확인하세요.")
        return rows
    print(pad('계정', 18) + pad('권한', 10) + pad('상태', 10) + '최근 로그인')
    print("-" * 60)
    for r in rows:
        권한 = "관리자" if r['is_admin'] else "일반"
        상태 = "정상" if r['is_allowed'] else "잠김"
        print(pad(r['username'], 18) + pad(권한, 10) + pad(상태, 10)
              + (r['last_login_at'] or '-'))
    print()
    return rows


def read_new_password():
    """두 번 입력받아 대조한다. 오타가 그대로 저장돼 본인도 모르는 값이 되는 걸 막는다."""
    while True:
        pw = getpass.getpass("새 비밀번호: ")
        if not pw:
            sys.exit("[-] 입력이 비어 있어 중단합니다.")
        if len(pw) < MIN_LENGTH:
            print(f"[!] {MIN_LENGTH}자 이상을 권장합니다. 다시 입력하세요.")
            continue
        if pw != getpass.getpass("한 번 더 입력: "):
            print("[!] 두 입력이 서로 다릅니다. 다시 입력하세요.")
            continue
        return pw


def generate_password(length=12):
    """혼동하기 쉬운 글자(0/O, 1/l/I)를 뺀 임시 비밀번호."""
    alphabet = ''.join(c for c in string.ascii_letters + string.digits
                       if c not in '0O1lI')
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def write_audit(username, unlocked, promoted):
    """앱 로그와 같은 타임라인에 흔적을 남긴다. 비밀번호 자체는 절대 남기지 않는다.

    이번 사고에서 '언제 무엇이 비밀번호를 바꿨는지' 를 찾느라 로그를 뒤졌다.
    CLI 로 바꾼 것도 같은 파일에 남아야 다음번 추적이 짧아진다.
    """
    extra = []
    if unlocked:
        extra.append("계정 잠금 해제")
    if promoted:
        extra.append("관리자 권한 부여")
    suffix = (" + " + ", ".join(extra)) if extra else ""
    line = (f"{time.strftime('%H:%M:%S')}.000 [WARNING] [reset_password.py] "
            f"CLI 로 비밀번호 초기화: username='{username}'{suffix}\n")
    try:
        with open(AUDIT_LOG, 'a', encoding='utf-8') as f:
            f.write(line)
    except OSError:
        pass  # 로그를 못 남겨도 복구 자체는 진행한다.


def main():
    parser = argparse.ArgumentParser(
        description="로그인 불가 상태에서 계정 비밀번호를 되살리는 복구 도구",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--user', help='대상 계정 (생략하면 목록에서 고름)')
    parser.add_argument('--db', default=DEFAULT_DB, help=f'DB 경로 (기본: {DEFAULT_DB})')
    parser.add_argument('--random', action='store_true',
                        help='무작위 임시 비밀번호를 생성해 화면에 한 번 보여줌')
    parser.add_argument('--unlock', action='store_true',
                        help='미승인/차단 상태(is_allowed=0)도 함께 풀어줌')
    parser.add_argument('--make-admin', action='store_true',
                        help='관리자 권한(is_admin=1)까지 부여 (권한을 잃었을 때만)')
    parser.add_argument('--list', action='store_true', help='계정 목록만 출력하고 종료')
    args = parser.parse_args()

    conn = connect(args.db)
    rows = show_users(conn, args.db)
    if args.list or not rows:
        return

    username = args.user
    if not username:
        username = input("초기화할 계정: ").strip()
    if not username:
        sys.exit("[-] 계정을 지정하지 않아 중단합니다.")

    target = conn.execute(
        "SELECT username, is_allowed, is_admin FROM users WHERE username = ?",
        (username,)).fetchone()
    if not target:
        sys.exit(f"[-] '{username}' 계정이 이 DB 에 없습니다. 위 목록에서 정확한 아이디를 고르세요.")

    if args.random:
        password = generate_password()
    else:
        password = read_new_password()

    # is_allowed 는 명시적으로 요청할 때만 건드린다. 관리자가 의도적으로 막아 둔
    # 계정을 비밀번호 초기화라는 이름으로 슬그머니 열어 주면 안 된다.
    sets = ["password_hash = ?"]
    values = [generate_password_hash(password)]
    unlocked = bool(args.unlock and not target['is_allowed'])
    promoted = bool(args.make_admin and not target['is_admin'])
    if unlocked:
        sets.append("is_allowed = 1")
    if promoted:
        sets.append("is_admin = 1")

    plan = f"'{username}' 계정의 비밀번호를 변경"
    if unlocked:
        plan += " + 잠금 해제"
    if promoted:
        plan += " + 관리자 권한 부여"
    if input(f"\n{plan}합니다. 진행할까요? (y/N): ").strip().lower() != 'y':
        sys.exit("[-] 사용자가 취소했습니다.")

    values.append(username)
    with conn:
        conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE username = ?", values)
    changed = conn.total_changes
    conn.close()

    if changed != 1:
        sys.exit(f"[-] 예상과 다르게 {changed}개 행이 변경되었습니다. DB 를 확인하세요.")

    write_audit(username, unlocked, promoted)
    print(f"\n✅ '{username}' 계정의 비밀번호를 변경했습니다.")
    if args.random:
        print(f"   임시 비밀번호: {password}")
        print("   (이 화면에만 표시됩니다. 로그인 후 바로 변경하세요.)")
    if target['is_allowed'] == 0 and not unlocked:
        print("[!] 이 계정은 '잠김' 상태라 비밀번호가 맞아도 로그인할 수 없습니다.")
        print("    --unlock 을 붙여 다시 실행하세요.")
    print("   서버를 재시작할 필요 없이 바로 적용됩니다.")


if __name__ == '__main__':
    main()
