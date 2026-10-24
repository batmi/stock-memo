"""종목·계좌·통화 식별과 거래 순서, 이동평균 원가 계산의 공통 규칙."""

from datetime import datetime, timedelta, timezone
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

KST = timezone(timedelta(hours=9))
EPS = 1e-9
INACTIVE_TRADE_STATUSES = ('CANCELED', 'SUBMITTED')
_LEGACY_DATE_RE = re.compile(r'^(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})')


def normalize_stock_code(value):
    return str(value).strip().upper() if value is not None else ''


def stock_identity(entry):
    return normalize_stock_code(entry.get('stockCode')) or (entry.get('stockName') or '').strip()


def currency_of(entry):
    # 통화를 기록하지 않은 기존 데이터는 종전처럼 KRW로 해석한다.
    return (entry.get('currency') or 'KRW').strip().upper()


def account_identity(entry):
    # 계좌 별칭은 바뀔 수 있다. 번호가 없는 옛 기록만 별칭을 사용한다.
    return re.sub(r'[\s-]', '', str(entry.get('subAccount') or '')) or str(entry.get('accountName') or '').strip()


def position_scope(entry):
    return account_identity(entry), currency_of(entry)


def position_key(entry):
    return (*position_scope(entry), stock_identity(entry))


def is_settled_trade(entry):
    return (entry.get('tradeStatus') or 'FILLED') not in INACTIVE_TRADE_STATUSES


def parse_entry_dt(entry):
    """정렬은 KST 시각. 오프셋이 있는 값도 같은 시간대로 맞춘다."""
    if entry.get('rawDate'):
        try:
            value = datetime.fromisoformat(str(entry['rawDate']))
            return value.astimezone(KST).replace(tzinfo=None) if value.tzinfo else value
        except ValueError:
            pass
    parts = _LEGACY_DATE_RE.match(entry.get('date') or '')
    if parts:
        try:
            return datetime(*map(int, parts.groups()))
        except ValueError:
            pass
    try:
        return datetime.fromtimestamp(int(entry.get('id')) / 1000, KST).replace(tzinfo=None)
    except (ValueError, TypeError, OSError, OverflowError):
        return None


def trade_day(entry):
    """봇의 거래소 현지 거래일을 우선한다. 수기·구버전은 기존 거래 일시를 쓴다."""
    if entry.get('tradeDate'):
        try:
            return datetime.strptime(entry['tradeDate'], '%Y-%m-%d')
        except (ValueError, TypeError):
            pass
    return parse_entry_dt(entry)


def entry_order(entry):
    return parse_entry_dt(entry) or datetime.min, int(entry.get('id') or 0)


def empty_holding():
    return {'qty': 0.0, 'totalCost': 0.0, 'avgPrice': 0.0}


def apply_trade(holding, entry):
    qty, price = float(entry.get('quantity') or 0), float(entry.get('price') or 0)
    realized = cost = dividend = 0.0
    if entry.get('tradeType') == '매수':
        holding['qty'] += qty
        holding['totalCost'] += price * qty
        if holding['qty'] > 0:
            holding['avgPrice'] = holding['totalCost'] / holding['qty']
    elif entry.get('tradeType') == '매도':
        cost = holding['avgPrice'] * qty
        realized = (price - holding['avgPrice']) * qty
        holding['qty'] -= qty
        holding['totalCost'] -= cost
        if holding['qty'] <= EPS:
            holding.update(qty=0.0, totalCost=0.0, avgPrice=0.0)
    elif entry.get('tradeType') == '배당':
        dividend = price * qty
    return realized, cost, dividend


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


def trade_date_for(dt_aware, exchange):
    """거래소 현지 기준 거래일(YYYY-MM-DD)."""
    tz_name = _EXCHANGE_TZ.get((exchange or '').strip().upper())
    if tz_name:
        try:
            return dt_aware.astimezone(ZoneInfo(tz_name)).strftime('%Y-%m-%d')
        except ZoneInfoNotFoundError:
            pass
    # tz 데이터가 없거나 미등록 거래소면 요청에 실려 온 오프셋의 현지 날짜를 쓴다.
    return dt_aware.strftime('%Y-%m-%d')


