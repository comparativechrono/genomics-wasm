"""Release-gate adversarial tests; labelled fixtures are not tool validation evidence."""
import base64
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SPEC = importlib.util.spec_from_file_location('metagenomics_packager', Path(__file__).resolve().parents[1] / 'scripts/package-metagenomics.py')
packager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packager)


def tar_bytes(files):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w:gz') as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return stream.getvalue()


class PackageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        root_patch = patch.object(packager, 'ROOT', self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        for name in ('LICENSE', *{name for paths in packager.RUNTIME.values() for name in paths}, *packager.VALIDATION,
                     'runtime/README.md', 'runtime/KRAKEN2.md', 'runtime/BRACKEN.md', 'scripts/setup-emsdk.sh',
                     'scripts/package-metagenomics.py', 'scripts/test-metagenomics-demo.mjs', 'scripts/fetch-metagenomics-test-data.py',
                     'tests/test_metagenomics_compare.py', 'tests/test_metagenomics_package.py', 'tests/runtime.test.mjs',
                     'tests/kraken2.test.mjs', 'tests/bracken.test.mjs', 'tests/fixtures/mock-tool.mjs',
                     'examples/metagenomics/index.html', 'examples/metagenomics/app.mjs', 'examples/metagenomics/README.md',
                     'validation/metagenomics/README.md', 'datasets/metagenomics/README.md'):
            self.write(name, f'Labelled source fixture: {name}\n'.encode())
        self.write('runtime/README.md', b'# Runtime fixture\n\n## Interface\nFixture interface.\n')
        self.write_json('package.json', {'devDependencies': {'playwright': '1.64.0'}})
        self.write_json('package-lock.json', {'name': 'original', 'version': '0.1.0', 'lockfileVersion': 3,
            'packages': {'': {'name': 'original', 'version': '0.1.0', 'devDependencies': {'playwright': '1.64.0'}}}})
        self.plan = {'schema': 1, 'datasets': ['zymo'], 'tools': {}}
        for tool in packager.TOOLS:
            baseline = {'id': 'baseline', 'kind': 'success', 'runner': tool, 'args': ['--fixture', 'out.tsv'],
                'outputs': ['out.tsv'], 'executions': ['native', 'patched-native', 'wasm'],
                'artifacts': [{'path': 'out.tsv', 'kind': 'classifications', 'minimumRecords': 2, 'minimumClassified': 1, 'minimumUnclassified': 1}],
                'comparisons': ['native-contract', 'patched-native', 'wasm']}
            self.plan['tools'][tool] = {'cases': [copy.deepcopy(baseline),
                {**baseline, 'id': 'filtered', 'effect': 'baseline', 'args': ['--filter', 'out.tsv']},
                {**baseline, 'id': 'cross-build', 'equivalentTo': 'baseline',
                 'inputsFrom': [{'case': 'baseline', 'path': 'out.tsv', 'as': 'input.tsv', 'target': 'native'}]},
                {**baseline, 'id': 'repeat', 'kind': 'repeat', 'executions': ['wasm'], 'comparisons': ['wasm'], 'baseline': 'baseline'},
                {**baseline, 'id': 'missing', 'kind': 'failure', 'args': ['missing'], 'comparisons': [],
                 'diagnosticRegex': 'diagnostic', 'expectedExitCode': 1}]}
        self.write_json('validation/metagenomics/cases.json', self.plan)
        self.write_json('examples/metagenomics/config.json', {'tools': {tool: {} for tool in packager.TOOLS}, 'datasets': {'zymo': {}}})
        self.kraken_build()
        self.bracken_build()
        source = b'Original public paired-read archive fixture'
        self.write('build/metagenomics-sources/reads.tar.gz', source)
        self.manifest = {'schema_version': 1, 'datasets': {'zymo': {'observations': {'fixture': True}}}, 'files': {},
            'sources': {'reads': {'cachePath': 'build/metagenomics-sources/reads.tar.gz', 'sha256': packager.digest(source), 'bytes': len(source)}},
            'regeneration': {'files': self.hashes(['scripts/fetch-metagenomics-test-data.py'])}}
        for name in ('reads_1.fastq', 'reads_2.fastq', 'db/library/reference.fna', 'db/taxonomy/nodes.dmp'):
            data = f'Biological data fixture: {name}'.encode()
            self.write('datasets/metagenomics/zymo/' + name, data)
            self.manifest['files']['zymo/' + name] = {'sha256': packager.digest(data), 'bytes': len(data)}
        self.write_json('datasets/metagenomics/manifest.json', self.manifest)
        self.builds = {tool: packager.check_build_identity(tool)[2] for tool in packager.TOOLS}
        self.reports = {tool: self.make_reports(tool) for tool in packager.TOOLS}

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def write_json(self, name, value):
        return self.write(name, packager.json_bytes(value))

    def hashes(self, names):
        return {name: packager.digest((self.root / name).read_bytes()) for name in names}

    def install_common(self, tool, lock, archives):
        self.write_json(f'tools/{tool}/sources.lock.json', lock)
        for name in ('README.md', 'browser.patch', 'build.py', *lock['requiredNotices']):
            data = f'{tool} {name} source fixture'.encode()
            self.write(f'tools/{tool}/{name}', data)
            if name in lock['requiredNotices']:
                self.write(f'dist/{tool}/notices/{name}', data)
        self.write(f'scripts/build-{tool}.sh', b'#!/bin/sh\n# Labelled build recipe fixture\n').chmod(0o755)
        for name, data in archives.items():
            self.write(f'dist/{tool}/sources/{name}', data)

    def finish_build(self, tool, environments=None):
        inputs = [f'tools/{tool}/{name}' for name in packager.tree_hashes(f'tools/{tool}', source_tree=True)] + [f'scripts/build-{tool}.sh']
        identity = {'schema': 1, 'tool': tool, 'assets': packager.tree_hashes(f'dist/{tool}', exclude=('build-manifest.json',)),
                    'inputs': self.hashes(inputs), 'nativeFiles': {kind: packager.native_tree(tool, kind) for kind in ('native', 'patched-native')}}
        if environments is not None:
            identity['nativeEnvironments'] = environments
        self.write_json(f'dist/{tool}/build-manifest.json', identity)

    def kraken_build(self):
        archive = tar_bytes({'kraken2/source.cc': b'Labelled C++ fixture'})
        lock = {'schema': 1, 'tool': 'kraken2', 'version': '2.17.2', 'upstreamVersion': '2.17.1', 'emscripten': '3.1.64',
                'archive': 'kraken2.tar.gz', 'sha256': packager.digest(archive), 'dependencies': {},
                'programs': list(packager.PROGRAMS['kraken2']), 'requiredNotices': ['LICENSE', 'THIRD_PARTY_LICENSES.txt']}
        self.install_common('kraken2', lock, {'kraken2.tar.gz': archive})
        for name in lock['programs']:
            self.write(f'dist/kraken2/{name}.mjs', b'// Labelled JS fixture\n')
            self.write(f'dist/kraken2/{name}.wasm', packager.WASM_HEADER)
            for kind in ('native', 'patched-native'):
                self.write(f'build/{kind}/kraken2-package/{name}', f'{kind} {name} binary fixture'.encode())
                self.write(f'build/{kind}/kraken2', b'Launcher fixture')
        self.finish_build('kraken2')

    def bracken_build(self):
        python = {'est_abundance.py': b'# Original abundance script fixture\n', 'generate_kmer_distribution.py': b'# Original distribution script fixture\n'}
        pyodide = {name: b'Pyodide core fixture' for name in packager.PYODIDE_FILES}
        pyodide['pyodide.asm.wasm'] = packager.WASM_HEADER
        pyodide['pyodide-lock.json'] = packager.json_bytes({'info': {'python': '3.14.2', 'arch': 'wasm32', 'platform': 'emscripten_5_0_3'}})
        ws = {'package.json': b'{"name":"ws","version":"8.18.3"}', 'index.js': b'// Original ws fixture', 'LICENSE': b'ws notice fixture'}
        archives = {'bracken.tar.gz': tar_bytes({f'bracken/src/{name}': data for name, data in python.items()}),
                    'pyodide.tar.gz': tar_bytes({f'pyodide/{name}': data for name, data in pyodide.items()}),
                    'ws.tgz': tar_bytes({f'package/{name}': data for name, data in ws.items()}), 'emscripten.tar.gz': b'Runtime original source fixture'}
        lock = {'schema': 1, 'tool': 'bracken', 'version': '3.1', 'python': '3.14.2', 'pyodide': '314.0.7', 'emscripten': '3.1.64',
                'archive': 'bracken.tar.gz', 'sha256': packager.digest(archives['bracken.tar.gz']),
                'dependencies': {key: {'archive': name, 'sha256': packager.digest(archives[name]), 'version': version}
                    for key, name, version in [('pyodide-core', 'pyodide.tar.gz', '314.0.7'), ('runtime-ws', 'ws.tgz', '8.18.3'), ('runtime-emscripten', 'emscripten.tar.gz', '5.0.3')]},
                'requiredNotices': ['LICENSE', 'THIRD_PARTY_LICENSES.txt']}
        self.install_common('bracken', lock, archives)
        notice_path, notice = 'tools/bracken/runtime-notices/pyodide/LICENSE', b'Original runtime notice fixture'
        self.write(notice_path, notice)
        self.write_json('tools/bracken/runtime-sources.json', [dict(lock['dependencies']['pyodide-core'], licensePaths=[notice_path])])
        self.write_json('tools/bracken/runtime-notices/manifest.json', [{'destination': notice_path, 'sha256': packager.digest(notice)}])
        for directory, files in [('pyodide', pyodide), ('node_modules/ws', ws), ('python', python)]:
            for name, data in files.items():
                self.write(f'dist/bracken/{directory}/{name}', data)
        self.write('dist/bracken/kmer2read_distr.mjs', b'// Labelled module fixture')
        self.write('dist/bracken/kmer2read_distr.wasm', packager.WASM_HEADER)
        self.write_json('dist/bracken/manifest.json', {'schema': 1, 'tool': 'bracken', 'version': '3.1', 'upstreamVersionText': '3.0.1',
            'pyodide': {'path': 'pyodide/', 'version': '314.0.7', 'python': '3.14.2'},
            'scripts': {name: {'path': 'python/' + name, 'sha256': packager.digest(data)} for name, data in python.items()}})
        environments = {}
        for kind in ('native', 'patched-native'):
            for name in ('bracken', 'bracken-build', 'kmer2read_distr'):
                self.write(f'build/{kind}/{name}', f'{kind} {name} binary fixture'.encode())
            for name, data in python.items():
                self.write(f'build/{kind}/bracken-package/src/{name}', data)
            environments[kind] = {'python': {'version': '3.14.2', 'implementation': 'CPython', 'executableSha256': packager.digest(b'Pinned Python binary fixture')},
                                  'scripts': {name: packager.digest(data) for name, data in python.items()}}
        self.finish_build('bracken', environments)

    def make_reports(self, tool):
        cases = []
        output = b'Classification result fixture'
        output_sha = packager.digest(output)
        for case_id, spec in packager.planned_cases(tool).items():
            failure = spec['kind'] == 'failure'
            biological = packager.digest(('filtered' if spec['id'] == 'filtered' else 'baseline').encode())
            executions = {kind: {'exitCode': int(failure), 'canonicalExitCode': int(failure), 'signal': None,
                'stdout': packager.digest(b''), 'stderr': packager.digest(b'Diagnostic fixture') if failure else packager.digest(b''),
                'diagnosticMatched': True if failure else None, 'missingOutputs': ['out.tsv'] if failure else [],
                'files': {} if failure else {'out.tsv': output_sha}, 'fileBytes': {} if failure else {'out.tsv': len(output)},
                'inputsFrom': [{**source, 'sourceTarget': source.get('target', kind), 'sourceSha256': output_sha, 'sourceBytes': len(output),
                               'sha256': output_sha, 'bytes': len(output)} for source in spec.get('inputsFrom', [])]} for kind in spec['executions']}
            invariants = {'out.tsv': {'records': 2, 'classified': 1, 'unclassified': 1}}
            comparisons = {kind: {'equal': True, 'expectedSemanticDigest': biological, 'semanticDigest': biological, 'biologicalDigest': biological,
                'invariants': copy.deepcopy(invariants), 'nativeInvariants': copy.deepcopy(invariants),
                'nativeAnomalies': {'expected': [], 'actual': []}} for kind in spec['comparisons']}
            record = {'id': case_id, 'tool': tool, 'dataset': spec['_dataset'], 'case': spec['id'], 'kind': spec['kind'],
                      'runner': spec['runner'], 'args': spec['args'], 'status': 'passed', 'executions': executions, 'comparisons': comparisons}
            if spec.get('effect') or spec.get('equivalentTo'):
                changed = bool(spec.get('effect'))
                record['optionEffect'] = {'baseline': 'baseline', 'before': packager.digest(b'baseline'), 'after': biological,
                                         'changed': changed, 'expected': 'changed' if changed else 'equivalent'}
            cases.append(record)
        manifest_sha = packager.digest((self.root / 'datasets/metagenomics/manifest.json').read_bytes())
        datasets = {'zymo': {'manifestSha256': manifest_sha, 'files': {name: info['sha256'] for name, info in self.manifest['files'].items()},
                             'observations': self.manifest['datasets']['zymo']['observations']}}
        paths = []
        for engine in ('node', 'chromium', 'firefox', 'webkit'):
            report = {'schema': 1, 'tool': tool, 'engine': engine, 'node': 'v22.fixture', 'python': 'Python 3.12.14',
                      'browserVersion': 'fixture' if engine != 'node' else None, 'status': 'passed',
                      'builds': {name: self.builds[name] for name in packager.BUNDLES[tool]},
                      'runtime': self.hashes(packager.RUNTIME[tool]), 'validation': self.hashes(packager.VALIDATION),
                      'manifestSha256': manifest_sha, 'datasets': datasets, 'expectedCases': [case['id'] for case in cases], 'cases': cases}
            if tool == 'bracken':
                report['nativePython'] = {'version': '3.14.2', 'implementation': 'CPython', 'sha256': packager.digest(b'Pinned Python binary fixture')}
            paths.append(self.write_json(f'reports/{engine}/summary-{tool}-all.json', report))
        return paths

    def change_report(self, change, tool='kraken2', index=0):
        path = self.reports[tool][index]
        value = json.loads(path.read_text())
        change(value)
        path.write_bytes(packager.json_bytes(value))

    def all_reports(self):
        return self.reports['kraken2'] + self.reports['bracken']

    def assert_bad_report(self, mutation, pattern=None):
        self.reports['kraken2'] = self.make_reports('kraken2')
        self.change_report(mutation)
        with self.assertRaisesRegex(RuntimeError, pattern or '.'):
            packager.check_reports('kraken2', self.reports['kraken2'], self.builds)

    def test_independent_zip_contents_checksums_and_determinism(self):
        for tool in packager.TOOLS:
            with self.subTest(tool=tool), contextlib.redirect_stdout(io.StringIO()):
                output = packager.write_package(tool, self.root / 'releases', self.all_reports())
                original = output.read_bytes()
                packager.write_package(tool, self.root / 'releases', self.all_reports())
                self.assertEqual(original, output.read_bytes())
                self.assertTrue(output.with_suffix('.zip.sha256').read_text().startswith(packager.digest(original)))
                prefix = f'{tool}-' + ('2.17.2' if tool == 'kraken2' else '3.1') + '-wasm/'
                with zipfile.ZipFile(output) as archive:
                    self.assertIsNone(archive.testzip())
                    contents = {name[len(prefix):]: archive.read(name) for name in archive.namelist()}
                    self.assertTrue(all(name.startswith(prefix) for name in archive.namelist()))
                    self.assertFalse(any(name.startswith(('build/native/', 'build/patched-native/', 'build/downloads/')) for name in contents))
                    expected = {line[66:]: line[:64] for line in contents['SHA256SUMS'].decode().splitlines()}
                    self.assertEqual(set(expected), set(contents) - {'SHA256SUMS'})
                    self.assertTrue(all(packager.digest(contents[name]) == sha for name, sha in expected.items()))
                    sources = json.loads(contents['source-manifest.json'])['files']
                    self.assertTrue(all(packager.digest(contents[name]) == sha for name, sha in sources.items()))
                    self.assertEqual(set(json.loads(contents['examples/metagenomics/config.json'])['tools']), set(packager.BUNDLES[tool]))
                    release = json.loads(contents['release.json'])
                    self.assertEqual(release['bundledTools'], list(packager.BUNDLES[tool]))
                    for name in packager.BUNDLES[tool]:
                        self.assertEqual(set(release['reports'][name]), packager.ENGINES)
                        self.assertIn(f'tests/{name}.test.mjs', contents)
                    if tool == 'kraken2':
                        self.assertFalse(any('/bracken/' in name or name.startswith('runtime/bracken') for name in contents))
                    else:
                        self.assertIn('dist/bracken/node_modules/ws/index.js', contents)
                        self.assertIn('dist/kraken2/classify.wasm', contents)

    def test_bracken_needs_both_complete_four_engine_suites(self):
        with self.assertRaisesRegex(RuntimeError, 'missing passing reports'):
            packager.collect('bracken', self.reports['bracken'])
        for tool in packager.TOOLS:
            with self.subTest(tool=tool), self.assertRaisesRegex(RuntimeError, 'missing passing reports'):
                packager.collect('bracken', [path for path in self.all_reports() if path != self.reports[tool][-1]])

    def test_duplicate_or_failed_engine_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'duplicate'):
            packager.check_reports('kraken2', self.reports['kraken2'] * 2, self.builds)
        self.assert_bad_report(lambda r: r.update(status='failed'), 'not a passing')

    def test_entire_case_plan_must_be_present_and_unique(self):
        for change in [lambda r: r['cases'].pop(), lambda r: r['cases'].append(r['cases'][0]),
                       lambda r: r['expectedCases'].pop(), lambda r: r['cases'][0].update(status='failed')]:
            self.assert_bad_report(change, 'complete passing case plan')

    def test_stale_runtime_validator_data_or_build_rejected(self):
        for field in ('runtime', 'validation', 'datasets', 'builds'):
            self.assert_bad_report(lambda r, field=field: r.update({field: {}}))

    def test_native_python_identity_is_pinned_and_consistent(self):
        for mutation in [lambda r: r['nativePython'].update(version='3.13.0'),
                         lambda r: r['nativePython'].update(sha256=packager.digest(b'Other binary'))]:
            self.reports['bracken'] = self.make_reports('bracken')
            self.change_report(mutation, 'bracken')
            with self.assertRaisesRegex(RuntimeError, 'interpreter identity|different native Python|source build identity'):
                packager.check_reports('bracken', self.reports['bracken'], self.builds)

    def test_case_runner_args_outputs_and_exit_must_match(self):
        for change in [lambda r: r['cases'][0].update(runner='other'), lambda r: r['cases'][0].update(args=['other']),
                       lambda r: r['cases'][0]['executions']['wasm']['files'].clear(),
                       lambda r: r['cases'][0]['executions']['wasm'].update(exitCode=1, canonicalExitCode=1),
                       lambda r: r['cases'][0]['executions']['wasm'].update(signal='SIGSEGV')]:
            self.assert_bad_report(change)

    def test_failure_requires_actual_diagnostic_and_exit_parity(self):
        for change in [lambda e: e.update(diagnosticMatched=False), lambda e: e.update(exitCode=2, canonicalExitCode=2),
                       lambda e: e.update(stdout=packager.digest(b''), stderr=packager.digest(b''))]:
            self.assert_bad_report(lambda r: change(r['cases'][-1]['executions']['wasm']))

    def test_semantic_equality_alone_cannot_replace_native_baseline(self):
        def mutation(report):
            report['cases'][0]['comparisons']['wasm'].update(semanticDigest=packager.digest(b'other'), expectedSemanticDigest=packager.digest(b'other'))
        self.assert_bad_report(mutation, 'unchanged native baseline')

    def test_cross_build_input_provenance_cannot_switch_targets_or_bytes(self):
        for change in [lambda s: s.update(sourceTarget='wasm'), lambda s: s.update(sha256=packager.digest(b'other'))]:
            self.assert_bad_report(lambda r: change(r['cases'][2]['executions']['wasm']['inputsFrom'][0]), 'derived input bytes')

    def test_vacuous_classification_and_option_effect_rejected(self):
        for change in [lambda r: r['cases'][0]['comparisons']['wasm']['invariants']['out.tsv'].update(classified=0),
                       lambda r: r['cases'][1]['optionEffect'].update(changed=False)]:
            self.assert_bad_report(change)

    def test_kraken_abi_and_abundance_invariants(self):
        for kind, counts in [('kraken-options', {'fields': 9, 'bytes': 64}), ('bracken-abundance', {'records': 1, 'estimatedReads': 2, 'addedReads': 1})]:
            spec, comparison = {'artifacts': [{'kind': kind, 'path': 'out'}]}, {'invariants': {'out': counts}, 'nativeInvariants': {'out': counts}}
            packager.check_invariants('fixture', spec, comparison)
            counts[next(iter(counts))] = 0
            with self.assertRaises(RuntimeError):
                packager.check_invariants('fixture', spec, comparison)

    def test_source_or_native_environment_changes_rejected(self):
        for name in ('tools/kraken2/browser.patch', 'build/native/kraken2-package/classify', 'dist/kraken2/build_db.wasm'):
            path = self.root / name
            before = path.read_bytes()
            path.write_bytes(before + b'changed')
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                packager.check_build_identity('kraken2')
            path.write_bytes(before)
        self.write('build/patched-native/bracken-package/new.py', b'Unrecorded execution file')
        with self.assertRaisesRegex(RuntimeError, 'execution files differ'):
            packager.check_build_identity('bracken')

    def test_source_cache_is_not_execution_environment(self):
        self.write('tools/kraken2/__pycache__/build.pyc', b'Transient source cache')
        packager.check_build_identity('kraken2')
        self.write('build/native/kraken2-package/extra', b'Unexpected installed executable')
        with self.assertRaisesRegex(RuntimeError, 'execution environment differs'):
            packager.check_build_identity('kraken2')

    def test_original_sources_notices_runtime_and_python_are_required(self):
        for name in ('dist/bracken/sources/bracken.tar.gz', 'dist/bracken/notices/LICENSE', 'dist/bracken/python/est_abundance.py',
                     'dist/bracken/node_modules/ws/index.js', 'dist/bracken/pyodide/pyodide.mjs'):
            path = self.root / name
            before = path.read_bytes()
            path.write_bytes(before + b'changed')
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                packager.check_sources('bracken', packager.read_json('tools/bracken/sources.lock.json'))
            path.write_bytes(before)

    def test_unpinned_source_archive_and_data_source_changes_rejected(self):
        self.write('dist/kraken2/sources/unpinned.tar.bz2', b'Unknown source fixture')
        with self.assertRaisesRegex(RuntimeError, 'unpinned'):
            packager.check_sources('kraken2', packager.read_json('tools/kraken2/sources.lock.json'))
        (self.root / 'dist/kraken2/sources/unpinned.tar.bz2').unlink()
        self.write('build/metagenomics-sources/reads.tar.gz', b'Wrong bytes')
        with self.assertRaisesRegex(RuntimeError, 'Checksum mismatch'):
            packager.collect('kraken2', self.all_reports())

    def test_traversing_paths_and_symlinks_rejected(self):
        for name in ('../escape', '/absolute', 'a/../escape', 'a//b', 'C:/drive', 'a\\b', '.git/config'):
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                packager.relative_name(name)
        (self.root / 'dist/kraken2/linked').symlink_to(self.root / 'LICENSE')
        with self.assertRaisesRegex(RuntimeError, 'symlink'):
            packager.check_build_identity('kraken2')

    def test_final_newline_transform_has_exact_source_length_and_byte_evidence(self):
        self.plan['tools']['kraken2']['cases'][2]['inputsFrom'][0]['transform'] = 'remove-final-newline'
        self.write_json('validation/metagenomics/cases.json', self.plan)
        self.reports['kraken2'] = self.make_reports('kraken2')
        def valid(report):
            for execution in report['cases'][2]['executions'].values():
                source = execution['inputsFrom'][0]
                source.update(sha256=packager.digest(b'Classification result fixture without LF'), bytes=source['sourceBytes'] - 1, removedFinalByte=10)
        for index in range(4):
            self.change_report(valid, index=index)
        packager.check_reports('kraken2', self.reports['kraken2'], self.builds)
        self.change_report(lambda r: r['cases'][2]['executions']['wasm']['inputsFrom'][0].update(removedFinalByte=13))
        with self.assertRaisesRegex(RuntimeError, 'derived input bytes'):
            packager.check_reports('kraken2', self.reports['kraken2'], self.builds)

    def test_native_eof_exception_is_narrow_and_bound_to_raw_output(self):
        fragment = b'\0\t-21\t\t\n'
        raw = b'genuine\t1\t\t1:2\n' + fragment
        anomaly = {'path': 'raw.tsv', 'offset': len(raw) - len(fragment), 'length': len(fragment),
                   'rawSha256': packager.digest(raw), 'fragmentSha256': packager.digest(fragment), 'base64': base64.b64encode(fragment).decode(),
                   'reason': 'Bracken 3.1 unchanged native kraken_processing.cpp reads beyond the terminal newline; one non-biological EOF phantom excluded'}
        execution = {'files': {'raw.tsv': packager.digest(raw)}, 'fileBytes': {'raw.tsv': len(raw)}}
        spec = {'artifacts': [{'path': 'raw.tsv', 'kind': 'bracken-read-distribution', 'nativeEofArtifact': True}]}
        comparison = {'nativeAnomalies': {'expected': [anomaly], 'actual': []}}
        packager.check_native_anomalies('fixture', spec, comparison, 'wasm', execution, execution)
        for change in [lambda a: a.update(offset=1), lambda a: a.update(rawSha256=packager.digest(b'other')),
                       lambda a: a.update(base64=base64.b64encode(b'not a phantom').decode())]:
            bad = copy.deepcopy(comparison)
            change(bad['nativeAnomalies']['expected'][0])
            with self.assertRaises(RuntimeError):
                packager.check_native_anomalies('fixture', spec, bad, 'wasm', execution, execution)
        comparison['nativeAnomalies']['actual'] = [anomaly]
        with self.assertRaisesRegex(RuntimeError, 'unplanned native EOF exception'):
            packager.check_native_anomalies('fixture', spec, comparison, 'wasm', execution, execution)

    def test_workflow_stage_hash_edges_are_replayed(self):
        self.plan['fixtureInputs'] = ['reads_1.fastq']
        self.write_json('validation/metagenomics/cases.json', self.plan)
        initial = {'/work/reads_1.fastq': self.manifest['files']['zymo/reads_1.fastq']['sha256']}
        first, final = packager.digest(b'Actual first-stage bytes'), packager.digest(b'Actual final-stage bytes')
        spec = {'workflowStages': ['kraken2', 'generate_kmer_distribution.py']}
        stages = [{'tool': 'kraken2', 'inputs': initial, 'outputHashes': {'/work/intermediate': first}, 'exitCode': 0, 'missingOutputs': []},
                  {'tool': 'generate_kmer_distribution.py', 'inputs': {**initial, '/work/intermediate': first},
                   'outputHashes': {'/work/out': final}, 'exitCode': 0, 'missingOutputs': []}]
        execution = {'inputsFrom': [], 'workflow': {'stages': stages}, 'files': {'out': final},
                     'workflowEvidence': {'stageCount': 2, 'tools': spec['workflowStages'], 'dependencyEdges': 3, 'retainedOutputs': 1, 'retainedOutputHashes': {'out': final}}}
        packager.check_workflow('fixture', spec, execution, self.manifest, 'zymo')
        stages[1]['inputs']['/work/intermediate'] = packager.digest(b'Wrong intermediate')
        with self.assertRaisesRegex(RuntimeError, 'preceding bytes'):
            packager.check_workflow('fixture', spec, execution, self.manifest, 'zymo')


if __name__ == '__main__':
    unittest.main()
