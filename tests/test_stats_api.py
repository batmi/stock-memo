"""/api/stats — 집계 결과가 화면에 나가기까지의 필터링.

계산식 자체의 회귀는 tests/test_stats.py 가 본다. 여기서는 "무엇을 빼고 세는가"
(모의거래, 제외 표시한 계좌)를 API 레벨에서 확인한다.
"""

import json


from helpers import _buy, _ensure_user, _insert_raw, _login, _sell


# ─────────────────────────────────────────────────────────────
# ⭐️ 매매 성과 분석(/api/stats) 테스트
# ─────────────────────────────────────────────────────────────
def test_stats_empty(client):
    """기록이 없으면 0값 통계를 반환한다."""
    _login(client, 'statsempty')
    res = client.get('/api/stats')
    assert res.status_code == 200
    s = res.json
    assert s['totalRealized'] == 0
    assert s['sellCount'] == 0
    assert s['monthly'] == []

def test_stats_realized_and_winrate(client):
    """실현손익/승률/손익비가 이동평균단가 기준으로 정확히 계산된다."""
    _login(client, 'statscalc')
    # 100원 10주 매수 → 평단 100
    client.post('/api/entry', json=_buy(stock='A', qty=10, price=100,
                                        rawDate='2024-01-10T09:00', id=1))
    # 120원 5주 매도 → 이익 (120-100)*5 = +100
    client.post('/api/entry', json=_sell(stock='A', qty=5, price=120,
                                         rawDate='2024-02-10T09:00', id=2))
    # 80원 5주 매도 → 손실 (80-100)*5 = -100
    client.post('/api/entry', json=_sell(stock='A', qty=5, price=80,
                                         rawDate='2024-03-10T09:00', id=3))

    s = client.get('/api/stats').json
    assert round(s['totalRealized']) == 0           # +100 -100
    assert s['sellCount'] == 2
    assert s['winCount'] == 1 and s['lossCount'] == 1
    assert round(s['winRate']) == 50
    assert round(s['avgWin']) == 100
    assert round(s['avgLoss']) == 100
    assert round(s['profitFactor'], 2) == 1.0
    # 월별 3개월(매수1, 매도2)이 집계되어야 한다
    assert len(s['monthly']) == 3
    # 종목별 집계
    assert s['perStock'][0]['stock'] == 'A'
    assert s['perStock'][0]['sellCount'] == 2

def test_stats_holding_period_and_dividend(client):
    """평균 보유기간(FIFO)과 배당 수익이 반영된다."""
    _login(client, 'statshold')
    client.post('/api/entry', json=_buy(stock='B', qty=10, price=100,
                                        rawDate='2024-01-01T09:00', id=1))
    # 10일 보유 후 전량 매도
    client.post('/api/entry', json=_sell(stock='B', qty=10, price=110,
                                         rawDate='2024-01-11T09:00', id=2))
    client.post('/api/entry', json={
        "type": "trade", "tradeType": "배당", "stockName": "B",
        "price": 50, "quantity": 10, "rawDate": "2024-02-01T09:00", "id": 3})

    s = client.get('/api/stats').json
    assert round(s['avgHoldingDays']) == 10
    assert round(s['totalDividend']) == 500
    assert round(s['totalPnl']) == round(s['totalRealized'] + 500)

def test_stats_exclude_simulated_trades(client):
    """모의투자 체결이 실현손익·승률 통계를 오염시키면 안 된다."""
    _login(client, 'simstats')
    # 실거래: 100원 10주 매수 → 120원 10주 매도 = +200
    client.post('/api/entry', json=_buy(stock='A', qty=10, price=100,
                                        rawDate='2024-01-10T09:00', id=1))
    client.post('/api/entry', json=_sell(stock='A', qty=10, price=120,
                                         rawDate='2024-02-10T09:00', id=2))
    # 모의투자: 크게 손실 난 체결 — 통계에 섞이면 총 실현손익이 음수가 된다
    _insert_raw('simstats', id=101, stockName='B', tradeType='매수', price=1000,
                quantity=100, rawDate='2024-01-11T09:00', isSimulated=1)
    _insert_raw('simstats', id=102, stockName='B', tradeType='매도', price=100,
                quantity=100, rawDate='2024-02-11T09:00', isSimulated=1)

    s = client.get('/api/stats').json
    assert round(s['totalRealized']) == 200      # 모의 손실(-90,000)이 섞이지 않았다
    assert s['sellCount'] == 1
    assert s['lossCount'] == 0
    assert [p['stock'] for p in s['perStock']] == ['A']

def test_stats_filtered_request_also_excludes_simulated(client):
    """entry_ids 를 직접 넘겨도(차트 필터 경로) 모의투자는 걸러져야 한다."""
    _login(client, 'simstats2')
    client.post('/api/entry', json=_buy(stock='A', qty=10, price=100,
                                        rawDate='2024-01-10T09:00', id=1))
    client.post('/api/entry', json=_sell(stock='A', qty=10, price=120,
                                         rawDate='2024-02-10T09:00', id=2))
    _insert_raw('simstats2', id=101, stockName='B', tradeType='매수', price=1000,
                quantity=100, rawDate='2024-01-11T09:00', isSimulated=1)
    _insert_raw('simstats2', id=102, stockName='B', tradeType='매도', price=100,
                quantity=100, rawDate='2024-02-11T09:00', isSimulated=1)

    s = client.post('/api/stats', json={'entry_ids': [1, 2, 101, 102]}).json
    assert round(s['totalRealized']) == 200
    assert [p['stock'] for p in s['perStock']] == ['A']

def test_simulated_entries_are_still_returned_to_dashboard(client):
    """카드 슬롯에는 떠야 하므로 목록 조회에서는 빠지면 안 된다."""
    _login(client, 'simlist')
    _insert_raw('simlist', id=101, stockName='B', tradeType='매수', price=1000,
                quantity=100, rawDate='2024-01-11T09:00', isSimulated=1)

    data = client.get('/api/data').json
    assert len(data) == 1
    assert data[0]['stockName'] == 'B'
    assert data[0]['isSimulated'] == 1

def test_stats_exclude_flagged_account(client):
    """계좌 관리에서 '금액 계산 제외'로 체크한 계좌는 통계에서 빠져야 한다.

    계좌 별칭은 언제든 바꿀 수 있으므로 이름이 아니라 계좌번호(exclude_from_stats)로 판정한다.
    """
    _login(client, 'excacct')
    _ensure_user('excacct')
    assert client.post('/api/mappings', json={
        "brokers": {},
        "accounts": {
            "11112222-01": {"broker_code": "243", "broker_name": "한국투자증권",
                            "alias": "실거래계좌"},
            "33334444-01": {"broker_code": "243", "broker_name": "한국투자증권",
                            "alias": "연습계좌", "exclude_from_stats": True},
        }
    }).status_code == 200
    # 실거래 계좌: 100원 10주 매수 → 120원 10주 매도 = +200
    _insert_raw('excacct', id=1, stockName='A', tradeType='매수', price=100,
                quantity=10, rawDate='2024-01-10T09:00', subAccount='11112222-01')
    _insert_raw('excacct', id=2, stockName='A', tradeType='매도', price=120,
                quantity=10, rawDate='2024-02-10T09:00', subAccount='11112222-01')
    # 제외 계좌: 큰 손실 — 섞이면 총 실현손익이 음수가 된다. 하이픈 없는 표기로 들어와도 걸러야 한다.
    _insert_raw('excacct', id=101, stockName='B', tradeType='매수', price=1000,
                quantity=100, rawDate='2024-01-11T09:00', subAccount='3333444401')
    _insert_raw('excacct', id=102, stockName='B', tradeType='매도', price=100,
                quantity=100, rawDate='2024-02-11T09:00', subAccount='3333444401')

    s = client.get('/api/stats').json
    assert round(s['totalRealized']) == 200
    assert [p['stock'] for p in s['perStock']] == ['A']

    # 차트 필터 경로(entry_ids 직접 전달)도 동일하게 걸러진다
    s2 = client.post('/api/stats', json={'entry_ids': [1, 2, 101, 102]}).json
    assert round(s2['totalRealized']) == 200

    # 계좌번호 없이 이름만 남은 수기 기록은 별칭으로 대조해 걸러낸다
    _insert_raw('excacct', id=103, stockName='C', tradeType='매수', price=1000,
                quantity=100, rawDate='2024-01-12T09:00', accountName='연습계좌')
    _insert_raw('excacct', id=104, stockName='C', tradeType='매도', price=100,
                quantity=100, rawDate='2024-02-12T09:00', accountName='연습계좌')
    s3 = client.get('/api/stats').json
    assert round(s3['totalRealized']) == 200

    # 체크를 풀면 다시 통계에 잡힌다 (매핑 저장 시 캐시가 무효화돼야 한다)
    client.post('/api/mappings', json={
        "brokers": {},
        "accounts": {
            "33334444-01": {"broker_code": "243", "broker_name": "한국투자증권",
                            "alias": "연습계좌"},
        }
    })
    s4 = client.get('/api/stats').json
    assert round(s4['totalRealized']) < 0
