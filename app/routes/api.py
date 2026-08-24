"""화면·데이터 API — 메인 화면, 기록 CRUD, 통계, 시세, 뉴스, 봇 연동 조작.

로그인한 사용자가 화면에서 쓰는 API 를 모은다. 인증(`auth`), 관리자(`admin`),
백업(`backup_api`)은 각자의 블루프린트가 갖고, 남은 것이 여기다.

`/api/v1/*`(봇이 직접 호출하는 REST API)는 `trading_api` 소관이다. 여기 있는
`/api/me/bot/*` 는 **웹 화면이** 봇에게 지시를 내리거나 상태를 읽는 쪽이라 다르다.
"""

import json
import logging
import shutil
import os
import time
from datetime import datetime, timedelta

from flask import (Blueprint, current_app, jsonify, render_template, request,
                   send_from_directory, session)
from werkzeug.security import check_password_hash, generate_password_hash

from app.services import accounts
import config
from app.database import entry_logic
from app.services import images
from app.services import news
from app.services import prices
from app.services import stats
from app.utils import statscache
import trading_api
from app.database.db import db_conn
from app.services.users import bump_session_epoch, user_dir, validate_password

log = logging.getLogger('api')

bp = Blueprint('api', __name__)


def register(app):
    app.register_blueprint(bp)


@bp.route('/')
def index():
    log.debug("index() route 호출됨: templates/stock-memo.html 파일을 렌더링합니다.")
    # ⭐️ 프런트 세션 타이머가 서버의 절대 만료 시각과 동기화되도록 렌더링 시점에 전달
    return render_template('stock-memo.html',
                           session_expires_at=session.get('expires_at', 0),
                           keep_logged_in=bool(session.get('keep_logged_in', False)))


@bp.route('/api/ping', methods=['POST'])
def ping():
    # ⭐️ 세션 연장 팝업의 "연장하기" 전용 엔드포인트.
    #    "로그인 유지" 미선택 세션만 현재 시각 기준 1시간으로 만료 시각을 재설정한다.
    #    (로그인 유지 세션은 로그인 시점 + 24시간에 그대로 만료되므로 연장하지 않음)
    if not session.get('keep_logged_in'):
        session['expires_at'] = time.time() + 3600
    return jsonify({"status": "success", "expires_at": session.get('expires_at', 0)})


@bp.route('/api/me/api-key', methods=['GET'])
def get_api_key():
    """발급된 API 키 목록을 반환합니다.

    ⚠️ 키 원문은 해시로만 저장하므로 다시 조회할 수 없습니다. 발급 직후 1회만
    노출되며, 이후에는 식별용 앞자리(key_prefix)만 볼 수 있습니다.
    이렇게 해야 DB 가 유출돼도 키가 그대로 새어 나가지 않습니다.
    """
    username = session.get('username')
    if not username:
        return jsonify({"error": "Unauthorized"}), 401

    keys = trading_api.list_api_keys(username)
    active = [k for k in keys if not k['revoked_at']]
    return jsonify({
        "keys": keys,
        "has_active_key": bool(active),
        # 하위 호환: 예전 프런트가 api_key 필드를 읽으므로 남겨두되 원문은 줄 수 없다.
        "api_key": None,
    })


@bp.route('/api/me/api-key', methods=['POST'])
def generate_api_key():
    """새 API 키를 발급합니다. 응답의 api_key 는 이때 단 한 번만 볼 수 있습니다."""
    username = session.get('username')
    if not username:
        return jsonify({"error": "Unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    label = (body.get('label') or 'HTS 연동 키')[:100]
    # 기본 동작은 '기존 키 전부 폐기 후 재발급' — 예전 UI 의 재발급 버튼과 의미가 같다.
    if body.get('keep_existing') is not True:
        trading_api.revoke_all_api_keys(username)

    created = trading_api.create_api_key(username, label=label)
    return jsonify({
        "status": "success",
        "api_key": created['api_key'],   # 원문 노출은 이 응답이 유일하다
        "id": created['id'],
        "key_prefix": created['key_prefix'],
        "scopes": created['scopes'],
        "label": created['label'],
    })


@bp.route('/api/me/api-key/<int:key_id>', methods=['DELETE'])
def revoke_api_key_route(key_id):
    """API 키를 폐기합니다. 그 키로 발급된 토큰도 즉시 무효화됩니다."""
    username = session.get('username')
    if not username:
        return jsonify({"error": "Unauthorized"}), 401

    if not trading_api.revoke_api_key(username, key_id):
        return jsonify({"error": "해당 키를 찾을 수 없거나 이미 폐기되었습니다."}), 404
    return jsonify({"status": "success"})


# 재동기화 기간 프리셋. 달력 기준(예: '이번 분기')이 아니라 롤링 일수인 이유는,
# 분기 초에 누르면 범위가 며칠뿐이라 정작 지워진 구간을 못 덮기 때문이다.
RESYNC_PRESET_DAYS = {'quarter': 90, 'half': 180, 'year': 365}


@bp.route('/api/me/bot/resync', methods=['POST'])
def request_bot_resync():
    """봇에 재동기화를 요청합니다. 봇은 다음 Ping(최대 10초) 때 이 지시를 받습니다.

    지운 기록을 되살리는 유일한 경로입니다. 봇은 로컬 거래기록을 원본으로 삼아
    해당 기간의 체결을 다시 보내고, 서버는 멱등키로 이미 있는 기록을 걸러내므로
    기간을 넉넉히 잡아도 중복이 생기지 않습니다.

    웹 화면은 프리셋(분기/반기/1년)만 씁니다. from/to 직접 지정도 계속 받아 두는데,
    봇은 임의 구간을 처리할 수 있고 API 스펙(commandParams)에도 그렇게 정의돼 있어서,
    화면에 입력칸이 없다고 계약까지 좁힐 이유는 없기 때문입니다.

    ⭐️ 봇이 둘 이상이면 botId 를 반드시 지정해야 합니다. 명령은 한 번만 전달되므로
    (at-most-once) 대상을 비워 두면 먼저 Ping 한 아무 봇이나 채가는데, 그 봇은 자기
    계좌만 재전송하고 ack 까지 보내 화면에는 '완료'로 뜹니다 — 정작 복구하려던 계좌는
    아무 일도 없었는데 운용자는 알 방법이 없는 실패입니다. 그래서 요청을 거절합니다.
    """
    username = session.get('username')
    if not username:
        return jsonify({"error": "Unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    preset = body.get('preset')
    date_from, date_to = body.get('from'), body.get('to')
    bot_id = (body.get('botId') or '').strip() or None

    bots = trading_api.list_bots(username)
    known = {b['botId'] for b in bots}
    if bot_id and bot_id not in known:
        return jsonify({"error": f"등록되지 않은 봇입니다: {bot_id}"}), 400
    if not bot_id:
        if len(bots) > 1:
            return jsonify({
                "error": "봇이 여러 대 연결되어 있습니다. 재동기화할 봇을 선택하세요.",
                "bots": bots,
            }), 400
        # 한 대뿐이면 그 봇을 명시적으로 지정한다 — 두 대째가 붙어도 이 명령의
        # 수신자는 바뀌지 않는다.
        bot_id = bots[0]['botId'] if bots else None

    if preset:
        days = RESYNC_PRESET_DAYS.get(preset)
        if days is None:
            return jsonify({"error": f"알 수 없는 기간입니다: {preset}"}), 400
        date_from = (datetime.now(trading_api.KST)
                     - timedelta(days=days)).strftime('%Y-%m-%d')
        date_to = None
    elif not date_from:
        return jsonify({"error": "기간(preset 또는 from)이 필요합니다."}), 400

    try:
        command_id = trading_api.request_bot_command(
            username, 'resync', {'from': date_from, 'to': date_to}, bot_id=bot_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({
        "status": "success",
        "commandId": command_id,
        "botId": bot_id,
        "from": date_from,
        "to": date_to,
        "command": trading_api.latest_bot_command(username, 'resync', bot_id=bot_id),
    })


@bp.route('/api/me/bot/registration/<path:bot_id>', methods=['DELETE'])
def delete_bot_registration(bot_id):
    """봇 등록을 목록에서 지웁니다.

    식별자가 바뀌거나 기기를 폐기하면 옛 행이 남는데, 표시등은 **가장 나쁜 봇**을
    따르므로 그 유령 행 하나가 상태를 영구히 '통신단절'로 굳혀 진짜 장애 신호를
    죽입니다. 살아 있는 봇을 지워도 다음 Ping(≤10초)에 다시 등록됩니다.
    """
    username = session.get('username')
    if not username:
        return jsonify({"error": "Unauthorized"}), 401
    if not trading_api.delete_bot(username, bot_id):
        return jsonify({"error": "등록되지 않은 봇입니다."}), 404
    return jsonify({"status": "success", "bots": trading_api.list_bots(username)})


@bp.route('/api/me/bot/resync', methods=['GET'])
def get_bot_resync_status():
    """최근 재동기화 요청의 진행 상태 (웹 위젯이 주기적으로 조회)."""
    username = session.get('username')
    if not username:
        return jsonify({"error": "Unauthorized"}), 401
    bot_id = (request.args.get('botId') or '').strip() or None
    return jsonify({"command": trading_api.latest_bot_command(username, 'resync',
                                                              bot_id=bot_id)})


# ⭐️ 매핑 조회의 정본은 accounts 모듈이다. 봇 API 와 웹 화면이 같은 값을 보도록
#    양쪽이 같은 함수를 임포트한다. (여기 별칭은 기존 호출부 호환용)
get_user_mappings = accounts.load_for

@bp.route('/api/mappings', methods=['GET'])
def get_mappings_frontend():
    username = session.get('username')
    if not username:
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify(get_user_mappings(username)), 200

@bp.route('/api/mappings', methods=['POST'])
def save_mappings_frontend():
    username = session.get('username')
    if not username:
        return jsonify({"error": "Unauthorized"}), 401
    data = request.json
    if not isinstance(data, dict):
        return jsonify({"error": "잘못된 데이터 형식입니다."}), 400

    with db_conn() as conn:
        try:
            accounts.save(conn, username, data)
        except accounts.UnknownUserError:
            return jsonify({"error": "계정을 찾을 수 없습니다."}), 404
        conn.commit()

    # ⭐️ '금액 계산 제외' 체크가 바뀌면 통계 결과 자체가 달라진다. 캐시를 그대로 두면
    #    체크를 해도 예전 수치가 그대로 보인다.
    statscache.invalidate(username)

    return jsonify({"status": "success"}), 200


@bp.route('/api/me', methods=['GET'])
def get_me():
    username = session.get('username')
    pending_count = 0
    reset_request_count = 0
    must_change_password = False
    admin_flag = False

    bot_status = None
    bot_last_seen = None

    if username:
        with db_conn() as conn:
            c = conn.cursor()
            # ⭐️ 매 요청 시마다 DB에서 최신 관리자 권한을 조회하여 세션 동기화
            c.execute("SELECT is_admin, bot_status, bot_last_seen, must_change_password "
                      "FROM users WHERE username = ?", (username,))
            user = c.fetchone()
            if user:
                admin_flag = bool(user['is_admin'])
                session['is_admin'] = admin_flag  # 브라우저 세션에 즉각 갱신 반영
                bot_status = user['bot_status']
                bot_last_seen = user['bot_last_seen']
                must_change_password = bool(user['must_change_password'])

            if admin_flag:
                c.execute("SELECT COUNT(*) FROM users WHERE is_allowed = 0")
                pending_count = c.fetchone()[0]
                c.execute("SELECT COUNT(*) FROM password_reset_requests")
                reset_request_count = c.fetchone()[0]

    # ⭐️ 봇 만료 판정은 서버가 확정해서 내려준다. 브라우저 시계가 틀어져 있거나
    #    타임존이 다르면 클라이언트 계산은 그대로 오판이 되기 때문이다.
    #
    # ⭐️ 판정의 원본은 bots 테이블이다. users 의 단일 칸은 봇이 여러 대면 마지막에
    #    Ping 한 놈으로 덮여, 실전봇이 죽어도 모의봇 Ping 이 화면을 '정상 가동중'으로
    #    유지한다. bots 가 비어 있을 때(botId 도입 전 기록)만 옛 칸으로 폴백한다.
    bots = trading_api.list_bots(username) if username else []
    if bots:
        bot_state, bot_elapsed, worst_id = trading_api.summarize_bot_states(bots)
        worst = next((b for b in bots if b['botId'] == worst_id), None)
        bot_status = worst['status'] if worst else bot_status
        bot_last_seen = worst['lastSeen'] if worst else bot_last_seen
    else:
        bot_state, bot_elapsed = trading_api.evaluate_bot_state(bot_status, bot_last_seen)

    return jsonify({
        "username": username,
        "is_admin": admin_flag,
        "pending_count": pending_count,
        # ⭐️ 로그인 화면에서 접수된 비밀번호 재설정 요청 수 (관리자에게만 의미 있음)
        "reset_request_count": reset_request_count,
        # ⭐️ 관리자가 임시 비밀번호로 초기화한 계정은 즉시 변경을 강제한다.
        "must_change_password": must_change_password,
        "bot_status": bot_status,
        "bot_last_seen": bot_last_seen,
        "bot_state": bot_state,
        "bot_elapsed_seconds": round(bot_elapsed, 1) if bot_elapsed is not None else None,
        # 대표 상태는 '가장 나쁜 봇'이다. 어느 봇이 그런지는 이 목록에서 본다.
        "bots": bots,
        "bot_ping_interval_seconds": trading_api.BOT_PING_INTERVAL_SECONDS,
        "bot_offline_after_seconds": trading_api.BOT_OFFLINE_AFTER_SECONDS
    })


@bp.route('/api/account', methods=['DELETE'])
def delete_account():
    username = session.get('username')
    if not username:
        return jsonify({"error": "로그인이 필요합니다."}), 401
    is_admin_flag = session.get('is_admin', False)
    # ⭐️ 최고 관리자 계정은 탈퇴할 수 없도록 보호
    if is_admin_flag:
        return jsonify({"error": "최고 관리자 계정은 탈퇴할 수 없습니다."}), 403

    data = request.json or {}
    password = data.get('password')
    if not password:
        return jsonify({"error": "비밀번호를 입력해주세요."}), 400

    with db_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT password_hash FROM users WHERE username = ?", (username,))
        user_record = c.fetchone()

        if not user_record or not check_password_hash(user_record['password_hash'], password):
            return jsonify({"error": "비밀번호가 일치하지 않습니다."}), 400

        # 사용자 데이터 및 계정 삭제 (API 키도 함께 파기해야 탈퇴 후 접근이 막힌다)
        c.execute("DELETE FROM entries WHERE username = ?", (username,))
        c.execute("DELETE FROM api_keys WHERE username = ?", (username,))
        c.execute("DELETE FROM users WHERE username = ?", (username,))
        conn.commit()

    statscache.invalidate(username)

    # 사용자 전용 업로드 폴더 삭제
    user_folder = user_dir(config.UPLOAD_FOLDER, username)
    if user_folder and os.path.exists(user_folder):
        shutil.rmtree(user_folder)

    session.pop('logged_in', None)
    session.pop('username', None)
    return jsonify({"status": "success"})


@bp.route('/uploads/<req_username>/<filename>')
def uploaded_file(req_username, filename):
    # 사용자 격리된 파일 접근 제어
    if req_username != session.get('username'):
        return jsonify({"error": "Unauthorized"}), 403
    user_folder = os.path.join(config.UPLOAD_FOLDER, req_username)
    # ⭐️ 브라우저(특히 Safari)가 다운로드된 ZIP 파일을 강제로 자동 압축 해제하지 않도록 attachment로 전송
    return send_from_directory(user_folder, filename, as_attachment=True)


@bp.route('/api/data', methods=['GET'])
def get_data():
    username = session.get('username')
    if not username:  # ⭐️ 세션 만료/미로그인 시 빈 배열 대신 401 반환 (화면 공백 방지)
        return jsonify({"error": "unauthorized"}), 401

    # ⭐️ ETag 기반 조건부 응답: 데이터가 바뀌지 않았으면 본문 없이 304 만 반환.
    #    브라우저가 If-None-Match 를 자동으로 보내고 304 수신 시 캐시 본문을 그대로
    #    사용하므로 프론트엔드 수정 없이 재방문 전송량과 서버 직렬화 비용이 사라진다.
    etag = statscache.data_etag(username)
    if request.if_none_match.contains(etag):
        response = current_app.make_response(('', 304))
        response.set_etag(etag)
        response.headers['Cache-Control'] = 'no-cache, private'
        return response

    with db_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM entries WHERE username = ? ORDER BY id DESC", (username,))
        data = [dict(row) for row in c.fetchall()]
    response = jsonify(data)
    response.set_etag(etag)
    # no-cache = 저장은 하되 매번 재검증 (다른 계정 로그인 시 세션이 달라 ETag 도 달라짐)
    response.headers['Cache-Control'] = 'no-cache, private'
    return response


@bp.route('/api/entry', methods=['POST'])
def create_entry():
    username = session.get('username')
    entry = request.json
    # ⭐️ 본문 내장 base64 이미지를 파일로 추출 (초기 로딩 응답 크기 유지)
    entry = images.extract_inline_images(username, entry)
    with db_conn() as conn:
        c = conn.cursor()

        # ⭐️ 데이터 무결성 검증 (매도 수량/보유 여부)
        validation_error = entry_logic.validate_trade_entry(c, username, entry)
        if validation_error:
            return jsonify({"error": validation_error}), 400

        entry_logic.insert_entry(c, username, entry)
        conn.commit()
    statscache.invalidate(username)
    return jsonify({"status": "success"})


@bp.route('/api/entry/<int:entry_id>', methods=['PUT'])
def update_entry(entry_id):
    username = session.get('username')
    entry = request.json
    # ⭐️ 본문 내장 base64 이미지를 파일로 추출 (초기 로딩 응답 크기 유지)
    entry = images.extract_inline_images(username, entry)
    with db_conn() as conn:
        c = conn.cursor()

        # ⭐️ 데이터 무결성 검증 (수정 중인 기록 자신은 집계에서 제외)
        validation_error = entry_logic.validate_trade_entry(c, username, entry, exclude_id=entry_id)
        if validation_error:
            return jsonify({"error": validation_error}), 400

        entry_logic.update_entry_row(c, entry_id, username, entry)
        conn.commit()
    statscache.invalidate(username)
    return jsonify({"status": "success"})


@bp.route('/api/entry/<int:entry_id>', methods=['DELETE'])
def delete_entry(entry_id):
    username = session.get('username')
    with db_conn() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM entries WHERE id=? AND username=?", (entry_id, username))
        conn.commit()
    statscache.invalidate(username)
    return jsonify({"status": "success"})


def _excluded_accounts(username):
    """'금액 계산 제외'로 체크한 계좌의 (정규화된 계좌번호 집합, 별칭 집합)."""
    return accounts.excluded_accounts(get_user_mappings(username))


def _is_excluded_account_row(row, codes, aliases):
    return accounts.is_excluded_row(row, codes, aliases)


@bp.route('/api/stats', methods=['GET', 'POST'])
def get_stats():
    """로그인한 사용자의 매매 성과 분석 지표를 반환합니다.
    POST 요청 시 JSON 바디에 entry_ids 리스트를 전달하면 해당 항목들로만 통계를 계산합니다."""
    username = session.get('username')
    entry_ids = None
    granularity = 'monthly'
    # ⭐️ 차트가 보고 있는 기간(기간 이동 버튼 반영)을 그대로 받아 같은 구간만 집계한다.
    period_start = period_end = None
    if request.method == 'POST':
        data = request.json or {}
        entry_ids = data.get('entry_ids')
        granularity = data.get('granularity', 'monthly')
        period_start = data.get('period_start')
        period_end = data.get('period_end')

    # ⭐️ 전체 통계 요청은 캐시 우선 조회 (필터링·기간 지정 요청은 캐시 대상 아님)
    if entry_ids is None and not period_start and not period_end:
        cache_key = (username, granularity)
        cached = statscache.get(cache_key)
        if cached is not None:
            return jsonify(cached)

    # ⭐️ 통계 계산에 필요한 컬럼만 조회 — SELECT * 는 본문 HTML(thoughts)까지
    #    전부 읽어와, 본문이 커질수록 통계 응답이 느려진다. (특히 캐시가 없는 POST 필터 요청)
    # ⭐️ stockCode 는 종목 동일성 판정에 쓴다(stats.stock_identity). 이름으로만 묶으면
    #    표기가 갈린 같은 종목의 손익이 쪼개진다.
    stats_cols = ("id, type, stockName, stockCode, tradeType, price, quantity, "
                  "rawDate, subAccount, accountName")
    # ⭐️ 모의투자(isSimulated=1) 체결은 실제 돈이 오간 기록이 아니므로 성과 분석에서 제외한다.
    #    프론트에서도 걸러 보내지만, 통계는 '실제 성과'를 말하는 화면이라 여기서도 막는다.
    real_only = "COALESCE(isSimulated, 0) = 0"
    with db_conn() as conn:
        c = conn.cursor()
        if entry_ids is not None:
            if not entry_ids:
                rows = []
            else:
                chunk_size = 900
                rows = []
                for i in range(0, len(entry_ids), chunk_size):
                    chunk = entry_ids[i:i+chunk_size]
                    placeholders = ','.join('?' for _ in chunk)
                    c.execute(f"SELECT {stats_cols} FROM entries WHERE username = ? AND {real_only} "
                              f"AND id IN ({placeholders})", (username, *chunk))
                    rows.extend([dict(row) for row in c.fetchall()])
        else:
            c.execute(f"SELECT {stats_cols} FROM entries WHERE username = ? AND {real_only}", (username,))
            rows = [dict(row) for row in c.fetchall()]

    # ⭐️ 계좌 관리에서 '금액 계산 제외'로 지정한 계좌의 기록도 성과 분석에서 뺀다.
    #    프론트에서도 걸러 보내지만, 통계는 '실제 성과'를 말하는 화면이라 여기서도 막는다.
    excluded_codes, excluded_aliases = _excluded_accounts(username)
    if excluded_codes or excluded_aliases:
        rows = [row for row in rows
                if not _is_excluded_account_row(row, excluded_codes, excluded_aliases)]

    result = stats.compute_trade_stats(rows, granularity=granularity,
                                       period_start=period_start, period_end=period_end)
    # ⭐️ 프론트가 "어느 구간을 본 결과인지" 화면에 표기할 수 있도록 되돌려 준다.
    result['periodStart'] = period_start
    result['periodEnd'] = period_end

    if entry_ids is None and not period_start and not period_end:
        statscache.put((username, granularity), result)

    return jsonify(result)


@bp.route('/api/market_calendar', methods=['GET'])
def get_market_calendar():
    """KRX 휴장일 목록을 프론트에 내려준다 (오늘 기준 앞뒤 1년).

    ⭐️ 프론트의 getMarketStatus() 가 휴장일을 몰라 공휴일에도 60초마다 시세를
       폴링하던 문제를 막는다. 판정 기준은 서버(prices) 한 곳이 소유한다.

    ⭐️ maxYear 는 '목록이 몇 년까지 등록됐는가' 였다. 휴장일을 손으로 관리하던
       시절의 축이라 지금은 의미가 없다(holidays 패키지가 임의의 연도를 계산한다).
       대신 판정이 **가능한지**를 available 로 알린다 — 라이브러리가 없으면 모든
       평일이 정규장으로 보이므로 화면도 그 사실을 알아야 한다.
       (구버전 화면 호환을 위해 maxYear 는 내려보낸 범위의 마지막 해로 채운다)
    """
    days = prices.holiday_list()
    return jsonify({
        'holidays': days,
        'available': prices.holidays_available(),
        'maxYear': int(days[-1][:4]) if days else 0,
    })


@bp.route('/api/current_price', methods=['POST'])
def get_current_price():
    data = request.json or {}
    codes = data.get('codes', [])
    market_mode = data.get('market_mode', 'AUTO')
    # ⭐️ allow_cached: 자동 폴링(60초 주기)만 True 로 보내 서버측 단기(25초) 캐시 허용.
    #    수동 새로고침은 False(기본) → 항상 외부 API 라이브 조회로 "진짜 현재가"를 보장.
    allow_cached = bool(data.get('allow_cached', False))
    return jsonify(prices.get_prices(codes, market_mode, allow_cached=allow_cached))


@bp.route('/api/change_password', methods=['POST'])
def change_password():
    username = session.get('username')
    if not username:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json
    current_password = data.get('current_password')
    new_password = data.get('new_password')

    revoke_api_keys = bool(data.get('revoke_api_keys'))

    if not current_password or not new_password:
        return jsonify({"error": "모든 필드를 입력해주세요."}), 400

    policy_error = validate_password(new_password, username)
    if policy_error:
        return jsonify({"error": policy_error}), 400
    if new_password == current_password:
        return jsonify({"error": "새 비밀번호가 기존 비밀번호와 같습니다."}), 400

    with db_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT password_hash FROM users WHERE username = ?", (username,))
        user_record = c.fetchone()

        if not user_record or not check_password_hash(user_record['password_hash'], current_password):
            return jsonify({"error": "현재 비밀번호가 일치하지 않습니다."}), 400

        hashed_pw = generate_password_hash(new_password)
        # ⭐️ 임시 비밀번호로 로그인한 상태였다면 여기서 강제 변경 플래그가 풀린다.
        c.execute("UPDATE users SET password_hash = ?, must_change_password = 0 "
                  "WHERE username = ?", (hashed_pw, username))
        # ⭐️ 다른 기기·침입자의 세션을 전부 끊는다.
        epoch = bump_session_epoch(c, username)

        key_count = c.execute(
            "SELECT COUNT(*) AS n FROM api_keys WHERE username = ? AND revoked_at IS NULL",
            (username,)).fetchone()['n']
        revoked = 0
        if revoke_api_keys and key_count:
            # ⭐️ 자동으로 폐기하지는 않는다 — 봇이 조용히 멈춘다. 사용자가 고른 경우에만.
            #    trading_api.revoke_all_api_keys() 는 별도 커넥션을 열기 때문에
            #    이 트랜잭션이 쥔 쓰기 잠금과 부딪혀 'database is locked' 가 난다.
            #    같은 커서로 처리해 한 트랜잭션 안에서 끝낸다.
            c.execute("UPDATE api_keys SET revoked_at = ? "
                      "WHERE username = ? AND revoked_at IS NULL",
                      (time.strftime('%Y-%m-%d %H:%M:%S'), username))
            revoked = c.rowcount
            key_count = 0

        # 처리 완료된 재설정 요청은 요청함에서 내린다.
        c.execute("DELETE FROM password_reset_requests WHERE username = ?", (username,))
        conn.commit()

    # 방금 바꾼 본인 세션은 유지되어야 하므로 새 epoch 으로 갱신한다.
    session['epoch'] = epoch
    log.info(f"🔑 비밀번호 변경: username='{username}' 세션 무효화 완료 "
                    f"(API 키 폐기 {revoked}개)")

    return jsonify({
        "status": "success",
        "sessions_invalidated": True,
        "api_keys_revoked": revoked,
        # 남아 있는 키가 있으면 화면에서 안내할 수 있도록 알려준다.
        "api_keys_remaining": key_count,
    })


@bp.route('/api/preferences', methods=['GET'])
def get_preferences():
    username = session.get('username')
    if not username:
        return jsonify({}), 401
    with db_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT preferences FROM users WHERE username = ?", (username,))
        row = c.fetchone()
    prefs = {}
    if row and row['preferences']:
        try:
            prefs = json.loads(row['preferences'])
        except Exception:
            pass
    return jsonify(prefs)


@bp.route('/api/preferences', methods=['POST'])
def save_preferences():
    username = session.get('username')
    if not username:
        return jsonify({"error": "Unauthorized"}), 401
    prefs = request.json
    with db_conn() as conn:
        c = conn.cursor()
        c.execute("UPDATE users SET preferences = ? WHERE username = ?", (json.dumps(prefs), username))
        conn.commit()
    return jsonify({"status": "success"})


@bp.route('/api/news', methods=['POST'])
def get_news():
    """보유 종목의 최근 뉴스. 조회·캐시·병렬 처리는 news 모듈이 갖는다."""
    data = request.json or {}
    return jsonify(news.fetch_many(
        data.get('stocks', []),
        force_refresh=bool(data.get('force_refresh', False)),
    ))
