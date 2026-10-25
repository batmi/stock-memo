"""HTTP 라우트 — /api/v1/*.

각 핸들러는 얇게 유지한다: 요청을 읽고, 도메인 모듈에 넘기고, 응답을 만든다.
계산·검증·저장 규칙은 validation / entries / bots 가 갖는다.
"""

import sqlite3
from datetime import datetime, time, timedelta, timezone

from flask import request, jsonify

from app.services import accounts, realized_pnl
from app.services import entries as entry_service
from app.utils import ratelimit
from app.utils import statscache
from app.database import entry_logic
from app.database.db import db_conn

from .bots import (
    _apply_command_ack, _normalize_bot_id, _take_pending_command, _upsert_bot,
)
from .common import (
    API_VERSION, BOT_PING_INTERVAL_SECONDS,
    KST, MAX_BATCH_ITEMS, SCOPE_BOT_WRITE, SCOPE_TRADES_READ,
    SCOPE_TRADES_WRITE, TOKEN_TTL_SECONDS, bp, _err, _log, _now_iso, _now_kst,
    _now_kst_str,
)
from .entries import (
    _fetch_by_exec_id, _insert_trade, build_entry, entry_to_response, opening_execution_id,
)
from .keys import _hash_key
from .security import _account_disabled, _client_ip, _serializer, require_token
from .validation import (
    ValidationError, _normalize_enum, _num, _parse_executed_at, _text, _VALID_CONFIDENCE, _VALID_STATUS,
)


# 기존 웹 기록의 rawDate는 KST이고 executedAtUtc는 UTC다. 같은 시간축으로
# 비교해야 해외 오프셋 조회와 레거시 기록의 마지막 동기화 지점이 일치한다.
_RAW_TIME_SQL = "COALESCE(NULLIF(rawDate, ''), date)"
_LEGACY_TIME_SQL = (
    f"CASE WHEN substr({_RAW_TIME_SQL}, -5, 1) IN ('+', '-') "
    f"THEN substr({_RAW_TIME_SQL}, 1, length({_RAW_TIME_SQL}) - 2) || ':' || substr({_RAW_TIME_SQL}, -2) "
    f"ELSE {_RAW_TIME_SQL} END")
_LEGACY_OFFSET_SQL = (
    f"CASE WHEN instr(substr({_RAW_TIME_SQL}, 11), '+') > 0 "
    f"OR instr(substr({_RAW_TIME_SQL}, 11), '-') > 0 OR upper(substr({_RAW_TIME_SQL}, -1)) = 'Z' "
    "THEN '+0 hours' ELSE '-9 hours' END")
_TRADE_TIME_SQL = ("COALESCE(julianday(NULLIF(executedAtUtc, '')), "
                   f"julianday({_LEGACY_TIME_SQL}, {_LEGACY_OFFSET_SQL}))")


def _query_time(value, field):
    if not value:
        return None
    value = value.strip()
    try:
        if len(value) == 10:
            day = datetime.strptime(value, '%Y-%m-%d').date()
            # 기존 날짜 전용 필터의 UTC 기준을 유지하고 계약에 명시한다.
            if field == 'to':
                day += timedelta(days=1)
            return datetime.combine(day, time.min, timezone.utc)
        parsed, _ = _parse_executed_at(value)
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError, ValidationError) as exc:
        raise ValidationError('INVALID_FIELD', f'{field} 시각이 올바르지 않습니다.', field) from exc


@bp.route('/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'ok',
        'serverTime': _now_kst().strftime('%Y-%m-%dT%H:%M:%S%z'),
        'apiVersion': API_VERSION,
    }), 200


@bp.route('/auth/token', methods=['POST'])
def auth_token():
    allowed, _, retry_after = ratelimit.api_tokens.check(_client_ip())
    if not allowed:
        resp, status = _err(429, 'RATE_LIMITED',
                            '토큰 발급 요청이 너무 잦습니다. 잠시 후 다시 시도하세요.')
        resp.headers['Retry-After'] = str(retry_after)
        return resp, status

    api_key = request.headers.get('X-API-KEY')
    if not api_key:
        body = request.get_json(silent=True)
        if isinstance(body, dict):
            api_key = body.get('api_key')
    if not api_key or not isinstance(api_key, str):
        return _err(400, 'MISSING_API_KEY', 'API 키가 누락되었습니다.')

    with db_conn() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT k.id, k.username, k.scopes, k.revoked_at, u.is_allowed, u.must_change_password "
            "FROM api_keys k LEFT JOIN users u ON u.username = k.username "
            "WHERE k.key_hash = ?",
            (_hash_key(api_key.strip()),))
        row = c.fetchone()

    if row is None or row['revoked_at']:
        return _err(401, 'INVALID_API_KEY', '유효하지 않은 API 키입니다.')
    if not row['is_allowed']:
        return _account_disabled()
    if row['must_change_password']:
        return _err(403, 'PASSWORD_CHANGE_REQUIRED', '웹에서 새 비밀번호를 먼저 설정해주세요.')

    token = _serializer().dumps({'u': row['username'], 'k': row['id']})
    return jsonify({
        'access_token': token,
        'token_type': 'Bearer',
        'expires_in': TOKEN_TTL_SECONDS,
        'scopes': (row['scopes'] or '').split(),
    }), 200


@bp.route('/bot/status', methods=['POST'])
@require_token(SCOPE_BOT_WRITE)
def bot_status(username, scopes):
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or 'status' not in data:
        return _err(400, 'MISSING_FIELD', 'status 필드가 필요합니다.', field='status')

    status_value = str(data['status']).strip().lower()
    if status_value not in ('running', 'stopped', 'error'):
        return _err(400, 'INVALID_FIELD',
                    'status 는 running/stopped/error 중 하나여야 합니다.', field='status')

    # ⭐️ 부가 필드는 검증하지 않고 잘라서 받는다. 라벨이 길다고 하트비트를 400 으로
    #    되돌리면 화면이 '통신단절'로 바뀐다 — 상태 보고가 라벨보다 중요하다.
    bot_id = _normalize_bot_id(data.get('botId'))
    label = str(data.get('label') or '').strip()[:60] or None
    message = str(data.get('message') or '').strip()[:500] or None
    is_simulated = bool(data.get('isSimulated'))

    # ⭐️ 오프셋 포함 ISO 8601 로 저장한다. 만료 판정(마지막 Ping 이후 경과 시간)에
    #    쓰이는 값이라 타임존이 빠지면 읽는 쪽에서 몇 시간씩 어긋난다.
    now = _now_iso()
    with db_conn() as conn:
        c = conn.cursor()
        _upsert_bot(c, username, bot_id, status_value, now,
                    label=label, is_simulated=is_simulated, message=message)

        # ⭐️ users 의 단일 칸은 하위호환으로만 유지한다. 봇이 여러 대면 마지막에
        #    Ping 한 놈으로 덮이므로 **화면 판정에는 쓰지 않는다** (bots 테이블이 원본).
        c.execute("UPDATE users SET bot_status = ?, bot_last_seen = ? WHERE username = ?",
                  (status_value, now, username))

        # ack 를 먼저 반영해야 방금 끝낸 명령을 같은 응답에서 또 내려보내지 않는다.
        _apply_command_ack(c, username, data.get('commandAck'), bot_id)

        # 봇이 멈추는 중이면 새 일감을 주지 않는다 — 받아도 처리하지 못한다.
        pending = (_take_pending_command(c, username, bot_id)
                   if status_value == 'running' else None)
        conn.commit()

    body = {
        'status': 'success',
        'updatedAt': now,
        'botId': bot_id,
        'nextPingSeconds': BOT_PING_INTERVAL_SECONDS,
        'command': pending['command'] if pending else 'none',
    }
    if pending:
        body['commandId'] = pending['id']
        body['commandParams'] = pending['params']
    return jsonify(body), 200


@bp.route('/trades', methods=['POST'])
@require_token(SCOPE_TRADES_WRITE)
def create_trade(username, scopes):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _err(400, 'INVALID_REQUEST', '요청 본문이 JSON 객체가 아닙니다.')

    # brokerExecutionId 가 없으면 Idempotency-Key 헤더를 대체 멱등키로 쓴다.
    if not data.get('brokerExecutionId'):
        idem = request.headers.get('Idempotency-Key')
        if idem:
            data = dict(data, brokerExecutionId=idem.strip()[:200])

    with db_conn(write=True) as conn:
        c = conn.cursor()
        mappings = accounts.load(conn, username)
        try:
            entry = build_entry(c, username, data, mappings)
        except ValidationError as e:
            return _err(400, e.code, e.message, field=e.field)

        try:
            entry_id, created, warnings = _insert_trade(c, username, entry)
        except sqlite3.Error as e:
            _log().error(f"[trading_api] 매매 기록 저장 실패: {e}")
            return _err(500, 'INTERNAL_ERROR', '기록 저장 중 오류가 발생했습니다.')
        if created:
            realized_pnl.refresh(conn, username)
        conn.commit()

        c.execute("SELECT * FROM entries WHERE id = ?", (entry_id,))
        row = c.fetchone()

    if created:
        statscache.invalidate(username)

    body = entry_to_response(row)
    if warnings:
        body['warnings'] = warnings
    return jsonify(body), (201 if created else 200)


@bp.route('/trades/batch', methods=['POST'])
@require_token(SCOPE_TRADES_WRITE)
def create_trades_batch(username, scopes):
    payload = request.get_json(silent=True)
    default_source = None
    if isinstance(payload, dict):
        trades = payload.get('trades')
        default_source = payload.get('source')
    else:
        trades = payload  # 레거시 v1: 최상위 배열

    if not isinstance(trades, list):
        return _err(400, 'INVALID_REQUEST',
                    "요청 본문은 배열 또는 {\"trades\": [...]} 형태여야 합니다.")
    if not trades:
        return jsonify({'status': 'success', 'inserted': 0, 'skipped': 0,
                        'failed': 0, 'results': [], 'errors': None}), 200
    if len(trades) > MAX_BATCH_ITEMS:
        return _err(413, 'PAYLOAD_TOO_LARGE',
                    f'한 번에 최대 {MAX_BATCH_ITEMS}건까지 전송할 수 있습니다.',
                    received=len(trades), maximum=MAX_BATCH_ITEMS)

    results = []
    inserted = skipped = failed = 0
    legacy_errors = []
    seen_exec_ids = set()  # 같은 배치 안의 중복도 걸러낸다

    with db_conn(write=True) as conn:
        c = conn.cursor()
        mappings = accounts.load(conn, username)

        for index, item in enumerate(trades):
            try:
                entry = build_entry(c, username, item, mappings,
                                    default_source=default_source)
            except ValidationError as e:
                failed += 1
                legacy_errors.append(e.message)
                results.append({'index': index, 'status': 'failed', 'id': None,
                                'brokerExecutionId': (item or {}).get('brokerExecutionId')
                                if isinstance(item, dict) else None,
                                'errorCode': e.code, 'error': e.message})
                continue

            exec_id = entry.get('brokerExecutionId') or ''
            if exec_id and exec_id in seen_exec_ids:
                skipped += 1
                results.append({'index': index, 'status': 'duplicate', 'id': None,
                                'brokerExecutionId': exec_id,
                                'errorCode': None, 'error': None})
                continue

            try:
                entry_id, created, warnings = _insert_trade(c, username, entry)
            except sqlite3.Error as e:
                failed += 1
                message = f'저장 실패: {e}'
                legacy_errors.append(message)
                results.append({'index': index, 'status': 'failed', 'id': None,
                                'brokerExecutionId': exec_id or None,
                                'errorCode': 'INTERNAL_ERROR', 'error': message})
                continue

            if exec_id:
                seen_exec_ids.add(exec_id)
            if created:
                inserted += 1
            else:
                skipped += 1
            row = {'index': index, 'status': 'created' if created else 'duplicate',
                   'id': str(entry_id), 'brokerExecutionId': exec_id or None,
                   'errorCode': None, 'error': None}
            if warnings:
                row['warnings'] = warnings
            results.append(row)

        if inserted:
            realized_pnl.refresh(conn, username)
        conn.commit()

    if inserted:
        statscache.invalidate(username)

    return jsonify({
        'status': 'success' if not failed else 'partial',
        'inserted': inserted,
        'skipped': skipped,
        'failed': failed,
        'results': results,
        'errors': legacy_errors or None,
    }), (201 if inserted else 200)


@bp.route('/trades', methods=['GET'])
@require_token(SCOPE_TRADES_READ)
def list_trades(username, scopes):
    args = request.args
    conditions = ["username = ?", "type = 'trade'"]
    params = [username]

    try:
        from_val, to_val = (_query_time(args.get(field), field) for field in ('from', 'to'))
        exclusive_end = bool(args.get('to') and len(args['to'].strip()) == 10)
        if from_val and to_val and (from_val > to_val or exclusive_end and from_val == to_val):
            raise ValidationError('INVALID_FIELD', '조회 시작은 종료 시각보다 늦을 수 없습니다.', 'from')
    except ValidationError as exc:
        return _err(400, exc.code, exc.message, field=exc.field)
    if from_val:
        conditions.append(f'{_TRADE_TIME_SQL} >= julianday(?)')
        params.append(from_val.isoformat())
    if to_val:
        conditions.append(f"{_TRADE_TIME_SQL} {'<' if exclusive_end else '<='} julianday(?)")
        params.append(to_val.isoformat())
    if args.get('source'):
        conditions.append("source = ?")
        params.append(args.get('source'))

    # ⭐️ 명시적으로 준 경우에만 거른다. 안 주면 종전대로 모의·실거래를 함께 돌려준다
    #    (화면의 통합 표시를 위해 분리를 해제한 결정을 그대로 둔다).
    sim_arg = args.get('isSimulated')
    if sim_arg is not None:
        conditions.append("COALESCE(isSimulated, 0) = ?")
        params.append(1 if str(sim_arg).strip().lower() in ('1', 'true', 'yes') else 0)

    cursor = args.get('cursor')
    if cursor:
        try:
            conditions.append("id < ?")
            params.append(int(cursor))
        except (TypeError, ValueError):
            return _err(400, 'INVALID_FIELD', 'cursor 값이 올바르지 않습니다.', field='cursor')

    try:
        limit = min(max(int(args.get('limit', 100)), 1), 500)
    except (TypeError, ValueError):
        limit = 100

    with db_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM entries WHERE " + " AND ".join(conditions)
                  + " ORDER BY id DESC LIMIT ?", params + [limit + 1])
        rows = c.fetchall()

    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = str(rows[-1]['id']) if has_more and rows else None

    return jsonify({
        'trades': [entry_to_response(r) for r in rows],
        'nextCursor': next_cursor,
    }), 200


@bp.route('/trades/last-sync', methods=['GET'])
@require_token(SCOPE_TRADES_READ)
def last_sync(username, scopes):
    args = request.args
    conditions = ["username = ?", "type = 'trade'"]
    params = [username]

    if args.get('source'):
        conditions.append("source = ?")
        params.append(args.get('source'))
    if args.get('account'):
        conditions.append("REPLACE(subAccount, '-', '') = ?")
        params.append(args.get('account').replace('-', ''))

    # ⭐️ 백필 기준점은 클라이언트가 요청한 축으로 좁혀 준다. 거르지 않으면 한쪽의 최신
    #    체결이 다른 쪽의 기준점을 앞당기고, 뒤처진 쪽의 누락 구간이 스캔에서 통째로
    #    빠진다 — 그 체결은 **영영 올라오지 않는다**(재시도 로직이 손대지 못하는 구멍).
    #    지금은 가상투자가 계정 자체를 따로 쓰므로 1차 방어는 username 이다. 이건 그
    #    뒤에 서는 두 번째 방어이자, 클라이언트가 보내는 파라미터가 말한 대로 동작하게
    #    하는 것이다(예전엔 읽고 버렸다). 값을 안 주면 종전대로 전체를 본다.
    sim_arg = args.get('isSimulated')
    if sim_arg is not None:
        conditions.append("COALESCE(isSimulated, 0) = ?")
        params.append(1 if str(sim_arg).strip().lower() in ('1', 'true', 'yes') else 0)

    where = " AND ".join(conditions)
    with db_conn() as conn:
        c = conn.cursor()
        c.execute(f"SELECT COUNT(*) AS cnt FROM entries WHERE {where}", params)
        count = c.fetchone()['cnt']
        c.execute(
            f"SELECT executedAtUtc, rawDate, date, brokerExecutionId FROM entries WHERE {where} "
            f"ORDER BY {_TRADE_TIME_SQL} DESC, id DESC LIMIT 1", params)
        row = c.fetchone()

    last_time = row['executedAtUtc'] if row else None
    if row and not last_time:
        try:
            parsed, _ = _parse_executed_at(row['rawDate'] or row['date'])
            last_time = parsed.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        except ValidationError:
            last_time = None
    return jsonify({
        'lastExecutedAt': last_time,
        'lastBrokerExecutionId': (row['brokerExecutionId'] or None) if row else None,
        'count': count,
    }), 200


@bp.route('/trades/by-exec-id/<path:broker_execution_id>', methods=['GET'])
@require_token(SCOPE_TRADES_READ)
def get_trade_by_exec_id(username, scopes, broker_execution_id):
    with db_conn() as conn:
        c = conn.cursor()
        row = _fetch_by_exec_id(c, username, broker_execution_id)
    if row is None:
        return _err(404, 'NOT_FOUND', '해당 멱등키의 기록이 없습니다.')
    return jsonify(entry_to_response(row)), 200


# PATCH 로 갱신 가능한 API 필드 -> DB 컬럼
_PATCHABLE = {
    'price': 'price',
    'volume': 'quantity',
    'status': 'tradeStatus',
    'confidence': 'confidence',
    'realizedPnl': 'realizedPnl',
    'realizedPnlBasis': 'realizedPnlBasis',
    'realizedPnlRate': 'realizedPnlRate',
    'fee': 'fee',
    'tax': 'tax',
    'strategyScore': 'strategyScore',
    'stopLossRate': 'stopLossRate',
    'memo': 'thoughts',
    'name': 'stockName',
}


@bp.route('/trades/<trade_id>', methods=['PATCH'])
@require_token(SCOPE_TRADES_WRITE)
def patch_trade(username, scopes, trade_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not data:
        return _err(400, 'INVALID_REQUEST', '갱신할 필드를 1개 이상 보내야 합니다.')

    changes = {}
    executed_dt = None
    try:
        for field, value in data.items():
            column = _PATCHABLE.get(field)
            if column is None:
                continue
            if field == 'price':
                value = _num(value, 'price', allow_none=False, minimum=0)
            elif field == 'volume':
                value = _num(value, 'volume', allow_none=False, exclusive_min=0)
            elif field in ('realizedPnl', 'realizedPnlRate', 'strategyScore', 'stopLossRate'):
                value = _num(value, field)
            elif field == 'realizedPnlBasis':
                from app.domain.validation import PNL_BASES
                if value not in PNL_BASES:
                    raise ValidationError('INVALID_FIELD', 'realizedPnlBasis는 gross, net, unknown 중 하나여야 합니다.', field)
            elif field in ('fee', 'tax'):
                value = _num(value, field, minimum=0)
            elif field == 'status':
                value = _normalize_enum(value, _VALID_STATUS, 'FILLED', 'status')
            elif field == 'confidence':
                value = _normalize_enum(value, _VALID_CONFIDENCE, 'CONFIRMED', 'confidence')
            elif field == 'memo':
                value = _text(value, 'memo', 5000)
            elif field == 'name':
                value = _text(value, 'name', 100)
            changes[column] = value

        if 'executedAt' in data:
            executed_dt, _ = _parse_executed_at(data['executedAt'])
            local_kst = executed_dt.astimezone(KST)
            changes['rawDate'] = local_kst.strftime('%Y-%m-%dT%H:%M:%S')
            changes['date'] = local_kst.strftime('%Y-%m-%d')
            changes['executedAtUtc'] = executed_dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

        if 'tags' in data:
            tags = data['tags']
            if not isinstance(tags, list):
                raise ValidationError('INVALID_FIELD', 'tags 는 문자열 배열이어야 합니다.', 'tags')
            changes['tags'] = ','.join(_text(t, 'tags', 50) for t in tags[:30])
    except ValidationError as e:
        return _err(400, e.code, e.message, field=e.field)

    if not changes:
        return _err(400, 'INVALID_REQUEST', '갱신 가능한 필드가 없습니다.',
                    allowed=sorted(list(_PATCHABLE) + ['executedAt', 'tags']))

    changes['updatedAt'] = _now_kst_str()

    # ⭐️ 정정은 웹 수정과 같은 서비스를 지난다. 매수를 취소·축소하면 이미 한 매도에,
    #    매도를 키우면 그 매도에 '검토 필요'가 붙는다 — 막지는 않는다(유실 금지).
    try:
        row, warnings = entry_service.patch_bot_trade(username, trade_id, changes, executed_at=executed_dt)
    except entry_service.EntryNotFound:
        return _err(404, 'NOT_FOUND', '해당 기록이 없거나 API 로 수정할 수 없는 기록입니다.')

    body = entry_to_response(row)
    if warnings:
        body['warnings'] = warnings
    return jsonify(body), 200


@bp.route('/trades/<trade_id>', methods=['DELETE'])
@require_token(SCOPE_TRADES_WRITE)
def delete_trade(username, scopes, trade_id):
    try:
        warnings = entry_service.delete_bot_trade(username, trade_id)
    except entry_service.EntryNotFound:
        return _err(404, 'NOT_FOUND', '해당 기록이 없거나 API 로 삭제할 수 없는 기록입니다.')
    # 204 는 본문이 없다. 표시된 매도는 웹 화면의 '검토 필요' 배지로 드러난다.
    for message in warnings:
        _log().warning(f"[trading_api] 봇 삭제로 초과 매도 발생 ({username}): {message}")
    return '', 204


@bp.route('/positions/opening', methods=['POST'])
@require_token(SCOPE_TRADES_WRITE)
def create_opening_positions(username, scopes):
    """연동 시작 시점의 보유 잔고를 매수 기록으로 등록합니다.

    이 단계가 없으면 연동 이전부터 들고 있던 종목의 첫 매도가 전부
    '매수 기록 없음' 경고를 달고 보유 수량 집계가 음수로 내려갑니다.
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _err(400, 'INVALID_REQUEST', '요청 본문이 JSON 객체가 아닙니다.')

    as_of = payload.get('asOf')
    positions = payload.get('positions')
    if not as_of or not isinstance(positions, list):
        return _err(400, 'MISSING_FIELD', 'asOf 와 positions 가 필요합니다.')
    if len(positions) > MAX_BATCH_ITEMS:
        return _err(413, 'PAYLOAD_TOO_LARGE',
                    f'한 번에 최대 {MAX_BATCH_ITEMS}건까지 등록할 수 있습니다.')

    is_simulated = bool(payload.get('isSimulated'))
    env = 'SIM' if is_simulated else 'REAL'
    source = payload.get('source') or 'opening-balance'

    results = []
    inserted = skipped = failed = 0
    with db_conn(write=True) as conn:
        c = conn.cursor()
        mappings = accounts.load(conn, username)

        for index, pos in enumerate(positions):
            if not isinstance(pos, dict):
                failed += 1
                results.append({'index': index, 'status': 'failed', 'id': None,
                                'errorCode': 'INVALID_REQUEST',
                                'error': '각 항목은 JSON 객체여야 합니다.'})
                continue
            # ⭐️ 멱등키(brokerExecutionId)에 티커가 그대로 박히므로 여기서도 접어야
            #    한다. 'aapl' 과 'AAPL' 이 서로 다른 키가 되면 같은 기초잔고가 두 번
            #    들어온다.
            symbol = entry_logic.normalize_stock_code(pos.get('symbol'))
            trade_input = {
                'symbol': symbol,
                'side': 'BUY',
                'price': pos.get('avgPrice'),
                'volume': pos.get('volume'),
                'executedAt': f'{as_of}T00:00:00+09:00',
                'brokerExecutionId': f'OPENING:{env}:{as_of}:{symbol}',
                'isSimulated': is_simulated,
                # 기초잔고는 시스템 트레이딩이 낸 체결이 아니라 '연동 이전부터 들고
                # 있던 것'이다. 예전에는 분류가 비면 '시스템'으로 채워져 자동매매
                # 성과에 섞여 들어갔다.
                'isSystem': False,
                'orderOrigin': 'BACKFILL',
                'source': source,
                'name': pos.get('name'),
                'currency': pos.get('currency'),
                'exchange': pos.get('exchange'),
                'assetType': pos.get('assetType'),
                'brokerAccount': pos.get('brokerAccount'),
                'subAccount': pos.get('subAccount'),
                'memo': pos.get('memo') or f'연동 시작 기초잔고 ({as_of} 기준)',
                'tags': ['기초잔고'],
            }
            try:
                entry = build_entry(c, username, trade_input, mappings)
                entry['brokerExecutionId'] = opening_execution_id(c, username, entry)
                entry_id, created, _warn = _insert_trade(c, username, entry)
            except ValidationError as e:
                failed += 1
                results.append({'index': index, 'status': 'failed', 'id': None,
                                'errorCode': e.code, 'error': e.message})
                continue
            except sqlite3.Error as e:
                failed += 1
                results.append({'index': index, 'status': 'failed', 'id': None,
                                'errorCode': 'INTERNAL_ERROR', 'error': str(e)})
                continue

            if created:
                inserted += 1
            else:
                skipped += 1
            results.append({'index': index,
                            'status': 'created' if created else 'duplicate',
                            'id': str(entry_id), 'errorCode': None, 'error': None})
        if inserted:
            realized_pnl.refresh(conn, username)
        conn.commit()

    if inserted:
        statscache.invalidate(username)

    return jsonify({
        'status': 'success' if not failed else 'partial',
        'inserted': inserted, 'skipped': skipped, 'failed': failed,
        'results': results, 'errors': None,
    }), (201 if inserted else 200)

