/* Classic Worker: Emscripten's generated load.js and arg-module.js are classic
 * scripts; out.js exports the ES module factory. Keep all three on one origin.
 * Integration is pinned to container2wasm v0.8.4 / xterm-pty 0.10.1's PTY ABI.
 */
'use strict';

const POLL_MS = 100;
const LOG_LIMIT = 256 * 1024;
let running = false;
let finished = false;
let pollTimer;
let moduleInstance;
let request;
let startedAt;
let initializedAt;
let guestStartedAt;
let stderrOffset = 0;
let runtimeLogBytes = 0;
const stderrDecoder = new TextDecoder();
const runtimeDecoder = new TextDecoder();
const childWorkers = new Set();
const BrowserWorker = self.Worker;
// Emscripten's PThread manager creates all pthread workers on this runtime
// thread. Track them so cancellation does not rely on orphan-worker collection.
self.Worker = class extends BrowserWorker {
  constructor(...args) { super(...args); childWorkers.add(this); }
  terminate() { childWorkers.delete(this); return super.terminate(); }
};

function stopChildren() {
  clearInterval(pollTimer);
  for (const worker of childWorkers) worker.terminate();
}

function send(type, value) { self.postMessage({type, value}); }
function progress(phase, message, extra = {}) { send('progress', {phase, message, ...extra}); }
function fail(error) {
  if (finished) return;
  finished = true;
  stopChildren();
  self.postMessage({type: 'error', name: error?.name || 'Error', message: String(error?.message || error)});
}

// Noninteractive, output-only implementation of the exact PTY interface used by
// the upstream Emscripten library. stdin is never used for file data or commands.
function createPty() {
  let termios = {iflag: 0, oflag: 0, cflag: 0xbf, lflag: 0, cc: Array(32).fill(0)};
  return {
    readable: false,
    writable: true,
    read: () => [],
    onReadable: () => ({dispose() {}}),
    onSignal: () => ({dispose() {}}),
    write(bytes) {
      if (finished || runtimeLogBytes >= LOG_LIMIT) return;
      const data = typeof bytes === 'string' ? new TextEncoder().encode(bytes) : Uint8Array.from(bytes);
      const visible = data.subarray(0, LOG_LIMIT - runtimeLogBytes);
      runtimeLogBytes += visible.length;
      const text = runtimeDecoder.decode(visible, {stream: true});
      if (text) send('log', {stream: 'runtime', text});
      if (runtimeLogBytes === LOG_LIMIT) send('log', {stream: 'runtime', text: '\n[Runtime log truncated at 256 KiB.]\n'});
    },
    ioctl(operation, value) {
      if (operation === 'TCGETS') return {...termios, cc: [...termios.cc]};
      if (operation === 'TCSETS') { termios = {...value, cc: [...value.cc]}; return; }
      if (operation === 'TIOCGWINSZ') return [80, 24];
      throw new Error(`Unsupported PTY operation: ${operation}`);
    },
  };
}

function sizeOf(FS, path) {
  try { return FS.stat(path).size; } catch (error) {
    if (error.errno === 44) return null; // Emscripten ENOENT
    throw error;
  }
}

function appendStderr(FS, size) {
  const amount = Math.min(size - stderrOffset, LOG_LIMIT - stderrOffset);
  if (amount <= 0) return;
  const handle = FS.open('/work/stderr', 'r');
  const data = new Uint8Array(amount);
  let count;
  try { count = FS.read(handle, data, 0, amount, stderrOffset); } finally { FS.close(handle); }
  stderrOffset += count;
  const text = stderrDecoder.decode(data.subarray(0, count), {stream: true});
  if (text) send('log', {stream: 'stderr', text});
  if (stderrOffset === LOG_LIMIT) send('log', {stream: 'stderr', text: '\n[Live stderr truncated at 256 KiB; complete stderr remains available in the result.]\n'});
}

function inspectFiles() {
  if (finished || !moduleInstance?.FS) return false;
  const FS = moduleInstance.FS;
  const now = performance.now();
  // The bridge files already exist to avoid upstream's Emscripten-to-Linux
  // ENOENT mismatch. The wrapper truncates the one-byte startup sentinel.
  if (!guestStartedAt && sizeOf(FS, '/work/started') === 0) {
    guestStartedAt = now;
    progress('running', 'Linux container started; seqtk is running.', {inputBytes: request.inputBytes});
  }
  const stdoutSize = sizeOf(FS, '/work/stdout') || 0;
  const stderrSize = sizeOf(FS, '/work/stderr') || 0;
  if (stdoutSize + stderrSize > request.maxOutputBytes) {
    throw new Error('Output exceeded the prototype limit of 64 MiB.');
  }
  appendStderr(FS, stderrSize);
  const statusSize = sizeOf(FS, '/work/status.json');
  if (statusSize === null || statusSize === 0) return false;
  if (statusSize > 1024) throw new Error('Container returned an invalid completion record.');
  const status = JSON.parse(FS.readFile('/work/status.json', {encoding: 'utf8'}));
  if (!Number.isInteger(status.exitCode) || status.exitCode < 0 || status.exitCode > 255) {
    throw new Error('Container returned an invalid seqtk exit code.');
  }
  const stdout = FS.readFile('/work/stdout');
  const stderr = FS.readFile('/work/stderr');
  const remainingStderr = stderrDecoder.decode();
  if (remainingStderr) send('log', {stream: 'stderr', text: remainingStderr});
  const metrics = {
    totalMs: now - startedAt,
    loadMs: initializedAt ? initializedAt - startedAt : null,
    bootMs: guestStartedAt && initializedAt ? Math.max(0, guestStartedAt - initializedAt) : null,
    executionMs: guestStartedAt ? Math.max(0, now - guestStartedAt) : null,
    inputBytes: request.inputBytes,
    outputBytes: stdout.byteLength,
    wasmMemoryBytes: moduleInstance.HEAPU8?.buffer.byteLength ?? null,
    timingResolutionMs: POLL_MS,
  };
  finished = true;
  stopChildren();
  progress('complete', `seqtk exited with status ${status.exitCode}.`, {outputBytes: stdout.byteLength});
  self.postMessage({type: 'result', result: {exitCode: status.exitCode, stdout: stdout.buffer, stderr: stderr.buffer, metrics}}, [stdout.buffer, stderr.buffer]);
  return true;
}

async function start(message) {
  request = message;
  startedAt = performance.now();
  const assetBase = new URL(message.assetBaseURL);
  const asset = name => new URL(name, assetBase).href;
  if (assetBase.origin !== self.location.origin) throw new Error('Runtime assets must be served from the same origin as the prototype.');
  progress('loading', 'Downloading and initializing the Linux container runtime.');

  const pty = createPty();
  self.Module = {
    preRun: [],
    pty,
    locateFile: path => asset(path),
    mainScriptUrlOrBlob: asset('out.js'),
    print: text => pty.write(`${text}\n`),
    printErr: text => pty.write(`${text}\n`),
    onAbort: reason => fail(new Error(`Container runtime aborted: ${reason}`)),
    onRuntimeInitialized() {
      initializedAt = performance.now();
      progress('booting', 'Starting the emulated Linux container.');
    },
    onExit(code) {
      try {
        if (!inspectFiles()) fail(new Error(`The virtual machine exited (${code}) before seqtk wrote its completion record. Check the runtime log.`));
      } catch (error) { fail(error); }
    },
  };

  try { importScripts(asset('load.js'), asset('arg-module.js')); }
  catch (error) { throw new Error(`Cannot load the container assets. Build dist/seqtk-container/runtime first and serve it over HTTP(S). ${error.message}`); }
  const options = self.Module;
  if (!Array.isArray(options.arguments) || options.arguments.some(arg => typeof arg !== 'string') ||
      !options.arguments.includes('-kernel') || !Array.isArray(options.preRun)) {
    throw new Error('The generated runtime configuration is incompatible. Rebuild with the pinned container2wasm version.');
  }
  // Keep the NIC device present for the upstream migration snapshot, but attach
  // it only to an isolated QEMU hub. No websocket or external network is opened.
  const netdevIndex = options.arguments.indexOf('-netdev');
  if (netdevIndex >= 0) {
    if (!options.arguments[netdevIndex + 1]?.includes('id=vmnic')) throw new Error('Unexpected generated QEMU network configuration.');
    options.arguments[netdevIndex + 1] = 'hubport,id=vmnic,hubid=0';
  }
  options.preRun.push(mod => {
    moduleInstance = mod;
    const FS = mod.FS;
    FS.mkdirTree('/work');
    for (const file of message.files) {
      FS.mkdirTree(file.path.slice(0, file.path.lastIndexOf('/')));
      FS.writeFile(file.path, file.data);
    }
    // MEMFS now owns its copy; release the worker's cloned input buffers.
    message.files = [];
    FS.writeFile('/work/argv.txt', message.args.length ? `${message.args.join('\n')}\n` : '');
    // QEMU's pinned 9p backend passes Emscripten ENOENT (44) to Linux unchanged
    // (Linux expects 2), so the guest cannot reliably create a missing file.
    // Existing files can be opened/truncated; rename replaces the empty status
    // file atomically. Keep every bridge path present before the guest boots.
    for (const name of ['stdout', 'stderr', 'started', 'status.json.tmp', 'status.json']) {
      FS.writeFile(`/work/${name}`, new Uint8Array());
    }
    FS.writeFile('/work/started', new Uint8Array([1]));
    FS.mkdirTree('/pack');
    FS.writeFile('/pack/info', `t: ${Math.floor(Date.now() / 1000)}\nm: /work\ne: /usr/local/bin/run-seqtk\n`);
    // QEMU's asynchronous poll extension passes a notification callback. There
    // is no stdin for this batch runner, so it is never readable.
    mod.TTY.stream_ops.poll = () => 0;
    pollTimer = setInterval(() => { try { inspectFiles(); } catch (error) { fail(error); } }, POLL_MS);
  });
  const {default: initEmscriptenModule} = await import(asset('out.js'));
  moduleInstance = await initEmscriptenModule(options);
}

self.onmessage = ({data}) => {
  if (data?.type === 'stop') {
    finished = true;
    stopChildren();
    self.postMessage({type: 'stopped'});
    self.close();
    return;
  }
  if (running || data?.type !== 'run') return;
  running = true;
  start(data).catch(fail);
};
self.addEventListener('unhandledrejection', event => fail(event.reason));
