import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { KingRunner, KING_VERSION } from '../runtime/king.mjs';

test('KING selects the versioned local module and rejects ambiguous base URLs', () => {
  assert.equal(KING_VERSION, '2.3.2');
  assert.equal(new KingRunner().moduleUrl, new URL('../dist/king/2.3.2/king.mjs', import.meta.url).href);
  assert.equal(new KingRunner({ baseUrl: 'https://example.test/custom/' }).moduleUrl, 'https://example.test/custom/king.mjs');
  assert.equal(new KingRunner().waitForExit, false);
  for (const baseUrl of ['https://example.test/custom', 'data:text/plain,example/']) assert.throws(() => new KingRunner({ baseUrl }), TypeError);
});

// These checks use the actual compiled KING program when available. They are
// skipped in a source-only checkout; native scientific comparisons live in
// scripts/validate-king.mjs and must be run separately before claiming support.
const built = existsSync(new URL('../dist/king/2.3.2/king.wasm', import.meta.url));
const runner = new KingRunner();
test('actual KING prints its version through a fresh worker', { skip: !built }, async () => {
  const result = await runner.run([], { timeoutMs: 30000 });
  assert.match(new TextDecoder().decode(result.stdout), /^KING 2\.3\.2\b/);
  // The upstream CLI returns 1 after printing usage without an input file.
  assert.equal(result.exitCode, 1);
});

test('actual KING reports missing inputs and recovers in the next worker', { skip: !built }, async () => {
  const failed = await runner.run(['-b', 'absent.bed', '--kinship'], { timeoutMs: 30000 });
  assert.notEqual(failed.exitCode, 0);
  assert.equal(failed.files.length, 0);
  assert.match(new TextDecoder().decode(failed.stdout) + new TextDecoder().decode(failed.stderr), /absent|cannot|failed|not found|unable/i);
  const recovered = await runner.run([], { timeoutMs: 30000 });
  assert.equal(recovered.exitCode, 1);
  assert.match(new TextDecoder().decode(recovered.stdout), /^KING 2\.3\.2\b/);
});

test('actual KING can be cancelled or timed out without poisoning later runs', { skip: !built }, async () => {
  const controller = new AbortController();
  const pending = runner.run([], { signal: controller.signal, timeoutMs: 30000 });
  controller.abort();
  await assert.rejects(pending, { name: 'AbortError' });
  await assert.rejects(runner.run([], { timeoutMs: 1 }), { name: 'TimeoutError' });
  const recovered = await runner.run([], { timeoutMs: 30000 });
  assert.equal(recovered.exitCode, 1);
  assert.match(new TextDecoder().decode(recovered.stdout), /^KING 2\.3\.2\b/);
});

test('actual KING rejects unsupported CPU and external plotting options, then completes an analysis', { skip: !built }, async () => {
  const files = await Promise.all(['cohort.bed', 'cohort.bim', 'cohort.fam'].map(async path => ({
    path, data: await readFile(new URL(`../datasets/king/${path}`, import.meta.url)),
  })));
  const marker = '/work/results/.directory';
  files.push({ path: marker, data: '' });
  const options = { files, outputDirectories: ['results'], timeoutMs: 30000 };
  const cases = [
    { args: ['--cpus', '2'], diagnostic: /KING browser profile supports --cpus 1 only\./ },
    { args: ['--cpus', '-1'], diagnostic: /KING browser profile supports --cpus 1 only\./ },
    { args: ['--rplot'], diagnostic: /KING browser profile cannot launch R or ps2pdf/ },
    { args: ['--pngplot'], diagnostic: /KING browser profile cannot launch R or ps2pdf/ },
    { args: ['--rpath', 'x'], diagnostic: /KING browser profile cannot launch R or ps2pdf/ },
  ];
  for (const { args, diagnostic } of cases) {
    const result = await runner.run(['-b', 'cohort.bed', '--kinship', '--prefix', 'results/guard', ...args], options);
    assert.notEqual(result.exitCode, 0, `${args.join(' ')} must fail`);
    assert.match(new TextDecoder().decode(result.stdout) + new TextDecoder().decode(result.stderr), diagnostic);
    assert.deepEqual(result.missingOutputs, []);
    // Recursively collecting the whole output folder proves the rejected
    // command did not leave a partial analysis result hidden by a file list.
    assert.deepEqual(result.files.map(file => file.path), [marker]);
    assert.equal(result.files[0].data.length, 0);
  }
  const recovered = await runner.run(['-b', 'cohort.bed', '--kinship', '--cpus', '1', '--prefix', 'results/recovered'], options);
  assert.equal(recovered.exitCode, 0);
  assert.deepEqual(recovered.missingOutputs, []);
  for (const suffix of ['.kin', '.kin0']) {
    const file = recovered.files.find(file => file.path === `/work/results/recovered${suffix}`);
    assert.ok(file, `Successful recovery produced ${suffix}`);
    const rows = new TextDecoder().decode(file.data).trim().split(/\r?\n/);
    assert.match(rows[0], /Kinship/);
    assert.ok(rows.length > 1, `Successful recovery populated ${suffix}`);
  }
});
