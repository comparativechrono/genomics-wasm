"""Adversarial packaging tests. Labelled synthetic inputs are NOT validation evidence."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from uuid import uuid4

SPEC = importlib.util.spec_from_file_location('plink2_packager', Path(__file__).resolve().parents[1] / 'scripts/package-plink2.py')
packager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packager)


class PackageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        root_patch = patch.object(packager, 'ROOT', self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        for name in ('LICENSE', *{name for paths in packager.RUNTIME.values() for name in paths}, *packager.VALIDATION,
                     'runtime/README.md', 'runtime/PLINK2.md', 'scripts/setup-emsdk.sh',
                     'scripts/package-plink2.py', 'scripts/test-plink2-demo.mjs', 'scripts/fetch-plink2-test-data.py',
                     'tests/test_plink2_compare.py', 'tests/test_plink2_package.py', 'tests/runtime.test.mjs',
                     'tests/plink2.test.mjs', 'tests/fixtures/mock-tool.mjs',
                     *{name for paths in packager.TEST_FIXTURES.values() for name in paths},
                     'examples/plink2/index.html', 'examples/plink2/app.mjs', 'examples/plink2/README.md',
                     'validation/plink2/README.md', 'datasets/plink2/README.md'):
            self.write(name, f'Labelled source fixture: {name}\n'.encode())
        self.write('runtime/README.md', b'# Runtime fixture\n\n## Interface\nFixture interface.\n')
        self.write_json('package.json', {'devDependencies': {'playwright': '1.64.0'}})
        self.write_json('package-lock.json', {'name': 'original', 'version': '0.1.0', 'lockfileVersion': 3,
            'packages': {'': {'name': 'original', 'version': '0.1.0', 'devDependencies': {'playwright': '1.64.0'}}}})
        self.plan = {'schema': 1, 'datasets': list(packager.TOOLS), 'fixtureInputs': [], 'tools': {}}
        for tool in packager.TOOLS:
            baseline = {'id': 'baseline', 'kind': 'success', 'runner': tool, 'args': ['--fixture', 'out.tsv'],
                'outputs': ['out.tsv'], 'executions': ['native', 'patched-native', 'wasm'],
                'artifacts': [{'path': 'out.tsv', 'kind': 'exact', 'minimumRecords': 1}],
                'comparisons': ['native-contract', 'patched-native', 'wasm']}
            self.plan['tools'][tool] = {'datasets': [tool], 'fixtureInputs': ['input.fa'], 'cases': [copy.deepcopy(baseline),
                {**baseline, 'id': 'filtered', 'effect': 'baseline', 'args': ['--filter', 'out.tsv']},
                {**baseline, 'id': 'cross-build', 'equivalentTo': 'baseline',
                 'inputsFrom': [{'case': 'baseline', 'path': 'out.tsv', 'as': 'input.tsv', 'target': 'native'}]},
                {**baseline, 'id': 'repeat', 'kind': 'repeat', 'executions': ['wasm'], 'comparisons': ['wasm'], 'baseline': 'baseline'},
                {**baseline, 'id': 'missing', 'kind': 'failure', 'args': ['missing'], 'comparisons': [],
                 'diagnosticRegex': 'diagnostic', 'expectedExitCode': 1}]}
            self.plan['fixtureInputs'].append(tool + '/input.fa')
            self.install_build(tool)
        self.write_json('validation/plink2/cases.json', self.plan)
        self.write_json('examples/plink2/config.json', {'tools': {tool: {'requiresIsolation': True, 'waitForExit': True} for tool in packager.TOOLS},
            'datasets': {tool: {'path': f'../../datasets/plink2/{tool}/', 'files': ['input.fa']} for tool in packager.TOOLS}})
        source = b'Original public biological source archive fixture'
        self.write('build/plink2-sources/source.tar.gz', source)
        self.manifest = {'schema_version': 1, 'datasets': {tool: {'observations': {'fixture': True}} for tool in packager.TOOLS},
            'files': {}, 'sources': [{'name': 'reference', 'cachePath': 'build/plink2-sources/source.tar.gz',
                'sha256': packager.digest(source), 'bytes': len(source)}],
            'regeneration': {'files': self.hashes(['scripts/fetch-plink2-test-data.py'])}}
        for tool in packager.TOOLS:
            data = f'Biological data fixture: {tool}'.encode()
            self.write(f'datasets/plink2/{tool}/input.fa', data)
            self.manifest['files'][f'{tool}/input.fa'] = {'sha256': packager.digest(data), 'bytes': len(data)}
        readme = (self.root / 'datasets/plink2/README.md').read_bytes()
        self.manifest['files']['README.md'] = {'sha256': packager.digest(readme), 'bytes': len(readme)}
        self.write_json('datasets/plink2/manifest.json', self.manifest)
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

    def install_build(self, tool):
        archive = f'Labelled original {tool} archive fixture'.encode()
        lock = {'schema': 1, 'tool': tool, 'version': packager.VERSIONS[tool], 'emscripten': '3.1.64', 'profile': 'nolapack',
                'archive': tool + '.tar.gz', 'sha256': packager.digest(archive),
                'programs': list(packager.PROGRAMS[tool]), 'requiredNotices': ['LICENSE', 'THIRD_PARTY_LICENSES.txt']}
        self.write_json(f'tools/{tool}/sources.lock.json', lock)
        for name in ('README.md', 'browser.patch', 'build.py', *lock['requiredNotices']):
            data = f'{tool} {name} source fixture'.encode()
            self.write(f'tools/{tool}/{name}', data)
            if name in lock['requiredNotices']:
                self.write(f'dist/{tool}/notices/{name}', data)
        self.write(f'scripts/build-{tool}.sh', b'#!/bin/sh\n# Labelled build recipe fixture\n').chmod(0o755)
        self.write(f'dist/{tool}/sources/{tool}.tar.gz', archive)
        for name in lock['programs']:
            self.write(f'dist/{tool}/{name}.mjs', b'// Labelled JS fixture\n')
            self.write(f'dist/{tool}/{name}.wasm', packager.WASM_HEADER)
            self.write(f'dist/{tool}/{name}.worker.mjs', b'// Labelled pthread worker fixture\n')
            for kind in ('native', 'patched-native'):
                path = name
                self.write(f'build/{kind}/{path}', f'{kind} {name} binary fixture'.encode())
        self.finish_build(tool)

    def finish_build(self, tool):
        inputs = [f'tools/{tool}/{name}' for name in packager.tree_hashes(f'tools/{tool}', source_tree=True)] + [f'scripts/build-{tool}.sh']
        self.write_json(f'dist/{tool}/build-manifest.json', {'schema': 1, 'tool': tool,
            'assets': packager.tree_hashes(f'dist/{tool}', exclude=('build-manifest.json',)), 'inputs': self.hashes(inputs),
            'nativeFiles': {kind: packager.native_tree(tool, kind) for kind in ('native', 'patched-native')}})

    def make_reports(self, tool):
        cases = []
        output = b'Biological result fixture'
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
            for kind, execution in executions.items():
                execution['inputs'] = {'input.fa': self.manifest['files'][tool + '/input.fa']['sha256']}
                for source in execution['inputsFrom']:
                    execution['inputs'][source['as']] = source['sha256']
                execution['stdin'] = None
                if kind != 'wasm':
                    execution['command'] = {'program': f'build/{kind}/plink2', 'args': spec['args']}
            invariants = {'out.tsv': {'records': 1, 'bytes': len(output)}}
            comparisons = {kind: {'equal': True, 'expectedSemanticDigest': biological, 'semanticDigest': biological, 'biologicalDigest': biological,
                'invariants': copy.deepcopy(invariants), 'nativeInvariants': copy.deepcopy(invariants),
                'expectedFiles': {'out.tsv': output_sha}, 'actualFiles': {'out.tsv': output_sha},
                'expectedCanonicalFiles': {'out.tsv': output_sha}, 'canonicalFiles': {'out.tsv': output_sha},
                'expectedFileBytes': {'out.tsv': len(output)}, 'actualFileBytes': {'out.tsv': len(output)},
                'expectedBiologicalDigest': biological, 'numericTolerance': 0, 'numericComparisons': {}, 'differences': []} for kind in spec['comparisons']}
            record = {'id': case_id, 'tool': tool, 'dataset': spec['_dataset'], 'case': spec['id'], 'kind': spec['kind'],
                      'runner': spec['runner'], 'args': spec['args'], 'status': 'passed', 'executions': executions, 'comparisons': comparisons}
            if spec.get('effect') or spec.get('equivalentTo'):
                changed = bool(spec.get('effect'))
                record['optionEffect'] = {'baseline': 'baseline', 'before': packager.digest(b'baseline'), 'after': biological,
                                         'changed': changed, 'expected': 'changed' if changed else 'equivalent'}
            cases.append(record)
        manifest_sha = packager.digest((self.root / 'datasets/plink2/manifest.json').read_bytes())
        datasets = {tool: {'manifestSha256': manifest_sha,
            'files': {name: info['sha256'] for name, info in self.manifest['files'].items() if name.startswith(tool + '/')},
            'observations': self.manifest['datasets'][tool]['observations']} for tool in (tool,)}
        paths = []
        for engine in ('node', 'chromium', 'firefox', 'webkit'):
            report = {'schema': 1, 'tool': tool, 'engine': engine, 'runId': str(uuid4()), 'crossOriginIsolated': True, 'node': 'v22.fixture', 'python': 'Python 3.12.14',
                      'browserVersion': 'fixture' if engine != 'node' else None, 'status': 'passed',
                      'builds': {tool: self.builds[tool]}, 'runtime': self.hashes(packager.RUNTIME[tool]),
                      'validation': self.hashes(packager.VALIDATION), 'manifestSha256': manifest_sha, 'datasets': datasets,
                      'expectedCases': [case['id'] for case in cases], 'cases': cases}
            paths.append(self.write_json(f'reports/{engine}/summary-{tool}-all.json', report))
        return paths

    def change_report(self, change, tool='plink2', index=0):
        path = self.reports[tool][index]
        value = json.loads(path.read_text())
        change(value)
        path.write_bytes(packager.json_bytes(value))

    def assert_bad_report(self, mutation, pattern=None):
        self.reports['plink2'] = self.make_reports('plink2')
        self.change_report(mutation)
        with self.assertRaisesRegex(RuntimeError, pattern or '.'):
            packager.check_reports('plink2', self.reports['plink2'], self.builds)

    def test_independent_zip_checksums_contents_and_determinism(self):
        for tool in packager.TOOLS:
            with self.subTest(tool=tool), contextlib.redirect_stdout(io.StringIO()):
                output = packager.write_package(tool, self.root / 'releases', self.reports[tool])
                original = output.read_bytes()
                packager.write_package(tool, self.root / 'releases', self.reports[tool])
                self.assertEqual(original, output.read_bytes())
                self.assertTrue(output.with_suffix('.zip.sha256').read_text().startswith(packager.digest(original)))
                prefix = f'{tool}-{packager.VERSIONS[tool]}-wasm/'
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
                    config = json.loads(contents['examples/plink2/config.json'])
                    self.assertEqual(set(config['tools']), {tool})
                    self.assertEqual(set(config['datasets']), {tool})
                    release = json.loads(contents['release.json'])
                    self.assertEqual(release['bundledTools'], [tool])
                    self.assertEqual(set(release['reports'][tool]), packager.ENGINES)
                    self.assertIn(f'tests/{tool}.test.mjs', contents)
                    self.assertFalse(any(name.startswith(('dist/prodigal/', 'dist/hmmer/', 'tools/prodigal/', 'tools/hmmer/')) for name in contents))
                    self.assertNotIn('tests/hmmer.test.mjs', json.loads(contents['package.json'])['scripts']['test'])

    def test_missing_duplicate_unknown_or_failed_engine_rejected(self):
        for tool in packager.TOOLS:
            with self.assertRaisesRegex(RuntimeError, 'missing passing reports'):
                packager.collect(tool, self.reports[tool][:-1])
        with self.assertRaisesRegex(RuntimeError, 'duplicate'):
            packager.check_reports('plink2', self.reports['plink2'] * 2, self.builds)
        self.assert_bad_report(lambda r: r.update(status='failed'), 'not a passing')
        self.assert_bad_report(lambda r: r.update(engine='unknown'), 'unknown validation engine')

    def test_relabelled_node_report_is_not_browser_evidence(self):
        original = json.loads(self.reports['plink2'][0].read_text())
        original.update(engine='chromium', browserVersion='invented')
        self.reports['plink2'][1].write_bytes(packager.json_bytes(original))
        with self.assertRaisesRegex(RuntimeError, 'independent validation run identity'):
            packager.check_reports('plink2', self.reports['plink2'], self.builds)

    def test_repeat_may_compare_all_targets_to_original_native_baseline(self):
        repeat = self.plan['tools']['plink2']['cases'][3]
        repeat.update(executions=['native', 'patched-native', 'wasm'], comparisons=['native-contract', 'patched-native', 'wasm'])
        self.write_json('validation/plink2/cases.json', self.plan)
        self.reports['plink2'] = self.make_reports('plink2')
        packager.check_reports('plink2', self.reports['plink2'], self.builds)
        self.change_report(lambda r: r['cases'][3]['comparisons']['native-contract'].update(biologicalDigest=packager.digest(b'other')))
        with self.assertRaisesRegex(RuntimeError, 'unchanged native baseline'):
            packager.check_reports('plink2', self.reports['plink2'], self.builds)
        repeat.update(executions=['native', 'wasm'])
        self.write_json('validation/plink2/cases.json', self.plan)
        with self.assertRaisesRegex(RuntimeError, 'unchanged native baseline'):
            packager.planned_cases('plink2')

    def test_entire_case_plan_required_and_unique(self):
        for change in [lambda r: r['cases'].pop(), lambda r: r['cases'].append(r['cases'][0]),
                       lambda r: r['expectedCases'].pop(), lambda r: r['cases'][0].update(status='failed')]:
            self.assert_bad_report(change, 'complete passing case plan')

    def test_stale_runtime_validator_data_or_build_rejected(self):
        for field in ('runtime', 'validation', 'datasets', 'builds'):
            self.assert_bad_report(lambda r, field=field: r.update({field: {}}))

    def test_actual_node_python_browser_versions_required(self):
        self.assert_bad_report(lambda r: r.update(node=None))
        self.assert_bad_report(lambda r: r.update(python='unknown'))
        self.reports['plink2'] = self.make_reports('plink2')
        self.change_report(lambda r: r.update(browserVersion=None), index=1)
        with self.assertRaisesRegex(RuntimeError, 'browser version'):
            packager.check_reports('plink2', self.reports['plink2'], self.builds)

    def test_case_runner_args_outputs_exit_and_signal_bound(self):
        for change in [lambda r: r['cases'][0].update(runner='other'), lambda r: r['cases'][0].update(args=['other']),
                       lambda r: r['cases'][0]['executions']['wasm']['files'].clear(),
                       lambda r: r['cases'][0]['executions']['wasm'].update(exitCode=1, canonicalExitCode=1),
                       lambda r: r['cases'][0]['executions']['wasm'].update(signal='SIGSEGV')]:
            self.assert_bad_report(change)

    def test_failure_requires_diagnostic_and_native_exit_parity(self):
        for change in [lambda e: e.update(diagnosticMatched=False), lambda e: e.update(exitCode=2, canonicalExitCode=2),
                       lambda e: e.update(stdout=packager.digest(b''), stderr=packager.digest(b''))]:
            self.assert_bad_report(lambda r: change(r['cases'][-1]['executions']['wasm']))

    def test_semantic_equality_must_bind_unchanged_native_baseline(self):
        def mutation(report):
            report['cases'][0]['comparisons']['wasm'].update(semanticDigest=packager.digest(b'other'), expectedSemanticDigest=packager.digest(b'other'))
        self.assert_bad_report(mutation, 'unchanged native baseline')

    def test_comparison_must_bind_observed_output_bytes(self):
        for field in ('expectedFiles', 'actualFiles'):
            self.assert_bad_report(lambda r, field=field: r['cases'][0]['comparisons']['wasm'].update({field: {'out.tsv': packager.digest(b'other')}}),
                                   'output bytes')
        self.assert_bad_report(lambda r: r['cases'][0]['executions']['wasm']['files'].update({'out.tsv': packager.digest(b'other')}),
                               'output bytes')

    def test_equal_semantic_digest_cannot_override_different_raw_output(self):
        def mutation(report):
            forged = packager.digest(b'Different bytes of the same output')
            report['cases'][0]['executions']['wasm']['files']['out.tsv'] = forged
            report['cases'][0]['comparisons']['wasm']['actualFiles']['out.tsv'] = forged
        self.assert_bad_report(mutation, 'exact comparison output bytes differ')

    def test_canonical_checksums_cannot_be_omitted_or_substituted(self):
        self.assert_bad_report(lambda r: r['cases'][0]['comparisons']['wasm'].update(canonicalFiles={}), 'canonical')
        self.assert_bad_report(lambda r: r['cases'][0]['comparisons']['wasm'].update(expectedCanonicalFiles={}), 'native baseline')
        self.assert_bad_report(lambda r: r['cases'][0]['comparisons']['wasm']['canonicalFiles'].update({'out.tsv': packager.digest(b'other')}),
                               'canonical')


    def test_repeat_cannot_reuse_fabricated_baseline_or_omit_native_comparison(self):
        self.assert_bad_report(lambda r: r['cases'][3]['comparisons']['wasm'].update(biologicalDigest=packager.digest(b'other')),
                               'unchanged native baseline')
        self.assert_bad_report(lambda r: r['cases'][0]['comparisons'].pop('native-contract'), 'semantic comparisons')

    def test_actual_inputs_stdin_and_native_command_cannot_be_substituted(self):
        for change in [lambda r: r['cases'][0]['executions']['wasm'].update(inputs={}),
                       lambda r: r['cases'][0]['executions']['wasm'].update(stdin={'sha256': packager.digest(b'other')}),
                       lambda r: r['cases'][0]['executions']['native']['command'].update(program='other')]:
            self.assert_bad_report(change)

    def test_derived_input_cannot_switch_targets_or_bytes(self):
        for change in [lambda s: s.update(sourceTarget='wasm'), lambda s: s.update(sha256=packager.digest(b'other'))]:
            self.assert_bad_report(lambda r: change(r['cases'][2]['executions']['wasm']['inputsFrom'][0]), 'derived input bytes')

    def test_nonempty_biological_invariants_and_option_effect_required(self):
        self.assert_bad_report(lambda r: r['cases'][0]['comparisons']['wasm']['invariants']['out.tsv'].update(records=0), 'nonempty')
        self.assert_bad_report(lambda r: r['cases'][1]['optionEffect'].update(changed=False), 'option effect')
        self.assert_bad_report(lambda r: r['cases'][0]['comparisons']['wasm'].update(nativeInvariants={}), 'artifact evidence')



    def test_source_native_assets_extra_files_and_omitted_inputs_rejected(self):
        for name in ('tools/plink2/browser.patch', 'build/native/plink2', 'dist/plink2/plink2.wasm'):
            path = self.root / name
            before = path.read_bytes()
            path.write_bytes(before + b'changed')
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                packager.check_build_identity('plink2')
            path.write_bytes(before)
        self.write('tools/plink2/new-source.c', b'Unrecorded new source')
        with self.assertRaisesRegex(RuntimeError, 'omits source inputs'):
            packager.check_build_identity('plink2')

    def test_dependency_source_archives_are_required_pinned_and_bundled(self):
        lock = packager.read_json('tools/plink2/sources.lock.json')
        dependency = b'Labelled pinned dependency archive fixture'
        lock['dependencies'] = {'zlib': {'archive': 'zlib-fixture.tar.gz', 'version': '1.3.1',
                                        'sha256': packager.digest(dependency)}}
        self.write_json('tools/plink2/sources.lock.json', lock)
        with self.assertRaisesRegex(RuntimeError, 'missing or unpinned'):
            packager.check_sources('plink2', lock)
        self.write('dist/plink2/sources/zlib-fixture.tar.gz', dependency)
        self.assertEqual(packager.check_sources('plink2', lock)['zlib-fixture.tar.gz'], packager.digest(dependency))
        self.finish_build('plink2')
        self.builds['plink2'] = packager.check_build_identity('plink2')[2]
        self.reports['plink2'] = self.make_reports('plink2')
        entries, _ = packager.collect('plink2', self.reports['plink2'])
        self.assertEqual(entries['dist/plink2/sources/zlib-fixture.tar.gz'][0], dependency)
        self.write('dist/plink2/sources/zlib-fixture.tar.gz', dependency + b'changed')
        with self.assertRaisesRegex(RuntimeError, 'Checksum mismatch'):
            packager.check_sources('plink2', lock)

    def test_numerical_tolerance_extra_cases_or_unbound_output_lengths_rejected(self):
        self.assert_bad_report(lambda r: r['cases'][0]['comparisons']['wasm'].update(numericTolerance=1e-9))
        self.assert_bad_report(lambda r: r['cases'].append({**r['cases'][0], 'id': 'unplanned'}), 'complete passing case plan')
        self.assert_bad_report(lambda r: r['cases'][0]['executions']['wasm']['fileBytes'].clear(), 'byte lengths')
        self.assert_bad_report(lambda r: r['cases'][0]['comparisons']['wasm']['actualFileBytes'].update({'out.tsv': 1}),
                               'output bytes')

    def test_archive_and_notices_cannot_be_replaced(self):
        for name in ('dist/plink2/sources/plink2.tar.gz', 'dist/plink2/notices/LICENSE'):
            path = self.root / name
            before = path.read_bytes()
            path.write_bytes(before + b'changed')
            lock = packager.read_json('tools/plink2/sources.lock.json')
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                packager.check_sources('plink2', lock)
            path.write_bytes(before)

    def test_dataset_requires_complete_inventory_and_planned_inputs(self):
        self.write('datasets/plink2/plink2/unmanifested.fa', b'Unrecorded data')
        with self.assertRaisesRegex(RuntimeError, 'every bundled'):
            packager.checked_dataset_manifest()
        (self.root / 'datasets/plink2/plink2/unmanifested.fa').unlink()
        self.write('datasets/plink2/provenance/unmanifested.txt', b'Unrecorded provenance')
        with self.assertRaisesRegex(RuntimeError, 'every bundled'):
            packager.checked_dataset_manifest()
        (self.root / 'datasets/plink2/provenance/unmanifested.txt').unlink()
        self.plan['fixtureInputs'].append('plink2/missing.fa')
        self.write_json('validation/plink2/cases.json', self.plan)
        with self.assertRaisesRegex(RuntimeError, 'Validation input'):
            packager.checked_dataset_manifest()

    def test_browser_fixture_must_be_local_manifested_and_unique(self):
        path = self.root / 'examples/plink2/config.json'
        original = json.loads(path.read_text())
        for mutation in [lambda c: c['datasets']['plink2'].update(path='https://example.invalid/data/'),
                         lambda c: c['datasets']['plink2'].update(files=['missing.fa']),
                         lambda c: c['datasets']['plink2'].update(files=['input.fa', 'input.fa'])]:
            config = copy.deepcopy(original)
            mutation(config)
            self.write_json('examples/plink2/config.json', config)
            with self.assertRaises(RuntimeError):
                packager.checked_example_config(self.manifest)

    def test_original_inputs_and_regeneration_recipe_must_match(self):
        for name in ('build/plink2-sources/source.tar.gz', 'scripts/fetch-plink2-test-data.py'):
            path = self.root / name
            original = path.read_bytes()
            path.write_bytes(original + b'changed')
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, 'Checksum mismatch'):
                packager.collect('plink2', self.reports['plink2'])
            path.write_bytes(original)

    def test_path_traversal_control_characters_and_symlinks_rejected(self):
        for name in ('../secret', '/absolute', 'a/../b', 'a//b', 'C:/data', 'a\\b', 'a\nname', '.git/config'):
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                packager.relative_name(name)
        target = self.root / 'dist/plink2/linked'
        target.symlink_to(self.root / 'LICENSE')
        with self.assertRaisesRegex(RuntimeError, 'symlink'):
            packager.tree_hashes('dist/plink2')

    def table_comparison(self):
        spec = {'artifacts': [{'path': 'glm.tsv', 'kind': 'table', 'floatColumns': ['BETA', 'SE']}]}
        counts = {'records': 2, 'columns': 4, 'columnNames': ['ID', 'OBS_CT', 'BETA', 'SE'],
                  'numericValues': 4, 'exactCells': 4}
        comparison = {'numericTolerance': copy.deepcopy(packager.NUMERIC_POLICY),
            'invariants': {'glm.tsv': copy.deepcopy(counts)}, 'nativeInvariants': {'glm.tsv': copy.deepcopy(counts)},
            'numericComparisons': {'glm.tsv': {'numericFields': ['BETA', 'SE'], 'exactFields': ['ID', 'OBS_CT'],
                'comparedValues': 4, 'maxAbsoluteDifference': .00001, 'maxTolerance': .0001,
                'withinTolerance': True, 'outsideTolerance': 0}}}
        return spec, comparison

    def test_numeric_policy_field_allowlist_coverage_and_finite_bounds(self):
        spec, original = self.table_comparison()
        packager.check_invariants('fixture', spec, original)
        self.assertEqual(packager.check_numeric_comparisons('fixture', spec, original), {'glm.tsv'})
        for mutate in [
            lambda c: c.update(numericTolerance={'relativeSlack': 1}),
            lambda c: c['numericComparisons']['glm.tsv'].update(numericFields=['OBS_CT', 'BETA', 'SE']),
            lambda c: c['numericComparisons']['glm.tsv'].update(exactFields=['ID']),
            lambda c: c['numericComparisons']['glm.tsv'].update(comparedValues=3),
            lambda c: c['numericComparisons']['glm.tsv'].update(outsideTolerance=1),
            lambda c: c['numericComparisons']['glm.tsv'].update(withinTolerance=False),
            lambda c: c['numericComparisons']['glm.tsv'].update(maxAbsoluteDifference=.1),
            lambda c: c['numericComparisons']['glm.tsv'].update(maxTolerance=float('inf')),
            lambda c: c['numericComparisons']['glm.tsv'].update(maxAbsoluteDifference=float('nan')),
            lambda c: c['nativeInvariants']['glm.tsv'].update(exactCells=0),
        ]:
            changed = copy.deepcopy(original)
            mutate(changed)
            with self.assertRaises(RuntimeError):
                packager.check_numeric_comparisons('fixture', spec, changed)

    def test_numeric_comparison_may_differ_but_must_bind_raw_outputs(self):
        spec, comparison = self.table_comparison()
        native = {'files': {'glm.tsv': packager.digest(b'Native result')}, 'fileBytes': {'glm.tsv': 13}}
        actual = {'files': {'glm.tsv': packager.digest(b'WASM rounding result')}, 'fileBytes': {'glm.tsv': 20}}
        comparison.update(expectedFiles=native['files'], actualFiles=actual['files'],
            expectedFileBytes=native['fileBytes'], actualFileBytes=actual['fileBytes'],
            expectedCanonicalFiles={'glm.tsv': packager.digest(b'Native semantic result')},
            canonicalFiles={'glm.tsv': packager.digest(b'WASM semantic result')})
        packager.check_comparison_files('fixture', spec, comparison, native, actual)
        changed = copy.deepcopy(comparison)
        changed['actualFiles']['glm.tsv'] = packager.digest(b'Substituted result')
        with self.assertRaisesRegex(RuntimeError, 'output bytes'):
            packager.check_comparison_files('fixture', spec, changed, native, actual)

    def test_discrete_table_columns_cannot_inherit_float_tolerance(self):
        spec, comparison = self.table_comparison()
        spec['artifacts'][0]['floatColumns'] = []
        counts = comparison['invariants']['glm.tsv']
        counts.update(numericValues=0, exactCells=8)
        comparison['nativeInvariants'] = copy.deepcopy(comparison['invariants'])
        evidence = comparison['numericComparisons']['glm.tsv']
        evidence.update(numericFields=[], exactFields=counts['columnNames'], comparedValues=0,
                        maxAbsoluteDifference=0, maxTolerance=0)
        comparison.update(numericComparisons={}, numericTolerance=0)
        native = {'files': {'glm.tsv': packager.digest(b'Native result')}, 'fileBytes': {'glm.tsv': 13}}
        actual = {'files': {'glm.tsv': packager.digest(b'Changed count')}, 'fileBytes': {'glm.tsv': 13}}
        comparison.update(expectedFiles=native['files'], actualFiles=actual['files'],
            expectedFileBytes=native['fileBytes'], actualFileBytes=actual['fileBytes'],
            expectedCanonicalFiles=native['files'], canonicalFiles=actual['files'])
        with self.assertRaisesRegex(RuntimeError, 'canonical artifact checksums differ'):
            packager.check_comparison_files('fixture', spec, comparison, native, actual)

    def test_arbitrary_normalization_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'undeclared artifact normalization'):
            packager.normalised_artifacts({'id': 'fixture', 'artifacts': [
                {'kind': 'table', 'path': 'out.tsv', 'normalizeIdentifiers': True}]})

    def independent_truth_fixture(self, mode='king-table'):
        # These labelled miniature bytes exercise the evidence gate only;
        # they are never presented as executed PLINK scientific validation.
        data = {'bed': b'\x6c\x1b\x01\x38\x2c',
                'bim': b'1 v1 0 1 A G\n1 v2 0 2 A G\n',
                'fam': b'F dad 0 0 1 -9\nF child dad mom 1 -9\nX other 0 0 2 -9\n',
                'keep': b'#FID IID\nF dad\nF child\nX other\n',
                'subset': b'#FID1 IID1 FID2 IID2\nF dad F child\nF dad X other\n'}
        names = {key: 'synthetic/gate/cohort.' + key for key in data}
        for key, value in data.items():
            name = 'plink2/' + names[key]
            self.write('datasets/plink2/' + name, value)
            self.manifest['files'][name] = {'sha256': packager.digest(value), 'bytes': len(value)}
        self.write_json('datasets/plink2/manifest.json', self.manifest)
        keys = ['bed', 'bim', 'fam', 'keep'] + (['subset'] if mode == 'king-subset' else [])
        config = {'mode': mode, **{key: names[key] for key in keys}}
        expected = {'method': 'direct SNP-major BED genotype intersections', 'mode': mode,
                    'samples': 3, 'variants': 2,
                    'checkedValues': 9 if mode in ('king-matrix', 'distance-ibs') else 2 if mode == 'king-subset' else 3,
                    'firstSamplePairs': 2, 'firstSample': ['F', 'dad'],
                    'inputs': {names[key]: packager.digest(data[key]) for key in keys}, 'passed': True}
        return {'_dataset': 'plink2', 'oddSampleTruth': config}, {'independentTruth': {
            'expected': copy.deepcopy(expected), 'actual': copy.deepcopy(expected)}}

    def test_independent_genotype_truth_requires_both_sides_and_exact_coverage(self):
        for mode in ('king-table', 'king-subset', 'king-matrix', 'distance-ibs', 'king-cutoff'):
            spec, comparison = self.independent_truth_fixture(mode)
            packager.check_independent_truth('fixture', spec, comparison, self.manifest)
            for side in ('expected', 'actual'):
                for field, value in [('passed', False), ('passed', 1), ('samples', 4),
                                     ('variants', 1), ('checkedValues', 0), ('checkedValues', float(comparison['independentTruth'][side]['checkedValues'])),
                                     ('firstSamplePairs', 1), ('firstSample', ['F', 'child']),
                                     ('mode', 'other'), ('inputs', {}), ('method', 'parity only')]:
                    with self.subTest(mode=mode, side=side, field=field):
                        changed = copy.deepcopy(comparison)
                        changed['independentTruth'][side][field] = value
                        with self.assertRaisesRegex(RuntimeError, 'independent genotype truth evidence'):
                            packager.check_independent_truth('fixture', spec, changed, self.manifest)
            for value in ({}, {'independentTruth': {'actual': comparison['independentTruth']['actual']}}):
                with self.assertRaisesRegex(RuntimeError, 'independent genotype truth evidence'):
                    packager.check_independent_truth('fixture', spec, value, self.manifest)

    def test_passing_parity_report_cannot_omit_independent_truth(self):
        spec, evidence = self.independent_truth_fixture()
        self.plan['tools']['plink2']['cases'][0]['oddSampleTruth'] = spec['oddSampleTruth']
        self.write_json('validation/plink2/cases.json', self.plan)
        self.reports['plink2'] = self.make_reports('plink2')
        with self.assertRaisesRegex(RuntimeError, 'independent genotype truth evidence'):
            packager.check_reports('plink2', self.reports['plink2'], self.builds)
        for path in self.reports['plink2']:
            report = json.loads(path.read_text())
            for comparison in report['cases'][0]['comparisons'].values():
                comparison.update(copy.deepcopy(evidence))
            self.write_json(str(path.relative_to(self.root)), report)
        packager.check_reports('plink2', self.reports['plink2'], self.builds)
        self.change_report(lambda r: r['cases'][0]['comparisons']['wasm']['independentTruth']['actual'].update(firstSamplePairs=1))
        with self.assertRaisesRegex(RuntimeError, 'independent genotype truth evidence'):
            packager.check_reports('plink2', self.reports['plink2'], self.builds)

    def test_table_dimensions_and_vcf_call_coverage_are_required(self):
        spec, comparison = self.table_comparison()
        for field, value in [('columnNames', []), ('columns', 1), ('exactCells', 0), ('numericValues', -1)]:
            changed = copy.deepcopy(comparison)
            changed['invariants']['glm.tsv'][field] = value
            with self.assertRaises(RuntimeError):
                packager.check_invariants('fixture', spec, changed)
        spec = {'artifacts': [{'kind': 'vcf', 'path': 'out.vcf'}]}
        counts = {'records': 3, 'samples': 2, 'phasedCalls': 6, 'missingCalls': 1, 'multiallelicRecords': 1}
        comparison = {'invariants': {'out.vcf': counts}, 'nativeInvariants': {'out.vcf': dict(counts)}}
        packager.check_invariants('fixture', spec, comparison)
        for field, value in [('samples', 0), ('phasedCalls', 7), ('missingCalls', 7), ('multiallelicRecords', 4)]:
            changed = copy.deepcopy(comparison)
            changed['invariants']['out.vcf'][field] = value
            with self.assertRaises(RuntimeError):
                packager.check_invariants('fixture', spec, changed)


    def test_thread_worker_isolation_and_wait_for_exit_required(self):
        worker = self.root / 'dist/plink2/plink2.worker.mjs'
        worker.unlink()
        self.finish_build('plink2')
        with self.assertRaisesRegex(RuntimeError, 'missing or unsafe'):
            packager.check_build_identity('plink2')
        self.write('dist/plink2/plink2.worker.mjs', b'// Worker fixture')
        self.finish_build('plink2')
        self.builds['plink2'] = packager.check_build_identity('plink2')[2]
        self.reports['plink2'] = self.make_reports('plink2')
        self.change_report(lambda r: r.update(crossOriginIsolated=False), index=1)
        with self.assertRaisesRegex(RuntimeError, 'isolation evidence'):
            packager.check_reports('plink2', self.reports['plink2'], self.builds)
        self.reports['plink2'] = self.make_reports('plink2')
        config = packager.read_json('examples/plink2/config.json')
        config['tools']['plink2'].update(waitForExit=False)
        self.write_json('examples/plink2/config.json', config)
        with self.assertRaisesRegex(RuntimeError, 'threading settings'):
            packager.collect('plink2', self.reports['plink2'])


if __name__ == '__main__':
    unittest.main()
