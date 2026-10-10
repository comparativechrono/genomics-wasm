#!/usr/bin/env python3
"""Build pinned FreeBayes sources for native, patched-native and WebAssembly."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / 'tools/freebayes'
CACHE = ROOT / 'build/cache'
DOWNLOADS = ROOT / 'build/downloads'
SOURCE = CACHE / 'freebayes-upstream'
REVISION = '084dce52e54af5adbd1e2b0a67f3733dd8bfddc0'
SIMDE_REVISION = '71fd833d9666141edcd1d3c109a80e228303d8d7'
JOBS = int(os.environ.get('JOBS', '4'))


def run(args, **kwargs):
    subprocess.run([str(x) for x in args], check=True, **kwargs)


def obtain(url, filename, digest):
    path = DOWNLOADS / filename
    if not path.exists():
        temporary = path.with_suffix(path.suffix + '.part-freebayes')
        run(['curl', '--fail', '--location', '--retry', '3', '-o', temporary, url])
        temporary.replace(path)
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != digest:
        raise RuntimeError(f'Checksum mismatch for {path}: {actual}')
    return path


def extract(archive, directory):
    directory.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tar:
        # These are trusted, SHA-256 pinned upstream archives, but reject traversal.
        base = directory.resolve()
        for member in tar.getmembers():
            if not (base / member.name).resolve().is_relative_to(base):
                raise RuntimeError('Unsafe archive member: ' + member.name)
        tar.extractall(directory, filter='data')


def verify_offline_sources():
    """Check the complete source tree supplied in a standalone source package."""
    manifest = json.loads((ROOT / 'source-manifest.json').read_text())
    if manifest.get('schemaVersion') != 1 or manifest.get('tool') != 'freebayes':
        raise RuntimeError('Unsupported offline source manifest')
    lock_hash = hashlib.sha256((HERE / 'upstream.lock.json').read_bytes()).hexdigest()
    if manifest.get('upstreamLockSha256') != lock_hash:
        raise RuntimeError('Offline source manifest does not match upstream.lock.json')
    files = manifest.get('files', {})
    prefix = SOURCE.relative_to(ROOT).as_posix() + '/'
    expected = {path for path in files if path.startswith(prefix)}
    actual = {path.relative_to(ROOT).as_posix() for path in SOURCE.rglob('*') if path.is_file()}
    if not expected or expected != actual:
        raise RuntimeError('Offline source files are missing or unexpected files were added')
    for filename, expected_hash in files.items():
        path = ROOT / filename
        if not path.resolve().is_relative_to(ROOT) or not path.is_file():
            raise RuntimeError('Missing or unsafe manifest path: ' + filename)
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
            raise RuntimeError('Offline source checksum mismatch: ' + filename)


def sources():
    CACHE.mkdir(parents=True, exist_ok=True)
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    if not SOURCE.exists():
        run(['git', 'clone', '--no-checkout', 'https://github.com/freebayes/freebayes.git', SOURCE])
        run(['git', 'checkout', '--detach', REVISION], cwd=SOURCE)
        run(['git', 'submodule', 'update', '--init', '--recursive', '--jobs', str(JOBS)], cwd=SOURCE)
    if not (SOURCE / '.git').exists():
        verify_offline_sources()
    else:
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=SOURCE, text=True).strip()
        if head != REVISION:
            raise RuntimeError(f'Expected FreeBayes {REVISION}, found {head}')
        submodules = subprocess.check_output(['git', 'submodule', 'status', '--recursive'], cwd=SOURCE, text=True)
        if any(line[0] != ' ' for line in submodules.splitlines()):
            raise RuntimeError('FreeBayes submodules are missing or do not match pinned commits')
        lock = HERE / 'upstream.lock.json'
        expected = json.loads(lock.read_text())['submodules']
        actual = {line.split()[1]: line.split()[0] for line in submodules.splitlines()}
        if expected != actual:
            raise RuntimeError('Recursive submodule pins differ from upstream.lock.json')
        run(['git', 'diff', '--exit-code', '--ignore-submodules=none', 'HEAD'], cwd=SOURCE)
    zlib = obtain('https://zlib.net/fossils/zlib-1.3.1.tar.gz', 'zlib-1.3.1.tar.gz',
                  '9a93b2b7dfdac77ceba5a558a580e74667dd6fede4585b91eefb60f03b72df23')
    simde = obtain(f'https://codeload.github.com/simd-everywhere/simde/tar.gz/{SIMDE_REVISION}',
                   'simde-0.8.2.tar.gz', '72b2c14a487560b7eb203795f2c2fead5c7499662e639944cca2a9bb19f09029')
    simde_source = CACHE / f'simde-{SIMDE_REVISION}'
    if not simde_source.exists():
        extract(simde, CACHE)
    return zlib, simde_source


def build(mode, zlib_archive, simde):
    wasm = mode == 'wasm'
    patched = mode != 'native'
    work = ROOT / 'build/freebayes' / mode
    work.mkdir(parents=True, exist_ok=True)
    cc = os.environ.get('EMCC', 'emcc') if wasm else os.environ.get('CC', 'cc')
    cxx = os.environ.get('EMXX', 'em++') if wasm else os.environ.get('CXX', 'c++')
    ar = os.environ.get('EMAR', 'emar') if wasm else os.environ.get('AR', 'ar')
    if wasm:
        version = subprocess.check_output([cc, '--version'], text=True)
        if '3.1.64' not in version.splitlines()[0]:
            raise RuntimeError('This build is pinned to Emscripten 3.1.64')

    local_source = SOURCE
    if patched:
        local_source = work / 'source'
        patches = sorted((HERE / 'patches').glob('*.patch'))
        digest = hashlib.sha256(b''.join(p.read_bytes() for p in patches)).hexdigest()
        stamp = work / 'patches.sha256'
        if local_source.exists() and (not stamp.exists() or stamp.read_text() != digest):
            shutil.rmtree(local_source)
        if not local_source.exists():
            shutil.copytree(SOURCE, local_source, ignore=shutil.ignore_patterns('.git'))
            for patch in patches:
                run(['patch', '-p1', '-i', patch], cwd=local_source)
            stamp.write_text(digest)

    zdir = work / 'zlib-1.3.1'
    if not zdir.exists():
        extract(zlib_archive, work)
    common = ['-O2', '-pipe', '-ffunction-sections', '-fdata-sections']
    if wasm:
        common += ['-msimd128']
    zsources = 'adler32 compress crc32 deflate gzclose gzlib gzread gzwrite infback inffast inflate inftrees trees uncompr zutil'.split()
    zobjects = []
    for name in zsources:
        obj = zdir / (name + '.o')
        run([cc, *common, '-DHAVE_UNISTD_H=1', '-I' + str(zdir), '-c', zdir / (name + '.c'), '-o', obj])
        zobjects.append(obj)
    zlibrary = zdir / 'libz.a'
    run([ar, 'rcs', zlibrary, *zobjects])

    # Identical feature configuration for all three baselines. CRAM optional codecs
    # and remote protocols are intentionally outside this package's supported scope.
    config = work / 'config'
    config.mkdir(exist_ok=True)
    (config / 'config.h').write_text('#define HAVE_DRAND48 1\n')
    (config / 'version.h').write_text('#define HTS_VERSION_TEXT "freebayes htslib"\n#define HTSCODECS_VERSION_TEXT ""\n')
    (config / 'config_vars.h').write_text(
        '#define HTS_CC "' + cc + '"\n#define HTS_CPPFLAGS ""\n'
        '#define HTS_CFLAGS "-O2"\n#define HTS_LDFLAGS ""\n#define HTS_LIBS "-lz -lm"\n')
    includes = [config, zdir, simde] + [local_source / p for p in (
        'src', 'ttmath', 'contrib', 'contrib/SeqLib', 'contrib/htslib',
        'contrib/htslib/cram', 'vcflib/src', 'vcflib/tabixpp',
        'vcflib/smithwaterman', 'vcflib/multichoose', 'vcflib/filevercmp', 'vcflib/fastahack')]
    flags = [*common, *('-I' + str(p) for p in includes)]
    if patched:
        flags += ['-DGENOMICS_WASM_PORT=1', '-DTTMATH_NOASM=', '-DTTMATH_PLATFORM64=1']
    files = [file for group in json.loads((HERE / 'sources.json').read_text()).values() for file in group]
    objects = []
    commands = []
    for filename in files:
        source = local_source / filename
        obj = work / 'objects' / (filename + '.o')
        obj.parent.mkdir(parents=True, exist_ok=True)
        cplusplus = source.suffix == '.cpp'
        # intervaltree's pinned header uses numeric_limits without including limits.
        # Supply that standard declaration equally to pristine and port builds.
        extra = ['-std=c++14', '-include', 'limits', '-Wno-deprecated', '-Wno-narrowing'] if cplusplus else ['-std=gnu99']
        if wasm and cplusplus:
            extra += ['-fexceptions']
        objects.append(obj)
        commands.append([cxx if cplusplus else cc, *flags, *extra, '-c', source, '-o', obj])
    with concurrent.futures.ThreadPoolExecutor(max_workers=JOBS) as pool:
        list(pool.map(run, commands))
    if wasm:
        output = ROOT / 'dist/freebayes/freebayes.mjs'
        link = ['-sMODULARIZE=1', '-sEXPORT_ES6=1', '-sEXPORT_NAME=createFreebayes',
                '-sINVOKE_RUN=0', '-sEXPORTED_RUNTIME_METHODS=FS,callMain',
                '-sINCOMING_MODULE_JS_API=preRun,noInitialRun,stdin,stdout,stderr,onExit,quit,locateFile,wasmBinary,print,printErr',
                '-sENVIRONMENT=web,worker,node', '-sALLOW_MEMORY_GROWTH=1', '-sEXIT_RUNTIME=1',
                '-sINITIAL_MEMORY=67108864', '-sMAXIMUM_MEMORY=2147483648',
                '-sSTACK_SIZE=8388608', '-sFORCE_FILESYSTEM=1', '-sDISABLE_EXCEPTION_CATCHING=0',
                '-fexceptions']
    else:
        output = ROOT / 'build' / mode / 'freebayes'
        link = ['-pthread', '-Wl,--gc-sections']
    output.parent.mkdir(parents=True, exist_ok=True)
    run([cxx, *common, *objects, zlibrary, '-lm', *link, '-o', output])
    if wasm:
        notices = output.parent / 'LICENSES'
        notices.mkdir(exist_ok=True)
        for label, relative in (
            ('freebayes', 'LICENSE'),
            ('htslib', 'contrib/htslib/LICENSE'),
            ('htscodecs', 'contrib/htslib/htscodecs/LICENSE.md'),
            ('seqlib', 'contrib/SeqLib/LICENSE'),
            ('intervaltree', 'intervaltree/LICENSE'),
            ('vcflib', 'vcflib/LICENSE'),
            ('tabixpp', 'vcflib/tabixpp/LICENSE'),
            ('smithwaterman', 'vcflib/smithwaterman/LICENSE'),
            ('libdisorder', 'vcflib/smithwaterman/libdisorder.LICENSE'),
            ('fastahack', 'vcflib/fastahack/LICENSE'),
            ('multichoose', 'vcflib/multichoose/LICENSE'),
        ):
            shutil.copyfile(local_source / relative, notices / (label + '.txt'))
        shutil.copyfile(simde / 'COPYING', notices / 'simde.txt')
        shutil.copyfile(zdir / 'README', notices / 'zlib.txt')
        # These small vendored components carry their notices in source headers.
        for label, relative in (
            ('ttmath', 'ttmath/ttmathtypes.h'),
            ('ssw', 'contrib/SeqLib/src/ssw.c'),
            ('filevercmp', 'vcflib/filevercmp/filevercmp.h'),
        ):
            content = (local_source / relative).read_text()
            end = content.index('#ifndef') if label == 'ttmath' else content.index('*/') + 2
            (notices / (label + '.txt')).write_text(content[:end] + '\n')
        shutil.copyfile(HERE / 'README.md', output.parent / 'README.md')
        shutil.copyfile(HERE / 'upstream.lock.json', output.parent / 'upstream.lock.json')
    print(f'Built {output}', flush=True)


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'all'
    if mode not in ('all', 'native', 'patched-native', 'wasm'):
        raise SystemExit('Usage: scripts/build-freebayes.sh [all|native|patched-native|wasm]')
    zlib_archive, simde = sources()
    for target in (('native', 'patched-native', 'wasm') if mode == 'all' else (mode,)):
        build(target, zlib_archive, simde)
