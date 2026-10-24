"""실현손익 저장: 외부 금액은 보존하고, 없는 금액만 당시 체결 이력으로 재구성한다.

조회에는 저장된 금액을 사용한다. 재구성은 거래 변경 트랜잭션과 초기 이관에서만 수행한다.
계좌·통화·종목을 분리하며 이후 매수는 이전 매도의 손익에 영향을 주지 않는다.
"""

from collections import defaultdict
from math import isfinite

from app.domain.trades import (
    apply_trade, empty_holding, entry_order, is_settled_trade, position_key, EPS,
)


def refresh(conn, username):
    """호출자가 커밋한다. 제공된 금액을 보호하고 재구성한 금액만 이력 정정에 맞춰 갱신한다."""
    from app.services import accounts
    identity_changes = accounts.refresh_identities(conn, username)
    columns = ('id,tradeSequence,type,stockName,stockCode,brokerAccount,brokerIdentity,subAccount,accountName,currency,tradeType,quantity,price,'
               'rawDate,date,tradeDate,tradeStatus,realizedPnl,realizedPnlSource,realizedPnlBasis')
    rows = [dict(row) for row in conn.execute(
        f'SELECT {columns} FROM entries WHERE username=? AND type=\'trade\'', (username,))]
    from app.services import ledger
    rows = sorted(ledger.prepare(conn, username, rows), key=entry_order)
    holdings = defaultdict(empty_holding)
    changes = []
    for row in rows:
        old = (row['realizedPnl'], row['realizedPnlSource'], row['realizedPnlBasis'])
        provided = row['realizedPnl'] is not None and row['realizedPnlSource'] not in ('reconstructed', 'unavailable')
        if provided:
            new = (row['realizedPnl'], 'recorded', row['realizedPnlBasis'] or 'unknown')
        else:
            new = (None, None, None)
        if is_settled_trade(row):
            holding = holdings[position_key(row)]
            qty = float(row.get('quantity') or 0)
            enough = holding['qty'] + EPS >= qty and qty > 0
            # 재구성에는 저장 손익을 사용하지 않고 매도 당시까지의 체결가격만 사용한다.
            profit, _, _ = apply_trade(holding, dict(row, realizedPnl=None, realizedPnlSource=None))
            if row['tradeType'] == '매도' and not provided:
                new = (profit, 'reconstructed', 'gross') if enough and isfinite(profit) else (None, 'unavailable', None)
        if new != old:
            changes.append((*new, row['id'], username))
    if changes:
        conn.executemany('UPDATE entries SET realizedPnl=?,realizedPnlSource=?,realizedPnlBasis=? WHERE id=? AND username=?', changes)
    return len(changes) + identity_changes


def backfill():
    """기존 DB의 빈 손익을 채운다. 새 코드의 최초 기동 및 명시적 데이터 이관에서 호출한다."""
    from app.database.db import db_conn
    from app.utils import statscache
    changed = []
    with db_conn(write=True) as conn:
        migration = 'canonical_broker_pnl_basis_v1'
        repair = conn.execute('SELECT 1 FROM data_migrations WHERE name=?', (migration,)).fetchone() is None
        # 명확한 증권사 코드/이름을 연결하고 기존 손익의 비용 기준을 1회 분류한다.
        # recorded 금액은 refresh에서 보호한다. 완료 표시는 손익 변경과 함께 커밋한다.
        usernames = [row[0] for row in conn.execute('''SELECT DISTINCT username FROM entries
            WHERE type='trade' AND (? OR (tradeType='매도' AND realizedPnlSource IS NULL))
            AND COALESCE(tradeStatus,'FILLED') NOT IN ('CANCELED','SUBMITTED')''', (repair,))]
        for username in usernames:
            if refresh(conn, username):
                changed.append(username)
        if repair:
            conn.execute('DELETE FROM historical_watchers WHERE username NOT IN (SELECT username FROM users)')
            conn.execute('INSERT INTO data_migrations(name) VALUES (?)', (migration,))
        conn.commit()
    for username in changed:
        statscache.invalidate(username)
    return len(changed)
