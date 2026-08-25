"""accounts 모듈 — 계좌 정규화 규칙과 DB 저장소 검증."""

import json
import os
import sqlite3

import pytest

from app.services import accounts
import backend_app
import trading_api


# ── 정규화 규칙 ────────────────────────────────────────────────

@pytest.mark.parametrize('raw,expected', [
    ('44048158-01', '4404815801'),
    ('4404815801', '4404815801'),
    (' 4404 8158-01 ', '4404815801'),
    (None, ''),
    ('', ''),
    (12345, '12345'),
])
def test_account_key_normalizes(raw, expected):
    assert accounts.account_key(raw) == expected


def test_trading_api_shares_the_same_rule():
    """봇 API 가 웹 화면과 다른 규칙을 쓰면 매핑이 조용히 어긋난다."""
    assert trading_api._account_key is accounts.account_key
    assert trading_api._find_account_mapping is accounts.find_account_mapping


def test_find_account_mapping_prefers_exact_then_folded():
    mapping = {'44048158-01': {'alias': '주계좌'}, '99998888': {'alias': '보조'}}
    assert accounts.find_account_mapping(mapping, '44048158-01')[0] == '44048158-01'
    # HTS 가 하이픈 없이 보내도 같은 계좌로 찾아야 한다
    assert accounts.find_account_mapping(mapping, '4404815801')[0] == '44048158-01'
    # 반대 방향(등록은 하이픈 없이, 조회는 하이픈 있게)도 성립해야 한다
    assert accounts.find_account_mapping(mapping, '9999-8888')[0] == '99998888'
    assert accounts.find_account_mapping(mapping, '없는번호') == (None, None)
    assert accounts.find_account_mapping(None, '4404815801') == (None, None)


@pytest.mark.parametrize('bad', [None, [], 'string', 42, {'brokers': 'nope'}])
def test_normalize_survives_garbage(bad):
    out = accounts.normalize(bad)
    assert isinstance(out['brokers'], dict)
    assert isinstance(out['accounts'], dict)


def test_empty_mappings_is_not_shared():
    """호출자가 반환값을 수정해도 다음 호출이 오염되면 안 된다."""
    first = accounts.empty_mappings()
    first['accounts']['x'] = 1
    assert accounts.empty_mappings()['accounts'] == {}


# ── 제외 계좌 판정 ─────────────────────────────────────────────

def test_excluded_accounts_collects_codes_and_aliases():
    codes, aliases = accounts.excluded_accounts({'accounts': {
        '1111-2222': {'alias': '실거래'},
        '3333-4444': {'alias': '연습', 'exclude_from_stats': True},
        '5555-6666': {'exclude_from_stats': True},          # 별칭 없음
        '7777-8888': 'not-a-dict',                          # 손상된 항목
    }})
    assert codes == {'33334444', '55556666'}
    assert aliases == {'연습'}


def test_is_excluded_row_matches_by_number_then_alias():
    codes, aliases = {'33334444'}, {'연습'}
    assert accounts.is_excluded_row({'subAccount': '3333-4444'}, codes, aliases)
    assert accounts.is_excluded_row({'subAccount': '33334444'}, codes, aliases)
    # 계좌번호 없이 이름만 남은 수기 기록
    assert accounts.is_excluded_row({'accountName': '연습'}, codes, aliases)
    assert not accounts.is_excluded_row({'subAccount': '1111-2222'}, codes, aliases)
    assert not accounts.is_excluded_row({}, codes, aliases)


def test_shared_alias_is_not_used_for_name_matching():
    """제외 계좌와 포함 계좌가 같은 별칭을 쓰면 이름 대조를 포기한다.

    증권사마다 '일반계좌'가 있는 게 보통이라, 토스증권 '일반계좌' 하나를 제외했을 때
    한국투자증권 '일반계좌'까지 함께 빠지면 안 된다.
    """
    codes, aliases = accounts.excluded_accounts({'accounts': {
        '189-01-501685': {'broker_name': '토스증권', 'alias': '일반계좌',
                          'exclude_from_stats': True},
        '68029263-01': {'broker_name': '한국투자증권', 'alias': '일반계좌'},
        '43399290-01': {'broker_name': '한국투자증권', 'alias': 'ISA계좌'},
    }})
    assert codes == {'18901501685'}
    assert aliases == set()          # '일반계좌'는 양쪽에 있어 이름으로 판정 불가


def test_registered_account_ignores_alias_match():
    """계좌번호로 계좌가 특정되는 기록은 그 계좌의 설정만 따른다."""
    mappings = {'accounts': {
        '189-01-501685': {'alias': '일반계좌', 'exclude_from_stats': True},
        '68029263-01': {'alias': '일반계좌'},
    }}
    codes, aliases = accounts.excluded_accounts(mappings)
    known = accounts.known_account_keys(mappings)
    assert known == {'18901501685', '6802926301'}

    # 봇 API 는 매핑된 계좌의 별칭을 accountName 으로 저장한다 — 이름이 같아도 제외 대상이 아니다.
    kis = {'subAccount': '68029263-01', 'accountName': '일반계좌'}
    toss = {'subAccount': '189-01-501685', 'accountName': '일반계좌'}
    assert not accounts.is_excluded_row(kis, codes, aliases, known)
    assert accounts.is_excluded_row(toss, codes, aliases, known)


def test_registered_account_wins_over_excluded_alias():
    """별칭이 겹치지 않아도, 등록된 포함 계좌의 기록은 이름 대조로 넘어가지 않는다."""
    mappings = {'accounts': {
        '3333-4444': {'alias': '연습', 'exclude_from_stats': True},
        '1111-2222': {'alias': '실거래'},
    }}
    codes, aliases = accounts.excluded_accounts(mappings)
    known = accounts.known_account_keys(mappings)
    assert aliases == {'연습'}
    # 계좌번호는 포함 계좌인데 이름만 예전 별칭('연습')으로 남은 기록 → 계좌번호가 정답
    row = {'subAccount': '1111-2222', 'accountName': '연습'}
    assert not accounts.is_excluded_row(row, codes, aliases, known)
    # 등록되지 않은 계좌번호라면 이름 대조는 그대로 동작한다
    assert accounts.is_excluded_row({'subAccount': '9999-0000', 'accountName': '연습'},
                                    codes, aliases, known)


# ── DB 저장소 ──────────────────────────────────────────────────

@pytest.fixture
def conn(app):
    """init_db() 로 스키마가 잡힌 임시 DB 연결 (app 픽스처가 DB_FILE 을 교체한다)."""
    c = backend_app.get_db()
    c.execute("INSERT INTO users (username, password_hash, is_allowed) VALUES ('u1', 'x', 1)")
    c.commit()
    yield c
    c.close()


def test_load_returns_empty_for_fresh_user(conn):
    assert accounts.load(conn, 'u1') == {'brokers': {}, 'accounts': {}}
    assert accounts.load(conn, None) == {'brokers': {}, 'accounts': {}}


def test_save_then_load_roundtrip(conn):
    data = {'brokers': {'243': '한국투자증권'}, 'accounts': {'1111-2222': {'alias': '주계좌'}}}
    accounts.save(conn, 'u1', data)
    conn.commit()
    assert accounts.load(conn, 'u1') == data


def test_save_raises_for_unknown_user(conn):
    """UPDATE 가 0건이면 사용자는 저장됐다고 믿는데 값은 사라진다."""
    with pytest.raises(accounts.UnknownUserError):
        accounts.save(conn, '없는사람', {'accounts': {}})


def test_load_tolerates_corrupted_json(conn):
    conn.execute("UPDATE users SET account_mappings = '{ broken' WHERE username='u1'")
    conn.commit()
    assert accounts.load(conn, 'u1') == {'brokers': {}, 'accounts': {}}


def test_load_tolerates_missing_column():
    """컬럼이 없는 구버전 DB 를 만나도 화면 전체가 죽으면 안 된다."""
    c = sqlite3.connect(':memory:')
    c.execute("CREATE TABLE users (username TEXT)")
    assert accounts.load(c, 'u1') == {'brokers': {}, 'accounts': {}}
    c.close()


def test_dumps_matches_legacy_file_shape(conn):
    text = accounts.dumps({'accounts': {'1111': {'alias': 'A'}}})
    parsed = json.loads(text)
    assert parsed == {'brokers': {}, 'accounts': {'1111': {'alias': 'A'}}}


# ── 레거시 JSON 파일 이관 ──────────────────────────────────────

def _write_legacy(json_dir, username, payload):
    d = os.path.join(json_dir, username)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, accounts.BACKUP_ARCNAME)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f)
    return path


def test_migrate_json_files_moves_and_retires_source(conn, tmp_path):
    src = _write_legacy(str(tmp_path), 'u1', {'accounts': {'1111': {'alias': '주계좌'}}})

    assert accounts.migrate_json_files(conn, str(tmp_path)) == 1
    assert accounts.load(conn, 'u1')['accounts'] == {'1111': {'alias': '주계좌'}}
    # 원본은 지우지 않고 이름만 바꿔 둔다 (이관이 잘못됐을 때 되돌릴 수 있어야 한다)
    assert not os.path.exists(src)
    assert os.path.exists(src + '.migrated')


def test_migrate_json_files_never_overwrites_db(conn, tmp_path):
    accounts.save(conn, 'u1', {'accounts': {'DB': {'alias': 'DB값'}}})
    conn.commit()
    _write_legacy(str(tmp_path), 'u1', {'accounts': {'FILE': {'alias': '파일값'}}})

    assert accounts.migrate_json_files(conn, str(tmp_path)) == 0
    assert 'DB' in accounts.load(conn, 'u1')['accounts']


def test_migrate_json_files_skips_orphan_folders(conn, tmp_path):
    """계정이 없는 잔재 폴더는 건드리지 않는다."""
    src = _write_legacy(str(tmp_path), '탈퇴한사람', {'accounts': {}})
    assert accounts.migrate_json_files(conn, str(tmp_path)) == 0
    assert os.path.exists(src)


def test_migrate_json_files_survives_broken_file(conn, tmp_path):
    d = os.path.join(str(tmp_path), 'u1')
    os.makedirs(d)
    with open(os.path.join(d, accounts.BACKUP_ARCNAME), 'w', encoding='utf-8') as f:
        f.write('{ not json')

    assert accounts.migrate_json_files(conn, str(tmp_path)) == 0


def test_migrate_json_files_noop_when_dir_missing(conn, tmp_path):
    assert accounts.migrate_json_files(conn, str(tmp_path / '없음')) == 0
