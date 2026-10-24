"""화면 읽기 모델: 전체 이력은 서버에서 계산하고, 브라우저에는 필요한 결과만 보낸다.

본문·첨부를 제외한 거래 인덱스를 사용자 데이터 버전마다 한 번 읽는다. 페이지 크기나
차트의 표시 구간으로 계산 입력을 자르지 않는다. 과거 매수가 현재 평단·실현손익을
결정하기 때문이다. 본문은 검색, 페이지 미리보기, 단건 상세에서만 읽는다.
"""

from collections import defaultdict, deque
from datetime import datetime, timedelta
import hashlib
import html
import json
import re

from app.database.db import db_conn
from app.domain.trades import (
    apply_trade as _apply, currency_of, empty_holding as _empty_holding,
    is_settled_trade, position_key, trade_day,
)
from app.services import accounts, analysis, stats
from app.utils import statscache

INDEX_COLUMNS = (
    'id', 'type', 'stockName', 'stockCode', 'date', 'rawDate', 'brokerAccount',
    'subAccount', 'accountName', 'tradeClass', 'tradeType', 'price', 'quantity',
    'createdAt', 'updatedAt', 'isHidden', 'needsReview', 'tradeStatus', 'fee', 'tax',
    'currency', 'exchange', 'tradeDate', 'realizedPnl', 'realizedPnlSource',
)
PAGE_COLUMNS = INDEX_COLUMNS + ('title', 'tags', 'reviewReason')
EPS = 1e-9


class StalePage(ValueError):
    """페이지를 읽는 동안 기록이나 계좌 매핑이 변경됐다."""


def plain_text(value):
    return html.unescape(re.sub(r'<[^>]*>?', '', value or ''))


def _date(row):
    # 날짜 해석 규칙은 통계와 하나다(stats.parse_entry_dt). 둘이 다르면 체결 순서가 갈린다.
    return stats.parse_entry_dt(row) or datetime.min


def _stamp(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError, AttributeError, OSError):
        return 0


def snapshot(username):
    key = (username, 'journal-index')
    cached = statscache.get(key)
    if cached is not None:
        return cached
    version = statscache.data_etag(username)
    with db_conn() as conn:
        conn.execute('BEGIN')
        rows = [dict(r) for r in conn.execute(
            f"SELECT {', '.join(INDEX_COLUMNS)} FROM entries WHERE username = ?", (username,))]
        mappings = accounts.load(conn, username)
    excluded, aliases = accounts.excluded_accounts(mappings)
    known = accounts.known_account_keys(mappings)
    by_name, names, hidden, latest_hidden = {}, {}, set(), {}
    for row in rows:
        row['_dt'] = _date(row)
        row['_day'] = trade_day(row) or datetime.min
        row['_identity'] = stats.stock_identity(row)
        row['_excluded'] = accounts.is_excluded_row(row, excluded, aliases, known)
        # 취소·접수 주문은 목록에는 이력으로 남지만 수량·손익·건수 집계에서는 뺀다.
        row['_settled'] = is_settled_trade(row)
        _, info = accounts.find_account_mapping(mappings['accounts'], row.get('subAccount'))
        row['_account'] = (row.get('accountName')
                           or (info.get('alias') if isinstance(info, dict) else '')
                           or row.get('subAccount') or '')
    rows.sort(key=lambda r: (r['_dt'], r['id']))
    for row in rows:
        name = (row.get('stockName') or '').strip()
        if not name or (row.get('type') or 'trade') != 'trade':
            continue
        ident = row['_identity']
        by_name[name] = ident
        names[ident] = name
        stamp = (_stamp(row.get('updatedAt') or row.get('createdAt')), row['id'])
        if ident not in latest_hidden or stamp > latest_hidden[ident][0]:
            latest_hidden[ident] = (stamp, bool(row.get('isHidden')))
    hidden.update(ident for ident, (_, value) in latest_hidden.items() if value)
    for row in rows:
        if (row.get('type') or 'trade') != 'trade':
            name = (row.get('stockName') or '').strip()
            row['_identity'] = by_name.get(name, name)
    result = {'rows': rows, 'version': version, 'names': names, 'by_name': by_name, 'hidden': hidden}
    # 계산 도중 변경된 버전은 캐시에 들어가지 않는다.
    statscache.put(key, result, etag=version)
    return result


def metadata(model):
    rows = model['rows']
    stock_options, recent, recent_trades = {}, {}, {}
    for row in rows:
        name = (row.get('stockName') or '').strip()
        if name:
            stock_options[row['_identity']] = name
            # 자동완성에 필요한 칸만 종목별 마지막 한 건으로 보낸다.
            item = {k: row.get(k) for k in (
                'stockName', 'stockCode', 'brokerAccount', 'subAccount', 'accountName', 'tradeClass', 'currency')}
            recent[name] = item
            if row.get('type') == 'trade':
                recent_trades[name] = item
    recent.update(recent_trades)

    def values(column, source=rows):
        return sorted({r[column] for r in source if r.get(column)})

    currencies = sorted({currency_of(r) for r in rows if r.get('type') == 'trade'})
    return {
        'version': model['version'], 'total': len(rows),
        'currencies': sorted(set(currencies) | {'KRW'}),
        'defaultCurrency': 'KRW' if not currencies or 'KRW' in currencies else currencies[0],
        'reviewCount': sum(bool(r.get('needsReview')) for r in rows),
        'hasAnyTrade': any(r.get('type') == 'trade' and r.get('stockName') for r in rows),
        'stockIdentityByName': model['by_name'], 'stockNameByIdentity': model['names'],
        'hiddenStocks': sorted(model['hidden']), 'stocks': sorted(stock_options.values()),
        'stockNames': values('stockName'), 'brokers': values('brokerAccount'),
        'rawSubAccounts': values('subAccount'), 'subAccounts': values('_account'),
        'chartSubAccounts': values('_account', [r for r in rows if not r['_excluded']]),
        'tradeClasses': values('tradeClass'), 'recentEntries': recent,
    }


def filters(args):
    result = {}
    for key in ('stock', 'tradeClass', 'subAccount', 'date', 'type', 'currency'):
        value = args.get(key, '')
        if len(value) > 200:
            raise ValueError('필터 값이 너무 깁니다.')
        result[key] = value if value != 'all' else ''
    for key in ('brokers', 'keywords'):
        try:
            values = json.loads(args.get(key, '[]'))
        except (ValueError, TypeError) as exc:
            raise ValueError('필터 목록 형식이 올바르지 않습니다.') from exc
        if (not isinstance(values, list) or len(values) > 20
                or any(not isinstance(v, str) or len(v) > 200 for v in values)):
            raise ValueError('필터 목록 형식이 올바르지 않습니다.')
        result[key] = values
    if result['currency'] and not re.fullmatch('[A-Z]{3}', result['currency']):
        raise ValueError('통화는 KRW, USD 같은 3자리 코드여야 합니다.')
    result['review'] = args.get('review') == '1'
    result['showClosed'] = args.get('showClosed') == '1'
    return result


def matches(row, query):
    return (row.get('type') == 'memo' or not query.get('currency') or currency_of(row) == query['currency']) and (
        not query['stock'] or row['_identity'] == query['stock']) and (
        not query['tradeClass'] or row.get('tradeClass') == query['tradeClass']) and (
        not query['brokers'] or row.get('brokerAccount') in query['brokers']) and (
        not query['subAccount'] or row['_account'] == query['subAccount'])


def _holding_key(row):
    return row['_identity'] + ('::제외' if row['_excluded'] else '')


def resolve_currency(model, query):
    """명시한 통화를 우선하고, 생략하면 조회 조건에 해당하는 거래에서 고른다."""
    if query.get('currency'):
        return query['currency']
    currencies = {currency_of(row) for row in model['rows']
                  if row.get('type') == 'trade' and row['_settled'] and matches(row, query)}
    return 'KRW' if not currencies or 'KRW' in currencies else min(currencies)


def _consume_lots(lots, qty):
    """필터 밖 매도도 같은 계좌의 잔여 매수 수량을 FIFO로 소진한다."""
    while qty > EPS and lots:
        lot = lots[0]
        used = min(qty, lot[0])
        qty -= used
        lot[0] -= used
        if lot[0] <= EPS:
            lots.popleft()


def portfolio(model, query, month):
    query = dict(query, currency=resolve_currency(model, query))
    holdings = {}
    positions = defaultdict(_empty_holding)
    lots = defaultdict(deque)
    realized = 0.0
    buys = sells = 0
    for row in model['rows']:
        if (row.get('type') != 'trade' or not row.get('stockName') or not row['_settled']
                or currency_of(row) != query['currency']):
            continue
        key = _holding_key(row)
        ident = position_key(row), key
        selected = matches(row, query)
        kind = row.get('tradeType')
        qty = float(row.get('quantity') or 0)
        profit, cost, dividend = _apply(positions[ident], row)
        if kind == '매수':
            lots[ident].append([qty, selected])
        elif kind == '매도':
            _consume_lots(lots[ident], qty)
            if positions[ident]['qty'] <= EPS:
                lots[ident].clear()
        if not selected:
            continue
        p = holdings.setdefault(key, dict(
            **_empty_holding(), stock=model['names'].get(row['_identity'], row['stockName']),
            identity=row['_identity'], isExcluded=row['_excluded'],
            excludeLabel='제외' if row['_excluded'] else '', realizedProfit=0.0, realizedCost=0.0,
            accountName='', tradeClass='', traded=False, stockCode='', currency=query['currency']))
        p['tradeClass'] = row.get('tradeClass') or p['tradeClass']
        p['stockCode'] = row.get('stockCode') or p['stockCode']
        if kind in ('매수', '매도', '배당'):
            p['traded'] = True
            p['realizedProfit'] += profit + dividend
            p['realizedCost'] += cost
            if not row['_excluded']:
                realized += profit + dividend
                if row['_day'].strftime('%Y-%m') == month:
                    buys += row.get('tradeType') == '매수'
                    sells += row.get('tradeType') == '매도'
    # 분류별 잔여 수량은 매수 로트에 귀속한다. 평가 원가는 분류와 무관하게
    # 전체 체결로 계산한 계좌별 이동평균단가를 적용한다.
    for ident, remaining in lots.items():
        qty = sum(qty for qty, selected in remaining if selected)
        if qty <= EPS:
            continue
        p = holdings[ident[1]]
        p['qty'] += qty
        p['totalCost'] += qty * positions[ident]['avgPrice']
        p['avgPrice'] = p['totalCost'] / p['qty']
    return {'holdings': holdings, 'currency': query['currency'], 'totalRealizedProfit': realized,
                'monthlyBuyCount': buys, 'monthlySellCount': sells}


def _history_rows(model, query):
    rows = model['rows']
    quantities = defaultdict(float)
    positions = defaultdict(_empty_holding)
    related = {k: set() for k in ('tradeClass', 'brokers', 'subAccount')}
    related_queries = {}
    for key in related:
        if query[key]:
            probe = dict(query, stock='', tradeClass='', brokers=[], subAccount='')
            probe[key] = query[key]
            related_queries[key] = probe
    for row in rows:
        if (row.get('type') or 'trade') != 'trade' or not row.get('stockName'):
            continue
        if row.get('tradeType') in ('매수', '매도') and row['_settled']:
            _apply(positions[position_key(row), _holding_key(row)], row)
        for key, probe in related_queries.items():
            if matches(row, probe):
                related[key].add(row['_identity'])
    # 다른 계좌의 초과 매도로 정상 계좌의 보유량을 상쇄하지 않는다.
    # 포트폴리오와 같은 계좌별 청산 판정을 목록에도 적용한다.
    for (_, holding_key), holding in positions.items():
        quantities[holding_key] += holding['qty']
    result = []
    for row in reversed(rows):
        kind = row.get('type') or 'trade'
        if query['review'] and not row.get('needsReview'):
            continue
        if query['date'] and row['_day'].strftime('%Y-%m-%d') != query['date']:
            continue
        if query['type'] and kind != query['type']:
            continue
        if query['stock'] and row['_identity'] != query['stock']:
            continue
        if kind == 'memo':
            if any(query[k] and row['_identity'] not in related[k] for k in related):
                continue
        elif not matches(row, query):
            continue
        if not query['showClosed'] and not query['review'] and row.get('stockName'):
            if row['_identity'] in model['hidden']:
                continue
            keys = [_holding_key(row)] if kind == 'trade' else [
                row['_identity'], row['_identity'] + '::제외']
            known = [quantities[k] for k in keys if k in quantities]
            if known and all(q <= EPS for q in known):
                continue
        result.append(row)
    return result


def history(username, model, query, limit, cursor, signer):
    fingerprint = hashlib.sha256(json.dumps(query, sort_keys=True).encode()).hexdigest()
    after = None
    if cursor:
        from itsdangerous import BadSignature
        try:
            payload = signer.loads(cursor)
        except BadSignature as exc:
            raise ValueError('페이지 정보가 올바르지 않습니다.') from exc
        if not isinstance(payload, dict) or payload.get('query') != fingerprint:
            raise ValueError('페이지의 검색 조건이 다릅니다.')
        if payload.get('version') != model['version']:
            raise StalePage('기록이 변경되었습니다. 목록을 새로 불러옵니다.')
        after = payload.get('after')
    rows = _history_rows(model, query)
    with db_conn() as conn:
        if query['keywords']:
            # 제목·태그·본문 검색은 서버에서 수행한다. SQL 매개변수로 전달하므로
            # %, _, 따옴표도 검색어 그대로이며, HTML 태그는 검색 대상에서 뺀다.
            conn.create_function('journal_text', 1, lambda v: plain_text(v).casefold())
            fields = ('stockName', 'brokerAccount', 'subAccount', 'tags', 'title', 'thoughts')
            clauses, values = [], [username]
            for word in query['keywords']:
                clauses.append('(' + ' OR '.join(f'instr(journal_text({c}), ?) > 0' for c in fields) + ')')
                values.extend([word.casefold()] * len(fields))
            ids = {r[0] for r in conn.execute(
                'SELECT id FROM entries WHERE username = ? AND ' + ' AND '.join(clauses), values)}
            rows = [r for r in rows if r['id'] in ids]
        total = len(rows)
        if after is not None:
            pos = next((i for i, row in enumerate(rows) if row['id'] == after), None)
            if pos is None:
                raise ValueError('페이지의 마지막 기록을 찾을 수 없습니다.')
            rows = rows[pos + 1:]
        page = rows[:limit]
        items = []
        if page:
            conn.create_function('journal_preview', 1, lambda v: plain_text(v)[:240])
            marks = ','.join('?' for _ in page)
            records = {r['id']: dict(r) for r in conn.execute(
                f"SELECT {', '.join(PAGE_COLUMNS)}, journal_preview(thoughts) AS preview, "
                "(COALESCE(thoughts, '') != '' OR COALESCE(attachedImage, '') != '' "
                "OR COALESCE(attachedFile, '') != '') AS hasDetail "
                f"FROM entries WHERE username = ? AND id IN ({marks})",
                [username, *[r['id'] for r in page]])}
            items = [records[r['id']] for r in page if r['id'] in records]
    if model['version'] != statscache.data_etag(username):
        raise StalePage('기록이 변경되었습니다. 목록을 새로 불러옵니다.')
    next_cursor = signer.dumps({'version': model['version'], 'query': fingerprint, 'after': page[-1]['id']}) \
        if len(rows) > limit else None
    return {'items': items, 'total': total, 'nextCursor': next_cursor, 'version': model['version']}


def calendar(model, month, currency='KRW'):
    daily = {}
    holdings = defaultdict(_empty_holding)
    counts = {'매수': 'buyCount', '매도': 'sellCount', '관망': 'watchCount',
              '주시': 'watchCount', '배당': 'dividendCount'}
    for row in model['rows']:
        if row.get('type') == 'trade' and (not row['_settled'] or currency_of(row) != currency):
            continue
        profit = dividend = 0.0
        if row.get('type') == 'trade' and row.get('stockName') and not row['_excluded']:
            profit, _, dividend = _apply(holdings[position_key(row)], row)
        if row['_day'].strftime('%Y-%m') != month:
            continue
        day = daily.setdefault(row['_day'].strftime('%Y-%m-%d'), {'profit': 0.0, 'details': {}})
        day['profit'] += profit + dividend
        stock = model['names'].get(row['_identity'], row.get('stockName') or '') \
            if row.get('type') == 'trade' else row.get('stockName') or ''
        detail = day['details'].setdefault(stock, {
            'buyCount': 0, 'sellCount': 0, 'watchCount': 0, 'memoCount': 0, 'dividendCount': 0})
        field = 'memoCount' if row.get('type') == 'memo' else counts.get(row.get('tradeType'))
        if field:
            detail[field] += 1
    return daily


def chart(model, query, labels, weekly):
    query = dict(query, currency=resolve_currency(model, query))
    fields = ('realized', 'evaluated', 'buy_volume', 'sell_volume', 'dividend', 'cumulative', 'cumulative_dividend')
    breakdowns = ('realized', 'evaluated', 'buy_volume', 'sell_volume', 'dividend', 'cumulative', 'cumulative_div')
    monthly = {label: {**dict.fromkeys(fields, 0.0),
                       **{k + '_breakdown': {} for k in breakdowns}} for label in labels}
    holdings = defaultdict(_empty_holding)
    lots = defaultdict(deque)
    events = []

    def add(period, field, stock, amount):
        period[field] = period.get(field, 0.0) + amount
        breakdown = period[field + '_breakdown']
        breakdown[stock] = breakdown.get(stock, 0.0) + amount

    for row in model['rows']:
        if (row.get('type') != 'trade' or not row.get('stockName') or not row['_settled']
                or row['_excluded'] or currency_of(row) != query['currency']):
            continue
        selected = matches(row, query)
        dt = row['_day']
        key = (dt - timedelta(days=dt.weekday())).strftime('%Y-%m-%d') if weekly else dt.strftime('%Y-%m')
        ident = position_key(row)
        stock = model['names'].get(row['_identity'], row['stockName'])
        qty, price = float(row.get('quantity') or 0), float(row.get('price') or 0)
        kind = row.get('tradeType')
        profit, _, dividend = _apply(holdings[ident], row)
        holdings[ident]['stockCode'] = row.get('stockCode') or holdings[ident].get('stockCode', '')
        if kind == '매수':
            lots[ident].append([qty, key, price, selected])
        elif kind == '매도':
            _consume_lots(lots[ident], qty)
            if holdings[ident]['qty'] <= EPS:
                lots[ident].clear()
        if not selected:
            continue
        if kind in ('매도', '배당'):
            events.append((key, stock, profit, dividend))
        if key in monthly:
            period = monthly[key]
            if kind == '매수':
                add(period, 'buy_volume', stock, price * qty)
            elif kind == '매도':
                add(period, 'sell_volume', stock, price * qty)
                add(period, 'realized', stock, profit)
            elif kind == '배당':
                add(period, 'dividend', stock, dividend)
    events.sort(key=lambda event: event[0])
    event_index = 0
    cumulative = cumulative_dividend = 0.0
    cumulative_stocks, cumulative_dividends = defaultdict(float), defaultdict(float)
    for label, period in monthly.items():
        while event_index < len(events) and events[event_index][0] <= label:
            _, stock, profit, dividend = events[event_index]
            cumulative += profit + dividend
            cumulative_dividend += dividend
            cumulative_stocks[stock] += profit + dividend
            cumulative_dividends[stock] += dividend
            event_index += 1
        period.update(cumulative=cumulative, cumulative_dividend=cumulative_dividend,
                      cumulative_breakdown=dict(cumulative_stocks),
                      cumulative_div_breakdown=dict(cumulative_dividends))
    # 현재가만 브라우저에서 적용한다. 개별 매수 대신 기간·종목별 잔여 수량과 원가를
    # 보낸다. 구간 밖 매도도 FIFO 에 적용했으므로 과거 잔여 물량이 되살아나지 않는다.
    evaluation = {}
    for ident, remaining in lots.items():
        for qty, key, price, selected in remaining:
            if not selected or key not in monthly:
                continue
            item = evaluation.setdefault((ident[-1], key), {
                'period': key, 'stock': model['names'].get(ident[-1], ident[-1]),
                'stockCode': holdings[ident].get('stockCode'), 'quantity': 0.0, 'cost': 0.0})
            item['quantity'] += qty
            item['cost'] += qty * price
    return {'monthlyData': monthly, 'evaluation': list(evaluation.values()), 'currency': query['currency']}


def trade_stats(model, query, granularity='monthly', start=None, end=None):
    result = analysis.report(
        [r for r in model['rows'] if not r['_excluded']], resolve_currency(model, query),
        lambda row: matches(row, query), granularity, start, end,
        all_currencies=not query.get('currency'))
    return dict(result, version=model['version'])


def stats_trades(model, query, granularity, start, end, options, version):
    if version and version != model['version']:
        raise StalePage('기록이 변경되었습니다. 분석을 새로 조회해주세요.')
    currency = resolve_currency(model, query)
    _, facts = analysis.calculate([r for r in model['rows'] if not r['_excluded']], currency,
                                  lambda row: matches(row, query), granularity, start, end)
    return dict(analysis.detail_page(facts, options), currency=currency, version=model['version'])
