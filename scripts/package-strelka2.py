#!/usr/bin/env python3
"""Package standalone Strelka2 after complete native and browser validation."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tarfile
import types
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ('strelka2',)
LABELS = {'strelka2': 'Strelka2'}
PROGRAMS = ('strelka2', 'GetChromDepth', 'MergeRunStats', 'bgzip', 'bgzf_cat', 'tabix', 'htsfile')
MODELS = ('config/somaticSNVScoringModels.json', 'config/somaticIndelScoringModels.json')
ENGINES = {'node', 'chromium', 'firefox', 'webkit'}
BASE_RUNTIME = ('runtime/index.mjs', 'runtime/worker.mjs', 'runtime/execute.mjs')
RUNTIME = {'strelka2': (*BASE_RUNTIME, 'runtime/strelka2.mjs', 'runtime/strelka2-somatic.mjs')}
VALIDATION = ('scripts/validate-strelka2.mjs', 'validation/strelka2/compare.py',
              'validation/strelka2/cases.json', 'validation/strelka2/workflow.py', 'validation/compare.py')
DATA = 'datasets/strelka2'
EXAMPLE = 'examples/strelka2'
SHA256 = re.compile(r'[0-9a-f]{64}')


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


def tree_hashes(directory: str, *, suffixes=None, exclude=(), ignore_python_cache=False) -> dict[str, str]:
    base = local_path(directory)
    if not base.is_dir() or base.is_symlink():
        raise RuntimeError(f'Required package directory is missing or unsafe: {directory}')
    result = {}
    for path in sorted(base.rglob('*')):
        if ignore_python_cache and ('__pycache__' in path.relative_to(base).parts or path.suffix in ('.pyc', '.pyo')):
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
        name = relative_name(record['archive'])
        checksum = record.get('sha256')
        if not isinstance(checksum, str) or not SHA256.fullmatch(checksum):
            raise RuntimeError(f'{tool}: invalid source archive SHA256: {name}')
        if name in archives and archives[name] != checksum:
            raise RuntimeError(f'{tool}: conflicting source archive checksums: {name}')
        archives[name] = checksum

    add(lock)
    for dependency in lock.get('dependencies', {}).values():
        for record in dependency if isinstance(dependency, list) else [dependency]:
            if 'bundledPath' in record:
                relative_name(record['bundledPath'])
                if not SHA256.fullmatch(record.get('sha256', '')):
                    raise RuntimeError(f'{tool}: bundled dependency lacks its archive checksum')
            else:
                add(record)
    for record in lock.get('sourceArchives', []):
        add(record)
    return archives


def check_embedded_archives(lock: dict):
    """Verify nested upstream archives without distributing duplicate copies."""
    records = [record for dependency in lock.get('dependencies', {}).values()
               for record in (dependency if isinstance(dependency, list) else [dependency])
               if 'bundledPath' in record]
    records += lock.get('bundledDependencies', [])
    seen = {}
    with tarfile.open(local_path(f'dist/strelka2/sources/{relative_name(lock["archive"])}')) as archive:
        members = archive.getmembers()
        for record in records:
            name = relative_name(record['bundledPath'])
            if not SHA256.fullmatch(record.get('sha256', '')) or name in seen:
                raise RuntimeError(f'Invalid or duplicate embedded source record: {name}')
            seen[name] = record['sha256']
            matches = [member for member in members if member.name == name or member.name.split('/', 1)[-1] == name]
            if len(matches) != 1 or not matches[0].isfile():
                raise RuntimeError(f'Expected one regular embedded dependency archive: {name}')
            stream = archive.extractfile(matches[0])
            if stream is None or digest(stream.read()) != record['sha256']:
                raise RuntimeError(f'Embedded source checksum mismatch: {name}')


def check_native_python() -> dict:
    prefix = local_path('build/strelka2-python')
    raw = required(prefix / 'build-manifest.json')
    identity = json.loads(raw)
    lock = read_json('tools/strelka2-python/sources.lock.json')
    if (identity.get('schema') != 1 or identity.get('tool') != 'cpython'
            or identity.get('version') != '2.7.18' or identity.get('source') != lock):
        raise RuntimeError('Native Python identity differs from the pinned 2.7.18 source')
    inputs = {f'tools/strelka2-python/{name}' for name in tree_hashes('tools/strelka2-python')}
    inputs.add('scripts/setup-strelka2-python.py')
    if set(identity.get('inputs', {})) != inputs:
        raise RuntimeError('Native Python identity omits source recipe inputs')
    for name, expected in identity['inputs'].items():
        verify(local_path(name), expected)
    files, links = {}, {}
    for path in sorted(prefix.rglob('*')):
        if path.is_symlink():
            if not path.resolve().is_relative_to(prefix.resolve()):
                raise RuntimeError('Native Python symlink escapes the recorded installation')
            links[relative_name(path.relative_to(prefix).as_posix())] = path.readlink().as_posix()
            continue
        if path.is_file() and path.name != 'build-manifest.json':
            files[relative_name(path.relative_to(prefix).as_posix())] = digest(required(path))
    if not files or identity.get('files') != files or identity.get('symlinks') != links or 'bin/python2.7' not in files:
        raise RuntimeError('Native Python installation differs from its complete recorded file tree')
    return {'version': '2.7.18', 'sha256': files['bin/python2.7'],
            'buildManifest': digest(raw), 'files': files, 'symlinks': links}


def check_build_identity(tool: str) -> tuple[dict, dict, dict]:
    lock = read_json(f'tools/{tool}/sources.lock.json')
    if lock.get('tool') != 'strelka2' or lock.get('version') != '2.9.10':
        raise RuntimeError('The standalone port must identify pinned Strelka2 2.9.10')
    raw = required(local_path(f'dist/{tool}/build-manifest.json'))
    manifest = json.loads(raw)
    assets = tree_hashes(f'dist/{tool}', exclude=('build-manifest.json',))
    if manifest.get('schema') != 1 or manifest.get('tool') != tool or manifest.get('assets') != assets:
        raise RuntimeError(f'{tool}: build manifest does not identify the compiled distribution')
    for program in PROGRAMS:
        if not required(local_path(f'dist/{tool}/{program}.wasm')).startswith(b'\0asm\1\0\0\0'):
            raise RuntimeError(f'{tool}: invalid WebAssembly header for {program}')
        if not required(local_path(f'dist/{tool}/{program}.mjs')):
            raise RuntimeError(f'{tool}: missing JavaScript module for {program}')
    for model in MODELS:
        if not read_json(f'dist/{tool}/{model}'):
            raise RuntimeError(f'{tool}: missing bundled scoring model: {model}')
    expected_inputs = {f'tools/{tool}/{name}' for name in tree_hashes(f'tools/{tool}')}
    expected_inputs.add(f'scripts/build-{tool}.sh')
    expected_inputs.update(f'tools/strelka2-python/{name}' for name in tree_hashes('tools/strelka2-python'))
    expected_inputs.add('scripts/setup-strelka2-python.py')
    expected_inputs.update(lock.get('buildScripts', []))
    recorded = manifest.get('inputs', {})
    if not isinstance(recorded, dict) or not expected_inputs <= recorded.keys():
        raise RuntimeError(f'{tool}: build manifest omits source inputs: {sorted(expected_inputs - recorded.keys())}')
    for name, checksum in recorded.items():
        verify(local_path(name), checksum)
        prefix = f'tools/{tool}/'
        if name.startswith(prefix):
            copied = local_path(f'dist/{tool}/{name[len(prefix):]}')
            if copied.is_file():
                verify(copied, checksum)
    build = {'assets': assets, 'module': assets[f'{tool}.mjs'], 'wasm': assets[f'{tool}.wasm'],
             'buildManifest': digest(raw),
             **{kind: digest(required(local_path(f'build/{kind}/{tool}'))) for kind in ('native', 'patched-native')}}
    environments = manifest.get('nativeFiles', {})
    if set(environments) != {'native', 'patched-native'}:
        raise RuntimeError('strelka2: both complete native workflow environments must be identified')
    for kind, recorded_files in environments.items():
        actual = {}
        for path in sorted(local_path(f'build/{kind}').glob('strelka2*')):
            if path.is_dir():
                actual.update({f'{path.name}/{name}': checksum for name, checksum in tree_hashes(path.relative_to(ROOT).as_posix()).items()})
            else:
                actual[path.name] = digest(required(path))
        if actual != recorded_files or actual.get('strelka2') != build[kind] or not any(name.endswith('.py') for name in actual):
            raise RuntimeError(f'strelka2: {kind} workflow environment differs from its build manifest')
        for program in PROGRAMS:
            if f'strelka2-package/libexec/{program}' not in actual:
                raise RuntimeError(f'strelka2: native environment omits helper {program}')
        for model in MODELS:
            if actual.get(f'strelka2-package/share/{model}') != assets[model]:
                raise RuntimeError(f'strelka2: browser and {kind} scoring model bytes differ')
    build['nativeFiles'] = environments
    build['nativePython'] = check_native_python()
    return lock, manifest, build


def planned_cases(tool: str) -> dict:
    plan = read_json('validation/strelka2/cases.json')
    if plan.get('schema') != 1 or plan.get('tool') != tool or plan.get('datasets') != ['hcc1395']:
        raise RuntimeError('Unknown Strelka2 case-plan schema, tool or datasets')
    result = {}
    for spec in plan['cases']:
        if spec.get('kind') not in ('success', 'repeat', 'failure', 'restriction') or 'nativeErrorPolicy' in spec:
            raise RuntimeError(f'{tool}: unsupported case kind or signal exception')
        if spec['kind'] == 'success' and (set(spec.get('executions', [])) != {'native', 'patched-native', 'wasm'}
                or set(spec.get('comparisons', [])) != {'native-contract', 'patched-native', 'wasm'}):
            raise RuntimeError(f'{tool}: successful cases must compare all three implementations')
        if spec['kind'] == 'repeat' and (spec.get('executions') != ['wasm']
                or spec.get('comparisons') != ['wasm'] or not spec.get('baseline')):
            raise RuntimeError(f'{tool}: repeats must identify their unchanged native baseline')
        datasets = [None] if spec.get('oncePerTool') else spec.get('datasets', plan['datasets'])
        for dataset in datasets:
            if dataset is not None and dataset not in plan['datasets']:
                raise RuntimeError(f'{tool}: unknown case dataset: {dataset}')
            case_id = f'{tool}-{dataset}-{spec["id"]}' if dataset else f'{tool}-{spec["id"]}'
            if case_id in result:
                raise RuntimeError(f'{tool}: duplicate planned case: {case_id}')
            result[case_id] = {**spec, '_dataset': dataset or plan['datasets'][0]}
    if not result:
        raise RuntimeError(f'{tool}: empty validation case plan')
    return result


def checked_dataset_manifest() -> tuple[dict, str]:
    raw = required(local_path(f'{DATA}/manifest.json'))
    manifest = json.loads(raw)
    if manifest.get('schema_version') != 1 or set(manifest.get('datasets', {})) != {'hcc1395'}:
        raise RuntimeError('The somatic manifest must identify the real matched HCC1395 dataset')
    if not manifest.get('files'):
        raise RuntimeError('The somatic manifest contains no checked files')
    if not isinstance(manifest.get('regionMappings'), list) or not manifest['regionMappings']:
        raise RuntimeError('The somatic manifest omits the original coordinate mappings')
    for name, info in manifest['files'].items():
        data = verify(local_path(f'{DATA}/{relative_name(name)}'), info['sha256'])
        if type(info.get('bytes')) is not int or info['bytes'] != len(data):
            raise RuntimeError(f'Dataset byte count differs from its manifest: {name}')
    return manifest, digest(raw)


def check_invariants(case_id: str, spec: dict, comparison: dict):
    """Require the planned biological checks on both sides of each comparison."""
    for side in ('invariants', 'nativeInvariants'):
        checks = comparison.get(side)
        if not isinstance(checks, dict) or not checks:
            raise RuntimeError(f'{case_id}: missing {side}')
        for vcf in spec.get('vcfs', []):
            counts = checks.get(vcf['path'], {})
            fields = ('records', 'variantRecords', 'snvRecords', 'indelRecords',
                      'somaticRecords', 'referenceCheckedRecords')
            if any(type(counts.get(field)) is not int or counts[field] < 0 for field in fields):
                raise RuntimeError(f'{case_id}: invalid VCF biological counts')
            if (counts['referenceCheckedRecords'] != counts['records']
                    or counts['variantRecords'] > counts['records']
                    or counts['snvRecords'] + counts['indelRecords'] > counts['variantRecords']
                    or counts['somaticRecords'] > counts['records']):
                raise RuntimeError(f'{case_id}: inconsistent VCF biological counts')
            for minimum, field in (('minRecords', 'records'), ('minVariants', 'variantRecords'),
                                   ('minSnvs', 'snvRecords'), ('minIndels', 'indelRecords'),
                                   ('minSomatic', 'somaticRecords')):
                if counts[field] < vcf.get(minimum, 0):
                    raise RuntimeError(f'{case_id}: missing nonempty biological evidence for {minimum}')
            if 'expectedSamples' in vcf and counts.get('samples') != vcf['expectedSamples']:
                raise RuntimeError(f'{case_id}: incorrect matched sample order')
        for auxiliary in spec.get('auxiliary', []):
            counts = checks.get(auxiliary['path'], {})
            kind = auxiliary['kind']
            fields = {'stats-xml': ('candidateIndels', 'nonCandidateIndels'),
                      'stats-tsv': ('CallRegionCandidateIndels', 'CallRegionNonCandidateIndels'),
                      'depth': ('contigs', 'positiveContigs'), 'bed': ('records', 'bases')}.get(kind)
            if fields is None or any(type(counts.get(field)) is not int or counts[field] < 0 for field in fields):
                raise RuntimeError(f'{case_id}: invalid {kind} output evidence')
            if kind == 'depth' and not 0 < counts['positiveContigs'] <= counts['contigs']:
                raise RuntimeError(f'{case_id}: chromosome-depth evidence is empty or inconsistent')
            if kind == 'bed' and (counts['records'] < auxiliary.get('minRecords', 0)
                                  or counts['bases'] < counts['records']):
                raise RuntimeError(f'{case_id}: callable-region evidence is empty or inconsistent')
            if any(counts.get(name) != value for name, value in auxiliary.get('expectedCounts', {}).items()):
                raise RuntimeError(f'{case_id}: wide statistics counters differ from the planned values')


def workflow_checker():
    path = local_path('validation/strelka2/workflow.py')
    # Execute the checked source bytes, never an unrelated cached .pyc file.
    module = types.ModuleType('strelka2_release_workflow')
    module.__file__ = str(path)
    exec(compile(required(path), str(path), 'exec'), module.__dict__)
    return module


def check_workflow(case_id: str, spec: dict, record: dict, by_id: dict, build: dict, dataset: dict):
    checker = workflow_checker()
    workflow = record.get('workflow', {})
    stages = workflow.get('nativeStages')
    executions = record['executions']
    if (workflow.get('oracle') != 'unchanged upstream Python2 workflow'
            or not isinstance(stages, list) or not stages
            or any(not isinstance(stage, dict) or set(stage) != {'tool', 'args'}
                   or not isinstance(stage['tool'], str) or not stage['tool']
                   or PurePosixPath(stage['tool']).name != stage['tool']
                   or not isinstance(stage['args'], list)
                   or any(not isinstance(arg, str) or '\0' in arg for arg in stage['args']) for stage in stages)
            or set(workflow.get('targets', {})) != set(executions)):
        raise RuntimeError(f'{case_id}: incomplete upstream workflow stage evidence')
    commands = Counter(json.dumps(stage, sort_keys=True) for stage in stages)
    if spec['kind'] == 'repeat':
        baseline = by_id[f'strelka2-{spec["_dataset"]}-{spec["baseline"]}']['workflow']['nativeStages']
        if stages != baseline:
            raise RuntimeError(f'{case_id}: repeat workflow differs from the native baseline')
    models = {f'/work/__strelka2/{name}': build['assets'][name] for name in MODELS}
    for target, evidence in workflow['targets'].items():
        observed = evidence.get('stages')
        if (evidence.get('schema') != 1 or evidence.get('equal') is not True
                or evidence.get('nativeStages') != stages or not isinstance(observed, list)
                or Counter(json.dumps(stage, sort_keys=True) for stage in observed) != commands
                or evidence.get('nativeStageCount') != len(stages)
                or evidence.get('matchedStageCount') != len(stages)
                or evidence.get('missingCommands') != [] or evidence.get('extraCommands') != []
                or evidence.get('commandDigest') != checker.digest(sorted(commands.items()))):
            raise RuntimeError(f'{case_id}: workflow commands differ for {target}')
        raw = executions[target].get('workflow')
        if target != 'wasm':
            if (evidence.get('mode') != 'native-command-ledger'
                    or evidence.get('successfulStages') != len(stages)
                    or not isinstance(raw, dict) or not isinstance(raw.get('nativeCommands'), str)
                    or not raw['nativeCommands']):
                raise RuntimeError(f'{case_id}: missing successful native workflow ledger for {target}')
            continue
        initial = {f'/work/{name.split("/", 1)[1]}': sha for name, sha in dataset['files'].items()}
        initial.update({f'/work/{relative_name(name)}': digest(value.encode()) for name, value in spec.get('extraFiles', {}).items()})
        initial.update({f'/work/{relative_name(item["as"])}': item['sha256'] for item in executions[target]['inputsFrom']})
        initial.update(models)
        retained = {f'/work/{relative_name(name)}': sha for name, sha in executions[target]['files'].items()}
        try:
            actual_stages, actual_edges = checker.runtime_stages(raw, initial, retained)
        except (ValueError, KeyError, TypeError) as error:
            raise RuntimeError(f'{case_id}: invalid workflow dependency evidence: {error}') from error
        lock = read_json('tools/strelka2/sources.lock.json')
        if (evidence.get('mode') != 'runtime-command-ledger-and-file-edges'
                or actual_stages != observed or any(evidence.get(key) != value for key, value in actual_edges.items())
                or actual_edges['models'] != models or actual_edges['dependencyEdges'] <= 0
                or actual_edges['retainedOutputs'] <= 0
                or raw.get('upstream') != {'version': lock['version'], 'commit': lock['revision'], 'archiveSha256': lock['sha256']}):
            raise RuntimeError(f'{case_id}: workflow models, file edges or retained outputs differ')


def check_reports(tool: str, reports: list[Path], build: dict) -> dict:
    checked = {}
    validation = {name: digest(required(local_path(name))) for name in VALIDATION}
    manifest, manifest_sha = checked_dataset_manifest()
    datasets = {name: {'manifestSha256': manifest_sha,
                      'files': {path: info['sha256'] for path, info in manifest['files'].items() if path.startswith(name + '/')},
                      'observations': data['observations'], 'regionMappings': manifest['regionMappings']}
                for name, data in manifest['datasets'].items()}
    plan = planned_cases(tool)
    empty_hash = digest(b'')
    for path in reports:
        if not path.is_file():
            raise RuntimeError(f'Validation report is missing: {path}')
        raw = path.read_bytes()
        report = json.loads(raw)
        if tool not in report.get('builds', {}):
            continue
        report_tools = set(report['builds'])
        if not report_tools <= set(TOOLS):
            raise RuntimeError(f'{tool}: report contains unknown tools')
        runtime = {name: digest(required(local_path(name))) for other in sorted(report_tools) for name in RUNTIME[other]}
        engine = report.get('engine')
        if engine not in ENGINES or engine in checked:
            raise RuntimeError(f'{tool}: duplicate or unknown validation engine: {engine}')
        if report.get('schema') != 1 or report.get('status') != 'passed':
            raise RuntimeError(f'{tool}: report is not a passing schema-1 validation: {path}')
        if not isinstance(report.get('node'), str) or not report['node']:
            raise RuntimeError(f'{tool}: report omits the validation host version')
        if not isinstance(report.get('python'), str) or not re.fullmatch(r'Python 3\.\d+\.\d+', report['python']):
            raise RuntimeError(f'{tool}: report omits the native workflow/comparator Python version')
        if engine != 'node' and (not isinstance(report.get('browserVersion'), str) or not report['browserVersion']):
            raise RuntimeError(f'{tool}: report omits the actual browser version')
        if report['builds'][tool] != build:
            raise RuntimeError(f'{tool}: compiled/native assets or build inputs differ from those tested in {path}')
        if report.get('runtime') != runtime:
            raise RuntimeError(f'{tool}: runtime differs from that tested in {path}')
        if report.get('validation') != validation:
            raise RuntimeError(f'{tool}: validation implementation or case plan differs from {path}')
        if report.get('manifestSha256') != manifest_sha or report.get('datasets') != datasets:
            raise RuntimeError(f'{tool}: real matched datasets differ from those tested in {path}')
        all_ids = {case_id for other in report_tools for case_id in planned_cases(other)}
        expected = report.get('expectedCases', [])
        records = report.get('cases', [])
        actual = [record.get('id') for record in records]
        if (len(expected) != len(set(expected)) or set(expected) != all_ids
                or len(actual) != len(set(actual)) or set(actual) != all_ids
                or any(record.get('status') != 'passed' for record in records)):
            raise RuntimeError(f'{tool}: report does not contain the complete passing planned case set: {path}')
        by_id = {record['id']: record for record in records}
        for case_id, spec in plan.items():
            record = by_id[case_id]
            for key, value in {'tool': tool, 'dataset': spec['_dataset'], 'case': spec['id'],
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
                if spec.get('stdoutFile') and files.get(relative_name(spec['stdoutFile'])) != execution['stdout']:
                    raise RuntimeError(f'{case_id}: standard-output artifact is missing or differs for {target}')
                if failure and execution['stdout'] == execution['stderr'] == empty_hash:
                    raise RuntimeError(f'{case_id}: expected a failure diagnostic for {target}')
                if failure and spec.get('diagnosticRegex') and execution.get('diagnosticMatched') is not True:
                    raise RuntimeError(f'{case_id}: required failure diagnostic was not verified for {target}')
                dependencies = []
                for source in spec.get('inputsFrom', []):
                    source_id = f'{tool}-{spec["_dataset"]}-{source["case"]}'
                    checksum = by_id[source_id]['executions'][target]['files'][source['path']]
                    dependencies.append({**source, 'sha256': checksum})
                if execution.get('inputsFrom') != dependencies:
                    raise RuntimeError(f'{case_id}: derived input bytes differ from the preceding {target} outputs')
            if failure and len({execution['canonicalExitCode'] for execution in executions.values()}) != 1:
                raise RuntimeError(f'{case_id}: native and WebAssembly failure exits disagree')
            if spec.get('statisticsBoundary'):
                boundary = spec['statisticsBoundary']
                evidence = record.get('statisticsBoundary', {})
                if ({key: evidence.get(key) for key in ('case', 'path', 'values')} != boundary
                        or set(evidence.get('targets', {})) != set(executions)):
                    raise RuntimeError(f'{case_id}: missing declared statistics-boundary provenance')
                source_id = f'{tool}-{spec["_dataset"]}-{boundary["case"]}'
                for target, details in evidence['targets'].items():
                    source_hash = by_id[source_id]['executions'][target]['files'][boundary['path']]
                    inputs = details.get('inputs', {})
                    if (details.get('sourceSha256') != source_hash
                            or set(inputs) != {'stats-a.xml', 'stats-b.xml'}
                            or any(not isinstance(value, str) or not SHA256.fullmatch(value) for value in inputs.values())
                            or len(set(inputs.values())) != 2):
                        raise RuntimeError(f'{case_id}: statistics-boundary inputs are not bound to real {target} output')
                if any(details['inputs'] != evidence['targets']['native']['inputs']
                       for details in evidence['targets'].values()):
                    raise RuntimeError(f'{case_id}: statistics-boundary input bytes differ between implementations')
            comparisons = record.get('comparisons', {})
            if set(comparisons) != set(spec['comparisons']):
                raise RuntimeError(f'{case_id}: required semantic comparisons are missing')
            baseline_id = f'{tool}-{spec["_dataset"]}-{spec["baseline"]}' if spec['kind'] == 'repeat' else case_id
            native_contract = by_id[baseline_id]['comparisons'].get('native-contract')
            for target, comparison in comparisons.items():
                if (comparison.get('equal') is not True
                        or not all(isinstance(comparison.get(key), str) and SHA256.fullmatch(comparison[key])
                                   for key in ('expectedSemanticDigest', 'semanticDigest', 'biologicalDigest'))
                        or comparison['expectedSemanticDigest'] != comparison['semanticDigest']
                        or not comparison.get('invariants')):
                    raise RuntimeError(f'{case_id}: semantic comparison did not pass for {target}')
                if (not native_contract
                        or comparison['expectedSemanticDigest'] != native_contract['semanticDigest']
                        or comparison['biologicalDigest'] != native_contract['biologicalDigest']):
                    raise RuntimeError(f'{case_id}: comparison is not bound to its unchanged native baseline')
                check_invariants(case_id, spec, comparison)
            if spec.get('workflow') and not failure:
                check_workflow(case_id, spec, record, by_id, build, datasets[spec['_dataset']])
            if spec.get('effect') or spec.get('equivalentTo'):
                baseline = spec.get('effect') or spec['equivalentTo']
                before = by_id[f'{tool}-{spec["_dataset"]}-{baseline}']['comparisons']['native-contract']['biologicalDigest']
                after = comparisons['native-contract']['biologicalDigest']
                changed = bool(spec.get('effect'))
                expected_effect = {'baseline': baseline, 'before': before, 'after': after,
                                   'changed': changed, 'expected': 'changed' if changed else 'equivalent'}
                if record.get('optionEffect') != expected_effect or (before != after) != changed:
                    raise RuntimeError(f'{case_id}: required option-effect evidence is missing or inconsistent')
        checked[engine] = (raw, report)
    if set(checked) != ENGINES:
        raise RuntimeError(f'{tool}: missing passing reports for ' + ', '.join(sorted(ENGINES - checked.keys())))
    return checked


def readme(tool: str, lock: dict, config: dict) -> str:
    return f'''# Strelka2 {lock['version']} for WebAssembly

This package includes Strelka2 and its compiled helper programs, scoring models, JavaScript workflow, browser example, real matched tumour/normal reads, source archives, build recipes and licence notices.

Run `python3 -m http.server 8000` here and open `http://localhost:8000/examples/strelka2/`. Keep the complete `dist/strelka2/` directory together. The browser executes the tools locally; ordinary static hosting is sufficient.

See `runtime/STRELKA2.md` for the JavaScript API, `tools/strelka2/README.md` for supported options and limitations, and `datasets/strelka2/README.md` for provenance and original coordinate mappings. The compact human reference and BAM dictionaries use the documented transformations; the underlying sequencing reads are real.

## Rebuild and validate

Install the prerequisites described in the tool README and Emscripten {lock['emscripten']}. The recipes verify and reuse the included upstream sources. The pinned Python 2 interpreter is needed only for the unchanged native workflow used in comparisons.

```sh
python3 scripts/setup-strelka2-python.py
bash scripts/build-strelka2.sh all
python3 scripts/fetch-strelka2-test-data.py --verify
npm ci
node scripts/validate-strelka2.mjs --engine node
npx playwright install chromium firefox webkit
```

Repeat validation with `--engine chromium`, `--engine firefox` and `--engine webkit`. Check the browser example with `BROWSER=chromium node scripts/test-strelka2-demo.mjs`, changing the browser name for the other engines. The data README explains exact regeneration and required host tools.

All four included reports identify the exact binaries, native environments, workflow, models, fixtures and test plan. Native agreement checks the port on the supplied regions and options; it does not establish whole-genome capacity or make every biological call correct. `release.json` records that scope. Verify the package with `sha256sum -c SHA256SUMS`.

## Licences

Retain the original notices and corresponding sources in `tools/strelka2/`, `tools/strelka2-python/` and `dist/strelka2/`, along with the patches, build recipes and data provenance. `LICENSE` covers this project's infrastructure.
'''


def collect(tool: str, reports: list[Path]) -> tuple[dict[str, tuple[bytes, int]], dict]:
    if tool not in TOOLS:
        raise RuntimeError(f'Unknown caller: {tool}')
    lock, identity, build = check_build_identity(tool)
    checked = check_reports(tool, reports, build)
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
        for name in tree_hashes(directory, ignore_python_cache=directory == 'validation/strelka2'):
            add(f'{directory}/{name}', source=source)

    for name in ('LICENSE', *RUNTIME[tool], 'runtime/README.md', 'scripts/setup-emsdk.sh',
                 f'scripts/build-{tool}.sh', 'scripts/package-strelka2.py', *VALIDATION,
                 'scripts/test-strelka2-demo.mjs', 'scripts/fetch-strelka2-test-data.py',
                 'tests/test_strelka2_compare.py', 'tests/test_strelka2_package.py',
                 'tests/runtime.test.mjs', 'tests/fixtures/mock-tool.mjs'):
        add(name)
    if tool == 'strelka2':
        add('runtime/STRELKA2.md')
        add('tests/strelka2.test.mjs')
    for directory in (f'tools/{tool}', EXAMPLE, 'validation/strelka2', DATA):
        tree(directory)
    for name, checksum in identity['inputs'].items():
        add(name, expected=checksum)
    tree(f'dist/{tool}', source=False)
    archives = source_archives(tool, lock)
    python_lock = read_json('tools/strelka2-python/sources.lock.json')
    archives.setdefault(relative_name(python_lock['archive']), python_lock['sha256'])
    if archives[python_lock['archive']] != python_lock['sha256']:
        raise RuntimeError('Core and interpreter source archives disagree')
    for name, checksum in archives.items():
        path = f'dist/{tool}/sources/{name}'
        verify(local_path(path), checksum)
        sources[path] = checksum
    if set(tree_hashes(f'dist/{tool}/sources')) != archives.keys():
        raise RuntimeError(f'{tool}: source distribution contains missing or unpinned archives')
    check_embedded_archives(lock)
    if not lock.get('requiredNotices'):
        raise RuntimeError('Strelka2 source lock omits required licence notices')
    for notice in set(lock['requiredNotices']):
        source = required(local_path(f'tools/{tool}/{relative_name(notice)}'))
        copies = [local_path(f'dist/{tool}/{prefix}{notice}') for prefix in ('notices/', '')]
        present = [path for path in copies if path.is_file()]
        if not present:
            raise RuntimeError(f'{tool}: required distributed notice is missing: {notice}')
        if any(required(path) != source for path in present):
            raise RuntimeError(f'{tool}: distributed notice differs from the original: {notice}')
    if not manifest.get('sources'):
        raise RuntimeError('The somatic data manifest omits original source inputs')
    for name, record in manifest['sources'].items():
        cache = relative_name(record['cachePath'])
        if not cache.startswith('build/somatic-sources/'):
            raise RuntimeError(f'Dataset source lies outside its expected cache: {name}')
        data = verify(local_path(cache), record['sha256'])
        if record.get('bytes') != len(data):
            raise RuntimeError(f'Dataset source byte count differs: {name}')
        put(cache, data)
    for name, info in (manifest.get('regeneration', {}).get('files') or {}).items():
        add(name, expected=info['sha256'] if isinstance(info, dict) else info)
    config = read_json(f'{EXAMPLE}/config.json')
    config['tools'] = {tool: config['tools'][tool]}
    if set(config.get('datasets', {})) != {'hcc1395'}:
        raise RuntimeError('Standalone browser configuration omits the real matched dataset')
    if config['tools'][tool].get('requiresIsolation') or config['tools'][tool].get('waitForExit'):
        raise RuntimeError(f'{tool}: browser threading settings differ from the serial port')
    replacement = json_bytes(config)
    entries[f'{EXAMPLE}/config.json'] = (replacement, 0o644)
    sources[f'{EXAMPLE}/config.json'] = digest(replacement)
    runtime_doc = entries['runtime/README.md'][0].decode().split('## Interface', 1)
    if len(runtime_doc) != 2:
        raise RuntimeError('The runtime README is missing its interface documentation')
    replacement = ('# JavaScript runner\n\nSee the package README and `examples/strelka2/` for the included caller.\n\n## Interface' + runtime_doc[1]).encode()
    entries['runtime/README.md'] = (replacement, 0o644)
    sources['runtime/README.md'] = digest(replacement)

    playwright = read_json('package.json')['devDependencies']['playwright']
    if not re.fullmatch(r'\d+\.\d+\.\d+', playwright):
        raise RuntimeError('Playwright must be pinned to an exact version')
    node_tests = 'tests/runtime.test.mjs' + (' tests/strelka2.test.mjs')
    package = {'name': f'{tool}-wasm', 'version': lock['version'], 'private': True, 'type': 'module',
               'scripts': {'test': 'python3 -m unittest discover -s tests -p "test_strelka2_*.py" && node --test ' + node_tests,
                           'validate': 'node scripts/validate-strelka2.mjs'},
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
    add('build/strelka2-python/build-manifest.json', source=False)
    put('source-manifest.json', json_bytes({
        'schemaVersion': 1, 'tool': tool, 'upstreamVersion': lock['version'],
        'upstreamLockSha256': digest(required(local_path(f'tools/{tool}/sources.lock.json'))),
        'files': dict(sorted(sources.items())),
        'notes': 'Original source archives are stored once under dist; includes dependency notices, patches, recipes, runtime and matched-data provenance.',
    }), source=False)
    put('release.json', json_bytes({
        'schemaVersion': 1, 'tool': tool, 'upstreamVersion': lock['version'],
        'emscripten': lock['emscripten'], 'build': build, 'datasetManifestSha256': manifest_sha,
        'validationStatus': 'passed for the recorded test scope', 'testedEngines': sorted(checked),
        'reports': {engine: digest(raw) for engine, (raw, _) in checked.items()},
        'requiresCrossOriginIsolation': False,
    }), source=False)
    put('README.md', readme(tool, lock, config).encode(), source=False)
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
    parser.add_argument('--output', type=Path, default=ROOT / 'releases')
    parser.add_argument('--validation-report', action='append', type=Path, required=True)
    args = parser.parse_args()
    requested = TOOLS
    prepared = {tool: collect(tool, args.validation_report) for tool in requested}
    for tool in requested:
        write_package(tool, args.output.resolve(), args.validation_report, prepared=prepared[tool])


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, KeyError, OSError, TypeError, zipfile.BadZipFile, tarfile.TarError) as error:
        print(f'Packaging failed: {error}', file=sys.stderr)
        raise SystemExit(1)
