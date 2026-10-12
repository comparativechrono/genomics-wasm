import test from 'node:test';
import assert from 'node:assert/strict';
import { BrackenRunner, runWorkflow } from '../runtime/bracken.mjs';
const encode = text => new TextEncoder().encode(text);
const decode = data => new TextDecoder().decode(data);
const estimate = ['-d', 'db', '-i', 'sample.report', '-o', 'out.tsv'];
const estimateFiles = [ { path: 'sample.report', data: 'opaque input' },
  { path: 'db/database100mers.kmer_distrib', data: 'opaque distribution' } ];
const buildFiles = ['db/hash.k2d', 'db/taxonomy/nodes.dmp', 'db/seqid2taxid.map', 'db/library/a.fna']
  .map(path => ({ path, data: 'opaque input' }));
const result = (files = [], exitCode = 0, stdout = '') => ({ files, exitCode, stdout: encode(stdout), stderr: encode(''), missingOutputs: [] });
// These mocks exercise coordination only; biological equivalence uses actual
// upstream native executables and WASM on independently sourced datasets.

test('help/version work offline and unsupported options launch no helpers', async () => {
  const runner = new BrackenRunner({ baseUrl: new URL('./missing/', import.meta.url) });
  assert.match(decode((await runner.run(['bracken', '-h'])).stdout), /Usage: bracken/);
  assert.equal(decode((await runner.run(['bracken', '-v'])).stdout), 'Bracken v3.0.1\n');
  assert.equal(decode((await runner.run(['build', '-v'])).stdout), 'bracken-build.sh v3.0.1\n');
  assert.equal((await runner.run(['build', '-d', 'db', '-t', '2'])).exitCode, 2);
  assert.equal((await runner.run(['bracken', '--unknown'])).exitCode, 2);
  assert.equal((await runner.run(['bracken', '-d'])).exitCode, 2);
});

test('a failed Python stage returns its status and partial output, preserving caller bytes', async () => {
  const original = Buffer.from('original');
  const response = await runWorkflow(false, estimate, { files: [...estimateFiles, { path: 'keep', data: original }], outputs: ['partial', 'keep', 'absent'] },
    async (_tool, _args, options) => {
      options.files.find(f => f.path === '/work/keep').data[0] = 33;
      return result([{ path: '/work/partial', data: encode('partial record') }], 7);
    });
  assert.equal(response.exitCode, 7);
  assert.equal(response.workflow.stages.length, 1);
  assert.equal(decode(response.files.find(f => f.path === '/work/partial').data), 'partial record');
  assert.deepEqual(response.missingOutputs, ['/work/absent']);
  assert.equal(original.toString(), 'original');
});

test('default redistributed report follows upstream rank names and splitext behavior', async () => {
  for (const [level, report] of [['S', 'sample_bracken_species.report'], ['G', 'sample_bracken_genuses.report'], ['D', 'sample_bracken_domains.report'], ['S1', 'sample_bracken_S1.report']]) {
    let required;
    await runWorkflow(false, [...estimate, '-l', level], { files: estimateFiles }, async (_tool, _args, options) => {
      required = options.outputs; return result();
    });
    assert.ok(required.includes('/work/' + report));
  }
});

test('build uses cached whole-reference assignments and carries every intermediate with hash edges', async () => {
  const calls = [];
  const response = await runWorkflow(true, ['-d', 'db', '-l', '151'], { files: [...buildFiles, { path: 'db/database.kraken.tsv', data: 'cached classifications' }], outputs: ['db/database.kraken', 'db/database151mers.kraken', 'db/database151mers.kmer_distrib'] }, async (tool, args, options) => {
    calls.push(tool);
    assert.equal(decode(options.files.find(f => f.path === '/work/db/database.kraken').data), 'cached classifications');
    const output = tool === 'kmer2read_distr' ? 'database151mers.kraken' : 'database151mers.kmer_distrib';
    return result([{ path: '/work/db/' + output, data: encode(output) }]);
  });
  assert.deepEqual(calls, ['kmer2read_distr', 'generate_kmer_distribution.py']);
  assert.equal(response.exitCode, 0);
  assert.equal(response.files.length, 3);
  const [first, second] = response.workflow.stages;
  assert.equal(second.inputs['/work/db/database151mers.kraken'], first.outputHashes['/work/db/database151mers.kraken']);
});

test('a classification failure stops build and retains redirected partial stdout', async () => {
  const response = await runWorkflow(true, ['-d', 'db'], { files: buildFiles, outputs: ['db/database.kraken'] }, async tool => {
    assert.equal(tool, 'kraken2'); return result([], 9, 'partial classifications');
  });
  assert.equal(response.exitCode, 9);
  assert.equal(response.workflow.stages.length, 1);
  assert.equal(decode(response.files[0].data), 'partial classifications');
});

test('missing required outputs cannot become successful workflows', async () => {
  await assert.rejects(runWorkflow(false, estimate, { files: estimateFiles }, async () => ({ ...result(), missingOutputs: ['/work/out.tsv'] })), /omitted required outputs/);
});

test('an overall deadline cancels active execution; pre-aborted requests launch nothing', async () => {
  const controller = new AbortController(); controller.abort();
  await assert.rejects(runWorkflow(false, estimate, { files: estimateFiles, signal: controller.signal }, () => assert.fail('must not run')), { name: 'AbortError' });
  await assert.rejects(runWorkflow(false, estimate, { files: estimateFiles, timeoutMs: 20 }, async (_tool, _args, options) => new Promise((resolve, reject) => {
    options.signal.addEventListener('abort', () => { const error = new Error('cancelled'); error.name = 'AbortError'; reject(error); }, { once: true });
  })), { name: 'TimeoutError' });
});

test('duplicate or traversing input paths fail before execution', async () => {
  await assert.rejects(runWorkflow(false, estimate, { files: [{ path: '../bad', data: '' }] }, () => assert.fail('must not run')), /inside \/work/);
  await assert.rejects(runWorkflow(false, estimate, { files: [{ path: 'a', data: '' }, { path: '/work/a', data: '' }] }, () => assert.fail('must not run')), /unique/);
});

test('flat database uploads support -d . without relaxing file path confinement', async () => {
  let inputPaths;
  const response = await runWorkflow(false, ['-d', '.', '-i', 'sample.report', '-o', 'out.tsv'], {
    files: [{ path: 'sample.report', data: 'opaque report' }, { path: 'database100mers.kmer_distrib', data: 'opaque distribution' }],
  }, async (_tool, args) => { inputPaths = args; return result([], 3); });
  assert.equal(response.exitCode, 3);
  assert.equal(inputPaths[inputPaths.indexOf('-k') + 1], '/work/database100mers.kmer_distrib');
  await assert.rejects(runWorkflow(false, ['-d', '../outside', '-i', 'sample.report', '-o', 'out.tsv'], { files: estimateFiles }, () => assert.fail('must not run')), /inside \/work/);
});
