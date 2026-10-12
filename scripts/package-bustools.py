#!/usr/bin/env python3
"""Package independently usable bustools after complete native and browser validation."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tarfile
import zipfile
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ('bustools',)
VERSIONS = {'bustools': '0.45.1'}
BUNDLES = {tool: (tool,) for tool in TOOLS}
LABELS = {'bustools': 'bustools'}
ENGINES = {'node', 'chromium', 'firefox', 'webkit'}
BASE_RUNTIME = ('runtime/index.mjs', 'runtime/worker.mjs', 'runtime/execute.mjs')
RUNTIME = {tool: (*BASE_RUNTIME, f'runtime/{tool}.mjs') for tool in TOOLS}
PROGRAMS = {'bustools': ('bustools',)}
VALIDATION = ('scripts/validate-bustools.mjs', 'validation/bustools/compare.py', 'validation/bustools/cases.json')
TEST_FIXTURES = {'bustools': ()}
DATA = 'datasets/bustools'
EXAMPLE = 'examples/bustools'
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
    return {'bustools': digest(required(local_path(f'build/{kind}/bustools')))}


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
    if lock.get('tool') != tool or lock.get('version') != VERSIONS[tool]:
        raise RuntimeError(f'{tool}: source version differs from the release contract')
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
    plan = read_json('validation/bustools/cases.json')
    datasets = plan.get('datasets', [])
    if (manifest.get('schema_version') != 1 or not datasets or len(datasets) != len(set(datasets))
            or set(manifest.get('datasets', {})) != set(datasets)):
        raise RuntimeError('The bustools manifest must identify every planned real dataset')
    if not manifest.get('files'):
        raise RuntimeError('The bustools manifest contains no verified files')
    for name, info in manifest['files'].items():
        data = verify(local_path(f'{DATA}/{relative_name(name)}'), info['sha256'])
        if type(info.get('bytes')) is not int or info['bytes'] != len(data):
            raise RuntimeError(f'Dataset byte count differs from its manifest: {name}')
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
        if dataset.get('path') != f'../../datasets/bustools/{name}/':
            raise RuntimeError('Browser example must use its bundled local fixture')
        files = dataset.get('files')
        if not isinstance(files, list) or not files or len(files) != len(set(files)):
            raise RuntimeError('Browser example fixture files must be nonempty and unique')
        for path in files:
            if f'{name}/{relative_name(path)}' not in manifest['files']:
                raise RuntimeError(f'Browser example input is absent from the dataset manifest: {path}')
    return config


def planned_cases(tool: str) -> dict:
    plan = read_json('validation/bustools/cases.json')
    if plan.get('schema') != 1 or not plan.get('datasets'):
        raise RuntimeError('Unknown bustools case-plan schema or datasets')
    result = {}
    for spec in plan['tools'][tool]['cases']:
        if spec.get('runner') != tool:
            raise RuntimeError(f'{tool}: standalone case uses an unbundled runner')
        if spec.get('kind') not in ('success', 'repeat', 'failure', 'restriction') or 'nativeErrorPolicy' in spec:
            raise RuntimeError(f'{tool}: unsupported case kind or signal exception')
        if spec['kind'] == 'success' and (set(spec.get('executions', [])) != {'native', 'patched-native', 'wasm'}
                or set(spec.get('comparisons', [])) != {'native-contract', 'patched-native', 'wasm'}):
            raise RuntimeError(f'{tool}: successful cases must compare all three implementations')
        if spec['kind'] in ('failure', 'restriction') and spec.get('comparisons') != []:
            raise RuntimeError(f'{tool}: failure cases must record exits and diagnostics without semantic comparisons')
        if spec['kind'] == 'repeat' and (spec.get('executions') != ['wasm']
                or spec.get('comparisons') != ['wasm'] or not spec.get('baseline')):
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
    return result


def check_invariants(case_id: str, spec: dict, comparison: dict) -> None:
    kinds = {'bus', 'busz', 'matrix', 'identifiers', 'ec', 'json', 'fastq-gzip', 'text', 'exact'}
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
            if kind not in kinds or not isinstance(counts, dict):
                raise RuntimeError(f'{case_id}: unknown artifact invariant kind: {kind}')
            count = counts.get('records')
            minimum = artifact.get('minimumRecords', 0 if artifact.get('allowEmpty') else 1)
            if type(count) is not int or count < minimum:
                raise RuntimeError(f'{case_id}: missing nonempty {kind} evidence')
            for key, lower_bound in artifact.get('minimum', {}).items():
                value = counts.get(key)
                if type(value) not in (int, float) or not math.isfinite(value) or value < lower_bound:
                    raise RuntimeError(f'{case_id}: missing required {kind} invariant: {key}')
            for key, expected in artifact.get('expected', {}).items():
                if key not in counts or counts[key] != expected:
                    raise RuntimeError(f'{case_id}: incorrect required {kind} invariant: {key}')
            if kind in ('exact', 'busz') and (count != 1 or type(counts.get('bytes')) is not int
                    or counts['bytes'] < (0 if artifact.get('allowEmpty') else 1)):
                raise RuntimeError(f'{case_id}: missing complete binary artifact evidence')
            if kind == 'busz' and counts['bytes'] < 40:
                raise RuntimeError(f'{case_id}: incomplete compressed BUS evidence')
            if kind == 'bus':
                for key in ('barcodes', 'umis', 'ecs', 'duplicateMultiplicity', 'nonzeroFlags'):
                    if type(counts.get(key)) is not int or not 0 <= counts[key] <= count:
                        raise RuntimeError(f'{case_id}: inconsistent BUS record counts: {key}')
                if (type(counts.get('totalCount')) is not int or counts['totalCount'] < count
                        or type(counts.get('headerTextBytes')) is not int or counts['headerTextBytes'] < 0
                        or any(type(counts.get(key)) is not int or not 1 <= counts[key] <= 32
                               for key in ('barcodeLength', 'umiLength'))
                        or (count == 0 and counts['totalCount'] != 0)
                        or count > 0 and any(counts[key] == 0 for key in ('barcodes', 'umis', 'ecs'))):
                    raise RuntimeError(f'{case_id}: inconsistent BUS header or molecule counts')
            if kind == 'matrix':
                if any(type(counts.get(key)) is not int or counts[key] <= 0 for key in ('rows', 'columns')):
                    raise RuntimeError(f'{case_id}: missing matrix dimensions')
                for key, dimension in (('nonemptyRows', 'rows'), ('nonemptyColumns', 'columns')):
                    if type(counts.get(key)) is not int or not (1 if count else 0) <= counts[key] <= min(count, counts[dimension]):
                        raise RuntimeError(f'{case_id}: inconsistent nonempty matrix dimensions')
                total = counts.get('totalCount')
                if (type(total) not in (int, float) or not math.isfinite(total) or total < 0
                        or (count > 0) != (total > 0)):
                    raise RuntimeError(f'{case_id}: missing matrix count coverage')
                for field, axis in (('rowLabels', 'rows'), ('columnLabels', 'columns')):
                    if field in artifact and (artifact[field] not in checks or checks[artifact[field]]['records'] != counts[axis]):
                        raise RuntimeError(f'{case_id}: matrix identity count differs from {axis}')
            if kind == 'ec' and (type(counts.get('transcripts')) is not int
                                 or counts['transcripts'] < (1 if count else 0)):
                raise RuntimeError(f'{case_id}: missing transcript membership coverage')
    if comparison['invariants'] != comparison['nativeInvariants']:
        raise RuntimeError(f'{case_id}: native and WebAssembly output invariants differ')


def check_comparison_files(case_id: str, spec: dict, comparison: dict, native: dict, actual: dict) -> None:
    paths = [item['path'] for item in spec['artifacts']]
    if (comparison.get('expectedFiles') != comparison.get('actualFiles')
            or comparison.get('expectedFileBytes') != comparison.get('actualFileBytes')):
        raise RuntimeError(f'{case_id}: exact comparison output bytes differ')
    for prefix, execution in (('expected', native), ('actual', actual)):
        if (any(path not in execution['files'] for path in paths)
                or comparison.get(prefix + 'Files') != {path: execution['files'][path] for path in paths}
                or comparison.get(prefix + 'FileBytes') != {path: execution['fileBytes'][path] for path in paths}):
            raise RuntimeError(f'{case_id}: comparison is not bound to recorded execution output bytes')


def check_execution_inputs(case_id: str, tool: str, target: str, spec: dict, execution: dict, manifest: dict) -> None:
    plan = read_json('validation/bustools/cases.json')
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
        expected_stdin = {'sha256': source['sha256'], 'bytes': source['bytes'], 'transport': 'stdin-bytes'}
    if 'stdin' not in execution or execution['stdin'] != expected_stdin:
        raise RuntimeError(f'{case_id}: standard input differs from its bound fixture')
    if target in ('native', 'patched-native'):
        program = f'build/{target}/bustools'
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
                for name, metadata in manifest['datasets'].items() if name in read_json('validation/bustools/cases.json')['tools'][tool]['datasets']}
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
            raise RuntimeError(f'{tool}: browser execution omitted ordinary static-hosting evidence')
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
                               'kind': spec['kind'], 'runner': spec['runner'], 'args': spec['args']}.items():
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
                if (comparison.get('equal') is not True or comparison.get('differences') != [] or comparison.get('numericTolerance') != 0
                        or any(not isinstance(comparison.get(key), str) or not SHA256.fullmatch(comparison[key])
                               for key in ('expectedSemanticDigest', 'semanticDigest', 'biologicalDigest'))
                        or comparison['expectedSemanticDigest'] != comparison['semanticDigest']):
                    raise RuntimeError(f'{case_id}: semantic comparison did not pass')
                if (not native_contract or comparison['expectedSemanticDigest'] != native_contract['semanticDigest']
                        or comparison['biologicalDigest'] != native_contract['biologicalDigest']
                        or comparison.get('expectedBiologicalDigest') != native_contract['biologicalDigest']):
                    raise RuntimeError(f'{case_id}: comparison is not bound to its unchanged native baseline')
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
    return f'''# {LABELS[tool]} in the browser

Run `python3 -m http.server 8000` from the package root and open
`http://localhost:8000/examples/bustools/`. Load the bundled biological data
or upload your own inputs. Commands run locally in browser workers. Ordinary
static hosting is sufficient; cross-origin isolation is not required.

Arguments are a JSON array. Generated files can be retained for subsequent
commands and downloaded. Cancel stops the worker; reloading clears its files.
See [tool documentation](../../tools/{tool}/README.md),
[data provenance](../../datasets/bustools/README.md), and
[validation rules](../../validation/bustools/README.md).
'''


def readme(tool: str, lock: dict) -> str:
    return f'''# {LABELS[tool]} {lock['version']} for WebAssembly

This independent package contains compiled programs, a JavaScript API, browser
example, biological test data, validation reports, complete upstream source
archives, patches, build recipes and licence notices.

## Browser and JavaScript

Run `python3 -m http.server 8000` here and open
`http://localhost:8000/examples/bustools/`. Load the example data or upload
files, choose a preset and run. Computation happens locally in workers; the
server only serves files. No cross-origin isolation headers, remote computation
or runtime downloads are needed.

See `runtime/{tool.upper()}.md` for the JavaScript API and
`tools/{tool}/README.md` for supported commands and limitations. Keep the
`dist/{tool}/` directory together. Node needs no npm packages to run the API.
The wasm32 serial build has a 2 GiB heap ceiling; practical input limits are
smaller and depend on the workload and browser memory.

## Rebuild and validate

Install the host prerequisites documented under `tools/{tool}/`, including the
pinned Emscripten version. Recipes verify and reuse the included original
source archives. Installing host compilers may require network access.

```sh
bash scripts/build-{tool}.sh all
python3 scripts/fetch-bustools-test-data.py --verify
npm ci
npm test
node scripts/validate-bustools.mjs --tool {tool} --engine node --dataset all
npx playwright install chromium firefox webkit
```

Repeat validation with `--engine chromium`, `--engine firefox` and
`--engine webkit`. Run `BUSTOOLS_TOOL={tool} BROWSER=chromium node scripts/test-bustools-demo.mjs`, changing the browser for the other engines.
The data README documents regeneration from the included original inputs.

Reports bind exact compiled assets, source recipes, native binaries, runtime,
biological data and the complete configured case plan. They compare unchanged
upstream native programs, patched native programs and WebAssembly in Node and
three browser engines. The evidence covers the recorded fixture and options;
it does not establish performance or accuracy on every possible input.
`release.json` records the tested engines. Verify files using
`sha256sum -c SHA256SUMS`.

## Licences

`LICENSE` covers project infrastructure. Retain upstream notices, source
archives, patches, recipes and data provenance when redistributing. Tool and
runtime notices are under `tools/` and `dist/{tool}/notices/`.
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
                 'scripts/package-bustools.py', *VALIDATION, 'scripts/test-bustools-demo.mjs',
                 'scripts/fetch-bustools-test-data.py', 'tests/test_bustools_compare.py',
                 'tests/test_bustools_package.py', 'tests/runtime.test.mjs', 'tests/fixtures/mock-tool.mjs'):
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
    for directory in (EXAMPLE, 'validation/bustools', DATA):
        tree(directory, source_tree=True)
    if not manifest.get('sources'):
        raise RuntimeError('The bustools manifest omits original source inputs')
    for name, record in manifest['sources'].items():
        cache = relative_name(record['cachePath'])
        if not cache.startswith('build/bustools-sources/'):
            raise RuntimeError(f'Dataset source lies outside its expected cache: {name}')
        data = verify(local_path(cache), record['sha256'])
        if type(record.get('bytes')) is not int or record['bytes'] != len(data):
            raise RuntimeError(f'Dataset source byte count differs: {name}')
        put(cache, data)
    for name, info in (manifest.get('regeneration', {}).get('files') or {}).items():
        add(name, expected=info['sha256'] if isinstance(info, dict) else info)
    config = checked_example_config(manifest)
    config['tools'] = {name: config['tools'][name] for name in BUNDLES[tool]}
    config['datasets'] = {tool: config['datasets'][tool]}
    for name, settings in config['tools'].items():
        if settings.get('requiresIsolation') or settings.get('waitForExit'):
            raise RuntimeError(f'{name}: browser threading settings differ from the serial port')
    for name, replacement in ((f'{EXAMPLE}/config.json', json_bytes(config)), (f'{EXAMPLE}/README.md', example_readme(tool).encode())):
        entries[name], sources[name] = (replacement, 0o644), digest(replacement)
    runtime_doc = entries['runtime/README.md'][0].decode().split('## Interface', 1)
    if len(runtime_doc) != 2:
        raise RuntimeError('The runtime README is missing its interface documentation')
    replacement = ('# JavaScript runner\n\nSee the package README and `examples/bustools/` for the included tools.\n\n## Interface' + runtime_doc[1]).encode()
    entries['runtime/README.md'], sources['runtime/README.md'] = (replacement, 0o644), digest(replacement)
    playwright = read_json('package.json')['devDependencies']['playwright']
    if not re.fullmatch(r'\d+\.\d+\.\d+', playwright):
        raise RuntimeError('Playwright must be pinned to an exact version')
    node_tests = 'tests/runtime.test.mjs ' + ' '.join(f'tests/{name}.test.mjs' for name in BUNDLES[tool])
    lock = identities[tool][0]
    npm_version = lock['version'] if lock['version'].count('.') == 2 else lock['version'] + '.0'
    package = {'name': f'{tool}-wasm', 'version': npm_version, 'private': True, 'type': 'module',
               'scripts': {'test': 'python3 -m unittest discover -s tests -p "test_bustools_*.py" && node --test ' + node_tests,
                           'validate': f'node scripts/validate-bustools.mjs --tool {tool}'},
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
