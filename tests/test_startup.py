"""기동 경로 — 임포트에 부작용이 없어야 한다.

`import backend_app` 한 줄이 실제 DB 에 스키마를 적용하고, 계좌 매핑을 이관하고,
자동 백업 스레드를 띄운 적이 있다. 테스트·CLI 도구·다른 모듈이 모두 이 모듈을
그냥 임포트하므로, 그때마다 사용자의 운영 데이터가 건드려졌다.
기동은 항상 `bootstrap()` 을 **명시적으로** 불러서만 일어나야 한다.
"""

import subprocess
import sys
import textwrap

import backend_app


def test_importing_backend_app_has_no_side_effects(tmp_path):
    """새 파이썬 프로세스에서 임포트만 했을 때 DB 파일이 만들어지면 안 된다."""
    db_path = tmp_path / 'should-not-exist.db'
    script = textwrap.dedent(f'''
        import config
        config.DB_FILE = {str(db_path)!r}
        import backend_app          # 임포트만 한다
        import os, threading
        print('DB_CREATED' if os.path.exists(config.DB_FILE) else 'NO_DB')
        names = [t.name for t in threading.enumerate()]
        print('JOBS' if any(n in ('auto-backup', 'nxt-close') for n in names) else 'NO_JOBS')
    ''')
    out = subprocess.run([sys.executable, '-c', script], capture_output=True,
                         text=True, cwd='.', timeout=60)
    assert out.returncode == 0, out.stderr
    assert 'NO_DB' in out.stdout, f"임포트만으로 DB 가 생성됐다\n{out.stdout}\n{out.stderr}"
    assert 'NO_JOBS' in out.stdout, f"임포트만으로 백그라운드 스레드가 떴다\n{out.stdout}"


def test_application_is_the_wsgi_app():
    """gunicorn 이 backend_app:application 을 잡아도 앱 자체는 얻어야 한다."""
    assert backend_app.application is backend_app.app


def test_bootstrap_applies_schema_without_starting_jobs(tmp_path, monkeypatch):
    """bootstrap(start_jobs=False) 는 스키마만 적용하고 스레드는 띄우지 않는다."""
    import threading

    import config
    import jobs

    monkeypatch.setattr(config, 'DB_FILE', str(tmp_path / 'j.db'))
    monkeypatch.setattr(config, 'JSON_DIR', str(tmp_path / 'json'))

    started = []
    monkeypatch.setattr(jobs, 'start_all', lambda: started.append(True))

    before = {t.name for t in threading.enumerate()}
    backend_app.bootstrap(start_jobs=False)

    assert started == [], "start_jobs=False 인데 백그라운드 작업이 시작됐다"
    assert {t.name for t in threading.enumerate()} == before

    with backend_app.db_conn() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {'entries', 'users', 'api_keys'} <= tables


def test_bootstrap_starts_jobs_when_asked(tmp_path, monkeypatch):
    import config
    import jobs

    monkeypatch.setattr(config, 'DB_FILE', str(tmp_path / 'j2.db'))
    monkeypatch.setattr(config, 'JSON_DIR', str(tmp_path / 'json2'))
    started = []
    monkeypatch.setattr(jobs, 'start_all', lambda: started.append(True))

    backend_app.bootstrap(start_jobs=True)
    assert started == [True]


def test_bootstrap_is_idempotent(tmp_path, monkeypatch):
    """두 번 불러도 터지지 않아야 한다 (재기동·워커 재시작)."""
    import config
    import jobs

    monkeypatch.setattr(config, 'DB_FILE', str(tmp_path / 'j3.db'))
    monkeypatch.setattr(config, 'JSON_DIR', str(tmp_path / 'json3'))
    monkeypatch.setattr(jobs, 'start_all', lambda: None)

    backend_app.bootstrap(start_jobs=False)
    backend_app.bootstrap(start_jobs=False)
