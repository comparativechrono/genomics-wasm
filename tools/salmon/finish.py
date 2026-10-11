#!/usr/bin/env python3
"""Collect licensed inputs and tie Salmon's runtime assets to build inputs."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[2]
SUFFIXES = {'.patch', '.h', '.hpp', '.c', '.cc', '.cpp', '.rs', '.py', '.sh', '.js', '.json', '.lock'}


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inputs() -> dict[str, str]:
    files = [ROOT / 'scripts/build-salmon.sh']
    files += sorted(path for path in (ROOT / 'tools/salmon').rglob('*') if path.is_file() and path.suffix in SUFFIXES)
    return {path.relative_to(ROOT).as_posix(): digest(path) for path in files}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record-inputs', type=Path)
    parser.add_argument('--verify-inputs', type=Path)
    args = parser.parse_args()
    current = inputs()
    if args.record_inputs:
        args.record_inputs.parent.mkdir(parents=True, exist_ok=True)
        args.record_inputs.write_text(json.dumps(current, indent=2) + '\n')
        return
    if not args.verify_inputs:
        parser.error('--verify-inputs is required when collecting a build')
    if json.loads(args.verify_inputs.read_text()) != current:
        raise SystemExit('Salmon build inputs changed during compilation; rebuild before packaging.')
    output = ROOT / 'dist/salmon'
    assets = {p.name: digest(p) for p in sorted(output.iterdir()) if p.is_file() and p.suffix in {'.mjs', '.js', '.wasm'}}
    if not {'salmon.mjs', 'salmon.wasm'} <= assets.keys():
        raise SystemExit('Missing Salmon runtime module or WebAssembly binary')
    lock = json.loads((ROOT / 'tools/salmon/sources.lock.json').read_text())
    for record in [lock, *lock['dependencies']['crates']]:
        archive = ROOT / 'build/downloads' / record['archive']
        if digest(archive) != record['sha256']:
            raise SystemExit(f'Source archive checksum mismatch: {archive}')
        target = output / record['archive']
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(archive, target)
    for path in (ROOT / 'tools/salmon').iterdir():
        if path.is_file() and (path.name.startswith(('LICENSE', 'COPYRIGHT')) or path.name in {'THIRD_PARTY_LICENSES.txt', 'sources.lock.json'}):
            shutil.copyfile(path, output / path.name)
    manifest = {'schema': 1, 'tool': 'salmon', 'inputs': current, 'assets': assets}
    (output / 'build-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')


if __name__ == '__main__':
    main()
