#!/usr/bin/env python3
"""Package independently usable Kraken2 and Bracken after complete validation."""
from __future__ import annotations
import argparse
import base64
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
TOOLS = ('kraken2', 'bracken')
BUNDLES = {'kraken2': ('kraken2',), 'bracken': ('kraken2', 'bracken')}
LABELS = {'kraken2': 'Kraken2', 'bracken': 'Bracken'}
ENGINES = {'node', 'chromium', 'firefox', 'webkit'}
BASE_RUNTIME = ('runtime/index.mjs', 'runtime/worker.mjs', 'runtime/execute.mjs')
RUNTIME = {'kraken2': (*BASE_RUNTIME, 'runtime/kraken2.mjs'),
           'bracken': (*BASE_RUNTIME, 'runtime/kraken2.mjs', 'runtime/bracken.mjs',
                       'runtime/bracken-worker.mjs', 'runtime/bracken-execute.mjs')}
PROGRAMS = {'kraken2': ('classify', 'build_db', 'estimate_capacity', 'dump_table', 'lookup_accession_numbers')}
VALIDATION = ('scripts/validate-metagenomics.mjs', 'validation/metagenomics/compare.py', 'validation/metagenomics/cases.json')
DATA = 'datasets/metagenomics'
EXAMPLE = 'examples/metagenomics'
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
    for dependency in lock.get('dependencies', {}).values():
        for record in dependency if isinstance(dependency, list) else [dependency]:
            if 'bundledPath' in record:
                raise RuntimeError(f'{tool}: unimplemented embedded dependency verification')
            add(record)
    for record in lock.get('sourceArchives', []):
        add(record)
    return archives


def native_tree(tool: str, kind: str) -> dict[str, str]:
    files = {}
    for path in sorted(local_path(f'build/{kind}').glob(tool + '*')):
        if path.is_dir():
            files.update({f'{path.name}/{name}': sha for name, sha in tree_hashes(path.relative_to(ROOT).as_posix()).items()})
        else:
            files[path.name] = digest(required(path))
    if not files or tool not in files:
        raise RuntimeError(f'{tool}: missing {kind} launcher and execution environment')
    if tool == 'bracken':
        files['kmer2read_distr'] = digest(required(local_path(f'build/{kind}/kmer2read_distr')))
    return files


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
    if tool == 'bracken':
        return lock, identity, check_bracken_identity(lock, identity, assets, digest(raw))
    if (lock.get('tool') != tool or lock.get('version') != '2.17.2'
            or lock.get('upstreamVersion') != '2.17.1' or tuple(lock.get('programs', [])) != PROGRAMS[tool]):
        raise RuntimeError('Kraken2 source tag, embedded version or helper inventory differs')
    for program in PROGRAMS[tool]:
        if (not required(local_path(f'dist/{tool}/{program}.wasm')).startswith(WASM_HEADER)
                or not required(local_path(f'dist/{tool}/{program}.mjs'))):
            raise RuntimeError(f'{tool}: missing genuine WebAssembly helper: {program}')
    environments = identity.get('nativeFiles', {})
    if set(environments) != {'native', 'patched-native'}:
        raise RuntimeError(f'{tool}: both native execution environments must be identified')
    for kind, files in environments.items():
        if files != native_tree(tool, kind):
            raise RuntimeError(f'{tool}: {kind} execution environment differs from its build manifest')
        if any(f'{tool}-package/{program}' not in files for program in PROGRAMS[tool]):
            raise RuntimeError(f'{tool}: native execution environment omits compiled helpers')
    return lock, identity, {'assets': assets, 'buildManifest': digest(raw), 'nativeFiles': environments}


def check_bracken_identity(lock: dict, identity: dict, assets: dict, manifest_sha: str) -> dict:
    distribution = read_json('dist/bracken/manifest.json')
    if (lock.get('tool') != 'bracken' or lock.get('version') != '3.1'
            or distribution.get('schema') != 1 or distribution.get('tool') != 'bracken'
            or distribution.get('version') != lock['version'] or distribution.get('upstreamVersionText') != '3.0.1'):
        raise RuntimeError('Bracken runtime/source tag or preserved upstream version differs')
    if distribution.get('pyodide') != {'path': 'pyodide/', 'version': lock['pyodide'], 'python': lock['python']}:
        raise RuntimeError('Bracken Python runtime identity differs from its source lock')
    if lock['pyodide'] != '314.0.7' or lock['python'] != '3.14.2':
        raise RuntimeError('Unexpected Bracken Python runtime version')
    for name in PYODIDE_FILES:
        required(local_path('dist/bracken/pyodide/' + name))
    for name in ('pyodide/pyodide.asm.wasm', 'kmer2read_distr.wasm'):
        if not required(local_path('dist/bracken/' + name)).startswith(WASM_HEADER):
            raise RuntimeError('Bracken distribution lacks genuine WebAssembly: ' + name)
    required(local_path('dist/bracken/kmer2read_distr.mjs'))
    catalogue = read_json('dist/bracken/pyodide/pyodide-lock.json')['info']
    if (catalogue.get('python') != lock['python'] or catalogue.get('arch') != 'wasm32'
            or catalogue.get('platform') != 'emscripten_' + lock['dependencies']['runtime-emscripten']['version'].replace('.', '_')):
        raise RuntimeError('Pyodide catalogue differs from the pinned browser interpreter')
    scripts = distribution.get('scripts', {})
    expected_scripts = {'est_abundance.py', 'generate_kmer_distribution.py'}
    if set(scripts) != expected_scripts or set(tree_hashes('dist/bracken/python')) != expected_scripts:
        raise RuntimeError('Bracken runtime omits or adds Python entrypoints')
    script_hashes = {}
    for name, record in scripts.items():
        if record.get('path') != 'python/' + name:
            raise RuntimeError('Bracken Python entrypoint path differs')
        verify(local_path('dist/bracken/' + record['path']), record['sha256'])
        script_hashes[name] = record['sha256']
    environments, native = identity.get('nativeEnvironments', {}), identity.get('nativeFiles', {})
    if set(environments) != {'native', 'patched-native'} or set(native) != set(environments):
        raise RuntimeError('Bracken must identify both complete native environments')
    for kind in environments:
        if native[kind] != native_tree('bracken', kind):
            raise RuntimeError(f'Bracken {kind} execution files differ from the recorded build')
        python = environments[kind].get('python', {})
        if (set(environments[kind]) != {'python', 'scripts'} or environments[kind]['scripts'] != script_hashes
                or set(python) != {'version', 'implementation', 'executableSha256'}
                or python['version'] != lock['python'] or python['implementation'] != 'CPython'
                or not isinstance(python['executableSha256'], str) or not SHA256.fullmatch(python['executableSha256'])):
            raise RuntimeError(f'Bracken {kind} interpreter or Python scripts differ')
        for name, sha in script_hashes.items():
            if native[kind].get('bracken-package/src/' + name) != sha:
                raise RuntimeError(f'Bracken {kind} and browser Python source bytes differ')
    return {'assets': assets, 'buildManifest': manifest_sha, 'nativeFiles': native, 'nativeEnvironments': environments}


def check_archive_tree(data: bytes, prefix: str, directory: str, *, selected=None) -> None:
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
            raise RuntimeError(f'{directory}: required runtime files are absent from the pinned archive')
        expected = {name: expected[name] for name in selected}
    if not expected or expected != tree_hashes(directory):
        raise RuntimeError(f'{directory}: runtime files differ from the pinned original archive')


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
    if tool == 'bracken':
        for record in read_json('tools/bracken/runtime-sources.json'):
            if archives.get(record['archive']) != record['sha256']:
                raise RuntimeError('Bracken omits an audited Python runtime source')
            for name in record['licensePaths']:
                if not relative_name(name).startswith('tools/bracken/runtime-notices/'):
                    raise RuntimeError('Bracken runtime notice escapes its source directory')
                required(local_path(name))
        for record in read_json('tools/bracken/runtime-notices/manifest.json'):
            if not relative_name(record['destination']).startswith('tools/bracken/runtime-notices/'):
                raise RuntimeError('Bracken extracted notice escapes its source directory')
            verify(local_path(record['destination']), record['sha256'])
        for dependency, prefix, directory, selected in (
                ('pyodide-core', 'pyodide/', 'dist/bracken/pyodide', PYODIDE_FILES),
                ('runtime-ws', 'package/', 'dist/bracken/node_modules/ws', None)):
            record = lock['dependencies'][dependency]
            check_archive_tree(required(local_path('dist/bracken/sources/' + record['archive'])), prefix, directory, selected=selected)
        with tarfile.open(local_path('dist/bracken/sources/' + lock['archive'])) as source:
            for name in ('est_abundance.py', 'generate_kmer_distribution.py'):
                matches = [member for member in source.getmembers() if member.name.split('/', 1)[-1] == 'src/' + name]
                if len(matches) != 1 or not matches[0].isfile() or source.extractfile(matches[0]).read() != required(local_path('dist/bracken/python/' + name)):
                    raise RuntimeError('Bracken Python source differs from the unchanged upstream archive: ' + name)
    return archives


def checked_dataset_manifest() -> tuple[dict, str]:
    raw = required(local_path(f'{DATA}/manifest.json'))
    manifest = json.loads(raw)
    if manifest.get('schema_version') != 1 or set(manifest.get('datasets', {})) != {'zymo'}:
        raise RuntimeError('The metagenomics manifest must identify the real Zymo fixture')
    if not manifest.get('files'):
        raise RuntimeError('The metagenomics manifest contains no verified files')
    for name, info in manifest['files'].items():
        data = verify(local_path(f'{DATA}/{relative_name(name)}'), info['sha256'])
        if type(info.get('bytes')) is not int or info['bytes'] != len(data):
            raise RuntimeError(f'Dataset byte count differs from its manifest: {name}')
    actual = {f'zymo/{name}': sha for name, sha in tree_hashes(f'{DATA}/zymo').items()}
    recorded = {name: info['sha256'] for name, info in manifest['files'].items() if name.startswith('zymo/')}
    if actual != recorded:
        raise RuntimeError('The manifest must identify every bundled Zymo fixture file')
    plan = read_json('validation/metagenomics/cases.json')
    for name in plan.get('fixtureInputs', []):
        if f'zymo/{relative_name(name)}' not in recorded:
            raise RuntimeError(f'Validation input is absent from the dataset manifest: {name}')
    required(local_path(f'{DATA}/README.md'))
    return manifest, digest(raw)


def checked_example_config(manifest: dict) -> dict:
    config = read_json(f'{EXAMPLE}/config.json')
    if set(config.get('datasets', {})) != {'zymo'}:
        raise RuntimeError('Standalone browser configuration omits the real Zymo dataset')
    dataset = config['datasets']['zymo']
    if dataset.get('path') != '../../datasets/metagenomics/zymo/':
        raise RuntimeError('Browser example must use its bundled local Zymo fixture')
    files = dataset.get('files')
    if not isinstance(files, list) or not files or len(files) != len(set(files)):
        raise RuntimeError('Browser example fixture files must be nonempty and unique')
    for name in files:
        if f'zymo/{relative_name(name)}' not in manifest['files']:
            raise RuntimeError(f'Browser example input is absent from the dataset manifest: {name}')
    return config


def planned_cases(tool: str) -> dict:
    plan = read_json('validation/metagenomics/cases.json')
    if plan.get('schema') != 1 or plan.get('datasets') != ['zymo']:
        raise RuntimeError('Unknown metagenomics case-plan schema or datasets')
    result = {}
    for spec in plan['tools'][tool]['cases']:
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
        raise RuntimeError(f'{tool}: empty case plan')
    return result


def check_invariants(case_id: str, spec: dict, comparison: dict) -> None:
    for side in ('invariants', 'nativeInvariants'):
        checks, artifacts = comparison.get(side), spec.get('artifacts', [])
        if not artifacts or not isinstance(checks, dict) or set(checks) != {item['path'] for item in artifacts}:
            raise RuntimeError(f'{case_id}: incomplete {side} artifact evidence')
        for artifact in artifacts:
            counts = checks[artifact['path']]
            if artifact['kind'] == 'kraken-options':
                if counts != {'fields': 9, 'bytes': 64}:
                    raise RuntimeError(f'{case_id}: incomplete Kraken database ABI field checks')
                continue
            if artifact['kind'] not in ('exact', 'classifications', 'kraken-report', 'bracken-abundance',
                                         'bracken-distribution', 'bracken-read-distribution', 'fastq'):
                raise RuntimeError(f'{case_id}: unknown artifact invariant kind')
            field = 'bytes' if artifact['kind'] == 'exact' else 'records'
            count = counts.get(field)
            minimum = artifact.get('minimumRecords', 0) if field == 'records' else 0
            if artifact.get('requireNonempty', True):
                minimum = max(1, minimum)
            if type(count) is not int or count < minimum:
                raise RuntimeError(f'{case_id}: missing nonempty {artifact["kind"]} evidence')
            if artifact['kind'] == 'classifications':
                if (any(type(counts.get(name)) is not int or counts[name] < 0 for name in ('classified', 'unclassified'))
                        or counts['classified'] + counts['unclassified'] != count
                        or counts['classified'] < artifact.get('minimumClassified', 0)
                        or counts['unclassified'] < artifact.get('minimumUnclassified', 0)):
                    raise RuntimeError(f'{case_id}: classification counts are inconsistent or vacuous')
            if artifact['kind'] == 'bracken-abundance':
                if (any(type(counts.get(name)) is not int or counts[name] < 0 for name in ('estimatedReads', 'addedReads'))
                        or counts['addedReads'] > counts['estimatedReads']
                        or counts['estimatedReads'] < (1 if artifact.get('requireNonempty', True) else 0)
                        or counts['addedReads'] < artifact.get('minimumAddedReads', 0)):
                    raise RuntimeError(f'{case_id}: abundance counts are inconsistent or vacuous')


def check_native_anomalies(case_id: str, spec: dict, comparison: dict, target: str, native: dict, actual: dict) -> None:
    anomalies = comparison.get('nativeAnomalies')
    if not isinstance(anomalies, dict) or set(anomalies) != {'expected', 'actual'}:
        raise RuntimeError(f'{case_id}: missing explicit native anomaly evidence')
    allowed = {item['path'] for item in spec['artifacts'] if item.get('nativeEofArtifact') and item['kind'] == 'bracken-read-distribution'}
    for side, execution in (('expected', native), ('actual', actual)):
        records = anomalies[side]
        if not isinstance(records, list) or len({item['path'] for item in records}) != len(records):
            raise RuntimeError(f'{case_id}: duplicate native EOF anomaly')
        for anomaly in records:
            if (anomaly.get('path') not in allowed or (side == 'actual' and target != 'native-contract')
                    or set(anomaly) != {'path', 'offset', 'length', 'rawSha256', 'fragmentSha256', 'base64', 'reason'}):
                raise RuntimeError(f'{case_id}: unplanned native EOF exception')
            try:
                fragment = base64.b64decode(anomaly['base64'], validate=True)
            except (ValueError, TypeError) as error:
                raise RuntimeError(f'{case_id}: invalid native EOF bytes') from error
            if (not re.fullmatch(rb'\x00\t-?[0-9]+\t\t\n', fragment)
                    or not -(2 ** 31) <= int(fragment.split(b'\t')[1]) < 2 ** 31
                    or type(anomaly['offset']) is not int or anomaly['offset'] <= 0
                    or type(anomaly['length']) is not int or anomaly['length'] != len(fragment)
                    or anomaly['rawSha256'] != execution['files'].get(anomaly['path'])
                    or anomaly['offset'] + anomaly['length'] != execution['fileBytes'].get(anomaly['path'])
                    or anomaly['fragmentSha256'] != digest(fragment)
                    or anomaly['reason'] != 'Bracken 3.1 unchanged native kraken_processing.cpp reads beyond the terminal newline; one non-biological EOF phantom excluded'):
                raise RuntimeError(f'{case_id}: native EOF evidence is not bound to the measured terminal fragment')


def check_workflow(case_id: str, spec: dict, execution: dict, manifest: dict, dataset: str) -> None:
    """Replay recorded workflow hash edges from independently identified inputs."""
    plan = read_json('validation/metagenomics/cases.json')
    current = {'/work/' + name: manifest['files'][dataset + '/' + name]['sha256'] for name in plan['fixtureInputs']}
    for source in execution['inputsFrom']:
        current['/work/' + relative_name(source['as'])] = source['sha256']
    for name, value in spec.get('extraFiles', {}).items():
        current['/work/' + relative_name(name)] = digest(value.encode())
    for name in spec.get('omitInputs', []):
        current.pop('/work/' + relative_name(name), None)
    stages = execution.get('workflow', {}).get('stages')
    if not isinstance(stages, list) or [stage.get('tool') for stage in stages] != spec['workflowStages']:
        raise RuntimeError(f'{case_id}: required workflow stages are missing or reordered')
    edges = 0
    for stage in stages:
        if stage.get('exitCode') != 0 or stage.get('missingOutputs') != [] or stage.get('inputs') != current:
            raise RuntimeError(f'{case_id}: workflow stage did not consume the preceding bytes')
        outputs = stage.get('outputHashes')
        if not isinstance(outputs, dict) or not outputs:
            raise RuntimeError(f'{case_id}: workflow stage omits output hashes')
        edges += len(current)
        for path, sha in outputs.items():
            if not path.startswith('/work/') or not isinstance(sha, str) or not SHA256.fullmatch(sha):
                raise RuntimeError(f'{case_id}: invalid workflow output identity')
            relative_name(path[len('/work/'):])
        current.update(outputs)
    retained = execution['files']
    if any(current.get('/work/' + name) != sha for name, sha in retained.items()):
        raise RuntimeError(f'{case_id}: returned workflow bytes differ from their producing stage')
    expected = {'stageCount': len(stages), 'tools': spec['workflowStages'], 'dependencyEdges': edges,
                'retainedOutputs': len(retained), 'retainedOutputHashes': retained}
    if execution.get('workflowEvidence') != expected:
        raise RuntimeError(f'{case_id}: workflow evidence differs from the complete stage ledger')


def check_reports(tool: str, reports: list[Path], builds: dict) -> dict:
    checked, python_identity = {}, None
    runtime = {name: digest(required(local_path(name))) for name in RUNTIME[tool]}
    validation = {name: digest(required(local_path(name))) for name in VALIDATION}
    manifest, manifest_sha = checked_dataset_manifest()
    datasets = {name: {'manifestSha256': manifest_sha,
                      'files': {path: info['sha256'] for path, info in manifest['files'].items() if path.startswith(name + '/')},
                      'observations': metadata['observations']}
                for name, metadata in manifest['datasets'].items()}
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
        if report.get('schema') != 1 or report.get('status') != 'passed':
            raise RuntimeError(f'{tool}: report is not a passing schema-1 validation: {path}')
        if not isinstance(report.get('node'), str) or not report['node']:
            raise RuntimeError(f'{tool}: report omits the Node host version')
        if not isinstance(report.get('python'), str) or not re.fullmatch(r'Python 3\.\d+\.\d+', report['python']):
            raise RuntimeError(f'{tool}: report omits the Python host version')
        if engine != 'node' and (not isinstance(report.get('browserVersion'), str) or not report['browserVersion']):
            raise RuntimeError(f'{tool}: report omits the actual browser version')
        if report.get('builds') != {name: builds[name] for name in BUNDLES[tool]}:
            raise RuntimeError(f'{tool}: tool or dependency build differs from {path}')
        if tool == 'bracken':
            observed = report.get('nativePython', {})
            if (set(observed) != {'version', 'implementation', 'sha256'}
                    or observed['version'] != '3.14.2' or observed['implementation'] != 'CPython'
                    or not isinstance(observed['sha256'], str) or not SHA256.fullmatch(observed['sha256'])):
                raise RuntimeError('Bracken report omits its exact observed native interpreter identity')
            if python_identity is not None and observed != python_identity:
                raise RuntimeError('Bracken browser reports used different native Python binaries')
            if any(environment['python']['executableSha256'] != observed['sha256'] for environment in builds['bracken']['nativeEnvironments'].values()):
                raise RuntimeError('Bracken native interpreter differs from the source build identity')
            python_identity = observed
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
                if target == 'wasm' and spec.get('workflowStages'):
                    check_workflow(case_id, spec, execution, manifest, spec['_dataset'])
            if failure and len({execution['canonicalExitCode'] for execution in executions.values()}) != 1:
                raise RuntimeError(f'{case_id}: native and WebAssembly failure exits disagree')
            comparisons = record.get('comparisons', {})
            if set(comparisons) != set(spec['comparisons']):
                raise RuntimeError(f'{case_id}: required semantic comparisons are missing')
            baseline_id = f'{tool}-{spec["_dataset"]}-{spec["baseline"]}' if spec['kind'] == 'repeat' else case_id
            native_contract = by_id[baseline_id]['comparisons'].get('native-contract')
            for target, comparison in comparisons.items():
                if (comparison.get('equal') is not True
                        or any(not isinstance(comparison.get(key), str) or not SHA256.fullmatch(comparison[key])
                               for key in ('expectedSemanticDigest', 'semanticDigest', 'biologicalDigest'))
                        or comparison['expectedSemanticDigest'] != comparison['semanticDigest']):
                    raise RuntimeError(f'{case_id}: semantic comparison did not pass')
                if (not native_contract or comparison['expectedSemanticDigest'] != native_contract['semanticDigest']
                        or comparison['biologicalDigest'] != native_contract['biologicalDigest']):
                    raise RuntimeError(f'{case_id}: comparison is not bound to its unchanged native baseline')
                check_invariants(case_id, spec, comparison)
                check_native_anomalies(case_id, spec, comparison, target, by_id[baseline_id]['executions']['native'],
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
    label = 'Kraken2 and Bracken' if tool == 'bracken' else 'Kraken2'
    specific = ('Run Kraken2 classification before Bracken abundance estimation. A distribution must match the database and read length. The build preset runs the included Kraken2 dependency, compiled Bracken helper and unchanged Bracken Python script.'
                if tool == 'bracken' else 'The build preset uses local reference sequences, taxonomy and the sequence-to-taxonomy map. Database downloads and masking are not part of this browser build.')
    links = ', '.join(f'[{LABELS[name]}](../../tools/{name}/README.md)' for name in BUNDLES[tool])
    return f'''# {label} in the browser

Run `python3 -m http.server 8000` from the package root and open `http://localhost:8000/examples/metagenomics/`. Load the real Zymo reads or upload your own files. Computation runs locally in browser workers. Ordinary static hosting is sufficient; cross-origin isolation is not required.

Arguments are a JSON array. Successful output files remain loaded for later commands and can be downloaded. Add output paths to **Files to keep**, or a directory to **Directories to keep**. **Cancel** stops the current command. Page reload clears the files.

{specific}

See the tool documentation ({links}), [data provenance](../../datasets/metagenomics/README.md), and [validation rules](../../validation/metagenomics/README.md). The compact reference database covers only part of the community. Native/browser agreement checks this port; it does not establish whole-database capacity or biological accuracy.
'''


def readme(tool: str, lock: dict) -> str:
    commands = '\n'.join(f'bash scripts/build-{name}.sh all' for name in BUNDLES[tool])
    validation = '\n'.join(f'node scripts/validate-metagenomics.mjs --tool {name} --engine node --dataset all' for name in BUNDLES[tool])
    runner = 'BrackenRunner' if tool == 'bracken' else 'Kraken2Runner'
    args = (['bracken', '-d', 'db', '-i', 'sample.report', '-o', 'sample.bracken', '-w', 'redistributed.report', '-r', '151']
            if tool == 'bracken' else ['--db', 'db', '--paired', '--report', 'sample.report', '--output', 'sample.kraken', 'reads_1.fastq', 'reads_2.fastq'])
    outputs = ['sample.bracken', 'redistributed.report'] if tool == 'bracken' else ['sample.kraken', 'sample.report']
    specific = ('Bracken includes the exact Kraken2 dependency used to build and validate its distributions. Its original Python programs run in the bundled Pyodide runtime. The upstream v3.1 shell prints version 3.0.1; that text is preserved. Kraken2 release 2.17.2 similarly embeds version 2.17.1.'
                if tool == 'bracken' else 'This uses the Kraken2 2.17.2 release source, which embeds version 2.17.1. The original version text is preserved.')
    return f'''# {LABELS[tool]} {lock['version']} for WebAssembly

This standalone package contains compiled programs, a JavaScript API, browser example, real paired reads and reference data, validation reports, original source archives, build recipes and licence notices. {specific}

## Run the browser example

Run `python3 -m http.server 8000` here and open `http://localhost:8000/examples/metagenomics/`. Load the example data or upload your files, edit arguments and run commands. Results can be viewed and downloaded. Computation happens in browser workers; the server only serves files. No cross-origin isolation headers, remote computation or runtime downloads are needed.

Keep each `dist/` tool directory together. See `tools/{tool}/README.md`, `runtime/{tool.upper()}.md` and `datasets/metagenomics/README.md` for supported inputs, limitations and provenance. The compact database is deliberately incomplete and is suitable for testing, not comprehensive community analysis.

## JavaScript API

```js
import {{ {runner} }} from './runtime/{tool}.mjs';
const runner = new {runner}({{
  baseUrl: new URL('./dist/{tool}/', import.meta.url),
}});
const result = await runner.run({json.dumps(args)}, {{
  files: inputs, // [{{path: 'db/hash.k2d', data: Uint8Array}}, ...]
  outputs: {json.dumps(outputs)},
  timeoutMs: 600000,
}});
```

Check `result.exitCode` and `result.missingOutputs`. Generated files are returned in `result.files`; standard output and error are byte arrays. An abort signal cancels the command. Node needs no npm packages to run this API; Bracken includes its required Node `ws` package.

## Rebuild and validate

Install the host prerequisites documented in each included `tools/` directory, including the pinned Emscripten version and, for Bracken, CPython 3.14.2. Recipes verify and reuse the included original source archives. Host compiler installation may need network access.

```sh
{commands}
python3 scripts/fetch-metagenomics-test-data.py --verify
npm ci
{validation}
npx playwright install chromium firefox webkit
npm test
```

Repeat validation with `--engine chromium`, `--engine firefox` and `--engine webkit`. Run `METAGENOMICS_TOOL={tool} BROWSER=chromium node scripts/test-metagenomics-demo.mjs`, changing the browser for the other engines. The data README documents regeneration from the included original inputs and any installed programs it requires.

Reports bind these exact compiled assets, source recipes, native programs, JavaScript runtime, real data and complete configured case plans. Bracken's release includes passing evidence for its Kraken2 dependency as well. The evidence covers the supplied fixture and options; it does not establish full reference-database capacity. `release.json` records the tested engines. Verify all files with `sha256sum -c SHA256SUMS`.

## Licences

`LICENSE` covers this project's infrastructure. Retain upstream notices, source archives, patches, recipes and data provenance when redistributing. Tool and runtime notices are under `tools/` and `dist/*/notices/`.
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
                 'scripts/package-metagenomics.py', *VALIDATION, 'scripts/test-metagenomics-demo.mjs',
                 'scripts/fetch-metagenomics-test-data.py', 'tests/test_metagenomics_compare.py',
                 'tests/test_metagenomics_package.py', 'tests/runtime.test.mjs', 'tests/fixtures/mock-tool.mjs'):
        add(name)
    for name, (lock, identity, _) in identities.items():
        add(f'runtime/{name.upper()}.md')
        add(f'tests/{name}.test.mjs')
        tree(f'tools/{name}', source_tree=True)
        for path, checksum in identity['inputs'].items():
            add(path, expected=checksum)
        tree(f'dist/{name}', source=False)
        for archive, checksum in check_sources(name, lock).items():
            sources[f'dist/{name}/sources/{archive}'] = checksum
    for directory in (EXAMPLE, 'validation/metagenomics', DATA):
        tree(directory, source_tree=True)
    if not manifest.get('sources'):
        raise RuntimeError('The metagenomics manifest omits original source inputs')
    for name, record in manifest['sources'].items():
        cache = relative_name(record['cachePath'])
        if not cache.startswith('build/metagenomics-sources/'):
            raise RuntimeError(f'Dataset source lies outside its expected cache: {name}')
        data = verify(local_path(cache), record['sha256'])
        if type(record.get('bytes')) is not int or record['bytes'] != len(data):
            raise RuntimeError(f'Dataset source byte count differs: {name}')
        put(cache, data)
    for name, info in (manifest.get('regeneration', {}).get('files') or {}).items():
        add(name, expected=info['sha256'] if isinstance(info, dict) else info)
    config = checked_example_config(manifest)
    config['tools'] = {name: config['tools'][name] for name in BUNDLES[tool]}
    for name, settings in config['tools'].items():
        if settings.get('requiresIsolation') or settings.get('waitForExit'):
            raise RuntimeError(f'{name}: browser threading settings differ from the serial port')
    for name, replacement in ((f'{EXAMPLE}/config.json', json_bytes(config)), (f'{EXAMPLE}/README.md', example_readme(tool).encode())):
        entries[name], sources[name] = (replacement, 0o644), digest(replacement)
    runtime_doc = entries['runtime/README.md'][0].decode().split('## Interface', 1)
    if len(runtime_doc) != 2:
        raise RuntimeError('The runtime README is missing its interface documentation')
    replacement = ('# JavaScript runner\n\nSee the package README and `examples/metagenomics/` for the included tools.\n\n## Interface' + runtime_doc[1]).encode()
    entries['runtime/README.md'], sources['runtime/README.md'] = (replacement, 0o644), digest(replacement)
    playwright = read_json('package.json')['devDependencies']['playwright']
    if not re.fullmatch(r'\d+\.\d+\.\d+', playwright):
        raise RuntimeError('Playwright must be pinned to an exact version')
    node_tests = 'tests/runtime.test.mjs ' + ' '.join(f'tests/{name}.test.mjs' for name in BUNDLES[tool])
    lock = identities[tool][0]
    package = {'name': f'{tool}-wasm', 'version': lock['version'], 'private': True, 'type': 'module',
               'scripts': {'test': 'python3 -m unittest discover -s tests -p "test_metagenomics_*.py" && node --test ' + node_tests,
                           'validate': f'node scripts/validate-metagenomics.mjs --tool {tool}'},
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
        'notes': 'Original archives are stored once under each distribution; includes dependency notices, patches, recipes, runtime and real-data provenance.',
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
