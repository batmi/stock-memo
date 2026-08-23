"""계정 식별자·비밀번호 규칙과 사용자별 경로 격리.

대상: users.py (순수 함수 위주).
"""

import os


import users


# ══════════════════════════════════════════════════════════════
# 보안: 사용자명 검증 (경로 탈출 차단)
# ══════════════════════════════════════════════════════════════
def test_is_valid_username_rules():
    ok = ['batmi', 'user_1', 'user.name', 'a1b', 'A' * 32]
    bad = ['ab', '', None, '../../etc', 'a/b', 'a\\b', 'a..b', '한글이름',
           'A' * 33, '_lead', '.lead', '-lead', 'has space', 'null\x00byte']
    for n in ok:
        assert users.is_valid_username(n) is True, n
    for n in bad:
        assert users.is_valid_username(n) is False, n

def test_user_dir_blocks_escape(tmp_path):
    """경로 조합 헬퍼가 상위 탈출을 막고 None 을 돌려준다."""
    base = str(tmp_path)
    assert users.user_dir(base, 'batmi') == os.path.join(base, 'batmi')
    assert users.user_dir(base, '../../etc') is None
    assert users.user_dir(base, 'a/b') is None
    assert users.user_dir(base, '') is None

# ══════════════════════════════════════════════════════════════
# 보안: 비밀번호 정책
# ══════════════════════════════════════════════════════════════
def test_validate_password_rules():
    assert users.validate_password('Passw0rd!') is None
    assert users.validate_password('abcd1234') is None          # 소문자+숫자
    assert '8자' in users.validate_password('Ab1!')             # 너무 짧음
    assert '두 종류' in users.validate_password('abcdefghij')   # 소문자만
    assert '너무 깁니다' in users.validate_password('Ab1' + 'x' * 300)
    assert '아이디와' in users.validate_password('Testuser1', 'testuser1')

def test_generate_temp_password_is_random():
    a = users.generate_temp_password()
    b = users.generate_temp_password()
    assert a != b and len(a) == 12
