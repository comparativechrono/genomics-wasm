import test from 'node:test';
import assert from 'node:assert/strict';
import { BustoolsRunner } from '../runtime/bustools.mjs';

// Tests of transport and lifetime only. Real BUS operations are independently
// compared with the unchanged upstream executable by validate-bustools.mjs.
const runner = new BustoolsRunner({ moduleUrl: new URL('./fixtures/mock-tool.mjs', import.meta.url) });

test('bustools passes literal CLI arguments and starts every invocation with fresh state', async () => {
  const args = ['sort', '-o', 'sample output.bus', 'input;$(literal).bus'];
  const result = await runner.run(['args', ...args]);
  assert.deepEqual(JSON.parse(new TextDecoder().decode(result.stdout)), args);
  const results = await Promise.all([runner.run(['count']), runner.run(['count'])]);
  for (const value of results) assert.deepEqual([...value.stdout], [1]);
});

test('bustools preserves binary streams, mounted files and files carried to another invocation', async () => {
  const data = Uint8Array.from([66, 85, 83, 0, 0, 128, 255, 10]);
  const original = data.slice();
  const options = { files: [{ path: 'nested/input.bin', data }], stdin: data, outputs: ['nested/output.bin'] };
  const first = await runner.run([], options);
  assert.deepEqual(first.stdout, original);
  assert.deepEqual(first.files[0].data, original);
  assert.deepEqual(data, original);
  const second = await runner.run([], {
    files: [{ path: 'nested/input.bin', data: first.files[0].data }],
    stdin: first.stdout, outputs: ['nested/output.bin', 'missing.bin'],
  });
  assert.deepEqual(second.stdout, original);
  assert.deepEqual(second.files[0].data, original);
  assert.deepEqual(second.missingOutputs, ['/work/missing.bin']);
});

test('bustools reports native failures and termination without contaminating later runs', async () => {
  const failed = await runner.run(['exit']);
  assert.equal(failed.exitCode, 7);
  assert.deepEqual([...failed.stderr], [255]);
  await assert.rejects(runner.run(['hang'], { timeoutMs: 100 }), { name: 'TimeoutError' });
  await assert.rejects(runner.run([], { signal: AbortSignal.abort() }), { name: 'AbortError' });
  await assert.rejects(runner.run(['trap']), { name: 'RuntimeError' });
  assert.deepEqual([...(await runner.run(['count'])).stdout], [1]);
});

test('bustools rejects unsafe workspace paths and invalid configuration before execution', async () => {
  for (const baseUrl of ['https://example.test/dist/bustools', 'data:text/plain,code/']) {
    assert.throws(() => new BustoolsRunner({ baseUrl }), TypeError);
  }
  await assert.rejects(runner.run(['sort', 3]), TypeError);
  await assert.rejects(runner.run([], { files: [{ path: '../input.bus', data: '' }] }), TypeError);
  await assert.rejects(runner.run([], { outputDirectories: ['/tmp'] }), TypeError);
});
