"""매매 기록(entries) 영속화 및 데이터 무결성 검증 로직.

INSERT 컬럼 목록을 단일 소스로 관리하여 create_entry / 복원 / 마이그레이션
세 곳에 흩어져 있던 21개 컬럼 중복을 제거합니다.
모든 함수는 커서(cursor)를 인자로 받아 DB 모듈에 직접 의존하지 않습니다.

쓰기 경로와 매도 무결성 정책
----------------------------
entries 에 쓰는 곳은 네 군데이고, **매도 무결성 위반을 어떻게 다루는지가 서로
다릅니다.** 그 판단은 각 호출부의 사정에서 나오므로 여기서 하나로 강제하지
않되, 어디가 무엇을 쓰는지는 한곳에 적어 둡니다 — 새 쓰기 경로를 만들 때
"둘 중 어느 쪽인가"를 반드시 정하고 넘어가라는 뜻입니다.

  api.py (웹 UI 입력)      validate_trade_entry  → 위반 시 **차단**(400)
      사람이 화면 앞에 있으므로 즉시 고칠 수 있다. 틀린 채로 저장하는 것보다
      되돌려 주는 편이 낫다.
      매수를 줄이거나 지우는 쪽은 oversell_after_change → **경고(409) 후 확인하면
      진행**하고, 초과된 매도에 needsReview 를 붙인다(flag_oversold_sells).
      매수 삭제를 차단하면 관련 매도를 먼저 모두 고쳐야 해서 정정이 막힌다.

  trading_api/entries.py   check_sell_integrity  → 저장하되 **needsReview 표시**
      (봇 체결)            봇 체결을 400 으로 되돌리면 재시도해도 계속 실패해
      그 체결이 영구 유실된다. 유실보다는 '검토 필요' 상태로 남기는 편이 낫다.

  backup_api.py (복원)     검증하지 않음
      이미 한 번 통과했던 과거 데이터를 그대로 되돌리는 일이다. 지금 규칙으로
      다시 재면 옛 기록이 복원 도중 거부되어 백업이 반쪽이 된다.

  auth.py (레거시 JSON 이관)  검증하지 않음
      위와 같은 이유. 1회성 이관이며 원본을 손실 없이 옮기는 것이 목적이다.
"""

import sqlite3

# 시스템 트레이딩 API(v2)가 채우는 확장 컬럼.
# ⚠️ 이 컬럼들은 INSERT 에만 포함하고 _UPDATE_COLUMNS 에는 넣지 않는다.
#    웹 UI 의 수정(PUT /api/entry/<id>)은 화면 입력값으로 entry 를 새로 조립하므로
#    UPDATE 대상에 넣으면 봇이 기록한 손익·모의여부 등이 NULL 로 덮여 사라진다.
#    API 경유 정정(PATCH)은 trading_api 가 대상 컬럼만 지정해 직접 UPDATE 한다.
#
# ⚠️ isSystem 은 isSimulated 와 달리 0/1 로 눕히지 않는다(_value_for 참고).
#    '시스템 트레이딩이 낸 주문이 아니다(0)'와 '봇이 알려주지 않았다(NULL)'는 서로
#    다른 사실이고, 분류(tradeClass) 폴백이 정확히 그 구분에 걸려 있다.
BOT_COLUMNS = [
    'isSimulated', 'tradeStatus', 'confidence', 'orderOrigin', 'source',
    'orderId', 'originalOrderId', 'realizedPnl', 'realizedPnlRate', 'fee', 'tax',
    'strategyScore', 'stopLossRate', 'executedAtUtc', 'tradeDate', 'needsReview',
    'isSystem', 'reviewReason',
]

# entries 테이블 INSERT 시 사용하는 컬럼 순서 (단일 소스)
INSERT_COLUMNS = [
    'id', 'username', 'type', 'stockName', 'stockCode', 'title', 'thoughts',
    'date', 'rawDate', 'attachedImage', 'brokerAccount', 'subAccount',
    'accountName', 'tradeType', 'price', 'quantity', 'createdAt', 'updatedAt',
    'tags', 'attachedFile', 'attachedFileName', 'isHidden', 'brokerExecutionId',
    'currency', 'exchange', 'assetType', 'tradeClass'
] + BOT_COLUMNS

# UPDATE 시 갱신하는 컬럼 (id/username/createdAt 및 BOT_COLUMNS 제외)
_UPDATE_COLUMNS = [
    'type', 'stockName', 'stockCode', 'title', 'thoughts', 'date', 'rawDate',
    'attachedImage', 'brokerAccount', 'subAccount', 'accountName', 'tradeType',
    'price', 'quantity', 'updatedAt', 'tags', 'attachedFile', 'attachedFileName',
    'isHidden', 'brokerExecutionId', 'currency', 'exchange', 'assetType', 'tradeClass'
]

# 문자열 기본값 컬럼(없으면 ''), 숫자 기본값 컬럼(없으면 0)
_DEFAULT_EMPTY = {'stockCode', 'subAccount', 'tags', 'attachedFile', 'attachedFileName', 'brokerExecutionId', 'currency', 'exchange', 'assetType', 'tradeClass'}
# isHidden: 종목 숨김 플래그(0/1). 값이 없으면 0(표시)으로 저장한다.
# isSimulated/needsReview 도 NULL 을 남기지 않아야 `= 0` 필터가 기존 기록을 놓치지 않는다.
_DEFAULT_ZERO = {'price', 'quantity', 'isHidden', 'isSimulated', 'needsReview'}

_INSERT_SQL = (
    "INSERT INTO entries ({cols}) VALUES ({ph})".format(
        cols=', '.join(INSERT_COLUMNS),
        ph=', '.join(['?'] * len(INSERT_COLUMNS)),
    )
)

_UPDATE_SQL = (
    "UPDATE entries SET {sets} WHERE id=? AND username=?".format(
        sets=', '.join(f"{c}=?" for c in _UPDATE_COLUMNS)
    )
)


def normalize_stock_code(value):
    """종목코드 표기 정규화 — 앞뒤 공백을 떼고 **대문자로 접는다**.

    ⭐️ 종목코드는 종목 동일성 판정의 1순위 키다(stats.stock_identity ·
       calc.js stockIdentity · net_holding_for_stock). 그런데 접는 규칙이 갈려
       있었다: 통계·화면·시세 조회는 대문자로 접고, 보유 매칭과 매도 검증은
       저장된 원본 그대로 비교했다. 국내 6자리 숫자 코드에서는 차이가 없어
       드러나지 않지만, 해외 티커는 봇이 'aapl' 을 보내고 사람이 'AAPL' 로
       적어 둘 수 있다. 그러면 통계·화면은 한 종목으로 묶는데 매도 검증만
       '보유 기록 없음'으로 거부한다 — 화면상 멀쩡한 보유가 팔리지 않는다.

       쓰기(_value_for → INSERT/UPDATE)와 읽기(비교) 양쪽이 이 함수 하나를
       쓴다. 저장된 값이 이미 정규형이므로 SQL 은 그대로 `=` 로 맞춰도 된다.
    """
    if value is None:
        return ''
    return str(value).strip().upper()


def _value_for(entry, col):
    if col == 'stockCode':
        # ⭐️ 정규화는 여기 한 곳에서만 한다. 웹 입력·봇 체결·백업 복원·레거시 이관
        #    네 쓰기 경로가 모두 INSERT/UPDATE 를 통해 이 함수를 지나간다.
        return normalize_stock_code(entry.get(col))
    if col in ('isHidden', 'isSimulated', 'needsReview'):
        # 프론트엔드/API 가 true/false 로 보내므로 NULL 없이 항상 0/1 로 정규화한다.
        return 1 if entry.get(col) else 0
    if col in _DEFAULT_EMPTY:
        return entry.get(col, '')
    if col in _DEFAULT_ZERO:
        return entry.get(col, 0)
    return entry.get(col)


def insert_entry(c, username, entry, attached_image=...):
    """단일 기록을 entries 테이블에 삽입합니다.

    attached_image를 명시하면 entry의 attachedImage 대신 그 값을 사용합니다.
    (JSON→DB 마이그레이션 시 base64 이미지를 파일 URL로 치환하는 용도)
    """
    values = []
    for col in INSERT_COLUMNS:
        if col == 'username':
            values.append(username)
        elif col == 'attachedImage' and attached_image is not ...:
            values.append(attached_image)
        else:
            values.append(_value_for(entry, col))
    c.execute(_INSERT_SQL, values)


def update_entry_row(c, entry_id, username, entry):
    """본인 소유의 기록을 갱신합니다."""
    values = [_value_for(entry, col) for col in _UPDATE_COLUMNS]
    values.extend([entry_id, username])
    c.execute(_UPDATE_SQL, values)


def _stock_conditions(username, stock_name, stock_code):
    """'이 사용자의 이 종목, 잔고에 반영되는 매매 기록' WHERE 조건과 파라미터.

    보유 수량 집계와 초과 매도 표시가 **같은 종목 판정**을 쓰도록 한곳에 둔다.
    """
    conditions = ["username = ?", "type = 'trade'"]
    params = [username]

    code = normalize_stock_code(stock_code)
    if code:
        # 코드 일치 OR (코드가 없는 레거시 기록 중 이름 일치)
        conditions.append("(stockCode = ? OR (COALESCE(stockCode, '') = '' AND stockName = ?))")
        params.extend([code, stock_name])
    else:
        conditions.append("stockName = ?")
        params.append(stock_name)

    # 취소된 주문과 미체결 접수는 잔고에 반영하지 않는다.
    conditions.append("COALESCE(tradeStatus, 'FILLED') NOT IN ('CANCELED', 'SUBMITTED')")
    return conditions, params


def net_holding_for_stock(c, username, stock_name, exclude_id=None, stock_code=None):
    """해당 사용자의 특정 종목 현재 순보유 수량(매수 합계 - 매도 합계)을 계산합니다.

    종목코드(stock_code)가 주어지면 코드를 1순위 기준으로 집계합니다. 종목명은
    동일 종목이라도 표기가 갈리고(우선주·해외 티커·증권사별 명칭) 봇은 코드만
    보내오므로, 이름만으로 맞추면 보유 매칭이 어긋나 정상 매도가 거부됩니다.
    코드가 비어 있는 레거시 수동 기록도 함께 잡히도록 이름 조건을 OR 로 유지합니다.
    """
    conditions, params = _stock_conditions(username, stock_name, stock_code)

    if exclude_id is not None:
        conditions.append("id != ?")
        params.append(exclude_id)

    c.execute("SELECT tradeType, quantity FROM entries WHERE " + " AND ".join(conditions), params)

    held = 0.0
    for row in c.fetchall():
        qty = float(row['quantity'] or 0)
        if row['tradeType'] == '매수':
            held += qty
        elif row['tradeType'] == '매도':
            held -= qty
    return held


def check_sell_integrity(c, username, entry, exclude_id=None):
    """매도 거래의 무결성을 점검하고 (오류코드, 메시지) 또는 None 을 반환합니다.

    - 매수 보유 기록이 없는 종목의 매도 → ('NO_POSITION', ...)
    - 보유 수량을 초과하는 매도(오버셀) → ('OVERSELL', ...)

    호출자가 이 결과를 '차단'으로 쓸지 '경고'로 쓸지 결정합니다.
    웹 UI 입력은 차단(사용자가 즉시 고칠 수 있음), 봇 API 는 경고 후 저장
    (차단하면 그 체결이 영구 유실됨) — 이 차이가 이 함수를 분리한 이유입니다.
    """
    if entry.get('type') != 'trade' or entry.get('tradeType') != '매도':
        return None

    # 취소·접수 기록은 잔고를 소모하지 않으므로 검증 대상이 아니다.
    if (entry.get('tradeStatus') or 'FILLED') in ('CANCELED', 'SUBMITTED'):
        return None

    stock_name = (entry.get('stockName') or '').strip()
    stock_code = normalize_stock_code(entry.get('stockCode'))
    if not stock_name and not stock_code:
        return None

    try:
        sell_qty = float(entry.get('quantity') or 0)
    except (TypeError, ValueError):
        return None
    if sell_qty <= 0:
        return None

    held = net_holding_for_stock(
        c, username, stock_name, exclude_id=exclude_id, stock_code=stock_code
    )
    label = stock_name or stock_code

    EPS = 1e-6  # 부동소수점 오차 허용
    if held <= EPS:
        return ('NO_POSITION', f"'{label}'은(는) 매수 보유 기록이 없어 매도할 수 없습니다.")
    if sell_qty > held + EPS:
        return ('OVERSELL', f"'{label}'의 매도 수량({sell_qty:g})이 "
                            f"현재 보유 수량({held:g})을 초과합니다.")
    return None


def _is_live_buy(entry):
    return (entry is not None
            and (entry.get('type') or 'trade') == 'trade'
            and entry.get('tradeType') == '매수'
            and (entry.get('tradeStatus') or 'FILLED') not in ('CANCELED', 'SUBMITTED'))


def _same_stock(a, b):
    code_a, code_b = normalize_stock_code(a.get('stockCode')), normalize_stock_code(b.get('stockCode'))
    if code_a and code_b:
        return code_a == code_b
    return (a.get('stockName') or '').strip() == (b.get('stockName') or '').strip()


def oversell_after_change(c, username, old_row, new_entry=None):
    """기존 매수(old_row)를 new_entry 로 고치거나(None 이면 삭제) 했을 때 이미 저장된
    매도가 보유 수량을 넘게 되는지 본다. 넘으면 (초과 수량, 메시지), 아니면 None.

    ⭐️ 매도는 입력할 때 보유 수량을 검사하지만(validate_trade_entry), 매수를 나중에
       줄이거나 지우는 쪽은 아무것도 보지 않았다. 매수 10·매도 10 에서 매수를 1 로
       고치면 보유가 -9 가 되는데도 그대로 저장됐다. 웹 입력은 경고 후 사용자가
       확인하면 진행하고, 그때 초과된 매도에 검토 필요를 붙인다(flag_oversold_sells).

       이미 음수였던 종목(과거 데이터)은 **이번 변경으로 더 나빠질 때만** 알린다.
       그렇지 않으면 무관한 수정(메모 한 줄)마다 경고가 뜬다.
    """
    old_row = dict(old_row) if old_row is not None else None
    if not _is_live_buy(old_row):
        return None
    name = (old_row.get('stockName') or '').strip()
    code = normalize_stock_code(old_row.get('stockCode'))

    held_without = net_holding_for_stock(c, username, name, exclude_id=old_row['id'],
                                         stock_code=code)
    held_before = held_without + float(old_row.get('quantity') or 0)
    held_after = held_without
    if _is_live_buy(new_entry) and _same_stock(old_row, new_entry):
        try:
            held_after += float(new_entry.get('quantity') or 0)
        except (TypeError, ValueError):
            pass

    EPS = 1e-6
    if held_after >= -EPS or held_after >= held_before - EPS:
        return None
    deficit = -held_after
    label = name or code
    return deficit, (f"이 변경으로 '{label}'의 매도 수량이 보유 수량을 "
                     f"{deficit:g}주 초과하게 됩니다.")


def flag_oversold_sells(c, username, stock_name, stock_code, deficit, reason):
    """초과분을 덮을 때까지 **가장 최근 매도부터** 검토 필요를 붙인다. 붙인 id 목록을 돌려준다.

    어느 매도가 '틀린' 것인지는 서버가 알 수 없다. 보유를 넘어선 것은 시간상 마지막
    매도들이므로 거기서부터 표시해, 사람이 확인할 출발점을 준다.
    """
    conditions, params = _stock_conditions(username, stock_name, stock_code)
    conditions.append("tradeType = '매도'")
    c.execute("SELECT id, quantity FROM entries WHERE " + " AND ".join(conditions)
              + " ORDER BY COALESCE(rawDate, '') DESC, id DESC", params)
    flagged, covered = [], 0.0
    for row in c.fetchall():
        if covered >= deficit - 1e-6:
            break
        flagged.append(row['id'])
        covered += float(row['quantity'] or 0)
    for entry_id in flagged:
        c.execute("UPDATE entries SET needsReview = 1, reviewReason = ? WHERE id = ? AND username = ?",
                  (reason, entry_id, username))
    return flagged


def validate_trade_entry(c, username, entry, exclude_id=None):
    """웹 UI 입력용 검증 — 위반 시 한국어 오류 메시지(str), 통과 시 None.

    (※ 백업 복원 등 과거 데이터 일괄 삽입에는 적용하지 않습니다.)
    """
    result = check_sell_integrity(c, username, entry, exclude_id=exclude_id)
    return result[1] if result else None


def migrate_stock_code_case(c, logger=None):
    """저장된 종목코드를 정규형(대문자)으로 접는다. 고친 행 수를 돌려준다.

    ⭐️ 정규화 규칙이 갈려 있던 시절에 들어온 기록이 남아 있다 — 통계·화면·시세
       조회는 대문자로 접고, 보유 매칭과 매도 검증은 저장된 원본 그대로 비교했다.
       이제 코드는 normalize_stock_code 하나로 모았지만, **이미 저장된 값까지
       접어 두지 않으면 옛 기록만 계속 어긋난다**: 새 매도는 정규형으로 조회하는데
       옛 매수는 소문자로 누워 있어 '매수 보유 기록이 없다'가 된다.

       멱등하다 — 고칠 것이 없으면 UPDATE 자체가 나가지 않으므로 기동할 때마다
       불러도 된다. 비교·치환을 SQL 의 upper() 가 아니라 Python 에서 하는 이유는
       저장 시점(normalize_stock_code)과 글자 하나까지 같은 규칙을 쓰기 위해서다
       (SQLite 의 upper() 는 ASCII 만 접는다).

       스키마가 아니라 **값의 의미를 아는 이관**이라 schema 모듈이 아니라 여기 있다.
    """
    try:
        c.execute("SELECT DISTINCT stockCode FROM entries "
                  "WHERE stockCode IS NOT NULL AND stockCode != ''")
        stored = [row[0] for row in c.fetchall()]
    except sqlite3.OperationalError:
        return 0  # entries 가 아직 없다 — 다음 기동 때 처리된다.

    changed = [(normalize_stock_code(code), code) for code in stored
               if normalize_stock_code(code) != code]
    if not changed:
        return 0

    # BINARY 콜레이션이라 `= ?` 가 대소문자를 구분한다 — 옛 표기만 정확히 집힌다.
    c.executemany("UPDATE entries SET stockCode = ? WHERE stockCode = ?", changed)
    fixed = c.rowcount
    if logger:
        logger.info(f"🔄 종목코드 표기 {len(changed)}종({fixed}건)을 대문자로 정규화했습니다.")
    return fixed
