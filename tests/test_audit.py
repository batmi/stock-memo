"""전체 코드 감사(2026-09-24)에서 찾은 결함의 회귀 방지 테스트.

각 테스트는 감사 때 **실제로 재현된** 결함(AUDIT-xx)을 바람직한 동작으로 단언한다.
처음에는 xfail(strict=True) 로 두고 결함을 확인했고, 수정과 함께 표시를 걷어냈다.
화면 XSS(AUDIT-11·12)는 브라우저가 필요해 test_audit_frontend.py 에 있다.
"""
import io
import json
import os
import time
import zipfile

import pytest

import backend_app
import config
from app.services import images, jobs
from app.utils import statscache
from helpers import _ensure_user, _login


def _make_admin(client, username='boss'):
    with backend_app.db_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (username, password_hash, is_allowed, is_admin) "
            "VALUES (?, 'x', 1, 1)", (username,))
        conn.commit()
    _login(client, username)
    with client.session_transaction() as sess:
        sess['is_admin'] = True


def _entry_count(username):
    with backend_app.db_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM entries WHERE username = ?",
                            (username,)).fetchone()[0]


# ---------------------------------------------------------------------------
# 1. 삭제·차단된 계정의 기존 세션이 계속 살아 있다
# ---------------------------------------------------------------------------

# AUDIT-01
def test_deleted_user_session_is_rejected(app):
    victim = app.test_client()
    _ensure_user('ghost')
    _login(victim, 'ghost')

    admin = app.test_client()
    _make_admin(admin)
    assert admin.delete('/api/admin/users/ghost').status_code == 200

    # 계정이 사라진 뒤의 옛 쿠키로 기록을 쓰면 고아 행이 생긴다 — 막혀야 한다.
    res = victim.post('/api/entry', json={'id': 1, 'type': 'memo', 'title': 't', 'thoughts': 'x'})
    assert res.status_code == 401
    assert _entry_count('ghost') == 0


# AUDIT-02
def test_disallowed_user_session_is_rejected(app):
    victim = app.test_client()
    _ensure_user('blocked')
    _login(victim, 'blocked')

    admin = app.test_client()
    _make_admin(admin)
    res = admin.post('/api/admin/users/blocked/toggle_allow')
    assert res.get_json()['is_allowed'] == 0

    assert victim.get('/api/data').status_code == 401


# AUDIT-03
def test_disallowed_user_cannot_get_bot_token(app):
    import trading_api
    _ensure_user('blocked')
    raw = trading_api.create_api_key('blocked')['api_key']

    admin = app.test_client()
    _make_admin(admin)
    admin.post('/api/admin/users/blocked/toggle_allow')

    res = app.test_client().post('/api/v1/auth/token', headers={'X-API-KEY': raw})
    assert res.status_code in (401, 403)


# ---------------------------------------------------------------------------
# 2. 관리자 계정 삭제가 사용자명을 검증하지 않고 rmtree 한다
# ---------------------------------------------------------------------------

# AUDIT-04
def test_admin_delete_does_not_escape_user_folder(app):
    other = os.path.join(config.UPLOAD_FOLDER, 'alice')
    os.makedirs(other, exist_ok=True)
    with open(os.path.join(other, 'keep.png'), 'wb') as f:
        f.write(b'x')

    admin = app.test_client()
    _make_admin(admin)
    admin.delete('/api/admin/users/.')

    assert os.path.exists(os.path.join(other, 'keep.png'))


# ---------------------------------------------------------------------------
# 3. 봇 토큰 발급 레이트리밋이 X-Forwarded-For 로 우회된다
# ---------------------------------------------------------------------------

# AUDIT-05
def test_token_rate_limit_ignores_spoofed_forwarded_for(app):
    client = app.test_client()
    limit = __import__('app.utils.ratelimit', fromlist=['x']).TOKEN_RATE_LIMIT[0]
    codes = [client.post('/api/v1/auth/token', headers={
                 'X-API-KEY': 'wrong', 'X-Forwarded-For': f'10.0.0.{i}'}).status_code
             for i in range(limit + 1)]
    assert codes[-1] == 429


# ---------------------------------------------------------------------------
# 4. 통계 캐시 키가 클라이언트 입력(granularity)으로 무한히 늘어난다
# ---------------------------------------------------------------------------

# AUDIT-06
def test_stats_cache_is_bounded_by_granularity(app):
    client = app.test_client()
    _ensure_user('trader')
    _login(client, 'trader')
    for i in range(50):
        client.post('/api/stats', json={'granularity': f'junk-{i}'})
    keys = [k for k in statscache._stats_cache if k[0] == 'trader']
    assert len(keys) <= 2  # monthly / weekly


# ---------------------------------------------------------------------------
# 5. 자동 백업: 한 사용자에서 예외가 나면 나머지 사용자 백업이 모두 건너뛰어진다
# ---------------------------------------------------------------------------

class _StopLoop(Exception):
    pass


# AUDIT-07
def test_auto_backup_continues_after_one_user_fails(app, monkeypatch):
    _ensure_user('aaa_first')
    _ensure_user('zzz_second')

    calls = {'n': 0}

    def fake_sleep(_sec):
        calls['n'] += 1
        if calls['n'] > 1:
            raise _StopLoop()

    real_load = jobs.accounts.load

    def flaky_load(conn, username):
        if username == 'aaa_first':
            raise RuntimeError('boom')
        return real_load(conn, username)

    monkeypatch.setattr(jobs.time, 'sleep', fake_sleep)
    monkeypatch.setattr(jobs.accounts, 'load', flaky_load)
    with pytest.raises(_StopLoop):
        jobs.auto_backup_job()

    second_dir = os.path.join(config.BACKUP_DIR, 'zzz_second')
    assert os.path.isdir(second_dir) and os.listdir(second_dir)


# ---------------------------------------------------------------------------
# 6. 레거시 JSON 이관 이미지가 서빙될 수 없는 URL 로 저장된다
# ---------------------------------------------------------------------------

# AUDIT-08
def test_legacy_migrated_image_url_is_servable(app):
    client = app.test_client()
    _ensure_user('trader')
    _login(client, 'trader')
    url = images.process_image('trader', 'data:image/png;base64,' + 'iVBORw0KGgo=', 123)
    assert client.get(url).status_code == 200


# ---------------------------------------------------------------------------
# 7. 복원 실패 시 내부 예외 문자열이 그대로 응답에 실린다
# ---------------------------------------------------------------------------

# AUDIT-09
def test_restore_error_does_not_leak_internals(app, monkeypatch):
    import sqlite3
    from app.database import entry_logic

    client = app.test_client()
    _login(client, 'trader')

    def boom(*_a, **_k):
        raise sqlite3.OperationalError('secret-sql-detail')
    monkeypatch.setattr(entry_logic, 'insert_entry', boom)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('data.json', json.dumps([{'id': 1, 'type': 'memo'}]))
    buf.seek(0)
    res = client.post('/api/restore', data={'file': (buf, 'b.zip')},
                      content_type='multipart/form-data')
    assert res.status_code == 500
    assert 'secret-sql-detail' not in res.get_data(as_text=True)


def test_restore_accepts_non_integer_ids(app):
    """감사 당시 500 을 내던 입력(정수가 아닌 id)은 새 id 를 받아 복원된다."""
    client = app.test_client()
    _login(client, 'trader')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('data.json', json.dumps([{'id': 'not-an-int', 'type': 'memo', 'title': 't'}]))
    buf.seek(0)
    res = client.post('/api/restore', data={'file': (buf, 'b.zip')},
                      content_type='multipart/form-data')
    assert res.status_code == 200
    assert _entry_count('trader') == 1


def test_restore_keeps_inline_images_from_old_backups(app):
    """구버전 백업의 본문 base64 이미지는 교체될 새 폴더에 추출되어 살아남는다."""
    client = app.test_client()
    _login(client, 'trader')
    thoughts = '<p><img src="data:image/png;base64,iVBORw0KGgo="></p>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('data.json', json.dumps([{'id': 5, 'type': 'memo', 'thoughts': thoughts}]))
    buf.seek(0)
    assert client.post('/api/restore', data={'file': (buf, 'b.zip')},
                       content_type='multipart/form-data').status_code == 200

    with backend_app.db_conn() as conn:
        stored = conn.execute("SELECT thoughts FROM entries WHERE username='trader'").fetchone()[0]
    url = stored.split('src="')[1].split('"')[0]
    assert url.startswith('/uploads/trader/')
    assert client.get(url).status_code == 200


# ---------------------------------------------------------------------------
# 8. 계정 삭제 시 봇·명령·재설정 요청 행이 남는다 (같은 이름 재가입자가 물려받음)
# ---------------------------------------------------------------------------

# AUDIT-10
def test_account_delete_removes_all_user_rows(app):
    _ensure_user('ghost')
    with backend_app.db_conn() as conn:
        conn.execute("INSERT INTO bots (username, bot_id, status, last_seen) "
                     "VALUES ('ghost', 'b1', 'running', ?)", (time.strftime('%Y-%m-%d'),))
        conn.execute("INSERT INTO password_reset_requests (username, requested_at) "
                     "VALUES ('ghost', 'now')")
        conn.commit()

    admin = app.test_client()
    _make_admin(admin)
    admin.delete('/api/admin/users/ghost')

    with backend_app.db_conn() as conn:
        left = sum(conn.execute(f"SELECT COUNT(*) FROM {t} WHERE username = 'ghost'").fetchone()[0]
                   for t in ('bots', 'bot_commands', 'password_reset_requests'))
    assert left == 0


# ---------------------------------------------------------------------------
# 부수 수정의 회귀 방지
# ---------------------------------------------------------------------------

def test_disabled_account_existing_bot_token_is_rejected(app):
    """차단 전에 받아 둔 토큰도 다음 호출에서 막힌다 (AUDIT-03 의 나머지 절반)."""
    import trading_api
    _ensure_user('blocked')
    raw = trading_api.create_api_key('blocked')['api_key']
    token = app.test_client().post('/api/v1/auth/token',
                                   headers={'X-API-KEY': raw}).get_json()['access_token']

    admin = app.test_client()
    _make_admin(admin)
    admin.post('/api/admin/users/blocked/toggle_allow')

    res = app.test_client().get('/api/v1/trades', headers={'Authorization': f'Bearer {token}'})
    assert res.status_code == 403
    assert res.get_json()['errorCode'] == 'ACCOUNT_DISABLED'


def test_reallowed_user_can_log_in_again(app):
    """차단을 풀면 캐시가 비워져 다시 로그인할 수 있다 (차단 캐시가 굳지 않는다)."""
    victim = app.test_client()
    _login(victim, 'blocked')
    admin = app.test_client()
    _make_admin(admin)
    admin.post('/api/admin/users/blocked/toggle_allow')   # 차단
    assert victim.get('/api/data').status_code == 401
    admin.post('/api/admin/users/blocked/toggle_allow')   # 해제

    again = app.test_client()
    _login(again, 'blocked')
    assert again.get('/api/data').status_code == 200


@pytest.mark.parametrize('body', [[1, 2], 'text', {'entry_ids': 'abc'}, {'entry_ids': [1, 'x']}])
def test_stats_rejects_malformed_body(app, body):
    client = app.test_client()
    _login(client, 'trader')
    assert client.post('/api/stats', json=body).status_code == 400


@pytest.mark.parametrize('path', ['/api/entry', '/api/change_password', '/api/preferences'])
def test_non_object_json_body_is_400_not_500(app, path):
    client = app.test_client()
    _login(client, 'trader')
    assert client.post(path, json=[1, 2, 3]).status_code == 400


def test_trusted_proxy_count_parsing(monkeypatch):
    for raw, expected in [('', 0), ('2', 2), ('-1', 0), ('abc', 0)]:
        monkeypatch.setenv('TRUSTED_PROXY_COUNT', raw)
        assert config.trusted_proxy_count() == expected
