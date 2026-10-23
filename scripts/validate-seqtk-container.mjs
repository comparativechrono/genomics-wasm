#!/usr/bin/env node
/** Real native/Docker/direct-WASM/container2wasm differential validation.
 * Build the three distributions before invoking this script. No mocks or
 * fallback engines are used: a missing build or unavailable Docker fails it.
 */
import assert from 'node:assert/strict';
import { createReadStream } from 'node:fs';
import { mkdir, readFile, writeFile, readdir, rm, stat } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { performance } from 'node:perf_hooks';
import { gzipSync } from 'node:zlib';
import { createStaticServer } from './seqtk-container-server.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const engine = process.env.BROWSER || 'chromium';
const timeoutMs = Number(process.env.SEQTK_CONTAINER_TIMEOUT_MS || 180000);
const image = process.env.SEQTK_CONTAINER_IMAGE || 'genomics-wasm-seqtk-container:1.5';
if (!['chromium', 'firefox'].includes(engine)) throw new Error('BROWSER must be chromium or firefox');
if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new Error('SEQTK_CONTAINER_TIMEOUT_MS must be positive');
const reportDir = join(root, 'reports', 'seqtk-container', engine);
await mkdir(reportDir, {recursive: true});
const summary = {
  schema: 1, startedAt: new Date().toISOString(), passed: false, status: 'running', browser: engine,
  node: process.version, host: {platform: process.platform, architecture: process.arch},
  timeoutMs, image, builds: {}, datasets: {}, cases: [], lifecycle: [],
  measurementNotes: [
    'Each tool invocation uses a fresh process or worker; the container2wasm worker boots a fresh VM.',
    'wallMs includes local process startup or browser messaging; browserRunMs measures the runner call inside the page.',
    'Container runtime timing is polled at its reported timingResolutionMs; short jobs may report executionMs=0.',
    'wasmMemoryBytes is allocated linear memory at completion, not a peak or total browser memory measurement.',
    'Peak memory is unsupported by this harness and is reported as null. No performance thresholds are asserted.',
    'stdout and exit codes are compared exactly. stderr is retained verbatim but not compared across builds.',
  ],
};
let browser, server, page;
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');

async function hashFile(filename) {
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(filename)) hash.update(chunk);
  return {sha256: hash.digest('hex'), bytes: (await stat(filename)).size};
}

async function hashTree(directory) {
  const files = {};
  const visit = async dir => {
    for (const entry of (await readdir(dir, {withFileTypes: true})).sort((a, b) => a.name.localeCompare(b.name))) {
      const filename = join(dir, entry.name);
      if (entry.isDirectory()) await visit(filename);
      else if (entry.isFile()) files[relative(directory, filename)] = await hashFile(filename);
    }
  };
  await visit(directory);
  return files;
}

function command(executable, args, options = {}) {
  const started = performance.now();
  const result = spawnSync(executable, args, {
    cwd: root, stdio: ['ignore', 'pipe', 'pipe'], timeout: timeoutMs,
    maxBuffer: 80 * 1024 * 1024, ...options,
  });
  if (result.error) throw result.error;
  if (!Number.isInteger(result.status)) throw new Error(`${executable} was terminated by ${result.signal}`);
  return {exitCode: result.status, stdout: result.stdout, stderr: result.stderr, wallMs: performance.now() - started};
}

async function native(kind, id, args, files) {
  const work = join(root, 'build', 'validation', 'seqtk-container', engine, id, kind);
  await rm(work, {recursive: true, force: true});
  await mkdir(work, {recursive: true});
  for (const file of files) {
    const filename = join(work, file.path);
    await mkdir(dirname(filename), {recursive: true});
    await writeFile(filename, file.data);
  }
  if (kind === 'native') {
    // The host has no /work mount. Only the input-path prefix changes; seqtk
    // receives identical options and bytes, and stdout remains strictly exact.
    return command(join(root, 'build/native/seqtk'), args.map(arg => arg.startsWith('/work/') ? join(work, arg.slice(6)) : arg), {cwd: work});
  }
  return command('docker', ['run', '--rm', '--network', 'none', '--platform', 'linux/amd64',
    '--mount', `type=bind,src=${work},dst=/work,readonly`, '--workdir', '/work',
    '--entrypoint', '/usr/local/bin/seqtk', image, ...args]);
}

function encodeFiles(files) {
  return files.map(file => ({path: file.path, base64: Buffer.from(file.data).toString('base64')}));
}

async function browserRun(kind, args, files) {
  const started = performance.now();
  const result = await page.evaluate(async ({kind, args, files, timeoutMs}) => {
    const input = files.map(file => ({path: file.path, data: Uint8Array.from(atob(file.base64), c => c.charCodeAt(0))}));
    const progress = [], logs = [];
    const started = performance.now();
    let result;
    try {
      result = await window.seqtkValidation[kind].run(args, {
      files: input, timeoutMs,
      onProgress: event => progress.push({...event, atMs: performance.now() - started}),
      onLog: event => logs.push(event),
      });
    } catch (error) {
      return {failure: {name: error.name, message: error.message}, progress, logs,
        browserRunMs: performance.now() - started};
    }
    const encode = bytes => {
      let binary = '';
      for (let offset = 0; offset < bytes.length; offset += 32768) {
        binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768));
      }
      return btoa(binary);
    };
    return {exitCode: result.exitCode, stdout: encode(result.stdout), stderr: encode(result.stderr),
      metrics: result.metrics ?? null, browserRunMs: performance.now() - started, progress, logs};
  }, {kind, args, files: encodeFiles(files), timeoutMs});
  if (result.failure) {
    summary.lastBrowserFailure = {kind, args, ...result};
    throw new Error(`${kind}: ${result.failure.name}: ${result.failure.message}`);
  }
  return {...result, stdout: Buffer.from(result.stdout, 'base64'), stderr: Buffer.from(result.stderr, 'base64'), wallMs: performance.now() - started};
}

async function saveResult(id, kind, result) {
  await Promise.all([
    writeFile(join(reportDir, `${id}.${kind}.stdout`), result.stdout),
    writeFile(join(reportDir, `${id}.${kind}.stderr`), result.stderr),
  ]);
  return {
    exitCode: result.exitCode, stdoutBytes: result.stdout.length, stdoutSha256: sha256(result.stdout),
    stderrBytes: result.stderr.length, stderrSha256: sha256(result.stderr), wallMs: result.wallMs,
    browserRunMs: result.browserRunMs ?? null, metrics: result.metrics ?? null,
    peakMemoryBytes: null, peakMemoryStatus: 'unsupported', progress: result.progress ?? [], logs: result.logs ?? [],
  };
}

async function compareCase(id, args, files, expectedExit = 0) {
  const record = {id, args, status: 'running', results: {}};
  summary.cases.push(record);
  let baseline;
  for (const kind of ['native', 'docker', 'directWasm', 'container']) {
    const result = ['native', 'docker'].includes(kind) ? await native(kind, id, args, files) : await browserRun(kind, args, files);
    record.results[kind] = await saveResult(id, kind, result);
    assert.equal(result.exitCode, expectedExit, `${id} ${kind}: unexpected exit; see ${relative(root, reportDir)}/${id}.${kind}.stderr`);
    if (!baseline) baseline = result;
    else {
      assert.equal(result.exitCode, baseline.exitCode, `${id} ${kind}: exit status differs from native`);
      assert.ok(result.stdout.equals(baseline.stdout), `${id} ${kind}: stdout differs byte-for-byte; see ${relative(root, reportDir)}`);
    }
  }
  record.status = 'passed';
  console.log(`PASS ${engine} ${id}`);
  return baseline;
}

async function checkLifecycle(mode, files, expected) {
  const record = {id: mode, status: 'running'};
  summary.lifecycle.push(record);
  const outcome = await page.evaluate(async ({mode, files, timeoutMs}) => {
    const runner = window.seqtkValidation.container;
    const input = files.map(file => ({path: file.path, data: Uint8Array.from(atob(file.base64), c => c.charCodeAt(0))}));
    const controller = new AbortController();
    const phases = [];
    const started = performance.now();
    try {
      await runner.run(['seq', '-A', '/work/reads.fq'], {
        files: input, signal: controller.signal, timeoutMs: mode === 'timeout' ? 1 : timeoutMs,
        onProgress: event => {
          phases.push(event.phase);
          // Wait for the actual emulator worker to begin loading before aborting.
          if (mode === 'cancellation' && event.phase === 'loading') controller.abort();
        },
      });
      return {resolved: true, phases, elapsedMs: performance.now() - started};
    } catch (error) {
      return {resolved: false, errorName: error.name, errorMessage: error.message, phases, elapsedMs: performance.now() - started};
    }
  }, {mode, files: encodeFiles(files), timeoutMs});
  Object.assign(record, outcome);
  assert.equal(outcome.resolved, false, `${mode}: interrupted work must reject`);
  assert.equal(outcome.errorName, mode === 'timeout' ? 'TimeoutError' : 'AbortError');
  if (mode === 'cancellation') assert.ok(outcome.phases.includes('loading'), 'Cancellation must reach a real worker');
  const recovered = await browserRun('container', ['seq', '-A', '/work/reads.fq'], files);
  record.recovery = await saveResult(`${mode}-recovery`, 'container', recovered);
  assert.equal(recovered.exitCode, expected.exitCode, `${mode}: subsequent command failed`);
  assert.ok(recovered.stdout.equals(expected.stdout), `${mode}: recovered result differs from native`);
  record.status = 'passed';
  console.log(`PASS ${engine} ${mode} and recovery`);
}

async function checkUI(files, expected) {
  const record = {status: 'running'};
  summary.ui = record;
  await page.locator('#input-file').setInputFiles({name: 'human-reads.fastq', mimeType: 'application/octet-stream', buffer: files[0].data});
  await page.waitForFunction(() => window.seqtkPrototype?.state.input?.name === 'human-reads.fastq');
  await page.locator('#operation').selectOption('fasta');
  await page.locator('#timeout').fill(String(Math.min(1800, Math.max(10, Math.ceil(timeoutMs / 1000)))));
  await page.locator('#run').click();
  await page.waitForFunction(() => ['success', 'error'].includes(window.seqtkPrototype.state.phase), null, {timeout: timeoutMs + 10000});
  const outcome = await page.evaluate(() => ({phase: window.seqtkPrototype.state.phase, error: window.seqtkPrototype.state.error,
    exitCode: window.seqtkPrototype.state.result?.exitCode, status: document.getElementById('status').textContent}));
  Object.assign(record, outcome);
  assert.equal(outcome.phase, 'success', `UI run failed: ${outcome.status}`);
  assert.equal(outcome.exitCode, expected.exitCode);
  const [download] = await Promise.all([page.waitForEvent('download'), page.locator('#download').click()]);
  const filename = await download.path();
  assert.ok(filename, 'UI output download must have a local file');
  const data = await readFile(filename);
  assert.ok(data.equals(expected.stdout), 'Downloaded UI output must exactly match native seqtk');
  assert.equal(download.suggestedFilename(), 'human-reads.fasta');
  record.outputSha256 = sha256(data);
  record.outputBytes = data.length;
  record.status = 'passed';
  console.log(`PASS ${engine} browser upload/run/download`);
}

try {
  summary.builds.native = await hashFile(join(root, 'build/native/seqtk'));
  summary.builds.directWasm = await hashTree(join(root, 'dist/seqtk'));
  summary.builds.container = await hashTree(join(root, 'dist/seqtk-container/runtime'));
  for (const asset of ['out.js', 'load.js', 'arg-module.js', 'qemu-system-x86_64.wasm', 'qemu-system-x86_64.data']) {
    assert.ok(summary.builds.container[asset]?.bytes > 0, `Missing container asset: ${asset}`);
  }
  assert.ok(summary.builds.directWasm['seqtk.mjs'] && summary.builds.directWasm['seqtk.wasm'], 'Build direct seqtk WASM before validation');
  const inspect = command('docker', ['image', 'inspect', image]);
  assert.equal(inspect.exitCode, 0, `Build the Docker image first: ${inspect.stderr}`);
  const imageInfo = JSON.parse(inspect.stdout)[0];
  summary.dockerImage = {id: imageInfo.Id, architecture: imageInfo.Architecture, os: imageInfo.Os, repoDigests: imageInfo.RepoDigests};
  assert.equal(imageInfo.Architecture, 'amd64');

  server = createStaticServer(root);
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  const playwright = await import('playwright');
  browser = await playwright[engine].launch({headless: true});
  summary.browserVersion = browser.version();
  page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${server.address().port}/examples/seqtk-container/`);
  summary.browserEnvironment = await page.evaluate(() => ({
    userAgent: navigator.userAgent, crossOriginIsolated, isSecureContext,
    sharedArrayBuffer: typeof SharedArrayBuffer !== 'undefined', hardwareConcurrency: navigator.hardwareConcurrency,
  }));
  assert.equal(summary.browserEnvironment.crossOriginIsolated, true, 'The test server must enable COOP/COEP');
  await page.evaluate(async () => {
    const [{SeqtkContainerRunner}, {ToolRunner}] = await Promise.all([
      import('/runtime/container2wasm-seqtk.mjs'), import('/runtime/index.mjs'),
    ]);
    window.seqtkValidation = {
      container: new SeqtkContainerRunner(),
      directWasm: new ToolRunner({moduleUrl: new URL('/dist/seqtk/seqtk.mjs', location.href)}),
    };
  });

  let recoveryInput, recoveryExpected;
  for (const dataset of ['human', 'yeast']) {
    const data = await readFile(join(root, 'datasets', dataset, 'reads_1.fastq'));
    const files = [{path: 'reads.fq', data}];
    const gzip = gzipSync(data, {level: 9});
    summary.datasets[dataset] = {source: `datasets/${dataset}/reads_1.fastq`, bytes: data.length, sha256: sha256(data),
      gzipBytes: gzip.length, gzipSha256: sha256(gzip)};
    const fasta = await compareCase(`${dataset}-fasta`, ['seq', '-A', '/work/reads.fq'], files);
    await compareCase(`${dataset}-reverse-complement`, ['seq', '-r', '/work/reads.fq'], files);
    await compareCase(`${dataset}-fixed-trim`, ['trimfq', '-b', '5', '-e', '10', '/work/reads.fq'], files);
    await compareCase(`${dataset}-fqchk`, ['fqchk', '/work/reads.fq'], files);
    await compareCase(`${dataset}-gzip-fasta`, ['seq', '-A', '/work/reads.fq.gz'], [{path: 'reads.fq.gz', data: gzip}]);
    if (dataset === 'human') { recoveryInput = files; recoveryExpected = fasta; }
  }
  await compareCase('missing-input', ['seq', '-A', '/work/missing.fq'], [], 1);
  await compareCase('usage', [], [], 1);
  await compareCase('repeat-human-fasta', ['seq', '-A', '/work/reads.fq'], recoveryInput);
  await checkLifecycle('cancellation', recoveryInput, recoveryExpected);
  await checkLifecycle('timeout', recoveryInput, recoveryExpected);
  await checkUI(recoveryInput, recoveryExpected);
  summary.status = 'passed';
  summary.passed = true;
} catch (error) {
  summary.status = 'failed';
  summary.error = error.stack || String(error);
  console.error(summary.error);
  process.exitCode = 1;
} finally {
  summary.finishedAt = new Date().toISOString();
  const report = JSON.stringify(summary, null, 2) + '\n';
  await Promise.all([
    writeFile(join(reportDir, 'summary.json'), report),
    writeFile(join(root, 'reports/seqtk-container/report.json'), report),
  ]);
  await browser?.close();
  if (server) await new Promise(resolve => { server.close(resolve); server.closeAllConnections?.(); });
}
