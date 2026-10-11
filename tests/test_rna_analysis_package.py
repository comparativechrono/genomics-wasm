"""Release-gate tests using labelled fixture bytes, not real genomics binaries."""
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

SPEC = importlib.util.spec_from_file_location('rna_analysis_packager', Path(__file__).resolve().parents[1] / 'scripts/package-rna-analysis.py')
packager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packager)


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.root_patch = patch.object(packager, 'ROOT', self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        common = {
            'LICENSE': b'Infrastructure licence fixture\n',
            'THIRD_PARTY.md': b'Third-party notice fixture\n',
            'runtime/README.md': b'# Runtime\n\n## Interface\nFixture interface.\n',
            'examples/rna-analysis/index.html': b'<!doctype html><title>Fixture</title>',
            'examples/rna-analysis/README.md': b'# Example fixture\n',
            'examples/rna-analysis/app.mjs': b'// example source fixture\n',
            'examples/rna-analysis/isolation-worker.js': b'// worker source fixture\n',
            'examples/rna-analysis/serve.py': b'# static server source fixture\n',
            'datasets/rna-analysis/README.md': b'# Biological-data manifest fixture\n',
            'build/rna-analysis-sources/source.dat': b'Original data source fixture',
        }
        for name in (*packager.RUNTIME, *packager.VALIDATION,
                     'scripts/setup-emsdk.sh', 'scripts/package-rna-analysis.py',
                     'scripts/test-rna-analysis-demo.mjs', 'scripts/fetch-rna-analysis-test-data.py',
                     'tests/runtime.test.mjs', 'tests/fixtures/mock-tool.mjs',
                     'tests/test_rna_analysis_compare.py', 'tests/test_rna_analysis_package.py'):
            common[name] = f'Source fixture: {name}\n'.encode()
        for name, data in common.items():
            self.write(name, data)
        self.write_json('package.json', {'devDependencies': {'playwright': '1.64.0'}})
        self.write_json('package-lock.json', {'name': 'original', 'version': '0.1.0', 'lockfileVersion': 3,
                                            'packages': {'': {'name': 'original', 'version': '0.1.0', 'devDependencies': {'playwright': '1.64.0'}}}})
        config = {'tools': {}, 'datasets': {'human': {}, 'yeast': {}}}
        plan = {'schema': 1, 'datasets': ['human', 'yeast'], 'tools': {}}
        success = {'id': 'main', 'kind': 'success', 'args': ['input.bam'], 'outputs': ['result.txt'],
                   'executions': ['native', 'patched-native', 'wasm'], 'comparisons': ['native-contract', 'patched-native', 'wasm']}
        for tool in packager.TOOLS:
            cases = [success, {**success, 'id': 'filtered', 'args': ['--filter', 'input.bam'], 'effect': 'main'},
                     {**success, 'id': 'same', 'args': ['--same', 'input.bam'], 'equivalentTo': 'main'},
                     {**success, 'id': 'repeat', 'kind': 'repeat', 'baseline': 'main', 'executions': ['wasm'], 'comparisons': ['wasm']},
                     {**success, 'id': 'missing', 'kind': 'failure', 'args': ['missing.bam'], 'comparisons': [], 'oncePerTool': True},
                     {**success, 'id': 'threads', 'kind': 'restriction', 'args': ['--threads', '99'],
                      'executions': ['patched-native', 'wasm'], 'comparisons': [], 'oncePerTool': True}]
            plan['tools'][tool] = {'cases': copy.deepcopy(cases), 'waitForExit': tool == 'featurecounts'}
            config['tools'][tool] = {'requiresIsolation': tool == 'featurecounts', 'waitForExit': tool == 'featurecounts',
                                     'commands': {'main': copy.deepcopy(success)}}
            source = f'{tool} original archive fixture'.encode()
            dependency = f'{tool} dependency archive fixture'.encode()
            self.write(f'build/downloads/{tool}.tar.gz', source)
            self.write(f'build/downloads/{tool}-dep.tar.gz', dependency)
            lock = {'tool': tool, 'version': '1.2.3', 'revision': 'fixture', 'emscripten': '3.1.64',
                    'archive': f'{tool}.tar.gz', 'sha256': packager.digest(source),
                    'dependencies': {'dependency': {'version': '1', 'archive': f'{tool}-dep.tar.gz', 'sha256': packager.digest(dependency)}},
                    'requiredNotices': ['LICENSE', 'THIRD_PARTY_LICENSES.txt', 'LICENSE.dependency']}
            self.write_json(f'tools/{tool}/sources.lock.json', lock)
            for name in ('README.md', 'LICENSE', 'THIRD_PARTY_LICENSES.txt', 'browser.patch'):
                self.write(f'tools/{tool}/{name}', f'{tool} {name} fixture\n'.encode())
            if tool == 'mosdepth':
                for name in ('port.nim', 'port.nims', 'port.nimble', 'port.cfg'):
                    self.write(f'tools/{tool}/{name}', f'Nim source fixture {name}\n'.encode())
            self.write(f'scripts/build-{tool}.sh', b'#!/bin/sh\n# Build fixture\n', executable=True)
            self.write(f'build/native/{tool}', f'{tool} original native binary fixture'.encode())
            self.write(f'build/patched-native/{tool}', f'{tool} patched native binary fixture'.encode())
            self.write(f'dist/{tool}/{tool}.mjs', b'// WASM module fixture, not executable tool code\n')
            self.write(f'dist/{tool}/{tool}.wasm', b'\0asm\1\0\0\0')
            self.write(f'dist/{tool}/LICENSE.dependency', b'Dependency licence fixture\n')
            self.write(f'dist/{tool}/{tool}.tar.gz', source)
            self.write(f'dist/{tool}/{tool}-dep.tar.gz', dependency)
            if tool == 'featurecounts':
                self.write(f'dist/{tool}/{tool}.worker.js', b'// pthread worker fixture\n')
            self.write_build_manifest(tool)
        self.write_json('validation/rna-analysis/cases.json', plan)
        self.write_json('examples/rna-analysis/config.json', config)
        manifest = {'schema_version': 1, 'datasets': {}, 'files': {}, 'sources': {
            'source.dat': {'cachePath': 'build/rna-analysis-sources/source.dat', 'bytes': len(common['build/rna-analysis-sources/source.dat']),
                           'sha256': packager.digest(common['build/rna-analysis-sources/source.dat'])}},
                    'regeneration': {'script': 'scripts/fetch-rna-analysis-test-data.py', 'files': []}}
        for dataset in ('human', 'yeast'):
            manifest['datasets'][dataset] = {'observations': {'alignmentRecords': 1, 'mappedRecords': 1}}
            for filename, data in [('input.sam', b'read1\t0\tchr1\t1\t60\t1M\t*\t0\t0\tA\tI\n'),
                                   ('input.bam', b'BAM data fixture')]:
                name = f'{dataset}/{filename}'
                self.write(f'datasets/rna-analysis/{name}', data)
                manifest['files'][name] = {'sha256': packager.digest(data), 'bytes': len(data), 'generated': True}
        self.write_json('datasets/rna-analysis/manifest.json', manifest)
        self.reports = {tool: self.make_reports(tool, plan, manifest) for tool in packager.TOOLS}

    def write(self, name, data, executable=False):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if executable:
            path.chmod(0o755)
        return path

    def write_json(self, name, value):
        return self.write(name, packager.json_bytes(value))

    def load(self, name):
        return json.loads((self.root / name).read_text())

    def hashes(self, names):
        return {name: packager.digest((self.root / name).read_bytes()) for name in names}

    def assets(self, tool):
        return {path.name: packager.digest(path.read_bytes()) for path in (self.root / f'dist/{tool}').iterdir()
                if path.suffix in {'.mjs', '.js', '.wasm'}}

    def write_build_manifest(self, tool):
        inputs = [f'scripts/build-{tool}.sh', f'tools/{tool}/sources.lock.json', f'tools/{tool}/browser.patch']
        if tool == 'mosdepth':
            inputs.extend(f'tools/{tool}/{name}' for name in ('port.nim', 'port.nims', 'port.nimble', 'port.cfg'))
        self.write_json(f'dist/{tool}/build-manifest.json', {'schema': 1, 'tool': tool, 'assets': self.assets(tool), 'inputs': self.hashes(inputs)})

    def make_reports(self, tool, plan, manifest):
        assets = self.assets(tool)
        manifest_sha = packager.digest((self.root / 'datasets/rna-analysis/manifest.json').read_bytes())
        datasets = {name: {'manifestSha256': manifest_sha, 'files': {path: item['sha256'] for path, item in manifest['files'].items() if path.startswith(name + '/')},
                          'observations': manifest['datasets'][name]['observations'], 'samRecords': 1, 'mappedRecords': 1} for name in ('human', 'yeast')}
        cases = []
        for dataset in ('human', 'yeast'):
            for spec in plan['tools'][tool]['cases']:
                if spec.get('oncePerTool') and dataset == 'yeast':
                    continue
                failure = spec['kind'] in ('failure', 'restriction')
                case_id = f'{tool}-{spec["id"]}' if spec.get('oncePerTool') else f'{tool}-{dataset}-{spec["id"]}'
                biological = packager.digest(f'{dataset}-{"filtered" if spec["id"] == "filtered" else "main"}'.encode())
                executions = {kind: {'exitCode': 1 if failure else 0, 'canonicalExitCode': 1 if failure else 0, 'signal': None,
                                      'missingOutputs': ['result.txt'] if failure else [], 'seconds': 0.1,
                                      'stdout': packager.digest(b''), 'stderr': packager.digest(b'Expected fixture failure') if failure else packager.digest(b''),
                                      'files': {} if failure else {'result.txt': packager.digest(b'Fixture result')}} for kind in spec['executions']}
                comparisons = {kind: {'equal': True, 'expectedSemanticDigest': biological, 'semanticDigest': biological,
                                       'biologicalDigest': biological, 'invariants': {'rows': 1}} for kind in spec['comparisons']}
                record = {'id': case_id, 'tool': tool, 'dataset': dataset, 'case': spec['id'], 'kind': spec['kind'],
                          'args': spec['args'], 'status': 'passed', 'executions': executions, 'comparisons': comparisons}
                if spec.get('nativeErrorPolicy'):
                    executions['native'].update(exitCode=None, canonicalExitCode=None, signal='SIGSEGV')
                    for kind in ('patched-native', 'wasm'):
                        executions[kind].update(exitCode=2, canonicalExitCode=2, portDiagnosticMatched=True)
                    record['errorPolicyObserved'] = {'reason': spec['nativeErrorPolicy']['reason'],
                                                     'native': {'exitCode': None, 'signal': 'SIGSEGV'},
                                                     'portExitCode': spec['portExitCode'], 'portDiagnostic': spec['portDiagnostic']}
                if spec.get('effect') or spec.get('equivalentTo'):
                    changed = bool(spec.get('effect'))
                    record['optionEffect'] = {'baseline': 'main', 'changed': changed, 'expected': 'changed' if changed else 'equivalent',
                                              'before': packager.digest(f'{dataset}-main'.encode()), 'after': biological}
                cases.append(record)
        paths = []
        for engine in ('node', 'chromium', 'firefox', 'webkit'):
            report = {'schema': 1, 'engine': engine, 'node': 'v22.fixture', 'browserVersion': 'fixture' if engine != 'node' else None,
                      'crossOriginIsolated': True, 'status': 'passed', 'runtime': self.hashes(packager.RUNTIME),
                      'validation': self.hashes(packager.VALIDATION), 'manifestSha256': manifest_sha, 'datasets': datasets,
                      'expectedCases': [case['id'] for case in cases], 'cases': cases,
                      'builds': {tool: {'assets': assets, 'module': assets[f'{tool}.mjs'], 'wasm': assets[f'{tool}.wasm'],
                                       'native': packager.digest((self.root / f'build/native/{tool}').read_bytes()),
                                       'patched-native': packager.digest((self.root / f'build/patched-native/{tool}').read_bytes())}}}
            paths.append(self.write_json(f'reports/{engine}/{tool}.json', report))
        return paths

    def change_report(self, callback, *, tool='featurecounts', index=0):
        path = self.reports[tool][index]
        report = json.loads(path.read_text())
        callback(report)
        path.write_bytes(packager.json_bytes(report))

    def error_policy_fixture(self, tool='mosdepth'):
        plan = self.load('validation/rna-analysis/cases.json')
        cases = plan['tools'][tool]['cases']
        template = next(case for case in cases if case['id'] == 'missing')
        cases.remove(template)
        for case_id, diagnostic in packager.MOSDEPTH_ERROR_DIAGNOSTICS.items():
            cases.append({**copy.deepcopy(template), 'id': case_id,
                          'nativeErrorPolicy': {'allowedSignals': ['SIGSEGV'], 'allowNonzeroExit': True,
                                                'reason': 'Labelled upstream missing-file crash fixture; port adds an open guard.'},
                          'portExitCode': 2, 'portDiagnostic': diagnostic})
        self.write_json('validation/rna-analysis/cases.json', plan)
        self.reports[tool] = self.make_reports(tool, plan, self.load('datasets/rna-analysis/manifest.json'))
        return plan

    def test_independent_archives_are_deterministic_and_integrity_checked(self):
        for tool in packager.TOOLS:
            with self.subTest(tool=tool), contextlib.redirect_stdout(io.StringIO()):
                output = packager.write_package(tool, self.root / 'releases', self.reports[tool])
                first = output.read_bytes()
                packager.write_package(tool, self.root / 'releases', self.reports[tool])
                self.assertEqual(first, output.read_bytes())
                self.assertTrue(output.with_suffix('.zip.sha256').read_text().startswith(packager.digest(first)))
                prefix = f'{tool}-1.2.3-wasm/'
                with zipfile.ZipFile(output) as archive:
                    names = archive.namelist()
                    config = json.loads(archive.read(prefix + 'examples/rna-analysis/config.json'))
                    self.assertEqual(list(config['tools']), [tool])
                    self.assertEqual(config['tools'][tool]['waitForExit'], tool == 'featurecounts')
                    for other in set(packager.TOOLS) - {tool}:
                        self.assertFalse(any(f'/dist/{other}/' in name or f'/tools/{other}/' in name for name in names))
                    self.assertFalse(any('/datasets/rna-quant/' in name or '/__pycache__/' in name for name in names))
                    for required in ('build/rna-analysis-sources/source.dat', f'build/downloads/{tool}.tar.gz',
                                     f'build/downloads/{tool}-dep.tar.gz', 'scripts/fetch-rna-analysis-test-data.py',
                                     'validation-reports/webkit.json', 'package-lock.json'):
                        self.assertIn(prefix + required, names)
                    for line in archive.read(prefix + 'SHA256SUMS').decode().splitlines():
                        checksum, name = line.split('  ', 1)
                        self.assertEqual(packager.digest(archive.read(prefix + name)), checksum)
                    source = json.loads(archive.read(prefix + 'source-manifest.json'))
                    for name, checksum in source['files'].items():
                        self.assertEqual(packager.digest(archive.read(prefix + name)), checksum)
                    self.assertEqual((archive.getinfo(prefix + f'scripts/build-{tool}.sh').external_attr >> 16) & 0o777, 0o755)

    def test_all_four_engines_are_required_and_duplicates_rejected(self):
        for reports in (self.reports['featurecounts'][:-1], self.reports['featurecounts'] + self.reports['featurecounts'][:1]):
            with self.subTest(count=len(reports)), self.assertRaisesRegex(RuntimeError, 'missing passing reports|duplicate'):
                packager.collect('featurecounts', reports)

    def test_complete_combined_tool_reports_are_accepted(self):
        paths = []
        for index, engine in enumerate(('node', 'chromium', 'firefox', 'webkit')):
            report = json.loads(self.reports['featurecounts'][index].read_text())
            for tool in ('stringtie', 'mosdepth'):
                other = json.loads(self.reports[tool][index].read_text())
                report['builds'].update(other['builds'])
                report['cases'].extend(other['cases'])
                report['expectedCases'].extend(other['expectedCases'])
            paths.append(self.write_json(f'reports/{engine}/all.json', report))
        for tool in packager.TOOLS:
            with self.subTest(tool=tool):
                checked = packager.check_reports(tool, paths, self.assets(tool))
                self.assertEqual(set(checked), {'node', 'chromium', 'firefox', 'webkit'})

    def test_all_tool_cli_validates_before_replacing_any_release(self):
        output = self.root / 'releases'
        output.mkdir()
        original = output / 'featurecounts-1.2.3-wasm.zip'
        original.write_bytes(b'Previous reviewed release fixture')
        (self.root / 'build/native/mosdepth').unlink()
        arguments = ['package-rna-analysis.py', '--tool', 'all', '--output', str(output)]
        for paths in self.reports.values():
            for path in paths:
                arguments.extend(['--validation-report', str(path)])
        with patch.object(packager.sys, 'argv', arguments), self.assertRaisesRegex(RuntimeError, 'Required package input'):
            packager.main()
        self.assertEqual(original.read_bytes(), b'Previous reviewed release fixture')
        self.assertEqual(list(output.iterdir()), [original])

    def test_report_cannot_shrink_its_own_expected_case_set(self):
        def truncate(report):
            report['cases'].pop()
            report['expectedCases'].pop()
        self.change_report(truncate)
        with self.assertRaisesRegex(RuntimeError, 'complete passing planned case set'):
            packager.collect('featurecounts', self.reports['featurecounts'])

    def test_native_and_semantic_comparison_evidence_is_required(self):
        self.change_report(lambda report: report['cases'][0]['executions'].pop('native'))
        with self.assertRaisesRegex(RuntimeError, 'executions are missing'):
            packager.collect('featurecounts', self.reports['featurecounts'])
        self.reports['featurecounts'][0].write_bytes(self.reports['featurecounts'][1].read_bytes().replace(b'"engine": "chromium"', b'"engine": "node"'))
        self.change_report(lambda report: report['cases'][0]['comparisons']['wasm'].update(equal=False))
        with self.assertRaisesRegex(RuntimeError, 'semantic comparison did not pass'):
            packager.collect('featurecounts', self.reports['featurecounts'])

    def test_missing_outputs_or_wrong_arguments_prevent_release(self):
        self.change_report(lambda report: report['cases'][0]['executions']['wasm']['files'].clear())
        with self.assertRaisesRegex(RuntimeError, 'missing outputs'):
            packager.collect('featurecounts', self.reports['featurecounts'])
        self.change_report(lambda report: report['cases'][0].update(args=['different.bam']))
        with self.assertRaisesRegex(RuntimeError, 'command differs'):
            packager.collect('featurecounts', self.reports['featurecounts'])

    def test_option_effect_and_failure_exit_agreement_are_required(self):
        self.change_report(lambda report: report['cases'][1]['optionEffect'].update(changed=False))
        with self.assertRaisesRegex(RuntimeError, 'option-effect evidence'):
            packager.collect('featurecounts', self.reports['featurecounts'])
        self.change_report(lambda report: report['cases'][1]['optionEffect'].update(changed=True))
        self.change_report(lambda report: next(case for case in report['cases'] if case['kind'] == 'failure')['executions']['wasm'].update(exitCode=2, canonicalExitCode=2))
        with self.assertRaisesRegex(RuntimeError, 'failure exits disagree'):
            packager.collect('featurecounts', self.reports['featurecounts'])

    def test_only_planned_mosdepth_native_error_paths_allow_upstream_sigsegv(self):
        self.error_policy_fixture()
        checked = packager.check_reports('mosdepth', self.reports['mosdepth'], self.assets('mosdepth'))
        self.assertEqual(set(checked), packager.ENGINES)
        for index in range(4):
            def ordinary_exit(report):
                for case in report['cases']:
                    if 'errorPolicyObserved' in case:
                        case['executions']['native'].update(exitCode=1, canonicalExitCode=1, signal=None)
                        case['errorPolicyObserved']['native'] = {'exitCode': 1, 'signal': None}
            self.change_report(ordinary_exit, tool='mosdepth', index=index)
        self.assertEqual(set(packager.check_reports('mosdepth', self.reports['mosdepth'], self.assets('mosdepth'))), packager.ENGINES)

    def test_error_policy_requires_clean_port_exit_diagnostic_and_exact_evidence(self):
        self.error_policy_fixture()
        path = self.reports['mosdepth'][0]
        original = path.read_bytes()
        mutations = [
            lambda case: case['executions']['native'].update(signal='SIGABRT'),
            lambda case: case['executions']['native'].update(exitCode=1),
            lambda case: case['executions']['patched-native'].update(signal='SIGSEGV', exitCode=None, canonicalExitCode=None),
            lambda case: case['executions']['wasm'].update(signal='SIGSEGV', exitCode=None, canonicalExitCode=None),
            lambda case: case['executions']['wasm'].update(exitCode=1, canonicalExitCode=1),
            lambda case: case['executions']['wasm'].pop('portDiagnosticMatched'),
            lambda case: case['executions']['patched-native'].update(portDiagnosticMatched=False),
            lambda case: case.pop('errorPolicyObserved'),
            lambda case: case['errorPolicyObserved'].update(portDiagnostic='different diagnostic'),
            lambda case: case['errorPolicyObserved']['native'].update(signal=None),
            lambda case: case['executions']['native'].pop('signal'),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index):
                path.write_bytes(original)
                self.change_report(lambda report: mutate(next(case for case in report['cases'] if case['case'] == 'missing-input')), tool='mosdepth')
                with self.assertRaisesRegex(RuntimeError, 'signal|crash|exit/diagnostic|error-policy evidence'):
                    packager.check_reports('mosdepth', self.reports['mosdepth'], self.assets('mosdepth'))

    def test_native_error_exception_cannot_expand_to_other_tools_or_cases(self):
        plan = self.error_policy_fixture()
        original = copy.deepcopy(plan)
        mutations = [
            lambda spec: spec.update(id='main', kind='success'),
            lambda spec: spec.update(id='other-failure'),
            lambda spec: spec.update(portExitCode=1),
            lambda spec: spec.update(portDiagnostic='any failure'),
            lambda spec: spec['nativeErrorPolicy'].update(allowedSignals=['SIGSEGV', 'SIGABRT']),
            lambda spec: spec['nativeErrorPolicy'].update(allowNonzeroExit=False),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index):
                plan = copy.deepcopy(original)
                mutate(next(case for case in plan['tools']['mosdepth']['cases'] if case['id'] == 'missing-input'))
                self.write_json('validation/rna-analysis/cases.json', plan)
                with self.assertRaisesRegex(RuntimeError, 'unsupported native error-policy exception'):
                    packager.planned_cases('mosdepth')
        self.write_json('validation/rna-analysis/cases.json', original)
        for tool in ('featurecounts', 'stringtie'):
            with self.subTest(tool=tool):
                self.error_policy_fixture(tool)
                with self.assertRaisesRegex(RuntimeError, 'unsupported native error-policy exception'):
                    packager.planned_cases(tool)

    def test_crashes_in_ordinary_success_failure_and_restriction_cases_are_rejected(self):
        path = self.reports['featurecounts'][0]
        original = path.read_bytes()
        for kind in ('success', 'failure', 'restriction'):
            for target in ('native', 'patched-native', 'wasm'):
                if kind == 'restriction' and target == 'native':
                    continue
                with self.subTest(kind=kind, target=target):
                    path.write_bytes(original)
                    def crash(report):
                        case = next(case for case in report['cases'] if case['kind'] == kind)
                        case['executions'][target].update(exitCode=None, canonicalExitCode=None, signal='SIGSEGV')
                    self.change_report(crash)
                    with self.assertRaisesRegex(RuntimeError, 'unexpected signal or crash'):
                        packager.check_reports('featurecounts', self.reports['featurecounts'], self.assets('featurecounts'))

    def test_stale_native_binary_runtime_validator_and_data_are_rejected(self):
        for name in ('build/native/featurecounts', 'runtime/index.mjs', 'validation/rna-analysis/compare.py', 'datasets/rna-analysis/human/input.bam'):
            with self.subTest(name=name):
                path = self.root / name
                original = path.read_bytes()
                path.write_bytes(original + b'changed')
                with self.assertRaisesRegex(RuntimeError, 'differ|Checksum mismatch'):
                    packager.collect('featurecounts', self.reports['featurecounts'])
                path.write_bytes(original)

    def test_build_manifest_binds_every_nim_input_and_compiled_worker(self):
        manifest = self.load('dist/mosdepth/build-manifest.json')
        manifest['inputs'].pop('tools/mosdepth/port.nim')
        self.write_json('dist/mosdepth/build-manifest.json', manifest)
        with self.assertRaisesRegex(RuntimeError, 'omits source inputs.*port.nim'):
            packager.collect('mosdepth', self.reports['mosdepth'])
        self.write('dist/featurecounts/featurecounts.worker.js', b'changed worker')
        with self.assertRaisesRegex(RuntimeError, 'build manifest does not identify'):
            packager.collect('featurecounts', self.reports['featurecounts'])

    def test_new_manifest_cannot_attach_old_reports_to_changed_assets(self):
        self.write('dist/featurecounts/featurecounts.mjs', b'changed module')
        self.write_build_manifest('featurecounts')
        with self.assertRaisesRegex(RuntimeError, 'assets differ'):
            packager.collect('featurecounts', self.reports['featurecounts'])

    def test_copied_source_recipe_cannot_disagree_with_build_inputs(self):
        self.write('dist/featurecounts/browser.patch', b'Stale copied portability patch')
        with self.assertRaisesRegex(RuntimeError, 'Checksum mismatch.*browser.patch'):
            packager.collect('featurecounts', self.reports['featurecounts'])

    def test_pinned_archives_can_come_from_complete_distribution(self):
        for name in ('featurecounts.tar.gz', 'featurecounts-dep.tar.gz'):
            (self.root / 'build/downloads' / name).unlink()
        entries, _ = packager.collect('featurecounts', self.reports['featurecounts'])
        self.assertIn('build/downloads/featurecounts.tar.gz', entries)
        self.assertNotIn('dist/featurecounts/featurecounts.tar.gz', entries)
        self.write('dist/featurecounts/featurecounts-dep.tar.gz', b'corrupt source')
        with self.assertRaisesRegex(RuntimeError, 'Checksum mismatch'):
            packager.collect('featurecounts', self.reports['featurecounts'])

    def test_changed_original_data_source_and_missing_licence_stop_packaging(self):
        path = self.root / 'build/rna-analysis-sources/source.dat'
        original = path.read_bytes()
        path.write_bytes(b'changed data source')
        with self.assertRaisesRegex(RuntimeError, 'Checksum mismatch'):
            packager.collect('featurecounts', self.reports['featurecounts'])
        path.write_bytes(original)
        (self.root / 'dist/featurecounts/LICENSE.dependency').unlink()
        with self.assertRaisesRegex(RuntimeError, 'required licence notice'):
            packager.collect('featurecounts', self.reports['featurecounts'])

    def test_archive_paths_and_symlinks_cannot_escape_root(self):
        lock = self.load('tools/featurecounts/sources.lock.json')
        lock['archive'] = '../../outside.tar.gz'
        with self.assertRaisesRegex(RuntimeError, 'Unsafe package path'):
            packager.source_archives('featurecounts', lock)
        with tempfile.TemporaryDirectory() as directory:
            outside = Path(directory) / 'source.patch'
            outside.write_bytes(b'Outside-root test fixture')
            patch_path = self.root / 'tools/featurecounts/browser.patch'
            patch_path.unlink()
            patch_path.symlink_to(outside)
            with self.assertRaisesRegex(RuntimeError, 'escapes root'):
                packager.collect('featurecounts', self.reports['featurecounts'])

    def test_release_cannot_silently_change_threading_or_omit_browser_version(self):
        config = self.load('examples/rna-analysis/config.json')
        config['tools']['stringtie']['waitForExit'] = True
        self.write_json('examples/rna-analysis/config.json', config)
        with self.assertRaisesRegex(RuntimeError, 'threading contract'):
            packager.collect('stringtie', self.reports['stringtie'])
        self.change_report(lambda report: report.pop('browserVersion'), index=1)
        with self.assertRaisesRegex(RuntimeError, 'browser version'):
            packager.collect('featurecounts', self.reports['featurecounts'])


if __name__ == '__main__':
    unittest.main()
