/** Internal worker implementation. Public callers should use ToolRunner. */
export async function execute({ moduleUrl, waitForExit = false, args, files, outputs, outputDirectories = [], stdin }) {
  const factory = (await import(moduleUrl)).default;
  if (typeof factory !== 'function') throw new TypeError('Tool module must export an Emscripten factory as its default export');
  const stdout = new ByteCollector();
  const stderr = new ByteCollector();
  let inputOffset = 0;
  let exitCode = 0;
  let prepared = false;
  let finishExit;
  const exitEvent = new Promise(resolve => { finishExit = resolve; });
  const module = await factory({
    noInitialRun: true,
    print: (...values) => stdout.writeText(values.join(' ') + '\n'),
    printErr: (...values) => stderr.writeText(values.join(' ') + '\n'),
    onExit: status => { exitCode = status; finishExit({ status }); },
    onAbort: reason => finishExit({ error: new WebAssembly.RuntimeError(`Tool aborted: ${reason}`) }),
    // Emscripten's Node default must not terminate the worker before files are read.
    quit: (status, exception) => { exitCode = status; throw exception; },
    preRun: [instance => {
      const { FS } = instance;
      if (!FS) throw new Error('Tool build must export FS');
      FS.init(() => inputOffset < stdin.length ? stdin[inputOffset++] : null, byte => stdout.write(byte), byte => stderr.write(byte));
      FS.mkdirTree('/work');
      FS.chdir('/work');
      for (const file of files) {
        FS.mkdirTree(file.path.slice(0, file.path.lastIndexOf('/')));
        FS.writeFile(file.path, file.data);
      }
      prepared = true;
    }],
  });
  if (!prepared) throw new Error('Tool build did not run its preRun hooks');
  if (typeof module.callMain !== 'function') throw new Error('Tool build must export callMain');
  try {
    const result = module.callMain(args);
    if (Number.isInteger(result)) exitCode = result;
  } catch (error) {
    if (error?.name === 'ExitStatus' && Number.isInteger(error.status)) exitCode = error.status;
    else throw error;
  }
  // PROXY_TO_PTHREAD returns from callMain after starting the application.
  // The main runtime worker must keep serving filesystem proxy requests until
  // the application actually exits, before output files can be collected.
  if (waitForExit) {
    const outcome = await exitEvent;
    if (outcome.error) throw outcome.error;
    exitCode = outcome.status;
  }
  const resultFiles = [];
  const missingOutputs = [];
  const collected = new Set();
  const collectFile = path => {
    if (collected.has(path)) return;
    resultFiles.push({ path, data: module.FS.readFile(path) });
    collected.add(path);
  };
  for (const path of outputs) {
    if (!module.FS.analyzePath(path).exists) missingOutputs.push(path);
    else collectFile(path);
  }
  const collectDirectory = path => {
    const { FS } = module;
    if (!FS.isDir(FS.lstat(path).mode)) throw new Error(`Output directory is not a directory: ${path}`);
    for (const name of FS.readdir(path).filter(name => name !== '.' && name !== '..').sort()) {
      const child = `${path}/${name}`;
      const mode = FS.lstat(child).mode;
      if (FS.isDir(mode)) collectDirectory(child);
      else if (FS.isFile(mode)) collectFile(child);
      else throw new Error(`Output directory contains a non-regular file: ${child}`);
    }
  };
  for (const path of outputDirectories) {
    if (!module.FS.analyzePath(path).exists) missingOutputs.push(path);
    else collectDirectory(path);
  }
  return { exitCode, stdout: stdout.finish(), stderr: stderr.finish(), files: resultFiles, missingOutputs };
}

class ByteCollector {
  constructor() { this.chunks = []; this.chunk = new Uint8Array(65536); this.used = 0; this.length = 0; }
  writeText(value) { for (const byte of new TextEncoder().encode(value)) this.write(byte); }
  write(byte) {
    // Emscripten uses null/undefined to flush. A byte of zero is real output.
    if (byte === null || byte === undefined) return;
    if (this.used === this.chunk.length) { this.chunks.push(this.chunk); this.chunk = new Uint8Array(65536); this.used = 0; }
    this.chunk[this.used++] = byte;
    this.length++;
  }
  finish() {
    const result = new Uint8Array(this.length);
    let offset = 0;
    for (const chunk of this.chunks) { result.set(chunk, offset); offset += chunk.length; }
    result.set(this.chunk.subarray(0, this.used), offset);
    return result;
  }
}
