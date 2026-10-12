#!/usr/bin/env python3
"""Build pinned HMMER offline programs without replacing upstream algorithms."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tarfile
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / 'tools/hmmer'
DIST = ROOT / 'dist/hmmer'
LOCK = json.loads((TOOL / 'sources.lock.json').read_text())
PROGRAMS = LOCK['programs']


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inputs():
    paths = [ROOT / 'scripts/build-hmmer.sh', *sorted(TOOL.rglob('*'))]
    return {str(p.relative_to(ROOT)): sha(p) for p in paths
            if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc', '.pyo')}


def run(command, cwd, env):
    print('+ ' + shlex.join(map(str, command)), flush=True)
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
        raise RuntimeError('HMMER source archive checksum mismatch')
    return archive


def build(kind, work, archive, base_env):
    directory = work / kind
    directory.mkdir()
    with tarfile.open(archive) as bundle:
        bundle.extractall(directory, filter='data')
    source = directory / 'hmmer-3.4'
    if kind != 'native':
        run(['patch', '--batch', '--fuzz=0', '-p1', '-i', TOOL / 'browser.patch'], source, base_env)
    wasm = kind == 'wasm'
    compiler = 'emcc' if wasm else base_env.get('CC', 'cc')
    # IEEE floating-point behavior, no fast-math or contraction, same upstream
    # serial SSE2 implementation in all variants. No algorithm selection patch.
    flags = ['-O3', '-ffp-contract=off']
    if wasm:
        flags += ['-msimd128', '-msse2', '-I' + str(TOOL / 'compat')]
    env = {**base_env, 'CC': compiler, 'CFLAGS': ' '.join(flags)}
    if wasm:
        env.update(AR='emar', RANLIB='emranlib', ac_cv_func_popen='no')
    configure = ['./configure', '--disable-threads', '--disable-mpi', '--enable-sse']
    if wasm:
        configure = ['emconfigure', *configure, '--host=wasm32-unknown-emscripten']
    run(configure, source, env)
    # The serialized profile format stores off_t and raw SSE vectors. Assert
    # compatibility before producing binaries, independently of scientific tests.
    run([compiler, *flags, '-std=c11', '-c', TOOL / 'abi-check.c', '-o', directory / 'abi-check.o'], source, env)
    jobs = base_env.get('JOBS', '2')
    run(['make', '-j' + jobs, '-C', 'easel', 'libeasel.a'], source, env)
    run(['make', '-j' + jobs, '-C', 'libdivsufsort', 'libdivsufsort.a'], source, env)
    if not wasm:
        run(['make', '-j' + jobs, '-C', 'src', *PROGRAMS], source, env)
        return source / 'src'
    # Preserve upstream Makefile dependency graphs. Link the same main objects
    # and static libraries as each upstream executable, using the worker ABI.
    run(['make', '-j' + jobs, '-C', 'src', 'libhmmer.a', *[p + '.o' for p in PROGRAMS]], source, env)
    link = ['-sMODULARIZE=1', '-sEXPORT_ES6=1', '-sEXPORT_NAME=createHmmer',
            '-sINVOKE_RUN=0', '-sEXPORTED_RUNTIME_METHODS=FS,callMain',
            '-sINCOMING_MODULE_JS_API=preRun,noInitialRun,stdin,stdout,stderr,onExit,quit,locateFile,wasmBinary,print,printErr,onAbort,thisProgram',
            '-sENVIRONMENT=web,worker,node', '-sALLOW_MEMORY_GROWTH=1', '-sABORTING_MALLOC=1',
            '-sINITIAL_MEMORY=67108864', '-sMAXIMUM_MEMORY=2147483648', '-sSTACK_SIZE=8388608',
            '-sEXIT_RUNTIME=1', '-sFORCE_FILESYSTEM=1', '-Wl,--fatal-warnings']
    for name in PROGRAMS:
        pre_js = directory / (name + '-program-name.js')
        pre_js.write_text('Module["thisProgram"] = Module["thisProgram"] || ' + json.dumps(name) + ';\n')
        run([compiler, *flags, name + '.o', 'libhmmer.a', '../easel/libeasel.a',
             '../libdivsufsort/libdivsufsort.a', '-lm', *link, '--pre-js', pre_js,
             '-o', directory / (name + '.mjs')], source / 'src', env)
    return directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('all', 'native', 'patched-native', 'wasm'), nargs='?', default='all')
    mode = parser.parse_args().mode
    before = inputs()
    archive = source_archive()
    work = ROOT / 'build/hmmer' / uuid.uuid4().hex
    (work / 'tmp').mkdir(parents=True)
    env = {**os.environ, 'TMPDIR': str(work / 'tmp'), 'SOURCE_DATE_EPOCH': '1692057600'}
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
            target = ROOT / 'build' / kind / 'hmmer'
            if target.exists():
                shutil.rmtree(target)
            target.mkdir(parents=True)
            for name in PROGRAMS:
                shutil.copy2(built[kind] / name, target / name)
    if 'wasm' not in built:
        return
    native_files = {}
    for kind in ('native', 'patched-native'):
        base = ROOT / 'build' / kind
        native_files[kind] = {str((Path('hmmer') / p)): sha(base / 'hmmer' / p) for p in PROGRAMS}
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
    manifest = {'schema': 1, 'tool': 'hmmer', 'inputs': before,
                'assets': {str(p.relative_to(DIST)): sha(p) for p in sorted(DIST.rglob('*')) if p.is_file()},
                'nativeFiles': native_files}
    (DIST / 'build-manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')


if __name__ == '__main__':
    main()
