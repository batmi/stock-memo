"""구조 검토에서 발견한 저장·동시성·복원의 경계 사례."""

from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
import threading
import time
import zipfile

import pytest

import config
from app.database import entry_logic
from app.database.db import db_conn
from app.routes import auth
from app.services import accounts, entries, restores
from app.services import stats
from helpers import _buy, _login, _sell
from trading_api.entries import _insert_trade


@pytest.mark.parametrize('field,value', [
    ('quantity', 'oops'), ('quantity', -1), ('quantity', 0), ('quantity', True),
    ('quantity', float('inf')), ('price', -1), ('price', float('nan')),
    ('price', True), ('stockName', []), ('thoughts', {}),
    ('tradeType', 'UNKNOWN'), ('rawDate', '2026-99-01'),
])
def test_web_rejects_invalid_trade_without_poisoning_views(client, field, value):
    _login(client)
    response = client.post('/api/entry', json=_buy(**{field: value}))
    assert response.status_code == 400
    assert client.get('/api/data').json == []
    assert client.get('/api/journal/portfolio').status_code == 200


def test_web_edit_preserves_bot_identity_and_currency(client):
    _login(client)
    original = _buy(id=1, brokerExecutionId='execution-1', currency='USD', exchange='NASDAQ',
                    assetType='STOCK', source='broker', tradeStatus='FILLED', fee=2)
    entries.create('trader', original)
    assert client.put('/api/entry/1', json=_buy(thoughts='메모 수정')).status_code == 200
    with db_conn() as conn:
        row = dict(conn.execute('SELECT * FROM entries WHERE id=1').fetchone())
        for field in ('brokerExecutionId', 'currency', 'exchange', 'assetType', 'source', 'fee'):
            assert row[field] == original[field]
        _, inserted, _ = _insert_trade(conn.cursor(), 'trader', dict(original, id=None))
        conn.commit()
        assert not inserted
        assert conn.execute('SELECT COUNT(*) FROM entries').fetchone()[0] == 1


def test_web_memo_edit_keeps_canceled_sell_inactive(client):
    _login(client)
    with db_conn() as conn:
        entry_logic.insert_entry(conn.cursor(), 'trader', _sell(id=1, tradeStatus='CANCELED', source='broker'))
        conn.commit()
    assert client.put('/api/entry/1', json=_sell(thoughts='취소 사유')).status_code == 200
    assert client.get('/api/entry/1').json['tradeStatus'] == 'CANCELED'


def test_concurrent_sells_cannot_spend_the_same_position(app, monkeypatch):
    _login(app.test_client())
    entries.create('trader', _buy(id=1))
    barrier = threading.Barrier(2)
    original = entry_logic.validate_trade_entry

    def delayed_validation(*args, **kwargs):
        result = original(*args, **kwargs)
        time.sleep(0.05)  # 기존 구현의 검사와 INSERT 사이에 다른 요청이 들어온다.
        return result

    def sell(entry_id):
        barrier.wait(timeout=5)
        try:
            entries.create('trader', _sell(id=entry_id))
            return 'saved'
        except entries.InvalidEntry:
            return 'rejected'

    monkeypatch.setattr(entry_logic, 'validate_trade_entry', delayed_validation)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(sell, [2, 3])) == ['rejected', 'saved']


def test_concurrent_first_signups_create_only_one_admin(app, monkeypatch):
    barrier = threading.Barrier(2)

    def hash_password(password):
        barrier.wait(timeout=5)
        return 'test-hash'

    def signup(username):
        return app.test_client().post('/signup', data={
            'username': username, 'password': 'Password123!', 'password_confirm': 'Password123!'})

    monkeypatch.setattr(auth, 'generate_password_hash', hash_password)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert all(r.status_code == 200 for r in pool.map(signup, ['first_one', 'first_two']))
    with db_conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 2
        assert conn.execute('SELECT COUNT(*) FROM users WHERE is_admin=1').fetchone()[0] == 1


@pytest.mark.parametrize('sale', [
    _sell(id=2, subAccount='B', rawDate='2026-01-03T10:00'),
    _sell(id=2, subAccount='A', rawDate='2026-01-01T10:00'),
    _sell(id=2, subAccount='A', rawDate='2026-01-03T10:00', currency='USD'),
])
def test_sell_cannot_use_another_account_currency_or_future_buy(client, sale):
    _login(client)
    assert client.post('/api/entry', json=_buy(
        id=1, subAccount='A', rawDate='2026-01-02T10:00', currency='KRW')).status_code == 200
    assert client.post('/api/entry', json=sale).status_code == 400


def backup(entries_to_restore, mappings=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('data.json', json.dumps(entries_to_restore))
        zf.writestr('uploads/new.png', 'new')
        if mappings is not None:
            zf.writestr(accounts.BACKUP_ARCNAME, json.dumps(mappings))
    buf.seek(0)
    return {'file': (buf, 'backup.zip')}


def test_failed_mapping_restore_rolls_back_entries_and_attachments(client, monkeypatch):
    _login(client)
    entries.create('trader', {'id': 1, 'type': 'memo', 'title': 'old'})
    folder = os.path.join(config.UPLOAD_FOLDER, 'trader')
    os.makedirs(folder, exist_ok=True)
    old_file = os.path.join(folder, 'old.png')
    with open(old_file, 'w') as f:
        f.write('old')

    def fail(*args, **kwargs):
        raise RuntimeError('mapping storage unavailable')

    monkeypatch.setattr(accounts, 'save', fail)
    response = client.post('/api/restore', data=backup(
        [{'id': 2, 'type': 'memo', 'title': 'new'}], {'accounts': {'A': {'alias': 'A'}}}))
    assert response.status_code == 500
    assert [e['title'] for e in client.get('/api/data').json] == ['old']
    assert os.listdir(folder) == ['old.png']


def test_app_can_restore_its_own_backup_larger_than_normal_upload_limit(client):
    _login(client)
    folder = os.path.join(config.UPLOAD_FOLDER, 'trader')
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, 'large.jpg'), 'wb') as f:
        f.write(os.urandom(17 * 1024 * 1024))
    response = client.get('/api/backup')
    try:
        blob = response.data
    finally:
        response.close()
    assert len(blob) > config.MAX_CONTENT_LENGTH
    restored = client.post('/api/restore', data={'file': (io.BytesIO(blob), 'own.zip')})
    assert restored.status_code == 200
    assert os.path.getsize(os.path.join(folder, 'large.jpg')) == 17 * 1024 * 1024


def test_account_costs_are_consistent_in_all_financial_views(client):
    _login(client)
    for row in [
        _buy(id=1, subAccount='A', qty=1, price=100, rawDate='2026-01-01T09:00'),
        _buy(id=2, subAccount='B', qty=1, price=200, rawDate='2026-01-02T09:00'),
        _sell(id=3, subAccount='A', qty=1, price=120, rawDate='2026-01-03T09:00'),
    ]:
        assert client.post('/api/entry', json=row).status_code == 200
    assert client.get('/api/journal/stats').json['totalRealized'] == 20
    portfolio = client.get('/api/journal/portfolio').json
    assert portfolio['totalRealizedProfit'] == 20
    assert portfolio['holdings']['005930']['totalCost'] == 200
    calendar = client.get('/api/journal/calendar?month=2026-01').json
    assert calendar['2026-01-03']['profit'] == 20
    chart = client.get('/api/journal/chart?period=2026-01').json
    assert chart['monthlyData']['2026-01']['realized'] == 20
    assert sum(e['cost'] for e in chart['evaluation']) == 200


def test_money_views_never_add_different_currencies(client):
    _login(client)
    krw = _buy(id=1, price=10000, qty=1, currency='KRW')
    usd = _buy(id=2, price=100, qty=1, currency='USD', stockCode='AAPL', stock='Apple')
    entries.create('trader', krw)
    entries.create('trader', usd)
    assert client.get('/api/journal/meta').json['currencies'] == ['KRW', 'USD']
    for currency, expected in [('KRW', 10000), ('USD', 100)]:
        result = client.get('/api/journal/stats?currency=' + currency).json
        assert result['totalBuyAmount'] == expected
        assert result['currency'] == currency
        portfolio = client.get('/api/journal/portfolio?currency=' + currency).json
        assert sum(p['totalCost'] for p in portfolio['holdings'].values()) == expected
    with pytest.raises(ValueError, match='통화'):
        stats.compute_trade_stats([krw, usd])


def test_exchange_trade_day_controls_calendar_and_monthly_stats(client):
    _login(client)
    entries.create('trader', _buy(id=1, currency='USD', tradeDate='2026-01-31',
                                 rawDate='2026-02-01T09:00', exchange='NASDAQ'))
    days = client.get('/api/journal/calendar?month=2026-01&currency=USD').json
    assert list(days) == ['2026-01-31']
    assert client.get('/api/journal/stats?currency=USD').json['monthly'][0]['month'] == '2026-01'


def test_editing_bot_time_updates_derived_trade_day(client):
    _login(client)
    entries.create('trader', _buy(id=1, currency='USD', exchange='NASDAQ',
                                 rawDate='2026-02-01T06:00', tradeDate='2026-01-31',
                                 executedAtUtc='2026-01-31T21:00:00Z'))
    assert client.put('/api/entry/1', json={'rawDate': '2026-02-02T06:00'}).status_code == 200
    row = client.get('/api/entry/1').json
    assert row['tradeDate'] == '2026-02-01'
    assert row['executedAtUtc'] == '2026-02-01T21:00:00Z'


def test_legacy_memo_with_null_numbers_can_still_be_edited(client):
    _login(client)
    with db_conn() as conn:
        conn.execute("INSERT INTO entries (id, username, type) VALUES (1, 'trader', 'memo')")
        conn.commit()
    assert client.put('/api/entry/1', json={'thoughts': '새 메모'}).status_code == 200
    assert client.get('/api/entry/1').json['thoughts'] == '새 메모'


def test_restore_file_swap_failure_preserves_rows_files_and_mappings(client, monkeypatch):
    _login(client)
    entries.create('trader', {'id': 1, 'type': 'memo', 'title': 'old'})
    folder = os.path.join(config.UPLOAD_FOLDER, 'trader')
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, 'old.png'), 'w') as f:
        f.write('old')
    old_mappings = {'brokers': {}, 'accounts': {'old': {'alias': '원본'}}}
    assert client.post('/api/mappings', json=old_mappings).status_code == 200
    rename = restores.os.rename

    def fail_staging(source, target):
        if source.endswith('/staging'):
            raise OSError('disk failure')
        return rename(source, target)

    monkeypatch.setattr(restores.os, 'rename', fail_staging)
    response = client.post('/api/restore', data=backup(
        [{'id': 2, 'type': 'memo', 'title': 'new'}], {'accounts': {'new': {'alias': '새 계좌'}}}))
    assert response.status_code == 500
    assert [e['title'] for e in client.get('/api/data').json] == ['old']
    assert os.listdir(folder) == ['old.png']
    assert client.get('/api/mappings').json == old_mappings
    assert os.listdir(config.UPLOAD_FOLDER) == ['trader']


def test_restore_limit_does_not_relax_regular_json_limit(client, monkeypatch):
    _login(client)
    monkeypatch.setitem(client.application.config, 'MAX_CONTENT_LENGTH', 1024)
    assert client.post('/api/restore', data=backup([{'id': 1, 'type': 'memo'}])).status_code == 200
    assert client.post('/api/entry', json={'type': 'memo', 'thoughts': 'x' * 2048}).status_code == 413
