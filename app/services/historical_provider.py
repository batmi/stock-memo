"""일별 종가 어댑터. Yahoo Close의 분할 조정을 되돌려 당시 거래 가격으로 저장한다.

배당까지 보정한 Adj Close는 사용하지 않는다. 분할 정보는 조회 종료일 이후도 필요하다.
HTTP 및 파싱은 DB/사용자 기록과 독립적이며 종목·통화를 검증한 응답만 사용한다.
"""

from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
import json
import math
import re
from time import monotonic
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import holidays

from app.services import prices

GOLD_SYMBOL = 'NAVER:M04020000'
GOLD_URL = 'https://api.stock.naver.com/marketindex/metals/M04020000'
GOLD_PAGE_SIZE = 60


def candidates(code, currency):
    if currency == 'KRW' and re.fullmatch(r'[0-9A-Z]{6}', code):
        return [code + '.KS', code + '.KQ']
    if currency == 'USD' and re.fullmatch(r'[A-Z][A-Z.\-]{0,9}', code):
        return [code.replace('.', '-')]
    return []


def supports(code, currency):
    return (currency == 'KRW' and code in ('KRXGOLD', 'GOLD')) or bool(candidates(code, currency))


def market_timezone(currency):
    return ZoneInfo('Asia/Seoul' if currency == 'KRW' else 'America/New_York')


@lru_cache(maxsize=100)
def _us_holidays(year):
    return holidays.financial_holidays('NYSE', years=year)


def is_session(day, currency):
    if day.weekday() >= 5:
        return False
    if currency == 'KRW':
        return (day.year, day.month, day.day) not in prices.holidays_for(day.year)
    return day not in _us_holidays(day.year)


def completed_day(currency, now=None):
    """정규장 종료 후 데이터 확정 여유를 둔다. 장중 가격을 일별 종가로 고정하지 않는다."""
    local = (now or datetime.now(timezone.utc)).astimezone(market_timezone(currency))
    day = local.date() if local.hour >= 18 else local.date() - timedelta(days=1)
    while not is_session(day, currency):
        day -= timedelta(days=1)
    return day


def _read_json(url, *, timeout=12, headers=None):
    req = Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json', **(headers or {})})
    with urlopen(req, timeout=timeout) as response:
        raw = response.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError('시세 응답이 너무 큽니다.')
    return json.loads(raw)


def _gold_json(path='', *, timeout=12):
    return _read_json(GOLD_URL + path, timeout=timeout, headers={'Referer': 'https://m.stock.naver.com/'})


def _gold_asset():
    meta = _gold_json()
    exchange = meta.get('stockExchangeType') or {}
    if (meta.get('symbolCode') != 'M04020000' or meta.get('unit') != '원/g'
            or exchange.get('code') != 'KRX' or exchange.get('zoneId') != 'Asia/Seoul'):
        raise ValueError('KRX 금현물의 거래소 또는 가격 단위가 올바르지 않습니다.')
    return {'symbol': GOLD_SYMBOL, 'timezone': 'Asia/Seoul', 'splits': {}}


def _gold_closes(start, end):
    """원/g 종가. 오래된 구간은 페이지를 이진 탐색해 불필요한 전체 이력 조회를 피한다."""
    pages = {}
    deadline = monotonic() + 120  # 메타 조회를 포함해 수집 큐의 180초 lease 안에서 끝낸다.

    def page(number):
        if number in pages:
            return pages[number]
        remaining = deadline - monotonic()
        if remaining <= 0 or len(pages) >= 24:
            raise ValueError('금현물 시세 조회 범위 또는 대기 시간을 초과했습니다.')
        payload = _gold_json(f'/prices?page={number}&pageSize={GOLD_PAGE_SIZE}', timeout=min(12, remaining))
        if not isinstance(payload, list) or len(payload) > GOLD_PAGE_SIZE:
            raise ValueError('금현물 일별 시세 응답이 올바르지 않습니다.')
        rows = []
        for item in payload:
            stamp = datetime.fromisoformat(item['localTradedAt'])
            if stamp.utcoffset() != timedelta(hours=9):
                raise ValueError('금현물 거래일의 시간대가 올바르지 않습니다.')
            day = stamp.date()
            if rows and day >= rows[-1][0]:
                raise ValueError('금현물 시세의 날짜 순서가 올바르지 않습니다.')
            raw = item.get('closePrice')
            close = float(str(raw).replace(',', '')) if raw is not None else None
            if close is not None and (not math.isfinite(close) or close <= 0):
                close = None
            rows.append((day, close))
        pages[number] = rows
        return rows

    rows = page(1)
    if not rows:
        return {}
    number = 1
    if rows[-1][0] > end:
        # 하루에 최대 한 행이므로 달력 일수로 과거 페이지의 상한을 잡을 수 있다.
        low, high = 2, (rows[0][0] - end).days // GOLD_PAGE_SIZE + 2
        while low < high:
            mid = (low + high) // 2
            probe = page(mid)
            if not probe or probe[-1][0] <= end:
                high = mid
            else:
                low = mid + 1
        number = low

    result = {}
    previous = None
    while True:
        rows = page(number)
        if not rows:
            return result
        if previous is not None and rows[0][0] >= previous:
            raise ValueError('금현물 과거 시세 페이지가 반복됩니다.')
        for day, close in rows:
            if start <= day <= end and close is not None:
                result[day.isoformat()] = close
        if rows[-1][0] <= start:
            return result
        previous = rows[-1][0]
        number += 1


def _chart(symbol, start, end, interval):
    params = urlencode({
        'period1': int(datetime.combine(start, time(), timezone.utc).timestamp()),
        'period2': int(datetime.combine(end + timedelta(days=2), time(), timezone.utc).timestamp()),
        'interval': interval, 'events': 'splits', 'includeAdjustedClose': 'false',
    })
    url = 'https://query1.finance.yahoo.com/v8/finance/chart/' + quote(symbol, safe='') + '?' + params
    payload = _read_json(url)['chart']
    if payload.get('error') or not payload.get('result'):
        raise ValueError('과거 시세를 제공하지 않는 종목입니다.')
    return payload['result'][0]


def _validate(result, currency):
    meta = result['meta']
    if meta.get('currency') != currency:
        raise ValueError('종목의 시세 통화가 거래 기록과 다릅니다.')
    expected = str(market_timezone(currency))
    if meta.get('exchangeTimezoneName') != expected:
        raise ValueError('지원하는 거래소의 시세가 아닙니다.')
    return meta


def fetch_asset(code, currency):
    """월간 응답으로 전체 분할 이력을 작은 payload로 확인한다(종가 조회는 별도)."""
    if currency == 'KRW' and code in ('KRXGOLD', 'GOLD'):
        return _gold_asset()
    symbols = candidates(code, currency)
    if not symbols:
        raise ValueError('과거 시세는 국내 주식·ETF·KRX 금현물과 미국 종목을 지원합니다.')
    last_error = None
    for symbol in symbols:
        try:
            result = _chart(symbol, date(1970, 1, 1), datetime.now(timezone.utc).date(), '1mo')
            meta = _validate(result, currency)
            tz = ZoneInfo(meta['exchangeTimezoneName'])
            splits = {}
            for event in result.get('events', {}).get('splits', {}).values():
                ratio = float(event['numerator']) / float(event['denominator'])
                if not math.isfinite(ratio) or ratio <= 0:
                    raise ValueError('분할 비율이 올바르지 않습니다.')
                day = datetime.fromtimestamp(event['date'], tz).date().isoformat()
                splits[day] = splits.get(day, 1.0) * ratio
            return {'symbol': symbol, 'timezone': meta['exchangeTimezoneName'], 'splits': splits}
        except Exception as exc:
            last_error = exc
    raise ValueError('종목 정보와 분할 이력을 조회하지 못했습니다.') from last_error


def fetch_closes(asset, currency, start, end):
    if asset['symbol'] == GOLD_SYMBOL:
        if currency != 'KRW':
            raise ValueError('금현물 시세 통화는 KRW입니다.')
        return _gold_closes(start, end)
    result = _chart(asset['symbol'], start - timedelta(days=1), end, '1d')
    _validate(result, currency)
    tz = ZoneInfo(asset['timezone'])
    timestamps = result.get('timestamp') or []
    closes = result['indicators']['quote'][0].get('close') or []
    if len(timestamps) != len(closes):
        raise ValueError('종가 응답의 날짜와 가격 개수가 다릅니다.')
    rows = {}
    for stamp, close in zip(timestamps, closes, strict=True):
        day = datetime.fromtimestamp(stamp, tz).date()
        if not start <= day <= end or close is None:
            continue
        close = float(close)
        if not math.isfinite(close) or close <= 0:
            continue
        # Yahoo Close는 이후 분할을 반영한다. 기록의 실제 체결 단가와 단위를 맞춘다.
        factor = math.prod(ratio for split_day, ratio in asset['splits'].items() if split_day > day.isoformat())
        rows[day.isoformat()] = close * factor
    return rows
