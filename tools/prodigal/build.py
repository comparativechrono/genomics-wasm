#!/usr/bin/env python3
"""Compile pinned original Prodigal into native references and WebAssembly."""
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

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / 'tools/prodigal'
LOCK = json.loads((TOOL / 'sources.lock.json').read_text())
WORK = Path(os.environ.get('PRODIGAL_BUILD_ROOT', ROOT / 'build/prodigal')).resolve()
DIST = ROOT / 'dist/prodigal'
JOBS = int(os.environ.get('JOBS', '2'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args, *, cwd=None):
    print('+ ' + shlex.join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), cwd=cwd, check=True)


def verify(path):
    actual = digest(path)
    if actual != LOCK['sha256']:
        raise SystemExit(f'Source checksum mismatch: expected {LOCK["sha256"]}, got {actual}')
    return path


def archive():
    for path in (ROOT / 'build/downloads' / LOCK['archive'], DIST / 'sources' / LOCK['archive']):
        if path.is_file():
            return verify(path)
    destination = ROOT / 'build/downloads' / LOCK['archive']
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + '.tmp')
    run(['curl', '--fail', '--location', '--retry', '3', '--max-time', '300',
         LOCK['url'], '-o', temporary])
    verify(temporary)
    temporary.replace(destination)
    return destination


def extract(source, target):
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    with tarfile.open(source, 'r:*') as tar:
        roots = {member.name.split('/')[0] for member in tar.getmembers() if member.name.strip('/')}
        if len(roots) != 1:
            raise SystemExit('Unexpected Prodigal source archive layout')
        prefix = next(iter(roots)) + '/'
        for member in tar.getmembers():
            if not member.name.startswith(prefix) or member.name == prefix:
                continue
            member.name = member.name[len(prefix):]
            tar.extract(member, target, filter='data')


def inputs():
    paths = [ROOT / 'scripts/build-prodigal.sh']
    paths += [path for path in TOOL.rglob('*') if path.is_file() and '__pycache__' not in path.parts]
    return {path.relative_to(ROOT).as_posix(): digest(path) for path in sorted(paths)}


def build(variant, source):
    work = WORK / variant
    src = work / 'source'
    extract(source, src)
    if variant != 'native':
        with (TOOL / 'browser.patch').open('rb') as patch:
            subprocess.run(['patch', '--batch', '--forward', '-p1'], cwd=src, stdin=patch, check=True)
    wasm = variant == 'wasm'
    cc = ['emcc'] if wasm else shlex.split(os.environ.get('CC', 'cc'))
    compiler = subprocess.check_output(cc + ['--version'], text=True).splitlines()[0]
    if wasm and LOCK['emscripten'] not in compiler:
        raise SystemExit(f'Expected Emscripten {LOCK["emscripten"]}; found {compiler}')
    objects = work / 'objects'
    if objects.exists():
        shutil.rmtree(objects)
    objects.mkdir()
    # No fast-math: native/browser validation covers all reported scores.
    flags = ['-O2', '-std=gnu11', '-ffunction-sections', '-fdata-sections', '-I' + str(src)]
    if not wasm:
        # Ubuntu injects fortification, whose sprintf wrapper erases overlapping
        # output strings in the unchanged upstream baseline. The non-fortified
        # glibc call retains the historical upstream output used as our oracle.
        flags += ['-U_FORTIFY_SOURCE']
    units = [(path, objects / (path.stem + '.o')) for path in sorted(src.glob('*.c'))]
    units.append((TOOL / 'abi-check.c', objects / 'abi-check.o'))

    def compile_unit(unit):
        path, output = unit
        # This 25 MiB file consists of literal model initializers and training
        # file I/O. Optimizing its assignments dominates compile time without
        # changing prediction speed; use the same -O0 choice on every target.
        unit_flags = flags + (['-O0'] if path.name == 'training.c' else [])
        run(cc + unit_flags + ['-c', path, '-o', output])

    with ThreadPoolExecutor(max_workers=JOBS) as pool:
        list(pool.map(compile_unit, units))
    executable = work / ('prodigal.mjs' if wasm else 'prodigal')
    link = ['-O2']
    if wasm:
        link += ['-Wl,--fatal-warnings', '-sMODULARIZE=1', '-sEXPORT_ES6=1',
                 '-sEXPORT_NAME=createProdigal', '-sINVOKE_RUN=0',
                 '-sEXPORTED_RUNTIME_METHODS=FS,callMain',
                 '-sINCOMING_MODULE_JS_API=preRun,noInitialRun,stdin,stdout,stderr,onExit,quit,locateFile,wasmBinary,print,printErr,onAbort,thisProgram',
                 '-sENVIRONMENT=web,worker,node', '-sALLOW_MEMORY_GROWTH=1',
                 '-sABORTING_MALLOC=1', '-sINITIAL_MEMORY=67108864',
                 '-sMAXIMUM_MEMORY=2147483648', '-sSTACK_SIZE=8388608',
                 '-sEXIT_RUNTIME=1', '-sFORCE_FILESYSTEM=1']
    else:
        link += ['-Wl,--gc-sections']
    run(cc + link + [output for _, output in units] + ['-lm', '-o', executable])
    if not wasm:
        destination = ROOT / 'build' / variant / 'prodigal'
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix('.tmp')
        shutil.copy2(executable, temporary)
        temporary.replace(destination)
    return executable, compiler


def publish(executable, compiler, source, before):
    # Retain an offline source cache even when it supplied the rebuild.
    saved_source = WORK / LOCK['archive']
    if source.resolve() != saved_source.resolve():
        shutil.copy2(source, saved_source)
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)
    for path in (executable, executable.with_suffix('.wasm')):
        shutil.copy2(path, DIST / path.name)
    (DIST / 'sources').mkdir()
    shutil.copy2(saved_source, DIST / 'sources' / saved_source.name)
    (DIST / 'notices').mkdir()
    for name in LOCK['requiredNotices']:
        shutil.copy2(TOOL / name, DIST / 'notices' / name)
    manifest = {'schema': 1, 'tool': 'prodigal', 'version': LOCK['version'],
                'revision': LOCK['revision'], 'compiler': compiler, 'inputs': before,
                'assets': {path.relative_to(DIST).as_posix(): digest(path)
                           for path in sorted(DIST.rglob('*')) if path.is_file()},
                'nativeFiles': {}}
    for variant in ('native', 'patched-native'):
        path = ROOT / 'build' / variant / 'prodigal'
        if path.is_file():
            manifest['nativeFiles'][variant] = {'prodigal': digest(path)}
    (DIST / 'build-manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('all', 'native', 'patched-native', 'wasm'), nargs='?', default='all')
    args = parser.parse_args()
    (WORK / 'tmp').mkdir(parents=True, exist_ok=True)
    os.environ['TMPDIR'] = str(WORK / 'tmp')
    source = archive()
    before = inputs()
    for variant in (('native', 'patched-native', 'wasm') if args.mode == 'all' else (args.mode,)):
        executable, compiler = build(variant, source)
        if inputs() != before:
            raise SystemExit('Build inputs changed during compilation; rerun to create a consistent manifest')
        if variant == 'wasm':
            publish(executable, compiler, source, before)


if __name__ == '__main__':
    main()
