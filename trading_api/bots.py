"""봇 인스턴스 등록·상태 판정과 하행 명령 큐.

봇은 대개 가정용 네트워크 뒤에 있어 서버가 먼저 접속할 수 없다. 그래서 상태는
봇이 올리는 하트비트로만 알 수 있고, 지시는 다음 하트비트 응답에 실어 내려보낸다.
이 파일은 그 두 방향을 모두 담는다.
"""

import json
from datetime import timedelta

from app.database.db import db_conn

from . import common
from .common import (
    BOT_ID_MAX_LEN, BOT_OFFLINE_AFTER_SECONDS,
    LEGACY_BOT_ID, SUPPORTED_BOT_COMMANDS, _BOT_STATE_SEVERITY,
    _now_iso, _now_kst, _parse_stored_dt,
)

def evaluate_bot_state(bot_status, bot_last_seen, now=None):
    """봇 표시 상태를 서버에서 확정한다.

    만료 판정을 클라이언트에 맡기면 브라우저 시계 오차와 타임존 해석 차이가
    그대로 오판이 된다. 화면은 여기서 내려준 state 를 그리기만 하면 된다.

    반환: (state, elapsed_seconds)
      never   — 연동 기록 없음
      running — 정상 가동중
      stopped — HTS 가 정상 종료를 알림
      error   — HTS 가 오류를 알림
      offline — Ping 이 BOT_MISSED_PINGS_ALLOWED 회 연속 누락됨(통신단절)
    """
    if not bot_status:
        return 'never', None

    last_seen = _parse_stored_dt(bot_last_seen)
    if last_seen is None:
        return 'never', None

    elapsed = ((now or _now_kst()) - last_seen).total_seconds()

    # 마지막 보고가 'stopped'/'error' 면 오래됐든 아니든 그 사유가 통신단절보다 정확하다.
    status = str(bot_status).strip().lower()
    if status in ('stopped', 'error'):
        return status, elapsed
    if elapsed > BOT_OFFLINE_AFTER_SECONDS:
        return 'offline', elapsed
    return 'running', elapsed


def _normalize_bot_id(value):
    """봇 식별자 정규화. 비었으면 구버전 취급(LEGACY_BOT_ID)."""
    text = str(value or '').strip()[:BOT_ID_MAX_LEN]
    return text or LEGACY_BOT_ID


def _upsert_bot(c, username, bot_id, status, now, *, label=None,
                is_simulated=False, message=None):
    """봇 하트비트를 인스턴스 단위로 기록한다.

    label 은 봇이 보낼 때만 갱신한다 — 매 Ping 마다 덮으면, 라벨을 안 보내는
    구버전으로 잠깐 되돌렸을 때 화면에서 이름이 사라진다.
    """
    c.execute(
        "INSERT INTO bots (username, bot_id, label, status, last_seen, "
        "                  is_simulated, message, first_seen) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(username, bot_id) DO UPDATE SET "
        "  status = excluded.status, last_seen = excluded.last_seen, "
        "  is_simulated = excluded.is_simulated, message = excluded.message, "
        "  label = COALESCE(excluded.label, bots.label)",
        (username, bot_id, label, status, now, 1 if is_simulated else 0, message, now))


def list_bots(username, now=None):
    """이 사용자의 봇 인스턴스 목록. 각 행에 서버가 확정한 state 를 붙여 돌려준다."""
    with db_conn() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT bot_id, label, status, last_seen, is_simulated, message, first_seen "
            "FROM bots WHERE username = ? ORDER BY bot_id", (username,))
        rows = [dict(r) for r in c.fetchall()]

    items = []
    for row in rows:
        state, elapsed = evaluate_bot_state(row['status'], row['last_seen'], now=now)
        items.append({
            'botId': row['bot_id'],
            'label': row['label'] or row['bot_id'],
            'status': row['status'],
            'state': state,
            'lastSeen': row['last_seen'],
            'elapsedSeconds': round(elapsed, 1) if elapsed is not None else None,
            'isSimulated': bool(row['is_simulated']),
            'message': row['message'] or None,
        })
    return items


def delete_bot(username, bot_id):
    """봇 등록을 지운다. 지워졌으면 True.

    필요한 이유: 봇 식별자가 바뀌거나(모드별 구분자 도입 등) 기기를 폐기하면 옛 행이
    남는데, 대표 상태는 **가장 나쁜 봇**을 따르므로 그 유령 행 하나가 표시등을 영구히
    '통신단절'로 굳혀 버린다. 그러면 진짜 장애를 알리는 신호가 죽는다.

    살아 있는 봇을 지워도 다음 Ping 에 다시 등록되므로 파괴적인 동작은 아니다.
    받을 봇이 없어진 대기 명령은 함께 지운다.
    """
    with db_conn() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM bots WHERE username = ? AND bot_id = ?", (username, bot_id))
        removed = c.rowcount > 0
        if removed:
            c.execute("DELETE FROM bot_commands "
                      "WHERE username = ? AND bot_id = ? AND acked_at IS NULL",
                      (username, bot_id))
        conn.commit()
    return removed


def summarize_bot_states(bots):
    """봇 목록에서 화면 대표 상태 하나를 고른다. (state, elapsed, botId)

    **가장 나쁜 상태가 이긴다.** 하나라도 살아 있으면 초록으로 칠하는 방식은
    실전봇이 죽은 것을 모의봇 Ping 이 가려 버린다 — 이 기능이 막으려는 그 오표시다.
    """
    if not bots:
        return 'never', None, None
    worst = max(bots, key=lambda b: _BOT_STATE_SEVERITY.get(b['state'], 0))
    return worst['state'], worst['elapsedSeconds'], worst['botId']


def _command_expiry_cutoff():
    # ⭐️ TTL 은 `common.<이름>` 으로 **쓰는 순간에** 읽는다. `from .common import
    #    BOT_COMMAND_TTL_SECONDS` 로 이름만 떼어 오면 임포트 시점의 값이 복사되어,
    #    테스트가 값을 바꿔치기해도 이 함수는 옛 값을 계속 본다.
    #    (config.py 가 경로 상수에 대해 같은 이유로 같은 규칙을 둔다)
    return (_now_kst() - timedelta(seconds=common.BOT_COMMAND_TTL_SECONDS)).isoformat()


def _take_pending_command(c, username, bot_id=None):
    """이 봇에 내려보낼 명령을 하나 집는다. 없으면 None.

    **한 번만 전달한다(at-most-once).** ack 를 받을 때까지 반복 전달하면 명령이
    반드시 실행되는 대신, 봇이 명령을 받고 ack 를 보내기 전에 재시작할 때 같은
    재동기화가 한 번 더 돈다. 서버 데이터로 보면 멱등하지만 **운용자의 의도로 보면
    멱등하지 않다** — 두 실행 사이에 운용자가 일부러 지운 기록이 되살아난다.
    지운 기록을 마음대로 되살리는 것보다, 전달이 유실됐을 때 버튼을 한 번 더 누르게
    하는 편이 훨씬 낫다.

    전달만 되고 ack 가 오지 않으면 만료될 때까지 '처리 중'으로 남았다가 '미처리'로
    바뀐다. 운용자는 그것을 보고 다시 누르면 된다.

    **대상이 지정되지 않은 명령(bot_id IS NULL)은 봇이 한 대일 때만 전달한다.**
    at-most-once 라서 여러 대가 붙어 있으면 먼저 Ping 한 아무 봇이나 채가는데,
    그 봇은 자기 로컬 DB 만 재전송하고 ack 까지 보낸다 — 정작 복구하려던 계좌는
    아무 일도 일어나지 않았는데 웹에는 '완료'로 뜨는 조용한 실패가 된다.
    전달되지 않은 명령은 '미처리'로 남아 운용자가 다시 누를 수 있다.
    """
    bot_id = _normalize_bot_id(bot_id)
    c.execute("SELECT COUNT(*) AS cnt FROM bots WHERE username = ?", (username,))
    solo = (c.fetchone()['cnt'] or 0) <= 1

    scope = "(bot_id = ? OR bot_id IS NULL)" if solo else "bot_id = ?"
    c.execute(
        "SELECT id, command, params_json FROM bot_commands "
        f"WHERE username = ? AND {scope} "
        "  AND acked_at IS NULL AND delivered_at IS NULL AND requested_at >= ? "
        "ORDER BY id LIMIT 1",
        (username, bot_id, _command_expiry_cutoff()))
    row = c.fetchone()
    if row is None:
        return None

    c.execute("UPDATE bot_commands SET delivered_at = ? WHERE id = ?",
              (_now_iso(), row['id']))
    try:
        params = json.loads(row['params_json']) if row['params_json'] else None
    except (TypeError, ValueError):
        params = None
    return {'id': row['id'], 'command': row['command'], 'params': params}


def _apply_command_ack(c, username, ack, bot_id=None):
    """봇이 보고한 처리 결과를 반영한다. 형식이 어긋나면 조용히 무시한다.

    ack 가 잘못됐다고 Ping 자체를 400 으로 되돌리면 안 된다 — 하트비트가 끊겨
    웹 화면이 '통신단절'로 바뀐다. 상태 보고가 ack 보다 중요하다.

    bot_id 를 주면 그 봇이 실제로 받아 간 명령만 마감한다. 봇이 여러 대일 때
    엉뚱한 봇의 ack 가 남의 명령을 '완료'로 덮는 것을 막는다.
    """
    if not isinstance(ack, dict):
        return
    try:
        command_id = int(ack.get('id'))
    except (TypeError, ValueError):
        return

    result = str(ack.get('result') or 'queued')[:20]
    try:
        count = int(ack.get('count') or 0)
    except (TypeError, ValueError):
        count = 0
    message = str(ack.get('message') or '')[:500]

    scope = "AND (bot_id = ? OR bot_id IS NULL)" if bot_id else ""
    args = [_now_iso(), result, count, message, command_id, username]
    if bot_id:
        args.append(bot_id)
    c.execute(
        "UPDATE bot_commands SET acked_at = ?, result = ?, result_count = ?, "
        f"result_message = ? WHERE id = ? AND username = ? AND acked_at IS NULL {scope}",
        args)


def request_bot_command(username, command, params=None, bot_id=None):
    """웹 세션에서 호출 — 봇에 내려보낼 명령을 큐에 넣는다. (명령 id)

    같은 명령이 이미 대기 중이면 새로 만들지 않고 그것을 돌려준다. 버튼을 여러 번
    눌렀다고 재동기화가 여러 번 돌 이유가 없다. **봇이 여럿이면 대상별로 따로 센다**
    — 실전봇 재동기화가 대기 중이라고 모의봇 요청까지 삼키면 안 된다.

    bot_id=None 은 '대상 미지정'이다. 봇이 한 대뿐일 때만 전달된다.
    """
    if command not in SUPPORTED_BOT_COMMANDS:
        raise ValueError(f'지원하지 않는 명령입니다: {command}')

    bot_id = str(bot_id).strip()[:BOT_ID_MAX_LEN] if bot_id else None

    with db_conn() as conn:
        c = conn.cursor()
        scope = "bot_id = ?" if bot_id else "bot_id IS NULL"
        args = [username, command]
        if bot_id:
            args.append(bot_id)
        c.execute(
            f"SELECT id FROM bot_commands WHERE username = ? AND command = ? AND {scope} "
            "AND acked_at IS NULL AND requested_at >= ? ORDER BY id LIMIT 1",
            (*args, _command_expiry_cutoff()))
        existing = c.fetchone()
        if existing is not None:
            return existing['id']

        c.execute(
            "INSERT INTO bot_commands (username, command, params_json, requested_at, bot_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (username, command,
             json.dumps(params, ensure_ascii=False) if params else None,
             _now_iso(), bot_id))
        conn.commit()
        return c.lastrowid


def latest_bot_command(username, command=None, bot_id=None):
    """웹 화면 표시용 — 가장 최근 명령의 상태. 없으면 None."""
    with db_conn() as conn:
        c = conn.cursor()
        sql = ("SELECT id, command, params_json, requested_at, delivered_at, "
               "acked_at, result, result_count, result_message, bot_id "
               "FROM bot_commands WHERE username = ?")
        params = [username]
        if command:
            sql += " AND command = ?"
            params.append(command)
        if bot_id:
            sql += " AND bot_id = ?"
            params.append(bot_id)
        c.execute(sql + " ORDER BY id DESC LIMIT 1", params)
        row = c.fetchone()

    if row is None:
        return None

    item = dict(row)
    item['botId'] = item.pop('bot_id', None)
    try:
        item['params'] = json.loads(item.pop('params_json') or 'null')
    except (TypeError, ValueError):
        item['params'] = None

    if item['acked_at']:
        item['state'] = 'done'
    elif item['requested_at'] < _command_expiry_cutoff():
        # 봇이 만료될 때까지 가져가지 않았다 — 대개 봇이 꺼져 있었던 것이다.
        item['state'] = 'expired'
    elif item['delivered_at']:
        item['state'] = 'running'
    else:
        item['state'] = 'pending'
    return item

