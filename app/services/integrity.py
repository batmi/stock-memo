"""저장된 거래의 정합성·변경 이력과 복원 전 잔고 비교. 외부 조회 없이 동작한다."""

from collections import defaultdict
from datetime import datetime
import json
import hashlib

from flask import current_app
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.database.db import db_conn
from app.domain.trades import KST, apply_trade, empty_holding, entry_order, is_settled_trade, position_key, split_scope
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
    with db_conn(read=True) as conn:
        rows = ledger.prepare(conn, username, [dict(r) for r in conn.execute(
            'SELECT * FROM entries WHERE username=?', (username,))])
        rules = {(r['code'], r['currency'], r['day'], r['scope']): r['mode'] for r in conn.execute(
            'SELECT * FROM split_rules WHERE username=?', (username,))}
        assets = ledger.split_events(conn, username, {
            (r.get('stockCode'), r.get('currency') or 'KRW') for r in rows if r.get('stockCode')})
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
            if day > datetime.now(KST).date().isoformat():
                continue
            for scope in ('', split_scope(row)):
                split_key = (*asset, day, scope)
                if split_key in seen:
                    continue
                seen.add(split_key)
                splits.append({'code': asset[0], 'currency': asset[1], 'stock': row.get('stockName'),
                    'day': day, 'ratio': ratio, 'scope': scope,
                    'broker': row.get('brokerAccount') if scope else '',
                    'account': (row.get('accountName') or row.get('subAccount') or '미지정') if scope else '전체 계좌',
                    'mode': rules.get(split_key, rules.get((*asset, day, ''), 'apply'))})
    return {'total': len(rows), 'issueCount': len(issues), 'issues': issues[:200],
                'truncated': len(issues) > 200, 'splits': splits}


def _split_change(conn, username, data):
    if not isinstance(data, dict) or data.get('mode') not in ('apply', 'recorded'):
        raise ValueError('분할 적용 방식을 확인해주세요.')
    if data.get('previewToken') is not None and (not isinstance(data['previewToken'], str) or len(data['previewToken']) > 4096):
        raise ValueError('분할 미리보기 정보가 올바르지 않습니다.')
    code, currency, day = (data.get(k) for k in ('code', 'currency', 'day'))
    if not all(isinstance(v, str) for v in (code, currency, day)):
        raise ValueError('분할 정보를 확인해주세요.')
    scope = data.get('scope', '')
    if not isinstance(scope, str) or len(scope) > 4096:
        raise ValueError('분할 계좌 범위를 확인해주세요.')
    owned = ledger.prepare(conn, username, [dict(r) for r in conn.execute(
        'SELECT * FROM entries WHERE username=?', (username,))])
    if not any(r.get('stockCode') == code and (r.get('currency') or 'KRW') == currency
               and (not scope or split_scope(r) == scope) for r in owned):
        raise ValueError('기록에 없는 종목 또는 계좌입니다.')
    asset = ledger.split_events(conn, username, {(code, currency)})
    if day not in asset.get((code, currency), {}):
        raise ValueError('확인된 분할 정보가 없습니다.')
    return code, currency, day, scope


def _split_signature(conn, username, data):
    owner = tuple(conn.execute('SELECT id,account_mappings FROM users WHERE username=?', (username,)).fetchone())
    rows = [tuple(r) for r in conn.execute('SELECT id,revision FROM entries WHERE username=? ORDER BY id', (username,))]
    rules = [tuple(r) for r in conn.execute('SELECT * FROM split_rules WHERE username=? ORDER BY code,currency,day,scope', (username,))]
    asset = ledger.split_events(conn, username, {(data['code'], data['currency'])})
    action = [data[k] for k in ('code', 'currency', 'day', 'mode')] + [data.get('scope', '')]
    return hashlib.sha256(json.dumps([username, owner, rows, rules,
        asset.get((data['code'], data['currency']), {}), action], sort_keys=True).encode()).hexdigest()


def _split_signer():
    return URLSafeTimedSerializer(current_app.secret_key, salt='split-preview-v1')


@serialized_user_change
def set_split_rule(username, data):
    with db_conn(write=True) as conn:
        code, currency, day, scope = _split_change(conn, username, data)
        if data.get('previewToken'):
            from app.services.journal import StalePage
            try:
                if _split_signer().loads(data['previewToken'], max_age=600) != _split_signature(conn, username, data):
                    raise BadSignature('state changed')
            except BadSignature as exc:
                raise StalePage('미리보기 이후 거래 또는 분할 정보가 변경되었습니다. 다시 확인해주세요.') from exc
        before = conn.execute('SELECT mode FROM split_rules WHERE username=? AND code=? AND currency=? AND day=? AND scope=?',
                              (username, code, currency, day, scope)).fetchone()
        conn.execute('INSERT OR REPLACE INTO split_rules(username,code,currency,day,mode,scope) VALUES (?,?,?,?,?,?)',
                     (username, code, currency, day, data['mode'], scope))
        conn.execute('''INSERT INTO entry_changes(username,action,origin,before_json,after_json)
            VALUES (?,'split_rule','web',?,?)''', (username,
            json.dumps({'code': code, 'currency': currency, 'day': day, 'scope': scope, 'mode': before['mode'] if before else 'apply'}),
            json.dumps({'code': code, 'currency': currency, 'day': day, 'scope': scope, 'mode': data['mode']})))
        realized_pnl.refresh(conn, username)
        conn.commit()
    statscache.invalidate(username)


def preview_split_rule(username, data):
    with db_conn(read=True) as conn:
        code, currency, day, scope = _split_change(conn, username, data)
        rows = [dict(r) for r in conn.execute('SELECT * FROM entries WHERE username=?', (username,))]
        rules = [dict(r) for r in conn.execute('SELECT * FROM split_rules WHERE username=?', (username,))]
        before_rows = ledger.prepare(conn, username, rows, rules=rules)
        rules = [r for r in rules if (r['code'], r['currency'], r['day'], r['scope']) != (code, currency, day, scope)]
        rules.append({'code': code, 'currency': currency, 'day': day, 'scope': scope, 'mode': data['mode']})
        after_rows = ledger.prepare(conn, username, rows, rules=rules)
        token = _split_signer().dumps(_split_signature(conn, username, data))
    before, after = positions(before_rows), positions(after_rows)
    changes = []
    for key in sorted(before.keys() | after.keys()):
        old, new = before[key], after[key]
        if abs(old['qty'] - new['qty']) > 1e-9 or abs(old['totalCost'] - new['totalCost']) > 1e-6:
            changes.append({'broker': key[0], 'account': key[1], 'currency': key[2], 'stock': key[3],
                'beforeQuantity': old['qty'], 'afterQuantity': new['qty'], 'beforeCost': old['totalCost'], 'afterCost': new['totalCost']})
    def realized(rows):
        holdings = defaultdict(empty_holding)
        total = 0.0
        for row in sorted(rows, key=entry_order):
            if row.get('type') != 'trade' or not is_settled_trade(row) or (row.get('currency') or 'KRW') != currency:
                continue
            holding = holdings[position_key(row)]
            supplied = row.get('realizedPnl') is not None and row.get('realizedPnlSource') not in ('reconstructed', 'unavailable')
            enough = holding['qty'] + 1e-9 >= float(row.get('quantity') or 0)
            probe = row if supplied else dict(row, realizedPnl=None, realizedPnlSource=None)
            profit, _, _ = apply_trade(holding, probe)
            if row.get('tradeType') == '매도' and (supplied or enough):
                total += profit
        return total
    return {'holdings': changes, 'beforeRealized': realized(before_rows), 'afterRealized': realized(after_rows),
                'currency': currency, 'scope': scope, 'previewToken': token}


def changes(username, before=None):
    with db_conn(read=True) as conn:
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
