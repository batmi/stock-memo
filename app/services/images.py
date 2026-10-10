"""첨부 이미지 저장 — 본문 내장 base64 추출과 단일 이미지 저장.

`thoughts` 본문에 base64 로 박힌 이미지를 파일로 빼내는 일은 기록 저장 경로
(웹 폼)와 최초 가입 시의 레거시 JSON 이관, 그리고 1회성 일괄 마이그레이션까지
세 곳에서 필요하다. 라우트 파일에 두면 그 셋이 서로를 임포트해야 한다.
"""

import base64
import logging
import os
import re
import uuid

import config
from app.database.db import db_conn
from app.services.users import user_dir

log = logging.getLogger('images')

# ⭐️ 본문(thoughts) HTML 에 내장된 base64 이미지 패턴
#    Quill 에디터가 이미지를 base64 로 본문에 심으면 기록 1건이 수백 KB 가 되어
#    /api/data 응답이 수 MB 로 커지고, 초기 로딩 시 '멈칫' 현상의 주원인이 된다.
_INLINE_IMG_RE = re.compile(
    r'src=(["\'])(data:image/(png|jpe?g|gif|webp);base64,([^"\']+))\1',
    re.IGNORECASE)


def extract_inline_images(username, entry, target_folder=None):
    """본문 HTML 내 base64 이미지를 사용자 업로드 폴더의 파일로 추출하고
    src 를 /uploads/ URL 로 치환한 새 entry(dict)를 반환한다.

    이미지가 없으면 원본 entry 를 그대로 반환한다. 디코딩 불가능한 손상
    데이터는 원본 그대로 보존한다.

    target_folder 를 주면 파일을 그 폴더에 쓴다(URL 은 그대로 사용자 폴더 기준).
    복원이 새 첨부 폴더를 옆에 완성한 뒤 맞바꾸기 때문에 필요하다 — 살아 있는
    폴더에 쓰면 곧바로 교체되며 함께 지워진다.
    """
    thoughts = entry.get('thoughts')
    if not username or not isinstance(thoughts, str) or 'data:image' not in thoughts:
        return entry

    user_folder = target_folder or user_dir(config.UPLOAD_FOLDER, username)
    if user_folder is None:
        return entry  # 경로에 쓸 수 없는 계정이면 이미지 추출을 건너뛴다
    os.makedirs(user_folder, exist_ok=True)

    def _save_to_file(match):
        quote, ext, b64 = match.group(1), match.group(3), match.group(4)
        try:
            raw = base64.b64decode(b64)
        except Exception:
            return match.group(0)
        ext = 'jpg' if ext.lower() in ('jpeg', 'jpg') else ext.lower()
        filename = f"qimg_{uuid.uuid4().hex[:12]}.{ext}"
        with open(os.path.join(user_folder, filename), 'wb') as f:
            f.write(raw)
        return f'src={quote}/uploads/{username}/{filename}{quote}'

    new_thoughts = _INLINE_IMG_RE.sub(_save_to_file, thoughts)
    if new_thoughts == thoughts:
        return entry
    new_entry = dict(entry)
    new_entry['thoughts'] = new_thoughts
    return new_entry


def process_image(username, image_data, entry_id):
    """Base64 이미지를 사용자 업로드 폴더에 저장하고 URL 경로를 반환한다.

    ⭐️ 예전에는 uploads/ 바로 아래에 저장하고 /uploads/<file> 을 돌려줬다. 그런데
       첨부 라우트는 /uploads/<user>/<file> 하나뿐이라(사용자 격리) 레거시 JSON 에서
       옮겨 온 이미지는 전부 404 로 깨졌다. extract_inline_images 와 같은 규칙으로
       사용자 폴더에 둔다. 파일명에 쓰는 id 도 경로 문자가 섞이지 않게 거른다.
    """
    if not image_data:
        return None
    if not image_data.startswith('data:image'):
        return image_data  # 이미 URL 형식인 경우 그대로 반환

    user_folder = user_dir(config.UPLOAD_FOLDER, username)
    if user_folder is None:
        return None
    header, encoded = image_data.split(',', 1)
    ext = 'png' if 'png' in header else 'jpg'
    safe_id = re.sub(r'[^0-9A-Za-z_-]', '', str(entry_id)) or uuid.uuid4().hex[:12]
    filename = f"img_{safe_id}.{ext}"

    os.makedirs(user_folder, exist_ok=True)
    with open(os.path.join(user_folder, filename), 'wb') as f:
        f.write(base64.b64decode(encoded))
    return f"/uploads/{username}/{filename}"


# ---------------------------------------------------------------------------
# 첨부 파일 수명 — 기록이 더 이상 참조하지 않는 파일 정리
# ---------------------------------------------------------------------------
#
# ⭐️ 예전에는 파일을 만들기만 하고 지우지 않았다. 기록을 지우거나, 수정하면서 본문의
#    이미지를 빼거나, 저장이 검증(400)·확인(409)에서 되돌아가도 파일은 그대로 남아
#    uploads/ 가 계속 불어났다. 같은 파일을 여러 기록이 함께 참조할 수 있으므로
#    (기록 복사·이동이 본문을 그대로 옮긴다) '이 기록이 쓰던 파일'이 아니라
#    '이제 아무 기록도 쓰지 않는 파일'만 지운다.

# /uploads/<사용자>/<파일명> — 상대 경로든 http://host/uploads/... 든 경로 부분만 본다.
_UPLOAD_URL_RE = re.compile(r'/uploads/([^/"\'\s<>?#]+)/([^/"\'\s<>?#]+)')

# 기록에서 첨부 URL 이 들어 있을 수 있는 컬럼
_UPLOAD_REF_COLUMNS = ('thoughts', 'attachedImage', 'attachedFile')


def entry_uploads(username, entry):
    """기록이 참조하는 이 사용자의 첨부 파일명 집합."""
    if entry is None:
        return set()
    names = set()
    for col in _UPLOAD_REF_COLUMNS:
        text = entry[col] if col in entry.keys() else None
        if isinstance(text, str):
            names.update(name for user, name in _UPLOAD_URL_RE.findall(text) if user == username)
    return names


def store_inline_images(username, entry):
    """extract_inline_images 와 같되, 이번에 새로 만든 파일명도 함께 돌려준다.

    저장이 실패하면 호출자가 그 파일들을 remove_unreferenced() 로 되돌린다.
    """
    before = entry_uploads(username, entry)
    stored = extract_inline_images(username, entry)
    return stored, entry_uploads(username, stored) - before


def remove_unreferenced(username, filenames):
    """filenames 중 **어떤 기록도** 참조하지 않는 첨부 파일을 지운다. 지운 이름 목록을 돌려준다.

    ⚠️ 반드시 커밋한 **뒤에** 부른다. 커밋 전에 지우면 롤백됐을 때 기록만 살아나고
       파일은 사라진다. 참조 여부는 모든 사용자의 기록에서 찾는다(다른 계정이 이
       경로를 담은 백업을 복원했을 수 있다). instr 로 찾는 이유는 LIKE 가 파일명의
       '_' 를 와일드카드로 읽기 때문이다.
    """
    if not filenames:
        return []
    folder = user_dir(config.UPLOAD_FOLDER, username)
    if folder is None:
        return []
    removed = []
    where = ' OR '.join(f"instr({col}, ?) > 0" for col in _UPLOAD_REF_COLUMNS)
    with db_conn() as conn:
        for name in sorted(filenames):
            # 경로 문자가 섞인 이름은 정규식에서 이미 걸러지지만, 지우는 쪽이라 한 번 더 막는다.
            if not name or name != os.path.basename(name) or name.startswith('.'):
                continue
            url = f"/uploads/{username}/{name}"
            hit = conn.execute(f"SELECT 1 FROM entries WHERE {where} LIMIT 1",
                               (url,) * len(_UPLOAD_REF_COLUMNS)).fetchone()
            if hit:
                continue
            try:
                os.remove(os.path.join(folder, name))
                removed.append(name)
            except FileNotFoundError:
                pass
            except OSError as e:
                log.warning(f"⚠️ 첨부 파일 정리 실패({username}/{name}): {e}")
    return removed
