"""화면 스크립트 묶음 — static/js/ 조각을 파일명 순서대로 이어 붙여 한 번에 내려준다.

⭐️ 조각을 <script> 하나씩 실으면 같은 서버에서 받는 파일이 20개가 넘는다. waitress 는
   HTTP/1.1 이라 브라우저가 연결을 6개까지만 열어, 모바일처럼 왕복 시간이 긴 망에서는
   4~5번에 나눠 받는 동안 화면이 떠 있어도 버튼이 반응하지 않았다.
   조각들은 ES 모듈이 아니라 전역을 공유하는 클래식 스크립트라, 순서대로 이어 붙여도
   실행 의미가 같다. (예외: 한 조각의 최상위에서 예외가 나면 뒤 조각도 실행되지 않는다)

묶음은 조각의 (이름, 수정시각) 목록이 바뀔 때만 다시 만든다. gzip 결과도 함께 보관해
라즈베리파이의 느린 CPU 에서 요청마다 압축하지 않게 한다.
"""

import gzip
import hashlib
import io
import os
import threading

_lock = threading.Lock()
_cache = {}  # js_dir -> (signature, version, raw_bytes, gzip_bytes)


def script_files(js_dir):
    """(파일명, 수정시각) 목록을 파일명 순서로 돌려준다. 번호 접두사 순서가 곧 실행 순서다."""
    names = sorted(f for f in os.listdir(js_dir) if f.endswith('.js'))
    return [(n, os.stat(os.path.join(js_dir, n)).st_mtime_ns) for n in names]


def bundle(js_dir):
    """(version, raw_bytes, gzip_bytes). version 은 내용 해시라 ?v= 캐시 버스팅에 쓴다."""
    signature = tuple(script_files(js_dir))
    with _lock:
        cached = _cache.get(js_dir)
    if cached is not None and cached[0] == signature:
        return cached[1:]
    parts = []
    for name, _ in signature:
        with open(os.path.join(js_dir, name), 'rb') as f:
            # 앞 조각이 세미콜론 없이 끝나거나 // 주석으로 끝나도 다음 조각과 붙지 않게 한다.
            parts.append(b'\n;/* ==== js/' + name.encode() + b' ==== */\n' + f.read() + b'\n')
    raw = b''.join(parts)
    version = hashlib.sha256(raw).hexdigest()[:16]
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode='wb', compresslevel=6, mtime=0) as gz:
        gz.write(raw)
    entry = (signature, version, raw, buf.getvalue())
    with _lock:
        _cache[js_dir] = entry
    return entry[1:]
