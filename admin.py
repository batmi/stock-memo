"""관리자 라우트 — 계정 승인·삭제·비밀번호 초기화·재설정 요청 처리.

관리자 권한으로만 닿을 수 있는 조작을 한 파일에 모은다. 흩어져 있으면
`@admin_required` 를 빠뜨린 라우트가 있는지 눈으로 확인할 방법이 없다.
(tests/test_admin.py 가 이 블루프린트의 모든 라우트에 데코레이터가 붙어 있는지
 검사한다 — 새 라우트를 추가하면 자동으로 함께 검증된다)
"""

import logging
import os
import shutil

from flask import Blueprint, jsonify, session
from werkzeug.security import generate_password_hash

import config
import ratelimit
import statscache
from auth import admin_required
from db import db_conn
from users import bump_session_epoch, generate_temp_password

log = logging.getLogger('admin')

bp = Blueprint('admin', __name__)


def register(app):
    app.register_blueprint(bp)


@bp.route('/api/admin/users', methods=['GET'])
@admin_required
def admin_get_users():
    with db_conn() as conn:
        c = conn.cursor()
        # ⭐️ 비밀번호 재설정 요청(로그인 화면에서 접수)을 같은 표에 실어 보낸다.
        c.execute('''
            SELECT u.username, u.is_allowed, u.is_admin, u.created_at, u.last_login_at,
                   u.must_change_password,
                   COUNT(e.id) as entry_count,
                   r.requested_at AS reset_requested_at,
                   r.note AS reset_note,
                   r.request_count AS reset_request_count
            FROM users u
            LEFT JOIN entries e ON u.username = e.username
            LEFT JOIN password_reset_requests r ON r.username = u.username
            GROUP BY u.username
        ''')
        users = [dict(row) for row in c.fetchall()]
    return jsonify(users)


@bp.route('/api/admin/password_resets/<target_username>', methods=['DELETE'])
@admin_required
def admin_dismiss_reset_request(target_username):
    """초기화하지 않고 요청만 내린다 (본인이 다시 기억해냈다거나 장난 요청인 경우)."""
    with db_conn() as conn:
        conn.execute("DELETE FROM password_reset_requests WHERE username = ?",
                     (target_username,))
        conn.commit()
    log.info(f"🔑 비밀번호 재설정 요청 해제: username='{target_username}' "
                    f"(by {session.get('username')})")
    return jsonify({"status": "success"})


@bp.route('/api/admin/users/<target_username>', methods=['DELETE'])
@admin_required
def admin_delete_user(target_username):
    with db_conn() as conn:
        c = conn.cursor()

        c.execute("SELECT is_admin FROM users WHERE username = ?", (target_username,))
        target_user = c.fetchone()
        if target_user and target_user['is_admin']:
            return jsonify({"error": "최고 관리자는 삭제할 수 없습니다."}), 400

        c.execute("DELETE FROM entries WHERE username = ?", (target_username,))
        c.execute("DELETE FROM api_keys WHERE username = ?", (target_username,))
        c.execute("DELETE FROM users WHERE username = ?", (target_username,))
        conn.commit()

    statscache.invalidate(target_username)

    user_folder = os.path.join(config.UPLOAD_FOLDER, target_username)
    if os.path.exists(user_folder):
        shutil.rmtree(user_folder)

    return jsonify({"status": "success"})


@bp.route('/api/admin/users/<target_username>/toggle_allow', methods=['POST'])
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


@bp.route('/api/admin/users/<target_username>/reset_password', methods=['POST'])
@admin_required
def admin_reset_password(target_username):
    # ⭐️ secrets 기반 12자리 임시 비밀번호 (예전 uuid4().hex[:8] 은 32비트뿐이었다)
    new_password = generate_temp_password(12)
    hashed_pw = generate_password_hash(new_password)

    with db_conn() as conn:
        c = conn.cursor()
        row = c.execute("SELECT username FROM users WHERE username = ?",
                        (target_username,)).fetchone()
        if not row:
            return jsonify({"error": "존재하지 않는 계정입니다."}), 404

        # must_change_password: 임시 비밀번호로 로그인하면 즉시 변경을 강제한다.
        c.execute("UPDATE users SET password_hash = ?, must_change_password = 1 "
                  "WHERE username = ?", (hashed_pw, target_username))
        # 초기화도 '비밀번호가 바뀐 것'이므로 기존 세션을 전부 끊는다.
        bump_session_epoch(c, target_username)
        # 처리한 재설정 요청은 요청함에서 내린다.
        c.execute("DELETE FROM password_reset_requests WHERE username = ?", (target_username,))
        conn.commit()

    ratelimit.clear_user_failures(target_username)
    log.info(f"🔑 관리자 비밀번호 초기화: username='{target_username}' "
                    f"(by {session.get('username')})")

    return jsonify({"status": "success", "new_password": new_password})
