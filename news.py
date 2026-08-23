"""종목 뉴스 조회 서비스 — 구글 뉴스 RSS.

⭐️ 예전에는 이 전부가 api.py 의 라우트 핸들러 **안에** 있었다. HTTP 호출·XML
   파싱·캐시·스레드풀이 한 함수에 뭉쳐 있어서, 뉴스 출처를 바꾸면(네이버 RSS
   전면 종료로 실제로 한 번 바꿨다) 라우트 파일을 건드려야 했다. 외부 시세
   연동을 prices.py 가 갖고 있는 것과 같은 이유로 여기로 분리한다.
   api.py 는 이제 요청을 읽고 fetch_many() 를 부르기만 한다.
"""

import concurrent.futures
import logging
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

logger = logging.getLogger('news')

# 검색어(종목명)당 결과를 임시 보관한다.
CACHE_TTL = 600   # 10분
CACHE_MAX = 300   # ⭐️ 종목명이 키라 상시 구동 시 무한히 쌓인다. 넘으면 만료분부터 정리.
MAX_ITEMS_PER_STOCK = 5
FETCH_TIMEOUT = 3
MAX_PARALLEL = 10

# 보유 종목이 없을 때 대신 쓰는 검색어
DEFAULT_QUERY = '국내 증시'

_cache = {}       # 종목명 -> (뉴스목록, 저장시각)
_cache_lock = threading.Lock()


def _prune(now_ts):
    """만료된 항목 정리. (상한을 넘었을 때만 훑는다)"""
    with _cache_lock:
        if len(_cache) <= CACHE_MAX:
            return
        for key in [k for k, v in _cache.items() if (now_ts - v[1]) >= CACHE_TTL]:
            _cache.pop(key, None)


def _fetch_rss(stock):
    """구글 뉴스 RSS 에서 최근 7일 기사를 가져온다. 실패하면 (None) 을 돌려준다.

    ⭐️ 네이버 RSS 서비스 전면 종료(404)에 따라 안정적인 구글 뉴스 RSS로 복귀했다.
    """
    query = urllib.parse.quote(f"{stock} when:7d")
    ts = int(time.time() * 1000)
    url = (f"https://news.google.com/rss/search?q={query}"
           f"&hl=ko&gl=KR&ceid=KR:ko&_={ts}")
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})

    news_list = []
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as response:
            root = ET.fromstring(response.read())
            for idx, item in enumerate(root.findall('.//item')):
                if idx >= MAX_ITEMS_PER_STOCK:
                    break
                title_elem = item.find('title')
                link_elem = item.find('link')
                pub_elem = item.find('pubDate')
                news_list.append({
                    'stock': stock,
                    'title': title_elem.text if title_elem is not None else '',
                    'link': link_elem.text if link_elem is not None else '',
                    'pubDate': pub_elem.text if pub_elem is not None else '',
                })
    except Exception as e:
        logger.error(f"Error fetching Google news for {stock}: {e}")
        return None
    return news_list


def fetch_one(stock, force_refresh=False):
    """한 종목의 뉴스. 캐시가 유효하면 외부 호출 없이 돌려준다."""
    now = time.time()

    if not force_refresh:
        with _cache_lock:
            hit = _cache.get(stock)
        if hit is not None and (now - hit[1]) < CACHE_TTL:
            return hit[0]

    news_list = _fetch_rss(stock)

    if news_list is None:
        # ⭐️ 실패한 결과는 캐시하지 않는다. 3초 타임아웃 한 번만 나도 빈 결과를
        #    캐시하면 그 종목 뉴스가 10분 동안 빈 화면으로 굳어, 수동 새로고침
        #    전까지 회복되지 않았다.
        #    다만 만료됐더라도 마지막으로 성공했던 뉴스를 보여주는 편이 낫다.
        with _cache_lock:
            stale = _cache.get(stock)
        return stale[0] if stale else []

    with _cache_lock:
        _cache[stock] = (news_list, now)
    _prune(now)
    return news_list


def fetch_many(stocks, force_refresh=False):
    """여러 종목의 뉴스를 병렬로 모아 하나의 리스트로 돌려준다."""
    if not stocks:
        stocks = [DEFAULT_QUERY]

    all_news = []
    # ⭐️ 보유 종목이 많을 경우를 대비해 스레드 풀로 병렬 조회한다.
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_PARALLEL) as executor:
        for res_list in executor.map(lambda s: fetch_one(s, force_refresh), stocks):
            all_news.extend(res_list)
    return all_news


def clear_cache():
    """테스트용 — 앞 테스트의 캐시가 다음 테스트로 새지 않도록."""
    with _cache_lock:
        _cache.clear()
