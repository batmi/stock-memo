"""DB 커밋 표식과 파일 작업 일지로 중단된 복원을 다음 기동에 정리한다."""

import json
import logging
import os
from pathlib import Path
import shutil

import config
from app.database.db import db_conn
from app.services.users import user_dir
from app.utils import statscache
from app.utils.userlocks import user_lock
from werkzeug.exceptions import ServiceUnavailable

log = logging.getLogger(__name__)
JOURNAL = 'restore-state.json'
_blocked_accounts = set()


def block_pending(work):
    """실행 중 되돌리기까지 실패하면 복구 전의 후속 읽기·변경을 차단한다."""
    path = Path(work) / JOURNAL
    if path.exists():
        with path.open(encoding='utf-8') as source:
            state = json.load(source)
        _blocked_accounts.add((config.DB_FILE, state['username']))


def assert_account_ready(username):
    if (config.DB_FILE, username) in _blocked_accounts:
        raise ServiceUnavailable('백업 복원 복구가 필요합니다. 서버를 재시작한 후 다시 시도해주세요.')


def prepare(work, conn, username, had_folder):
    owner = conn.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone()
    operation = os.path.basename(work)
    state = {'operation': operation, 'username': username, 'userId': owner['id'],
             'hadFolder': had_folder, 'storageVersion': 2}
    # 파일 교체보다 먼저 디스크에 남긴다. DB 표식은 원본 데이터와 함께 커밋된다.
    with open(os.path.join(work, JOURNAL), 'x', encoding='utf-8') as out:
        json.dump(state, out)
        out.flush()
        os.fsync(out.fileno())
    conn.execute('INSERT INTO restore_commits(operation_id,username,user_id) VALUES (?,?,?)',
                 (operation, username, owner['id']))
    return operation


def forget(operation):
    with db_conn(write=True) as conn:
        conn.execute('DELETE FROM restore_commits WHERE operation_id=?', (operation,))
        conn.commit()


def rollback_files(work, folder, had_folder):
    """이름 변경 직후 중단되어 Python 플래그가 갱신되지 않았어도 원본을 복구한다."""
    work = Path(work)
    retired, staging = work / 'retired', work / 'staging'
    if retired.exists():
        if os.path.exists(folder):
            shutil.rmtree(folder)
        os.rename(str(retired), folder)
    elif not had_folder and not staging.exists() and os.path.exists(folder):
        shutil.rmtree(folder)


def recover_pending_restores():
    """리슨/백그라운드 작업 시작 전에 호출한다. 복구 실패 시 기동을 중단한다.

    표식 있음: 새 DB가 확정되었으므로 새 첨부 유지. 표식 없음: 구 DB이므로
    retired를 복귀한다. 반복 실행해도 이미 복구한 원본을 삭제하지 않는다.
    """
    base = Path(config.UPLOAD_FOLDER)
    if not base.exists():
        return
    for work in sorted(base.glob('.restore-*')):
        if not work.is_dir() or work.is_symlink():
            continue
        journal = work / JOURNAL
        if not journal.exists():
            # 준비/미리보기 도중 중단된 작업은 아직 원본을 건드리지 않았다.
            # 이전 버전이 남긴 retired는 운영자 복구를 위해 그대로 보존한다.
            if not (work / 'retired').exists():
                shutil.rmtree(work)
            continue
        with journal.open(encoding='utf-8') as source:
            state = json.load(source)
        username = state['username']
        if state['operation'] != work.name or state.get('storageVersion', 1) not in (1, 2):
            raise ValueError('복원 작업 일지의 계정 또는 작업 ID가 올바르지 않습니다.')
        with user_lock(username):
            with db_conn() as conn:
                owner = conn.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone()
                marker = conn.execute('SELECT username,user_id FROM restore_commits WHERE operation_id=?',
                                      (work.name,)).fetchone()
            same_owner = owner is not None and owner['id'] == state['userId']
            if same_owner:
                if state.get('storageVersion', 1) == 1:
                    from app.services.storage import legacy_user_dir
                    with db_conn() as conn:
                        owners = [row['username'] for row in conn.execute('SELECT username FROM users')]
                    folder = legacy_user_dir(str(base), username, owners)
                else:
                    folder = user_dir(str(base), username, user_id=owner['id'])
                if folder is None:
                    raise ValueError('복원 작업 일지의 계정이 올바르지 않습니다.')
                committed = marker is not None and tuple(marker) == (username, state['userId'])
                if committed:
                    if not os.path.isdir(folder):
                        raise RuntimeError(f'확정된 복원의 첨부 폴더가 없습니다: {work.name}')
                else:
                    rollback_files(work, folder, state['hadFolder'])
                statscache.invalidate(username)
            # 동일 이름의 새 계정 폴더에는 이전 작업의 첨부를 쓰지 않는다.
            shutil.rmtree(work)
            forget(work.name)
            _blocked_accounts.discard((config.DB_FILE, username))
            log.info('중단된 백업 복원 작업 정리: %s', work.name)
    # 파일 정리 직후 표식 삭제 전에 중단된 작업의 DB 표식만 제거한다.
    with db_conn(write=True) as conn:
        for row in conn.execute('SELECT operation_id FROM restore_commits').fetchall():
            if not (base / row['operation_id']).exists():
                conn.execute('DELETE FROM restore_commits WHERE operation_id=?', (row['operation_id'],))
        conn.commit()
