# Cutadapt JavaScript API

`CutadaptRunner` runs the bundled Cutadapt in a new Python/WebAssembly worker for
each command. It uses the same arguments as the native command and the same file
and result format as `ToolRunner`. It does not parse shell commands.

```js
import { CutadaptRunner } from './runtime/cutadapt.mjs';

const runner = new CutadaptRunner({
  baseUrl: new URL('./dist/cutadapt/', import.meta.url),
});
const result = await runner.run([
  '-j', '1', '-a', 'AGATCGGAAGAGCACACGTCTGAACTCCAGTCA',
  '--json', 'report.json', '-o', 'trimmed.fastq', 'reads.fastq',
], {
  files: [{ path: 'reads.fastq', data: fastqBytes }],
  outputs: ['trimmed.fastq', 'report.json'],
  timeoutMs: 300000,
});

if (result.exitCode !== 0 || result.missingOutputs.length) {
  throw new Error(new TextDecoder().decode(result.stderr));
}
const trimmed = result.files.find(file => file.path === '/work/trimmed.fastq');
```

`files` accepts strings, `Uint8Array` or `ArrayBuffer` values. Paths must be
relative to the command's working directory or start with `/work/`. Parent
directory components and paths outside `/work/` are rejected by the JavaScript
file interface. Add `outputDirectories: ['directory']` to retrieve all regular
files under an output directory, including files created by demultiplexing.
Requested files that were not created appear in `missingOutputs`.

`stdout` and `stderr` are byte arrays; the runner does not add or remove newlines.
Pass `stdin: fastqBytes` and use `-` as the input filename to read standard input.
Using `-o -` returns the FASTQ in `result.stdout`. Arguments are passed literally:
pipes, glob patterns and shell redirection have no special meaning.

Pass an `AbortSignal` as `signal` to cancel. Cancellation and timeouts terminate
the worker and discard its Python state. A later command starts with a new
interpreter. The caller keeps its input buffers and decides which successful
outputs to supply to subsequent commands. Commands that fail to start or raise
an uncaught runtime error reject the promise. Normal Cutadapt errors return a
nonzero `exitCode` and diagnostics.

## Hosting and dependencies

Keep the complete `dist/cutadapt/` directory, including its manifest, Pyodide
files, wheels and bundled Node dependency. All analysis runs locally. Runtime
files are loaded from that directory; no package index or CDN is needed.
The browser package works on ordinary static hosting without cross-origin
isolation or a service worker. Serve JavaScript and WebAssembly with their
normal MIME types. Browser pages must be served over HTTP(S), rather than opened
as `file:` URLs. Node can load the package directly from the local filesystem.

The build uses one processing thread. Omit `-j` or use `-j 1`; automatic and
multiple-core modes are rejected explicitly. Inputs, outputs and the Python
runtime occupy memory in the worker, with additional copies in the caller.
The small bundled datasets do not establish capacity for whole sequencing runs.
See `tools/cutadapt/README.md` for build details and supported compression formats.

The runner preserves Cutadapt's algorithms and invokes `cutadapt.cli.main`.
The Cython extensions are compiled to WebAssembly. Version checks, wheel
checksums and validation reports identify the included software. The comparison
rules and exact test commands are in `validation/cutadapt/`.
