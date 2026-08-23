"""로깅 설정 — 파일(일자별 로테이션) + 콘솔, 한 줄 포맷.

`logging` 이라는 이름은 표준 라이브러리와 겹치므로 `applog` 로 둔다.
"""

import logging
import os
import re
from logging.handlers import TimedRotatingFileHandler

import config

LOG_FILENAME = 'backend_app.log'

# ⭐️ 몇 초마다 반복돼 콘솔을 덮어버리는 경로. 성공했을 때만 조용히 넘긴다.
#    (봇 하트비트는 인스턴스마다 10초 주기라 여러 대가 붙으면 3~4초에 한 줄씩 찍힌다)
QUIET_CONSOLE_PATHS = {'/api/v1/bot/status'}

# ⭐️ 콘솔에서 걷어낼 백그라운드 작업 (파일에는 그대로 남는다)
QUIET_CONSOLE_FUNCS = {'auto_fetch_nxt_close_job'}

# ⭐️ app.logger 와 같은 핸들러에 붙일 도메인 모듈 로거
MODULE_LOGGERS = ('prices', 'users')


class CustomDailyRotatingFileHandler(TimedRotatingFileHandler):
    """백업 로그 파일명을 backend_app_YYYYMMDD.log 형태로 저장한다."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.suffix = "%Y%m%d"
        self.extMatch = re.compile(r"^\d{8}$")

    def rotation_filename(self, default_name):
        # 기본 'backend_app.log.20231027' → 'backend_app_20231027.log'
        return default_name.replace('.log.', '_') + '.log'

    def getFilesToDelete(self):
        dirName, baseName = os.path.split(self.baseFilename)
        fileNames = os.listdir(dirName)
        result = []
        prefix = baseName.replace('.log', '_')
        for fileName in fileNames:
            if fileName.startswith(prefix) and fileName.endswith('.log'):
                suffix = fileName[len(prefix):-4]
                if self.extMatch.match(suffix):
                    result.append(os.path.join(dirName, fileName))
        if len(result) < self.backupCount:
            return []
        result.sort()
        return result[:len(result) - self.backupCount]


class SingleLineFormatter(logging.Formatter):
    """모든 로그(Traceback 포함)를 개행 없이 한 줄로 출력한다."""

    def format(self, record):
        msg = super().format(record)
        return msg.replace('\n', ' ').replace('\r', '')


class ConsoleFilter(logging.Filter):
    """반복되는 '정상' 로그를 콘솔에서만 걷어낸다 (파일에는 남는다)."""

    def filter(self, record):
        if record.funcName in QUIET_CONSOLE_FUNCS:
            return False
        if getattr(record, 'quiet_console', False):
            return False
        return True


def setup(app):
    """앱 로거와 prices 로거를 파일·콘솔 핸들러에 연결한다."""
    os.makedirs(config.LOG_DIR, exist_ok=True)
    log_file = os.path.join(config.LOG_DIR, LOG_FILENAME)

    formatter = SingleLineFormatter(
        '%(asctime)s.%(msecs)03d [%(levelname)s] [%(funcName)s] '
        '%(filename)s:%(lineno)d - %(message)s',
        datefmt='%H:%M:%S')

    # 매일 자정 갱신, 30일(약 1달) 보관
    file_handler = CustomDailyRotatingFileHandler(
        log_file, when='midnight', interval=1, backupCount=30, encoding='utf-8')
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.DEBUG)
    console_handler.addFilter(ConsoleFilter())

    # ⭐️ 기본 Flask 로거 초기화 및 중복 출력 방지 (시간이 없는 기본 화면 로그 차단)
    app.logger.handlers.clear()
    app.logger.propagate = False
    app.logger.addHandler(file_handler)
    app.logger.addHandler(console_handler)
    app.logger.setLevel(logging.DEBUG)

    # ⭐️ Werkzeug 기본 Access 로그 비활성화 (직접 남기므로 중복이다)
    logging.getLogger('werkzeug').setLevel(logging.ERROR)

    # ⭐️ 도메인 모듈 로거를 앱 로그와 같은 파일/콘솔로 흘려보낸다.
    #    app.logger 는 propagate=False 라 루트 로거를 타지 않으므로 직접 붙여야 한다.
    #    레벨은 INFO — 단계별 실패(debug)는 평소 묻어두고, 캐시 대체·전체 실패만 남긴다.
    #    (여기 없는 모듈의 경고는 어디에도 남지 않는다 — 새 모듈을 만들면 추가할 것)
    for name in MODULE_LOGGERS:
        module_logger = logging.getLogger(name)
        module_logger.handlers.clear()
        module_logger.propagate = False
        module_logger.addHandler(file_handler)
        module_logger.addHandler(console_handler)
        module_logger.setLevel(logging.INFO)

    return file_handler, console_handler
