import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { LoFreqRunner } from '../runtime/lofreq.mjs';
import { pythonFixed, runSomatic } from '../runtime/lofreq-somatic.mjs';

const encode = text => new TextEncoder().encode(text);
const decode = value => new TextDecoder().decode(value);
const args = ['-n', 'normal.bam', '-t', 'tumor.bam', '-f', 'ref.fa', '-o', 'out_'];
const files = ['normal.bam', 'normal.bam.bai', 'tumor.bam', 'tumor.bam.bai', 'ref.fa'].map(path => ({ path, data: encode('input') }));
const counts = 'Number of substitution tests performed: 123\nNumber of indel tests performed: 456\n';

// These callbacks test only orchestration failures, file lifetime and control
// flow. They do not produce biological records or stand in for native validation.
function stageResult(command, { exitCode = 0, stderr = counts, omit = false } = {}) {
  const output = '/work/' + command[command.indexOf('-o') + 1];
  return {
    exitCode, stdout: encode(''), stderr: encode(stderr),
    files: omit ? [] : [{ path: output, data: encode('test-only opaque bytes') }, { path: output + '.tbi', data: encode('test-only index') }],
    missingOutputs: omit ? [output] : [],
  };
}

test('Python fixed formatting matches a separate Python interpreter on IEEE754 edge cases', () => {
  const values = [0, -0, 0.0078125, 0.0234375, -0.0078125, -0.0234375, Number.MIN_VALUE, Number.MAX_VALUE, 1e-7, 1e21, 0.01, 0.1];
  // Include adjacent binary64 values and reproducible finite bit patterns.
  let state = 0x0123456789abcdefn;
  const view = new DataView(new ArrayBuffer(8));
  for (let index = 0; index < 256; index++) {
    state = (state * 6364136223846793005n + 1442695040888963407n) & ((1n << 64n) - 1n);
    view.setBigUint64(0, state);
    const value = view.getFloat64(0);
    if (Number.isFinite(value)) values.push(value);
  }
  const hex = values.map(value => { view.setFloat64(0, value); return view.getBigUint64(0).toString(16).padStart(16, '0'); });
  const oracle = spawnSync('python3', ['-c', 'import json,sys,struct; print(json.dumps(["%f" % struct.unpack(">d", bytes.fromhex(x))[0] for x in json.load(sys.stdin)]))'], { input: JSON.stringify(hex), encoding: 'utf8' });
  assert.equal(oracle.status, 0, oracle.stderr);
  assert.deepEqual(values.map(value => pythonFixed(value)), JSON.parse(oracle.stdout));
  assert.notEqual(pythonFixed(0.0078125), (0.0078125).toFixed(6));
});

test('LoFreqRunner delegates ordinary commands to the existing isolated runtime', async () => {
  const runner = new LoFreqRunner({ moduleUrl: new URL('./fixtures/mock-tool.mjs', import.meta.url) });
  assert.deepEqual([...(await runner.run(['count'])).stdout], [1]);
  assert.deepEqual([...(await runner.run(['count'])).stdout], [1]);
  const help = await runner.run(['somatic', '--help']);
  assert.equal(help.exitCode, 0);
  assert.equal(help.workflow.stages.length, 0);
  assert.match(decode(help.stdout), /--call-indels/);
});

test('workflow stops at a failed stage, retains earlier VCF and index, and reports selected partial outputs', async () => {
  let calls = 0;
  const result = await runSomatic(args, {
    files,
    outputs: ['out_normal_relaxed.vcf.gz', 'out_normal_relaxed.log', 'out_normal_stringent.snvs.vcf.gz', 'out_somatic_final.snvs.vcf.gz'],
  }, async (command, options) => {
    calls++;
    if (calls === 1) return stageResult(command);
    assert.ok(options.files.some(file => file.path === '/work/out_normal_relaxed.vcf.gz.tbi'));
    return stageResult(command, { exitCode: 7, stderr: 'native failure\n' });
  });
  assert.equal(calls, 2);
  assert.equal(result.exitCode, 1);
  assert.deepEqual(result.workflow.stages.map(stage => stage.exitCode), [0, 7]);
  assert.equal(result.files.length, 3);
  assert.deepEqual(result.missingOutputs, ['/work/out_somatic_final.snvs.vcf.gz']);
  assert.match(decode(result.stderr), /native failure/);
  assert.match(decode(result.files.find(file => file.path.endsWith('.log')).data), /stderr: Number of substitution tests performed: 123/);
});

test('a successful child without its promised VCF cannot become successful empty workflow output', async () => {
  const result = await runSomatic(args, { files, outputs: ['out_normal_relaxed.vcf.gz'] }, async command => stageResult(command, { omit: true }));
  assert.equal(result.exitCode, 1);
  assert.equal(result.workflow.stages.length, 1);
  assert.deepEqual(result.workflow.stages[0].missingOutputs, ['/work/out_normal_relaxed.vcf.gz']);
  assert.deepEqual(result.missingOutputs, ['/work/out_normal_relaxed.vcf.gz']);
});

test('missing native test counts stop the workflow with the actual completed stage files', async () => {
  const result = await runSomatic(args, { files, outputs: ['out_normal_relaxed.log'] }, async command => stageResult(command, { stderr: 'no test counts\n' }));
  assert.equal(result.exitCode, 1);
  assert.equal(result.workflow.stages.length, 1);
  assert.match(decode(result.files[0].data), /stderr: no test counts/);
});

test('invalid input, overwrite and initial unsupported features never launch a core command', async () => {
  let calls = 0;
  const core = async () => { calls++; throw new Error('unexpected execution'); };
  for (const extra of [['--threads', '2'], ['--continue'], ['--tumor-alpha', 'NaN'], ['--min-cov', '9007199254740992']]) {
    assert.equal((await runSomatic([...args, ...extra], { files }, core)).exitCode, 2);
  }
  assert.equal((await runSomatic(args, { files: files.filter(file => !file.path.endsWith('.bai')) }, core)).exitCode, 1);
  assert.equal((await runSomatic(args, { files: [...files, { path: 'out_germline.indels.vcf.gz', data: '' }] }, core)).exitCode, 1);
  assert.equal((await runSomatic([...args, '-o', 'missing/out_'], { files }, core)).exitCode, 1);
  assert.equal(calls, 0);
});

test('snapshots inputs, preserves literal path arguments and gives each run fresh state', async () => {
  const supplied = files.map(file => ({ ...file, data: file.data.slice() }));
  const pathArgs = args.map(value => value === 'normal.bam' ? 'normal sample.bam' : value);
  supplied[0].path = 'normal sample.bam';
  supplied[1].path = 'normal sample.bam.bai';
  for (let repeat = 0; repeat < 2; repeat++) {
    const result = await runSomatic(pathArgs, { files: supplied }, async (command, options) => {
      assert.ok(command.includes('normal sample.bam'));
      assert.equal(options.files.length, supplied.length);
      options.files[0].data[0] = 255;
      return stageResult(command, { exitCode: 3 });
    });
    assert.equal(result.exitCode, 1);
    assert.equal(supplied[0].data[0], 'i'.charCodeAt(0));
    assert.equal(result.workflow.stages.length, 1);
  }
});

test('caller cancellation reaches the active stage and a later run still works', async () => {
  const controller = new AbortController();
  let stages = 0;
  const result = runSomatic(args, { files, signal: controller.signal }, async (command, options) => {
    stages++;
    return new Promise((resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(Object.assign(new Error('stage cancelled'), { name: 'AbortError' })), { once: true });
      queueMicrotask(() => controller.abort());
    });
  });
  await assert.rejects(result, { name: 'AbortError' });
  assert.equal(stages, 1);
  assert.equal((await runSomatic(['--help'], {}, () => assert.fail('help must not launch core'))).exitCode, 0);
});

test('one timeout covers consecutive stages instead of restarting with each stage', async () => {
  let stages = 0;
  await assert.rejects(runSomatic(args, { files, timeoutMs: 180 }, async (command, options) => {
    stages++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => resolve(stageResult(command)), 110);
      options.signal.addEventListener('abort', () => { clearTimeout(timer); reject(Object.assign(new Error('stage cancelled'), { name: 'AbortError' })); }, { once: true });
    });
  }), { name: 'TimeoutError' });
  assert.equal(stages, 2);
});

test('runtime traps reject without inventing a completed workflow', async () => {
  await assert.rejects(runSomatic(args, { files }, async () => { throw new WebAssembly.RuntimeError('core trap'); }), { name: 'RuntimeError', message: 'core trap' });
  await assert.rejects(runSomatic(args, { files, signal: AbortSignal.abort() }, () => assert.fail('cancelled run must not start')), { name: 'AbortError' });
});
