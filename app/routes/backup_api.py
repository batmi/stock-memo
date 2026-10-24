"""백업 다운로드·복원 HTTP 처리. 실제 변경 경계는 restores 서비스가 소유한다."""

import logging
import tempfile
import time
import zipfile

from flask import Blueprint, jsonify, request, send_file, session

import config
from app.services import backups, restores

log = logging.getLogger('backup_api')
bp = Blueprint('backup_api', __name__)


def register(app):
    app.register_blueprint(bp)


@bp.get('/api/backup')
def full_backup():
    username = session.get('username')
    if not username:
        return jsonify(error='로그인이 필요합니다.'), 401
    target = tempfile.TemporaryFile()
    try:
        backups.write_user_backup(target, username)
        size = target.tell()
        target.seek(0)
        filename = f'TradingJournal_backup_{username}_{time.strftime("%Y%m%d_%H%M%S")}.zip'
        response = send_file(target, mimetype='application/zip', download_name=filename, as_attachment=True)
        response.content_length = size
        response.call_on_close(target.close)
        response.headers['Access-Control-Expose-Headers'] = 'Content-Disposition'
        return response
    except BaseException:
        target.close()
        raise


@bp.post('/api/restore/preview')
@bp.post('/api/restore')
def full_restore():
    username = session.get('username')
    if not username:
        return jsonify(error='로그인이 필요합니다.'), 401
    # Flask 3.1의 요청별 한도. 다른 JSON/폼 업로드는 기존 16MiB 제한을 유지한다.
    request.max_content_length = config.MAX_RESTORE_UPLOAD_BYTES
    if 'file' not in request.files:
        return jsonify(error='업로드된 파일이 없습니다.'), 400
    file = request.files['file']
    if not file.filename or not file.filename.lower().endswith('.zip'):
        return jsonify(error='유효하지 않은 파일입니다. .zip 백업 파일을 업로드해주세요.'), 400
    try:
        if request.path.endswith('/preview'):
            return jsonify(restores.preview(username, file.stream))
        restores.restore_user_backup(username, file.stream, request.form.get('previewToken'))
    except restores.RestoreError as exc:
        return jsonify(error=str(exc)), exc.status
    except (zipfile.BadZipFile, ValueError):
        log.warning('복원 실패(손상된 백업): username=%s', username, exc_info=True)
        return jsonify(error='손상된 백업 파일입니다. (ZIP 또는 JSON을 읽을 수 없습니다)'), 400
    except Exception:
        log.exception('복원 실패: username=%s', username)
        return jsonify(error='복원 중 서버 오류가 발생했습니다. 데이터를 확인한 뒤 다시 시도해주세요.'), 500
    return jsonify(status='success')
