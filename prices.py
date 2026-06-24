"""현재가(시세) 조회 서비스.

기존 backend_app.py 의 ~230줄 단일 함수(get_current_price/fetch_price)를
provider 단위로 분해하고, 다음 성능 개선을 적용합니다.

  - 종목당 DB 커넥션 1개만 사용 (기존: 폴백 단계마다 새 연결 → 수십 회)
  - 단계별 HTTP timeout 단축 (3s → 2.5s)

폴백 우선순위 등 기존 동작 자체는 그대로 보존합니다. (장애 시 마지막 가격
유지는 DB price_cache 가 담당하므로 별도 메모리 캐시는 두지 않아 실패를 즉시
표면화합니다.)
get_db 는 순환 임포트를 피하기 위해 backend_app 에서 주입(set_db_provider)합니다.
"""
import re
import json
import time
import urllib.request
import datetime as _dt
import concurrent.futures

# ⭐️ 한국거래소(KRX) 휴장일 목록 (매년 연초에 갱신 필요)
KRX_HOLIDAYS = {
    (2026, 1, 1),    # 신정
    (2026, 2, 16),   # 설날 연휴
    (2026, 2, 17),   # 설날
    (2026, 2, 18),   # 설날 연휴
    (2026, 3, 2),    # 삼일절 대체공휴일 (3/1 일요일)
    (2026, 5, 1),    # 근로자의 날
    (2026, 5, 5),    # 어린이날
    (2026, 5, 25),   # 석가탄신일 대체공휴일 (5/24 일요일)
    (2026, 6, 3),    # 지방선거일
    (2026, 6, 6),    # 현충일 (토요일이지만 목록에 포함)
    (2026, 7, 17),   # 제헌절
    (2026, 8, 17),   # 광복절 대체공휴일 (8/15 토요일)
    (2026, 9, 24),   # 추석 연휴
    (2026, 9, 25),   # 추석
    (2026, 10, 5),   # 개천절 대체공휴일 (10/3 토요일)
    (2026, 10, 9),   # 한글날
    (2026, 12, 25),  # 성탄절
    (2026, 12, 31),  # 연말 휴장일
}

HTTP_TIMEOUT = 2.5      # 단계별 외부 API 호출 제한시간(초)

_MOBILE_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1',
    'Accept': 'application/json, text/plain, */*',
    'Referer': 'https://m.stock.naver.com/'
}
_PC_HEADERS = {'User-Agent': 'Mozilla/5.0'}
_YAHOO_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
}

# get_db 주입 지점 (backend_app 에서 설정) — 순환 임포트 방지
_get_db = None


def set_db_provider(fn):
    global _get_db
    _get_db = fn


# ─────────────────────────────────────────────────────────────
# DB 가격 캐시 (전일 종가/NXT 유지용) — 호출자가 연결을 전달
# ─────────────────────────────────────────────────────────────
def save_price_cache(conn, code_val, price_val, market_type='KRX'):
    try:
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
    time_num = kst.hour * 100 + kst.minute
    day_of_week = kst.weekday()
    is_holiday = (kst.year, kst.month, kst.day) in KRX_HOLIDAYS
    return (day_of_week >= 5) or is_holiday or not (900 <= time_num < 1530)


# ─────────────────────────────────────────────────────────────
# Provider 들
# ─────────────────────────────────────────────────────────────
def _fetch_gold(conn, code_str):
    """KRX 금현물(1g) 전용 처리."""
    try:
        url = "https://api.stock.naver.com/marketindex/metals/M04020000"
        req = urllib.request.Request(url, headers=_PC_HEADERS)
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as response:
            res_data = json.loads(response.read())
            price_str = res_data.get('closePrice', '')
            if price_str:
                price_val = float(price_str.replace(',', ''))
                save_price_cache(conn, code_str, price_val)
                return price_val
    except Exception:
        pass
    try:
        krx_url = "https://www.krx.co.kr/contents/COM/Finance/KRX_Gold_Market.jsp"
        krx_req = urllib.request.Request(krx_url, headers=_PC_HEADERS)
        with urllib.request.urlopen(krx_req, timeout=HTTP_TIMEOUT) as krx_res:
            html = krx_res.read().decode('utf-8', errors='ignore')
            match = re.search(r'현재가</th>\s*<td[^>]*>\s*<strong>([\d,]+)</strong>', html)
            if match:
                price_val = float(match.group(1).replace(',', ''))
                save_price_cache(conn, code_str, price_val)
                return price_val
    except Exception:
        pass
    return None


def _fetch_krx_realtime(code_str):
    """정규장 실시간 시세(PC siseJson). 모바일 API의 CDN 지연을 우회."""
    try:
        sise_url = f"https://api.finance.naver.com/siseJson.naver?symbol={code_str}&requestType=1"
        sise_req = urllib.request.Request(sise_url, headers=_PC_HEADERS)
        with urllib.request.urlopen(sise_req, timeout=HTTP_TIMEOUT) as sise_res:
            sise_data = sise_res.read().decode('euc-kr', errors='ignore')
            match = re.search(r'"nowVal"\s*:\s*(\d+)', sise_data)
            if match:
                val = float(match.group(1))
                if val > 0:
                    return val
    except Exception:
        pass
    return None


def _fetch_nxt_pc_crawl(code_str):
    """자정 이후 등 모바일 API가 비었을 때 PC 웹의 시간외단일가 크롤링."""
    try:
        pc_url = f"https://finance.naver.com/item/main.naver?code={code_str}"
        pc_req = urllib.request.Request(pc_url, headers=_PC_HEADERS)
        with urllib.request.urlopen(pc_req, timeout=HTTP_TIMEOUT) as pc_res:
            html = pc_res.read().decode('euc-kr', errors='ignore')
            nxt_area_match = re.search(r'시간외단일가.*?</table>', html, re.DOTALL)
            if nxt_area_match:
                nxt_html = nxt_area_match.group(0)
                if '거래 내역이 없습니다' not in nxt_html:
                    price_match = re.search(r'<span class="blind">([\d,]+)</span>', nxt_html)
                    if price_match:
                        return float(price_match.group(1).replace(',', ''))
    except Exception:
        pass
    return None


def _fetch_kr(conn, code_str, market_mode):
    """국내 주식 시세. 정규장/시간외(NXT) 분기 및 다단계 폴백을 수행."""
    out_of_hours = is_kr_out_of_hours()
    try:
        # 정규장 실시간 시세 (장중일 때만)
        realtime_krx_price = None if out_of_hours else _fetch_krx_realtime(code_str)

        # 네이버 모바일 기본 시세 (캐시 방지 파라미터 포함)
        ts = int(time.time() * 1000)
        url = f"https://m.stock.naver.com/api/stock/{code_str}/basic?_={ts}"
        req = urllib.request.Request(url, headers=_MOBILE_HEADERS)
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as response:
            res_data = json.loads(response.read())
            price_str = str(res_data.get('closePrice', ''))
            close_price = float(price_str.replace(',', '')) if price_str and price_str != '0' else None
            current_krx_price = realtime_krx_price if realtime_krx_price else close_price

            if market_mode == 'NXT':
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

                # 5) KRX 기본 시세로 폴백
                if current_krx_price:
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
    except Exception:
        # 통신 에러: 캐시를 최후 보루로
        if market_mode == 'NXT':
            cached = load_price_cache(conn, code_str, 'NXT')
            if cached:
                return cached
        cached = load_price_cache(conn, code_str, 'KRX')
        if cached:
            return cached
    return None


def _fetch_yahoo(conn, code_str):
    """해외/기타 종목 또는 국내 조회 실패 시 야후 파이낸스."""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{code_str}"
        req = urllib.request.Request(url, headers=_YAHOO_HEADERS)
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as response:
            res_data = json.loads(response.read())
            price = res_data['chart']['result'][0]['meta']['regularMarketPrice']
            save_price_cache(conn, code_str, float(price), 'KRX')
            return float(price)
    except Exception:
        pass
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
    if market_mode == 'NXT':
        cached = load_price_cache(conn, code_str, 'NXT')
        if cached:
            return cached
    cached = load_price_cache(conn, code_str, 'KRX')
    if cached:
        return cached
    return None


def fetch_price(code, market_mode='AUTO'):
    """단일 종목 현재가 조회. (code, price) 튜플 반환."""
    code_str = str(code).strip().upper()
    if not code_str:
        return None, None  # 빈 코드는 결과에서 제외 (호출자가 code is None 으로 필터)

    conn = None
    try:
        conn = _get_db()
        return code, _fetch_price_uncached(conn, code_str, market_mode)
    except Exception:
        return code, None
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def get_prices(codes, market_mode='AUTO'):
    """다수 종목 현재가를 스레드 풀로 병렬 조회하여 {code: price} 반환."""
    prices = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        results = executor.map(lambda c: fetch_price(c, market_mode), codes)
        for code, price in results:
            if code is not None:
                prices[code] = price
    return prices
