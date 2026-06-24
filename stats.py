"""매매 성과 분석(통계) 계산 로직.

DB에 의존하지 않는 순수 함수로 구성되어 단위 테스트가 용이합니다.
백엔드 라우트(/api/stats)에서 조회한 기록 리스트를 받아 지표를 계산합니다.
"""
from collections import defaultdict, deque
from datetime import datetime, timedelta

def parse_entry_dt(entry):
    """기록의 거래 일시를 datetime으로 파싱합니다. rawDate를 우선 사용하고,
    실패 시 id(밀리초 타임스탬프)로 대체합니다. 모두 실패하면 None."""
    raw = entry.get('rawDate')
    if raw:
        for fmt in ('%Y-%m-%dT%H:%M', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
            try:
                return datetime.strptime(str(raw), fmt)
            except (ValueError, TypeError):
                continue
    try:
        return datetime.fromtimestamp(int(entry.get('id')) / 1000)
    except (ValueError, TypeError, OSError):
        return None

# 하위 호환을 위한 별칭 (기존 내부 명칭)
_parse_entry_dt = parse_entry_dt

def get_monday(dt):
    """주어진 날짜의 월요일(YYYY-MM-DD)을 반환합니다."""
    monday = dt - timedelta(days=dt.weekday())
    return monday.strftime('%Y-%m-%d')

def compute_trade_stats(rows, granularity='monthly'):
    """매매 기록 리스트로부터 성과 분석 지표를 계산합니다.

    실현손익은 프론트엔드 대시보드와 동일한 이동평균단가(average-cost) 방식으로,
    보유기간은 FIFO 로트 매칭으로 추정합니다.
    """
    trades = [r for r in rows if r.get('type') == 'trade' and (r.get('stockName') or '').strip()]
    trades.sort(key=lambda r: parse_entry_dt(r) or datetime.min)

    portfolio = {}  # stock -> {qty, totalCost, avgPrice, lots(deque of [dt, qty])}
    monthly = defaultdict(lambda: {'realized': 0.0, 'dividend': 0.0, 'buyAmount': 0.0, 'sellAmount': 0.0})
    per_stock = defaultdict(lambda: {'realized': 0.0, 'dividend': 0.0, 'sellCount': 0, 'winCount': 0})
    realized_events = []  # (dt, amount) — 누적 실현손익 곡선 / MDD 계산용

    total_realized = 0.0      # 매도 실현손익 합계
    total_dividend = 0.0      # 배당 수익 합계
    buy_count = sell_count = dividend_count = 0
    win_count = loss_count = 0
    gross_profit = 0.0        # 이익 매도건 손익 합
    gross_loss = 0.0          # 손실 매도건 손익 절댓값 합
    holding_days_weighted = 0.0
    holding_qty_total = 0.0
    
    max_single_win = 0.0
    max_single_loss = 0.0
    total_buy_amount = 0.0
    total_sell_amount = 0.0

    EPS = 1e-9

    for t in trades:
        stock = t['stockName'].strip()
        qty = float(t.get('quantity') or 0)
        price = float(t.get('price') or 0)
        ttype = t.get('tradeType')
        dt = parse_entry_dt(t)
        mkey = '미상'
        if dt:
            mkey = get_monday(dt) if granularity == 'weekly' else dt.strftime('%Y-%m')

        p = portfolio.setdefault(stock, {'qty': 0.0, 'totalCost': 0.0, 'avgPrice': 0.0, 'lots': deque()})

        if ttype == '매수':
            buy_count += 1
            p['qty'] += qty
            
            vol = price * qty
            p['totalCost'] += vol
            total_buy_amount += vol
            monthly[mkey]['buyAmount'] += vol
            
            if p['qty'] > 0:
                p['avgPrice'] = p['totalCost'] / p['qty']
            p['lots'].append([dt, qty])

        elif ttype == '매도':
            sell_count += 1
            avg = p['avgPrice']
            profit = (price - avg) * qty

            total_realized += profit
            monthly[mkey]['realized'] += profit
            
            vol = price * qty
            monthly[mkey]['sellAmount'] += vol
            total_sell_amount += vol
            
            per_stock[stock]['realized'] += profit
            per_stock[stock]['sellCount'] += 1
            
            if profit > max_single_win:
                max_single_win = profit
            if profit < max_single_loss:
                max_single_loss = profit
                
            if profit > 0:
                win_count += 1
                gross_profit += profit
                per_stock[stock]['winCount'] += 1
            elif profit < 0:
                loss_count += 1
                gross_loss += -profit
            if dt:
                realized_events.append((dt, profit))

            # FIFO 로트 매칭으로 보유기간(일) 가중 합산
            remaining = qty
            while remaining > EPS and p['lots']:
                lot = p['lots'][0]
                lot_dt, lot_qty = lot[0], lot[1]
                matched = min(remaining, lot_qty)
                if lot_dt and dt:
                    holding_days_weighted += (dt - lot_dt).total_seconds() / 86400.0 * matched
                    holding_qty_total += matched
                lot[1] -= matched
                remaining -= matched
                if lot[1] <= EPS:
                    p['lots'].popleft()

            # 보유 포지션 갱신 (청산 시 초기화)
            p['qty'] -= qty
            p['totalCost'] -= avg * qty
            if p['qty'] <= EPS:
                p['qty'] = 0.0
                p['totalCost'] = 0.0
                p['avgPrice'] = 0.0
                p['lots'].clear()

        elif ttype == '배당':
            dividend_count += 1
            amount = price * qty
            total_dividend += amount
            monthly[mkey]['dividend'] += amount
            per_stock[stock]['dividend'] += amount
            if dt:
                realized_events.append((dt, amount))

    # 누적 실현손익 곡선 및 최대 낙폭(MDD, 금액 기준)
    realized_events.sort(key=lambda x: x[0])
    cumulative = peak = max_drawdown = 0.0
    for _dt, amount in realized_events:
        cumulative += amount
        if cumulative > peak:
            peak = cumulative
        drawdown = peak - cumulative
        if drawdown > max_drawdown:
            max_drawdown = drawdown

    decided = win_count + loss_count
    win_rate = (win_count / decided * 100.0) if decided else 0.0
    avg_win = (gross_profit / win_count) if win_count else 0.0
    avg_loss = (gross_loss / loss_count) if loss_count else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None
    avg_holding_days = (holding_days_weighted / holding_qty_total) if holding_qty_total > 0 else 0.0

    # 월별: 최근 12개월만 시간순으로 반환 ('미상' 제외)
    monthly_list = [
        {'month': k, **v}
        for k, v in sorted(monthly.items()) if k != '미상'
    ][-12:]

    # 종목별: 실현손익(+배당) 합계 내림차순
    per_stock_list = []
    for stock, v in per_stock.items():
        sc = v['sellCount']
        per_stock_list.append({
            'stock': stock,
            'realized': v['realized'],
            'dividend': v['dividend'],
            'total': v['realized'] + v['dividend'],
            'sellCount': sc,
            'winCount': v['winCount'],
            'winRate': (v['winCount'] / sc * 100.0) if sc else 0.0,
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
        'winRate': (win_count / sell_count * 100.0) if sell_count > 0 else 0.0,
        'profitFactor': (gross_profit / gross_loss) if gross_loss > 0 else (gross_profit if gross_profit > 0 else None),
        'avgWin': (gross_profit / win_count) if win_count > 0 else 0.0,
        'avgLoss': (gross_loss / loss_count) if loss_count > 0 else 0.0,
        'avgHoldingDays': (holding_days_weighted / holding_qty_total) if holding_qty_total > 0 else 0.0,
        'maxDrawdown': max_drawdown,
        'maxSingleWin': max_single_win,
        'maxSingleLoss': max_single_loss,
        'totalBuyAmount': total_buy_amount,
        'totalSellAmount': total_sell_amount,
        'monthly': monthly_list,
        'perStock': per_stock_list
    }
