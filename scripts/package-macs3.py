#!/usr/bin/env python3
"""Package the independently usable MACS3/Pyodide distribution after validation."""
from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
import io
import json
import math
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ENGINES = {'node', 'chromium', 'firefox', 'webkit'}
RUNTIME = ('runtime/index.mjs', 'runtime/macs3.mjs', 'runtime/macs3-worker.mjs',
           'runtime/macs3-execute.mjs')
VALIDATION = ('scripts/validate-macs3.mjs', 'validation/macs3/compare.py',
              'validation/macs3/cases.json')
DATA = 'datasets/macs3'
EXAMPLE = 'examples/macs3'
DIST = 'dist/macs3'
TOOL = 'tools/macs3'
SHA256 = re.compile(r'[0-9a-f]{64}')
WASM_HEADER = b'\0asm\1\0\0\0'
PYODIDE_FILES = {'pyodide.mjs', 'pyodide.asm.mjs', 'pyodide.asm.wasm', 'python_stdlib.zip', 'pyodide-lock.json'}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()


def relative_name(name: str) -> str:
    if (not isinstance(name, str) or not name or '\\' in name or any(ord(char) < 32 for char in name)
            or PurePosixPath(name).is_absolute() or re.match(r'^[A-Za-z]:', name)
            or any(part in ('', '.', '..', '.git', '__pycache__') for part in name.split('/'))):
        raise RuntimeError(f'Unsafe package path: {name!r}')
    return name


def local_path(name: str) -> Path:
    path = ROOT / relative_name(name)
    if not path.resolve().is_relative_to(ROOT.resolve()):
        raise RuntimeError(f'Package path escapes root: {name}')
    return path


def required(path: Path) -> bytes:
    if path.is_symlink() or not path.resolve().is_relative_to(ROOT.resolve()) or not path.is_file():
        raise RuntimeError(f'Required regular package input is missing or unsafe: {path}')
    return path.read_bytes()


def verify(path: Path, expected: str) -> bytes:
    if not isinstance(expected, str) or not SHA256.fullmatch(expected):
        raise RuntimeError(f'Invalid SHA256 for {path}')
    data = required(path)
    if digest(data) != expected:
        raise RuntimeError(f'Checksum mismatch: {path}')
    return data


def read_json(name: str):
    return json.loads(required(local_path(name)))


def tree_hashes(directory: str, *, exclude=(), ignore_bytecode=False) -> dict[str, str]:
    base = local_path(directory)
    if not base.is_dir() or base.is_symlink():
        raise RuntimeError(f'Required package directory is missing or unsafe: {directory}')
    result = {}
    for path in sorted(base.rglob('*')):
        if path.is_symlink():
            raise RuntimeError(f'Package directory contains a symlink: {path}')
        if path.is_file():
            relative = path.relative_to(base).as_posix()
            if ignore_bytecode and ('__pycache__' in path.relative_to(base).parts or path.suffix in ('.pyc', '.pyo')):
                continue
            if relative not in exclude:
                result[relative_name(relative)] = digest(required(path))
    return result


def check_wasm_archive(data: bytes, name: str) -> None:
    """NumPy ships two static WebAssembly libraries; never accept host objects."""
    if not data.startswith(b'!<arch>\n'):
        raise RuntimeError(f'{name}: invalid static WebAssembly archive')
    offset, objects = 8, 0
    while offset < len(data):
        header = data[offset:offset + 60]
        if len(header) != 60 or header[58:60] != b'`\n':
            raise RuntimeError(f'{name}: corrupt static WebAssembly archive')
        try:
            size = int(header[48:58])
        except ValueError as error:
            raise RuntimeError(f'{name}: invalid static archive member length') from error
        end = offset + 60 + size
        if size < 0 or end > len(data):
            raise RuntimeError(f'{name}: truncated static WebAssembly archive')
        member = header[:16].strip()
        if member not in (b'/', b'//', b'/SYM64/'):
            if not data[offset + 60:end].startswith(WASM_HEADER):
                raise RuntimeError(f'{name}: static archive contains a non-WebAssembly object')
            objects += 1
        offset = end + (size % 2)
    if offset != len(data) or not objects:
        raise RuntimeError(f'{name}: static archive contains no valid WebAssembly objects')


def check_wheel(data: bytes, name: str, version: str, *, require_wasm: bool = False) -> None:
    """Confirm package identity and reject desktop native extensions in browser wheels."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or archive.testzip():
            raise RuntimeError(f'{name}: duplicate or corrupt wheel entries')
        metadata = [item for item in names if item.endswith('.dist-info/METADATA')]
        if len(metadata) != 1:
            raise RuntimeError(f'{name}: wheel has no unique package metadata')
        headers = BytesParser().parsebytes(archive.read(metadata[0]))
        normalize = lambda value: re.sub(r'[-_.]+', '-', value).lower()
        if normalize(headers.get('Name', '')) != normalize(name) or headers.get('Version') != version:
            raise RuntimeError(f'{name}: wheel package name or version differs from its manifest')
        extensions = 0
        for entry in archive.infolist():
            relative_name(entry.filename.rstrip('/'))
            if stat.S_ISLNK(entry.external_attr >> 16):
                raise RuntimeError(f'{name}: wheel contains a symlink')
            if entry.is_dir():
                continue
            contents = archive.read(entry)
            if contents.startswith(b'\x7fELF') or entry.filename.endswith(('.pyd', '.dll', '.dylib', '.exe')):
                raise RuntimeError(f'{name}: wheel contains a desktop native binary')
            if entry.filename.endswith('.a'):
                check_wasm_archive(contents, entry.filename)
            if entry.filename.endswith(('.so', '.wasm')):
                if not contents.startswith(WASM_HEADER):
                    raise RuntimeError(f'{name}: extension is not WebAssembly')
                extensions += 1
        if require_wasm and not extensions:
            raise RuntimeError(f'{name}: compiled wheel contains no WebAssembly extensions')


def archive_records(lock: dict) -> dict[str, dict]:
    """All archived build inputs, including corresponding runtime sources."""
    records = {}

    def add(record):
        name = relative_name(record.get('archive') or record['filename'])
        if not isinstance(record.get('sha256'), str) or not SHA256.fullmatch(record['sha256']):
            raise RuntimeError(f'Invalid archived input SHA256: {name}')
        if name in records and records[name]['sha256'] != record['sha256']:
            raise RuntimeError(f'Conflicting archived input checksums: {name}')
        records[name] = record

    add(lock)
    for record in lock['dependencies'].values():
        add(record)
        for key in ('wheel', 'nativeWheel'):
            if record.get(key):
                add(record[key])
    for record in lock.get('sourceArchives', []):
        add(record)
    for requirement in lock['buildRequirements']:
        add(requirement['wheel'])
        add(requirement['source'])
    toolchains = lock['toolchainDownloads']
    if (len(toolchains) != 2 or any(record.get('role') != 'host-toolchain' or record.get('distribute') is not False
                                  for record in toolchains)):
        raise RuntimeError('Host compiler downloads must be explicitly identified as external build tools')
    expected_kinds = set()
    for record in toolchains:
        name = relative_name(record['filename'])
        if not re.fullmatch(r'(?:macs3|cutadapt)-toolchain/[^/]+', name) or not SHA256.fullmatch(record.get('sha256', '')):
            raise RuntimeError('Invalid pinned host compiler download')
        if re.fullmatch(r'(?:macs3|cutadapt)-toolchain/[0-9a-f]{40}-wasm-binaries\.tar\.xz', name):
            expected_kinds.add('compiler')
        elif re.fullmatch(r'(?:macs3|cutadapt)-toolchain/node-v\d+\.\d+\.\d+-linux-x64\.tar\.xz', name):
            expected_kinds.add('node')
        else:
            raise RuntimeError('Unknown host toolchain exclusion')
    if expected_kinds != {'compiler', 'node'}:
        raise RuntimeError('The explicit host compiler and Node downloads must both be pinned')
    return records


def check_dependency_closure(packages: list[dict], lock: dict) -> None:
    """Evaluate PEP 508 markers with the verified, bundled packaging parser."""
    parser_packages = [package for package in packages if package['name'] == 'packaging']
    if len(parser_packages) != 1:
        raise RuntimeError('The runtime omits its pinned packaging dependency parser')
    parser = parser_packages[0]
    pinned = lock['dependencies']['packaging']['wheel']['sha256']
    if parser['sha256'] != pinned:
        raise RuntimeError('Dependency parser differs from its pinned original wheel')
    command = [sys.executable, '-I', '-S', str(local_path(f'{TOOL}/check-wheel-dependencies.py')),
               '--packaging', str(local_path(f'{DIST}/{parser["filename"]}')),
               '--packaging-sha256', pinned, '--python', lock['python'],
               *[str(local_path(f'{DIST}/{package["filename"]}')) for package in packages]]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if completed.returncode:
        raise RuntimeError('Wheel metadata dependency closure failed: ' + (completed.stderr or completed.stdout).strip())


def check_build_identity() -> tuple[dict, dict, dict]:
    lock = read_json(f'{TOOL}/sources.lock.json')
    distribution = read_json(f'{DIST}/manifest.json')
    raw = required(local_path(f'{DIST}/build-manifest.json'))
    identity = json.loads(raw)
    assets = tree_hashes(DIST, exclude=('build-manifest.json',))
    if identity.get('schema') != 1 or identity.get('tool') != 'macs3' or identity.get('assets') != assets:
        raise RuntimeError('Build manifest does not identify the complete compiled distribution')
    expected_inputs = {f'{TOOL}/{name}' for name in tree_hashes(TOOL, ignore_bytecode=True)} | {'scripts/build-macs3.py'}
    recorded = identity.get('inputs', {})
    if not isinstance(recorded, dict) or not expected_inputs <= recorded.keys():
        raise RuntimeError('Build manifest omits source inputs: ' + ', '.join(sorted(expected_inputs - recorded.keys())))
    for name, checksum in recorded.items():
        verify(local_path(name), checksum)
    if (distribution.get('schema') != 1 or distribution.get('tool') != 'macs3'
            or distribution.get('version') != lock.get('version')):
        raise RuntimeError('Runtime manifest and source lock disagree on MACS3 identity')
    pyodide = distribution['pyodide']
    pybase = relative_name(pyodide['path'].rstrip('/'))
    if pybase != 'pyodide' or not required(local_path(f'{DIST}/{pybase}/pyodide.asm.wasm')).startswith(WASM_HEADER):
        raise RuntimeError('The Python runtime is not the expected WebAssembly distribution')
    for name in PYODIDE_FILES:
        required(local_path(f'{DIST}/{pybase}/{name}'))
    pylock = read_json(f'{DIST}/{pybase}/pyodide-lock.json')
    if (pyodide['version'] != lock['pyodide'] or pyodide['python'] != lock['python']
            or pylock.get('info', {}).get('python') != pyodide['python']
            or pylock.get('info', {}).get('arch') != 'wasm32'
            or pylock.get('info', {}).get('platform') != 'emscripten_' + lock['emscripten'].replace('.', '_')):
        raise RuntimeError('Bundled Pyodide catalogue and runtime versions disagree')
    packages = distribution['packages']
    expected_packages = lock.get('runtimePackages', [])
    if (not expected_packages or expected_packages[-1] != 'macs3' or len(expected_packages) != len(set(expected_packages))
            or [package['name'] for package in packages] != expected_packages):
        raise RuntimeError('Runtime must contain only the declared MACS3 package set in dependency order')
    package_files = set()
    for package in packages:
        expected_version = lock['version'] if package['name'] == 'macs3' else lock['dependencies'][package['name']]['version']
        if package['version'] != expected_version:
            raise RuntimeError('Runtime Python package version differs from its source lock')
        filename = relative_name(package['filename'])
        expected_directory = 'pyodide/' if package.get('source') == 'pyodide' else 'wheels/'
        if not filename.startswith(expected_directory) or not filename.endswith('.whl') or filename in package_files:
            raise RuntimeError('Invalid or duplicate browser wheel path')
        data = verify(local_path(f'{DIST}/{filename}'), package['sha256'])
        check_wheel(data, package['name'], package['version'], require_wasm=package['name'] in {'macs3', 'hmmlearn'})
        if package.get('source') == 'pyodide':
            original = pylock.get('packages', {}).get(package['name'], {})
            pinned = lock['dependencies'][package['name']].get('wheel', {})
            if (original.get('version') != package['version'] or original.get('file_name') != filename.removeprefix('pyodide/')
                    or original.get('sha256') != package['sha256'] or pinned.get('sha256') != package['sha256']
                    or pinned.get('filename') not in (filename, filename.removeprefix('pyodide/'))):
                raise RuntimeError('Official Python wheel differs from the pinned Pyodide catalogue')
        elif package['name'] != 'macs3' and lock['dependencies'][package['name']].get('wheel'):
            pinned = lock['dependencies'][package['name']]['wheel']
            if pinned['sha256'] != package['sha256']:
                raise RuntimeError('Prebuilt Python wheel differs from its pinned original download')
        package_files.add(filename)
    if set(tree_hashes(f'{DIST}/wheels')) != {name.removeprefix('wheels/') for name in package_files if name.startswith('wheels/')}:
        raise RuntimeError('Unexpected or missing Python wheels in the distribution')
    if set(tree_hashes(f'{DIST}/pyodide')) != PYODIDE_FILES | {name.removeprefix('pyodide/') for name in package_files if name.startswith('pyodide/')}:
        raise RuntimeError('Unexpected or missing assets in the Python runtime')
    for package in packages:
        if package.get('source') == 'pyodide':
            dependencies = pylock['packages'][package['name']].get('depends', [])
            if not set(dependencies) <= set(expected_packages):
                raise RuntimeError('The packaged Pyodide dependency closure is incomplete')
    check_dependency_closure(packages, lock)
    entrypoint = distribution.get('entrypoint', {})
    if entrypoint.get('filename') != 'macs3.py':
        raise RuntimeError('Runtime manifest omits the upstream MACS3 entrypoint')
    verify(local_path(f'{DIST}/macs3.py'), entrypoint.get('sha256'))
    for name, checksum in assets.items():
        if name.startswith('sources/'):
            continue
        data = verify(local_path(f'{DIST}/{name}'), checksum)
        if data.startswith(b'\x7fELF') or name.endswith(('.a', '.dll', '.dylib', '.pyd', '.exe')):
            raise RuntimeError('Browser runtime contains a host native binary or compiler archive')
    environments = identity.get('nativeEnvironments', {})
    if set(environments) != {'native', 'patched-native'}:
        raise RuntimeError('Both native comparison environments must be identified')
    for kind, environment in environments.items():
        python = environment.get('python', {})
        if python.get('version') != pyodide['python'] or python.get('implementation') != 'CPython':
            raise RuntimeError(f'{kind}: native Python identity does not match the browser interpreter')
        files = environment.get('files', {})
        if not {'macs3', 'macs3-entry.py'} <= files.keys() or not any(name.startswith('macs3-packages/') for name in files):
            raise RuntimeError(f'{kind}: native environment omits its launcher or installed packages')
        actual = {}
        for path in sorted(local_path(f'build/{kind}').glob('macs3*')):
            if path.is_dir():
                actual.update({f'{path.name}/{name}': value for name, value in tree_hashes(path.relative_to(ROOT).as_posix()).items()})
            else:
                actual[path.name] = digest(required(path))
        if actual != files or environment.get('launcher') != actual.get('macs3'):
            raise RuntimeError(f'{kind}: installed native environment differs from its build manifest')
    build = {'assets': assets, 'buildManifest': digest(raw), 'manifest': assets['manifest.json'],
             'native': environments['native']['launcher'], 'patched-native': environments['patched-native']['launcher'],
             'nativeEnvironments': environments}
    return lock, identity, build


def check_archive_tree(data: bytes, prefix: str, directory: str, *, selected: set[str] | None = None) -> None:
    expected = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:*') as archive:
        for member in archive.getmembers():
            relative_name(member.name.rstrip('/'))
            if member.isdir():
                continue
            if not member.isfile() or not member.name.startswith(prefix):
                raise RuntimeError(f'Unexpected archive entry for {directory}: {member.name}')
            name = relative_name(member.name[len(prefix):])
            if name in expected:
                raise RuntimeError(f'Duplicate archive entry for {directory}: {name}')
            expected[name] = digest(archive.extractfile(member).read())
    if selected is not None:
        if not selected <= expected.keys():
            raise RuntimeError(f'{directory}: required runtime files are missing from the pinned archive')
        expected = {name: expected[name] for name in selected}
    actual = tree_hashes(directory)
    if selected is not None:
        actual = {name: checksum for name, checksum in actual.items() if name in selected}
    if not expected or expected != actual:
        raise RuntimeError(f'{directory}: installed runtime files differ from their pinned original archive')


def check_scientific_notices(archives: dict[str, dict]) -> None:
    directory = f'{TOOL}/scientific-notices'
    records = read_json(f'{directory}/manifest.json')
    if not isinstance(records, list) or not records:
        raise RuntimeError('Scientific dependency licence manifest is missing or empty')
    destinations = set()
    for notice in records:
        destination = relative_name(notice['destination'])
        if not destination.startswith(directory + '/') or destination in destinations:
            raise RuntimeError('Invalid or duplicate scientific licence notice destination')
        archive = relative_name(notice['archive'])
        if archive not in archives or archives[archive]['sha256'] != notice['archiveSha256']:
            raise RuntimeError('Scientific licence notice is not tied to a pinned corresponding source or wheel')
        relative_name(notice['member'])
        if notice['kind'] not in ('complete-file', 'embedded-comment'):
            raise RuntimeError('Unknown scientific licence extraction method')
        verify(local_path(destination), notice['sha256'])
        destinations.add(destination.removeprefix(directory + '/'))
    if set(tree_hashes(directory)) - {'manifest.json', 'README.md'} != destinations:
        raise RuntimeError('Scientific licence manifest does not cover every extracted notice')


def collect(reports: list[Path]) -> tuple[dict[str, tuple[bytes, int]], dict]:
    lock, identity, build = check_build_identity()
    if read_json('validation/macs3/cases.json').get('version') != lock['version']:
        raise RuntimeError('Case plan and runtime MACS3 versions disagree')
    checked = check_reports(reports, build)
    manifest, manifest_sha = checked_dataset_manifest()
    entries: dict[str, tuple[bytes, int]] = {}
    sources = {}

    def put(name: str, data: bytes, mode=0o644, *, source=True):
        relative_name(name)
        if name in entries and entries[name][0] != data:
            raise RuntimeError(f'Conflicting package entries: {name}')
        entries[name] = (data, mode)
        if source:
            sources[name] = digest(data)

    def add(name: str, *, source=True, expected=None):
        path = local_path(name)
        data = verify(path, expected) if expected else required(path)
        put(name, data, 0o755 if path.stat().st_mode & 0o111 else 0o644, source=source)

    def tree(directory: str, *, source=True):
        for name in tree_hashes(directory, ignore_bytecode=source):
            add(f'{directory}/{name}', source=source)

    for name in ('LICENSE', *RUNTIME, 'runtime/MACS3.md',
                 'scripts/build-macs3.py', 'scripts/package-macs3.py', *VALIDATION,
                 'scripts/test-macs3-demo.mjs', 'scripts/test-macs3-package.mjs', 'scripts/fetch-macs3-test-data.py',
                 'tests/test_macs3_compare.py', 'tests/test_macs3_package.py'):
        add(name)
    for path in sorted(local_path('tests').glob('macs3*.test.mjs')):
        add(path.relative_to(ROOT).as_posix())
    for path in sorted(local_path('tests').glob('test_macs3_*.py')):
        add(path.relative_to(ROOT).as_posix())
    for directory in (TOOL, EXAMPLE, 'validation/macs3', DATA):
        tree(directory)
    for name, checksum in identity['inputs'].items():
        add(name, expected=checksum)
    tree(DIST, source=False)

    archives = archive_records(lock)
    for name, record in archives.items():
        data = verify(local_path(f'{DIST}/sources/{name}'), record['sha256'])
        # The build recipe can reuse this archive as its verified input cache.
        sources[f'{DIST}/sources/{name}'] = digest(data)
        for notice in record.get('licensePaths', []):
            if not relative_name(notice).startswith(TOOL + '/'):
                raise RuntimeError('Runtime notice lies outside the selected tool')
            required(local_path(notice))
    if set(tree_hashes(f'{DIST}/sources')) != archives.keys():
        raise RuntimeError('Source distribution contains missing or unpinned archive inputs')
    for notice in set(lock['requiredNotices']) | {'LICENSE', 'THIRD_PARTY_LICENSES.txt'}:
        required(local_path(f'{TOOL}/{relative_name(notice)}'))
    for notice in read_json(f'{TOOL}/runtime-notices/manifest.json'):
        name = relative_name(notice['destination'])
        if not name.startswith(f'{TOOL}/runtime-notices/'):
            raise RuntimeError('Extracted runtime notice lies outside its expected directory')
        archive = relative_name(notice['archive'])
        if archive not in archives or name not in archives[archive].get('licensePaths', []):
            raise RuntimeError('An audited Python runtime notice is missing its pinned corresponding source')
        verify(local_path(name), notice['sha256'])
    check_scientific_notices(archives)
    with tarfile.open(fileobj=io.BytesIO(entries[f'{DIST}/sources/{lock["archive"]}'][0]), mode='r:*') as upstream:
        launchers = [member for member in upstream.getmembers() if member.isfile() and member.name.endswith('/bin/macs3')]
        if len(launchers) != 1 or upstream.extractfile(launchers[0]).read() != entries[f'{DIST}/macs3.py'][0]:
            raise RuntimeError('MACS3 entrypoint differs from the pinned original source archive')
    for dependency, prefix, directory in (
        ('pyodide-core', 'pyodide/', f'{DIST}/pyodide'),
        ('runtime-ws', 'package/', f'{DIST}/node_modules/ws'),
    ):
        record = lock['dependencies'][dependency]
        data = entries[f'{DIST}/sources/{record["archive"]}'][0]
        check_archive_tree(data, prefix, directory, selected=PYODIDE_FILES if dependency == 'pyodide-core' else None)

    if not manifest.get('sources'):
        raise RuntimeError('The MACS3 fixture manifest omits original source archives')
    for record in manifest['sources']:
        name = record['name']
        cache = relative_name(record['cachePath'])
        if not cache.startswith('build/macs3-fixture-sources/'):
            raise RuntimeError(f'Dataset source cache lies outside its expected directory: {name}')
        data = verify(local_path(cache), record['sha256'])
        if record.get('bytes') != len(data):
            raise RuntimeError(f'Dataset source byte count differs: {name}')
        put(cache, data)
    for name, info in manifest['regeneration'].get('files', {}).items():
        add(name, expected=info['sha256'])
    config = read_json(f'{EXAMPLE}/config.json')
    if set(config.get('datasets', {})) != {'macs3'} or 'callpeak' not in config.get('commands', {}):
        raise RuntimeError('Standalone browser configuration omits required examples')
    playwright = read_json('package.json')['devDependencies']['playwright']
    if not re.fullmatch(r'\d+\.\d+\.\d+', playwright):
        raise RuntimeError('Playwright must be pinned to an exact version')
    package = {'name': 'macs3-wasm', 'version': lock['version'], 'private': True, 'type': 'module',
               'scripts': {'test': 'python3 -m unittest discover -s tests -p "test_macs3_*.py" && node --test tests/macs3*.test.mjs',
                           'validate': 'node scripts/validate-macs3.mjs'},
               'devDependencies': {'playwright': playwright}, 'engines': {'node': '>=20'}}
    npm_lock = read_json('package-lock.json')
    if npm_lock['packages']['']['devDependencies'] != package['devDependencies']:
        raise RuntimeError('The npm lock differs from the pinned browser test dependency')
    npm_lock.update(name=package['name'], version=package['version'])
    npm_lock['packages'][''].update(name=package['name'], version=package['version'])
    put('package.json', json_bytes(package))
    put('package-lock.json', json_bytes(npm_lock))
    for engine, (raw, _) in checked.items():
        put(f'validation-reports/{engine}.json', raw, source=False)
    put('source-manifest.json', json_bytes({
        'schemaVersion': 1, 'tool': 'macs3', 'upstreamVersion': lock['version'],
        'upstreamLockSha256': digest(required(local_path(f'{TOOL}/sources.lock.json'))),
        'files': dict(sorted(sources.items())),
        'notes': 'Includes original package and runtime sources, build inputs, patches, recipes, licence notices and real-data provenance.',
    }), source=False)
    put('release.json', json_bytes({
        'schemaVersion': 1, 'tool': 'macs3', 'upstreamVersion': lock['version'],
        'python': lock['python'], 'pyodide': lock['pyodide'], 'emscripten': lock['emscripten'],
        'build': build, 'datasetManifestSha256': manifest_sha,
        'validationStatus': 'passed for the recorded test scope', 'testedEngines': sorted(checked),
        'reports': {engine: digest(raw) for engine, (raw, _) in checked.items()},
        'requiresCrossOriginIsolation': False,
    }), source=False)
    put('README.md', readme(lock['version'], config).encode(), source=False)
    put('SHA256SUMS', ''.join(f'{digest(data)}  {name}\n' for name, (data, _) in sorted(entries.items())).encode(), source=False)
    return entries, lock


def checked_dataset_manifest() -> tuple[dict, str]:
    raw = required(local_path(f'{DATA}/manifest.json'))
    manifest = json.loads(raw)
    if manifest.get('schema_version') != 1 or set(manifest.get('datasets', {})) != {'macs3'}:
        raise RuntimeError('The MACS3 manifest must identify the pinned real datasets')
    if not manifest.get('files'):
        raise RuntimeError('The MACS3 manifest contains no checked files')
    for name, info in manifest['files'].items():
        data = verify(local_path(f'{DATA}/{relative_name(name)}'), info['sha256'])
        if type(info.get('bytes')) is not int or info['bytes'] != len(data):
            raise RuntimeError(f'Dataset byte count differs from its manifest: {name}')
    return manifest, digest(raw)


def planned_cases() -> dict:
    """Expand the checked-in plan; a report cannot reduce its own required scope."""
    plan = read_json('validation/macs3/cases.json')
    if (plan.get('schema') != 1 or plan.get('tool') != 'macs3'
            or set(plan.get('datasets', [])) != {'macs3'}):
        raise RuntimeError('Unknown MACS3 case-plan schema')
    result = {}
    for spec in plan['cases']:
        if spec.get('kind') not in ('success', 'repeat', 'failure', 'restriction') or 'nativeErrorPolicy' in spec:
            raise RuntimeError('Unsupported MACS3 validation case kind or signal exception')
        datasets = [None] if spec.get('oncePerTool') else spec.get('datasets', plan['datasets'])
        for dataset in datasets:
            if dataset is not None and dataset not in plan['datasets']:
                raise RuntimeError(f'Unknown MACS3 case dataset: {dataset}')
            case_id = f'macs3-{dataset}-{spec["id"]}' if dataset else f'macs3-{spec["id"]}'
            if case_id in result:
                raise RuntimeError(f'Duplicate planned case: {case_id}')
            result[case_id] = {**spec, '_dataset': dataset or plan['datasets'][0]}
    if not result:
        raise RuntimeError('Empty MACS3 validation case plan')
    return result


def dataset_identity(manifest: dict, manifest_sha: str) -> dict:
    """Bind the validation corpus and its observed provenance to every report."""
    return {dataset: {
        'manifestSha256': manifest_sha,
        'files': {name: info['sha256'] for name, info in manifest['files'].items() if name.startswith(dataset + '/')},
        'observations': metadata['observations'],
    } for dataset, metadata in manifest['datasets'].items()}


def check_reports(reports: list[Path], build: dict) -> dict:
    checked = {}
    runtime = {name: digest(required(local_path(name))) for name in RUNTIME}
    validation = {name: digest(required(local_path(name))) for name in VALIDATION}
    manifest, manifest_sha = checked_dataset_manifest()
    datasets = dataset_identity(manifest, manifest_sha)
    plan = planned_cases()
    case_plan = read_json('validation/macs3/cases.json')
    numeric_policy = case_plan['numericPolicy']
    if case_plan.get('pythonVersion') != build['nativeEnvironments']['native']['python']['version']:
        raise RuntimeError('Case plan and native comparison Python versions disagree')
    empty_hash = digest(b'')
    for path in reports:
        if not path.is_file():
            raise RuntimeError(f'Validation report is missing: {path}')
        raw = path.read_bytes()
        report = json.loads(raw)
        engine = report.get('engine')
        if engine not in ENGINES or engine in checked:
            raise RuntimeError(f'Duplicate or unknown validation engine: {engine}')
        if report.get('schema') != 1 or report.get('status') != 'passed':
            raise RuntimeError(f'Report is not a passing schema-1 validation: {path}')
        if not isinstance(report.get('node'), str) or not report['node']:
            raise RuntimeError('Report omits the validation host version')
        if engine != 'node' and (not isinstance(report.get('browserVersion'), str) or not report['browserVersion']):
            raise RuntimeError('Report omits the actual browser version')
        if report.get('builds') != {'macs3': build}:
            raise RuntimeError(f'Compiled or native environments differ from those tested in {path}')
        expected_python = {key: build['nativeEnvironments']['native']['python'][key] for key in ('version', 'implementation')}
        if report.get('nativePython') != expected_python:
            raise RuntimeError('Report omits or changes the observed native Python identity')
        if report.get('runtime') != runtime:
            raise RuntimeError(f'Runtime differs from that tested in {path}')
        if report.get('validation') != validation:
            raise RuntimeError(f'Validation implementation or case plan differs from {path}')
        if report.get('manifestSha256') != manifest_sha or report.get('datasets') != datasets:
            raise RuntimeError(f'Real datasets differ from those tested in {path}')
        expected = report.get('expectedCases', [])
        records = report.get('cases', [])
        actual = [record.get('id') for record in records]
        if (len(expected) != len(set(expected)) or set(expected) != plan.keys()
                or len(actual) != len(set(actual)) or set(actual) != plan.keys()
                or any(record.get('status') != 'passed' for record in records)):
            raise RuntimeError(f'Report does not contain the complete passing planned case set: {path}')
        by_id = {record['id']: record for record in records}
        by_case = {(record['dataset'], record['case']): record for record in records}
        for case_id, spec in plan.items():
            record = by_id[case_id]
            for key, value in {'tool': 'macs3', 'dataset': spec['_dataset'], 'case': spec['id'],
                               'kind': spec['kind'], 'args': spec['args']}.items():
                if record.get(key) != value:
                    raise RuntimeError(f'{case_id}: recorded command differs from its case plan')
            executions = record.get('executions', {})
            if set(executions) != set(spec['executions']):
                raise RuntimeError(f'{case_id}: required native/patched-native/WASM executions are missing')
            failure = spec['kind'] in ('failure', 'restriction')
            if 'errorPolicyObserved' in record:
                raise RuntimeError(f'{case_id}: unplanned signal exception')
            for target, execution in executions.items():
                if 'signal' not in execution or execution['signal'] is not None:
                    raise RuntimeError(f'{case_id}: missing signal evidence or unexpected crash for {target}')
                exit_code = execution.get('exitCode')
                canonical = (exit_code & 255) if type(exit_code) is int and exit_code < 0 else exit_code
                if type(exit_code) is not int or execution.get('canonicalExitCode') != canonical:
                    raise RuntimeError(f'{case_id}: invalid recorded exit code for {target}')
                if (canonical == 0) == failure:
                    raise RuntimeError(f'{case_id}: unexpected exit code for {target}')
                files = execution.get('files', {})
                sizes = execution.get('fileBytes', {})
                if (not isinstance(sizes, dict) or set(sizes) != set(files)
                        or any(type(size) is not int or size < 0 for size in sizes.values())):
                    raise RuntimeError(f'{case_id}: output byte counts are missing or invalid for {target}')
                for name, checksum in files.items():
                    relative_name(name)
                    if not isinstance(checksum, str) or not SHA256.fullmatch(checksum):
                        raise RuntimeError(f'{case_id}: invalid output checksum for {target}')
                if not failure and (execution.get('missingOutputs') != [] or not set(spec.get('outputs', [])) <= files.keys()):
                    raise RuntimeError(f'{case_id}: successful execution has missing outputs for {target}')
                for stream in ('stdout', 'stderr'):
                    if not isinstance(execution.get(stream), str) or not SHA256.fullmatch(execution[stream]):
                        raise RuntimeError(f'{case_id}: missing {stream} evidence for {target}')
                if spec.get('stdoutFile'):
                    artifact = relative_name(spec['stdoutFile'])
                    if files.get(artifact) != execution['stdout']:
                        raise RuntimeError(f'{case_id}: standard-output artifact is missing or differs for {target}')
                if failure and execution['stdout'] == execution['stderr'] == empty_hash:
                    raise RuntimeError(f'{case_id}: expected a failure diagnostic for {target}')
                if failure and execution.get('diagnosticMatched') is not True:
                    raise RuntimeError(f'{case_id}: planned failure diagnostic was not verified for {target}')
                dependencies = []
                for dependency in spec.get('dependencies', []):
                    previous = by_case[(spec['_dataset'], dependency['case'])]['executions'][target]
                    dependencies.append({**dependency, 'sourceTarget': target, 'sha256': previous['files'][dependency['path']]})
                if execution.get('inputsFrom') != dependencies:
                    raise RuntimeError(f'{case_id}: generated input provenance differs for {target}')
            if failure and len({execution['canonicalExitCode'] for execution in executions.values()}) != 1:
                raise RuntimeError(f'{case_id}: native and WebAssembly failure exits disagree')
            comparisons = record.get('comparisons', {})
            if set(comparisons) != set(spec['comparisons']):
                raise RuntimeError(f'{case_id}: required semantic comparisons are missing')
            for target, comparison in comparisons.items():
                if (comparison.get('equal') is not True
                        or not all(isinstance(comparison.get(key), str) and SHA256.fullmatch(comparison[key])
                                   for key in ('expectedSemanticDigest', 'semanticDigest', 'biologicalDigest'))
                        or not comparison.get('invariants')):
                    raise RuntimeError(f'{case_id}: semantic comparison did not pass for {target}')
                artifacts = {artifact['path']: artifact for artifact in spec.get('artifacts', [])}
                if not artifacts or len(artifacts) != len(spec['artifacts']) or comparison.get('differences') != []:
                    raise RuntimeError(f'{case_id}: incomplete artifact comparison evidence for {target}')
                baseline = by_case[(spec['_dataset'], spec['baseline'])] if spec['kind'] == 'repeat' else record
                for side, execution in (('expected', baseline['executions']['native']),
                                        ('actual', executions['native' if target == 'native-contract' else target])):
                    for field, observed in (('Files', execution['files']), ('FileBytes', execution['fileBytes'])):
                        expected_files = {name: observed[name] for name in artifacts}
                        if comparison.get(side + field) != expected_files:
                            raise RuntimeError(f'{case_id}: comparator {side} input bytes differ from execution for {target}')
                for field in ('invariants', 'nativeInvariants', 'canonicalFiles', 'expectedCanonicalFiles'):
                    evidence = comparison.get(field, {})
                    if set(evidence) != artifacts.keys():
                        raise RuntimeError(f'{case_id}: missing complete artifact {field} for {target}')
                    if field.endswith('Files') and any(not isinstance(value, str) or not SHA256.fullmatch(value) for value in evidence.values()):
                        raise RuntimeError(f'{case_id}: invalid canonical artifact checksums for {target}')
                numeric = comparison.get('numericComparisons')
                if comparison.get('numericTolerance') != numeric_policy or not isinstance(numeric, dict):
                    raise RuntimeError(f'{case_id}: numerical comparison policy differs from the checked case plan')
                if not set(numeric) <= artifacts.keys():
                    raise RuntimeError(f'{case_id}: numerical comparison describes an unrequested artifact')
                differences = 0
                for artifact, statistics in numeric.items():
                    artifact_plan = artifacts[artifact]
                    tolerance = ({'absolute': 0, 'relative': 0} if artifact_plan.get('exactFloats')
                                 else numeric_policy['hmm' if artifact_plan['kind'] == 'hmm-model' else 'score'])
                    if statistics.get('tolerance') != tolerance:
                        raise RuntimeError(f'{case_id}: artifact numerical tolerance differs from its plan')
                    fields, tolerated = statistics.get('fields'), statistics.get('toleratedDifferences')
                    if (type(fields) is not int or fields < 1 or type(tolerated) is not int or not 0 <= tolerated <= fields
                            or any(type(statistics.get(key)) not in (int, float) or not math.isfinite(statistics[key])
                                   or statistics[key] < 0 for key in ('maxAbsoluteError', 'maxRelativeError'))):
                        raise RuntimeError(f'{case_id}: invalid numerical comparison evidence for {artifact}')
                    differences += tolerated
                if comparison['expectedSemanticDigest'] != comparison['semanticDigest'] and not differences:
                    raise RuntimeError(f'{case_id}: semantic digest differs without recorded tolerated numeric differences')
            if spec.get('effect') or spec.get('equivalentTo'):
                baseline = spec.get('effect') or spec['equivalentTo']
                before = by_id[f'macs3-{spec["_dataset"]}-{baseline}']['comparisons']['native-contract']['biologicalDigest']
                after = comparisons['native-contract']['biologicalDigest']
                changed = bool(spec.get('effect'))
                expected_effect = {'baseline': baseline, 'before': before, 'after': after,
                                   'changed': changed, 'expected': 'changed' if changed else 'equivalent'}
                if record.get('optionEffect') != expected_effect or (before != after) != changed:
                    raise RuntimeError(f'{case_id}: required option-effect evidence is missing or inconsistent')
        checked[engine] = (raw, report)
    if set(checked) != ENGINES:
        raise RuntimeError('Missing passing reports for ' + ', '.join(sorted(ENGINES - checked.keys())))
    return checked


def readme(version: str, config: dict) -> str:
    command = config['commands']['callpeak']
    return f'''# MACS3 {version} for the browser

This standalone package contains MACS3 and its scientific dependencies, the local Pyodide runtime, a JavaScript runner, a browser demo, authentic upstream fixtures, validation reports, corresponding sources and licence notices.

## Run the example

Run `python3 -m http.server 8000` from this directory and open `http://localhost:8000/examples/macs3/`. Select an example or upload files, edit the arguments, then run MACS3. Results can be viewed and downloaded. Computation happens in a fresh browser worker. Cross-origin isolation is not required.

Keep the complete `dist/macs3/` directory together. Python and its dependencies load locally. Browser memory limits apply. See `tools/macs3/README.md` for supported options and restrictions.

## JavaScript API

```js
import {{ MACS3Runner }} from './runtime/macs3.mjs';
const runner = new MACS3Runner({{
  baseUrl: new URL('./dist/macs3/', import.meta.url),
}});
const result = await runner.run({json.dumps(command['args'])}, {{
  files: inputs, // chip.bed.gz and control.bed.gz, each {{path, data: Uint8Array}}
  outputs: {json.dumps(command['outputs'])},
  outputDirectories: {json.dumps(command.get('outputDirectories', []))},
  timeoutMs: 600000,
}});
```

Check `result.exitCode` and `result.missingOutputs`. Files are returned in `result.files`; standard output and error are byte arrays. An abort signal cancels the command. See `runtime/MACS3.md` for the complete interface.

## Rebuild and validate

Build prerequisites and commands are documented in `tools/macs3/README.md`. The ZIP includes original archives, dependency sources, patches and recipes. Rebuilding requires separately installed host prerequisites and the checksum-pinned compiler and Node downloads. Host environments are excluded. Run `npm ci` to install the pinned browser-test tools.

```sh
python3 scripts/fetch-macs3-test-data.py --verify
npm test
node scripts/test-macs3-package.mjs
node scripts/validate-macs3.mjs --engine node --dataset all
npx playwright install chromium firefox webkit
```

Repeat validation with `--engine chromium`, `--engine firefox` and `--engine webkit`. Run the browser interface check with `BROWSER=chromium node scripts/test-macs3-demo.mjs`, changing the browser for the other engines. The scientific validator requires the native comparison environments to be rebuilt first; the browser demo and its independent tests use the packaged assets.

The reports identify the exact runtime, compiled assets, native environments, inputs, commands and output comparisons. They check coordinates, counts, peak/variant identities and model structure, with field-specific numeric tolerances. They cover the included small fixtures and listed options; they do not establish correctness or performance for every input. See `datasets/macs3/README.md` for provenance and limits.

`release.json` records the validated engines. Verify the complete archive with `sha256sum -c SHA256SUMS`.

## Licences

`LICENSE` covers this project's original infrastructure. Preserve upstream sources, patches, recipes, data provenance and separate notices in `tools/macs3/` and `dist/macs3/notices/` when redistributing.
'''


def write_package(output: Path, reports: list[Path], *, prepared=None) -> Path:
    entries, lock = prepared if prepared is not None else collect(reports)
    stem = f'macs3-{lock["version"]}-wasm'
    if not re.fullmatch(r'[A-Za-z0-9._-]+', stem):
        raise RuntimeError('Unsafe package version or filename')
    output.mkdir(parents=True, exist_ok=True)
    destination = output / f'{stem}.zip'
    temporary = destination.with_suffix('.zip.tmp')
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, (content, mode) in sorted(entries.items()):
                info = zipfile.ZipInfo(f'{stem}/{relative_name(name)}', date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | mode) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, content)
        with zipfile.ZipFile(temporary) as archive:
            bad = archive.testzip()
            if bad:
                raise RuntimeError(f'Corrupt ZIP entry: {bad}')
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    destination.with_suffix('.zip.sha256').write_text(f'{digest(destination.read_bytes())}  {destination.name}\n')
    print(f'{destination}: {len(entries)} files, {destination.stat().st_size} bytes')
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'releases')
    parser.add_argument('--validation-report', action='append', type=Path, required=True)
    args = parser.parse_args()
    write_package(args.output.resolve(), args.validation_report)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, KeyError, OSError, TypeError, zipfile.BadZipFile, tarfile.TarError, subprocess.SubprocessError) as error:
        print(f'Packaging failed: {error}', file=sys.stderr)
        raise SystemExit(1)
