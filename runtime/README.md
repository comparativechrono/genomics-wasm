# JavaScript runner

The same interface runs a compiled tool in a browser or Node.js. It has no runtime package dependencies. Copy `runtime/` and the compiled directory for the tool you need. The JavaScript module and its `.wasm` file must remain together. A browser deployment needs HTTP hosting; opening HTML with `file://` does not work.

```js
import { ToolRunner } from './runtime/index.mjs';

const bwa = new ToolRunner({
  moduleUrl: new URL('./dist/bwa/bwa.mjs', import.meta.url),
});

const reference = new Uint8Array(await referenceFile.arrayBuffer());
const suffixes = ['amb', 'ann', 'bwt', 'pac', 'sa'];
const index = await bwa.run(['index', 'reference.fa'], {
  files: [{ path: 'reference.fa', data: reference }],
  outputs: suffixes.map(suffix => `reference.fa.${suffix}`),
  timeoutMs: 120000,
});

if (index.exitCode !== 0 || index.missingOutputs.length) {
  throw new Error(new TextDecoder().decode(index.stderr));
}

const alignment = await bwa.run(['mem', 'reference.fa', 'reads.fq'], {
  files: [
    { path: 'reference.fa', data: reference },
    { path: 'reads.fq', data: new Uint8Array(await readsFile.arrayBuffer()) },
    ...index.files,
  ],
});

// stdout is raw bytes: save alignment.stdout directly as SAM.
console.log(alignment.exitCode, new TextDecoder().decode(alignment.stderr));
```

In Node.js, use `await readFile(path)` from `node:fs/promises` in place of the browser File reads. The module URL must use the `file:` scheme in Node.js; `new URL(..., import.meta.url)` supplies that when used from an ES module on disk.

## Interface

`new ToolRunner({moduleUrl})` accepts an absolute URL string or URL object pointing to the Emscripten ES module. An optional `workerUrl` locates `runtime/worker.mjs` if your deployment puts it elsewhere.

`run(args, options)` takes an array of argument strings, without the program name. These strings are passed directly to the tool; quotes, wildcard expansion, pipes and redirection are not interpreted as shell syntax.

Options:

- `files`: an array of `{path, data}` objects. Data can be a `Uint8Array`, `ArrayBuffer` or UTF-8 string. Relative paths resolve under `/work`; absolute paths under `/work/` also work. Parent traversal is rejected. Caller buffers are not detached.
- `outputs`: paths to retrieve after the command exits. Only these files are returned; request any indexes or other generated files that you want to keep.
- `stdin`: bytes or a UTF-8 string; defaults to immediate end-of-file.
- `signal`: an AbortSignal for cancellation.
- `timeoutMs`: optional elapsed-time limit, including startup; zero means no limit.

The promise resolves to `{exitCode, stdout, stderr, files, missingOutputs}`. The two streams and each returned file's `data` are `Uint8Array` objects, including when they contain binary data or lack a final newline. Returned file paths are absolute under `/work`. A nonzero tool exit is a result, not a rejected promise. Missing requested output files are listed in `missingOutputs`, so callers can distinguish successful output from absent files. Module-loading errors and runtime traps reject the promise. Cancellation and timeouts reject with `AbortError` and `TimeoutError` respectively and terminate the worker.

Each command runs in a new worker with a fresh module and filesystem. Results from one command must be supplied as inputs to the next. Concurrent calls use independent workers. This avoids C/C++ global state leaking between runs, but input files and module memory are copied for each command. Use small inputs first, and avoid launching many memory-intensive commands at once.

## Build contract

The module must default-export an Emscripten asynchronous factory with `MODULARIZE=1`, `EXPORT_ES6=1`, `INVOKE_RUN=0`, `EXIT_RUNTIME=1`, and runtime exports `FS` and `callMain`. It must support `web`, `worker` and `node` environments. Preserve the incoming module API hooks `preRun`, `noInitialRun`, `onExit` and `quit`. The runner initializes FS streams in `preRun`, before libc starts, so stdout and stderr retain their exact bytes. It does not depend on line-oriented `print` callbacks.

## Tests

`node --test tests/runtime.test.mjs` checks worker isolation, binary I/O, literal arguments, file transfer, failed commands and termination. Its fixture is deliberately a small mock module. These tests validate the runner contract only; native-versus-WebAssembly tool tests provide the separate evidence for genomic results.
