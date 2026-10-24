"""요청의 계정 세대와 쓰기 잠금. 삭제·재가입 사이에 옛 요청이 기록을 남기지 않는다."""

from flask import g, has_request_context
from werkzeug.exceptions import Unauthorized

from app.utils.userlocks import user_lock


def lock_request(username):
    lock = user_lock(username)
    lock.acquire()
    g.user_write_locks = [*getattr(g, 'user_write_locks', []), lock]


def lock_request_users(*usernames):
    # 관리자는 본인과 삭제 대상 잠금을 함께 잡는다. 고정 순서로 잡아
    # striped lock의 해시 충돌이 두 관리자 사이의 교착으로 이어지지 않게 한다.
    for lock in sorted({user_lock(name) for name in usernames}, key=id):
        lock.acquire()
        g.user_write_locks = [*getattr(g, 'user_write_locks', []), lock]


def release_request(error=None):
    for lock in reversed(g.pop('user_write_locks', [])):
        lock.release()


def validate_owner(conn):
    if not has_request_context() or not getattr(g, 'account_identity', None):
        return
    username, user_id = g.account_identity
    row = conn.execute('SELECT id,is_allowed FROM users WHERE username=?', (username,)).fetchone()
    if row is None or row['id'] != user_id or not row['is_allowed']:
        raise Unauthorized('계정이 변경되었습니다. 다시 로그인해주세요.')
