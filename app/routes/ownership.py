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
    from app.services.restore_recovery import assert_account_ready
    assert_account_ready(username)
    row = conn.execute('SELECT id,is_allowed,session_epoch FROM users WHERE username=?',
                       (username,)).fetchone()
    epoch = getattr(g, 'account_epoch', None)
    if (row is None or row['id'] != user_id or not row['is_allowed']
            or epoch is not None and (row['session_epoch'] or 0) != epoch):
        raise Unauthorized('계정이 변경되었습니다. 다시 로그인해주세요.')


def response_owner_version(version):
    """HTTP 캐시도 동명이인 계정 간에 재사용되지 않도록 신원을 포함한다."""
    identity = getattr(g, 'account_identity', None)
    return f'{version}-owner-{identity[1]}' if identity else version
