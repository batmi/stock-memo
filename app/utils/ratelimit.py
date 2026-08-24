"""무차별 대입 방어 — 요청 제한과 잠금의 **단일 소스**.

예전에는 같은 "dict + Lock + 상한 넘으면 훑어서 정리" 패턴이 네 벌 흩어져 있었다.
가입/재설정 IP 제한(여기), 계정 잠금(여기), 봇 API 레이트리밋(trading_api),
그리고 로그인 IP 잠금(auth). 네 번째만 **락이 없어서**, waitress 가 16 스레드로
도는 환경에서 동시 로그인이 겹치면 정리 루프가 dict 를 순회하는 도중 다른
스레드가 키를 넣어 RuntimeError 로 로그인 화면 전체가 500 이 될 수 있었다.

그래서 원시 타입 두 개만 두고 나머지는 전부 이것을 쓴다.

  SlidingWindow    창(window) 안에서 N 회까지 — "1시간에 5회" 같은 순수 요청 제한
  FailureLockout   실패를 누적하다 임계치를 넘으면 일정 시간 잠금 — 로그인 방어

두 장치는 서로를 보완한다. IP 제한만으로는 IP 를 바꾸면 우회되고, 계정 잠금만
으로는 여러 계정을 훑는 시도를 막지 못한다.

⚠️ 상태는 프로세스 메모리다. waitress 단일 프로세스 구성을 전제로 하며,
   멀티 워커(gunicorn -w N)로 띄우면 워커 수만큼 한도가 늘어난다.
"""

import logging
import threading
import time

log = logging.getLogger('ratelimit')


# ---------------------------------------------------------------------------
# 원시 타입
# ---------------------------------------------------------------------------

class SlidingWindow:
    """키마다 '최근 window 초 안에 limit 회'를 강제하는 스레드 안전 카운터.

    다중 프로세스로 확장할 때는 Redis 등 공유 저장소로 교체해야 한다.
    """

    def __init__(self, limit, window, max_keys=1000):
        self.limit = limit
        self.window = window
        self._max_keys = max_keys
        self._hits = {}   # key -> [timestamps]
        self._lock = threading.Lock()

    def check(self, key, now=None):
        """1회를 소비하고 (허용여부, 남은횟수, 재시도까지 초) 를 돌려준다.

        거부된 요청은 소비하지 않는다 — 막힌 뒤에도 계속 두드리면 창이 영영
        갱신되지 않아, 정직하게 기다린 사람이 오히려 더 오래 막히기 때문이다.
        """
        now = now if now is not None else time.time()
        cutoff = now - self.window
        with self._lock:
            self._evict(now)
            stamps = [t for t in self._hits.get(key, []) if t > cutoff]
            if len(stamps) >= self.limit:
                self._hits[key] = stamps
                retry_after = int(self.window - (now - stamps[0])) + 1
                return False, 0, retry_after
            stamps.append(now)
            self._hits[key] = stamps
            return True, self.limit - len(stamps), 0

    def allow(self, key, now=None):
        """check() 의 허용 여부만 필요한 호출부용."""
        return self.check(key, now)[0]

    def _evict(self, now):
        """상한을 넘었을 때만 죽은 키를 훑어 정리한다 (24시간 상시 구동 대비)."""
        if len(self._hits) <= self._max_keys:
            return
        cutoff = now - self.window
        for k in [k for k, ts in self._hits.items() if not ts or ts[-1] <= cutoff]:
            self._hits.pop(k, None)

    def reset(self):
        with self._lock:
            self._hits.clear()


class FailureLockout:
    """실패를 누적하다 임계치를 넘으면 일정 시간 잠그는 스레드 안전 카운터.

    SlidingWindow 와 달리 '성공하면 즉시 0 으로 되돌아간다'가 핵심이다. 정상
    사용자는 한 번만 제대로 입력하면 흔적이 사라지고, 계속 틀리는 쪽만 막힌다.

    failure_window   이 시간 안의 실패만 센다 (None 이면 성공/잠금 전까지 누적)
    reset_on_lock    잠글 때 누적 횟수를 0 으로 되돌릴지
                     (False 면 잠금이 풀린 뒤 한 번만 더 틀려도 곧바로 재잠금)
    """

    def __init__(self, threshold, lockout_seconds, failure_window=None,
                 reset_on_lock=True, max_keys=200, label=''):
        self.threshold = threshold
        self.lockout_seconds = lockout_seconds
        self.failure_window = failure_window
        self.reset_on_lock = reset_on_lock
        self._max_keys = max_keys
        self._label = label
        self._records = {}   # key -> {'count', 'lockout_until', 'last'}
        self._lock = threading.Lock()

    def remaining(self, key, now=None):
        """잠겨 있으면 남은 초(int), 아니면 0."""
        if not key:
            return 0
        now = now if now is not None else time.time()
        with self._lock:
            rec = self._records.get(key)
            if not rec:
                return 0
            return max(0, int(rec['lockout_until'] - now))

    def count(self, key):
        """현재까지 누적된 실패 횟수 (화면에 'N/5' 로 보여주는 용도)."""
        if not key:
            return 0
        with self._lock:
            rec = self._records.get(key)
            return rec['count'] if rec else 0

    def record_failure(self, key, now=None):
        """실패 1회를 기록하고 누적 횟수를 돌려준다."""
        if not key:
            return 0
        now = now if now is not None else time.time()
        with self._lock:
            self._evict(now)
            rec = self._records.get(key)
            if not rec or (self.failure_window is not None
                           and rec['last'] < now - self.failure_window):
                rec = {'count': 0, 'lockout_until': 0, 'last': now}
            rec['count'] += 1
            rec['last'] = now
            counted = rec['count']
            if rec['count'] >= self.threshold:
                rec['lockout_until'] = now + self.lockout_seconds
                if self.reset_on_lock:
                    rec['count'] = 0
                if self._label:
                    log.warning(f"🔒 {self._label} 잠금(반복 실패): key='{key}' "
                                f"{self.lockout_seconds}초")
            self._records[key] = rec
            return counted

    def clear(self, key):
        """성공했을 때 — 흔적을 완전히 지운다."""
        if not key:
            return
        with self._lock:
            self._records.pop(key, None)

    def _evict(self, now):
        if len(self._records) <= self._max_keys:
            return
        stale_cutoff = now - (self.failure_window or 3600)
        for k in [k for k, r in self._records.items()
                  if r['last'] < stale_cutoff and r['lockout_until'] < now]:
            self._records.pop(k, None)

    def reset(self):
        with self._lock:
            self._records.clear()


# ---------------------------------------------------------------------------
# 이 앱이 쓰는 리미터들
# ---------------------------------------------------------------------------

# ⭐️ 로그인 화면에서 누구나 호출할 수 있는 공개 엔드포인트라 두 가지를 지킨다.
#    1) 계정 존재 여부를 응답으로 알려주지 않는다 (있든 없든 같은 문구)
#    2) IP 당 요청 횟수를 제한한다 (요청함을 스팸으로 채우지 못하게)
RESET_REQUEST_MAX_PER_HOUR = 5
SIGNUP_MAX_PER_HOUR = 5

reset_requests = SlidingWindow(RESET_REQUEST_MAX_PER_HOUR, 3600)
signups = SlidingWindow(SIGNUP_MAX_PER_HOUR, 3600)

# ⭐️ 로그인 IP 잠금. 5회 연속 실패하면 60초.
#    reset_on_lock=False — 잠금이 풀린 직후 한 번만 더 틀려도 즉시 다시 잠긴다.
#    자동화된 시도는 60초마다 1회로 눌리고, 사람은 한 번 맞히면 clear 된다.
LOGIN_IP_THRESHOLD = 5
LOGIN_IP_LOCKOUT_SECONDS = 60
login_ips = FailureLockout(LOGIN_IP_THRESHOLD, LOGIN_IP_LOCKOUT_SECONDS,
                           reset_on_lock=False, max_keys=50, label='로그인 IP')

# ⭐️ 계정 단위 로그인 실패 추적. IP 잠금(5회/60초)만으로는 IP 를 바꾸면 우회되고,
#    한 계정을 노린 느린 대입 공격을 막지 못한다.
USER_LOCKOUT_THRESHOLD = 10      # 실패 횟수
USER_LOCKOUT_SECONDS = 300       # 잠금 시간(초)
USER_FAILURE_WINDOW = 900        # 이 시간 안의 실패만 센다
users = FailureLockout(USER_LOCKOUT_THRESHOLD, USER_LOCKOUT_SECONDS,
                       failure_window=USER_FAILURE_WINDOW, label='계정')

# ⭐️ 봇 API(/api/v1). 토큰 발급은 IP 당, 그 뒤 호출은 키 당 센다.
#    발급을 IP 로 조이는 이유는 API 키 자체를 무차별 대입하는 시도를 막기 위해서다.
TOKEN_RATE_LIMIT = (10, 300)   # IP 당 5분에 10회
API_RATE_LIMIT = (600, 60)     # 키 당 1분에 600회

api_tokens = SlidingWindow(*TOKEN_RATE_LIMIT, max_keys=5000)
api_calls = SlidingWindow(*API_RATE_LIMIT, max_keys=5000)

_ALL = (reset_requests, signups, login_ips, users, api_tokens, api_calls)


# ---------------------------------------------------------------------------
# 호출부용 얇은 래퍼 (의도를 이름으로 드러낸다)
# ---------------------------------------------------------------------------

def reset_request_allowed(client_ip):
    return reset_requests.allow(client_ip)


def signup_allowed(client_ip):
    return signups.allow(client_ip)


def login_ip_lockout_remaining(client_ip, now=None):
    return login_ips.remaining(client_ip, now)


def record_login_ip_failure(client_ip):
    """실패를 기록하고 누적 횟수를 돌려준다 ('실패 횟수: N/5' 표시용)."""
    return login_ips.record_failure(client_ip)


def clear_login_ip_failures(client_ip):
    login_ips.clear(client_ip)


def user_lockout_remaining(username, now=None):
    return users.remaining(username, now)


def record_user_failure(username):
    users.record_failure(username)


def clear_user_failures(username):
    users.clear(username)


def reset_all():
    """테스트용 — 앞 테스트의 카운터가 다음 테스트로 새지 않도록."""
    for limiter in _ALL:
        limiter.reset()
