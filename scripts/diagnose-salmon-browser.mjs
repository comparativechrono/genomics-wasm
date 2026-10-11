#!/usr/bin/env node
/** Diagnostic only: trace the existing Salmon build through the public runtime.
 * This is deliberately separate from native-equivalence validation. Neither the
 * compiled assets nor the production worker/runtime are changed or replaced.
 */
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { createServer } from 'node:http';
import { dirname, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const options = { engine: 'webkit', case: 'index', dataset: 'human', timeout: '60000' };
const usage = 'diagnose-salmon-browser.mjs [--engine chromium|firefox|webkit] [--case index|version] [--dataset human|yeast] [--timeout milliseconds]';
if (process.argv.includes('--help')) { console.log(usage); process.exit(0); }
for (let i = 2; i < process.argv.length; i += 2) {
  const key = process.argv[i].replace(/^--/, '');
  if (!(key in options) || !process.argv[i + 1]) throw new Error(usage);
  options[key] = process.argv[i + 1];
}
if (!['chromium', 'firefox', 'webkit'].includes(options.engine) || !['index', 'version'].includes(options.case)
    || !['human', 'yeast'].includes(options.dataset) || !/^\d+$/.test(options.timeout)
    || Number(options.timeout) < 1000 || Number(options.timeout) > 120000) throw new Error(usage);
const timeoutMs = Number(options.timeout);
const reportDirectory = join(root, 'reports', 'quant', options.engine);
const reportPath = join(reportDirectory, `diagnostic-salmon-${options.case}-${options.dataset}.json`);
const report = { schema: 1, diagnosticOnly: true, startedAt: new Date().toISOString(), options,
  node: process.version, status: 'running', events: [] };
let browser, server;
const digest = data => createHash('sha256').update(data).digest('hex');
function record(type, detail) {
  const event = { at: new Date().toISOString(), type, detail };
  if (report.events.length < 10000) report.events.push(event);
  else report.eventsTruncated = true;
  console.log(`[${event.at}] ${type}: ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`);
}
async function bounded(promise, milliseconds, label) {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(`${label} exceeded ${milliseconds} ms`)), milliseconds);
    })]);
  } finally { clearTimeout(timer); }
}

// Served only by this diagnostic server. The ordinary runtime worker imports
// this module using ToolRunner.moduleUrl, and it imports the untouched build.
const wrapperSource = String.raw`
const prefix = '[salmon-diag] ';
let phase = 'wrapper-loaded', sequence = 0;
const workers = new Map();
function emit(event, detail = {}) {
  console.log(prefix + JSON.stringify({ event, phase, elapsedMs: Math.round(performance.now()), ...detail }));
}
function errorDetail(error) {
  return { name: error?.name, message: error?.message || String(error), stack: error?.stack };
}
emit('wrapper-loaded', { isolated: self.crossOriginIsolated, workerName: self.name,
  sharedArrayBuffer: typeof SharedArrayBuffer, waitAsync: typeof Atomics.waitAsync });
self.addEventListener('unhandledrejection', event => emit('unhandledrejection', errorDetail(event.reason)));
self.addEventListener('error', event => emit('runtime-worker-error', {
  filename: event.filename, lineno: event.lineno, ...errorDetail(event.error || event.message) }));
const OriginalWorker = globalThis.Worker;
globalThis.Worker = class DiagnosticWorker extends OriginalWorker {
  constructor(url, settings) {
    emit('child-constructing', { url: String(url), settings });
    super(url, settings);
    this.diagnosticId = ++sequence;
    workers.set(this.diagnosticId, 'created');
    emit('child-created', { id: this.diagnosticId });
    this.addEventListener('message', event => {
      const data = event.data;
      if (data?.cmd === 'loaded') workers.set(this.diagnosticId, 'loaded');
      if (data?.cmd !== 'callHandler') emit('child-message', {
        id: this.diagnosticId, cmd: data?.cmd, target: data?.target, handler: data?.handler });
    });
    this.addEventListener('error', event => emit('child-error', {
      id: this.diagnosticId, message: event.message, filename: event.filename, lineno: event.lineno }));
    this.addEventListener('messageerror', () => emit('child-messageerror', { id: this.diagnosticId }));
  }
  postMessage(...args) {
    const data = args[0];
    if (data?.cmd !== 'checkMailbox') emit('child-post', {
      id: this.diagnosticId, cmd: data?.cmd, workerID: data?.workerID,
      hasModule: data?.wasmModule instanceof WebAssembly.Module,
      memoryBytes: data?.wasmMemory?.buffer?.byteLength });
    try { return super.postMessage(...args); }
    catch (error) { emit('child-post-error', { id: this.diagnosticId, ...errorDetail(error) }); throw error; }
  }
  terminate() {
    emit('child-terminate', { id: this.diagnosticId });
    workers.set(this.diagnosticId, 'terminated');
    return super.terminate();
  }
};

// The normal runtime installs byte callbacks in FS.init; printErr alone cannot
// expose Rust's stderr. Tee those callbacks without changing their bytes or
// return values. Keeping FS.init.initialized on the wrapper preserves the
// Emscripten startup guard after the original function sets that property.
function traceStdio(instance) {
  const original = instance.FS.init;
  if (original.diagnosticWrapped) return;
  const tee = (callback, stream) => {
    if (typeof callback !== 'function') return callback;
    let bytes = [];
    return byte => {
      const result = callback(byte);
      if (byte != null && byte !== 10) bytes.push(byte);
      if (byte == null || byte === 10 || bytes.length >= 16384) {
        if (bytes.length) emit(stream, { text: new TextDecoder().decode(Uint8Array.from(bytes)) });
        bytes = [];
      }
      return result;
    };
  };
  const wrapped = function(input, stdout, stderr, ...rest) {
    emit('stdio-initializing');
    return original.call(this, input, tee(stdout, 'stdout'), tee(stderr, 'stderr'), ...rest);
  };
  Object.assign(wrapped, original, { diagnosticWrapped: true });
  instance.FS.init = wrapped;
}

export default async function diagnosticFactory(options) {
  phase = 'module-import';
  emit('factory-enter');
  const heartbeat = setInterval(() => emit('heartbeat', { workers: [...workers] }), 5000);
  try {
    const { default: createSalmon } = await import('/dist/salmon/salmon.mjs');
    emit('module-imported');
    phase = 'factory';
    const hooks = typeof options.preRun === 'function' ? [options.preRun] : (options.preRun || []);
    const instance = await createSalmon({
      ...options,
      preRun: hooks.map(hook => module => {
        emit('preRun-enter');
        traceStdio(module);
        const result = hook(module);
        emit('preRun-returned');
        return result;
      }),
      printErr: (...args) => { emit('printErr', { text: args.join(' ') }); return options.printErr?.(...args); },
      print: (...args) => { emit('print', { text: args.join(' ') }); return options.print?.(...args); },
      onExit: (...args) => {
        phase = 'exit'; emit('onExit', { status: args[0] }); clearInterval(heartbeat);
        return options.onExit?.(...args);
      },
      onAbort: (...args) => {
        phase = 'abort'; emit('onAbort', { reason: String(args[0]) }); clearInterval(heartbeat);
        return options.onAbort?.(...args);
      },
    });
    phase = 'factory-resolved';
    emit('factory-resolved', { memoryBytes: instance.HEAPU8?.byteLength });
    const originalCallMain = instance.callMain;
    instance.callMain = function(...args) {
      phase = 'callMain'; emit('callMain-enter', { args: args[0] });
      try {
        const result = originalCallMain.apply(this, args);
        phase = 'waiting-for-exit'; emit('callMain-returned', { result });
        return result;
      } catch (error) {
        emit('callMain-threw', errorDetail(error));
        throw error;
      }
    };
    return instance;
  } catch (error) {
    emit('factory-error', errorDetail(error));
    clearInterval(heartbeat);
    throw error;
  }
}
`;

try {
  await mkdir(reportDirectory, { recursive: true });
  report.sha256 = Object.fromEntries(await Promise.all([
    'dist/salmon/salmon.mjs', 'dist/salmon/salmon.wasm',
    'runtime/index.mjs', 'runtime/worker.mjs', 'runtime/execute.mjs',
  ].map(async path => [path, digest(await readFile(join(root, path)))])));
  const files = options.case === 'index' ? [{ path: 'transcripts.fa',
    data: await readFile(join(root, 'datasets', 'rna-quant', options.dataset, 'transcripts.fa')) }] : [];
  report.inputs = Object.fromEntries(files.map(file => [file.path, { sha256: digest(file.data), bytes: file.data.length }]));
  report.args = options.case === 'version' ? ['--version'] : [
    'index', '-t', 'transcripts.fa', '-i', 'index', '-k', '31', '-p', '1', '--ramLimit', '1', '--keepDuplicates', '--no-clip',
  ];
  server = createServer(async (request, response) => {
    for (const [name, value] of Object.entries({ 'Cross-Origin-Opener-Policy': 'same-origin',
      'Cross-Origin-Embedder-Policy': 'require-corp', 'Cross-Origin-Resource-Policy': 'same-origin' })) response.setHeader(name, value);
    try {
      const url = new URL(request.url, 'http://localhost');
      response.on('finish', () => record('http', { path: url.pathname, status: response.statusCode }));
      if (url.pathname === '/') {
        response.setHeader('Content-Type', 'text/html');
        response.end('<!doctype html><meta charset="utf-8"><title>Salmon diagnostic</title>'); return;
      }
      response.setHeader('Content-Type', url.pathname.endsWith('.wasm') ? 'application/wasm' : 'text/javascript');
      if (url.pathname === '/diagnostic-factory.mjs') { response.end(wrapperSource); return; }
      const path = resolve(root, '.' + decodeURIComponent(url.pathname));
      if (!path.startsWith(root + sep) || !['runtime/', 'dist/salmon/'].some(prefix => relative(root, path).startsWith(prefix))) {
        response.writeHead(404).end(); return;
      }
      response.end(await readFile(path));
    } catch (error) { record('http-error', error.message); response.writeHead(404).end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const playwright = await import('playwright');
  browser = await playwright[options.engine].launch({ headless: true, timeout: 30000 });
  report.browserVersion = browser.version();
  const page = await browser.newPage();
  page.setDefaultTimeout(30000);
  page.setDefaultNavigationTimeout(30000);
  page.on('console', message => record(`console-${message.type()}`, message.text()));
  page.on('pageerror', error => record('page-error', error.stack || error.message));
  page.on('crash', () => record('page-crash', 'Browser page crashed'));
  page.on('requestfailed', request => record('request-failed', { url: request.url(), failure: request.failure() }));
  page.on('worker', worker => record('page-worker', worker.url()));
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  report.environment = await page.evaluate(() => ({ crossOriginIsolated, isSecureContext,
    userAgent: navigator.userAgent, hardwareConcurrency: navigator.hardwareConcurrency }));
  if (!report.environment.crossOriginIsolated) throw new Error('Diagnostic server did not enable cross-origin isolation');
  record('command-starting', { args: report.args, timeoutMs, environment: report.environment });
  report.result = await bounded(page.evaluate(async ({ args, files, timeoutMs, index }) => {
    const { ToolRunner } = await import('/runtime/index.mjs');
    const runner = new ToolRunner({ moduleUrl: new URL('/diagnostic-factory.mjs', location.href), waitForExit: true });
    const result = await runner.run(args, { timeoutMs, outputDirectories: index ? ['index'] : [],
      files: files.map(file => ({ path: file.path, data: Uint8Array.from(atob(file.data), character => character.charCodeAt(0)) })) });
    return { exitCode: result.exitCode, missingOutputs: result.missingOutputs,
      stdout: new TextDecoder().decode(result.stdout), stderr: new TextDecoder().decode(result.stderr),
      files: result.files.map(file => ({ path: file.path, bytes: file.data.length })) };
  }, { args: report.args, timeoutMs, index: options.case === 'index',
    files: files.map(file => ({ path: file.path, data: file.data.toString('base64') })) }),
  timeoutMs + 15000, 'Browser evaluation');
  if (report.result.exitCode !== 0 || report.result.missingOutputs.length) throw new Error(`Command returned exit ${report.result.exitCode}`);
  report.status = 'passed';
  record('command-completed', report.result);
} catch (error) {
  report.status = 'failed'; report.error = { name: error.name, message: error.message, stack: error.stack };
  record('diagnostic-failed', report.error);
  process.exitCode = 1;
} finally {
  report.finishedAt = new Date().toISOString();
  await mkdir(reportDirectory, { recursive: true });
  await writeFile(reportPath, JSON.stringify(report, null, 2) + '\n');
  console.log(`Diagnostic report: ${relative(root, reportPath)}`);
  if (browser) {
    try { await bounded(browser.close(), 30000, 'Browser close'); }
    catch (error) {
      report.cleanupError = error.message;
      report.status = 'failed';
      process.exitCode = 1;
      console.error(error.message);
    }
  }
  if (server) {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
  if (report.cleanupError) {
    await writeFile(reportPath, JSON.stringify(report, null, 2) + '\n');
    process.exit(1);
  }
}
