"""백업·복원 라우트 — 전체 데이터를 ZIP 한 벌로 내보내고 되돌린다.

이 두 라우트만 파일시스템을 통째로 갈아엎는다(첨부 폴더 교체, DB 행 전체 치환).
잘못되면 사용자의 기록이 사라지는 유일한 경로라, 라우트 200줄이 다른 API 사이에
섞여 있는 것보다 따로 두고 통째로 읽을 수 있는 편이 안전하다.

복원의 핵심 규칙 두 가지:
  1) 기존 첨부 폴더를 먼저 지우지 않는다. 새 폴더를 옆에 완성한 뒤 rename 두 번으로
     맞바꾼다. 예전에는 rmtree 후 복사라, 복사가 도중에 실패하면 첨부파일이
     영구 소실되고 되돌릴 방법이 없었다.
  2) 압축 해제 후 크기를 먼저 합산해 상한을 넘으면 거부한다 (zip bomb).
"""

import io
import json
import logging
import os
import shutil
import tempfile
import time
import zipfile

from flask import Blueprint, jsonify, request, send_file, session

import accounts
import config
import entry_logic
import images
import statscache
from db import db_conn
from users import user_dir

log = logging.getLogger('backup_api')

bp = Blueprint('backup_api', __name__)


def register(app):
    app.register_blueprint(bp)


@bp.route('/api/backup', methods=['GET'])
def full_backup():
    """DB와 업로드 이미지를 포함한 전체 폴더를 압축하여 다운로드 제공"""
    username = session.get('username')
    if not username:
        return jsonify({"error": "로그인이 필요합니다."}), 401
    with db_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM entries WHERE username = ?", (username,))
        rows = [dict(row) for row in c.fetchall()]
        user_mappings = accounts.load(conn, username)

    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
        # 1. 사용자 데이터를 JSON으로 백업
        json_data = json.dumps(rows, ensure_ascii=False, indent=2)
        zf.writestr('data.json', json_data)

        # 2. 사용자 이미지 폴더 백업
        user_folder = user_dir(config.UPLOAD_FOLDER, username)
        if user_folder and os.path.exists(user_folder):
            for root, _dirs, files in os.walk(user_folder):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.join('uploads', file)
                    zf.write(file_path, arcname=arcname)
                    
        # 3. 사용자 매핑 정보 백업 (DB → ZIP, 파일명은 구버전과 동일하게 유지)
        if user_mappings.get('brokers') or user_mappings.get('accounts'):
            zf.writestr(accounts.BACKUP_ARCNAME, accounts.dumps(user_mappings))

    memory_file.seek(0)

    # 파일명에 현재 날짜와 시간 추가 (예: TradingJournal_backup_20231027_153000.zip)
    current_time = time.strftime('%Y%m%d_%H%M%S')
    filename = f'TradingJournal_backup_{username}_{current_time}.zip'

    response = send_file(memory_file, mimetype='application/zip', download_name=filename, as_attachment=True)
    response.headers["Access-Control-Expose-Headers"] = "Content-Disposition"
    return response


@bp.route('/api/restore', methods=['POST'])
def full_restore():
    """백업 받은 ZIP 파일을 해제하여 DB 및 업로드 이미지를 완벽 원복"""
    username = session.get('username')
    if not username:
        return jsonify({"error": "로그인이 필요합니다."}), 401
    if 'file' not in request.files:
        return jsonify({'error': '업로드된 파일이 없습니다.'}), 400

    file = request.files['file']
    if not file.filename or not file.filename.endswith('.zip'):
        return jsonify({'error': '유효하지 않은 파일입니다. .zip 백업 파일을 업로드해주세요.'}), 400

    temp_dir = tempfile.mkdtemp()
    try:
        with zipfile.ZipFile(file.stream, 'r') as zf:
            # ⭐️ 해제 후 크기를 먼저 확인한다. ZIP 은 압축률이 매우 높을 수 있어
            #    업로드 크기 제한(MAX_CONTENT_LENGTH)만으로는 디스크가 가득 차는 것을
            #    막지 못한다. (라즈베리파이처럼 저장공간이 작으면 실수로도 발생한다)
            total_size = sum(info.file_size for info in zf.infolist())
            if total_size > config.MAX_RESTORE_UNCOMPRESSED_BYTES:
                return jsonify({'error':
                    f'백업 파일의 압축 해제 크기가 너무 큽니다. '
                    f'(약 {total_size // (1024 * 1024)}MB, 최대 '
                    f'{config.MAX_RESTORE_UNCOMPRESSED_BYTES // (1024 * 1024)}MB)'}), 413
            zf.extractall(temp_dir)

        json_path = os.path.join(temp_dir, 'data.json')
        if not os.path.exists(json_path):
            return jsonify({'error': '손상된 백업 파일입니다. (data.json을 찾을 수 없습니다)'}), 400

        with open(json_path, 'r', encoding='utf-8') as f:
            entries = json.load(f)

        # ⭐️ 형식을 먼저 확인한다. 리스트가 아니면 아래 루프가 엉뚱한 값을 순회하다
        #    500 과 불투명한 메시지로 끝나, 사용자는 원인을 알 수 없었다.
        if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
            return jsonify({'error':
                '손상된 백업 파일입니다. (data.json 이 기록 목록 형식이 아닙니다)'}), 400

        with db_conn() as conn:
            c = conn.cursor()

            # 1. 기존 사용자의 데이터만 삭제
            c.execute("DELETE FROM entries WHERE username = ?", (username,))

            # 2. 복원할 데이터 삽입 (구버전 백업의 본문 내장 base64 이미지도 파일로 추출)
            #
            # ⭐️ entries.id 는 전역 PRIMARY KEY 인데 위 DELETE 는 '이 계정의 행'만
            #    지운다. 그래서 백업 안의 id 가 다른 계정의 기존 행과 겹치면
            #    'UNIQUE constraint failed: entries.id' 로 복원 전체가 실패했다.
            #    (예: test 계정으로 batmi 의 백업을 복원)
            #    id 는 밀리초 타임스탬프라 값 자체에 의미가 크지 않으므로, 비어 있으면
            #    원래 id 를 그대로 쓰고 이미 쓰이는 것만 새로 배정한다.
            taken = {row['id'] for row in c.execute("SELECT id FROM entries")}
            next_id = (max(taken) + 1) if taken else int(time.time() * 1000)
            remapped = 0

            for entry in entries:
                entry = images.extract_inline_images(username, entry)
                entry_id = entry.get('id')
                if entry_id is None or entry_id in taken:
                    next_id += 1
                    entry = dict(entry, id=next_id)
                    entry_id = next_id
                    remapped += 1
                taken.add(entry_id)
                entry_logic.insert_entry(c, username, entry)
            conn.commit()

        if remapped:
            log.info(f"🔄 복원: id 가 이미 사용 중이던 {remapped}건에 새 id 를 배정했습니다."
                            f" (username={username})")

        statscache.invalidate(username)

        # 3. 사용자 첨부파일 폴더 교체
        #    ⭐️ 예전에는 기존 폴더를 먼저 지우고(rmtree) 나서 복사했다. 복사 도중
        #       디스크가 차거나 권한 오류가 나면 원본 첨부파일이 이미 사라진 뒤라
        #       되돌릴 방법이 없었다. 그래서 '새 폴더를 옆에 완성한 뒤 맞바꾸는'
        #       순서로 바꾼다. 실패하면 기존 폴더가 그대로 남는다.
        user_folder = user_dir(config.UPLOAD_FOLDER, username)
        if user_folder is None:
            return jsonify({'error': '계정 이름이 올바르지 않습니다.'}), 400
        staging = user_folder + '.restoring'
        retired = user_folder + '.old'
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(retired, ignore_errors=True)
        os.makedirs(staging, exist_ok=True)

        temp_uploads = os.path.join(temp_dir, 'uploads')
        if os.path.exists(temp_uploads):
            for f in os.listdir(temp_uploads):
                src_path = os.path.join(temp_uploads, f)
                if os.path.isfile(src_path):
                    shutil.copy2(src_path, os.path.join(staging, f))

        # 여기까지 왔으면 새 폴더가 완성됐다. 이제 rename 두 번으로 맞바꾼다.
        # (rename 은 같은 파일시스템 안에서 사실상 원자적이라 중간 상태가 짧다)
        try:
            if os.path.exists(user_folder):
                os.rename(user_folder, retired)
            os.rename(staging, user_folder)
        except Exception:
            # 교체 실패: 기존 폴더를 되돌려 놓는다
            if not os.path.exists(user_folder) and os.path.exists(retired):
                os.rename(retired, user_folder)
            shutil.rmtree(staging, ignore_errors=True)
            raise
        shutil.rmtree(retired, ignore_errors=True)

        # 4. 사용자 매핑 정보 복원 (ZIP → DB)
        #    구버전 백업도 같은 파일명을 담고 있으므로 그대로 읽힌다.
        temp_account_info = os.path.join(temp_dir, accounts.BACKUP_ARCNAME)
        if os.path.exists(temp_account_info):
            try:
                with open(temp_account_info, 'r', encoding='utf-8') as f:
                    restored_mappings = json.load(f)
            except (ValueError, OSError) as e:
                log.warning(f"계좌 매핑 복원 건너뜀({username}): {e}")
            else:
                with db_conn() as conn:
                    try:
                        accounts.save(conn, username, restored_mappings)
                        conn.commit()
                    except accounts.UnknownUserError:
                        log.warning(f"계좌 매핑 복원 건너뜀: 계정 없음({username})")
                statscache.invalidate(username)

        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        # 예외로 중단됐을 때 남을 수 있는 작업용 폴더 정리 (원본은 건드리지 않는다)
        safe_folder = user_dir(config.UPLOAD_FOLDER, username) if username else None
        if safe_folder:
            shutil.rmtree(safe_folder + '.restoring', ignore_errors=True)
