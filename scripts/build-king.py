#!/usr/bin/env python3
"""Build KING 2.3.2 locally for personal use; never publish sources or binaries."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import tarfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'tools/king'
LOCK = json.loads((TOOL / 'versions.lock.json').read_text())
KINDS = ('native', 'patched-native', 'wasm')
JOBS = max(1, min(2, int(os.environ.get('JOBS', '2'))))


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def hashes(directory):
    return {p.relative_to(directory).as_posix(): sha(p) for p in sorted(directory.rglob('*'))
            if p.is_file() and not p.is_symlink()}


def inputs():
    paths = [ROOT / 'scripts/build-king.py', ROOT / 'scripts/setup-emsdk.sh', *TOOL.rglob('*')]
    return {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(paths)
            if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc', '.pyo')}


def run(command, *, cwd=None, env=None):
    print('+ ' + shlex.join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), cwd=cwd, env=env, check=True)


def archive(record):
    path = ROOT / 'build/downloads' / record['archive']
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        cached = ROOT / 'build/research/king' / record['archive']
        if cached.is_file():
            shutil.copyfile(cached, path)
        else:
            temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.partial')
            try:
                run(['curl', '--fail', '--location', '--retry', '3', '--max-time', '600',
                     record['url'], '-o', temporary])
                if sha(temporary) != record['sha256'] or temporary.stat().st_size != record['bytes']:
                    raise RuntimeError('Source checksum/size mismatch: ' + path.name)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
    if sha(path) != record['sha256'] or path.stat().st_size != record['bytes']:
        raise RuntimeError('Source checksum/size mismatch: ' + path.name)
    return path


def extract(path, destination, *, strip_root=False):
    destination.mkdir(parents=True)
    with tarfile.open(path) as bundle:
        members = bundle.getmembers()
        roots = {PurePosixPath(m.name).parts[0] for m in members if PurePosixPath(m.name).parts}
        if strip_root and len(roots) != 1:
            raise RuntimeError('Unexpected archive roots: ' + str(path))
        for member in members:
            parts = PurePosixPath(member.name).parts
            if member.name.startswith('/') or '..' in parts or member.issym() or member.islnk():
                raise RuntimeError('Unsafe archive member: ' + member.name)
            if not member.isdir() and not member.isfile():
                raise RuntimeError('Unsupported archive member: ' + member.name)
            if strip_root:
                parts = parts[1:]
            if not parts:
                continue
            member.name = '/'.join(parts)
            bundle.extract(member, destination, filter='data')


def environment(kind, work):
    temporary = work / 'tmp'
    temporary.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, 'TMPDIR': str(temporary), 'LC_ALL': 'C', 'TZ': 'UTC'}
    if kind == 'wasm':
        sdk = Path(env.get('GENOMICS_EMSDK_DIR', ROOT / 'toolchain/emsdk')).resolve()
        if not shutil.which('emcc', path=env.get('PATH')) and (sdk / 'upstream/emscripten/emcc').is_file():
            env['PATH'] = str(sdk / 'upstream/emscripten') + os.pathsep + env.get('PATH', '')
            env['EM_CONFIG'] = str(sdk / '.emscripten')
        env.update(CC='emcc', CXX='em++', AR='emar', RANLIB='emranlib')
    else:
        env.update(CC=env.get('CC', 'cc'), CXX=env.get('CXX', 'c++'),
                   AR=env.get('AR', 'ar'), RANLIB=env.get('RANLIB', 'ranlib'))
    return env


def cflags(kind):
    return ['-O2', '-ffp-contract=off', '-ffunction-sections', '-fdata-sections',
            '-D_FILE_OFFSET_BITS=64', '-D_LARGEFILE_SOURCE'] + ([] if kind == 'wasm' else ['-fPIC'])


def transform(path, pattern, replacement):
    original = path.read_bytes()
    updated, count = re.subn(pattern, replacement, original)
    if count != 1:
        raise RuntimeError(f'Expected exactly one portability edit in {path.name}, found {count}')
    path.write_bytes(updated)


def patch_personal_copy(source):
    # These transformations operate only on the user's downloaded local copy.
    # They preserve every upstream notice and are not a distribution of that copy.
    transform(source / 'Main.cpp', rb'(bool\s+Bit64Flag\s*=\s*)[^;]+;',
              rb'\g<1>sizeof(unsigned long long) == 8;')
    transform(source / 'Main.cpp', rb'bool\s+NoScreenFlag\s*=\s*false;',
              rb'int NoScreenFlag = 0;')
    transform(source / 'Main.cpp', rb'int\s+normalization\s*=\s*0;',
              rb'bool normalization = false;')
    transform(source / 'Main.cpp', rb'(\s+pl\.Status\(\);)',
              rb'\g<1>\n   if (CoreCount != 0 && CoreCount != 1)\n'
              rb'      error("KING browser profile supports --cpus 1 only.");\n'
              rb'   if (rplotFlag || pngFlag || !rpath.IsEmpty())\n'
              rb'      error("KING browser profile cannot launch R or ps2pdf; '
              rb'--rplot, --pngplot, and --rpath are unsupported.");\n')
    transform(source / 'StringBasics.cpp', rb'unsigned long\s+(base\s*=\s*10;)',
              rb'unsigned long long \g<1>')


def check_portability(kind, work, env, cppflags):
    executable = work / ('portability-check.cjs' if kind == 'wasm' else 'portability-check')
    command = [env['CXX'], *cppflags, TOOL / 'portability-check.cpp',
               *[work / 'objects' / (name + '.o') for name in ('StringBasics', 'Error', 'InputFile')],
               work / 'prefix/lib/libz.a', '-o', executable]
    if kind == 'wasm':
        command += ['-sENVIRONMENT=node', '-sEXIT_RUNTIME=1', '-sWASM_ASYNC_COMPILATION=0']
    else:
        command += ['-Wl,--gc-sections']
    run(command, env=env)
    result = subprocess.run(['node', str(executable)] if kind == 'wasm' else [str(executable)],
                            env=env, text=True, capture_output=True, timeout=15, check=True)
    expected = '0\n9\n10\n999999999\n1000000000\n2147483648\n4294967295\n'
    if result.stdout != expected or result.stderr:
        raise RuntimeError('Unexpected KING decimal portability output: ' + repr(result))
    return {'name': 'unsigned-decimal-boundaries', 'cases': 7, 'exitCode': result.returncode,
            'stdout': result.stdout, 'stderr': result.stderr}


def build(kind, work, archives, before):
    env = environment(kind, work)
    description = subprocess.check_output([env['CXX'], '--version'], env=env, text=True).splitlines()[0]
    if kind == 'wasm' and LOCK['emscripten'] not in description:
        raise RuntimeError('Expected Emscripten ' + LOCK['emscripten'] + ', got ' + description)
    flags = cflags(kind)
    prefix = work / 'prefix'
    zlib = work / 'zlib'
    extract(archives['zlib'], zlib, strip_root=True)
    build_env = {**env, 'CFLAGS': shlex.join(flags)}
    run([zlib / 'configure', '--static', '--prefix=' + str(prefix)], cwd=zlib, env=build_env)
    run(['make', '-j' + str(JOBS)], cwd=zlib, env=build_env)
    run(['make', 'install'], cwd=zlib, env=build_env)
    source = work / 'source'
    extract(archives['king'], source)
    main = (source / 'Main.cpp').read_bytes()
    if not re.search(rb'^#define\s+VERSION\s+"2\.3\.2"\s*$', main, re.MULTILINE):
        raise RuntimeError('Unexpected KING source version')
    pristine = hashes(source)
    if kind != 'native':
        patch_personal_copy(source)
    modified = {name: digest for name, digest in hashes(source).items() if pristine.get(name) != digest}
    ompflags = ['-fopenmp'] if kind == 'native' else [
        '-D_OPENMP=201511', '-Wno-unknown-pragmas', '-I' + str(TOOL / 'serial-omp')]
    cppflags = [*flags, *ompflags, '-std=c++11', '-fexceptions', '-D__ZLIB_AVAILABLE__',
                '-I' + str(prefix / 'include'), '-I' + str(source)]
    run([env['CXX'], *cppflags, '-c', TOOL / 'abi-check.cpp', '-o', work / 'abi-check.o'], env=env)
    sources = sorted(source.glob('*.cpp'))
    objects_dir = work / 'objects'
    objects_dir.mkdir()
    objects = [objects_dir / (p.stem + '.o') for p in sources]
    def compile_one(pair):
        src, obj = pair
        run([env['CXX'], *cppflags, '-c', src, '-o', obj], env=env)
    with ThreadPoolExecutor(max_workers=JOBS) as pool:
        list(pool.map(compile_one, zip(sources, objects)))
    executable = work / ('king.mjs' if kind == 'wasm' else 'king')
    link = [env['CXX'], *flags, *ompflags, '-fexceptions', *objects, prefix / 'lib/libz.a', '-lm', '-o', executable]
    if kind == 'wasm':
        pre = work / 'pre.js'
        pre.write_text('Module["thisProgram"] = Module["thisProgram"] || "king";\n')
        link += ['-sMODULARIZE=1', '-sEXPORT_ES6=1', '-sEXPORT_NAME=createKing', '-sINVOKE_RUN=0',
                 '-sEXPORTED_RUNTIME_METHODS=FS,callMain', '-sDISABLE_EXCEPTION_CATCHING=0',
                 '-sINCOMING_MODULE_JS_API=preRun,noInitialRun,stdin,stdout,stderr,onExit,quit,locateFile,wasmBinary,print,printErr,onAbort,thisProgram',
                 '-sENVIRONMENT=web,worker,node', '-sALLOW_MEMORY_GROWTH=1', '-sABORTING_MALLOC=1',
                 '-sINITIAL_MEMORY=67108864', '-sMAXIMUM_MEMORY=2147483648', '-sSTACK_SIZE=8388608',
                 '-sEXIT_RUNTIME=1', '-sFORCE_FILESYSTEM=1', '-Wl,--fatal-warnings', '--pre-js', pre]
    else:
        link += ['-Wl,--gc-sections']
    run(link, env=env)
    portability = check_portability(kind, work, env, cppflags)
    if inputs() != before:
        raise RuntimeError('KING build inputs changed while compiling; rerun for a consistent result')
    return {'executable': executable, 'compiler': description, 'sourceFiles': pristine,
            'modifiedSourceFiles': modified, 'compileFlags': cppflags, 'portabilityChecks': [portability]}


def install_local(kind, built, before, identifier):
    destination = ROOT / ('dist/king/2.3.2' if kind == 'wasm' else 'build/king/' + kind)
    staging = destination.with_name(destination.name + '.' + identifier + '.partial')
    staging.mkdir(parents=True)
    executable = built['executable']
    shutil.copy2(executable, staging / executable.name)
    if kind == 'wasm':
        shutil.copy2(executable.with_suffix('.wasm'), staging / 'king.wasm')
    manifest = {'schema': 1, 'tool': 'king', 'version': LOCK['version'], 'kind': kind,
                'compiler': built['compiler'], 'buildId': identifier, 'inputs': before,
                'source': LOCK['source'], 'dependencies': LOCK['dependencies'],
                'sourceFiles': built['sourceFiles'], 'modifiedSourceFiles': built['modifiedSourceFiles'],
                'compileFlags': built['compileFlags'], 'assets': hashes(staging),
                'distribution': LOCK['distribution'], 'runtime': LOCK['runtime'],
                'portabilityChecks': built['portabilityChecks'],
                'profile': {'nativeOpenMP': kind == 'native', 'validationCpus': 1,
                            'serialOpenMPCompatibility': kind != 'native', 'lapack': False,
                            'genotypeWordBits': 64, 'externalPlotting': kind == 'native'}}
    (staging / 'build-manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    if destination.exists():
        shutil.rmtree(destination)
    staging.replace(destination)
    print('Built locally (not for redistribution): ' + str(destination), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('all', *KINDS), nargs='?', default='all')
    args = parser.parse_args()
    print('Local personal-use build only: upstream redistribution permission is not established.', flush=True)
    before = inputs()
    archives = {name: archive(record) for name, record in {'king': LOCK['source'], **LOCK['dependencies']}.items()}
    identifier = uuid.uuid4().hex
    work = ROOT / 'build/king/work' / identifier
    work.mkdir(parents=True)
    (work / 'inputs.json').write_text(json.dumps(before, indent=2, sort_keys=True) + '\n')
    for kind in KINDS:
        if args.mode in ('all', kind):
            built = build(kind, work / kind, archives, before)
            install_local(kind, built, before, identifier)


if __name__ == '__main__':
    main()
