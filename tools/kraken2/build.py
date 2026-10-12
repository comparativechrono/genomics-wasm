#!/usr/bin/env python3
"""Build the pinned upstream Kraken2 programs and their portable native oracle."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / 'tools/kraken2'
DIST = ROOT / 'dist/kraken2'
LOCK = json.loads((TOOL / 'sources.lock.json').read_text())
PROGRAMS = LOCK['programs']


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inputs():
    paths = [ROOT / 'scripts/build-kraken2.sh', *sorted(TOOL.rglob('*'))]
    return {str(p.relative_to(ROOT)): sha(p) for p in paths
            if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc', '.pyo')}


def run(command, cwd, env):
    print('+ ' + ' '.join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), cwd=cwd, env=env, check=True)


def source_archive():
    archive = ROOT / 'build/downloads' / LOCK['archive']
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        saved = DIST / 'sources' / LOCK['archive']
        if saved.exists():
            shutil.copyfile(saved, archive)
        else:
            temporary = archive.with_suffix('.partial')
            with urllib.request.urlopen(LOCK['url'], timeout=180) as response, temporary.open('wb') as out:
                shutil.copyfileobj(response, out)
            temporary.replace(archive)
    if sha(archive) != LOCK['sha256']:
        raise RuntimeError('Kraken2 source archive checksum mismatch')
    return archive


def build(kind, work, archive, env):
    directory = work / kind
    directory.mkdir()
    with tarfile.open(archive) as bundle:
        bundle.extractall(directory, filter='data')
    source = directory / 'kraken2-2.17.2'
    src = source / 'src'
    if kind != 'native':
        run(['patch', '--batch', '--fuzz=0', '-p1', '-i', TOOL / 'browser.patch'], source, env)
    wasm = kind == 'wasm'
    compiler = 'em++' if wasm else 'c++'
    flags = '-std=c++11 -O3 -DLINEAR_PROBING -fexceptions'
    if wasm:
        flags += ' -sMEMORY64=2'
    elif kind == 'native':
        flags += ' -fopenmp'
    # This is a compile-time layout assertion, not a replacement database format.
    run([compiler, *flags.split(), '-I', src, '-c', TOOL / 'abi-check.cc', '-o', directory / 'abi-check.o'], source, env)
    run(['make', '-j2', f'CXX={compiler}', f'CXXFLAGS={flags}', *PROGRAMS], src, env) if not wasm else None
    if wasm:
        # Ask the unchanged upstream Makefile to select and compile each object's
        # dependency graph, then link its same object lists to ES modules.
        object_map = {
            'classify': 'classify reports hyperloglogplus mmap_file compact_hash taxonomy seqreader mmscanner omp_hack aa_translate utilities',
            'build_db': 'build_db mmap_file compact_hash taxonomy seqreader mmscanner omp_hack utilities',
            'estimate_capacity': 'estimate_capacity seqreader mmscanner omp_hack utilities',
            'dump_table': 'dump_table mmap_file compact_hash omp_hack taxonomy reports hyperloglogplus',
            'lookup_accession_numbers': 'lookup_accession_numbers mmap_file omp_hack utilities',
        }
        objects = sorted({x + '.o' for group in object_map.values() for x in group.split()})
        run(['make', '-j2', f'CXX={compiler}', f'CXXFLAGS={flags}', *objects], src, env)
        link = ['-sMEMORY64=2', '-sDISABLE_EXCEPTION_CATCHING=0', '-sMODULARIZE=1', '-sEXPORT_ES6=1',
                '-sINVOKE_RUN=0', '-sEXPORTED_RUNTIME_METHODS=FS,callMain',
                '-sINCOMING_MODULE_JS_API=preRun,noInitialRun,stdin,stdout,stderr,onExit,quit,locateFile,wasmBinary,print,printErr,onAbort,thisProgram',
                '-sENVIRONMENT=web,worker,node', '-sALLOW_MEMORY_GROWTH=1', '-sABORTING_MALLOC=1',
                '-sINITIAL_MEMORY=67108864', '-sMAXIMUM_MEMORY=2147483648', '-sSTACK_SIZE=8388608',
                '-sEXIT_RUNTIME=1', '-sFORCE_FILESYSTEM=1', '-Wl,--fatal-warnings']
        for name, group in object_map.items():
            run([compiler, *flags.split(), *[x + '.o' for x in group.split()], *link, '-o', directory / (name + '.mjs')], src, env)
        return directory
    package = directory / 'kraken2-package'
    package.mkdir()
    for name in PROGRAMS:
        shutil.copy2(src / name, package / name)
    for original in sorted((source / 'scripts').iterdir()):
        if not original.is_file():
            continue
        # These are the substitutions made by upstream install_kraken2.sh.
        # Upstream's own fallback resolves the directory after relocation.
        content = original.read_text().replace('#####=KRAKEN2_DIR=#####', str(ROOT / 'build' / kind / 'kraken2-package'))
        content = content.replace('#####=VERSION=#####', (source / 'VERSION').read_text().strip())
        target = package / original.name
        target.write_text(content)
        target.chmod(0o755 if original.stat().st_mode & 0o111 else 0o644)
    return directory


def publish_native(kind, directory):
    base = ROOT / 'build' / kind
    base.mkdir(parents=True, exist_ok=True)
    target = base / 'kraken2-package'
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(directory / 'kraken2-package', target)
    launcher = base / 'kraken2'
    launcher.write_text('#!/usr/bin/env bash\nset -euo pipefail\nexec "$(dirname "$0")/kraken2-package/kraken2" "$@"\n')
    launcher.chmod(0o755)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('all', 'native', 'patched-native', 'wasm'), nargs='?', default='all')
    mode = parser.parse_args().mode
    before = inputs()
    archive = source_archive()
    work = ROOT / 'build/kraken2' / uuid.uuid4().hex
    (work / 'tmp').mkdir(parents=True)
    env = {**os.environ, 'TMPDIR': str(work / 'tmp'), 'SOURCE_DATE_EPOCH': '1759795200'}
    if mode in ('all', 'wasm'):
        version = subprocess.check_output(['emcc', '--version'], text=True, env=env)
        if LOCK['emscripten'] not in version.splitlines()[0]:
            raise RuntimeError('Expected Emscripten ' + LOCK['emscripten'])
    built = {}
    for kind in ('native', 'patched-native', 'wasm'):
        if mode in ('all', kind):
            built[kind] = build(kind, work, archive, env)
    if inputs() != before:
        raise RuntimeError('Build inputs changed during compilation')
    for kind in ('native', 'patched-native'):
        if kind in built:
            publish_native(kind, built[kind])
    if 'wasm' not in built:
        return
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)
    for name in PROGRAMS:
        for suffix in ('.mjs', '.wasm'):
            shutil.copy2(built['wasm'] / (name + suffix), DIST / (name + suffix))
    (DIST / 'sources').mkdir()
    shutil.copy2(archive, DIST / 'sources' / archive.name)
    (DIST / 'notices').mkdir()
    for name in LOCK['requiredNotices']:
        shutil.copy2(TOOL / name, DIST / 'notices' / name)
    manifest = {'schema': 1, 'tool': 'kraken2', 'inputs': before,
                'assets': {str(p.relative_to(DIST)): sha(p) for p in sorted(DIST.rglob('*')) if p.is_file()},
                'nativeFiles': {}}
    for kind in ('native', 'patched-native'):
        base = ROOT / 'build' / kind
        paths = [base / 'kraken2', *sorted((base / 'kraken2-package').rglob('*'))]
        manifest['nativeFiles'][kind] = {str(p.relative_to(base)): sha(p) for p in paths if p.is_file()}
    (DIST / 'build-manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')


if __name__ == '__main__':
    main()
