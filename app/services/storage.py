"""사용자명 기반 파일 저장소를 계정 ID 기반 저장소로 이관한다."""

import logging
import os

import config
from app.database.db import db_conn
from app.services.users import is_valid_username, user_dir

log = logging.getLogger(__name__)


def legacy_user_dir(base, username, owners):
    """옛 폴더의 소유자가 유일한지 확인한다. 섞인 파일은 추측해서 배분하지 않는다."""
    if not is_valid_username(username):
        return None
    path = os.path.join(base, username)
    if os.path.islink(path):
        raise RuntimeError(f'사용자 저장소 이관 전 심볼릭 링크를 확인해주세요: {path}')
    if os.path.isdir(path):
        for other in owners:
            if other == username or not is_valid_username(other):
                continue
            candidate = os.path.join(base, other)
            if os.path.exists(candidate) and os.path.samefile(path, candidate):
                raise RuntimeError(
                    f'사용자 저장소가 겹칩니다: {username!r}, {other!r}. '
                    f'원본을 보존했습니다. {path}의 소유자를 확인한 후 다시 시작해주세요.')
    return path


def migrate_user_storage():
    """리슨 전에 수행한다. rename이 원자적이므로 중단 후 다시 실행해도 안전하다."""
    with db_conn() as conn:
        owners = {row['username']: row['id'] for row in conn.execute('SELECT id,username FROM users')}
    moves = []
    for base in (config.UPLOAD_FOLDER, config.BACKUP_DIR):
        for username, user_id in owners.items():
            source = legacy_user_dir(base, username, owners)
            target = user_dir(base, username, user_id=user_id)
            if source is None or not os.path.isdir(source):
                continue
            if os.path.exists(target):
                raise RuntimeError(f'이관할 원본과 새 사용자 폴더가 모두 존재합니다: {source}, {target}')
            moves.append((source, target))
    # 충돌은 파일을 하나라도 옮기기 전에 전부 검사한다.
    for source, target in moves:
        os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
        os.rename(source, target)
        log.info('사용자 저장소 이관: %s → %s', source, target)
