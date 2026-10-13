import test from 'node:test';
import assert from 'node:assert/strict';
import { Plink2Runner, preparePlink2Args } from '../runtime/plink2.mjs';

// Transport/lifetime fixture only. Scientific behavior is checked independently
// against unchanged native PLINK 2 by validate-plink2.mjs.
const runner = new Plink2Runner({ moduleUrl: new URL('./fixtures/mock-tool.mjs', import.meta.url) });

test('PLINK 2 bounds resource flags and preserves literal scientific arguments', () => {
  const args = ['--pfile', 'cohort;$(literal)', '--freq'];
  assert.deepEqual(preparePlink2Args(args), [...args, '--threads', '1', '--memory', '640', 'require']);
  assert.deepEqual(args, ['--pfile', 'cohort;$(literal)', '--freq']);
  for (const alias of ['--threads', '-threads', '--thread-num', '-num_threads']) {
    assert.deepEqual(preparePlink2Args([alias, '2', '--memory', 'require', '768']), ['--threads', '2', '--memory', '768', 'require']);
  }
  assert.deepEqual(preparePlink2Args(['--help', 'memory', 'threads']), ['--help', 'memory', 'threads']);
  assert.deepEqual(preparePlink2Args(['--version']), ['--version']);
  for (const option of ['--zst-decompress', '-zst-decompress', '--zd', '-zd']) {
    const args = [option, 'input.zst', 'output.txt'];
    assert.deepEqual(preparePlink2Args(args), args);
  }
  for (const args of [
    ['--threads', '0'], ['--threads', '3'], ['--threads=2'], ['--thread-num', '2', '--threads', '1'],
    ['--memory', '639'], ['--memory', '1025'], ['--memory', '-1'], ['--memory', '640.5'], ['--memory'],
    ['--memory', '640', '--memory', '640'], ['--memory=640'],
    ['--script', 'args.txt'], ['-script', 'args.txt'], ['--rerun'], ['-rerun', 'old.log'], ['--script=args.txt'],
  ]) assert.throws(() => preparePlink2Args(args), undefined, JSON.stringify(args));
});

test('PLINK 2 waits for pthread exit before collecting output files', async () => {
  const result = await runner.run(['async-exit'], { outputs: ['late.bin'], timeoutMs: 5000 });
  assert.equal(result.exitCode, 7);
  assert.deepEqual([...result.stdout], [42]);
  assert.deepEqual([...result.files[0].data], [1, 2, 3]);
  assert.deepEqual(result.missingOutputs, []);
});

test('PLINK 2 stops aborted or timed-out application workers and can run again', async () => {
  await assert.rejects(runner.run(['async-abort'], { timeoutMs: 5000 }), { name: 'RuntimeError' });
  await assert.rejects(runner.run(['async-hang'], { timeoutMs: 100 }), { name: 'TimeoutError' });
  await assert.rejects(runner.run([], { signal: AbortSignal.abort() }), { name: 'AbortError' });
  assert.equal((await runner.run(['async-exit'], { timeoutMs: 5000 })).exitCode, 7);
});

test('PLINK 2 validates paths and always waits for the real application exit', async () => {
  assert.equal(runner.waitForExit, true);
  assert.equal(new Plink2Runner({ waitForExit: false }).waitForExit, true);
  for (const baseUrl of ['https://example.test/dist/plink2', 'data:text/plain,code/']) {
    assert.throws(() => new Plink2Runner({ baseUrl }), TypeError);
  }
  await assert.rejects(runner.run(['--threads', 1]), TypeError);
  await assert.rejects(runner.run([], { files: [{ path: '../input.pgen', data: '' }] }), TypeError);
  await assert.rejects(runner.run([], { outputDirectories: ['/tmp'] }), TypeError);
});
