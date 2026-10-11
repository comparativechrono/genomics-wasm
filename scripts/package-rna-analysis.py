#!/usr/bin/env python3
"""Package validated, independently usable RNA analysis WebAssembly tools."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ('featurecounts', 'stringtie', 'mosdepth')
LABELS = {'featurecounts': 'featureCounts', 'stringtie': 'StringTie', 'mosdepth': 'mosdepth'}
ENGINES = {'node', 'chromium', 'firefox', 'webkit'}
RUNTIME = ('runtime/index.mjs', 'runtime/worker.mjs', 'runtime/execute.mjs')
VALIDATION = ('scripts/validate-rna-analysis.mjs', 'validation/rna-analysis/compare.py',
              'validation/rna-analysis/cases.json')
DATA = 'datasets/rna-analysis'
EXAMPLE = 'examples/rna-analysis'
SOURCE_SUFFIXES = {'.patch', '.h', '.hpp', '.c', '.cc', '.cpp', '.rs', '.py', '.sh',
                   '.js', '.mjs', '.json', '.lock', '.nim', '.nims', '.nimble', '.cfg', '.inc'}
ASSET_SUFFIXES = {'.mjs', '.js', '.wasm'}
SHA256 = re.compile(r'[0-9a-f]{64}')
MOSDEPTH_ERROR_DIAGNOSTICS = {
    'missing-input': '[mosdepth] could not open alignment file',
    'missing-regions': '[mosdepth] could not open regions file: missing.bed',
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()


def relative_name(name: str) -> str:
    """Reject ambiguous archive and manifest paths before opening anything."""
    if not isinstance(name, str) or not name or '\\' in name or '\x00' in name:
        raise RuntimeError(f'Invalid package path: {name!r}')
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ('', '.', '..', '.git', '__pycache__') for part in name.split('/')):
        raise RuntimeError(f'Unsafe package path: {name!r}')
    return name


def local_path(name: str) -> Path:
    path = ROOT / relative_name(name)
    if not path.resolve().is_relative_to(ROOT.resolve()):
        raise RuntimeError(f'Package path escapes root: {name}')
    return path


def required(path: Path) -> bytes:
    if not path.resolve().is_relative_to(ROOT.resolve()):
        raise RuntimeError(f'Package input escapes root: {path}')
    if not path.is_file():
        raise RuntimeError(f'Required package input is missing: {path}')
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


def source_archives(tool: str, lock: dict) -> dict[str, str]:
    """List complete original archives; bundled trees live in the main archive."""
    archives = {}

    def add(record, default):
        name = record.get('archive', default)
        directory = record.get('directory', 'build/downloads')
        path = relative_name(f'{directory}/{relative_name(name)}')
        checksum = record['sha256']
        if not isinstance(checksum, str) or not SHA256.fullmatch(checksum):
            raise RuntimeError(f'Invalid source archive SHA256: {path}')
        if path in archives and archives[path] != checksum:
            raise RuntimeError(f'Conflicting source archive checksums: {path}')
        archives[path] = checksum

    add(lock, f'{tool}-{lock["revision"]}.tar.gz')
    for name, dependency in lock.get('dependencies', {}).items():
        for record in dependency if isinstance(dependency, list) else [dependency]:
            if 'bundledPath' in record:
                relative_name(record['bundledPath'])
                if not SHA256.fullmatch(record.get('sourceTreeSha256', '')):
                    raise RuntimeError(f'{tool}: bundled dependency {name} lacks its source tree hash')
                continue
            version = record.get('revision') or record.get('version')
            add(record, f'{name}-{version}.tar.gz')
    for record in lock.get('sourceArchives', []):
        add(record, record['archive'])
    return archives


def check_build_identity(tool: str, assets: dict[str, str]) -> dict:
    manifest = read_json(f'dist/{tool}/build-manifest.json')
    if manifest.get('schema') != 1 or manifest.get('tool') != tool or manifest.get('assets') != assets:
        raise RuntimeError(f'{tool}: build manifest does not identify the compiled distribution')
    lock = read_json(f'tools/{tool}/sources.lock.json')
    expected = {f'scripts/build-{tool}.sh', f'tools/{tool}/sources.lock.json', *lock.get('buildScripts', [])}
    expected.update(path.relative_to(ROOT).as_posix() for path in local_path(f'tools/{tool}').rglob('*')
                    if path.is_file() and (path.suffix in SOURCE_SUFFIXES or path.name in ('Makefile', 'CMakeLists.txt')))
    recorded = manifest.get('inputs', {})
    if not isinstance(recorded, dict) or not expected <= recorded.keys():
        raise RuntimeError(f'{tool}: build manifest omits source inputs: {sorted(expected - recorded.keys())}')
    for name, checksum in recorded.items():
        verify(local_path(name), checksum)
        prefix = f'tools/{tool}/'
        if name.startswith(prefix):
            copied = local_path(f'dist/{tool}/{name[len(prefix):]}')
            if copied.is_file():
                verify(copied, checksum)
    return manifest


def checked_dataset_manifest() -> tuple[dict, str]:
    raw = required(local_path(f'{DATA}/manifest.json'))
    manifest = json.loads(raw)
    if manifest.get('schema_version') != 1 or not {'human', 'yeast'} <= manifest.get('datasets', {}).keys():
        raise RuntimeError('The RNA analysis manifest must identify both real datasets')
    if not manifest.get('files'):
        raise RuntimeError('The RNA analysis manifest contains no checked files')
    for name, info in manifest['files'].items():
        data = verify(local_path(f'{DATA}/{relative_name(name)}'), info['sha256'])
        if info.get('bytes') != len(data):
            raise RuntimeError(f'Dataset byte count differs from its manifest: {name}')
    return manifest, digest(raw)


def planned_cases(tool: str) -> dict:
    """Expand the checked-in case plan; reports cannot choose their own scope."""
    plan = read_json('validation/rna-analysis/cases.json')
    if plan.get('schema') != 1 or set(plan.get('datasets', [])) != {'human', 'yeast'}:
        raise RuntimeError('Unknown RNA analysis case-plan schema')
    # The validator and packager share this data file, not a report-provided list.
    result = {}
    for spec in plan['tools'][tool]['cases']:
        if spec.get('kind') not in ('success', 'repeat', 'failure', 'restriction'):
            raise RuntimeError(f'{tool}: unsupported validation case kind')
        if any(key in spec for key in ('nativeErrorPolicy', 'portExitCode', 'portDiagnostic')):
            policy = spec.get('nativeErrorPolicy', {})
            if (tool != 'mosdepth' or spec.get('id') not in MOSDEPTH_ERROR_DIAGNOSTICS
                    or spec['kind'] != 'failure' or spec.get('oncePerTool') is not True
                    or set(policy) != {'allowedSignals', 'allowNonzeroExit', 'reason'}
                    or policy.get('allowedSignals') != ['SIGSEGV'] or policy.get('allowNonzeroExit') is not True
                    or not isinstance(policy.get('reason'), str) or not policy['reason'].strip()
                    or type(spec.get('portExitCode')) is not int or spec['portExitCode'] != 2
                    or spec.get('portDiagnostic') != MOSDEPTH_ERROR_DIAGNOSTICS[spec['id']]
                    or set(spec['executions']) != {'native', 'patched-native', 'wasm'} or spec['comparisons'] != []):
                raise RuntimeError(f'{tool}: unsupported native error-policy exception')
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


def check_reports(tool: str, reports: list[Path], assets: dict[str, str]) -> dict:
    checked = {}
    runtime_hashes = {name: digest(required(local_path(name))) for name in RUNTIME}
    validation_hashes = {name: digest(required(local_path(name))) for name in VALIDATION}
    manifest, manifest_sha = checked_dataset_manifest()
    datasets = {}
    for dataset in ('human', 'yeast'):
        sam = required(local_path(f'{DATA}/{dataset}/input.sam')).decode()
        records = [line.split('\t') for line in sam.splitlines() if line and not line.startswith('@')]
        if not records or any(len(row) < 11 for row in records):
            raise RuntimeError(f'{dataset}: incomplete biological SAM fixture')
        datasets[dataset] = {
            'manifestSha256': manifest_sha,
            'files': {name: info['sha256'] for name, info in manifest['files'].items() if name.startswith(dataset + '/')},
            'observations': manifest['datasets'][dataset]['observations'],
            'samRecords': len(records), 'mappedRecords': sum(not (int(row[1]) & 4) for row in records),
        }
    build = {'assets': assets, 'module': assets[f'{tool}.mjs'], 'wasm': assets[f'{tool}.wasm'],
             **{kind: digest(required(local_path(f'build/{kind}/{tool}'))) for kind in ('native', 'patched-native')}}
    required_cases = planned_cases(tool)
    empty_hash = digest(b'')
    for path in reports:
        # Report files may be supplied from an artifact download outside ROOT;
        # their package destination is a fixed engine name, never their path.
        if not path.is_file():
            raise RuntimeError(f'Validation report is missing: {path}')
        raw = path.read_bytes()
        report = json.loads(raw)
        if tool not in report.get('builds', {}):
            continue
        engine = report.get('engine')
        if engine not in ENGINES or engine in checked:
            raise RuntimeError(f'{tool}: duplicate or unknown validation engine: {engine}')
        if report.get('schema') != 1 or report.get('status') != 'passed':
            raise RuntimeError(f'{tool}: report is not a passing schema-1 validation: {path}')
        if not isinstance(report.get('node'), str) or not report['node']:
            raise RuntimeError(f'{tool}: report omits the validation host version')
        if engine != 'node' and (not isinstance(report.get('browserVersion'), str) or not report['browserVersion']):
            raise RuntimeError(f'{tool}: report omits the actual browser version')
        if engine != 'node' and tool == 'featurecounts' and report.get('crossOriginIsolated') is not True:
            raise RuntimeError('featurecounts: browser report did not establish cross-origin isolation')
        if report['builds'][tool] != build:
            raise RuntimeError(f'{tool}: compiled or native assets differ from those tested in {path}')
        if report.get('runtime') != runtime_hashes:
            raise RuntimeError(f'{tool}: runtime differs from that tested in {path}')
        if report.get('validation') != validation_hashes:
            raise RuntimeError(f'{tool}: validation implementation or case plan differs from {path}')
        if report.get('manifestSha256') != manifest_sha or report.get('datasets') != datasets:
            raise RuntimeError(f'{tool}: real datasets differ from those tested in {path}')
        report_tools = set(report['builds'])
        if not report_tools <= set(TOOLS):
            raise RuntimeError(f'{tool}: report contains unknown tools')
        all_ids = {case_id for report_tool in report_tools for case_id in planned_cases(report_tool)}
        expected = report.get('expectedCases', [])
        records = report.get('cases', [])
        actual = [record.get('id') for record in records]
        if (len(expected) != len(set(expected)) or set(expected) != all_ids
                or len(actual) != len(set(actual)) or set(actual) != all_ids
                or any(record.get('status') != 'passed' for record in records)):
            raise RuntimeError(f'{tool}: report does not contain the complete passing planned case set: {path}')
        by_id = {record['id']: record for record in records}
        for case_id, spec in required_cases.items():
            record = by_id[case_id]
            if any(record.get(key) != value for key, value in {
                'tool': tool, 'dataset': spec['_dataset'], 'case': spec['id'], 'kind': spec['kind'], 'args': spec['args'],
            }.items()):
                raise RuntimeError(f'{case_id}: recorded command differs from its case plan')
            executions = record.get('executions', {})
            if set(executions) != set(spec['executions']):
                raise RuntimeError(f'{case_id}: required native/patched-native/WASM executions are missing')
            failure = spec['kind'] in ('failure', 'restriction')
            error_policy = spec.get('nativeErrorPolicy')
            for target, execution in executions.items():
                exit_code = execution.get('exitCode')
                signal = execution.get('signal')
                if 'signal' not in execution:
                    raise RuntimeError(f'{case_id}: missing signal evidence for {target}')
                if signal is not None:
                    if (not error_policy or target != 'native' or signal != 'SIGSEGV'
                            or exit_code is not None or execution.get('canonicalExitCode') is not None):
                        raise RuntimeError(f'{case_id}: unexpected signal or crash evidence for {target}')
                else:
                    canonical = (exit_code & 255) if type(exit_code) is int and exit_code < 0 else exit_code
                    if type(exit_code) is not int or execution.get('canonicalExitCode') != canonical:
                        raise RuntimeError(f'{case_id}: invalid recorded exit code for {target}')
                    if (canonical == 0) == failure:
                        raise RuntimeError(f'{case_id}: unexpected exit code for {target}')
                if error_policy and target != 'native':
                    if exit_code != 2 or execution.get('portDiagnosticMatched') is not True:
                        raise RuntimeError(f'{case_id}: required clean port exit/diagnostic evidence is missing for {target}')
                elif 'portDiagnosticMatched' in execution:
                    raise RuntimeError(f'{case_id}: unexpected port diagnostic exception for {target}')
                files = execution.get('files', {})
                for name, checksum in files.items():
                    relative_name(name)
                    if not isinstance(checksum, str) or not SHA256.fullmatch(checksum):
                        raise RuntimeError(f'{case_id}: invalid output checksum for {target}')
                if not failure and (execution.get('missingOutputs') != [] or not set(spec.get('outputs', [])) <= files.keys()):
                    raise RuntimeError(f'{case_id}: successful execution has missing outputs for {target}')
                for stream in ('stdout', 'stderr'):
                    if not SHA256.fullmatch(execution.get(stream, '')):
                        raise RuntimeError(f'{case_id}: missing {stream} evidence for {target}')
                if failure and execution['stdout'] == execution['stderr'] == empty_hash:
                    raise RuntimeError(f'{case_id}: expected a failure diagnostic for {target}')
            if error_policy:
                expected_policy = {'reason': error_policy['reason'],
                                   'native': {key: executions['native'][key] for key in ('exitCode', 'signal')},
                                   'portExitCode': spec['portExitCode'], 'portDiagnostic': spec['portDiagnostic']}
                if record.get('errorPolicyObserved') != expected_policy:
                    raise RuntimeError(f'{case_id}: native error-policy evidence is missing or inconsistent')
            elif 'errorPolicyObserved' in record:
                raise RuntimeError(f'{case_id}: unplanned native error-policy evidence')
            normal_executions = [execution for target, execution in executions.items() if not (error_policy and target == 'native')]
            if failure and len({execution['canonicalExitCode'] for execution in normal_executions}) != 1:
                raise RuntimeError(f'{case_id}: native and WebAssembly failure exits disagree')
            comparisons = record.get('comparisons', {})
            if set(comparisons) != set(spec['comparisons']):
                raise RuntimeError(f'{case_id}: required semantic comparisons are missing')
            for target, comparison in comparisons.items():
                if (comparison.get('equal') is not True
                        or not all(SHA256.fullmatch(comparison.get(key, '')) for key in ('expectedSemanticDigest', 'semanticDigest', 'biologicalDigest'))
                        or comparison['expectedSemanticDigest'] != comparison['semanticDigest']
                        or not comparison.get('invariants')):
                    raise RuntimeError(f'{case_id}: semantic comparison did not pass for {target}')
            if spec.get('effect') or spec.get('equivalentTo'):
                baseline = spec.get('effect') or spec['equivalentTo']
                baseline_record = by_id[f'{tool}-{spec["_dataset"]}-{baseline}']
                before = baseline_record['comparisons']['native-contract']['biologicalDigest']
                after = comparisons['native-contract']['biologicalDigest']
                changed = bool(spec.get('effect'))
                expected_effect = {'baseline': baseline, 'before': before, 'after': after,
                                   'changed': changed, 'expected': 'changed' if changed else 'equivalent'}
                if record.get('optionEffect') != expected_effect or (before != after) != changed:
                    raise RuntimeError(f'{case_id}: required option-effect evidence is missing or inconsistent')
        checked[engine] = (raw, report)
    if set(checked) != ENGINES:
        raise RuntimeError(f'{tool}: missing passing reports for {", ".join(sorted(ENGINES - checked.keys()))}')
    return checked


def collect(tool: str, reports: list[Path]) -> tuple[dict[str, tuple[bytes, int]], dict]:
    if tool not in TOOLS:
        raise ValueError(f'Unknown tool: {tool}')
    lock_path = local_path(f'tools/{tool}/sources.lock.json')
    lock = json.loads(required(lock_path))
    module = required(local_path(f'dist/{tool}/{tool}.mjs'))
    wasm = required(local_path(f'dist/{tool}/{tool}.wasm'))
    if not module or not wasm.startswith(b'\0asm\1\0\0\0'):
        raise RuntimeError(f'{tool}: missing module or invalid WebAssembly header')
    dist = local_path(f'dist/{tool}')
    assets = {path.relative_to(dist).as_posix(): digest(required(path))
              for path in sorted(dist.rglob('*')) if path.is_file() and path.suffix in ASSET_SUFFIXES}
    identity = check_build_identity(tool, assets)
    checked = check_reports(tool, reports, assets)
    manifest, manifest_sha = checked_dataset_manifest()
    entries: dict[str, tuple[bytes, int]] = {}
    sources: dict[str, str] = {}

    def put(name: str, data: bytes, mode: int = 0o644, *, source: bool = True):
        relative_name(name)
        if name in entries and entries[name][0] != data:
            raise RuntimeError(f'Conflicting package entries: {name}')
        entries[name] = (data, mode)
        if source:
            sources[name] = digest(data)

    def add(name: str, *, source: bool = True, expected: str | None = None):
        path = local_path(name)
        data = verify(path, expected) if expected else required(path)
        put(name, data, 0o755 if path.stat().st_mode & 0o111 else 0o644, source=source)

    def tree(directory: str, *, source: bool = True):
        directory_path = local_path(directory)
        if not directory_path.is_dir():
            raise RuntimeError(f'Required package directory is missing: {directory}')
        for path in sorted(directory_path.rglob('*')):
            if path.is_file() and not {'.git', '__pycache__'} & set(path.parts) and path.suffix != '.pyc':
                add(path.relative_to(ROOT).as_posix(), source=source)

    for name in ('LICENSE', 'THIRD_PARTY.md', *RUNTIME, 'runtime/README.md',
                 'scripts/setup-emsdk.sh', f'scripts/build-{tool}.sh',
                 'scripts/package-rna-analysis.py', *VALIDATION,
                 'scripts/test-rna-analysis-demo.mjs', 'scripts/fetch-rna-analysis-test-data.py',
                 'tests/runtime.test.mjs', 'tests/fixtures/mock-tool.mjs',
                 'tests/test_rna_analysis_compare.py', 'tests/test_rna_analysis_package.py'):
        add(name)
    for directory in (f'tools/{tool}', EXAMPLE, 'validation/rna-analysis', DATA):
        tree(directory)
    for name, checksum in identity['inputs'].items():
        add(name, expected=checksum)
    for name in lock.get('buildScripts', []):
        add(name)

    archives = source_archives(tool, lock)
    archive_basenames = {Path(name).name for name in archives}
    for path in sorted(dist.rglob('*')):
        if not path.is_file() or path.name in archive_basenames:
            continue
        if path.suffix in {'.o', '.a', '.so', '.exe', '.dylib', '.dll', '.pyc'}:
            raise RuntimeError(f'Unexpected compiled object in distribution: {path}')
        if path.name.endswith(('.tar.gz', '.tar.xz', '.tar.bz2', '.tar.zst', '.tar', '.tgz', '.tbz2', '.txz', '.zip', '.crate')):
            raise RuntimeError(f'Unpinned source archive in distribution: {path}')
        add(path.relative_to(ROOT).as_posix(), source=False)
    for name, checksum in archives.items():
        canonical = local_path(name)
        if canonical.is_file():
            data = verify(canonical, checksum)
        else:
            relative = name.removeprefix('build/downloads/') if name.startswith('build/downloads/') else Path(name).name
            bundled = dist / relative
            if not bundled.is_file():
                bundled = dist / Path(name).name
            data = verify(bundled, checksum)
        put(name, data)
    notices = set(lock.get('requiredNotices', [])) | {'LICENSE', 'THIRD_PARTY_LICENSES.txt'}
    for notice in notices:
        relative_name(notice)
        if not any(f'{directory}/{notice}' in entries for directory in (f'tools/{tool}', f'dist/{tool}')):
            raise RuntimeError(f'{tool}: required licence notice is missing: {notice}')
    for notice in lock.get('distRequiredNotices', []):
        required(local_path(f'dist/{tool}/{relative_name(notice)}'))

    if not manifest.get('sources'):
        raise RuntimeError('The RNA analysis manifest omits its original source inputs')
    for name, info in manifest['sources'].items():
        cache = relative_name(info['cachePath'])
        if not cache.startswith('build/rna-analysis-sources/'):
            raise RuntimeError(f'Dataset source cache is outside its expected directory: {name}')
        data = verify(local_path(cache), info['sha256'])
        if info.get('bytes') != len(data):
            raise RuntimeError(f'Dataset source byte count differs from its manifest: {name}')
        put(cache, data)
    helpers = manifest.get('regeneration', {}).get('files') or {}
    if not isinstance(helpers, dict):
        raise RuntimeError('Dataset regeneration helper files must be a mapping of paths to checksums')
    for name, info in helpers.items():
        add(name, expected=info['sha256'] if isinstance(info, dict) else info)
    config = json.loads(entries[f'{EXAMPLE}/config.json'][0])
    config['tools'] = {tool: config['tools'][tool]}
    threaded = tool == 'featurecounts'
    if config['tools'][tool].get('waitForExit') is not threaded or config['tools'][tool].get('requiresIsolation') is not threaded:
        raise RuntimeError(f'{tool}: browser configuration does not match the port threading contract')
    config_bytes = json_bytes(config)
    entries[f'{EXAMPLE}/config.json'] = (config_bytes, 0o644)
    sources[f'{EXAMPLE}/config.json'] = digest(config_bytes)
    runtime_readme = entries['runtime/README.md'][0].decode().split('## Interface', 1)
    if len(runtime_readme) != 2:
        raise RuntimeError('The runtime README is missing its interface documentation')
    api = ('# JavaScript runner\n\nSee the package README and `examples/rna-analysis/` for the included program.\n\n## Interface' + runtime_readme[1]).encode()
    entries['runtime/README.md'] = (api, 0o644)
    sources['runtime/README.md'] = digest(api)

    package = read_json('package.json')
    playwright = package['devDependencies']['playwright']
    if not re.fullmatch(r'\d+\.\d+\.\d+', playwright):
        raise RuntimeError('Playwright must be pinned to an exact version')
    package = {
        'name': f'{tool}-wasm', 'version': lock['version'], 'private': True, 'type': 'module',
        'scripts': {
            'test': 'python3 -m unittest discover -s tests -p "test_rna_analysis_*.py" && node --test tests/runtime.test.mjs',
            'validate': f'node scripts/validate-rna-analysis.mjs --tool {tool}',
        },
        'devDependencies': {'playwright': playwright}, 'engines': {'node': '>=20'},
    }
    put('package.json', json_bytes(package))
    npm_lock = read_json('package-lock.json')
    if npm_lock['packages']['']['devDependencies'] != package['devDependencies']:
        raise RuntimeError('The npm lock does not match the pinned browser test dependency')
    npm_lock.update(name=package['name'], version=package['version'])
    npm_lock['packages'][''].update(name=package['name'], version=package['version'])
    put('package-lock.json', json_bytes(npm_lock))
    for engine, (raw, _) in checked.items():
        put(f'validation-reports/{engine}.json', raw, source=False)
    put('source-manifest.json', json_bytes({
        'schemaVersion': 1, 'tool': tool, 'upstreamRevision': lock['revision'],
        'upstreamLockSha256': digest(required(lock_path)), 'files': dict(sorted(sources.items())),
        'notes': 'Includes original source archives, dependency sources, patches, build scripts, runtime and real-data provenance. Compiler toolchains and documented host dependencies must be installed separately.',
    }), source=False)
    put('release.json', json_bytes({
        'schemaVersion': 1, 'tool': tool, 'upstreamVersion': lock['version'], 'upstreamRevision': lock['revision'],
        'emscripten': lock['emscripten'], 'assets': assets, 'datasetManifestSha256': manifest_sha,
        'validationStatus': 'passed for the recorded test scope', 'testedEngines': sorted(checked),
        'reports': {engine: digest(raw) for engine, (raw, _) in checked.items()},
        'requiresCrossOriginIsolation': threaded, 'waitForExit': threaded,
        'notes': 'Reports compare these exact assets, runtime and real-data subsets with untouched and patched native programs. The recorded scope does not establish correctness or capacity for every input or option.',
    }), source=False)
    put('README.md', readme(tool, lock, config).encode(), source=False)
    put('SHA256SUMS', ''.join(f'{digest(data)}  {name}\n' for name, (data, _) in sorted(entries.items())).encode(), source=False)
    return entries, lock


def readme(tool: str, lock: dict, config: dict) -> str:
    command = next(iter(config['tools'][tool]['commands'].values()))
    isolation = ('featureCounts uses pthread workers and needs cross-origin isolation. The included static server supplies the required headers. The browser example also includes an optional service-worker setup for plain static hosting.'
                 if tool == 'featurecounts' else f'{LABELS[tool]} runs in one worker without cross-origin isolation.')
    return f'''# {LABELS[tool]} {lock['version']} for WebAssembly

This independent package includes the actual compiled program, JavaScript runner, browser example, real human and yeast RNA-seq fixtures, validation reports, corresponding sources and licence notices.

## Run the browser example

From this directory, run `python3 examples/rna-analysis/serve.py`, then open `http://localhost:8000/examples/rna-analysis/`. Load example data, choose a command and run it. You can edit arguments, upload your own files and download the original results. All computation happens in the browser; the server only serves files.

{isolation} See `examples/rna-analysis/README.md` and `tools/{tool}/README.md` for supported inputs, options and hosting requirements. The small real-data subsets are command checks; their selection and limitations are documented in `datasets/rna-analysis/README.md`. Browser memory limits still apply.

## JavaScript API

```js
import {{ ToolRunner }} from './runtime/index.mjs';
const runner = new ToolRunner({{
  moduleUrl: new URL('./dist/{tool}/{tool}.mjs', import.meta.url),
  waitForExit: {'true' if tool == 'featurecounts' else 'false'},
}});
const result = await runner.run({json.dumps(command['args'])}, {{
  files: inputs, // [{{path: 'input.bam', data: Uint8Array}}, ...] plus required indexes/annotation
  outputs: {json.dumps(command.get('outputs', []))},
  outputDirectories: {json.dumps(command.get('outputDirectories', []))},
  timeoutMs: 300000,
}});
```

Check `result.exitCode` and `result.missingOutputs`. Generated files are returned in `result.files`; standard output and error are byte arrays. Use an abort signal to cancel. Keep the complete `dist/{tool}/` directory together. See `runtime/README.md` for the interface.

## Rebuild and validate

Install the prerequisites documented in `tools/{tool}/README.md`, including Emscripten {lock['emscripten']}, then run:

```sh
bash scripts/build-{tool}.sh all
python3 scripts/fetch-rna-analysis-test-data.py --verify
node scripts/validate-rna-analysis.mjs --tool {tool} --engine node --dataset all
npm ci
npx playwright install chromium firefox webkit
```

Repeat validation with `--engine chromium`, `--engine firefox` and `--engine webkit`. Run the browser example checks with `RNA_ANALYSIS_TOOL={tool} BROWSER=chromium node scripts/test-rna-analysis-demo.mjs`. Pinned source archives and build recipes are included; compiler toolchains and documented host dependencies are installed separately. The fixture README explains regeneration of the real data.

`validation-reports/` identifies the tested binaries, runtime, inputs, planned cases and comparisons. The semantic comparison rules are in `validation/rna-analysis/`. These checks cover the bundled datasets and listed options, not every possible input. `release.json` records the validated engines. Verify all package files with `sha256sum -c SHA256SUMS`.

## Licences

`LICENSE` covers this project's original infrastructure. Retain all tool and dependency notices, original source archives, patches, build recipes and data provenance when redistributing. See `THIRD_PARTY.md`, `tools/{tool}/` and `datasets/rna-analysis/README.md` for the separate upstream terms.
'''


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
    # Validate every requested tool before replacing any existing release.
    prepared = {tool: collect(tool, args.validation_report) for tool in requested}
    for tool in requested:
        write_package(tool, args.output.resolve(), args.validation_report, prepared=prepared[tool])


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, KeyError, OSError, TypeError) as error:
        print(f'Packaging failed: {error}', file=sys.stderr)
        raise SystemExit(1)
