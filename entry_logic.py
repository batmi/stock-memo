"""매매 기록(entries) 영속화 및 데이터 무결성 검증 로직.

INSERT 컬럼 목록을 단일 소스로 관리하여 create_entry / 복원 / 마이그레이션
세 곳에 흩어져 있던 21개 컬럼 중복을 제거합니다.
모든 함수는 커서(cursor)를 인자로 받아 DB 모듈에 직접 의존하지 않습니다.
"""

# entries 테이블 INSERT 시 사용하는 컬럼 순서 (단일 소스)
INSERT_COLUMNS = [
    'id', 'username', 'type', 'stockName', 'stockCode', 'title', 'thoughts',
    'date', 'rawDate', 'attachedImage', 'brokerAccount', 'subAccount',
    'accountName', 'tradeType', 'price', 'quantity', 'createdAt', 'updatedAt',
    'tags', 'attachedFile', 'attachedFileName',
]

# UPDATE 시 갱신하는 컬럼 (id/username/createdAt 제외)
_UPDATE_COLUMNS = [
    'type', 'stockName', 'stockCode', 'title', 'thoughts', 'date', 'rawDate',
    'attachedImage', 'brokerAccount', 'subAccount', 'accountName', 'tradeType',
    'price', 'quantity', 'updatedAt', 'tags', 'attachedFile', 'attachedFileName',
]

# 문자열 기본값 컬럼(없으면 ''), 숫자 기본값 컬럼(없으면 0)
_DEFAULT_EMPTY = {'stockCode', 'subAccount', 'tags', 'attachedFile', 'attachedFileName'}
_DEFAULT_ZERO = {'price', 'quantity'}

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


def _value_for(entry, col):
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


def net_holding_for_stock(c, username, stock_name, exclude_id=None):
    """해당 사용자의 특정 종목 현재 순보유 수량(매수 합계 - 매도 합계)을 계산합니다.
    프론트엔드 포트폴리오 대시보드와 동일하게 종목명을 기준으로 집계합니다."""
    query = ("SELECT tradeType, quantity FROM entries "
             "WHERE username = ? AND type = 'trade' AND stockName = ?")
    params = [username, stock_name]
    if exclude_id is not None:
        query += " AND id != ?"
        params.append(exclude_id)
    c.execute(query, params)

    held = 0.0
    for row in c.fetchall():
        qty = float(row['quantity'] or 0)
        if row['tradeType'] == '매수':
            held += qty
        elif row['tradeType'] == '매도':
            held -= qty
    return held


def validate_trade_entry(c, username, entry, exclude_id=None):
    """매도 거래의 데이터 무결성을 검증합니다.

    - 매수 보유 기록이 없는 종목의 매도를 차단합니다.
    - 보유 수량을 초과하는 매도(오버셀)를 차단합니다.

    검증 통과 시 None, 위반 시 한국어 오류 메시지(str)를 반환합니다.
    (※ 백업 복원 등 과거 데이터 일괄 삽입에는 적용하지 않습니다.)
    """
    if entry.get('type') != 'trade' or entry.get('tradeType') != '매도':
        return None

    stock_name = (entry.get('stockName') or '').strip()
    if not stock_name:
        return None

    try:
        sell_qty = float(entry.get('quantity') or 0)
    except (TypeError, ValueError):
        return None
    if sell_qty <= 0:
        return None

    held = net_holding_for_stock(c, username, stock_name, exclude_id=exclude_id)

    EPS = 1e-6  # 부동소수점 오차 허용
    if held <= EPS:
        return f"'{stock_name}'은(는) 매수 보유 기록이 없어 매도할 수 없습니다."
    if sell_qty > held + EPS:
        return (f"'{stock_name}'의 매도 수량({sell_qty:g})이 "
                f"현재 보유 수량({held:g})을 초과합니다.")
    return None
