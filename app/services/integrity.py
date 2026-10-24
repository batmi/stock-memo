"""저장된 거래의 정합성·변경 이력과 복원 전 잔고 비교. 외부 조회 없이 동작한다."""

from collections import defaultdict
from datetime import date
import json

from app.database.db import db_conn
from app.domain.trades import apply_trade, empty_holding, entry_order, is_settled_trade, position_key
from app.services import ledger, realized_pnl
from app.utils import statscache
from app.utils.userlocks import serialized_user_change


def positions(rows):
    holdings = defaultdict(empty_holding)
    for row in sorted(rows, key=entry_order):
        if row.get('type') == 'trade' and is_settled_trade(row):
            apply_trade(holdings[position_key(row)], row)
    return holdings


def inspect(username):
    with db_conn() as conn:
        conn.execute('BEGIN')
        rows = ledger.prepare(conn, username, [dict(r) for r in conn.execute(
            'SELECT * FROM entries WHERE username=?', (username,))])
        rules = {(r['code'], r['currency'], r['day']): r['mode'] for r in conn.execute(
            'SELECT * FROM split_rules WHERE username=?', (username,))}
        assets = {(r['code'], r['currency']): json.loads(r['splits']) for r in conn.execute(
            'SELECT code,currency,splits FROM historical_assets')}
    issues, splits, seen = [], [], set()
    holdings = defaultdict(empty_holding)
    for row in sorted(rows, key=entry_order):
        if row.get('type') != 'trade' or not is_settled_trade(row):
            continue
        key = position_key(row)
        reasons = []
        if not row.get('stockCode'):
            reasons.append('종목코드 미확인: 시세 연결을 확인해주세요.')
        if row.get('needsReview'):
            reasons.append(row.get('reviewReason') or '검토 필요로 표시된 거래입니다.')
        if row.get('tradeType') == '매도' and float(row.get('quantity') or 0) > holdings[key]['qty'] + 1e-9:
            reasons.append('매도 수량에 비해 앞선 매수 기록이 부족합니다.')
        if row.get('realizedPnlSource') == 'unavailable':
            reasons.append('매수 기록이 부족해 실현손익을 재구성하지 못했습니다.')
        if reasons:
            issues.append({'id': row['id'], 'stock': row.get('stockName'),
                           'date': row.get('rawDate') or row.get('date'), 'reasons': reasons})
        apply_trade(holdings[key], row)
        asset = (row.get('stockCode'), row.get('currency') or 'KRW')
        for day, ratio in assets.get(asset, {}).items():
            split_key = (*asset, day)
            if split_key in seen or day > date.today().isoformat():
                continue
            seen.add(split_key)
            splits.append({'code': asset[0], 'currency': asset[1], 'stock': row.get('stockName'),
                               'day': day, 'ratio': ratio, 'mode': rules.get(split_key, 'apply')})
    return {'total': len(rows), 'issueCount': len(issues), 'issues': issues[:200],
                'truncated': len(issues) > 200, 'splits': splits}


@serialized_user_change
def set_split_rule(username, data):
    if not isinstance(data, dict) or data.get('mode') not in ('apply', 'recorded'):
        raise ValueError('분할 적용 방식을 확인해주세요.')
    code, currency, day = (data.get(k) for k in ('code', 'currency', 'day'))
    if not all(isinstance(v, str) for v in (code, currency, day)):
        raise ValueError('분할 정보를 확인해주세요.')
    with db_conn(write=True) as conn:
        owned = ledger.prepare(conn, username, [dict(r) for r in conn.execute(
            'SELECT * FROM entries WHERE username=?', (username,))])
        if not any(r.get('stockCode') == code and (r.get('currency') or 'KRW') == currency for r in owned):
            raise ValueError('기록에 없는 종목입니다.')
        asset = conn.execute('SELECT splits FROM historical_assets WHERE code=? AND currency=?',
                             (code, currency)).fetchone()
        if not asset or day not in json.loads(asset['splits']):
            raise ValueError('확인된 분할 정보가 없습니다.')
        before = conn.execute('SELECT mode FROM split_rules WHERE username=? AND code=? AND currency=? AND day=?',
                              (username, code, currency, day)).fetchone()
        conn.execute('INSERT OR REPLACE INTO split_rules VALUES (?,?,?,?,?)',
                     (username, code, currency, day, data['mode']))
        conn.execute('''INSERT INTO entry_changes(username,action,origin,before_json,after_json)
            VALUES (?,'split_rule','web',?,?)''', (username,
            json.dumps({'code': code, 'currency': currency, 'day': day, 'mode': before['mode'] if before else 'apply'}),
            json.dumps({'code': code, 'currency': currency, 'day': day, 'mode': data['mode']})))
        realized_pnl.refresh(conn, username)
        conn.commit()
    statscache.invalidate(username)


def changes(username, before=None):
    with db_conn() as conn:
        rows = conn.execute('''SELECT * FROM entry_changes WHERE username=? AND (? IS NULL OR id<?)
            ORDER BY id DESC LIMIT 51''', (username, before, before)).fetchall()
    items = []
    for row in rows[:50]:
        old, new = json.loads(row['before_json'] or '{}'), json.loads(row['after_json'] or '{}')
        fields = sorted(k for k in old.keys() | new.keys() if old.get(k) != new.get(k))
        # 목록에는 첨부·본문 전체를 보내지 않는다. 긴 텍스트는 앞부분만 보여준다.
        def compact(value):
            return value[:200] + '…' if isinstance(value, str) and len(value) > 200 else value
        items.append({'id': row['id'], 'entryId': row['entry_id'], 'action': row['action'], 'origin': row['origin'],
                          'changedAt': row['changed_at'], 'stock': new.get('stockName') or old.get('stockName'),
                          'fields': {k: [compact(old.get(k)), compact(new.get(k))] for k in fields}})
    return {'items': items, 'nextCursor': rows[49]['id'] if len(rows) > 50 else None}
