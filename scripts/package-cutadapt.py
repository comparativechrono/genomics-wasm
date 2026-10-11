#!/usr/bin/env python3
"""Package the independently usable Cutadapt/Pyodide distribution after validation."""
from __future__ import annotations

import argparse
from email.parser import BytesParser
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ENGINES = {'node', 'chromium', 'firefox', 'webkit'}
RUNTIME = ('runtime/index.mjs', 'runtime/cutadapt.mjs', 'runtime/cutadapt-worker.mjs',
           'runtime/cutadapt-execute.mjs')
VALIDATION = ('scripts/validate-cutadapt.mjs', 'validation/cutadapt/compare.py',
              'validation/cutadapt/cases.json')
DATA = 'datasets/cutadapt'
EXAMPLE = 'examples/cutadapt'
DIST = 'dist/cutadapt'
TOOL = 'tools/cutadapt'
SOURCE_SUFFIXES = {'.patch', '.h', '.hpp', '.c', '.cc', '.cpp', '.rs', '.py', '.pyx', '.pxd', '.pxi',
                   '.sh', '.js', '.mjs', '.json', '.lock', '.toml', '.cfg', '.ini', '.in', '.mk', '.cmake'}
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


def tree_hashes(directory: str, *, exclude=()) -> dict[str, str]:
    base = local_path(directory)
    if not base.is_dir() or base.is_symlink():
        raise RuntimeError(f'Required package directory is missing or unsafe: {directory}')
    result = {}
    for path in sorted(base.rglob('*')):
        if path.is_symlink():
            raise RuntimeError(f'Package directory contains a symlink: {path}')
        if path.is_file():
            relative = path.relative_to(base).as_posix()
            if relative not in exclude:
                result[relative_name(relative)] = digest(required(path))
    return result


def check_wheel(data: bytes, name: str, version: str) -> None:
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
        for entry in archive.infolist():
            relative_name(entry.filename.rstrip('/'))
            if stat.S_ISLNK(entry.external_attr >> 16):
                raise RuntimeError(f'{name}: wheel contains a symlink')
            if entry.is_dir():
                continue
            contents = archive.read(entry)
            if contents.startswith(b'\x7fELF') or entry.filename.endswith(('.pyd', '.dll', '.dylib', '.a')):
                raise RuntimeError(f'{name}: wheel contains a desktop native binary')
            if entry.filename.endswith(('.so', '.wasm')) and not contents.startswith(WASM_HEADER):
                raise RuntimeError(f'{name}: extension is not WebAssembly')


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
        if not name.startswith('cutadapt-toolchain/') or not SHA256.fullmatch(record.get('sha256', '')):
            raise RuntimeError('Invalid pinned host compiler download')
        if re.fullmatch(r'cutadapt-toolchain/[0-9a-f]{40}-wasm-binaries\.tar\.xz', name):
            expected_kinds.add('compiler')
        elif re.fullmatch(r'cutadapt-toolchain/node-v\d+\.\d+\.\d+-linux-x64\.tar\.xz', name):
            expected_kinds.add('node')
        else:
            raise RuntimeError('Unknown host toolchain exclusion')
    if expected_kinds != {'compiler', 'node'}:
        raise RuntimeError('The explicit host compiler and Node downloads must both be pinned')
    return records


def check_build_identity() -> tuple[dict, dict, dict]:
    lock = read_json(f'{TOOL}/sources.lock.json')
    distribution = read_json(f'{DIST}/manifest.json')
    raw = required(local_path(f'{DIST}/build-manifest.json'))
    identity = json.loads(raw)
    assets = tree_hashes(DIST, exclude=('build-manifest.json',))
    if identity.get('schema') != 1 or identity.get('tool') != 'cutadapt' or identity.get('assets') != assets:
        raise RuntimeError('Build manifest does not identify the complete compiled distribution')
    expected_inputs = {f'{TOOL}/{name}' for name in tree_hashes(TOOL)} | {'scripts/build-cutadapt.py'}
    recorded = identity.get('inputs', {})
    if not isinstance(recorded, dict) or not expected_inputs <= recorded.keys():
        raise RuntimeError('Build manifest omits source inputs: ' + ', '.join(sorted(expected_inputs - recorded.keys())))
    for name, checksum in recorded.items():
        verify(local_path(name), checksum)
    if (distribution.get('schema') != 1 or distribution.get('tool') != 'cutadapt'
            or distribution.get('version') != lock.get('version')):
        raise RuntimeError('Runtime manifest and source lock disagree on Cutadapt identity')
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
    if [package['name'] for package in packages] != ['xopen', 'dnaio', 'cutadapt']:
        raise RuntimeError('Runtime must contain only the declared Cutadapt package set in dependency order')
    package_files = set()
    for package in packages:
        expected_version = lock['version'] if package['name'] == 'cutadapt' else lock['dependencies'][package['name']]['version']
        if package['version'] != expected_version:
            raise RuntimeError('Runtime Python package version differs from its source lock')
        filename = relative_name(package['filename'])
        if not filename.startswith('wheels/') or not filename.endswith('.whl') or filename in package_files:
            raise RuntimeError('Invalid or duplicate browser wheel path')
        data = verify(local_path(f'{DIST}/{filename}'), package['sha256'])
        check_wheel(data, package['name'], package['version'])
        package_files.add(filename.removeprefix('wheels/'))
    if set(tree_hashes(f'{DIST}/wheels')) != package_files:
        raise RuntimeError('Unexpected or missing Python wheels in the distribution')
    environments = identity.get('nativeEnvironments', {})
    if set(environments) != {'native', 'patched-native'}:
        raise RuntimeError('Both native comparison environments must be identified')
    for kind, environment in environments.items():
        python = environment.get('python', {})
        if python.get('version') != pyodide['python'] or python.get('implementation') != 'CPython':
            raise RuntimeError(f'{kind}: native Python identity does not match the browser interpreter')
        files = environment.get('files', {})
        if not {'cutadapt', 'cutadapt-entry.py'} <= files.keys() or not any(name.startswith('cutadapt-packages/') for name in files):
            raise RuntimeError(f'{kind}: native environment omits its launcher or installed packages')
        actual = {}
        for path in sorted(local_path(f'build/{kind}').glob('cutadapt*')):
            if path.is_dir():
                actual.update({f'{path.name}/{name}': value for name, value in tree_hashes(path.relative_to(ROOT).as_posix()).items()})
            else:
                actual[path.name] = digest(required(path))
        if actual != files or environment.get('launcher') != actual.get('cutadapt'):
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
    if not expected or expected != tree_hashes(directory):
        raise RuntimeError(f'{directory}: installed runtime files differ from their pinned original archive')


def collect(reports: list[Path]) -> tuple[dict[str, tuple[bytes, int]], dict]:
    lock, identity, build = check_build_identity()
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
        for name in tree_hashes(directory):
            add(f'{directory}/{name}', source=source)

    for name in ('LICENSE', *RUNTIME, 'runtime/CUTADAPT.md',
                 'scripts/build-cutadapt.py', 'scripts/package-cutadapt.py', *VALIDATION,
                 'scripts/test-cutadapt-demo.mjs', 'scripts/fetch-cutadapt-test-data.py',
                 'tests/test_cutadapt_compare.py', 'tests/test_cutadapt_package.py'):
        add(name)
    for directory in (TOOL, EXAMPLE, 'validation/cutadapt', DATA):
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
    for record in read_json(f'{TOOL}/runtime-sources.json'):
        if record['archive'] not in archives or archives[record['archive']]['sha256'] != record['sha256']:
            raise RuntimeError('An audited Python runtime source is missing from the source lock')
        for notice in record['licensePaths']:
            required(local_path(notice))
    for notice in set(lock['requiredNotices']) | {'LICENSE', 'LICENSE.dnaio', 'LICENSE.xopen', 'THIRD_PARTY_LICENSES.txt'}:
        required(local_path(f'{TOOL}/{relative_name(notice)}'))
    for notice in read_json(f'{TOOL}/runtime-notices/manifest.json'):
        name = relative_name(notice['destination'])
        if not name.startswith(f'{TOOL}/runtime-notices/'):
            raise RuntimeError('Extracted runtime notice lies outside its expected directory')
        verify(local_path(name), notice['sha256'])
    for dependency, prefix, directory in (
        ('pyodide-core', 'pyodide/', f'{DIST}/pyodide'),
        ('runtime-ws', 'package/', f'{DIST}/node_modules/ws'),
    ):
        record = lock['dependencies'][dependency]
        data = entries[f'{DIST}/sources/{record["archive"]}'][0]
        check_archive_tree(data, prefix, directory, selected=PYODIDE_FILES if dependency == 'pyodide-core' else None)

    if not manifest.get('sources'):
        raise RuntimeError('The Cutadapt fixture manifest omits original source archives')
    for name, record in manifest['sources'].items():
        cache = relative_name(record['cachePath'])
        if not cache.startswith('build/cutadapt-sources/'):
            raise RuntimeError(f'Dataset source cache lies outside its expected directory: {name}')
        data = verify(local_path(cache), record['sha256'])
        if record.get('bytes') != len(data):
            raise RuntimeError(f'Dataset source byte count differs: {name}')
        put(cache, data)
    for name, info in manifest['regeneration'].get('files', {}).items():
        add(name, expected=info['sha256'])
    config = read_json(f'{EXAMPLE}/config.json')
    if set(config.get('datasets', {})) != {'human', 'yeast'} or 'paired' not in config.get('commands', {}):
        raise RuntimeError('Standalone browser configuration omits required examples')
    playwright = read_json('package.json')['devDependencies']['playwright']
    if not re.fullmatch(r'\d+\.\d+\.\d+', playwright):
        raise RuntimeError('Playwright must be pinned to an exact version')
    package = {'name': 'cutadapt-wasm', 'version': lock['version'], 'private': True, 'type': 'module',
               'scripts': {'test': 'python3 -m unittest discover -s tests -p "test_cutadapt_*.py"',
                           'validate': 'node scripts/validate-cutadapt.mjs'},
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
        'schemaVersion': 1, 'tool': 'cutadapt', 'upstreamVersion': lock['version'],
        'upstreamLockSha256': digest(required(local_path(f'{TOOL}/sources.lock.json'))),
        'files': dict(sorted(sources.items())),
        'notes': 'Includes original package and runtime sources, build inputs, patches, recipes, licence notices and real-data provenance.',
    }), source=False)
    put('release.json', json_bytes({
        'schemaVersion': 1, 'tool': 'cutadapt', 'upstreamVersion': lock['version'],
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
    if manifest.get('schema_version') != 1 or set(manifest.get('datasets', {})) != {'human', 'yeast'}:
        raise RuntimeError('The Cutadapt manifest must identify both real datasets')
    if not manifest.get('files'):
        raise RuntimeError('The Cutadapt manifest contains no checked files')
    for name, info in manifest['files'].items():
        data = verify(local_path(f'{DATA}/{relative_name(name)}'), info['sha256'])
        if type(info.get('bytes')) is not int or info['bytes'] != len(data):
            raise RuntimeError(f'Dataset byte count differs from its manifest: {name}')
    return manifest, digest(raw)


def planned_cases() -> dict:
    """Expand the checked-in plan; a report cannot reduce its own required scope."""
    plan = read_json('validation/cutadapt/cases.json')
    if plan.get('schema') != 1 or set(plan.get('datasets', [])) != {'human', 'yeast'}:
        raise RuntimeError('Unknown Cutadapt case-plan schema')
    result = {}
    for spec in plan['cases']:
        if spec.get('kind') not in ('success', 'repeat', 'failure', 'restriction') or 'nativeErrorPolicy' in spec:
            raise RuntimeError('Unsupported Cutadapt validation case kind or signal exception')
        datasets = [None] if spec.get('oncePerTool') else spec.get('datasets', plan['datasets'])
        for dataset in datasets:
            if dataset is not None and dataset not in plan['datasets']:
                raise RuntimeError(f'Unknown Cutadapt case dataset: {dataset}')
            case_id = f'cutadapt-{dataset}-{spec["id"]}' if dataset else f'cutadapt-{spec["id"]}'
            if case_id in result:
                raise RuntimeError(f'Duplicate planned case: {case_id}')
            result[case_id] = {**spec, '_dataset': dataset or plan['datasets'][0]}
    if not result:
        raise RuntimeError('Empty Cutadapt validation case plan')
    return result


def dataset_identity(manifest: dict, manifest_sha: str) -> dict:
    datasets = {}
    for dataset, metadata in manifest['datasets'].items():
        counts, bases, names = [], [], []
        for key in ('read1', 'read2'):
            name = relative_name(metadata[key])
            zipped = relative_name(metadata[key + 'Gzip'])
            if not name.startswith(dataset + '/') or not zipped.startswith(dataset + '/'):
                raise RuntimeError(f'{dataset}: fixture paths do not belong to this dataset')
            raw = required(local_path(f'{DATA}/{name}'))
            if gzip.decompress(required(local_path(f'{DATA}/{zipped}'))) != raw:
                raise RuntimeError(f'{dataset}: gzip fixture differs from its plain FASTQ')
            rows = raw.decode('ascii').splitlines()
            if not rows or len(rows) % 4:
                raise RuntimeError(f'{dataset}: incomplete FASTQ fixture')
            identifiers = []
            for offset in range(0, len(rows), 4):
                title, sequence, plus, qualities = rows[offset:offset + 4]
                if not title.startswith('@') or not plus.startswith('+') or len(sequence) != len(qualities):
                    raise RuntimeError(f'{dataset}: malformed FASTQ fixture')
                token = title[1:].split()[0]
                identifiers.append(token[:-2] if token.endswith(('/1', '/2')) else token)
            counts.append(len(rows) // 4)
            bases.append(sum(len(row) for row in rows[1::4]))
            names.append(identifiers)
        if counts[0] != counts[1] or names[0] != names[1]:
            raise RuntimeError(f'{dataset}: fixture mates have different names or counts')
        datasets[dataset] = {
            'manifestSha256': manifest_sha,
            'files': {name: info['sha256'] for name, info in manifest['files'].items() if name.startswith(dataset + '/')},
            'observations': metadata['observations'], 'readPairs': counts[0], 'inputBases': bases,
        }
    return datasets


def check_reports(reports: list[Path], build: dict) -> dict:
    checked = {}
    runtime = {name: digest(required(local_path(name))) for name in RUNTIME}
    validation = {name: digest(required(local_path(name))) for name in VALIDATION}
    manifest, manifest_sha = checked_dataset_manifest()
    datasets = dataset_identity(manifest, manifest_sha)
    plan = planned_cases()
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
        if report.get('builds') != {'cutadapt': build}:
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
        for case_id, spec in plan.items():
            record = by_id[case_id]
            for key, value in {'tool': 'cutadapt', 'dataset': spec['_dataset'], 'case': spec['id'],
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
            if failure and len({execution['canonicalExitCode'] for execution in executions.values()}) != 1:
                raise RuntimeError(f'{case_id}: native and WebAssembly failure exits disagree')
            comparisons = record.get('comparisons', {})
            if set(comparisons) != set(spec['comparisons']):
                raise RuntimeError(f'{case_id}: required semantic comparisons are missing')
            for target, comparison in comparisons.items():
                if (comparison.get('equal') is not True
                        or not all(isinstance(comparison.get(key), str) and SHA256.fullmatch(comparison[key])
                                   for key in ('expectedSemanticDigest', 'semanticDigest', 'biologicalDigest'))
                        or comparison['expectedSemanticDigest'] != comparison['semanticDigest']
                        or not comparison.get('invariants')):
                    raise RuntimeError(f'{case_id}: semantic comparison did not pass for {target}')
            if spec.get('effect') or spec.get('equivalentTo'):
                baseline = spec.get('effect') or spec['equivalentTo']
                before = by_id[f'cutadapt-{spec["_dataset"]}-{baseline}']['comparisons']['native-contract']['biologicalDigest']
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
    command = config['commands']['paired']
    return f'''# Cutadapt {version} for the browser

This standalone package contains Cutadapt and its compiled Python extensions, the local Pyodide runtime, a JavaScript runner, a browser example, real human and yeast reads, validation reports, sources and licence notices.

## Run the example

Run `python3 -m http.server 8000` from this directory and open `http://localhost:8000/examples/cutadapt/`. Load the example reads or upload your own files, edit the arguments, then run Cutadapt. Results can be viewed and downloaded. Computation happens in a fresh browser worker; the server only serves files. Cross-origin isolation is not required.

Keep the complete `dist/cutadapt/` directory together. The browser loads its Python runtime and wheels locally and does not download packages from a public Python package service. Browser memory limits apply. See `tools/cutadapt/README.md` for supported options and restrictions.

## JavaScript API

```js
import {{ CutadaptRunner }} from './runtime/cutadapt.mjs';
const runner = new CutadaptRunner({{
  baseUrl: new URL('./dist/cutadapt/', import.meta.url),
}});
const result = await runner.run({json.dumps(command['args'])}, {{
  files: inputs, // [{{path: 'reads_1.fastq', data: Uint8Array}}, ...]
  outputs: {json.dumps(command['outputs'])},
  timeoutMs: 600000,
}});
```

Check `result.exitCode` and `result.missingOutputs`. Output files are returned in `result.files`; standard output and error are byte arrays. An abort signal cancels the command. See `runtime/CUTADAPT.md` for the interface, including standard input and directory outputs.

## Rebuild and validate

The build prerequisites and commands are documented in `tools/cutadapt/README.md`. Original archives, dependency sources, patches and build recipes are included. The browser and Node runtime assets are local. Rebuilding requires the separately installed host prerequisites and downloads the checksum-pinned compiler and Node toolchain archives; those host binaries are not included in this ZIP. The Node runtime dependency is vendored. Run `npm ci` to install the pinned browser-test tools.

```sh
python3 scripts/fetch-cutadapt-test-data.py --verify
node scripts/validate-cutadapt.mjs --engine node --dataset all
npx playwright install chromium firefox webkit
```

Repeat validation with `--engine chromium`, `--engine firefox` and `--engine webkit`. Run the browser interface check with `BROWSER=chromium node scripts/test-cutadapt-demo.mjs`, changing the browser name for the other engines. The fixture README explains exact regeneration from the included human snapshots and original yeast archives.

The supplied reports identify the exact runtime, compiled assets, native comparison environments, inputs, commands and output comparisons. They check ordered read names, sequences, qualities and Cutadapt JSON statistics. They cover the included small datasets and listed options; they do not establish performance or correctness for every possible input. Human reads were recovered from an already processed BAM file; yeast reads were selected from real FASTQ records to make trimming effects visible. See `datasets/cutadapt/README.md` for provenance and limitations.

`release.json` records the validated engines. Verify all package files with `sha256sum -c SHA256SUMS`.

## Licences

`LICENSE` covers this project's original infrastructure. Preserve the upstream sources, patches, build recipes, data provenance and separate notices in `tools/cutadapt/` and `dist/cutadapt/notices/` when redistributing.
'''


def write_package(output: Path, reports: list[Path], *, prepared=None) -> Path:
    entries, lock = prepared if prepared is not None else collect(reports)
    stem = f'cutadapt-{lock["version"]}-wasm'
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
    except (RuntimeError, ValueError, KeyError, OSError, TypeError, zipfile.BadZipFile) as error:
        print(f'Packaging failed: {error}', file=sys.stderr)
        raise SystemExit(1)
