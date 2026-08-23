"""첨부 이미지 저장 — 본문 내장 base64 추출과 단일 이미지 저장.

`thoughts` 본문에 base64 로 박힌 이미지를 파일로 빼내는 일은 기록 저장 경로
(웹 폼)와 최초 가입 시의 레거시 JSON 이관, 그리고 1회성 일괄 마이그레이션까지
세 곳에서 필요하다. 라우트 파일에 두면 그 셋이 서로를 임포트해야 한다.
"""

import base64
import os
import re
import uuid

import config
from users import user_dir

# ⭐️ 본문(thoughts) HTML 에 내장된 base64 이미지 패턴
#    Quill 에디터가 이미지를 base64 로 본문에 심으면 기록 1건이 수백 KB 가 되어
#    /api/data 응답이 수 MB 로 커지고, 초기 로딩 시 '멈칫' 현상의 주원인이 된다.
_INLINE_IMG_RE = re.compile(
    r'src=(["\'])(data:image/(png|jpe?g|gif|webp);base64,([^"\']+))\1',
    re.IGNORECASE)


def extract_inline_images(username, entry):
    """본문 HTML 내 base64 이미지를 사용자 업로드 폴더의 파일로 추출하고
    src 를 /uploads/ URL 로 치환한 새 entry(dict)를 반환한다.

    이미지가 없으면 원본 entry 를 그대로 반환한다. 디코딩 불가능한 손상
    데이터는 원본 그대로 보존한다.
    """
    thoughts = entry.get('thoughts')
    if not username or not thoughts or 'data:image' not in thoughts:
        return entry

    user_folder = user_dir(config.UPLOAD_FOLDER, username)
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


def process_image(image_data, entry_id):
    """Base64 이미지를 파일로 저장하고 URL 경로를 반환"""
    if not image_data:
        return None
    if image_data.startswith('data:image'):
        header, encoded = image_data.split(',', 1)
        ext = 'jpg'
        if 'png' in header:
            ext = 'png'

        filename = f"img_{entry_id}.{ext}"
        filepath = os.path.join(config.UPLOAD_FOLDER, filename)

        with open(filepath, 'wb') as f:
            f.write(base64.b64decode(encoded))

        return f"/uploads/{filename}"
    return image_data  # 이미 URL 형식인 경우 그대로 반환
