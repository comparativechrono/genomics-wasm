import test from 'node:test';
import assert from 'node:assert/strict';
import { ToolRunner } from '../runtime/index.mjs';

const runner = new ToolRunner({ moduleUrl: new URL('./fixtures/mock-tool.mjs', import.meta.url) });

test('stdin, stdout, stderr and files preserve every byte, including NUL and no trailing newline', async () => {
  const data = Uint8Array.from([0, 9, 10, 13, 127, 128, 255, 65]);
  const result = await runner.run([], {
    stdin: data,
    files: [{ path: 'nested/input.bin', data }],
    outputs: ['nested/output.bin', 'absent.txt'],
  });
  assert.equal(result.exitCode, 0);
  assert.deepEqual(result.stdout, data);
  assert.deepEqual(result.stderr, Uint8Array.from([0, 128, 255]));
  assert.deepEqual(result.files, [{ path: '/work/nested/output.bin', data }]);
  assert.deepEqual(result.missingOutputs, ['/work/absent.txt']);
  assert.equal(data.byteLength, 8, 'caller buffer was not detached');
});

test('byte collection spans several chunks without loss', async () => {
  const result = await runner.run(['large']);
  assert.equal(result.stdout.length, 150001);
  assert.ok(result.stdout.every((byte, index) => byte === index % 256));
});

test('collects complete directory outputs including nested and hidden files without duplicates', async () => {
  const result = await runner.run(['directory-output'], {
    outputs: ['index/nested/data.bin'],
    outputDirectories: ['index', 'index/nested', 'absent'],
  });
  assert.equal(result.exitCode, 0);
  assert.deepEqual(result.files, [
    { path: '/work/index/nested/data.bin', data: Uint8Array.from([0, 128, 255]) },
    { path: '/work/index/.metadata', data: Uint8Array.from([123, 125]) },
  ]);
  assert.deepEqual(result.missingOutputs, ['/work/absent']);
});

test('directory collection rejects symlinks and paths that are ordinary files', async () => {
  await assert.rejects(runner.run(['directory-output', 'link'], { outputDirectories: ['index'] }), /non-regular file/);
  await assert.rejects(runner.run(['directory-output'], { outputDirectories: ['index/.metadata'] }), /not a directory/);
});

test('every command has fresh global state, including concurrent commands', async () => {
  const results = await Promise.all([runner.run(['count']), runner.run(['count']), runner.run(['count'])]);
  assert.deepEqual(results.map(result => [...result.stdout]), [[1], [1], [1]]);
});

test('arguments are passed literally without shell interpretation', async () => {
  const args = ['with space', '$(touch unsafe)', '>output', '*.fq', 'a;b', 'é'];
  const result = await runner.run(['args', ...args]);
  assert.deepEqual(JSON.parse(new TextDecoder().decode(result.stdout)), args);
});

test('nonzero program exit returns status and diagnostics', async () => {
  const result = await runner.run(['exit']);
  assert.equal(result.exitCode, 7);
  assert.deepEqual([...result.stderr], [255]);
});

test('proxied main waits for real exit, final output files and status', async () => {
  const asynchronous = new ToolRunner({ moduleUrl: new URL('./fixtures/mock-tool.mjs', import.meta.url), waitForExit: true });
  const result = await asynchronous.run(['async-exit'], { outputs: ['late.bin'], timeoutMs: 5000 });
  assert.equal(result.exitCode, 7);
  assert.deepEqual([...result.stdout], [42]);
  assert.equal(new TextDecoder().decode(result.stderr), 'Runtime diagnostic\n');
  assert.deepEqual([...result.files[0].data], [1, 2, 3]);
  await assert.rejects(asynchronous.run(['async-abort'], { timeoutMs: 5000 }), /Fixture abort/);
  await assert.rejects(asynchronous.run(['async-hang'], { timeoutMs: 150 }), { name: 'TimeoutError' });
  assert.throws(() => new ToolRunner({ moduleUrl: new URL('./fixtures/mock-tool.mjs', import.meta.url), waitForExit: 'yes' }), TypeError);
});

test('runtime traps reject instead of reporting a successful exit', async () => {
  await assert.rejects(runner.run(['trap']), { name: 'RuntimeError', message: 'Fixture trap' });
});

test('timeouts terminate a blocked synchronous worker and subsequent runs still work', async () => {
  await assert.rejects(runner.run(['hang'], { timeoutMs: 150 }), { name: 'TimeoutError' });
  assert.deepEqual([...(await runner.run(['count'])).stdout], [1]);
});

test('cancellation terminates a running command', async () => {
  const controller = new AbortController();
  const command = runner.run(['hang'], { signal: controller.signal });
  setTimeout(() => controller.abort(), 100);
  await assert.rejects(command, { name: 'AbortError' });
});

test('already-cancelled signals reject without starting work', async () => {
  await assert.rejects(runner.run(['hang'], { signal: AbortSignal.abort() }), { name: 'AbortError' });
});

test('reject invalid arguments and filesystem paths before execution', async () => {
  for (const path of ['../escape', '/tmp/file', 'a/../b', '.', '', '/work', 'a//b', 'a\u0000b']) {
    await assert.rejects(runner.run([], { outputs: [path] }), TypeError);
    await assert.rejects(runner.run([], { outputDirectories: [path] }), TypeError);
  }
  await assert.rejects(runner.run('mem reference reads'), TypeError);
  await assert.rejects(runner.run(['a\u0000b']), TypeError);
  await assert.rejects(runner.run([], { timeoutMs: -1 }), TypeError);
  await assert.rejects(runner.run([], { outputDirectories: 'index' }), TypeError);
  await assert.rejects(runner.run([], { files: [{ path: 'a', data: '1' }, { path: '/work/a', data: '2' }] }), TypeError);
});

test('failure to load a module rejects the command', async () => {
  const missing = new ToolRunner({ moduleUrl: new URL('./fixtures/not-present.mjs', import.meta.url) });
  await assert.rejects(missing.run([]), /Cannot find module/);
});
