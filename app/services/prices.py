"""현재가(시세) 조회 서비스.

기존 backend_app.py 의 ~230줄 단일 함수(get_current_price/fetch_price)를
provider 단위로 분해하고, 다음 성능 개선을 적용합니다.

  - 종목당 DB 커넥션 1개만 사용 (기존: 폴백 단계마다 새 연결 → 수십 회)
  - 단계별 HTTP timeout 단축 (3s → 2.5s)

폴백 우선순위 등 기존 동작 자체는 그대로 보존합니다. (장애 시 마지막 가격
유지는 DB price_cache 가 담당하므로 별도 메모리 캐시는 두지 않아 실패를 즉시
표면화합니다.)

⭐️ 조회 실패는 화면에 "조회 실패" 로만 드러나 원인 추적이 불가능했으므로,
   단계별 실패는 debug, 종목 전체 실패는 warning 으로 남긴다. (logger 'prices')
"""
import re
import json
import time
import logging
import http.client
import threading
import datetime as _dt
import concurrent.futures
from urllib.parse import urlsplit

from app.database.db import get_db
from app.utils.memcache import TTLCache

logger = logging.getLogger('prices')

# ══════════════════════════════════════════════════════════════════════
# 한국거래소(KRX) 휴장일
# ══════════════════════════════════════════════════════════════════════
#
# ⭐️ 목록을 사람이 관리하지 않는다. `holidays` 패키지가 계산한다.
#
#    처음에는 이 집합이 소스에 박혀 있었고, 다음엔 data/krx_holidays.json 으로
#    옮겼다. 둘 다 **연초마다 사람이 손으로 채워 넣어야 한다**는 점에서 같은
#    물건이었다. 설날·추석은 음력 환산이 필요하고 대체공휴일 규칙도 매년 따져야
#    하니, 실제로는 미뤄지다 잊히고 그러면 공휴일을 정규장으로 오판한다.
#
#    holidays.KR() 은 음력 환산과 대체공휴일 규칙을 모두 계산한다. 다만 그것은
#    '국가 공휴일'이고 **거래소 휴장일과는 다르다** — 근로자의 날(5/1)은 법정
#    공휴일이 아니지만 KRX 는 쉬고, 연말 폐장일(12/31)도 공휴일이 아니지만 쉰다.
#    그 둘만 얹는다. (같은 규약을 my-stock-hts 의 api/market_calendar.py 가 쓴다)
#
# ⚠️ 임시공휴일은 패키지가 새 버전을 내야 반영된다. 기동할 때마다 자동
#    업그레이드하지는 않는다 — 휴장 판정 라이브러리가 매 기동 조용히 바뀌면
#    장 운영시간 판단이 사람 모르게 달라진다. 갱신은 tools/update_holidays.sh 를
#    주기 작업(주 1회 cron)으로 분리해 둔다.

# 국가 공휴일이 아니지만 KRX 가 쉬는 날 (월, 일) -> 이름
KRX_EXTRA_HOLIDAYS = {
    (5, 1): '근로자의 날',
    (12, 31): '연말 폐장일',
}

# 프런트엔드에 내려보낼 연도 범위 (오늘 기준 앞뒤로 이만큼)
HOLIDAY_YEARS_BACK = 1
HOLIDAY_YEARS_AHEAD = 1

_holiday_cache = {}          # year -> {(y, m, d): 이름}
_holiday_cache_lock = threading.Lock()
_holiday_warn_date = None


def _compute_holidays(year):
    """그 해의 KRX 휴장일 {(y,m,d): 이름}. 라이브러리가 없으면 빈 dict."""
    try:
        import holidays as _holidays_pkg
    except ImportError:
        return {}
    try:
        cal = _holidays_pkg.KR(years=year)
        for (month, day), name in KRX_EXTRA_HOLIDAYS.items():
            cal[_dt.date(year, month, day)] = name
        return {(d.year, d.month, d.day): str(nm)
                for d, nm in cal.items() if d.year == year}
    except Exception as e:
        logger.error("⚠️ %d년 휴장일 계산에 실패했습니다: %r", year, e)
        return {}


def holidays_for(year):
    """그 해의 휴장일 (연 단위 캐시). 프로세스가 사는 동안 유지된다."""
    with _holiday_cache_lock:
        cached = _holiday_cache.get(year)
    if cached is not None:
        return cached
    computed = _compute_holidays(year)
    with _holiday_cache_lock:
        _holiday_cache[year] = computed
    return computed


def holidays_available():
    """휴장일 라이브러리를 쓸 수 있는지."""
    try:
        import holidays  # noqa: F401
    except ImportError:
        return False
    return True


def holiday_version():
    try:
        import holidays
        return getattr(holidays, '__version__', '(버전 미상)')
    except ImportError:
        return None


def is_market_holiday(date_obj):
    """그 날짜가 KRX 휴장일인지 (주말은 보지 않는다 — 호출자가 따로 판정한다)."""
    return (date_obj.year, date_obj.month, date_obj.day) in holidays_for(date_obj.year)


def _holiday_years(today=None):
    today = today or _dt.date.today()
    return range(today.year - HOLIDAY_YEARS_BACK, today.year + HOLIDAY_YEARS_AHEAD + 1)


def holiday_list(today=None):
    """프런트엔드에 내려보낼 휴장일 (YYYY-MM-DD 문자열, 정렬됨).

    화면의 getMarketStatus() 가 이 목록으로 공휴일 폴링을 멈춘다. 라이브러리가
    임의의 연도를 계산할 수 있으므로 '등록된 마지막 해' 개념은 사라졌고,
    오늘 기준 앞뒤 1년만 내려보낸다 (응답 크기와 실용성의 절충).
    """
    days = []
    for year in _holiday_years(today):
        days.extend(holidays_for(year))
    return ['%04d-%02d-%02d' % ymd for ymd in sorted(days)]


def holiday_coverage(today=None):
    """휴장일 판정 상태 요약 — (요약문, 심각도). bootstrap 이 기동 로그에 남긴다.

    심각도: 'ok' | 'error'. 예전에는 '목록이 몇 년까지 등록됐는가'를 봤지만,
    이제 그 축은 없어졌다. 대신 **라이브러리가 실제로 있는가**를 본다 —
    없으면 모든 평일이 정규장으로 판정되므로 조용히 틀린다.
    """
    today = today or _dt.date.today()
    version = holiday_version()
    if version is None:
        return ("휴장일 라이브러리(holidays)가 설치되지 않았습니다 — 모든 평일을 "
                "정규장으로 봅니다. `pip install -r requirements.txt` 를 실행하세요."), 'error'

    this_year = holidays_for(today.year)
    if not this_year:
        return (f"holidays {version} 이 {today.year}년 휴장일을 하나도 계산하지 "
                f"못했습니다 — 패키지 상태를 확인하세요."), 'error'

    upcoming = sum(1 for ymd in this_year if _dt.date(*ymd) >= today)
    return (f"휴장일 판정: holidays {version} "
            f"({today.year}년 {len(this_year)}건, 앞으로 {upcoming}일 남음)"), 'ok'


def _warn_if_holidays_unavailable(kst):
    """휴장일 판정이 불가능하면 하루 1회만 경고한다.

    라이브러리가 없으면 모든 공휴일이 '정규장'으로 판정되므로, 갱신을 잊으면
    실시간 시세를 무의미하게 호출하면서도 아무도 알아채지 못한다.
    """
    global _holiday_warn_date
    if holidays_for(kst.year):
        return
    today = (kst.year, kst.month, kst.day)
    if _holiday_warn_date == today:
        return
    _holiday_warn_date = today
    logger.warning(
        "⚠️ %d년 휴장일을 계산하지 못했습니다 (holidays 패키지 상태를 확인하세요). "
        "공휴일이 정규장으로 오판됩니다.", kst.year)


HTTP_TIMEOUT = 2.5      # 단계별 외부 API 호출 제한시간(초)

# ⭐️ price_cache 의 기본 슬롯 이름. 'NXT'(시간외단일가)와 구분하기 위한 값일
#    뿐이며 국내/해외를 가리지 않는 "정규장 기준가" 슬롯이다. 저장·조회가
#    같은 이름을 쓰기만 하면 되므로 해외 종목도 이 슬롯을 사용한다.
DEFAULT_CACHE_MARKET = 'KRX'

_MOBILE_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1',
    'Accept': 'application/json, text/plain, */*',
    'Referer': 'https://m.stock.naver.com/'
}
_PC_HEADERS = {'User-Agent': 'Mozilla/5.0'}
_YAHOO_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
}

# ─────────────────────────────────────────────────────────────
# HTTP Keep-Alive 커넥션 풀 (표준 라이브러리 http.client 기반)
#   - urllib 은 호출마다 새 소켓/ TLS 핸드셰이크를 수행하지만,
#     여기서는 (스레드, 호스트)별 커넥션을 재사용해 핸드셰이크 비용을 제거합니다.
#   - 워커 스레드가 _executor 로 상주하므로 60초 폴링 사이에도 커넥션이 유지됩니다.
#   - http.client 는 응답 본문을 완전히 읽은 뒤에만 재사용 가능하므로
#     _http_get 은 항상 body 를 끝까지 읽어 반환합니다.
# ─────────────────────────────────────────────────────────────
_conn_pool = threading.local()


def _make_conn(scheme, host):
    if scheme == 'https':
        return http.client.HTTPSConnection(host, timeout=HTTP_TIMEOUT)
    return http.client.HTTPConnection(host, timeout=HTTP_TIMEOUT)


def _http_get(url, headers):
    """Keep-Alive 커넥션을 재사용하는 GET. 응답 본문(bytes)을 반환합니다.

    끊긴(stale) 커넥션이면 1회 폐기 후 재연결하여 재시도합니다.
    """
    parts = urlsplit(url)
    host = parts.netloc
    path = parts.path + (('?' + parts.query) if parts.query else '')

    conns = getattr(_conn_pool, 'conns', None)
    if conns is None:
        conns = {}
        _conn_pool.conns = conns

    req_headers = dict(headers)
    req_headers.setdefault('Connection', 'keep-alive')

    for attempt in (0, 1):
        conn = conns.get(host)
        if conn is None:
            conn = _make_conn(parts.scheme, host)
            conns[host] = conn
        try:
            conn.request('GET', path, headers=req_headers)
            resp = conn.getresponse()
            return resp.read()  # 본문을 끝까지 읽어야 커넥션 재사용 가능
        except Exception:
            # stale/오류 커넥션 폐기 후 새 커넥션으로 1회 재시도
            try:
                conn.close()
            except Exception:
                pass
            conns.pop(host, None)
            if attempt == 1:
                raise
    return b''


# 시세 병렬 조회용 상주 스레드 풀 — 워커가 살아있어 Keep-Alive 커넥션이 폴링
# 주기 사이에도 유지됩니다. (요청마다 풀을 새로 만들면 커넥션도 매번 폐기됨)
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=10)


# ─────────────────────────────────────────────────────────────
# DB 가격 캐시 (전일 종가/NXT 유지용) — 호출자가 연결을 전달
# ─────────────────────────────────────────────────────────────
def save_price_cache(conn, code_val, price_val, market_type='KRX'):
    try:
        # ⭐️ 가격이 직전 저장값과 동일하면 쓰기 생략 — 60초 폴링마다 종목 수만큼
        #    REPLACE+commit 이 발생해 SD카드(라즈베리파이)에 상시 쓰기가 쌓이는 것을 방지.
        #    시세는 대부분 직전 값과 같으므로 실제 쓰기는 가격 변동 시에만 일어난다.
        cur = conn.execute(
            "SELECT price FROM price_cache WHERE code = ? AND market_type = ?",
            (code_val, market_type))
        row = cur.fetchone()
        if row is not None and row['price'] == price_val:
            return
        now_str = _dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn.execute(
            "REPLACE INTO price_cache (code, market_type, price, updated_at) VALUES (?, ?, ?, ?)",
            (code_val, market_type, price_val, now_str))
        conn.commit()
    except Exception:
        pass


def load_price_cache(conn, code_val, market_type='KRX'):
    try:
        cur = conn.execute(
            "SELECT price FROM price_cache WHERE code = ? AND market_type = ?",
            (code_val, market_type))
        row = cur.fetchone()
        if row:
            return row['price']
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────
# 시장(국가) 구분 및 장중 여부 판정
# ─────────────────────────────────────────────────────────────
def detect_market(code_str):
    if re.fullmatch(r'^[A-Z\.\-]{1,6}$', code_str):
        return "US"
    if len(code_str) == 6 and re.fullmatch(r'^\d{5}[0-9A-Z]$', code_str):
        return "KR"
    if len(code_str) == 6 and code_str.isalnum():
        return "KR"  # 예외: 0162Z0 등 영문 혼합 국내 신주인수권/ETN 포괄용
    if re.fullmatch(r'^\d+$', code_str):
        return "OTHER_ASIAN"
    return "UNKNOWN"


def is_kr_out_of_hours(now_kst=None):
    """KRX 정규장(평일 09:00~15:30, 공휴일 제외) 시간 밖인지 판정."""
    kst = now_kst or (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=9))
    _warn_if_holidays_unavailable(kst)
    time_num = kst.hour * 100 + kst.minute
    day_of_week = kst.weekday()
    is_holiday = is_market_holiday(kst)
    return (day_of_week >= 5) or is_holiday or not (900 <= time_num < 1530)


def is_nxt_mode(market_mode):
    """시간외단일가(NXT) 모드인지 여부.

    'NXT' 외의 값(KRX/AUTO/미지정)은 모두 정규장 기준으로 취급한다.
    즉 'AUTO' 는 별도 자동 판정이 아니라 KRX 와 동일한 동작이다.
    """
    return str(market_mode).strip().upper() == 'NXT'


# ─────────────────────────────────────────────────────────────
# Provider 들
# ─────────────────────────────────────────────────────────────
def _fetch_gold(conn, code_str):
    """KRX 금현물(1g) 전용 처리."""
    try:
        url = "https://api.stock.naver.com/marketindex/metals/M04020000"
        res_data = json.loads(_http_get(url, _PC_HEADERS))
        price_str = res_data.get('closePrice', '')
        if price_str:
            price_val = float(price_str.replace(',', ''))
            save_price_cache(conn, code_str, price_val)
            return price_val
    except Exception as e:
        logger.debug("금현물 네이버 조회 실패 (%s): %r", code_str, e)
    try:
        krx_url = "https://www.krx.co.kr/contents/COM/Finance/KRX_Gold_Market.jsp"
        html = _http_get(krx_url, _PC_HEADERS).decode('utf-8', errors='ignore')
        match = re.search(r'현재가</th>\s*<td[^>]*>\s*<strong>([\d,]+)</strong>', html)
        if match:
            price_val = float(match.group(1).replace(',', ''))
            save_price_cache(conn, code_str, price_val)
            return price_val
    except Exception as e:
        logger.debug("금현물 KRX 크롤링 실패 (%s): %r", code_str, e)
    logger.warning("⚠️ 금현물 시세를 모든 경로에서 가져오지 못했습니다 (%s)", code_str)
    return None


def _fetch_krx_realtime(code_str):
    """정규장 실시간 시세(PC siseJson). 모바일 API의 CDN 지연을 우회."""
    try:
        sise_url = f"https://api.finance.naver.com/siseJson.naver?symbol={code_str}&requestType=1"
        sise_data = _http_get(sise_url, _PC_HEADERS).decode('euc-kr', errors='ignore')
        match = re.search(r'"nowVal"\s*:\s*(\d+)', sise_data)
        if match:
            val = float(match.group(1))
            if val > 0:
                return val
    except Exception as e:
        logger.debug("정규장 실시간(siseJson) 조회 실패 (%s): %r", code_str, e)
    return None


def _fetch_nxt_pc_crawl(code_str):
    """자정 이후 등 모바일 API가 비었을 때 PC 웹의 시간외단일가 크롤링."""
    try:
        pc_url = f"https://finance.naver.com/item/main.naver?code={code_str}"
        html = _http_get(pc_url, _PC_HEADERS).decode('euc-kr', errors='ignore')
        nxt_area_match = re.search(r'시간외단일가.*?</table>', html, re.DOTALL)
        if nxt_area_match:
            nxt_html = nxt_area_match.group(0)
            if '거래 내역이 없습니다' not in nxt_html:
                price_match = re.search(r'<span class="blind">([\d,]+)</span>', nxt_html)
                if price_match:
                    return float(price_match.group(1).replace(',', ''))
    except Exception as e:
        logger.debug("시간외단일가 PC 크롤링 실패 (%s): %r", code_str, e)
    return None


def fetch_nxt_close(code_str):
    """시간외단일가(NXT)만 조회한다. DB 를 건드리지 않고 가격만 반환.

    백그라운드 캐싱 잡이 쓰는 진입점. 모바일 API 의 overMarketPriceInfo 를
    먼저 보고, 비어 있으면 PC 웹 크롤링으로 폴백한다. (_fetch_kr 의 NXT
    단계와 같은 소스를 쓰므로 네이버 스펙 변경 시 고칠 곳이 한 군데다.)
    """
    try:
        ts = int(time.time() * 1000)
        url = f"https://m.stock.naver.com/api/stock/{code_str}/basic?_={ts}"
        res_data = json.loads(_http_get(url, _MOBILE_HEADERS))
        over_info = res_data.get('overMarketPriceInfo', {})
        if isinstance(over_info, dict) and over_info.get('overPrice'):
            return float(str(over_info.get('overPrice')).replace(',', ''))
    except Exception as e:
        logger.debug("시간외단일가 모바일 API 조회 실패 (%s): %r", code_str, e)
    return _fetch_nxt_pc_crawl(code_str)


def _fetch_kr(conn, code_str, market_mode):
    """국내 주식 시세. 정규장/시간외(NXT) 분기 및 다단계 폴백을 수행."""
    out_of_hours = is_kr_out_of_hours()
    try:
        # 정규장 실시간 시세 (장중일 때만)
        realtime_krx_price = None if out_of_hours else _fetch_krx_realtime(code_str)

        # ⭐️ 장중 실시간 시세 성공 시 즉시 반환 — 기존에는 모바일 기본 시세까지
        #    항상 호출한 뒤 실시간 값을 우선 반환했으므로, KRX/NXT 모든 모드에서
        #    동작은 동일하고 장중 외부 HTTP 요청만 절반으로 줄어든다.
        if realtime_krx_price:
            save_price_cache(conn, code_str, realtime_krx_price, 'KRX')
            return realtime_krx_price

        # 네이버 모바일 기본 시세 (캐시 방지 파라미터 포함)
        ts = int(time.time() * 1000)
        url = f"https://m.stock.naver.com/api/stock/{code_str}/basic?_={ts}"
        res_data = json.loads(_http_get(url, _MOBILE_HEADERS))
        price_str = str(res_data.get('closePrice', ''))
        close_price = float(price_str.replace(',', '')) if price_str and price_str != '0' else None
        current_krx_price = close_price

        if is_nxt_mode(market_mode):
            # 1) 정규장에는 무조건 KRX 실시간 우선
            if not out_of_hours and current_krx_price:
                save_price_cache(conn, code_str, current_krx_price, 'KRX')
                return current_krx_price

            # 2) 장외 시간: NXT 시세 시도
            over_info = res_data.get('overMarketPriceInfo', {})
            if isinstance(over_info, dict) and over_info.get('overPrice'):
                nxt_price = float(str(over_info.get('overPrice')).replace(',', ''))
                save_price_cache(conn, code_str, nxt_price, 'NXT')
                return nxt_price

            # 3) 모바일 API가 비었으면 PC 크롤링
            nxt_price = _fetch_nxt_pc_crawl(code_str)
            if nxt_price:
                save_price_cache(conn, code_str, nxt_price, 'NXT')
                return nxt_price

            # 4) NXT 전용 캐시
            cached_nxt = load_price_cache(conn, code_str, 'NXT')
            if cached_nxt:
                return cached_nxt

            # 5) KRX 기본 시세로 폴백 (NXT 슬롯을 오염시키지 않도록 KRX 로 저장)
            if current_krx_price:
                save_price_cache(conn, code_str, current_krx_price, 'KRX')
                return current_krx_price

            # 6) KRX 캐시로 최종 방어
            cached_krx = load_price_cache(conn, code_str, 'KRX')
            if cached_krx:
                return cached_krx
        else:
            # KRX 모드: NXT 무시, KRX 가격만
            if current_krx_price:
                save_price_cache(conn, code_str, current_krx_price, 'KRX')
                return current_krx_price

        if current_krx_price:
            return current_krx_price
    except Exception as e:
        # 통신 에러: 캐시를 최후 보루로
        logger.debug("국내 시세 조회 실패 (%s, mode=%s): %r", code_str, market_mode, e)
        if is_nxt_mode(market_mode):
            cached = load_price_cache(conn, code_str, 'NXT')
            if cached:
                logger.info("ℹ️ %s 시세를 NXT 캐시로 대체합니다 (통신 오류).", code_str)
                return cached
        cached = load_price_cache(conn, code_str, 'KRX')
        if cached:
            logger.info("ℹ️ %s 시세를 KRX 캐시로 대체합니다 (통신 오류).", code_str)
            return cached
    return None


# ⭐️ 야후는 국내/아시아 종목을 접미사 없는 코드로는 찾지 못한다(005930 → 404).
#    시장별 후보 접미사를 순서대로 시도하고, 성공한 심볼은 기억해 두어 다음
#    조회부터는 곧바로 맞는 심볼 하나만 호출한다. (불필요한 404 왕복 제거)
_YAHOO_SUFFIXES = {
    'KR': ('.KS', '.KQ'),           # 코스피 / 코스닥
    'OTHER_ASIAN': ('.T', '.HK'),   # 일본(도쿄) / 홍콩
}
_yahoo_symbol_hint = {}  # code_str -> 실제로 성공했던 야후 심볼


def _yahoo_symbol_candidates(code_str):
    """야후에 질의할 심볼 후보를 우선순위대로 반환."""
    hint = _yahoo_symbol_hint.get(code_str)
    if hint:
        return [hint]
    suffixes = _YAHOO_SUFFIXES.get(detect_market(code_str))
    if not suffixes:
        return [code_str]
    # 접미사 후보를 먼저, 마지막으로 원본 코드(접미사 없는 심볼)도 시도
    return [code_str + sfx for sfx in suffixes] + [code_str]


def _fetch_yahoo(conn, code_str):
    """해외/기타 종목 또는 국내 조회 실패 시 야후 파이낸스."""
    for symbol in _yahoo_symbol_candidates(code_str):
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
            res_data = json.loads(_http_get(url, _YAHOO_HEADERS))
            price = res_data['chart']['result'][0]['meta']['regularMarketPrice']
            if price is None:
                continue
            _yahoo_symbol_hint[code_str] = symbol
            save_price_cache(conn, code_str, float(price), DEFAULT_CACHE_MARKET)
            return float(price)
        except Exception as e:
            logger.debug("야후 조회 실패 (%s): %r", symbol, e)
    # 기억해 둔 심볼이 더 이상 통하지 않으면 다음 호출에서 전체 후보를 다시 시도
    _yahoo_symbol_hint.pop(code_str, None)
    return None


def _fetch_price_uncached(conn, code_str, market_mode):
    """메모리 캐시를 제외한 실제 조회 로직 (DB 캐시 포함)."""
    if code_str in ['KRXGOLD', 'GOLD']:
        return _fetch_gold(conn, code_str)

    market_type = detect_market(code_str)

    if market_type == "KR":
        price = _fetch_kr(conn, code_str, market_mode)
        if price is not None:
            return price

    # US / OTHER_ASIAN / UNKNOWN 또는 국내 조회 실패 시 야후
    price = _fetch_yahoo(conn, code_str)
    if price is not None:
        return price

    # 최후 보루: 캐시
    if is_nxt_mode(market_mode):
        cached = load_price_cache(conn, code_str, 'NXT')
        if cached:
            logger.info("ℹ️ %s 시세를 NXT 캐시로 대체합니다 (라이브 조회 전부 실패).", code_str)
            return cached
    cached = load_price_cache(conn, code_str, DEFAULT_CACHE_MARKET)
    if cached:
        logger.info("ℹ️ %s 시세를 캐시로 대체합니다 (라이브 조회 전부 실패).", code_str)
        return cached
    logger.warning("⚠️ %s(%s) 시세를 모든 경로에서 가져오지 못했습니다 (mode=%s).",
                   code_str, market_type, market_mode)
    return None


# ─────────────────────────────────────────────────────────────
# 자동 폴링 전용 단기 메모리 캐시
#   - 여러 탭/기기(폰+PC)가 동시에 60초 폴링할 때 외부 API 중복 호출을 흡수한다.
#   - ⭐️ 수동 새로고침(allow_cached=False, 기본값)은 이 캐시를 완전히 우회하여
#     항상 실제 현재 시세를 다시 조회한다. (사용자가 "진짜 현재가"를 신뢰할 수 있어야 함)
#   - 수동/자동 어느 쪽이든 라이브 조회 성공 값은 캐시에 갱신해 둔다.
# ─────────────────────────────────────────────────────────────
# ⭐️ TTL 은 폴링 주기(60초)보다 조금만 짧게 둔다. 25초로 두면 기기별 폴링
#    시점이 30초쯤 어긋날 때 대부분 캐시 미스가 되어 "중복 호출 흡수"라는
#    목적을 달성하지 못한다. 50초면 한 주기 안의 중복만 흡수하고 다음 주기는
#    반드시 새로 조회하므로 신선도도 유지된다.
PRICE_MEM_TTL = 50  # 초
PRICE_MEM_MAX = 500  # 항목 수 상한 — 넘으면 만료분부터 정리
_price_mem_cache = TTLCache(PRICE_MEM_TTL, PRICE_MEM_MAX, name='prices')


def fetch_price(code, market_mode='AUTO', allow_cached=False):
    """단일 종목 현재가 조회. (code, price) 튜플 반환.

    allow_cached=True(자동 폴링)일 때만 단기 메모리 캐시를 조회하며,
    수동 새로고침은 항상 외부 API 라이브 조회를 수행한다.
    """
    code_str = str(code).strip().upper()
    if not code_str:
        return None, None  # 빈 코드는 결과에서 제외 (호출자가 code is None 으로 필터)

    if allow_cached:
        cached = _price_mem_cache.get((code_str, market_mode))
        if cached is not None:
            return code, cached

    conn = None
    try:
        conn = get_db()
        price = _fetch_price_uncached(conn, code_str, market_mode)
        if price is not None:
            _price_mem_cache.put((code_str, market_mode), price)
        return code, price
    except Exception as e:
        logger.warning("⚠️ %s 시세 조회 중 예외: %r", code_str, e)
        return code, None
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def get_prices(codes, market_mode='AUTO', allow_cached=False):
    """다수 종목 현재가를 상주 스레드 풀로 병렬 조회하여 {code: price} 반환."""
    prices = {}
    results = _executor.map(lambda c: fetch_price(c, market_mode, allow_cached), codes)
    for code, price in results:
        if code is not None:
            prices[code] = price
    return prices
