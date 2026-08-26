"""매도 무결성 — 보유하지 않은 수량은 팔 수 없다.

대상: entry_logic.validate_trade_entry / check_sell_integrity.
봇 경로(차단하지 않고 needsReview 표시)는 tests/test_trading_api.py 가 본다.
"""


import backend_app
from app.database import entry_logic
from helpers import _buy, _insert_raw, _login, _sell


def test_sell_without_buy_is_rejected(client):
    """매수 보유 기록이 없는 종목의 매도는 400으로 차단되어야 한다."""
    _login(client)
    res = client.post('/api/entry', json=_sell(qty=5))
    assert res.status_code == 400
    assert 'error' in res.json
    # 차단되었으므로 DB에 저장되지 않아야 한다
    assert client.get('/api/data').json == []

def test_oversell_is_rejected(client):
    """보유 수량을 초과하는 매도는 400으로 차단되어야 한다."""
    _login(client)
    assert client.post('/api/entry', json=_buy(qty=10)).status_code == 200
    res = client.post('/api/entry', json=_sell(qty=15))
    assert res.status_code == 400
    assert '초과' in res.json['error']
    # 매수 1건만 남아있어야 한다
    assert len(client.get('/api/data').json) == 1

def test_valid_sell_is_accepted(client):
    """보유 수량 이내의 매도는 정상 저장되어야 한다."""
    _login(client)
    assert client.post('/api/entry', json=_buy(qty=10)).status_code == 200
    assert client.post('/api/entry', json=_sell(qty=10)).status_code == 200
    assert len(client.get('/api/data').json) == 2

def test_update_oversell_is_rejected(client):
    """기록 수정(PUT) 시에도 보유 수량 초과 매도는 차단되어야 한다."""
    _login(client)
    client.post('/api/entry', json=_buy(qty=10))
    sell = _sell(qty=5, id=12345)
    assert client.post('/api/entry', json=sell).status_code == 200
    # 매도 수량을 20으로 늘리는 수정 시도 → 차단
    sell['quantity'] = 20
    res = client.put('/api/entry/12345', json=sell)
    assert res.status_code == 400

def test_dividend_not_blocked(client):
    """배당 기록은 보유 검증 대상이 아니므로 정상 저장되어야 한다."""
    _login(client)
    res = client.post('/api/entry', json={
        "type": "trade", "tradeType": "배당", "stockName": "삼성전자",
        "price": 500, "quantity": 10})
    assert res.status_code == 200




# ── 종목코드 표기 정규화 ────────────────────────────────────────────────
#
# ⭐️ 종목코드는 동일성 판정의 1순위 키인데, 접는 규칙이 갈려 있었다.
#    통계·화면·시세 조회는 대문자로 접고, 보유 매칭과 매도 검증은 저장된 원본
#    그대로 비교했다. 국내 6자리 숫자 코드에서는 드러나지 않고, 해외 티커에서만
#    "화면에는 보유가 보이는데 매도만 거부된다"로 나타난다.

def test_stored_stock_code_is_normalized_to_upper(client):
    """저장 시점에 종목코드가 대문자 정규형으로 눕는다."""
    _login(client)
    assert client.post('/api/entry', json=_buy(stock='Apple', stockCode=' aapl ')).status_code == 200
    assert client.get('/api/data').json[0]['stockCode'] == 'AAPL'


def test_sell_matches_holding_regardless_of_stock_code_case(client):
    """표기가 갈린 같은 티커의 매도는 보유에 걸려 통과해야 한다."""
    _login(client)
    assert client.post('/api/entry',
                       json=_buy(stock='Apple', stockCode='AAPL', qty=10)).status_code == 200
    # 봇은 소문자 티커를 보낼 수 있다 — 같은 종목이므로 매도가 막히면 안 된다.
    res = client.post('/api/entry', json=_sell(stock='Apple', stockCode='aapl', qty=10))
    assert res.status_code == 200, res.json


def test_migration_folds_stock_code_case_already_in_db(app):
    """규칙이 갈려 있던 시절에 소문자로 저장된 기록도 정규형으로 접힌다."""
    _insert_raw('trader', stockName='Apple', stockCode='aapl',
                tradeType='매수', quantity=10, price=100)
    _insert_raw('trader', stockName='삼성전자', stockCode='005930',
                tradeType='매수', quantity=10, price=70000)

    with backend_app.db_conn() as conn:
        c = conn.cursor()
        assert entry_logic.migrate_stock_code_case(c) == 1  # 이미 정규형인 행은 건드리지 않는다
        conn.commit()
        codes = {row[0] for row in c.execute(
            "SELECT stockCode FROM entries WHERE username = 'trader'")}
        assert codes == {'AAPL', '005930'}

        # 멱등 — 기동할 때마다 불러도 두 번째부터는 아무것도 고치지 않는다.
        assert entry_logic.migrate_stock_code_case(c) == 0
