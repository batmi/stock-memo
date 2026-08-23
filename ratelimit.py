"""무차별 대입 방어 — IP 단위 요청 제한과 계정 단위 로그인 잠금.

두 장치가 서로를 보완한다. IP 제한만으로는 IP 를 바꾸면 우회되고, 계정 잠금만
으로는 여러 계정을 훑는 시도를 막지 못한다.

⚠️ 상태는 프로세스 메모리다. waitress 단일 프로세스 구성을 전제로 하며,
   멀티 워커(gunicorn -w N)로 띄우면 워커 수만큼 한도가 늘어난다.
"""

import logging
import threading
import time

log = logging.getLogger('ratelimit')


# ⭐️ 로그인 화면에서 누구나 호출할 수 있는 공개 엔드포인트라 두 가지를 지킨다.
#    1) 계정 존재 여부를 응답으로 알려주지 않는다 (있든 없든 같은 문구)
#    2) IP 당 요청 횟수를 제한한다 (요청함을 스팸으로 채우지 못하게)
RESET_REQUEST_MAX_PER_HOUR = 5
SIGNUP_MAX_PER_HOUR = 5

# ⭐️ 로그인 없이 부를 수 있는 엔드포인트(가입·재설정 요청)용 IP 단위 리미터.
#    인메모리라 재시작하면 초기화되지만, 자동화된 대량 시도를 늦추기에는 충분하다.
ip_attempts = {}  # (bucket, ip) -> [timestamps]
_ip_attempts_lock = threading.Lock()


def ip_rate_allowed(bucket, client_ip, limit, window=3600):
    now = time.time()
    cutoff = now - window
    key = (bucket, client_ip)
    with _ip_attempts_lock:
        # 오래된 기록 정리 (24시간 상시 구동에서 무한 증가 방지)
        if len(ip_attempts) > 500:
            for k, ts in list(ip_attempts.items()):
                if not [t for t in ts if t > cutoff]:
                    ip_attempts.pop(k, None)
        stamps = [t for t in ip_attempts.get(key, []) if t > cutoff]
        if len(stamps) >= limit:
            ip_attempts[key] = stamps
            return False
        stamps.append(now)
        ip_attempts[key] = stamps
        return True


# ⭐️ 계정 단위 로그인 실패 추적. IP 잠금(5회/60초)만으로는 IP 를 바꾸면 우회되고,
#    한 계정을 노린 느린 대입 공격을 막지 못한다.
USER_LOCKOUT_THRESHOLD = 10      # 실패 횟수
USER_LOCKOUT_SECONDS = 300       # 잠금 시간(초)
USER_FAILURE_WINDOW = 900        # 이 시간 안의 실패만 센다
user_failures = {}              # username -> {'count': int, 'lockout_until': float, 'last': float}
_user_failures_lock = threading.Lock()


def user_lockout_remaining(username, now=None):
    """계정이 잠겨 있으면 남은 초(int), 아니면 0."""
    if not username:
        return 0
    now = now or time.time()
    with _user_failures_lock:
        rec = user_failures.get(username)
        if not rec:
            return 0
        return max(0, int(rec['lockout_until'] - now))


def record_user_failure(username):
    if not username:
        return
    now = time.time()
    with _user_failures_lock:
        if len(user_failures) > 200:
            for u, r in list(user_failures.items()):
                if r['last'] < now - USER_FAILURE_WINDOW and r['lockout_until'] < now:
                    user_failures.pop(u, None)
        rec = user_failures.get(username)
        if not rec or rec['last'] < now - USER_FAILURE_WINDOW:
            rec = {'count': 0, 'lockout_until': 0, 'last': now}
        rec['count'] += 1
        rec['last'] = now
        if rec['count'] >= USER_LOCKOUT_THRESHOLD:
            rec['lockout_until'] = now + USER_LOCKOUT_SECONDS
            rec['count'] = 0
            log.warning(f"🔒 계정 잠금(로그인 반복 실패): username='{username}' "
                               f"{USER_LOCKOUT_SECONDS}초")
        user_failures[username] = rec


def clear_user_failures(username):
    with _user_failures_lock:
        user_failures.pop(username, None)


def reset_request_allowed(client_ip):
    return ip_rate_allowed('reset', client_ip, RESET_REQUEST_MAX_PER_HOUR)


def signup_allowed(client_ip):
    return ip_rate_allowed('signup', client_ip, SIGNUP_MAX_PER_HOUR)


def reset_all():
    """테스트용 — 앞 테스트의 카운터가 다음 테스트로 새지 않도록."""
    with _ip_attempts_lock:
        ip_attempts.clear()
    with _user_failures_lock:
        user_failures.clear()
