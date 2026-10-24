"""검증한 개발 환경의 직접·간접 의존성을 constraints 파일로 기록한다.

사용: .venv/bin/python tools/lock_dependencies.py
먼저 requirements-dev.txt를 설치하고 전체 검증을 통과한 환경에서 실행한다.
"""

from importlib.metadata import distribution
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]


def requirements(path):
    for line in path.read_text().splitlines():
        line = line.split('#', 1)[0].strip()
        if line.startswith('-r '):
            yield from requirements(path.parent / line[3:].strip())
        elif line:
            yield Requirement(line)


def main():
    pending = list(requirements(ROOT / 'requirements-dev.txt'))
    selected = {}
    while pending:
        requirement = pending.pop()
        if requirement.marker and not requirement.marker.evaluate({'extra': ''}):
            continue
        name = canonicalize_name(requirement.name)
        package = distribution(name)
        if package.version not in requirement.specifier:
            raise ValueError(f'{name} {package.version}은 {requirement}을 만족하지 않습니다.')
        if name in selected:
            continue
        selected[name] = package.version
        pending.extend(Requirement(item) for item in (package.requires or []))
    text = ('# 검증 환경의 런타임·테스트 의존성. tools/lock_dependencies.py로 갱신한다.\n'
            '# 설치: python -m pip install -r requirements-dev.txt -c requirements.lock\n')
    (ROOT / 'requirements.lock').write_text(text + ''.join(
        f'{name}=={version}\n' for name, version in sorted(selected.items())))


if __name__ == '__main__':
    main()
