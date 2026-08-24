"""로깅 배선 — 프로젝트가 남긴 로그가 실제로 로그 파일에 도착하는가.

⭐️ 계층 분리로 모듈을 쪼개면서 각 모듈이 `logging.getLogger(모듈명)` 을 갖게
   됐는데, applog.MODULE_LOGGERS 는 예전 그대로 ('prices', 'users') 였다. 그래서
   auth·admin·jobs·ratelimit·api·backup_api 의 로그가 **로그 파일에 한 줄도
   남지 않았다.** 핸들러 없는 로거는 루트로 전파되고 루트에도 핸들러가 없어,
   파이썬의 lastResort 가 WARNING 이상만 포맷 없이 stderr 로 흘린다. INFO 는
   그대로 사라진다.

   로그인 실패 사유와 IP, 계정 잠금, 자동 백업 성패, 복원 결과가 전부 그 대상이라,
   "나중에 로그를 보면 되지" 가 통하지 않는 상태였다. 조용히 없어지는 종류의
   고장이므로 주석("새 모듈을 만들면 추가할 것")이 아니라 테스트로 막는다.
"""
import ast
import logging
import os
import re

import pytest

from app.utils import applog

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

# 검사 대상 — 프로젝트 소스만 (테스트·가상환경·도구 제외)
_SKIP_DIRS = {'.venv', 'venv', '.git', 'tests', '__pycache__', 'node_modules',
              'logs', 'backup', 'uploads', 'db', 'json', 'static', 'tools'}


def _project_py_files():
    """도메인 로그를 남기는 프로젝트 소스.

    applog.py 자신은 제외한다 — 거기 있는 getLogger 는 로그를 남기려는 것이 아니라
    서드파티 로거(werkzeug)를 침묵시키는 **설정**이다.
    """
    applog_path = os.path.join(ROOT, 'app', 'utils', 'applog.py')
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for f in filenames:
            if f.endswith('.py') and os.path.join(dirpath, f) != applog_path:
                yield os.path.join(dirpath, f)


def _declared_logger_names():
    """소스에서 `logging.getLogger('이름')` 으로 만든 로거 이름을 모은다."""
    found = {}
    for path in _project_py_files():
        tree = ast.parse(open(path, encoding='utf-8').read())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not (isinstance(fn, ast.Attribute) and fn.attr == 'getLogger'):
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            name = node.args[0].value
            if isinstance(name, str):
                found.setdefault(name, os.path.relpath(path, ROOT))
    return found


DECLARED = _declared_logger_names()


def test_every_module_logger_is_wired_to_the_log_file():
    """소스가 만든 로거는 모두 MODULE_LOGGERS 에 있어야 한다.

    빠지면 그 모듈의 로그는 파일에 남지 않는다 — 그런데 화면과 동작은 멀쩡해서
    아무도 알아채지 못한다. 새 모듈을 추가하면서 이 목록을 잊으면 여기서 걸린다.
    """
    missing = {n: f for n, f in DECLARED.items() if n not in applog.MODULE_LOGGERS}
    assert not missing, (
        "applog.MODULE_LOGGERS 에 빠진 로거가 있습니다 "
        "(이 모듈들의 로그는 로그 파일에 남지 않습니다): "
        + ', '.join(f"{n} ({f})" for n, f in sorted(missing.items())))


def test_module_loggers_list_has_no_ghosts():
    """반대로, 목록에만 있고 아무도 쓰지 않는 이름이 남아 있지 않은지."""
    ghosts = [n for n in applog.MODULE_LOGGERS if n not in DECLARED]
    assert not ghosts, f"쓰이지 않는 로거가 목록에 남아 있습니다: {ghosts}"


def test_at_least_the_known_modules_are_declared():
    """이 테스트 자체가 무력화되지 않았는지 (수집이 0건이면 아무것도 검사 못 한다)."""
    assert len(DECLARED) >= 8, f"로거 수집이 너무 적습니다: {DECLARED}"
    for expected in ('auth', 'jobs', 'prices'):
        assert expected in DECLARED, f"{expected} 로거를 찾지 못했습니다"


@pytest.mark.parametrize('name', sorted(applog.MODULE_LOGGERS))
def test_module_logger_output_reaches_the_log_file(name, tmp_path, monkeypatch):
    """실제로 한 줄 남겨 보고 파일에 도착하는지 확인한다.

    배선만 검사하면 "목록에는 있는데 핸들러가 안 붙는" 경우를 놓친다.
    """
    import config
    from flask import Flask

    monkeypatch.setattr(config, 'LOG_DIR', str(tmp_path / 'logs'))
    app = Flask(f'probe_{name}')
    file_handler, console_handler = applog.setup(app)
    try:
        marker = f'__WIRED__{name}__'
        logging.getLogger(name).warning(marker)
        file_handler.flush()

        log_path = os.path.join(config.LOG_DIR, applog.LOG_FILENAME)
        content = open(log_path, encoding='utf-8').read()
        assert marker in content, f"{name} 로거의 출력이 로그 파일에 없습니다"
        # 포맷(시각·레벨·파일:줄)까지 붙었는지 — lastResort 로 샌 것과 구분한다
        line = next(ln for ln in content.splitlines() if marker in ln)
        assert re.match(r'^\d\d:\d\d:\d\d\.\d\d\d \[WARNING\] ', line), (
            f"{name} 로그가 포맷 없이 남았습니다: {line!r}")
    finally:
        # 다음 테스트로 임시 핸들러가 새지 않도록 되돌린다.
        # propagate 도 True 로 돌려놓는다 — 핸들러만 지우고 propagate=False 로
        # 두면 그 로거는 '어디에도 안 남는' 상태가 되어, 뒤에 오는 테스트의
        # 로그 단언이 이유 없이 통과하거나 실패한다.
        for handler in (file_handler, console_handler):
            handler.close()
        for logger_name in applog.MODULE_LOGGERS:
            probe_logger = logging.getLogger(logger_name)
            probe_logger.handlers.clear()
            probe_logger.propagate = True


def test_custom_daily_rotating_file_handler(tmp_path):
    from app.utils.applog import CustomDailyRotatingFileHandler
    import os
    
    log_file = tmp_path / "backend_app.log"
    handler = CustomDailyRotatingFileHandler(str(log_file), when='midnight', backupCount=1)
    
    # Check rotation_filename
    assert handler.rotation_filename("backend_app.log.20231027") == "backend_app_20231027.log"
    
    # Check getFilesToDelete
    (tmp_path / "backend_app_20231026.log").touch()
    (tmp_path / "backend_app_20231027.log").touch()
    (tmp_path / "backend_app_20231028.log").touch()
    (tmp_path / "other_file.txt").touch()
    
    files_to_delete = handler.getFilesToDelete()
    assert len(files_to_delete) == 2
    assert "backend_app_20231026.log" in files_to_delete[0]
    assert "backend_app_20231027.log" in files_to_delete[1]
    
    handler.close()


def test_console_filter():
    from app.utils.applog import ConsoleFilter, QUIET_CONSOLE_FUNCS
    import logging
    
    f = ConsoleFilter()
    record = logging.LogRecord(name="test", level=logging.INFO, pathname="", lineno=0, msg="test", args=(), exc_info=None)
    
    # Normal record
    assert f.filter(record) is True
    
    # Filtered by funcName
    record.funcName = list(QUIET_CONSOLE_FUNCS)[0]
    assert f.filter(record) is False
    
    # Filtered by quiet_console attribute
    record.funcName = "normal_func"
    record.quiet_console = True
    assert f.filter(record) is False
