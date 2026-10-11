#!/usr/bin/env python3
"""Build SomaticSniper and its original SAMtools library from locked sources."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / 'tools/somaticsniper'
LOCK = json.loads((TOOL / 'sources.lock.json').read_text())
WORK = Path(os.environ.get('SOMATICSNIPER_BUILD_ROOT', ROOT / 'build/somaticsniper')).resolve()
DIST = ROOT / 'dist/somaticsniper'
JOBS = int(os.environ.get('JOBS', '2'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args, *, cwd=None, env=None):
    print('+ ' + shlex.join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), cwd=cwd, env=env, check=True)


def verify(path, source):
    actual = digest(path)
    if actual != source['sha256']:
        raise SystemExit(f'Checksum mismatch for {path}: expected {source["sha256"]}, got {actual}')
    return path


def archive(source, *, parent=None):
    # A downloaded standalone distribution is also a complete, offline source cache.
    for path in (ROOT / 'build/downloads' / source['archive'], DIST / 'sources' / source['archive']):
        if path.is_file():
            return verify(path, source)
    destination = ROOT / 'build/downloads' / source['archive']
    destination.parent.mkdir(parents=True, exist_ok=True)
    if parent and source.get('vendoredPath'):
        with tarfile.open(parent, 'r:*') as tar:
            members = [member for member in tar.getmembers()
                       if member.name.endswith('/' + source['vendoredPath'])]
            if len(members) != 1:
                raise SystemExit('Pinned source archive is missing the expected vendored dependency')
            destination.write_bytes(tar.extractfile(members[0]).read())
    else:
        temporary = destination.with_suffix(destination.suffix + '.tmp')
        run(['curl', '--fail', '--location', '--retry', '3', '--max-time', '300',
             source['url'], '-o', temporary])
        verify(temporary, source)
        temporary.replace(destination)
    return verify(destination, source)


def extract(source, target):
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    with tarfile.open(source, 'r:*') as tar:
        # All three locked archives have one top-level directory.
        roots = {member.name.split('/')[0] for member in tar.getmembers() if member.name.strip('/')}
        if len(roots) != 1:
            raise SystemExit(f'Unexpected archive layout: {source}')
        prefix = next(iter(roots)) + '/'
        for member in tar.getmembers():
            if not member.name.startswith(prefix) or member.name == prefix:
                continue
            member.name = member.name[len(prefix):]
            tar.extract(member, target, filter='data')


def inputs():
    paths = [ROOT / 'scripts/build-somaticsniper.sh']
    paths += [path for path in TOOL.rglob('*') if path.is_file() and '__pycache__' not in path.parts]
    return {path.relative_to(ROOT).as_posix(): digest(path) for path in sorted(paths)}


def build(variant, sources):
    work = WORK / variant
    work.mkdir(parents=True, exist_ok=True)
    src, sam, zlib = (work / name for name in ('source', 'samtools', 'zlib'))
    extract(sources['somaticsniper'], src)
    extract(sources['samtools'], sam)
    extract(sources['zlib'], zlib)
    if variant != 'native':
        with (TOOL / 'browser.patch').open('rb') as patch:
            subprocess.run(['patch', '--batch', '--forward', '-p1'], cwd=src, stdin=patch, check=True)
        with (TOOL / 'samtools.patch').open('rb') as patch:
            subprocess.run(['patch', '--batch', '--forward', '-p1'], cwd=sam, stdin=patch, check=True)
    wasm = variant == 'wasm'
    cc = ['emcc'] if wasm else shlex.split(os.environ.get('CC', 'cc'))
    ar = ['emar'] if wasm else shlex.split(os.environ.get('AR', 'ar'))
    ranlib = ['emranlib'] if wasm else shlex.split(os.environ.get('RANLIB', 'ranlib'))
    if wasm:
        version = subprocess.check_output(cc + ['--version'], text=True).splitlines()[0]
        if LOCK['emscripten'] not in version:
            raise SystemExit(f'Expected Emscripten {LOCK["emscripten"]}; found {version}')
    before = inputs()
    prefix = work / 'zlib-prefix'
    if prefix.exists():
        shutil.rmtree(prefix)
    env = dict(os.environ, CC=shlex.join(cc), AR=shlex.join(ar), RANLIB=shlex.join(ranlib), CFLAGS='-O2')
    run(['./configure', '--static', f'--prefix={prefix}'], cwd=zlib, env=env)
    run(['make', f'-j{JOBS}', 'libz.a'], cwd=zlib, env=env)
    run(['make', 'install'], cwd=zlib, env=env)
    generated = work / 'generated'
    generated.mkdir(exist_ok=True)
    (generated / 'version.h').write_text(
        '#pragma once\n'
        f'const static char* __g_prog_version = "{LOCK["version"]}";\n'
        f'const static char* __g_commit_hash = "{LOCK["revision"]}";\n'
        'const static char* __g_build_type = "Release";\n')
    objects = work / 'objects'
    if objects.exists():
        shutil.rmtree(objects)
    objects.mkdir()
    # GNU89 inline semantics are required by SAMtools 0.1.6's external inline definitions.
    # Its documented stdio build (without _USE_KNETFILE) is used on every target.
    flags = ['-O2', '-std=gnu99', '-fgnu89-inline', '-D_FILE_OFFSET_BITS=64',
             '-ffunction-sections', '-fdata-sections', '-I' + str(prefix / 'include'),
             '-I' + str(sam), '-I' + str(src / 'src/lib'), '-I' + str(generated)]
    sam_names = 'bgzf kstring bam_aux bam bam_import sam bam_index bam_pileup bam_lpileup bam_md glf razf faidx knetfile bam_sort'.split()
    sniper_names = 'allele_util dqstats output_bed output_classic output_format output_vcf sniper_maqcns sniper_pileup somatic_sniper'.split()
    units = [(sam / (name + '.c'), objects / ('sam-' + name + '.o')) for name in sam_names]
    units += [(src / 'src/lib/sniper' / (name + '.c'), objects / ('sniper-' + name + '.o')) for name in sniper_names]
    units.append((src / 'src/exe/bam-somaticsniper/main.c', objects / 'main.o'))

    def compile_unit(unit):
        source, output = unit
        run(cc + flags + ['-c', source, '-o', output])

    with ThreadPoolExecutor(max_workers=JOBS) as pool:
        list(pool.map(compile_unit, units))
    library = objects / 'libbam.a'
    run(ar + ['rcs', library] + [objects / ('sam-' + name + '.o') for name in sam_names])
    executable = work / ('somaticsniper.mjs' if wasm else 'somaticsniper')
    link = ['-O2']
    if wasm:
        link += ['-Wl,--fatal-warnings', '-sMODULARIZE=1', '-sEXPORT_ES6=1',
                 '-sEXPORT_NAME=createSomaticSniper', '-sINVOKE_RUN=0',
                 '-sEXPORTED_RUNTIME_METHODS=FS,callMain',
                 '-sINCOMING_MODULE_JS_API=preRun,noInitialRun,stdin,stdout,stderr,onExit,quit,locateFile,wasmBinary,print,printErr,onAbort,thisProgram',
                 '-sENVIRONMENT=web,worker,node', '-sALLOW_MEMORY_GROWTH=1',
                 '-sABORTING_MALLOC=1', '-sINITIAL_MEMORY=67108864',
                 '-sMAXIMUM_MEMORY=2147483648', '-sSTACK_SIZE=8388608',
                 '-sEXIT_RUNTIME=1', '-sFORCE_FILESYSTEM=1']
    else:
        link += ['-Wl,--gc-sections']
    run(cc + link + [objects / 'main.o'] + [objects / ('sniper-' + name + '.o') for name in sniper_names]
        + [library, prefix / 'lib/libz.a', '-lm', '-o', executable])
    if inputs() != before:
        raise SystemExit('Build inputs changed during compilation; rerun to create a consistent manifest')
    if not wasm:
        destination = ROOT / 'build' / variant / 'somaticsniper'
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix('.tmp')
        shutil.copy2(executable, temporary)
        temporary.replace(destination)
        return
    DIST.mkdir(parents=True, exist_ok=True)
    for path in (executable, executable.with_suffix('.wasm')):
        shutil.copy2(path, DIST / path.name)
    for path in TOOL.iterdir():
        if path.is_file():
            shutil.copy2(path, DIST / path.name)
    (DIST / 'sources').mkdir(exist_ok=True)
    for source in sources.values():
        destination = DIST / 'sources' / source.name
        if source.resolve() != destination.resolve():
            shutil.copy2(source, destination)
    assets = {path.relative_to(DIST).as_posix(): digest(path)
              for path in sorted(DIST.rglob('*'))
              if path.is_file() and path.name != 'build-manifest.json'}
    manifest = {'schema': 1, 'tool': 'somaticsniper', 'inputs': before, 'assets': assets,
                'compiler': version, 'version': LOCK['version'], 'revision': LOCK['revision']}
    (DIST / 'build-manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'all'
    if mode not in {'all', 'native', 'patched-native', 'wasm'}:
        raise SystemExit('Expected all, native, patched-native or wasm')
    (WORK / 'tmp').mkdir(parents=True, exist_ok=True)
    os.environ['TMPDIR'] = str(WORK / 'tmp')
    sources = {'somaticsniper': archive(LOCK)}
    sources.update({name: archive(source, parent=sources['somaticsniper'])
                    for name, source in LOCK['dependencies'].items()})
    for variant in (('native', 'patched-native', 'wasm') if mode == 'all' else (mode,)):
        build(variant, sources)


if __name__ == '__main__':
    main()
