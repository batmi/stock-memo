"""WSGI 진입점 — gunicorn/uwsgi 로 띄울 때 여기를 가리킨다.

    gunicorn 'wsgi:application' --bind 0.0.0.0:5000 --threads 16

`backend_app` 을 그냥 임포트하는 것과 다른 점은 **bootstrap() 을 부른다**는 것이다.
스키마 적용·1회성 이관·백그라운드 작업이 여기서 일어난다. backend_app 임포트
자체에는 부작용이 없어야 하므로(테스트·도구가 그냥 임포트한다) 별도 파일로 둔다.

⚠️ 멀티 워커(`-w 2` 이상)는 권장하지 않는다. 통계 캐시·레이트리밋·계정 잠금이
   모두 프로세스 메모리라 워커 수만큼 한도가 늘어나고 캐시가 어긋난다.
   그래도 멀티 워커로 띄워야 한다면 워커 하나에서만 백그라운드 작업이 돌도록
   `START_BACKGROUND_JOBS=0` 을 주고, 자동 백업은 cron 으로 따로 돌린다.
"""

import os

from backend_app import bootstrap

_start_jobs = os.environ.get('START_BACKGROUND_JOBS', '1').strip().lower() \
    not in ('0', 'false', 'no', 'off')

application = bootstrap(start_jobs=_start_jobs)
