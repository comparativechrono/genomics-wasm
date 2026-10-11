#!/usr/bin/env python3
"""Build the pinned interpreter for Strelka2's unchanged native workflow."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'tools/strelka2-python'
PREFIX = ROOT / 'build/strelka2-python'
LOCK = json.loads((TOOL / 'sources.lock.json').read_text())


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    archive = ROOT / 'build/downloads' / LOCK['archive']
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.is_file():
        bundled = ROOT / 'dist/strelka2/sources' / LOCK['archive']
        if bundled.is_file():
            shutil.copyfile(bundled, archive)
        else:
            temporary = archive.with_suffix('.partial')
            with urllib.request.urlopen(LOCK['url'], timeout=180) as response, temporary.open('wb') as output:
                shutil.copyfileobj(response, output)
            temporary.replace(archive)
    if archive.stat().st_size != LOCK['bytes'] or digest(archive) != LOCK['sha256']:
        raise RuntimeError('CPython source archive checksum mismatch')
    inputs = {path.relative_to(ROOT).as_posix(): digest(path)
              for path in [Path(__file__).resolve(), *sorted(TOOL.rglob('*'))] if path.is_file()}
    manifest_path = PREFIX / 'build-manifest.json'
    if manifest_path.is_file():
        old = json.loads(manifest_path.read_text())
        actual = {p.relative_to(PREFIX).as_posix(): digest(p) for p in sorted(PREFIX.rglob('*'))
                  if p.is_file() and not p.is_symlink() and p != manifest_path}
        links = {p.relative_to(PREFIX).as_posix(): os.readlink(p) for p in sorted(PREFIX.rglob('*')) if p.is_symlink()}
        if old.get('inputs') == inputs and old.get('files') == actual and old.get('symlinks') == links:
            subprocess.run([PREFIX / 'bin/python2.7', '-V'], check=True)
            print('Verified existing CPython 2.7.18 build', flush=True)
            return
        raise RuntimeError('Existing CPython build differs from its manifest; remove build/strelka2-python before rebuilding')
    source_parent = ROOT / 'build/strelka2-python-source'
    if source_parent.exists():
        shutil.rmtree(source_parent)
    source_parent.mkdir(parents=True)
    with tarfile.open(archive) as source:
        source.extractall(source_parent, filter='data')
    work = source_parent / 'Python-2.7.18'
    temporary = ROOT / 'build/strelka2-python-tmp'
    temporary.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'LC_ALL': 'C', 'TZ': 'UTC', 'TMPDIR': str(temporary)}
    commands = [
        ['./configure', f'--prefix={PREFIX}', '--disable-shared', '--without-ensurepip'],
        ['make', '-j2'],
        ['make', 'install'],
    ]
    for command in commands:
        print('+ ' + ' '.join(command), flush=True)
        subprocess.run(command, cwd=work, env=env, check=True)
    subprocess.run([PREFIX / 'bin/python2.7', '-c',
                    'import sys, json, gzip, multiprocessing, subprocess, ctypes; assert sys.version_info[:3] == (2,7,18)'],
                   env=env, check=True)
    files = {p.relative_to(PREFIX).as_posix(): digest(p) for p in sorted(PREFIX.rglob('*'))
             if p.is_file() and not p.is_symlink() and p != manifest_path}
    links = {p.relative_to(PREFIX).as_posix(): os.readlink(p) for p in sorted(PREFIX.rglob('*')) if p.is_symlink()}
    manifest_path.write_text(json.dumps({'schema': 1, 'tool': 'cpython', 'version': LOCK['version'],
                                        'source': LOCK, 'inputs': inputs, 'files': files, 'symlinks': links}, indent=2, sort_keys=True) + '\n')
    print(f'Built {PREFIX / "bin/python2.7"}', flush=True)


if __name__ == '__main__':
    main()
