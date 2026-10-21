#!/usr/bin/env python3
"""Compile and run a real C consumer of each released HTSlib WASM SDK.

The native reference links the original HTSlib archive from build/native. The
WASM consumer links only release SDK headers/libraries and uses public APIs to
parse VCF/SAM, write BCF/BAM, build indexes and retrieve indexed records.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[1]
LOCK = json.loads((ROOT / 'tools/hts/versions.lock.json').read_text())
SOURCE = ROOT / 'tests/fixtures/hts-sdk.c'
VCF = '''##fileformat=VCFv4.2
##contig=<ID=chr1,length=1000>
##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\tS2
chr1\t100\t.\tA\tC\t50\tPASS\t.\tGT\t0/1\t1/1
chr1\t200\t.\tG\tT\t50\tPASS\t.\tGT\t0/0\t./.
chr1\t300\t.\tC\tA\t50\tPASS\t.\tGT\t1/0\t0/0
'''
SAM = '''@HD\tVN:1.6\tSO:coordinate
@SQ\tSN:chr1\tLN:1000
@SQ\tSN:chr2\tLN:1000
r1\t0\tchr1\t91\t60\t20M\t*\t0\t0\tAAAAAAAAAAAAAAAAAAAA\tIIIIIIIIIIIIIIIIIIII
r2\t0\tchr1\t201\t40\t10M\t*\t0\t0\tCCCCCCCCCC\tIIIIIIIIII
r3\t0\tchr2\t301\t30\t5M1I4M1D5M\t*\t0\t0\tGGGGGGGGGGGGGGG\tIIIIIIIIIIIIIII
r4\t4\t*\t0\t0\t*\t*\t0\t0\tTTTTT\tIIIII
'''
NODE_RUNNER = '''import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
const [modulePath, vcf, sam] = process.argv.slice(2);
const { default: createModule } = await import(pathToFileURL(modulePath));
const stdout = [], stderr = [];
let exitCode = 0;
const module = await createModule({
  noInitialRun: true,
  wasmBinary: fs.readFileSync(modulePath.replace(/\\.mjs$/, '.wasm')),
  print: text => stdout.push(text), printErr: text => stderr.push(text),
  onExit: code => { exitCode = code; }
});
module.FS.mkdir('/work');
module.FS.writeFile('/work/input.vcf', fs.readFileSync(vcf));
module.FS.writeFile('/work/input.sam', fs.readFileSync(sam));
try {
  const code = module.callMain(['/work/input.vcf', '/work/input.sam', '/work/output.bcf', '/work/output.bam']);
  if (typeof code === 'number') exitCode = code;
} catch (error) {
  if (error?.name === 'ExitStatus') exitCode = error.status;
  else throw error;
}
if (stderr.length) process.stderr.write(stderr.join('\\n') + '\\n');
process.stdout.write(stdout.join('\\n') + '\\n');
process.exitCode = exitCode;
'''


def sha(path: Path) -> str:
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def run(command: list[str], env: dict | None = None) -> str:
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f'Command failed: {shlex.join(command)}\n{result.stdout}{result.stderr}')
    return result.stdout


def check(version: str, emcc: str, node: str, cc: str, output_root: Path) -> dict:
    sdk = ROOT / f'dist/hts-{version}/htslib'
    manifest_path = sdk.parent / 'build-manifest.json'
    manifest = json.loads(manifest_path.read_text())
    # Exercise the exact release files whose provenance is recorded in the build.
    sdk_assets = {name: digest for name, digest in manifest['assets'].items() if name.startswith('htslib/')}
    if not sdk_assets:
        raise RuntimeError('Build manifest contains no HTSlib SDK assets')
    for name, digest in sdk_assets.items():
        if sha(sdk.parent / name) != digest:
            raise RuntimeError('SDK asset hash differs from build manifest: ' + name)
    native_archive = ROOT / f'build/native/hts-{version}/libhts.a'
    expected_native = manifest.get('nativeFiles', {}).get('native', {}).get('libhts.a')
    if expected_native is None or sha(native_archive) != expected_native:
        raise RuntimeError('Original-native HTSlib archive is missing or differs from build manifest')
    native_prefix = native_archive.parent
    for name in ('lib/libbz2.a', 'lib/liblzma.a', 'lib/libz.a'):
        expected = manifest.get('nativeFiles', {}).get('native', {}).get(name)
        if expected is None or sha(native_prefix / name) != expected:
            raise RuntimeError('Original-native dependency differs from build manifest: ' + name)
    work = output_root / version
    work.mkdir(parents=True, exist_ok=True)
    (work / 'tmp').mkdir(exist_ok=True)
    for name, contents in (('input.vcf', VCF), ('input.sam', SAM), ('run.mjs', NODE_RUNNER)):
        (work / name).write_text(contents)
    sdk_root = Path(os.environ.get('GENOMICS_EMSDK_DIR', ROOT / 'toolchain/emsdk')).resolve()
    env = {**os.environ, 'LC_ALL': 'C', 'TZ': 'UTC', 'TMPDIR': str(work / 'tmp')}
    if (sdk_root / '.emscripten').is_file():
        env['EM_CONFIG'] = str(sdk_root / '.emscripten')
    compiler = run([emcc, '--version'], env).splitlines()[0]
    if LOCK['emscripten'] not in compiler:
        raise RuntimeError(f'Expected Emscripten {LOCK["emscripten"]}, got {compiler}')
    common = ['-std=c11', '-O2', '-ffp-contract=off', '-D_FILE_OFFSET_BITS=64', '-D_LARGEFILE_SOURCE']
    native_libraries = [str(native_prefix / 'lib' / name) for name in ('libbz2.a', 'liblzma.a', 'libz.a')]
    wasm_libraries = [str(sdk / 'lib' / name) for name in ('libbz2.a', 'liblzma.a', 'libz.a')]
    run([cc, *common, '-I' + str(sdk / 'include'), str(SOURCE), str(native_archive),
         *native_libraries, '-lm', '-lpthread', '-o', str(work / 'consumer-native')], env)
    run([emcc, *common, '-I' + str(sdk / 'include'), str(SOURCE), str(sdk / 'libhts.a'),
         *wasm_libraries, '-lm', '-sMODULARIZE=1', '-sEXPORT_ES6=1', '-sINVOKE_RUN=0',
         '-sEXPORTED_RUNTIME_METHODS=FS,callMain', '-sENVIRONMENT=node', '-sALLOW_MEMORY_GROWTH=1',
         '-sEXIT_RUNTIME=1', '-sASSERTIONS=1', '-sSTACK_SIZE=8388608',
         '-Wl,--fatal-warnings', '-o', str(work / 'consumer.mjs')], env)
    native = json.loads(run([str(work / 'consumer-native'), str(work / 'input.vcf'), str(work / 'input.sam'),
                             str(work / 'output.bcf'), str(work / 'output.bam')], env))
    wasm = json.loads(run([node, str(work / 'run.mjs'), str(work / 'consumer.mjs'),
                           str(work / 'input.vcf'), str(work / 'input.sam')], env))
    if native != wasm or wasm['htslib'] != version:
        raise RuntimeError(f'HTSlib SDK consumer results differ: native={native}, WASM={wasm}')
    report = {'schema': 1, 'version': version, 'passed': True, 'compiler': compiler,
              'consumerSourceSha256': sha(SOURCE), 'testScriptSha256': sha(Path(__file__)),
              'buildManifestSha256': sha(manifest_path), 'sdkAssets': sdk_assets,
              'nativeArchiveSha256': sha(native_archive), 'result': wasm,
              'checks': ['public-headers-and-static-link', '64-bit-offset-and-position-ABI',
                         'VCF-genotypes-to-BCF', 'BCF-index-and-region-query',
                         'SAM-CIGAR-to-BAM', 'BAM-index-and-region-query', 'original-native-equality']}
    (work / 'report.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', choices=('all', *LOCK['versions']), default='all')
    parser.add_argument('--emcc', default=str(Path(os.environ.get('GENOMICS_EMSDK_DIR', ROOT / 'toolchain/emsdk')) / 'upstream/emscripten/emcc'))
    parser.add_argument('--node', default='node')
    parser.add_argument('--cc', default='cc')
    parser.add_argument('--output', type=Path, default=ROOT / 'build/hts-sdk-smoke')
    args = parser.parse_args()
    for version in LOCK['versions'] if args.version == 'all' else [args.version]:
        report = check(version, args.emcc, args.node, args.cc, args.output.resolve())
        print(json.dumps({'version': version, 'passed': report['passed'],
                          'report': str(args.output.resolve() / version / 'report.json')}))


if __name__ == '__main__':
    main()
