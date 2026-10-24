"""매매 기록 변경 — 웹 화면과 봇 API 가 **같은 규칙으로** 기록을 고친다.

예전에는 이 규칙이 각 HTTP 핸들러 안에 조립돼 있었다. 그래서 웹 수정·삭제에
'매수를 줄이면 이미 한 매도에 검토 필요를 붙인다'를 넣었을 때 봇의 PATCH·DELETE
는 그것을 지나치지 않았고, 봇이 매수를 취소로 정정하면 보유가 음수가 된 채
아무 표시 없이 남았다. 같은 기록을 고치는 길이 둘이면 규칙은 한 곳에 있어야 한다.

한 번의 변경은 항상 이 순서를 지난다.
    기존 기록 확인 → 매도 무결성 → 초과 매도 판정 → 쓰기 → 검토 표시 → 캐시 무효화

정책은 호출자에 따라 하나만 다르다(entry_logic 머리말 참고).
    웹  사람이 화면 앞에 있다. 무결성 위반은 막고(InvalidEntry), 초과 매도는 확인을
        받는다(OversellConflict → 화면이 force=True 로 다시 보낸다).
    봇  막지 않는다. 되돌리면 재시도해도 계속 실패해 체결이 유실된다. 저장하고
        '검토 필요'를 붙인 뒤 경고로 알린다.

봇 체결의 **신규 저장**(POST /api/v1/trades)은 멱등키·배치 트랜잭션이 얽혀 있어
trading_api.entries 가 맡는다. 정책은 위의 '봇' 그대로다.
"""

import time
from datetime import datetime, timezone

from app.database import entry_logic
from app.database.db import db_conn
from app.domain.validation import normalize_web_entry
from app.domain.trades import KST, parse_entry_dt, trade_date_for
from app.services import images, realized_pnl
from app.utils import statscache
from app.utils.userlocks import serialized_user_change


class InvalidEntry(Exception):
    """저장할 수 없는 입력(웹). 메시지는 화면에 그대로 보여 줄 문장이다."""


class EntryNotFound(Exception):
    """없거나, 이 사용자의 것이 아니거나, 이 경로로 고칠 수 없는 기록."""


class StaleEntry(Exception):
    """수정창을 연 뒤 기록이 변경되었다."""


class OversellConflict(Exception):
    """이 변경으로 이미 한 매도가 보유를 넘는다(웹). 확인을 받은 뒤 force=True 로 진행한다."""


# 봇 PATCH 가 이 칸들을 건드릴 때만 매도 자신을 다시 잰다. 메모 한 줄 고쳤다고
# 사람이 이미 확인을 마친 옛 매도에 표시가 되살아나면 안 된다.
_HOLDING_COLUMNS = frozenset({'quantity', 'tradeStatus', 'stockName', 'stockCode', 'tradeType', 'type',
                              'brokerAccount', 'subAccount', 'accountName', 'currency', 'rawDate', 'date'})
_PNL_COLUMNS = _HOLDING_COLUMNS | {'price', 'realizedPnl', 'realizedPnlSource'}


def _refresh_pnl_if_changed(conn, username, before, after):
    # 본문·태그만 고칠 때는 전체 거래의 원가 재구성과 정렬을 생략한다.
    if any(before.get(k) != after.get(k) for k in _PNL_COLUMNS):
        realized_pnl.refresh(conn, username)


def _load(c, username, entry_id):
    return c.execute("SELECT * FROM entries WHERE id = ? AND username = ?",
                     (entry_id, username)).fetchone()


def _assign_entry_id(c, entry):
    """화면이 보낸 id 를 쓰되, 쓸 수 없으면 새로 배정한다. 최종 id 를 돌려준다.

    ⭐️ 화면은 id 를 Date.now()(밀리초)로 만든다. entries.id 는 **모든 사용자에 걸친**
       기본키라, 같은 밀리초에 다른 사용자가 저장하면 UNIQUE 위반으로 500 이 났다.
       정수가 아니거나 이미 쓰이는 id 면 가장 큰 id 다음 값을 준다. 화면은 응답의 id 로
       자기 목록을 고친다.
    """
    entry_id = entry.get('id')
    if isinstance(entry_id, int) and not isinstance(entry_id, bool) and 0 < entry_id < 2**53:
        if c.execute("SELECT 1 FROM entries WHERE id = ?", (entry_id,)).fetchone() is None:
            return entry_id
    top = c.execute("SELECT MAX(id) FROM entries").fetchone()[0] or 0
    return max(top + 1, int(time.time() * 1000))


def _flag_oversold(c, username, old_row, conflict):
    deficit, message = conflict
    return entry_logic.flag_oversold_sells(
        c, username, (old_row['stockName'] or '').strip(), old_row['stockCode'],
        deficit, message + " (매수 기록을 고치거나 지운 뒤 확인이 필요합니다)", scope=dict(old_row))


def _release_uploads(username, old_row, new_row=None):
    """old_row 가 쓰던 첨부 중 new_row 가 더 이상 쓰지 않는 것을 정리한다(커밋 뒤에 부른다).

    다른 기록이 같은 파일을 쓰고 있으면 남긴다(images.remove_unreferenced).
    """
    if old_row is None:
        return
    images.remove_unreferenced(
        username, images.entry_uploads(username, old_row) - images.entry_uploads(username, new_row))


def _derived_time_fields(entry, executed_at=None):
    dt = executed_at if executed_at is not None else parse_entry_dt(entry)
    if dt is None:
        raise InvalidEntry('거래 일시를 입력해주세요.')
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return {
        'executedAtUtc': dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'tradeDate': trade_date_for(dt, entry.get('exchange')),
    }


# ---------------------------------------------------------------------------
# 웹 화면
# ---------------------------------------------------------------------------

@serialized_user_change
def create(username, entry):
    """새 기록을 저장하고 최종 id 를 돌려준다. 매도 무결성 위반이면 InvalidEntry."""
    try:
        entry = normalize_web_entry(entry)
    except ValueError as exc:
        raise InvalidEntry(str(exc)) from exc
    # 새 요청이 제공한 금액의 출처는 서버에서 결정한다. 복원의 출처 보존과 분리한다.
    entry['realizedPnlSource'] = 'recorded' if entry.get('realizedPnl') is not None else None
    with db_conn(write=True) as conn:
        c = conn.cursor()
        entry_id = _assign_entry_id(c, entry)
        entry = dict(entry, id=entry_id)
        if not entry.get('rawDate') and not entry.get('date'):
            entry['rawDate'] = datetime.now(KST).replace(tzinfo=None).isoformat()
        error = entry_logic.validate_trade_entry(c, username, entry)
        if error:
            raise InvalidEntry(error)
        # ⭐️ 본문 내장 base64 이미지를 파일로 추출 (초기 로딩 응답 크기 유지).
        #    검증을 통과한 뒤에 한다 — 먼저 하면 거부된 저장의 이미지가 파일로 남는다.
        entry, created = images.store_inline_images(username, dict(entry, id=entry_id))
        try:
            entry_logic.insert_entry(c, username, entry)
            if entry.get('type') == 'trade':
                realized_pnl.refresh(conn, username)
            conn.commit()
        except Exception:
            conn.rollback()
            images.remove_unreferenced(username, created)
            raise
    statscache.invalidate(username)
    return entry_id


@serialized_user_change
def update(username, entry_id, entry, *, force=False):
    """기록 전체를 화면 입력으로 바꾼다. 검토 필요를 붙인 매도 id 목록을 돌려준다."""
    with db_conn(write=True) as conn:
        c = conn.cursor()
        # ⭐️ 없는(다른 기기에서 지운) 기록을 고치면 예전에는 0건 갱신인데도 성공으로
        #    답해, 사용자는 저장된 줄 알았다. 먼저 있는지 본다.
        old = _load(c, username, entry_id)
        if old is None:
            raise EntryNotFound()

        expected = entry.get('revision')
        if expected is not None and (type(expected) is not int or expected != old['revision']):
            raise StaleEntry('다른 곳에서 변경된 기록입니다. 내용을 다시 불러온 뒤 수정해주세요.')

        # 연동 원본과 누락된 값은 보존한다. 웹 폼은 체결 ID·통화 등을 보내지 않는다.
        allowed = set(entry_logic.WEB_UPDATE_COLUMNS)
        entry = dict(old, **{k: v for k, v in entry.items() if k in allowed})
        try:
            entry = normalize_web_entry(entry)
        except ValueError as exc:
            raise InvalidEntry(str(exc)) from exc

        # 수정 중인 기록 자신은 집계에서 뺀다.
        error = (entry_logic.validate_trade_entry(c, username, entry, exclude_id=entry_id)
                 if any(entry.get(k) != old[k] for k in _HOLDING_COLUMNS) else None)
        if error:
            raise InvalidEntry(error)

        conflict = entry_logic.oversell_after_change(c, username, old, entry)
        if conflict and not force:
            raise OversellConflict(conflict[1])

        # ⭐️ 본문 내장 base64 이미지를 파일로 추출 (초기 로딩 응답 크기 유지).
        #    검증·확인(409)을 모두 지난 뒤에 한다 — 409 후 force 로 다시 보내면
        #    같은 이미지가 두 벌 저장되고 앞의 것은 아무도 참조하지 않았다.
        entry, created = images.store_inline_images(username, entry)
        try:
            entry_logic.update_entry_row(c, entry_id, username, entry)
            if (old['executedAtUtc'] or old['tradeDate']) and (
                    entry.get('rawDate') != old['rawDate'] or entry.get('exchange') != old['exchange']):
                entry_logic.update_columns(c, entry_id, username, _derived_time_fields(entry))
            flagged = _flag_oversold(c, username, old, conflict) if conflict else []
            if old['type'] == 'trade' or entry.get('type') == 'trade':
                _refresh_pnl_if_changed(conn, username, dict(old), entry)
            conn.commit()
        except Exception:
            conn.rollback()
            images.remove_unreferenced(username, created)
            raise
    # 수정하면서 본문에서 뺀 이미지 — 다른 기록도 쓰지 않으면 지운다(커밋 뒤에).
    _release_uploads(username, old, entry)
    statscache.invalidate(username)
    return flagged


@serialized_user_change
def delete(username, entry_id, *, force=False):
    """기록을 지운다. 이미 없으면 조용히 통과한다(다른 기기에서 먼저 지운 경우)."""
    flagged = []
    with db_conn(write=True) as conn:
        c = conn.cursor()
        old = _load(c, username, entry_id)
        if old is not None:
            conflict = entry_logic.oversell_after_change(c, username, old, None)
            if conflict and not force:
                raise OversellConflict(conflict[1])
            c.execute("DELETE FROM entries WHERE id = ? AND username = ?", (entry_id, username))
            if conflict:
                flagged = _flag_oversold(c, username, old, conflict)
            if old['type'] == 'trade':
                realized_pnl.refresh(conn, username)
            conn.commit()
    _release_uploads(username, old)
    statscache.invalidate(username)
    return flagged


def resolve_review(username, entry_id):
    """'검토 필요' 표시를 내린다. 사람이 확인을 마쳤다는 뜻이다.

    기록을 수정해도 자동으로 풀지 않는다 — 고친 뒤에도 맞는지는 사람이 판단한다.
    """
    with db_conn() as conn:
        cur = conn.execute("UPDATE entries SET needsReview = 0, reviewReason = NULL "
                           "WHERE id = ? AND username = ?", (entry_id, username))
        conn.commit()
    if cur.rowcount == 0:
        raise EntryNotFound()
    statscache.invalidate(username)


# ---------------------------------------------------------------------------
# 봇 API — 봇이 만든 기록만, 막지 않고 표시한다
# ---------------------------------------------------------------------------

def _load_bot_entry(c, username, entry_id):
    row = _load(c, username, entry_id)
    if row is None or not entry_logic.is_bot_entry(row):
        raise EntryNotFound()
    return row


@serialized_user_change
def patch_bot_trade(username, entry_id, changes, *, executed_at=None):
    """봇이 만든 기록의 일부 칸(DB 컬럼 → 값)만 고친다. (고친 행, 경고 목록)을 돌려준다."""
    warnings = []
    with db_conn(write=True) as conn:
        c = conn.cursor()
        old = _load_bot_entry(c, username, entry_id)
        changes = dict(changes)
        if 'realizedPnl' in changes:
            changes['realizedPnlSource'] = 'recorded' if changes['realizedPnl'] is not None else None
        after = dict(old, **changes)
        if 'rawDate' in changes:
            changes.update(_derived_time_fields(after, executed_at))
            after.update(changes)

        if _HOLDING_COLUMNS & set(changes):
            integrity = entry_logic.check_sell_integrity(c, username, after, exclude_id=entry_id)
            if integrity:
                changes['needsReview'] = 1
                changes['reviewReason'] = integrity[1]
                warnings.append(integrity[1])

        conflict = entry_logic.oversell_after_change(c, username, old, after)
        entry_logic.update_columns(c, entry_id, username, changes)
        if conflict:
            _flag_oversold(c, username, old, conflict)
            warnings.append(conflict[1])
        _refresh_pnl_if_changed(conn, username, dict(old), after)
        conn.commit()
        row = _load(c, username, entry_id)
    # memo 를 바꾸면 웹에서 붙였던 본문 이미지가 빠질 수 있다.
    _release_uploads(username, old, row)
    statscache.invalidate(username)
    return row, warnings


@serialized_user_change
def delete_bot_trade(username, entry_id):
    """봇이 만든 기록을 지운다. 그 매수로 덮이던 매도가 있으면 검토 필요를 붙인다."""
    with db_conn(write=True) as conn:
        c = conn.cursor()
        old = _load_bot_entry(c, username, entry_id)
        conflict = entry_logic.oversell_after_change(c, username, old, None)
        c.execute("DELETE FROM entries WHERE id = ? AND username = ?", (entry_id, username))
        warnings = []
        if conflict:
            _flag_oversold(c, username, old, conflict)
            warnings.append(conflict[1])
        realized_pnl.refresh(conn, username)
        conn.commit()
    # 웹에서 이 봇 기록에 본문 이미지를 붙였을 수 있다.
    _release_uploads(username, old)
    statscache.invalidate(username)
    return warnings
