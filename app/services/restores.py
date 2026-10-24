"""사용자 백업 복원. HTTP 처리와 분리하여 DB·첨부 변경 경계를 한곳에서 관리한다."""

import hashlib
import json
from contextlib import contextmanager
import os
from pathlib import PurePosixPath
import shutil
import tempfile
import time
import zipfile
from datetime import date

from flask import current_app
from itsdangerous import BadSignature, URLSafeTimedSerializer

import config
from app.database import entry_logic
from app.database.db import db_conn
from app.domain.validation import normalize_restored_entry
from app.domain.trades import entry_order, parse_entry_dt
from app.services import accounts, images, realized_pnl, ledger, integrity
from app.services.users import user_dir
from app.utils import statscache
from app.utils.userlocks import serialized_user_change


class RestoreError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _read_rules(folder):
    path = os.path.join(folder, 'split_rules.json')
    if not os.path.exists(path):
        return []
    with open(path, encoding='utf-8') as f:
        rules = json.load(f)
    if not isinstance(rules, list):
        raise RestoreError('분할 설정 형식이 올바르지 않습니다.')
    seen = set()
    for row in rules:
        if (not isinstance(row, dict) or row.get('mode') not in ('apply', 'recorded')
                or any(not isinstance(row.get(k), str) or not row[k] for k in ('code', 'currency', 'day'))):
            raise RestoreError('분할 설정 형식이 올바르지 않습니다.')
        date.fromisoformat(row['day'])
        key = (row['code'], row['currency'], row['day'])
        if key in seen:
            raise RestoreError('분할 설정이 중복되었습니다.')
        seen.add(key)
    return rules


def _digest(stream):
    stream.seek(0)
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
        digest.update(chunk)
    stream.seek(0)
    return digest.hexdigest()


def _state(conn, username):
    owner = conn.execute('SELECT id,account_mappings FROM users WHERE username=?', (username,)).fetchone()
    rows = [tuple(r) for r in conn.execute(
        'SELECT id,revision FROM entries WHERE username=? ORDER BY id', (username,))]
    rules = [tuple(r) for r in conn.execute(
        'SELECT code,currency,day,mode FROM split_rules WHERE username=? ORDER BY code,currency,day', (username,))]
    # 분할 수집으로 잔고가 달라질 수 있으므로 공급처 분할 버전도 함께 검사한다.
    splits = [tuple(r) for r in conn.execute('SELECT code,currency,splits FROM historical_assets ORDER BY code,currency')]
    return hashlib.sha256(json.dumps([username, tuple(owner), rows, rules, splits]).encode()).hexdigest()


def _signer():
    return URLSafeTimedSerializer(current_app.secret_key, salt='restore-preview-v1')


def _check_preview(conn, username, digest, token):
    try:
        data = _signer().loads(token, max_age=600)
    except BadSignature as exc:
        raise RestoreError('복원 미리보기가 만료되었습니다. 파일을 다시 선택해주세요.', 409) from exc
    if data != {'digest': digest, 'state': _state(conn, username)}:
        raise RestoreError('미리보기 이후 데이터 또는 파일이 변경되었습니다. 다시 확인해주세요.', 409)


@serialized_user_change
def preview(username, stream):
    digest = _digest(stream)
    os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)
    with _workspace() as (folder, _):
        incoming, mappings = _extract(stream, folder)
        rules = _read_rules(folder)
        legacy = not os.path.exists(os.path.join(folder, 'manifest.json'))
    with db_conn() as conn:
        conn.execute('BEGIN')
        current = [dict(r) for r in conn.execute('SELECT * FROM entries WHERE username=?', (username,))]
        before = integrity.positions(ledger.prepare(conn, username, current))
        after = integrity.positions(ledger.prepare(conn, username, incoming, rules=rules))
        token = _signer().dumps({'digest': digest, 'state': _state(conn, username)})
        mapping_count = len(accounts.load(conn, username)['accounts'])
    changes = []
    for key in sorted(before.keys() | after.keys()):
        old, new = before[key], after[key]
        if abs(old['qty'] - new['qty']) > 1e-9 or abs(old['totalCost'] - new['totalCost']) > 1e-6:
            changes.append({'broker': key[0], 'account': key[1], 'currency': key[2], 'stock': key[3],
                                'beforeQuantity': old['qty'], 'afterQuantity': new['qty'],
                                'beforeCost': old['totalCost'], 'afterCost': new['totalCost']})
    return {'previewToken': token, 'beforeCount': len(current), 'afterCount': len(incoming),
                'beforeAccounts': mapping_count, 'afterAccounts': len(mappings.get('accounts', {})),
                'holdings': changes, 'legacy': legacy}


@contextmanager
def _workspace():
    path = tempfile.mkdtemp(prefix='.restore-', dir=config.UPLOAD_FOLDER)
    state = {'cleanup': True}
    try:
        yield path, state
    finally:
        # 되돌리기까지 실패하면 원본(retired)을 운영자가 복구할 수 있게 보존한다.
        if state['cleanup']:
            shutil.rmtree(path, ignore_errors=True)


def _extract(stream, folder):
    with zipfile.ZipFile(stream) as archive:
        members = archive.infolist()
        size = sum(info.file_size for info in members)
        if size > config.MAX_RESTORE_UNCOMPRESSED_BYTES:
            raise RestoreError('백업 파일의 압축 해제 크기가 너무 큽니다.', 413)
        for info in members:
            path = PurePosixPath(info.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in info.filename:
                raise RestoreError('백업에 안전하지 않은 파일 경로가 있습니다.')
        archive.extractall(folder)
    try:
        with open(os.path.join(folder, 'data.json'), encoding='utf-8') as f:
            rows = json.load(f)
    except FileNotFoundError as exc:
        raise RestoreError('손상된 백업 파일입니다. (data.json을 찾을 수 없습니다)') from exc
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise RestoreError('손상된 백업 파일입니다. (data.json은 기록 목록이어야 합니다)')
    for index, row in enumerate(rows):
        try:
            rows[index] = normalize_restored_entry(row)
        except ValueError as exc:
            raise RestoreError(f'백업의 {index + 1}번째 기록이 올바르지 않습니다. ({exc})') from exc
    manifest_path = os.path.join(folder, 'manifest.json')
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding='utf-8') as f:
            manifest = json.load(f)
        if not isinstance(manifest, dict) or manifest.get('formatVersion') != 2:
            raise RestoreError('지원하지 않는 백업 형식입니다.')
        if any(not os.path.exists(os.path.join(folder, name))
               for name in (accounts.BACKUP_ARCNAME, 'split_rules.json')):
            raise RestoreError('백업의 계좌 또는 분할 설정 파일이 누락되었습니다.')
    mappings = accounts.empty_mappings()
    path = os.path.join(folder, accounts.BACKUP_ARCNAME)
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            mappings = json.load(f)
        if not isinstance(mappings, dict) or any(
                key in mappings and not isinstance(mappings[key], dict) for key in ('accounts', 'brokers')):
            raise RestoreError('계좌 매핑 형식이 올바르지 않습니다.')
    return rows, mappings


@serialized_user_change
def restore_user_backup(username, stream, preview_token=None):
    digest = _digest(stream) if preview_token else None
    folder = user_dir(config.UPLOAD_FOLDER, username)
    if folder is None:
        raise RestoreError('계정 이름이 올바르지 않습니다.')
    os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)
    # 작업별 고유 경로이며, 원본과 같은 파일시스템 안에 있어 rename이 가능하다.
    with _workspace() as (work, state):
        incoming = os.path.join(work, 'incoming')
        staging = os.path.join(work, 'staging')
        retired = os.path.join(work, 'retired')
        os.mkdir(incoming)
        rows, mappings = _extract(stream, incoming)
        rules = _read_rules(incoming)
        os.mkdir(staging)
        uploaded = os.path.join(incoming, 'uploads')
        if os.path.isdir(uploaded):
            for name in os.listdir(uploaded):
                source = os.path.join(uploaded, name)
                if os.path.isfile(source):
                    shutil.copy2(source, os.path.join(staging, name))
        files = set(os.listdir(staging))
        prepared = [images.extract_inline_images(
            username, images.rehome_upload_urls(username, row, files), target_folder=staging)
            for row in rows]
        # 원래 체결 순서·날짜를 DB ID 재배정과 분리한다. 중복 ID가 있던 옛 백업도 안정 정렬한다.
        prepared.sort(key=entry_order)
        for sequence, row in enumerate(prepared, 1):
            stamp = parse_entry_dt(row)
            if stamp and not row.get('rawDate'):
                row['rawDate'] = stamp.isoformat()
            row['tradeSequence'] = sequence
        old_moved = new_moved = False
        with db_conn(write=True) as conn:
            try:
                if preview_token:
                    _check_preview(conn, username, digest, preview_token)
                c = conn.cursor()
                previous_count = c.execute('SELECT COUNT(*) FROM entries WHERE username=?', (username,)).fetchone()[0]
                c.execute('DELETE FROM split_rules WHERE username=?', (username,))
                c.executemany('INSERT INTO split_rules VALUES (?,?,?,?,?)',
                              [(username, r['code'], r['currency'], r['day'], r['mode']) for r in rules])
                c.execute('DELETE FROM entries WHERE username = ?', (username,))
                taken = {r[0] for r in c.execute('SELECT id FROM entries')}
                # 뒤에 나올 원본 ID와 재배정 ID가 겹치지 않게 먼저 예약한다.
                reserved = taken | {r['id'] for r in prepared if isinstance(r.get('id'), int)
                                    and not isinstance(r['id'], bool) and 0 < r['id'] < 2**53}
                next_id = max(reserved | {int(time.time() * 1000)})
                for row in prepared:
                    entry_id = row.get('id')
                    if (not isinstance(entry_id, int) or isinstance(entry_id, bool)
                            or not 0 < entry_id < 2**53 or entry_id in taken):
                        next_id += 1
                        row = dict(row, id=next_id)
                    taken.add(row['id'])
                    entry_logic.insert_entry(c, username, row)
                realized_pnl.refresh(conn, username)
                if mappings is not None:
                    accounts.save(conn, username, mappings)
                c.execute("""INSERT INTO entry_changes(username,action,origin,before_json,after_json)
                    VALUES (?,'restore','web',?,?)""", (username, json.dumps({'recordCount': previous_count}),
                    json.dumps({'recordCount': len(prepared), 'accountCount': len(mappings.get('accounts', {})),
                                'splitRuleCount': len(rules)})))
                state['cleanup'] = False
                if os.path.exists(folder):
                    os.rename(folder, retired)
                    old_moved = True
                os.rename(staging, folder)
                new_moved = True
                conn.commit()
                state['cleanup'] = True
            except BaseException:
                conn.rollback()
                if new_moved:
                    shutil.rmtree(folder)
                if old_moved:
                    os.rename(retired, folder)
                state['cleanup'] = True
                raise
        # 확정 이후의 정리 실패를 '복원 실패'로 오인하지 않도록 정리는 최선 노력이다.
        statscache.invalidate(username)
        shutil.rmtree(retired, ignore_errors=True)
    return len(prepared)
