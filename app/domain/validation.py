"""웹과 봇 입력에서 공통으로 사용하는 숫자 검증과 웹 기록 계약."""

from datetime import datetime
import math
import re


def finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError('숫자여야 합니다.')
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError('숫자여야 합니다.') from exc
    if not math.isfinite(number):
        raise ValueError('유한한 숫자여야 합니다.')
    return number


def normalize_currency(value, default='KRW'):
    if value is None or value == '':
        return default
    if not isinstance(value, str):
        raise ValueError('통화는 KRW, USD 같은 3자리 코드여야 합니다.')
    value = value.strip().upper()
    if not value:
        return default
    if not re.fullmatch('[A-Z]{3}', value):
        raise ValueError('통화는 KRW, USD 같은 3자리 코드여야 합니다.')
    return value


TEXT_LIMITS = {
    'type': 20, 'tradeType': 20, 'stockName': 200, 'stockCode': 32,
    'title': 1000, 'thoughts': 16 * 1024 * 1024, 'tags': 5000,
    'brokerAccount': 200, 'subAccount': 200, 'accountName': 200, 'tradeClass': 200,
    'currency': 10, 'exchange': 20, 'assetType': 20, 'brokerExecutionId': 200,
    'rawDate': 64, 'date': 100, 'createdAt': 64, 'updatedAt': 64,
    'attachedImage': 16 * 1024 * 1024, 'attachedFile': 16 * 1024 * 1024,
    'attachedFileName': 500,
}

OPTIONAL_NUMBERS = frozenset({'realizedPnl', 'realizedPnlRate', 'fee', 'tax', 'strategyScore', 'stopLossRate'})
PNL_SOURCES = ('recorded', 'reconstructed', 'unavailable')


def normalize_trade_numbers(entry):
    """선택 숫자의 누락과 명시적인 0을 구분한다. 웹·복원이 같은 검증을 쓴다."""
    for field in OPTIONAL_NUMBERS:
        if field not in entry or entry[field] is None:
            continue
        if entry[field] == '':
            entry[field] = None
            continue
        try:
            entry[field] = finite_number(entry[field])
        except ValueError as exc:
            raise ValueError(f'{field}: {exc}') from exc
        if field in ('fee', 'tax') and entry[field] < 0:
            raise ValueError(f'{field}: 0 이상이어야 합니다.')
    source = entry.get('realizedPnlSource')
    if source is not None and source not in PNL_SOURCES:
        raise ValueError('realizedPnlSource: 손익 출처가 올바르지 않습니다.')


def normalize_web_entry(data):
    """필드 누락은 그대로 둔다. 수정 시 기존 값과 병합한 뒤 최종 기록을 검증한다."""
    if not isinstance(data, dict):
        raise ValueError('요청 본문은 JSON 객체여야 합니다.')
    entry = dict(data)
    for field, limit in TEXT_LIMITS.items():
        value = entry.get(field)
        if value is not None and (not isinstance(value, str) or len(value) > limit):
            raise ValueError(f'{field}: {limit}자 이하의 문자열이어야 합니다.')
    # 예전 HTTP 클라이언트의 buy/sell 표기도 실제 집계되는 거래로 정규화한다.
    if entry.get('type') in ('buy', 'sell'):
        entry['tradeType'] = '매수' if entry['type'] == 'buy' else '매도'
        entry['type'] = 'trade'
    if entry.get('type') not in ('trade', 'memo'):
        raise ValueError('기록 유형은 trade 또는 memo여야 합니다.')
    for field in ('price', 'quantity'):
        if field in entry:
            if entry[field] is None and (entry['type'] == 'memo' or entry.get('tradeType') in ('관망', '주시')):
                entry[field] = 0
            entry[field] = finite_number(entry[field])
            if entry[field] < 0:
                raise ValueError(f'{field}: 0 이상이어야 합니다.')
    if entry['type'] == 'trade':
        if not (entry.get('stockName') or '').strip():
            raise ValueError('종목명을 입력해주세요.')
        if entry.get('tradeType') not in ('매수', '매도', '배당', '관망', '주시'):
            raise ValueError('매매 유형이 올바르지 않습니다.')
        if entry['tradeType'] in ('매수', '매도', '배당'):
            if entry.get('quantity', 0) <= 0:
                raise ValueError('수량은 0보다 커야 합니다.')
            if 'price' not in entry:
                raise ValueError('가격을 입력해주세요.')
    if entry.get('rawDate'):
        try:
            datetime.fromisoformat(entry['rawDate'])
        except ValueError as exc:
            raise ValueError('거래 일시가 올바르지 않습니다.') from exc
    if 'currency' in entry:
        entry['currency'] = normalize_currency(entry['currency'], default='')
    normalize_trade_numbers(entry)
    return entry


def normalize_restored_entry(data):
    """구버전의 누락 필드는 허용하되 집계를 깨뜨리는 값은 교체 전에 거부한다.

    복원은 매수보다 먼저 도착한 매도나 수량 없는 옛 메모도 보존해야 하므로
    새 거래의 필수값·보유 검증과 분리한다. 긴 옛 본문에는 ZIP 크기 한도를 적용한다.
    """
    entry = dict(data)
    sequence = entry.get('tradeSequence')
    if sequence is not None and (isinstance(sequence, bool) or not isinstance(sequence, int)
                                 or not 0 < sequence < 2**63):
        raise ValueError('tradeSequence: 양의 정수여야 합니다.')
    text_fields = set(TEXT_LIMITS) | {
        'tradeStatus', 'confidence', 'orderOrigin', 'source', 'orderId',
        'originalOrderId', 'executedAtUtc', 'tradeDate', 'reviewReason', 'realizedPnlSource',
    }
    for field in text_fields:
        if entry.get(field) is not None and not isinstance(entry[field], str):
            raise ValueError(f'{field}: 문자열이어야 합니다.')
    normalize_trade_numbers(entry)
    for field in ('price', 'quantity'):
        if field not in entry or entry[field] is None:
            continue
        if entry[field] == '':
            entry[field] = 0
            continue
        try:
            entry[field] = finite_number(entry[field])
        except ValueError as exc:
            raise ValueError(f'{field}: {exc}') from exc
        if entry[field] < 0:
            raise ValueError(f'{field}: 0 이상이어야 합니다.')
    if entry.get('type') in ('buy', 'sell'):
        entry['tradeType'] = '매수' if entry['type'] == 'buy' else '매도'
        entry['type'] = 'trade'
    if 'currency' in entry:
        entry['currency'] = normalize_currency(entry['currency'], default='')
    return entry
