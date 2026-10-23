import test from 'node:test';
import assert from 'node:assert/strict';
import { SeqtkContainerRunner, validateRun, MAX_INPUT_BYTES } from '../runtime/container2wasm-seqtk.mjs';

const bytes = new Uint8Array([0, 10, 128, 255]);

test('container jobs reject path traversal and collisions with the guest control files', () => {
  for (const path of ['../reads.fq', '/tmp/reads.fq', '/work/../reads.fq', 'a/../b', 'a/./b',
    '/work', '', '.', 'a//b', 'a\\b', 'a\0b', 'a\nb', 'stdout', '/work/argv.txt',
    'stderr', 'started', 'status.json', 'status.json.tmp', 'stdout/reads.fq']) {
    assert.throws(() => validateRun(['seq', '-A', '/work/reads.fq'], [{path, data: bytes}]), TypeError, path);
  }
  assert.throws(() => validateRun([], [
    {path: 'reads.fq', data: bytes}, {path: '/work/reads.fq', data: bytes},
  ]), /Duplicate/);
});

test('arguments cannot corrupt the line-delimited guest protocol; shell characters remain literal', () => {
  for (const args of ['seq -A reads.fq', [1], ['a\0b'], ['a\nb'], ['a\rb']]) {
    assert.throws(() => validateRun(args), TypeError);
  }
  const args = ['seq', '-A', '/work/reads $(touch unsafe);*.fq'];
  const input = {path: 'reads $(touch unsafe);*.fq', data: bytes};
  const normalized = validateRun(args, [input]);
  assert.deepEqual(normalized.args, args);
  assert.equal(normalized.files[0].path, '/work/reads $(touch unsafe);*.fq');
  assert.equal(normalized.inputBytes, bytes.length);
  assert.deepEqual(bytes, new Uint8Array([0, 10, 128, 255]));
});

test('prototype limits reject oversized jobs before allocating a VM', () => {
  assert.throws(() => validateRun(Array(129).fill('x')), TypeError);
  assert.throws(() => validateRun(['x'.repeat(64 * 1024)]), RangeError);
  assert.throws(() => validateRun([], [{path: 'reads.fq', data: 'not bytes'}]), TypeError);
  assert.throws(() => validateRun([], Array.from({length: 33}, (_, i) => ({path: `${i}.fq`, data: bytes}))), TypeError);
  assert.throws(() => validateRun([], [{path: 'reads.fq', data: new Uint8Array(MAX_INPUT_BYTES + 1)}]), RangeError);
});

// These are worker-lifecycle tests, not substitutes for executing QEMU. The
// separate validate:seqtk-container script compares the four real runtimes.
function mockBrowser(t, {acknowledgeStop = true} = {}) {
  const workers = [];
  class WorkerStub {
    constructor() { this.terminated = 0; workers.push(this); }
    postMessage(message) {
      if (message.type === 'stop') {
        if (acknowledgeStop) queueMicrotask(() => this.onmessage?.({data: {type: 'stopped'}}));
      } else this.request = message;
    }
    terminate() { this.terminated++; }
    fail(message) { this.onerror?.({message}); }
    result() { this.onmessage?.({data: {type: 'result', result: {
      exitCode: 0, stdout: new Uint8Array([65]).buffer, stderr: new ArrayBuffer(0), metrics: {},
    }}}); }
  }
  const values = {Worker: WorkerStub, isSecureContext: true, crossOriginIsolated: true};
  for (const [name, value] of Object.entries(values)) {
    const original = Object.getOwnPropertyDescriptor(globalThis, name);
    Object.defineProperty(globalThis, name, {configurable: true, writable: true, value});
    t.after(() => original ? Object.defineProperty(globalThis, name, original) : delete globalThis[name]);
  }
  return workers;
}

test('a crashed worker rejects and is terminated; a stale result cannot complete its replacement', async t => {
  const workers = mockBrowser(t);
  const runner = new SeqtkContainerRunner();
  const first = runner.run([]);
  workers[0].fail('The emulator crashed');
  await assert.rejects(first, /emulator crashed/);
  assert.equal(workers[0].terminated, 1);

  const recovery = runner.run([]);
  let completed = false;
  recovery.then(() => { completed = true; });
  workers[0].result();
  await Promise.resolve();
  assert.equal(completed, false, 'Late messages from a dead worker must be ignored');
  workers[1].result();
  assert.equal((await recovery).exitCode, 0);
  assert.equal(workers[1].terminated, 1);
});

test('deadline and cancellation terminate blocked workers and leave the runner reusable', async t => {
  const workers = mockBrowser(t);
  const runner = new SeqtkContainerRunner();
  await assert.rejects(runner.run([], {timeoutMs: 10}), {name: 'TimeoutError'});
  assert.equal(workers[0].terminated, 1);

  const controller = new AbortController();
  const running = runner.run([], {signal: controller.signal});
  controller.abort();
  await assert.rejects(running, {name: 'AbortError'});
  assert.equal(workers[1].terminated, 1);

  const recovery = runner.run([]);
  workers[2].result();
  assert.equal((await recovery).exitCode, 0);
  assert.equal(workers[2].terminated, 1);
});

test('bad deadlines, pre-cancellation and absent isolation never start a worker', async t => {
  const workers = mockBrowser(t);
  const runner = new SeqtkContainerRunner();
  for (const timeoutMs of [0, -1, Infinity, NaN]) {
    await assert.rejects(runner.run([], {timeoutMs}), RangeError);
  }
  await assert.rejects(runner.run([], {signal: AbortSignal.abort()}), {name: 'AbortError'});
  globalThis.crossOriginIsolated = false;
  await assert.rejects(runner.run([]), /cross-origin isolation/);
  assert.equal(workers.length, 0);
});

test('an active job cannot be replaced, and dispose terminates it', async t => {
  const workers = mockBrowser(t);
  const runner = new SeqtkContainerRunner();
  const running = runner.run([]);
  await assert.rejects(runner.run([]), /active job/);
  assert.equal(workers.length, 1);
  runner.dispose();
  await assert.rejects(running, {name: 'AbortError'});
  assert.equal(workers[0].terminated, 1);
});

test('a throwing progress observer rejects the job and releases its worker', async t => {
  const workers = mockBrowser(t);
  const runner = new SeqtkContainerRunner();
  const running = runner.run([], {onProgress() { throw new Error('Observer failed'); }});
  workers[0].onmessage({data: {type: 'progress', value: {phase: 'loading'}}});
  await assert.rejects(running, /Observer failed/);
  assert.equal(workers[0].terminated, 1);
});

test('a worker unable to acknowledge shutdown is forcibly terminated', async t => {
  const workers = mockBrowser(t, {acknowledgeStop: false});
  const runner = new SeqtkContainerRunner();
  const running = runner.run([]);
  workers[0].fail('Worker stopped responding');
  await assert.rejects(running, /stopped responding/);
  const deadline = Date.now() + 1000;
  while (!workers[0].terminated && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(workers[0].terminated, 1, 'Unresponsive worker shutdown is bounded');
});
