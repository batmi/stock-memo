"""권한 판정 — 이 세션이 무엇을 할 수 있는가.

⭐️ 왜 별도 파일인가. `admin_required` 는 auth.py 안에 있었고, admin.py 가 그걸
   가져다 썼다. 그래서 "관리자 화면"이 "로그인·가입·비밀번호 재설정 라우트 묶음"에
   의존하는 모양이 됐다 — 데코레이터 한 줄 때문에 라우트 모듈이 다른 라우트 모듈을
   임포트한 것이다. 새 라우트 파일이 생길 때마다 같은 방향으로 하나씩 늘어난다.

   권한 판정은 특정 화면의 것이 아니라 라우트 전체에 걸친 규칙이므로 여기가 소유한다.
   라우트를 하나도 등록하지 않는다(블루프린트가 없다) — auth.py / admin.py 양쪽이
   이것을 임포트할 뿐, 반대 방향은 없다.

   app/utils/ 로 내리지 않은 이유: utils 는 Flask 에 의존하지 않는다는 성질을
   지키고 있다(로깅·레이트리밋·캐시 모두 프레임워크와 무관하다). 이 규칙은
   Flask 세션을 읽으므로 웹 계층에 남는 것이 맞다.
"""

from functools import wraps

from flask import jsonify, session


def is_admin():
    return session.get('is_admin', False)


def admin_required(f):
    """관리자만 통과시키는 라우트 데코레이터. 아니면 403(JSON)."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not is_admin():
            return jsonify({"error": "Unauthorized"}), 403
        return f(*args, **kwargs)
    return wrapper
