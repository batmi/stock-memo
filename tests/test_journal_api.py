"""페이지를 나눠도 전체 이력의 손익·필터·계좌 격리가 유지되는지 검증한다."""

import json
from pathlib import Path

import pytest

from app.database.db import db_conn
from app.utils import statscache
from helpers import _ensure_user, _insert_raw, _login


def seed(username='reader', **values):
    values.setdefault('stockName', '종목')
    values.setdefault('stockCode', '005930')
    values.setdefault('rawDate', '2026-01-01T09:00')
    values.setdefault('tradeType', '매수')
    values.setdefault('price', 100)
    values.setdefault('quantity', 1)
    _insert_raw(username, **values)
    statscache.invalidate(username)


def get(client, endpoint, **query):
    for key in ('keywords', 'brokers'):
        if key in query:
            query[key] = json.dumps(query[key], ensure_ascii=False)
    return client.get('/api/journal/' + endpoint, query_string=query)


@pytest.mark.parametrize('endpoint', ['meta', 'portfolio', 'entries', 'calendar', 'stats', 'chart'])
def test_login_required(client, endpoint):
    assert get(client, endpoint).status_code == 401
    assert client.get('/api/entry/1').status_code == 401


def test_page_cursor_uses_trade_date_and_id_without_gaps(client):
    _login(client, 'reader')
    # id 순서와 거래 시각 순서가 다르고, 같은 거래 시각에도 여러 체결이 있다.
    for entry_id, day in [(1, '03'), (100, '01'), (2, '03'), (3, '02'), (4, '04')]:
        seed(id=entry_id, rawDate=f'2026-01-{day}T09:00')
    ids, cursor = [], None
    for _ in range(3):
        response = get(client, 'entries', limit=2, cursor=cursor or '')
        assert response.status_code == 200
        assert response.json['total'] == 5
        ids.extend(r['id'] for r in response.json['items'])
        cursor = response.json['nextCursor']
    assert ids == [4, 2, 1, 3, 100]
    assert cursor is None


def test_pages_and_detail_are_user_scoped(client):
    _login(client, 'reader')
    _ensure_user('other')
    seed(id=1, thoughts='<p>나의 원문</p>')
    seed('other', id=2, thoughts='타인 원문')
    assert [e['id'] for e in get(client, 'entries').json['items']] == [1]
    assert client.get('/api/entry/2').status_code == 404
    detail = client.get('/api/entry/1')
    assert detail.json['thoughts'] == '<p>나의 원문</p>'
    assert detail.headers['Cache-Control'] == 'no-store'
    assert get(client, 'meta').json['total'] == 1


def test_cursor_rejects_mutation_filter_change_and_tampering(client):
    _login(client, 'reader')
    seed(id=1)
    seed(id=2)
    cursor = get(client, 'entries', limit=1).json['nextCursor']
    assert get(client, 'entries', cursor=cursor, type='memo').status_code == 400
    assert get(client, 'entries', cursor=cursor + 'x').status_code == 400
    client.delete('/api/entry/1')
    assert get(client, 'entries', cursor=cursor).status_code == 409


def test_search_reads_full_body_beyond_preview_and_page(client):
    _login(client, 'reader')
    seed(id=1, thoughts='<p>' + '긴 본문 ' * 100 + 'Revenue &amp; 실적 100%_</p>', tags='보고서')
    for entry_id in range(2, 35):
        seed(id=entry_id)
    assert 1 not in [r['id'] for r in get(client, 'entries', limit=10).json['items']]
    response = get(client, 'entries', keywords=['revenue &', '실적', '100%_', '보고서'])
    assert [r['id'] for r in response.json['items']] == [1]
    assert 'Revenue' not in response.json['items'][0]['preview']
    assert 'thoughts' not in response.json['items'][0]
    assert get(client, 'entries', keywords=['不存在']).json['total'] == 0


def test_initial_payload_does_not_grow_with_body_size(client):
    _login(client, 'reader')
    body = '<p>' + '큰 본문과 첨부를 초기 응답에 넣지 않는다. ' * 1000 + '</p>'
    for entry_id in range(1, 81):
        seed(id=entry_id, thoughts=body, attachedImage='data:image/png;base64,' + 'A' * 2000)
    legacy = client.get('/api/data')
    meta = get(client, 'meta')
    page = get(client, 'entries', limit=15)
    summary = get(client, 'portfolio', month='2026-01')
    calendar = get(client, 'calendar', month='2026-01')
    size = sum(len(r.data) for r in (meta, page, summary, calendar))
    assert len(page.json['items']) == 15
    assert size < len(legacy.data) / 50
    assert 'thoughts' not in meta.get_data(as_text=True)
    assert 'attachedImage' not in page.get_data(as_text=True)
    assert summary.json['holdings']['005930']['qty'] == 80


def test_historical_buys_determine_portfolio_calendar_and_visible_chart(client):
    _login(client, 'reader')
    seed(id=1, rawDate='2024-01-01T09:00', quantity=10, price=100)
    seed(id=2, rawDate='2026-02-01T09:00', tradeType='매도', quantity=4, price=150)
    seed(id=3, rawDate='2026-02-02T09:00', tradeType='배당', quantity=6, price=2)
    first = get(client, 'entries', limit=1).json
    assert first['items'][0]['id'] == 3
    holding = get(client, 'portfolio', month='2026-02').json
    assert holding['holdings']['005930']['qty'] == 6
    assert holding['holdings']['005930']['avgPrice'] == 100
    assert holding['totalRealizedProfit'] == 212
    days = get(client, 'calendar', month='2026-02').json
    assert set(days) == {'2026-02-01', '2026-02-02'}
    assert days['2026-02-01']['profit'] == 200
    chart = get(client, 'chart', period='2026-02').json['monthlyData']['2026-02']
    assert chart['realized'] == 200 and chart['dividend'] == 12
    assert chart['cumulative'] == 212 and chart['cumulative_dividend'] == 12
    assert get(client, 'stats').json['totalPnl'] == 212


def test_canceled_and_submitted_orders_stay_in_history_but_not_in_totals(client):
    """API 명세: SUBMITTED·CANCELED 는 통계에서 제외되고 이력으로만 남는다.

    예전에는 매도 검증만 이 상태를 뺐고, 화면 집계는 체결로 셌다. 추정 체결을 취소로
    정정하면 보유 수량과 실현손익이 그대로 남았다.
    """
    _login(client, 'reader')
    seed(id=1, rawDate='2026-02-01T09:00', quantity=10, price=100, tradeStatus='FILLED')
    seed(id=2, rawDate='2026-02-02T09:00', quantity=10, price=100, tradeStatus='CANCELED')
    seed(id=3, rawDate='2026-02-03T09:00', quantity=10, price=100, tradeStatus='SUBMITTED')
    seed(id=4, rawDate='2026-02-04T09:00', tradeType='매도', quantity=10, price=300, tradeStatus='CANCELED')

    portfolio = get(client, 'portfolio', month='2026-02').json
    assert portfolio['holdings']['005930']['qty'] == 10
    assert portfolio['totalRealizedProfit'] == 0
    assert (portfolio['monthlyBuyCount'], portfolio['monthlySellCount']) == (1, 0)
    assert set(get(client, 'calendar', month='2026-02').json) == {'2026-02-01'}
    chart = get(client, 'chart', period='2026-02').json
    assert chart['monthlyData']['2026-02']['buy_volume'] == 1000
    assert chart['monthlyData']['2026-02']['realized'] == 0
    assert chart['evaluation'][0]['quantity'] == 10
    stats = get(client, 'stats').json
    assert (stats['buyCount'], stats['sellCount'], stats['totalPnl']) == (1, 0, 0)

    # 목록에는 이력으로 모두 남는다. 실제 보유는 10주라 청산 종목으로 숨지 않는다.
    history = get(client, 'entries').json
    assert history['total'] == 4
    assert {e['id']: e['tradeStatus'] for e in history['items']}[4] == 'CANCELED'


def test_legacy_rows_without_raw_date_use_one_order_for_portfolio_and_stats(client):
    """rawDate 없는 옛 기록: 거래일(date)과 작성 순서(id)가 다르면 예전에는 화면은 거래일,
    통계는 작성 시각으로 체결 순서를 정했다. 평단이 순서에 달려 실현손익이 갈렸다."""
    _login(client, 'reader')
    # 거래일 순서: 매수 100 → 매도 200(+100) → 매수 300. 작성(id) 순서는 그 반대다.
    seed(id=3, rawDate=None, date='2026. 1. 1. 오전 9:00', price=100)
    seed(id=2, rawDate=None, date='2026. 1. 2. 오전 9:00', price=200, tradeType='매도')
    seed(id=1, rawDate=None, date='2026. 1. 3. 오전 9:00', price=300)
    assert get(client, 'portfolio').json['totalRealizedProfit'] == 100
    assert get(client, 'stats').json['totalPnl'] == 100


def test_chart_fifo_consumes_sells_after_the_visible_period(client):
    _login(client, 'reader')
    seed(id=1, rawDate='2024-01-01T09:00', quantity=10, price=100)
    seed(id=2, rawDate='2026-01-01T09:00', quantity=10, price=200)
    seed(id=3, rawDate='2026-03-01T09:00', tradeType='매도', quantity=15, price=300)
    chart = get(client, 'chart', period='2026-01').json
    assert chart['evaluation'] == [{'period': '2026-01', 'stock': '종목', 'stockCode': '005930', 'quantity': 5, 'cost': 1000}]
    assert chart['monthlyData']['2026-01']['buy_volume'] == 2000


def test_weekly_chart_and_stats_agree(client):
    _login(client, 'reader')
    seed(id=1, quantity=2, rawDate='2026-01-02T09:00')
    seed(id=2, quantity=1, price=120, tradeType='매도', rawDate='2026-01-04T09:00')
    chart = get(client, 'chart', granularity='weekly', period='2025-12-29').json
    stats = get(client, 'stats', granularity='weekly').json
    assert chart['monthlyData']['2025-12-29']['realized'] == 20
    assert stats['monthly'][0]['month'] == '2025-12-29'
    assert stats['monthly'][0]['realized'] == 20


def test_filters_keep_related_memos_aliases_and_excluded_holdings(client):
    _login(client, 'reader')
    client.post('/api/mappings', json={'accounts': {
        '11-22': {'alias': '일반', 'exclude_from_stats': False},
        '33-44': {'alias': '일반', 'exclude_from_stats': True}}, 'brokers': {}})
    seed(id=1, stockName='옛이름', subAccount='1122', brokerAccount='264', tradeClass='장기', price=100)
    seed(id=2, stockName='새이름', subAccount='3344', brokerAccount='1', tradeClass='장기', price=900)
    seed(id=3, type='memo', stockName='옛이름', stockCode='', thoughts='관련 메모')
    meta = get(client, 'meta').json
    assert meta['stockIdentityByName']['옛이름'] == meta['stockIdentityByName']['새이름']
    assert meta['chartSubAccounts'] == ['일반']
    filtered = get(client, 'entries', brokers=['264', '1'], subAccount='일반', tradeClass='장기').json
    assert filtered['total'] == 3
    holdings = get(client, 'portfolio').json['holdings']
    assert holdings['005930']['totalCost'] == 100
    assert holdings['005930::제외']['totalCost'] == 900
    assert get(client, 'stats').json['totalBuyAmount'] == 100
    assert get(client, 'entries', stock='005930').json['total'] == 3


def test_closed_hidden_and_review_filters_use_all_records(client):
    _login(client, 'reader')
    seed(id=1, quantity=2)
    seed(id=2, quantity=2, tradeType='매도', needsReview=1)
    assert get(client, 'entries').json['total'] == 0
    assert get(client, 'entries', showClosed='1').json['total'] == 2
    assert get(client, 'entries', review='1').json['total'] == 1
    client.post('/api/entry/2/review')
    assert get(client, 'meta').json['reviewCount'] == 0
    seed(id=3, stockCode='AAPL', isHidden=1)
    assert get(client, 'entries').json['total'] == 0
    assert get(client, 'entries', showClosed='1').json['total'] == 3


def test_etag_distinguishes_filters_and_invalidates_after_write(client):
    _login(client, 'reader')
    seed(id=1)
    first = get(client, 'meta')
    headers = {'If-None-Match': first.headers['ETag']}
    assert client.get('/api/journal/meta', headers=headers).status_code == 304
    assert client.get('/api/journal/entries', headers=headers).status_code == 200
    client.delete('/api/entry/1')
    changed = client.get('/api/journal/meta', headers=headers)
    assert changed.status_code == 200 and changed.json['total'] == 0


@pytest.mark.parametrize('endpoint,query', [
    ('entries', {'limit': 'bad'}), ('entries', {'limit': 1000}),
    ('entries', {'keywords': '{}'}), ('entries', {'brokers': '[1]'}),
    ('calendar', {'month': '2026-99'}), ('chart', {'period': 'bad'}),
    ('chart', {}), ('stats', {'granularity': 'bad'}),
])
def test_invalid_query_is_400(client, endpoint, query):
    _login(client, 'reader')
    assert client.get('/api/journal/' + endpoint, query_string=query).status_code == 400


FIXTURES = json.loads((Path(__file__).parent / 'fixtures/parity_fixtures.json').read_text())
EXPECTED = json.loads((Path(__file__).parent / 'fixtures/stats_expected.json').read_text())


@pytest.mark.parametrize('name', list(FIXTURES))
def test_server_portfolio_matches_existing_profit_fixtures(client, name):
    _login(client, 'reader')
    with db_conn() as conn:
        for row in FIXTURES[name]:
            fields = list(row)
            conn.execute(f"INSERT INTO entries (username, {', '.join(fields)}) "
                         f"VALUES (?, {', '.join('?' for _ in fields)})", ['reader', *row.values()])
        conn.commit()
    got = get(client, 'portfolio').json
    assert got['totalRealizedProfit'] == pytest.approx(EXPECTED[name]['totalPnl'])
