"""거래소·통화별 현재가와 마지막 확인 상태. 숫자 종목코드로 시장을 추측하지 않는다."""

from datetime import datetime, timezone
import json
import math
import re
import threading
import time
from urllib.parse import quote

import config
from app.database.db import db_conn
from app.services import prices

_inflight = {}
_lock = threading.RLock()
_capacity = threading.BoundedSemaphore(300)
_MARKETS = {
    'KRX': 'KRX', 'KOSPI': 'KRX', 'KOSDAQ': 'KRX', 'NXT': 'KRX', 'KR': 'KRX',
    'NASDAQ': 'NASDAQ', 'NAS': 'NASDAQ', 'NYSE': 'NYSE', 'NYS': 'NYSE',
    'AMEX': 'AMEX', 'AMS': 'AMEX', 'NYSEARCA': 'AMEX', 'US': 'US',
    'HKEX': 'HKEX', 'HKG': 'HKEX', 'SEHK': 'HKEX', 'HK': 'HKEX',
    'TSE': 'TSE', 'TYO': 'TSE', 'JPX': 'TSE', 'JP': 'TSE',
}
_DEFAULT_MARKET = {'KRW': 'KRX', 'USD': 'US', 'HKD': 'HKEX', 'JPY': 'TSE'}
_EXCHANGES = {'NASDAQ': {'NMS', 'NGM', 'NCM', 'NASDAQ'}, 'NYSE': {'NYQ', 'NYSE'},
              'AMEX': {'ASE', 'PCX', 'AMEX', 'NYSEARCA'}, 'HKEX': {'HKG', 'HKEX'},
              'TSE': {'JPX', 'TYO', 'TSE'}}
_EXCHANGES['US'] = _EXCHANGES['NASDAQ'] | _EXCHANGES['NYSE'] | _EXCHANGES['AMEX']


def asset_identity(data):
    if not isinstance(data, dict):
        raise ValueError('assets의 각 항목은 종목 객체여야 합니다.')
    values = [data.get('exchange', ''), data.get('currency', 'KRW'), data.get('code', '')]
    values = [default if value is None or value == '' else value
              for value, default in zip(values, ('', 'KRW', ''), strict=True)]
    if not all(isinstance(v, str) for v in values):
        raise ValueError('거래소·통화·종목코드는 문자열이어야 합니다.')
    exchange, currency, code = [v.strip().upper() for v in values]
    if (len(exchange) > 20 or not re.fullmatch(r'[A-Z]{3}', currency)
            or not re.fullmatch(r'[A-Z0-9.^=\-]{1,32}', code)):
        raise ValueError('거래소·통화·종목코드를 확인해주세요.')
    return {'exchange': exchange, 'currency': currency, 'code': code,
            'key': json.dumps([exchange, currency, code], separators=(',', ':'))}


def _empty(asset, mode):
    return dict(asset, price=None, asOf=None, market=mode, stale=False, status='unavailable')


def _positive(value):
    return not isinstance(value, bool) and isinstance(value, (float, int)) and math.isfinite(value) and value > 0


def _foreign(asset, exchange):
    code, currency = asset['code'], asset['currency']
    if currency != {'HKEX': 'HKD', 'TSE': 'JPY'}.get(exchange, 'USD'):
        return None
    if exchange not in _EXCHANGES:
        return None
    if exchange in ('HKEX', 'TSE'):
        suffix = '.HK' if exchange == 'HKEX' else '.T'
        bare = code.removesuffix(suffix)
        if not bare.isdigit():
            return None
        symbol = (bare.zfill(4) if exchange == 'HKEX' else bare) + suffix
    else:
        if not re.fullmatch(r'[A-Z][A-Z.\-]{0,15}', code):
            return None
        # 과거 시세 어댑터와 동일하게 미국 주식 클래스 구분자를 맞춘다.
        symbol = code.replace('.', '-')
    raw = json.loads(prices._http_get(
        'https://query1.finance.yahoo.com/v8/finance/chart/' + quote(symbol, safe=''), prices._YAHOO_HEADERS))
    meta = raw['chart']['result'][0]['meta']
    if (str(meta.get('currency', '')).upper() != currency
            or str(meta.get('symbol', '')).upper() != symbol
            or str(meta.get('exchangeName', '')).upper() not in _EXCHANGES[exchange]):
        return None
    return meta.get('regularMarketPrice')


def _fetch(asset, mode, allow_cached):
    now = time.time()
    with db_conn() as conn:
        row = conn.execute('SELECT payload,checked_at FROM quote_cache WHERE asset_key=? AND market_mode=?',
                           (asset['key'], mode)).fetchone()
    previous = json.loads(row['payload']) if row else None
    if allow_cached and row and now - row['checked_at'] < prices.PRICE_MEM_TTL:
        return previous
    exchange = _MARKETS.get(asset['exchange']) if asset['exchange'] else _DEFAULT_MARKET.get(asset['currency'])
    value = None
    try:
        if exchange == 'KRX' and asset['currency'] == 'KRW':
            if prices.detect_market(asset['code']) == 'KR' or asset['code'] in ('GOLD', 'KRXGOLD'):
                _, value = prices.fetch_price(asset['code'], mode, allow_cached=False)
        elif exchange:
            value = _foreign(asset, exchange)
    except Exception:
        prices.logger.debug('종목 시세 확인 실패: %s', asset['key'], exc_info=True)
    if _positive(value) and not isinstance(value, (prices.FallbackPrice, prices.CachedPrice)):
        result = dict(asset, price=float(value), asOf=datetime.fromtimestamp(now, timezone.utc).isoformat(),
                      market=mode if exchange == 'KRX' else exchange, stale=False, status='ok')
        with db_conn(write=True) as conn:
            conn.execute('INSERT OR REPLACE INTO quote_cache VALUES (?,?,?,?)',
                         (asset['key'], mode, json.dumps(result), now))
            conn.commit()
        return result
    if previous:
        return dict(previous, stale=True, status='stale')
    if _positive(value):
        # 이전 버전의 가격 캐시에는 정확한 확인 시각이 없으므로 시각을 만들지 않는다.
        return dict(asset, price=float(value), asOf=None, market=mode, stale=True, status='stale')
    return _empty(asset, mode)


def _schedule(asset, mode, allow_cached):
    key = (config.DB_FILE, asset['key'], mode)
    with _lock:
        if key in _inflight:
            return _inflight[key]
        if not _capacity.acquire(blocking=False):
            return None
        try:
            future = prices._executor.submit(_fetch, asset, mode, allow_cached)
        except BaseException:
            _capacity.release()
            raise
        _inflight[key] = future

        def finished(_):
            with _lock:
                _inflight.pop(key, None)
            _capacity.release()
        future.add_done_callback(finished)
        return future


def get_quotes(assets, market_mode='AUTO', allow_cached=False):
    normalized = [asset_identity(asset) for asset in assets]
    mode = 'AFT' if prices.is_aft_mode(market_mode) else 'KRX'
    futures = {}
    for asset in normalized:
        if asset['key'] not in futures:
            futures[asset['key']] = _schedule(asset, mode, allow_cached)
    result = []
    for asset in normalized:
        try:
            future = futures[asset['key']]
            result.append(future.result() if future is not None else _empty(asset, mode))
        except Exception:
            prices.logger.exception('현재가 작업 실패: %s', asset['key'])
            result.append(_empty(asset, mode))
    return result
