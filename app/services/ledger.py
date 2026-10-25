"""원본 체결을 보존하면서 모든 잔고·원가 계산의 주식 단위를 맞춘다.

분할 후 단위로 매수·매도 수량과 단가를 함께 환산하면 거래금액과 실현손익은
변하지 않는다. 과거 일별 평가에서는 원본 단위와 당시 종가를 사용한다.
"""

from datetime import datetime
import json
import math

from app.domain.trades import KST, currency_of, resolve_trade_identities, split_scope, stock_identity, trade_day


def split_events(conn, username, keys, snapshot=None):
    """복원한 계정의 과거 기업행사는 보존하고 이후 사건만 공유 공급처에서 추가한다."""
    stored = {(r['code'], r['currency']): (r['as_of'], json.loads(r['splits'])) for r in conn.execute(
        'SELECT * FROM user_split_snapshots WHERE username=?', (username,))}
    if snapshot is not None:
        stored = {(r['code'], r['currency']): (snapshot['asOf'], r['splits']) for r in snapshot['assets']}
    events = {}
    for key in keys:
        row = conn.execute('SELECT splits FROM historical_assets WHERE code=? AND currency=?', key).fetchone()
        splits = json.loads(row['splits']) if row else {}
        if key in stored:
            cutoff, archived = stored[key]
            splits = {**{day: ratio for day, ratio in splits.items() if day > cutoff}, **archived}
        events[key] = splits
    return events


def prepare(conn, username, rows, as_of=None, rules=None, mappings=None, snapshot=None):
    from app.services import accounts
    mappings = accounts.load(conn, username) if mappings is None else mappings
    aliases = accounts.broker_aliases(mappings)
    rows = resolve_trade_identities([accounts.identify_entry(row, mappings, aliases) for row in rows])
    last = as_of or datetime.now(KST).date().isoformat()
    if rules is None:
        rules = list(conn.execute('SELECT * FROM split_rules WHERE username=?', (username,)))
    rules = {(r['code'], r['currency'], r['day'], r.get('scope', '')): r['mode'] for r in map(dict, rules)}
    actions = {key: {day: ratio for day, ratio in splits.items() if day <= last}
               for key, splits in split_events(conn, username,
                   {(stock_identity(r), currency_of(r)) for r in rows if r.get('type') == 'trade'}, snapshot).items()}
    result = []
    for source in rows:
        row = dict(source)
        key = stock_identity(row), currency_of(row)
        scope = split_scope(row)
        splits = {day: ratio for day, ratio in actions.get(key, {}).items()
                  if rules.get((*key, day, scope), rules.get((*key, day, ''), 'apply')) != 'recorded'}
        stamp = trade_day(row)
        factor = math.prod(ratio for day, ratio in splits.items() if stamp and day > stamp.date().isoformat())
        if not math.isfinite(factor) or factor <= 0:
            raise ValueError('주식 분할 비율이 올바르지 않습니다.')
        qty, price = row.get('_originalQuantity', row.get('quantity')), row.get('_originalPrice', row.get('price'))
        row.update(_originalQuantity=qty, _originalPrice=price, _splitFactor=factor, _splits=splits)
        if row.get('type') == 'trade':
            row['quantity'] = float(qty or 0) * factor
            row['price'] = float(price or 0) / factor
        result.append(row)
    return result
