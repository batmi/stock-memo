"""사용자 백업 복원. HTTP 처리와 분리하여 DB·첨부 변경 경계를 한곳에서 관리한다."""

import json
from contextlib import contextmanager
import os
from pathlib import PurePosixPath
import shutil
import tempfile
import time
import zipfile

import config
from app.database import entry_logic
from app.database.db import db_conn
from app.services import accounts, images
from app.services.users import user_dir
from app.utils import statscache
from app.utils.userlocks import serialized_user_change


class RestoreError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


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
    mappings = None
    path = os.path.join(folder, accounts.BACKUP_ARCNAME)
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            mappings = json.load(f)
        if not isinstance(mappings, dict) or any(
                key in mappings and not isinstance(mappings[key], dict) for key in ('accounts', 'brokers')):
            raise RestoreError('계좌 매핑 형식이 올바르지 않습니다.')
    return rows, mappings


@serialized_user_change
def restore_user_backup(username, stream):
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
        old_moved = new_moved = False
        with db_conn(write=True) as conn:
            try:
                c = conn.cursor()
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
                if mappings is not None:
                    accounts.save(conn, username, mappings)
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
