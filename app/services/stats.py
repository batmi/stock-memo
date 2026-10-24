"""매매 성과 분석(통계) 계산 로직.

DB에 의존하지 않는 순수 함수로 구성되어 단위 테스트가 용이합니다.
조회 API(/api/journal/stats)가 넘긴 기록 리스트를 받아 지표를 계산합니다.

종목·계좌·통화 식별과 이동평균 원가 계산은 app.domain.trades의 공통 규칙을 씁니다.
"""
from collections import defaultdict, deque
from datetime import datetime, timedelta

from app.domain.trades import (
    account_group_key, apply_trade, currency_of, empty_holding, entry_order, is_settled_trade,
    normalize_stock_code, parse_entry_dt as parse_entry_dt, position_key, resolve_trade_identities,
    stock_identity, trade_day,
)

# 보유기간 구간 (상한 일수, 표시 이름). 상한은 '이하'로 포함하고, 첫 구간만 '미만'이다.
HOLDING_BUCKETS = (
    (1, '1일 미만'),
    (7, '1주 이내'),
    (30, '1개월 이내'),
    (90, '3개월 이내'),
    (None, '3개월 초과'),
)


def _holding_bucket(days):
    """매도 1건의 (수량 가중) 보유일수가 속하는 HOLDING_BUCKETS 의 인덱스."""
    if days < HOLDING_BUCKETS[0][0]:
        return 0
    for i, (limit, _label) in enumerate(HOLDING_BUCKETS[1:], start=1):
        if limit is None or days <= limit:
            return i
    return len(HOLDING_BUCKETS) - 1


def _rate(profit, basis):
    """원금(basis) 대비 수익률(%). 원금이 없으면 정의할 수 없어 None."""
    return (profit / basis * 100.0) if basis > 1e-9 else None


def get_monday(dt):
    """주어진 날짜의 월요일(YYYY-MM-DD)을 반환합니다."""
    monday = dt - timedelta(days=dt.weekday())
    return monday.strftime('%Y-%m-%d')

def _parse_period_bound(value):
    """'YYYY-MM-DD' 형태의 구간 경계를 datetime으로 파싱합니다. 실패 시 None."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], '%Y-%m-%d')
    except (ValueError, TypeError):
        return None

def compute_trade_stats(rows, granularity='monthly', period_start=None, period_end=None, *, include=None,
                        trade_facts=None):
    """매매 기록 리스트로부터 성과 분석 지표를 계산합니다.

    실현손익은 거래별 저장 금액을 사용하며, 보유기간은 FIFO 로트 매칭으로 추정합니다.
    저장 금액이 없는 구버전 입력만 당시 이동평균 원가로 계산합니다.

    period_start / period_end ('YYYY-MM-DD', 종료일 포함)를 주면 해당 구간의 체결만
    집계합니다. 단, 평단(avgPrice)은 구간 이전 매수까지 모두 반영해야 실현손익이
    맞으므로 보유 상태는 전체 기록으로 갱신하고 '지표 누적'만 구간 안으로 제한합니다.
    include(record)도 같은 방식으로 지표만 제한합니다. 분류가 다른 매수·매도를
    계산에서 먼저 제거하면 원가와 보유기간이 달라지므로 전체 이력을 전달해야 합니다.
    trade_facts 리스트를 전달하면 같은 계산에서 거래별 근거도 수집합니다.
    본문·첨부는 포함하지 않으며, 화면 상세와 추가 분석이 원가를 재계산하지 않습니다.
    """
    # 취소·접수 주문은 이력으로만 남고 성과에 넣지 않는다(entry_logic.is_settled_trade).
    trades = [r for r in resolve_trade_identities(rows) if r.get('type') == 'trade' and (r.get('stockName') or '').strip()
              and is_settled_trade(r)]
    # ⭐️ 같은 시각의 체결은 **id 가 큰 쪽(나중에 들어온 쪽)이 뒤**로 간다.
    #    예전에는 정렬 키가 시각 하나뿐이었고, 파이썬 정렬이 안정적이라 동점의 순서가
    #    'SQL 이 돌려준 순서'(api.py 의 조회에는 ORDER BY 가 없다)로 결정됐다. 이 순서가
    #    ① 표시 이름(display_names — 마지막에 쓴 이름이 이긴다)과 ② 선입선출 청산 순서를
    #    좌우하는데, 화면(14-history.js)은 동점을 id 로 가른다. 규칙을 화면과 맞춘다.
    trades.sort(key=entry_order)

    currencies = {currency_of(row) for row in trades}
    if len(currencies) > 1:
        raise ValueError('통화별로 나누어 집계해야 합니다.')

    win_start = _parse_period_bound(period_start)
    win_end = _parse_period_bound(period_end)
    if win_end is not None:
        win_end += timedelta(days=1)  # 종료일 당일까지 포함

    def in_window(dt):
        """해당 체결을 지표에 반영할지 여부. 구간 지정이 없으면 항상 True."""
        if win_start is None and win_end is None:
            return True
        if dt is None:
            return False  # 날짜 미상 기록은 구간을 특정할 수 없어 제외
        if win_start is not None and dt < win_start:
            return False
        if win_end is not None and dt >= win_end:
            return False
        return True

    portfolio = {}  # key -> {qty, totalCost, avgPrice, lots(deque of [dt, qty])}
    # 표시용 종목명. 같은 코드에 표기가 여럿이면 가장 최근 기록의 이름을 쓴다.
    display_names = {}
    display_codes = {}
    monthly = defaultdict(lambda: {'realized': 0.0, 'dividend': 0.0, 'buyAmount': 0.0, 'sellAmount': 0.0,
                                   'sellCount': 0, 'winCount': 0, 'lossCount': 0})
    # basis = 매도분의 평단 원금, basisProfit = 원금이 있는 매도의 손익(수익률 분자).
    # ⭐️ 매수 기록 없이 매도만 있는 종목은 평단이 0이라 매도금 전체가 이익으로 잡힌다.
    #    그런 매도까지 수익률에 넣으면 분모 없이 분자만 커지므로 수익률에서는 뺀다.
    per_stock = defaultdict(lambda: {'realized': 0.0, 'dividend': 0.0, 'sellCount': 0,
                                     'winCount': 0, 'lossCount': 0, 'basis': 0.0, 'basisProfit': 0.0,
                                     'holdDays': 0.0, 'holdQty': 0.0})
    buckets = [{'sellCount': 0, 'winCount': 0, 'lossCount': 0, 'realized': 0.0,
                'basis': 0.0, 'basisProfit': 0.0} for _ in HOLDING_BUCKETS]
    realized_events = []  # (dt, amount) — 누적 실현손익 곡선 / MDD 계산용

    total_realized = 0.0      # 매도 실현손익 합계
    total_dividend = 0.0      # 배당 수익 합계
    buy_count = sell_count = dividend_count = 0
    known_pnl_sell_count = 0
    win_count = loss_count = 0
    gross_profit = 0.0        # 이익 매도건 손익 합
    gross_loss = 0.0          # 손실 매도건 손익 절댓값 합
    holding_days_weighted = 0.0
    holding_qty_total = 0.0
    
    max_single_win = 0.0
    max_single_loss = 0.0
    total_buy_amount = 0.0
    total_sell_amount = 0.0

    # 수익률: 원금이 있는 매도만 (per_stock 의 basis 주석 참고)
    sold_basis = basis_profit = 0.0
    return_rate_sum = 0.0
    return_rate_count = 0
    # 이익·손실 거래별 보유기간 (수량 가중)
    win_hold_days = win_hold_qty = loss_hold_days = loss_hold_qty = 0.0
    # 연승·연패. 손익 0 매도는 승률과 같이 '승도 패도 아닌 것'으로 보고 흐름을 끊지 않는다.
    win_streak = loss_streak = max_win_streak = max_loss_streak = 0
    # 수수료·세금. 봇(증권사 연동)이 넣은 기록에만 있고 손으로 쓴 기록에는 보통 없다.
    total_fee = total_tax = 0.0
    cost_record_count = 0

    EPS = 1e-9

    for t in trades:
        stock = stock_identity(t)
        display_names[stock] = t['stockName'].strip()
        display_codes[stock] = normalize_stock_code(t.get('stockCode'))
        qty = float(t.get('quantity') or 0)
        price = float(t.get('price') or 0)
        ttype = t.get('tradeType')
        dt = parse_entry_dt(t)
        day = trade_day(t)
        mkey = '미상'
        if day:
            mkey = get_monday(day) if granularity == 'weekly' else day.strftime('%Y-%m')

        p = portfolio.setdefault(position_key(t), dict(empty_holding(), lots=deque()))
        inw = in_window(day) and (include is None or include(t))
        known_pnl = t.get('realizedPnlSource') != 'unavailable'
        profit = basis = dividend = 0.0
        holding_days = None
        missing_qty = max(0.0, qty - p['qty']) if ttype == '매도' else 0.0

        if inw and (t.get('fee') is not None or t.get('tax') is not None):
            cost_record_count += 1
            total_fee += float(t.get('fee') or 0)
            total_tax += float(t.get('tax') or 0)

        if ttype == '매수':
            vol = price * qty
            if inw:
                buy_count += 1
                total_buy_amount += vol
                monthly[mkey]['buyAmount'] += vol

            apply_trade(p, t)
            p['lots'].append([dt, qty])

        elif ttype == '매도':
            profit, basis, _ = apply_trade(p, t)
            vol = price * qty

            # FIFO 로트 매칭으로 이 매도의 보유기간(일 × 수량)을 구한다
            sell_hold_days = sell_hold_qty = 0.0
            remaining = qty
            while remaining > EPS and p['lots']:
                lot = p['lots'][0]
                lot_dt, lot_qty = lot[0], lot[1]
                matched = min(remaining, lot_qty)
                if lot_dt and dt:
                    sell_hold_days += (dt - lot_dt).total_seconds() / 86400.0 * matched
                    sell_hold_qty += matched
                lot[1] -= matched
                remaining -= matched
                if lot[1] <= EPS:
                    p['lots'].popleft()
            if sell_hold_qty > 0:
                holding_days = sell_hold_days / sell_hold_qty

            if inw:
                sell_count += 1
                known_pnl_sell_count += known_pnl
                total_realized += profit
                monthly[mkey]['realized'] += profit
                monthly[mkey]['sellAmount'] += vol
                monthly[mkey]['sellCount'] += 1
                total_sell_amount += vol

                ps = per_stock[stock]
                ps['realized'] += profit
                ps['sellCount'] += 1

                if profit > max_single_win:
                    max_single_win = profit
                if profit < max_single_loss:
                    max_single_loss = profit

                if profit > 0:
                    win_count += 1
                    gross_profit += profit
                    ps['winCount'] += 1
                    monthly[mkey]['winCount'] += 1
                    win_streak += 1
                    loss_streak = 0
                    max_win_streak = max(max_win_streak, win_streak)
                elif profit < 0:
                    loss_count += 1
                    gross_loss += -profit
                    ps['lossCount'] += 1
                    monthly[mkey]['lossCount'] += 1
                    loss_streak += 1
                    win_streak = 0
                    max_loss_streak = max(max_loss_streak, loss_streak)
                if dt and known_pnl:
                    realized_events.append((day, profit))

                sell_rate = _rate(profit, basis) if known_pnl else None
                if sell_rate is not None:
                    sold_basis += basis
                    basis_profit += profit
                    return_rate_sum += sell_rate
                    return_rate_count += 1
                    ps['basis'] += basis
                    ps['basisProfit'] += profit

                if sell_hold_qty > 0:
                    holding_days_weighted += sell_hold_days
                    holding_qty_total += sell_hold_qty
                    ps['holdDays'] += sell_hold_days
                    ps['holdQty'] += sell_hold_qty
                    if profit > 0:
                        win_hold_days += sell_hold_days
                        win_hold_qty += sell_hold_qty
                    elif profit < 0:
                        loss_hold_days += sell_hold_days
                        loss_hold_qty += sell_hold_qty
                    b = buckets[_holding_bucket(sell_hold_days / sell_hold_qty)]
                    b['sellCount'] += 1
                    b['realized'] += profit
                    if profit > 0:
                        b['winCount'] += 1
                    elif profit < 0:
                        b['lossCount'] += 1
                    if sell_rate is not None:
                        b['basis'] += basis
                        b['basisProfit'] += profit

            # 공통 원가 계산에서 청산된 포지션의 보유기간 로트도 비운다.
            if p['qty'] <= EPS:
                p['lots'].clear()

        elif ttype == '배당':
            if not inw:
                continue
            dividend_count += 1
            amount = price * qty
            dividend = amount
            total_dividend += amount
            monthly[mkey]['dividend'] += amount
            per_stock[stock]['dividend'] += amount
            if dt:
                realized_events.append((day, amount))

        if trade_facts is not None and inw and ttype in ('매수', '매도', '배당'):
            trade_facts.append({
                'id': t.get('id'), 'date': day.strftime('%Y-%m-%d') if day else None,
                'period': mkey, 'stock': t['stockName'].strip(), 'identity': stock,
                'stockCode': normalize_stock_code(t.get('stockCode')), 'tradeType': ttype,
                'quantity': t.get('_originalQuantity', qty), 'price': t.get('_originalPrice', price), 'realized': profit, 'basis': basis,
                'realizedSource': (t.get('realizedPnlSource') or (
                    'recorded' if t.get('realizedPnl') is not None else 'reconstructed')) if ttype == '매도' else None,
                'dividend': dividend, 'returnRate': _rate(profit, basis) if ttype == '매도' and known_pnl else None,
                'holdingDays': holding_days,
                'holdingBucket': HOLDING_BUCKETS[_holding_bucket(holding_days)][1]
                    if holding_days is not None else None,
                'missingBuyQuantity': missing_qty / t.get('_splitFactor', 1), 'needsReview': bool(t.get('needsReview')),
                'fee': t.get('fee'), 'tax': t.get('tax'), 'currency': currency_of(t),
                'tradeClass': t.get('tradeClass') or '', 'accountKey': account_group_key(t),
                'broker': t.get('brokerAccount') or '',
                'accountNumber': t.get('subAccount') or '',
                'account': t.get('_account') or t.get('accountName') or t.get('subAccount') or '미지정',
            })

    # 누적 실현손익 곡선 및 최대 낙폭(MDD, 금액 기준)
    realized_events.sort(key=lambda x: x[0])
    cumulative = peak = max_drawdown = 0.0
    peak_dt = mdd_peak_dt = mdd_trough_dt = None  # peak_dt 가 None 이면 '시작 시점(0원)'이 고점
    daily_cumulative = {}  # 날짜 → 그날 마지막 누적값 (화면의 누적 실현손익 곡선)
    for event_dt, amount in realized_events:
        cumulative += amount
        if cumulative > peak:
            peak = cumulative
            peak_dt = event_dt
        drawdown = peak - cumulative
        if drawdown > max_drawdown:
            max_drawdown = drawdown
            mdd_peak_dt, mdd_trough_dt = peak_dt, event_dt
        daily_cumulative[event_dt.strftime('%Y-%m-%d')] = cumulative
    equity_curve = [{'date': d, 'value': v} for d, v in daily_cumulative.items()]

    def day(dt):
        return dt.strftime('%Y-%m-%d') if dt else None

    # ⭐️ 승률은 '승/(승+패)'. 예전에는 여기서 이렇게 계산해 놓고 버린 뒤 반환값에서는
    #    '승/전체 매도건수'로 다시 계산해, 손익이 정확히 0인 매도가 섞이면 화면에
    #    '승률 50.0% (1승 0패)' 같은 자기모순이 찍혔다. 라벨과 맞는 쪽으로 통일한다.
    decided = win_count + loss_count
    win_rate = (win_count / decided * 100.0) if decided else 0.0
    avg_win = (gross_profit / win_count) if win_count else 0.0
    avg_loss = (gross_loss / loss_count) if loss_count else 0.0
    # ⭐️ 손익비는 '이익합/손실합' 비율이다. 손실이 한 건도 없을 때 예전에는
    #    gross_profit(금액)을 그대로 돌려줘 화면에 '손익비 100.00' 처럼 금액이
    #    비율인 척 찍혔다. 정의할 수 없는 구간이므로 None(화면에서 '—')으로 둔다.
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None
    avg_holding_days = (holding_days_weighted / holding_qty_total) if holding_qty_total > 0 else 0.0
    # ⭐️ 손익비(payoff ratio)는 '평균수익/평균손실'. profit_factor(총이익/총손실)와 다르다 —
    #    승률이 낮아도 손익비가 크면 남고, 승률이 높아도 손익비가 작으면 잃는다.
    payoff_ratio = (avg_win / avg_loss) if (avg_win > 0 and avg_loss > 0) else None
    # 기대값: 매도 1건당 평균 실현손익 (= 승률×평균수익 − 패율×평균손실, 손익 0 매도 포함)
    expectancy = (total_realized / known_pnl_sell_count) if known_pnl_sell_count else None
    total_cost = total_fee + total_tax

    def win_rate_of(v):
        decided_n = v['winCount'] + v['lossCount']
        return (v['winCount'] / decided_n * 100.0) if decided_n else None

    # 기간별: 전체 기간을 시간순으로 반환 ('미상' 제외)
    monthly_list = [
        {'month': k, **v, 'winRate': win_rate_of(v)}
        for k, v in sorted(monthly.items()) if k != '미상'
    ]

    holding_buckets = [
        {'label': label, 'sellCount': b['sellCount'], 'winCount': b['winCount'],
         'lossCount': b['lossCount'], 'winRate': win_rate_of(b), 'realized': b['realized'],
         'returnRate': _rate(b['basisProfit'], b['basis'])}
        for (_limit, label), b in zip(HOLDING_BUCKETS, buckets, strict=True) if b['sellCount']
    ]

    # 종목별: 실현손익(+배당) 합계 내림차순
    per_stock_list = []
    for stock, v in per_stock.items():
        sc = v['sellCount']
        per_stock_list.append({
            'stock': display_names.get(stock, stock),
            'stockCode': display_codes.get(stock, ''),
            'realized': v['realized'],
            'dividend': v['dividend'],
            'total': v['realized'] + v['dividend'],
            'sellCount': sc,
            'winCount': v['winCount'],
            'lossCount': v['lossCount'],
            # 전체 승률과 같은 정의(승/(승+패)) — 손익 0 매도는 분모에서 뺀다.
            'winRate': (v['winCount'] / (v['winCount'] + v['lossCount']) * 100.0)
                       if (v['winCount'] + v['lossCount']) else 0.0,
            'returnRate': _rate(v['basisProfit'], v['basis']),
            'avgHoldingDays': (v['holdDays'] / v['holdQty']) if v['holdQty'] > 0 else None,
        })
    per_stock_list.sort(key=lambda x: x['total'], reverse=True)

    return {
        'totalPnl': total_realized + total_dividend,
        'totalRealized': total_realized,
        'totalDividend': total_dividend,
        'buyCount': buy_count,
        'sellCount': sell_count,
        'dividendCount': dividend_count,
        'winCount': win_count,
        'lossCount': loss_count,
        'winRate': win_rate,
        'profitFactor': profit_factor,
        'avgWin': avg_win,
        'avgLoss': avg_loss,
        'avgHoldingDays': avg_holding_days,
        'maxDrawdown': max_drawdown,
        'maxSingleWin': max_single_win,
        'maxSingleLoss': max_single_loss,
        'totalBuyAmount': total_buy_amount,
        'totalSellAmount': total_sell_amount,
        # 수익률 — 실현은 '원금이 있는 매도'의 손익 합 ÷ 원금 합, 평균은 매도 1건 수익률의 단순 평균
        'realizedReturnRate': _rate(basis_profit, sold_basis),
        'avgReturnRate': (return_rate_sum / return_rate_count) if return_rate_count else None,
        'payoffRatio': payoff_ratio,
        'expectancy': expectancy,
        'avgHoldingDaysWin': (win_hold_days / win_hold_qty) if win_hold_qty > 0 else None,
        'avgHoldingDaysLoss': (loss_hold_days / loss_hold_qty) if loss_hold_qty > 0 else None,
        'maxWinStreak': max_win_streak,
        'maxLossStreak': max_loss_streak,
        'maxDrawdownPeakDate': day(mdd_peak_dt) if max_drawdown > 0 else None,
        'maxDrawdownTroughDate': day(mdd_trough_dt) if max_drawdown > 0 else None,
        # 비용은 기록된 것만 더한다. costRecordCount 가 0이면 '기록 없음'이지 '비용 0원'이 아니다.
        'totalFee': total_fee,
        'totalTax': total_tax,
        'totalCost': total_cost,
        'costRecordCount': cost_record_count,
        'netPnl': total_realized + total_dividend - total_cost,
        'equityCurve': equity_curve,
        'holdingBuckets': holding_buckets,
        'monthly': monthly_list,
        'perStock': per_stock_list
    }
