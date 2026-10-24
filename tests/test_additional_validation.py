"""업그레이드·거래소 날짜 경계·인증 대기 중 변경을 실제 저장 경로로 검증한다."""

import pytest

from app.database import entry_logic, schema
from app.database.db import db_conn
from app.services import entries, journal, realized_pnl
from helpers import _buy, _sell, _login, _ensure_user
from test_integrity_workflows import archive, record, upload
from test_trading_api import api as api, _trade
from test_valuation import cache, evaluate, values


@pytest.mark.parametrize('operation', ['edit', 'restore'])
def test_first_update_of_migrated_entry_invalidates_old_revision(client, operation):
    _login(client)
    with db_conn() as conn:
        # 이전 버전의 기록에는 이력이 없으며 새 revision 컬럼은 기본값 1이다.
        conn.execute('DROP TRIGGER entries_audit_insert')
        conn.execute('DROP TRIGGER entries_audit_update')
        conn.execute('''CREATE TRIGGER entries_audit_update AFTER UPDATE ON entries
            WHEN OLD.title IS NOT NEW.title BEGIN
                INSERT INTO entry_changes(username,entry_id,action,origin)
                VALUES (NEW.username,NEW.id,'update','system');
                UPDATE entries SET revision=last_insert_rowid() WHERE id=NEW.id;
            END''')
        entry_logic.insert_entry(conn.cursor(), 'trader', record(1))
        conn.commit()
        schema.init(conn)
        assert conn.execute('SELECT COUNT(*) FROM entry_changes').fetchone()[0] == 0
    old = client.get('/api/entry/1').json
    data = archive([record(1)])
    token = upload(client, '/api/restore/preview', data).json['previewToken']
    with db_conn(write=True) as conn:
        conn.execute("UPDATE entries SET title='새 내용' WHERE id=1")
        conn.commit()
    if operation == 'edit':
        response = client.put('/api/entry/1', json=dict(old, title='오래된 편집창'))
    else:
        response = upload(client, '/api/restore', data, previewToken=token)
    assert response.status_code == 409
    assert client.get('/api/entry/1').json['title'] == '새 내용'


@pytest.mark.parametrize('quantity', [5, 15])
def test_us_afterhours_split_uses_exchange_trade_day_in_every_calculation(client, quantity):
    _login(client)
    common = {'stock': 'Apple', 'stockCode': 'AAPL', 'currency': 'USD', 'exchange': 'NASDAQ'}
    cache({'2026-01-05': 100, '2026-01-06': 60}, code='AAPL', currency='USD', splits={'2026-01-06': 2})
    entries.create('trader', _buy(id=1, qty=10, price=100, rawDate='2026-01-06T09:00:00+09:00',
                                  tradeDate='2026-01-05', **common))
    sale = _sell(id=2, qty=quantity, price=60, rawDate='2026-01-06T23:30:00+09:00',
                 tradeDate='2026-01-06', **common)
    response = client.post('/api/entry', json=sale)
    assert response.status_code == 200, response.json
    assert client.get('/api/entry/2').json['realizedPnl'] == quantity * 10
    assert journal.snapshot('trader')['rows'][0]['quantity'] == 20
    with db_conn() as conn:
        assert entry_logic.net_holding_for_stock(conn.cursor(), 'trader', 'Apple', stock_code='AAPL') == 20 - quantity
    assert values(evaluate(client, '2026-01-05', '2026-01-06', currency='USD')) == [0, (20 - quantity) * 10]


@pytest.mark.parametrize('path,expected', [('/', 302), ('/api/journal/meta', 401)])
def test_legacy_session_goes_to_login_for_pages_and_returns_json_for_api(client, path, expected):
    _login(client)
    with client.session_transaction() as session:
        session.pop('user_id')
    response = client.get(path)
    assert response.status_code == expected
    if expected == 302:
        assert '/login' in response.location
    else:
        assert response.is_json


@pytest.mark.parametrize('change', ['revoke', 'recreate', 'disable'])
def test_bot_rechecks_auth_changes_while_waiting_for_user_lock(api, monkeypatch, change):
    from app.routes import ownership
    from app.services.users import purge_account
    original = ownership.lock_request
    def race(username):
        with db_conn() as conn:
            if change == 'revoke':
                conn.execute("UPDATE api_keys SET revoked_at='2026-10-25' WHERE id=?", (api['key_id'],))
            elif change == 'disable':
                conn.execute('UPDATE users SET is_allowed=0 WHERE username=?', (username,))
            else:
                purge_account(conn.cursor(), username)
            conn.commit()
        if change == 'recreate':
            _ensure_user(username)
        original(username)
    monkeypatch.setattr(ownership, 'lock_request', race)
    response = api['client'].post('/api/v1/trades', json=_trade(), headers=api['headers'])
    assert response.status_code == (403 if change == 'disable' else 401)
    assert response.is_json and response.json['errorCode']
    with db_conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM entries').fetchone()[0] == 0


def test_startup_repairs_existing_exchange_day_profit_once_and_preserves_recorded_profit(client):
    _login(client)
    common = {'stock': 'Apple', 'stockCode': 'AAPL', 'currency': 'USD', 'exchange': 'NASDAQ'}
    cache({}, code='AAPL', currency='USD', splits={'2026-01-06': 2})
    entries.create('trader', _buy(id=1, qty=10, price=100, rawDate='2026-01-06T09:00+09:00',
                                  tradeDate='2026-01-05', **common))
    entries.create('trader', _sell(id=2, qty=5, price=60, rawDate='2026-01-06T23:30+09:00',
                                   tradeDate='2026-01-06', **common))
    entries.create('trader', _sell(id=3, qty=5, price=60, realizedPnl=7,
                                   rawDate='2026-01-07T00:00+09:00', tradeDate='2026-01-06', **common))
    with db_conn(write=True) as conn:
        conn.execute("UPDATE entries SET realizedPnl=-200,realizedPnlSource='reconstructed' WHERE id=2")
        conn.execute("INSERT OR IGNORE INTO data_migrations(name) VALUES ('shared_splits_sequence_v3')")
        conn.commit()
    assert realized_pnl.backfill() == 1
    assert client.get('/api/entry/2').json['realizedPnl'] == 50
    assert client.get('/api/entry/3').json['realizedPnl'] == 7
    assert client.get('/api/entry/1').json['quantity'] == 10
    assert realized_pnl.backfill() == 0


def test_failed_restore_rolls_back_entries_revisions_settings_history_and_files(client, monkeypatch):
    from pathlib import Path
    import config
    from app.services import restores
    _login(client)
    cache({}, splits={'2026-01-06': 2})
    entries.create('trader', record(1))
    assert client.post('/api/mappings', json={'accounts': {'old': {'alias': '기존'}}}).status_code == 200
    assert client.put('/api/journal/split-rule', json={
        'code': '005930', 'currency': 'KRW', 'day': '2026-01-06', 'mode': 'recorded'}).status_code == 200
    folder = Path(config.UPLOAD_FOLDER) / 'trader'
    folder.mkdir(exist_ok=True)
    (folder / 'original.txt').write_text('원래 첨부', encoding='utf-8')
    def snapshot():
        with db_conn() as conn:
            tables = {table: [tuple(r) for r in conn.execute(f'SELECT * FROM {table} WHERE username=?', ('trader',))]
                      for table in ('entries', 'entry_changes', 'split_rules')}
            tables['mappings'] = conn.execute("SELECT account_mappings FROM users WHERE username='trader'").fetchone()[0]
        return tables
    before = snapshot()
    original = restores.os.rename
    def fail_staging(source, target):
        if Path(source).name == 'staging':
            raise OSError('첨부 폴더 교체 실패 재현')
        return original(source, target)
    monkeypatch.setattr(restores.os, 'rename', fail_staging)
    data = archive([record(2)])
    token = upload(client, '/api/restore/preview', data).json['previewToken']
    assert upload(client, '/api/restore', data, previewToken=token).status_code == 500
    assert snapshot() == before
    assert (folder / 'original.txt').read_text(encoding='utf-8') == '원래 첨부'
    assert not list(Path(config.UPLOAD_FOLDER).glob('.restore-*'))


def test_rollback_does_not_consume_edit_revision_or_leave_audit_history(client):
    _login(client)
    entries.create('trader', record(1))
    old = client.get('/api/entry/1').json
    with db_conn() as conn:
        history_before = [tuple(r) for r in conn.execute('SELECT * FROM entry_changes')]
    with pytest.raises(RuntimeError), db_conn(write=True) as conn:
        conn.execute("UPDATE entries SET title='롤백될 수정' WHERE id=1")
        raise RuntimeError('트랜잭션 중단')
    assert client.get('/api/entry/1').json == old
    with db_conn() as conn:
        assert [tuple(r) for r in conn.execute('SELECT * FROM entry_changes')] == history_before
    assert client.put('/api/entry/1', json=dict(old, title='정상 수정')).status_code == 200
