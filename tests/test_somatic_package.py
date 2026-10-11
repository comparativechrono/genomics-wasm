"""Somatic release-gate tests using labelled fixtures, never simulated tool results."""
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

SPEC = importlib.util.spec_from_file_location('somatic_packager', Path(__file__).resolve().parents[1] / 'scripts/package-somatic.py')
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
        for name in ('LICENSE', *{path for paths in packager.RUNTIME.values() for path in paths}, *packager.VALIDATION,
                     'runtime/README.md', 'runtime/LOFREQ.md', 'validation/README.md', 'scripts/setup-emsdk.sh', 'scripts/package-somatic.py',
                     'scripts/test-somatic-demo.mjs', 'scripts/fetch-somatic-test-data.py',
                     'tests/test_somatic_compare.py', 'tests/test_somatic_package.py', 'tests/runtime.test.mjs', 'tests/lofreq.test.mjs', 'tests/fixtures/mock-tool.mjs',
                     'examples/somatic/index.html', 'examples/somatic/app.mjs', 'examples/somatic/README.md',
                     'validation/somatic/README.md', 'datasets/somatic/README.md'):
            self.write(name, f'Labelled source fixture: {name}\n'.encode())
        self.write('runtime/README.md', b'# Runtime fixture\n\n## Interface\nFixture interface.\n')
        self.write_json('package.json', {'devDependencies': {'playwright': '1.64.0'}})
        self.write_json('package-lock.json', {'name': 'original', 'version': '0.1.0', 'lockfileVersion': 3,
                                            'packages': {'': {'name': 'original', 'version': '0.1.0', 'devDependencies': {'playwright': '1.64.0'}}}})
        self.plan = {'schema': 1, 'datasets': ['hcc1395'], 'tools': {}}
        config = {'datasets': {'hcc1395': {}}, 'tools': {}}
        baseline = {'id': 'baseline', 'kind': 'success', 'args': ['tumor.bam', 'normal.bam', 'out.vcf'],
                    'outputs': ['out.vcf'], 'executions': ['native', 'patched-native', 'wasm'],
                    'vcfs': [{'path': 'out.vcf', 'minVariants': 1, 'minSomatic': 1, 'expectedSamples': ['NORMAL', 'TUMOR']}],
                    'comparisons': ['native-contract', 'patched-native', 'wasm']}
        for tool in packager.TOOLS:
            self.plan['tools'][tool] = {'cases': [
                copy.deepcopy(baseline), {**baseline, 'id': 'filtered', 'effect': 'baseline', 'args': ['--filter', 'out.vcf']},
                {**baseline, 'id': 'equivalent', 'equivalentTo': 'baseline', 'args': ['--equivalent', 'out.vcf']},
                {**baseline, 'id': 'repeat', 'kind': 'repeat', 'executions': ['wasm'], 'comparisons': ['wasm'], 'baseline': 'baseline'},
                {**baseline, 'id': 'missing', 'kind': 'failure', 'args': ['missing.bam'], 'oncePerTool': True, 'comparisons': [], 'diagnosticRegex': 'diagnostic'},
            ]}
            config['tools'][tool] = {'commands': {'somatic': copy.deepcopy(baseline)}}
            archives = {f'{tool}.tar.gz': f'{tool} original source archive fixture'.encode(),
                        'dependency.tar.gz': b'Dependency original source archive fixture'}
            lock = {'schema': 1, 'tool': tool, 'version': '1.2.3', 'revision': 'fixture', 'emscripten': '3.1.64',
                    'archive': f'{tool}.tar.gz', 'sha256': packager.digest(archives[f'{tool}.tar.gz']),
                    'dependencies': {'dependency': {'archive': 'dependency.tar.gz', 'sha256': packager.digest(archives['dependency.tar.gz'])}},
                    'requiredNotices': ['LICENSE', 'THIRD_PARTY_LICENSES.txt', 'LICENSE.dependency']}
            self.write_json(f'tools/{tool}/sources.lock.json', lock)
            for name in ('README.md', 'browser.patch', 'build.py', 'LICENSE', 'THIRD_PARTY_LICENSES.txt', 'LICENSE.dependency'):
                data = f'{tool} {name} source/notice fixture\n'.encode()
                self.write(f'tools/{tool}/{name}', data)
                if name in lock['requiredNotices']:
                    prefix = 'notices/' if tool == 'lofreq' else ''
                    self.write(f'dist/{tool}/{prefix}{name}', data)
            self.write(f'scripts/build-{tool}.sh', b'#!/bin/sh\n# Labelled build recipe fixture\n').chmod(0o755)
            self.write(f'dist/{tool}/{tool}.mjs', b'// Labelled module fixture; no tool implementation\n')
            self.write(f'dist/{tool}/{tool}.wasm', b'\0asm\1\0\0\0')
            for name, data in archives.items():
                self.write(f'dist/{tool}/sources/{name}', data)
            native_files = {}
            for kind in ('native', 'patched-native'):
                self.write(f'build/{kind}/{tool}', f'{kind} {tool} binary fixture'.encode())
                if tool == 'lofreq':
                    self.write(f'build/{kind}/lofreq2_somatic.py', f'{kind} Python workflow fixture'.encode())
                    native_files[kind] = {name: packager.digest((self.root / f'build/{kind}/{name}').read_bytes())
                                          for name in ('lofreq', 'lofreq2_somatic.py')}
            identity = {'schema': 1, 'tool': tool, 'assets': packager.tree_hashes(f'dist/{tool}'),
                        'inputs': self.hashes([f'tools/{tool}/{name}' for name in packager.tree_hashes(f'tools/{tool}')] + [f'scripts/build-{tool}.sh'])}
            if native_files:
                identity['nativeFiles'] = native_files
            self.write_json(f'dist/{tool}/build-manifest.json', identity)
        self.write_json('validation/somatic/cases.json', self.plan)
        self.write_json('examples/somatic/config.json', config)
        source = b'Original public BAM byte-range fixture'
        self.write('build/somatic-sources/tumor.range.bin', source)
        self.manifest = {'schema_version': 1, 'datasets': {'hcc1395': {'observations': {'fixture': True}}},
                         'regionMappings': [{'originalContig': 'chr21', 'originalStart': 100, 'localContig': 'window1'}],
                         'files': {}, 'sources': {'tumor-range': {'cachePath': 'build/somatic-sources/tumor.range.bin',
                         'sha256': packager.digest(source), 'bytes': len(source), 'range': [100, 100 + len(source) - 1]}},
                         'regeneration': {'files': {}}}
        for name in ('tumor.bam', 'normal.bam', 'reference.fa', 'regions.bed', 'truth.vcf'):
            data = f'Matched biological-data fixture: {name}'.encode()
            relative = 'hcc1395/' + name
            self.write('datasets/somatic/' + relative, data)
            self.manifest['files'][relative] = {'sha256': packager.digest(data), 'bytes': len(data)}
        self.write_json('datasets/somatic/manifest.json', self.manifest)
        self.builds = {tool: packager.check_build_identity(tool)[2] for tool in packager.TOOLS}
        self.reports = {tool: self.make_reports(tool) for tool in packager.TOOLS}

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def write_json(self, name, data):
        return self.write(name, packager.json_bytes(data))

    def hashes(self, names):
        return {name: packager.digest((self.root / name).read_bytes()) for name in names}

    def make_reports(self, tool):
        cases = []
        for case_id, spec in packager.planned_cases(tool).items():
            failure = spec['kind'] == 'failure'
            biological = packager.digest(('filtered' if spec['id'] == 'filtered' else 'baseline').encode())
            executions = {kind: {'exitCode': 1 if failure else 0, 'canonicalExitCode': 1 if failure else 0, 'signal': None,
                                 'inputsFrom': [], 'diagnosticMatched': True if failure else None,
                                 'stdout': packager.digest(b''), 'stderr': packager.digest(b'Expected diagnostic fixture') if failure else packager.digest(b''),
                                 'missingOutputs': ['out.vcf'] if failure else [],
                                 'files': {} if failure else {'out.vcf': packager.digest(b'VCF result fixture')}} for kind in spec['executions']}
            invariants = {'out.vcf': {'records': 1, 'variantRecords': 1, 'snvRecords': 1, 'indelRecords': 0,
                                      'somaticRecords': 1, 'referenceCheckedRecords': 1, 'samples': ['NORMAL', 'TUMOR']}}
            comparisons = {kind: {'equal': True, 'expectedSemanticDigest': biological, 'semanticDigest': biological,
                                  'biologicalDigest': biological, 'invariants': copy.deepcopy(invariants),
                                  'nativeInvariants': copy.deepcopy(invariants)} for kind in spec['comparisons']}
            record = {'id': case_id, 'tool': tool, 'dataset': spec['_dataset'], 'case': spec['id'], 'kind': spec['kind'],
                      'args': spec['args'], 'status': 'passed', 'executions': executions, 'comparisons': comparisons}
            if spec.get('effect') or spec.get('equivalentTo'):
                changed = bool(spec.get('effect'))
                record['optionEffect'] = {'baseline': 'baseline', 'before': packager.digest(b'baseline'), 'after': biological,
                                          'changed': changed, 'expected': 'changed' if changed else 'equivalent'}
            cases.append(record)
        manifest_sha = self.hashes(['datasets/somatic/manifest.json'])['datasets/somatic/manifest.json']
        datasets = {'hcc1395': {'manifestSha256': manifest_sha,
                              'files': {name: record['sha256'] for name, record in self.manifest['files'].items()},
                              'regionMappings': self.manifest['regionMappings'],
                              **self.manifest['datasets']['hcc1395']}}
        paths = []
        for engine in ('node', 'chromium', 'firefox', 'webkit'):
            report = {'schema': 1, 'engine': engine, 'node': 'v22.fixture', 'python': 'Python 3.12.14', 'browserVersion': 'fixture' if engine != 'node' else None,
                      'status': 'passed', 'builds': {tool: self.builds[tool]}, 'runtime': self.hashes(packager.RUNTIME[tool]),
                      'validation': self.hashes(packager.VALIDATION), 'manifestSha256': manifest_sha, 'datasets': datasets,
                      'expectedCases': [case['id'] for case in cases], 'cases': cases}
            paths.append(self.write_json(f'reports/{engine}/summary-{tool}-all.json', report))
        return paths

    def change_report(self, change, tool='somaticsniper', index=0):
        path = self.reports[tool][index]
        value = json.loads(path.read_text())
        change(value)
        path.write_bytes(packager.json_bytes(value))

    def refresh_build(self, tool):
        path = self.root / f'dist/{tool}/build-manifest.json'
        value = json.loads(path.read_text())
        value['assets'] = packager.tree_hashes(f'dist/{tool}', exclude=('build-manifest.json',))
        value['inputs'] = self.hashes(value['inputs'])
        path.write_bytes(packager.json_bytes(value))
        self.builds[tool] = packager.check_build_identity(tool)[2]
        self.reports[tool] = self.make_reports(tool)

    def test_independent_archives_are_deterministic_and_store_each_source_once(self):
        for tool in packager.TOOLS:
            with self.subTest(tool=tool), contextlib.redirect_stdout(io.StringIO()):
                output = packager.write_package(tool, self.root / 'releases', self.reports[tool])
                original = output.read_bytes()
                packager.write_package(tool, self.root / 'releases', self.reports[tool])
                self.assertEqual(original, output.read_bytes())
                self.assertTrue(output.with_suffix('.zip.sha256').read_text().startswith(packager.digest(original)))
                prefix = f'{tool}-1.2.3-wasm/'
                with zipfile.ZipFile(output) as archive:
                    names = archive.namelist()
                    self.assertFalse(any('/build/downloads/' in name or '/build/native/' in name for name in names))
                    other = next(value for value in packager.TOOLS if value != tool)
                    self.assertFalse(any(f'/dist/{other}/' in name or f'/tools/{other}/' in name for name in names))
                    if tool == 'somaticsniper':
                        self.assertFalse(any('/runtime/lofreq' in name or '/runtime/LOFREQ.md' in name for name in names))
                        self.assertNotIn(prefix + 'tests/lofreq.test.mjs', names)
                    else:
                        self.assertIn(prefix + 'runtime/lofreq-somatic.mjs', names)
                        self.assertIn(prefix + 'runtime/LOFREQ.md', names)
                        self.assertIn(prefix + 'tests/lofreq.test.mjs', names)
                    config = json.loads(archive.read(prefix + 'examples/somatic/config.json'))
                    self.assertEqual(list(config['tools']), [tool])
                    self.assertNotIn(f'../../tools/{other}/', archive.read(prefix + 'examples/somatic/README.md').decode())
                    for name in (f'dist/{tool}/sources/{tool}.tar.gz', 'build/somatic-sources/tumor.range.bin', 'validation-reports/webkit.json'):
                        self.assertIn(prefix + name, names)
                    for line in archive.read(prefix + 'SHA256SUMS').decode().splitlines():
                        checksum, name = line.split('  ', 1)
                        self.assertEqual(packager.digest(archive.read(prefix + name)), checksum)
                    source = json.loads(archive.read(prefix + 'source-manifest.json'))
                    for name, checksum in source['files'].items():
                        self.assertEqual(packager.digest(archive.read(prefix + name)), checksum)

    def test_missing_or_duplicate_browser_reports_prevent_release(self):
        for paths in (self.reports['somaticsniper'][:-1], self.reports['somaticsniper'] + self.reports['somaticsniper'][:1]):
            with self.subTest(count=len(paths)), self.assertRaisesRegex(RuntimeError, 'missing passing reports|duplicate'):
                packager.collect('somaticsniper', paths)

    def test_workflow_stages_and_derived_inputs_must_match_observed_native_execution(self):
        cases = self.plan['tools']['lofreq']['cases']
        cases[0].update(workflow=True, expectedStages=2)
        dependency = {'case': 'baseline', 'path': 'out.vcf', 'as': 'input.vcf'}
        cases[1]['inputsFrom'] = [dependency]
        self.write_json('validation/somatic/cases.json', self.plan)
        self.reports['lofreq'] = self.make_reports('lofreq')
        for path in self.reports['lofreq']:
            report = json.loads(path.read_text())
            stages = [['call', 'tumor.bam'], ['filter', 'out.vcf']]
            report['cases'][0]['workflow'] = {
                'expectedStages': 2, 'oracle': 'unchanged upstream Python workflow', 'nativeStages': stages,
                'targets': {target: {'stages': stages, 'equal': True, 'successfulStages': 2}
                            for target in ('native', 'patched-native', 'wasm')}}
            for target, execution in report['cases'][1]['executions'].items():
                execution['inputsFrom'] = [{**dependency, 'sha256': report['cases'][0]['executions'][target]['files']['out.vcf']}]
            path.write_bytes(packager.json_bytes(report))
        packager.collect('lofreq', self.reports['lofreq'])
        path = self.reports['lofreq'][0]
        original = path.read_bytes()
        mutations = [lambda report: report['cases'][0]['workflow']['targets']['wasm'].update(successfulStages=1),
                     lambda report: report['cases'][0]['workflow']['targets']['wasm'].update(stages=[['different']]),
                     lambda report: report['cases'][1]['executions']['wasm']['inputsFrom'][0].update(sha256='0' * 64),
                     lambda report: report['cases'][1]['executions']['native'].pop('inputsFrom')]
        for change in mutations:
            path.write_bytes(original)
            self.change_report(change, tool='lofreq')
            with self.assertRaises(RuntimeError):
                packager.collect('lofreq', self.reports['lofreq'])

    def test_reports_cannot_reduce_scope_or_replace_comparisons(self):
        path = self.reports['somaticsniper'][0]
        original = path.read_bytes()
        mutations = [lambda report: (report['cases'].pop(), report['expectedCases'].pop()),
                     lambda report: report['cases'][0]['executions'].pop('native'),
                     lambda report: report['cases'][0]['comparisons'].pop('native-contract'),
                     lambda report: report['cases'][0]['comparisons']['wasm'].update(equal=False),
                     lambda report: report['cases'][0]['comparisons']['wasm'].update(expectedSemanticDigest='0' * 64, semanticDigest='0' * 64),
                     lambda report: report['cases'][3]['comparisons']['wasm'].update(expectedSemanticDigest='0' * 64, semanticDigest='0' * 64),
                     lambda report: report['cases'][0]['comparisons']['wasm']['invariants']['out.vcf'].update(somaticRecords=0),
                     lambda report: report['cases'][0]['comparisons']['wasm'].pop('nativeInvariants'),
                     lambda report: report['cases'][-1]['executions']['wasm'].pop('diagnosticMatched'),
                     lambda report: report.pop('python'),
                     lambda report: report['cases'][0].update(args=['different.bam']),
                     lambda report: report['cases'][1]['optionEffect'].update(changed=False),
                     lambda report: report['cases'][0]['executions']['wasm']['files'].clear()]
        for index, change in enumerate(mutations):
            with self.subTest(index=index):
                path.write_bytes(original)
                self.change_report(change)
                with self.assertRaises(RuntimeError):
                    packager.collect('somaticsniper', self.reports['somaticsniper'])

    def test_unexpected_signals_and_different_failure_statuses_are_rejected(self):
        path = self.reports['lofreq'][0]
        original = path.read_bytes()
        for target in ('native', 'patched-native', 'wasm'):
            with self.subTest(target=target):
                path.write_bytes(original)
                self.change_report(lambda report: report['cases'][0]['executions'][target].update(signal='SIGSEGV', exitCode=None, canonicalExitCode=None), 'lofreq')
                with self.assertRaisesRegex(RuntimeError, 'unexpected crash'):
                    packager.collect('lofreq', self.reports['lofreq'])
        path.write_bytes(original)
        self.change_report(lambda report: report['cases'][-1]['executions']['wasm'].update(exitCode=2, canonicalExitCode=2), 'lofreq')
        with self.assertRaisesRegex(RuntimeError, 'failure exits disagree'):
            packager.collect('lofreq', self.reports['lofreq'])

    def test_all_tool_cli_checks_both_before_replacing_any_zip(self):
        output = self.root / 'releases'
        output.mkdir()
        previous = output / 'somaticsniper-1.2.3-wasm.zip'
        previous.write_bytes(b'Previous reviewed archive fixture')
        (self.root / 'build/native/lofreq').unlink()
        arguments = ['package-somatic.py', '--tool', 'all', '--output', str(output)]
        for paths in self.reports.values():
            for path in paths:
                arguments.extend(['--validation-report', str(path)])
        with patch.object(packager.sys, 'argv', arguments), self.assertRaises(RuntimeError):
            packager.main()
        self.assertEqual(previous.read_bytes(), b'Previous reviewed archive fixture')
        self.assertEqual(list(output.iterdir()), [previous])

    def test_every_native_workflow_helper_is_bound_to_the_tested_environment(self):
        self.write('build/native/lofreq2_somatic.py', b'Changed native workflow')
        with self.assertRaisesRegex(RuntimeError, 'helper environment differs'):
            packager.collect('lofreq', self.reports['lofreq'])

    def test_changed_recipe_runtime_validation_or_data_invalidates_evidence(self):
        for name in ('tools/somaticsniper/build.py', 'runtime/index.mjs', 'validation/somatic/compare.py', 'datasets/somatic/hcc1395/tumor.bam'):
            with self.subTest(name=name):
                path = self.root / name
                original = path.read_bytes()
                path.write_bytes(original + b'changed')
                with self.assertRaisesRegex(RuntimeError, 'Checksum mismatch|differs'):
                    packager.collect('somaticsniper', self.reports['somaticsniper'])
                path.write_bytes(original)

    def test_refreshed_build_manifest_cannot_reuse_old_reports(self):
        path = self.root / 'dist/somaticsniper/build-manifest.json'
        identity = json.loads(path.read_text())
        identity['annotation'] = 'A new build manifest identity'
        path.write_bytes(packager.json_bytes(identity))
        with self.assertRaisesRegex(RuntimeError, 'build inputs differ'):
            packager.collect('somaticsniper', self.reports['somaticsniper'])

    def test_fresh_reports_cannot_relabel_changed_or_extra_source_archives(self):
        self.write('dist/somaticsniper/sources/unpinned.tar.bz2', b'Unpinned archive')
        self.refresh_build('somaticsniper')
        with self.assertRaisesRegex(RuntimeError, 'missing or unpinned archives'):
            packager.collect('somaticsniper', self.reports['somaticsniper'])
        (self.root / 'dist/somaticsniper/sources/unpinned.tar.bz2').unlink()
        self.write('dist/somaticsniper/sources/dependency.tar.gz', b'Changed original dependency source')
        self.refresh_build('somaticsniper')
        with self.assertRaisesRegex(RuntimeError, 'Checksum mismatch.*dependency'):
            packager.collect('somaticsniper', self.reports['somaticsniper'])

    def test_provenance_chunks_and_distributed_notices_are_verified(self):
        self.write('build/somatic-sources/tumor.range.bin', b'Changed public source range')
        with self.assertRaisesRegex(RuntimeError, 'Checksum mismatch'):
            packager.collect('somaticsniper', self.reports['somaticsniper'])
        self.write('dist/lofreq/notices/LICENSE.dependency', b'Different notice')
        self.refresh_build('lofreq')
        with self.assertRaisesRegex(RuntimeError, 'notice differs'):
            packager.collect('lofreq', self.reports['lofreq'])

    def test_paths_and_symlinks_are_not_allowed_to_escape_or_alias_inputs(self):
        for name in ('../secret', '/absolute', 'C:/absolute', 'a//b', 'a/./b', 'a\\b', 'a\nfile', '.git/config'):
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, 'Unsafe package path'):
                packager.local_path(name)
        path = self.root / 'alias'
        path.symlink_to(self.root / 'runtime/index.mjs')
        with self.assertRaisesRegex(RuntimeError, 'missing or unsafe'):
            packager.required(path)


if __name__ == '__main__':
    unittest.main()
