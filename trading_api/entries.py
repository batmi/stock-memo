"""봇 체결의 저장 경로 — 입력 dict → entries 행 → 응답 dict.

⭐️ 웹 UI 의 저장 경로(api.create_entry)와의 결정적 차이: **매도 무결성 위반을
   차단하지 않는다.** 봇 체결을 400 으로 되돌리면 재시도해도 계속 실패해 그
   체결이 영구 유실되므로, 저장하되 needsReview 로 표시해 사람이 확인하게 한다.
   INSERT 컬럼 목록 자체는 entry_logic 이 단독으로 소유한다(웹과 공용).
"""

import sqlite3
from datetime import timezone

import entry_logic

from .common import KST, _now_kst_str
from .validation import (
    ValidationError, _inherit_trade_class, _normalize_enum, _normalize_trade_class,
    _num, _parse_executed_at, _resolve_account, _text, _trade_date_for,
    _VALID_CONFIDENCE, _VALID_ORIGIN, _VALID_STATUS,
)


def _lookup_stock_name(c, username, symbol):
    """종목명 미제공 시 **같은 사용자의** 기존 기록에서 조회.

    (v1 은 username 조건이 없어 다른 사용자의 종목명이 새어 나왔다.)
    """
    c.execute(
        "SELECT stockName FROM entries "
        "WHERE username = ? AND stockCode = ? AND stockName IS NOT NULL AND stockName != '' "
        "ORDER BY id DESC LIMIT 1",
        (username, symbol))
    row = c.fetchone()
    return row['stockName'] if row else symbol

def build_entry(c, username, data, mappings, *, default_source=None):
    """API 입력(dict)을 entries 행(dict)으로 정규화합니다.

    검증 실패 시 ValidationError 를 던집니다. (매도 무결성은 여기서 보지 않음)
    """
    if not isinstance(data, dict):
        raise ValidationError('INVALID_REQUEST', '각 항목은 JSON 객체여야 합니다.')

    for field in ('symbol', 'side', 'price', 'volume', 'executedAt'):
        if data.get(field) is None or data.get(field) == '':
            raise ValidationError('MISSING_FIELD', f'필수 파라미터가 누락되었습니다: {field}', field)

    symbol = _text(data['symbol'], 'symbol', 32)
    if not symbol:
        raise ValidationError('MISSING_FIELD', '필수 파라미터가 누락되었습니다: symbol', 'symbol')

    side = str(data['side']).strip().upper()
    if side not in ('BUY', 'SELL'):
        raise ValidationError('INVALID_FIELD', "side 는 BUY 또는 SELL 이어야 합니다.", 'side')

    price = _num(data['price'], 'price', allow_none=False, minimum=0)
    volume = _num(data['volume'], 'volume', allow_none=False, exclusive_min=0)

    executed_dt, _ = _parse_executed_at(data['executedAt'])
    exchange = _text(data.get('exchange'), 'exchange', 20)

    # rawDate/date 는 웹 UI 의 표시·정렬 기준(KST 로컬, 오프셋 없음)을 유지한다.
    # 오프셋이 섞이면 문자열 MAX/정렬이 깨지므로 UTC 는 별도 컬럼에 둔다.
    local_kst = executed_dt.astimezone(KST)
    raw_date = local_kst.strftime('%Y-%m-%dT%H:%M:%S')

    trade_status = _normalize_enum(data.get('status'), _VALID_STATUS, 'FILLED', 'status')
    confidence = _normalize_enum(data.get('confidence'), _VALID_CONFIDENCE, 'CONFIRMED', 'confidence')
    order_origin = _normalize_enum(data.get('orderOrigin'), _VALID_ORIGIN, '', 'orderOrigin') \
        if data.get('orderOrigin') else ''

    # ⭐️ isSystem 은 3상태다 — True(자동매매), False(사람이 낸 주문), None(봇이 모름).
    #    None 과 False 를 뭉개면 분류 폴백이 무너지므로 여기서 구분해 둔다.
    is_system = data.get('isSystem')
    is_system = None if is_system is None else bool(is_system)

    trade_class = _normalize_trade_class(data.get('tradeClass'), is_system=is_system)
    if not trade_class and not is_system:
        trade_class = _inherit_trade_class(c, username, symbol)

    is_simulated = 1 if data.get('isSimulated') else 0

    tags = data.get('tags') or []
    if not isinstance(tags, list):
        raise ValidationError('INVALID_FIELD', 'tags 는 문자열 배열이어야 합니다.', 'tags')
    if len(tags) > 30:
        raise ValidationError('INVALID_FIELD', 'tags 는 최대 30개까지 허용됩니다.', 'tags')
    tags = [_text(t, 'tags', 50) for t in tags]
    if trade_class and trade_class not in tags:
        tags.append(trade_class)
    if confidence == 'ESTIMATED' and '추정체결' not in tags:
        tags.append('추정체결')
    # ⭐️ 모의투자 체결은 배지뿐 아니라 태그로도 남긴다. 배지는 눈으로만 구분되지만
    #    태그는 검색·필터에 걸리므로 '모의' 기록만 따로 모아 볼 수 있다.
    if is_simulated and '모의' not in tags:
        tags.append('모의')

    stock_name = _text(data.get('name'), 'name', 100)
    if not stock_name:
        stock_name = _lookup_stock_name(c, username, symbol)

    broker, sub_account, account_name = _resolve_account(username, data, mappings)

    source = _text(data.get('source'), 'source', 100) or _text(default_source, 'source', 100)

    now = _now_kst_str()
    return {
        'type': 'trade',
        'stockName': stock_name,
        'stockCode': symbol,
        'title': '',
        'thoughts': _text(data.get('memo'), 'memo', 5000),
        'date': local_kst.strftime('%Y-%m-%d'),
        'rawDate': raw_date,
        'brokerAccount': broker,
        'subAccount': sub_account,
        'accountName': account_name,
        'tradeClass': trade_class,
        'tradeType': '매수' if side == 'BUY' else '매도',
        'price': price,
        'quantity': volume,
        'tags': ','.join(tags),
        'createdAt': now,
        'updatedAt': now,
        'brokerExecutionId': _text(data.get('brokerExecutionId'), 'brokerExecutionId', 200),
        'currency': _text(data.get('currency'), 'currency', 10) or 'KRW',
        'exchange': exchange,
        'assetType': _text(data.get('assetType'), 'assetType', 20),
        # ── 확장 컬럼 ──
        'isSimulated': is_simulated,
        'tradeStatus': trade_status,
        'confidence': confidence,
        'orderOrigin': order_origin,
        'source': source,
        'orderId': _text(data.get('orderId'), 'orderId', 100),
        'originalOrderId': _text(data.get('originalOrderId'), 'originalOrderId', 100),
        'realizedPnl': _num(data.get('realizedPnl'), 'realizedPnl'),
        'realizedPnlRate': _num(data.get('realizedPnlRate'), 'realizedPnlRate'),
        'fee': _num(data.get('fee'), 'fee', minimum=0),
        'tax': _num(data.get('tax'), 'tax', minimum=0),
        'strategyScore': _num(data.get('strategyScore'), 'strategyScore'),
        'stopLossRate': _num(data.get('stopLossRate'), 'stopLossRate'),
        'executedAtUtc': executed_dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'tradeDate': _trade_date_for(executed_dt, exchange),
        'needsReview': 0,
        # None 을 그대로 저장해 '모른다'를 남긴다 (0 으로 눕히면 False 와 섞인다).
        'isSystem': None if is_system is None else (1 if is_system else 0),
    }


def entry_to_response(row):
    """DB 행을 API 응답(TradeRecord)으로 변환합니다."""
    row = dict(row)
    tags = [t for t in (row.get('tags') or '').split(',') if t]
    return {
        'id': str(row.get('id')),
        'symbol': row.get('stockCode') or '',
        'name': row.get('stockName') or '',
        'side': 'BUY' if row.get('tradeType') == '매수' else 'SELL',
        'price': row.get('price'),
        'volume': row.get('quantity'),
        'executedAt': row.get('executedAtUtc') or row.get('rawDate'),
        'tradeDate': row.get('tradeDate'),
        'brokerExecutionId': row.get('brokerExecutionId') or None,
        'isSimulated': bool(row.get('isSimulated')),
        'isSystem': None if row.get('isSystem') is None else bool(row.get('isSystem')),
        'status': row.get('tradeStatus') or 'FILLED',
        'confidence': row.get('confidence') or 'CONFIRMED',
        'orderOrigin': row.get('orderOrigin') or None,
        'source': row.get('source') or None,
        'orderId': row.get('orderId') or None,
        'originalOrderId': row.get('originalOrderId') or None,
        'realizedPnl': row.get('realizedPnl'),
        'realizedPnlRate': row.get('realizedPnlRate'),
        'fee': row.get('fee'),
        'tax': row.get('tax'),
        'strategyScore': row.get('strategyScore'),
        'stopLossRate': row.get('stopLossRate'),
        'memo': row.get('thoughts') or '',
        'tradeClass': row.get('tradeClass') or '',
        'brokerAccount': row.get('brokerAccount') or '',
        'subAccount': row.get('subAccount') or '',
        'accountName': row.get('accountName') or '',
        'currency': row.get('currency') or 'KRW',
        'exchange': row.get('exchange') or '',
        'assetType': row.get('assetType') or '',
        'tags': tags,
        'needsReview': bool(row.get('needsReview')),
        'createdAt': row.get('createdAt'),
        'updatedAt': row.get('updatedAt'),
    }


def _fetch_by_exec_id(c, username, exec_id):
    c.execute("SELECT * FROM entries WHERE username = ? AND brokerExecutionId = ?",
              (username, exec_id))
    return c.fetchone()


def _insert_trade(c, username, entry):
    """멱등 INSERT. (기록 id, 신규여부, 경고목록) 을 반환합니다.

    매도 무결성 위반은 차단하지 않고 needsReview 로 표시만 합니다 —
    봇 체결을 400 으로 되돌리면 재시도해도 계속 실패해 그 기록이 영구 유실됩니다.
    """
    warnings = []
    integrity = entry_logic.check_sell_integrity(c, username, entry)
    if integrity:
        warnings.append(integrity[1])
        entry['needsReview'] = 1

    exec_id = entry.get('brokerExecutionId') or ''
    if exec_id:
        existing = _fetch_by_exec_id(c, username, exec_id)
        if existing is not None:
            return existing['id'], False, warnings

    try:
        entry_logic.insert_entry(c, username, entry)
    except sqlite3.IntegrityError:
        # UNIQUE 경합 — 다른 요청이 방금 같은 멱등키를 넣었다.
        if exec_id:
            existing = _fetch_by_exec_id(c, username, exec_id)
            if existing is not None:
                return existing['id'], False, warnings
        raise
    return c.lastrowid, True, warnings


def _load_bot_entry(c, username, trade_id):
    """봇이 만든 기록만 반환합니다 (웹 UI 수동 입력은 API 로 건드리지 않는다)."""
    c.execute("SELECT * FROM entries WHERE id = ? AND username = ?", (trade_id, username))
    row = c.fetchone()
    if row is None:
        return None
    if not (row['brokerExecutionId'] or row['source']):
        return None
    return row

