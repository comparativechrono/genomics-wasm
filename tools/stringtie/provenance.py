#!/usr/bin/env python3
"""Verify pinned bundled dependencies and bind output assets to build inputs."""
from pathlib import Path
import hashlib
import json
import sys


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_sha(root):
    digest = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        if path.is_file():
            digest.update((path.relative_to(root).as_posix() + '\0' + sha(path) + '\n').encode())
    return digest.hexdigest()


mode, first, second = sys.argv[1:]
if mode == 'verify-trees':
    lock, source = json.loads(Path(first).read_text()), Path(second)
    for name, dependency in lock['dependencies'].items():
        if 'bundledPath' in dependency:
            actual = tree_sha(source / dependency['bundledPath'])
            if actual != dependency['sourceTreeSha256']:
                raise SystemExit(f'Bundled dependency checksum mismatch: {name}: {actual}')
elif mode == 'record':
    root, output = Path(first), Path(second)
    paths = {root / 'scripts/build-stringtie.sh'}
    paths.update(p for p in (root / 'tools/stringtie').rglob('*') if p.is_file()
                 and p.suffix in {'.patch', '.h', '.hpp', '.c', '.cc', '.cpp', '.rs', '.py', '.sh', '.js', '.json', '.lock'})
    inputs = {p.relative_to(root).as_posix(): sha(p) for p in sorted(paths)}
    output.write_text(json.dumps(inputs, indent=2, sort_keys=True) + '\n')
elif mode == 'finish':
    root, record = Path(first), Path(second)
    inputs = json.loads(record.read_text())
    for name, expected in inputs.items():
        if sha(root / name) != expected:
            raise SystemExit(f'Build input changed during compilation: {name}')
    dist = root / 'dist/stringtie'
    assets = {p.relative_to(dist).as_posix(): sha(p) for p in sorted(dist.rglob('*'))
              if p.is_file() and p.suffix in {'.mjs', '.js', '.wasm'}}
    (dist / 'build-manifest.json').write_text(json.dumps(
        {'schema': 1, 'tool': 'stringtie', 'inputs': inputs, 'assets': assets},
        indent=2, sort_keys=True) + '\n')
else:
    raise SystemExit(f'Unknown mode: {mode}')
