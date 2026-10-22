#!/usr/bin/env python3
"""Package repeat analysis only after replaying four independent engine reports."""
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
VERSIONS = {'expansionhunter': '5.0.0', 'reviewer': '0.2.7', 'samtools': '1.21'}
UNITS = ('expansionhunter', 'reviewer', 'hts-1.21')
PROGRAMS = {'expansionhunter': 'ExpansionHunter', 'reviewer': 'REViewer', 'samtools': 'samtools'}
ENGINES = {'node', 'chromium', 'firefox', 'webkit'}
RUNTIME = ('runtime/index.mjs', 'runtime/worker.mjs', 'runtime/execute.mjs', 'runtime/repeats.mjs')
VALIDATION = ('scripts/validate-repeats.mjs', 'validation/repeats/compare.py',
              'validation/repeats/cases.json', 'scripts/fetch-repeat-test-data.py',
              'datasets/repeats/provenance/sources.lock.json')
DATA = 'datasets/repeats'
EXAMPLE = 'examples/repeats'
SHA256 = re.compile(r'[0-9a-f]{64}')
WASM_HEADER = b'\0asm\1\0\0\0'


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


def module_from_file(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def hts_packager():
    module = module_from_file('repeats_hts_packager', local_path('scripts/package-hts.py'))
    module.ROOT = ROOT
    return module


def source_records(lock: dict) -> dict[str, dict]:
    records = {}
    for record in [lock.get('source', {}), *lock.get('dependencies', {}).values()]:
        name = relative_name(record.get('archive'))
        if (name in records or not SHA256.fullmatch(record.get('sha256', ''))
                or type(record.get('bytes')) is not int or record['bytes'] <= 0
                or not isinstance(record.get('url'), str) or not record['url'].startswith('https://')
                or not record.get('license')):
            raise RuntimeError(f'Invalid, unlicensed or duplicate source archive: {name}')
        records[name] = record
    return records


def check_tool_build(tool: str) -> tuple[dict, dict, dict]:
    lock = read_json(f'tools/{tool}/versions.lock.json')
    if (lock.get('schema') != 1 or lock.get('tool') != tool or lock.get('version') != VERSIONS[tool]
            or lock.get('emscripten') != '3.1.64'):
        raise RuntimeError(f'{tool}: invalid pinned release contract')
    records = source_records(lock)
    base = f'dist/{tool}'
    raw = required(local_path(f'{base}/build-manifest.json'))
    identity = json.loads(raw)
    assets = tree_hashes(base, exclude=('build-manifest.json',))
    if (identity.get('schema') != 1 or identity.get('tool') != tool or identity.get('version') != VERSIONS[tool]
            or identity.get('assets') != assets
            or not re.search(r'(?<![0-9.])3\.1\.64(?![0-9.])', identity.get('compiler', ''))):
        raise RuntimeError(f'{tool}: compiled assets, version or compiler differ from the pinned build')
    expected_inputs = {f'tools/{tool}/{name}' for name in tree_hashes(f'tools/{tool}', source=True)}
    expected_inputs.update((f'scripts/build-{tool}.py', 'scripts/setup-emsdk.sh'))
    inputs = identity.get('inputs', {})
    if not isinstance(inputs, dict) or not expected_inputs <= inputs.keys():
        raise RuntimeError(f'{tool}: build manifest omits recipe or source inputs')
    for name, checksum in inputs.items():
        verify(local_path(name), checksum)
    if (not required(local_path(f'{base}/{tool}.wasm')).startswith(WASM_HEADER)
            or not required(local_path(f'{base}/{tool}.mjs'))):
        raise RuntimeError(f'{tool}: missing WebAssembly executable')
    for name in assets:
        data = required(local_path(f'{base}/{name}'))
        if data.startswith(b'\x7fELF') or name.endswith(('.so', '.dll', '.dylib', '.exe')):
            raise RuntimeError(f'{tool}: desktop binary in browser bundle: {name}')
        if name.endswith('.a'):
            hts_packager().check_wasm_archive(data, name)
    native = identity.get('nativeFiles', {})
    if set(native) != {'native', 'patched-native'}:
        raise RuntimeError(f'{tool}: both native reference environments must be bound')
    for kind, hashes in native.items():
        if PROGRAMS[tool] not in hashes or hashes != tree_hashes(f'build/{kind}/{tool}'):
            raise RuntimeError(f'{tool}: {kind} files differ from the validated build')
    if set(tree_hashes(f'{base}/sources')) != set(records):
        raise RuntimeError(f'{tool}: source archives are missing or unpinned')
    for name, record in records.items():
        if len(verify(local_path(f'{base}/sources/{name}'), record['sha256'])) != record['bytes']:
            raise RuntimeError(f'{tool}: source archive length differs: {name}')
    notices = lock.get('requiredNotices', [])
    if not notices or len(notices) != len(set(notices)):
        raise RuntimeError(f'{tool}: missing or duplicate required licence notices')
    if {f'notices/{name}' for name in tree_hashes(f'{base}/notices')} != set(notices):
        raise RuntimeError(f'{tool}: distributed notice inventory differs from the source lock')
    for name in notices:
        if not name.startswith('notices/') or required(local_path(f'{base}/{name}')) != required(local_path(f'tools/{tool}/{name}')):
            raise RuntimeError(f'{tool}: distributed licence notice differs: {name}')
    return lock, identity, {'assets': assets, 'buildManifest': digest(raw), 'inputs': inputs,
                            'nativeFiles': native, 'tool': tool, 'version': VERSIONS[tool]}


def check_builds() -> tuple[dict, dict, dict]:
    locks, identities, builds = {}, {}, {}
    for tool in ('expansionhunter', 'reviewer'):
        locks[tool], identities[tool], builds[tool] = check_tool_build(tool)
    locks['hts-1.21'], identities['hts-1.21'], hts_build = hts_packager().check_build_identity('1.21')
    builds['hts-1.21'] = {key: value for key, value in hts_build.items() if key != 'plugins'}
    builds['hts-1.21'].update(tool='hts', version='1.21')
    return locks, identities, builds


def checked_datasets() -> tuple[dict, str]:
    raw = required(local_path(f'{DATA}/manifest.json'))
    manifest = json.loads(raw)
    if manifest.get('schema') != 1 or manifest.get('tool') != 'repeats' or not manifest.get('files'):
        raise RuntimeError('Invalid repeat fixture manifest')
    for name, info in manifest['files'].items():
        if type(info.get('bytes')) is not int or len(verify(local_path(f'{DATA}/{relative_name(name)}'), info['sha256'])) != info['bytes']:
            raise RuntimeError(f'Fixture length differs from its manifest: {name}')
    actual = {f'{directory}/{name}': checksum for directory in ('common', 'upstream')
              for name, checksum in tree_hashes(f'{DATA}/{directory}').items()}
    if actual != {name: item['sha256'] for name, item in manifest['files'].items()}:
        raise RuntimeError('Every repeat fixture must be identified')
    sources = json.loads(verify(local_path(f'{DATA}/provenance/sources.lock.json'), manifest.get('sourcesLockSha256')))
    if sources.get('schema') != 1 or not sources.get('files'):
        raise RuntimeError('Missing original fixture provenance')
    projects = sources.get('projects', {})
    if set(projects) != {'expansionhunter', 'reviewer'}:
        raise RuntimeError('Missing original fixture projects')
    for tool, repository in (('expansionhunter', 'Illumina/ExpansionHunter'), ('reviewer', 'Illumina/REViewer')):
        project = projects[tool]
        if (project.get('repository') != repository or project.get('version') != VERSIONS[tool]
                or not re.fullmatch('[0-9a-f]{40}', project.get('commit', '')) or not project.get('license')):
            raise RuntimeError(f'{tool}: unpinned or unlicensed fixture project')
    originals = set()
    for source in sources['files']:
        project = projects.get(source.get('project'))
        if not project:
            raise RuntimeError('Unknown original fixture project')
        source_path = relative_name(source.get('sourcePath'))
        expected_url = f'https://raw.githubusercontent.com/{project["repository"]}/{project["commit"]}/{source_path}'
        if (source.get('url') != expected_url or source.get('repository') != project['repository']
                or source.get('commit') != project['commit'] or source.get('license') != project['license']
                or not SHA256.fullmatch(source.get('sha256', ''))
                or type(source.get('bytes')) is not int or source['bytes'] <= 0):
            raise RuntimeError('Unpinned original fixture source')
        if 'outputPath' in source:
            name = relative_name(source['outputPath'])
            if name in originals or not name.startswith(f'upstream/{source["project"]}/'):
                raise RuntimeError('Duplicate or misplaced original fixture')
            originals.add(name)
            if manifest['files'].get(name) != {'sha256': source['sha256'], 'bytes': source['bytes']}:
                raise RuntimeError(f'Original fixture bytes differ from pinned source: {name}')
    for tool in projects:
        if f'upstream/{tool}/LICENSE.txt' not in originals:
            raise RuntimeError(f'{tool}: missing original fixture licence')
    return manifest, digest(raw)


def planned_cases() -> dict:
    plan = read_json('validation/repeats/cases.json')
    if (plan.get('schema') != 1 or plan.get('tool') != 'repeats' or plan.get('versions') != VERSIONS
            or plan.get('runtime') != list(RUNTIME)):
        raise RuntimeError('Invalid repeat scientific case plan')
    cases = {}
    for case in plan.get('cases', []):
        name = case.get('id')
        if not isinstance(name, str) or not name or name in cases or case.get('kind') not in ('success', 'failure'):
            raise RuntimeError('Duplicate or invalid scientific case')
        relative_name(name)
        if not case.get('steps'):
            raise RuntimeError(f'{name}: no executed commands')
        for step in case['steps']:
            if step.get('tool') not in PROGRAMS or not isinstance(step.get('args'), list) or any(not isinstance(arg, str) for arg in step['args']):
                raise RuntimeError(f'{name}: invalid scientific command')
        cases[name] = case
    if not cases or not any(case.get('artifacts') and case['kind'] == 'success' for case in cases.values()):
        raise RuntimeError('Scientific plan contains no output comparisons')
    covered = {step['tool'] for case in cases.values() if case['kind'] == 'success'
               for step in case['steps'] if not step.get('oracle')}
    if covered != set(PROGRAMS):
        raise RuntimeError('Scientific plan must execute every shipped repeat-analysis tool in WebAssembly')
    return cases


def native_program(tool: str, target: str) -> str:
    unit = 'hts-1.21' if tool == 'samtools' else tool
    return f'build/{target}/{unit}/{PROGRAMS[tool]}'


def check_reports(paths: list[Path], build: dict, dataset: dict, dataset_sha: str) -> dict:
    """Bind recorded commands to actual evidence; reuse the scientific comparator.

    This reads saved output bytes and rechecks semantic comparisons. It never
    launches a native or WASM executable while packaging.
    """
    plan = planned_cases()
    runtime = {name: digest(required(local_path(name))) for name in RUNTIME}
    validation = {name: digest(required(local_path(name))) for name in VALIDATION}
    module_spec = importlib.util.spec_from_file_location('repeats_release_comparator', local_path('validation/repeats/compare.py'))
    comparator = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(comparator)
    reports, run_ids, biology = {}, set(), {}
    for path in paths:
        path = path.resolve()
        raw = required(path)
        report = json.loads(raw)
        engine = report.get('engine')
        if engine not in ENGINES or engine in reports:
            raise RuntimeError(f'repeats: unknown or duplicate validation engine')
        run_id = report.get('runId')
        if (not isinstance(run_id, str) or str(UUID(run_id)) != run_id or run_id in run_ids
                or report.get('reportDirectory') != run_id):
            raise RuntimeError(f'repeats/{engine}: missing or reused independent run identity')
        run_ids.add(run_id)
        if (report.get('schema') != 1 or report.get('tool') != 'repeats' or report.get('versions') != VERSIONS or report.get('status') != 'passed'
                or report.get('crossOriginIsolated') is not False or not report.get('node')
                or report.get('browserErrors') != [] or report.get('externalRequests') != []
                or (engine != 'node' and not report.get('browserVersion'))
                or report.get('build') != build or report.get('runtime') != runtime
                or report.get('validation') != validation or report.get('datasetManifestSha256') != dataset_sha
                or report.get('expectedCases') != list(plan)):
            raise RuntimeError(f'repeats/{engine}: stale, incomplete or failed validation identity')
        records = report.get('cases', [])
        if [record.get('id') for record in records] != list(plan):
            raise RuntimeError(f'repeats/{engine}: scientific case inventory differs')
        evidence = path.parent / run_id
        for record in records:
            case = plan[record['id']]
            case_id = relative_name(case['id'])
            failure = case['kind'] == 'failure'
            if (record.get('status') != 'passed' or record.get('kind') != case['kind']
                    or record.get('steps') != case['steps']
                    or set(record.get('executions', {})) != {'native', 'patched-native', 'wasm'}):
                raise RuntimeError(f'{case_id}: scientific commands or targets differ')
            initial = {}
            for source in case.get('inputs', []):
                source_name = relative_name(source['source'])
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
                        command = {'program': native_program(step['tool'], origin), 'args': step['args']}
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
                    observed = comparator.compare(evidence / case_id / 'native', evidence / case_id / target, case)
                    if observed.get('equal') is not True or comparisons[kind] != observed:
                        raise RuntimeError(f'{case_id}/{kind}: comparison differs from executed output bytes or fixture truths')
                    if biology.setdefault(case_id, observed['biologicalDigest']) != observed['biologicalDigest']:
                        raise RuntimeError(f'{case_id}: scientific results differ between validation engines')
        reports[engine] = raw
    if set(reports) != ENGINES:
        raise RuntimeError(f'repeats: require independent passing Node, Chromium, Firefox and WebKit reports')
    return reports


def readme() -> str:
    return '''# ExpansionHunter and REViewer WebAssembly bundle

This standalone bundle contains ExpansionHunter 5.0.0, REViewer 0.2.7 and
SAMtools 1.21 for preparing ExpansionHunter's BAMlet alignments. It also includes
the matching HTS 1.21 utilities and SDK, with their original notices and sources.
The repeat workflow runs the upstream C/C++ implementations in fresh workers.

## Browser demo

From this extracted directory run:

```sh
python3 -m http.server 8000 --bind 127.0.0.1
```

Open <http://127.0.0.1:8000/examples/repeats/>. Load the example data or your own
local inputs, run ExpansionHunter, sort and index its BAMlet, then run REViewer
to produce SVG plots and metrics. The demo supports editable command arguments,
file chaining, plot previews and downloads. Keep the runtime, dist, datasets
and examples directories together. HTTP(S) is required; opening file:// does
not support the module/worker loading. Cross-origin isolation is not required.

## JavaScript API

```js
import { RepeatRunner } from './runtime/repeats.mjs';
const runner = new RepeatRunner({ tool: 'expansionhunter' });
const result = await runner.run(args, {
  files: [{ path: 'reads.bam', data: bamBytes }, /* reference, indexes, catalog */],
  outputs: ['sample.json', 'sample.vcf', 'sample_realigned.bam'],
  timeoutMs: 300000,
});
```

Use tool: 'samtools' for sorting/indexing and tool: 'reviewer' for rendering.
See runtime/REPEATS.md and examples/repeats/README.md for complete commands and
input requirements. Commands have fresh filesystems: pass generated output
bytes into the next invocation. Check exitCode and missingOutputs. Inputs,
working data and outputs occupy browser memory; the small fixture checks do
not establish whole-genome capacity or throughput.

## Rebuilding, verification and licences

Pinned sources, patches, build recipes and complete notices are included.
See tools/expansionhunter/README.md, tools/reviewer/README.md and tools/hts/README.md.
Native host build tools and Emscripten 3.1.64 are prerequisites for rebuilding.
The package contains WebAssembly programs, not Linux executables.

```sh
sha256sum -c SHA256SUMS
npm ci
npm test
node scripts/test-repeats-package.mjs
npx playwright install --with-deps chromium firefox webkit
BROWSER=chromium node scripts/test-repeats-demo.mjs
BROWSER=firefox node scripts/test-repeats-demo.mjs
BROWSER=webkit node scripts/test-repeats-demo.mjs
```

Four passing scientific reports are under validation-reports/. Each binds the
exact compiled assets, native references, runtime, fixtures, command sequence
and output comparisons. Packaging replays comparisons over captured raw outputs;
it does not use a report's status field alone as proof. Independent extracted-ZIP
tests run after packaging. Tests cover their listed fixtures and options, not
every possible input.

LICENSE covers this project's infrastructure. The pinned ExpansionHunter 5.0.0
release, REViewer 0.2.7 release and their dependencies retain their own terms;
the exact release licences and attribution notices are under each dist tool's
notices directory. Preserve those notices and accompanying sources/recipes when
redistributing. Public fixture provenance is under datasets/repeats/provenance/.
'''


def collect(reports: list[Path]) -> dict[str, tuple[bytes, int]]:
    locks, identities, builds = check_builds()
    dataset, dataset_sha = checked_datasets()
    checked = check_reports(reports, builds, dataset, dataset_sha)
    entries, sources, anchors = {}, {}, {}
    first_report = json.loads(next(iter(checked.values())))
    anchors.update(first_report['runtime'])
    anchors.update(first_report['validation'])
    anchors[f'{DATA}/manifest.json'] = dataset_sha
    anchors.update({f'{DATA}/{name}': info['sha256'] for name, info in dataset['files'].items()})
    for unit, identity in identities.items():
        anchors.update(identity['inputs'])
        anchors[f'dist/{unit}/build-manifest.json'] = builds[unit]['buildManifest']
        anchors.update({f'dist/{unit}/{name}': checksum for name, checksum in identity['assets'].items()})

    def put(name: str, data: bytes, mode=0o644, *, source=True):
        relative_name(name)
        if name in entries and entries[name][0] != data:
            raise RuntimeError(f'Conflicting package entries: {name}')
        entries[name] = (data, mode)
        if source:
            sources[name] = digest(data)

    def add(name: str, *, source=True):
        path = local_path(name)
        data = verify(path, anchors[name]) if name in anchors else required(path)
        put(name, data, 0o755 if path.stat().st_mode & 0o111 else 0o644, source=source)

    def tree(directory: str, *, source=True):
        for name in tree_hashes(directory, source=source):
            add(f'{directory}/{name}', source=source)

    for name in ('LICENSE', *RUNTIME, 'runtime/REPEATS.md', *VALIDATION,
                 'scripts/package-repeats.py', 'scripts/package-hts.py', 'scripts/test-repeats-package.mjs',
                 'scripts/test-repeats-demo.mjs', 'tests/runtime.test.mjs', 'tests/fixtures/mock-tool.mjs',
                 'scripts/test-hts-sdk.py', 'tests/fixtures/hts-sdk.c', 'runtime/hts.mjs', 'runtime/HTS.md'):
        add(name)
    tests = sorted((ROOT / 'tests').glob('test_repeat*.py'))
    if not tests:
        raise RuntimeError('Missing standalone repeat-analysis unit tests')
    for path in tests:
        add(path.relative_to(ROOT).as_posix())
    for directory in ('tools/expansionhunter', 'tools/reviewer', 'tools/hts', 'validation/repeats', DATA, EXAMPLE):
        tree(directory)
    for unit, identity in identities.items():
        for name in identity['inputs']:
            add(name)
        tree(f'dist/{unit}', source=False)
        records = hts_packager().source_records(locks[unit], '1.21') if unit == 'hts-1.21' else source_records(locks[unit])
        for name, info in records.items():
            sources[f'dist/{unit}/sources/{name}'] = info['sha256']
    # Keep the demo configuration byte-for-byte: command insertion order carries
    # create-then-consume dependencies (EH -> sort -> index -> REViewer).
    playwright = read_json('package.json')['devDependencies']['playwright']
    if not re.fullmatch(r'\d+\.\d+\.\d+', playwright):
        raise RuntimeError('Playwright must use an exact pinned version')
    package = {'name': 'repeats-wasm', 'version': '1.0.0', 'private': True, 'type': 'module',
               'scripts': {'test': 'python3 -m unittest discover -s tests -p "test_repeat*.py" && node --test tests/runtime.test.mjs',
                           'validate': 'node scripts/validate-repeats.mjs --engine node'},
               'devDependencies': {'playwright': playwright}, 'engines': {'node': '>=20'}}
    npm_lock = read_json('package-lock.json')
    if npm_lock['packages']['']['devDependencies'] != package['devDependencies']:
        raise RuntimeError('NPM lock differs from the pinned browser-test dependency')
    npm_lock.update(name=package['name'], version=package['version'])
    npm_lock['packages'][''].update(name=package['name'], version=package['version'])
    put('package.json', json_bytes(package))
    put('package-lock.json', json_bytes(npm_lock))
    report_hashes = {}
    for engine, raw in checked.items():
        put(f'validation-reports/{engine}.json', raw, source=False)
        report_hashes[engine] = digest(raw)
    put('source-manifest.json', json_bytes({'schemaVersion': 1, 'tool': 'repeats', 'versions': VERSIONS,
        'files': dict(sorted(sources.items())),
        'notes': 'Includes pinned upstream sources, complete licences, recipes, runtime and fixture provenance.'}), source=False)
    put('release.json', json_bytes({'schemaVersion': 1, 'tool': 'repeats', 'versions': VERSIONS,
        'programs': list(PROGRAMS), 'build': builds, 'datasetManifestSha256': dataset_sha,
        'validationStatus': 'passed for the recorded test scope', 'testedEngines': sorted(ENGINES),
        'reports': report_hashes, 'requiresCrossOriginIsolation': False}), source=False)
    put('README.md', readme().encode(), source=False)
    put('SHA256SUMS', ''.join(f'{digest(data)}  {name}\n' for name, (data, _) in sorted(entries.items())).encode(), source=False)
    return entries


def write_package(output: Path, reports: list[Path]) -> Path:
    entries = collect(reports)
    stem = 'repeats-wasm'
    output.mkdir(parents=True, exist_ok=True)
    destination = output / f'{stem}.zip'
    temporary = destination.with_suffix('.zip.tmp')
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9, allowZip64=True) as archive:
            for name, (data, mode) in sorted(entries.items()):
                info = zipfile.ZipInfo(f'{stem}/{relative_name(name)}', date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | mode) << 16
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
    parser.add_argument('--output', type=Path, default=ROOT / 'releases')
    parser.add_argument('--validation-report', action='append', type=Path, required=True)
    args = parser.parse_args()
    write_package(args.output.resolve(), args.validation_report)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, AssertionError, ValueError, KeyError, OSError, TypeError, zipfile.BadZipFile) as error:
        print(f'Packaging failed: {error}', file=sys.stderr)
        raise SystemExit(1)
