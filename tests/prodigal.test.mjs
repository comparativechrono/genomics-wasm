import test from 'node:test';
import assert from 'node:assert/strict';
import { ProdigalRunner } from '../runtime/prodigal.mjs';

const runner = () => new ProdigalRunner({ moduleUrl: new URL('./fixtures/prodigal-transport.mjs', import.meta.url) });
const argsOf = result => JSON.parse(new TextDecoder().decode(result.stdout));

test('Prodigal gives its training pass seekable stdin without changing any bytes', async () => {
  const bytes = Uint8Array.from([62, 120, 10, 65, 67, 71, 84, 0, 128, 255, 10]);
  const result = await runner().run(['-p', 'single'], { stdin: bytes, outputs: ['copied.bin'] });
  assert.equal(result.exitCode, 0);
  assert.deepEqual(result.files[0].data, bytes);
  assert.deepEqual(argsOf(result).slice(0, 2), ['-p', 'single']);
  assert.equal(result.missingOutputs.length, 0);
  assert.equal(bytes.byteLength, 11);
});

test('explicit Prodigal -i or -I input takes precedence over stdin', async () => {
  for (const flag of ['-i', '-I']) {
    const args = [flag, 'input.fa'];
    const result = await runner().run(args, {
      files: [{ path: 'input.fa', data: 'selected input' }], stdin: 'unused stdin', outputs: ['copied.bin'],
    });
    assert.deepEqual(argsOf(result), args);
    assert.equal(new TextDecoder().decode(result.files[0].data), 'selected input');
  }
});

test('stdin staging preserves caller files and avoids file, directory and output collisions', async () => {
  const result = await runner().run([], {
    stdin: 'new input',
    files: [{ path: '__prodigal_stdin.fna', data: 'old input' }, { path: '__prodigal_stdin_1.fna/child', data: 'nested input' }],
    outputs: ['copied.bin', '__prodigal_stdin.fna', '__prodigal_stdin_2.fna'],
  });
  assert.equal(argsOf(result).at(-1), '/work/__prodigal_stdin_3.fna');
  assert.equal(new TextDecoder().decode(result.files.find(file => file.path.endsWith('/copied.bin')).data), 'new input');
  assert.equal(new TextDecoder().decode(result.files.find(file => file.path.endsWith('/__prodigal_stdin.fna')).data), 'old input');
  assert.deepEqual(result.missingOutputs, ['/work/__prodigal_stdin_2.fna']);
});

test('empty explicit stdin is preserved as an empty file', async () => {
  const result = await runner().run([], { stdin: '', outputs: ['copied.bin'] });
  assert.equal(result.files[0].data.byteLength, 0);
});

test('invalid inputs and cancellation are not converted into successful Prodigal output', async () => {
  await assert.rejects(runner().run(['bad\0argument']), TypeError);
  await assert.rejects(runner().run([], { stdin: 'x', files: [{ path: '../escape', data: 'x' }] }), TypeError);
  await assert.rejects(runner().run([], { stdin: 'x', signal: AbortSignal.abort() }), { name: 'AbortError' });
});
