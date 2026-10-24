"""영속 일별 시세 캐시와 단일 수집 큐. 요청에서는 HTTP를 호출하지 않는다.

SQLite lease로 프로세스 간 중복 실행을 막고, 중단된 작업은 lease 만료 후 재개한다.
날짜별 성공/휴장/누락 상태를 저장하므로 같은 빈 구간을 매 조회마다 요청하지 않는다.
"""

from datetime import date, datetime, timedelta, timezone
import json
import logging
import time

from app.database.db import db_conn
from app.services import historical_provider as provider

log = logging.getLogger(__name__)
DAY = 86400
MAX_ATTEMPTS = 5
LEASE_SECONDS = 180


def days_between(start, end):
    day = date.fromisoformat(start) if isinstance(start, str) else start
    end = date.fromisoformat(end) if isinstance(end, str) else end
    while day <= end:
        yield day.isoformat()
        day += timedelta(days=1)


def read(assets, start, end):
    quotes, metadata = {}, {}
    with db_conn() as conn:
        # 분할 정보와 가격을 같은 DB 스냅샷에서 읽는다.
        conn.execute('BEGIN')
        for code, currency in assets:
            key = (code, currency)
            row = conn.execute('SELECT * FROM historical_assets WHERE code=? AND currency=?', key).fetchone()
            if row:
                metadata[key] = dict(row, splits=json.loads(row['splits']))
            quotes[key] = {r['day']: dict(r) for r in conn.execute(
                'SELECT * FROM historical_prices WHERE code=? AND currency=? AND day BETWEEN ? AND ?',
                (*key, start, end))}
    return quotes, metadata


def enqueue(needs, *, now=None, retry=False):
    """needs={(code,currency): {필요한 거래일}}. 저장된 날짜·처리 중인 범위를 제외한다."""
    now = time.time() if now is None else now
    recent = (datetime.fromtimestamp(now, timezone.utc).date() - timedelta(days=7)).isoformat()
    with db_conn(write=True) as conn:
        for (code, currency), wanted in needs.items():
            if not wanted or not provider.supports(code, currency):
                continue
            first, last = min(wanted), max(wanted)
            rows = {r['day']: r for r in conn.execute(
                'SELECT day,status,checked_at FROM historical_prices WHERE code=? AND currency=? AND day BETWEEN ? AND ?',
                (code, currency, first, last))}
            # 누락은 하루 뒤 재검사. 최근 일주일은 정정 시세를 하루 한 번 확인한다.
            missing = {d for d in wanted if d not in rows or rows[d]['checked_at'] == 0 or (
                rows[d]['status'] == 'missing' and rows[d]['checked_at'] < now - (60 if retry else DAY)) or (
                d >= recent and rows[d]['checked_at'] < now - DAY)}
            jobs = conn.execute('SELECT * FROM historical_jobs WHERE code=? AND currency=? AND end>=? AND start<=?',
                                (code, currency, first, last)).fetchall()
            for job in jobs:
                retry_allowed = retry and job['next_attempt'] - DAY < now - 60
                if job['status'] in ('queued', 'running') or (
                        job['status'] == 'failed' and job['next_attempt'] > now and not retry_allowed):
                    missing.difference_update(days_between(job['start'], job['end']))
            # 최대 1년씩 묶고, 보유하지 않은 긴 공백은 건너뛴다.
            batches = []
            for day in sorted(missing):
                if (not batches or (date.fromisoformat(day) - date.fromisoformat(batches[-1][0])).days >= 366
                        or (date.fromisoformat(day) - date.fromisoformat(batches[-1][1])).days > 7):
                    batches.append([day, day])
                else:
                    batches[-1][1] = day
            for start, end in batches:
                conn.execute('''INSERT INTO historical_jobs(code,currency,start,end) VALUES (?,?,?,?)
                    ON CONFLICT(code,currency,start,end) DO UPDATE SET status='queued', attempts=0,
                    next_attempt=0, lease_until=0, error='' ''', (code, currency, start, end))
        conn.commit()


def progress(needs):
    pending = failed = 0
    total = sum(len(days) for days in needs.values())
    ready = 0
    latest = 0
    errors = []
    with db_conn() as conn:
        conn.execute('BEGIN')
        for (code, currency), wanted in needs.items():
            if not wanted:
                continue
            first, last = min(wanted), max(wanted)
            rows = [r for r in conn.execute(
                'SELECT day,status,checked_at FROM historical_prices WHERE code=? AND currency=? AND day BETWEEN ? AND ?',
                (code, currency, first, last)) if r['day'] in wanted]
            valid = {r['day'] for r in rows if r['status'] == 'ok' and r['checked_at'] > 0}
            latest = max(latest, max((r['checked_at'] for r in rows), default=0))
            ready += len(valid & wanted)
            for row in conn.execute('''SELECT status,error,start,end FROM historical_jobs
                    WHERE code=? AND currency=? AND start<=? AND end>=?''', (code, currency, last, first)):
                if not any(row['start'] <= day <= row['end'] for day in wanted):
                    continue
                pending += row['status'] in ('queued', 'running')
                is_failed = row['status'] == 'failed' and any(
                    row['start'] <= day <= row['end'] for day in wanted - valid)
                failed += is_failed
                if is_failed and row['error']:
                    errors.append({'code': code, 'message': row['error']})
    return {'total': total, 'ready': ready, 'pending': pending, 'failed': failed,
            'errors': errors[:10], 'latestCheckedAt': latest}


def claim(*, now=None):
    now = time.time() if now is None else now
    with db_conn(write=True) as conn:
        # 모든 웹 워커를 합쳐 외부 수집은 한 번에 한 건이다.
        if conn.execute("SELECT 1 FROM historical_jobs WHERE status='running' AND lease_until>?", (now,)).fetchone():
            return None
        job = conn.execute('''SELECT * FROM historical_jobs WHERE
            (status='queued' AND next_attempt<=?) OR (status='running' AND lease_until<=?)
            ORDER BY id LIMIT 1''', (now, now)).fetchone()
        if job is None:
            return None
        lease = now + LEASE_SECONDS
        conn.execute("UPDATE historical_jobs SET status='running',attempts=attempts+1,lease_until=? WHERE id=?",
                     (lease, job['id']))
        conn.commit()
        return dict(job, attempts=job['attempts'] + 1, lease_until=lease)


def run_one():
    job = claim()
    if job is None:
        return False
    key = (job['code'], job['currency'])
    now = time.time()
    try:
        quotes, metadata = read([key], job['start'], job['end'])
        asset = metadata.get(key)
        if not asset or asset['checked_at'] < now - DAY:
            asset = dict(provider.fetch_asset(*key), checked_at=now)
        closes = provider.fetch_closes(asset, job['currency'], date.fromisoformat(job['start']),
                                       date.fromisoformat(job['end']))
        with db_conn(write=True) as conn:
            # lease를 잃은 오래된 워커는 새 결과를 덮어쓰지 않는다.
            current = conn.execute('SELECT status,lease_until FROM historical_jobs WHERE id=?', (job['id'],)).fetchone()
            if current['status'] != 'running' or current['lease_until'] != job['lease_until']:
                return True
            changed_splits = key in metadata and metadata[key]['splits'] != asset['splits']
            if changed_splits:
                conn.execute('UPDATE historical_prices SET checked_at=0 WHERE code=? AND currency=?', key)
            conn.execute('''INSERT INTO historical_assets(code,currency,symbol,timezone,splits,checked_at)
                VALUES (?,?,?,?,?,?) ON CONFLICT(code,currency) DO UPDATE SET symbol=excluded.symbol,
                timezone=excluded.timezone,splits=excluded.splits,checked_at=excluded.checked_at''',
                (*key, asset['symbol'], asset['timezone'], json.dumps(asset['splits']), asset['checked_at']))
            for day in days_between(job['start'], job['end']):
                close = closes.get(day)
                old = quotes[key].get(day)
                if close is None and old and old['checked_at'] > 0 and not changed_splits:
                    close = old['close']
                status = 'ok' if close is not None else (
                    'missing' if provider.is_session(date.fromisoformat(day), job['currency']) else 'closed')
                # 공급처의 일시적인 빈 응답으로 이미 검증한 종가를 삭제하지 않는다.
                conn.execute('''INSERT INTO historical_prices VALUES (?,?,?,?,?,?)
                    ON CONFLICT(code,currency,day) DO UPDATE SET close=excluded.close,
                    status=excluded.status,checked_at=excluded.checked_at''',
                    (*key, day, close, status, now))
            conn.execute("UPDATE historical_jobs SET status='done',lease_until=0,error='' WHERE id=?", (job['id'],))
            conn.commit()
    except Exception:
        log.warning('과거 시세 수집 실패: %s %s %s~%s', *key, job['start'], job['end'], exc_info=True)
        exhausted = job['attempts'] >= MAX_ATTEMPTS
        delay = DAY if exhausted else min(3600, 30 * 2 ** (job['attempts'] - 1))
        with db_conn(write=True) as conn:
            conn.execute('''UPDATE historical_jobs SET status=?,next_attempt=?,lease_until=0,error=?
                WHERE id=? AND status='running' AND lease_until=?''',
                ('failed' if exhausted else 'queued', now + delay,
                 '시세 제공처에서 데이터를 가져오지 못했습니다. 잠시 후 다시 확인해주세요.',
                 job['id'], job['lease_until']))
            conn.commit()
    return True


def watch(username):
    with db_conn(write=True) as conn:
        conn.execute('''INSERT OR IGNORE INTO historical_watchers(username)
            SELECT username FROM users WHERE username=?''', (username,))
        conn.commit()


def schedule_watchers():
    # import 순환을 피하고, 배경 스레드에서만 전체 사용자 목록을 순회한다.
    from app.services import journal, valuation
    with db_conn() as conn:
        usernames = [r[0] for r in conn.execute('''SELECT w.username FROM historical_watchers w
            JOIN users u ON u.username=w.username''')]
    for username in usernames:
        try:
            model = journal.snapshot(username)
            for currency in ('KRW', 'USD'):
                rows = [r for r in model['rows'] if r.get('type') == 'trade' and r['_settled']
                        and not r['_excluded'] and (r.get('currency') or 'KRW') == currency]
                identities = sorted({r['_identity'] for r in rows
                                     if provider.supports(r['_identity'], currency)})
                # 큰 일지도 요청 처리의 메모리 한도를 넘지 않게 100종목·1년씩 계획한다.
                for offset in range(0, len(identities), 100):
                    group = set(identities[offset:offset + 100])
                    subset = [r for r in rows if r['_identity'] in group]
                    first = max(date(1970, 1, 1), min(r['_day'].date() for r in subset))
                    last = provider.completed_day(currency)
                    while first <= last:
                        end = min(first + timedelta(days=365), last)
                        valuation.report(dict(model, rows=subset), journal.filters({'currency': currency}),
                                         first.isoformat(), end.isoformat(), schedule=True)
                        first = end + timedelta(days=1)
        except Exception:
            log.exception('과거 시세 정기 수집 예약 실패')


def worker():
    next_schedule = 0
    while True:
        try:
            if time.monotonic() >= next_schedule:
                schedule_watchers()
                next_schedule = time.monotonic() + 3600
            worked = run_one()
        except Exception:
            log.exception('과거 시세 워커 오류')
            worked = False
        time.sleep(1 if worked else 5)
