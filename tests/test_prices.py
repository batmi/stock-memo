"""prices.py(시세 조회 서비스) 모듈의 단위 테스트.

Flask 라우트를 거치지 않고 provider 함수들을 직접 호출하여
시장 판정/장중 판정/DB 캐시/HTTP keep-alive/다단계 폴백 분기를 검증합니다.
"""
import os
import sys
import time
import sqlite3
import datetime as _dt
from unittest.mock import patch, MagicMock

import pytest

# prices 모듈을 임포트할 수 있도록 상위 경로 추가
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import prices


# ─────────────────────────────────────────────────────────────
# 인메모리 price_cache 커넥션 픽스처
# ─────────────────────────────────────────────────────────────
@pytest.fixture
def conn():
    """price_cache 테이블만 갖춘 인메모리 SQLite 커넥션."""
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.execute('''
        CREATE TABLE price_cache (
            code TEXT,
            market_type TEXT DEFAULT 'KRX',
            price REAL,
            updated_at TEXT,
            PRIMARY KEY (code, market_type)
        )
    ''')
    c.commit()
    yield c
    c.close()


# ─────────────────────────────────────────────────────────────
# detect_market: 시장(국가) 구분
# ─────────────────────────────────────────────────────────────
def test_detect_market_us_ticker():
    assert prices.detect_market('AAPL') == 'US'
    assert prices.detect_market('BRK.B') == 'US'


def test_detect_market_kr_numeric():
    assert prices.detect_market('005930') == 'KR'


def test_detect_market_kr_alphanumeric():
    # 영문 혼합 6자리(신주인수권/ETN 등)도 국내로 포괄
    assert prices.detect_market('0162Z0') == 'KR'


def test_detect_market_other_asian():
    # 6자리가 아닌 순수 숫자 (예: 일본/홍콩 등)
    assert prices.detect_market('7203') == 'OTHER_ASIAN'


def test_detect_market_unknown():
    assert prices.detect_market('!!bad!!') == 'UNKNOWN'


# ─────────────────────────────────────────────────────────────
# is_kr_out_of_hours: 정규장 시간 판정
# ─────────────────────────────────────────────────────────────
def test_is_kr_out_of_hours_during_session():
    # 2026-06-29(월) 10:00 KST → 장중
    weekday_open = _dt.datetime(2026, 6, 29, 10, 0)
    assert prices.is_kr_out_of_hours(weekday_open) is False


def test_is_kr_out_of_hours_after_close():
    # 2026-06-29(월) 16:00 KST → 장 마감 후
    weekday_closed = _dt.datetime(2026, 6, 29, 16, 0)
    assert prices.is_kr_out_of_hours(weekday_closed) is True


def test_is_kr_out_of_hours_weekend():
    # 2026-06-27(토)
    saturday = _dt.datetime(2026, 6, 27, 10, 0)
    assert prices.is_kr_out_of_hours(saturday) is True


def test_is_kr_out_of_hours_holiday():
    # 2026-01-01 신정 (KRX_HOLIDAYS 포함)
    holiday = _dt.datetime(2026, 1, 1, 10, 0)
    assert prices.is_kr_out_of_hours(holiday) is True


def test_is_kr_out_of_hours_default_now():
    # 인자 없이 호출해도 bool 을 반환
    assert isinstance(prices.is_kr_out_of_hours(), bool)


# ─────────────────────────────────────────────────────────────
# save_price_cache / load_price_cache
# ─────────────────────────────────────────────────────────────
def test_save_and_load_price_cache(conn):
    prices.save_price_cache(conn, '005930', 71000.0, 'KRX')
    assert prices.load_price_cache(conn, '005930', 'KRX') == 71000.0


def test_save_price_cache_replaces(conn):
    prices.save_price_cache(conn, '005930', 100.0, 'KRX')
    prices.save_price_cache(conn, '005930', 200.0, 'KRX')
    assert prices.load_price_cache(conn, '005930', 'KRX') == 200.0


def test_load_price_cache_miss_returns_none(conn):
    assert prices.load_price_cache(conn, 'NOPE', 'KRX') is None


def test_load_price_cache_market_type_separated(conn):
    prices.save_price_cache(conn, '005930', 100.0, 'KRX')
    prices.save_price_cache(conn, '005930', 105.0, 'NXT')
    assert prices.load_price_cache(conn, '005930', 'NXT') == 105.0
    assert prices.load_price_cache(conn, '005930', 'KRX') == 100.0


def test_save_price_cache_swallows_errors():
    # 잘못된 커넥션이어도 예외를 삼킴
    bad = MagicMock()
    bad.execute.side_effect = Exception("db error")
    prices.save_price_cache(bad, '005930', 1.0)  # 예외 없이 통과


def test_load_price_cache_swallows_errors():
    bad = MagicMock()
    bad.execute.side_effect = Exception("db error")
    assert prices.load_price_cache(bad, '005930') is None


# ─────────────────────────────────────────────────────────────
# _http_get: keep-alive 커넥션 재사용 및 stale 재시도
# ─────────────────────────────────────────────────────────────
def _reset_conn_pool():
    """스레드로컬 커넥션 풀을 초기화."""
    prices._conn_pool = prices.threading.local()


def test_http_get_success_returns_body():
    _reset_conn_pool()
    fake_resp = MagicMock()
    fake_resp.read.return_value = b'BODY'
    fake_conn = MagicMock()
    fake_conn.getresponse.return_value = fake_resp

    with patch.object(prices, '_make_conn', return_value=fake_conn):
        body = prices._http_get('https://example.com/path?x=1', {})
    assert body == b'BODY'
    fake_conn.request.assert_called_once()


def test_http_get_reuses_connection():
    _reset_conn_pool()
    fake_resp = MagicMock()
    fake_resp.read.return_value = b'OK'
    fake_conn = MagicMock()
    fake_conn.getresponse.return_value = fake_resp

    with patch.object(prices, '_make_conn', return_value=fake_conn) as mk:
        prices._http_get('https://example.com/a', {})
        prices._http_get('https://example.com/b', {})
    # 같은 호스트는 커넥션 1개만 생성하여 재사용
    assert mk.call_count == 1


def test_http_get_stale_retry():
    _reset_conn_pool()
    # 첫 커넥션은 request 시 예외 → 폐기 후 두 번째 커넥션으로 성공
    stale_conn = MagicMock()
    stale_conn.request.side_effect = Exception("stale socket")

    ok_resp = MagicMock()
    ok_resp.read.return_value = b'RETRIED'
    ok_conn = MagicMock()
    ok_conn.getresponse.return_value = ok_resp

    with patch.object(prices, '_make_conn', side_effect=[stale_conn, ok_conn]):
        body = prices._http_get('https://example.com/x', {})
    assert body == b'RETRIED'
    stale_conn.close.assert_called_once()


def test_http_get_raises_after_two_failures():
    _reset_conn_pool()
    bad_conn = MagicMock()
    bad_conn.request.side_effect = Exception("down")

    with patch.object(prices, '_make_conn', return_value=bad_conn):
        with pytest.raises(Exception):
            prices._http_get('https://example.com/x', {})


def test_make_conn_https_and_http():
    https = prices._make_conn('https', 'example.com')
    assert isinstance(https, prices.http.client.HTTPSConnection)
    http_c = prices._make_conn('http', 'example.com')
    assert isinstance(http_c, prices.http.client.HTTPConnection)


# ─────────────────────────────────────────────────────────────
# _fetch_krx_realtime / _fetch_nxt_pc_crawl
# ─────────────────────────────────────────────────────────────
def test_fetch_krx_realtime_success():
    with patch.object(prices, '_http_get', return_value='{ "nowVal": 95000 }'.encode('euc-kr')):
        assert prices._fetch_krx_realtime('005930') == 95000.0


def test_fetch_krx_realtime_zero_value_returns_none():
    with patch.object(prices, '_http_get', return_value='{ "nowVal": 0 }'.encode('euc-kr')):
        assert prices._fetch_krx_realtime('005930') is None


def test_fetch_krx_realtime_exception_returns_none():
    with patch.object(prices, '_http_get', side_effect=Exception("net")):
        assert prices._fetch_krx_realtime('005930') is None


def test_fetch_nxt_pc_crawl_success():
    html = (
        '시간외단일가'
        '<span class="blind">71,500</span>'
        '</table>'
    ).encode('euc-kr')
    with patch.object(prices, '_http_get', return_value=html):
        assert prices._fetch_nxt_pc_crawl('005930') == 71500.0


def test_fetch_nxt_pc_crawl_no_trade_returns_none():
    html = '시간외단일가 거래 내역이 없습니다 </table>'.encode('euc-kr')
    with patch.object(prices, '_http_get', return_value=html):
        assert prices._fetch_nxt_pc_crawl('005930') is None


def test_fetch_nxt_pc_crawl_no_match_returns_none():
    with patch.object(prices, '_http_get', return_value=b'no relevant area'):
        assert prices._fetch_nxt_pc_crawl('005930') is None


def test_fetch_nxt_pc_crawl_exception_returns_none():
    with patch.object(prices, '_http_get', side_effect=Exception("net")):
        assert prices._fetch_nxt_pc_crawl('005930') is None


# ─────────────────────────────────────────────────────────────
# _fetch_gold
# ─────────────────────────────────────────────────────────────
def test_fetch_gold_naver_success(conn):
    with patch.object(prices, '_http_get', return_value=b'{"closePrice": "88,000"}'):
        assert prices._fetch_gold(conn, 'KRXGOLD') == 88000.0
    # 성공 시 캐시에 저장됨
    assert prices.load_price_cache(conn, 'KRXGOLD', 'KRX') == 88000.0


def test_fetch_gold_krx_crawl_fallback(conn):
    def side_effect(url, headers=None):
        if 'M04020000' in url:
            raise Exception("naver gold down")
        return b"<th>\xed\x98\x84\xec\x9e\xac\xea\xb0\x80</th><td><strong>90,000</strong></td>"

    with patch.object(prices, '_http_get', side_effect=side_effect):
        assert prices._fetch_gold(conn, 'KRXGOLD') == 90000.0


def test_fetch_gold_all_fail_returns_none(conn):
    with patch.object(prices, '_http_get', side_effect=Exception("all down")):
        assert prices._fetch_gold(conn, 'KRXGOLD') is None


# ─────────────────────────────────────────────────────────────
# _fetch_yahoo
# ─────────────────────────────────────────────────────────────
def test_fetch_yahoo_success(conn):
    body = b'{"chart": {"result": [{"meta": {"regularMarketPrice": 250.5}}]}}'
    with patch.object(prices, '_http_get', return_value=body):
        assert prices._fetch_yahoo(conn, 'AAPL') == 250.5
    assert prices.load_price_cache(conn, 'AAPL', 'KRX') == 250.5


def test_fetch_yahoo_exception_returns_none(conn):
    with patch.object(prices, '_http_get', side_effect=Exception("net")):
        assert prices._fetch_yahoo(conn, 'AAPL') is None


# ─────────────────────────────────────────────────────────────
# _fetch_kr: NXT/KRX 분기 및 폴백
# ─────────────────────────────────────────────────────────────
def test_fetch_kr_krx_mode_realtime_priority(conn):
    # 장중: 실시간 시세 우선
    with patch.object(prices, 'is_kr_out_of_hours', return_value=False), \
         patch.object(prices, '_fetch_krx_realtime', return_value=95000.0), \
         patch.object(prices, '_http_get', return_value=b'{"closePrice": "90000"}'):
        assert prices._fetch_kr(conn, '005930', 'KRX') == 95000.0


def test_fetch_kr_krx_mode_uses_close_when_no_realtime(conn):
    # 장외: 실시간 없음 → 모바일 closePrice 사용
    with patch.object(prices, 'is_kr_out_of_hours', return_value=True), \
         patch.object(prices, '_http_get', return_value=b'{"closePrice": "90000"}'):
        assert prices._fetch_kr(conn, '005930', 'KRX') == 90000.0


def test_fetch_kr_nxt_mode_intraday_uses_krx(conn):
    # NXT 모드 + 장중 → KRX 실시간 우선
    with patch.object(prices, 'is_kr_out_of_hours', return_value=False), \
         patch.object(prices, '_fetch_krx_realtime', return_value=95000.0), \
         patch.object(prices, '_http_get', return_value=b'{"closePrice": "90000"}'):
        assert prices._fetch_kr(conn, '005930', 'NXT') == 95000.0


def test_fetch_kr_nxt_mode_over_price(conn):
    # NXT 모드 + 장외 → overMarketPriceInfo 사용
    body = b'{"closePrice": "90000", "overMarketPriceInfo": {"overPrice": "91,200"}}'
    with patch.object(prices, 'is_kr_out_of_hours', return_value=True), \
         patch.object(prices, '_http_get', return_value=body):
        assert prices._fetch_kr(conn, '005930', 'NXT') == 91200.0
    assert prices.load_price_cache(conn, '005930', 'NXT') == 91200.0


def test_fetch_kr_nxt_mode_pc_crawl_fallback(conn):
    # NXT 모드 + 장외 + overPrice 없음 → PC 크롤링 폴백
    body = b'{"closePrice": "90000"}'
    with patch.object(prices, 'is_kr_out_of_hours', return_value=True), \
         patch.object(prices, '_http_get', return_value=body), \
         patch.object(prices, '_fetch_nxt_pc_crawl', return_value=71500.0):
        assert prices._fetch_kr(conn, '005930', 'NXT') == 71500.0


def test_fetch_kr_nxt_mode_cached_nxt_fallback(conn):
    # NXT 모드 + 모든 NXT 소스 실패 → NXT 캐시
    prices.save_price_cache(conn, '005930', 70000.0, 'NXT')
    body = b'{"closePrice": "0"}'  # close_price None 처리
    with patch.object(prices, 'is_kr_out_of_hours', return_value=True), \
         patch.object(prices, '_http_get', return_value=body), \
         patch.object(prices, '_fetch_nxt_pc_crawl', return_value=None):
        assert prices._fetch_kr(conn, '005930', 'NXT') == 70000.0


def test_fetch_kr_network_error_falls_back_to_cache(conn):
    # 통신 에러 → KRX 캐시를 최후 보루로
    prices.save_price_cache(conn, '005930', 68000.0, 'KRX')
    with patch.object(prices, 'is_kr_out_of_hours', return_value=True), \
         patch.object(prices, '_http_get', side_effect=Exception("net down")):
        assert prices._fetch_kr(conn, '005930', 'KRX') == 68000.0


def test_fetch_kr_network_error_nxt_cache(conn):
    prices.save_price_cache(conn, '005930', 69000.0, 'NXT')
    with patch.object(prices, 'is_kr_out_of_hours', return_value=True), \
         patch.object(prices, '_http_get', side_effect=Exception("net down")):
        assert prices._fetch_kr(conn, '005930', 'NXT') == 69000.0


# ─────────────────────────────────────────────────────────────
# _fetch_price_uncached: 라우팅 통합
# ─────────────────────────────────────────────────────────────
def test_fetch_price_uncached_gold(conn):
    with patch.object(prices, '_fetch_gold', return_value=88000.0) as g:
        assert prices._fetch_price_uncached(conn, 'KRXGOLD', 'AUTO') == 88000.0
        g.assert_called_once()


def test_fetch_price_uncached_kr_success(conn):
    with patch.object(prices, '_fetch_kr', return_value=95000.0):
        assert prices._fetch_price_uncached(conn, '005930', 'AUTO') == 95000.0


def test_fetch_price_uncached_kr_fail_then_yahoo(conn):
    with patch.object(prices, '_fetch_kr', return_value=None), \
         patch.object(prices, '_fetch_yahoo', return_value=100.0):
        assert prices._fetch_price_uncached(conn, '005930', 'AUTO') == 100.0


def test_fetch_price_uncached_us_uses_yahoo(conn):
    with patch.object(prices, '_fetch_yahoo', return_value=250.5):
        assert prices._fetch_price_uncached(conn, 'AAPL', 'AUTO') == 250.5


def test_fetch_price_uncached_final_cache_fallback(conn):
    prices.save_price_cache(conn, 'AAPL', 123.0, 'KRX')
    with patch.object(prices, '_fetch_yahoo', return_value=None):
        assert prices._fetch_price_uncached(conn, 'AAPL', 'AUTO') == 123.0


def test_fetch_price_uncached_nxt_cache_fallback(conn):
    prices.save_price_cache(conn, '005930', 77000.0, 'NXT')
    with patch.object(prices, '_fetch_kr', return_value=None), \
         patch.object(prices, '_fetch_yahoo', return_value=None):
        assert prices._fetch_price_uncached(conn, '005930', 'NXT') == 77000.0


def test_fetch_price_uncached_all_fail_returns_none(conn):
    with patch.object(prices, '_fetch_kr', return_value=None), \
         patch.object(prices, '_fetch_yahoo', return_value=None):
        assert prices._fetch_price_uncached(conn, '005930', 'AUTO') is None


# ─────────────────────────────────────────────────────────────
# fetch_price / get_prices: 최상위 진입점
# ─────────────────────────────────────────────────────────────
def test_fetch_price_empty_code():
    assert prices.fetch_price('   ') == (None, None)


def test_fetch_price_success(conn):
    with patch.object(prices, '_get_db', return_value=conn), \
         patch.object(prices, '_fetch_price_uncached', return_value=95000.0):
        code, price = prices.fetch_price('005930')
    assert code == '005930'
    assert price == 95000.0


def test_fetch_price_db_error_returns_none():
    with patch.object(prices, '_get_db', side_effect=Exception("no db")):
        code, price = prices.fetch_price('005930')
    assert code == '005930'
    assert price is None


def test_fetch_price_closes_connection():
    fake_conn = MagicMock()
    with patch.object(prices, '_get_db', return_value=fake_conn), \
         patch.object(prices, '_fetch_price_uncached', return_value=1.0):
        prices.fetch_price('005930')
    fake_conn.close.assert_called_once()


def test_get_prices_aggregates(conn):
    def fake_fetch(code, market_mode='AUTO', allow_cached=False):
        return code, {'005930': 95000.0, 'AAPL': 250.5}.get(code)

    with patch.object(prices, 'fetch_price', side_effect=fake_fetch):
        result = prices.get_prices(['005930', 'AAPL'])
    assert result == {'005930': 95000.0, 'AAPL': 250.5}


def test_get_prices_filters_none_code():
    def fake_fetch(code, market_mode='AUTO', allow_cached=False):
        return (None, None) if code == '' else (code, 1.0)

    with patch.object(prices, 'fetch_price', side_effect=fake_fetch):
        result = prices.get_prices(['', '005930'])
    assert result == {'005930': 1.0}


# ─────────────────────────────────────────────────────────────
# is_nxt_mode / holiday_list / 휴장일 목록 만료 경고
# ─────────────────────────────────────────────────────────────
def test_is_nxt_mode_variants():
    assert prices.is_nxt_mode('NXT') is True
    assert prices.is_nxt_mode(' nxt ') is True
    assert prices.is_nxt_mode('KRX') is False
    assert prices.is_nxt_mode('AUTO') is False   # AUTO 는 KRX 와 동일 취급
    assert prices.is_nxt_mode(None) is False


def test_holiday_list_is_sorted_iso_strings():
    lst = prices.holiday_list()
    assert lst == sorted(lst)
    assert '2026-01-01' in lst
    assert all(len(d) == 10 and d.count('-') == 2 for d in lst)


def test_holidays_outdated_warns_once_per_day(caplog):
    prices._holiday_warn_date = None
    future = _dt.datetime(prices.KRX_HOLIDAYS_MAX_YEAR + 1, 3, 4, 10, 0)
    with caplog.at_level('WARNING', logger='prices'):
        prices.is_kr_out_of_hours(future)
        prices.is_kr_out_of_hours(future)  # 같은 날 재호출은 침묵
    warnings = [r for r in caplog.records if 'KRX_HOLIDAYS' in r.getMessage()]
    assert len(warnings) == 1
    prices._holiday_warn_date = None


def test_holidays_within_range_does_not_warn(caplog):
    prices._holiday_warn_date = None
    inside = _dt.datetime(prices.KRX_HOLIDAYS_MAX_YEAR, 3, 4, 10, 0)
    with caplog.at_level('WARNING', logger='prices'):
        prices.is_kr_out_of_hours(inside)
    assert not [r for r in caplog.records if 'KRX_HOLIDAYS' in r.getMessage()]


# ─────────────────────────────────────────────────────────────
# 야후 심볼 접미사 (국내/아시아 종목은 접미사 없이는 조회 불가)
# ─────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _clear_yahoo_hint():
    prices._yahoo_symbol_hint.clear()
    yield
    prices._yahoo_symbol_hint.clear()


def test_yahoo_candidates_kr_gets_ks_kq():
    assert prices._yahoo_symbol_candidates('005930') == ['005930.KS', '005930.KQ', '005930']


def test_yahoo_candidates_us_is_bare():
    assert prices._yahoo_symbol_candidates('AAPL') == ['AAPL']


def test_yahoo_candidates_other_asian():
    assert prices._yahoo_symbol_candidates('7203') == ['7203.T', '7203.HK', '7203']


def test_fetch_yahoo_kr_falls_through_to_kosdaq(conn):
    body = b'{"chart": {"result": [{"meta": {"regularMarketPrice": 41000}}]}}'
    calls = []

    def fake_get(url, headers):
        calls.append(url)
        if url.endswith('.KS'):
            raise Exception('404')
        return body

    with patch.object(prices, '_http_get', side_effect=fake_get):
        assert prices._fetch_yahoo(conn, '035720') == 41000.0
    assert calls[0].endswith('035720.KS')
    assert calls[1].endswith('035720.KQ')
    # 성공한 심볼을 기억해 다음 조회는 한 번만 호출한다
    assert prices._yahoo_symbol_hint['035720'] == '035720.KQ'


def test_fetch_yahoo_uses_remembered_symbol(conn):
    prices._yahoo_symbol_hint['035720'] = '035720.KQ'
    body = b'{"chart": {"result": [{"meta": {"regularMarketPrice": 41000}}]}}'
    with patch.object(prices, '_http_get', return_value=body) as g:
        assert prices._fetch_yahoo(conn, '035720') == 41000.0
    assert g.call_count == 1


def test_fetch_yahoo_forgets_stale_symbol(conn):
    prices._yahoo_symbol_hint['035720'] = '035720.KQ'
    with patch.object(prices, '_http_get', side_effect=Exception('404')):
        assert prices._fetch_yahoo(conn, '035720') is None
    # 기억한 심볼이 실패하면 잊고 다음 호출에서 전체 후보를 다시 시도한다
    assert '035720' not in prices._yahoo_symbol_hint


def test_fetch_yahoo_null_price_is_skipped(conn):
    body = b'{"chart": {"result": [{"meta": {"regularMarketPrice": null}}]}}'
    with patch.object(prices, '_http_get', return_value=body):
        assert prices._fetch_yahoo(conn, 'AAPL') is None


# ─────────────────────────────────────────────────────────────
# fetch_nxt_close: 백그라운드 캐싱 잡이 쓰는 진입점
# ─────────────────────────────────────────────────────────────
def test_fetch_nxt_close_uses_over_price():
    body = b'{"overMarketPriceInfo": {"overPrice": "91,200"}}'
    with patch.object(prices, '_http_get', return_value=body):
        assert prices.fetch_nxt_close('005930') == 91200.0


def test_fetch_nxt_close_falls_back_to_pc_crawl():
    with patch.object(prices, '_http_get', return_value=b'{}'), \
         patch.object(prices, '_fetch_nxt_pc_crawl', return_value=71500.0):
        assert prices.fetch_nxt_close('005930') == 71500.0


def test_fetch_nxt_close_network_error_falls_back():
    with patch.object(prices, '_http_get', side_effect=Exception('net')), \
         patch.object(prices, '_fetch_nxt_pc_crawl', return_value=None):
        assert prices.fetch_nxt_close('005930') is None


# ─────────────────────────────────────────────────────────────
# 메모리 캐시 정리
# ─────────────────────────────────────────────────────────────
def test_prune_price_mem_cache_drops_expired():
    prices._price_mem_cache.clear()
    now = 1000.0
    for i in range(prices.PRICE_MEM_MAX + 5):
        prices._price_mem_cache[(f'C{i}', 'KRX')] = (1.0, now - prices.PRICE_MEM_TTL - 1)
    prices._price_mem_cache[('FRESH', 'KRX')] = (2.0, now)
    prices._prune_price_mem_cache(now)
    assert list(prices._price_mem_cache) == [('FRESH', 'KRX')]
    prices._price_mem_cache.clear()


def test_prune_price_mem_cache_noop_under_limit():
    prices._price_mem_cache.clear()
    prices._price_mem_cache[('OLD', 'KRX')] = (1.0, 0.0)
    prices._prune_price_mem_cache(9999.0)
    # 상한 미만이면 만료됐어도 굳이 훑지 않는다 (락 구간을 짧게 유지)
    assert ('OLD', 'KRX') in prices._price_mem_cache
    prices._price_mem_cache.clear()


def test_fetch_price_uses_mem_cache_only_when_allowed():
    prices._price_mem_cache.clear()
    prices._price_mem_cache[('005930', 'KRX')] = (95000.0, time.time())
    # 자동 폴링: 캐시 사용 → DB 접근 없음
    assert prices.fetch_price('005930', 'KRX', allow_cached=True) == ('005930', 95000.0)
    # 수동 새로고침: 캐시 우회 → 라이브 조회
    with patch.object(prices, '_get_db', return_value=MagicMock()), \
         patch.object(prices, '_fetch_price_uncached', return_value=96000.0):
        assert prices.fetch_price('005930', 'KRX', allow_cached=False) == ('005930', 96000.0)
    prices._price_mem_cache.clear()
