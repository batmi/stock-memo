"""환경설정의 부분 저장·기존 클라이언트 호환·잘못된 저장값 복구."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.database.db import db_conn
from helpers import _login


def test_stale_tabs_patch_only_changed_fields(app):
    first, second = app.test_client(), app.test_client()
    _login(first, 'trader')
    _login(second, 'trader')
    initial = {'defaultView': 'list', 'showClosedPositions': False, 'extension': {'enabled': True}}
    assert first.post('/api/preferences', json=initial).status_code == 200
    assert first.get('/api/preferences').json == second.get('/api/preferences').json == initial

    assert first.patch('/api/preferences', json={'defaultView': 'chart'}).status_code == 200
    assert second.patch('/api/preferences', json={'showClosedPositions': True}).status_code == 200
    response = first.get('/api/preferences')
    assert response.json == {**initial, 'defaultView': 'chart', 'showClosedPositions': True}
    assert response.headers['Cache-Control'] == 'no-store'


def test_concurrent_patches_merge_in_one_write_transaction(app):
    clients = [app.test_client(), app.test_client()]
    for client in clients:
        _login(client, 'trader')
    assert clients[0].post('/api/preferences', json={'extension': 'preserved'}).status_code == 200
    barrier = Barrier(2)

    def save(client, patch):
        barrier.wait(timeout=5)
        return client.patch('/api/preferences', json=patch).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(save, clients[0], {'defaultView': 'calendar'})
        second = pool.submit(save, clients[1], {'portfolioOrder': ['005930', '000660']})
        assert first.result(timeout=10) == second.result(timeout=10) == 200
    assert clients[0].get('/api/preferences').json == {
        'extension': 'preserved', 'defaultView': 'calendar', 'portfolioOrder': ['005930', '000660'],
    }


def test_patch_removes_explicit_null_and_post_keeps_full_replacement_contract(client):
    _login(client)
    initial = {'defaultView': 'chart', 'portfolioOrder': ['005930'], 'extension': None}
    assert client.post('/api/preferences', json=initial).status_code == 200
    assert client.get('/api/preferences').json == initial
    assert client.patch('/api/preferences', json={'portfolioOrder': None}).status_code == 200
    assert client.get('/api/preferences').json == {'defaultView': 'chart', 'extension': None}
    assert client.post('/api/preferences', json={'theme': 'dark', 'chartType': 'bar'}).status_code == 200
    assert client.get('/api/preferences').json == {'theme': 'dark', 'chartType': 'bar'}


@pytest.mark.parametrize('method', ['post', 'patch'])
@pytest.mark.parametrize('invalid', [
    {'portfolioOrder': {}},
    {'portfolioOrder': ['005930', 660]},
    {'portfolioOrder': ['x' * 501]},
    {'defaultView': 'unsupported'},
    {'defaultView': ['chart']},
    {'showClosedPositions': 'false'},
    {'isDashboardCollapsed': 1},
    {'currentFilterStock': {}},
    {'currentChartAccount': 'x' * 4097},
    {'currentMarketMode': 'unknown'},
    {'extension': float('nan')},
])
def test_invalid_preferences_do_not_change_existing_values(client, method, invalid):
    _login(client)
    saved = {'defaultView': 'chart', 'extension': {'active': True}}
    assert client.post('/api/preferences', json=saved).status_code == 200
    response = getattr(client, method)('/api/preferences', json=invalid)
    assert response.status_code == 400
    assert client.get('/api/preferences').json == saved


@pytest.mark.parametrize('stored', ['INVALID_JSON', 'null', '[]', '42', '"string"'])
def test_get_recovers_nonobject_legacy_preferences(client, stored):
    _login(client)
    with db_conn(write=True) as conn:
        conn.execute('UPDATE users SET preferences=? WHERE username=?', (stored, 'trader'))
        conn.commit()
    assert client.get('/api/preferences').json == {}
    assert client.patch('/api/preferences', json={'defaultView': 'chart'}).status_code == 200
    assert client.get('/api/preferences').json == {'defaultView': 'chart'}


def test_get_drops_invalid_known_fields_but_preserves_legacy_extensions(client):
    _login(client)
    stored = {
        'portfolioOrder': {}, 'defaultView': [], 'showHistoryClosedPositions': 'false',
        'currentChartStock': {'name': '삼성전자'}, 'currentMarketMode': 'NXT',
        'extension': {'nested': [1, None, True]}, 'showClosedPositions': False,
    }
    with db_conn(write=True) as conn:
        conn.execute('UPDATE users SET preferences=? WHERE username=?', (json.dumps(stored), 'trader'))
        conn.commit()
    assert client.get('/api/preferences').json == {
        'currentMarketMode': 'NXT', 'extension': {'nested': [1, None, True]}, 'showClosedPositions': False,
    }


def test_patch_is_authenticated_and_scoped_to_current_account(app):
    first, second = app.test_client(), app.test_client()
    assert first.patch('/api/preferences', json={'defaultView': 'chart'}).status_code == 401
    _login(first, 'first')
    _login(second, 'second')
    assert first.patch('/api/preferences', json={'defaultView': 'chart'}).status_code == 200
    assert second.patch('/api/preferences', json={'defaultView': 'calendar'}).status_code == 200
    assert first.get('/api/preferences').json == {'defaultView': 'chart'}
    assert second.get('/api/preferences').json == {'defaultView': 'calendar'}


@pytest.mark.parametrize('invalid', [[], 'string', 42])
def test_patch_rejects_nonobject_body(client, invalid):
    _login(client)
    assert client.patch('/api/preferences', json=invalid).status_code == 400
