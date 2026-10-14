/** Run the unmodified Linux seqtk executable inside container2wasm/QEMU.
 * Each call owns a fresh Worker and an in-memory filesystem. No input is uploaded.
 */
export const MAX_INPUT_BYTES = 20 * 1024 * 1024;
export const MAX_OUTPUT_BYTES = 64 * 1024 * 1024;
const DEFAULT_TIMEOUT_MS = 120_000;
const RESERVED_PATHS = new Set(['argv.txt', 'stdout', 'stderr', 'started', 'status.json', 'status.json.tmp']);

function namedError(name, message) {
  const error = new Error(message);
  error.name = name;
  return error;
}

/** Validate the narrow prototype's input contract before creating a VM. */
export function validateRun(args, files = []) {
  if (!Array.isArray(args) || args.length > 128 || args.some(arg => typeof arg !== 'string' || /[\r\n\0]/.test(arg))) {
    throw new TypeError('seqtk arguments must be an array of at most 128 strings without NUL or line breaks.');
  }
  if (args.reduce((size, arg) => size + new TextEncoder().encode(arg).length + 1, 0) > 64 * 1024) {
    throw new RangeError('The seqtk argument list exceeds 64 KiB.');
  }
  if (!Array.isArray(files) || files.length > 32) throw new TypeError('Provide at most 32 input files.');
  const seen = new Set();
  let inputBytes = 0;
  const normalizedFiles = files.map(file => {
    if (!file || typeof file.path !== 'string' || !(file.data instanceof Uint8Array)) {
      throw new TypeError('Each input needs a path and Uint8Array data.');
    }
    const relativePath = file.path.startsWith('/work/') ? file.path.slice(6) : file.path;
    const parts = relativePath.split('/');
    if (!relativePath || relativePath.length > 240 || /[\x00-\x1f\x7f\\]/.test(relativePath) ||
        parts.some(part => !part || part === '.' || part === '..') || RESERVED_PATHS.has(parts[0])) {
      throw new TypeError('Input paths must be relative paths beneath /work and cannot use runtime-reserved names.');
    }
    const path = `/work/${relativePath}`;
    if (seen.has(path)) throw new TypeError(`Duplicate input path: ${path}`);
    seen.add(path);
    inputBytes += file.data.byteLength;
    return {path, data: file.data};
  });
  if (inputBytes > MAX_INPUT_BYTES) throw new RangeError('This prototype accepts at most 20 MiB of input per run.');
  return {args: [...args], files: normalizedFiles, inputBytes};
}

export class SeqtkContainerRunner {
  constructor({assetBaseURL = new URL('../dist/seqtk-container/runtime/', import.meta.url)} = {}) {
    this.assetBaseURL = new URL(assetBaseURL, import.meta.url).href;
    if (!this.assetBaseURL.endsWith('/')) this.assetBaseURL += '/';
    this.active = null;
  }

  /** Callbacks receive {stream, text} and {phase, message, ...}, respectively. */
  run(args, {files = [], timeoutMs = DEFAULT_TIMEOUT_MS, signal, onLog, onProgress} = {}) {
    if (this.active) return Promise.reject(new Error('This runner already has an active job.'));
    let request;
    try {
      request = validateRun(args, files);
      if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new RangeError('timeoutMs must be a positive finite number.');
      if (signal?.aborted) throw namedError('AbortError', 'Run cancelled.');
      if (!globalThis.isSecureContext || !globalThis.crossOriginIsolated || typeof SharedArrayBuffer === 'undefined') {
        throw new Error('container2wasm needs HTTPS (or localhost) and cross-origin isolation. Serve COOP: same-origin and COEP: require-corp headers.');
      }
      if (typeof Worker === 'undefined') throw new Error('This browser does not support Web Workers.');
    } catch (error) {
      return Promise.reject(error);
    }

    return new Promise((resolve, reject) => {
      let worker;
      let timer;
      let cleanupTimer;
      let completeCleanup;
      let settled = false;
      const stopWorker = done => {
        if (!worker) { done(); return; }
        // QEMU owns nested pthread workers. Let the responsive outer worker stop
        // them explicitly before terminating it; also bound cleanup if loading
        // a classic script or a browser/runtime fault blocks that event loop.
        completeCleanup = () => {
          if (!completeCleanup) return;
          completeCleanup = null;
          clearTimeout(cleanupTimer);
          worker.terminate();
          done();
        };
        cleanupTimer = setTimeout(() => completeCleanup?.(), 250);
        try { worker.postMessage({type: 'stop'}); } catch { completeCleanup?.(); }
      };
      const finish = (error, result) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        signal?.removeEventListener('abort', abort);
        // Settle after cleanup so an awaited cancellation/retry cannot overlap
        // two live QEMU instances and their large shared-memory reservations.
        stopWorker(() => {
          this.active = null;
          if (error) reject(error); else resolve(result);
        });
      };
      const abort = () => finish(namedError('AbortError', 'Run cancelled.'));
      this.active = {cancel: abort};
      try {
        worker = new Worker(new URL('./container2wasm-seqtk-worker.js', import.meta.url), {name: 'seqtk-container'});
        worker.onmessage = ({data}) => {
          if (data?.type === 'stopped') { completeCleanup?.(); return; }
          if (settled || !data) return;
          try {
            if (data.type === 'log') { onLog?.(data.value); return; }
            if (data.type === 'progress') { onProgress?.(data.value); return; }
            if (data.type === 'error') { finish(namedError(data.name || 'Error', data.message)); return; }
            if (data.type === 'result') {
              finish(null, {...data.result, stdout: new Uint8Array(data.result.stdout), stderr: new Uint8Array(data.result.stderr)});
            }
          } catch (error) {
            finish(error);
          }
        };
        worker.onerror = event => finish(new Error(event.message || 'The container worker failed.'));
        worker.onmessageerror = () => finish(new Error('The container worker returned an unreadable message.'));
        signal?.addEventListener('abort', abort, {once: true});
        timer = setTimeout(() => finish(namedError('TimeoutError', `Run exceeded ${Math.round(timeoutMs / 1000)} seconds and was stopped.`)), timeoutMs);
        // Structured cloning deliberately preserves the caller's input buffer for comparisons/reruns.
        worker.postMessage({type: 'run', ...request, assetBaseURL: this.assetBaseURL, maxOutputBytes: MAX_OUTPUT_BYTES});
      } catch (error) {
        finish(error);
      }
    });
  }

  cancel() { this.active?.cancel(); }
  dispose() { this.cancel(); }
}
