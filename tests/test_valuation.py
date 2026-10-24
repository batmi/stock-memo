"""과거 평가손익: 당시 보유량·원가, 종가 누락, 영속 수집 큐와 제공처 가격 단위."""

from datetime import date, datetime, timedelta, timezone
import json
import time
from urllib.parse import parse_qs, urlsplit

import pytest

from app.database.db import db_conn
from app.services import historical_prices as history, historical_provider as provider
from app.services import journal
from helpers import _login
from test_journal_api import seed

completed_day = provider.completed_day


@pytest.fixture(autouse=True)
def fixed_close(monkeypatch):
    monkeypatch.setattr(provider, 'completed_day', lambda currency: date(2026, 1, 12))


def cache(closes, code='005930', currency='KRW', splits=None, checked=None):
    checked = time.time() if checked is None else checked
    with db_conn() as conn:
        conn.execute('INSERT OR REPLACE INTO historical_assets VALUES (?,?,?,?,?,?)',
                     (code, currency, code + '.KS' if currency == 'KRW' else code,
                      str(provider.market_timezone(currency)), json.dumps(splits or {}), checked))
        for day, close in closes.items():
            conn.execute('INSERT OR REPLACE INTO historical_prices VALUES (?,?,?,?,?,?)',
                         (code, currency, day, close, 'missing' if close is None else 'ok', checked))
        conn.commit()


def evaluate(client, start='2026-01-05', end='2026-01-09', method='GET', **query):
    return client.open('/api/journal/stats/evaluation', method=method,
                       query_string={'from': start, 'to': end, **query})


def values(response):
    assert response.status_code == 200, response.json
    return [point['value'] for point in response.json['curve']]


def test_collect_once_then_reuse_and_replay_partial_sales(client, monkeypatch):
    _login(client, 'reader')
    seed(id=1, quantity=2, price=100, rawDate='2026-01-05T09:00')
    seed(id=2, quantity=1, price=130, tradeType='매도', rawDate='2026-01-07T09:00')
    seed(id=3, quantity=1, price=150, tradeType='매도', rawDate='2026-01-08T09:00')
    seed(id=4, quantity=2, price=80, rawDate='2026-01-09T09:00')
    seed(id=5, quantity=2, price=200, tradeType='매도', rawDate='2026-02-01T09:00')
    called = []
    monkeypatch.setattr(provider, 'fetch_asset', lambda *a: {
        'symbol': '005930.KS', 'timezone': 'Asia/Seoul', 'splits': {}})
    def fetch(asset, currency, start, end):
        called.append((start, end))
        return {'2026-01-05': 110, '2026-01-06': 120, '2026-01-07': 140, '2026-01-09': 90}
    monkeypatch.setattr(provider, 'fetch_closes', fetch)
    response = evaluate(client, method='POST')
    assert values(response) == [None, None, None, 0, None]
    assert response.json['pending'] and not called  # 요청 스레드에서는 HTTP 호출 없음
    evaluate(client, method='POST')  # 같은 기간 중복 요청
    assert history.run_one() and not history.run_one()
    assert values(evaluate(client)) == [20, 40, 40, 0, 20]
    assert values(evaluate(client)) == [20, 40, 40, 0, 20]
    assert len(called) == 1
    with db_conn() as conn:
        assert conn.execute('SELECT username FROM historical_watchers').fetchone()[0] == 'reader'


def test_period_without_trades_keeps_opening_holdings_and_no_future_sales(client):
    _login(client, 'reader')
    seed(id=1, quantity=4, price=100, rawDate='2025-12-01T09:00')
    seed(id=2, quantity=4, tradeType='매도', rawDate='2026-02-01T09:00')
    cache({'2026-01-08': 140, '2026-01-09': 150})
    result = evaluate(client, '2026-01-08', '2026-01-09')
    assert values(result) == [160, 200]
    assert result.json['hasHoldings']


def test_class_filter_keeps_average_cost_and_consumes_other_class_sales(client):
    _login(client, 'reader')
    client.post('/api/mappings', json={'brokers': {}, 'accounts': {
        'B': {'alias': '제외계좌', 'exclude_from_stats': True}}})
    seed(id=1, subAccount='A', tradeClass='장기', quantity=10, price=100, rawDate='2025-12-31T09:00')
    seed(id=2, subAccount='A', tradeClass='단기', quantity=10, price=200, rawDate='2026-01-05T09:00')
    seed(id=3, subAccount='A', tradeClass='기타', quantity=12, price=300,
         tradeType='매도', rawDate='2026-01-06T09:00')
    seed(id=4, subAccount='B', tradeClass='단기', quantity=100, price=1, rawDate='2026-01-05T09:00')
    seed(id=5, subAccount='A', tradeClass='단기', quantity=100, price=1,
         rawDate='2026-01-05T09:00', tradeStatus='CANCELED')
    seed(id=6, stockCode='AAPL', currency='USD', tradeClass='단기', price=1, rawDate='2026-01-05T09:00')
    cache({'2026-01-06': 160})
    assert values(evaluate(client, '2026-01-06', '2026-01-06', tradeClass='단기', currency='KRW')) == [80]


def test_missing_session_is_not_zero_or_carried_across_gap(client):
    _login(client, 'reader')
    seed(id=1, quantity=1, price=100, rawDate='2026-01-05T09:00')
    seed(id=2, stockCode='000660', stockName='두번째', quantity=1, price=100, rawDate='2026-01-05T09:00')
    cache({'2026-01-08': 120, '2026-01-09': None})
    cache({'2026-01-08': 110, '2026-01-09': 130}, code='000660')
    response = evaluate(client, '2026-01-08', '2026-01-11')
    assert values(response) == [30, None, None, None]
    assert response.json['missingDays'] == 3
    cache({'2026-01-09': 140})
    assert values(evaluate(client, '2026-01-08', '2026-01-11')) == [30, 70, 70, 70]


def test_split_changes_units_before_trades_but_future_split_does_not(client):
    _login(client, 'reader')
    seed(id=1, quantity=10, price=100, rawDate='2026-01-05T09:00')
    seed(id=2, quantity=5, price=60, tradeType='매도', rawDate='2026-01-08T09:00')
    cache({'2026-01-06': 120, '2026-01-07': 60, '2026-01-08': 60},
          splits={'2026-01-07': 2, '2026-01-20': 3})
    response = evaluate(client, '2026-01-06', '2026-01-08')
    assert values(response) == [200, 200, 150]
    assert '분할' in ' '.join(response.json['notes'])


def test_quotes_do_not_cross_user_or_currency_and_stale_version_fails(client):
    assert evaluate(client).status_code == 401
    _login(client, 'reader')
    seed(id=1, quantity=1, price=100, rawDate='2026-01-05T09:00')
    cache({'2026-01-05': 110})
    version = journal.snapshot('reader')['version']
    seed('other', id=2, quantity=1000, price=1, rawDate='2026-01-05T09:00')
    response = evaluate(client, '2026-01-05', '2026-01-05', version=version)
    assert values(response) == [10]
    assert response.headers['Cache-Control'] == 'no-store'
    seed(id=3, quantity=1, price=100, rawDate='2026-01-06T09:00')
    assert evaluate(client, version=version).status_code == 409
    assert evaluate(client, currency='EUR').status_code == 400
    assert evaluate(client, start='2026-02-01', end='2026-02-10').status_code == 400


def test_queue_overlaps_and_lease_survive_restart(app):
    key = ('005930', 'KRW')
    history.enqueue({key: set(history.days_between('2026-01-05', '2026-01-09'))})
    history.enqueue({key: set(history.days_between('2026-01-07', '2026-01-12'))})
    with db_conn() as conn:
        jobs = conn.execute('SELECT start,end FROM historical_jobs ORDER BY id').fetchall()
    assert [tuple(row) for row in jobs] == [('2026-01-05', '2026-01-09'), ('2026-01-10', '2026-01-12')]
    first = history.claim(now=100)
    assert history.claim(now=101) is None
    resumed = history.claim(now=100 + history.LEASE_SECONDS)
    assert first['id'] == resumed['id'] and resumed['attempts'] == 2


def test_failure_backs_off_then_manual_retry_is_throttled(app, monkeypatch):
    now = [time.time()]
    monkeypatch.setattr(history.time, 'time', lambda: now[0])
    monkeypatch.setattr(provider, 'fetch_asset', lambda *a: (_ for _ in ()).throw(OSError('offline')))
    needs = {('005930', 'KRW'): {'2026-01-05'}}
    history.enqueue(needs)
    for attempt in range(history.MAX_ATTEMPTS):
        assert history.run_one()
        with db_conn() as conn:
            job = dict(conn.execute('SELECT * FROM historical_jobs').fetchone())
        assert not history.run_one()
        if attempt < history.MAX_ATTEMPTS - 1:
            now[0] = job['next_attempt']
    assert job['status'] == 'failed'
    history.enqueue(needs, retry=True)
    assert not history.run_one()  # 반복 클릭으로 제공처 호출 제한을 우회하지 않는다.
    now[0] += 61
    history.enqueue(needs, retry=True)
    assert history.run_one()


def test_empty_provider_result_preserves_verified_close_without_request_loop(app, monkeypatch):
    cache({'2026-01-05': 100}, checked=time.time() - history.DAY * 2)
    monkeypatch.setattr(provider, 'fetch_asset', lambda *a: {
        'symbol': '005930.KS', 'timezone': 'Asia/Seoul', 'splits': {}})
    monkeypatch.setattr(provider, 'fetch_closes', lambda *a: {})
    with db_conn() as conn:
        conn.execute("INSERT INTO historical_jobs(code,currency,start,end) VALUES ('005930','KRW','2026-01-05','2026-01-06')")
        conn.commit()
    assert history.run_one()
    quotes, _ = history.read([('005930', 'KRW')], '2026-01-05', '2026-01-06')
    assert quotes[('005930', 'KRW')]['2026-01-05']['close'] == 100
    assert quotes[('005930', 'KRW')]['2026-01-06']['status'] == 'missing'
    history.enqueue({('005930', 'KRW'): {'2026-01-05', '2026-01-06'}})
    assert not history.run_one()


def test_adjustment_correction_invalidates_other_cached_ranges(app, monkeypatch):
    cache({'2025-12-01': 100, '2026-01-05': 100}, checked=time.time() - history.DAY * 2)
    monkeypatch.setattr(provider, 'fetch_asset', lambda *a: {
        'symbol': '005930.KS', 'timezone': 'Asia/Seoul', 'splits': {'2026-01-06': 2}})
    monkeypatch.setattr(provider, 'fetch_closes', lambda *a: {'2026-01-05': 110})
    with db_conn() as conn:
        conn.execute("INSERT INTO historical_jobs(code,currency,start,end) VALUES ('005930','KRW','2026-01-05','2026-01-05')")
        conn.commit()
    history.run_one()
    quotes, _ = history.read([('005930', 'KRW')], '2025-12-01', '2026-01-05')
    assert quotes[('005930', 'KRW')]['2025-12-01']['checked_at'] == 0
    assert quotes[('005930', 'KRW')]['2026-01-05']['close'] == 110


def test_provider_restores_actual_close_using_splits_after_requested_end(monkeypatch):
    timestamp = int(datetime(2026, 1, 5, 0, tzinfo=timezone.utc).timestamp())
    monkeypatch.setattr(provider, '_chart', lambda *a: {
        'meta': {'currency': 'KRW', 'exchangeTimezoneName': 'Asia/Seoul'},
        'timestamp': [timestamp], 'indicators': {'quote': [{'close': [20]}], 'adjclose': [{'adjclose': [17]}]}})
    asset = {'symbol': '005930.KS', 'timezone': 'Asia/Seoul', 'splits': {'2026-01-07': 2, '2026-02-01': 3}}
    assert provider.fetch_closes(asset, 'KRW', date(2026, 1, 5), date(2026, 1, 5)) == {'2026-01-05': 120}
    with pytest.raises(ValueError, match='통화'):
        provider.fetch_closes(asset, 'USD', date(2026, 1, 5), date(2026, 1, 5))


@pytest.mark.parametrize('code', ['005930', '0193T0', '0193W0', '00088K'])
def test_provider_accepts_alphanumeric_korean_codes(code):
    assert provider.candidates(code, 'KRW') == [code + '.KS', code + '.KQ']
    assert provider.supports(code, 'KRW')


@pytest.mark.parametrize('code', ['', '삼성전자', '../ABC', '005930.KS', '0193T00'])
def test_provider_rejects_invalid_korean_codes(code):
    assert not provider.supports(code, 'KRW')


def gold_meta():
    return {'symbolCode': 'M04020000', 'unit': '원/g',
            'stockExchangeType': {'code': 'KRX', 'zoneId': 'Asia/Seoul'}}


@pytest.mark.parametrize('code', ['KRXGOLD', 'GOLD'])
def test_gold_aliases_validate_krw_per_gram(monkeypatch, code):
    monkeypatch.setattr(provider, '_gold_json', lambda *a, **kw: gold_meta())
    assert provider.supports(code, 'KRW')
    asset = provider.fetch_asset(code, 'KRW')
    assert asset == {'symbol': provider.GOLD_SYMBOL, 'timezone': 'Asia/Seoul', 'splits': {}}
    with pytest.raises(ValueError, match='통화'):
        provider.fetch_closes(asset, 'USD', date(2026, 1, 5), date(2026, 1, 5))
    # 같은 문자열의 미국 주식 티커를 원화 금현물로 조회하지 않는다.
    assert provider.candidates('GOLD', 'USD') == ['GOLD']
    monkeypatch.setattr(provider, '_chart', lambda *a: {
        'meta': {'currency': 'USD', 'exchangeTimezoneName': 'America/New_York'}})
    assert provider.fetch_asset('GOLD', 'USD')['symbol'] == 'GOLD'


@pytest.mark.parametrize('field,value', [('unit', '달러/트로이온스'), ('symbolCode', 'GC'),
                                       ('stockExchangeType', {'code': 'COMEX', 'zoneId': 'Asia/Seoul'})])
def test_gold_rejects_other_units_or_markets(monkeypatch, field, value):
    monkeypatch.setattr(provider, '_gold_json', lambda *a, **kw: {**gold_meta(), field: value})
    with pytest.raises(ValueError, match='단위'):
        provider.fetch_asset('KRXGOLD', 'KRW')


def test_gold_pagination_seeks_old_range_and_keeps_missing_close(monkeypatch):
    latest = date(2026, 10, 23)
    rows = [{'localTradedAt': f'{day.isoformat()}T00:00:00+09:00',
             'closePrice': None if day == date(2021, 1, 6) else '171,250'}
            for offset in range(2500) if (day := latest - timedelta(days=offset)).weekday() < 5]
    calls = []
    def fetch(path, **kwargs):
        query = parse_qs(urlsplit(path).query)
        number, size = int(query['page'][0]), int(query['pageSize'][0])
        calls.append(number)
        assert size == 60 and 0 < kwargs['timeout'] <= 12
        return rows[(number - 1) * size:number * size]
    monkeypatch.setattr(provider, '_gold_json', fetch)
    result = provider.fetch_closes({'symbol': provider.GOLD_SYMBOL}, 'KRW', date(2021, 1, 4), date(2021, 1, 8))
    assert result == {f'2021-01-{day:02}': 171250 for day in (4, 5, 7, 8)}
    assert len(calls) < 10 and max(calls) > 20  # 수년 전 자료도 최신부터 모든 페이지를 순회하지 않는다.
    assert len(calls) == len(set(calls))


def test_gold_repeated_page_is_not_saved_as_missing_history(monkeypatch):
    rows = [{'localTradedAt': '2026-01-09T00:00:00+09:00', 'closePrice': '171,250'}]
    monkeypatch.setattr(provider, '_gold_json', lambda *a, **kw: rows)
    with pytest.raises(ValueError, match='반복'):
        provider.fetch_closes({'symbol': provider.GOLD_SYMBOL}, 'KRW', date(2026, 1, 5), date(2026, 1, 9))


def test_gold_collection_stops_before_job_lease_expires(monkeypatch):
    clock = iter([0, 121])
    monkeypatch.setattr(provider, 'monotonic', lambda: next(clock))
    with pytest.raises(ValueError, match='대기 시간'):
        provider.fetch_closes({'symbol': provider.GOLD_SYMBOL}, 'KRW', date(2026, 1, 5), date(2026, 1, 9))


def test_alphanumeric_etfs_and_gold_collect_then_produce_complete_curve(client, monkeypatch):
    _login(client, 'reader')
    for id_, code, price in [(1, '0193T0', 100), (2, '0193W0', 200), (3, 'KRXGOLD', 300)]:
        seed(id=id_, stockCode=code, stockName=code, quantity=1, price=price,
             currency='' if code == 'KRXGOLD' else 'KRW', rawDate='2026-01-05T09:00')
    calls = []
    def chart(symbol, start, end, interval):
        calls.append((symbol, interval))
        base = {'0193T0.KS': 100, '0193W0.KS': 200}[symbol]
        return {'meta': {'currency': 'KRW', 'exchangeTimezoneName': 'Asia/Seoul'},
                'timestamp': [int(datetime(2026, 1, d, tzinfo=timezone.utc).timestamp()) for d in (5, 6)],
                'indicators': {'quote': [{'close': [base * 1.1, base * 1.2]}]}}
    def gold(path='', **kwargs):
        calls.append(('gold', path))
        return ([{'localTradedAt': f'2026-01-{d:02}T00:00:00+09:00', 'closePrice': str(close)}
                 for d, close in [(6, 360), (5, 330)]] if path else gold_meta())
    monkeypatch.setattr(provider, '_chart', chart)
    monkeypatch.setattr(provider, '_gold_json', gold)
    pending = evaluate(client, end='2026-01-06', method='POST')
    assert values(pending) == [None, None]
    assert pending.json['pending'] and not pending.json['notes'] and not calls
    for _ in range(3):
        assert history.run_one()
    assert not history.run_one()
    ready = evaluate(client, end='2026-01-06')
    assert values(ready) == pytest.approx([60, 120])
    assert ready.json['missingDays'] == 0 and not ready.json['pending']
    assert len(calls) == 6
    evaluate(client, end='2026-01-06', method='POST')
    assert not history.run_one() and len(calls) == 6
    history.schedule_watchers()
    with db_conn() as conn:
        queued = conn.execute("SELECT code,start,end FROM historical_jobs WHERE status='queued'").fetchall()
    assert {tuple(row) for row in queued} == {
        (code, '2026-01-07', '2026-01-12') for code in ('0193T0', '0193W0', 'KRXGOLD')}


def test_provider_holidays_and_intraday_close_cutoff():
    assert not provider.is_session(date(2026, 1, 1), 'KRW')
    assert not provider.is_session(date(2026, 1, 1), 'USD')
    assert not provider.is_session(date(2026, 1, 10), 'KRW')
    assert completed_day('KRW', datetime(2026, 1, 5, 8, tzinfo=timezone.utc)) == date(2026, 1, 2)
    assert completed_day('KRW', datetime(2026, 1, 5, 9, tzinfo=timezone.utc)) == date(2026, 1, 5)
    assert completed_day('USD', datetime(2026, 1, 5, 21, tzinfo=timezone.utc)) == date(2026, 1, 2)
    assert completed_day('USD', datetime(2026, 1, 6, 0, tzinfo=timezone.utc)) == date(2026, 1, 5)


def test_daily_watcher_fills_gap_and_sees_new_trades(client, monkeypatch):
    _login(client, 'reader')
    seed(id=1, quantity=1, price=100, rawDate='2026-01-05T09:00')
    cache({'2026-01-05': 110})
    history.watch('reader')
    history.schedule_watchers()
    with db_conn() as conn:
        job = conn.execute('SELECT start,end FROM historical_jobs').fetchone()
        assert tuple(job) == ('2026-01-06', '2026-01-12')
    seed(id=2, quantity=1, stockCode='000660', rawDate='2026-01-08T09:00')
    history.schedule_watchers()
    with db_conn() as conn:
        assert conn.execute("SELECT count(*) FROM historical_jobs WHERE code='005930'").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM historical_jobs WHERE code='000660'").fetchone()[0] == 1


def test_old_worker_cannot_overwrite_reclaimed_job(app, monkeypatch):
    history.enqueue({('005930', 'KRW'): {'2026-01-05'}})
    monkeypatch.setattr(provider, 'fetch_asset', lambda *a: {
        'symbol': '005930.KS', 'timezone': 'Asia/Seoul', 'splits': {}})
    def takeover(*args):
        assert history.claim(now=time.time() + history.LEASE_SECONDS + 1)
        return {'2026-01-05': 123}
    monkeypatch.setattr(provider, 'fetch_closes', takeover)
    assert history.run_one()
    with db_conn() as conn:
        assert conn.execute('SELECT count(*) FROM historical_prices').fetchone()[0] == 0
        assert conn.execute('SELECT attempts FROM historical_jobs').fetchone()[0] == 2


def test_collection_finishing_during_report_requests_one_more_poll(client, monkeypatch):
    _login(client, 'reader')
    seed(id=1, quantity=1, price=100, rawDate='2026-01-05T09:00')
    original_enqueue = history.enqueue
    def completed_during_request(needs, **kwargs):
        cache({'2026-01-05': 110})
        original_enqueue(needs, **kwargs)
    monkeypatch.setattr(history, 'enqueue', completed_during_request)
    first = evaluate(client, '2026-01-05', '2026-01-05')
    assert values(first) == [None]
    assert first.json['pending'] and first.json['progress']['pending'] == 0
    monkeypatch.setattr(history, 'enqueue', original_enqueue)
    second = evaluate(client, '2026-01-05', '2026-01-05')
    assert values(second) == [10] and not second.json['pending']
