#!/usr/bin/env python3
"""Build matched HTS tools with original-native and built-in-plugin comparisons."""
from __future__ import annotations
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

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'tools/hts'
LOCK = json.loads((TOOL / 'versions.lock.json').read_text())
JOBS = int(os.environ.get('JOBS', '2'))
PROGRAMS = LOCK['programs']
KINDS = ('native', 'patched-native', 'wasm')


def sha(path: Path) -> str:
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def hashes(directory: Path) -> dict[str, str]:
    return {p.relative_to(directory).as_posix(): sha(p) for p in sorted(directory.rglob('*'))
            if p.is_file() and not p.is_symlink()}


def inputs() -> dict[str, str]:
    files = [ROOT / 'scripts/build-hts.py', ROOT / 'scripts/setup-emsdk.sh', *TOOL.rglob('*')]
    return {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(files)
            if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc', '.pyo')}


def run(command, *, cwd=None, env=None):
    print('+ ' + shlex.join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), cwd=cwd, env=env, check=True)


def archive(record: dict) -> Path:
    path = ROOT / 'build/downloads' / record['archive']
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        candidates = [ROOT / f'dist/hts-{v}/sources' / record['archive'] for v in LOCK['versions']]
        saved = next((p for p in candidates if p.is_file()), None)
        if saved:
            shutil.copyfile(saved, path)
        else:
            partial = path.with_suffix(path.suffix + '.partial')
            run(['curl', '--fail', '--location', '--retry', '3', '--max-time', '300',
                 record['url'], '-o', partial])
            if sha(partial) != record['sha256']:
                raise RuntimeError('Source checksum mismatch: ' + record['archive'])
            partial.replace(path)
    if sha(path) != record['sha256'] or path.stat().st_size != record['bytes']:
        raise RuntimeError('Source checksum/size mismatch: ' + record['archive'])
    return path


def extract(path: Path, destination: Path):
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
            bundle.extract(member, destination, filter='data')


def environment(kind: str, work: Path) -> dict[str, str]:
    temporary = work / 'tmp'
    temporary.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, 'TMPDIR': str(temporary), 'LC_ALL': 'C', 'TZ': 'UTC'}
    if kind == 'wasm':
        sdk = Path(env.get('GENOMICS_EMSDK_DIR', ROOT / 'toolchain/emsdk')).resolve()
        if not shutil.which('emcc', path=env.get('PATH')) and (sdk / 'upstream/emscripten/emcc').is_file():
            env['PATH'] = str(sdk / 'upstream/emscripten') + os.pathsep + env.get('PATH', '')
            env['EM_CONFIG'] = str(sdk / '.emscripten')
        env.update(CC='emcc', CXX='em++', AR='emar', RANLIB='emranlib', NM='emnm')
    else:
        env.update(CC=env.get('CC', 'cc'), CXX=env.get('CXX', 'c++'),
                   AR=env.get('AR', 'ar'), RANLIB=env.get('RANLIB', 'ranlib'), NM=env.get('NM', 'nm'))
    return env


def compiler(env: dict, wasm: bool) -> str:
    description = subprocess.check_output([env['CC'], '--version'], env=env, text=True).splitlines()[0]
    if wasm and LOCK['emscripten'] not in description:
        raise RuntimeError('Expected Emscripten ' + LOCK['emscripten'] + ', got ' + description)
    return description


def compile_flags(kind: str) -> list[str]:
    flags = ['-O2', '-ffp-contract=off', '-ffunction-sections', '-fdata-sections',
             '-D_FILE_OFFSET_BITS=64', '-D_LARGEFILE_SOURCE']
    if kind != 'wasm':
        flags.append('-fPIC')
    return flags


def wasm_flags(pre: Path) -> list[str]:
    return ['-O2', '-ffp-contract=off', '-sMODULARIZE=1', '-sEXPORT_ES6=1',
            '-sEXPORT_NAME=createHTS', '-sINVOKE_RUN=0', '-sEXPORTED_RUNTIME_METHODS=FS,callMain',
            '-sINCOMING_MODULE_JS_API=preRun,noInitialRun,stdin,stdout,stderr,onExit,quit,locateFile,wasmBinary,print,printErr,onAbort,thisProgram',
            '-sENVIRONMENT=web,worker,node', '-sALLOW_MEMORY_GROWTH=1', '-sABORTING_MALLOC=1',
            '-sINITIAL_MEMORY=67108864', '-sMAXIMUM_MEMORY=2147483648', '-sSTACK_SIZE=8388608',
            '-sEXIT_RUNTIME=1', '-sFORCE_FILESYSTEM=1', '-Wl,--fatal-warnings', '--pre-js', str(pre)]


def configure(source: Path, args: list[str], env: dict, kind: str):
    host = ['--host=wasm32-unknown-emscripten'] if kind == 'wasm' else []
    run([source / 'configure', *host, *args], cwd=source, env=env)


def dependencies(kind: str, work: Path, archives: dict, env: dict) -> Path:
    prefix = work / 'prefix'
    (prefix / 'include').mkdir(parents=True)
    (prefix / 'lib').mkdir()
    flags = compile_flags(kind)
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


def patch(source: Path, name: str, env: dict):
    path = TOOL / name
    if not path.is_file():
        raise RuntimeError('Missing required portability patch: ' + str(path))
    run(['patch', '--batch', '--fuzz=0', '-p1', '-i', path], cwd=source, env=env)


def build(version: str, kind: str, work: Path, archives: dict, prefix: Path, env: dict) -> dict:
    directory = work / version
    sources = {name: directory / name for name in ('htslib', 'samtools', 'bcftools')}
    for name, source in sources.items():
        extract(archives[name], source)
    hts, sam, bcf = (sources[name] for name in ('htslib', 'samtools', 'bcftools'))
    if kind != 'native':
        patch(bcf, f'portability-{version}.patch', env)
        patch(hts, f'htslib-portability-{version}.patch', env)
        patch(sam, f'samtools-portability-{version}.patch', env)
    flags = compile_flags(kind)
    cppflags = ['-I' + str(prefix / 'include')]
    build_env = {**env, 'CFLAGS': shlex.join(flags), 'CPPFLAGS': shlex.join(cppflags),
                 'LDFLAGS': '-L' + str(prefix / 'lib'), 'PKG_CONFIG_LIBDIR': str(prefix / 'lib/pkgconfig')}
    configure(hts, ['--disable-libcurl', '--disable-s3', '--disable-gcs', '--disable-plugins',
                    '--without-libdeflate'], build_env, kind)
    abi = directory / 'abi-check.o'
    run([env['CC'], *flags, '-std=c11', '-I' + str(hts), '-c', TOOL / 'abi-check.c', '-o', abi], env=env)
    link_flags = ['-L' + str(prefix / 'lib')]
    if kind == 'wasm':
        pre = directory / 'pre.js'
        pre.write_text('Module["thisProgram"] = Module["thisProgram"] || '
                       'new URL(import.meta.url).pathname.split("/").pop().replace(/\\.mjs$/, "");\n')
        link_flags += wasm_flags(pre)
    else:
        link_flags += ['-Wl,--gc-sections']
    run(['make', '-j' + str(JOBS), 'lib-static', 'bgzip', 'tabix', 'htsfile',
         'CC=' + env['CC'], 'AR=' + env['AR'], 'RANLIB=' + env['RANLIB'],
         'LDFLAGS=' + shlex.join(link_flags)], cwd=hts, env=build_env)
    configure(sam, ['--with-htslib=' + str(hts), '--disable-configure-htslib', '--without-curses'],
              build_env, kind)
    run(['make', '-j' + str(JOBS), 'samtools', 'CC=' + env['CC'], 'AR=' + env['AR'],
         'LDFLAGS=' + shlex.join(link_flags)], cwd=sam, env=build_env)
    configure(bcf, ['--with-htslib=' + str(hts), '--disable-configure-htslib',
                    '--enable-bcftools-plugins'], build_env, kind)
    run(['make', 'version.h'], cwd=bcf, env=build_env)
    plugin_manifest = None
    make_flags = ['CC=' + env['CC'], 'AR=' + env['AR'], 'RANLIB=' + env['RANLIB'],
                  'LDFLAGS=' + shlex.join(link_flags)]
    if kind != 'native':
        plugin_flags = [*flags, '-I.', '-I' + str(hts), *cppflags]
        run(['python3', TOOL / 'prepare-plugins.py', '--source', bcf, '--version', version,
             '--cc', env['CC'], '--nm', env['NM'], '--cflags-json', json.dumps(plugin_flags),
             '--jobs', str(JOBS)], cwd=bcf, env=build_env)
        plugin_manifest = json.loads((bcf / '.builtin-plugins/manifest.json').read_text())
        make_flags += ['PLUGIN_OBJS=vcfplugin.o ' + ' '.join(plugin_manifest['objects']),
                       'CPPFLAGS=' + shlex.join([*cppflags, '-DBCFTOOLS_BUILTIN_PLUGINS']),
                       'DYNAMIC_FLAGS=']
    targets = ['bcftools', 'plugins'] if kind == 'native' else ['bcftools']
    run(['make', '-j' + str(JOBS), *targets, *make_flags], cwd=bcf, env=build_env)
    result = {'sources': sources, 'prefix': prefix, 'pluginManifest': plugin_manifest,
              'executables': {'bcftools': bcf / 'bcftools', 'samtools': sam / 'samtools',
                              **{name: hts / name for name in ('bgzip', 'tabix', 'htsfile')}}}
    if kind != 'wasm':
        destination = ROOT / 'build' / kind / ('hts-' + version)
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir(parents=True)
        for name, executable in result['executables'].items():
            shutil.copy2(executable, destination / name)
        if kind == 'native':
            (destination / 'plugins').mkdir()
            for name in LOCK['versions'][version]['plugins']:
                shutil.copy2(bcf / 'plugins' / (name + '.so'), destination / 'plugins' / (name + '.so'))
        else:
            shutil.copy2(bcf / '.builtin-plugins/manifest.json', destination / 'builtin-plugins.json')
        shutil.copy2(hts / 'libhts.a', destination / 'libhts.a')
        (destination / 'lib').mkdir()
        for library in ('libz.a', 'libbz2.a', 'liblzma.a'):
            shutil.copy2(prefix / 'lib' / library, destination / 'lib' / library)
    return result


def publish(version: str, built: dict, source_archives: dict, dependency_archives: dict, before: dict):
    dist = ROOT / 'dist' / ('hts-' + version)
    if dist.exists():
        shutil.rmtree(dist)
    dist.mkdir(parents=True)
    wasm = built['wasm']
    for name, executable in wasm['executables'].items():
        shutil.copy2(executable, dist / (name + '.mjs'))
        shutil.copy2(executable.with_suffix('.wasm'), dist / (name + '.wasm'))
    (dist / 'sources').mkdir()
    for path in [*source_archives.values(), *dependency_archives.values()]:
        shutil.copy2(path, dist / 'sources' / path.name)
    for name in LOCK['versions'][version]['requiredNotices']:
        target = dist / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(TOOL / name, target)
    sdk = dist / 'htslib'
    sdk.mkdir()
    shutil.copy2(wasm['sources']['htslib'] / 'libhts.a', sdk / 'libhts.a')
    shutil.copytree(wasm['sources']['htslib'] / 'htslib', sdk / 'include/htslib')
    for path in (sdk / 'include/htslib').rglob('*'):
        if path.is_file() and path.suffix != '.h':
            path.unlink()
    shutil.copytree(wasm['prefix'] / 'include', sdk / 'include', dirs_exist_ok=True)
    (sdk / 'lib').mkdir()
    for library in ('libz.a', 'libbz2.a', 'liblzma.a'):
        shutil.copy2(wasm['prefix'] / 'lib' / library, sdk / 'lib' / library)
    (sdk / 'README.md').write_text(
        '# HTSlib ' + version + ' for wasm32\n\n'
        'Use Emscripten ' + LOCK['emscripten'] + '. Compile with `-Ihtslib/include` and link '
        '`htslib/libhts.a htslib/lib/libbz2.a htslib/lib/liblzma.a htslib/lib/libz.a -lm`. '
        'The archive uses 64-bit off_t and genomic positions. It has local file and '
        'CRAM codec support; remote protocols and pthread execution are disabled.\n')
    (dist / 'builtin-plugins.json').write_text(json.dumps(wasm['pluginManifest'], indent=2, sort_keys=True) + '\n')
    native_files = {}
    for kind in ('native', 'patched-native'):
        destination = ROOT / 'build' / kind / ('hts-' + version)
        if destination.is_dir():
            native_files[kind] = hashes(destination)
    manifest = {'schema': 1, 'tool': 'hts', 'version': version, 'programs': PROGRAMS,
                'plugins': LOCK['versions'][version]['plugins'], 'pluginLoader': 'builtin',
                'compiler': wasm['compiler'], 'inputs': before, 'assets': hashes(dist),
                'nativeFiles': native_files, 'nativePluginDirectory': f'build/native/hts-{version}/plugins',
                'runtime': LOCK['runtime'], 'buildId': wasm['buildId']}
    (dist / 'build-manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    print('Built ' + str(dist), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('all', *KINDS), nargs='?', default='all')
    parser.add_argument('--version', choices=('all', *LOCK['versions']), default='all')
    args = parser.parse_args()
    versions = list(LOCK['versions']) if args.version == 'all' else [args.version]
    before = inputs()
    dependency_archives = {name: archive(record) for name, record in LOCK['dependencies'].items()}
    component_archives = {v: {name: archive(record) for name, record in LOCK['versions'][v]['components'].items()}
                          for v in versions}
    identifier = uuid.uuid4().hex
    work = ROOT / 'build/hts' / identifier
    work.mkdir(parents=True)
    (work / 'inputs.json').write_text(json.dumps(before, indent=2, sort_keys=True) + '\n')
    results = {v: {} for v in versions}
    for kind in KINDS:
        if args.mode not in ('all', kind):
            continue
        target = work / kind
        env = environment(kind, target)
        description = compiler(env, kind == 'wasm')
        prefix = dependencies(kind, target, dependency_archives, env)
        for version in versions:
            result = build(version, kind, target, component_archives[version], prefix, env)
            result.update(compiler=description, buildId=identifier)
            results[version][kind] = result
    if args.mode in ('all', 'wasm') and inputs() != before:
        raise RuntimeError('Build inputs changed while compiling; rerun for a consistent manifest')
    for version, built in results.items():
        if 'wasm' in built:
            publish(version, built, component_archives[version], dependency_archives, before)


if __name__ == '__main__':
    main()
