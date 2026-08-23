"""시스템 트레이딩 봇 연동 REST API — Universal Trading History API v2.

UniversalTradingHistoryAPI.json 의 계약을 그대로 구현합니다.

설계 원칙
---------
1. **유실 금지**: 봇이 보낸 체결은 무결성 검증에 걸려도 저장한다. 400 으로 되돌리면
   봇은 재시도해도 계속 실패하고 그 체결은 영구히 사라진다. 대신 needsReview 로
   표시해 웹에서 사람이 확인하게 한다. (웹 UI 직접 입력은 종전대로 차단)
2. **멱등**: brokerExecutionId 에 UNIQUE 제약을 걸고 INSERT OR IGNORE 로 경합을 막는다.
   중복 재전송은 200 + 기존 기록 반환. 그래서 타임아웃 시 무조건 재전송해도 안전하다.
3. **모의/실거래 분리**: isSimulated 를 별도 컬럼으로 두고 기본 조회에서 제외한다.
4. **최소 권한**: API 키는 해시로만 저장하고 스코프를 부여한다. 키를 폐기하면 그 키로
   발급된 토큰도 즉시 무효가 된다(토큰에 key id 를 심고 매 요청 DB 대조).

파일 구성
---------
예전에는 이 전부가 1,600줄짜리 단일 모듈이었다. 인증·검증·저장·봇 상태·명령
큐·라우트가 한 파일에 섞여 있어, 한 가지를 고치려면 관계없는 다섯 가지를 스쳐
지나가야 했다. 의존 방향이 한쪽으로만 흐르도록 갈랐다.

    common      블루프린트·상수·시각 유틸·오류 포맷   (아무에게도 의존하지 않음)
    keys        API 키 해시 저장·발급·폐기·레거시 이관
    security    토큰 서명/검증·스코프·레이트리밋
    validation  입력 정규화와 형태 검증
    entries     입력 → entries 행 → 응답, 멱등 INSERT
    bots        봇 등록·상태 판정·하행 명령 큐
    routes      HTTP 핸들러 (위의 것들을 조립만 한다)

DB·계좌 매핑·통계 캐시는 각 도메인 모듈을 직접 임포트합니다 (순환 없음).
"""

import accounts

# ⚠️ 아래는 **값 복사**다. 상수를 런타임에 바꿔치기하려면(테스트 등) 이 이름이
#    아니라 그 값을 실제로 읽는 모듈(trading_api.common)을 패치해야 한다.
from .common import (  # noqa: F401
    API_VERSION, BOT_COMMAND_TTL_SECONDS, BOT_ID_MAX_LEN, BOT_MISSED_PINGS_ALLOWED,
    BOT_OFFLINE_AFTER_SECONDS, BOT_PING_GRACE_SECONDS, BOT_PING_INTERVAL_SECONDS,
    ALL_SCOPES, API_KEY_PREFIX, DEFAULT_SCOPES, KST, LAST_USED_WRITE_INTERVAL_SECONDS,
    LEGACY_BOT_ID, MAX_BATCH_ITEMS, SCOPE_BOT_WRITE, SCOPE_TRADES_READ,
    SCOPE_TRADES_WRITE, SUPPORTED_BOT_COMMANDS, TOKEN_TTL_SECONDS, bp,
    _now_iso, _now_kst, _now_kst_str,
)
from .keys import (  # noqa: F401
    create_api_key, list_api_keys, migrate_data, revoke_all_api_keys, revoke_api_key,
    _migrate_legacy_api_keys, _should_touch_last_used,
)
from .security import (  # noqa: F401
    API_RATE_LIMIT, TOKEN_RATE_LIMIT, require_token,
)
from .validation import (  # noqa: F401
    ValidationError, _resolve_account,
)
from .entries import build_entry, entry_to_response  # noqa: F401
from .bots import (  # noqa: F401
    delete_bot, evaluate_bot_state, latest_bot_command, list_bots,
    request_bot_command, summarize_bot_states,
)

# ⭐️ 라우트는 임포트되는 것만으로 bp 에 등록된다. 반드시 마지막에 둔다.
from . import routes  # noqa: E402,F401

# ⭐️ 계좌번호 정규화·조회는 공용 도메인 규칙이므로 accounts 모듈이 소유한다.
#    (예전에는 이 두 함수가 여기 비공개로 있었고 backend_app 이 밑줄 이름을 직접
#     불러 썼다. 봇 연동 모듈이 웹 화면 통계의 의존 대상이 되는 건 계층이 뒤집힌 것이다)
_account_key = accounts.account_key
_find_account_mapping = accounts.find_account_mapping


def register(app):
    """블루프린트를 앱에 등록합니다.

    ⭐️ 예전에는 init_app(app, db_conn=..., get_user_mappings=..., ...) 로 의존성을
       주입받았다. 이유는 "순환 임포트 회피"였는데, db.py 가 분리되고 매핑 조회가
       accounts 로 옮겨간 지금은 셋 다 의존이 없는 말단 모듈이라 그냥 임포트하면
       된다. 그래서 backend_app 최하단에서 순서를 맞춰 호출하던 제약도 없어졌다.
    """
    app.register_blueprint(bp)
