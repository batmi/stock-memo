"""시스템 트레이딩 API(/api/v1/*) 테스트.

핵심 계약을 회귀로 고정한다.
  - 인증: 키 해시 저장, 스코프, 폐기 즉시 토큰 무효화
  - 멱등: brokerExecutionId 중복 재전송이 새 기록을 만들지 않는다
  - 유실 금지: 매도 무결성 위반도 저장하되 needsReview 로 표시한다
  - 모의/실거래 분리, 해외 거래일 귀속, 배치 부분 성공
"""
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
    assert res.get_json()['nextPingSeconds'] == 60

    with backend_app.db_conn() as conn:
        row = conn.cursor().execute(
            "SELECT bot_status FROM users WHERE username = 'bot'").fetchone()
    assert row['bot_status'] == 'running'


def test_bot_status_rejects_unknown_value(api):
    res = api['client'].post('/api/v1/bot/status', json={'status': '몰라'},
                             headers=api['headers'])
    assert res.status_code == 400


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
