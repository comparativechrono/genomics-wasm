#!/usr/bin/env python3
"""Build original bustools and pinned zlib with native comparison executables."""
from concurrent.futures import ThreadPoolExecutor
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tarfile
import uuid

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / 'tools/bustools'
DIST = ROOT / 'dist/bustools'
LOCK = json.loads((TOOL / 'sources.lock.json').read_text())
JOBS = int(os.environ.get('JOBS', '2'))
ZLIB_UNITS = 'adler32 compress crc32 deflate gzclose gzlib gzread gzwrite infback inffast inflate inftrees trees uncompr zutil'.split()


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inputs():
    paths = [ROOT / 'scripts/build-bustools.sh', *TOOL.rglob('*')]
    return {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(paths)
            if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc', '.pyo')}


def run(command, *, cwd=None, env=None):
    print('+ ' + shlex.join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), cwd=cwd, env=env, check=True)


def archive(lock):
    target = ROOT / 'build/downloads' / lock['archive']
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        saved = DIST / 'sources' / lock['archive']
        if saved.is_file():
            shutil.copyfile(saved, target)
        else:
            temporary = target.with_suffix('.partial')
            run(['curl', '--fail', '--location', '--retry', '3', '--max-time', '300',
                 lock['url'], '-o', temporary])
            if sha(temporary) != lock['sha256']:
                raise RuntimeError('Source archive checksum mismatch: ' + lock['archive'])
            temporary.replace(target)
    if sha(target) != lock['sha256']:
        raise RuntimeError('Source archive checksum mismatch: ' + lock['archive'])
    return target


def extract(archive_path, destination):
    destination.mkdir(parents=True)
    with tarfile.open(archive_path) as bundle:
        roots = {m.name.split('/')[0] for m in bundle.getmembers() if m.name.strip('/')}
        if len(roots) != 1:
            raise RuntimeError('Unexpected source archive structure')
        prefix = roots.pop() + '/'
        for member in bundle.getmembers():
            if not member.name.startswith(prefix) or member.name == prefix:
                continue
            member.name = member.name[len(prefix):]
            bundle.extract(member, destination, filter='data')


def build(kind, work, archives, env):
    directory = work / kind
    source = directory / 'source'
    zlib = directory / 'zlib'
    extract(archives['bustools'], source)
    extract(archives['zlib'], zlib)
    if kind != 'native':
        run(['patch', '--batch', '--fuzz=0', '-p1', '-i', TOOL / 'browser.patch'], cwd=source, env=env)
    wasm = kind == 'wasm'
    cc = ['emcc'] if wasm else shlex.split(env.get('CC', 'cc'))
    cxx = ['em++'] if wasm else shlex.split(env.get('CXX', 'c++'))
    compiler = subprocess.check_output(cxx + ['--version'], env=env, text=True).splitlines()[0]
    if wasm and LOCK['emscripten'] not in compiler:
        raise RuntimeError('Expected Emscripten ' + LOCK['emscripten'])
    objects = directory / 'objects'
    objects.mkdir()
    flags = ['-O2', '-ffp-contract=off', '-ffunction-sections', '-fdata-sections']
    includes = ['-I' + str(source / 'src'), '-I' + str(zlib),
                '-I' + str(source / 'dependencies/CppNumericalSolvers-v1/include'),
                '-I' + str(source / 'dependencies/eigen-3.3.7'),
                '-I' + str(source / 'dependencies/LBFGSpp-2020-03-06/include')]
    # EIGEN_MPL2_ONLY is a license guard, not an algorithm or optimization change.
    cpp_flags = ['-std=c++11', '-DEIGEN_MPL2_ONLY', *includes]
    if wasm:
        cpp_flags += ['-fexceptions']
    if kind != 'native':
        cpp_flags += ['-DBUSTOOLS_WASM_PORT=1']
    if not wasm:
        cpp_flags += ['-pthread']
    units = [(path, objects / (path.stem + '.o'), cxx, cpp_flags)
             for path in sorted((source / 'src').glob('*.cpp'))]
    units += [(TOOL / 'abi-check.cpp', objects / 'abi-check.o', cxx, cpp_flags)]
    units += [(source / 'src/roaring.c', objects / 'roaring.o', cc,
               ['-std=c99', '-I' + str(source / 'src')])]
    units += [(zlib / (name + '.c'), objects / ('zlib-' + name + '.o'), cc,
               ['-std=c99', '-DHAVE_UNISTD_H', '-I' + str(zlib)]) for name in ZLIB_UNITS]

    def compile_unit(unit):
        path, output, command, unit_flags = unit
        run([*command, *flags, *unit_flags, '-c', path, '-o', output], env=env)

    with ThreadPoolExecutor(max_workers=JOBS) as pool:
        list(pool.map(compile_unit, units))
    executable = directory / ('bustools.mjs' if wasm else 'bustools')
    link = ['-O2', '-ffp-contract=off']
    if wasm:
        link += ['-fexceptions', '-sMODULARIZE=1', '-sEXPORT_ES6=1', '-sEXPORT_NAME=createBustools',
                 '-sINVOKE_RUN=0', '-sEXPORTED_RUNTIME_METHODS=FS,callMain',
                 '-sINCOMING_MODULE_JS_API=preRun,noInitialRun,stdin,stdout,stderr,onExit,quit,locateFile,wasmBinary,print,printErr,onAbort,thisProgram',
                 '-sENVIRONMENT=web,worker,node', '-sALLOW_MEMORY_GROWTH=1',
                 '-sABORTING_MALLOC=1', '-sINITIAL_MEMORY=67108864',
                 '-sMAXIMUM_MEMORY=2147483648', '-sSTACK_SIZE=8388608',
                 '-sEXIT_RUNTIME=1', '-sFORCE_FILESYSTEM=1', '-Wl,--fatal-warnings',
                 '--pre-js', TOOL / 'pre.js']
    else:
        link += ['-pthread', '-Wl,--gc-sections']
    run([*cxx, *link, *[output for _, output, _, _ in units], '-lm', '-o', executable], env=env)
    return executable, compiler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('all', 'native', 'patched-native', 'wasm'), nargs='?', default='all')
    mode = parser.parse_args().mode
    before = inputs()
    archives = {'bustools': archive(LOCK), 'zlib': archive(LOCK['dependencies']['zlib'])}
    work = ROOT / 'build/bustools' / uuid.uuid4().hex
    (work / 'tmp').mkdir(parents=True)
    env = {**os.environ, 'TMPDIR': str(work / 'tmp')}
    built = {}
    for kind in ('native', 'patched-native', 'wasm'):
        if mode in ('all', kind):
            executable, compiler = build(kind, work, archives, env)
            if inputs() != before:
                raise RuntimeError('Build inputs changed during compilation; rerun for a consistent manifest')
            if kind != 'wasm':
                target = ROOT / 'build' / kind / 'bustools'
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(executable, target)
            built[kind] = (executable, compiler)
    if 'wasm' not in built:
        return
    executable, compiler = built['wasm']
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)
    for path in (executable, executable.with_suffix('.wasm')):
        shutil.copy2(path, DIST / path.name)
    (DIST / 'sources').mkdir()
    for path in archives.values():
        shutil.copy2(path, DIST / 'sources' / path.name)
    (DIST / 'notices').mkdir()
    for name in LOCK['requiredNotices']:
        shutil.copy2(TOOL / name, DIST / 'notices' / name)
    manifest = {'schema': 1, 'tool': 'bustools', 'version': LOCK['version'],
                'revision': LOCK['revision'], 'compiler': compiler, 'inputs': before,
                'assets': {p.relative_to(DIST).as_posix(): sha(p)
                           for p in sorted(DIST.rglob('*')) if p.is_file()}, 'nativeFiles': {}}
    for kind in ('native', 'patched-native'):
        path = ROOT / 'build' / kind / 'bustools'
        if path.is_file():
            manifest['nativeFiles'][kind] = {'bustools': sha(path)}
    (DIST / 'build-manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')


if __name__ == '__main__':
    main()
