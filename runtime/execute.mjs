/** Internal worker implementation. Public callers should use ToolRunner. */
export async function execute({ moduleUrl, args, files, outputs, stdin }) {
  const factory = (await import(moduleUrl)).default;
  if (typeof factory !== 'function') throw new TypeError('Tool module must export an Emscripten factory as its default export');
  const stdout = new ByteCollector();
  const stderr = new ByteCollector();
  let inputOffset = 0;
  let exitCode = 0;
  let prepared = false;
  const module = await factory({
    noInitialRun: true,
    onExit: status => { exitCode = status; },
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
  const resultFiles = [];
  const missingOutputs = [];
  for (const path of outputs) {
    if (!module.FS.analyzePath(path).exists) missingOutputs.push(path);
    else resultFiles.push({ path, data: module.FS.readFile(path) });
  }
  return { exitCode, stdout: stdout.finish(), stderr: stderr.finish(), files: resultFiles, missingOutputs };
}

class ByteCollector {
  constructor() { this.chunks = []; this.chunk = new Uint8Array(65536); this.used = 0; this.length = 0; }
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
