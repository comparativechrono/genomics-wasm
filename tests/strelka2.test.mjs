import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { Strelka2Runner } from '../runtime/strelka2.mjs';
import { runSomatic, parseArguments, parseConfig, parseFai, parseRegion, segmentGroups, mergeDepth, pythonFixed, rewriteHeader } from '../runtime/strelka2-somatic.mjs';

const encode = text => new TextEncoder().encode(text);
const decode = data => new TextDecoder().decode(data);
const args = ['--normalBam', 'normal.bam', '--tumorBam', 'tumor.bam', '--referenceFasta', 'ref.fa', '--runDir', 'out', '--snvScoringModelFile', 'snv.json', '--indelScoringModelFile', 'indel.json'];
const files = ['normal.bam', 'normal.bam.bai', 'tumor.bam', 'tumor.bam.bai', 'ref.fa', 'snv.json', 'indel.json'].map(path => ({ path, data: encode('opaque unit-test input') }));
files.push({ path: 'ref.fa.fai', data: encode('chrA\t100\t6\t100\t101\nchrB\t200\t113\t200\t201\n') });
const header = '@HD\tVN:1.6\n@SQ\tSN:chrA\tLN:100\n@SQ\tSN:chrB\tLN:200\n';
const result = (stdout = '', exitCode = 0, outputFiles = []) => ({ exitCode, stdout: encode(stdout), stderr: encode(''), files: outputFiles, missingOutputs: [] });

// Mock callbacks below exercise only coordination failure paths. They neither
// calculate biological output nor replace independent native/WASM comparisons.
test('high-level help and version need no compiled modules; unsupported options fail visibly', async () => {
  const runner = new Strelka2Runner({ baseUrl: new URL('./absent/', import.meta.url) });
  const help = await runner.run(['somatic', '--help']);
  assert.equal(help.exitCode, 0);
  assert.match(decode(help.stdout), /--indelCandidates/);
  assert.equal(help.workflow.stages.length, 0);
  assert.equal(decode((await runner.run(['somatic', '--version'])).stdout), '2.9.10\n');
  for (const extra of [['--germline'], ['--jobs', '2'], ['--scanSizeMb', '0'], ['--scanSizeMb', '1.5']]) assert.equal((await runner.run(['somatic', ...args, ...extra], { files })).exitCode, 2);
});

test('argument parser rejects duplicate samples and preserves repeatable external variant inputs', () => {
  assert.throws(() => parseArguments([...args, '--tumourBam=another.bam']), /At most one/);
  assert.throws(() => parseArguments(args.map(x => x === 'tumor.bam' ? 'normal.bam' : x)), /must differ/);
  const parsed = parseArguments([...args, '--indelCandidates=one candidate.vcf.gz', '--indelCandidates', 'two.vcf.gz', '--forcedGT', 'forced.vcf.gz', '--noiseVcf', 'noise.vcf.gz', '--region', 'chrA:3-7+chrB', '--targeted']);
  assert.deepEqual(parsed.indelCandidates, ['/work/one candidate.vcf.gz', '/work/two.vcf.gz']);
  assert.deepEqual(parsed.regions, ['chrA:3-7+chrB']);
  assert.equal(parsed.exome, true);
  assert.equal(parsed.parameters.ssnvNoise, '0.0000000005');
  assert.equal(parsed.parameters.sindelPrior, '0.000001');
});

test('INI overrides retain numeric spellings and reject silent unsupported behavior', () => {
  const config = parseConfig('[StrelkaSomatic]\nssnvPrior = 0.000100\nextraVariantCallerArguments = --min-base-qscore 25 ; detail\nisWriteRealignedBam = off\n');
  assert.equal(config.ssnvPrior, '0.000100');
  assert.equal(config.extraVariantCallerArguments, '--min-base-qscore 25');
  assert.throws(() => parseConfig('[StrelkaSomatic]\nunknownAlgorithmToggle = 1\n'), /Unsupported/);
  assert.throws(() => parseConfig('[StrelkaSomatic]\nssnvPrior = %(value)s\n'), /interpolation/);
  assert.throws(() => parseConfig('[StrelkaSomatic]\nconstructor = injected\n'), /Unsupported/);
});

test('INI DEFAULT inheritance agrees with an independent ConfigParser oracle', () => {
  const python2 = fileURLToPath(new URL('../build/strelka2-python/bin/python2.7', import.meta.url));
  const interpreter = existsSync(python2) ? python2 : 'python3';
  const configs = [
    '[DEFAULT]\ndepthFilterMultiple = 1.0\n[StrelkaSomatic]\n',
    '[DEFAULT]\ndepthFilterMultiple = 1.0\n[StrelkaSomatic]\ndepthFilterMultiple = 2.0\n',
    '[StrelkaSomatic]\ndepthFilterMultiple = 2.0\n[DEFAULT]\ndepthFilterMultiple = 1.0\n',
    '[DEFAULT]\ndepthFilterMultiple = 1.0\n',
  ];
  const oracle = spawnSync(interpreter, ['-B', '-c', `import sys,json\ntry:\n import ConfigParser as configparser\n from StringIO import StringIO\nexcept ImportError:\n import configparser\n from io import StringIO\nanswers=[]\nfor text in json.load(sys.stdin):\n p=configparser.RawConfigParser(); p.optionxform=str; p.readfp(StringIO(text))\n answers.append(dict(p.items('StrelkaSomatic')) if p.has_section('StrelkaSomatic') else {})\nprint(json.dumps(answers))`], { input: JSON.stringify(configs), encoding: 'utf8', env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' } });
  assert.equal(oracle.status, 0, oracle.stderr);
  assert.deepEqual(configs.map(parseConfig), JSON.parse(oracle.stdout));
});

test('region subdivision covers requested bases exactly and retains upstream segment identities', () => {
  const chromosomes = parseFai('chrA\t2000003\t6\t100\t101\nchr:B\t9\t2000009\t9\t10\n');
  const groups = segmentGroups(chromosomes, [], 1);
  assert.deepEqual(groups.flat().map(segment => segment.bamRegion), ['chrA:1-666668', 'chrA:666669-1333336', 'chrA:1333337-2000003', 'chr:B:1-9']);
  assert.equal(groups.flat().reduce((sum, segment) => sum + segment.size, 0), 2000012);
  const region = parseRegion('chr:B:2-8', chromosomes);
  const selected = segmentGroups(chromosomes, [region], 12, new Set(['chr:B']));
  assert.equal(selected[0][0].id, 'chromId_001_chr_B-2-8_0000');
  assert.equal(selected[0][0].bamRegion, 'chr:B:2-8');
  assert.deepEqual(segmentGroups(chromosomes, [region], 12, new Set(['chrA'])), []);
  assert.throws(() => parseRegion('chrA:9-2', chromosomes), /Invalid region/);
  assert.throws(() => parseFai('chrA\t100\t6\t100\t101\nchrA\t100\t6\t100\t101\n'), /duplicate/);
  const unicode = parseFai('chrα\t10\t6\t10\t11\n');
  assert.equal(segmentGroups(unicode, [], 12)[0][0].id, 'chromId_000_chr___0000');
});

test('depth merge matches independent Python binary64 fixed formatting including half ties', () => {
  const values = [0, -0, 0.0625, 0.1875, -0.0625, 0.0015, Number.MIN_VALUE, Number.MAX_VALUE, 987654321.1234];
  const view = new DataView(new ArrayBuffer(8));
  const hex = values.map(value => { view.setFloat64(0, value); return view.getBigUint64(0).toString(16).padStart(16, '0'); });
  const oracle = spawnSync('python3', ['-c', 'import json,sys,struct; print(json.dumps(["%.3f" % struct.unpack(">d", bytes.fromhex(x))[0] for x in json.load(sys.stdin)]))'], { input: JSON.stringify(hex), encoding: 'utf8' });
  assert.equal(oracle.status, 0, oracle.stderr);
  assert.deepEqual(values.map(value => pythonFixed(value, 3)), JSON.parse(oracle.stdout));
  assert.equal(mergeDepth(['chrB\t0.0625\nchrA\t10.25\n', 'chrA\t0.5\nchrB\t0\n']), 'chrB\t0.062\nchrA\t10.750\n');
  assert.equal(mergeDepth(['chrA\t-0.0\n']), 'chrA\t-0.000\n');
  assert.throws(() => mergeDepth(['chrA\t1\nchrA\t2\n']), /Duplicate/);
  assert.throws(() => mergeDepth(['chrA\t1\n', 'chrB\t2\n']), /Chromosome sets differ/);
  assert.throws(() => mergeDepth(['chrA\tNaN\n']), /Non-finite/);
});

test('header replacement preserves every non-command byte and handles absent and repeated command metadata', () => {
  const record = '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\nchrA\t1\t.\tA\tC\t20\tPASS\tX=1\n';
  assert.equal(decode(rewriteHeader(encode(`##fileformat=VCFv4.1\n${record}`), 'new command')), `##fileformat=VCFv4.1\n##cmdline=new command\n${record}`);
  assert.equal(decode(rewriteHeader(encode(`##cmdline=old\n##other=unchanged\n##cmdline=older\n${record}`), 'new command')), `##cmdline=new command\n##other=unchanged\n##cmdline=new command\n${record}`);
});

test('invalid inputs, existing run directories and unsupported realigned BAM output launch no helper', async () => {
  const execute = () => assert.fail('invalid input must not execute native code');
  for (const given of [files.filter(file => file.path !== 'ref.fa.fai'), files.filter(file => file.path !== 'normal.bam.bai'), [...files, { path: 'out/already.txt', data: '' }]]) assert.notEqual((await runSomatic(args, { files: given }, execute)).exitCode, 0);
  const config = { path: 'settings.ini', data: '[StrelkaSomatic]\nisWriteRealignedBam = 1\n' };
  const rejected = await runSomatic([...args, '--config', 'settings.ini'], { files: [...files, config] }, execute);
  assert.equal(rejected.exitCode, 2);
  assert.match(decode(rejected.stderr), /not implemented/);
  const variants = { path: 'candidates.vcf.gz', data: 'opaque' };
  assert.notEqual((await runSomatic([...args, '--indelCandidates', variants.path], { files: [...files, variants] }, execute)).exitCode, 0);
});

test('missing configure inputs return upstream usage status 2 without starting a helper', async () => {
  const execute = () => assert.fail('missing configure input must not execute native code');
  for (const missing of ['tumor.bam', 'normal.bam', 'ref.fa', 'ref.fa.fai', 'snv.json']) {
    const failure = await runSomatic(args, { files: files.filter(file => file.path !== missing) }, execute);
    assert.equal(failure.exitCode, 2, missing);
    assert.match(decode(failure.stderr), /Input file does not exist/);
    assert.equal(failure.workflow.stages.length, 0);
  }
});

test('inconsistent BAM chromosome order fails at configuration before any variant call', async () => {
  let count = 0;
  const failure = await runSomatic(args, { files }, async tool => {
    assert.equal(tool, 'htsfile');
    count++;
    return result(count === 1 ? header : '@SQ\tSN:chrB\tLN:200\n@SQ\tSN:chrA\tLN:100\n');
  });
  assert.equal(failure.exitCode, 2);
  assert.equal(count, 2);
  assert.match(decode(failure.stderr), /order differs/);
});

test('somatic configure locks the complete reference dictionary, including with --region', async () => {
  // BamSetChecker.check explicitly enables isReferenceLocked=True upstream.
  for (const bamHeader of ['@SQ\tSN:chrA\tLN:100\n', '@SQ\tSN:chrA\tLN:100\n@SQ\tSN:unknown\tLN:200\n']) {
    let count = 0;
    const failure = await runSomatic([...args, '--region', 'chrA'], { files }, async tool => {
      assert.equal(tool, 'htsfile'); count++; return result(bamHeader);
    });
    assert.equal(failure.exitCode, 2);
    assert.equal(count, 1);
    assert.match(decode(failure.stderr), /same chromosome names and lengths/);
  }
});

test('failed child retains selected partial outputs and hashes actual supplied model bytes', async () => {
  let calls = 0;
  const partial = 'out/partial.log';
  const failure = await runSomatic([...args, '--exome'], { files, outputs: [partial, 'out/results/variants/somatic.snvs.vcf.gz'], outputDirectories: ['out'] }, async (tool, command, options) => {
    calls++;
    if (tool === 'htsfile') return result(header);
    assert.equal(tool, 'strelka2');
    assert.ok(options.files.some(file => file.path === '/work/out/results/variants/.strelka2-directory'));
    assert.ok(command.includes('--somatic-snv-scoring-model-file'));
    return result('', 9, [{ path: '/work/' + partial, data: encode('actual failed-child bytes') }]);
  });
  assert.equal(calls, 3);
  assert.equal(failure.exitCode, 1);
  assert.deepEqual(failure.missingOutputs, ['/work/out/results/variants/somatic.snvs.vcf.gz']);
  assert.equal(failure.files.length, 1);
  assert.match(failure.workflow.models['/work/snv.json'], /^[a-f0-9]{64}$/);
  assert.equal(failure.workflow.stages.at(-1).exitCode, 9);
  assert.equal(failure.workflow.stages.at(-1).inputs['/work/snv.json'], failure.workflow.models['/work/snv.json']);
  assert.match(failure.workflow.stages.at(-1).outputHashes['/work/' + partial], /^[a-f0-9]{64}$/);
});

test('a successful child missing any promised output cannot pass the workflow', async () => {
  let calls = 0;
  const failure = await runSomatic([...args, '--exome'], { files }, async tool => { calls++; return result(tool === 'htsfile' ? header : ''); });
  assert.equal(calls, 3);
  assert.equal(failure.exitCode, 1);
  assert.equal(failure.workflow.stages.at(-1).missingOutputs.length, 3);
});

test('abort and overall deadline reach active helpers and do not contaminate a later run', async () => {
  const controller = new AbortController();
  const pending = runSomatic(args, { files, signal: controller.signal }, async (_tool, _command, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new Error('cancelled helper')), { once: true });
    queueMicrotask(() => controller.abort());
  }));
  await assert.rejects(pending, { name: 'AbortError' });
  let calls = 0;
  await assert.rejects(runSomatic(args, { files, timeoutMs: 180 }, async (_tool, _command, options) => {
    calls++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => resolve(result(header)), 110);
      options.signal.addEventListener('abort', () => { clearTimeout(timer); reject(new Error('cancelled helper')); }, { once: true });
    });
  }), { name: 'TimeoutError' });
  assert.equal(calls, 2);
  const later = await runSomatic(['--help'], {}, () => assert.fail('help must not execute'));
  assert.equal(later.exitCode, 0);
});

test('inputs are snapshotted and runtime traps remain explicit failures', async () => {
  const supplied = files.map(file => ({ ...file, data: Buffer.from(file.data) }));
  const original = supplied[0].data.slice();
  await assert.rejects(runSomatic(args, { files: supplied }, async (_tool, _command, options) => {
    options.files[0].data[0] ^= 255;
    throw new WebAssembly.RuntimeError('native trap');
  }), { name: 'RuntimeError' });
  assert.deepEqual(supplied[0].data, original);
  await assert.rejects(runSomatic(args, { files, signal: AbortSignal.abort() }, () => assert.fail('cancelled')), { name: 'AbortError' });
});
