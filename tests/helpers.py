"""여러 테스트 파일이 함께 쓰는 조립 헬퍼.

⭐️ 예전에는 test_backend_app.py 하나가 2,000줄에 걸쳐 모든 모듈을 검사했고,
   이 헬퍼들도 그 파일 중간중간에 흩어져 있었다. 테스트를 모듈별로 가르면서
   공용 부분만 여기로 모은다. (픽스처는 conftest.py 가 갖는다)
"""
import time

import backend_app


def _login(client, username='trader'):
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = username
        # before_request 가 절대 만료(expires_at)를 검사하므로 없으면 전부 401 이 된다.
        sess['expires_at'] = time.time() + 3600

def _ensure_user(username):
    """users 테이블에 계정 행을 만든다.

    _login() 은 세션만 조작하므로 users 행이 없다. 계좌 매핑처럼 users 행에
    저장되는 기능을 테스트하려면 실제 계정이 있어야 한다.
    """
    conn = backend_app.get_db()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO users (username, password_hash, is_allowed) VALUES (?, ?, 1)",
            (username, 'x'),
        )
        conn.commit()
    finally:
        conn.close()

def _buy(stock='삼성전자', qty=10, price=80000, **kw):
    e = {"type": "trade", "tradeType": "매수", "stockName": stock,
         "stockCode": "005930", "price": price, "quantity": qty}
    e.update(kw)
    return e

def _sell(stock='삼성전자', qty=10, price=90000, **kw):
    e = {"type": "trade", "tradeType": "매도", "stockName": stock,
         "stockCode": "005930", "price": price, "quantity": qty}
    e.update(kw)
    return e

def _insert_raw(username, **cols):
    """트레이딩 API 를 거치지 않고 기록을 직접 넣는다 (isSimulated 지정용)."""
    cols.setdefault('type', 'trade')
    cols.setdefault('username', username)
    keys = ', '.join(cols)
    marks = ', '.join('?' for _ in cols)
    with backend_app.db_conn() as conn:
        conn.cursor().execute(
            f"INSERT INTO entries ({keys}) VALUES ({marks})", tuple(cols.values()))
        conn.commit()

def _signup_and_login(client, username, password):
    client.post('/signup', data={'username': username, 'password': password,
                                 'password_confirm': password})
    with backend_app.db_conn() as conn:
        conn.execute("UPDATE users SET is_allowed = 1 WHERE username = ?", (username,))
        conn.commit()
    return client.post('/login', data={'username': username, 'password': password})
