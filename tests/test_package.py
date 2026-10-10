"""Packaging integrity tests; fixture bytes are not real compiled genomics tools."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SPEC = importlib.util.spec_from_file_location('release_packager', Path(__file__).resolve().parents[1] / 'scripts/package.py')
packager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packager)


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.patch = patch.object(packager, 'ROOT', self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        files = {
            'LICENSE': b'Infrastructure licence fixture\n',
            'THIRD_PARTY.md': b'Upstream notice fixture\n',
            'scripts/setup-emsdk.sh': b'# compiler setup fixture\n',
            'scripts/build-bwa.sh': b'# build recipe fixture\n',
            'scripts/package.py': b'# packager source fixture\n',
            'runtime/index.mjs': b'export class ToolRunner {}\n',
            'runtime/worker.mjs': b'// worker fixture\n',
            'runtime/execute.mjs': b'// execution fixture\n',
            'runtime/README.md': b'# Runtime\n\n## Interface\nDocumented API.\n',
            'tests/runtime.test.mjs': b'// runtime test fixture\n',
            'tests/fixtures/mock-tool.mjs': b'// mock fixture\n',
            'tools/bwa/README.md': b'# Tool documentation fixture\n',
            'tools/bwa/COPYING': b'Upstream licence fixture\n',
            'dist/bwa/bwa.mjs': b'export default function fixture() {}\n',
            'dist/bwa/bwa.wasm': b'\x00asm\x01\x00\x00\x00',
            'dist/bwa/COPYING': b'Upstream licence fixture\n',
            'dist/bwa/LICENSE.zlib': b'zlib licence fixture\n',
            'build/downloads/bwa-fixture.tar.gz': b'upstream-source-archive-fixture',
            'build/downloads/zlib-fixture.tar.gz': b'zlib-source-archive-fixture',
        }
        for name, content in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        lock = {
            'version': 'test', 'revision': 'fixture', 'emscripten': '3.1.64',
            'sha256': packager.sha256(files['build/downloads/bwa-fixture.tar.gz']),
            'dependencies': {'zlib': {'version': 'fixture', 'sha256': packager.sha256(files['build/downloads/zlib-fixture.tar.gz'])}},
        }
        (self.root / 'tools/bwa/sources.lock.json').write_text(json.dumps(lock))

    def test_standalone_zip_is_deterministic_and_all_manifests_match(self):
        with contextlib.redirect_stdout(io.StringIO()):
            filename = packager.write_package('bwa', self.root / 'releases')
            first = filename.read_bytes()
            packager.write_package('bwa', self.root / 'releases')
        self.assertEqual(filename.read_bytes(), first)
        prefix = 'bwa-test-wasm/'
        with zipfile.ZipFile(filename) as archive:
            sums = archive.read(prefix + 'SHA256SUMS').decode().splitlines()
            self.assertEqual(len(sums), len(archive.namelist()) - 1)
            for line in sums:
                digest, name = line.split('  ', 1)
                self.assertEqual(packager.sha256(archive.read(prefix + name)), digest, name)
            source_manifest = json.loads(archive.read(prefix + 'source-manifest.json'))
            for name, digest in source_manifest['files'].items():
                self.assertEqual(packager.sha256(archive.read(prefix + name)), digest, name)
            self.assertIn(prefix + 'build/downloads/bwa-fixture.tar.gz', archive.namelist())
            self.assertIn(prefix + 'runtime/worker.mjs', archive.namelist())
            self.assertIn(prefix + 'examples/help.mjs', archive.namelist())
            self.assertFalse(any('/.git/' in name for name in archive.namelist()))

    def test_source_checksum_change_prevents_release(self):
        (self.root / 'build/downloads/bwa-fixture.tar.gz').write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeError, 'Checksum mismatch'):
            packager.write_package('bwa', self.root / 'releases')
        self.assertFalse((self.root / 'releases').exists())

    def test_missing_binary_or_licence_prevents_release(self):
        wasm = self.root / 'dist/bwa/bwa.wasm'
        wasm.unlink()
        with self.assertRaisesRegex(RuntimeError, 'Required release input is missing'):
            packager.collect('bwa')
        wasm.write_bytes(b'not a WebAssembly binary')
        with self.assertRaisesRegex(RuntimeError, 'WebAssembly binary header'):
            packager.collect('bwa')
        wasm.write_bytes(b'\x00asm\x01\x00\x00\x00')
        (self.root / 'dist/bwa/LICENSE.zlib').unlink()
        with self.assertRaisesRegex(RuntimeError, 'LICENSE.zlib'):
            packager.collect('bwa')

    def mem2_fixture(self):
        files = {
            'scripts/build-bwa-mem2.sh': b'# BWA-MEM2 build fixture\n',
            'tools/bwa-mem2/README.md': b'# BWA-MEM2 source documentation fixture\n',
            'tools/bwa-mem2/patches/port.patch': b'portability patch fixture\n',
            'dist/bwa-mem2/bwa-mem2.mjs': b'export default function fixture() {}\n',
            'dist/bwa-mem2/bwa-mem2.wasm': b'\x00asm\x01\x00\x00\x00',
            'dist/bwa-mem2/LICENSE': b'BWA-MEM2 licence fixture\n',
            'dist/bwa-mem2/LICENSE.zlib': b'zlib licence fixture\n',
            'dist/bwa-mem2/LICENSE.safestringlib': b'safestringlib licence fixture\n',
            'dist/bwa-mem2/LICENSE-COPYING.safestringlib.txt': b'safestringlib additional licence fixture\n',
            'build/downloads/bwa-mem2-fixture.tar.gz': b'mem2-upstream-source-archive-fixture',
            'build/downloads/safestringlib-fixture.tar.gz': b'safestringlib-source-archive-fixture',
        }
        for name, content in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        lock = json.loads((self.root / 'tools/bwa/sources.lock.json').read_text())
        lock['version'] = '2.2.1'
        lock['sha256'] = packager.sha256(files['build/downloads/bwa-mem2-fixture.tar.gz'])
        lock['dependencies']['safestringlib'] = {
            'revision': 'fixture', 'sha256': packager.sha256(files['build/downloads/safestringlib-fixture.tar.gz']),
        }
        (self.root / 'tools/bwa-mem2/sources.lock.json').write_text(json.dumps(lock))

    def test_mem2_package_is_independent_and_includes_all_source_archives(self):
        self.mem2_fixture()
        with contextlib.redirect_stdout(io.StringIO()):
            filename = packager.write_package('bwa-mem2', self.root / 'releases')
        self.assertEqual(filename.name, 'bwa-mem2-2.2.1-wasm.zip')
        prefix = 'bwa-mem2-2.2.1-wasm/'
        with zipfile.ZipFile(filename) as archive:
            names = archive.namelist()
            for name in ('bwa-mem2-fixture', 'safestringlib-fixture', 'zlib-fixture'):
                self.assertIn(prefix + f'build/downloads/{name}.tar.gz', names)
            self.assertFalse(any('/tools/bwa/' in name or '/dist/bwa/' in name for name in names))
            readme = archive.read(prefix + 'README.md').decode()
            self.assertIn('BWA-MEM2 2.2.1', readme)
            self.assertIn('`.bwt.2bit.64`', readme)
            self.assertIn('build-bwa-mem2.sh all', readme)
            self.assertIn("tool.run([]", archive.read(prefix + 'examples/help.mjs').decode())
            source_manifest = json.loads(archive.read(prefix + 'source-manifest.json'))
            for name, digest in source_manifest['files'].items():
                self.assertEqual(packager.sha256(archive.read(prefix + name)), digest, name)
            release = json.loads(archive.read(prefix + 'release.json'))
            self.assertEqual(release['tool'], 'bwa-mem2')
            self.assertEqual(release['wasmSha256'], packager.sha256(archive.read(prefix + 'dist/bwa-mem2/bwa-mem2.wasm')))

    def test_mem2_changed_dependency_or_missing_notice_prevents_release(self):
        self.mem2_fixture()
        dependency = self.root / 'build/downloads/safestringlib-fixture.tar.gz'
        original = dependency.read_bytes()
        dependency.write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeError, 'Checksum mismatch.*safestringlib'):
            packager.collect('bwa-mem2')
        dependency.write_bytes(original)
        (self.root / 'dist/bwa-mem2/LICENSE.safestringlib').unlink()
        with self.assertRaisesRegex(RuntimeError, 'LICENSE.safestringlib'):
            packager.collect('bwa-mem2')


if __name__ == '__main__':
    unittest.main()
