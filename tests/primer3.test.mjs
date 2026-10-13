import test from 'node:test';
import assert from 'node:assert/strict';
import { Primer3Runner, PRIMER3_PROGRAMS, maskerReadsStdin } from '../runtime/primer3.mjs';

const moduleUrl = new URL('./fixtures/mock-tool.mjs', import.meta.url);
const decode = bytes => new TextDecoder().decode(bytes);

test('Primer3 dispatch preserves the selected CLI and literal arguments', async () => {
  const runner = new Primer3Runner({ program: 'primer3_core', moduleUrl });
  const args = ['--p3_settings_file=settings with spaces.txt', 'literal;$(input).txt'];
  const result = await runner.run(['args', ...args]);
  assert.deepEqual(JSON.parse(decode(result.stdout)), args);
  assert.equal(runner.program, 'primer3_core');
  assert.equal(new Primer3Runner({ program: 'ntthal', baseUrl: new URL('https://example.org/assets/') }).moduleUrl,
    'https://example.org/assets/ntthal.mjs');
  assert.equal(Object.isFrozen(PRIMER3_PROGRAMS), true);
  assert.throws(() => new Primer3Runner({ program: '../other' }), /Unsupported Primer3 program/);
  assert.throws(() => new Primer3Runner({ baseUrl: 'https://example.org/assets' }), /directory URL/);
});

test('Primer3 preserves Boulder IO bytes and binary resource mounts between invocations', async () => {
  const runner = new Primer3Runner({ moduleUrl });
  const input = 'SEQUENCE_ID=record 1\r\nSEQUENCE_TEMPLATE=ACGT\r\n=\r\n';
  const data = Uint8Array.from([67, 52, 84, 71, 0, 128, 255]);
  const expected = data.slice();
  const first = await runner.run([], {
    stdin: input, files: [{ path: 'nested/input.bin', data }], outputs: ['nested/output.bin'],
  });
  assert.equal(decode(first.stdout), input);
  assert.deepEqual(first.files[0].data, expected);
  assert.deepEqual(data, expected);
  const second = await runner.run([], {
    stdin: input, files: [{ path: 'nested/input.bin', data: first.files[0].data }], outputs: ['nested/output.bin'],
  });
  assert.deepEqual(second.files[0].data, expected);
});

test('Primer3 runner starts fresh state and cancellation leaves the runner reusable', async () => {
  const runner = new Primer3Runner({ moduleUrl });
  await assert.rejects(runner.run(['hang'], { timeoutMs: 100 }), { name: 'TimeoutError' });
  const values = await Promise.all([runner.run(['count']), runner.run(['count'])]);
  for (const value of values) assert.deepEqual([...value.stdout], [1]);
});

test('masker stdin detection respects options, list coefficients, named input and upstream help', () => {
  for (const args of [
    ['-l', 'kmers.list', '1', '0', '-a', '2'],
    ['--list', 'kmers.list', '-0.25', '1', 'sq', '--soft_mask'],
    ['-l', '-4.336', '-l', 'kmers.list', '0.1772', '1', '-s'],
    ['--lists_file', 'formula.txt', '--kmer_lists_path', 'indices/', '--mask_5p', '2'],
    ['-lp', 'human', '-m3', '1', '-c', 'N', '--masking_direction', 'fwd', '-D'],
  ]) assert.equal(maskerReadsStdin(args), true, JSON.stringify(args));
  for (const args of [
    [], ['--help'], ['-'], ['-l', 'kmers.list', '1', '0', '-s', 'input.fa'],
    ['-l', 'kmers.list', '1', '0', '-a', '2', 'input.fa'],
    ['-a'], ['--unrecognized'], ['-l'],
  ]) assert.equal(maskerReadsStdin(args), false, JSON.stringify(args));
});

test('masker materializes stdin without altering bytes or replacing colliding user files', async () => {
  const runner = new Primer3Runner({ program: 'primer3_masker', moduleUrl: new URL('./fixtures/mock-primer3-masker.mjs', import.meta.url) });
  const input = Uint8Array.from([62, 102, 97, 115, 116, 97, 13, 10, 65, 67, 71, 84, 0, 255, 10]);
  const expected = input.slice();
  const args = ['-l', 'kmers.list', '1', '0', '-a', '2'];
  const result = await runner.run(args, {
    stdin: input,
    files: [{ path: '__primer3_masker_stdin.fasta', data: 'original file' },
      { path: '__primer3_masker_stdin_1.fasta/child', data: 'original directory' }],
    outputs: ['copied.fasta', '__primer3_masker_stdin.fasta', '__primer3_masker_stdin_1.fasta/child'],
  });
  assert.deepEqual(JSON.parse(decode(result.stdout)), [...args, '/work/__primer3_masker_stdin_2.fasta']);
  assert.deepEqual(result.files.find(file => file.path === '/work/copied.fasta').data, expected);
  assert.equal(decode(result.files.find(file => file.path === '/work/__primer3_masker_stdin.fasta').data), 'original file');
  assert.equal(decode(result.files.find(file => file.path.endsWith('/child')).data), 'original directory');
  assert.equal(result.files.some(file => file.path === '/work/__primer3_masker_stdin_2.fasta'), false);
  assert.deepEqual(input, expected);
});

test('masker explicit input keeps precedence over separately supplied stdin', async () => {
  const runner = new Primer3Runner({ program: 'primer3_masker', moduleUrl: new URL('./fixtures/mock-primer3-masker.mjs', import.meta.url) });
  const args = ['-l', 'kmers.list', '1', '0', '-a', '2', 'selected.fa'];
  const result = await runner.run(args, { stdin: '>ignored\nAAAA\n', files: [{ path: 'selected.fa', data: '>selected\nACGT\n' }], outputs: ['copied.fasta'] });
  assert.deepEqual(JSON.parse(decode(result.stdout)), args);
  assert.equal(decode(result.files[0].data), '>selected\nACGT\n');
});
