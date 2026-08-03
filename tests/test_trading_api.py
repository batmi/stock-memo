"""시스템 트레이딩 API(/api/v1/*) 테스트.

핵심 계약을 회귀로 고정한다.
  - 인증: 키 해시 저장, 스코프, 폐기 즉시 토큰 무효화
  - 멱등: brokerExecutionId 중복 재전송이 새 기록을 만들지 않는다
  - 유실 금지: 매도 무결성 위반도 저장하되 needsReview 로 표시한다
  - 모의/실거래 분리, 해외 거래일 귀속, 배치 부분 성공
"""
import time
from datetime import datetime, timedelta

import pytest

import backend_app
import trading_api


@pytest.fixture
def api(app):
    """봇 사용자 + API 키 + 인증 헤더를 준비한다."""
    trading_api._limiter.reset()
    with backend_app.db_conn() as conn:
        conn.cursor().execute(
            "INSERT INTO users (username, password_hash, is_allowed) VALUES ('bot', 'x', 1)")
        conn.commit()
    created = trading_api.create_api_key('bot')
    client = app.test_client()
    token = client.post('/api/v1/auth/token',
                        headers={'X-API-KEY': created['api_key']}).get_json()['access_token']
    return {
        'client': client,
        'key': created['api_key'],
        'key_id': created['id'],
        'headers': {'Authorization': f'Bearer {token}'},
    }


def _trade(**overrides):
    base = {
        'symbol': '005930', 'side': 'BUY', 'price': 71000, 'volume': 10,
        'executedAt': '2026-08-01T09:30:00+09:00',
        'brokerExecutionId': 'REAL:1234:20260801:0001',
        'name': '삼성전자', 'exchange': 'KRX', 'source': 'my-stock-hts',
    }
    base.update(overrides)
    return base


# ── 인증 ──────────────────────────────────────────────────────────────

def test_health_needs_no_auth(client):
    res = client.get('/api/v1/health')
    assert res.status_code == 200
    assert res.get_json()['status'] == 'ok'


def test_api_key_is_not_stored_in_plaintext(api):
    """DB 가 유출돼도 키 원문이 그대로 새어 나가면 안 된다."""
    with backend_app.db_conn() as conn:
        row = conn.cursor().execute(
            "SELECT key_hash, key_prefix FROM api_keys WHERE username = 'bot'").fetchone()
    assert row['key_hash'] != api['key']
    assert len(row['key_hash']) == 64          # sha256 hex
    assert api['key'].startswith(row['key_prefix'])


def test_invalid_api_key_rejected(client):
    res = client.post('/api/v1/auth/token', headers={'X-API-KEY': 'skm_nope'})
    assert res.status_code == 401
    assert res.get_json()['errorCode'] == 'INVALID_API_KEY'


def test_missing_token_rejected(client):
    res = client.post('/api/v1/trades', json=_trade())
    assert res.status_code == 401
    assert res.get_json()['errorCode'] == 'TOKEN_MISSING'


def test_revoking_key_invalidates_existing_token(api):
    """서명만 검증하면 키를 폐기해도 토큰이 24시간 살아남는다 — DB 대조로 즉시 끊는다."""
    assert api['client'].get('/api/v1/trades', headers=api['headers']).status_code == 200
    trading_api.revoke_api_key('bot', api['key_id'])
    res = api['client'].get('/api/v1/trades', headers=api['headers'])
    assert res.status_code == 401
    assert res.get_json()['errorCode'] == 'TOKEN_REVOKED'


def test_insufficient_scope_is_forbidden(app):
    trading_api._limiter.reset()
    with backend_app.db_conn() as conn:
        conn.cursor().execute(
            "INSERT INTO users (username, password_hash, is_allowed) VALUES ('ro', 'x', 1)")
        conn.commit()
    created = trading_api.create_api_key('ro', scopes=[trading_api.SCOPE_TRADES_READ])
    client = app.test_client()
    token = client.post('/api/v1/auth/token',
                        headers={'X-API-KEY': created['api_key']}).get_json()['access_token']
    headers = {'Authorization': f'Bearer {token}'}

    assert client.get('/api/v1/trades', headers=headers).status_code == 200
    res = client.post('/api/v1/trades', json=_trade(), headers=headers)
    assert res.status_code == 403
    assert res.get_json()['errorCode'] == 'INSUFFICIENT_SCOPE'


def test_token_endpoint_is_rate_limited(client):
    """API 키 무차별 대입 차단."""
    trading_api._limiter.reset()
    limit = trading_api.TOKEN_RATE_LIMIT[0]
    for _ in range(limit):
        client.post('/api/v1/auth/token', headers={'X-API-KEY': 'skm_wrong'})
    res = client.post('/api/v1/auth/token', headers={'X-API-KEY': 'skm_wrong'})
    assert res.status_code == 429
    assert res.headers.get('Retry-After')


# ── 멱등성 ────────────────────────────────────────────────────────────

def test_duplicate_execution_id_is_idempotent(api):
    first = api['client'].post('/api/v1/trades', json=_trade(), headers=api['headers'])
    assert first.status_code == 201

    second = api['client'].post('/api/v1/trades', json=_trade(), headers=api['headers'])
    assert second.status_code == 200                      # 새로 만들지 않았다
    assert second.get_json()['id'] == first.get_json()['id']

    with backend_app.db_conn() as conn:
        count = conn.cursor().execute(
            "SELECT COUNT(*) c FROM entries WHERE brokerExecutionId = ?",
            (_trade()['brokerExecutionId'],)).fetchone()['c']
    assert count == 1


def test_unique_index_blocks_duplicate_at_db_level(api):
    """check-then-insert 경합으로 뚫려도 DB 제약이 마지막 방어선이 된다."""
    import sqlite3
    api['client'].post('/api/v1/trades', json=_trade(), headers=api['headers'])
    with backend_app.db_conn() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.cursor().execute(
                "INSERT INTO entries (username, type, brokerExecutionId) VALUES (?, ?, ?)",
                ('bot', 'trade', _trade()['brokerExecutionId']))


def test_empty_execution_id_is_not_constrained(api):
    """수동 입력 기록은 멱등키가 비어 있다 — UNIQUE 제약에 걸리면 안 된다."""
    with backend_app.db_conn() as conn:
        c = conn.cursor()
        for _ in range(3):
            c.execute("INSERT INTO entries (username, type, brokerExecutionId) VALUES (?, ?, '')",
                      ('bot', 'trade'))
        conn.commit()


def test_idempotency_key_header_used_as_fallback(api):
    payload = _trade()
    payload.pop('brokerExecutionId')
    headers = dict(api['headers'], **{'Idempotency-Key': 'manual-key-1'})

    first = api['client'].post('/api/v1/trades', json=payload, headers=headers)
    second = api['client'].post('/api/v1/trades', json=payload, headers=headers)
    assert first.status_code == 201 and second.status_code == 200
    assert first.get_json()['id'] == second.get_json()['id']


# ── 유실 금지 (매도 무결성) ───────────────────────────────────────────

def test_oversell_is_saved_with_review_flag(api):
    """봇 체결을 400 으로 되돌리면 재시도해도 계속 실패해 영구 유실된다."""
    api['client'].post('/api/v1/trades', json=_trade(volume=10), headers=api['headers'])
    res = api['client'].post('/api/v1/trades', headers=api['headers'], json=_trade(
        side='SELL', volume=100, brokerExecutionId='REAL:1234:20260801:0002'))

    assert res.status_code == 201                # 저장은 됐다
    body = res.get_json()
    assert body['needsReview'] is True           # 사람이 확인하도록 표시됐다
    assert body['warnings']


def test_web_ui_still_blocks_oversell(client, app):
    """웹 UI 직접 입력은 사용자가 즉시 고칠 수 있으므로 종전대로 차단한다."""
    with backend_app.db_conn() as conn:
        conn.cursor().execute(
            "INSERT INTO users (username, password_hash, is_allowed) VALUES ('u', 'x', 1)")
        conn.commit()
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'u'
        sess['expires_at'] = 9999999999

    client.post('/api/entry', json={'type': 'trade', 'tradeType': '매수', 'stockName': '삼성전자',
                                    'stockCode': '005930', 'quantity': 5, 'price': 70000})
    res = client.post('/api/entry', json={'type': 'trade', 'tradeType': '매도',
                                          'stockName': '삼성전자', 'stockCode': '005930',
                                          'quantity': 50, 'price': 75000})
    assert res.status_code == 400


def test_holding_matched_by_code_not_name(api):
    """봇은 코드만 보낸다. 이름 표기가 갈려도 보유 매칭이 어긋나면 안 된다."""
    api['client'].post('/api/v1/trades', headers=api['headers'],
                       json=_trade(volume=10, name='삼성전자'))
    res = api['client'].post('/api/v1/trades', headers=api['headers'], json=_trade(
        side='SELL', volume=10, name='삼성전자(우)',       # 이름만 다름
        brokerExecutionId='REAL:1234:20260801:0003'))
    assert res.get_json()['needsReview'] is False


def test_simulated_holdings_do_not_cover_real_sells(api):
    api['client'].post('/api/v1/trades', headers=api['headers'],
                       json=_trade(volume=10, isSimulated=True,
                                   brokerExecutionId='SIM:1234:20260801:0001'))
    res = api['client'].post('/api/v1/trades', headers=api['headers'], json=_trade(
        side='SELL', volume=10, brokerExecutionId='REAL:1234:20260801:0004'))
    assert res.get_json()['needsReview'] is True


# ── 모의/실거래 분리 ──────────────────────────────────────────────────

def test_simulated_trades_excluded_from_default_queries(api):
    api['client'].post('/api/v1/trades', json=_trade(), headers=api['headers'])
    api['client'].post('/api/v1/trades', headers=api['headers'],
                       json=_trade(isSimulated=True, brokerExecutionId='SIM:1:1:1'))

    real = api['client'].get('/api/v1/trades', headers=api['headers']).get_json()['trades']
    assert len(real) == 1 and real[0]['isSimulated'] is False

    sim = api['client'].get('/api/v1/trades?isSimulated=true',
                            headers=api['headers']).get_json()['trades']
    assert len(sim) == 1 and sim[0]['isSimulated'] is True


# ── 시각·거래일 ───────────────────────────────────────────────────────

def test_us_after_hours_trade_date_uses_exchange_local_day(api):
    """미 동부 8/1 16:30 체결은 한국시간으로 8/2 새벽이지만 거래일은 8/1 이다."""
    res = api['client'].post('/api/v1/trades', headers=api['headers'], json=_trade(
        symbol='AAPL', name='Apple', exchange='NASDAQ', currency='USD',
        executedAt='2026-08-01T16:30:00-04:00', brokerExecutionId='REAL:1234:20260801:0005'))
    body = res.get_json()
    assert body['tradeDate'] == '2026-08-01'
    assert body['executedAt'] == '2026-08-01T20:30:00Z'


def test_naive_executed_at_is_treated_as_kst(api):
    res = api['client'].post('/api/v1/trades', headers=api['headers'],
                             json=_trade(executedAt='2026-08-01T09:30:00'))
    assert res.get_json()['executedAt'] == '2026-08-01T00:30:00Z'


def test_invalid_executed_at_rejected(api):
    res = api['client'].post('/api/v1/trades', headers=api['headers'],
                             json=_trade(executedAt='어제쯤'))
    assert res.status_code == 400
    assert res.get_json()['errorCode'] == 'INVALID_FIELD'


# ── 입력 검증 ─────────────────────────────────────────────────────────

@pytest.mark.parametrize('field,value,code', [
    ('price', -1, 'INVALID_FIELD'),
    ('price', 'abc', 'INVALID_FIELD'),
    ('volume', 0, 'INVALID_FIELD'),
    ('side', 'HOLD', 'INVALID_FIELD'),
    ('status', 'DONE', 'INVALID_FIELD'),
    ('symbol', '', 'MISSING_FIELD'),
])
def test_invalid_fields_are_rejected(api, field, value, code):
    res = api['client'].post('/api/v1/trades', headers=api['headers'],
                             json=_trade(**{field: value}))
    assert res.status_code == 400
    assert res.get_json()['errorCode'] == code


def test_null_optional_fields_do_not_crash(api):
    """JSON null 이 와도 500 이 나면 안 된다 (v1 은 .replace() 에서 터졌다)."""
    res = api['client'].post('/api/v1/trades', headers=api['headers'], json=_trade(
        subAccount=None, brokerAccount=None, accountName=None, memo=None, tags=None))
    assert res.status_code == 201


def test_memo_length_is_capped(api):
    res = api['client'].post('/api/v1/trades', headers=api['headers'],
                             json=_trade(memo='가' * 6000))
    assert res.status_code == 400


# ── 계좌 매핑 ─────────────────────────────────────────────────────────

_ACCOUNT_MAPPINGS = {
    'accounts': {
        '44048158-01': {'alias': '시스템계좌', 'broker_code': '243', 'broker_name': '한국투자증권'},
    },
    'brokers': {},
}


@pytest.mark.parametrize('received', ['44048158-01', '4404815801', ' 44048158 01 '])
def test_account_matches_regardless_of_hyphens(received):
    """등록은 '-' 로 했는데 HTS 는 '-' 없이 보낸다. 어느 쪽이든 같은 계좌로 붙어야 한다."""
    broker, sub, name = trading_api._resolve_account(
        'bot', {'subAccount': received, 'brokerAccount': '243'}, _ACCOUNT_MAPPINGS)
    assert (broker, name) == ('한국투자증권', '시스템계좌')
    # 저장 표기는 사용자가 등록한 형태로 통일한다 (같은 계좌가 두 번호로 쌓이지 않도록)
    assert sub == '44048158-01'


def test_unregistered_account_keeps_hyphen_stripped_form():
    """매핑에 없는 계좌는 기존과 동일하게 하이픈을 뗀 형태로 남는다."""
    _, sub, name = trading_api._resolve_account(
        'bot', {'subAccount': '99999999-01', 'brokerAccount': '243'}, _ACCOUNT_MAPPINGS)
    assert sub == '9999999901'
    assert name == ''


# ── 배치 ──────────────────────────────────────────────────────────────

def test_batch_reports_per_item_results(api):
    """봇이 재처리할 항목을 특정할 수 있어야 한다 — index 로 원본을 지목한다."""
    payload = {'source': 'my-stock-hts', 'trades': [
        _trade(brokerExecutionId='B1'),
        _trade(brokerExecutionId='B1'),                    # 배치 내 중복
        _trade(brokerExecutionId='B2', price=-5),          # 검증 실패
        _trade(brokerExecutionId='B3'),
    ]}
    res = api['client'].post('/api/v1/trades/batch', json=payload, headers=api['headers'])
    body = res.get_json()

    assert res.status_code == 201
    assert (body['inserted'], body['skipped'], body['failed']) == (2, 1, 1)
    assert [r['index'] for r in body['results']] == [0, 1, 2, 3]
    assert [r['status'] for r in body['results']] == \
        ['created', 'duplicate', 'failed', 'created']
    assert body['results'][2]['errorCode'] == 'INVALID_FIELD'


def test_batch_accepts_legacy_bare_array(api):
    res = api['client'].post('/api/v1/trades/batch', json=[_trade()], headers=api['headers'])
    assert res.status_code == 201
    assert res.get_json()['inserted'] == 1


def test_batch_size_is_capped(api):
    payload = {'trades': [_trade(brokerExecutionId=f'X{i}')
                          for i in range(trading_api.MAX_BATCH_ITEMS + 1)]}
    res = api['client'].post('/api/v1/trades/batch', json=payload, headers=api['headers'])
    assert res.status_code == 413
    assert res.get_json()['errorCode'] == 'PAYLOAD_TOO_LARGE'


# ── 동기화 지점 ───────────────────────────────────────────────────────

def test_last_sync_is_scoped_by_source(api):
    """웹에서 손으로 넣은 미래 날짜 기록이 봇의 백필 구간을 망치면 안 된다."""
    api['client'].post('/api/v1/trades', json=_trade(), headers=api['headers'])
    with backend_app.db_conn() as conn:
        conn.cursor().execute(
            "INSERT INTO entries (username, type, tradeType, stockName, rawDate, "
            "executedAtUtc, source) VALUES ('bot', 'trade', '매수', '수동', "
            "'2099-01-01T00:00:00', '2098-12-31T15:00:00Z', '')")
        conn.commit()

    scoped = api['client'].get('/api/v1/trades/last-sync?source=my-stock-hts',
                               headers=api['headers']).get_json()
    assert scoped['lastExecutedAt'] == '2026-08-01T00:30:00Z'
    assert scoped['count'] == 1
    assert scoped['lastBrokerExecutionId'] == _trade()['brokerExecutionId']


def test_get_by_execution_id(api):
    api['client'].post('/api/v1/trades', json=_trade(), headers=api['headers'])
    res = api['client'].get(f"/api/v1/trades/by-exec-id/{_trade()['brokerExecutionId']}",
                            headers=api['headers'])
    assert res.status_code == 200
    assert res.get_json()['symbol'] == '005930'

    assert api['client'].get('/api/v1/trades/by-exec-id/nope',
                             headers=api['headers']).status_code == 404


# ── 정정·삭제 ─────────────────────────────────────────────────────────

def test_patch_corrects_estimated_fill(api):
    """추정 체결이 취소로 확정되는 일은 실제로 일어난다 — 정정 수단이 없으면 오기록이 박제된다."""
    created = api['client'].post('/api/v1/trades', headers=api['headers'],
                                 json=_trade(confidence='ESTIMATED')).get_json()
    assert created['confidence'] == 'ESTIMATED'

    res = api['client'].patch(f"/api/v1/trades/{created['id']}", headers=api['headers'],
                              json={'status': 'CANCELED', 'confidence': 'CONFIRMED',
                                    'memo': '취소로 확정'})
    assert res.status_code == 200
    body = res.get_json()
    assert body['status'] == 'CANCELED' and body['memo'] == '취소로 확정'


def test_canceled_trade_does_not_consume_holdings(api):
    api['client'].post('/api/v1/trades', json=_trade(volume=10), headers=api['headers'])
    sell = api['client'].post('/api/v1/trades', headers=api['headers'], json=_trade(
        side='SELL', volume=10, brokerExecutionId='S1')).get_json()
    api['client'].patch(f"/api/v1/trades/{sell['id']}", json={'status': 'CANCELED'},
                        headers=api['headers'])

    # 매도가 취소됐으므로 보유 10주가 그대로 남아 다음 매도가 정상이어야 한다.
    res = api['client'].post('/api/v1/trades', headers=api['headers'],
                             json=_trade(side='SELL', volume=10, brokerExecutionId='S2'))
    assert res.get_json()['needsReview'] is False


def test_delete_removes_bot_entry(api):
    created = api['client'].post('/api/v1/trades', json=_trade(),
                                 headers=api['headers']).get_json()
    assert api['client'].delete(f"/api/v1/trades/{created['id']}",
                                headers=api['headers']).status_code == 204
    assert api['client'].get(f"/api/v1/trades/by-exec-id/{_trade()['brokerExecutionId']}",
                             headers=api['headers']).status_code == 404


def test_api_cannot_touch_manually_entered_records(api):
    """웹에서 사람이 쓴 기록을 봇 토큰이 지우거나 고칠 수 있으면 안 된다."""
    with backend_app.db_conn() as conn:
        c = conn.cursor()
        c.execute("INSERT INTO entries (username, type, tradeType, stockName, brokerExecutionId,"
                  " source) VALUES ('bot', 'trade', '매수', '수기입력', '', '')")
        manual_id = c.lastrowid
        conn.commit()

    assert api['client'].delete(f'/api/v1/trades/{manual_id}',
                                headers=api['headers']).status_code == 404
    assert api['client'].patch(f'/api/v1/trades/{manual_id}', json={'price': 1},
                               headers=api['headers']).status_code == 404


# ── 기초잔고 ──────────────────────────────────────────────────────────

def test_opening_positions_prevent_first_sell_warning(api):
    """연동 이전 보유분을 등록하지 않으면 첫 매도가 전부 '보유 기록 없음'이 된다."""
    res = api['client'].post('/api/v1/positions/opening', headers=api['headers'], json={
        'asOf': '2026-07-01', 'source': 'my-stock-hts',
        'positions': [{'symbol': '005930', 'name': '삼성전자', 'volume': 30, 'avgPrice': 68000}]})
    assert res.status_code == 201 and res.get_json()['inserted'] == 1

    sell = api['client'].post('/api/v1/trades', headers=api['headers'], json=_trade(
        side='SELL', volume=30, brokerExecutionId='REAL:1234:20260801:0009'))
    assert sell.get_json()['needsReview'] is False


def test_opening_positions_are_idempotent(api):
    body = {'asOf': '2026-07-01',
            'positions': [{'symbol': '005930', 'volume': 30, 'avgPrice': 68000}]}
    api['client'].post('/api/v1/positions/opening', json=body, headers=api['headers'])
    res = api['client'].post('/api/v1/positions/opening', json=body, headers=api['headers'])
    assert res.get_json()['inserted'] == 0 and res.get_json()['skipped'] == 1


# ── 종목명 조회 격리 ──────────────────────────────────────────────────

def test_stock_name_lookup_does_not_leak_across_users(api):
    """v1 은 username 조건이 없어 다른 사용자의 종목명이 새어 나왔다."""
    with backend_app.db_conn() as conn:
        conn.cursor().execute(
            "INSERT INTO entries (username, type, stockCode, stockName) "
            "VALUES ('other', 'trade', '999999', '남의비밀종목')")
        conn.commit()

    payload = _trade(symbol='999999', brokerExecutionId='REAL:1234:20260801:0007')
    payload.pop('name')
    res = api['client'].post('/api/v1/trades', json=payload, headers=api['headers'])
    assert res.get_json()['name'] == '999999'          # 코드로 폴백, 남의 이름을 쓰지 않는다


# ── 봇 상태 ───────────────────────────────────────────────────────────

def test_bot_status_ping(api):
    res = api['client'].post('/api/v1/bot/status', json={'status': 'running'},
                             headers=api['headers'])
    assert res.status_code == 200
    assert res.get_json()['nextPingSeconds'] == trading_api.BOT_PING_INTERVAL_SECONDS

    with backend_app.db_conn() as conn:
        row = conn.cursor().execute(
            "SELECT bot_status, bot_last_seen FROM users WHERE username = 'bot'").fetchone()
    assert row['bot_status'] == 'running'
    # 만료 판정에 쓰이는 값이므로 오프셋(타임존)이 반드시 붙어 있어야 한다.
    assert datetime.fromisoformat(row['bot_last_seen']).tzinfo is not None


def test_bot_status_rejects_unknown_value(api):
    res = api['client'].post('/api/v1/bot/status', json={'status': '몰라'},
                             headers=api['headers'])
    assert res.status_code == 400


# ── 봇 상태 판정(서버 확정) ───────────────────────────────────────────

def _seen(seconds_ago):
    return (trading_api._now_kst() - timedelta(seconds=seconds_ago)).isoformat(timespec='seconds')


def test_evaluate_bot_state_running_within_threshold():
    """Ping 을 3회 놓치기 전까지는 정상 가동중을 유지한다."""
    state, _ = trading_api.evaluate_bot_state('running', _seen(20))
    assert state == 'running'


def test_evaluate_bot_state_offline_after_three_missed_pings():
    """10초 간격 Ping 이 3회 연속 누락되면(+여유) 통신단절로 본다."""
    state, elapsed = trading_api.evaluate_bot_state(
        'running', _seen(trading_api.BOT_OFFLINE_AFTER_SECONDS + 1))
    assert state == 'offline'
    assert elapsed > trading_api.BOT_OFFLINE_AFTER_SECONDS


def test_evaluate_bot_state_stopped_beats_staleness():
    """HTS 가 종료를 알렸으면 오래된 기록이어도 '통신단절'이 아니라 '정지됨'이다."""
    state, _ = trading_api.evaluate_bot_state('stopped', _seen(86400))
    assert state == 'stopped'


def test_evaluate_bot_state_never_without_record():
    assert trading_api.evaluate_bot_state(None, None)[0] == 'never'
    assert trading_api.evaluate_bot_state('running', '이상한값')[0] == 'never'


def test_evaluate_bot_state_accepts_legacy_naive_timestamp():
    """오프셋 없이 저장된 이전 형식 값은 KST 로 간주해 그대로 판정한다."""
    legacy = trading_api._now_kst().strftime('%Y-%m-%d %H:%M:%S')
    assert trading_api.evaluate_bot_state('running', legacy)[0] == 'running'


def test_api_me_exposes_server_computed_state(api):
    """화면이 직접 만료를 계산하지 않도록 /api/me 가 확정 상태를 내려준다."""
    api['client'].post('/api/v1/bot/status', json={'status': 'running'},
                       headers=api['headers'])
    with api['client'].session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'bot'
        sess['expires_at'] = time.time() + 3600

    data = api['client'].get('/api/me').get_json()
    assert data['bot_state'] == 'running'
    assert data['bot_ping_interval_seconds'] == trading_api.BOT_PING_INTERVAL_SECONDS
    assert data['bot_offline_after_seconds'] == trading_api.BOT_OFFLINE_AFTER_SECONDS


# ── 레거시 키 이관 ────────────────────────────────────────────────────

def test_legacy_plaintext_key_is_migrated_and_erased(app):
    """평문으로 저장돼 있던 기존 키는 해시로 옮기고 원문을 지운다 (키는 계속 사용 가능)."""
    trading_api._limiter.reset()
    legacy = 'legacy-uuid-key'
    with backend_app.db_conn() as conn:
        c = conn.cursor()
        c.execute("INSERT INTO users (username, password_hash, is_allowed, api_key) "
                  "VALUES ('old', 'x', 1, ?)", (legacy,))
        conn.commit()
        trading_api._migrate_legacy_api_keys(conn)
        row = c.execute("SELECT api_key FROM users WHERE username = 'old'").fetchone()

    assert row['api_key'] is None                                  # 평문 삭제됨
    res = app.test_client().post('/api/v1/auth/token', headers={'X-API-KEY': legacy})
    assert res.status_code == 200                                  # 기존 키는 그대로 동작


# ── 봇 명령 채널 (재동기화) ───────────────────────────────────────────
#  봇은 가정용 네트워크 뒤에 있어 서버가 먼저 접속할 수 없다. 웹에서 누른 지시는
#  Ping 응답에 실려야만 전달되므로, 그 경로가 끊기면 버튼이 통째로 죽는다.

def _ping(api, **body):
    payload = {'status': 'running'}
    payload.update(body)
    return api['client'].post('/api/v1/bot/status', json=payload,
                              headers=api['headers']).get_json()


def test_ping_carries_no_command_by_default(api):
    body = _ping(api)
    assert body['command'] == 'none'
    assert 'commandId' not in body


def test_requested_resync_reaches_the_bot_on_next_ping(api):
    cmd_id = trading_api.request_bot_command(
        'bot', 'resync', {'from': '2026-05-01', 'to': None})

    body = _ping(api)

    assert body['command'] == 'resync'
    assert body['commandId'] == cmd_id
    assert body['commandParams'] == {'from': '2026-05-01', 'to': None}


def test_command_is_delivered_only_once(api):
    """재동기화가 두 번 돌면 운용자가 일부러 지운 기록이 되살아난다.

    ack 를 받을 때까지 반복 전달하면 '명령이 반드시 실행된다'는 보장은 얻지만,
    봇이 명령을 받고 ack 를 보내기 전에 재시작하면 같은 재동기화가 또 돈다.
    서버 데이터로는 멱등해도 **운용자의 의도로는 멱등하지 않다.**
    """
    trading_api.request_bot_command('bot', 'resync', {'from': '2026-05-01'})

    assert _ping(api)['command'] == 'resync'
    assert _ping(api)['command'] == 'none'        # ack 전이어도 다시 주지 않는다
    assert _ping(api)['command'] == 'none'


def test_delivered_but_unacked_command_shows_as_running(api):
    """봇이 받아갔는데 결과 보고가 없으면 '처리 중'이다 — 다시 보내지는 않는다."""
    trading_api.request_bot_command('bot', 'resync', {'from': '2026-05-01'})
    _ping(api)

    latest = trading_api.latest_bot_command('bot', 'resync')
    assert latest['state'] == 'running'
    assert latest['delivered_at'] and not latest['acked_at']


def test_result_is_still_recorded_after_single_delivery(api):
    """한 번만 전달하더라도 결과 보고는 받아야 웹에 표시할 수 있다."""
    cmd_id = trading_api.request_bot_command('bot', 'resync', {'from': '2026-05-01'})
    assert _ping(api)['commandId'] == cmd_id

    _ping(api, commandAck={'id': cmd_id, 'result': 'queued', 'count': 42})
    assert trading_api.latest_bot_command('bot', 'resync')['state'] == 'done'


def test_ack_is_recorded_for_the_web_view(api):
    cmd_id = trading_api.request_bot_command('bot', 'resync', {'from': '2026-05-01'})
    _ping(api)
    _ping(api, commandAck={'id': cmd_id, 'result': 'queued', 'count': 42,
                           'message': '로컬 체결 1284건 확인'})

    latest = trading_api.latest_bot_command('bot', 'resync')
    assert latest['state'] == 'done'
    assert latest['result'] == 'queued'
    assert latest['result_count'] == 42
    assert '1284건' in latest['result_message']


def test_pressing_the_button_twice_does_not_queue_two_commands(api):
    first = trading_api.request_bot_command('bot', 'resync', {'from': '2026-05-01'})
    second = trading_api.request_bot_command('bot', 'resync', {'from': '2026-05-01'})
    assert first == second


def test_malformed_ack_never_breaks_the_heartbeat(api):
    """ack 가 이상하다고 Ping 을 400 으로 되돌리면 웹이 '통신단절'로 바뀐다."""
    res = api['client'].post('/api/v1/bot/status',
                             json={'status': 'running', 'commandAck': 'nonsense'},
                             headers=api['headers'])
    assert res.status_code == 200
    assert res.get_json()['status'] == 'success'


def test_stopping_bot_is_not_given_new_work(api):
    """멈추는 중인 봇에 일감을 줘도 처리하지 못한다."""
    trading_api.request_bot_command('bot', 'resync', {'from': '2026-05-01'})
    assert _ping(api, status='stopped')['command'] == 'none'


def test_expired_command_is_not_delivered(api, monkeypatch):
    """봇이 오래 꺼져 있었다면, 뒤늦게 켜졌을 때 옛 지시가 튀어나오면 안 된다."""
    trading_api.request_bot_command('bot', 'resync', {'from': '2026-05-01'})
    monkeypatch.setattr(trading_api, 'BOT_COMMAND_TTL_SECONDS', -1)

    assert _ping(api)['command'] == 'none'
    assert trading_api.latest_bot_command('bot', 'resync')['state'] == 'expired'


def test_unsupported_command_is_refused_at_the_source(api):
    """pause/resume 은 매매를 멈추는 지시다 — 서버가 큐에 넣는 것부터 막는다."""
    import pytest
    for command in ('pause', 'resume'):
        with pytest.raises(ValueError):
            trading_api.request_bot_command('bot', command)


def test_completed_command_is_never_sent_again(api):
    """화면에 '완료'로 표시된 재동기화는 어떤 경우에도 다시 나가면 안 된다.

    봇의 중복 실행 방지는 메모리에만 있어 재시작하면 잊는다. 그래서 '두 번 실행되지
    않는다'는 보장은 서버가 져야 한다 — 봇을 못 믿어서가 아니라, 라즈베리파이는
    재부팅되는 물건이기 때문이다.
    """
    cmd_id = trading_api.request_bot_command('bot', 'resync', {'from': '2026-05-01'})
    assert _ping(api)['commandId'] == cmd_id
    _ping(api, commandAck={'id': cmd_id, 'result': 'queued', 'count': 42})
    assert trading_api.latest_bot_command('bot', 'resync')['state'] == 'done'

    # 봇이 재시작해 처리 이력을 잊은 상태로 계속 Ping 해도 다시 받지 않는다.
    for _ in range(30):
        assert _ping(api)['command'] == 'none'


# ── 봇 인스턴스 분리 (botId) ──────────────────────────────────────────
#  하트비트·명령의 스코프는 API 키가 아니라 **사용자**다(키는 인증 직후 username 으로
#  바뀐다). 그래서 HTS 를 여러 대 돌리면 키를 따로 발급해도 상태가 한 칸에 겹쳐 쓰였다.

def test_pings_from_different_bots_do_not_overwrite_each_other(api):
    """실전봇이 죽어도 모의봇 Ping 이 화면을 '정상'으로 유지하던 문제."""
    _ping(api, botId='real', label='실전 68029263')
    _ping(api, botId='sim', label='모의 50196591', isSimulated=True)

    bots = {b['botId']: b for b in trading_api.list_bots('bot')}
    assert set(bots) == {'real', 'sim'}
    assert bots['real']['label'] == '실전 68029263'
    assert bots['sim']['isSimulated'] is True


def test_missing_bot_id_is_grouped_as_legacy(api):
    """botId 를 안 보내는 구버전 HTS 도 그대로 동작해야 한다."""
    body = _ping(api)
    assert body['botId'] == trading_api.LEGACY_BOT_ID
    assert [b['botId'] for b in trading_api.list_bots('bot')] == [trading_api.LEGACY_BOT_ID]


def test_summary_reports_the_worst_bot_not_the_healthiest(api):
    """'하나라도 살아 있으면 초록'은 정확히 이 기능이 막으려는 오표시다."""
    bots = [
        {'botId': 'sim', 'state': 'running', 'elapsedSeconds': 3.0},
        {'botId': 'real', 'state': 'offline', 'elapsedSeconds': 900.0},
    ]
    state, elapsed, worst = trading_api.summarize_bot_states(bots)
    assert (state, worst) == ('offline', 'real')
    assert elapsed == 900.0


def test_command_targeted_at_one_bot_is_not_taken_by_another(api):
    """엉뚱한 봇이 채가면 그 봇이 ack 까지 보내 웹에는 '완료'로 뜬다 — 조용한 실패다."""
    _ping(api, botId='real')
    _ping(api, botId='sim')
    cmd_id = trading_api.request_bot_command(
        'bot', 'resync', {'from': '2026-05-01'}, bot_id='real')

    assert _ping(api, botId='sim')['command'] == 'none'
    assert _ping(api, botId='real')['commandId'] == cmd_id


def test_untargeted_command_is_withheld_while_several_bots_are_connected(api):
    """대상을 모르는 명령은 배달하지 않는다 — 오배달보다 미배달이 낫다.

    전달되지 않은 명령은 '미처리'로 남아 운용자가 대상을 골라 다시 누를 수 있다.
    """
    _ping(api, botId='real')
    _ping(api, botId='sim')
    trading_api.request_bot_command('bot', 'resync', {'from': '2026-05-01'})

    assert _ping(api, botId='real')['command'] == 'none'
    assert _ping(api, botId='sim')['command'] == 'none'


def test_untargeted_command_still_reaches_a_solo_bot(api):
    """봇이 한 대뿐이면 예전처럼 그냥 전달된다 (하위호환)."""
    _ping(api, botId='real')
    cmd_id = trading_api.request_bot_command('bot', 'resync', {'from': '2026-05-01'})
    assert _ping(api, botId='real')['commandId'] == cmd_id


def test_pending_command_is_counted_per_bot(api):
    """실전봇 재동기화가 대기 중이라고 모의봇 요청까지 삼키면 안 된다."""
    _ping(api, botId='real')
    _ping(api, botId='sim')
    first = trading_api.request_bot_command('bot', 'resync', {'from': '2026-05-01'}, bot_id='real')
    second = trading_api.request_bot_command('bot', 'resync', {'from': '2026-05-01'}, bot_id='sim')
    assert first != second


# ── 매매 분류 (tradeClass / isSystem) ─────────────────────────────────
#  봇은 자기 계좌에서 일어난 체결을 전부 보고한다 — 증권사 앱·HTS 에서 사람이 낸
#  주문까지 포함해서다. 예전엔 분류가 비면 '시스템'으로 채워 그 전부가 자동매매
#  성과로 뭉쳐졌다.

def _post(api, **overrides):
    return api['client'].post('/api/v1/trades', json=_trade(**overrides),
                              headers=api['headers'])


def test_system_flag_pins_the_class(api):
    res = _post(api, isSystem=True)
    assert res.status_code == 201
    assert res.get_json()['tradeClass'] == '시스템'


def test_missing_class_is_no_longer_defaulted_to_system(api):
    """분류를 안 보냈다고 '시스템'으로 채우면 외부 주문이 자동매매 성과에 섞인다."""
    res = _post(api, isSystem=False)
    assert res.status_code == 201
    assert res.get_json()['tradeClass'] == ''


def test_unknown_origin_also_avoids_system(api):
    """isSystem 자체가 없어도 '시스템'으로 단정하지 않는다."""
    assert _post(api).get_json()['tradeClass'] == ''


def test_non_system_trade_inherits_class_from_same_symbol(api):
    """토스 앱에서 산 종목을 이미 '장기투자'로 분류해 뒀다면 그것을 따른다."""
    _post(api, brokerExecutionId='SEED', tradeClass='장기투자', isSystem=False)
    res = _post(api, brokerExecutionId='REAL:1234:20260802:0002', isSystem=False)
    assert res.get_json()['tradeClass'] == '장기투자'


def test_inheritance_never_picks_up_system(api):
    """예전 버전이 남긴 '시스템' 기록을 물려받으면 그 오염이 영구화된다."""
    _post(api, brokerExecutionId='OLD', isSystem=True)
    res = _post(api, brokerExecutionId='REAL:1234:20260802:0003', isSystem=False)
    assert res.get_json()['tradeClass'] == ''


def test_explicit_class_survives_the_system_flag_being_absent(api):
    """봇이 분류를 직접 지정하면 그대로 쓴다."""
    assert _post(api, tradeClass='배당투자').get_json()['tradeClass'] == '배당투자'


def test_is_system_is_three_state_in_storage(api):
    """'사람이 냈다(False)'와 '모른다(None)'는 다른 사실이다 — 분류 폴백이 거기 걸려 있다."""
    _post(api, brokerExecutionId='A', isSystem=False)
    _post(api, brokerExecutionId='B')
    with backend_app.db_conn() as conn:
        rows = dict(conn.cursor().execute(
            "SELECT brokerExecutionId, isSystem FROM entries "
            "WHERE brokerExecutionId IN ('A', 'B')").fetchall())
    assert rows['A'] == 0
    assert rows['B'] is None


def test_opening_balance_is_not_system(api):
    """연동 이전부터 들고 있던 잔고는 자동매매가 낸 체결이 아니다."""
    res = api['client'].post('/api/v1/positions/opening', json={
        'asOf': '2026-08-01',
        'positions': [{'symbol': '005930', 'avgPrice': 70000, 'volume': 5}],
    }, headers=api['headers'])
    assert res.status_code in (200, 201)
    with backend_app.db_conn() as conn:
        row = conn.cursor().execute(
            "SELECT tradeClass, isSystem FROM entries WHERE stockCode = '005930'").fetchone()
    assert row['isSystem'] == 0
    assert row['tradeClass'] != '시스템'


# ── 웹 화면 경로 (세션) ───────────────────────────────────────────────

def _as_web_user(api):
    # check_login 은 username 만으로는 통과하지 않는다 — logged_in 과 절대 만료 시각까지
    # 있어야 세션으로 인정한다(backend_app.check_login).
    with api['client'].session_transaction() as sess:
        sess['username'] = 'bot'
        sess['logged_in'] = True
        sess['expires_at'] = time.time() + 3600
    return api['client']


def test_resync_refuses_to_guess_when_several_bots_are_connected(api):
    """대상을 안 고르면 아무 봇이나 채가 엉뚱한 계좌가 재동기화된다 — 여기서 막는다."""
    _ping(api, botId='real', label='실전')
    _ping(api, botId='sim', label='모의')

    res = _as_web_user(api).post('/api/me/bot/resync', json={'preset': 'quarter'})
    assert res.status_code == 400
    assert {b['botId'] for b in res.get_json()['bots']} == {'real', 'sim'}


def test_resync_pins_the_solo_bot_explicitly(api):
    """한 대뿐이면 그 봇을 명시 지정한다 — 두 대째가 붙어도 수신자가 바뀌지 않는다."""
    _ping(api, botId='real')
    res = _as_web_user(api).post('/api/me/bot/resync', json={'preset': 'quarter'})
    assert res.status_code == 200
    assert res.get_json()['botId'] == 'real'


def test_resync_rejects_unknown_bot(api):
    _ping(api, botId='real')
    res = _as_web_user(api).post('/api/me/bot/resync',
                                 json={'preset': 'quarter', 'botId': 'nope'})
    assert res.status_code == 400


def test_me_reports_the_worst_bot_and_lists_all(api, monkeypatch):
    """실전봇이 죽어도 모의봇 Ping 이 초록불을 유지하던 것이 이 기능의 출발점이다."""
    _ping(api, botId='real', label='실전')
    _ping(api, botId='sim', label='모의')
    # 실전봇만 오래 굶긴다 — Ping 시각을 과거로 밀어 통신단절 상태를 만든다.
    with backend_app.db_conn() as conn:
        conn.cursor().execute(
            "UPDATE bots SET last_seen = ? WHERE username = 'bot' AND bot_id = 'real'",
            ((datetime.now(trading_api.KST) - timedelta(hours=2)).isoformat(),))
        conn.commit()

    body = _as_web_user(api).get('/api/me').get_json()
    assert body['bot_state'] == 'offline'
    assert {b['botId']: b['state'] for b in body['bots']} == {
        'real': 'offline', 'sim': 'running'}
