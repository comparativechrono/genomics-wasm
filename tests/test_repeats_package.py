"""Adversarial packaging fixtures; these synthetic reports are never release evidence."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4
import zipfile

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('repeat_packager', REPO / 'scripts/package-repeats.py')
pkg = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pkg)


def wasm_archive():
    data = pkg.WASM_HEADER
    header = f'{"fixture.o/":<16}{0:<12}{0:<6}{0:<6}{"100644":<8}{len(data):<10}`\n'.encode()
    return b'!<arch>\n' + header + data


class PackageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        root_patch = patch.object(pkg, 'ROOT', self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        names = ['LICENSE', *pkg.RUNTIME, *pkg.VALIDATION, 'runtime/REPEATS.md', 'runtime/hts.mjs', 'runtime/HTS.md',
                 'scripts/package-repeats.py', 'scripts/test-repeats-package.mjs', 'scripts/test-repeats-demo.mjs',
                 'scripts/test-hts-sdk.py', 'tests/fixtures/hts-sdk.c', 'scripts/setup-emsdk.sh',
                 'tests/test_repeats_package.py', 'tests/runtime.test.mjs', 'tests/fixtures/mock-tool.mjs',
                 'examples/repeats/index.html', 'examples/repeats/app.mjs']
        for name in names:
            self.write(name, f'Labelled synthetic fixture: {name}\n'.encode())
        self.write('scripts/package-hts.py', (REPO / 'scripts/package-hts.py').read_bytes())
        self.write('validation/repeats/compare.py', (REPO / 'validation/repeats/compare.py').read_bytes())
        self.demo = {'commands': {'expansionhunter': {'tool': 'expansionhunter'},
                                  'samtools-sort': {'tool': 'samtools'},
                                  'samtools-index': {'tool': 'samtools'},
                                  'reviewer': {'tool': 'reviewer'}}}
        self.write('examples/repeats/config.json', (json.dumps(self.demo, indent=2) + '\n').encode())
        self.write_json('package.json', {'devDependencies': {'playwright': '1.64.0'}})
        self.write_json('package-lock.json', {'name': 'fixture', 'version': '0.1.0', 'lockfileVersion': 3,
            'packages': {'': {'name': 'fixture', 'version': '0.1.0', 'devDependencies': {'playwright': '1.64.0'}}}})
        self.plan = {'schema': 1, 'tool': 'repeats', 'versions': pkg.VERSIONS, 'runtime': list(pkg.RUNTIME), 'cases': [
            {'id': 'synthetic-success', 'kind': 'success', 'inputs': [{'source': 'common/input.txt', 'as': 'input.txt'}],
             'steps': [{'tool': tool, 'args': ['input.txt'], 'outputs': [], 'stdoutFile': 'result.txt'}
                       for tool in pkg.PROGRAMS] +
                      [{'tool': 'samtools', 'args': ['view', 'result.txt'], 'oracle': True, 'outputs': [], 'stdoutFile': 'result.txt'}],
             'artifacts': [{'path': 'result.txt', 'kind': 'text', 'contract': {'exact': 'Synthetic result\n'}}]},
            {'id': 'synthetic-failure', 'kind': 'failure', 'inputs': [],
             'steps': [{'tool': 'reviewer', 'args': ['--missing'], 'outputs': []}],
             'artifacts': [], 'diagnosticRegex': 'unknown option', 'expectedExitCode': 1}]}
        self.write_json('validation/repeats/cases.json', self.plan)
        self.install_data()
        self.install_builds()
        self.builds = pkg.check_builds()[2]
        self.reports = self.make_reports()

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def write_json(self, name, value):
        return self.write(name, pkg.json_bytes(value))

    def hashes(self, names):
        return {name: pkg.digest((self.root / name).read_bytes()) for name in names}

    def install_data(self):
        self.write('datasets/repeats/common/input.txt', b'Explicitly synthetic input\n')
        records, projects = [], {}
        names = ['common/input.txt']
        for tool, repository in [('expansionhunter', 'Illumina/ExpansionHunter'), ('reviewer', 'Illumina/REViewer')]:
            name = f'upstream/{tool}/LICENSE.txt'
            raw = b'Public fixture licence\n'
            self.write(f'datasets/repeats/{name}', raw)
            projects[tool] = {'repository': repository, 'version': pkg.VERSIONS[tool], 'commit': 'a' * 40, 'license': 'Fixture licence'}
            records.append({'outputPath': name, 'sourcePath': 'LICENSE.txt', 'commit': 'a' * 40,
                            'url': f'https://raw.githubusercontent.com/{repository}/' + 'a' * 40 + '/LICENSE.txt',
                            'repository': repository, 'project': tool, 'license': 'Fixture licence',
                            'sha256': pkg.digest(raw), 'bytes': len(raw)})
            names.append(name)
        self.write_json('datasets/repeats/provenance/sources.lock.json', {'schema': 1, 'files': records, 'projects': projects})
        self.dataset = {'schema': 1, 'tool': 'repeats', 'sourcesLockSha256': pkg.digest(
            (self.root / 'datasets/repeats/provenance/sources.lock.json').read_bytes()),
            'files': {name: {'sha256': pkg.digest((self.root / 'datasets/repeats' / name).read_bytes()),
                             'bytes': (self.root / 'datasets/repeats' / name).stat().st_size} for name in names}}
        self.write_json('datasets/repeats/manifest.json', self.dataset)

    def install_builds(self):
        for tool in ('expansionhunter', 'reviewer'):
            source = f'{tool}.tar.gz'
            raw = f'Synthetic original {tool} source'.encode()
            lock = {'schema': 1, 'tool': tool, 'version': pkg.VERSIONS[tool], 'emscripten': '3.1.64',
                    'source': {'archive': source, 'url': 'https://example.org/' + source,
                               'sha256': pkg.digest(raw), 'bytes': len(raw), 'license': 'Fixture licence'},
                    'dependencies': {}, 'requiredNotices': ['notices/LICENSE']}
            self.write_json(f'tools/{tool}/versions.lock.json', lock)
            self.write(f'tools/{tool}/README.md', b'Synthetic build documentation\n')
            self.write(f'tools/{tool}/notices/LICENSE', b'Synthetic licence\n')
            self.write(f'scripts/build-{tool}.py', b'# Synthetic build recipe\n')
            self.write(f'dist/{tool}/sources/{source}', raw)
            self.write(f'dist/{tool}/notices/LICENSE', b'Synthetic licence\n')
            self.write(f'dist/{tool}/{tool}.mjs', b'// Synthetic JS fixture\n')
            self.write(f'dist/{tool}/{tool}.wasm', pkg.WASM_HEADER)
            for kind in ('native', 'patched-native'):
                self.write(f'build/{kind}/{tool}/{pkg.PROGRAMS[tool]}', b'Synthetic native fixture')
        hts = pkg.hts_packager()
        plugins = ['fill-tags', 'parental-origin', 'split-vep', 'trio-dnm2', 'trio-stats', 'trio-switch-rate']
        components = {}
        for tool in ('bcftools', 'samtools', 'htslib'):
            archive = f'{tool}-1.21.tar.gz'
            raw = f'Synthetic original {archive}'.encode()
            components[tool] = {'version': '1.21', 'archive': archive, 'url': 'https://example.org/' + archive,
                                'bytes': len(raw), 'sha256': pkg.digest(raw), 'license': 'Fixture licence'}
            self.write(f'dist/hts-1.21/sources/{archive}', raw)
        lock = {'schema': 1, 'tool': 'hts', 'emscripten': '3.1.64', 'programs': list(hts.PROGRAMS),
                'versions': {'1.21': {'version': '1.21', 'components': components, 'plugins': plugins,
                                      'requiredNotices': ['notices/LICENSE']}}, 'dependencies': {}}
        self.write_json('tools/hts/versions.lock.json', lock)
        self.write('tools/hts/README.md', b'Synthetic HTS documentation\n')
        self.write('tools/hts/notices/LICENSE', b'Synthetic HTS licence\n')
        self.write('dist/hts-1.21/notices/LICENSE', b'Synthetic HTS licence\n')
        self.write('scripts/build-hts.py', b'# Synthetic HTS recipe\n')
        for program in hts.PROGRAMS:
            self.write(f'dist/hts-1.21/{program}.mjs', b'// Synthetic JS fixture\n')
            self.write(f'dist/hts-1.21/{program}.wasm', pkg.WASM_HEADER)
            for kind in ('native', 'patched-native'):
                self.write(f'build/{kind}/hts-1.21/{program}', b'Synthetic native fixture')
        for name in ('htslib/libhts.a', 'htslib/lib/libz.a', 'htslib/lib/libbz2.a', 'htslib/lib/liblzma.a'):
            self.write(f'dist/hts-1.21/{name}', wasm_archive())
        for name in ('htslib/include/htslib/hts.h', 'htslib/include/htslib/vcf.h', 'htslib/include/htslib/sam.h',
                     'htslib/include/zlib.h', 'htslib/include/bzlib.h', 'htslib/include/lzma.h', 'htslib/README.md'):
            self.write(f'dist/hts-1.21/{name}', b'Synthetic SDK fixture')
        for unit in pkg.UNITS:
            tool = 'hts' if unit == 'hts-1.21' else unit
            inputs = [f'tools/{tool}/{name}' for name in pkg.tree_hashes(f'tools/{tool}')]
            inputs += [f'scripts/build-{tool}.py', 'scripts/setup-emsdk.sh']
            identity = {'schema': 1, 'tool': tool, 'version': '1.21' if tool == 'hts' else pkg.VERSIONS[tool],
                        'compiler': 'emcc 3.1.64', 'assets': pkg.tree_hashes(f'dist/{unit}'),
                        'inputs': self.hashes(inputs),
                        'nativeFiles': {kind: pkg.tree_hashes(f'build/{kind}/{unit}') for kind in ('native', 'patched-native')}}
            if tool == 'hts':
                identity.update(programs=list(hts.PROGRAMS), plugins=plugins)
            self.write_json(f'dist/{unit}/build-manifest.json', identity)

    def make_reports(self):
        comparator = pkg.module_from_file('test_repeat_comparator', self.root / 'validation/repeats/compare.py')
        paths = []
        for engine in sorted(pkg.ENGINES):
            run_id = str(uuid4())
            base = f'reports/repeats/{engine}/{run_id}'
            cases = []
            for case in self.plan['cases']:
                record = {'id': case['id'], 'kind': case['kind'], 'steps': case['steps'],
                          'status': 'passed', 'executions': {}, 'comparisons': {}}
                failure = case['kind'] == 'failure'
                for target in ('native', 'patched-native', 'wasm'):
                    directory = f'{base}/{case["id"]}/{target}'
                    workspace = {}
                    for item in case['inputs']:
                        data = (self.root / 'datasets/repeats' / item['source']).read_bytes()
                        self.write(f'{directory}/inputs/{item["as"]}', data)
                        workspace[item['as']] = {'sha256': pkg.digest(data), 'bytes': len(data)}
                    record['executions'][target] = []
                    for index, step in enumerate(case['steps']):
                        stdout = b'' if failure else b'Synthetic result\n'
                        stderr = b'unknown option\n' if failure else b''
                        execution = {'step': index, 'tool': step['tool'], 'args': step['args'], 'oracle': step.get('oracle', False),
                            'inputs': {name: info['sha256'] for name, info in workspace.items()},
                            'inputBytes': {name: info['bytes'] for name, info in workspace.items()},
                            'exitCode': int(failure), 'canonicalExitCode': int(failure), 'signal': None, 'missingOutputs': [],
                            'stdout': pkg.digest(stdout), 'stderr': pkg.digest(stderr), 'stdoutBytes': len(stdout), 'stderrBytes': len(stderr),
                            'files': {}, 'fileBytes': {}, 'seconds': 0.001}
                        if target != 'wasm' or step.get('oracle'):
                            origin = 'native' if step.get('oracle') else target
                            execution['command'] = {'program': pkg.native_program(step['tool'], origin), 'args': step['args']}
                        if failure:
                            execution['diagnosticMatched'] = True
                        evidence = f'{directory}/steps/{index}'
                        self.write(f'{evidence}/stdout.txt', stdout)
                        self.write(f'{evidence}/stderr.txt', stderr)
                        if not failure:
                            self.write(f'{evidence}/files/result.txt', stdout)
                            self.write(f'{directory}/result.txt', stdout)
                            execution['files']['result.txt'] = pkg.digest(stdout)
                            execution['fileBytes']['result.txt'] = len(stdout)
                            workspace['result.txt'] = {'sha256': pkg.digest(stdout), 'bytes': len(stdout)}
                        self.write_json(f'{evidence}/command-result.json', execution)
                        record['executions'][target].append(execution)
                if not failure:
                    for kind, target in [('native-contract', 'native'), ('patched-native', 'patched-native'), ('wasm', 'wasm')]:
                        directory = self.root / base / case['id']
                        record['comparisons'][kind] = comparator.compare(directory / 'native', directory / target, case)
                cases.append(record)
            report = {'schema': 1, 'tool': 'repeats', 'versions': pkg.VERSIONS, 'engine': engine, 'status': 'passed',
                'runId': run_id, 'reportDirectory': run_id, 'node': 'v22.synthetic-fixture', 'crossOriginIsolated': False,
                'browserErrors': [], 'externalRequests': [],
                'browserVersion': 'synthetic-fixture' if engine != 'node' else None, 'build': self.builds,
                'runtime': self.hashes(pkg.RUNTIME), 'validation': self.hashes(pkg.VALIDATION),
                'datasetManifestSha256': pkg.digest((self.root / 'datasets/repeats/manifest.json').read_bytes()),
                'expectedCases': [case['id'] for case in self.plan['cases']], 'cases': cases}
            paths.append(self.write_json(f'reports/repeats/{engine}/summary-all.json', report))
        return paths

    def gate(self, reports=None):
        dataset, checksum = pkg.checked_datasets()
        return pkg.check_reports(reports or self.reports, self.builds, dataset, checksum)

    def tamper(self, mutate):
        path = self.reports[0]
        report = json.loads(path.read_text())
        mutate(report)
        path.write_bytes(pkg.json_bytes(report))

    def test_archive_preserves_config_and_complete_checksums(self):
        with contextlib.redirect_stdout(io.StringIO()):
            destination = pkg.write_package(self.root / 'releases', self.reports)
            original = destination.read_bytes()
            pkg.write_package(self.root / 'releases', self.reports)
        self.assertEqual(original, destination.read_bytes())
        with zipfile.ZipFile(destination) as archive:
            contents = {name[len('repeats-wasm/'):]: archive.read(name) for name in archive.namelist()}
        self.assertEqual(contents['examples/repeats/config.json'], (self.root / 'examples/repeats/config.json').read_bytes())
        self.assertEqual(list(json.loads(contents['examples/repeats/config.json'])['commands']), list(self.demo['commands']))
        checks = dict(line.split('  ', 1)[::-1] for line in contents['SHA256SUMS'].decode().splitlines())
        self.assertEqual(set(checks), set(contents) - {'SHA256SUMS'})
        for name, checksum in checks.items():
            self.assertEqual(pkg.digest(contents[name]), checksum)
        self.assertEqual(json.loads(contents['release.json'])['versions'], pkg.VERSIONS)
        self.assertEqual(destination.with_suffix('.zip.sha256').read_text().split()[0], pkg.digest(original))

    def test_missing_duplicate_or_reused_engine_rejected(self):
        for reports in (self.reports[:-1], self.reports[:-1] + self.reports[:1]):
            with self.assertRaisesRegex(RuntimeError, 'independent|duplicate'):
                self.gate(reports)
        first = json.loads(self.reports[0].read_text())
        second = json.loads(self.reports[1].read_text())
        second.update(runId=first['runId'], reportDirectory=first['runId'])
        self.reports[1].write_bytes(pkg.json_bytes(second))
        with self.assertRaisesRegex(RuntimeError, 'independent run identity'):
            self.gate()

    def test_summary_tampering_rejected(self):
        mutations = [lambda r: r.update(status='failed'), lambda r: r.update(versions={}),
            lambda r: r.update(expectedCases=[]), lambda r: r['cases'].pop(),
            lambda r: r['browserErrors'].append('Uncaught worker error'),
            lambda r: r['externalRequests'].append('https://example.org/remote-data'),
            lambda r: r['runtime'].update({'runtime/execute.mjs': '0' * 64}),
            lambda r: r['build']['reviewer'].update(buildManifest='0' * 64),
            lambda r: r.update(datasetManifestSha256='0' * 64),
            lambda r: r['cases'][0]['steps'][0]['args'].append('--changed'),
            lambda r: r['cases'][0]['executions']['wasm'][1]['inputs'].update({'result.txt': '0' * 64}),
            lambda r: r['cases'][0]['executions']['wasm'][0].update(stdoutBytes=999),
            lambda r: r['cases'][0]['executions']['wasm'][0].update(exitCode=1),
            lambda r: r['cases'][0]['executions']['wasm'][0].update(signal='SIGSEGV'),
            lambda r: r['cases'][0]['executions']['wasm'][-1]['command'].update(program='build/patched-native/hts-1.21/samtools'),
            lambda r: r['cases'][0]['comparisons']['wasm'].update(biologicalDigest='0' * 64),
            lambda r: r['cases'][1]['executions']['wasm'][0].update(diagnosticMatched=False),
            lambda r: r.update(reportDirectory='../escape')]
        original = [path.read_bytes() for path in self.reports]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                for path, data in zip(self.reports, original):
                    path.write_bytes(data)
                self.tamper(mutation)
                with self.assertRaises((RuntimeError, ValueError)):
                    self.gate()

    def test_missing_wasm_tool_coverage_rejected(self):
        self.plan['cases'][0]['steps'][1]['oracle'] = True
        self.write_json('validation/repeats/cases.json', self.plan)
        with self.assertRaisesRegex(RuntimeError, 'every shipped repeat-analysis tool'):
            pkg.planned_cases()

    def test_raw_evidence_tampering_rejected(self):
        report = json.loads(self.reports[0].read_text())
        base = self.reports[0].parent / report['runId'] / 'synthetic-success' / 'wasm'
        for name in ('steps/0/stdout.txt', 'steps/0/files/result.txt', 'result.txt', 'inputs/input.txt'):
            path = base / name
            original = path.read_bytes()
            path.write_bytes(b'Altered evidence')
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                self.gate()
            path.write_bytes(original)

    def test_stale_sources_fixtures_notices_and_binaries_rejected(self):
        for name, check in [('runtime/execute.mjs', self.gate), ('datasets/repeats/common/input.txt', pkg.checked_datasets),
                            ('tools/reviewer/notices/LICENSE', pkg.check_builds),
                            ('dist/reviewer/reviewer.wasm', pkg.check_builds),
                            ('build/native/expansionhunter/ExpansionHunter', pkg.check_builds),
                            ('dist/hts-1.21/samtools.wasm', pkg.check_builds)]:
            path = self.root / name
            original = path.read_bytes()
            path.write_bytes(b'Changed fixture')
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                check()
            path.write_bytes(original)

    def test_undeclared_files_and_unsafe_paths_rejected(self):
        self.write('datasets/repeats/common/unlisted.txt', b'Unpinned fixture')
        with self.assertRaisesRegex(RuntimeError, 'Every repeat fixture'):
            pkg.checked_datasets()
        for name in ('../escape', '/absolute', 'C:/windows', 'a//b', 'a/./b', 'a\\b'):
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                pkg.relative_name(name)
        target = self.root / 'dist/reviewer/linked'
        target.symlink_to(self.root / 'dist/reviewer/reviewer.wasm')
        with self.assertRaisesRegex(RuntimeError, 'symlink'):
            pkg.check_builds()


if __name__ == '__main__':
    unittest.main()
