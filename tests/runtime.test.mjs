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
  }
  await assert.rejects(runner.run('mem reference reads'), TypeError);
  await assert.rejects(runner.run(['a\u0000b']), TypeError);
  await assert.rejects(runner.run([], { timeoutMs: -1 }), TypeError);
  await assert.rejects(runner.run([], { files: [{ path: 'a', data: '1' }, { path: '/work/a', data: '2' }] }), TypeError);
});

test('failure to load a module rejects the command', async () => {
  const missing = new ToolRunner({ moduleUrl: new URL('./fixtures/not-present.mjs', import.meta.url) });
  await assert.rejects(missing.run([]), /Cannot find module/);
});
