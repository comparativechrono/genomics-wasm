"""Release-gate integrity tests with explicitly labelled fixtures, not tool builds."""
import copy
import contextlib
import gzip
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import tarfile
import unittest
from unittest.mock import patch
import zipfile

SPEC = importlib.util.spec_from_file_location('cutadapt_packager', Path(__file__).resolve().parents[1] / 'scripts/package-cutadapt.py')
packager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packager)


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        root_patch = patch.object(packager, 'ROOT', self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        for name in (*packager.RUNTIME, *packager.VALIDATION):
            self.write(name, f'Labelled source fixture: {name}\n'.encode())
        main = {'id': 'main', 'kind': 'success', 'args': ['-o', 'out.fastq', 'reads_1.fastq'],
                'outputs': ['out.fastq', 'report.json'], 'executions': ['native', 'patched-native', 'wasm'],
                'comparisons': ['native-contract', 'patched-native', 'wasm']}
        self.plan = {'schema': 1, 'version': '5.2', 'datasets': ['human', 'yeast'], 'cases': [
            main, {**main, 'id': 'filtered', 'args': ['--quality-cutoff', '10'], 'effect': 'main'},
            {**main, 'id': 'same', 'args': ['--gzip'], 'equivalentTo': 'main'},
            {**main, 'id': 'repeat', 'kind': 'repeat', 'baseline': 'main', 'executions': ['wasm'], 'comparisons': ['wasm']},
            {**main, 'id': 'missing', 'kind': 'failure', 'args': ['missing.fastq'], 'oncePerTool': True, 'comparisons': []},
            {**main, 'id': 'threads', 'kind': 'restriction', 'args': ['-j', '2'], 'oncePerTool': True,
             'executions': ['patched-native', 'wasm'], 'comparisons': []},
        ]}
        self.write_json('validation/cutadapt/cases.json', self.plan)
        self.manifest = {'schema_version': 1, 'datasets': {}, 'files': {}}
        for dataset in ('human', 'yeast'):
            metadata = {'observations': {'fixture': True}}
            for mate in (1, 2):
                name = f'{dataset}/reads_{mate}.fastq'
                raw = f'@fixture/{mate}\nACGT\n+\nIIII\n'.encode()
                metadata[f'read{mate}'] = name
                metadata[f'read{mate}Gzip'] = name + '.gz'
                for filename, data in ((name, raw), (name + '.gz', gzip.compress(raw, mtime=0))):
                    self.write(f'datasets/cutadapt/{filename}', data)
                    self.manifest['files'][filename] = {'sha256': packager.digest(data), 'bytes': len(data)}
            self.manifest['datasets'][dataset] = metadata
        self.write_json('datasets/cutadapt/manifest.json', self.manifest)
        self.build = {'identity': 'Labelled build identity fixture; not a compiled distribution',
                      'nativeEnvironments': {kind: {'python': {'version': '3.14.2', 'implementation': 'CPython'}}
                                             for kind in ('native', 'patched-native')}}
        self.reports = self.make_reports()

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def write_json(self, name, data):
        return self.write(name, packager.json_bytes(data))

    def hashes(self, names):
        return {name: packager.digest((self.root / name).read_bytes()) for name in names}

    def make_reports(self):
        cases = []
        for case_id, spec in packager.planned_cases().items():
            failure = spec['kind'] in ('failure', 'restriction')
            biological = packager.digest(f'{spec["_dataset"]}-{"filtered" if spec["id"] == "filtered" else "main"}'.encode())
            executions = {kind: {'exitCode': 1 if failure else 0, 'canonicalExitCode': 1 if failure else 0, 'signal': None,
                                 'stdout': packager.digest(b''), 'stderr': packager.digest(b'Fixture failure') if failure else packager.digest(b''),
                                 'files': {} if failure else {name: packager.digest(b'Output fixture') for name in spec['outputs']},
                                 'missingOutputs': spec['outputs'] if failure else []} for kind in spec['executions']}
            if spec.get('stdoutFile'):
                for execution in executions.values():
                    execution['stdout'] = packager.digest(b'Standard-output fixture')
                    execution['files'][spec['stdoutFile']] = execution['stdout']
            comparisons = {kind: {'equal': True, 'expectedSemanticDigest': biological, 'semanticDigest': biological,
                                  'biologicalDigest': biological, 'invariants': {'fixture': True}} for kind in spec['comparisons']}
            record = {'id': case_id, 'tool': 'cutadapt', 'dataset': spec['_dataset'], 'case': spec['id'],
                      'kind': spec['kind'], 'args': spec['args'], 'status': 'passed', 'executions': executions, 'comparisons': comparisons}
            if spec.get('effect') or spec.get('equivalentTo'):
                changed = bool(spec.get('effect'))
                record['optionEffect'] = {'baseline': 'main', 'before': packager.digest(f'{spec["_dataset"]}-main'.encode()),
                                          'after': biological, 'changed': changed, 'expected': 'changed' if changed else 'equivalent'}
            cases.append(record)
        manifest_sha = packager.digest((self.root / 'datasets/cutadapt/manifest.json').read_bytes())
        paths = []
        for engine in ('node', 'chromium', 'firefox', 'webkit'):
            report = {'schema': 1, 'engine': engine, 'node': 'v22.fixture', 'browserVersion': 'fixture' if engine != 'node' else None,
                      'status': 'passed', 'runtime': self.hashes(packager.RUNTIME), 'validation': self.hashes(packager.VALIDATION),
                      'manifestSha256': manifest_sha, 'datasets': packager.dataset_identity(self.manifest, manifest_sha),
                      'nativePython': {'version': '3.14.2', 'implementation': 'CPython'},
                      'expectedCases': [case['id'] for case in cases], 'cases': cases, 'builds': {'cutadapt': self.build}}
            paths.append(self.write_json(f'reports/{engine}/summary-all.json', report))
        return paths

    def change_report(self, change, index=0):
        path = self.reports[index]
        report = json.loads(path.read_text())
        change(report)
        path.write_bytes(packager.json_bytes(report))

    def check(self, reports=None):
        return packager.check_reports(self.reports if reports is None else reports, self.build)

    @staticmethod
    def archive_bytes(files, prefix):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w:gz') as archive:
            for name, content in sorted(files.items()):
                info = tarfile.TarInfo(prefix + name)
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
        return stream.getvalue()

    @staticmethod
    def wheel_bytes(name, version):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr(f'{name}-{version}.dist-info/METADATA', f'Name: {name}\nVersion: {version}\n')
            archive.writestr(f'{name}/fixture.py', '# Labelled wheel fixture\n')
            if name != 'xopen':
                archive.writestr(f'{name}/fixture.so', packager.WASM_HEADER)
        return stream.getvalue()

    def build_fixture(self):
        for name in ('LICENSE', 'runtime/CUTADAPT.md', 'scripts/build-cutadapt.py', 'scripts/package-cutadapt.py',
                     'scripts/test-cutadapt-demo.mjs', 'scripts/fetch-cutadapt-test-data.py',
                     'tests/test_cutadapt_compare.py', 'tests/test_cutadapt_package.py',
                     'tools/cutadapt/README.md', 'tools/cutadapt/LICENSE', 'tools/cutadapt/LICENSE.dnaio',
                     'tools/cutadapt/LICENSE.xopen', 'tools/cutadapt/THIRD_PARTY_LICENSES.txt',
                     'tools/cutadapt/runtime-notices/ws/LICENSE', 'examples/cutadapt/index.html'):
            self.write(name, f'Labelled source/notice fixture: {name}\n'.encode())
        (self.root / 'scripts/build-cutadapt.py').chmod(0o755)
        self.write_json('examples/cutadapt/config.json', {'datasets': {'human': {}, 'yeast': {}}, 'commands': {'paired': self.plan['cases'][0]}})
        self.write_json('package.json', {'devDependencies': {'playwright': '1.64.0'}})
        self.write_json('package-lock.json', {'name': 'original', 'version': '0.1.0', 'lockfileVersion': 3,
                                            'packages': {'': {'name': 'original', 'version': '0.1.0', 'devDependencies': {'playwright': '1.64.0'}}}})
        data_source = b'Original FASTQ source fixture'
        self.write('build/cutadapt-sources/source.dat', data_source)
        self.manifest.update(sources={'fixture': {'cachePath': 'build/cutadapt-sources/source.dat',
                                                  'sha256': packager.digest(data_source), 'bytes': len(data_source)}},
                             regeneration={'files': {'scripts/fetch-cutadapt-test-data.py': {
                                 'sha256': self.hashes(['scripts/fetch-cutadapt-test-data.py'])['scripts/fetch-cutadapt-test-data.py']}}})
        self.write_json('datasets/cutadapt/manifest.json', self.manifest)
        core = {'pyodide.mjs': b'// Labelled loader fixture\n', 'pyodide.asm.mjs': b'// Labelled loader fixture\n',
                'pyodide.asm.wasm': packager.WASM_HEADER, 'python_stdlib.zip': b'Labelled stdlib fixture',
                'pyodide-lock.json': packager.json_bytes({'info': {'python': '3.14.2', 'arch': 'wasm32', 'platform': 'emscripten_5_0_3'}})}
        ws = {'package.json': b'{"name":"ws","version":"8.18.3"}', 'LICENSE': b'Labelled ws notice fixture'}
        for directory, files in [('pyodide', core), ('node_modules/ws', ws)]:
            for name, content in files.items():
                self.write(f'dist/cutadapt/{directory}/{name}', content)
        lock = {'schema': 1, 'tool': 'cutadapt', 'version': '5.2', 'python': '3.14.2', 'pyodide': '314.0.7', 'emscripten': '5.0.3',
                'archive': 'cutadapt.tar.gz', 'sha256': packager.digest(b'Cutadapt source fixture'),
                'dependencies': {}, 'buildRequirements': [], 'requiredNotices': [],
                'toolchainDownloads': [
                    {'filename': 'cutadapt-toolchain/' + 'a' * 40 + '-wasm-binaries.tar.xz', 'sha256': 'b' * 64, 'role': 'host-toolchain', 'distribute': False},
                    {'filename': 'cutadapt-toolchain/node-v22.16.0-linux-x64.tar.xz', 'sha256': 'c' * 64, 'role': 'host-toolchain', 'distribute': False}]}
        self.write('dist/cutadapt/sources/cutadapt.tar.gz', b'Cutadapt source fixture')
        distribution = {'schema': 1, 'tool': 'cutadapt', 'version': '5.2',
                        'pyodide': {'path': 'pyodide/', 'version': '314.0.7', 'python': '3.14.2'}, 'packages': []}
        for name, version in [('xopen', '2.1.0'), ('dnaio', '1.2.4'), ('cutadapt', '5.2')]:
            data = self.wheel_bytes(name, version)
            filename = f'wheels/{name}-{version}.whl'
            self.write(f'dist/cutadapt/{filename}', data)
            distribution['packages'].append({'name': name, 'version': version, 'filename': filename, 'sha256': packager.digest(data)})
            if name != 'cutadapt':
                source = f'{name} original source fixture'.encode()
                lock['dependencies'][name] = {'version': version, 'archive': f'{name}.tar.gz', 'sha256': packager.digest(source)}
                self.write(f'dist/cutadapt/sources/{name}.tar.gz', source)
        for name, version, data in [('pyodide-core', '314.0.7', self.archive_bytes(core, 'pyodide/')),
                                    ('runtime-ws', '8.18.3', self.archive_bytes(ws, 'package/'))]:
            filename = name + '.tar.gz'
            lock['dependencies'][name] = {'version': version, 'archive': filename, 'sha256': packager.digest(data)}
            self.write(f'dist/cutadapt/sources/{filename}', data)
        self.write_json('tools/cutadapt/runtime-sources.json', [{**lock['dependencies']['runtime-ws'], 'name': 'ws',
                                                               'licensePaths': ['tools/cutadapt/runtime-notices/ws/LICENSE']}])
        self.write_json('tools/cutadapt/runtime-notices/manifest.json', [{'destination': 'tools/cutadapt/runtime-notices/ws/LICENSE',
                                                                        'sha256': self.hashes(['tools/cutadapt/runtime-notices/ws/LICENSE'])['tools/cutadapt/runtime-notices/ws/LICENSE']}])
        self.write_json('tools/cutadapt/sources.lock.json', lock)
        self.write_json('dist/cutadapt/manifest.json', distribution)
        native = {}
        for kind in ('native', 'patched-native'):
            for name in ('cutadapt', 'cutadapt-entry.py', 'cutadapt-packages/cutadapt/__init__.py', 'cutadapt-packages/cutadapt/fixture.so'):
                self.write(f'build/{kind}/{name}', f'{kind} native package fixture: {name}'.encode())
            files = packager.tree_hashes(f'build/{kind}')
            native[kind] = {'files': files, 'launcher': files['cutadapt'], 'python': {'version': '3.14.2', 'implementation': 'CPython'}}
        self.write_json('dist/cutadapt/build-manifest.json', {
            'schema': 1, 'tool': 'cutadapt', 'nativeEnvironments': native,
            'assets': packager.tree_hashes('dist/cutadapt'),
            'inputs': self.hashes([f'tools/cutadapt/{name}' for name in packager.tree_hashes('tools/cutadapt')] + ['scripts/build-cutadapt.py'])})
        _, _, self.build = packager.check_build_identity()
        self.reports = self.make_reports()
        return lock

    def refresh_build_fixture(self):
        path = self.root / 'dist/cutadapt/build-manifest.json'
        identity = json.loads(path.read_text())
        identity['assets'] = packager.tree_hashes('dist/cutadapt', exclude=('build-manifest.json',))
        identity['inputs'] = self.hashes(identity['inputs'])
        path.write_bytes(packager.json_bytes(identity))
        _, _, self.build = packager.check_build_identity()
        self.reports = self.make_reports()

    def test_exact_complete_reports_are_accepted(self):
        self.assertEqual(set(self.check()), packager.ENGINES)

    def test_missing_or_duplicate_browser_reports_cannot_release(self):
        for paths in (self.reports[:-1], self.reports + self.reports[:1]):
            with self.subTest(count=len(paths)), self.assertRaisesRegex(RuntimeError, 'Missing passing reports|Duplicate'):
                self.check(paths)
        self.change_report(lambda report: report.pop('browserVersion'), index=1)
        with self.assertRaisesRegex(RuntimeError, 'actual browser version'):
            self.check()

    def test_reports_cannot_reduce_case_scope_or_replace_native_comparisons(self):
        original = self.reports[0].read_bytes()
        mutations = [
            lambda report: (report['cases'].pop(), report['expectedCases'].pop()),
            lambda report: report['cases'][0]['executions'].pop('native'),
            lambda report: report['cases'][0]['comparisons'].pop('native-contract'),
            lambda report: report['cases'][0]['comparisons']['wasm'].update(equal=False),
            lambda report: report['cases'][0].update(args=['different.fastq']),
            lambda report: next(case for case in report['cases'] if case['case'] == 'filtered')['optionEffect'].update(changed=False),
            lambda report: report['cases'][0]['executions']['wasm']['files'].clear(),
        ]
        for index, change in enumerate(mutations):
            with self.subTest(index=index):
                self.reports[0].write_bytes(original)
                self.change_report(change)
                with self.assertRaises(RuntimeError):
                    self.check()

    def test_all_unexpected_signals_and_failure_exit_differences_are_rejected(self):
        original = self.reports[0].read_bytes()
        for kind in ('success', 'repeat', 'failure', 'restriction'):
            for target in ('native', 'patched-native', 'wasm'):
                record = next(case for case in json.loads(original)['cases'] if case['kind'] == kind)
                if target not in record['executions']:
                    continue
                with self.subTest(kind=kind, target=target):
                    self.reports[0].write_bytes(original)
                    self.change_report(lambda report: next(case for case in report['cases'] if case['kind'] == kind)['executions'][target].update(signal='SIGSEGV', exitCode=None, canonicalExitCode=None))
                    with self.assertRaisesRegex(RuntimeError, 'unexpected crash'):
                        self.check()
        self.reports[0].write_bytes(original)
        self.change_report(lambda report: next(case for case in report['cases'] if case['kind'] == 'failure')['executions']['wasm'].update(exitCode=2, canonicalExitCode=2))
        with self.assertRaisesRegex(RuntimeError, 'failure exits disagree'):
            self.check()

    def test_stale_runtime_validator_data_or_native_identity_are_rejected(self):
        for name in ('runtime/cutadapt-execute.mjs', 'validation/cutadapt/compare.py', 'datasets/cutadapt/human/reads_1.fastq'):
            with self.subTest(name=name):
                path = self.root / name
                original = path.read_bytes()
                path.write_bytes(original + b'changed')
                with self.assertRaisesRegex(RuntimeError, 'differs|Checksum mismatch'):
                    self.check()
                path.write_bytes(original)
        self.change_report(lambda report: report['builds']['cutadapt'].update(nativeEnvironment='different'))
        with self.assertRaisesRegex(RuntimeError, 'environments differ'):
            self.check()

    def test_gzip_mate_content_is_checked_independently(self):
        raw = gzip.compress(b'@different/1\nAAAA\n+\nIIII\n', mtime=0)
        name = 'human/reads_1.fastq.gz'
        self.write(f'datasets/cutadapt/{name}', raw)
        self.manifest['files'][name] = {'sha256': packager.digest(raw), 'bytes': len(raw)}
        self.write_json('datasets/cutadapt/manifest.json', self.manifest)
        with self.assertRaisesRegex(RuntimeError, 'gzip fixture differs'):
            self.check()

    def test_paths_and_symlinks_cannot_escape_the_package_root(self):
        for name in ('../secret', '/absolute', 'C:/absolute', 'a//b', 'a/./b', 'a\\b', 'a\nfile', '.git/config'):
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, 'Unsafe package path'):
                packager.local_path(name)
        link = self.root / 'link'
        link.symlink_to(self.root / 'runtime/index.mjs')
        with self.assertRaisesRegex(RuntimeError, 'missing or unsafe'):
            packager.required(link)

    def test_browser_wheels_require_correct_identity_and_wasm_extensions(self):
        def wheel(metadata=b'Name: cutadapt\nVersion: 5.2\n', extension=packager.WASM_HEADER, extra=None):
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w') as archive:
                archive.writestr('cutadapt-5.2.dist-info/METADATA', metadata)
                archive.writestr('cutadapt/_align.so', extension)
                if extra:
                    archive.writestr(*extra)
            return stream.getvalue()
        packager.check_wheel(wheel(), 'cutadapt', '5.2')
        for content in (wheel(metadata=b'Name: cutadapt\nVersion: 5.1\n'), wheel(extension=b'\x7fELF desktop'),
                        wheel(extension=b'unknown library'), wheel(extra=('../escape', b'bad'))):
            with self.subTest(content=packager.digest(content)), self.assertRaises(RuntimeError):
                packager.check_wheel(content, 'cutadapt', '5.2')

    def test_standard_output_case_requires_the_actual_returned_bytes(self):
        self.plan['cases'][0].update(stdinKey='read1', stdoutFile='stdout.fastq', args=['-o', '-', '-'], outputs=['report.json'])
        self.write_json('validation/cutadapt/cases.json', self.plan)
        self.reports = self.make_reports()
        self.assertEqual(set(self.check()), packager.ENGINES)
        original = self.reports[0].read_bytes()
        for missing in (True, False):
            with self.subTest(missing=missing):
                self.reports[0].write_bytes(original)
                def change(report):
                    files = report['cases'][0]['executions']['wasm']['files']
                    if missing:
                        files.pop('stdout.fastq')
                    else:
                        files['stdout.fastq'] = packager.digest(b'Different output')
                self.change_report(change)
                with self.assertRaisesRegex(RuntimeError, 'standard-output artifact'):
                    self.check()

    def test_standalone_archive_is_complete_deterministic_and_excludes_host_toolchains(self):
        self.build_fixture()
        with contextlib.redirect_stdout(io.StringIO()):
            output = packager.write_package(self.root / 'releases', self.reports)
            original = output.read_bytes()
            packager.write_package(self.root / 'releases', self.reports)
        self.assertEqual(output.read_bytes(), original)
        self.assertTrue(output.with_suffix('.zip.sha256').read_text().startswith(packager.digest(original)))
        prefix = 'cutadapt-5.2-wasm/'
        with zipfile.ZipFile(output) as archive:
            names = archive.namelist()
            for name in ('runtime/cutadapt-execute.mjs', 'dist/cutadapt/node_modules/ws/package.json',
                         'dist/cutadapt/sources/cutadapt.tar.gz', 'build/cutadapt-sources/source.dat',
                         'validation-reports/webkit.json', 'examples/cutadapt/config.json'):
                self.assertIn(prefix + name, names)
            self.assertFalse(any('/cutadapt-toolchain/' in name or '/build/native/' in name or '/datasets/rna-' in name for name in names))
            self.assertFalse(any('/build/downloads/' in name for name in names))
            for line in archive.read(prefix + 'SHA256SUMS').decode().splitlines():
                checksum, name = line.split('  ', 1)
                self.assertEqual(packager.digest(archive.read(prefix + name)), checksum)
            source = json.loads(archive.read(prefix + 'source-manifest.json'))
            self.assertIn('dist/cutadapt/sources/cutadapt.tar.gz', source['files'])
            for name, checksum in source['files'].items():
                self.assertEqual(packager.digest(archive.read(prefix + name)), checksum)
            self.assertEqual((archive.getinfo(prefix + 'scripts/build-cutadapt.py').external_attr >> 16) & 0o777, 0o755)

    def test_native_installed_files_must_match_the_recorded_environment_exactly(self):
        self.build_fixture()
        path = self.root / 'build/native/cutadapt-packages/cutadapt/fixture.so'
        original = path.read_bytes()
        path.write_bytes(b'Changed native extension')
        with self.assertRaisesRegex(RuntimeError, 'installed native environment differs'):
            packager.check_build_identity()
        path.write_bytes(original)
        self.write('build/patched-native/cutadapt-packages/extra.py', b'Unrecorded package')
        with self.assertRaisesRegex(RuntimeError, 'installed native environment differs'):
            packager.check_build_identity()

    def test_stale_build_inputs_and_runtime_assets_prevent_packaging(self):
        self.build_fixture()
        for name in ('scripts/build-cutadapt.py', 'tools/cutadapt/runtime-notices/ws/LICENSE', 'dist/cutadapt/pyodide/pyodide.mjs'):
            with self.subTest(name=name):
                path = self.root / name
                original = path.read_bytes()
                path.write_bytes(original + b'changed')
                with self.assertRaisesRegex(RuntimeError, 'Checksum mismatch|complete compiled distribution'):
                    packager.collect(self.reports)
                path.write_bytes(original)
        self.write('tools/cutadapt/new-build-helper.py', b'New unrecorded build input')
        with self.assertRaisesRegex(RuntimeError, 'omits source inputs'):
            packager.check_build_identity()

    def test_fresh_reports_cannot_relabel_altered_original_runtime_or_sources(self):
        self.build_fixture()
        path = self.root / 'dist/cutadapt/pyodide/pyodide.mjs'
        original = path.read_bytes()
        path.write_bytes(b'Changed upstream runtime')
        self.refresh_build_fixture()
        with self.assertRaisesRegex(RuntimeError, 'runtime files differ from their pinned original archive'):
            packager.collect(self.reports)
        path.write_bytes(original)
        self.write('dist/cutadapt/sources/dnaio.tar.gz', b'Changed original source')
        self.refresh_build_fixture()
        with self.assertRaisesRegex(RuntimeError, 'Checksum mismatch.*dnaio'):
            packager.collect(self.reports)

    def test_missing_extra_or_reclassified_archives_cannot_bypass_source_coverage(self):
        lock = self.build_fixture()
        self.write('dist/cutadapt/sources/unpinned.tar.bz2', b'Unpinned archive')
        self.refresh_build_fixture()
        with self.assertRaisesRegex(RuntimeError, 'missing or unpinned archive'):
            packager.collect(self.reports)
        for mutate in (lambda value: value['toolchainDownloads'][0].update(distribute=True),
                       lambda value: value['toolchainDownloads'][0].update(filename='cutadapt-toolchain/unrelated.tar.xz'),
                       lambda value: value['toolchainDownloads'].pop()):
            changed = copy.deepcopy(lock)
            mutate(changed)
            with self.assertRaises(RuntimeError):
                packager.archive_records(changed)

    def test_failed_release_gate_preserves_an_existing_zip(self):
        self.build_fixture()
        output = self.root / 'releases'
        output.mkdir()
        previous = output / 'cutadapt-5.2-wasm.zip'
        previous.write_bytes(b'Previous reviewed release fixture')
        with self.assertRaisesRegex(RuntimeError, 'Missing passing reports'):
            packager.write_package(output, self.reports[:-1])
        self.assertEqual(previous.read_bytes(), b'Previous reviewed release fixture')
        self.assertEqual(list(output.iterdir()), [previous])


if __name__ == '__main__':
    unittest.main()
