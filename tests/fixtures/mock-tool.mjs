// Runtime contract fixture; this is not a genomics implementation.
let invocations = 0;

export default async function createMockTool(options) {
  const storage = new Map();
  const directories = new Set(['/']);
  const links = new Set();
  let input;
  let output;
  let errorOutput;
  const FS = {
    init(read, write, error) { input = read; output = write; errorOutput = error; },
    mkdirTree(path) {
      const parts = path.split('/').filter(Boolean);
      for (let i = 1; i <= parts.length; i++) directories.add('/' + parts.slice(0, i).join('/'));
    },
    chdir() {},
    writeFile(path, data) { storage.set(path, Uint8Array.from(data)); },
    readFile(path) {
      if (!storage.has(path)) throw new Error(`Missing fixture file: ${path}`);
      return Uint8Array.from(storage.get(path));
    },
    analyzePath(path) { return { exists: storage.has(path) || directories.has(path) || links.has(path) }; },
    lstat(path) {
      if (storage.has(path)) return { mode: 'file' };
      if (directories.has(path)) return { mode: 'directory' };
      if (links.has(path)) return { mode: 'link' };
      throw new Error(`Missing fixture path: ${path}`);
    },
    isDir(mode) { return mode === 'directory'; },
    isFile(mode) { return mode === 'file'; },
    readdir(path) {
      const prefix = path + '/';
      const names = [...storage.keys(), ...directories, ...links]
        .filter(name => name.startsWith(prefix) && !name.slice(prefix.length).includes('/'))
        .map(name => name.slice(prefix.length));
      return ['.', '..', ...new Set(names)];
    },
  };
  const module = {
    FS,
    callMain(args) {
      invocations++;
      if (args[0] === 'hang') while (true) { /* Worker termination must stop synchronous computation. */ }
      if (args[0] === 'trap') throw new WebAssembly.RuntimeError('Fixture trap');
      if (args[0] === 'async-exit') {
        setTimeout(() => {
          output(42);
          options.printErr('Runtime diagnostic');
          FS.writeFile('/work/late.bin', Uint8Array.from([1, 2, 3]));
          options.onExit(7);
        }, 30);
        return 0;
      }
      if (args[0] === 'async-abort') {
        setTimeout(() => options.onAbort('Fixture abort'), 30);
        return 0;
      }
      if (args[0] === 'async-hang') {
        setInterval(() => {}, 1000);
        return 0;
      }
      if (args[0] === 'count') { output(invocations); return 0; }
      if (args[0] === 'directory-output') {
        FS.mkdirTree('/work/index/nested');
        FS.mkdirTree('/work/index/empty');
        FS.writeFile('/work/index/nested/data.bin', Uint8Array.from([0, 128, 255]));
        FS.writeFile('/work/index/.metadata', Uint8Array.from([123, 125]));
        if (args[1] === 'link') links.add('/work/index/linked');
        return 0;
      }
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
