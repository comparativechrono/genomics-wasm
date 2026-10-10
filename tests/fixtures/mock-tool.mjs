// Runtime contract fixture; this is not a genomics implementation.
let invocations = 0;

export default async function createMockTool(options) {
  const storage = new Map();
  let input;
  let output;
  let errorOutput;
  const FS = {
    init(read, write, error) { input = read; output = write; errorOutput = error; },
    mkdirTree() {},
    chdir() {},
    writeFile(path, data) { storage.set(path, Uint8Array.from(data)); },
    readFile(path) {
      if (!storage.has(path)) throw new Error(`Missing fixture file: ${path}`);
      return Uint8Array.from(storage.get(path));
    },
    analyzePath(path) { return { exists: storage.has(path) }; },
  };
  const module = {
    FS,
    callMain(args) {
      invocations++;
      if (args[0] === 'hang') while (true) { /* Worker termination must stop synchronous computation. */ }
      if (args[0] === 'trap') throw new WebAssembly.RuntimeError('Fixture trap');
      if (args[0] === 'count') { output(invocations); return 0; }
      if (args[0] === 'exit') {
        errorOutput(255);
        const exception = Object.assign(new Error('Fixture exit'), { name: 'ExitStatus', status: 7 });
        options.onExit(7);
        options.quit(7, exception);
      }
      if (args[0] === 'large') {
        for (let i = 0; i < 150001; i++) output(i % 256);
        return 0;
      }
      if (args[0] === 'args') {
        for (const byte of new TextEncoder().encode(JSON.stringify(args.slice(1)))) output(byte);
        return 0;
      }
      for (let byte; (byte = input()) !== null;) output(byte);
      output(null);
      errorOutput(0);
      errorOutput(128);
      errorOutput(255);
      FS.writeFile('/work/nested/output.bin', FS.readFile('/work/nested/input.bin'));
      return 0;
    },
  };
  for (const callback of options.preRun) callback(module);
  return module;
}
