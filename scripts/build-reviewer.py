#!/usr/bin/env python3
"""Build REViewer 0.2.7, retaining a pristine GNU/Linux native reference."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tarfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'tools/reviewer'
LOCK = json.loads((TOOL / 'versions.lock.json').read_text())
KINDS = ('native', 'patched-native', 'wasm')
JOBS = int(os.environ.get('JOBS', '2'))


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def hashes(directory):
    return {p.relative_to(directory).as_posix(): sha(p) for p in sorted(directory.rglob('*'))
            if p.is_file() and not p.is_symlink()}


def inputs():
    paths = [ROOT / 'scripts/build-reviewer.py', ROOT / 'scripts/setup-emsdk.sh', *TOOL.rglob('*')]
    return {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(paths)
            if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc', '.pyo')}


def run(command, *, cwd=None, env=None):
    print('+ ' + shlex.join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), cwd=cwd, env=env, check=True)


def archive(record):
    path = ROOT / 'build/downloads' / record['archive']
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        saved = ROOT / 'dist/reviewer/sources' / path.name
        if saved.is_file():
            shutil.copyfile(saved, path)
        else:
            partial = path.with_name(path.name + '.' + uuid.uuid4().hex + '.partial')
            try:
                run(['curl', '--fail', '--location', '--retry', '3', '--max-time', '600', record['url'], '-o', partial])
                if sha(partial) != record['sha256'] or partial.stat().st_size != record['bytes']:
                    raise RuntimeError('Source checksum/size mismatch: ' + path.name)
                partial.replace(path)
            finally:
                partial.unlink(missing_ok=True)
    if sha(path) != record['sha256'] or path.stat().st_size != record['bytes']:
        raise RuntimeError('Source checksum/size mismatch: ' + path.name)
    return path


def extract(path, destination, selected=None):
    destination.mkdir(parents=True)
    with tarfile.open(path) as bundle:
        roots = {m.name.split('/')[0] for m in bundle.getmembers() if m.name.strip('/')}
        if len(roots) != 1:
            raise RuntimeError('Unexpected archive roots: ' + str(path))
        prefix = roots.pop() + '/'
        for member in bundle.getmembers():
            if not member.name.startswith(prefix) or member.name == prefix:
                continue
            member.name = member.name[len(prefix):]
            if selected and not any(member.name == p.rstrip('/') or member.name.startswith(p) for p in selected):
                continue
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
        env.update(CC=env.get('CC', 'cc'), CXX=env.get('CXX', 'c++'), AR=env.get('AR', 'ar'), RANLIB=env.get('RANLIB', 'ranlib'))
    return env


def cflags(kind):
    return ['-O2', '-ffp-contract=off', '-ffunction-sections', '-fdata-sections',
            '-D_FILE_OFFSET_BITS=64', '-D_LARGEFILE_SOURCE'] + ([] if kind == 'wasm' else ['-fPIC'])


def configure(source, args, env, kind):
    run([source / 'configure', *(['--host=wasm32-unknown-emscripten'] if kind == 'wasm' else []), *args], cwd=source, env=env)


def compression(kind, work, archives, env):
    prefix = work / 'prefix'
    (prefix / 'include').mkdir(parents=True)
    (prefix / 'lib').mkdir()
    flags = cflags(kind)
    build_env = {**env, 'CFLAGS': shlex.join(flags)}
    zlib = work / 'zlib'
    extract(archives['zlib'], zlib)
    run([zlib / 'configure', '--static', '--prefix=' + str(prefix)], cwd=zlib, env=build_env)
    run(['make', '-j' + str(JOBS)], cwd=zlib, env=build_env)
    run(['make', 'install'], cwd=zlib, env=build_env)
    bzip2 = work / 'bzip2'
    extract(archives['bzip2'], bzip2)
    run(['make', '-j' + str(JOBS), 'libbz2.a', 'CC=' + env['CC'], 'AR=' + env['AR'],
         'RANLIB=' + env['RANLIB'], 'CFLAGS=' + shlex.join(flags)], cwd=bzip2, env=env)
    shutil.copyfile(bzip2 / 'libbz2.a', prefix / 'lib/libbz2.a')
    shutil.copyfile(bzip2 / 'bzlib.h', prefix / 'include/bzlib.h')
    xz = work / 'xz'
    extract(archives['xz'], xz)
    configure(xz, ['--disable-shared', '--disable-xz', '--disable-xzdec', '--disable-lzmadec',
                   '--disable-lzmainfo', '--disable-nls', '--disable-scripts', '--disable-doc',
                   '--disable-threads', '--prefix=' + str(prefix)], build_env, kind)
    run(['make', '-j' + str(JOBS)], cwd=xz, env=build_env)
    run(['make', 'install'], cwd=xz, env=build_env)
    return prefix


def build(kind, work, archives, before):
    env = environment(kind, work)
    description = subprocess.check_output([env['CXX'], '--version'], env=env, text=True).splitlines()[0]
    if kind == 'wasm' and LOCK['emscripten'] not in description:
        raise RuntimeError('Expected Emscripten ' + LOCK['emscripten'] + ', got ' + description)
    if kind != 'wasm':
        checker = work / 'rng-check'
        run([env['CXX'], '-std=c++11', '-O2', TOOL / 'rng-check.cpp', '-o', checker], env=env)
        run([checker], env=env)
    prefix = compression(kind, work, archives, env)
    hts = work / 'htslib'
    extract(archives['htslib'], hts)
    flags = cflags(kind)
    build_env = {**env, 'CFLAGS': shlex.join(flags), 'CPPFLAGS': '-I' + str(prefix / 'include'),
                 'LDFLAGS': '-L' + str(prefix / 'lib'), 'PKG_CONFIG_LIBDIR': str(prefix / 'lib/pkgconfig')}
    configure(hts, ['--disable-libcurl', '--disable-s3', '--disable-gcs', '--disable-plugins', '--without-libdeflate'], build_env, kind)
    run(['make', '-j' + str(JOBS), 'lib-static', 'CC=' + env['CC'], 'AR=' + env['AR'], 'RANLIB=' + env['RANLIB']], cwd=hts, env=build_env)
    boost, spdlog, source = (work / n for n in ('boost', 'spdlog', 'reviewer'))
    extract(archives['boost'], boost, ('boost/', 'libs/program_options/src/', 'LICENSE_1_0.txt'))
    extract(archives['spdlog'], spdlog)
    extract(archives['reviewer'], source)
    code = source / 'reviewer'
    if kind != 'native':
        origin = code / 'app/Origin.cpp'
        original = origin.read_text()
        if original.count('rand()') != 1:
            raise RuntimeError('Unexpected upstream fragment-assignment RNG calls')
        origin.write_text(('// Modified for genomics-wasm, 2026-10-21: use a GNU libc-compatible default RNG\n'
                           '// for deterministic cross-platform read assignment.\n' + original)
                         .replace('#include "app/Origin.hh"', '#include "app/Origin.hh"\n#include "linux-rand.hh"')
                         .replace('rand()', 'reviewer_portability::linuxDefaultRand()'))
    includes = [code, code / 'thirdparty/graph-tools/include', code / 'thirdparty/graph-tools/external/include',
                boost, spdlog / 'include', hts, prefix / 'include', TOOL]
    cppflags = [*flags, '-std=c++11', '-fexceptions', '-include', 'limits', '-include', 'cstdint',
                '-DBOOST_ALL_NO_LIB', '-DBOOST_SYSTEM_NO_DEPRECATED',
                *['-I' + str(p) for p in includes]]
    run([env['CXX'], *cppflags, '-c', TOOL / 'abi-check.cpp', '-o', work / 'abi-check.o'], env=env)
    sources = sorted([*code.glob('app/*.cpp'), *code.glob('core/*.cpp'), *code.glob('metrics/*.cpp'),
                      *(code / 'thirdparty/graph-tools/src').rglob('*.cpp'), *(boost / 'libs/program_options/src').glob('*.cpp')])
    objects_dir = work / 'objects'
    objects_dir.mkdir()
    objects = [objects_dir / f'{i:03d}.o' for i in range(len(sources))]
    def compile_one(pair):
        src, obj = pair
        run([env['CXX'], *cppflags, '-c', src, '-o', obj], env=env)
    with ThreadPoolExecutor(max_workers=JOBS) as pool:
        list(pool.map(compile_one, zip(sources, objects)))
    executable = work / ('reviewer.mjs' if kind == 'wasm' else 'REViewer')
    libraries = [hts / 'libhts.a', prefix / 'lib/libbz2.a', prefix / 'lib/liblzma.a', prefix / 'lib/libz.a']
    link = [env['CXX'], *flags, '-fexceptions', *objects, *libraries, '-lm', '-o', executable]
    if kind == 'wasm':
        pre = work / 'pre.js'
        pre.write_text('Module["thisProgram"] = Module["thisProgram"] || "REViewer";\n')
        link += ['-sMODULARIZE=1', '-sEXPORT_ES6=1', '-sEXPORT_NAME=createREViewer', '-sINVOKE_RUN=0',
                 '-sEXPORTED_RUNTIME_METHODS=FS,callMain', '-sDISABLE_EXCEPTION_CATCHING=0',
                 '-sINCOMING_MODULE_JS_API=preRun,noInitialRun,stdin,stdout,stderr,onExit,quit,locateFile,wasmBinary,print,printErr,onAbort,thisProgram',
                 '-sENVIRONMENT=web,worker,node', '-sALLOW_MEMORY_GROWTH=1', '-sABORTING_MALLOC=1',
                 '-sINITIAL_MEMORY=67108864', '-sMAXIMUM_MEMORY=2147483648', '-sSTACK_SIZE=8388608',
                 '-sEXIT_RUNTIME=1', '-sFORCE_FILESYSTEM=1', '-Wl,--fatal-warnings', '--pre-js', pre]
    else:
        link += ['-pthread', '-Wl,--gc-sections']
    run(link, env=env)
    if inputs() != before:
        raise RuntimeError('REViewer build inputs changed while compiling; rerun for a consistent result')
    if kind != 'wasm':
        destination = ROOT / 'build' / kind / 'reviewer'
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir(parents=True)
        shutil.copy2(executable, destination / 'REViewer')
        (destination / 'build-inputs.json').write_text(json.dumps({'schema': 1, 'tool': 'reviewer',
            'version': LOCK['version'], 'kind': kind, 'compiler': description, 'inputs': before,
            'binarySha256': sha(destination / 'REViewer')}, indent=2, sort_keys=True) + '\n')
    return {'executable': executable, 'compiler': description}


def publish(built, archives, before, identifier):
    destination = ROOT / 'dist/reviewer'
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    shutil.copy2(built['executable'], destination / 'reviewer.mjs')
    shutil.copy2(built['executable'].with_suffix('.wasm'), destination / 'reviewer.wasm')
    (destination / 'sources').mkdir()
    for archive_path in archives.values():
        shutil.copyfile(archive_path, destination / 'sources' / archive_path.name)
    for name in LOCK['requiredNotices']:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(TOOL / name, target)
    native_files = {}
    for kind in ('native', 'patched-native'):
        native = ROOT / 'build' / kind / 'reviewer'
        if native.exists():
            record = json.loads((native / 'build-inputs.json').read_text())
            if record['inputs'] != before or record['binarySha256'] != sha(native / 'REViewer'):
                raise RuntimeError('Native REViewer oracle does not match the current build inputs')
            native_files[kind] = hashes(native)
    manifest = {'schema': 1, 'tool': 'reviewer', 'version': LOCK['version'], 'compiler': built['compiler'],
                'inputs': before, 'assets': hashes(destination), 'nativeFiles': native_files,
                'runtime': LOCK['runtime'], 'buildId': identifier,
                'portability': {'fragmentOriginRng': 'GNU libc default-seed sequence, scoped to Origin.cpp',
                                'nativeOracle': 'pristine upstream on GNU libc Linux'}}
    (destination / 'build-manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    print('Built ' + str(destination), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('all', *KINDS), nargs='?', default='all')
    args = parser.parse_args()
    before = inputs()
    archives = {name: archive(record) for name, record in {'reviewer': LOCK['source'], **LOCK['dependencies']}.items()}
    identifier = uuid.uuid4().hex
    work = ROOT / 'build/reviewer' / identifier
    work.mkdir(parents=True)
    (work / 'inputs.json').write_text(json.dumps(before, indent=2, sort_keys=True) + '\n')
    for kind in KINDS:
        if args.mode in ('all', kind):
            built = build(kind, work / kind, archives, before)
            if kind == 'wasm':
                publish(built, archives, before, identifier)


if __name__ == '__main__':
    main()
