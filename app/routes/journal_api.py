"""화면용 조회 API — 쓰기는 기존 entries 서비스가 담당한다."""

from datetime import datetime
import hashlib
import re

from flask import Blueprint, current_app, jsonify, request, session
from itsdangerous import URLSafeSerializer

from app.database.db import db_conn
from app.services import analysis, journal, historical_prices, valuation
from app.utils import statscache

bp = Blueprint('journal', __name__)


def register(app):
    app.register_blueprint(bp)


@bp.errorhandler(ValueError)
def invalid_query(error):
    return jsonify(error=str(error)), 409 if isinstance(error, journal.StalePage) else 400


def _month():
    month = request.args.get('month', datetime.now().strftime('%Y-%m'))
    if not re.fullmatch(r'\d{4}-\d{2}', month):
        raise ValueError('월은 YYYY-MM 형식이어야 합니다.')
    datetime.strptime(month, '%Y-%m')
    return month


def _response(build):
    username = session['username']
    version = statscache.data_etag(username)
    # URL 은 필터·월·페이지를 포함한다. 서로 다른 응답이 같은 ETag 를 쓰지 않는다.
    etag = hashlib.sha256((version + request.full_path).encode()).hexdigest()
    if request.if_none_match.contains(etag):
        response = current_app.make_response(('', 304))
    else:
        response = jsonify(build(journal.snapshot(username)))
    response.set_etag(etag)
    response.headers['Cache-Control'] = 'no-cache, private'
    return response


@bp.get('/api/journal/meta')
def metadata():
    return _response(journal.metadata)


@bp.get('/api/journal/portfolio')
def portfolio():
    query, month = journal.filters(request.args), _month()
    return _response(lambda model: journal.portfolio(model, query, month))


@bp.get('/api/journal/entries')
def history():
    query = journal.filters(request.args)
    try:
        limit = int(request.args.get('limit', '30'))
    except ValueError as exc:
        raise ValueError('페이지 크기는 정수여야 합니다.') from exc
    if not 1 <= limit <= 100:
        raise ValueError('페이지 크기는 1~100이어야 합니다.')
    cursor = request.args.get('cursor', '')
    if len(cursor) > 2048:
        raise ValueError('페이지 정보가 너무 깁니다.')
    signer = URLSafeSerializer(current_app.secret_key, salt='journal-page')
    return _response(lambda model: journal.history(
        session['username'], model, query, limit, cursor, signer))


@bp.get('/api/entry/<int:entry_id>')
def detail(entry_id):
    with db_conn() as conn:
        row = conn.execute('SELECT * FROM entries WHERE username = ? AND id = ?',
                           (session['username'], entry_id)).fetchone()
    if row is None:
        return jsonify(error='기록을 찾을 수 없습니다.'), 404
    response = jsonify(dict(row))
    response.headers['Cache-Control'] = 'no-store'
    return response


@bp.get('/api/journal/calendar')
def calendar():
    month = _month()
    currency = journal.filters(request.args)['currency'] or 'KRW'
    return _response(lambda model: journal.calendar(model, month, currency))


@bp.get('/api/journal/chart')
def chart():
    query = journal.filters(request.args)
    weekly = request.args.get('granularity') == 'weekly'
    labels = request.args.getlist('period')
    if not 1 <= len(labels) <= 12 or labels != sorted(set(labels)):
        raise ValueError('차트 기간은 오름차순으로 최대 12개까지 지정합니다.')
    for label in labels:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}' if weekly else r'\d{4}-\d{2}', label):
            raise ValueError('차트 기간 형식이 올바르지 않습니다.')
        datetime.strptime(label, '%Y-%m-%d' if weekly else '%Y-%m')
    return _response(lambda model: journal.chart(model, query, labels, weekly))


@bp.get('/api/journal/stats')
def stats():
    query, granularity, start, end = _stats_query()
    return _response(lambda model: journal.trade_stats(model, query, granularity, start, end))


def _stats_query():
    query = journal.filters(request.args)
    granularity = request.args.get('granularity', 'monthly')
    if granularity not in ('monthly', 'weekly'):
        raise ValueError('통계 집계 단위가 올바르지 않습니다.')
    start, end = analysis.period_bounds(request.args)
    return query, granularity, start, end


@bp.get('/api/journal/stats/trades')
def stats_trades():
    query, granularity, start, end = _stats_query()
    options = analysis.detail_options(request.args)
    version = request.args.get('version', '')
    if len(version) > 200:
        raise ValueError('데이터 버전이 올바르지 않습니다.')
    return _response(lambda model: journal.stats_trades(
        model, query, granularity, start, end, options, version))


@bp.route('/api/journal/stats/evaluation', methods=['GET', 'POST'])
def evaluation():
    query, _, start, end = _stats_query()
    username = session['username']
    model = journal.snapshot(username)
    version = request.args.get('version', '')
    if version and version != model['version']:
        raise journal.StalePage('기록이 변경되었습니다. 분석을 새로 조회해주세요.')
    result = valuation.report(model, query, start, end, schedule=True,
                              retry=request.method == 'POST' and request.args.get('retry') == '1')
    if request.method == 'POST':
        historical_prices.watch(username)
    response = jsonify(result)
    # 기록 버전과 별개로 시세 수집이 진행되므로 기존 통계 ETag를 재사용하지 않는다.
    response.headers['Cache-Control'] = 'no-store'
    return response
