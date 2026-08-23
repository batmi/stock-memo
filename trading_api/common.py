"""봇 연동 API 의 공용 토대 — 블루프린트·상수·시각·오류 포맷.

이 패키지의 다른 모듈은 모두 여기에 의존하고, 여기는 아무에게도 의존하지 않는다.
(의존 방향을 한쪽으로 고정해 두면 새 모듈을 붙일 때 순환을 고민할 필요가 없다)
"""

import logging
from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, has_app_context, jsonify

_module_log = logging.getLogger('trading_api')

try:  # 표준 tz 데이터베이스 (거래소 현지 거래일 산출용)
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.8 이하
    ZoneInfo = None


bp = Blueprint('trading_api', __name__, url_prefix='/api/v1')

API_VERSION = '2.0.0'
TOKEN_TTL_SECONDS = 86400
MAX_BATCH_ITEMS = 500

# ⭐️ api_keys.last_used_at 갱신 최소 간격(초).
#    이 값은 화면에 '마지막 사용'을 보여주기 위한 감사용 메타데이터인데, 예전에는
#    인증된 요청마다 UPDATE+commit 을 했다. 봇 하트비트가 10초 주기라 봇 1대만
#    붙어도 하루 8,640회의 쓰기가 SD카드(라즈베리파이)에 쌓인다.
#    prices.save_price_cache 가 같은 이유로 동일 값 쓰기를 생략하는 것과 같은 처리다.
#    60초로 뭉개면 쓰기는 1/6 로 줄고 화면 표시상 차이는 없다.
LAST_USED_WRITE_INTERVAL_SECONDS = 60
KST = timezone(timedelta(hours=9))

# ── 스코프 ─────────────────────────────────────────────────────────────
SCOPE_TRADES_WRITE = 'trades:write'
SCOPE_TRADES_READ = 'trades:read'
SCOPE_BOT_WRITE = 'bot:write'
ALL_SCOPES = (SCOPE_TRADES_WRITE, SCOPE_TRADES_READ, SCOPE_BOT_WRITE)
DEFAULT_SCOPES = ' '.join(ALL_SCOPES)

API_KEY_PREFIX = 'skm_'

# ── 봇 하트비트 ────────────────────────────────────────────────────────
# HTS 는 BOT_PING_INTERVAL_SECONDS 마다 상태를 보고하고(응답의 nextPingSeconds 로도 안내),
# 서버는 BOT_MISSED_PINGS_ALLOWED 회 연속 누락되면 '통신단절'로 판정한다.
# 여유(GRACE)는 네트워크 지연·스케줄러 흔들림으로 정상 가동 중에 깜빡이는 것을 막는 완충값이다.
BOT_PING_INTERVAL_SECONDS = 10
BOT_MISSED_PINGS_ALLOWED = 3
BOT_PING_GRACE_SECONDS = 5
BOT_OFFLINE_AFTER_SECONDS = (BOT_PING_INTERVAL_SECONDS * BOT_MISSED_PINGS_ALLOWED
                             + BOT_PING_GRACE_SECONDS)  # 35초

# ── 봇 명령 (Ping 응답에 실어 보내는 유일한 하행 채널) ──────────────────
# 봇은 대개 가정용 네트워크 뒤에 있어 서버가 먼저 접속할 수 없다. 웹에서 누른
# 지시는 다음 Ping 응답에 실려 전달되므로 최대 BOT_PING_INTERVAL_SECONDS 만큼 늦는다.
#
# 봇이 ack 를 돌려줄 때까지 같은 명령을 반복해서 내려보낸다 — 재실행은 멱등하므로
# 안전하고, 응답이 유실돼도 결국 전달된다. 다만 봇이 영영 응답하지 않는 경우까지
# 무한히 붙들 수는 없어 만료를 둔다. 만료된 명령은 웹 화면에 '미처리'로 표시된다.
BOT_COMMAND_TTL_SECONDS = 3600

# 서버가 내려보낼 수 있는 명령. 스펙의 enum 에는 pause/resume 도 있지만 여기 없다 —
# 웹서버가 매매봇을 멈추는 것은 재확인·자동만료 같은 안전장치를 갖춘 별도 설계가
# 필요한 일이라, 재동기화와 같은 취급을 해서는 안 된다.
SUPPORTED_BOT_COMMANDS = ('resync',)

# ── 봇 식별 ────────────────────────────────────────────────────────────
# 하트비트·명령의 스코프는 API 키가 아니라 **사용자**다(키는 인증만 하고 곧바로
# username 으로 바뀐다). 그래서 HTS 를 여러 대 돌리면 키를 따로 발급해도 상태가
# 한 칸에 겹쳐 쓰이고, 실전봇이 죽어도 모의봇 Ping 이 화면을 '정상'으로 유지한다.
# botId 는 그 겹침을 푸는 봇 인스턴스 식별자다 — HTS 가 스스로 정해서 보낸다.
#
# botId 를 보내지 않는 구버전 HTS 는 이 값으로 묶는다. 한 대만 쓰던 기존 사용자는
# 그대로 동작하고, 두 대째가 붙는 순간부터 각자의 botId 로 갈라진다.
LEGACY_BOT_ID = 'default'
BOT_ID_MAX_LEN = 64

# 화면 대표 상태를 고를 때의 우선순위 — **나쁜 쪽이 이긴다.**
# 여러 봇 중 하나라도 죽었으면 그것이 보여야 한다. '하나라도 살아 있으면 초록'은
# 정확히 이 기능이 막으려는 오표시(실전봇 사망을 모의봇 Ping 이 가리는 것)다.
_BOT_STATE_SEVERITY = {'never': 0, 'running': 1, 'stopped': 2, 'offline': 3, 'error': 4}

def _log():
    """요청 컨텍스트가 있으면 앱 로거, 없으면(스크립트·테스트) 모듈 로거."""
    return current_app.logger if has_app_context() else _module_log


def _now_kst():
    return datetime.now(KST)


def _now_kst_str():
    return _now_kst().strftime('%Y-%m-%d %H:%M:%S')


def _should_touch_last_used(stored_value, now=None):
    """last_used_at 을 지금 갱신해야 하는지 여부.

    저장된 값이 없거나 읽을 수 없으면 갱신한다(첫 사용 기록은 남겨야 한다).
    """
    last = _parse_stored_dt(stored_value)
    if last is None:
        return True
    elapsed = ((now or _now_kst()) - last).total_seconds()
    # 시계가 뒤로 간 경우(elapsed < 0)에도 갱신해 값이 미래에 굳는 것을 막는다.
    return not (0 <= elapsed < LAST_USED_WRITE_INTERVAL_SECONDS)


def _now_iso():
    """오프셋을 포함한 ISO 8601 문자열 (예: 2026-08-01T20:08:04+09:00).

    봇 하트비트처럼 '경과 시간'을 계산해야 하는 값은 오프셋 없는 KST 문자열로
    저장하면 안 된다. 브라우저나 다른 타임존의 서버가 이를 로컬 시각으로 읽어
    몇 시간씩 어긋난 경과 시간을 만들어내기 때문이다.
    """
    return _now_kst().isoformat(timespec='seconds')


def _parse_stored_dt(value):
    """DB 에 저장된 시각 문자열을 tz-aware datetime 으로 되돌린다.

    ISO 8601(오프셋 포함)이 표준이지만, 이전 버전이 남긴 오프셋 없는
    'YYYY-MM-DD HH:MM:SS' 값도 KST 로 간주해 함께 받아준다.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).strip().replace(' ', 'T', 1))
    except ValueError:
        return None
    return dt.replace(tzinfo=KST) if dt.tzinfo is None else dt


def _err(status, code, message, **details):
    body = {'error': message, 'errorCode': code}
    if details:
        body['details'] = details
    return jsonify(body), status

