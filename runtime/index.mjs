/** Run one command in an isolated worker. No shell parsing or shared tool state. */
export class ToolRunner {
  constructor({ moduleUrl, workerUrl = new URL('./worker.mjs', import.meta.url), waitForExit = false } = {}) {
    if (typeof waitForExit !== 'boolean') throw new TypeError('waitForExit must be a boolean');
    this.moduleUrl = absoluteUrl(moduleUrl, 'moduleUrl');
    this.workerUrl = absoluteUrl(workerUrl, 'workerUrl');
    this.waitForExit = waitForExit;
  }

  async run(args = [], { files = [], outputs = [], outputDirectories = [], stdin = new Uint8Array(), signal, timeoutMs = 0 } = {}) {
    if (!Array.isArray(args) || args.some(arg => typeof arg !== 'string' || arg.includes('\0'))) {
      throw new TypeError('args must be an array of strings without NUL characters');
    }
    if (!Array.isArray(files) || !Array.isArray(outputs) || !Array.isArray(outputDirectories)) throw new TypeError('files, outputs and outputDirectories must be arrays');
    if (!Number.isFinite(timeoutMs) || timeoutMs < 0) throw new TypeError('timeoutMs must be a non-negative number');
    if (signal?.aborted) throw abortError();
    const inputFiles = files.map(file => ({ path: workspacePath(file.path), data: bytes(file.data) }));
    if (new Set(inputFiles.map(file => file.path)).size !== inputFiles.length) throw new TypeError('Input file paths must be unique');
    const requestedOutputs = [...new Set(outputs.map(workspacePath))];
    const requestedDirectories = [...new Set(outputDirectories.map(workspacePath))];
    const input = bytes(stdin);

    return new Promise((resolve, reject) => {
      let worker;
      let timer;
      let settled = false;
      const finish = (error, result) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        signal?.removeEventListener('abort', onAbort);
        // Termination also disposes the WebAssembly heap and module global state.
        const terminated = worker?.terminate();
        if (terminated?.catch) terminated.catch(() => {});
        if (error) reject(error);
        else resolve(result);
      };
      const onAbort = () => finish(abortError());
      signal?.addEventListener('abort', onAbort, { once: true });
      if (signal?.aborted) return finish(abortError());
      if (timeoutMs) timer = setTimeout(() => {
        const error = new Error(`Command exceeded ${timeoutMs} ms`);
        error.name = 'TimeoutError';
        finish(error);
      }, timeoutMs);
      const onMessage = message => {
        if (message.type === 'result') finish(null, message.result);
        else if (message.type === 'error') {
          const error = new Error(message.error.message);
          error.name = message.error.name || 'Error';
          if (message.error.stack) error.stack = message.error.stack;
          finish(error);
        }
      };
      const start = async () => {
        if (typeof process !== 'undefined' && process.versions?.node) {
          const { Worker } = await import('node:worker_threads');
          if (settled) return;
          // CLI flags such as --input-type and process-only V8 flags are not
          // valid for workers. The runner does not require inherited flags.
          worker = new Worker(new URL(this.workerUrl), { type: 'module', execArgv: [] });
          worker.on('message', onMessage);
          worker.on('error', error => finish(error));
          worker.on('exit', code => {
            if (!settled) finish(new Error(`Tool worker exited without a result (status ${code})`));
          });
        } else {
          worker = new Worker(this.workerUrl, { type: 'module' });
          worker.addEventListener('message', event => onMessage(event.data));
          worker.addEventListener('error', event => finish(new Error(event.message || 'Tool worker failed')));
          worker.addEventListener('messageerror', () => finish(new Error('Could not read tool worker response')));
        }
        // Structured cloning leaves caller-owned input buffers intact.
        worker.postMessage({ moduleUrl: this.moduleUrl, waitForExit: this.waitForExit, args, files: inputFiles, outputs: requestedOutputs, outputDirectories: requestedDirectories, stdin: input });
      };
      start().catch(error => finish(error));
    });
  }
}

function absoluteUrl(value, label) {
  let url;
  try { url = new URL(value); } catch { throw new TypeError(`${label} must be an absolute URL or URL object`); }
  if (!['file:', 'http:', 'https:'].includes(url.protocol)) throw new TypeError(`${label} must use file, http or https`);
  return url.href;
}

export function workspacePath(value) {
  if (typeof value !== 'string' || !value || value.includes('\0') || value.includes('\\')) throw new TypeError('File paths must be non-empty POSIX paths');
  const relative = value.startsWith('/work/') ? value.slice(6) : value;
  const parts = relative.split('/');
  if (relative.startsWith('/') || parts.some(part => !part || part === '.' || part === '..')) throw new TypeError('File paths must be inside /work without empty, . or .. components');
  return `/work/${relative}`;
}

function bytes(value) {
  if (typeof value === 'string') return new TextEncoder().encode(value);
  if (value instanceof Uint8Array) return value;
  if (value instanceof ArrayBuffer) return new Uint8Array(value);
  throw new TypeError('File data and stdin must be a string, Uint8Array or ArrayBuffer');
}

function abortError() {
  const error = new Error('Command cancelled');
  error.name = 'AbortError';
  return error;
}
