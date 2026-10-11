#!/usr/bin/env python3
"""Package independently usable somatic callers after native/browser validation."""
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
TOOLS = ('somaticsniper', 'lofreq')
LABELS = {'somaticsniper': 'SomaticSniper', 'lofreq': 'LoFreq'}
ENGINES = {'node', 'chromium', 'firefox', 'webkit'}
BASE_RUNTIME = ('runtime/index.mjs', 'runtime/worker.mjs', 'runtime/execute.mjs')
RUNTIME = {'somaticsniper': BASE_RUNTIME,
           'lofreq': (*BASE_RUNTIME, 'runtime/lofreq.mjs', 'runtime/lofreq-somatic.mjs')}
VALIDATION = ('scripts/validate-somatic.mjs', 'validation/somatic/compare.py',
              'validation/somatic/cases.json', 'validation/somatic/bam.py',
              'validation/somatic/numerics.py', 'validation/compare.py')
DATA = 'datasets/somatic'
EXAMPLE = 'examples/somatic'
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


def tree_hashes(directory: str, *, suffixes=None, exclude=()) -> dict[str, str]:
    base = local_path(directory)
    if not base.is_dir() or base.is_symlink():
        raise RuntimeError(f'Required package directory is missing or unsafe: {directory}')
    result = {}
    for path in sorted(base.rglob('*')):
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
                if not SHA256.fullmatch(record.get('sourceTreeSha256', '')):
                    raise RuntimeError(f'{tool}: bundled dependency lacks its source tree hash')
            else:
                add(record)
    for record in lock.get('sourceArchives', []):
        add(record)
    return archives


def check_build_identity(tool: str) -> tuple[dict, dict, dict]:
    lock = read_json(f'tools/{tool}/sources.lock.json')
    raw = required(local_path(f'dist/{tool}/build-manifest.json'))
    manifest = json.loads(raw)
    assets = tree_hashes(f'dist/{tool}', exclude=('build-manifest.json',))
    if manifest.get('schema') != 1 or manifest.get('tool') != tool or manifest.get('assets') != assets:
        raise RuntimeError(f'{tool}: build manifest does not identify the compiled distribution')
    if not required(local_path(f'dist/{tool}/{tool}.wasm')).startswith(b'\0asm\1\0\0\0'):
        raise RuntimeError(f'{tool}: invalid WebAssembly header')
    if not required(local_path(f'dist/{tool}/{tool}.mjs')):
        raise RuntimeError(f'{tool}: missing JavaScript module')
    expected_inputs = {f'tools/{tool}/{name}' for name in tree_hashes(f'tools/{tool}')}
    expected_inputs.add(f'scripts/build-{tool}.sh')
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
    if tool == 'lofreq':
        environments = manifest.get('nativeFiles', {})
        if set(environments) != {'native', 'patched-native'}:
            raise RuntimeError('lofreq: both native helper environments must be identified')
        for kind, recorded_files in environments.items():
            actual = {}
            for path in sorted(local_path(f'build/{kind}').glob('lofreq*')):
                if path.is_dir():
                    actual.update({f'{path.name}/{name}': checksum for name, checksum in tree_hashes(path.relative_to(ROOT).as_posix()).items()})
                else:
                    actual[path.name] = digest(required(path))
            if actual != recorded_files or actual.get('lofreq') != build[kind] or not any(name.endswith('.py') for name in actual):
                raise RuntimeError(f'lofreq: {kind} helper environment differs from its build manifest')
        build['nativeFiles'] = environments
    return lock, manifest, build


def planned_cases(tool: str) -> dict:
    plan = read_json('validation/somatic/cases.json')
    if plan.get('schema') != 1 or plan.get('datasets') != ['hcc1395']:
        raise RuntimeError('Unknown somatic case-plan schema or datasets')
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
        for bam in spec.get('bams', []):
            counts = checks.get(bam['path'], {})
            if (type(counts.get('records')) is not int or counts['records'] <= 0
                    or type(counts.get('eligibleIndelQualityRecords')) is not int
                    or not 0 < counts['eligibleIndelQualityRecords'] <= counts['records']):
                raise RuntimeError(f'{case_id}: missing BAM indel-quality evidence')
        for index in spec.get('indexes', []):
            counts = checks.get(index['path'], {})
            if any(type(counts.get(field)) is not int or counts[field] <= 0
                   for field in ('indexedReferences', 'bins', 'chunks')):
                raise RuntimeError(f'{case_id}: missing nonempty index evidence')
        if spec.get('numerics'):
            counts = checks.get('numerics', {})
            if (type(counts.get('cases')) is not int or counts['cases'] <= 0
                    or counts.get('mantissaBits') not in (64, 113)
                    or counts.get('minExponent') != -16381
                    or not counts.get('oracle')
                    or len(counts.get('relativeErrors', [])) != counts['cases']):
                raise RuntimeError(f'{case_id}: missing independent numerical-oracle evidence')


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
            if spec.get('workflow'):
                workflow = record.get('workflow', {})
                stages = workflow.get('nativeStages', [])
                if (workflow.get('expectedStages') != spec['expectedStages']
                        or workflow.get('oracle') != 'unchanged upstream Python workflow'
                        or len(stages) != spec['expectedStages']
                        or any(not isinstance(args, list) or not args or any(not isinstance(arg, str) for arg in args) for args in stages)
                        or set(workflow.get('targets', {})) != set(executions)):
                    raise RuntimeError(f'{case_id}: incomplete upstream workflow stage evidence')
                for target, evidence in workflow['targets'].items():
                    if evidence != {'stages': stages, 'equal': True, 'successfulStages': spec['expectedStages']}:
                        raise RuntimeError(f'{case_id}: workflow stages differ for {target}')
                if spec.get('kind') == 'repeat':
                    baseline_id = f'{tool}-{spec["_dataset"]}-{spec["baseline"]}'
                    if stages != by_id[baseline_id]['workflow']['nativeStages']:
                        raise RuntimeError(f'{case_id}: repeat workflow differs from the native baseline')
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


def example_readme(tool: str) -> str:
    specific = ('SomaticSniper calls SNVs from a tumour and matched normal; the tumour BAM argument comes first. Use `-v` to show the version. An empty argument list prints usage and returns status 1, matching the native program.'
                if tool == 'somaticsniper' else
                'The somatic presets compare tumour and matched normal and return separate SNV and indel VCF files. LoFreq describes its somatic workflow as experimental. For indels, first run both indel-quality commands and both indexing commands, then select the somatic SNV and indel preset. The single-sample preset does not classify variants as somatic.')
    return f'''# {LABELS[tool]} in the browser

Run `python3 -m http.server 8000` from the package root and open `http://localhost:8000/examples/somatic/`. Load the real HCC1395 tumour/normal subset or upload your own BAM, index, reference and region files. Computation runs locally in browser workers. Ordinary static hosting is sufficient; cross-origin isolation is not required.

Arguments are a JSON array without the program name. Each entry is passed literally. Successful output files remain loaded for later commands and can be downloaded. Add output paths to **Files to keep**, or a directory to **Directories to keep**. **Cancel** stops the current command. A new command starts with fresh program state; page reload clears the files.

{specific}

See [the tool documentation](../../tools/{tool}/README.md), [data provenance and coordinate mappings](../../datasets/somatic/README.md), and [validation rules](../../validation/somatic/README.md). The small real regions do not establish whole-genome capacity. Native/browser agreement checks the port; the published reference calls provide separate evidence about biological accuracy.
'''


def readme(tool: str, lock: dict, config: dict) -> str:
    runner, module = ('LoFreqRunner', 'lofreq.mjs') if tool == 'lofreq' else ('ToolRunner', 'index.mjs')
    command = config['tools'][tool]['commands']['somatic']
    return f'''# {LABELS[tool]} {lock['version']} for WebAssembly

This standalone package contains the actual compiled caller, JavaScript runner, browser example, real matched human tumour/normal reads, validation reports, original source archives, build recipes and licence notices.

## Run the browser example

Run `python3 -m http.server 8000` from this directory and open `http://localhost:8000/examples/somatic/`. Load the example data or upload your own files, edit the arguments and run the caller. Results can be viewed and downloaded. Computation happens in browser workers; the server only serves files. Cross-origin isolation is not required.

Keep `dist/{tool}/` together. See `tools/{tool}/README.md` for supported inputs, options and limitations, and `datasets/somatic/README.md` for sample identity, original coordinates, selection and reference calls. The reads and reference come from real data; the small contigs and alignments use the documented local coordinate mappings.

## JavaScript API

```js
import {{ {runner} }} from './runtime/{module}';
const runner = new {runner}({{
  moduleUrl: new URL('./dist/{tool}/{tool}.mjs', import.meta.url),
}});
const result = await runner.run({json.dumps(command['args'])}, {{
  files: inputs, // [{{path: 'tumor.bam', data: Uint8Array}}, ...] and required indexes/reference
  outputs: {json.dumps(command['outputs'])},
  timeoutMs: 600000,
}});
```

Check `result.exitCode` and `result.missingOutputs`. Generated files are returned in `result.files`; standard output and error are byte arrays. An abort signal cancels the command. See the browser example for file handling.

## Rebuild and validate

Build prerequisites are documented in `tools/{tool}/README.md`. The included source archives are verified and reused by the build recipe. Install the documented host dependencies and Emscripten {lock['emscripten']}, then run:

```sh
bash scripts/build-{tool}.sh all
python3 scripts/fetch-somatic-test-data.py --verify
npm ci
node scripts/validate-somatic.mjs --tool {tool} --engine node --dataset all
npx playwright install chromium firefox webkit
```

Repeat validation with `--engine chromium`, `--engine firefox` and `--engine webkit`. Run the browser example checks with `SOMATIC_TOOL={tool} BROWSER=chromium node scripts/test-somatic-demo.mjs`, changing the browser name for the other engines. The data README documents exact regeneration and any external tools it requires. Original source chunks are included; the complete large sequencing BAMs are not needed to use this package.

The reports identify these exact compiled assets, native comparison inputs, runtime, source recipes, real fixtures and configured test cases. They cover the supplied regions and listed options. Agreement with the native program establishes evidence about this port; it does not make every call biologically correct or establish whole-genome performance. `release.json` records the validated engines. Verify all files with `sha256sum -c SHA256SUMS`.

## Licences

`LICENSE` covers this project's infrastructure. Retain the separate upstream notices, original sources, patches, build recipes and data provenance when redistributing. See `tools/{tool}/`, the notices in `dist/{tool}/`, and the data README.
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
        for name in tree_hashes(directory):
            add(f'{directory}/{name}', source=source)

    for name in ('LICENSE', *RUNTIME[tool], 'runtime/README.md', 'validation/README.md', 'scripts/setup-emsdk.sh',
                 f'scripts/build-{tool}.sh', 'scripts/package-somatic.py', *VALIDATION,
                 'scripts/test-somatic-demo.mjs', 'scripts/fetch-somatic-test-data.py',
                 'tests/test_somatic_compare.py', 'tests/test_somatic_package.py',
                 'tests/runtime.test.mjs', 'tests/fixtures/mock-tool.mjs'):
        add(name)
    if tool == 'lofreq':
        add('runtime/LOFREQ.md')
        add('tests/lofreq.test.mjs')
    for directory in (f'tools/{tool}', EXAMPLE, 'validation/somatic', DATA):
        tree(directory)
    for name, checksum in identity['inputs'].items():
        add(name, expected=checksum)
    tree(f'dist/{tool}', source=False)
    archives = source_archives(tool, lock)
    for name, checksum in archives.items():
        path = f'dist/{tool}/sources/{name}'
        verify(local_path(path), checksum)
        sources[path] = checksum
    if set(tree_hashes(f'dist/{tool}/sources')) != archives.keys():
        raise RuntimeError(f'{tool}: source distribution contains missing or unpinned archives')
    for notice in set(lock['requiredNotices']) | {'LICENSE', 'THIRD_PARTY_LICENSES.txt'}:
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
    replacement = example_readme(tool).encode()
    entries[f'{EXAMPLE}/README.md'] = (replacement, 0o644)
    sources[f'{EXAMPLE}/README.md'] = digest(replacement)
    runtime_doc = entries['runtime/README.md'][0].decode().split('## Interface', 1)
    if len(runtime_doc) != 2:
        raise RuntimeError('The runtime README is missing its interface documentation')
    replacement = ('# JavaScript runner\n\nSee the package README and `examples/somatic/` for the included caller.\n\n## Interface' + runtime_doc[1]).encode()
    entries['runtime/README.md'] = (replacement, 0o644)
    sources['runtime/README.md'] = digest(replacement)

    playwright = read_json('package.json')['devDependencies']['playwright']
    if not re.fullmatch(r'\d+\.\d+\.\d+', playwright):
        raise RuntimeError('Playwright must be pinned to an exact version')
    node_tests = 'tests/runtime.test.mjs' + (' tests/lofreq.test.mjs' if tool == 'lofreq' else '')
    package = {'name': f'{tool}-wasm', 'version': lock['version'], 'private': True, 'type': 'module',
               'scripts': {'test': 'python3 -m unittest discover -s tests -p "test_somatic_*.py" && node --test ' + node_tests,
                           'validate': f'node scripts/validate-somatic.mjs --tool {tool}'},
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
    except (RuntimeError, ValueError, KeyError, OSError, TypeError, zipfile.BadZipFile) as error:
        print(f'Packaging failed: {error}', file=sys.stderr)
        raise SystemExit(1)
