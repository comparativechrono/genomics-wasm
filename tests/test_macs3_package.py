"""Release-gate integrity tests with explicitly labelled fixtures, not tool builds."""
import copy
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import tarfile
import unittest
from unittest.mock import patch
import zipfile

SPEC = importlib.util.spec_from_file_location('macs3_packager', Path(__file__).resolve().parents[1] / 'scripts/package-macs3.py')
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
        # These intentionally minimal labelled wheels do not carry a real PEP 508
        # parser. The separate dependency-helper tests exercise that parser with
        # the original pinned packaging wheel, when downloaded for the build.
        dependency_patch = patch.object(packager, 'check_dependency_closure')
        dependency_patch.start()
        self.addCleanup(dependency_patch.stop)
        for name in (*packager.RUNTIME, *packager.VALIDATION):
            self.write(name, f'Labelled source fixture: {name}\n'.encode())
        main = {'id': 'main', 'kind': 'success', 'args': ['-o', 'out.fastq', 'reads_1.fastq'],
                'outputs': ['out.fastq', 'report.json'], 'executions': ['native', 'patched-native', 'wasm'],
                'artifacts': [{'path': name, 'kind': 'labelled-fixture'} for name in ('out.fastq', 'report.json')],
                'comparisons': ['native-contract', 'patched-native', 'wasm']}
        self.plan = {'schema': 1, 'tool': 'macs3', 'version': '3.0.5', 'pythonVersion': '3.14.2', 'datasets': ['macs3'],
                     'numericPolicy': {'score': {'absolute': 1e-5, 'relative': 1e-6}, 'hmm': {'absolute': 1e-7, 'relative': 1e-5}}, 'cases': [
            main, {**main, 'id': 'filtered', 'args': ['--quality-cutoff', '10'], 'effect': 'main'},
            {**main, 'id': 'same', 'args': ['--gzip'], 'equivalentTo': 'main'},
            {**main, 'id': 'repeat', 'kind': 'repeat', 'baseline': 'main', 'executions': ['wasm'], 'comparisons': ['wasm']},
            {**main, 'id': 'missing', 'kind': 'failure', 'args': ['missing.fastq'], 'oncePerTool': True, 'comparisons': []},
            {**main, 'id': 'threads', 'kind': 'restriction', 'args': ['-j', '2'], 'oncePerTool': True,
             'executions': ['patched-native', 'wasm'], 'comparisons': []},
        ]}
        self.write_json('validation/macs3/cases.json', self.plan)
        self.manifest = {'schema_version': 1, 'datasets': {}, 'files': {}}
        for dataset in ('macs3',):
            metadata = {'observations': {'fixture': True}}
            for filename in ('treatment.bed', 'control.bed'):
                name = dataset + '/' + filename
                raw = b'chr1\t10\t20\tfixture\t0\t+\n'
                self.write(f'datasets/macs3/{name}', raw)
                self.manifest['files'][name] = {'sha256': packager.digest(raw), 'bytes': len(raw)}
            self.manifest['datasets'][dataset] = metadata
        self.write_json('datasets/macs3/manifest.json', self.manifest)
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
                                 'fileBytes': {} if failure else {name: len(b'Output fixture') for name in spec['outputs']},
                                 'inputsFrom': [], 'diagnosticMatched': failure,
                                 'missingOutputs': spec['outputs'] if failure else []} for kind in spec['executions']}
            if spec.get('stdoutFile'):
                for execution in executions.values():
                    execution['stdout'] = packager.digest(b'Standard-output fixture')
                    execution['files'][spec['stdoutFile']] = execution['stdout']
                    execution['fileBytes'][spec['stdoutFile']] = len(b'Standard-output fixture')
            comparisons = {kind: {'equal': True, 'expectedSemanticDigest': biological, 'semanticDigest': biological,
                                  'biologicalDigest': biological, 'invariants': {'fixture': True},
                                  'numericTolerance': self.plan['numericPolicy'], 'numericComparisons': {}} for kind in spec['comparisons']}
            for comparison in comparisons.values():
                actual = executions['wasm']
                files = {artifact['path']: actual['files'][artifact['path']] for artifact in spec['artifacts']}
                sizes = {artifact['path']: actual['fileBytes'][artifact['path']] for artifact in spec['artifacts']}
                invariants = {name: {'fixture': True} for name in files}
                comparison.update(expectedFiles=files, actualFiles=files, expectedFileBytes=sizes, actualFileBytes=sizes,
                                  invariants=invariants, nativeInvariants=invariants, differences=[],
                                  canonicalFiles={name: biological for name in files}, expectedCanonicalFiles={name: biological for name in files})
            record = {'id': case_id, 'tool': 'macs3', 'dataset': spec['_dataset'], 'case': spec['id'],
                      'kind': spec['kind'], 'args': spec['args'], 'status': 'passed', 'executions': executions, 'comparisons': comparisons}
            if spec.get('effect') or spec.get('equivalentTo'):
                changed = bool(spec.get('effect'))
                record['optionEffect'] = {'baseline': 'main', 'before': packager.digest(f'{spec["_dataset"]}-main'.encode()),
                                          'after': biological, 'changed': changed, 'expected': 'changed' if changed else 'equivalent'}
            cases.append(record)
        manifest_sha = packager.digest((self.root / 'datasets/macs3/manifest.json').read_bytes())
        paths = []
        for engine in ('node', 'chromium', 'firefox', 'webkit'):
            report = {'schema': 1, 'engine': engine, 'node': 'v22.fixture', 'browserVersion': 'fixture' if engine != 'node' else None,
                      'status': 'passed', 'runtime': self.hashes(packager.RUNTIME), 'validation': self.hashes(packager.VALIDATION),
                      'manifestSha256': manifest_sha, 'datasets': packager.dataset_identity(self.manifest, manifest_sha),
                      'nativePython': {'version': '3.14.2', 'implementation': 'CPython'},
                      'expectedCases': [case['id'] for case in cases], 'cases': cases, 'builds': {'macs3': self.build}}
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
            if name != 'joblib':
                archive.writestr(f'{name}/fixture.so', packager.WASM_HEADER)
        return stream.getvalue()

    def build_fixture(self):
        for name in ('LICENSE', 'runtime/MACS3.md', 'scripts/build-macs3.py', 'scripts/package-macs3.py',
                     'scripts/test-macs3-demo.mjs', 'scripts/test-macs3-package.mjs', 'scripts/fetch-macs3-test-data.py',
                     'tests/test_macs3_compare.py', 'tests/test_macs3_package.py',
                     'tools/macs3/README.md', 'tools/macs3/LICENSE', 'tools/macs3/LICENSE.hmmlearn',
                     'tools/macs3/LICENSE.numpy', 'tools/macs3/THIRD_PARTY_LICENSES.txt',
                     'tools/macs3/runtime-notices/ws/LICENSE', 'examples/macs3/index.html'):
            self.write(name, f'Labelled source/notice fixture: {name}\n'.encode())
        (self.root / 'scripts/build-macs3.py').chmod(0o755)
        self.write_json('examples/macs3/config.json', {'datasets': {'macs3': {}}, 'commands': {'callpeak': self.plan['cases'][0]}})
        self.write_json('package.json', {'devDependencies': {'playwright': '1.64.0'}})
        self.write_json('package-lock.json', {'name': 'original', 'version': '0.1.0', 'lockfileVersion': 3,
                                            'packages': {'': {'name': 'original', 'version': '0.1.0', 'devDependencies': {'playwright': '1.64.0'}}}})
        data_source = b'Original peak-calling fixture'
        self.write('build/macs3-fixture-sources/source.dat', data_source)
        self.manifest.update(sources=[{'name': 'fixture', 'cachePath': 'build/macs3-fixture-sources/source.dat',
                                                  'sha256': packager.digest(data_source), 'bytes': len(data_source)}],
                             regeneration={'files': {'scripts/fetch-macs3-test-data.py': {
                                 'sha256': self.hashes(['scripts/fetch-macs3-test-data.py'])['scripts/fetch-macs3-test-data.py']}}})
        self.write_json('datasets/macs3/manifest.json', self.manifest)
        core = {'pyodide.mjs': b'// Labelled loader fixture\n', 'pyodide.asm.mjs': b'// Labelled loader fixture\n',
                'pyodide.asm.wasm': packager.WASM_HEADER, 'python_stdlib.zip': b'Labelled stdlib fixture',
                'pyodide-lock.json': packager.json_bytes({'info': {'python': '3.14.2', 'arch': 'wasm32', 'platform': 'emscripten_5_0_3'}})}
        ws = {'package.json': b'{"name":"ws","version":"8.18.3"}', 'LICENSE': b'Labelled ws notice fixture'}
        for directory, files in [('pyodide', core), ('node_modules/ws', ws)]:
            for name, content in files.items():
                self.write(f'dist/macs3/{directory}/{name}', content)
        entrypoint = b'# Labelled MACS3 source launcher fixture\n'
        original_source = self.archive_bytes({'bin/macs3': entrypoint}, 'MACS3-3.0.5/')
        lock = {'schema': 1, 'tool': 'macs3', 'version': '3.0.5', 'python': '3.14.2', 'pyodide': '314.0.7', 'emscripten': '5.0.3',
                'archive': 'macs3.tar.gz', 'sha256': packager.digest(original_source),
                'dependencies': {}, 'buildRequirements': [], 'requiredNotices': [],
                'runtimePackages': ['numpy', 'hmmlearn', 'macs3'],
                'toolchainDownloads': [
                    {'filename': 'macs3-toolchain/' + 'a' * 40 + '-wasm-binaries.tar.xz', 'sha256': 'b' * 64, 'role': 'host-toolchain', 'distribute': False},
                    {'filename': 'macs3-toolchain/node-v22.16.0-linux-x64.tar.xz', 'sha256': 'c' * 64, 'role': 'host-toolchain', 'distribute': False}]}
        self.write('dist/macs3/sources/macs3.tar.gz', original_source)
        self.write('dist/macs3/macs3.py', entrypoint)
        distribution = {'schema': 1, 'tool': 'macs3', 'version': '3.0.5',
                        'pyodide': {'path': 'pyodide/', 'version': '314.0.7', 'python': '3.14.2'}, 'packages': [],
                        'entrypoint': {'filename': 'macs3.py', 'sha256': packager.digest(entrypoint)}}
        for name, version in [('numpy', '2.3.0'), ('hmmlearn', '0.3.3'), ('macs3', '3.0.5')]:
            data = self.wheel_bytes(name, version)
            filename = f'wheels/{name}-{version}.whl'
            self.write(f'dist/macs3/{filename}', data)
            distribution['packages'].append({'name': name, 'version': version, 'filename': filename, 'sha256': packager.digest(data)})
            if name != 'macs3':
                source = f'{name} original source fixture'.encode()
                lock['dependencies'][name] = {'version': version, 'archive': f'{name}.tar.gz', 'sha256': packager.digest(source)}
                self.write(f'dist/macs3/sources/{name}.tar.gz', source)
        for name, version, data in [('pyodide-core', '314.0.7', self.archive_bytes(core, 'pyodide/')),
                                    ('runtime-ws', '8.18.3', self.archive_bytes(ws, 'package/'))]:
            filename = name + '.tar.gz'
            lock['dependencies'][name] = {'version': version, 'archive': filename, 'sha256': packager.digest(data)}
            self.write(f'dist/macs3/sources/{filename}', data)
        lock['dependencies']['runtime-ws']['licensePaths'] = ['tools/macs3/runtime-notices/ws/LICENSE']
        self.write_json('tools/macs3/runtime-notices/manifest.json', [{'destination': 'tools/macs3/runtime-notices/ws/LICENSE',
                                                                        'archive': lock['dependencies']['runtime-ws']['archive'],
                                                                        'sha256': self.hashes(['tools/macs3/runtime-notices/ws/LICENSE'])['tools/macs3/runtime-notices/ws/LICENSE']}])
        destination = 'tools/macs3/scientific-notices/fixture/LICENSE'
        self.write(destination, b'Explicit scientific licence fixture')
        self.write_json('tools/macs3/scientific-notices/manifest.json', [{'destination': destination,
                        'archive': lock['dependencies']['runtime-ws']['archive'], 'archiveSha256': lock['dependencies']['runtime-ws']['sha256'],
                        'member': 'package/LICENSE', 'kind': 'complete-file', 'sha256': self.hashes([destination])[destination]}])
        self.write_json('tools/macs3/sources.lock.json', lock)
        self.write_json('dist/macs3/manifest.json', distribution)
        native = {}
        for kind in ('native', 'patched-native'):
            for name in ('macs3', 'macs3-entry.py', 'macs3-packages/macs3/__init__.py', 'macs3-packages/macs3/fixture.so'):
                self.write(f'build/{kind}/{name}', f'{kind} native package fixture: {name}'.encode())
            files = packager.tree_hashes(f'build/{kind}')
            native[kind] = {'files': files, 'launcher': files['macs3'], 'python': {'version': '3.14.2', 'implementation': 'CPython'}}
        self.write_json('dist/macs3/build-manifest.json', {
            'schema': 1, 'tool': 'macs3', 'nativeEnvironments': native,
            'assets': packager.tree_hashes('dist/macs3'),
            'inputs': self.hashes([f'tools/macs3/{name}' for name in packager.tree_hashes('tools/macs3')] + ['scripts/build-macs3.py'])})
        _, _, self.build = packager.check_build_identity()
        self.reports = self.make_reports()
        return lock

    def refresh_build_fixture(self):
        self.rebind_build_assets()
        _, _, self.build = packager.check_build_identity()
        self.reports = self.make_reports()

    def rebind_build_assets(self):
        path = self.root / 'dist/macs3/build-manifest.json'
        identity = json.loads(path.read_text())
        identity['assets'] = packager.tree_hashes('dist/macs3', exclude=('build-manifest.json',))
        identity['inputs'] = self.hashes(identity['inputs'])
        path.write_bytes(packager.json_bytes(identity))

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
            lambda report: report['cases'][0]['comparisons']['wasm']['actualFiles'].clear(),
            lambda report: report['cases'][0]['comparisons']['wasm']['expectedFileBytes'].update({'out.fastq': 9999}),
            lambda report: report['cases'][0]['comparisons']['wasm']['canonicalFiles'].pop('out.fastq'),
            lambda report: report['cases'][0]['executions']['wasm']['inputsFrom'].append({'case': 'invented'}),
        ]
        for index, change in enumerate(mutations):
            with self.subTest(index=index):
                self.reports[0].write_bytes(original)
                self.change_report(change)
                with self.assertRaises(RuntimeError):
                    self.check()

    def test_tolerated_numerical_differences_require_pinned_policy_and_finite_evidence(self):
        def accepted(report):
            comparison = report['cases'][0]['comparisons']['wasm']
            comparison['semanticDigest'] = packager.digest(b'Tolerated numeric fixture')
            comparison['numericComparisons'] = {'out.fastq': {'fields': 3, 'toleratedDifferences': 1,
                                                             'maxAbsoluteError': 1e-8, 'maxRelativeError': 1e-9,
                                                             'tolerance': self.plan['numericPolicy']['score']}}
        self.change_report(accepted)
        self.assertEqual(set(self.check()), packager.ENGINES)
        original = self.reports[0].read_bytes()
        for change in (
            lambda value: value['numericTolerance']['score'].update(absolute=100),
            lambda value: value['numericComparisons'].clear(),
            lambda value: value['numericComparisons']['out.fastq'].update(maxRelativeError=float('inf')),
            lambda value: value['numericComparisons']['out.fastq'].update(toleratedDifferences=4),
            lambda value: value['numericComparisons'].update(unrequested=value['numericComparisons']['out.fastq']),
        ):
            self.reports[0].write_bytes(original)
            self.change_report(lambda report: change(report['cases'][0]['comparisons']['wasm']))
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
        for name in ('runtime/macs3-execute.mjs', 'validation/macs3/compare.py', 'datasets/macs3/macs3/treatment.bed'):
            with self.subTest(name=name):
                path = self.root / name
                original = path.read_bytes()
                path.write_bytes(original + b'changed')
                with self.assertRaisesRegex(RuntimeError, 'differs|Checksum mismatch'):
                    self.check()
                path.write_bytes(original)
        self.change_report(lambda report: report['builds']['macs3'].update(nativeEnvironment='different'))
        with self.assertRaisesRegex(RuntimeError, 'environments differ'):
            self.check()

    def test_fixture_byte_count_is_checked_independently(self):
        self.manifest['files']['macs3/treatment.bed']['bytes'] += 1
        self.write_json('datasets/macs3/manifest.json', self.manifest)
        with self.assertRaisesRegex(RuntimeError, 'byte count differs'):
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
        def wheel(metadata=b'Name: macs3\nVersion: 3.0.5\n', extension=packager.WASM_HEADER, extra=None):
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w') as archive:
                archive.writestr('macs3-3.0.5.dist-info/METADATA', metadata)
                archive.writestr('macs3/_align.so', extension)
                if extra:
                    archive.writestr(*extra)
            return stream.getvalue()
        packager.check_wheel(wheel(), 'macs3', '3.0.5')
        for content in (wheel(metadata=b'Name: macs3\nVersion: 3.0.4\n'), wheel(extension=b'\x7fELF desktop'),
                        wheel(extension=b'unknown library'), wheel(extra=('../escape', b'bad'))):
            with self.subTest(content=packager.digest(content)), self.assertRaises(RuntimeError):
                packager.check_wheel(content, 'macs3', '3.0.5')
        with self.assertRaisesRegex(RuntimeError, 'contains no WebAssembly extensions'):
            packager.check_wheel(self.wheel_bytes('joblib', '1.5.3'), 'joblib', '1.5.3', require_wasm=True)

    def test_static_libraries_must_contain_only_webassembly_objects(self):
        def archive(contents):
            header = f'{"object.o/":<16}{0:<12}{0:<6}{0:<6}{"644":<8}{len(contents):<10}`\n'.encode()
            return b'!<arch>\n' + header + contents + (b'\n' if len(contents) % 2 else b'')
        packager.check_wasm_archive(archive(packager.WASM_HEADER), 'fixture.a')
        for data in (archive(b'\x7fELF host object'), archive(packager.WASM_HEADER)[:-1], b'!<arch>\n'):
            with self.subTest(data=data), self.assertRaises(RuntimeError):
                packager.check_wasm_archive(data, 'fixture.a')

    def test_official_wheels_require_the_original_catalogue_identity_and_complete_closure(self):
        lock = self.build_fixture()
        distribution = json.loads((self.root / 'dist/macs3/manifest.json').read_text())
        package = distribution['packages'][0]
        original = self.root / 'dist/macs3' / package['filename']
        data = original.read_bytes()
        original.unlink()
        package.update(source='pyodide', filename='pyodide/numpy-2.3.0.whl')
        self.write('dist/macs3/' + package['filename'], data)
        lock['dependencies']['numpy']['wheel'] = {'filename': package['filename'], 'archive': 'macs3-pyodide-wheels/numpy.whl',
                                                 'sha256': packager.digest(data)}
        catalogue = json.loads((self.root / 'dist/macs3/pyodide/pyodide-lock.json').read_text())
        catalogue['packages'] = {'numpy': {'version': '2.3.0', 'file_name': 'numpy-2.3.0.whl',
                                           'sha256': packager.digest(data), 'depends': []}}
        self.write_json('tools/macs3/sources.lock.json', lock)
        self.write_json('dist/macs3/manifest.json', distribution)
        self.write_json('dist/macs3/pyodide/pyodide-lock.json', catalogue)
        self.refresh_build_fixture()
        catalogue['packages']['numpy']['depends'] = ['unbundled-library']
        self.write_json('dist/macs3/pyodide/pyodide-lock.json', catalogue)
        self.rebind_build_assets()
        with self.assertRaisesRegex(RuntimeError, 'dependency closure is incomplete'):
            packager.check_build_identity()
        catalogue['packages']['numpy'].update(depends=[], sha256='f' * 64)
        self.write_json('dist/macs3/pyodide/pyodide-lock.json', catalogue)
        self.rebind_build_assets()
        with self.assertRaisesRegex(RuntimeError, 'pinned Pyodide catalogue'):
            packager.check_build_identity()

    def test_native_runtime_binary_is_rejected_even_with_a_fresh_build_identity(self):
        self.build_fixture()
        self.write('dist/macs3/compiler', b'\x7fELF host executable fixture')
        self.rebind_build_assets()
        with self.assertRaisesRegex(RuntimeError, 'host native binary'):
            packager.check_build_identity()

    def test_modified_entrypoint_cannot_be_relabelled_with_fresh_reports(self):
        self.build_fixture()
        content = b'# Modified MACS3 source launcher fixture\n'
        self.write('dist/macs3/macs3.py', content)
        distribution = json.loads((self.root / 'dist/macs3/manifest.json').read_text())
        distribution['entrypoint']['sha256'] = packager.digest(content)
        self.write_json('dist/macs3/manifest.json', distribution)
        self.refresh_build_fixture()
        with self.assertRaisesRegex(RuntimeError, 'entrypoint differs from the pinned original'):
            packager.collect(self.reports)

    def test_standard_output_case_requires_the_actual_returned_bytes(self):
        self.plan['cases'][0].update(stdinKey='read1', stdoutFile='stdout.fastq', args=['-o', '-', '-'], outputs=['report.json'])
        self.plan['cases'][0]['artifacts'] = [{'path': name, 'kind': 'labelled-fixture'} for name in ('report.json', 'stdout.fastq')]
        for spec in self.plan['cases']:
            if spec['kind'] == 'repeat':
                spec.update(stdoutFile='stdout.fastq', outputs=['report.json'], artifacts=self.plan['cases'][0]['artifacts'])
        self.write_json('validation/macs3/cases.json', self.plan)
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
                with self.assertRaisesRegex(RuntimeError, 'standard-output artifact|output byte counts'):
                    self.check()

    def test_standalone_archive_is_complete_deterministic_and_excludes_host_toolchains(self):
        self.build_fixture()
        with contextlib.redirect_stdout(io.StringIO()):
            output = packager.write_package(self.root / 'releases', self.reports)
            original = output.read_bytes()
            packager.write_package(self.root / 'releases', self.reports)
        self.assertEqual(output.read_bytes(), original)
        self.assertTrue(output.with_suffix('.zip.sha256').read_text().startswith(packager.digest(original)))
        prefix = 'macs3-3.0.5-wasm/'
        with zipfile.ZipFile(output) as archive:
            names = archive.namelist()
            for name in ('runtime/macs3-execute.mjs', 'dist/macs3/node_modules/ws/package.json',
                         'dist/macs3/sources/macs3.tar.gz', 'build/macs3-fixture-sources/source.dat',
                         'validation-reports/webkit.json', 'examples/macs3/config.json'):
                self.assertIn(prefix + name, names)
            self.assertFalse(any('/macs3-toolchain/' in name or '/build/native/' in name or '/datasets/rna-' in name for name in names))
            self.assertFalse(any('/build/downloads/' in name for name in names))
            for line in archive.read(prefix + 'SHA256SUMS').decode().splitlines():
                checksum, name = line.split('  ', 1)
                self.assertEqual(packager.digest(archive.read(prefix + name)), checksum)
            source = json.loads(archive.read(prefix + 'source-manifest.json'))
            self.assertIn('dist/macs3/sources/macs3.tar.gz', source['files'])
            for name, checksum in source['files'].items():
                self.assertEqual(packager.digest(archive.read(prefix + name)), checksum)
            self.assertEqual((archive.getinfo(prefix + 'scripts/build-macs3.py').external_attr >> 16) & 0o777, 0o755)

    def test_native_installed_files_must_match_the_recorded_environment_exactly(self):
        self.build_fixture()
        path = self.root / 'build/native/macs3-packages/macs3/fixture.so'
        original = path.read_bytes()
        path.write_bytes(b'Changed native extension')
        with self.assertRaisesRegex(RuntimeError, 'installed native environment differs'):
            packager.check_build_identity()
        path.write_bytes(original)
        self.write('build/patched-native/macs3-packages/extra.py', b'Unrecorded package')
        with self.assertRaisesRegex(RuntimeError, 'installed native environment differs'):
            packager.check_build_identity()

    def test_stale_build_inputs_and_runtime_assets_prevent_packaging(self):
        self.build_fixture()
        for name in ('scripts/build-macs3.py', 'tools/macs3/runtime-notices/ws/LICENSE', 'dist/macs3/pyodide/pyodide.mjs'):
            with self.subTest(name=name):
                path = self.root / name
                original = path.read_bytes()
                path.write_bytes(original + b'changed')
                with self.assertRaisesRegex(RuntimeError, 'Checksum mismatch|complete compiled distribution'):
                    packager.collect(self.reports)
                path.write_bytes(original)
        self.write('tools/macs3/new-build-helper.py', b'New unrecorded build input')
        with self.assertRaisesRegex(RuntimeError, 'omits source inputs'):
            packager.check_build_identity()

    def test_generated_bytecode_is_excluded_from_source_trees_but_cannot_be_a_build_input(self):
        self.build_fixture()
        cache = 'tools/macs3/__pycache__/native-entry.cpython-312.pyc'
        self.write(cache, b'Generated bytecode fixture')
        self.write('validation/macs3/__pycache__/compare.cpython-312.pyc', b'Generated validation cache fixture')
        entries, _ = packager.collect(self.reports)
        self.assertFalse(any('__pycache__' in name for name in entries))
        path = self.root / 'dist/macs3/build-manifest.json'
        identity = json.loads(path.read_text())
        identity['inputs'][cache] = packager.digest(b'Generated bytecode fixture')
        path.write_bytes(packager.json_bytes(identity))
        with self.assertRaisesRegex(RuntimeError, 'Unsafe package path'):
            packager.check_build_identity()

    def test_scientific_notices_require_source_pins_and_complete_hash_inventory(self):
        lock = self.build_fixture()
        archive_records = packager.archive_records(lock)
        packager.check_scientific_notices(archive_records)
        path = self.root / 'tools/macs3/scientific-notices/manifest.json'
        original = path.read_bytes()
        manifest = json.loads(original)
        manifest[0]['archiveSha256'] = 'f' * 64
        path.write_bytes(packager.json_bytes(manifest))
        with self.assertRaisesRegex(RuntimeError, 'pinned corresponding source'):
            packager.check_scientific_notices(archive_records)
        path.write_bytes(original)
        self.write('tools/macs3/scientific-notices/fixture/UNRECORDED', b'Unrecorded notice fixture')
        with self.assertRaisesRegex(RuntimeError, 'every extracted notice'):
            packager.check_scientific_notices(archive_records)

    def test_fresh_reports_cannot_relabel_altered_original_runtime_or_sources(self):
        self.build_fixture()
        path = self.root / 'dist/macs3/pyodide/pyodide.mjs'
        original = path.read_bytes()
        path.write_bytes(b'Changed upstream runtime')
        self.refresh_build_fixture()
        with self.assertRaisesRegex(RuntimeError, 'runtime files differ from their pinned original archive'):
            packager.collect(self.reports)
        path.write_bytes(original)
        self.write('dist/macs3/sources/hmmlearn.tar.gz', b'Changed original source')
        self.refresh_build_fixture()
        with self.assertRaisesRegex(RuntimeError, 'Checksum mismatch.*hmmlearn'):
            packager.collect(self.reports)

    def test_missing_extra_or_reclassified_archives_cannot_bypass_source_coverage(self):
        lock = self.build_fixture()
        self.write('dist/macs3/sources/unpinned.tar.bz2', b'Unpinned archive')
        self.refresh_build_fixture()
        with self.assertRaisesRegex(RuntimeError, 'missing or unpinned archive'):
            packager.collect(self.reports)
        for mutate in (lambda value: value['toolchainDownloads'][0].update(distribute=True),
                       lambda value: value['toolchainDownloads'][0].update(filename='macs3-toolchain/unrelated.tar.xz'),
                       lambda value: value['toolchainDownloads'].pop()):
            changed = copy.deepcopy(lock)
            mutate(changed)
            with self.assertRaises(RuntimeError):
                packager.archive_records(changed)

    def test_failed_release_gate_preserves_an_existing_zip(self):
        self.build_fixture()
        output = self.root / 'releases'
        output.mkdir()
        previous = output / 'macs3-3.0.5-wasm.zip'
        previous.write_bytes(b'Previous reviewed release fixture')
        with self.assertRaisesRegex(RuntimeError, 'Missing passing reports'):
            packager.write_package(output, self.reports[:-1])
        self.assertEqual(previous.read_bytes(), b'Previous reviewed release fixture')
        self.assertEqual(list(output.iterdir()), [previous])


if __name__ == '__main__':
    unittest.main()
