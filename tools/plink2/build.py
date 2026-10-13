#!/usr/bin/env python3
"""Build pinned PLINK2 NOLAPACK sources with native comparison executables."""
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
TOOL = ROOT / 'tools/plink2'
DIST = ROOT / 'dist/plink2'
LOCK = json.loads((TOOL / 'sources.lock.json').read_text())
JOBS = int(os.environ.get('JOBS', '2'))
ZLIB_UNITS = 'adler32 compress crc32 deflate gzclose gzlib gzread gzwrite infback inffast inflate inftrees trees uncompr zutil'.split()


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inputs():
    paths = [ROOT / 'scripts/build-plink2.sh', *TOOL.rglob('*')]
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


def upstream_units(source, directory, env):
    # Ask the pinned upstream Makefile for its complete CLI source list.
    # This prevents a handwritten list from silently omitting science modules.
    query = directory / 'source-list.mk'
    query.write_text('include Makefile.src\nprint:\n\t@printf "%s\\n" $(CSRC) $(ZCSRC) $(ZSSRC) $(CCSRC)\n')
    result = subprocess.check_output(['make', '-s', '-f', str(query), 'print'],
                                     cwd=source / '2.0', env=env, text=True)
    names = result.splitlines()
    if len(names) != 89 or len(set(names)) != len(names):
        raise RuntimeError('Pinned upstream source list changed unexpectedly')
    paths = [source / '2.0' / name for name in names]
    if any(not p.is_file() or source.resolve() not in p.resolve().parents for p in paths):
        raise RuntimeError('Invalid upstream source path')
    return paths


def build(kind, work, archives, env):
    directory = work / kind
    source = directory / 'source'
    zlib = directory / 'zlib'
    extract(archives['plink2'], source)
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
    flags = ['-O2', '-ffp-contract=off', '-pthread', '-ffunction-sections', '-fdata-sections']
    common = ['-DNOLAPACK', '-DZSTD_MULTITHREAD', '-I' + str(source / '2.0/libdeflate'),
              '-I' + str(source / '2.0/include'), '-I' + str(zlib)]
    if kind != 'native':
        common += ['-DPLINK2_WASM_PORT=1']
    if wasm:
        flags += ['-msimd128', '-msse']
    units = []
    for path in upstream_units(source, directory, env):
        is_cpp = path.suffix == '.cc'
        # The pinned NOLAPACK header calls fabs without including math.h.
        # Supply the standard declaration identically without editing native source.
        extra = ['-std=c++17', '-include', 'math.h'] if is_cpp else ([] if path.suffix == '.S' else ['-std=gnu99'])
        name = path.relative_to(source / '2.0').as_posix().replace('/', '_') + '.o'
        units.append((path, objects / name, cxx if is_cpp else cc, common + extra))
    units.append((TOOL / 'abi-check.cpp', objects / 'abi-check.o', cxx, common + ['-std=c++17']))
    units += [(zlib / (name + '.c'), objects / ('zlib-' + name + '.o'), cc,
               ['-std=gnu99', '-DHAVE_UNISTD_H', '-I' + str(zlib)]) for name in ZLIB_UNITS]

    def compile_unit(unit):
        path, output, command, unit_flags = unit
        run([*command, *flags, *unit_flags, '-c', path, '-o', output], env=env)

    with ThreadPoolExecutor(max_workers=JOBS) as pool:
        list(pool.map(compile_unit, units))
    executable = directory / ('plink2.mjs' if wasm else 'plink2')
    link = ['-O2', '-ffp-contract=off', '-pthread']
    if wasm:
        link += ['-msimd128', '-msse', '--js-library', TOOL / 'mailbox.js',
                 '-sPROXY_TO_PTHREAD=1', '-sMODULARIZE=1', '-sEXPORT_ES6=1',
                 '-sEXPORT_NAME=createPlink2', '-sINVOKE_RUN=0',
                 '-sEXPORTED_RUNTIME_METHODS=FS,callMain,PThread',
                 '-sINCOMING_MODULE_JS_API=preRun,noInitialRun,stdin,stdout,stderr,onExit,quit,locateFile,wasmBinary,print,printErr,onAbort,thisProgram,mainScriptUrlOrBlob,instantiateWasm',
                 '-sENVIRONMENT=web,worker,node', '-sALLOW_MEMORY_GROWTH=1',
                 '-sABORTING_MALLOC=0', '-sINITIAL_MEMORY=67108864',
                 '-sMAXIMUM_MEMORY=2147483648', '-sSTACK_SIZE=8388608',
                 '-sDEFAULT_PTHREAD_STACK_SIZE=8388608', '-sPTHREAD_POOL_SIZE=8',
                 '-sPTHREAD_POOL_SIZE_STRICT=2', '-sEXIT_RUNTIME=1',
                 '-sFORCE_FILESYSTEM=1', '-sASSERTIONS=1', '-Wl,--fatal-warnings']
    else:
        link += ['-Wl,--gc-sections']
    run([*cxx, *link, *[output for _, output, _, _ in units], '-lm', '-o', executable], env=env)
    return executable, compiler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('all', 'native', 'patched-native', 'wasm'), nargs='?', default='all')
    mode = parser.parse_args().mode
    before = inputs()
    archives = {'plink2': archive(LOCK), 'zlib': archive(LOCK['dependencies']['zlib'])}
    work = ROOT / 'build/plink2' / uuid.uuid4().hex
    (work / 'tmp').mkdir(parents=True)
    env = {**os.environ, 'TMPDIR': str(work / 'tmp')}
    built = {}
    for kind in ('native', 'patched-native', 'wasm'):
        if mode in ('all', kind):
            executable, compiler = build(kind, work, archives, env)
            if inputs() != before:
                raise RuntimeError('Build inputs changed during compilation; rerun for a consistent manifest')
            if kind != 'wasm':
                target = ROOT / 'build' / kind / 'plink2'
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(executable, target)
            built[kind] = (executable, compiler)
    if 'wasm' not in built:
        return
    executable, compiler = built['wasm']
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)
    for path in sorted(executable.parent.glob('plink2.*')):
        if path.is_file() and path.suffix in ('.mjs', '.js', '.wasm'):
            shutil.copy2(path, DIST / path.name)
    (DIST / 'sources').mkdir()
    for path in archives.values():
        shutil.copy2(path, DIST / 'sources' / path.name)
    (DIST / 'notices').mkdir()
    for name in LOCK['requiredNotices']:
        shutil.copy2(TOOL / name, DIST / 'notices' / name)
    manifest = {'schema': 1, 'tool': 'plink2', 'version': LOCK['version'],
                'revision': LOCK['revision'], 'profile': LOCK['profile'],
                'compiler': compiler, 'inputs': before,
                'assets': {p.relative_to(DIST).as_posix(): sha(p)
                           for p in sorted(DIST.rglob('*')) if p.is_file()}, 'nativeFiles': {}}
    for kind in ('native', 'patched-native'):
        path = ROOT / 'build' / kind / 'plink2'
        if path.is_file():
            manifest['nativeFiles'][kind] = {'plink2': sha(path)}
    (DIST / 'build-manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')


if __name__ == '__main__':
    main()
