"""기록과 첨부의 변경·백업을 같은 사용자 단위로 직렬화한다.

DB 경합은 쓰기 트랜잭션이 처리한다. 이 잠금은 DB 밖의 첨부 교체/정리도 포함한다.
현재 지원하는 단일 프로세스 운영용이며, 고정 크기라 사용자 수만큼 늘어나지 않는다.
"""

from functools import wraps
import threading

_locks = tuple(threading.RLock() for _ in range(64))


def user_lock(username):
    return _locks[hash(username) % len(_locks)]


def serialized_user_change(function):
    @wraps(function)
    def wrapped(username, *args, **kwargs):
        with user_lock(username):
            return function(username, *args, **kwargs)
    return wrapped
