#!/usr/bin/env python3
"""Package independently usable RNA quantifiers with sources and checked results."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ('kallisto', 'salmon')
ENGINES = {'node', 'chromium', 'firefox', 'webkit'}
RUNTIME = ('runtime/index.mjs', 'runtime/worker.mjs', 'runtime/execute.mjs')
VALIDATION = ('scripts/validate-quant.mjs', 'validation/quant/compare.py', 'validation/quant/tolerances.json')


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()


def required(path: Path) -> bytes:
    if not path.is_file():
        raise RuntimeError(f'Required package input is missing: {path}')
    return path.read_bytes()


def verify(path: Path, expected: str) -> bytes:
    data = required(path)
    if digest(data) != expected:
        raise RuntimeError(f'Checksum mismatch: {path}')
    return data


def source_archives(tool: str, lock: dict) -> list[tuple[Path, str]]:
    """All build-time downloaded sources must have a pinned archive digest."""
    result = [(ROOT / 'build/downloads' / lock.get('archive', f'{tool}-{lock["revision"]}.tar.gz'), lock['sha256'])]
    for name, dependency in lock.get('dependencies', {}).items():
        for item in dependency if isinstance(dependency, list) else [dependency]:
            version = item.get('revision') or item.get('version')
            filename = item.get('archive') or f'{name}-{version}.tar.gz'
            result.append((ROOT / item.get('directory', 'build/downloads') / filename, item['sha256']))
    for archive in lock.get('sourceArchives', []):
        result.append((ROOT / archive.get('directory', 'build/downloads') / archive['archive'], archive['sha256']))
    for path, _ in result:
        if not path.resolve().is_relative_to(ROOT.resolve()):
            raise RuntimeError(f'Source archive path escapes package root: {path}')
    return result


def check_build_identity(tool: str, assets: dict[str, str]) -> None:
    manifest = json.loads(required(ROOT / f'dist/{tool}/build-manifest.json'))
    if manifest.get('schema') != 1 or manifest.get('tool') != tool or manifest.get('assets') != assets:
        raise RuntimeError(f'{tool}: build manifest does not identify the compiled distribution')
    source_suffixes = {'.patch', '.h', '.hpp', '.c', '.cc', '.cpp', '.rs', '.py', '.sh', '.json', '.lock'}
    expected = {f'scripts/build-{tool}.sh', f'tools/{tool}/sources.lock.json'}
    expected.update(path.relative_to(ROOT).as_posix() for path in (ROOT / f'tools/{tool}').rglob('*')
                    if path.is_file() and path.suffix in source_suffixes)
    recorded = manifest.get('inputs', {})
    if not expected <= recorded.keys():
        raise RuntimeError(f'{tool}: build manifest omits source inputs: {sorted(expected - recorded.keys())}')
    for name, checksum in recorded.items():
        path = ROOT / name
        if not path.resolve().is_relative_to(ROOT.resolve()):
            raise RuntimeError(f'{tool}: build input path escapes package root')
        verify(path, checksum)


def check_reports(tool: str, reports: list[Path], assets: dict[str, str], candidate: bool) -> dict[str, tuple[bytes, dict]]:
    checked = {}
    runtime_hashes = {name: digest(required(ROOT / name)) for name in RUNTIME}
    validation_hashes = {name: digest(required(ROOT / name)) for name in VALIDATION}
    fixture_manifest = json.loads(required(ROOT / 'datasets/rna-quant/manifest.json'))
    datasets = {}
    for dataset in ('human', 'yeast'):
        files = {}
        for name in ('transcripts.fa', 'reads_1.fastq', 'reads_2.fastq'):
            relative = f'{dataset}/{name}'
            expected = fixture_manifest['files'][relative]['sha256']
            verify(ROOT / 'datasets/rna-quant' / relative, expected)
            files[name] = expected
        datasets[dataset] = {'pairs': fixture_manifest['datasets'][dataset]['fastq_pairs'], 'files': files}
    for path in reports:
        raw = required(path)
        report = json.loads(raw)
        if tool not in report.get('builds', {}):
            continue
        engine = report.get('engine')
        if engine not in ENGINES or engine in checked:
            raise RuntimeError(f'{tool}: duplicate or unknown engine in {path}')
        if report.get('status') != 'passed' or not report.get('cases') or any(case.get('status') != 'passed' for case in report['cases']):
            raise RuntimeError(f'{tool}: {path} is not a completely passing comparison report')
        case_ids = [case['id'] for case in report['cases']]
        expected_cases = report.get('expectedCases', [])
        if len(set(case_ids)) != len(case_ids) or not expected_cases or len(set(expected_cases)) != len(expected_cases) or set(case_ids) != set(expected_cases):
            raise RuntimeError(f'{tool}: {path} has duplicate or incomplete validation cases')
        build = report['builds'][tool]
        if build.get('assets') != assets or build.get('wasm') != assets[f'{tool}.wasm'] or build.get('module') != assets[f'{tool}.mjs']:
            raise RuntimeError(f'{tool}: compiled assets differ from those tested in {path}')
        if report.get('runtime') != runtime_hashes:
            raise RuntimeError(f'{tool}: JavaScript runtime differs from that tested in {path}')
        if report.get('validation') != validation_hashes:
            raise RuntimeError(f'{tool}: comparison code or tolerances differ from those used in {path}')
        if report.get('datasets') != datasets:
            raise RuntimeError(f'{tool}: bundled input datasets differ from those tested in {path}')
        for dataset in ('human', 'yeast'):
            cases = {case['id']: case for case in report['cases']}
            index = cases.get(f'{tool}-{dataset}-index-k31')
            quant = cases.get(f'{tool}-{dataset}-paired')
            if not index or not quant or any(quant.get('comparisons', {}).get(target, {}).get('equal') is not True for target in ('patched-native', 'wasm')):
                raise RuntimeError(f'{tool}: {path} does not cover indexing and native quantification comparisons on both real datasets')
        checked[engine] = (raw, report)
    needed = {'node'} if candidate else ENGINES
    if not needed <= checked.keys():
        raise RuntimeError(f'{tool}: missing passing reports for {", ".join(sorted(needed - checked.keys()))}')
    return checked


def collect(tool: str, reports: list[Path], *, candidate: bool = False) -> tuple[dict[str, tuple[bytes, int]], dict]:
    if tool not in TOOLS:
        raise ValueError(f'Unknown tool: {tool}')
    lock_path = ROOT / f'tools/{tool}/sources.lock.json'
    lock = json.loads(required(lock_path))
    required(ROOT / f'tools/{tool}/README.md')
    module = required(ROOT / f'dist/{tool}/{tool}.mjs')
    wasm = required(ROOT / f'dist/{tool}/{tool}.wasm')
    if not module or not wasm.startswith(b'\0asm\1\0\0\0'):
        raise RuntimeError(f'{tool}: missing module or invalid WebAssembly header')
    assets = {path.relative_to(ROOT / f'dist/{tool}').as_posix(): digest(required(path))
              for path in sorted((ROOT / f'dist/{tool}').rglob('*')) if path.is_file() and path.suffix in ('.mjs', '.js', '.wasm')}
    check_build_identity(tool, assets)
    checked = check_reports(tool, reports, assets, candidate)
    entries: dict[str, tuple[bytes, int]] = {}
    sources: dict[str, str] = {}

    def add(path: Path, *, source: bool = True, expected: str | None = None):
        if not path.resolve().is_relative_to(ROOT.resolve()):
            raise RuntimeError(f'Package file escapes root: {path}')
        name = path.relative_to(ROOT).as_posix()
        data = verify(path, expected) if expected else required(path)
        entries[name] = (data, 0o755 if path.stat().st_mode & 0o111 else 0o644)
        if source:
            sources[name] = digest(data)

    def tree(directory: str, *, source: bool = True):
        path = ROOT / directory
        if not path.is_dir():
            raise RuntimeError(f'Required package directory is missing: {path}')
        for file in sorted(path.rglob('*')):
            if file.is_file() and not {'.git', '__pycache__'} & set(file.parts) and file.suffix != '.pyc':
                add(file, source=source)

    for name in ('LICENSE', 'THIRD_PARTY.md', *RUNTIME, 'runtime/README.md', 'scripts/setup-emsdk.sh',
                 f'scripts/build-{tool}.sh', 'scripts/package-quant.py', 'scripts/validate-quant.mjs',
                 'scripts/test-quant-demo.mjs', 'scripts/fetch-quant-test-data.py', 'scripts/fetch-rna-test-data.py',
                 'tests/runtime.test.mjs', 'tests/fixtures/mock-tool.mjs', 'tests/test_quant_compare.py',
                 'datasets/rna-manifest.json', 'datasets/provenance/ERR127306.xml',
                 'datasets/provenance/SRR6357070.xml', 'datasets/provenance/nfcore-rnaseq-README.md'):
        add(ROOT / name)
    for directory in (f'tools/{tool}', 'examples/quant', 'validation/quant', 'datasets/rna-quant', 'datasets/rna-human', 'datasets/rna-yeast'):
        tree(directory)
    # This package's page only offers its own installed program.
    config = json.loads(entries['examples/quant/config.json'][0])
    config['tools'] = {tool: config['tools'][tool]}
    entries['examples/quant/config.json'] = (json_bytes(config), 0o644)
    sources['examples/quant/config.json'] = digest(entries['examples/quant/config.json'][0])
    archives = source_archives(tool, lock)
    archive_names = {path.name for path, _ in archives}
    for path in sorted((ROOT / f'dist/{tool}').rglob('*')):
        if path.is_file() and path.name not in archive_names and not path.name.endswith(('.tar.gz', '.tar.xz', '.zip')):
            if path.suffix in ('.o', '.a', '.so', '.exe', '.dylib'):
                raise RuntimeError(f'Unexpected compiled object in distribution: {path}')
            add(path, source=False)
    # Source archives preserve upstream code, bundled dependencies and notices.
    for path, checksum in archives:
        if path.is_file():
            add(path, expected=checksum)
        else:
            # CI keeps the build's complete distribution. Restore the canonical
            # source-archive location inside the ZIP without a second download.
            relative = path.relative_to(ROOT / 'build/downloads') if path.is_relative_to(ROOT / 'build/downloads') else Path(path.name)
            bundled = ROOT / f'dist/{tool}' / relative
            if not bundled.is_file():
                bundled = ROOT / f'dist/{tool}' / path.name
            data = verify(bundled, checksum)
            name = path.relative_to(ROOT).as_posix()
            entries[name] = (data, 0o644)
            sources[name] = digest(data)
    for relative in lock.get('buildScripts', []):
        add(ROOT / relative)
    for notice in lock.get('requiredNotices', ['LICENSE', 'THIRD_PARTY_LICENSES.txt']):
        required(ROOT / f'tools/{tool}' / notice)
    if tool == 'kallisto':
        for notice in ('LICENSE', 'LICENSE.bifrost', 'LICENSE.BooPHF', 'LICENSE.CRoaring', 'LICENSE.libdivide'):
            required(ROOT / 'tools/kallisto' / notice)
        required(ROOT / 'dist/kallisto/LICENSE.zlib')
    fixture_manifest = json.loads(entries['datasets/rna-quant/manifest.json'][0])
    for filename, expected in fixture_manifest['files'].items():
        verify(ROOT / 'datasets/rna-quant' / filename, expected['sha256'])
    for filename, info in fixture_manifest['sources'].items():
        add(ROOT / 'build/quant-sources' / filename, expected=info['sha256'])
    if (ROOT / 'build/quant-sources/sources.json').is_file():
        add(ROOT / 'build/quant-sources/sources.json')
    api = entries['runtime/README.md'][0].decode().split('## Interface', 1)[1]
    api = ('# JavaScript runner\n\nSee the package README for the included program and `examples/quant/` for a complete browser example.\n\n## Interface' + api).encode()
    entries['runtime/README.md'] = (api, 0o644)
    sources['runtime/README.md'] = digest(api)
    root_package = json.loads(required(ROOT / 'package.json'))
    playwright = root_package['devDependencies']['playwright']
    if not re.fullmatch(r'\d+\.\d+\.\d+', playwright):
        raise RuntimeError('Playwright must be pinned to an exact version')
    entries['package.json'] = (json_bytes({
        'name': f'{tool}-wasm', 'version': lock['version'], 'private': True, 'type': 'module',
        'scripts': {
            'test': 'python3 -m unittest discover -s tests -p "test_quant_compare.py" && node --test tests/runtime.test.mjs',
            'validate': f'node scripts/validate-quant.mjs --tool {tool}',
        },
        'devDependencies': {'playwright': playwright}, 'engines': {'node': '>=20'},
    }), 0o644)
    sources['package.json'] = digest(entries['package.json'][0])
    for engine, (raw, _) in checked.items():
        entries[f'validation-reports/{engine}.json'] = (raw, 0o644)
    entries['source-manifest.json'] = (json_bytes({
        'schemaVersion': 1, 'tool': tool, 'upstreamRevision': lock['revision'],
        'upstreamLockSha256': digest(required(lock_path)), 'files': dict(sorted(sources.items())),
        'notes': 'Original source archives, dependency sources, patches, build scripts, runtime source and real-data provenance are included. Compiler toolchains must be installed separately.',
    }), 0o644)
    entries['release.json'] = (json_bytes({
        'schemaVersion': 1, 'tool': tool, 'upstreamVersion': lock['version'], 'upstreamRevision': lock['revision'],
        'emscripten': lock['emscripten'], 'assets': assets,
        'validationStatus': 'candidate: browser release checks still required' if candidate else 'passed for the recorded test scope',
        'testedEngines': sorted(checked),
        'reports': {engine: digest(raw) for engine, (raw, _) in checked.items()},
        'requiresCrossOriginIsolation': tool == 'salmon',
        'notes': 'Reports compare these exact compiled assets and runtime with original native programs on the bundled real-data subsets. They do not establish performance or correctness for every possible input. Numerical comparison tolerances are included.',
    }), 0o644)
    entries['README.md'] = (readme(tool, lock, candidate).encode(), 0o644)
    entries['SHA256SUMS'] = (''.join(f'{digest(content)}  {name}\n' for name, (content, _) in sorted(entries.items())).encode(), 0o644)
    return entries, lock


def readme(tool: str, lock: dict, candidate: bool) -> str:
    title = 'Salmon' if tool == 'salmon' else 'kallisto'
    status = '\nThis is a build candidate. Browser release checks have not yet been included; do not redistribute it as a validated release.\n' if candidate else ''
    isolation = ('Salmon uses worker threads and needs cross-origin isolation. The supplied static server sets the required headers. The page also offers an explicit service-worker setup for static hosting such as GitHub Pages; see `examples/quant/README.md`. '
                 if tool == 'salmon' else 'kallisto uses one worker and can run on ordinary static hosting without cross-origin isolation. ')
    limits = ('\nThe kallisto validation scope excludes bootstrap replicate equivalence. In fixed-seed runs on both bundled datasets, the main abundance table agreed exactly while bootstrap replicate tables differed between native and WebAssembly builds. See `tools/kallisto/README.md` before using those replicates.\n'
              if tool == 'kallisto' else '')
    args = "['quant', '-i', 'kallisto.idx', '-o', 'out', '--plaintext', '-t', '1', 'reads_1.fastq', 'reads_2.fastq']" if tool == 'kallisto' else "['quant', '-i', 'salmon-index', '-l', 'A', '-1', 'reads_1.fastq', '-2', 'reads_2.fastq', '-o', 'out', '-p', '1', '--deterministic']"
    return f'''# {title} {lock['version']} for WebAssembly

This standalone package contains the actual compiled program, JavaScript runner, browser example, real human and yeast RNA read subsets, native comparison reports, full corresponding sources and licence notices. No other genomics-wasm package is required.
{status}
## Run the example

From this extracted directory, run:

```sh
python3 examples/quant/serve.py
```

Open `http://localhost:8000/examples/quant/`. Load example data, build an index, then quantify reads. Arguments can be edited and original output files can be downloaded. Your own FASTA and FASTQ files can also be added. {isolation}The server serves static files; indexing and quantification take place in the browser.

These small real-data subsets are for checking commands, not for estimating whole-transcriptome expression. Their selection and limitations are recorded in `datasets/rna-quant/README.md`. Start by building an index with this program. Index compatibility with native builds is specific to each port; consult `tools/{tool}/README.md` and the recorded comparisons. Browser memory limits still apply.

## JavaScript API

The runner works in Node.js 20 or newer and in browsers. Keep every file in `dist/{tool}/` together. Each command gets a fresh worker and filesystem, and returns raw bytes:

```js
import {{ ToolRunner }} from './runtime/index.mjs';
const runner = new ToolRunner({{
  moduleUrl: new URL('./dist/{tool}/{tool}.mjs', import.meta.url),
  waitForExit: {'true' if tool == 'salmon' else 'false'},
}});
const result = await runner.run({args}, {{
  files: inputs, // [{{path: 'reads_1.fastq', data: Uint8Array}}, ...] including this build's index
  outputDirectories: ['out'],
  timeoutMs: 300000,
}});
```

Check `result.exitCode` and `result.missingOutputs`. Generated files are in `result.files`; `stdout` and `stderr` are byte arrays. Pass returned index files as inputs to a later command. Use `signal: abortController.signal` to cancel. See `runtime/README.md` for the full interface and `tools/{tool}/README.md` for the port's supported options and limits.

## Rebuild and compare

Install the native prerequisites in `tools/{tool}/README.md` and Emscripten {lock['emscripten']}, then run:

```sh
bash scripts/build-{tool}.sh all
python3 scripts/fetch-quant-test-data.py --verify
node scripts/validate-quant.mjs --tool {tool} --engine node --dataset all
```

The package includes pinned upstream and dependency source archives. Compilers and their system dependencies are installed separately. The build produces the original native program, a patched native program and the WebAssembly program. For browser comparisons, install the exact development dependency from `package.json`, install its browser engines, then use `--engine chromium`, `--engine firefox` or `--engine webkit`. The browser example's integration test is `QUANT_TOOLS={tool} BROWSER=chromium node scripts/test-quant-demo.mjs`.

`validation-reports/` records the tested binary and runtime hashes, inputs, cases, browser versions and comparison outcomes. `validation/quant/tolerances.json` defines the numerical contract; passing means agreement within that contract, not bit-identical floating-point values. Indexes are built separately for each target. Reports cover the supplied subsets, not every possible dataset or option.
{limits}

`python3 scripts/fetch-quant-test-data.py --regenerate` recreates the transcript references from the bundled checksum-pinned source data and copies the bundled original RNA fixture reads. Recreating the original read subsets with `scripts/fetch-rna-test-data.py` additionally needs its stated Python dependencies and public source downloads.

Verify the package files with:

```sh
sha256sum -c SHA256SUMS
```

## Licences

`LICENSE` covers this project's original runtime and infrastructure. It does not replace upstream program, dependency or data licences. Retain `THIRD_PARTY.md`, all tool and dependency notices, source archives, patches and build recipes when redistributing the compiled tool. See `tools/{tool}/README.md` and `datasets/rna-quant/README.md` for details.
'''


def write_package(tool: str, output: Path, reports: list[Path], *, candidate: bool = False) -> Path:
    entries, lock = collect(tool, reports, candidate=candidate)
    output.mkdir(parents=True, exist_ok=True)
    stem = f'{tool}-{lock["version"]}-wasm'
    destination = output / f'{stem}.zip'
    temporary = destination.with_suffix('.zip.tmp')
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, (content, mode) in sorted(entries.items()):
                info = zipfile.ZipInfo(f'{stem}/{name}', date_time=(1980, 1, 1, 0, 0, 0))
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
    print(f'{destination}: {len(entries)} files, {destination.stat().st_size} bytes')
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tool', choices=(*TOOLS, 'all'), default='all')
    parser.add_argument('--output', type=Path, default=ROOT / 'releases')
    parser.add_argument('--validation-report', action='append', type=Path, required=True)
    parser.add_argument('--candidate', action='store_true', help='Require Node comparisons only; mark the package as a build candidate')
    options = parser.parse_args()
    requested = TOOLS if options.tool == 'all' else (options.tool,)
    # Check every requested tool before replacing any existing package.
    for tool in requested:
        collect(tool, options.validation_report, candidate=options.candidate)
    packages = [write_package(tool, options.output.resolve(), options.validation_report, candidate=options.candidate) for tool in requested]
    for path in packages:
        path.with_suffix('.zip.sha256').write_text(f'{digest(path.read_bytes())}  {path.name}\n')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, KeyError, OSError) as error:
        print(f'Packaging failed: {error}', file=sys.stderr)
        raise SystemExit(1)
