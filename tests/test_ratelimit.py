"""레이트리밋 원시 타입 — 슬라이딩 윈도우와 실패 잠금.

⭐️ 예전에는 같은 알고리즘이 네 벌 흩어져 있었고(가입/재설정, 계정 잠금, 봇 API,
   로그인 IP), 그중 로그인 IP 만 **락이 없었다**. 한 벌로 합쳤으니 여기서 그
   한 벌의 계약을 못 박는다. 엔드포인트 레벨 동작은 test_auth.py 가 본다.
"""
import threading

import ratelimit
from ratelimit import FailureLockout, SlidingWindow


# ── SlidingWindow ─────────────────────────────────────────────────────

def test_window_allows_up_to_the_limit_then_refuses():
    w = SlidingWindow(limit=3, window=60)
    assert [w.check('ip', now=100)[0] for _ in range(3)] == [True, True, True]
    allowed, remaining, retry_after = w.check('ip', now=100)
    assert allowed is False
    assert remaining == 0
    assert retry_after > 0


def test_window_reports_remaining_budget():
    w = SlidingWindow(limit=3, window=60)
    assert [w.check('ip', now=0)[1] for _ in range(3)] == [2, 1, 0]


def test_window_keys_are_independent():
    w = SlidingWindow(limit=1, window=60)
    assert w.allow('a', now=0) is True
    assert w.allow('b', now=0) is True   # 다른 키는 영향을 받지 않는다
    assert w.allow('a', now=0) is False


def test_window_frees_up_as_it_slides():
    w = SlidingWindow(limit=2, window=60)
    w.check('ip', now=0)
    w.check('ip', now=0)
    assert w.allow('ip', now=30) is False
    assert w.allow('ip', now=61) is True   # 창이 지나 옛 기록이 빠졌다


def test_refused_requests_do_not_consume_the_window():
    """막힌 뒤에도 계속 두드리는 쪽이 더 오래 막히면 안 된다.

    거부된 요청이 창을 소비하면, 정직하게 기다린 사람보다 계속 재시도한 쪽이
    유리해지는 게 아니라 **영원히 못 들어가는** 상태가 된다.
    """
    w = SlidingWindow(limit=1, window=60)
    assert w.allow('ip', now=0) is True
    for t in range(1, 60):
        w.allow('ip', now=t)              # 계속 두드린다 (전부 거부)
    assert w.allow('ip', now=61) is True   # 그래도 원래 시각 기준으로 풀린다


def test_window_evicts_dead_keys():
    w = SlidingWindow(limit=1, window=10, max_keys=5)
    for i in range(50):
        w.check(f'ip{i}', now=0)
    w.check('trigger', now=1000)   # 상한을 넘긴 상태에서 한 번 더 → 정리 발동
    assert len(w._hits) < 50


def test_window_is_thread_safe():
    """정리 루프가 dict 를 순회하는 동안 다른 스레드가 키를 넣어도 터지지 않는다."""
    w = SlidingWindow(limit=1000, window=60, max_keys=10)
    errors = []

    def hammer(base):
        try:
            for i in range(300):
                w.check(f'{base}-{i}')
        except Exception as e:   # pragma: no cover - 실패 시에만 채워진다
            errors.append(e)

    threads = [threading.Thread(target=hammer, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, f"동시 접근 중 예외: {errors}"


# ── FailureLockout ────────────────────────────────────────────────────

def test_lockout_engages_at_the_threshold():
    f = FailureLockout(threshold=3, lockout_seconds=60)
    assert f.remaining('u', now=0) == 0
    for _ in range(2):
        f.record_failure('u', now=0)
    assert f.remaining('u', now=0) == 0    # 아직 임계치 미만
    f.record_failure('u', now=0)
    assert f.remaining('u', now=0) == 60


def test_success_clears_the_record_entirely():
    f = FailureLockout(threshold=3, lockout_seconds=60)
    f.record_failure('u', now=0)
    f.record_failure('u', now=0)
    f.clear('u')
    assert f.count('u') == 0
    f.record_failure('u', now=0)
    assert f.remaining('u', now=0) == 0    # 다시 1회부터 센다


def test_failures_outside_the_window_are_forgotten():
    f = FailureLockout(threshold=2, lockout_seconds=60, failure_window=100)
    f.record_failure('u', now=0)
    f.record_failure('u', now=500)         # 창 밖 → 카운터가 새로 시작한다
    assert f.remaining('u', now=500) == 0


def test_reset_on_lock_controls_what_happens_after_the_lock_expires():
    """잠금이 풀린 뒤 한 번만 더 틀려도 다시 잠기는가, 처음부터 다시 세는가."""
    strict = FailureLockout(threshold=2, lockout_seconds=10, reset_on_lock=False)
    strict.record_failure('u', now=0)
    strict.record_failure('u', now=0)      # 잠김
    strict.record_failure('u', now=100)    # 잠금 만료 후 1회 → 누적 3회라 즉시 재잠금
    assert strict.remaining('u', now=100) == 10

    lenient = FailureLockout(threshold=2, lockout_seconds=10, reset_on_lock=True)
    lenient.record_failure('u', now=0)
    lenient.record_failure('u', now=0)      # 잠김 + 카운터 0
    lenient.record_failure('u', now=100)    # 만료 후 1회 → 아직 임계치 미만
    assert lenient.remaining('u', now=100) == 0


def test_empty_key_is_ignored():
    """아이디를 비워 보낸 로그인 시도가 하나의 잠금 슬롯을 독점하면 안 된다."""
    f = FailureLockout(threshold=1, lockout_seconds=60)
    assert f.record_failure('', now=0) == 0
    assert f.remaining('', now=0) == 0
    assert f.count('') == 0


# ── 앱이 실제로 쓰는 리미터들 ─────────────────────────────────────────

def test_reset_all_clears_every_limiter():
    ratelimit.signups.check('ip')
    ratelimit.login_ips.record_failure('ip')
    ratelimit.users.record_failure('u')
    ratelimit.api_calls.check('k')

    ratelimit.reset_all()

    assert ratelimit.signups.allow('ip') is True
    assert ratelimit.login_ips.count('ip') == 0
    assert ratelimit.users.count('u') == 0
    assert ratelimit.api_calls.check('k')[1] == ratelimit.API_RATE_LIMIT[0] - 1


def test_login_and_account_lockouts_are_separate_defenses():
    """IP 를 바꾸면 IP 잠금은 피하지만 계정 잠금은 그대로 걸린다."""
    ratelimit.reset_all()
    for _ in range(ratelimit.LOGIN_IP_THRESHOLD):
        ratelimit.record_login_ip_failure('1.1.1.1')
        ratelimit.record_user_failure('victim')

    assert ratelimit.login_ip_lockout_remaining('1.1.1.1') > 0
    assert ratelimit.login_ip_lockout_remaining('2.2.2.2') == 0   # 새 IP 는 자유롭다
    # 계정 잠금은 IP 와 무관하게 실패를 누적해 간다
    for _ in range(ratelimit.USER_LOCKOUT_THRESHOLD - ratelimit.LOGIN_IP_THRESHOLD):
        ratelimit.record_user_failure('victim')
    assert ratelimit.user_lockout_remaining('victim') > 0
    ratelimit.reset_all()
