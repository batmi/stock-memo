"""전체 코드 감사(2026-09-24) 후속 — 기능 보완의 회귀 방지 테스트.

감사 뒤 기능 점검에서 재현된 것들이다.
  - 봇이 붙인 '검토 필요'를 화면에서 내릴 수단이 없었다 (검토 완료 API)
  - 매수를 줄이거나 지워 이미 한 매도가 보유를 넘어도 막지 않았다 (경고 후 진행)
  - 없는 기록을 고쳐도 성공, 다른 사용자와 id 가 겹치면 500
  - 자동 백업 검증이 실패해도 옛 백업을 지웠고, 계정이 담긴 DB 는 백업되지 않았다
  - 로그인 IP 실패 횟수가 시간이 지나도 줄지 않았다
화면 쪽(배지·확인 창)은 test_audit_frontend.py 가 본다.
"""
import os
import sqlite3
import time

import pytest

import backend_app
import config
from app.services import jobs
from app.utils import ratelimit
from helpers import _buy, _login, _sell


def _row(entry_id):
    with backend_app.db_conn() as conn:
        row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        return dict(row) if row else None


@pytest.fixture
def trader(app):
    client = app.test_client()
    _login(client, 'trader')
    return client


def _buy_then_sell(client, buy_qty=10, sell_qty=10):
    assert client.post('/api/entry', json=_buy(qty=buy_qty, id=1, rawDate='2026-01-01T09:00')).status_code == 200
    assert client.post('/api/entry', json=_sell(qty=sell_qty, id=2, rawDate='2026-01-02T09:00')).status_code == 200


# ---------------------------------------------------------------------------
# 매수 수정·삭제로 생기는 초과 매도 — 경고 후 진행
# ---------------------------------------------------------------------------

def test_shrinking_buy_below_sold_quantity_asks_for_confirmation(trader):
    _buy_then_sell(trader)
    res = trader.put('/api/entry/1', json=_buy(qty=1, id=1))
    assert res.status_code == 409
    body = res.get_json()
    assert body['requiresConfirm'] is True and '9주' in body['error']
    assert _row(1)['quantity'] == 10          # 확인 전에는 아무것도 바뀌지 않는다
    assert not _row(2)['needsReview']


def test_confirmed_shrink_saves_and_flags_the_sell(trader):
    _buy_then_sell(trader)
    res = trader.put('/api/entry/1?force=1', json=_buy(qty=1, id=1))
    assert res.status_code == 200
    assert res.get_json()['flagged'] == [2]
    assert _row(1)['quantity'] == 1
    sell = _row(2)
    assert sell['needsReview'] == 1 and '초과' in sell['reviewReason']


def test_deleting_the_buy_asks_then_flags(trader):
    _buy_then_sell(trader)
    assert trader.delete('/api/entry/1').status_code == 409
    assert _row(1) is not None
    res = trader.delete('/api/entry/1?force=1')
    assert res.status_code == 200 and res.get_json()['flagged'] == [2]
    assert _row(1) is None and _row(2)['needsReview'] == 1


def test_flags_only_the_most_recent_sells_needed_to_cover(trader):
    trader.post('/api/entry', json=_buy(qty=10, id=1, rawDate='2026-01-01T09:00'))
    trader.post('/api/entry', json=_sell(qty=4, id=2, rawDate='2026-01-02T09:00'))
    trader.post('/api/entry', json=_sell(qty=4, id=3, rawDate='2026-01-03T09:00'))
    # 매수 10 → 5 : 매도 8 이 보유 5 를 3 넘는다 → 가장 최근 매도(3) 하나로 충분하다
    res = trader.put('/api/entry/1?force=1', json=_buy(qty=5, id=1))
    assert res.get_json()['flagged'] == [3]
    assert not _row(2)['needsReview']


def test_buy_edits_that_keep_holdings_covered_do_not_warn(trader):
    _buy_then_sell(trader, buy_qty=10, sell_qty=5)
    assert trader.put('/api/entry/1', json=_buy(qty=5, id=1)).status_code == 200   # 딱 맞음
    assert trader.put('/api/entry/1', json=_buy(qty=20, id=1)).status_code == 200  # 늘림


def test_already_negative_stock_does_not_warn_on_unrelated_edit(trader):
    """과거 데이터로 이미 음수인 종목은, 이번 변경이 더 나쁘게 만들 때만 알린다."""
    from helpers import _insert_raw
    _insert_raw('trader', id=1, tradeType='매수', stockName='삼성전자', stockCode='005930', quantity=1)
    _insert_raw('trader', id=2, tradeType='매도', stockName='삼성전자', stockCode='005930', quantity=5)
    res = trader.put('/api/entry/1', json=_buy(qty=1, id=1, thoughts='<p>메모만 수정</p>'))
    assert res.status_code == 200


# ---------------------------------------------------------------------------
# 검토 완료
# ---------------------------------------------------------------------------

def test_review_can_be_resolved(trader):
    _buy_then_sell(trader)
    trader.put('/api/entry/1?force=1', json=_buy(qty=1, id=1))
    assert trader.post('/api/entry/2/review').status_code == 200
    sell = _row(2)
    assert sell['needsReview'] == 0 and sell['reviewReason'] is None


def test_review_cannot_touch_other_users_entries(app, trader):
    _buy_then_sell(trader)
    other = app.test_client()
    _login(other, 'mallory')
    assert other.post('/api/entry/2/review').status_code == 404


def test_web_edit_keeps_review_flag_until_resolved(trader):
    """수정만으로는 풀리지 않는다 — 고친 뒤에도 맞는지는 사람이 판단한다."""
    _buy_then_sell(trader)
    trader.put('/api/entry/1?force=1', json=_buy(qty=1, id=1))
    trader.put('/api/entry/2', json=_sell(qty=1, id=2))
    assert _row(2)['needsReview'] == 1


def test_bot_oversell_stores_the_reason(app):
    import trading_api
    from helpers import _ensure_user
    _ensure_user('botuser')
    raw = trading_api.create_api_key('botuser')['api_key']
    c = app.test_client()
    token = c.post('/api/v1/auth/token', headers={'X-API-KEY': raw}).get_json()['access_token']
    res = c.post('/api/v1/trades', headers={'Authorization': f'Bearer {token}'}, json={
        'symbol': '005930', 'side': 'SELL', 'price': 1, 'volume': 3,
        'executedAt': '2026-09-01T10:00:00+09:00', 'brokerExecutionId': 'ov-1'})
    entry_id = int(res.get_json()['id'])
    row = _row(entry_id)
    assert row['needsReview'] == 1 and row['reviewReason']


# ---------------------------------------------------------------------------
# 없는 기록 수정 / id 충돌
# ---------------------------------------------------------------------------

def test_updating_a_missing_entry_is_404(trader):
    res = trader.put('/api/entry/999999', json=_buy(id=999999))
    assert res.status_code == 404
    assert _row(999999) is None


def test_colliding_client_id_gets_a_new_id(app):
    alice, bob = app.test_client(), app.test_client()
    _login(alice, 'alice')
    _login(bob, 'bob')
    alice.post('/api/entry', json=_buy(id=1700000000000))
    res = bob.post('/api/entry', json=_buy(id=1700000000000))
    assert res.status_code == 200
    new_id = res.get_json()['id']
    assert new_id != 1700000000000 and _row(new_id)['username'] == 'bob'
    assert _row(1700000000000)['username'] == 'alice'


def test_create_returns_the_client_id_when_free(trader):
    res = trader.post('/api/entry', json=_buy(id=424242))
    assert res.get_json()['id'] == 424242


# ---------------------------------------------------------------------------
# 자동 백업 — 검증 실패 시 보존, DB 스냅샷
# ---------------------------------------------------------------------------

def _old_file(folder, name, days=10):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    with open(path, 'w') as f:
        f.write('old')
    old = time.time() - days * 86400
    os.utime(path, (old, old))
    return path


def test_failed_verification_keeps_old_backups(app, monkeypatch):
    from helpers import _ensure_user
    _ensure_user('keeper')
    old = _old_file(os.path.join(config.BACKUP_DIR, 'keeper'), 'TradingJournal_backup_keeper_old.zip')
    monkeypatch.setattr(jobs, 'verify_backup_zip', lambda *_a: (False, 'broken'))
    with pytest.raises(RuntimeError):
        jobs._backup_user('keeper')
    assert os.path.exists(old)


def test_successful_backup_prunes_old_files(app):
    from helpers import _ensure_user
    _ensure_user('keeper')
    old = _old_file(os.path.join(config.BACKUP_DIR, 'keeper'), 'TradingJournal_backup_keeper_old.zip')
    jobs._backup_user('keeper')
    assert not os.path.exists(old)
    assert not [f for f in os.listdir(os.path.join(config.BACKUP_DIR, 'keeper')) if f.endswith('.tmp')]


def test_database_snapshot_contains_accounts_and_prunes_old(app):
    from helpers import _ensure_user
    _ensure_user('snapuser')
    snap_dir = os.path.join(config.BACKUP_DIR, jobs.DB_SNAPSHOT_DIRNAME)
    old = _old_file(snap_dir, 'journal_20000101.db')

    path = jobs.snapshot_database()

    conn = sqlite3.connect(path)
    try:
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == 'ok'
        names = [r[0] for r in conn.execute("SELECT username FROM users")]
    finally:
        conn.close()
    assert 'snapuser' in names
    assert not os.path.exists(old)


def test_snapshot_folder_cannot_collide_with_a_username():
    from app.services.users import is_valid_username
    assert not is_valid_username(jobs.DB_SNAPSHOT_DIRNAME)


# ---------------------------------------------------------------------------
# 로그인 IP 실패 횟수의 감쇠
# ---------------------------------------------------------------------------

def test_login_ip_failures_expire_after_quiet_period():
    ratelimit.reset_all()
    now = time.time()
    for i in range(ratelimit.LOGIN_IP_THRESHOLD - 1):
        ratelimit.login_ips.record_failure('1.2.3.4', now=now + i)
    later = now + ratelimit.LOGIN_IP_FAILURE_WINDOW + 60
    assert ratelimit.login_ips.record_failure('1.2.3.4', now=later) == 1
    assert ratelimit.login_ip_lockout_remaining('1.2.3.4', later) == 0
