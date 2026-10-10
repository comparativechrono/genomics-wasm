#!/usr/bin/env python3
"""Create self-contained, source-inclusive ZIP packages for built tools."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
COMPILED_SUFFIXES = {'.o', '.a', '.so', '.dylib', '.dll', '.exe', '.wasm', '.pyc'}
TOOLS = ('bwa', 'bwa-mem2', 'freebayes', 'adapterremoval', 'seqtk', 'hisat2', 'minimap2')
DISPLAY_NAMES = {'bwa': 'BWA', 'bwa-mem2': 'BWA-MEM2', 'freebayes': 'FreeBayes',
                 'adapterremoval': 'AdapterRemoval', 'seqtk': 'seqtk', 'hisat2': 'HISAT2', 'minimap2': 'minimap2'}
HELP_ARGS = {'bwa': '[]', 'bwa-mem2': '[]', 'freebayes': "['--help']", 'adapterremoval': "['--help']",
             'seqtk': '[]', 'hisat2': "['align', '--help']", 'minimap2': "['--help']"}
NOTICES = {
    'bwa': ('COPYING', 'LICENSE.zlib'),
    'bwa-mem2': ('LICENSE', 'LICENSE.safestringlib', 'LICENSE-COPYING.safestringlib.txt', 'LICENSE.zlib'),
    'adapterremoval': ('LICENSE', 'LICENSE.zlib', 'LICENSE.bzip2'),
    'seqtk': ('LICENSE', 'LICENSE.zlib'),
    'hisat2': ('LICENSE', 'THIRD_PARTY_LICENSES.txt'),
    'minimap2': ('LICENSE.txt', 'LICENSE.zlib'),
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()


def required(path: Path) -> bytes:
    if not path.is_file():
        raise RuntimeError(f'Required release input is missing: {path.relative_to(ROOT)}')
    return path.read_bytes()


def verify_archive(filename: str, expected: str) -> Path:
    path = ROOT / 'build/downloads' / filename
    actual = sha256(required(path))
    if actual != expected:
        raise RuntimeError(f'Checksum mismatch for {path.relative_to(ROOT)}: {actual}')
    return path


def freebayes_sources(lock: dict) -> list[Path]:
    source = ROOT / 'build/cache/freebayes-upstream'
    if not source.is_dir():
        raise RuntimeError('FreeBayes recursive source tree is missing; run its build first')
    if (source / '.git').exists():
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
        if head != lock['revision']:
            raise RuntimeError(f'FreeBayes source revision mismatch: {head}')
        submodules = subprocess.check_output(['git', 'submodule', 'status', '--recursive'], cwd=source, text=True)
        if any(line[0] != ' ' for line in submodules.splitlines()):
            raise RuntimeError('FreeBayes source submodules are missing or modified')
        pins = {line.split()[1]: line.split()[0] for line in submodules.splitlines()}
        if pins != lock['submodules']:
            raise RuntimeError('FreeBayes recursive dependency revisions differ from upstream.lock.json')
        subprocess.run(['git', 'diff', '--exit-code', '--ignore-submodules=none', 'HEAD'], cwd=source, check=True)
        tracked = subprocess.check_output(['git', 'ls-files', '--recurse-submodules', '-z'], cwd=source)
        paths = [source / os.fsdecode(name) for name in tracked.split(b'\0') if name]
    else:
        # Repackaging an extracted release does not require Git metadata.
        manifest = json.loads(required(ROOT / 'source-manifest.json'))
        if manifest.get('upstreamLockSha256') != sha256(required(ROOT / 'tools/freebayes/upstream.lock.json')):
            raise RuntimeError('The bundled source manifest does not match the FreeBayes lock file')
        prefix = 'build/cache/freebayes-upstream/'
        recorded = {name: digest for name, digest in manifest['files'].items() if name.startswith(prefix)}
        present = {path.relative_to(ROOT).as_posix() for path in source.rglob('*') if path.is_file() and '.git' not in path.relative_to(source).parts}
        if set(recorded) != present:
            raise RuntimeError('Bundled FreeBayes source files do not match source-manifest.json')
        paths = [ROOT / name for name in sorted(recorded)]
        for path in paths:
            if sha256(required(path)) != recorded[path.relative_to(ROOT).as_posix()]:
                raise RuntimeError(f'Bundled source was changed: {path.relative_to(ROOT)}')
    for path in paths:
        if '.git' in path.relative_to(source).parts or path.suffix in COMPILED_SUFFIXES:
            raise RuntimeError(f'Unexpected compiled or Git metadata file in upstream source: {path.relative_to(ROOT)}')
        # The upstream documentation symlink is stored as its file contents,
        # so extraction does not need symbolic-link privileges.
        if not path.resolve().is_relative_to(source.resolve()):
            raise RuntimeError(f'Upstream source symlink escapes its source tree: {path}')
    return paths


def collect(tool: str) -> tuple[dict[str, tuple[bytes, int]], dict]:
    if tool not in TOOLS:
        raise ValueError(f'Unknown tool: {tool}')
    lock_name = 'upstream.lock.json' if tool == 'freebayes' else 'sources.lock.json'
    lock_path = ROOT / f'tools/{tool}/{lock_name}'
    lock = json.loads(required(lock_path))
    required(ROOT / f'tools/{tool}/README.md')
    module = required(ROOT / f'dist/{tool}/{tool}.mjs')
    wasm = required(ROOT / f'dist/{tool}/{tool}.wasm')
    if not module or not wasm.startswith(b'\x00asm\x01\x00\x00\x00'):
        raise RuntimeError(f'{tool}: release requires a nonempty ES module and valid WebAssembly binary header')
    entries: dict[str, tuple[bytes, int]] = {}
    source_hashes: dict[str, str] = {}

    def add(path: Path, *, source: bool = False) -> None:
        name = path.relative_to(ROOT).as_posix()
        content = required(path)
        entries[name] = (content, 0o755 if path.stat().st_mode & 0o111 else 0o644)
        if source:
            source_hashes[name] = sha256(content)

    for name in ('LICENSE', 'THIRD_PARTY.md', 'scripts/setup-emsdk.sh', f'scripts/build-{tool}.sh', 'scripts/package.py',
                 'tests/runtime.test.mjs', 'tests/fixtures/mock-tool.mjs'):
        add(ROOT / name, source=True)
    for directory in (ROOT / 'runtime', ROOT / f'tools/{tool}'):
        for path in sorted(directory.rglob('*')):
            if path.is_file() and '.git' not in path.parts and '__pycache__' not in path.parts:
                add(path, source=True)
    for path in sorted((ROOT / f'dist/{tool}').rglob('*')):
        if path.is_file() and not path.name.endswith(('.tar.gz', '.tar.xz', '.zip')) and '.git' not in path.parts:
            if path.suffix in COMPILED_SUFFIXES and path.name != f'{tool}.wasm':
                raise RuntimeError(f'Unexpected compiled output in distribution: {path.relative_to(ROOT)}')
            add(path)

    # Keep each package's introductory example specific to its included tool;
    # the common API and runtime tests remain identical across packages.
    api_documentation = required(ROOT / 'runtime/README.md').decode().split('## Interface', 1)[1]
    runtime_readme = ('# JavaScript runner\n\nSee the package README and `examples/help.mjs` for an example using the included tool. '
                      'The runner has no package dependencies and works in browsers and Node.js.\n\n## Interface' + api_documentation).encode()
    entries['runtime/README.md'] = (runtime_readme, 0o644)
    source_hashes['runtime/README.md'] = sha256(runtime_readme)

    if tool != 'freebayes':
        add(verify_archive(f'{tool}-{lock["revision"]}.tar.gz', lock['sha256']), source=True)
        for name, dependency in lock.get('dependencies', {}).items():
            version = dependency.get('revision') or dependency['version']
            filename = dependency.get('archive') or f'{name}-{version}.tar.gz'
            add(verify_archive(filename, dependency['sha256']), source=True)
        # Notice collection is part of the build, not an optional packaging step.
        for name in NOTICES[tool]:
            required(ROOT / f'dist/{tool}/{name}')
    else:
        for path in freebayes_sources(lock):
            add(path, source=True)
        for dependency in ('zlib', 'simde'):
            info = lock['dependencies'][dependency]
            add(verify_archive(f'{dependency}-{info["version"]}.tar.gz', info['sha256']), source=True)
        for notice in ('freebayes', 'htslib', 'htscodecs', 'seqlib', 'intervaltree', 'vcflib', 'tabixpp',
                       'smithwaterman', 'libdisorder', 'fastahack', 'multichoose', 'simde', 'zlib', 'ttmath', 'ssw', 'filevercmp'):
            required(ROOT / f'dist/freebayes/LICENSES/{notice}.txt')

    manifest = {
        'schemaVersion': 1,
        'tool': tool,
        'upstreamVersion': lock['version'],
        'upstreamRevision': lock['revision'],
        'upstreamLockSha256': sha256(required(lock_path)),
        'files': dict(sorted(source_hashes.items())),
        'notes': 'Upstream archives are unchanged. Recursive source files exclude Git metadata. Documentation symlinks are stored as their contents. Build recipes, port patches and original runtime source are included.',
    }
    entries['source-manifest.json'] = (json_bytes(manifest), 0o644)
    entries['README.md'] = (package_readme(tool, lock).encode(), 0o644)
    entries['examples/help.mjs'] = (help_example(tool).encode(), 0o644)
    entries['release.json'] = (json_bytes({
        'schemaVersion': 1, 'tool': tool, 'upstreamVersion': lock['version'],
        'upstreamRevision': lock['revision'], 'emscripten': lock['emscripten'],
        'wasmSha256': sha256(wasm), 'moduleSha256': sha256(module),
        'validation': 'This archive identifies build inputs. Refer to independently published validation reports for tested equivalence; packaging alone does not establish correctness.',
    }), 0o644)
    sums = ''.join(f'{sha256(content)}  {name}\n' for name, (content, _) in sorted(entries.items()))
    entries['SHA256SUMS'] = (sums.encode(), 0o644)
    return entries, lock


def package_readme(tool: str, lock: dict) -> str:
    display = DISPLAY_NAMES[tool]
    help_args = HELP_ARGS[tool]
    tool_command = {
        'bwa': "['mem', '-t', '1', 'reference.fa', 'reads.fastq']",
        'bwa-mem2': "['mem', '-t', '1', 'reference.fa', 'reads.fastq']",
        'freebayes': "['--fasta-reference', 'reference.fa', 'reads.bam']",
        'adapterremoval': "['--file1', 'reads.fastq', '--basename', 'trimmed', '--threads', '1', '--seed', '42', '--trimns', '--trimqualities']",
        'seqtk': "['trimfq', 'reads.fastq']",
        'hisat2': "['align', '-p', '1', '-x', 'reference', '-U', 'reads.fastq', '-S', 'alignment.sam']",
        'minimap2': "['-ax', 'splice:sr', '-t', '1', 'reference.fa', 'reads_1.fastq', 'reads_2.fastq']",
    }[tool]
    input_example = "{path: 'reads.fastq', data: readBytes}" if tool in ('adapterremoval', 'seqtk', 'hisat2') else "{path: 'reference.fa', data: referenceBytes}"
    inputs = {
        'bwa': 'For alignment, first run `index` and retain the reference `.amb`, `.ann`, `.bwt`, `.pac` and `.sa` files. Supply those index files and reads to the next command.',
        'bwa-mem2': 'For alignment, first run this tool\'s `index` command and retain the reference `.0123`, `.amb`, `.ann`, `.bwt.2bit.64` and `.pac` files. Supply those index files and reads to the next command. Original BWA indexes cannot replace the BWA-MEM2 index. This port supports one thread.',
        'freebayes': 'Supply a reference FASTA and its `.fai`, plus a coordinate-sorted BAM and its `.bai`. The caller writes VCF bytes to standard output.',
        'adapterremoval': 'Supply single or paired FASTQ inputs. Request the output FASTQ files and `.settings` report through the runner\'s `outputs` option. Single-read output names with this basename are `trimmed.truncated`, `trimmed.discarded` and `trimmed.settings`. Paired runs instead write `trimmed.pair1.truncated`, `trimmed.pair2.truncated`, `trimmed.singleton.truncated`, `trimmed.discarded` and `trimmed.settings`. This port supports one processing thread.',
        'seqtk': 'The `trimfq` command trims low-quality bases and writes FASTQ to standard output. Use `-b` and `-e` to remove fixed numbers of bases from the start and end. See the tool README for the validation scope of other seqtk commands.',
        'hisat2': 'First run `["build", "reference.fa", "reference"]` and keep all eight `reference.1.ht2` through `reference.8.ht2` index files. Supply the index files and reads to `align`. Request `alignment.sam` through the runner\'s `outputs` option. `inspect` reads an existing index. This port supports small indexes and one alignment thread.',
        'minimap2': 'The `splice:sr` preset aligns short RNA reads and `splice` aligns long RNA reads. The aligner writes SAM to standard output with `-a`. You may supply a reference FASTA directly or first build an index with `["-x", "splice:sr", "-d", "reference.mmi", "reference.fa"]`. An existing index fixes the indexing parameters, so build it with the preset intended for alignment. This port supports one processing thread.',
    }[tool]
    offline = {
        'bwa': 'The build finds its checksum-pinned BWA and zlib archives in `build/downloads/`.',
        'bwa-mem2': 'The build finds its checksum-pinned BWA-MEM2, safestringlib and zlib archives in `build/downloads/`. The complete dependency sources are included.',
        'freebayes': 'The build verifies `source-manifest.json` and uses the bundled recursive source tree in `build/cache/freebayes-upstream/` without Git metadata. zlib and SIMDe archives are in `build/downloads/`.',
        'adapterremoval': 'The build finds its checksum-pinned upstream and dependency archives in `build/downloads/`.',
        'seqtk': 'The build finds its checksum-pinned seqtk and zlib archives in `build/downloads/`.',
        'hisat2': 'The build finds its checksum-pinned HISAT2 source archive, including bundled dependency sources, in `build/downloads/`.',
        'minimap2': 'The build finds its checksum-pinned minimap2 and zlib archives in `build/downloads/`.',
    }[tool]
    help_status = f" {display}'s no-argument usage command normally exits with status 1." if tool in ('bwa', 'bwa-mem2', 'seqtk') else ''
    return f'''# {display} {lock['version']} for WebAssembly

This standalone package contains the compiled {display} tool, a dependency-free JavaScript runner, full corresponding sources, portability changes, build instructions and licence notices. No other genomics-wasm tool package is needed.

## Run a command

Keep the module and its `.wasm` file together. Node.js 20 or newer can run the included help example:

```sh
node examples/help.mjs
```

For a browser, serve the extracted directory over HTTP and import the runner from a module script. This same API also works in Node.js:

```js
import {{ ToolRunner }} from './runtime/index.mjs';
const tool = new ToolRunner({{
  moduleUrl: new URL('./dist/{tool}/{tool}.mjs', import.meta.url),
}});
const result = await tool.run({help_args});
console.log(result.exitCode, new TextDecoder().decode(result.stdout));
console.log(new TextDecoder().decode(result.stderr));
```

For a real command, use an argument array such as `{tool_command}` and supply input bytes through `files: [{input_example}, ...]`. {inputs} The runner creates a fresh worker and filesystem for every command. `stdout`, `stderr` and returned files are raw byte arrays. See `runtime/README.md` for the full file, output, cancellation and timeout interface and `tools/{tool}/README.md` for supported options and limits.{help_status}

## Rebuild from included sources

Install the native build prerequisites listed in `tools/{tool}/README.md`. For the WebAssembly target, separately install and activate Emscripten {lock['emscripten']}; the compiler toolchain itself is not bundled. Then run:

```sh
bash scripts/build-{tool}.sh all
```

Use `native`, `patched-native` or `wasm` instead of `all` for a single target. {offline} With the prerequisites installed, these tool-source inputs require no downloads. `scripts/setup-emsdk.sh` is provided for installing the compiler separately and does require network access.

The source manifest records hashes for upstream source files or archives, port patches, build recipes and runtime code. Verify every packaged file, excluding the checksum list itself, with:

```sh
sha256sum -c SHA256SUMS
```

`release.json` records exact upstream and compiler versions plus hashes of the compiled files. It is a build record, not a claim that every possible input has been validated. Consult the separately published native-versus-WebAssembly validation reports for the tested scope.

## Licences

`LICENSE` is the original repository licence for its infrastructure and runtime. It does not replace the upstream tool and dependency licences. Preserve `THIRD_PARTY.md`, all notices in `tools/{tool}/` and `dist/{tool}/`, the corresponding source files or archives, patches and build recipes when redistributing this package. The compiled tool has the combined upstream licensing obligations described in its tool documentation.
'''


def help_example(tool: str) -> str:
    args = HELP_ARGS[tool]
    return f'''import {{ ToolRunner }} from '../runtime/index.mjs';
const tool = new ToolRunner({{ moduleUrl: new URL('../dist/{tool}/{tool}.mjs', import.meta.url) }});
const result = await tool.run({args}, {{ timeoutMs: 30000 }});
process.stdout.write(result.stdout);
process.stderr.write(result.stderr);
console.error(`\\nTool exit status: ${{result.exitCode}}`);
'''


def write_package(tool: str, output: Path) -> Path:
    entries, lock = collect(tool)
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
            if archive.testzip() is not None:
                raise RuntimeError(f'ZIP integrity verification failed: {destination}')
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    print(f'{destination.relative_to(ROOT) if destination.is_relative_to(ROOT) else destination}: {len(entries)} files, {destination.stat().st_size} bytes')
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tool', choices=(*TOOLS, 'all'), default='all')
    parser.add_argument('--output', type=Path, default=ROOT / 'releases')
    options = parser.parse_args()
    requested = TOOLS if options.tool == 'all' else (options.tool,)
    # Fail before writing any release when required modules are absent.
    for tool in requested:
        required(ROOT / f'dist/{tool}/{tool}.mjs')
        required(ROOT / f'dist/{tool}/{tool}.wasm')
    for tool in requested:
        write_package(tool, options.output.resolve())
    checksums = ''.join(f'{sha256(path.read_bytes())}  {path.name}\n' for path in sorted(options.output.glob('*.zip')))
    (options.output / 'SHA256SUMS').write_text(checksums)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError, OSError, KeyError, ValueError) as error:
        print(f'Packaging failed: {error}', file=sys.stderr)
        raise SystemExit(1)
