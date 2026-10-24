"""원본 체결을 보존하면서 모든 잔고·원가 계산의 주식 단위를 맞춘다.

분할 후 단위로 매수·매도 수량과 단가를 함께 환산하면 거래금액과 실현손익은
변하지 않는다. 과거 일별 평가에서는 원본 단위와 당시 종가를 사용한다.
"""

from datetime import datetime
import json
import math

from app.domain.trades import KST, currency_of, resolve_trade_identities, split_scope, stock_identity, trade_day


def prepare(conn, username, rows, as_of=None, rules=None, mappings=None):
    from app.services import accounts
    mappings = accounts.load(conn, username) if mappings is None else mappings
    aliases = accounts.broker_aliases(mappings)
    rows = resolve_trade_identities([accounts.identify_entry(row, mappings, aliases) for row in rows])
    last = as_of or datetime.now(KST).date().isoformat()
    if rules is None:
        rules = list(conn.execute('SELECT * FROM split_rules WHERE username=?', (username,)))
    rules = {(r['code'], r['currency'], r['day'], r.get('scope', '')): r['mode'] for r in map(dict, rules)}
    actions = {}
    for key in {(stock_identity(r), currency_of(r)) for r in rows if r.get('type') == 'trade'}:
        asset = conn.execute('SELECT splits FROM historical_assets WHERE code=? AND currency=?', key).fetchone()
        splits = json.loads(asset['splits']) if asset else {}
        actions[key] = {day: ratio for day, ratio in splits.items() if day <= last}
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
