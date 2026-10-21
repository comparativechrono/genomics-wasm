#!/usr/bin/env python3
"""Build pinned ExpansionHunter with pristine-native, serial-native and WASM targets."""
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
TOOL = ROOT / 'tools/expansionhunter'
LOCK = json.loads((TOOL / 'versions.lock.json').read_text())
JOBS = int(os.environ.get('JOBS', '2'))
KINDS = ('native', 'patched-native', 'wasm')


def sha(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def hashes(directory):
    return {p.relative_to(directory).as_posix(): sha(p) for p in sorted(directory.rglob('*'))
            if p.is_file() and not p.is_symlink()}


def inputs():
    files = [Path(__file__).resolve(), ROOT / 'scripts/setup-emsdk.sh', *TOOL.rglob('*')]
    return {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(files)
            if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc', '.pyo')}


def run(command, *, cwd=None, env=None):
    print('+ ' + shlex.join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), cwd=cwd, env=env, check=True)


def archive(record):
    path = ROOT / 'build/downloads' / record['archive']
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        saved = ROOT / 'dist/expansionhunter/sources' / path.name
        if saved.is_file():
            shutil.copyfile(saved, path)
        else:
            partial = path.with_name(path.name + '.' + uuid.uuid4().hex + '.partial')
            try:
                run(['curl', '--fail', '--location', '--retry', '3', '--max-time', '300', record['url'], '-o', partial])
                if sha(partial) != record['sha256'] or partial.stat().st_size != record['bytes']:
                    raise RuntimeError('Source checksum/size mismatch: ' + path.name)
                partial.replace(path)
            finally:
                partial.unlink(missing_ok=True)
    if sha(path) != record['sha256'] or path.stat().st_size != record['bytes']:
        raise RuntimeError('Source checksum/size mismatch: ' + path.name)
    return path


def extract(path, destination):
    destination.mkdir(parents=True)
    with tarfile.open(path) as bundle:
        roots = {m.name.split('/')[0] for m in bundle.getmembers() if m.name.strip('/')}
        if len(roots) != 1:
            raise RuntimeError('Unexpected source archive roots: ' + str(path))
        prefix = roots.pop() + '/'
        for member in bundle.getmembers():
            if not member.name.startswith(prefix) or member.name == prefix:
                continue
            member.name = member.name[len(prefix):]
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
        env.update(CC=env.get('CC', 'cc'), CXX=env.get('CXX', 'c++'), AR=env.get('AR', 'ar'),
                   RANLIB=env.get('RANLIB', 'ranlib'))
    return env


def flags(kind):
    result = ['-O2', '-ffp-contract=off', '-ffunction-sections', '-fdata-sections',
              '-D_FILE_OFFSET_BITS=64', '-D_LARGEFILE_SOURCE']
    if kind != 'wasm':
        result += ['-fPIC']
    return result


def compile_files(sources, directory, compiler, options, env):
    directory.mkdir(parents=True, exist_ok=True)
    commands = []
    for index, source in enumerate(sources):
        output = directory / f'{index:04d}-{source.stem}.o'
        commands.append(([compiler, *options, '-c', source, '-o', output], output))
    with ThreadPoolExecutor(max_workers=JOBS) as pool:
        list(pool.map(lambda pair: run(pair[0], env=env), commands))
    return [output for _, output in commands]


def dependencies(kind, work, archives, shared, env):
    prefix = work / 'prefix'
    (prefix / 'include').mkdir(parents=True)
    (prefix / 'lib').mkdir()
    cflags = flags(kind)
    build_env = {**env, 'CFLAGS': shlex.join(cflags)}
    zlib = work / 'zlib'
    extract(archives['zlib'], zlib)
    run([zlib / 'configure', '--static', '--prefix=' + str(prefix)], cwd=zlib, env=build_env)
    run(['make', '-j' + str(JOBS)], cwd=zlib, env=build_env)
    run(['make', 'install'], cwd=zlib, env=build_env)
    bz = work / 'bzip2'
    extract(archives['bzip2'], bz)
    run(['make', '-j' + str(JOBS), 'libbz2.a', 'CC=' + env['CC'], 'AR=' + env['AR'],
         'RANLIB=' + env['RANLIB'], 'CFLAGS=' + shlex.join(cflags)], cwd=bz, env=env)
    shutil.copyfile(bz / 'libbz2.a', prefix / 'lib/libbz2.a')
    shutil.copyfile(bz / 'bzlib.h', prefix / 'include/bzlib.h')
    xz = work / 'xz'
    extract(archives['xz'], xz)
    host = ['--host=wasm32-unknown-emscripten'] if kind == 'wasm' else []
    run([xz / 'configure', *host, '--disable-shared', '--disable-xz', '--disable-xzdec',
         '--disable-lzmadec', '--disable-lzmainfo', '--disable-nls', '--disable-scripts', '--disable-doc',
         '--disable-threads', '--prefix=' + str(prefix)], cwd=xz, env=build_env)
    run(['make', '-j' + str(JOBS)], cwd=xz, env=build_env)
    run(['make', 'install'], cwd=xz, env=build_env)
    hts = work / 'htslib'
    extract(archives['htslib'], hts)
    hts_env = {**build_env, 'CPPFLAGS': '-I' + str(prefix / 'include'),
               'LDFLAGS': '-L' + str(prefix / 'lib'), 'PKG_CONFIG_LIBDIR': str(prefix / 'lib/pkgconfig')}
    run([hts / 'configure', *host, '--disable-libcurl', '--disable-s3', '--disable-gcs',
         '--disable-plugins', '--without-libdeflate'], cwd=hts, env=hts_env)
    run(['make', '-j' + str(JOBS), 'lib-static', 'CC=' + env['CC'], 'AR=' + env['AR'],
         'RANLIB=' + env['RANLIB']], cwd=hts, env=hts_env)
    shutil.copyfile(hts / 'libhts.a', prefix / 'lib/libhts.a')
    shutil.copytree(hts / 'htslib', prefix / 'include/htslib')
    boost = shared['boost']
    boost_sources = sorted((boost / 'libs/program_options/src').glob('*.cpp'))
    boost_sources += sorted((boost / 'libs/filesystem/src').glob('*.cpp'))
    boost_sources += sorted((boost / 'libs/system/src').glob('*.cpp'))
    boost_objects = compile_files(boost_sources, work / 'boost-objects', env['CXX'],
        [*cflags, '-std=c++11', '-fexceptions', '-I' + str(boost), '-DBOOST_ALL_NO_LIB'], env)
    run([env['AR'], 'rcs', prefix / 'lib/libboost-selected.a', *boost_objects], env=env)
    absl_build = work / 'abseil-build'
    cmake_program = env.get('CMAKE', 'cmake')
    cmake = ['emcmake', cmake_program] if kind == 'wasm' else [cmake_program]
    run([*cmake, '-S', shared['abseil'], '-B', absl_build, '-DCMAKE_BUILD_TYPE=Release',
         '-DCMAKE_CXX_COMPILER=' + env['CXX'], '-DCMAKE_CXX_STANDARD=11',
         '-DCMAKE_CXX_FLAGS=' + shlex.join([*cflags, '-fexceptions']),
         '-DBUILD_TESTING=OFF'], env=env)
    run([cmake_program, '--build', absl_build, '-j', str(JOBS), '--target', 'raw_hash_set', 'hash'], env=env)
    absl_libraries = sorted(absl_build.rglob('libabsl*.a'))
    return prefix, absl_libraries


def patch(source, env):
    run(['patch', '--batch', '--fuzz=0', '-p1', '-i', TOOL / 'serial.patch'], cwd=source, env=env)
    shutil.copyfile(TOOL / 'serial-thread.hh', source / 'ehunter/core/GenomicsSerialThread.hh')


def build(kind, work, archives, shared, before, identifier):
    env = environment(kind, work)
    description = subprocess.check_output([env['CXX'], '--version'], env=env, text=True).splitlines()[0]
    if kind == 'wasm' and LOCK['emscripten'] not in description:
        raise RuntimeError('Expected Emscripten ' + LOCK['emscripten'] + ', got ' + description)
    prefix, absl_libraries = dependencies(kind, work, archives, shared, env)
    source = work / 'ExpansionHunter'
    extract(archives['source'], source)
    original_sources = hashes(source)
    if kind != 'native':
        patch(source, env)
    after_sources = hashes(source)
    changes = {p: {'before': original_sources.get(p), 'after': digest}
               for p, digest in after_sources.items() if original_sources.get(p) != digest}
    eh = source / 'ehunter'
    graph = eh / 'thirdparty/graph-tools-master-f421f4c'
    includes = [eh, graph / 'include', graph / 'external/include',
                graph / 'external/sparsepp/sparsepp-e40d7a0',
                eh / 'thirdparty/ctpl/ctpl-0.0.2', prefix / 'include',
                shared['boost'], shared['spdlog'] / 'include', shared['abseil']]
    cxxflags = [*flags(kind), '-std=c++11', '-fexceptions', '-DBOOST_ALL_NO_LIB',
                '-include', 'limits', '-include', 'cstdint',
                *('-I' + str(p) for p in includes)]
    if kind != 'native':
        cxxflags += ['-DGENOMICS_SERIAL']
    popcount_check = work / ('popcount-check.cjs' if kind == 'wasm' else 'popcount-check')
    run([env['CXX'], *cxxflags, TOOL / 'popcount-check.cpp', '-o', popcount_check], env=env)
    run(['node', popcount_check] if kind == 'wasm' else [popcount_check], env=env)
    sources = sorted(p for directory in ('alignment', 'core', 'genotyping', 'io', 'locus', 'sample')
                     for p in (eh / directory).glob('*.cpp') if not p.name.endswith('Test.cpp'))
    sources += sorted((graph / 'src').rglob('*.cpp'))
    sources += [eh / 'app/ExpansionHunter.cpp', TOOL / 'abi-check.cpp']
    objects = compile_files(sources, work / 'objects', env['CXX'], cxxflags, env)
    (work / 'output').mkdir(exist_ok=True)
    executable = work / 'output' / ('expansionhunter.mjs' if kind == 'wasm' else 'ExpansionHunter')
    link_flags = ['-O2', '-ffp-contract=off', '-fexceptions']
    if kind == 'wasm':
        pre = work / 'pre.js'
        pre.write_text('Module["thisProgram"] = Module["thisProgram"] || "ExpansionHunter";\n')
        link_flags += ['-sMODULARIZE=1', '-sEXPORT_ES6=1', '-sEXPORT_NAME=createExpansionHunter',
            '-sINVOKE_RUN=0', '-sEXPORTED_RUNTIME_METHODS=FS,callMain',
            '-sINCOMING_MODULE_JS_API=preRun,noInitialRun,stdin,stdout,stderr,onExit,quit,locateFile,wasmBinary,print,printErr,onAbort,thisProgram',
            '-sENVIRONMENT=web,worker,node', '-sALLOW_MEMORY_GROWTH=1', '-sABORTING_MALLOC=1',
            '-sINITIAL_MEMORY=67108864', '-sMAXIMUM_MEMORY=2147483648', '-sSTACK_SIZE=8388608',
            '-sEXIT_RUNTIME=1', '-sFORCE_FILESYSTEM=1', '-sDISABLE_EXCEPTION_CATCHING=0',
            '-Wl,--fatal-warnings', '--pre-js', str(pre)]
    else:
        link_flags += ['-pthread', '-Wl,--gc-sections']
    libraries = [prefix / 'lib/libboost-selected.a', prefix / 'lib/libhts.a', *absl_libraries,
                 prefix / 'lib/libbz2.a', prefix / 'lib/liblzma.a', prefix / 'lib/libz.a']
    run([env['CXX'], *link_flags, *objects, '-Wl,--start-group', *libraries,
         '-Wl,--end-group', '-lm', '-o', executable], env=env)
    provenance = {'schema': 1, 'tool': LOCK['tool'], 'version': LOCK['version'], 'kind': kind,
                  'compiler': description, 'inputs': before, 'buildId': identifier,
                  'scientificSourceChanges': changes, 'buildOnlySourceChanges': {},
                  'buildCompatibility': ['Preinclude standard limits and cstdint for legacy GraphTools headers'],
                  'sourceFiles': original_sources, 'compileFlags': cxxflags, 'linkFlags': link_flags,
                  'notes': 'Direct source build replaces network-fetching CMake orchestration. Pristine native scientific sources are unchanged.'}
    if kind != 'wasm':
        destination = ROOT / 'build' / kind / 'expansionhunter'
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir(parents=True)
        shutil.copy2(executable, destination / 'ExpansionHunter')
        (destination / 'build-provenance.json').write_text(json.dumps(provenance, indent=2, sort_keys=True) + '\n')
    return {'executable': executable, 'compiler': description, 'buildId': identifier, 'provenance': provenance}


def publish(built, archives, before):
    dist = ROOT / 'dist/expansionhunter'
    if dist.exists():
        shutil.rmtree(dist)
    (dist / 'sources').mkdir(parents=True)
    for path in archives.values():
        shutil.copy2(path, dist / 'sources' / path.name)
    wasm = built['wasm']
    shutil.copy2(wasm['executable'], dist / 'expansionhunter.mjs')
    shutil.copy2(wasm['executable'].with_suffix('.wasm'), dist / 'expansionhunter.wasm')
    for name in LOCK['requiredNotices']:
        target = dist / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(TOOL / name, target)
    (dist / 'build-provenance.json').write_text(json.dumps(wasm['provenance'], indent=2, sort_keys=True) + '\n')
    native_files = {kind: hashes(ROOT / 'build' / kind / 'expansionhunter')
                    for kind in ('native', 'patched-native')
                    if (ROOT / 'build' / kind / 'expansionhunter').is_dir()}
    manifest = {'schema': 1, 'tool': LOCK['tool'], 'version': LOCK['version'], 'compiler': wasm['compiler'],
                'inputs': before, 'assets': hashes(dist), 'nativeFiles': native_files, 'buildId': wasm['buildId'],
                'runtime': LOCK['runtime']}
    (dist / 'build-manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    print('Built ' + str(dist), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('all', *KINDS), nargs='?', default='all')
    args = parser.parse_args()
    before = inputs()
    archives = {name: archive(record) for name, record in {'source': LOCK['source'], **LOCK['dependencies']}.items()}
    identifier = uuid.uuid4().hex
    work = ROOT / 'build/expansionhunter' / identifier
    shared = {name: work / 'sources' / name for name in ('boost', 'spdlog', 'abseil')}
    for name, destination in shared.items():
        extract(archives[name], destination)
    built = {}
    for kind in KINDS:
        if args.mode in ('all', kind):
            built[kind] = build(kind, work / kind, archives, shared, before, identifier)
    if inputs() != before:
        raise RuntimeError('Build inputs changed while compiling; rerun for a consistent manifest')
    if 'wasm' in built:
        publish(built, archives, before)


if __name__ == '__main__':
    main()
