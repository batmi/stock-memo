"""거래일별 보유 원가와 당시 종가로 계산하는 평가손익. 미래 체결은 과거에 적용하지 않는다."""

from collections import defaultdict, deque
from datetime import date, timedelta

from app.domain.trades import apply_trade, currency_of, empty_holding, normalize_stock_code, position_key
from app.services import historical_prices as history, historical_provider as provider, journal

EPS = 1e-9
MAX_DAYS = 366 * 30


def report(model, query, start=None, end=None, *, schedule=False, retry=False):
    currency = journal.resolve_currency(model, query)
    if currency not in ('KRW', 'USD'):
        raise ValueError('과거 평가손익은 현재 KRW·USD 거래를 지원합니다.')
    closed = provider.completed_day(currency)
    last = min(date.fromisoformat(end), closed) if end else closed
    rows = [r for r in model['rows'] if r.get('type') == 'trade' and r.get('stockName')
            and r['_settled'] and not r['_excluded'] and currency_of(r) == currency
            and r['_day'].date() <= last]
    rows = [dict(r, quantity=r.get('_originalQuantity', r.get('quantity')),
                 price=r.get('_originalPrice', r.get('price'))) for r in rows]
    selected = [r for r in rows if journal.matches(r, query)]
    first = date.fromisoformat(start) if start else min((r['_day'].date() for r in selected), default=last)
    if first > last:
        raise ValueError('선택 기간에 확정된 일별 종가가 없습니다. 종료된 거래일을 선택해주세요.')
    if (last - first).days > MAX_DAYS:
        raise ValueError('평가손익은 한 번에 최대 30년까지 조회할 수 있습니다. 분석 기간을 지정해주세요.')
    if first.year < 1970:
        raise ValueError('평가손익은 1970년 이후 날짜를 지원합니다.')
    assets = {(normalize_stock_code(r.get('stockCode')), currency) for r in selected if r.get('stockCode')}
    if len(assets) > 300:
        raise ValueError('한 번에 최대 300개 종목까지 분석합니다. 종목·계좌 필터를 지정해주세요.')
    if len(assets) * ((last - first).days + 1) > 500_000:
        raise ValueError('조회할 종목과 날짜가 너무 많습니다. 분석 기간이나 계좌·종목 필터를 좁혀주세요.')
    quote_start = first
    while not provider.is_session(quote_start, currency):
        quote_start -= timedelta(days=1)
    quotes, metadata = history.read(assets, quote_start.isoformat(), last.isoformat())
    # split은 당일 체결보다 먼저 반영한다. 당시 체결 단가/수량은 원본 그대로 보존한다.
    events = [(r['_day'].date(), 1, r['_dt'], r.get('tradeSequence') or r['id'], r) for r in rows]
    split_events = set()
    for row in rows:
        info = metadata.get((normalize_stock_code(row.get('stockCode')), currency))
        if not info:
            continue
        ident = position_key(row)
        for day, ratio in row.get('_splits', info['splits']).items():
            if (ident, day) not in split_events:
                split_events.add((ident, day))
                events.append((date.fromisoformat(day), 0, None, 0, (ident, ratio)))
    events.sort(key=lambda event: event[:4])
    holdings = {}
    needs = defaultdict(set)
    missing_stocks = set()
    unsupported = set()
    missing_days = 0
    insufficient = False
    split_applied = False
    had_holdings = False
    cursor = 0
    curve = []
    for day_text in history.days_between(first, last):
        day = date.fromisoformat(day_text)
        while cursor < len(events) and events[cursor][0] <= day:
            _, kind, _, _, event = events[cursor]
            cursor += 1
            if kind == 0:
                ident, ratio = event
                p = holdings.get(ident)
                if p and p['qty'] > EPS:
                    p['qty'] *= ratio
                    p['avgPrice'] /= ratio
                    p['selectedQty'] *= ratio
                    for lot in p['lots']:
                        lot[0] *= ratio
                    split_applied = True
                continue
            row = event
            ident = position_key(row)
            p = holdings.setdefault(ident, dict(empty_holding(), lots=deque(), selectedQty=0.0,
                                                code=normalize_stock_code(row.get('stockCode')),
                                                stock=model['names'].get(row['_identity'], row['stockName'])))
            qty = float(row.get('quantity') or 0)
            if row.get('tradeType') == '매수':
                included = journal.matches(row, query)
                p['lots'].append([qty, included])
                if included:
                    p['selectedQty'] += qty
            elif row.get('tradeType') == '매도':
                if qty > p['qty'] + EPS and journal.matches(row, query):
                    insufficient = True
                left = qty
                while left > EPS and p['lots']:
                    lot = p['lots'][0]
                    used = min(left, lot[0])
                    if lot[1]:
                        p['selectedQty'] -= used
                    lot[0] -= used
                    left -= used
                    if lot[0] <= EPS:
                        p['lots'].popleft()
            apply_trade(p, row)
            if p['qty'] <= EPS:
                p['selectedQty'] = 0.0
                p['lots'].clear()
        quote_day = day
        while not provider.is_session(quote_day, currency):
            quote_day -= timedelta(days=1)
        key_day = quote_day.isoformat()
        value = 0.0
        missing = False
        for p in holdings.values():
            qty = p['selectedQty']
            if qty <= EPS:
                continue
            had_holdings = True
            key = (p['code'], currency)
            if provider.supports(*key):
                needs[key].add(key_day)
            else:
                unsupported.add(p['stock'])
            entry = quotes.get(key, {}).get(key_day)
            if key not in metadata or not entry or entry['close'] is None or entry['checked_at'] == 0:
                missing = True
                missing_stocks.add(p['stock'])
            else:
                profit = qty * (entry['close'] - p['avgPrice'])
                value += profit
        if missing:
            missing_days += 1
        curve.append({'date': day_text, 'value': None if missing else value})
    if schedule:
        history.enqueue(needs, retry=retry)
    progress = history.progress(needs)
    snapshot_checked = max((quotes.get(key, {}).get(day, {}).get('checked_at', 0)
                            for key, days in needs.items() for day in days), default=0)
    # 계산 중 수집이 끝났으면 화면이 한 번 더 조회해 완성된 시세를 반영한다.
    pending = progress['pending'] > 0 or progress['latestCheckedAt'] > snapshot_checked
    notes = []
    if insufficient:
        notes.append('매수 이력이 부족한 매도가 있습니다. 거래 기록을 확인해주세요.')
    if split_applied:
        notes.append('제공처의 주식 분할·병합 비율을 보유 수량과 원가에 반영했습니다. 별도 수량 조정 기록이 있다면 중복 여부를 확인해주세요.')
    if unsupported:
        notes.append('종목코드 미입력 또는 과거 시세 미지원: ' + ', '.join(sorted(unsupported)[:5]))
    return {'curve': curve, 'currency': currency, 'period': {'from': first.isoformat(), 'to': last.isoformat()},
            'progress': progress, 'pending': pending, 'missingDays': missing_days,
            'missingStocks': sorted(missing_stocks), 'hasHoldings': had_holdings, 'notes': notes,
            'version': model['version']}
