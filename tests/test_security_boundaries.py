"""계정 간 파일 격리, 브라우저 요청 위조, 계정 복구의 권한 경계 회귀."""

import base64
import io
import json
from pathlib import Path
import re
import zipfile

from flask.testing import FlaskClient
import pytest

import config
from app.database.db import db_conn
from app.services import storage
from app.services.users import user_dir
from helpers import _ensure_user, _login, _signup_and_login


def _csrf(client):
    response = client.get('/login')
    return re.search(r'name="csrf_token" value="([^"]+)"', response.text)[1]


def _attachment(client, payload):
    encoded = base64.b64encode(payload).decode()
    assert client.post('/api/entry', json={
        'type': 'memo', 'thoughts': f'<img src="data:image/png;base64,{encoded}">'}).status_code == 200
    response = client.get('/api/backup')
    try:
        with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
            return {name: archive.read(name) for name in archive.namelist() if name.startswith('uploads/')}
    finally:
        response.close()


def test_case_variant_accounts_cannot_read_or_delete_each_others_files(app):
    owner, alias = app.test_client(), app.test_client()
    _signup_and_login(owner, 'reviewowner', 'OwnerPassword123!')
    _signup_and_login(alias, 'ReviewOwner', 'AliasPassword123!')
    original = _attachment(owner, b'private-owner-image')
    other = _attachment(alias, b'private-alias-image')
    assert list(original.values()) == [b'private-owner-image']
    assert list(other.values()) == [b'private-alias-image']
    assert Path(user_dir(config.UPLOAD_FOLDER, 'reviewowner')).resolve() != Path(
        user_dir(config.UPLOAD_FOLDER, 'ReviewOwner')).resolve()
    filename = next(iter(original)).split('/')[-1]
    assert alias.get(f'/uploads/ReviewOwner/{filename}').status_code == 404
    assert alias.get(f'/uploads/reviewowner/{filename}').status_code == 403
    assert alias.delete('/api/account', json={'password': 'AliasPassword123!'}).status_code == 200
    response = owner.get(f'/uploads/reviewowner/{filename}')
    assert response.status_code == 200 and response.data == b'private-owner-image'
    response.close()


def test_storage_migration_preserves_files_urls_and_is_restartable(client):
    _login(client, 'legacyowner')
    for base in (config.UPLOAD_FOLDER, config.BACKUP_DIR):
        folder = Path(base, 'legacyowner')
        folder.mkdir()
        (folder / 'private.txt').write_text('preserved')
    storage.migrate_user_storage()
    storage.migrate_user_storage()
    for base in (config.UPLOAD_FOLDER, config.BACKUP_DIR):
        assert not Path(base, 'legacyowner').exists()
        assert Path(user_dir(base, 'legacyowner'), 'private.txt').read_text() == 'preserved'
    response = client.get('/uploads/legacyowner/private.txt')
    assert response.status_code == 200 and response.data == b'preserved'
    response.close()


def test_ambiguous_legacy_storage_is_preserved_without_assigning_an_owner(app):
    _ensure_user('sharedowner')
    _ensure_user('SharedOwner')
    folder = Path(config.UPLOAD_FOLDER, 'sharedowner')
    folder.mkdir()
    (folder / 'private.txt').write_text('do not guess')
    if not Path(config.UPLOAD_FOLDER, 'SharedOwner').exists():
        pytest.skip('대소문자를 구분하지 않는 파일시스템에서 발생하는 충돌')
    with pytest.raises(RuntimeError, match='저장소가 겹칩니다'):
        storage.migrate_user_storage()
    assert (folder / 'private.txt').read_text() == 'do not guess'
    assert not Path(user_dir(config.UPLOAD_FOLDER, 'sharedowner')).exists()
    assert not Path(user_dir(config.UPLOAD_FOLDER, 'SharedOwner')).exists()


def test_storage_does_not_reuse_files_after_delete_and_reregister(client):
    _login(client, 'recycled')
    old = user_dir(config.BACKUP_DIR, 'recycled')
    with db_conn(write=True) as conn:
        conn.execute('DELETE FROM users WHERE username=?', ('recycled',))
        conn.commit()
    _ensure_user('recycled')
    assert user_dir(config.BACKUP_DIR, 'recycled') != old


def test_login_requires_session_bound_csrf_and_rejects_foreign_origin(app, client):
    _signup_and_login(client, 'csrfuser', 'Password123!')
    raw = FlaskClient(app, app.response_class)
    data = {'username': 'csrfuser', 'password': 'Password123!'}
    assert raw.post('/login', data=data).status_code == 403
    token = _csrf(raw)
    assert raw.post('/login', data={**data, 'csrf_token': token},
                    headers={'Origin': 'https://attacker.test'}).status_code == 403
    other = FlaskClient(app, app.response_class)
    assert other.post('/login', data={**data, 'csrf_token': token}).status_code == 403
    assert raw.post('/login', data={**data, 'csrf_token': token},
                    headers={'Origin': 'http://localhost'}).status_code == 302
    assert raw.get('/api/me').json['username'] == 'csrfuser'
    assert raw.post('/api/ping', headers={'X-CSRF-Token': token}).status_code == 403


@pytest.mark.parametrize('origin', ['http://untrusted.localhost', 'https://localhost', 'http://localhost:9000', 'null'])
def test_admin_change_rejects_other_origins_even_with_a_valid_token(client, origin):
    _signup_and_login(client, 'csrfadmin', 'Password123!')
    _ensure_user('targetuser')
    response = client.post('/api/admin/users/targetuser/toggle_allow', headers={'Origin': origin})
    assert response.status_code == 403
    with db_conn() as conn:
        assert conn.execute("SELECT is_allowed FROM users WHERE username='targetuser'").fetchone()[0] == 1


def test_logout_get_and_unprotected_post_cannot_end_session(app, client):
    _signup_and_login(client, 'logoutuser', 'Password123!')
    assert client.get('/logout').status_code == 200
    assert client.get('/api/me').status_code == 200
    raw = FlaskClient(app, app.response_class)
    raw.set_cookie('session', client.get_cookie('session').value)
    assert raw.post('/logout').status_code == 403
    assert raw.get('/api/me').status_code == 200
    assert client.post('/logout').status_code == 302
    assert client.get('/api/me').status_code == 401


def _reset_setup(app):
    admin, user = app.test_client(), app.test_client()
    _signup_and_login(admin, 'resetadmin', 'AdminPassword123!')
    _signup_and_login(user, 'resetuser', 'UserPassword123!')
    key = user.post('/api/me/api-key', json={}).json['api_key']
    token = user.post('/api/v1/auth/token', json={'api_key': key}).json['access_token']
    return admin, user, key, {'Authorization': 'Bearer ' + token}


def test_password_reset_revokes_old_api_credentials_by_default(app):
    admin, user, key, headers = _reset_setup(app)
    reset = admin.post('/api/admin/users/resetuser/reset_password')
    assert reset.status_code == 200 and reset.json['api_keys_revoked'] == 1
    assert user.get('/api/me').status_code == 401
    assert user.post('/api/v1/auth/token', json={'api_key': key}).status_code == 401
    assert user.get('/api/v1/trades', headers=headers).status_code == 401


def test_temporary_password_allows_only_password_change_and_logout(app):
    admin, user, key, headers = _reset_setup(app)
    reset = admin.post('/api/admin/users/resetuser/reset_password', json={'revoke_api_keys': False})
    temporary = reset.json['new_password']
    assert user.post('/login', data={'username': 'resetuser', 'password': temporary}).status_code == 302
    assert user.get('/').location.endswith('/change-required')
    assert user.get('/change-required').status_code == 200
    assert user.get('/api/me').json['must_change_password'] is True
    for path, method in [('/api/data', 'GET'), ('/api/backup', 'GET'), ('/api/me/api-key', 'POST'),
                         ('/api/entry', 'POST'), ('/api/admin/users', 'GET')]:
        response = user.open(path, method=method, json={})
        assert response.status_code == 403 and response.json['errorCode'] == 'PASSWORD_CHANGE_REQUIRED'
    assert user.post('/api/v1/auth/token', json={'api_key': key}).status_code == 403
    assert user.get('/api/v1/trades', headers=headers).status_code == 403
    changed = user.post('/api/change_password', json={
        'current_password': temporary, 'new_password': 'ChangedPassword123!'})
    assert changed.status_code == 200
    assert user.get('/api/data').status_code == 200
    assert user.get('/api/v1/trades', headers=headers).status_code == 200


def test_restore_rejects_missing_csrf_before_reading_a_multipart_body(app, client):
    _signup_and_login(client, 'restorecsrf', 'Password123!')
    raw = FlaskClient(app, app.response_class)
    raw.set_cookie('session', client.get_cookie('session').value)
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr('data.json', json.dumps([]))
    data.seek(0)
    assert raw.post('/api/restore', data={'file': (data, 'backup.zip')}).status_code == 403


def test_migration_checks_all_destinations_before_moving_files(app):
    _ensure_user('legacyowner')
    for base in (config.UPLOAD_FOLDER, config.BACKUP_DIR):
        source = Path(base, 'legacyowner')
        source.mkdir()
        (source / 'original.txt').write_text('original')
    target = Path(user_dir(config.BACKUP_DIR, 'legacyowner'))
    target.mkdir(parents=True)
    (target / 'new.txt').write_text('new')
    with pytest.raises(RuntimeError, match='모두 존재'):
        storage.migrate_user_storage()
    for base in (config.UPLOAD_FOLDER, config.BACKUP_DIR):
        assert Path(base, 'legacyowner', 'original.txt').read_text() == 'original'
    assert (target / 'new.txt').read_text() == 'new'
    assert not Path(user_dir(config.UPLOAD_FOLDER, 'legacyowner')).exists()


def test_id_storage_rejects_symlink_to_another_account(app):
    _ensure_user('owner')
    _ensure_user('alias')
    owner = Path(user_dir(config.UPLOAD_FOLDER, 'owner'))
    alias = Path(user_dir(config.UPLOAD_FOLDER, 'alias'))
    owner.mkdir(parents=True)
    (owner / 'private.txt').write_text('private')
    alias.symlink_to(owner, target_is_directory=True)
    with pytest.raises(ValueError, match='심볼릭 링크'):
        user_dir(config.UPLOAD_FOLDER, 'alias')
    assert (owner / 'private.txt').read_text() == 'private'


@pytest.mark.parametrize('committed', [False, True])
def test_interrupted_legacy_restore_recovers_before_storage_migration(app, committed):
    from app.services import restore_recovery

    _ensure_user('legacyrestore')
    with db_conn() as conn:
        user_id = conn.execute("SELECT id FROM users WHERE username='legacyrestore'").fetchone()[0]
    work = Path(config.UPLOAD_FOLDER, '.restore-legacy')
    retired = work / 'retired'
    retired.mkdir(parents=True)
    (retired / 'attachment.txt').write_text('old')
    folder = Path(config.UPLOAD_FOLDER, 'legacyrestore')
    folder.mkdir()
    (folder / 'attachment.txt').write_text('new')
    (work / restore_recovery.JOURNAL).write_text(json.dumps({
        'operation': work.name, 'username': 'legacyrestore', 'userId': user_id, 'hadFolder': True}))
    if committed:
        with db_conn(write=True) as conn:
            conn.execute('INSERT INTO restore_commits(operation_id, username, user_id) VALUES (?,?,?)',
                         (work.name, 'legacyrestore', user_id))
            conn.commit()
    restore_recovery.recover_pending_restores()
    storage.migrate_user_storage()
    assert not work.exists() and not folder.exists()
    actual = Path(user_dir(config.UPLOAD_FOLDER, 'legacyrestore'), 'attachment.txt').read_text()
    assert actual == ('new' if committed else 'old')


@pytest.mark.parametrize('keep_keys', [False, True])
@pytest.mark.parametrize('temporary', [False, True])
def test_cli_reset_invalidates_existing_sessions_and_credentials(app, monkeypatch, tmp_path, keep_keys, temporary):
    from tools import reset_password

    _, user, key, headers = _reset_setup(app)
    password = 'ChangedPassword123!'
    monkeypatch.setattr(reset_password, 'AUDIT_LOG', str(tmp_path / 'recovery.log'))
    monkeypatch.setattr(reset_password, 'read_new_password', lambda: password)
    monkeypatch.setattr(reset_password, 'generate_password', lambda: password)
    monkeypatch.setattr('builtins.input', lambda _: 'y')
    args = ['reset_password.py', '--db', config.DB_FILE, '--user', 'resetuser']
    if temporary:
        args.append('--random')
    if keep_keys:
        args.append('--keep-api-keys')
    monkeypatch.setattr('sys.argv', args)
    reset_password.main()
    assert user.get('/api/me').status_code == 401
    expected_status = (403 if temporary else 200) if keep_keys else 401
    assert user.post('/api/v1/auth/token', json={'api_key': key}).status_code == expected_status
    assert user.get('/api/v1/trades', headers=headers).status_code == expected_status
    assert user.post('/login', data={'username': 'resetuser', 'password': password}).status_code == 302
    assert user.get('/api/data').status_code == (403 if temporary else 200)
    assert password not in (tmp_path / 'recovery.log').read_text()
