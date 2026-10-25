"""계정 백업의 기업행사 스냅샷. 공유 시세를 수정하지 않고 계정 안에서 복원한다."""

from datetime import date, datetime
import json
import math
from pathlib import Path
import re

from app.domain.trades import KST, currency_of, stock_identity
from app.services import ledger

FILENAME = 'corporate_actions.json'


def export(conn, username, rows):
    prepared = ledger.prepare(conn, username, rows)
    keys = {(stock_identity(r), currency_of(r)) for r in prepared
            if r.get('type') == 'trade' and stock_identity(r)}
    last = datetime.now(KST).date().isoformat()
    return {'asOf': last, 'assets': [{'code': code, 'currency': currency,
        'splits': {day: ratio for day, ratio in events.items() if day <= last}}
        for (code, currency), events in sorted(ledger.split_events(conn, username, keys).items())]}


def read(folder):
    path = Path(folder, FILENAME)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(data, dict) or not isinstance(data.get('asOf'), str)
            or not isinstance(data.get('assets'), list) or len(data['assets']) > 10000):
        raise ValueError('기업행사 스냅샷 형식이 올바르지 않습니다.')
    cutoff = date.fromisoformat(data['asOf'])
    if cutoff.isoformat() != data['asOf'] or cutoff > datetime.now(KST).date():
        raise ValueError('기업행사 스냅샷의 기준일이 미래입니다.')
    seen = set()
    for item in data['assets']:
        if (not isinstance(item, dict) or not isinstance(item.get('code'), str) or not 0 < len(item['code']) <= 200
                or not isinstance(item.get('currency'), str) or not re.fullmatch('[A-Z]{3}', item['currency'])
                or not isinstance(item.get('splits'), dict) or len(item['splits']) > 2000):
            raise ValueError('기업행사 종목 정보가 올바르지 않습니다.')
        key = item['code'], item['currency']
        if key in seen:
            raise ValueError('기업행사 종목 정보가 중복되었습니다.')
        seen.add(key)
        for day, ratio in item['splits'].items():
            if (date.fromisoformat(day).isoformat() != day or date.fromisoformat(day) > cutoff or type(ratio) not in (int, float)
                    or not math.isfinite(ratio) or not 0 < ratio <= 1e9):
                raise ValueError('기업행사 날짜 또는 비율이 올바르지 않습니다.')
    return data


def replace(conn, username, snapshot):
    conn.execute('DELETE FROM user_split_snapshots WHERE username=?', (username,))
    if snapshot is not None:
        conn.executemany('INSERT INTO user_split_snapshots VALUES (?,?,?,?,?)',
            [(username, r['code'], r['currency'], snapshot['asOf'], json.dumps(r['splits']))
             for r in snapshot['assets']])
