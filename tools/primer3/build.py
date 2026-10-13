#!/usr/bin/env python3
"""Compile original Primer3 release sources for native and WebAssembly hosts."""
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
TOOL = ROOT / 'tools/primer3'
DIST = ROOT / 'dist/primer3'
LOCK = json.loads((TOOL / 'sources.lock.json').read_text())
JOBS = int(os.environ.get('JOBS', '2'))
# Original upstream src/Makefile program/object composition, without the
# archive indirection. Unreferenced function sections are discarded at link.
LIBRARIES = ['libprimer3', 'p3_seq_lib', 'dpal_primer', 'thal_primer',
             'thal_parameters', 'oligotm', 'amplicontm', 'masker']
PROGRAMS = {
    'primer3_core': ['primer3_boulder_main', 'format_output', 'read_boulder',
                     'print_boulder', *LIBRARIES],
    'ntdpal': ['ntdpal_main', 'dpal'],
    'ntthal': ['thal_main', 'thal', 'thal_parameters'],
    'oligotm': ['oligotm_main', 'oligotm'],
    'amplicon3_core': ['amplicontm_main', 'amplicontm'],
    'long_seq_tm_test': ['long_seq_tm_test_main', 'oligotm'],
    'primer3_masker': ['masker_main', *LIBRARIES],
}


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inputs():
    paths = [ROOT / 'scripts/build-primer3.sh', *TOOL.rglob('*')]
    return {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(paths)
            if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc', '.pyo')}


def run(command, *, cwd=None, env=None):
    print('+ ' + shlex.join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), cwd=cwd, env=env, check=True)


def archive():
    target = ROOT / 'build/downloads' / LOCK['archive']
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        saved = DIST / 'sources' / LOCK['archive']
        if saved.is_file():
            shutil.copyfile(saved, target)
        else:
            temporary = target.with_suffix('.partial')
            run(['curl', '--fail', '--location', '--retry', '3', '--max-time', '300',
                 LOCK['url'], '-o', temporary])
            if sha(temporary) != LOCK['sha256']:
                raise RuntimeError('Source archive checksum mismatch')
            temporary.replace(target)
    if sha(target) != LOCK['sha256']:
        raise RuntimeError('Source archive checksum mismatch')
    return target


def extract(archive_path, destination):
    destination.mkdir(parents=True)
    with tarfile.open(archive_path) as bundle:
        roots = {m.name.split('/')[0] for m in bundle.getmembers() if m.name.strip('/')}
        if len(roots) != 1:
            raise RuntimeError('Unexpected Primer3 archive structure')
        prefix = roots.pop() + '/'
        for member in bundle.getmembers():
            if not member.name.startswith(prefix) or member.name == prefix:
                continue
            member.name = member.name[len(prefix):]
            bundle.extract(member, destination, filter='data')


def build(kind, work, source_archive, env):
    directory = work / kind
    source = directory / 'source'
    extract(source_archive, source)
    src = source / 'src'
    wasm = kind == 'wasm'
    cc = ['emcc'] if wasm else shlex.split(env.get('CC', 'cc'))
    cxx = ['em++'] if wasm else shlex.split(env.get('CXX', 'c++'))
    compiler = subprocess.check_output(cxx + ['--version'], env=env, text=True).splitlines()[0]
    if wasm and LOCK['emscripten'] not in compiler:
        raise RuntimeError('Expected Emscripten ' + LOCK['emscripten'])
    objects = directory / 'objects'
    objects.mkdir()
    flags = ['-O2', '-ffp-contract=off', '-ffunction-sections', '-fdata-sections', '-I' + str(src)]
    units = {}
    for name in sorted({unit for values in PROGRAMS.values() for unit in values}):
        basename = name.removesuffix('_primer') if name in ('dpal_primer', 'thal_primer') else name
        path = src / (basename + ('.cc' if name == 'libprimer3' else '.c'))
        # Upstream compiles masker_main.c as C++ when linking it with libprimer3.
        command = cxx if name in ('libprimer3', 'masker_main') else cc
        unit_flags = flags + (['-std=c++11'] if command is cxx else ['-std=gnu11'])
        if name == 'ntdpal_main':
            unit_flags += ['-O0']
        if not wasm and name in ('thal', 'thal_primer', 'thal_parameters'):
            unit_flags += ['-ffloat-store']
        units[name] = (path, objects / (name + '.o'), command, unit_flags)
    units['abi-check'] = (TOOL / 'abi-check.c', objects / 'abi-check.o', cc, flags + ['-std=c11'])

    def compile_unit(unit):
        path, output, command, unit_flags = unit
        run([*command, *unit_flags, '-c', path, '-o', output], env=env)

    with ThreadPoolExecutor(max_workers=JOBS) as pool:
        list(pool.map(compile_unit, units.values()))
    executables = {}
    for name, members in PROGRAMS.items():
        executable = directory / (name + ('.mjs' if wasm else ''))
        link = ['-O2', '-ffp-contract=off']
        if wasm:
            pre = directory / (name + '-pre.js')
            pre.write_text('Module["thisProgram"] = Module["thisProgram"] || ' + json.dumps(name) + ';\n')
            link += ['-sMODULARIZE=1', '-sEXPORT_ES6=1', '-sEXPORT_NAME=createPrimer3',
                     '-sINVOKE_RUN=0', '-sEXPORTED_RUNTIME_METHODS=FS,callMain',
                     '-sINCOMING_MODULE_JS_API=preRun,noInitialRun,stdin,stdout,stderr,onExit,quit,locateFile,wasmBinary,print,printErr,onAbort,thisProgram',
                     '-sENVIRONMENT=web,worker,node', '-sALLOW_MEMORY_GROWTH=1',
                     '-sABORTING_MALLOC=1', '-sINITIAL_MEMORY=67108864',
                     '-sMAXIMUM_MEMORY=2147483648', '-sSTACK_SIZE=8388608',
                     '-sEXIT_RUNTIME=1', '-sFORCE_FILESYSTEM=1', '-Wl,--fatal-warnings',
                     '--pre-js', pre]
        else:
            link += ['-Wl,--gc-sections']
        command = cxx if name in ('primer3_core', 'primer3_masker') else cc
        run([*command, *link, *[units[unit][1] for unit in members], units['abi-check'][1],
             '-lm', '-o', executable], env=env)
        executables[name] = executable
    return executables, compiler, source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('all', 'native', 'patched-native', 'wasm'), nargs='?', default='all')
    mode = parser.parse_args().mode
    if set(LOCK['programs']) != set(PROGRAMS):
        raise RuntimeError('Program list differs from pinned lock')
    before = inputs()
    source_archive = archive()
    work = ROOT / 'build/primer3' / uuid.uuid4().hex
    (work / 'tmp').mkdir(parents=True)
    env = {**os.environ, 'TMPDIR': str(work / 'tmp')}
    built = {}
    for kind in ('native', 'patched-native', 'wasm'):
        if mode in ('all', kind):
            executables, compiler, source = build(kind, work, source_archive, env)
            if inputs() != before:
                raise RuntimeError('Build inputs changed during compilation; rerun for a consistent manifest')
            if kind != 'wasm':
                for name, executable in executables.items():
                    target = ROOT / 'build' / kind / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(executable, target)
            built[kind] = (executables, compiler, source)
    if 'wasm' not in built:
        return
    executables, compiler, source = built['wasm']
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)
    for executable in executables.values():
        for path in (executable, executable.with_suffix('.wasm')):
            shutil.copy2(path, DIST / path.name)
    (DIST / 'sources').mkdir()
    shutil.copy2(source_archive, DIST / 'sources' / source_archive.name)
    shutil.copytree(source / 'src/primer3_config', DIST / 'primer3_config')
    (DIST / 'notices').mkdir()
    for name in LOCK['requiredNotices']:
        shutil.copy2(TOOL / name, DIST / 'notices' / name)
    manifest = {'schema': 1, 'tool': 'primer3', 'version': LOCK['version'],
                'revision': LOCK['revision'], 'compiler': compiler, 'inputs': before,
                'programs': LOCK['programs'], 'sourcePatched': False,
                'assets': {p.relative_to(DIST).as_posix(): sha(p)
                           for p in sorted(DIST.rglob('*')) if p.is_file()}, 'nativeFiles': {}}
    for kind in ('native', 'patched-native'):
        binaries = {name: ROOT / 'build' / kind / name for name in PROGRAMS}
        if all(path.is_file() for path in binaries.values()):
            manifest['nativeFiles'][kind] = {name: sha(path) for name, path in binaries.items()}
    (DIST / 'build-manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')


if __name__ == '__main__':
    main()
