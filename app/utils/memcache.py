"""수명이 있는 인메모리 캐시 — TTL·상한·락을 한 번만 구현한다.

⭐️ 예전에는 이 패턴이 곳곳에 손으로 복제돼 있었다. dict 하나, Lock 하나,
   그리고 "상한을 넘으면 만료된 항목을 훑어 지우는" 정리 함수 하나. 시세
   (`prices._prune_price_mem_cache`)와 뉴스(`api._prune_news_cache`)가 글자만
   다른 같은 함수였고, 레이트리밋 쪽에도 세 벌이 더 있었다(그쪽은 요청 제한이라는
   다른 의미를 갖게 되어 `ratelimit` 모듈이 따로 소유한다).

   사본이 여럿이면 "만료된 값을 돌려줘도 되는가", "정리를 언제 도는가" 같은
   판단이 각자 다르게 굳는다. 실제로 뉴스 쪽만 '조회 실패 시 만료된 값이라도
   보여준다'는 규칙을 갖고 있었는데, 그건 캐시의 성질이 아니라 호출부의 정책이다.
   그래서 여기서는 **보관과 만료만** 책임지고, 만료된 값을 어떻게 쓸지는
   호출부가 정하도록 `get(..., allow_stale=True)` 로 열어 둔다.

⚠️ 프로세스 메모리다. 멀티 워커로 띄우면 워커마다 따로 채워진다.
   (단일 프로세스 구성 전제 — wsgi.py 참고)
"""

import threading
import time

# ⭐️ 이 앱에서 '프로세스 메모리에만 있는' 상태의 목록. 멀티 워커로 띄우면
#    워커 수만큼 한도가 늘어나거나 캐시가 서로 어긋나는 것들이다.
#    예전에는 이 제약이 wsgi.py·ratelimit.py·statscache.py 주석에 흩어져 있어서,
#    "그래서 지금 무엇이 깨지는가"에 답하려면 세 파일을 열어야 했다.
#    bootstrap() 이 기동 로그에 이 목록을 남긴다 — 운영 중에 눈에 보여야 한다.
PROCESS_LOCAL_STATE = (
    ('app.utils.ratelimit',  '요청 제한·로그인 잠금 (워커 수만큼 한도가 늘어난다)'),
    ('app.utils.statscache', '통계 캐시·데이터 버전 (한쪽에서 무효화해도 다른 쪽은 옛 값)'),
    ('app.services.users',   '세션 epoch 캐시 (비밀번호 변경 반영이 워커마다 늦을 수 있다)'),
    ('app.services.prices',  '시세 단기 캐시 (외부 API 중복 호출 흡수 효과가 줄어든다)'),
    ('app.services.news',    '뉴스 캐시 (같음)'),
    ('app.routes.middleware', '정적 자산 gzip 캐시 (워커마다 한 번씩 압축한다)'),
)

_MISS = object()


class TTLCache:
    """키마다 값과 저장 시각을 갖는 스레드 안전 캐시.

    ttl        이 시간(초)이 지난 값은 없는 것으로 본다
    max_size   항목 수 상한. 넘어선 뒤의 쓰기에서만 만료분을 훑어 지운다
               (매 쓰기마다 전체를 훑으면 캐시가 커질수록 쓰기가 느려진다)
    """

    def __init__(self, ttl, max_size=500, name=''):
        self.ttl = ttl
        self.max_size = max_size
        self._name = name
        self._items = {}   # key -> (value, stored_at)
        self._lock = threading.Lock()

    def get(self, key, default=None, *, allow_stale=False, now=None):
        """살아 있는 값. 없거나 만료됐으면 default.

        allow_stale=True 면 만료된 값도 돌려준다. 외부 조회가 실패했을 때
        "빈 화면보다는 낡은 값"이 나은 호출부를 위한 것이다.
        """
        now = now if now is not None else time.time()
        with self._lock:
            hit = self._items.get(key, _MISS)
        if hit is _MISS:
            return default
        value, stored_at = hit
        if allow_stale or (now - stored_at) < self.ttl:
            return value
        return default

    def put(self, key, value, now=None):
        now = now if now is not None else time.time()
        with self._lock:
            self._items[key] = (value, now)
            self._evict(now)

    def _evict(self, now):
        """상한을 넘었을 때만 만료분을 지운다. (락을 잡은 상태에서 호출할 것)

        키가 계속 바뀌는 캐시(종목 코드, 종목명)는 정리를 하지 않으면 상시
        구동 중에 단조 증가한다. 삭제된 종목의 옛 키가 대표적이다.
        """
        if len(self._items) <= self.max_size:
            return
        for k in [k for k, (_v, ts) in self._items.items() if (now - ts) >= self.ttl]:
            self._items.pop(k, None)

    def clear(self):
        """테스트용 — 앞 테스트의 캐시가 다음 테스트로 새지 않도록."""
        with self._lock:
            self._items.clear()

    def __len__(self):
        with self._lock:
            return len(self._items)
