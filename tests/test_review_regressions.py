"""구조 개선 후 재점검에서 발견한 복원·봇 정정·목록 경계 사례."""

import io
import json
import zipfile

import pytest

from app.database.db import db_conn
from app.services import entries
from helpers import _buy, _login, _sell
from test_trading_api import _trade, api as api
from trading_api.entries import _insert_trade


@pytest.mark.parametrize('field,value', [
    ('quantity', 'oops'), ('quantity', True), ('quantity', -1),
    ('price', float('inf')), ('fee', 'oops'), ('stockName', []),
])
def test_restore_rejects_invalid_fields_before_replacing_existing_data(client, field, value):
    _login(client)
    entries.create('trader', {'id': 1, 'type': 'memo', 'title': '원본'})
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, 'w') as archive:
        archive.writestr('data.json', json.dumps([_buy(id=2, **{field: value})]))
    blob.seek(0)
    response = client.post('/api/restore', data={'file': (blob, 'broken.zip')})
    assert response.status_code == 400
    assert '1번째' in response.json['error']
    assert [row['title'] for row in client.get('/api/data').json] == ['원본']
    assert client.get('/api/journal/portfolio').status_code == 200


@pytest.mark.parametrize('exchange,expected_day', [
    ('KRX', '2026-02-02'), ('NASDAQ', '2026-02-01'), ('UNKNOWN', '2026-02-01'),
])
def test_bot_time_correction_updates_exchange_trade_day(api, exchange, expected_day):
    client, headers = api['client'], api['headers']
    response = client.post('/api/v1/trades', headers=headers, json=_trade(
        exchange=exchange, executedAt='2026-01-31T06:00:00+09:00'))
    assert response.status_code == 201
    entry_id = response.json['id']
    response = client.patch(f'/api/v1/trades/{entry_id}', headers=headers,
                            json={'executedAt': '2026-02-01T16:00:00-05:00'})
    assert response.status_code == 200
    with db_conn() as conn:
        row = conn.execute('SELECT * FROM entries WHERE id=?', (entry_id,)).fetchone()
    assert row['tradeDate'] == expected_day
    _login(client, 'bot')
    days = client.get('/api/journal/calendar?month=2026-02').json
    assert list(days) == [expected_day]
    assert client.get('/api/journal/calendar?month=2026-01').json == {}


@pytest.mark.parametrize('currency', ['WRONG', True, '원'])
def test_bot_rejects_currency_that_cannot_be_queried(api, currency):
    response = api['client'].post('/api/v1/trades', headers=api['headers'], json=_trade(currency=currency))
    assert response.status_code == 400
    assert response.json['details']['field'] == 'currency'


def test_other_account_oversell_does_not_hide_a_held_stock_from_history(client):
    _login(client)
    entries.create('trader', _buy(id=1, subAccount='A', rawDate='2026-01-01T09:00'))
    with db_conn(write=True) as conn:
        _insert_trade(conn.cursor(), 'trader', _sell(
            id=2, subAccount='B', rawDate='2026-01-02T09:00', brokerExecutionId='oversell-B'))
        conn.commit()
    portfolio = client.get('/api/journal/portfolio').json
    assert portfolio['holdings']['005930']['qty'] == 10
    history = client.get('/api/journal/entries').json
    assert history['total'] == 2
    assert {row['id'] for row in history['items']} == {1, 2}


def test_restore_keeps_legacy_optional_fields_and_normalizes_numeric_strings(client):
    _login(client)
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, 'w') as archive:
        archive.writestr('data.json', json.dumps([
            {'id': 1, 'type': 'buy', 'stockName': 'Apple', 'stockCode': 'AAPL',
             'price': '12.5', 'quantity': '2', 'currency': ' usd ', 'fee': None},
            {'id': 2, 'type': 'memo', 'price': None, 'quantity': None, 'thoughts': '옛 메모'},
        ]))
    blob.seek(0)
    response = client.post('/api/restore', data={'file': (blob, 'legacy.zip')})
    assert response.status_code == 200
    row = client.get('/api/entry/1').json
    assert (row['type'], row['tradeType'], row['currency'], row['fee']) == ('trade', '매수', 'USD', None)
    assert client.get('/api/journal/portfolio?currency=USD').json['holdings']['AAPL']['totalCost'] == 25


@pytest.mark.parametrize('currency,expected', [(' usd ', 'USD'), (None, 'KRW'), ('', 'KRW')])
def test_bot_currency_normalization_keeps_valid_legacy_inputs(api, currency, expected):
    response = api['client'].post('/api/v1/trades', headers=api['headers'], json=_trade(currency=currency))
    assert response.status_code == 201
    assert response.json['currency'] == expected
