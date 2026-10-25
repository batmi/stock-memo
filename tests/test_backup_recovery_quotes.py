"""백업 검증/중단 복구 및 거래소를 구분하는 현재가 계약 회귀 검사."""

from app.services.users import user_dir as account_storage_dir
import io
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

import config
from app.database.db import db_conn
from app.services import backups, prices, quotes, restore_recovery
from app.services import restores
from helpers import _ensure_user, _insert_raw, _login


def archive(rows, extra=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as target:
        target.writestr('data.json', json.dumps(rows))
        for name, data in (extra or {}).items():
            target.writestr(name, data)
    stream.seek(0)
    return stream


@pytest.mark.parametrize('field,value', [('price', 'bad'), ('quantity', -1), ('fee', 'NaN')])
def test_backup_verification_uses_the_restore_contract(client, tmp_path, field, value):
    _login(client)
    stream = archive([{'id': 1, field: value}])
    path = tmp_path / 'bad.zip'
    path.write_bytes(stream.getvalue())
    valid, message = backups.verify_backup_zip(path, 1)
    assert not valid and field in message
    response = client.post('/api/restore/preview', data={'file': (stream, 'bad.zip')})
    assert response.status_code == 400


def test_backup_verification_checks_manifest_and_settings(tmp_path):
    path = tmp_path / 'missing-settings.zip'
    path.write_bytes(archive([], {'manifest.json': '{"formatVersion":2}'}).getvalue())
    valid, message = backups.verify_backup_zip(path, 0)
    assert not valid and '누락' in message


def test_new_backup_format_preserves_account_scopes_and_cost_basis(client):
    _login(client)
    _insert_raw('trader', id=1, stockName='Asset', tradeType='매도', price=200, quantity=1,
                realizedPnl=100, realizedPnlSource='recorded', realizedPnlBasis='net')
    scope = '["243","A"]'
    with db_conn(write=True) as conn:
        conn.execute('INSERT INTO split_rules VALUES (?,?,?,?,?,?)',
                     ('trader', '005930', 'KRW', '2026-01-01', 'recorded', scope))
        conn.commit()
    payload = client.get('/api/backup').data
    with zipfile.ZipFile(io.BytesIO(payload)) as stored:
        assert json.loads(stored.read('manifest.json'))['formatVersion'] == 4
        assert json.loads(stored.read('split_rules.json'))[0]['scope'] == scope
    result = client.post('/api/restore', data={'file': (io.BytesIO(payload), 'new.zip')})
    assert result.status_code == 200
    assert client.get('/api/data').json[0]['realizedPnlBasis'] == 'net'
    with db_conn() as conn:
        assert conn.execute('SELECT scope FROM split_rules').fetchone()[0] == scope


def test_version_two_backup_remains_readable(client):
    _login(client)
    stream = archive([{'id': 10, 'type': 'memo'}], {
        'manifest.json': '{"formatVersion":2}', 'account_info.json': '{}',
        'split_rules.json': '[{"code":"005930","currency":"KRW","day":"2026-01-01","mode":"recorded"}]',
    })
    assert client.post('/api/restore', data={'file': (stream, 'v2.zip')}).status_code == 200
    with db_conn() as conn:
        assert conn.execute('SELECT scope FROM split_rules').fetchone()[0] == ''


def test_restore_preserves_legacy_date_before_id_collision(client):
    _login(client)
    original_id = 1767574800000  # 2026-01-05 10:00 KST
    _insert_raw('another', id=original_id, type='memo')
    stream = archive([{'id': original_id, 'type': 'memo', 'rawDate': 'not-a-date'}])
    response = client.post('/api/restore', data={'file': (stream, 'old.zip')})
    assert response.status_code == 200
    row = client.get('/api/data').json[0]
    assert row['id'] != original_id
    assert row['rawDate'] == '2026-01-05T10:00:00'


@pytest.mark.parametrize('phase', ['prepared', 'old_moved', 'new_moved', 'committed'])
def test_process_exit_during_restore_recovers_database_and_files(app, tmp_path, phase):
    _ensure_user('recover')
    folder = Path(account_storage_dir(config.UPLOAD_FOLDER, 'recover'))
    folder.mkdir(parents=True)
    (folder / 'old.txt').write_text('old')
    script = '''
import os, sys
from pathlib import Path
import config
config.DB_FILE, config.UPLOAD_FOLDER, phase = sys.argv[1:]
from app.database.db import get_db
from app.services.restore_recovery import prepare
from app.services.users import user_dir as account_storage_dir
work = Path(config.UPLOAD_FOLDER) / '.restore-crash'
work.mkdir()
staging = work / 'staging'
staging.mkdir()
(staging / 'new.txt').write_text('new')
folder = Path(account_storage_dir(config.UPLOAD_FOLDER, 'recover'))
conn = get_db()
conn.execute('BEGIN IMMEDIATE')
conn.execute("UPDATE users SET preferences='new' WHERE username='recover'")
prepare(str(work), conn, 'recover', True)
if phase != 'prepared':
    os.rename(folder, work / 'retired')
if phase in ('new_moved', 'committed'):
    os.rename(staging, folder)
if phase == 'committed':
    conn.commit()
os._exit(79)
'''
    result = subprocess.run([sys.executable, '-c', script, config.DB_FILE, config.UPLOAD_FOLDER, phase],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 79, result.stderr
    restore_recovery.recover_pending_restores()
    restore_recovery.recover_pending_restores()  # 반복 실행해도 복구 결과는 유지된다.
    committed = phase == 'committed'
    assert [p.name for p in folder.iterdir()] == (['new.txt'] if committed else ['old.txt'])
    assert not list(Path(config.UPLOAD_FOLDER).glob('.restore-*'))
    with db_conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM restore_commits').fetchone()[0] == 0
        assert conn.execute("SELECT preferences FROM users WHERE username='recover'").fetchone()[0] == (
            'new' if committed else None)


def test_recovery_does_not_replace_a_recreated_accounts_files(app):
    _ensure_user('recreated')
    base = Path(config.UPLOAD_FOLDER)
    work = base / '.restore-previous-owner'
    work.mkdir()
    (work / 'retired').mkdir()
    (work / 'retired' / 'private.txt').write_text('previous owner')
    with db_conn(write=True) as conn:
        restore_recovery.prepare(str(work), conn, 'recreated', True)
        conn.execute("DELETE FROM users WHERE username='recreated'")
        conn.execute("INSERT INTO users(username,password_hash) VALUES ('recreated','new')")
        conn.commit()
    folder = Path(account_storage_dir(config.UPLOAD_FOLDER, 'recreated'))
    folder.mkdir(parents=True)
    (folder / 'new-owner.txt').write_text('new owner')
    restore_recovery.recover_pending_restores()
    assert [p.name for p in folder.iterdir()] == ['new-owner.txt']


def test_failed_rollback_blocks_later_restore_until_recovery(client, monkeypatch):
    _login(client, 'blocked')
    folder = Path(account_storage_dir(config.UPLOAD_FOLDER, 'blocked'))
    folder.mkdir(parents=True)
    (folder / 'original.txt').write_text('original')
    original_rename = restores.os.rename

    def fail_move(source, target):
        if Path(source).name in ('staging', 'retired'):
            raise OSError('temporary filesystem failure')
        return original_rename(source, target)

    monkeypatch.setattr(restores.os, 'rename', fail_move)
    result = client.post('/api/restore', data={'file': (archive([]), 'failed.zip')})
    assert result.status_code == 500
    assert list(Path(config.UPLOAD_FOLDER).glob('.restore-*/retired/original.txt'))
    monkeypatch.setattr(restores.os, 'rename', original_rename)
    result = client.post('/api/restore', data={'file': (archive([]), 'new.zip')})
    assert result.status_code == 503
    restore_recovery.recover_pending_restores()
    assert (folder / 'original.txt').read_text() == 'original'
    assert client.get('/api/data').status_code == 200


@pytest.mark.parametrize('interrupt_after', ['blocked-rename', 'staging'])
def test_exception_after_rename_still_restores_original_files(client, monkeypatch, interrupt_after):
    _login(client, 'blocked-rename')
    folder = Path(account_storage_dir(config.UPLOAD_FOLDER, 'blocked-rename'))
    folder.mkdir(parents=True)
    (folder / 'original.txt').write_text('original')
    original_rename = restores.os.rename

    def move_then_fail(source, target):
        original_rename(source, target)
        if (interrupt_after == 'blocked-rename' and Path(source) == folder
                or interrupt_after == 'staging' and Path(source).name == 'staging'):
            raise OSError('rename completed before interruption')

    monkeypatch.setattr(restores.os, 'rename', move_then_fail)
    result = client.post('/api/restore', data={'file': (archive([]), 'failed.zip')})
    assert result.status_code == 500
    assert (folder / 'original.txt').read_text() == 'original'
    assert not list(Path(config.UPLOAD_FOLDER).glob('.restore-*'))


def test_exception_after_commit_never_rolls_back_only_the_files(client, monkeypatch):
    _login(client, 'committed')
    folder = Path(account_storage_dir(config.UPLOAD_FOLDER, 'committed'))
    folder.mkdir(parents=True)
    (folder / 'original.txt').write_text('old')
    original_connection = restores.db_conn

    class InterruptedCommit:
        def __init__(self, connection):
            self.connection = connection

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def commit(self):
            self.connection.commit()
            raise OSError('commit completed before interruption')

    @contextmanager
    def connection(**kwargs):
        with original_connection(**kwargs) as conn:
            yield InterruptedCommit(conn)

    monkeypatch.setattr(restores, 'db_conn', connection)
    stream = archive([{'id': 77, 'type': 'memo', 'title': 'new record'}], {'uploads/new.txt': 'new'})
    result = client.post('/api/restore', data={'file': (stream, 'new.zip')})
    assert result.status_code == 500
    assert [p.name for p in folder.iterdir()] == ['new.txt']
    assert client.get('/api/data').json[0]['title'] == 'new record'
    restore_recovery.recover_pending_restores()
    assert (folder / 'new.txt').read_text() == 'new'


def response(symbol='1234.HK', currency='HKD', exchange='HKG', price=100):
    return json.dumps({'chart': {'result': [{'meta': {
        'symbol': symbol, 'currency': currency, 'exchangeName': exchange, 'regularMarketPrice': price,
    }}]}}).encode()


def test_quotes_distinguish_numeric_symbols_and_cache_by_market(app, monkeypatch):
    calls = []

    def provider(url, _headers):
        calls.append(url)
        if url.endswith('1234.HK'):
            return response(price=100)
        assert url.endswith('1234.T')
        return response('1234.T', 'JPY', 'JPX', 900)

    monkeypatch.setattr(prices, '_http_get', provider)
    assets = [{'code': '1234', 'currency': 'HKD'}, {'code': '1234', 'currency': 'JPY'}]
    result = quotes.get_quotes(assets)
    assert [q['price'] for q in result] == [100, 900]
    assert all(q['status'] == 'ok' and q['asOf'] for q in result)
    assert len({q['key'] for q in result}) == 2
    assert quotes.get_quotes(assets, allow_cached=True) == result
    assert len(calls) == 2


@pytest.mark.parametrize('overrides', [{'currency': 'JPY'}, {'exchange': 'JPX'}, {'symbol': '5678.HK'},
                                     {'price': -1}, {'price': float('nan')}, {'price': True}])
def test_quotes_reject_wrong_identity_and_invalid_price(app, monkeypatch, overrides):
    monkeypatch.setattr(prices, '_http_get', lambda *_: response(**overrides))
    result = quotes.get_quotes([{'code': '1234', 'currency': 'HKD', 'exchange': 'HKEX'}])[0]
    assert result['price'] is None and result['status'] == 'unavailable'


def test_quote_failure_keeps_last_verified_price_and_time(app, monkeypatch):
    asset = {'code': '1234', 'currency': 'HKD'}
    monkeypatch.setattr(prices, '_http_get', lambda *_: response())
    first = quotes.get_quotes([asset])[0]
    monkeypatch.setattr(prices, '_http_get', lambda *_: b'not json')
    second = quotes.get_quotes([asset])[0]
    assert second['price'] == first['price']
    assert second['asOf'] == first['asOf']
    assert second['status'] == 'stale' and second['stale']


@pytest.mark.parametrize('exchange', ['', 'KRX', 'NXT', 'KOSDAQ'])
def test_domestic_execution_exchange_uses_selected_price_session(app, monkeypatch, exchange):
    calls = []

    def domestic(code, mode, allow_cached=False):
        calls.append((code, mode))
        return code, 12345

    monkeypatch.setattr(prices, 'fetch_price', domestic)
    result = quotes.get_quotes([{'code': '005930', 'currency': 'KRW', 'exchange': exchange}], 'AFT')[0]
    assert result['price'] == 12345 and result['market'] == 'AFT'
    assert calls == [('005930', 'AFT')]


def test_us_share_class_matches_historical_symbol_normalization(app, monkeypatch):
    calls = []

    def provider(url, _headers):
        calls.append(url)
        return response('BRK-B', 'USD', 'NYQ', 500)

    monkeypatch.setattr(prices, '_http_get', provider)
    result = quotes.get_quotes([{'code': 'BRK.B', 'currency': 'USD', 'exchange': 'NYSE'}])[0]
    assert result['price'] == 500 and result['code'] == 'BRK.B'
    assert calls[0].endswith('BRK-B')


def test_current_price_accepts_assets_and_preserves_legacy_codes(client, monkeypatch):
    _login(client)
    monkeypatch.setattr(prices, '_http_get', lambda *_: response())
    result = client.post('/api/current_price', json={'assets': [{'code': '1234', 'currency': 'HKD'}]})
    assert result.status_code == 200
    assert result.json['quotes'][0]['price'] == 100
    monkeypatch.setattr(prices, 'get_prices', lambda *_args, **_kw: ({'005930': 70000}, False))
    assert client.post('/api/current_price', json={'codes': ['005930']}).json == {'005930': 70000}


@pytest.mark.parametrize('assets', [None, {}, [None], [{'code': '../foo'}], [{'code': 'X', 'currency': []}],
                                    [{'code': 'X', 'currency': 'invalid'}]])
def test_current_price_rejects_invalid_asset_contract(client, assets):
    _login(client)
    assert client.post('/api/current_price', json={'assets': assets}).status_code == 400
