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


TEXT_LIMITS = {
    'type': 20, 'tradeType': 20, 'stockName': 200, 'stockCode': 32,
    'title': 1000, 'thoughts': 16 * 1024 * 1024, 'tags': 5000,
    'brokerAccount': 200, 'subAccount': 200, 'accountName': 200, 'tradeClass': 200,
    'currency': 10, 'exchange': 20, 'assetType': 20, 'brokerExecutionId': 200,
    'rawDate': 64, 'date': 100, 'createdAt': 64, 'updatedAt': 64,
    'attachedImage': 16 * 1024 * 1024, 'attachedFile': 16 * 1024 * 1024,
    'attachedFileName': 500,
}


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
    if entry.get('currency'):
        entry['currency'] = entry['currency'].strip().upper()
        if not re.fullmatch('[A-Z]{3}', entry['currency']):
            raise ValueError('통화는 KRW, USD 같은 3자리 코드여야 합니다.')
    return entry
