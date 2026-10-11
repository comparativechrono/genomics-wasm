"""Strelka2 release-gate tests using labelled fixtures, never simulated tool results."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import tarfile
import unittest
from unittest.mock import patch
import zipfile

SPEC = importlib.util.spec_from_file_location('strelka2_packager', Path(__file__).resolve().parents[1] / 'scripts/package-strelka2.py')
packager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packager)
WORKFLOW_SOURCE = (Path(__file__).resolve().parents[1] / 'validation/strelka2/workflow.py').read_bytes()


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        root_patch = patch.object(packager, 'ROOT', self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        for name in ('LICENSE', *{path for paths in packager.RUNTIME.values() for path in paths}, *packager.VALIDATION,
                     'runtime/README.md', 'runtime/STRELKA2.md', 'validation/README.md', 'scripts/setup-emsdk.sh', 'scripts/package-strelka2.py',
                     'scripts/test-strelka2-demo.mjs', 'scripts/fetch-strelka2-test-data.py',
                     'tests/test_strelka2_compare.py', 'tests/test_strelka2_package.py', 'tests/runtime.test.mjs', 'tests/strelka2.test.mjs', 'tests/fixtures/mock-tool.mjs',
                     'examples/strelka2/index.html', 'examples/strelka2/app.mjs', 'examples/strelka2/README.md',
                     'validation/strelka2/README.md', 'datasets/strelka2/README.md'):
            self.write(name, f'Labelled source fixture: {name}\n'.encode())
        self.write('runtime/README.md', b'# Runtime fixture\n\n## Interface\nFixture interface.\n')
        self.write('validation/strelka2/workflow.py', WORKFLOW_SOURCE)
        self.write_json('package.json', {'devDependencies': {'playwright': '1.64.0'}})
        self.write_json('package-lock.json', {'name': 'original', 'version': '0.1.0', 'lockfileVersion': 3,
                                            'packages': {'': {'name': 'original', 'version': '0.1.0', 'devDependencies': {'playwright': '1.64.0'}}}})
        self.plan = {'schema': 1, 'tool': 'strelka2', 'datasets': ['hcc1395'], 'cases': []}
        config = {'datasets': {'hcc1395': {}}, 'tools': {}}
        baseline = {'id': 'baseline', 'kind': 'success', 'args': ['tumor.bam', 'normal.bam', 'out.vcf'],
                    'outputs': ['out.vcf'], 'executions': ['native', 'patched-native', 'wasm'],
                    'vcfs': [{'path': 'out.vcf', 'minVariants': 1, 'minSomatic': 1, 'expectedSamples': ['NORMAL', 'TUMOR']}],
                    'comparisons': ['native-contract', 'patched-native', 'wasm']}
        python_archive = b'Labelled CPython source archive fixture'
        python_lock = {'version': '2.7.18', 'archive': 'Python-2.7.18.tar.xz', 'sha256': packager.digest(python_archive)}
        self.write_json('tools/strelka2-python/sources.lock.json', python_lock)
        self.write('tools/strelka2-python/README.md', b'Native interpreter build instructions fixture')
        self.write('tools/strelka2-python/LICENSE', b'CPython licence fixture')
        self.write('scripts/setup-strelka2-python.py', b'# Labelled provisioning recipe fixture')
        self.write('dist/strelka2/sources/Python-2.7.18.tar.xz', python_archive)
        python_files = {}
        for name in ('bin/python2.7', 'lib/python2.7/site.py', 'lib/python2.7/site.pyc'):
            data = f'Native interpreter input fixture: {name}'.encode()
            self.write('build/strelka2-python/' + name, data);python_files[name] = packager.digest(data)
        (self.root / 'build/strelka2-python/bin/python2').symlink_to('python2.7')
        python_inputs = [f'tools/strelka2-python/{name}' for name in packager.tree_hashes('tools/strelka2-python')] + ['scripts/setup-strelka2-python.py']
        self.write_json('build/strelka2-python/build-manifest.json', {
            'schema': 1, 'tool': 'cpython', 'version': '2.7.18', 'source': python_lock,
            'inputs': self.hashes(python_inputs), 'files': python_files, 'symlinks': {'bin/python2': 'python2.7'}})
        for tool in packager.TOOLS:
            self.plan['cases'] = [
                copy.deepcopy(baseline), {**baseline, 'id': 'filtered', 'effect': 'baseline', 'args': ['--filter', 'out.vcf']},
                {**baseline, 'id': 'equivalent', 'equivalentTo': 'baseline', 'args': ['--equivalent', 'out.vcf']},
                {**baseline, 'id': 'repeat', 'kind': 'repeat', 'executions': ['wasm'], 'comparisons': ['wasm'], 'baseline': 'baseline'},
                {**baseline, 'id': 'missing', 'kind': 'failure', 'args': ['missing.bam'], 'oncePerTool': True, 'comparisons': [], 'diagnosticRegex': 'diagnostic'},
            ]
            config['tools'][tool] = {'commands': {'somatic': copy.deepcopy(baseline)}}
            embedded = b'Labelled bundled dependency source archive fixture'
            main_archive = io.BytesIO()
            with tarfile.open(fileobj=main_archive, mode='w:gz') as archive:
                info = tarfile.TarInfo('upstream/redist/embedded.tar.bz2');info.size = len(embedded)
                archive.addfile(info, io.BytesIO(embedded))
            archives = {f'{tool}.tar.gz': main_archive.getvalue(),
                        'dependency.tar.gz': b'Dependency original source archive fixture'}
            lock = {'schema': 1, 'tool': tool, 'version': '2.9.10', 'revision': 'fixture', 'emscripten': '3.1.64',
                    'archive': f'{tool}.tar.gz', 'sha256': packager.digest(archives[f'{tool}.tar.gz']),
                    'dependencies': {'dependency': {'archive': 'dependency.tar.gz', 'sha256': packager.digest(archives['dependency.tar.gz'])}},
                    'bundledDependencies': [{'archive': 'embedded.tar.bz2', 'bundledPath': 'redist/embedded.tar.bz2', 'sha256': packager.digest(embedded)}],
                    'requiredNotices': ['LICENSE', 'THIRD_PARTY_LICENSES.txt', 'LICENSE.dependency']}
            self.write_json(f'tools/{tool}/sources.lock.json', lock)
            for name in ('README.md', 'browser.patch', 'build.py', 'LICENSE', 'THIRD_PARTY_LICENSES.txt', 'LICENSE.dependency'):
                data = f'{tool} {name} source/notice fixture\n'.encode()
                self.write(f'tools/{tool}/{name}', data)
                if name in lock['requiredNotices']:
                    prefix = 'notices/' if tool == 'strelka2' else ''
                    self.write(f'dist/{tool}/{prefix}{name}', data)
            self.write(f'scripts/build-{tool}.sh', b'#!/bin/sh\n# Labelled build recipe fixture\n').chmod(0o755)
            for program in packager.PROGRAMS:
                self.write(f'dist/{tool}/{program}.mjs', b'// Labelled module fixture; no tool implementation\n')
                self.write(f'dist/{tool}/{program}.wasm', b'\0asm\1\0\0\0')
            for model in packager.MODELS:
                self.write_json(f'dist/{tool}/{model}', {'labelledModelFixture': model})
            for name, data in archives.items():
                self.write(f'dist/{tool}/sources/{name}', data)
            native_files = {}
            for kind in ('native', 'patched-native'):
                self.write(f'build/{kind}/{tool}', f'{kind} {tool} binary fixture'.encode())
                self.write(f'build/{kind}/strelka2-package/bin/runWorkflow.py', f'{kind} Python workflow fixture'.encode())
                for program in packager.PROGRAMS:
                    self.write(f'build/{kind}/strelka2-package/libexec/{program}', f'Native helper fixture {kind} {program}'.encode())
                for model in packager.MODELS:
                    self.write(f'build/{kind}/strelka2-package/share/{model}', (self.root / f'dist/{tool}/{model}').read_bytes())
                native_files[kind] = {'strelka2': packager.digest((self.root / f'build/{kind}/strelka2').read_bytes()),
                                     **{f'strelka2-package/{name}': digest for name, digest in packager.tree_hashes(f'build/{kind}/strelka2-package').items()}}
            identity = {'schema': 1, 'tool': tool, 'assets': packager.tree_hashes(f'dist/{tool}'),
                        'inputs': self.hashes([f'tools/{tool}/{name}' for name in packager.tree_hashes(f'tools/{tool}')] + [f'scripts/build-{tool}.sh'] + python_inputs)}
            if native_files:
                identity['nativeFiles'] = native_files
            self.write_json(f'dist/{tool}/build-manifest.json', identity)
        self.write_json('validation/strelka2/cases.json', self.plan)
        self.write_json('examples/strelka2/config.json', config)
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
            self.write('datasets/strelka2/' + relative, data)
            self.manifest['files'][relative] = {'sha256': packager.digest(data), 'bytes': len(data)}
        self.write_json('datasets/strelka2/manifest.json', self.manifest)
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
        manifest_sha = self.hashes(['datasets/strelka2/manifest.json'])['datasets/strelka2/manifest.json']
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

    def change_report(self, change, tool='strelka2', index=0):
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

    def test_standalone_archive_has_complete_sources_models_and_checksums(self):
        self.write('validation/strelka2/__pycache__/compare.cpython-fixture.pyc', b'Transient comparator cache')
        with contextlib.redirect_stdout(io.StringIO()):
            output = packager.write_package('strelka2', self.root / 'releases', self.reports['strelka2'])
            first = output.read_bytes()
            packager.write_package('strelka2', self.root / 'releases', self.reports['strelka2'])
        self.assertEqual(first, output.read_bytes())
        self.assertTrue(output.with_suffix('.zip.sha256').read_text().startswith(packager.digest(first)))
        prefix = 'strelka2-2.9.10-wasm/'
        with zipfile.ZipFile(output) as archive:
            names = archive.namelist()
            self.assertFalse(any('/build/downloads/' in name or '/build/native/' in name for name in names))
            self.assertFalse(any('/__pycache__/' in name for name in names))
            for name in ('runtime/strelka2-somatic.mjs', 'tests/strelka2.test.mjs',
                         'tools/strelka2-python/sources.lock.json', 'scripts/setup-strelka2-python.py',
                         'dist/strelka2/sources/Python-2.7.18.tar.xz', 'build/strelka2-python/build-manifest.json'):
                self.assertIn(prefix + name, names)
            for program in packager.PROGRAMS:
                self.assertIn(prefix + f'dist/strelka2/{program}.wasm', names)
            for model in packager.MODELS:
                self.assertIn(prefix + 'dist/strelka2/' + model, names)
            sums = dict(line.split('  ', 1)[::-1] for line in archive.read(prefix + 'SHA256SUMS').decode().splitlines())
            self.assertEqual(set(sums), {name[len(prefix):] for name in names} - {'SHA256SUMS'})
            for name, checksum in sums.items():
                self.assertEqual(packager.digest(archive.read(prefix + name)), checksum)
            source = json.loads(archive.read(prefix + 'source-manifest.json'))
            for name, checksum in source['files'].items():
                self.assertEqual(packager.digest(archive.read(prefix + name)), checksum)

    def test_missing_or_duplicate_browser_reports_prevent_release(self):
        for paths in (self.reports['strelka2'][:-1], self.reports['strelka2'] + self.reports['strelka2'][:1]):
            with self.subTest(count=len(paths)), self.assertRaisesRegex(RuntimeError, 'missing passing reports|duplicate'):
                packager.collect('strelka2', paths)

    def test_workflow_stages_and_derived_inputs_must_match_observed_native_execution(self):
        cases = self.plan['cases']
        cases[0].update(workflow=True)
        dependency = {'case': 'baseline', 'path': 'out.vcf', 'as': 'input.vcf'}
        cases[1]['inputsFrom'] = [dependency]
        self.write_json('validation/strelka2/cases.json', self.plan)
        self.reports['strelka2'] = self.make_reports('strelka2')
        for path in self.reports['strelka2']:
            report = json.loads(path.read_text())
            stages = [{'tool': 'strelka2', 'args': ['--somatic-snv-file', '/work/out.vcf']}]
            checker = packager.workflow_checker()
            models = {f'/work/__strelka2/{name}': self.builds['strelka2']['assets'][name] for name in packager.MODELS}
            initial = {f'/work/{name.split("/", 1)[1]}': info['sha256'] for name, info in self.manifest['files'].items()}
            initial.update(models)
            returned = {'/work/out.vcf': report['cases'][0]['executions']['wasm']['files']['out.vcf']}
            lock = json.loads((self.root / 'tools/strelka2/sources.lock.json').read_text())
            raw = {'models': models, 'generatedFiles': ['/work/out.vcf'],
                   'upstream': {'version': lock['version'], 'commit': lock['revision'], 'archiveSha256': lock['sha256']},
                   'stages': [{'id': 'call', 'kind': 'native', 'phase': 'workflow', **stages[0],
                               'exitCode': 0, 'missingOutputs': [], 'inputs': initial,
                               'outputs': ['/work/out.vcf'], 'outputHashes': returned}]}
            raw = json.loads(packager.json_bytes(raw))
            _, edges = checker.runtime_stages(raw, initial, returned)
            common = {'schema': 1, 'stages': stages, 'equal': True, 'nativeStages': stages,
                      'nativeStageCount': 1, 'matchedStageCount': 1, 'missingCommands': [], 'extraCommands': [],
                      'commandDigest': checker.digest(sorted(packager.Counter(json.dumps(stage, sort_keys=True) for stage in stages).items()))}
            report['cases'][0]['workflow'] = {
                'oracle': 'unchanged upstream Python2 workflow', 'nativeStages': stages,
                'targets': {target: {**common, 'successfulStages': 1, 'mode': 'native-command-ledger'}
                            for target in ('native', 'patched-native')}}
            report['cases'][0]['workflow']['targets']['wasm'] = {**common, **edges, 'mode': 'runtime-command-ledger-and-file-edges'}
            for target in ('native', 'patched-native'):
                report['cases'][0]['executions'][target]['workflow'] = {'nativeCommands': 'Labelled observed-command fixture'}
            report['cases'][0]['executions']['wasm']['workflow'] = raw
            for target, execution in report['cases'][1]['executions'].items():
                execution['inputsFrom'] = [{**dependency, 'sha256': report['cases'][0]['executions'][target]['files']['out.vcf']}]
            path.write_bytes(packager.json_bytes(report))
        packager.collect('strelka2', self.reports['strelka2'])
        path = self.reports['strelka2'][0]
        original = path.read_bytes()
        mutations = [lambda report: report['cases'][0]['workflow']['targets']['wasm'].update(successfulStages=0),
                     lambda report: report['cases'][0]['workflow']['targets']['wasm'].update(stages=[['different']]),
                     lambda report: report['cases'][0]['executions']['wasm']['workflow']['models'].update({'/work/__strelka2/config/somaticSNVScoringModels.json': '0' * 64}),
                     lambda report: report['cases'][0]['executions']['wasm']['workflow']['stages'][0]['inputs'].update({'/work/tumor.bam': '0' * 64}),
                     lambda report: report['cases'][0]['workflow']['targets']['wasm'].update(retainedOutputs=0),
                     lambda report: report['cases'][1]['executions']['wasm']['inputsFrom'][0].update(sha256='0' * 64),
                     lambda report: report['cases'][1]['executions']['native'].pop('inputsFrom')]
        for change in mutations:
            path.write_bytes(original)
            self.change_report(change, tool='strelka2')
            with self.assertRaises(RuntimeError):
                packager.collect('strelka2', self.reports['strelka2'])

    def test_reports_cannot_reduce_scope_or_replace_comparisons(self):
        path = self.reports['strelka2'][0]
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
                    packager.collect('strelka2', self.reports['strelka2'])

    def test_unexpected_signals_and_different_failure_statuses_are_rejected(self):
        path = self.reports['strelka2'][0]
        original = path.read_bytes()
        for target in ('native', 'patched-native', 'wasm'):
            with self.subTest(target=target):
                path.write_bytes(original)
                self.change_report(lambda report: report['cases'][0]['executions'][target].update(signal='SIGSEGV', exitCode=None, canonicalExitCode=None), 'strelka2')
                with self.assertRaisesRegex(RuntimeError, 'unexpected crash'):
                    packager.collect('strelka2', self.reports['strelka2'])
        path.write_bytes(original)
        self.change_report(lambda report: report['cases'][-1]['executions']['wasm'].update(exitCode=2, canonicalExitCode=2), 'strelka2')
        with self.assertRaisesRegex(RuntimeError, 'failure exits disagree'):
            packager.collect('strelka2', self.reports['strelka2'])

    def test_statistics_depth_and_callable_regions_require_complete_evidence(self):
        spec = {'auxiliary': [
            {'path': 'stats.xml', 'kind': 'stats-xml', 'expectedCounts': {'candidateIndels': 5000000017}},
            {'path': 'stats.tsv', 'kind': 'stats-tsv'},
            {'path': 'depth.tsv', 'kind': 'depth'},
            {'path': 'callable.bed.gz', 'kind': 'bed', 'minRecords': 1}]}
        counts = {'stats.xml': {'candidateIndels': 5000000017, 'nonCandidateIndels': 6000000023},
                  'stats.tsv': {'CallRegionCandidateIndels': 5000000017, 'CallRegionNonCandidateIndels': 6000000023},
                  'depth.tsv': {'contigs': 2, 'positiveContigs': 2},
                  'callable.bed.gz': {'records': 2, 'bases': 100}}
        comparison = {'invariants': counts, 'nativeInvariants': copy.deepcopy(counts)}
        packager.check_invariants('labelled-fixture', spec, comparison)
        changes = [lambda values: values['stats.xml'].update(candidateIndels=705032721),
                   lambda values: values['stats.tsv'].pop('CallRegionNonCandidateIndels'),
                   lambda values: values['depth.tsv'].update(positiveContigs=0),
                   lambda values: values['callable.bed.gz'].update(records=0),
                   lambda values: values['callable.bed.gz'].update(bases=1)]
        for side in ('invariants', 'nativeInvariants'):
            for mutate in changes:
                changed = copy.deepcopy(comparison)
                mutate(changed[side])
                with self.subTest(side=side), self.assertRaises(RuntimeError):
                    packager.check_invariants('labelled-fixture', spec, changed)

    def test_statistics_boundary_must_reuse_real_earlier_output_and_identical_transforms(self):
        boundary = {'case': 'baseline', 'path': 'out.vcf', 'values': [[5000000000, 6000000000], [17, 23]]}
        self.plan['cases'][1]['statisticsBoundary'] = boundary
        self.write_json('validation/strelka2/cases.json', self.plan)
        self.reports['strelka2'] = self.make_reports('strelka2')
        for path in self.reports['strelka2']:
            report = json.loads(path.read_text())
            report['cases'][1]['statisticsBoundary'] = {**boundary, 'targets': {
                target: {'sourceSha256': execution['files']['out.vcf'],
                         'inputs': {'stats-a.xml': packager.digest(b'Wide input fixture A'),
                                    'stats-b.xml': packager.digest(b'Wide input fixture B')}}
                for target, execution in report['cases'][0]['executions'].items()}}
            path.write_bytes(packager.json_bytes(report))
        packager.collect('strelka2', self.reports['strelka2'])
        path = self.reports['strelka2'][0]
        original = path.read_bytes()
        changes = [lambda evidence: evidence.update(values=[[1, 2], [3, 4]]),
                   lambda evidence: evidence['targets']['wasm'].update(sourceSha256='0' * 64),
                   lambda evidence: evidence['targets']['native']['inputs'].pop('stats-b.xml'),
                   lambda evidence: evidence['targets']['wasm']['inputs'].update({'stats-a.xml': '0' * 64}),
                   lambda evidence: evidence['targets'].pop('patched-native')]
        for mutate in changes:
            path.write_bytes(original)
            self.change_report(lambda report: mutate(report['cases'][1]['statisticsBoundary']))
            with self.assertRaisesRegex(RuntimeError, 'statistics-boundary'):
                packager.collect('strelka2', self.reports['strelka2'])

    def test_every_native_workflow_helper_is_bound_to_the_tested_environment(self):
        self.write('build/native/strelka2-package/bin/runWorkflow.py', b'Changed native workflow')
        with self.assertRaisesRegex(RuntimeError, 'workflow environment differs'):
            packager.collect('strelka2', self.reports['strelka2'])

    def test_native_interpreter_bytecode_and_symlinks_are_bound(self):
        path = self.root / 'build/strelka2-python/lib/python2.7/site.pyc'
        original = path.read_bytes()
        path.write_bytes(b'Changed cached interpreter input')
        with self.assertRaisesRegex(RuntimeError, 'Native Python installation differs'):
            packager.collect('strelka2', self.reports['strelka2'])
        path.write_bytes(original)
        alias = self.root / 'build/strelka2-python/bin/python2'
        alias.unlink();alias.symlink_to('../lib/python2.7/site.py')
        with self.assertRaisesRegex(RuntimeError, 'Native Python installation differs'):
            packager.collect('strelka2', self.reports['strelka2'])

    def test_browser_models_must_equal_the_native_workflow_models(self):
        self.write_json('dist/strelka2/' + packager.MODELS[0], {'changedModelFixture': True})
        path = self.root / 'dist/strelka2/build-manifest.json'
        identity = json.loads(path.read_text())
        identity['assets'] = packager.tree_hashes('dist/strelka2', exclude=('build-manifest.json',))
        path.write_bytes(packager.json_bytes(identity))
        with self.assertRaisesRegex(RuntimeError, 'scoring model bytes differ'):
            packager.collect('strelka2', self.reports['strelka2'])

    def test_embedded_dependency_archives_are_verified_without_duplicate_distribution(self):
        path = self.root / 'tools/strelka2/sources.lock.json'
        lock = json.loads(path.read_text());lock['bundledDependencies'][0]['sha256'] = '0' * 64
        path.write_bytes(packager.json_bytes(lock))
        self.refresh_build('strelka2')
        with self.assertRaisesRegex(RuntimeError, 'Embedded source checksum mismatch'):
            packager.collect('strelka2', self.reports['strelka2'])

    def test_changed_recipe_runtime_validation_or_data_invalidates_evidence(self):
        for name in ('tools/strelka2/build.py', 'runtime/index.mjs', 'validation/strelka2/compare.py', 'datasets/strelka2/hcc1395/tumor.bam'):
            with self.subTest(name=name):
                path = self.root / name
                original = path.read_bytes()
                path.write_bytes(original + b'changed')
                with self.assertRaisesRegex(RuntimeError, 'Checksum mismatch|differs'):
                    packager.collect('strelka2', self.reports['strelka2'])
                path.write_bytes(original)

    def test_refreshed_build_manifest_cannot_reuse_old_reports(self):
        path = self.root / 'dist/strelka2/build-manifest.json'
        identity = json.loads(path.read_text())
        identity['annotation'] = 'A new build manifest identity'
        path.write_bytes(packager.json_bytes(identity))
        with self.assertRaisesRegex(RuntimeError, 'build inputs differ'):
            packager.collect('strelka2', self.reports['strelka2'])

    def test_fresh_reports_cannot_relabel_changed_or_extra_source_archives(self):
        self.write('dist/strelka2/sources/unpinned.tar.bz2', b'Unpinned archive')
        self.refresh_build('strelka2')
        with self.assertRaisesRegex(RuntimeError, 'missing or unpinned archives'):
            packager.collect('strelka2', self.reports['strelka2'])
        (self.root / 'dist/strelka2/sources/unpinned.tar.bz2').unlink()
        self.write('dist/strelka2/sources/dependency.tar.gz', b'Changed original dependency source')
        self.refresh_build('strelka2')
        with self.assertRaisesRegex(RuntimeError, 'Checksum mismatch.*dependency'):
            packager.collect('strelka2', self.reports['strelka2'])

    def test_provenance_chunks_and_distributed_notices_are_verified(self):
        self.write('build/somatic-sources/tumor.range.bin', b'Changed public source range')
        with self.assertRaisesRegex(RuntimeError, 'Checksum mismatch'):
            packager.collect('strelka2', self.reports['strelka2'])
        self.write('dist/strelka2/notices/LICENSE.dependency', b'Different notice')
        self.refresh_build('strelka2')
        with self.assertRaisesRegex(RuntimeError, 'notice differs'):
            packager.collect('strelka2', self.reports['strelka2'])

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
