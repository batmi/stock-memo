"""SQLite 연결 — 모든 모듈이 같은 방식으로 DB 를 연다.

블루프린트가 여러 모듈로 나뉘면 각자 `sqlite3.connect` 를 부를 유혹이 생긴다.
그러면 PRAGMA 설정이 빠진 연결이 섞여 들어와, 잠금 경합에서 대기 대신 즉시
실패하는(busy_timeout 없음) 경로가 조용히 생긴다. 연결은 여기서만 만든다.
"""

import sqlite3
from contextlib import contextmanager

import config


def get_db():
    """설정이 적용된 새 연결. 호출자가 닫아야 한다 (가급적 db_conn() 을 쓸 것)."""
    # 모듈 전역 config.DB_FILE 을 동적으로 참조 (테스트가 경로를 교체할 수 있도록)
    conn = sqlite3.connect(config.DB_FILE)
    conn.row_factory = sqlite3.Row  # 결과를 dict 형태로 접근할 수 있게 함
    # journal_mode=WAL 은 DB 파일에 영속되므로 schema.init() 에서 1회만 설정한다.
    # 여기서는 비용이 거의 없는 연결별 설정만 적용한다.
    #   - synchronous=NORMAL : WAL 과 함께 쓸 때 안전하면서 쓰기 성능 향상
    #   - busy_timeout       : 시세 병렬 스레드와의 잠금 경합 시 즉시 실패 대신 대기
    conn.execute('PRAGMA synchronous=NORMAL;')
    conn.execute('PRAGMA busy_timeout=5000;')
    return conn


@contextmanager
def db_conn():
    """요청 핸들러용 DB 연결 컨텍스트 매니저.

    예외/조기 반환과 무관하게 연결을 반드시 닫아 누수를 방지합니다.
    commit 은 호출자가 명시적으로 수행합니다.
    """
    conn = get_db()
    try:
        yield conn
    finally:
        conn.close()
