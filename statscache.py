"""통계 캐시와 데이터 버전 — 기록이 바뀌면 즉시 무효화한다.

기록을 바꾸는 곳은 웹 폼, 봇 API(/api/v1), 계좌 매핑 저장, 복원, 관리자 계정
삭제까지 여러 갈래다. 캐시 딕셔너리가 라우트 파일 안에 있으면 그중 하나가
무효화를 빠뜨려도 드러나지 않는다 — 화면에는 옛 수치가 계속 보이고, 사용자는
"저장이 안 됐다"고 느낀다. 그래서 캐시와 무효화를 한 모듈로 묶는다.

⚠️ 프로세스 메모리다. 멀티 워커로 띄우면 한쪽 워커에서 무효화해도 다른 워커는
   옛 값을 그대로 들고 있다. 단일 프로세스(waitress) 구성을 전제로 한다.
"""

import threading
import uuid

# (username, granularity) -> 결과 dict
#   전체 통계는 대시보드 진입 시 자주 호출되며 SELECT * 전체 로드 + Python 재계산
#   비용이 크다. 기록이 변경될 때만 무효화하여 반복 계산을 제거한다.
#   (특정 entry_ids 로 필터링된 POST 요청은 케이스가 다양해 캐싱하지 않는다.)
_stats_cache = {}
_lock = threading.Lock()

# ⭐️ /api/data ETag 용 사용자별 데이터 버전 — 기록 변경 시마다 증가.
#    서버 재시작 시 버전이 0부터 다시 시작해도 잘못된 304 가 나가지 않도록
#    부팅마다 달라지는 식별자를 ETag 에 포함한다.
_BOOT_ID = uuid.uuid4().hex[:8]
_data_versions = {}  # username -> int


def get(key):
    with _lock:
        return _stats_cache.get(key)


def put(key, value):
    with _lock:
        _stats_cache[key] = value


def data_etag(username):
    with _lock:
        version = _data_versions.get(username, 0)
    return f"{username}-{_BOOT_ID}-{version}"


def invalidate(username):
    """해당 사용자의 통계 캐시 무효화 + 데이터 버전 증가.

    기록을 추가/수정/삭제하거나 '금액 계산 제외' 설정을 바꾼 모든 경로에서
    호출해야 한다. 빠뜨리면 화면에 옛 수치가 그대로 남는다.
    """
    if not username:
        return
    with _lock:
        for key in [k for k in _stats_cache if k[0] == username]:
            _stats_cache.pop(key, None)
        _data_versions[username] = _data_versions.get(username, 0) + 1


def clear_all():
    """테스트용 — 앞 테스트의 캐시가 다음 테스트로 새지 않도록."""
    with _lock:
        _stats_cache.clear()
        _data_versions.clear()
