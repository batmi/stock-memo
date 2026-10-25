"""고정 버전 npm 배포본을 검증해 화면 의존성을 저장한다. --verify는 네트워크 없이 검사한다."""

import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import tarfile
from urllib.parse import quote
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1] / 'static' / 'vendor'
PACKAGES = {
    'chart.js@4.4.1': {'dist/chart.umd.js': 'dist/chart.umd.min.js'},
    'sortablejs@1.15.2': ['Sortable.min.js'],
    'flatpickr@4.6.13': ['dist/flatpickr.min.js', 'dist/l10n/ko.js', 'dist/flatpickr.min.css', 'dist/themes/dark.css'],
    'quill@2.0.3': ['dist/quill.js', 'dist/quill.snow.css'],
    'quill-table-up@3.6.0': ['dist/index.css', 'dist/table-creator.css', 'dist/index.umd.js'],
    'marked@18.0.14': ['lib/marked.umd.js'],
    '@botom/quill-resize-module@2.2.2': ['dist/quill-resize-module.min.js'],
    'exceljs@4.3.0': ['dist/exceljs.min.js'],
}


def download():
    manifest, files = {}, {}
    for package, assets in PACKAGES.items():
        name, version = package.rsplit('@', 1)
        source = f'https://registry.npmjs.org/{quote(name, safe="")}/{version}'
        with urlopen(source, timeout=30) as response:
            metadata = json.load(response)
        dist = metadata['dist']
        with urlopen(dist['tarball'], timeout=60) as response:
            archive = response.read()
        algorithm, digest = dist['integrity'].split('-', 1)
        if algorithm != 'sha512' or base64.b64encode(hashlib.sha512(archive).digest()).decode() != digest:
            raise ValueError(f'배포본 무결성 검증 실패: {package}')
        with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as tar:
            names = tar.getnames()
            license_path = next(p for p in names if p.count('/') == 1 and p.split('/')[-1].lower().startswith('license'))
            assets = dict(assets) if isinstance(assets, dict) else {p: p for p in assets}
            assets[license_path.removeprefix('package/')] = 'LICENSE'
            for original, target in assets.items():
                data = tar.extractfile('package/' + original).read()
                path = f'{package}/{target}'
                files[path] = data
                manifest[path] = {'sha256': hashlib.sha256(data).hexdigest(), 'package': package,
                                  'source': dist['tarball'], 'integrity': dist['integrity'], 'member': original}
    # 모든 다운로드·검증을 마친 뒤 저장한다. tar 경로 자체를 추출하지 않는다.
    for path, data in files.items():
        destination = ROOT / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    (ROOT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')


def verify():
    manifest = json.loads((ROOT / 'manifest.json').read_text())
    for path, data in manifest.items():
        if hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != data['sha256']:
            raise ValueError(f'파일 무결성 검증 실패: {path}')
    print(f'{len(manifest)}개 배포 파일 검증 통과')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    if not args.verify:
        download()
    verify()
