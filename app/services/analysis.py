"""성과 분석의 기간 비교·근거 거래·분포. 원가 계산은 stats 한 곳에서 수행한다."""

from collections import defaultdict
from datetime import date, timedelta
from statistics import median
import re

from app.domain.trades import currency_of
from app.services import stats


def period_bounds(args):
    start, end = args.get('from', ''), args.get('to', '')
    if not start and not end:
        return None, None
    if not start or not end:
        raise ValueError('분석 시작일과 종료일을 함께 지정해주세요.')
    try:
        if not all(re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) for value in (start, end)):
            raise ValueError
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        if first > last:
            raise ValueError
        # 직전 동일 일수와 종료일 다음 날도 유효한 날짜여야 한다.
        first - (last - first + timedelta(days=1))
        last + timedelta(days=1)
    except (ValueError, OverflowError) as exc:
        raise ValueError('분석 기간을 올바른 날짜와 순서로 지정해주세요.') from exc
    return start, end


def calculate(rows, currency, include, granularity, start, end):
    facts = []
    result = stats.compute_trade_stats(
        [row for row in rows if currency_of(row) == currency], granularity,
        start, end, include=include, trade_facts=facts)
    return result, facts


def quality(facts):
    complete = sum(f['fee'] is not None and f['tax'] is not None for f in facts)
    missing = sum(f['fee'] is None and f['tax'] is None for f in facts)
    return {
        'tradeCount': len(facts), 'reviewCount': sum(f['needsReview'] for f in facts),
        'missingBuyCount': sum(f['missingBuyQuantity'] > 1e-9 for f in facts),
        'zeroBasisCount': sum(f['tradeType'] == '매도' and f['basis'] <= 1e-9 for f in facts),
        'missingRealizedCount': sum(f.get('realizedSource') == 'unavailable' for f in facts),
        'reconstructedRealizedCount': sum(f.get('realizedSource') == 'reconstructed' for f in facts),
        'completeCostCount': complete, 'partialCostCount': len(facts) - complete - missing,
        'missingCostCount': missing,
        'costCoverage': complete / len(facts) * 100 if facts else None,
    }


def distribution(facts):
    values = sorted(f['realized'] for f in facts if f['tradeType'] == '매도'
                    and f.get('realizedSource') != 'unavailable')
    wins = [v for v in values if v > 0]
    bins = []
    if values:
        low, high = values[0], values[-1]
        count = min(7, len(values)) if high > low else 1
        width = (high - low) / count
        bins = [{'from': low + width * i, 'to': low + width * (i + 1), 'count': 0}
                for i in range(count)]
        for value in values:
            index = min(count - 1, int((value - low) / width)) if width else 0
            bins[index]['count'] += 1
    return {
        'sellCount': len(values), 'median': median(values) if values else None, 'bins': bins,
        'top3WinShare': sum(wins[-3:]) / sum(wins) * 100 if wins else None,
        'withoutBestWin': sum(values) - (wins[-1] if wins else 0) if values else None,
    }


def recovery(facts, start, end):
    """배당 포함·비용 차감 전 실현손익의 낙폭. 미래 거래로 과거 회복을 판정하지 않는다."""
    dated = [f for f in facts if f['date']]
    baseline = start or min((f['date'] for f in dated), default=None)
    as_of = end or max((f['date'] for f in dated), default=None)
    peak_date = baseline
    cumulative = peak = largest = 0.0
    trough_date = recovery_date = None
    episode_open = False
    largest_in_episode = False
    for fact in sorted(dated, key=lambda f: f['date']):
        if fact['tradeType'] not in ('매도', '배당'):
            continue
        cumulative += fact['realized'] + fact['dividend']
        if cumulative >= peak:
            if episode_open and largest_in_episode and recovery_date is None:
                recovery_date = fact['date']
            episode_open = largest_in_episode = False
            peak, peak_date = cumulative, fact['date']
        else:
            episode_open = True
            if peak - cumulative > largest:
                largest = peak - cumulative
                trough_date = fact['date']
                recovery_date = None
                largest_in_episode = True
    return {
        'asOf': as_of, 'maxRecoveryDate': recovery_date,
        'maxRecoveryDays': (date.fromisoformat(recovery_date) - date.fromisoformat(trough_date)).days
            if recovery_date and trough_date else None,
        'currentDrawdown': peak - cumulative,
        'currentPeakDate': peak_date if episode_open else None,
        'currentUnderwaterDays': (date.fromisoformat(as_of) - date.fromisoformat(peak_date)).days
            if episode_open and peak_date and as_of else 0,
    }


def groups(facts, field):
    grouped = defaultdict(list)
    for fact in facts:
        grouped[(fact['currency'], fact[field])].append(fact)
    result = []
    for (currency, key), items in grouped.items():
        sells = [f for f in items if f['tradeType'] == '매도']
        wins = sum(f['realized'] > 0 for f in sells)
        losses = sum(f['realized'] < 0 for f in sells)
        known_basis = [f for f in sells if f['basis'] > 1e-9 and f.get('realizedSource') != 'unavailable']
        basis = sum(f['basis'] for f in known_basis)
        held = [f for f in sells if f['holdingDays'] is not None]
        held_qty = sum(f['quantity'] - f['missingBuyQuantity'] for f in held)
        result.append({
            'key': key, 'label': items[-1]['account'] if field == 'accountKey' else key or '미분류',
            'detail': ' · '.join(filter(None, (items[-1].get('broker'), items[-1].get('accountNumber'))))
                if field == 'accountKey' else '',
            'currency': currency, 'tradeCount': len(items), 'sellCount': len(sells),
            'totalPnl': sum(f['realized'] + f['dividend'] for f in items),
            'winRate': wins / (wins + losses) * 100 if wins + losses else None,
            'returnRate': sum(f['realized'] for f in known_basis) / basis * 100 if basis else None,
            'avgHoldingDays': sum(f['holdingDays'] * (f['quantity'] - f['missingBuyQuantity']) for f in held)
                / held_qty if held_qty > 1e-9 else None,
        })
    # 다른 통화 금액의 크기로 순위를 매기지 않는다.
    return sorted(result, key=lambda g: (g['currency'], -g['totalPnl'], g['label'], g['key']))


def report(rows, currency, include, granularity, start, end, *, all_currencies=False):
    result, facts = calculate(rows, currency, include, granularity, start, end)
    result.update(currency=currency, period={'from': start, 'to': end}, comparison=None,
                  quality=quality(facts), distribution=distribution(facts), recovery=recovery(facts, start, end))
    if start and end:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        previous_end = first - timedelta(days=1)
        previous_start = first - (last - first + timedelta(days=1))
        previous, previous_facts = calculate(rows, currency, include, granularity,
                                             previous_start.isoformat(), previous_end.isoformat())
        keys = ('totalPnl', 'totalRealized', 'sellCount', 'winCount', 'lossCount', 'winRate',
                'expectancy', 'realizedReturnRate')
        result['comparison'] = {
            'from': previous_start.isoformat(), 'to': previous_end.isoformat(),
            'currency': currency, 'tradeCount': len(previous_facts),
            'metrics': {key: previous[key] for key in keys},
        }
    group_facts = list(facts)
    if all_currencies:
        for other in sorted({currency_of(row) for row in rows} - {currency}):
            _, other_facts = calculate(rows, other, include, granularity, start, end)
            group_facts.extend(other_facts)
    result['groups'] = {'classes': groups(group_facts, 'tradeClass'), 'accounts': groups(group_facts, 'accountKey')}
    return result


DETAIL_METRICS = {'all', 'pnl', 'buy', 'sell', 'dividend', 'wins', 'losses', 'best', 'worst',
                  'review', 'missing-buy', 'missing-cost'}


def detail_options(args):
    metric = args.get('metric', 'all')
    group_by, group_key = args.get('groupBy', ''), args.get('groupKey', '')
    bucket, identity, period = (args.get(key, '') for key in ('bucket', 'identity', 'periodKey'))
    if metric not in DETAIL_METRICS or group_by not in ('', 'tradeClass', 'accountKey'):
        raise ValueError('거래 상세 조건이 올바르지 않습니다.')
    # 계좌 키는 각각 최대 200자인 증권사·계좌를 JSON으로 묶으므로 이스케이프 길이도 허용한다.
    group_limit = 4096 if group_by == 'accountKey' else 200
    if len(group_key) > group_limit or any(len(value) > 200 for value in (bucket, identity, period)):
        raise ValueError('거래 상세 조건이 너무 깁니다.')
    if bucket and bucket not in {label for _, label in stats.HOLDING_BUCKETS}:
        raise ValueError('보유기간 구간이 올바르지 않습니다.')
    try:
        offset, limit = int(args.get('offset', '0')), int(args.get('limit', '30'))
    except ValueError as exc:
        raise ValueError('페이지 크기와 위치는 정수여야 합니다.') from exc
    if not 0 <= offset <= 10000000 or not 1 <= limit <= 100:
        raise ValueError('페이지 크기는 1~100이며 위치는 0 이상이어야 합니다.')
    return {'metric': metric, 'group_by': group_by, 'group_key': group_key, 'bucket': bucket,
            'identity': identity, 'period': period, 'offset': offset, 'limit': limit}


def detail_page(facts, options):
    metric = options['metric']
    kinds = {'pnl': ('매도', '배당'), 'buy': ('매수',), 'sell': ('매도',), 'dividend': ('배당',)}
    selected = []
    for fact in facts:
        if options['group_by'] and fact[options['group_by']] != options['group_key']:
            continue
        if any(options[key] and fact[field] != options[key] for key, field in (
                ('bucket', 'holdingBucket'), ('identity', 'identity'), ('period', 'period'))):
            continue
        if metric in kinds and fact['tradeType'] not in kinds[metric]:
            continue
        if metric in ('wins', 'best') and fact['realized'] <= 0:
            continue
        if metric in ('losses', 'worst') and fact['realized'] >= 0:
            continue
        if metric == 'review' and not fact['needsReview']:
            continue
        if metric == 'missing-buy' and fact['missingBuyQuantity'] <= 1e-9:
            continue
        if metric == 'missing-cost' and fact['fee'] is not None and fact['tax'] is not None:
            continue
        selected.append(fact)
    if metric in ('best', 'worst') and selected:
        extreme = (max if metric == 'best' else min)(f['realized'] for f in selected)
        selected = [f for f in selected if f['realized'] == extreme]
    selected.reverse()
    offset, limit = options['offset'], options['limit']
    return {'items': selected[offset:offset + limit], 'total': len(selected),
            'nextOffset': offset + limit if offset + limit < len(selected) else None}
