"""응답 유실·동시 편집·새 서버 복원·예약 누락의 회귀 검사."""

import io
import json
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import config
from app.database.db import db_conn
from app.services import entries, integrity, jobs, ledger, users
from helpers import _buy, _sell, _login, _ensure_user


def test_create_and_update_retries_return_original_receipt(client):
    _login(client)
    key = {'Idempotency-Key': 'a' * 48}
    payload = {'type': 'memo', 'thoughts': '<p>처음</p>'}
    first = client.post('/api/entry', json=payload, headers=key)
    assert first.status_code == 200
    assert client.post('/api/entry', json=payload, headers=key).json == first.json
    assert len(client.get('/api/data').json) == 1
    changed = client.post('/api/entry', json={**payload, 'thoughts': '변경'}, headers=key)
    assert changed.status_code == 409 and changed.json['errorCode'] == 'REQUEST_CONFLICT'
    entry_id = first.json['id']
    payload = {'thoughts': '<p>수정</p>', 'revision': first.json['revision']}
    key = {'Idempotency-Key': 'b' * 48}
    updated = client.put(f'/api/entry/{entry_id}', json=payload, headers=key)
    assert updated.status_code == 200
    assert client.put(f'/api/entry/{entry_id}', json=payload, headers=key).json == updated.json
    assert client.get(f'/api/entry/{entry_id}').json['revision'] == updated.json['revision']
    with db_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM entry_changes WHERE action='update'").fetchone()[0] == 1


def test_receipts_are_account_scoped_and_purged_before_reregistration(client):
    key = {'Idempotency-Key': 'a' * 48}
    for name in ('one', 'two'):
        _login(client, name)
        assert client.post('/api/entry', json={'type': 'memo', 'thoughts': name}, headers=key).status_code == 200
    with db_conn(write=True) as conn:
        assert conn.execute('SELECT COUNT(*) FROM entry_requests').fetchone()[0] == 2
        users.purge_account(conn.cursor(), 'one')
        conn.commit()
        assert conn.execute('SELECT username FROM entry_requests').fetchone()[0] == 'two'


def test_stale_delete_rejects_even_with_force(client):
    _login(client)
    result = client.post('/api/entry', json={'type': 'memo', 'thoughts': '초기'}).json
    entry_id = result['id']
    assert client.put(f'/api/entry/{entry_id}', json={'thoughts': '새 내용', 'revision': result['revision']}).status_code == 200
    response = client.delete(f'/api/entry/{entry_id}?force=1', json={'revision': result['revision']})
    assert response.status_code == 409 and response.json['errorCode'] == 'STALE_ENTRY'
    row = client.get(f'/api/entry/{entry_id}').json
    assert row['thoughts'] == '새 내용'
    assert client.delete(f'/api/entry/{entry_id}', json={'revision': row['revision']}).status_code == 200


def test_web_cost_edit_distinguishes_zero_and_unknown_and_preserves_recorded_profit(client):
    _login(client)
    entries.create('trader', _buy(id=1, rawDate='2026-01-01T10:00'))
    entries.create('trader', _sell(id=2, rawDate='2026-01-02T10:00', realizedPnl=999, realizedPnlBasis='unknown'))
    response = client.put('/api/entry/2', json={'fee': 0, 'tax': 25, 'realizedPnlBasis': 'net'})
    assert response.status_code == 200
    row = client.get('/api/entry/2').json
    assert (row['fee'], row['tax'], row['realizedPnl'], row['realizedPnlBasis']) == (0, 25, 999, 'net')
    assert client.put('/api/entry/2', json={'fee': -1}).status_code == 400
    assert client.put('/api/entry/2', json={'fee': None}).status_code == 200
    assert client.get('/api/entry/2').json['fee'] is None


def split_backup(client):
    _login(client)
    entries.create('trader', _buy(id=1, qty=10, price=100, rawDate='2026-01-01T10:00'))
    with db_conn(write=True) as conn:
        conn.execute("INSERT INTO historical_assets(code,currency,symbol,timezone,splits,checked_at) VALUES(?,?,?,?,?,1)",
                     ('005930', 'KRW', '005930.KS', 'Asia/Seoul', '{"2026-02-01":2}'))
        conn.commit()
    entries.create('trader', _sell(id=2, qty=5, price=60, rawDate='2026-02-02T10:00'))
    return client.get('/api/backup').data


def upload(client, path, payload, **fields):
    return client.post(path, data={'file': (io.BytesIO(payload), 'backup.zip'), **fields})


def test_portable_backup_restores_splits_without_shared_cache_and_leaves_other_accounts_alone(client):
    payload = split_backup(client)
    _ensure_user('other')
    entries.create('other', _buy(id=3, qty=10, price=100, rawDate='2026-01-01T10:00'))
    with db_conn(write=True) as conn:
        conn.execute('DELETE FROM historical_assets')
        conn.commit()
    preview = upload(client, '/api/restore/preview', payload)
    assert preview.status_code == 200 and preview.json['warnings'] == []
    assert preview.json['holdings'][0]['afterQuantity'] == 15
    response = upload(client, '/api/restore', payload, previewToken=preview.json['previewToken'])
    assert response.status_code == 200
    assert client.get('/api/entry/2').json['realizedPnl'] == 50
    with db_conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM entries WHERE username='trader'")]
        assert next(iter(integrity.positions(ledger.prepare(conn, 'trader', rows)).values()))['qty'] == 15
        other = [dict(r) for r in conn.execute("SELECT * FROM entries WHERE username='other'")]
        assert ledger.prepare(conn, 'other', other)[0]['quantity'] == 10
        assert conn.execute('SELECT COUNT(*) FROM historical_assets').fetchone()[0] == 0
    inspection = client.get('/api/journal/integrity').json
    assert inspection['splits'][0]['ratio'] == 2
    assert client.post('/api/journal/split-rule/preview', json={**inspection['splits'][0], 'mode': 'recorded'}).status_code == 200


@pytest.mark.parametrize('change', ['missing', 'negative', 'boolean', 'compact_date'])
def test_bad_split_archive_rejected_before_mutation(client, change):
    payload = split_backup(client)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    if change == 'missing':
        del files['corporate_actions.json']
    else:
        actions = json.loads(files['corporate_actions.json'])
        actions['assets'][0]['splits'] = {'20260201' if change == 'compact_date' else '2026-02-01':
                                         True if change == 'boolean' else -1 if change == 'negative' else 2}
        files['corporate_actions.json'] = json.dumps(actions)
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    assert upload(client, '/api/restore', output.getvalue()).status_code == 400
    assert client.get('/api/entry/2').json['realizedPnl'] == 50


def test_snapshot_allows_new_future_splits_but_freezes_old_events(client):
    payload = split_backup(client)
    assert upload(client, '/api/restore', payload).status_code == 200
    with db_conn(write=True) as conn:
        cutoff = conn.execute('SELECT as_of FROM user_split_snapshots').fetchone()[0]
        future = (datetime.fromisoformat(cutoff) + timedelta(days=1)).date().isoformat()
        conn.execute('UPDATE historical_assets SET splits=?', (json.dumps({'2026-02-01': 3, future: 4}),))
        conn.commit()
        assert ledger.split_events(conn, 'trader', {('005930', 'KRW')})[('005930', 'KRW')] == {'2026-02-01': 2, future: 4}


def test_backup_catches_missed_schedule_once_and_persists_status(client, monkeypatch):
    _login(client)
    calls = []
    original = jobs.run_backup
    def run():
        calls.append('run')
        return original()
    monkeypatch.setattr(jobs, 'run_backup', run)
    sleeps = []
    def sleep(delay):
        sleeps.append(delay)
        if len(sleeps) == 2:
            raise KeyboardInterrupt()
    monkeypatch.setattr(jobs.time, 'sleep', sleep)
    with pytest.raises(KeyboardInterrupt):
        jobs.auto_backup_job()
    status = jobs.backup_status()
    assert calls == ['run'] and status['state'] == 'success' and not status['due']
    assert status['last_success'] and status['database_ok'] == 1
    assert list(Path(config.BACKUP_DIR, '_db').glob('*.db'))


def test_failed_backup_keeps_previous_valid_zip_and_last_success(client, monkeypatch):
    _login(client)
    jobs.run_backup()
    previous = jobs.backup_status()['last_success']
    path = next(Path(users.user_dir(config.BACKUP_DIR, 'trader')).glob('*.zip'))
    valid = path.read_bytes()
    monkeypatch.setattr(jobs, 'verify_backup_zip', lambda *_: (False, 'invalid'))
    jobs.run_backup()
    status = jobs.backup_status()
    assert status['state'] == 'failed' and status['failed_users'] == 1
    assert status['last_success'] == previous and path.read_bytes() == valid
    assert not jobs._backup_lock.locked()


def test_backup_manual_api_is_admin_only_and_does_not_duplicate_running_job(client, monkeypatch):
    _login(client)
    assert client.get('/api/admin/backup').status_code == 403
    assert client.post('/api/admin/backup').status_code == 403
    with db_conn(write=True) as conn:
        conn.execute("UPDATE users SET is_admin=1 WHERE username='trader'")
        conn.commit()
    with client.session_transaction() as session:
        session['is_admin'] = True
    monkeypatch.setattr(jobs, 'run_backup', lambda background: False)
    assert client.post('/api/admin/backup').status_code == 409
    monkeypatch.setattr(jobs, 'run_backup', lambda background: background)
    assert client.post('/api/admin/backup').status_code == 202
    assert client.get('/api/admin/backup').json['state'] == 'never'


def test_csrf_refresh_keeps_account_guard(client):
    _login(client)
    response = client.get('/api/session/csrf')
    assert response.status_code == 200 and response.headers['Cache-Control'] == 'no-store'
    with client.session_transaction() as session:
        assert response.json['csrf_token'] == session['csrf_token']
    assert client.get('/api/session/csrf', headers={'X-Journal-User-ID': '99999'}).status_code == 409


def test_concurrent_retries_commit_one_record_and_receipt(client):
    from concurrent.futures import ThreadPoolExecutor
    _login(client)
    payload = {'type': 'memo', 'thoughts': '한 번만 기록'}
    def save(_):
        return entries.create('trader', payload, request_id='concurrent-request-1234', receipt=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, range(2)))
    assert results[0] == results[1]
    with db_conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM entries').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM entry_requests').fetchone()[0] == 1


def test_receipt_failure_rolls_back_record_and_leaves_retry_available(client, monkeypatch):
    from app.services import write_receipts
    _login(client)
    original = write_receipts.remember
    def fail(*_):
        raise RuntimeError('receipt storage failed')
    monkeypatch.setattr(write_receipts, 'remember', fail)
    with pytest.raises(RuntimeError, match='receipt storage failed'):
        entries.create('trader', {'type': 'memo'}, request_id='rollback-request-1234')
    assert client.get('/api/data').json == []
    monkeypatch.setattr(write_receipts, 'remember', original)
    entries.create('trader', {'type': 'memo'}, request_id='rollback-request-1234')
    assert len(client.get('/api/data').json) == 1
