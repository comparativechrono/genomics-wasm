import test from 'node:test';
import assert from 'node:assert/strict';
import { HmmerRunner } from '../runtime/hmmer.mjs';

const runner = () => new HmmerRunner({ baseUrl: new URL('./fixtures/', import.meta.url) });

test('HMMER program routing preserves argument bytes and fresh invocation state', async () => {
  const tool = runner();
  const args = ['--name', 'a name with spaces', '-'];
  const result = await tool.run(['hmmsearch', 'args', ...args]);
  assert.deepEqual(JSON.parse(new TextDecoder().decode(result.stdout)), args);
  for (let index = 0; index < 2; index++) assert.deepEqual([...(await tool.run(['hmmsearch', 'count'])).stdout], [1]);
});

test('HMMER module selection cannot load arbitrary paths or unbundled services', async () => {
  for (const program of ['../mock-tool', '/hmmsearch', 'hmmpgmd', '', 'hmmsearch.mjs']) {
    await assert.rejects(runner().run([program]), RangeError);
  }
  await assert.rejects(runner().run(['hmmsearch', '\0']), TypeError);
});

test('HMMER forwards original exit status and termination controls', async () => {
  const result = await runner().run(['hmmsearch', 'exit']);
  assert.equal(result.exitCode, 7);
  assert.deepEqual([...result.stderr], [255]);
  await assert.rejects(runner().run(['hmmsearch', 'count'], { signal: AbortSignal.abort() }), { name: 'AbortError' });
  await assert.rejects(runner().run(['hmmsearch', 'hang'], { timeoutMs: 100 }), { name: 'TimeoutError' });
});

test('HMMER output collection preserves arbitrary file bytes', async () => {
  const bytes = Uint8Array.from([0, 10, 128, 255]);
  const result = await runner().run(['hmmsearch'], {
    stdin: bytes, files: [{ path: 'nested/input.bin', data: bytes }], outputs: ['nested/output.bin', 'absent'],
  });
  assert.deepEqual(result.stdout, bytes);
  assert.deepEqual(result.files[0].data, bytes);
  assert.deepEqual(result.missingOutputs, ['/work/absent']);
});
