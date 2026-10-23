#!/usr/bin/env python3
"""Collect a static prototype after the real-browser validation gate passes."""
import json
import pathlib
import shutil

root = pathlib.Path(__file__).resolve().parents[1]
report = root / 'reports/seqtk-container/report.json'
if not report.is_file() or json.loads(report.read_text()).get('passed') is not True:
    raise SystemExit('A successful reports/seqtk-container/report.json is required before packaging.')
destination = root / 'releases/seqtk-container'
destination.mkdir(parents=True, exist_ok=True)
for relative in ['examples/seqtk-container', 'dist/seqtk-container']:
    shutil.copytree(root / relative, destination / relative, dirs_exist_ok=True)
for relative in ['runtime/container2wasm-seqtk.mjs', 'runtime/container2wasm-seqtk-worker.js',
                 'scripts/seqtk-container-server.mjs', 'docs/seqtk-container.md',
                 'tools/seqtk-container/sources.lock.json', 'tools/seqtk-container/Dockerfile',
                 'tools/seqtk-container/runner.sh', 'scripts/build-seqtk-container.sh',
                 'tools/seqtk/sources.lock.json', 'LICENSE']:
    target = destination / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(root / relative, target)
shutil.copy2(report, destination / 'validation.json')
(destination / 'README.txt').write_text(
    'seqtk container2wasm browser prototype\n\n'
    'Developer preview: node scripts/seqtk-container-server.mjs\n'
    'Open http://127.0.0.1:8000/examples/seqtk-container/\n\n'
    'Deploy this directory on HTTPS static hosting that sends COOP: same-origin\n'
    'and COEP: require-corp (full names and examples in docs/seqtk-container.md).\n'
    'End users visit the hosted URL; they do not install Docker, WSL or Node.\n'
    'Small inputs only. The upstream emulator reserves about 3000 MiB.\n'
    'See validation.json for the actual checks performed.\n'
)
print(destination)
