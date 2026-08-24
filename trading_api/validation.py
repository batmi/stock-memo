"""봇이 보낸 JSON 을 entries 행으로 옮기기 전의 정규화·검증.

여기서 던지는 ValidationError 는 라우트가 잡아 400 으로 바꾼다. 값의 형태만
보고 판단할 수 있는 것들만 담고, "이 매도가 보유 수량을 넘는가" 같은 **기록
전체를 봐야 아는 무결성**은 entry_logic 이 본다.
"""

from datetime import datetime

from app.services import accounts

from .common import KST, ZoneInfo

# ══════════════════════════════════════════════════════════════════════

# 거래소 -> 현지 시간대. 거래일(tradeDate) 귀속에 쓴다.
# 미국은 프리(04:00)~애프터(20:00)가 모두 같은 ET 날짜이므로 ET 기준 날짜가 곧 거래일이다.
_EXCHANGE_TZ = {
    'KRX': 'Asia/Seoul', 'NXT': 'Asia/Seoul', 'KOSPI': 'Asia/Seoul',
    'KOSDAQ': 'Asia/Seoul', 'KONEX': 'Asia/Seoul',
    'NASDAQ': 'America/New_York', 'NAS': 'America/New_York',
    'NYSE': 'America/New_York', 'NYS': 'America/New_York',
    'AMEX': 'America/New_York', 'AMS': 'America/New_York',
    'BAQ': 'America/New_York', 'BAY': 'America/New_York', 'BAA': 'America/New_York',
    'TSE': 'Asia/Tokyo', 'HKEX': 'Asia/Hong_Kong', 'SEHK': 'Asia/Hong_Kong',
    'SSE': 'Asia/Shanghai', 'SZSE': 'Asia/Shanghai',
}

_TRADE_CLASS_MAP = {
    1: '장기투자', 2: '중기투자', 3: '단기스윙', 4: '단타(스캘핑)',
    5: '배당투자', 6: '공모주', 7: '시스템', 8: '기타',
}
_VALID_TRADE_CLASSES = set(_TRADE_CLASS_MAP.values())

_VALID_STATUS = {'FILLED', 'PARTIALLY_FILLED', 'CANCELED', 'SUBMITTED'}
_VALID_CONFIDENCE = {'CONFIRMED', 'ESTIMATED'}
_VALID_ORIGIN = {'AUTO', 'MANUAL', 'RESERVED', 'EXTERNAL', 'BACKFILL'}


class ValidationError(Exception):
    def __init__(self, code, message, field=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field


def _parse_executed_at(value):
    """체결 시각 문자열을 파싱해 (aware datetime, 원본오프셋여부) 를 돌려줍니다.

    오프셋이 없으면 KST 로 간주합니다. 해외 체결에 오프셋을 빠뜨리면 거래일이
    어긋나므로 클라이언트는 반드시 오프셋을 넣어야 합니다.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValidationError('MISSING_FIELD', 'executedAt 이 필요합니다.', 'executedAt')

    raw = value.strip().replace('Z', '+00:00').replace(' ', 'T', 1)
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M', '%Y-%m-%d'):
            try:
                dt = datetime.strptime(raw[:len(datetime.now().strftime(fmt))], fmt)
                break
            except ValueError:
                continue
        else:
            raise ValidationError(
                'INVALID_FIELD',
                f"executedAt 형식이 올바르지 않습니다: {value!r} "
                "(예: 2026-08-01T09:30:00+09:00)", 'executedAt')

    has_offset = dt.tzinfo is not None
    if not has_offset:
        dt = dt.replace(tzinfo=KST)
    return dt, has_offset


def _trade_date_for(dt_aware, exchange):
    """거래소 현지 기준 거래일(YYYY-MM-DD)."""
    tz_name = _EXCHANGE_TZ.get((exchange or '').strip().upper())
    if tz_name and ZoneInfo is not None:
        try:
            return dt_aware.astimezone(ZoneInfo(tz_name)).strftime('%Y-%m-%d')
        except Exception:
            pass
    # tz 데이터가 없거나 미등록 거래소면 요청에 실려 온 오프셋의 현지 날짜를 쓴다.
    return dt_aware.strftime('%Y-%m-%d')


def _num(value, field, *, allow_none=True, minimum=None, exclusive_min=None):
    if value is None or value == '':
        if allow_none:
            return None
        raise ValidationError('MISSING_FIELD', f'{field} 이(가) 필요합니다.', field)
    if isinstance(value, bool):
        raise ValidationError('INVALID_FIELD', f'{field} 은(는) 숫자여야 합니다.', field)
    try:
        num = float(value)
    except (TypeError, ValueError) as e:
        raise ValidationError('INVALID_FIELD', f'{field} 은(는) 숫자여야 합니다.', field) from e
    if num != num or num in (float('inf'), float('-inf')):
        raise ValidationError('INVALID_FIELD', f'{field} 값이 유효하지 않습니다.', field)
    if minimum is not None and num < minimum:
        raise ValidationError('INVALID_FIELD', f'{field} 은(는) {minimum} 이상이어야 합니다.', field)
    if exclusive_min is not None and num <= exclusive_min:
        raise ValidationError('INVALID_FIELD', f'{field} 은(는) {exclusive_min} 보다 커야 합니다.', field)
    return num


def _text(value, field, max_length, default=''):
    if value is None:
        return default
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    if len(value) > max_length:
        raise ValidationError('INVALID_FIELD',
                              f'{field} 길이가 최대 {max_length}자를 초과했습니다.', field)
    return value


def _normalize_trade_class(value, *, is_system=None, fallback=''):
    """매매 분류를 확정한다.

    **비어 있다고 '시스템'으로 채우지 않는다.** 예전에는 그렇게 했는데, HTS 는
    자기 계좌에서 일어난 체결을 전부 보고한다 — 토스 앱이나 증권사 HTS 에서 사람이
    직접 낸 주문까지 포함해서다. 그것들이 전부 '시스템'으로 찍혀 실제 자동매매 성과와
    수동 매매가 한 덩어리가 됐다.

    is_system: 봇이 알려준 '시스템 트레이딩이 낸 주문인가'. True 면 분류를 '시스템'으로
      확정한다. False/None 이면 아래 폴백으로 내려간다.
    fallback: 분류를 못 정했을 때 쓸 값 (보통 같은 종목의 직전 기록에서 상속한 분류).
    """
    if is_system:
        return '시스템'
    if value is None or value == '' or isinstance(value, bool):
        return fallback
    # 숫자 코드 또는 숫자 문자열 → 이름으로 치환 (v1 하위 호환)
    try:
        code = int(value)
        return _TRADE_CLASS_MAP.get(code, '기타')
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text if text in _VALID_TRADE_CLASSES else text or fallback


def _inherit_trade_class(c, username, symbol):
    """같은 사용자·종목의 직전 기록에서 분류를 물려받는다. 없으면 빈 문자열.

    **'시스템'은 물려받지 않는다.** 예전 버전이 HTS 발 기록을 전부 '시스템'으로
    저장해 둬서, 그대로 상속하면 새로 들어오는 외부 체결까지 계속 '시스템'이 된다
    — 고치려던 오염을 상속으로 영구화하는 셈이다. 상속은 사람이 실제로 뜻을 담아
    골랐을 법한 분류(장기투자·배당투자 등)에만 걸린다.
    """
    if not symbol:
        return ''
    c.execute(
        "SELECT tradeClass FROM entries "
        "WHERE username = ? AND stockCode = ? "
        "  AND tradeClass IS NOT NULL AND tradeClass != '' AND tradeClass != '시스템' "
        "ORDER BY id DESC LIMIT 1",
        (username, symbol))
    row = c.fetchone()
    return (row['tradeClass'] or '') if row else ''


def _normalize_enum(value, valid, default, field):
    if value is None or value == '':
        return default
    text = str(value).strip().upper()
    if text not in valid:
        raise ValidationError('INVALID_FIELD',
                              f"{field} 값이 올바르지 않습니다: {value!r} "
                              f"(허용: {', '.join(sorted(valid))})", field)
    return text


def _resolve_account(username, data, mappings):
    """계좌 코드/번호를 등록된 매핑 정보로 치환합니다."""
    raw_broker = _text(data.get('brokerAccount'), 'brokerAccount', 50)
    raw_sub = _text(data.get('subAccount'), 'subAccount', 50)

    # ⭐️ 계좌번호 정규화·조회는 공용 도메인 규칙이라 accounts 모듈이 소유한다.
    #    (예전에는 이 규칙의 사본이 여기 비공개 함수로 있었다)
    mapped = mappings.get('accounts') or {}
    matched_key, acc_info = accounts.find_account_mapping(mapped, raw_sub)

    # ⭐️ 매핑이 잡히면 사용자가 등록한 표기(하이픈 포함)를 그대로 저장한다.
    #    HTS 가 보낸 표기를 그대로 두면 같은 계좌가 두 가지 번호로 쌓인다.
    #    매핑이 없을 때만 기존과 동일하게 하이픈을 제거한 형태로 남긴다.
    sub_account = matched_key if matched_key else raw_sub.replace('-', '')

    if isinstance(acc_info, dict):
        broker = acc_info.get('broker_name') or raw_broker
        account_name = acc_info.get('alias') or _text(data.get('accountName'), 'accountName', 100)
    else:
        broker = (mappings.get('brokers') or {}).get(raw_broker, raw_broker)
        account_name = acc_info if isinstance(acc_info, str) and acc_info else \
            _text(data.get('accountName'), 'accountName', 100)
    return broker, sub_account, account_name

