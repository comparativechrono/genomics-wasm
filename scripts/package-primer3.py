#!/usr/bin/env python3
"""Package independently usable primer3 after complete native and browser validation."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tarfile
import zipfile
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ('primer3',)
VERSIONS = {'primer3': '2.6.1'}
BUNDLES = {tool: (tool,) for tool in TOOLS}
LABELS = {'primer3': 'Primer3'}
ENGINES = {'node', 'chromium', 'firefox', 'webkit'}
BASE_RUNTIME = ('runtime/index.mjs', 'runtime/worker.mjs', 'runtime/execute.mjs')
RUNTIME = {tool: (*BASE_RUNTIME, f'runtime/{tool}.mjs') for tool in TOOLS}
PROGRAMS = {'primer3': ('primer3_core', 'ntdpal', 'ntthal', 'oligotm', 'amplicon3_core', 'long_seq_tm_test', 'primer3_masker')}
VALIDATION = ('scripts/validate-primer3.mjs', 'validation/primer3/compare.py', 'validation/primer3/cases.json')
TEST_FIXTURES = {'primer3': ('tests/fixtures/mock-primer3-masker.mjs',)}
DATA = 'datasets/primer3'
EXAMPLE = 'examples/primer3'
SHA256 = re.compile(r'[0-9a-f]{64}')
WASM_HEADER = b'\0asm\1\0\0\0'


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


def tree_hashes(directory: str, *, suffixes=None, exclude=(), source_tree=False) -> dict[str, str]:
    base = local_path(directory)
    if not base.is_dir() or base.is_symlink():
        raise RuntimeError(f'Required package directory is missing or unsafe: {directory}')
    result = {}
    for path in sorted(base.rglob('*')):
        if source_tree and ('__pycache__' in path.relative_to(base).parts or path.suffix in ('.pyc', '.pyo')):
            continue
        if path.is_symlink():
            raise RuntimeError(f'Package directory contains a symlink: {path}')
        if path.is_file() and (suffixes is None or path.suffix in suffixes):
            name = relative_name(path.relative_to(base).as_posix())
            if name not in exclude:
                result[name] = digest(required(path))
    return result


def source_archives(tool: str, lock: dict) -> dict[str, str]:
    archives = {}
    def add(record):
        name, checksum = relative_name(record['archive']), record.get('sha256')
        if not isinstance(checksum, str) or not SHA256.fullmatch(checksum):
            raise RuntimeError(f'{tool}: invalid source archive SHA256: {name}')
        if name in archives and archives[name] != checksum:
            raise RuntimeError(f'{tool}: conflicting source archive checksums: {name}')
        archives[name] = checksum
    add(lock)
    for name, dependency in lock.get('dependencies', {}).items():
        for record in dependency if isinstance(dependency, list) else [dependency]:
            if 'archive' not in record:
                raise RuntimeError(f'{tool}: unidentified embedded dependency: {name}')
            add(record)
    for record in lock.get('sourceArchives', []):
        add(record)
    return archives


def native_tree(tool: str, kind: str) -> dict[str, str]:
    if kind not in ('native', 'patched-native'):
        raise RuntimeError(f'Unknown execution target: {kind}')
    return {program: digest(required(local_path(f'build/{kind}/{program}'))) for program in PROGRAMS[tool]}


def build_inputs(tool: str, lock: dict, identity: dict) -> None:
    expected = {f'tools/{tool}/{name}' for name in tree_hashes(f'tools/{tool}', source_tree=True)}
    expected.add(f'scripts/build-{tool}.sh')
    expected.update(lock.get('buildScripts', []))
    recorded = identity.get('inputs', {})
    if not isinstance(recorded, dict) or not expected <= recorded.keys():
        raise RuntimeError(f'{tool}: build manifest omits source inputs')
    for name, sha in recorded.items():
        verify(local_path(name), sha)


def check_build_identity(tool: str) -> tuple[dict, dict, dict]:
    if tool not in TOOLS:
        raise RuntimeError(f'Unknown tool: {tool}')
    lock = read_json(f'tools/{tool}/sources.lock.json')
    raw = required(local_path(f'dist/{tool}/build-manifest.json'))
    identity = json.loads(raw)
    assets = tree_hashes(f'dist/{tool}', exclude=('build-manifest.json',))
    if identity.get('schema') != 1 or identity.get('tool') != tool or identity.get('assets') != assets:
        raise RuntimeError(f'{tool}: build manifest does not identify the complete distribution')
    build_inputs(tool, lock, identity)
    if (lock.get('tool') != tool or lock.get('version') != VERSIONS[tool]
):
        raise RuntimeError(f'{tool}: source version differs from the release contract')
    if (identity.get('version') != lock['version'] or identity.get('revision') != lock.get('revision')
            or identity.get('programs') != list(PROGRAMS[tool]) or lock.get('programs') != list(PROGRAMS[tool])
            or not re.search(r'(?<![0-9.])' + re.escape(lock['emscripten']) + r'(?![0-9.])', identity.get('compiler', ''))):
        raise RuntimeError(f'{tool}: compiled version, revision, programs or compiler differ from the source lock')
    for program in PROGRAMS[tool]:
        if (not required(local_path(f'dist/{tool}/{program}.wasm')).startswith(WASM_HEADER)
                or not required(local_path(f'dist/{tool}/{program}.mjs'))):
            raise RuntimeError(f'{tool}: missing WebAssembly program: {program}')
    environments = identity.get('nativeFiles', {})
    if set(environments) != {'native', 'patched-native'}:
        raise RuntimeError(f'{tool}: both native execution environments must be identified')
    for kind, files in environments.items():
        if files != native_tree(tool, kind):
            raise RuntimeError(f'{tool}: {kind} execution environment differs from its build manifest')
    return lock, identity, {'assets': assets, 'buildManifest': digest(raw), 'nativeFiles': environments, 'inputs': identity['inputs']}


def check_sources(tool: str, lock: dict) -> dict[str, str]:
    archives = source_archives(tool, lock)
    if set(tree_hashes(f'dist/{tool}/sources')) != set(archives):
        raise RuntimeError(f'{tool}: missing or unpinned source archives')
    for name, sha in archives.items():
        verify(local_path(f'dist/{tool}/sources/{name}'), sha)
    if not lock.get('requiredNotices'):
        raise RuntimeError(f'{tool}: source lock omits licence notices')
    for name in lock['requiredNotices']:
        if required(local_path(f'tools/{tool}/{relative_name(name)}')) != required(local_path(f'dist/{tool}/notices/{name}')):
            raise RuntimeError(f'{tool}: distributed licence notice differs: {name}')
    return archives


def checked_dataset_manifest() -> tuple[dict, str]:
    raw = required(local_path(f'{DATA}/manifest.json'))
    manifest = json.loads(raw)
    plan = read_json('validation/primer3/cases.json')
    datasets = plan.get('datasets', [])
    if (manifest.get('schema_version') != 1 or not datasets or len(datasets) != len(set(datasets))
            or set(manifest.get('datasets', {})) != set(datasets)):
        raise RuntimeError('The primer3 manifest must identify every planned real dataset')
    if not manifest.get('files'):
        raise RuntimeError('The primer3 manifest contains no verified files')
    for name, info in manifest['files'].items():
        data = verify(local_path(f'{DATA}/{relative_name(name)}'), info['sha256'])
        if type(info.get('bytes')) is not int or info['bytes'] != len(data):
            raise RuntimeError(f'Dataset byte count differs from its manifest: {name}')
    complete = tree_hashes(DATA, exclude=('manifest.json',), source_tree=True)
    if complete != {name: info['sha256'] for name, info in manifest['files'].items()}:
        raise RuntimeError('The manifest must identify every bundled fixture and provenance file')
    for dataset in datasets:
        relative_name(dataset)
        actual = {f'{dataset}/{name}': sha for name, sha in tree_hashes(f'{DATA}/{dataset}').items()}
        recorded = {name: info['sha256'] for name, info in manifest['files'].items() if name.startswith(dataset + '/')}
        if actual != recorded:
            raise RuntimeError(f'The manifest must identify every bundled {dataset} fixture file')
    for name in plan.get('fixtureInputs', []):
        if relative_name(name) not in manifest['files']:
            raise RuntimeError(f'Validation input is absent from the dataset manifest: {name}')
    for tool, specification in plan.get('tools', {}).items():
        for name in specification.get('fixtureInputs', []):
            if f'{tool}/{relative_name(name)}' not in manifest['files']:
                raise RuntimeError(f'Validation input is absent from the dataset manifest: {tool}/{name}')
    required(local_path(f'{DATA}/README.md'))
    return manifest, digest(raw)


def checked_example_config(manifest: dict) -> dict:
    config = read_json(f'{EXAMPLE}/config.json')
    if set(config.get('datasets', {})) != set(manifest['datasets']):
        raise RuntimeError('Standalone browser configuration omits a real dataset')
    for name, dataset in config['datasets'].items():
        if dataset.get('path') != f'../../datasets/primer3/{name}/':
            raise RuntimeError('Browser example must use its bundled local fixture')
        files = dataset.get('files')
        if not isinstance(files, list) or not files or len(files) != len(set(files)):
            raise RuntimeError('Browser example fixture files must be nonempty and unique')
        for path in files:
            if f'{name}/{relative_name(path)}' not in manifest['files']:
                raise RuntimeError(f'Browser example input is absent from the dataset manifest: {path}')
    if set(config.get('programs', {})) != set(PROGRAMS[TOOLS[0]]):
        raise RuntimeError('Browser example omits a bundled executable')
    commands = config.get('commands', {})
    if not isinstance(commands, dict) or not commands:
        raise RuntimeError('Browser example has no commands')
    for name, command in commands.items():
        if command.get('program') not in PROGRAMS[TOOLS[0]] or not isinstance(command.get('args'), list) or any(not isinstance(arg, str) for arg in command['args']):
            raise RuntimeError(f'Browser example has an invalid command: {name}')
        if command.get('stdinFile') and not any(command['stdinFile'] in dataset['files'] for dataset in config['datasets'].values()):
            raise RuntimeError(f'Browser command standard input is not bundled: {name}')
    return config


def normalised_artifacts(spec: dict) -> set[str]:
    """Semantic parsers have fixed, source-bound normalization; no case overrides."""
    for artifact in spec.get('artifacts', []):
        if any(key.startswith('normalize') for key in artifact):
            raise RuntimeError(f"{spec.get('id')}: undeclared artifact normalization: {artifact['path']}")
    return {item['path'] for item in spec.get('artifacts', []) if item['kind'] in {'text', 'boulder'}}


def planned_cases(tool: str) -> dict:
    plan = read_json('validation/primer3/cases.json')
    if plan.get('schema') != 1 or not plan.get('datasets'):
        raise RuntimeError('Unknown primer3 case-plan schema or datasets')
    result = {}
    for spec in plan['tools'][tool]['cases']:
        normalised_artifacts(spec)
        if spec.get('nativeStdinTransport') is not None and (spec['nativeStdinTransport'] != 'file'
                or spec.get('program') != 'primer3_masker' or not spec.get('stdinFile')):
            raise RuntimeError(f'{tool}: invalid declared native standard-input transport')
        if spec.get('program') not in PROGRAMS[tool]:
            raise RuntimeError(f'{tool}: unknown planned executable')
        if spec.get('runner') != tool:
            raise RuntimeError(f'{tool}: standalone case uses an unbundled runner')
        if spec.get('kind') not in ('success', 'repeat', 'failure', 'restriction') or 'nativeErrorPolicy' in spec:
            raise RuntimeError(f'{tool}: unsupported case kind or signal exception')
        if spec['kind'] == 'success' and (set(spec.get('executions', [])) != {'native', 'patched-native', 'wasm'}
                or set(spec.get('comparisons', [])) != {'native-contract', 'patched-native', 'wasm'}):
            raise RuntimeError(f'{tool}: successful cases must compare all three implementations')
        if spec['kind'] in ('failure', 'restriction') and spec.get('comparisons') != []:
            raise RuntimeError(f'{tool}: failure cases must record exits and diagnostics without semantic comparisons')
        if spec['kind'] == 'repeat':
            only_wasm = spec.get('executions') == ['wasm'] and spec.get('comparisons') == ['wasm']
            all_targets = (set(spec.get('executions', [])) == {'native', 'patched-native', 'wasm'}
                           and set(spec.get('comparisons', [])) == {'native-contract', 'patched-native', 'wasm'})
            if not spec.get('baseline') or not (only_wasm or all_targets):
                raise RuntimeError(f'{tool}: repeats must identify their unchanged native baseline')
        datasets = [None] if spec.get('oncePerTool') else spec.get('datasets', plan['tools'][tool].get('datasets', [tool]))
        for dataset in datasets:
            if dataset is not None and dataset not in plan['datasets']:
                raise RuntimeError(f'{tool}: unknown case dataset: {dataset}')
            case_id = f'{tool}-{dataset}-{spec["id"]}' if dataset else f'{tool}-{spec["id"]}'
            if case_id in result:
                raise RuntimeError(f'{tool}: duplicate planned case: {case_id}')
            result[case_id] = {**spec, '_dataset': dataset or plan['tools'][tool].get('datasets', [tool])[0]}
    if not result:
        raise RuntimeError(f'{tool}: empty case plan')
    if not any(spec['kind'] == 'success' and any(artifact.get('kind') not in {'text', 'log', 'diagnostic'}
                   and not artifact.get('allowEmpty') for artifact in spec.get('artifacts', [])) for spec in result.values()):
        raise RuntimeError(f'{tool}: case plan omits nonempty biological result validation')
    return result


def check_invariants(case_id: str, spec: dict, comparison: dict) -> None:
    artifacts = spec.get('artifacts', [])
    paths = [item['path'] for item in artifacts]
    if not artifacts or len(paths) != len(set(paths)):
        raise RuntimeError(f'{case_id}: empty or duplicate artifact plan')
    for side in ('invariants', 'nativeInvariants'):
        checks = comparison.get(side)
        if not isinstance(checks, dict) or set(checks) != set(paths):
            raise RuntimeError(f'{case_id}: incomplete {side} artifact evidence')
        for artifact in artifacts:
            counts, kind = checks[artifact['path']], artifact['kind']
            if kind not in {'binary', 'text', 'boulder'} or not isinstance(counts, dict) or counts.get('kind') != kind:
                raise RuntimeError(f'{case_id}: unknown artifact invariant kind: {kind}')
            if kind == 'boulder':
                if type(counts.get('records')) is not int or counts['records'] < 1:
                    raise RuntimeError(f'{case_id}: missing nonempty Boulder evidence')
                for key in ('pairs', 'primers', 'errors'):
                    if type(counts.get(key)) is not int or counts[key] < 0:
                        raise RuntimeError(f'{case_id}: invalid Boulder {key} evidence')
                if counts['errors'] > counts['records']:
                    raise RuntimeError(f'{case_id}: impossible Boulder error count')
                for key in ('records', 'errors'):
                    if key in artifact and counts[key] != artifact[key]:
                        raise RuntimeError(f'{case_id}: incorrect required Boulder {key}')
                for field, minimum in (('pairs', 'minPairs'), ('primers', 'minPrimers')):
                    if counts[field] < artifact.get(minimum, 0):
                        raise RuntimeError(f'{case_id}: missing required Boulder {field}')
            else:
                if type(counts.get('bytes')) is not int or counts['bytes'] < 1:
                    raise RuntimeError(f'{case_id}: missing nonempty {kind} evidence')
                if kind == 'text' and (type(counts.get('lines')) is not int or counts['lines'] < 1):
                    raise RuntimeError(f'{case_id}: missing nonempty text line evidence')
    if comparison['invariants'] != comparison['nativeInvariants']:
        raise RuntimeError(f'{case_id}: native and WebAssembly output invariants differ')


def check_numeric_comparisons(case_id: str, spec: dict, comparison: dict) -> set[str]:
    if (any(item.get('floatColumns') for item in spec['artifacts'])
            or comparison.get('numericTolerance') != 0 or comparison.get('numericComparisons') != {}):
        raise RuntimeError(f'{case_id}: Primer3 requires exact scientific numeric values')
    return set()


def check_comparison_files(case_id: str, spec: dict, comparison: dict, native: dict, actual: dict) -> None:
    paths = [item['path'] for item in spec['artifacts']]
    floating = check_numeric_comparisons(case_id, spec, comparison)
    normalized = normalised_artifacts(spec)
    canonical = comparison.get('canonicalFiles')
    expected_canonical = comparison.get('expectedCanonicalFiles')
    for hashes in (canonical, expected_canonical):
        if (not isinstance(hashes, dict) or set(hashes) != set(paths)
                or any(not isinstance(sha, str) or not SHA256.fullmatch(sha) for sha in hashes.values())):
            raise RuntimeError(f'{case_id}: missing canonical artifact checksums')
    for prefix, execution in (('expected', native), ('actual', actual)):
        if (any(path not in execution['files'] for path in paths)
                or comparison.get(prefix + 'Files') != {path: execution['files'][path] for path in paths}
                or comparison.get(prefix + 'FileBytes') != {path: execution['fileBytes'][path] for path in paths}):
            raise RuntimeError(f'{case_id}: comparison is not bound to recorded execution output bytes')
    for path in paths:
        if path not in floating and canonical[path] != expected_canonical[path]:
            raise RuntimeError(f'{case_id}: exact canonical artifact checksums differ: {path}')
        if path not in floating | normalized:
            if (native['files'][path] != actual['files'][path]
                    or native['fileBytes'][path] != actual['fileBytes'][path]):
                raise RuntimeError(f'{case_id}: exact comparison output bytes differ: {path}')


def check_execution_inputs(case_id: str, tool: str, target: str, spec: dict, execution: dict, manifest: dict) -> None:
    plan = read_json('validation/primer3/cases.json')
    inputs = {name: dict(manifest['files'][spec['_dataset'] + '/' + name])
              for name in plan['tools'][tool]['fixtureInputs']}
    for source in execution['inputsFrom']:
        inputs[relative_name(source['as'])] = {'sha256': source['sha256'], 'bytes': source['bytes']}
    for name, value in spec.get('extraFiles', {}).items():
        data = value.encode()
        inputs[relative_name(name)] = {'sha256': digest(data), 'bytes': len(data)}
    for name in spec.get('omitInputs', []):
        inputs.pop(relative_name(name), None)
    if execution.get('inputs') != {name: record['sha256'] for name, record in inputs.items()}:
        raise RuntimeError(f'{case_id}: execution did not identify the exact fixture and derived input bytes')
    expected_stdin = None
    if spec.get('stdinFile'):
        source = inputs[relative_name(spec['stdinFile'])]
        transport = 'native-regular-file' if target in ('native', 'patched-native') and spec.get('nativeStdinTransport') == 'file' else 'stdin-bytes'
        expected_stdin = {'sha256': source['sha256'], 'bytes': source['bytes'], 'transport': transport}
    if 'stdin' not in execution or execution['stdin'] != expected_stdin:
        raise RuntimeError(f'{case_id}: standard input differs from its bound fixture')
    if target in ('native', 'patched-native'):
        program = f'build/{target}/{spec["program"]}'
        args = spec['args']
        if execution.get('command') != {'program': program, 'args': args}:
            raise RuntimeError(f'{case_id}: executed native command differs from the case plan')


def check_reports(tool: str, reports: list[Path], builds: dict) -> dict:
    checked, run_ids = {}, set()
    runtime = {name: digest(required(local_path(name))) for name in RUNTIME[tool]}
    validation = {name: digest(required(local_path(name))) for name in VALIDATION}
    manifest, manifest_sha = checked_dataset_manifest()
    datasets = {name: {'manifestSha256': manifest_sha,
                      'files': {path: info['sha256'] for path, info in manifest['files'].items() if path.startswith(name + '/')},
                      'observations': metadata['observations']}
                for name, metadata in manifest['datasets'].items() if name in read_json('validation/primer3/cases.json')['tools'][tool]['datasets']}
    plan, empty_hash = planned_cases(tool), digest(b'')
    for path in reports:
        raw = path.read_bytes()
        report = json.loads(raw)
        if report.get('tool') not in TOOLS:
            raise RuntimeError(f'Unknown validation report tool: {path}')
        if report['tool'] != tool:
            continue
        engine = report.get('engine')
        if engine not in ENGINES or engine in checked:
            raise RuntimeError(f'{tool}: duplicate or unknown validation engine: {engine}')
        run_id = report.get('runId')
        try:
            valid_run = isinstance(run_id, str) and str(UUID(run_id)) == run_id
        except ValueError:
            valid_run = False
        if not valid_run or run_id in run_ids:
            raise RuntimeError(f'{tool}: missing or reused independent validation run identity')
        run_ids.add(run_id)
        if report.get('schema') != 1 or report.get('status') != 'passed':
            raise RuntimeError(f'{tool}: report is not a passing schema-1 validation: {path}')
        if not isinstance(report.get('node'), str) or not report['node']:
            raise RuntimeError(f'{tool}: report omits the Node host version')
        if not isinstance(report.get('python'), str) or not re.fullmatch(r'Python 3\.\d+\.\d+', report['python']):
            raise RuntimeError(f'{tool}: report omits the Python host version')
        if engine != 'node' and (not isinstance(report.get('browserVersion'), str) or not report['browserVersion']):
            raise RuntimeError(f'{tool}: report omits the actual browser version')
        if engine != 'node' and report.get('crossOriginIsolated') is not False:
            raise RuntimeError(f'{tool}: missing plain static-hosting browser evidence')
        if report.get('builds') != {name: builds[name] for name in BUNDLES[tool]}:
            raise RuntimeError(f'{tool}: tool or dependency build differs from {path}')
        if report.get('runtime') != runtime or report.get('validation') != validation:
            raise RuntimeError(f'{tool}: runtime or validation source differs from {path}')
        if report.get('manifestSha256') != manifest_sha or report.get('datasets') != datasets:
            raise RuntimeError(f'{tool}: real dataset identity differs from {path}')
        expected, records = report.get('expectedCases', []), report.get('cases', [])
        actual = [record.get('id') for record in records]
        if (len(expected) != len(set(expected)) or set(expected) != plan.keys()
                or len(actual) != len(set(actual)) or set(actual) != plan.keys()
                or any(record.get('status') != 'passed' for record in records)):
            raise RuntimeError(f'{tool}: report omits the complete passing case plan')
        by_id = {record['id']: record for record in records}
        for case_id, spec in plan.items():
            record = by_id[case_id]
            for key, value in {'tool': tool, 'dataset': spec['_dataset'], 'case': spec['id'],
                               'kind': spec['kind'], 'runner': spec['runner'], 'program': spec['program'], 'args': spec['args']}.items():
                if record.get(key) != value:
                    raise RuntimeError(f'{case_id}: command or runner differs from the case plan')
            executions = record.get('executions', {})
            if set(executions) != set(spec['executions']):
                raise RuntimeError(f'{case_id}: required native/patched-native/WASM execution is missing')
            failure = spec['kind'] in ('failure', 'restriction')
            if 'errorPolicyObserved' in record:
                raise RuntimeError(f'{case_id}: unplanned signal exception')
            for target, execution in executions.items():
                if 'signal' not in execution or execution['signal'] is not None:
                    raise RuntimeError(f'{case_id}: missing signal evidence or unexpected crash for {target}')
                code = execution.get('exitCode')
                canonical = (code & 255) if type(code) is int and code < 0 else code
                if type(code) is not int or execution.get('canonicalExitCode') != canonical or (canonical == 0) == failure:
                    raise RuntimeError(f'{case_id}: invalid or unexpected recorded exit code')
                if 'expectedExitCode' in spec and canonical != spec['expectedExitCode']:
                    raise RuntimeError(f'{case_id}: failure exit differs from the planned status')
                files, lengths = execution.get('files', {}), execution.get('fileBytes', {})
                if set(lengths) != set(files) or any(type(size) is not int or size < 0 for size in lengths.values()):
                    raise RuntimeError(f'{case_id}: output byte lengths are missing or invalid')
                for name, sha in files.items():
                    relative_name(name)
                    if not isinstance(sha, str) or not SHA256.fullmatch(sha):
                        raise RuntimeError(f'{case_id}: invalid output checksum')
                    if (lengths[name] == 0) != (sha == empty_hash):
                        raise RuntimeError(f'{case_id}: empty output hash and byte length disagree')
                if not failure and (execution.get('missingOutputs') != [] or not set(spec['outputs']) <= files.keys()):
                    raise RuntimeError(f'{case_id}: successful command omits requested outputs')
                for stream in ('stdout', 'stderr'):
                    if not isinstance(execution.get(stream), str) or not SHA256.fullmatch(execution[stream]):
                        raise RuntimeError(f'{case_id}: missing {stream} evidence')
                if spec.get('stdoutFile') and files.get(spec['stdoutFile']) != execution['stdout']:
                    raise RuntimeError(f'{case_id}: standard-output artifact differs from captured bytes')
                if failure and (execution['stdout'] == execution['stderr'] == empty_hash
                                or not spec.get('diagnosticRegex') or execution.get('diagnosticMatched') is not True):
                    raise RuntimeError(f'{case_id}: required failure diagnostic was not verified')
                dependencies = execution.get('inputsFrom')
                if not isinstance(dependencies, list) or len(dependencies) != len(spec.get('inputsFrom', [])):
                    raise RuntimeError(f'{case_id}: derived input bytes or source build differ')
                for source, observed in zip(spec.get('inputsFrom', []), dependencies):
                    source_target = source.get('target', target)
                    source_id = f'{tool}-{spec["_dataset"]}-{source["case"]}'
                    origin = by_id[source_id]['executions'][source_target]
                    sha, size = origin['files'][source['path']], origin['fileBytes'][source['path']]
                    expected = {**source, 'sourceTarget': source_target, 'sourceSha256': sha, 'sourceBytes': size}
                    if source.get('transform'):
                        if (source['transform'] != 'remove-final-newline' or size <= 0
                                or not isinstance(observed.get('sha256'), str) or not SHA256.fullmatch(observed['sha256'])
                                or observed['sha256'] == sha):
                            raise RuntimeError(f'{case_id}: invalid final-newline transformation')
                        expected.update(sha256=observed['sha256'], bytes=size - 1, removedFinalByte=10)
                    else:
                        expected.update(sha256=sha, bytes=size)
                    if observed != expected:
                        raise RuntimeError(f'{case_id}: derived input bytes or source build differ')
                check_execution_inputs(case_id, tool, target, spec, execution, manifest)
            if failure and len({execution['canonicalExitCode'] for execution in executions.values()}) != 1:
                raise RuntimeError(f'{case_id}: native and WebAssembly failure exits disagree')
            comparisons = record.get('comparisons', {})
            if set(comparisons) != set(spec['comparisons']):
                raise RuntimeError(f'{case_id}: required semantic comparisons are missing')
            baseline_id = f'{tool}-{spec["_dataset"]}-{spec["baseline"]}' if spec['kind'] == 'repeat' else case_id
            native_contract = by_id[baseline_id]['comparisons'].get('native-contract')
            for target, comparison in comparisons.items():
                if (comparison.get('equal') is not True or comparison.get('differences') != []
                        or any(not isinstance(comparison.get(key), str) or not SHA256.fullmatch(comparison[key])
                               for key in ('expectedSemanticDigest', 'semanticDigest', 'biologicalDigest'))):
                    raise RuntimeError(f'{case_id}: semantic comparison did not pass')
                if (not native_contract or comparison['expectedSemanticDigest'] != native_contract['semanticDigest']
                        or comparison.get('expectedBiologicalDigest') != native_contract['biologicalDigest']
                        or comparison.get('expectedCanonicalFiles') != native_contract.get('canonicalFiles')):
                    raise RuntimeError(f'{case_id}: comparison is not bound to its unchanged native baseline')
                if not any(item.get('floatColumns') for item in spec['artifacts']):
                    if (comparison['semanticDigest'] != comparison['expectedSemanticDigest']
                            or comparison['biologicalDigest'] != native_contract['biologicalDigest']):
                        raise RuntimeError(f'{case_id}: exact comparison differs from its unchanged native baseline')
                check_invariants(case_id, spec, comparison)
                check_comparison_files(case_id, spec, comparison, by_id[baseline_id]['executions']['native'],
                                       executions['native' if target == 'native-contract' else target])
            if spec.get('effect') or spec.get('equivalentTo'):
                baseline = spec.get('effect') or spec['equivalentTo']
                before = by_id[f'{tool}-{spec["_dataset"]}-{baseline}']['comparisons']['native-contract']['biologicalDigest']
                after, changed = comparisons['native-contract']['biologicalDigest'], bool(spec.get('effect'))
                if (record.get('optionEffect') != {'baseline': baseline, 'before': before, 'after': after,
                                                  'changed': changed, 'expected': 'changed' if changed else 'equivalent'}
                        or (before != after) != changed):
                    raise RuntimeError(f'{case_id}: required option effect is missing or inconsistent')
        checked[engine] = (raw, report)
    if set(checked) != ENGINES:
        raise RuntimeError(f'{tool}: missing passing reports for ' + ', '.join(sorted(ENGINES - checked.keys())))
    return checked


def example_readme(tool: str) -> str:
    return f'''# Primer3 in the browser

Run `python3 -m http.server 8000` from the package root and open
`http://localhost:8000/examples/primer3/`. The server serves the included static
files. Computation runs locally in a browser worker. Load the bundled inputs
or upload files, select a preset, and download the resulting files.

See [API documentation](../../runtime/PRIMER3.md),
[tool documentation](../../tools/primer3/README.md),
[data provenance](../../datasets/primer3/README.md), and
[validation scope](../../validation/primer3/README.md).
'''


def readme(tool: str, lock: dict) -> str:
    return f'''# Primer3 {lock['version']} for WebAssembly

This independent package contains the JavaScript API, browser example, compiled
runtime, pinned source archives, build recipes, licence notices, fixture inputs,
provenance and validation reports. Its scientific scope and supported options
are documented in `tools/primer3/README.md` and `validation/primer3/README.md`.

## Run locally

Run `python3 -m http.server 8000` here and open
`http://localhost:8000/examples/primer3/`. Choose an example or upload inputs,
run a command and download its outputs. Runtime assets and inputs are included;
computation runs in a browser worker. Keep `dist/primer3/` together.
See `runtime/PRIMER3.md` for the JavaScript API and memory limits.

## Rebuild and validate

Follow `tools/primer3/README.md` to install the pinned host prerequisites and build
the unchanged upstream native, patched native and WebAssembly implementations.
Original sources and recipes are included. Host compilers and browser test
tools may require downloads. The dataset README explains regeneration from
the included original inputs.

```sh
python3 scripts/fetch-primer3-test-data.py --verify
npm ci
npm test
node scripts/validate-primer3.mjs --tool primer3 --engine node --dataset all
npx playwright install chromium firefox webkit
```

Repeat validation with `--engine chromium`, `--engine firefox`, and
`--engine webkit`. Run `BROWSER=chromium node scripts/test-primer3-demo.mjs`,
changing the browser name for the other engines.

The supplied reports identify the exact compiled assets, native environments,
runtime, source recipes, input data, commands and configured comparisons. The
package gate independently verifies this recorded evidence, including bound
output byte hashes, unchanged native baselines, nonempty biological invariants
and declared numerical policies. It does not recompute scientific comparisons
from retained output files. The reports cover the listed fixtures and options,
not every input or large-dataset performance. `release.json` records the tested
engines. Verify the package with `sha256sum -c SHA256SUMS`.

## Licences

`LICENSE` covers project infrastructure. Retain upstream notices, corresponding
source archives, recipes, patches and fixture provenance when redistributing.
Tool and dependency notices are under `tools/primer3/` and `dist/primer3/notices/`.
'''


def collect(tool: str, reports: list[Path]) -> tuple[dict[str, tuple[bytes, int]], dict]:
    if tool not in TOOLS:
        raise RuntimeError(f'Unknown tool: {tool}')
    identities = {name: check_build_identity(name) for name in BUNDLES[tool]}
    builds = {name: result[2] for name, result in identities.items()}
    checked = {name: check_reports(name, reports, builds) for name in BUNDLES[tool]}
    manifest, manifest_sha = checked_dataset_manifest()
    entries, sources = {}, {}
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
    def tree(directory: str, *, source=True, source_tree=False):
        for name in tree_hashes(directory, source_tree=source_tree):
            add(f'{directory}/{name}', source=source)
    for name in ('LICENSE', *RUNTIME[tool], 'runtime/README.md', 'scripts/setup-emsdk.sh',
                 'scripts/package-primer3.py', *VALIDATION, 'scripts/test-primer3-demo.mjs',
                 'scripts/fetch-primer3-test-data.py', 'tests/test_primer3_compare.py',
                 'tests/test_primer3_package.py', 'tests/runtime.test.mjs', 'tests/fixtures/mock-tool.mjs'):
        add(name)
    for name, (lock, identity, _) in identities.items():
        add(f'runtime/{name.upper()}.md')
        add(f'tests/{name}.test.mjs')
        for fixture in TEST_FIXTURES[name]:
            add(fixture)
        tree(f'tools/{name}', source_tree=True)
        for path, checksum in identity['inputs'].items():
            add(path, expected=checksum)
        tree(f'dist/{name}', source=False)
        for archive, checksum in check_sources(name, lock).items():
            sources[f'dist/{name}/sources/{archive}'] = checksum
    for directory in (EXAMPLE, 'validation/primer3', DATA):
        tree(directory, source_tree=True)
    original_sources = manifest.get('sources')
    if not isinstance(original_sources, list) or not original_sources:
        raise RuntimeError('The primer3 manifest omits original source inputs')
    source_names, source_paths = set(), set()
    for record in original_sources:
        name = relative_name(record['name'])
        cache = relative_name(record['cachePath'])
        if name in source_names or cache in source_paths:
            raise RuntimeError('Duplicate original dataset source identity')
        source_names.add(name)
        source_paths.add(cache)
        if not cache.startswith('build/primer3-sources/'):
            raise RuntimeError(f'Dataset source lies outside its expected cache: {name}')
        data = verify(local_path(cache), record['sha256'])
        if type(record.get('bytes')) is not int or record['bytes'] != len(data):
            raise RuntimeError(f'Dataset source byte count differs: {name}')
        put(cache, data)
    regeneration = manifest.get('regeneration', {}).get('files', {})
    if 'scripts/fetch-primer3-test-data.py' not in regeneration:
        raise RuntimeError('The primer3 manifest omits its regeneration recipe')
    for name, info in regeneration.items():
        add(name, expected=info['sha256'] if isinstance(info, dict) else info)
        if isinstance(info, dict) and (type(info.get('bytes')) is not int or info['bytes'] != len(entries[name][0])):
            raise RuntimeError(f'Regeneration recipe byte count differs: {name}')
    config = checked_example_config(manifest)
    runtime_doc = entries['runtime/README.md'][0].decode().split('## Interface', 1)
    if len(runtime_doc) != 2:
        raise RuntimeError('The runtime README is missing its interface documentation')
    replacement = ('# JavaScript runner\n\nSee the package README and `examples/primer3/` for the included tools.\n\n## Interface' + runtime_doc[1]).encode()
    entries['runtime/README.md'], sources['runtime/README.md'] = (replacement, 0o644), digest(replacement)
    playwright = read_json('package.json')['devDependencies']['playwright']
    if not re.fullmatch(r'\d+\.\d+\.\d+', playwright):
        raise RuntimeError('Playwright must be pinned to an exact version')
    node_tests = 'tests/runtime.test.mjs ' + ' '.join(f'tests/{name}.test.mjs' for name in BUNDLES[tool])
    lock = identities[tool][0]
    npm_version = lock['version']
    package = {'name': f'{tool}-wasm', 'version': npm_version, 'private': True, 'type': 'module',
               'scripts': {'test': 'python3 -m unittest discover -s tests -p "test_primer3_*.py" && node --test ' + node_tests,
                           'validate': f'node scripts/validate-primer3.mjs --tool {tool}'},
               'devDependencies': {'playwright': playwright}, 'engines': {'node': '>=20'}}
    npm_lock = read_json('package-lock.json')
    if npm_lock['packages']['']['devDependencies'] != package['devDependencies']:
        raise RuntimeError('The npm lock differs from the pinned browser test dependency')
    npm_lock.update(name=package['name'], version=package['version'])
    npm_lock['packages'][''].update(name=package['name'], version=package['version'])
    put('package.json', json_bytes(package))
    put('package-lock.json', json_bytes(npm_lock))
    report_hashes = {}
    for name, engines in checked.items():
        report_hashes[name] = {}
        for engine, (raw, _) in engines.items():
            put(f'validation-reports/{name}/{engine}.json', raw, source=False)
            report_hashes[name][engine] = digest(raw)
    put('source-manifest.json', json_bytes({
        'schemaVersion': 1, 'tool': tool, 'upstreamVersion': lock['version'],
        'tools': {name: {'version': result[0]['version'], 'upstreamLockSha256': digest(required(local_path(f'tools/{name}/sources.lock.json')))}
                  for name, result in identities.items()},
        'files': dict(sorted(sources.items())),
        'notes': 'Includes complete upstream archives, notices, patches, recipes, runtime and biological-data provenance.',
    }), source=False)
    put('release.json', json_bytes({
        'schemaVersion': 1, 'tool': tool, 'upstreamVersion': lock['version'], 'bundledTools': list(BUNDLES[tool]),
        'builds': builds, 'datasetManifestSha256': manifest_sha,
        'validationStatus': 'passed for the recorded test scope', 'testedEngines': sorted(ENGINES),
        'reports': report_hashes, 'requiresCrossOriginIsolation': False,
    }), source=False)
    put('README.md', readme(tool, lock).encode(), source=False)
    put('SHA256SUMS', ''.join(f'{digest(data)}  {name}\n' for name, (data, _) in sorted(entries.items())).encode(), source=False)
    return entries, lock


def write_package(tool: str, output: Path, reports: list[Path], *, prepared=None) -> Path:
    entries, lock = prepared if prepared is not None else collect(tool, reports)
    stem = f'{tool}-{lock["version"]}-wasm'
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
    parser.add_argument('--tool', choices=(*TOOLS, 'all'), default='all')
    parser.add_argument('--output', type=Path, default=ROOT / 'releases')
    parser.add_argument('--validation-report', action='append', type=Path, required=True)
    args = parser.parse_args()
    requested = TOOLS if args.tool == 'all' else (args.tool,)
    prepared = {tool: collect(tool, args.validation_report) for tool in requested}
    for tool in requested:
        write_package(tool, args.output.resolve(), args.validation_report, prepared=prepared[tool])


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, KeyError, OSError, TypeError, zipfile.BadZipFile, tarfile.TarError) as error:
        print(f'Packaging failed: {error}', file=sys.stderr)
        raise SystemExit(1)
