"""Adversarial release-gate fixtures: synthetic evidence, never release validation."""
import contextlib
import copy
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
SPEC = importlib.util.spec_from_file_location('hts_packager', REPO / 'scripts/package-hts.py')
pkg = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pkg)
COMPARATOR = (REPO / 'validation/hts/compare.py').read_bytes()
PLUGINS = ['fill-tags', 'parental-origin', 'split-vep', 'trio-dnm2', 'trio-stats', 'trio-switch-rate']


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
        root_patch.start(); self.addCleanup(root_patch.stop)
        names = ['LICENSE', *pkg.RUNTIME, *pkg.VALIDATION, 'runtime/HTS.md',
                 'scripts/package-hts.py', 'scripts/test-hts-package.mjs', 'scripts/test-hts-demo.mjs',
                 'scripts/test-hts-sdk.py', 'tests/fixtures/hts-sdk.c',
                 'scripts/build-hts.py', 'scripts/setup-emsdk.sh', 'tests/test_hts_package.py',
                 'tests/runtime.test.mjs', 'tests/fixtures/mock-tool.mjs',
                 'examples/hts/index.html', 'examples/hts/app.mjs', 'tools/hts/README.md',
                 'tools/hts/notices/LICENSE']
        for name in names:
            self.write(name, f'Labelled synthetic source fixture: {name}\n'.encode())
        self.write('validation/hts/compare.py', COMPARATOR)
        self.write_json('examples/hts/config.json', {'versions': ['1.21', '1.16']})
        self.write_json('package.json', {'devDependencies': {'playwright': '1.64.0'}})
        self.write_json('package-lock.json', {'name': 'fixture', 'version': '0.1.0', 'lockfileVersion': 3,
            'packages': {'': {'name': 'fixture', 'version': '0.1.0', 'devDependencies': {'playwright': '1.64.0'}}}})
        self.plan = {'schema': 1, 'tool': 'hts', 'versions': list(pkg.VERSIONS), 'cases': [
            {'id': 'synthetic-success', 'kind': 'success', 'plugins': PLUGINS,
             'inputs': [{'source': 'common/input.txt', 'as': 'input.txt'}],
             'steps': [{'tool': 'bcftools', 'args': ['+' + plugin, 'input.txt'], 'outputs': [], 'stdoutFile': 'result.txt'} for plugin in PLUGINS] +
                      [{'tool': 'bcftools', 'args': ['view', 'result.txt'], 'oracle': True, 'outputs': [], 'stdoutFile': 'result.txt'}],
             'artifacts': [{'path': 'result.txt', 'kind': 'text', 'contract': {'exact': 'Synthetic result\n'}}]},
            {'id': 'synthetic-failure', 'kind': 'failure', 'plugins': [], 'inputs': [],
             'steps': [{'tool': 'bcftools', 'args': ['+missing'], 'outputs': []}],
             'artifacts': [], 'diagnosticRegex': 'unknown plugin', 'expectedExitCode': 1}]}
        self.write_json('validation/hts/cases.json', self.plan)
        self.install_data()
        self.install_builds()
        self.builds = {version: pkg.check_build_identity(version)[2] for version in pkg.VERSIONS}
        self.reports = {version: self.make_reports(version) for version in pkg.VERSIONS}

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
        source_records = []
        self.write('datasets/hts/common/input.txt', b'Synthetic input\n')
        for version in pkg.VERSIONS:
            name = f'upstream/{version}/LICENSE'
            raw = b'Labelled synthetic licence fixture\n'
            self.write('datasets/hts/' + name, raw)
            source_records.append({'path': name, 'sourcePath': 'LICENSE', 'commit': 'a' * 40,
                'url': 'https://raw.githubusercontent.com/samtools/bcftools/' + 'a' * 40 + '/LICENSE',
                'sha256': pkg.digest(raw), 'bytes': len(raw)})
        source = self.write_json('datasets/hts/provenance/sources.lock.json',
            {'schema': 1, 'licence': 'Fixture licence', 'files': source_records})
        names = ['common/input.txt', *[record['path'] for record in source_records]]
        self.dataset = {'schema': 1, 'tool': 'hts', 'versions': list(pkg.VERSIONS),
            'sourcesLockSha256': pkg.digest(source.read_bytes()),
            'files': {name: {'sha256': pkg.digest((self.root / 'datasets/hts' / name).read_bytes()),
                             'bytes': (self.root / 'datasets/hts' / name).stat().st_size} for name in names}}
        self.write_json('datasets/hts/manifest.json', self.dataset)

    def install_builds(self):
        lock = {'schema': 1, 'tool': 'hts', 'emscripten': '3.1.64', 'programs': list(pkg.PROGRAMS), 'versions': {}, 'dependencies': {}}
        records = {}
        for version in pkg.VERSIONS:
            components = {}
            for name in ('bcftools', 'samtools', 'htslib'):
                archive = f'{name}-{version}.tar.gz'; raw = f'Synthetic original {archive}'.encode()
                records[archive] = raw
                components[name] = {'version': version, 'archive': archive, 'url': 'https://example.org/' + archive,
                    'bytes': len(raw), 'sha256': pkg.digest(raw), 'license': 'Fixture licence'}
            lock['versions'][version] = {'version': version, 'components': components, 'plugins': PLUGINS,
                                         'requiredNotices': ['notices/LICENSE']}
        self.write_json('tools/hts/versions.lock.json', lock)
        for version in pkg.VERSIONS:
            base = f'dist/hts-{version}'
            for program in pkg.PROGRAMS:
                self.write(f'{base}/{program}.mjs', b'// Labelled JS fixture\n')
                self.write(f'{base}/{program}.wasm', pkg.WASM_HEADER)
                for kind in ('native', 'patched-native'):
                    self.write(f'build/{kind}/hts-{version}/{program}', b'Labelled native fixture')
            for name in ('htslib/libhts.a', 'htslib/lib/libz.a', 'htslib/lib/libbz2.a', 'htslib/lib/liblzma.a'):
                self.write(f'{base}/{name}', wasm_archive())
            for name in ('htslib/include/htslib/hts.h', 'htslib/include/htslib/vcf.h', 'htslib/include/htslib/sam.h',
                         'htslib/include/zlib.h', 'htslib/include/bzlib.h', 'htslib/include/lzma.h', 'htslib/README.md'):
                self.write(f'{base}/{name}', b'Labelled SDK fixture')
            for record in lock['versions'][version]['components'].values():
                self.write(f'{base}/sources/{record["archive"]}', records[record['archive']])
            self.write(f'{base}/notices/LICENSE', (self.root / 'tools/hts/notices/LICENSE').read_bytes())
            inputs = [f'tools/hts/{name}' for name in pkg.tree_hashes('tools/hts')] + ['scripts/build-hts.py', 'scripts/setup-emsdk.sh']
            self.write_json(f'{base}/build-manifest.json', {'schema': 1, 'tool': 'hts', 'version': version,
                'compiler': 'emcc 3.1.64', 'programs': list(pkg.PROGRAMS), 'plugins': PLUGINS,
                'assets': pkg.tree_hashes(base), 'inputs': self.hashes(inputs),
                'nativeFiles': {kind: pkg.tree_hashes(f'build/{kind}/hts-{version}') for kind in ('native', 'patched-native')}})

    def make_reports(self, version):
        paths = []
        spec = importlib.util.spec_from_file_location('fixture_comparator', self.root / 'validation/hts/compare.py')
        comparator = importlib.util.module_from_spec(spec); spec.loader.exec_module(comparator)
        for engine in sorted(pkg.ENGINES):
            run_id = str(uuid4()); base = f'reports/hts-{version}/{engine}/{run_id}'
            cases = []
            for case in self.plan['cases']:
                record = {'id': case['id'], 'kind': case['kind'], 'plugins': case['plugins'], 'steps': case['steps'],
                          'status': 'passed', 'executions': {}, 'comparisons': {}}
                failure = case['kind'] == 'failure'
                for target in ('native', 'patched-native', 'wasm'):
                    directory = f'{base}/{case["id"]}/{target}'; workspace = {}
                    for item in case['inputs']:
                        data = (self.root / 'datasets/hts' / item['source']).read_bytes()
                        self.write(f'{directory}/inputs/{item["as"]}', data)
                        workspace[item['as']] = {'sha256': pkg.digest(data), 'bytes': len(data)}
                    record['executions'][target] = []
                    for index, step in enumerate(case['steps']):
                        stdout = b'' if failure else b'Synthetic result\n'; stderr = b'unknown plugin\n' if failure else b''
                        execution = {'step': index, 'tool': step['tool'], 'args': step['args'], 'oracle': step.get('oracle', False),
                            'inputs': {name: info['sha256'] for name, info in workspace.items()},
                            'inputBytes': {name: info['bytes'] for name, info in workspace.items()},
                            'exitCode': int(failure), 'canonicalExitCode': int(failure), 'signal': None, 'missingOutputs': [],
                            'stdout': pkg.digest(stdout), 'stderr': pkg.digest(stderr), 'stdoutBytes': len(stdout), 'stderrBytes': len(stderr),
                            'files': {}, 'fileBytes': {}, 'seconds': 0.001}
                        if target != 'wasm' or step.get('oracle'):
                            origin = 'native' if step.get('oracle') else target
                            execution['command'] = {'program': f'build/{origin}/hts-{version}/{step["tool"]}', 'args': step['args']}
                        if failure: execution['diagnosticMatched'] = True
                        evidence = f'{directory}/steps/{index}'
                        self.write(f'{evidence}/stdout.txt', stdout); self.write(f'{evidence}/stderr.txt', stderr)
                        if not failure:
                            self.write(f'{evidence}/files/result.txt', stdout); self.write(f'{directory}/result.txt', stdout)
                            execution['files']['result.txt'] = pkg.digest(stdout); execution['fileBytes']['result.txt'] = len(stdout)
                            workspace['result.txt'] = {'sha256': pkg.digest(stdout), 'bytes': len(stdout)}
                        self.write_json(f'{evidence}/command-result.json', execution)
                        record['executions'][target].append(execution)
                if not failure:
                    for kind, target in [('native-contract', 'native'), ('patched-native', 'patched-native'), ('wasm', 'wasm')]:
                        directory = self.root / base / case['id']
                        record['comparisons'][kind] = comparator.compare(directory / 'native', directory / target, case, version)
                cases.append(record)
            report = {'schema': 1, 'tool': 'hts', 'version': version, 'engine': engine, 'status': 'passed',
                'runId': run_id, 'reportDirectory': run_id, 'node': 'v22.synthetic-fixture', 'crossOriginIsolated': False,
                'browserVersion': 'synthetic-fixture' if engine != 'node' else None, 'build': self.builds[version],
                'runtime': self.hashes(pkg.RUNTIME), 'validation': self.hashes(pkg.VALIDATION),
                'datasetManifestSha256': pkg.digest((self.root / 'datasets/hts/manifest.json').read_bytes()),
                'pluginCoverage': PLUGINS, 'expectedCases': [case['id'] for case in self.plan['cases']], 'cases': cases}
            paths.append(self.write_json(f'reports/hts-{version}/{engine}/summary-all.json', report))
        return paths

    def gate(self, version='1.21', reports=None):
        dataset, checksum = pkg.checked_datasets()
        return pkg.check_reports(version, reports or self.reports[version], self.builds[version], dataset, checksum)

    def tamper(self, mutate, version='1.21'):
        path = self.reports[version][0]; report = json.loads(path.read_text()); mutate(report)
        path.write_bytes(pkg.json_bytes(report))

    def test_single_version_archives_and_checksums(self):
        for version in pkg.VERSIONS:
            with self.subTest(version=version), contextlib.redirect_stdout(io.StringIO()):
                destination = pkg.write_package(version, self.root / 'releases', self.reports[version])
                previous = destination.read_bytes()
                pkg.write_package(version, self.root / 'releases', self.reports[version])
                self.assertEqual(previous, destination.read_bytes())
                with zipfile.ZipFile(destination) as archive:
                    prefix = f'hts-{version}-wasm/'
                    contents = {name[len(prefix):]: archive.read(name) for name in archive.namelist()}
                checks = dict(line.split('  ', 1)[::-1] for line in contents['SHA256SUMS'].decode().splitlines())
                self.assertEqual(set(checks), set(contents) - {'SHA256SUMS'})
                for name, checksum in checks.items(): self.assertEqual(checksum, pkg.digest(contents[name]))
                self.assertEqual(json.loads(contents['examples/hts/config.json'])['versions'], [version])
                self.assertEqual(json.loads(contents['release.json'])['upstreamVersion'], version)
                other = '1.16' if version == '1.21' else '1.21'
                self.assertFalse(any(name.startswith(f'dist/hts-{other}/') for name in contents))
                self.assertIn('runtime/execute.mjs', contents)
                self.assertIn('build/hts-fixture-sources/upstream/1.16/LICENSE', contents)
                self.assertEqual(destination.with_suffix('.zip.sha256').read_text().split()[0], pkg.digest(previous))
        self.assertEqual(json.loads((self.root / 'examples/hts/config.json').read_text())['versions'], ['1.21', '1.16'])

    def test_missing_engine_or_mixed_version_rejected(self):
        for paths in (self.reports['1.21'][:-1], self.reports['1.21'][:-1] + self.reports['1.16'][-1:]):
            with self.assertRaisesRegex(RuntimeError, 'require independent'): self.gate(reports=paths)

    def test_report_tampering_rejected(self):
        mutations = [
            lambda r: r.update(status='failed'), lambda r: r.update(pluginCoverage=PLUGINS[:-1]),
            lambda r: r.update(expectedCases=[]), lambda r: r['cases'].pop(),
            lambda r: r['runtime'].update({'runtime/execute.mjs': '0' * 64}),
            lambda r: r['build'].update(buildManifest='0' * 64), lambda r: r.update(datasetManifestSha256='0' * 64),
            lambda r: r['cases'][0]['steps'][0]['args'].append('--changed'),
            lambda r: r['cases'][0]['executions']['wasm'][1]['inputs'].update({'result.txt': '0' * 64}),
            lambda r: r['cases'][0]['executions']['wasm'][0].update(stdoutBytes=999),
            lambda r: r['cases'][0]['executions']['wasm'][0].update(exitCode=1),
            lambda r: r['cases'][0]['executions']['wasm'][0].update(signal='SIGSEGV'),
            lambda r: r['cases'][0]['executions']['wasm'][-1]['command'].update(program='build/patched-native/hts-1.21/bcftools'),
            lambda r: r['cases'][0]['comparisons']['wasm'].update(biologicalDigest='0' * 64),
            lambda r: r['cases'][0]['comparisons']['wasm']['artifacts'][0].update(contract={}),
            lambda r: r['cases'][1]['executions']['wasm'][0].update(diagnosticMatched=False),
            lambda r: r.update(reportDirectory='../escape'),
        ]
        original = [path.read_bytes() for path in self.reports['1.21']]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                for path, data in zip(self.reports['1.21'], original): path.write_bytes(data)
                self.tamper(mutation)
                with self.assertRaises((RuntimeError, ValueError)): self.gate()

    def test_raw_evidence_tampering_rejected(self):
        report = json.loads(self.reports['1.21'][0].read_text())
        base = self.reports['1.21'][0].parent / report['runId'] / 'synthetic-success' / 'wasm'
        for name in ('steps/0/stdout.txt', 'steps/0/files/result.txt', 'result.txt', 'inputs/input.txt'):
            path = base / name; original = path.read_bytes(); path.write_bytes(b'Altered evidence')
            with self.subTest(name=name), self.assertRaises(RuntimeError): self.gate()
            path.write_bytes(original)

    def test_source_fixture_and_compiled_bytes_rejected_when_stale(self):
        for name, check in [('runtime/execute.mjs', self.gate), ('datasets/hts/common/input.txt', pkg.checked_datasets),
                            ('tools/hts/notices/LICENSE', lambda: pkg.check_build_identity('1.21')),
                            ('dist/hts-1.21/bcftools.wasm', lambda: pkg.check_build_identity('1.21')),
                            ('build/native/hts-1.21/bcftools', lambda: pkg.check_build_identity('1.21'))]:
            path = self.root / name; original = path.read_bytes(); path.write_bytes(b'Changed fixture')
            with self.subTest(name=name), self.assertRaises(RuntimeError): check()
            path.write_bytes(original)

    def test_paths_and_static_archives_fail_closed(self):
        for name in ('../escape', '/absolute', 'a/../b', 'a\\b', 'C:/escape', 'a//b', '.git/config'):
            with self.subTest(name=name), self.assertRaises(RuntimeError): pkg.relative_name(name)
        for data in (b'\x7fELF', b'!<arch>\n', wasm_archive()[:-1], wasm_archive().replace(pkg.WASM_HEADER, b'\x7fELFxxxx')):
            with self.subTest(data=data), self.assertRaises(RuntimeError): pkg.check_wasm_archive(data, 'fixture.a')
        (self.root / 'dist/hts-1.21/linked').symlink_to(self.root / 'LICENSE')
        with self.assertRaisesRegex(RuntimeError, 'symlink'): pkg.check_build_identity('1.21')


if __name__ == '__main__':
    unittest.main()
