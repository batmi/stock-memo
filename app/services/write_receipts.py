"""웹 저장 결과를 변경과 같은 트랜잭션에 남겨 응답 유실 후 재시도를 구분한다."""

import hashlib
import json
import re


class RequestConflict(ValueError):
    pass


def identify(conn, username, request_id, operation, payload):
    if request_id is None:
        return None, None
    if not isinstance(request_id, str) or not re.fullmatch(r'[a-zA-Z0-9_:-]{16,128}', request_id):
        raise ValueError('저장 요청 식별자가 올바르지 않습니다.')
    owner = conn.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone()
    if owner is None:
        raise ValueError('계정을 찾을 수 없습니다.')
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    identity = (username, owner['id'], request_id, operation, digest)
    old = conn.execute('SELECT operation,payload_hash,result_json FROM entry_requests WHERE user_id=? AND request_id=?',
                       (owner['id'], request_id)).fetchone()
    if old:
        if old['operation'] != operation or old['payload_hash'] != digest:
            raise RequestConflict('같은 저장 요청에 다른 내용이 전달되었습니다. 저장 상태를 확인해주세요.')
        return identity, json.loads(old['result_json'])
    return identity, None


def remember(conn, identity, result):
    if identity is not None:
        conn.execute('''INSERT INTO entry_requests
            (username,user_id,request_id,operation,payload_hash,result_json) VALUES (?,?,?,?,?,?)''',
            (*identity, json.dumps(result)))
