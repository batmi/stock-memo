"""TTLCache — 보관·만료·상한·동시성.

⭐️ 예전에는 이 규칙이 시세(prices)와 뉴스(api) 두 곳에 따로 구현돼 있어서,
   같은 성질을 두 번 검증하거나(한쪽만) 아예 검증하지 않았다. 한 벌로 합쳤으니
   여기서 그 한 벌의 계약을 못 박는다.
"""
import threading

from app.utils.memcache import TTLCache


def test_returns_what_was_put():
    c = TTLCache(ttl=60)
    c.put('k', 'v', now=0)
    assert c.get('k', now=0) == 'v'


def test_missing_key_returns_the_default():
    c = TTLCache(ttl=60)
    assert c.get('없음') is None
    assert c.get('없음', default=[]) == []


def test_value_expires_after_the_ttl():
    c = TTLCache(ttl=60)
    c.put('k', 'v', now=0)
    assert c.get('k', now=59) == 'v'
    assert c.get('k', now=60) is None      # 경계는 만료로 본다
    assert c.get('k', now=61) is None


def test_allow_stale_returns_expired_values():
    """외부 조회가 실패했을 때 빈 화면보다 낡은 값이 나은 호출부를 위한 문."""
    c = TTLCache(ttl=60)
    c.put('k', 'v', now=0)
    assert c.get('k', now=9999) is None
    assert c.get('k', now=9999, allow_stale=True) == 'v'


def test_allow_stale_on_a_missing_key_still_gives_the_default():
    c = TTLCache(ttl=60)
    assert c.get('없음', default=[], allow_stale=True) == []


def test_put_overwrites_and_refreshes_the_clock():
    c = TTLCache(ttl=60)
    c.put('k', 'old', now=0)
    c.put('k', 'new', now=50)
    assert c.get('k', now=100) == 'new'    # 50 에 다시 넣었으므로 아직 살아 있다


def test_none_is_storable_and_distinct_from_missing():
    """값이 None 인 것과 키가 없는 것은 다르다."""
    c = TTLCache(ttl=60)
    c.put('k', None, now=0)
    assert c.get('k', default='기본값', now=0) is None
    assert c.get('다른키', default='기본값', now=0) == '기본값'


def test_eviction_drops_only_expired_entries():
    c = TTLCache(ttl=60, max_size=5)
    for i in range(10):
        c.put(f'old{i}', i, now=0)         # 전부 만료될 값
    c.put('fresh', 'v', now=1000)          # 상한 초과 상태에서의 쓰기 → 정리 발동
    assert len(c) == 1
    assert c.get('fresh', now=1000) == 'v'


def test_eviction_does_not_run_under_the_limit():
    """상한 미만이면 만료됐어도 훑지 않는다 — 락 구간을 짧게 유지하기 위해서다."""
    c = TTLCache(ttl=60, max_size=100)
    c.put('k', 'v', now=0)
    c.put('k2', 'v', now=9999)             # 정리가 돌았다면 'k' 가 사라졌을 것
    assert len(c) == 2
    assert c.get('k', now=9999) is None    # 다만 조회로는 만료 처리된다


def test_clear_empties_the_cache():
    c = TTLCache(ttl=60)
    c.put('k', 'v', now=0)
    c.clear()
    assert len(c) == 0
    assert c.get('k', now=0) is None


def test_concurrent_writes_do_not_raise():
    """정리 루프가 dict 를 순회하는 동안 다른 스레드가 키를 넣어도 터지지 않는다."""
    c = TTLCache(ttl=0.001, max_size=10)
    errors = []

    def hammer(base):
        try:
            for i in range(300):
                c.put(f'{base}-{i}', i)
                c.get(f'{base}-{i}')
        except Exception as e:   # pragma: no cover - 실패 시에만 채워진다
            errors.append(e)

    threads = [threading.Thread(target=hammer, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, f"동시 접근 중 예외: {errors}"


# ─────────────────────────────────────────────────────────────
# 프로세스 지역 상태 목록
# ─────────────────────────────────────────────────────────────
def test_process_local_state_names_real_modules():
    """목록이 실제 모듈과 어긋나면 기동 로그가 거짓말을 하게 된다."""
    import importlib

    from app.utils import memcache

    for name, why in memcache.PROCESS_LOCAL_STATE:
        importlib.import_module(name)      # 없는 모듈이면 여기서 실패한다
        assert why.strip(), f"{name}: 왜 문제인지가 비어 있다"


def test_process_local_state_is_logged_at_startup(app, caplog):
    """멀티 워커로 띄웠을 때의 증상은 서로 무관해 보인다 — 로그에 목록이 있어야 한다.

    ⚠️ app 픽스처를 반드시 받는다. bootstrap() 은 스키마 적용과 1회성 이관을
       수행하므로, 픽스처 없이 부르면 **운영 DB** 를 건드린다.
    """
    import backend_app
    from app.utils import memcache

    with caplog.at_level('INFO'):
        backend_app.bootstrap(start_jobs=False)

    logged = ' '.join(r.getMessage() for r in caplog.records)
    for name, _why in memcache.PROCESS_LOCAL_STATE:
        assert name in logged, f"기동 로그에 {name} 이 없습니다"
