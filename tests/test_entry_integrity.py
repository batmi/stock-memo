"""매도 무결성 — 보유하지 않은 수량은 팔 수 없다.

대상: entry_logic.validate_trade_entry / check_sell_integrity.
봇 경로(차단하지 않고 needsReview 표시)는 tests/test_trading_api.py 가 본다.
"""

import json


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

def test_simulated_holdings_do_not_block_real_sell(client):
    """모의 보유가 실거래 매도 검증에 끼어들면 안 된다 (그 반대도 마찬가지)."""
    _login(client, 'simhold')
    # 모의로만 100주 보유
    _insert_raw('simhold', id=101, stockName='C', tradeType='매수', price=1000,
                quantity=100, rawDate='2024-01-11T09:00', isSimulated=1)

    # 실거래 보유는 0 이므로 실거래 매도는 차단되어야 한다
    res = client.post('/api/entry', json=_sell(stock='C', qty=10, price=1200))
    assert res.status_code == 400
