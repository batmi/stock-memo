"""prices.py(시세 조회 서비스) 모듈의 단위 테스트.

Flask 라우트를 거치지 않고 provider 함수들을 직접 호출하여
시장 판정/장중 판정/DB 캐시/HTTP keep-alive/다단계 폴백 분기를 검증합니다.
"""
import os
import sys
import sqlite3
import datetime as _dt
from unittest.mock import patch, MagicMock

import pytest

# prices 모듈을 임포트할 수 있도록 상위 경로 추가
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services import prices


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
        # 연결 자체가 죽었을 때 _http_get 이 조용히 None 을 돌려주면
        # 호출부가 "가격 없음"으로 오해한다. 반드시 예외로 터져야 한다.
        with pytest.raises(Exception, match='down'):
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


# ─────────────────────────────────────────────────────────────
# _fetch_gold
# ─────────────────────────────────────────────────────────────
def test_fetch_gold_naver_success(conn):
    with patch.object(prices, '_http_get', return_value=b'{"closePrice": "88,000"}'):
        assert prices._fetch_gold(conn, 'KRXGOLD') == 88000.0
    # 성공 시 캐시에 저장됨
    assert prices.load_price_cache(conn, 'KRXGOLD', 'KRX') == 88000.0


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


def test_fetch_kr_nxt_mode_cached_nxt_fallback(conn):
    # NXT 모드 + 모든 NXT 소스 실패 → NXT 캐시
    prices.save_price_cache(conn, '005930', 70000.0, 'NXT')
    body = b'{"closePrice": "0"}'  # close_price None 처리
    with patch.object(prices, 'is_kr_out_of_hours', return_value=True), \
         patch.object(prices, '_http_get', return_value=body):
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
    with patch.object(prices, 'get_db', return_value=conn), \
         patch.object(prices, '_fetch_price_uncached', return_value=95000.0):
        code, price = prices.fetch_price('005930')
    assert code == '005930'
    assert price == 95000.0


def test_fetch_price_db_error_returns_none():
    with patch.object(prices, 'get_db', side_effect=Exception("no db")):
        code, price = prices.fetch_price('005930')
    assert code == '005930'
    assert price is None


def test_fetch_price_closes_connection():
    fake_conn = MagicMock()
    with patch.object(prices, 'get_db', return_value=fake_conn), \
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


# ─────────────────────────────────────────────────────────────
# 휴장일 — holidays 패키지가 계산한다 (사람이 목록을 관리하지 않는다)
#
# ⭐️ 예전에는 소스에 박힌 집합이었고, 다음엔 data/krx_holidays.json 이었다.
#    둘 다 연초마다 사람이 손으로 채워야 한다는 점에서 같은 물건이었고, 실제로
#    미뤄지다 잊히면 공휴일을 정규장으로 오판했다. 이제 라이브러리가 음력 환산과
#    대체공휴일 규칙을 계산하고, 거래소 고유 휴장일 2건만 얹는다.
# ─────────────────────────────────────────────────────────────
def test_lunar_and_substitute_holidays_are_computed():
    """손으로 넣을 수 없던 것들 — 음력 기반 설날·추석과 대체공휴일."""
    assert prices.is_market_holiday(_dt.date(2026, 2, 17))   # 설날 (음력)
    assert prices.is_market_holiday(_dt.date(2026, 9, 25))   # 추석 (음력)
    assert prices.is_market_holiday(_dt.date(2026, 3, 2))    # 삼일절 대체공휴일
    assert prices.is_market_holiday(_dt.date(2026, 8, 17))   # 광복절 대체공휴일
    assert not prices.is_market_holiday(_dt.date(2026, 2, 19))


def test_future_years_need_no_manual_entry():
    """다음 해도 손대지 않고 나온다 — 이게 라이브러리로 옮긴 이유다."""
    assert prices.is_market_holiday(_dt.date(2027, 1, 1))    # 신정
    assert prices.is_market_holiday(_dt.date(2027, 2, 6))    # 설날 (음력)
    assert prices.holidays_for(2028), "2028년도 계산돼야 한다"


def test_krx_only_holidays_are_added_on_top():
    """근로자의 날·연말 폐장일은 국가 공휴일이 아니지만 KRX 는 쉰다."""
    for year in (2026, 2027):
        assert prices.is_market_holiday(_dt.date(year, 5, 1)), f"{year} 근로자의 날"
        assert prices.is_market_holiday(_dt.date(year, 12, 31)), f"{year} 연말 폐장일"


def test_holiday_list_covers_a_window_around_today():
    """프런트에 내려보내는 목록은 오늘 기준 앞뒤 1년."""
    today = _dt.date(2026, 6, 15)
    days = prices.holiday_list(today)
    years = {d[:4] for d in days}
    assert years == {'2025', '2026', '2027'}
    assert days == sorted(days)
    assert '2026-09-25' in days


def test_holidays_are_cached_per_year(monkeypatch):
    """연 단위로 한 번만 계산한다 (요청마다 라이브러리를 다시 돌리지 않는다)."""
    prices._holiday_cache.clear()
    calls = []
    real = prices._compute_holidays
    monkeypatch.setattr(prices, '_compute_holidays',
                        lambda y: (calls.append(y), real(y))[1])
    for _ in range(50):
        prices.is_market_holiday(_dt.date(2026, 9, 25))
    assert calls == [2026], f"연 단위 캐시가 동작하지 않습니다: {calls}"


def test_missing_library_degrades_without_crashing(monkeypatch):
    """라이브러리가 없어도 기동을 막지 않는다 — 휴장일을 모를 뿐이다."""
    prices._holiday_cache.clear()
    monkeypatch.setattr(prices, '_compute_holidays', lambda year: {})
    assert prices.is_market_holiday(_dt.date(2026, 9, 25)) is False
    assert prices.holiday_list(_dt.date(2026, 6, 15)) == []
    prices._holiday_cache.clear()


def test_broken_library_call_is_logged_not_raised(monkeypatch, caplog):
    """라이브러리가 예외를 던져도 조회 경로가 터지면 안 된다."""
    prices._holiday_cache.clear()
    import holidays as pkg
    monkeypatch.setattr(pkg, 'KR', lambda **kw: (_ for _ in ()).throw(RuntimeError('boom')))
    with caplog.at_level('ERROR', logger='prices'):
        assert prices._compute_holidays(2026) == {}
    assert any('휴장일 계산에 실패' in r.getMessage() for r in caplog.records)
    prices._holiday_cache.clear()


# ── 판정 불가 상태를 조용히 넘기지 않는다 ──────────────────────────
def test_coverage_is_ok_when_the_library_works():
    msg, severity = prices.holiday_coverage(_dt.date(2026, 6, 15))
    assert severity == 'ok'
    assert 'holidays' in msg and '2026' in msg


def test_coverage_is_an_error_when_the_library_is_missing(monkeypatch):
    monkeypatch.setattr(prices, 'holiday_version', lambda: None)
    msg, severity = prices.holiday_coverage(_dt.date(2026, 6, 15))
    assert severity == 'error'
    assert '설치되지 않았습니다' in msg


def test_coverage_is_an_error_when_nothing_is_computed(monkeypatch):
    prices._holiday_cache.clear()
    monkeypatch.setattr(prices, '_compute_holidays', lambda year: {})
    msg, severity = prices.holiday_coverage(_dt.date(2026, 6, 15))
    assert severity == 'error'
    assert '하나도 계산하지 못했습니다' in msg
    prices._holiday_cache.clear()


def test_unavailable_holidays_warn_once_per_day(monkeypatch, caplog):
    """판정이 불가능하면 하루 1회 경고 — 조용히 틀리게 두지 않는다."""
    prices._holiday_cache.clear()
    prices._holiday_warn_date = None
    monkeypatch.setattr(prices, '_compute_holidays', lambda year: {})
    when = _dt.datetime(2026, 3, 4, 10, 0)
    with caplog.at_level('WARNING', logger='prices'):
        prices.is_kr_out_of_hours(when)
        prices.is_kr_out_of_hours(when)      # 같은 날 재호출은 침묵
    warnings = [r for r in caplog.records if '휴장일을 계산하지 못했습니다' in r.getMessage()]
    assert len(warnings) == 1
    prices._holiday_warn_date = None
    prices._holiday_cache.clear()


def test_working_holidays_do_not_warn(caplog):
    prices._holiday_warn_date = None
    with caplog.at_level('WARNING', logger='prices'):
        prices.is_kr_out_of_hours(_dt.datetime(2026, 3, 4, 10, 0))
    assert not [r for r in caplog.records if '계산하지 못했습니다' in r.getMessage()]



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


def test_fetch_nxt_close_network_error_falls_back():
    with patch.object(prices, '_http_get', side_effect=Exception('net')):
        assert prices.fetch_nxt_close('005930') is None


# ─────────────────────────────────────────────────────────────
# 자동 폴링 전용 단기 메모리 캐시
#   (TTL·상한·정리 규칙 자체는 tests/test_memcache.py 가 본다)
# ─────────────────────────────────────────────────────────────
def test_fetch_price_uses_mem_cache_only_when_allowed():
    prices._price_mem_cache.clear()
    prices._price_mem_cache.put(('005930', 'KRX'), 95000.0)
    # 자동 폴링: 캐시 사용 → DB 접근 없음
    assert prices.fetch_price('005930', 'KRX', allow_cached=True) == ('005930', 95000.0)
    # 수동 새로고침: 캐시 우회 → 라이브 조회
    with patch.object(prices, 'get_db', return_value=MagicMock()), \
         patch.object(prices, '_fetch_price_uncached', return_value=96000.0):
        assert prices.fetch_price('005930', 'KRX', allow_cached=False) == ('005930', 96000.0)
    prices._price_mem_cache.clear()


def test_live_lookup_refreshes_the_cache_for_the_next_poll():
    """수동 조회가 캐시를 우회하더라도, 얻은 값은 캐시에 갱신해 둔다."""
    prices._price_mem_cache.clear()
    with patch.object(prices, 'get_db', return_value=MagicMock()), \
         patch.object(prices, '_fetch_price_uncached', return_value=96000.0):
        prices.fetch_price('005930', 'KRX', allow_cached=False)
    assert prices._price_mem_cache.get(('005930', 'KRX')) == 96000.0
    prices._price_mem_cache.clear()


def test_failed_lookup_does_not_poison_the_cache():
    """조회 실패(None)를 캐시하면 그 종목이 TTL 동안 '조회 실패'로 굳는다."""
    prices._price_mem_cache.clear()
    with patch.object(prices, 'get_db', return_value=MagicMock()), \
         patch.object(prices, '_fetch_price_uncached', return_value=None):
        assert prices.fetch_price('005930', 'KRX', allow_cached=False) == ('005930', None)
    assert prices._price_mem_cache.get(('005930', 'KRX')) is None
    assert len(prices._price_mem_cache) == 0
    prices._price_mem_cache.clear()
