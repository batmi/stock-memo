"""인증 라우트 — 로그인·회원가입·로그아웃·비밀번호 재설정 요청 + 세션 검사.

세션 수명·계정 잠금·가입 승인처럼 "누가 들어올 수 있는가"에 대한 결정을 한곳에
모은다. 예전에는 이 판단이 2,300줄짜리 파일 곳곳에 흩어져 있어, 세션 규칙을
바꿀 때 놓친 곳이 있는지 확인할 방법이 없었다.

 이 블루프린트 등록과 before_request 연결을 함께 한다.
세션 검사(check_login)는 블루프린트가 아니라 **앱 전체**에 걸어야 하므로
(정적 파일·업로드·다른 블루프린트까지 모두 통과해야 한다) 여기서 붙인다.
"""

import json
import os
import time
from functools import wraps

from flask import (Blueprint, jsonify, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import config
import entry_logic
import images
import logging
import ratelimit
from db import db_conn
from users import current_session_epoch, is_valid_username, validate_password

log = logging.getLogger('auth')

bp = Blueprint('auth', __name__)

# 세션 검사를 면제받는 엔드포인트 (로그인하지 않은 사람이 닿아야 하는 화면)
PUBLIC_ENDPOINTS = frozenset({
    'auth.login', 'auth.signup', 'auth.logout', 'auth.request_password_reset',
})

def register(app):
    """블루프린트 등록 + 앱 전역 세션 검사 연결."""
    app.register_blueprint(bp)
    # ⭐️ 블루프린트가 아니라 앱에 건다. 정적 파일·업로드·봇 API 를 포함한
    #    모든 요청이 이 검사를 지나야 하기 때문이다.
    app.before_request(check_login)


def check_login():
    # 로그인 및 회원가입 처리를 수행하는 라우트 및 API Key 연동 라우트는 세션 검사에서 제외
    # ⭐️ 블루프린트로 옮기면서 엔드포인트 이름에 'auth.' 접두사가 붙었다.
    #    이 목록이 실제 이름과 어긋나면 로그인 화면 자체가 무한 리다이렉트에 빠지므로,
    #    라우트 함수 이름에서 자동으로 만들지 않고 명시한다 (tests 가 이를 검증한다).
    if (request.endpoint not in PUBLIC_ENDPOINTS
            and not request.path.startswith('/api/v1/')):
        # 세션에 로그인 상태가 없으면 차단
        if not session.get('logged_in'):
            # 백엔드 API 요청인 경우 401 인증 에러 반환
            if request.path.startswith('/api/'):
                return jsonify({"error": "Unauthorized"}), 401
            # 일반 페이지 접근은 로그인 화면으로 리다이렉트
            return redirect(url_for('auth.login'))

        # ⭐️ 로그인 시점에 확정된 절대 만료 시각(expires_at)이 지나면 활동 여부와 무관하게 무조건 세션 파기
        #    (만료 정보가 없는 구버전 세션도 즉시 만료 처리하여 재로그인 유도)
        if time.time() > session.get('expires_at', 0):
            session.clear()
            if request.path.startswith('/api/'):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for('auth.login', timeout=1))

        # ⭐️ 비밀번호가 바뀌었으면(epoch 증가) 이 세션은 즉시 끊는다.
        #    비밀번호 변경이 '다른 기기·침입자 로그아웃'으로 실제 작동하게 하는 장치.
        #    epoch 이 없는 세션(이 기능 도입 전에 로그인한 쿠키)은 0 으로 본다.
        #    배포하자마자 전원이 로그아웃되는 일은 피하면서, 이후 비밀번호가 한 번이라도
        #    바뀌면(epoch >= 1) 그 옛 쿠키는 그때 끊긴다.
        sess_user = session.get('username')
        if sess_user and session.get('epoch', 0) != current_session_epoch(sess_user):
            session.clear()
            if request.path.startswith('/api/'):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for('auth.login', timeout=1))


# ⭐️ 시스템 트레이딩 API(/api/v1/*)의 토큰 발급·검증은 trading_api 모듈이 담당한다.
#    (키 해시 저장, 스코프 검사, 폐기 즉시 반영, 레이트 리밋이 함께 걸려 있다)

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


def _humanize_seconds(seconds):
    """잠금 시간을 사람이 읽는 표현으로. 60초 → "1분", 90초 → "90초".

    ⭐️ 상수를 그대로 끼워 넣으면 "60초 동안" 이 되어 원래 문구("1분 동안")보다
       어색해진다. 값은 상수에서 오되 표현은 자연스럽게 유지한다.
    """
    if seconds >= 60 and seconds % 60 == 0:
        return f"{seconds // 60}분"
    return f"{seconds}초"


@bp.route('/login', methods=['GET', 'POST'])
def login():
    client_ip = request.remote_addr
    current_time = time.time()

    # ⭐️ IP 잠금·계정 잠금은 둘 다 ratelimit 모듈이 소유한다. 예전에는 IP 쪽만
    #    이 함수 안에서 dict 를 직접 만지며 정리까지 했는데, 락이 없어 동시
    #    로그인 시 순회 중 변경으로 터질 수 있었다.
    error_message = None
    timeout_message = None

    if request.method == 'GET' and request.args.get('timeout'):
        timeout_message = "보안을 위해 로그인 세션이 만료되어 자동으로 로그아웃 되었습니다."

    if request.method == 'POST':
        typed_username = (request.form.get('username') or '').strip()
        # ⭐️ IP 잠금은 IP 를 바꾸면 우회된다. 계정 단위 잠금을 함께 걸어
        #    한 계정을 표적으로 삼은 무차별 대입을 늦춘다.
        user_locked_for = ratelimit.user_lockout_remaining(typed_username, current_time)
        ip_locked_for = ratelimit.login_ip_lockout_remaining(client_ip, current_time)
        if ip_locked_for:
            error_message = (f"로그인 {ratelimit.LOGIN_IP_THRESHOLD}회 실패로 차단되었습니다. "
                             f"{ip_locked_for}초 후에 다시 시도해주세요.")
        elif user_locked_for:
            error_message = (f"이 계정은 반복된 로그인 실패로 잠겨 있습니다. "
                             f"{user_locked_for}초 후에 다시 시도해주세요.")
        else:
            username = typed_username
            password = request.form.get('password') or ""

            # DB에서 입력한 아이디와 일치하는 암호화된 비밀번호 조회
            with db_conn() as conn:
                c = conn.cursor()
                c.execute("SELECT password_hash, is_allowed, is_admin FROM users WHERE username = ?", (username,))
                user_record = c.fetchone()

            # 계정이 존재하고, 입력한 비밀번호와 DB의 해시값이 일치하는지 검증
            if user_record and check_password_hash(user_record['password_hash'], password):
                if not user_record['is_allowed']:
                    log.warning(f"로그인 거부(미승인 계정): username='{username}' ip={client_ip}")
                    error_message = "관리자의 승인이 필요하거나 로그인이 제한된 계정입니다."
                else:
                    # 로그인 성공 시 최근 로그인 일시 업데이트
                    with db_conn() as conn:
                        c = conn.cursor()
                        current_time_str = time.strftime('%Y-%m-%d %H:%M:%S')
                        c.execute("UPDATE users SET last_login_at = ? WHERE username = ?", (current_time_str, username))
                        conn.commit()

                    ratelimit.clear_login_ip_failures(client_ip)
                    ratelimit.clear_user_failures(username)
                    # ⭐️ 이전 세션에 남아 있던 값을 물려받지 않도록 비우고 시작한다.
                    session.clear()
                    # ⭐️ "로그인 유지" 선택 여부에 따라 절대 만료 시각 확정
                    #   - 미선택: 1시간 뒤 만료(사용 중이면 연장 팝업으로 1시간씩 연장 가능) + 브라우저 완전 종료 시에도 만료(세션 쿠키)
                    #   - 선택: 24시간 뒤 무조건 만료(연장 없음), 브라우저를 닫아도 유지(영구 쿠키)
                    keep_logged_in = request.form.get('keep_logged_in') == 'on'
                    session.permanent = keep_logged_in
                    session['keep_logged_in'] = keep_logged_in
                    session['expires_at'] = time.time() + (24 * 3600 if keep_logged_in else 3600)
                    session['logged_in'] = True
                    session['username'] = username  # ⭐️ 계정별 설정 저장을 위해 세션에 저장
                    session['is_admin'] = bool(user_record['is_admin'])
                    session['epoch'] = current_session_epoch(username)
                    return redirect(url_for('api.index'))
            else:
                fail_count = ratelimit.record_login_ip_failure(client_ip)
                ratelimit.record_user_failure(username)
                # ⭐️ 화면에는 "아이디 또는 비밀번호" 로 뭉뚱그려 계정 존재 여부를 감추되,
                #    서버 로그에는 실제 사유를 남긴다. 두 경우가 같은 문구로 보이는 탓에
                #    '엉뚱한(빈) DB 를 보고 있어서 계정이 없는' 상황과 단순 오타를
                #    로그만으로 구분할 수 없었다. 그래서 조회한 DB 경로도 함께 남긴다.
                reason = "비밀번호 불일치" if user_record else "존재하지 않는 계정"
                log.warning(
                    f"로그인 실패({reason}): username='{username}' ip={client_ip} "
                    f"시도={fail_count}/{ratelimit.LOGIN_IP_THRESHOLD} db={config.DB_FILE}"
                )
                if fail_count >= ratelimit.LOGIN_IP_THRESHOLD:
                    error_message = (f"비밀번호 {ratelimit.LOGIN_IP_THRESHOLD}회 연속 실패! "
                                     f"{_humanize_seconds(ratelimit.LOGIN_IP_LOCKOUT_SECONDS)} "
                                     f"동안 로그인이 차단됩니다.")
                else:
                    error_message = ("아이디 또는 비밀번호가 일치하지 않습니다. "
                                     f"(실패 횟수: {fail_count}/{ratelimit.LOGIN_IP_THRESHOLD})")

    return render_template('login.html', error_message=error_message, timeout_message=timeout_message)


@bp.route('/signup', methods=['GET', 'POST'])
def signup():
    error_message = None
    success_message = None

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        password_confirm = request.form.get('password_confirm')

        username = (username or '').strip()

        if not username or not password:
            error_message = "아이디와 비밀번호를 모두 입력해주세요."
        elif not is_valid_username(username):
            # ⭐️ 사용자명은 파일 경로에 그대로 쓰이므로 화이트리스트로 제한한다.
            error_message = ("아이디는 영문·숫자로 시작하는 3~32자여야 하며, "
                             "영문·숫자와 _ . - 만 사용할 수 있습니다.")
        elif password != password_confirm:
            error_message = "비밀번호가 일치하지 않습니다."
        elif validate_password(password, username):
            error_message = validate_password(password, username)
        elif not ratelimit.signup_allowed(request.remote_addr):
            error_message = "가입 시도가 너무 잦습니다. 잠시 후 다시 시도해주세요."
        else:
            # ⭐️ with 로 감싼다. 예전에는 get_db() 로 열고 끝에서 close() 했는데,
            #    중간에 예외가 나면 연결이 그대로 새어 나갔다.
            with db_conn() as conn:
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
                        if c.fetchone()[0] == 0 and os.path.exists(config.DATA_FILE):
                            log.info("🔄 기존 JSON 데이터를 SQLite 데이터베이스로 마이그레이션 합니다...")
                            try:
                                with open(config.DATA_FILE, 'r', encoding='utf-8') as f:
                                    old_data = json.load(f)
                                    for entry in old_data:
                                        img_url = images.process_image(entry.get('attachedImage'), entry.get('id'))
                                        entry = images.extract_inline_images(username, entry)
                                        entry_logic.insert_entry(c, username, entry, attached_image=img_url)
                                    conn.commit()
                                    log.info("✅ 데이터 마이그레이션 완료! (이제부터 db/journal.db와 uploads 폴더를 사용합니다)")
                            except Exception as e:
                                log.error(f"❌ 마이그레이션 중 오류 발생: {e}")
                    else:
                        success_message = "회원가입이 완료되었습니다! 관리자의 승인 후 로그인할 수 있습니다. 잠시 후 로그인 화면으로 이동합니다."

    return render_template('signup.html', error_message=error_message, success_message=success_message)


@bp.route('/request_password_reset', methods=['GET', 'POST'])
def request_password_reset():
    """비밀번호를 잊은 사용자가 관리자에게 초기화를 요청한다.

    GET  — 요청 화면(별도 페이지). 로그인 폼과 섞이지 않도록 /signup 처럼 분리한다.
    POST — 요청 접수(JSON).

    ⭐️ 여기서 비밀번호를 바꾸지는 않는다. '요청'만 남기고, 실제 초기화는 관리자가
       대시보드에서 수행한다. 메일 발송 수단이 없는 환경이라 자동 재설정 링크를
       보낼 수 없고, 자동으로 바꿔주면 아이디만 알면 남의 계정을 잠글 수 있다.
    """
    if request.method == 'GET':
        return render_template('reset_request.html')

    data = request.get_json(silent=True) or request.form or {}
    username = str(data.get('username') or '').strip()
    note = str(data.get('note') or '').strip()[:300]

    # 응답 문구는 어떤 경우에도 동일하다 (계정 존재 여부 은닉)
    neutral = jsonify({
        'status': 'success',
        'message': '요청이 접수되었습니다. 관리자가 확인 후 비밀번호를 초기화해 드립니다.'
    })

    if not username or len(username) > 150:
        return neutral, 200

    client_ip = request.remote_addr
    if not ratelimit.reset_request_allowed(client_ip):
        log.warning(f"비밀번호 재설정 요청 과다: ip={client_ip}")
        return jsonify({
            'status': 'rate_limited',
            'message': '요청이 너무 잦습니다. 잠시 후 다시 시도해주세요.'
        }), 429

    with db_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT username FROM users WHERE username = ?", (username,))
        row = c.fetchone()
        if row:
            now_str = time.strftime('%Y-%m-%d %H:%M:%S')
            # 같은 계정이 다시 눌러도 줄은 하나. 시각과 횟수만 갱신한다.
            c.execute("""
                INSERT INTO password_reset_requests (username, requested_at, note, request_count)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(username) DO UPDATE SET
                    requested_at = excluded.requested_at,
                    note = COALESCE(NULLIF(excluded.note, ''), password_reset_requests.note),
                    request_count = password_reset_requests.request_count + 1
            """, (row['username'], now_str, note))
            conn.commit()
            log.info(f"🔑 비밀번호 재설정 요청 접수: username=\'{row['username']}\' ip={client_ip}")
        else:
            # 존재하지 않는 계정도 로그에만 남기고 응답은 동일하게 한다.
            log.info(f"🔑 비밀번호 재설정 요청(존재하지 않는 계정): username=\'{username}\' ip={client_ip}")

    return neutral, 200


@bp.route('/logout')
def logout():
    session.pop('logged_in', None)
    session.pop('username', None)  # ⭐️ 로그아웃 시 계정 정보 완벽 파기
    session.pop('is_admin', None)
    session.pop('expires_at', None)
    session.pop('keep_logged_in', None)
    if request.args.get('timeout'):
        return redirect(url_for('auth.login', timeout=1))
    return redirect(url_for('auth.login'))
