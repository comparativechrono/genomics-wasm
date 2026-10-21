#!/usr/bin/env python3
"""Package one pinned HTS release after complete native and browser validation."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import zipfile
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ('1.16', '1.21')
PROGRAMS = ('bcftools', 'samtools', 'bgzip', 'tabix', 'htsfile')
ENGINES = {'node', 'chromium', 'firefox', 'webkit'}
RUNTIME = ('runtime/index.mjs', 'runtime/worker.mjs', 'runtime/execute.mjs', 'runtime/hts.mjs')
VALIDATION = ('scripts/validate-hts.mjs', 'validation/hts/compare.py', 'validation/hts/cases.json',
              'scripts/fetch-hts-test-data.py', 'validation/hts/portability_fixtures.py',
              'datasets/hts/provenance/sources.lock.json')
SHA256 = re.compile(r'[0-9a-f]{64}')
WASM_HEADER = b'\0asm\1\0\0\0'
DATA = 'datasets/hts'
EXAMPLE = 'examples/hts'


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()


def relative_name(name: str) -> str:
    if (not isinstance(name, str) or not name or '\\' in name or any(ord(c) < 32 for c in name)
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


def tree_hashes(directory: str, *, exclude=(), source=False) -> dict[str, str]:
    base = local_path(directory)
    if not base.is_dir() or base.is_symlink():
        raise RuntimeError(f'Required package directory is missing or unsafe: {directory}')
    result = {}
    for path in sorted(base.rglob('*')):
        if source and ('__pycache__' in path.relative_to(base).parts or path.suffix in ('.pyc', '.pyo')):
            continue
        if path.is_symlink():
            raise RuntimeError(f'Package directory contains a symlink: {path}')
        if path.is_file():
            name = relative_name(path.relative_to(base).as_posix())
            if name not in exclude:
                result[name] = digest(required(path))
    return result


def source_records(lock: dict, version: str) -> dict[str, dict]:
    records = {}
    selected = lock['versions'][version]
    if set(selected.get('components', {})) != {'bcftools', 'samtools', 'htslib'}:
        raise RuntimeError(f'{version}: the three matching upstream components are required')
    for component in selected['components'].values():
        if component.get('version') != version:
            raise RuntimeError(f'{version}: mixed upstream component versions')
    for record in [*selected['components'].values(), *lock.get('dependencies', {}).values()]:
        name = relative_name(record['archive'])
        if (name in records or not SHA256.fullmatch(record.get('sha256', ''))
                or type(record.get('bytes')) is not int or record['bytes'] <= 0
                or not isinstance(record.get('url'), str) or not record['url'].startswith('https://')
                or not record.get('license')):
            raise RuntimeError(f'{version}: invalid, unlicensed or duplicate source archive: {name}')
        records[name] = record
    return records


def check_wasm_archive(data: bytes, name: str) -> None:
    if not data.startswith(b'!<arch>\n'):
        raise RuntimeError(f'{name}: invalid static WebAssembly archive')
    offset, objects = 8, 0
    while offset < len(data):
        header = data[offset:offset + 60]
        if len(header) != 60 or header[58:60] != b'`\n':
            raise RuntimeError(f'{name}: corrupt static archive header')
        try:
            length = int(header[48:58])
        except ValueError as error:
            raise RuntimeError(f'{name}: invalid archive member length') from error
        end = offset + 60 + length
        if length < 0 or end > len(data):
            raise RuntimeError(f'{name}: truncated archive member')
        if header[:16].strip() not in (b'/', b'//', b'/SYM64/'):
            if not data[offset + 60:end].startswith(WASM_HEADER):
                raise RuntimeError(f'{name}: archive contains a non-WebAssembly object')
            objects += 1
        offset = end + length % 2
    if offset != len(data) or not objects:
        raise RuntimeError(f'{name}: archive contains no valid WebAssembly objects')


def check_build_identity(version: str) -> tuple[dict, dict, dict]:
    if version not in VERSIONS:
        raise RuntimeError(f'Unsupported release: {version}')
    lock = read_json('tools/hts/versions.lock.json')
    selected = lock.get('versions', {}).get(version, {})
    if (lock.get('schema') != 1 or lock.get('tool') != 'hts' or selected.get('version') != version
            or lock.get('programs') != list(PROGRAMS)):
        raise RuntimeError(f'{version}: invalid pinned release contract')
    records = source_records(lock, version)
    base = f'dist/hts-{version}'
    raw = required(local_path(f'{base}/build-manifest.json'))
    identity = json.loads(raw)
    assets = tree_hashes(base, exclude=('build-manifest.json',))
    if (identity.get('schema') != 1 or identity.get('tool') != 'hts' or identity.get('version') != version
            or identity.get('programs') != list(PROGRAMS) or identity.get('assets') != assets
            or not re.search(r'(?<![0-9.])' + re.escape(lock['emscripten']) + r'(?![0-9.])', identity.get('compiler', ''))):
        raise RuntimeError(f'{version}: compiled assets, version or compiler differ from the pinned build')
    plugins = selected.get('plugins', [])
    if (not plugins or len(plugins) != len(set(plugins)) or identity.get('plugins') != plugins
            or not {'split-vep', 'fill-tags', 'trio-dnm2', 'trio-stats', 'trio-switch-rate', 'parental-origin'} <= set(plugins)):
        raise RuntimeError(f'{version}: built-in plugin inventory differs from the pinned release')
    expected_inputs = {f'tools/hts/{name}' for name in tree_hashes('tools/hts', source=True)}
    expected_inputs.update(('scripts/build-hts.py', 'scripts/setup-emsdk.sh'))
    inputs = identity.get('inputs', {})
    if not isinstance(inputs, dict) or not expected_inputs <= inputs.keys():
        raise RuntimeError(f'{version}: build manifest omits recipe or source inputs')
    for name, checksum in inputs.items():
        verify(local_path(name), checksum)
    for program in PROGRAMS:
        if (not required(local_path(f'{base}/{program}.wasm')).startswith(WASM_HEADER)
                or not required(local_path(f'{base}/{program}.mjs'))):
            raise RuntimeError(f'{version}: missing WebAssembly executable: {program}')
    for name in ('htslib/libhts.a', 'htslib/lib/libz.a', 'htslib/lib/libbz2.a', 'htslib/lib/liblzma.a'):
        check_wasm_archive(required(local_path(f'{base}/{name}')), name)
    for name in ('htslib/include/htslib/hts.h', 'htslib/include/htslib/vcf.h', 'htslib/include/htslib/sam.h',
                 'htslib/include/zlib.h', 'htslib/include/bzlib.h', 'htslib/include/lzma.h', 'htslib/README.md'):
        if not required(local_path(f'{base}/{name}')):
            raise RuntimeError(f'{version}: missing advertised SDK file: {name}')
    for name in assets:
        data = required(local_path(f'{base}/{name}'))
        if data.startswith(b'\x7fELF') or name.endswith(('.so', '.dll', '.dylib', '.exe')):
            raise RuntimeError(f'{version}: desktop binary or dynamic plugin in browser bundle: {name}')
        if name.endswith('.a'):
            check_wasm_archive(data, name)
    native = identity.get('nativeFiles', {})
    if set(native) != {'native', 'patched-native'}:
        raise RuntimeError(f'{version}: both native reference environments must be bound')
    for kind, hashes in native.items():
        if hashes != tree_hashes(f'build/{kind}/hts-{version}'):
            raise RuntimeError(f'{version}: {kind} files differ from the validated build')
    if set(tree_hashes(f'{base}/sources')) != set(records):
        raise RuntimeError(f'{version}: source archives are missing or unpinned')
    for name, record in records.items():
        if len(verify(local_path(f'{base}/sources/{name}'), record['sha256'])) != record['bytes']:
            raise RuntimeError(f'{version}: source archive length differs: {name}')
    notices = selected.get('requiredNotices', [])
    if not notices or len(notices) != len(set(notices)):
        raise RuntimeError(f'{version}: missing or duplicate required licence notices')
    if {f'notices/{name}' for name in tree_hashes(f'{base}/notices')} != set(notices):
        raise RuntimeError(f'{version}: distributed notice inventory differs from the source lock')
    for name in notices:
        if not name.startswith('notices/') or required(local_path(f'{base}/{name}')) != required(local_path(f'tools/hts/{name}')):
            raise RuntimeError(f'{version}: distributed licence notice differs: {name}')
    return lock, identity, {'assets': assets, 'buildManifest': digest(raw), 'inputs': inputs, 'nativeFiles': native, 'plugins': plugins}


def checked_datasets() -> tuple[dict, str]:
    raw = required(local_path(f'{DATA}/manifest.json'))
    manifest = json.loads(raw)
    if manifest.get('schema') != 1 or manifest.get('tool') != 'hts' or not manifest.get('files'):
        raise RuntimeError('Invalid HTS fixture manifest')
    for name, info in manifest['files'].items():
        if type(info.get('bytes')) is not int or len(verify(local_path(f'{DATA}/{relative_name(name)}'), info['sha256'])) != info['bytes']:
            raise RuntimeError(f'Fixture length differs from its manifest: {name}')
    actual = {f'{directory}/{name}': sha for directory in ('common', 'upstream')
              for name, sha in tree_hashes(f'{DATA}/{directory}').items()}
    if actual != {name: item['sha256'] for name, item in manifest['files'].items()}:
        raise RuntimeError('Every fixture must be identified')
    sources = json.loads(verify(local_path(f'{DATA}/provenance/sources.lock.json'), manifest['sourcesLockSha256']))
    if sources.get('schema') != 1 or not sources.get('licence'):
        raise RuntimeError('Missing original fixture provenance or licence')
    original = {}
    for source in sources.get('files', []):
        name = relative_name(source['path'])
        if (name in original or not name.startswith('upstream/')
                or not re.fullmatch('[0-9a-f]{40}', source.get('commit', ''))
                or source.get('url') != f'https://raw.githubusercontent.com/samtools/bcftools/{source["commit"]}/{relative_name(source["sourcePath"])}'):
            raise RuntimeError(f'Unpinned or duplicate original fixture source: {name}')
        original[name] = {'sha256': source['sha256'], 'bytes': source['bytes']}
    if original != {name: info for name, info in manifest['files'].items() if name.startswith('upstream/')}:
        raise RuntimeError('Original fixture bytes differ from their pinned upstream sources')
    for version in VERSIONS:
        if f'upstream/{version}/LICENSE' not in original:
            raise RuntimeError(f'{version}: missing upstream fixture licence')
    return manifest, digest(raw)


def planned_cases(version: str) -> dict:
    plan = read_json('validation/hts/cases.json')
    if plan.get('schema') != 1 or plan.get('tool') != 'hts' or version not in plan.get('versions', []):
        raise RuntimeError('Invalid HTS scientific case plan')
    cases = {}
    for case in plan.get('cases', []):
        if version not in case.get('versions', VERSIONS):
            continue
        name = case.get('id')
        if not isinstance(name, str) or not name or name in cases or case.get('kind') not in ('success', 'failure'):
            raise RuntimeError('Duplicate or invalid scientific case')
        if not case.get('steps'):
            raise RuntimeError(f'{name}: no executed commands')
        for step in case['steps']:
            if step.get('tool') not in PROGRAMS or not isinstance(step.get('args'), list) or any(not isinstance(arg, str) for arg in step['args']):
                raise RuntimeError(f'{name}: invalid scientific command')
        for plugin in case.get('plugins', []):
            if case['kind'] != 'success' or not any(step['tool'] == 'bcftools' and
                    (step['args'][:1] == ['+' + plugin] or step['args'][:2] == ['plugin', plugin]) for step in case['steps']):
                raise RuntimeError(f'{name}: declared plugin is not exercised successfully')
        cases[name] = case
    if not cases or not any(case.get('artifacts') and case['kind'] == 'success' for case in cases.values()):
        raise RuntimeError('Scientific plan contains no output comparisons')
    return cases


def check_reports(version: str, paths: list[Path], build: dict, dataset: dict, dataset_sha: str) -> dict:
    """Bind recorded commands to actual evidence; reuse the scientific comparator.

    This reads saved output bytes and rechecks semantic comparisons. It never
    launches a native or WASM executable while packaging.
    """
    plan = planned_cases(version)
    runtime = {name: digest(required(local_path(name))) for name in RUNTIME}
    validation = {name: digest(required(local_path(name))) for name in VALIDATION}
    coverage = sorted({plugin for case in plan.values() for plugin in case.get('plugins', [])})
    if coverage != sorted(build['plugins']):
        raise RuntimeError(f'{version}: scientific cases do not cover every built-in plugin')
    module_spec = importlib.util.spec_from_file_location('hts_release_comparator', local_path('validation/hts/compare.py'))
    comparator = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(comparator)
    reports, run_ids, biology = {}, set(), {}
    for path in paths:
        path = path.resolve()
        raw = required(path)
        report = json.loads(raw)
        if report.get('version') != version:
            continue
        engine = report.get('engine')
        if engine not in ENGINES or engine in reports:
            raise RuntimeError(f'{version}: unknown or duplicate validation engine')
        run_id = report.get('runId')
        if (not isinstance(run_id, str) or str(UUID(run_id)) != run_id or run_id in run_ids
                or report.get('reportDirectory') != run_id):
            raise RuntimeError(f'{version}/{engine}: missing or reused independent run identity')
        run_ids.add(run_id)
        if (report.get('schema') != 1 or report.get('tool') != 'hts' or report.get('status') != 'passed'
                or report.get('crossOriginIsolated') is not False or not report.get('node')
                or (engine != 'node' and not report.get('browserVersion'))
                or report.get('build') != build or report.get('runtime') != runtime
                or report.get('validation') != validation or report.get('datasetManifestSha256') != dataset_sha
                or report.get('pluginCoverage') != coverage or report.get('expectedCases') != list(plan)):
            raise RuntimeError(f'{version}/{engine}: stale, incomplete or failed validation identity')
        records = report.get('cases', [])
        if [record.get('id') for record in records] != list(plan):
            raise RuntimeError(f'{version}/{engine}: scientific case inventory differs')
        evidence = path.parent / run_id
        for record in records:
            case = plan[record['id']]
            case_id = relative_name(case['id'])
            failure = case['kind'] == 'failure'
            if (record.get('status') != 'passed' or record.get('kind') != case['kind']
                    or record.get('plugins') != case.get('plugins', []) or record.get('steps') != case['steps']
                    or set(record.get('executions', {})) != {'native', 'patched-native', 'wasm'}):
                raise RuntimeError(f'{case_id}: scientific commands or targets differ')
            initial = {}
            for source in case.get('inputs', []):
                source_name = relative_name(source['source'].replace('{version}', version))
                name = relative_name(source['as'])
                if name in initial or source_name not in dataset['files']:
                    raise RuntimeError(f'{case_id}: unbound or duplicate scientific input')
                initial[name] = dataset['files'][source_name]
            codes, final_outputs = [], {}
            for target, executions in record['executions'].items():
                if len(executions) != len(case['steps']):
                    raise RuntimeError(f'{case_id}/{target}: missing command evidence')
                directory = evidence / case_id / target
                workspace = {name: dict(info) for name, info in initial.items()}
                for name, info in initial.items():
                    if len(verify(directory / 'inputs' / name, info['sha256'])) != info['bytes']:
                        raise RuntimeError(f'{case_id}: recorded input length differs')
                final_outputs[target] = {}
                for index, (step, execution) in enumerate(zip(case['steps'], executions)):
                    code = execution.get('exitCode')
                    canonical = code & 255 if type(code) is int and code < 0 else code
                    if (execution.get('step') != index or execution.get('tool') != step['tool']
                            or execution.get('args') != step['args'] or execution.get('oracle', False) != step.get('oracle', False)
                            or execution.get('inputs') != {name: info['sha256'] for name, info in workspace.items()}
                            or execution.get('inputBytes') != {name: info['bytes'] for name, info in workspace.items()}
                            or type(code) is not int or execution.get('canonicalExitCode') != canonical
                            or 'signal' not in execution or execution['signal'] is not None
                            or (canonical == 0) == failure or execution.get('missingOutputs') != []
                            or ('expectedExitCode' in case and canonical != case['expectedExitCode'])):
                        raise RuntimeError(f'{case_id}/{target}/{index}: command, input, exit or output evidence differs')
                    codes.append(canonical)
                    step_dir = directory / 'steps' / str(index)
                    if json.loads(required(step_dir / 'command-result.json')) != execution:
                        raise RuntimeError(f'{case_id}: summary differs from the recorded command result')
                    if target != 'wasm' or step.get('oracle'):
                        origin = 'native' if step.get('oracle') else target
                        command = {'program': f'build/{origin}/hts-{version}/{step["tool"]}', 'args': step['args']}
                        if execution.get('command') != command:
                            raise RuntimeError(f'{case_id}: native command or oracle identity differs')
                    elif 'command' in execution:
                        raise RuntimeError(f'{case_id}: WebAssembly result claims a native command')
                    streams = {}
                    for stream in ('stdout', 'stderr'):
                        data = verify(step_dir / f'{stream}.txt', execution.get(stream))
                        if type(execution.get(stream + 'Bytes')) is not int or len(data) != execution[stream + 'Bytes']:
                            raise RuntimeError(f'{case_id}: {stream} byte length differs')
                        streams[stream] = data
                    if failure and (execution.get('diagnosticMatched') is not True or not case.get('diagnosticRegex')
                                    or not re.search(case['diagnosticRegex'], (streams['stdout'] + streams['stderr']).decode(errors='replace'), re.I)):
                        raise RuntimeError(f'{case_id}: missing required failure diagnostic')
                    outputs = set(step.get('outputs', [])) | {step[key] for key in ('stdoutFile', 'stderrFile') if key in step}
                    files, lengths = execution.get('files', {}), execution.get('fileBytes', {})
                    if set(files) != outputs or set(lengths) != outputs:
                        raise RuntimeError(f'{case_id}: output inventory differs from the command')
                    for name in outputs:
                        relative_name(name)
                        data = verify(step_dir / 'files' / name, files[name])
                        if type(lengths[name]) is not int or len(data) != lengths[name]:
                            raise RuntimeError(f'{case_id}: output byte length differs')
                        for stream in streams:
                            if step.get(stream + 'File') == name and streams[stream] != data:
                                raise RuntimeError(f'{case_id}: redirected output differs from captured bytes')
                        workspace[name] = {'sha256': files[name], 'bytes': lengths[name]}
                        final_outputs[target][name] = workspace[name]
                for artifact in case.get('artifacts', []):
                    name = relative_name(artifact['path'])
                    info = final_outputs[target].get(name)
                    if not info or len(verify(directory / name, info['sha256'])) != info['bytes']:
                        raise RuntimeError(f'{case_id}: comparison artifact is not an executed output')
            if failure:
                if len(set(codes)) != 1 or record.get('comparisons') != {}:
                    raise RuntimeError(f'{case_id}: failure behavior differs between targets')
            else:
                comparisons = record.get('comparisons', {})
                if set(comparisons) != {'native-contract', 'patched-native', 'wasm'}:
                    raise RuntimeError(f'{case_id}: missing scientific comparison')
                for kind, target in (('native-contract', 'native'), ('patched-native', 'patched-native'), ('wasm', 'wasm')):
                    observed = comparator.compare(evidence / case_id / 'native', evidence / case_id / target, case, version)
                    if observed.get('equal') is not True or comparisons[kind] != observed:
                        raise RuntimeError(f'{case_id}/{kind}: comparison differs from executed output bytes or fixture truths')
                    if biology.setdefault(case_id, observed['biologicalDigest']) != observed['biologicalDigest']:
                        raise RuntimeError(f'{case_id}: scientific results differ between validation engines')
        reports[engine] = raw
    if set(reports) != ENGINES:
        raise RuntimeError(f'{version}: require independent passing Node, Chromium, Firefox and WebKit reports')
    return reports


def readme(version: str, plugins: list[str]) -> str:
    return f'''# HTS {version} WebAssembly bundle

This standalone release contains matching BCFtools, SAMtools and HTSlib {version},
plus bgzip, tabix and htsfile. All {len(plugins)} stock BCFtools plugins are compiled
into the executable, including `+split-vep`, `+fill-tags` and the trio plugins.
No plugin search path or separate shared-library files are needed.

## Run the browser demo

From this extracted directory run:

```sh
python3 -m http.server 8000 --bind 127.0.0.1
```

Open <http://127.0.0.1:8000/examples/hts/>. The demo runs locally in a worker and
provides uploads, editable argument arrays, output previews and downloads.
Use HTTP(S), not `file://`. Cross-origin isolation is not required. Keep the
`runtime/`, `dist/`, `datasets/` and `examples/` relative paths intact.

## JavaScript API

```js
import {{ HTSRunner }} from './runtime/hts.mjs';
const runner = new HTSRunner({{ version: '{version}', tool: 'bcftools' }});
const result = await runner.run(['+split-vep', 'input.vcf', '-l'], {{
  files: [{{ path: 'input.vcf', data: vcfBytes }}], timeoutMs: 180000,
}});
console.log(result.exitCode, new TextDecoder().decode(result.stdout));
```

The same API supports `samtools`, `bgzip`, `tabix` and `htsfile`. See
`runtime/HTS.md` for file paths, output collection, cancellation and restrictions.
Calls run in fresh workers. Browser files and outputs occupy memory; large
whole-genome workloads and throughput have not been established by small tests.
This build uses local files and a single execution thread; interactive terminal
interfaces and HTSlib remote protocols are outside this browser profile.

## Source, SDK and checks

`dist/hts-{version}/htslib/` contains the matching WebAssembly static library,
dependency libraries and headers, with link instructions in its README.
`tests/fixtures/hts-sdk.c` and `scripts/test-hts-sdk.py` exercise a real C consumer
of those headers and archives. After rebuilding the original native reference
and installing the pinned Emscripten toolchain, run
`python3 scripts/test-hts-sdk.py --version {version}` for the SDK comparison.
Pinned original sources, patches, recipes and licences are included. Build
instructions are in `tools/hts/README.md`; host build tools are prerequisites.
To verify bytes and exercise the extracted archive independently:

```sh
sha256sum -c SHA256SUMS
npm ci
npm test
node scripts/test-hts-package.mjs --version {version}
npx playwright install --with-deps chromium firefox webkit
HTS_VERSION={version} BROWSER=chromium node scripts/test-hts-demo.mjs
HTS_VERSION={version} BROWSER=firefox node scripts/test-hts-demo.mjs
HTS_VERSION={version} BROWSER=webkit node scripts/test-hts-demo.mjs
```

Four passing scientific reports are under `validation-reports/`. They identify
the exact compiled assets, native references, runtime, fixtures, commands and
output comparisons. The package gate verifies those recorded identities and
comparison evidence; it does not rerun native comparisons while assembling the
archive. Tests cover the listed fixtures and options, not every possible input.

`LICENSE` covers this project's infrastructure. Retain upstream notices,
corresponding sources and recipes when redistributing. Dependency notices are
under `dist/hts-{version}/notices/`; fixture provenance is under `datasets/hts/`.
'''


def collect(version: str, reports: list[Path]) -> tuple[dict[str, tuple[bytes, int]], dict]:
    lock, identity, build = check_build_identity(version)
    dataset, dataset_sha = checked_datasets()
    checked = check_reports(version, reports, build, dataset, dataset_sha)
    entries, sources = {}, {}
    first_report = json.loads(next(iter(checked.values())))
    anchors = {**identity['inputs'], **first_report['runtime'], **first_report['validation'],
               f'{DATA}/manifest.json': dataset_sha,
               **{f'{DATA}/{name}': info['sha256'] for name, info in dataset['files'].items()},
               f'dist/hts-{version}/build-manifest.json': build['buildManifest'],
               **{f'dist/hts-{version}/{name}': checksum for name, checksum in identity['assets'].items()}}

    def put(name: str, data: bytes, mode=0o644, *, source=True):
        relative_name(name)
        if name in entries and entries[name][0] != data:
            raise RuntimeError(f'Conflicting package entries: {name}')
        entries[name] = (data, mode)
        if source:
            sources[name] = digest(data)

    def add(name: str, *, source=True, expected=None):
        path = local_path(name)
        expected = expected or anchors.get(name)
        data = verify(path, expected) if expected else required(path)
        put(name, data, 0o755 if path.stat().st_mode & 0o111 else 0o644, source=source)

    def tree(directory: str, *, source=True):
        for name in tree_hashes(directory, source=source):
            add(f'{directory}/{name}', source=source)

    for name in ('LICENSE', *RUNTIME, 'runtime/HTS.md', *VALIDATION, 'scripts/package-hts.py',
                 'scripts/test-hts-package.mjs', 'scripts/test-hts-demo.mjs', 'scripts/fetch-hts-test-data.py',
                 'scripts/test-hts-sdk.py', 'tests/fixtures/hts-sdk.c',
                 'tests/runtime.test.mjs', 'tests/fixtures/mock-tool.mjs'):
        add(name)
    matches = sorted((ROOT / 'tests').glob('test_hts_*.py'))
    if not matches:
        raise RuntimeError('Missing standalone HTS unit tests')
    for path in matches:
        add(path.relative_to(ROOT).as_posix())
    for directory in ('tools/hts', 'validation/hts', DATA, EXAMPLE):
        tree(directory)
    for name, checksum in identity['inputs'].items():
        add(name, expected=checksum)
    tree(f'dist/hts-{version}', source=False)
    for name, info in source_records(lock, version).items():
        sources[f'dist/hts-{version}/sources/{name}'] = info['sha256']
    # Upstream fixtures are unchanged originals. Populate the recipe's cache
    # from these verified bytes so regeneration also works without a network.
    for record in read_json(f'{DATA}/provenance/sources.lock.json')['files']:
        name = relative_name(record['path'])
        put(f'build/hts-fixture-sources/{name}', verify(local_path(f'{DATA}/{name}'), record['sha256']))
    config = read_json(f'{EXAMPLE}/config.json')
    if version not in config.get('versions', []) or not set(config['versions']) <= set(VERSIONS):
        raise RuntimeError('Demo version inventory does not contain the packaged release')
    config['versions'] = [version]
    # This documented projection is the only changed demo input: one ZIP ships
    # exactly one build, so the selector must never offer an absent version.
    # Presets execute in insertion order; sorting would index before sorting BAM.
    config_bytes = (json.dumps(config, indent=2) + '\n').encode()
    entries[f'{EXAMPLE}/config.json'] = (config_bytes, 0o644)
    sources[f'{EXAMPLE}/config.json'] = digest(config_bytes)
    playwright = read_json('package.json')['devDependencies']['playwright']
    if not re.fullmatch(r'\d+\.\d+\.\d+', playwright):
        raise RuntimeError('Playwright must use an exact pinned version')
    package = {'name': f'hts-{version}-wasm', 'version': f'{version}.0', 'private': True, 'type': 'module',
               'scripts': {'test': 'python3 -m unittest discover -s tests -p "test_hts_*.py" && node --test tests/runtime.test.mjs',
                           'validate': f'node scripts/validate-hts.mjs --version {version}'},
               'devDependencies': {'playwright': playwright}, 'engines': {'node': '>=20'}}
    npm_lock = read_json('package-lock.json')
    if npm_lock['packages']['']['devDependencies'] != package['devDependencies']:
        raise RuntimeError('NPM lock differs from the pinned browser-test dependency')
    npm_lock.update(name=package['name'], version=package['version'])
    npm_lock['packages'][''].update(name=package['name'], version=package['version'])
    put('package.json', json_bytes(package)); put('package-lock.json', json_bytes(npm_lock))
    report_hashes = {}
    for engine, raw in checked.items():
        put(f'validation-reports/{engine}.json', raw, source=False)
        report_hashes[engine] = digest(raw)
    put('source-manifest.json', json_bytes({'schemaVersion': 1, 'tool': 'hts', 'upstreamVersion': version,
        'upstreamLockSha256': digest(required(local_path('tools/hts/versions.lock.json'))), 'files': dict(sorted(sources.items())),
        'demoProjection': {'path': f'{EXAMPLE}/config.json', 'versions': [version],
                           'originalSha256': digest(required(local_path(f'{EXAMPLE}/config.json')))},
        'notes': 'Includes upstream component/dependency sources, licences, recipes, runtime and fixture provenance.'}), source=False)
    put('release.json', json_bytes({'schemaVersion': 1, 'tool': 'hts', 'upstreamVersion': version,
        'programs': list(PROGRAMS), 'plugins': identity['plugins'], 'build': build, 'datasetManifestSha256': dataset_sha,
        'validationStatus': 'passed for the recorded test scope', 'testedEngines': sorted(ENGINES),
        'reports': report_hashes, 'requiresCrossOriginIsolation': False}), source=False)
    put('README.md', readme(version, identity['plugins']).encode(), source=False)
    put('SHA256SUMS', ''.join(f'{digest(data)}  {name}\n' for name, (data, _) in sorted(entries.items())).encode(), source=False)
    return entries, lock


def write_package(version: str, output: Path, reports: list[Path], *, prepared=None) -> Path:
    entries, _ = prepared if prepared is not None else collect(version, reports)
    stem = f'hts-{version}-wasm'
    output.mkdir(parents=True, exist_ok=True)
    destination = output / f'{stem}.zip'
    temporary = destination.with_suffix('.zip.tmp')
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, (data, mode) in sorted(entries.items()):
                info = zipfile.ZipInfo(f'{stem}/{relative_name(name)}', date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3; info.external_attr = (stat.S_IFREG | mode) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip():
                raise RuntimeError('Package ZIP failed its CRC check')
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    destination.with_suffix('.zip.sha256').write_text(f'{digest(destination.read_bytes())}  {destination.name}\n')
    print(f'{destination}: {len(entries)} files, {destination.stat().st_size} bytes')
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', choices=(*VERSIONS, 'all'), default='all')
    parser.add_argument('--output', type=Path, default=ROOT / 'releases')
    parser.add_argument('--validation-report', action='append', type=Path, required=True)
    args = parser.parse_args()
    versions = VERSIONS if args.version == 'all' else (args.version,)
    # Validate every requested release before writing any ZIP.
    prepared = {version: collect(version, args.validation_report) for version in versions}
    for version in versions:
        write_package(version, args.output.resolve(), args.validation_report, prepared=prepared[version])


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, AssertionError, ValueError, KeyError, OSError, TypeError, zipfile.BadZipFile) as error:
        print(f'Packaging failed: {error}', file=sys.stderr)
        raise SystemExit(1)
