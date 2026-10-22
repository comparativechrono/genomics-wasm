# KING 2.3.2 JavaScript API

This runner uses KING 2.3.2 compiled locally for personal use. KING's source
headers restrict redistribution of modified versions; this integration does
not supply a distributable KING binary or source archive. See the
[build instructions and licensing notes](../tools/king/README.md).

```js
import { KingRunner } from './runtime/king.mjs';

const runner = new KingRunner();
const result = await runner.run(
  ['-b', 'cohort.bed', '--kinship', '--prefix', 'king'],
  {
    files: [
      { path: 'cohort.bed', data: bedBytes },
      { path: 'cohort.bim', data: bimBytes },
      { path: 'cohort.fam', data: famBytes },
    ],
    outputs: ['king.kin', 'king.kin0'],
    timeoutMs: 300000,
  },
);

if (result.exitCode !== 0) throw new Error(new TextDecoder().decode(result.stderr));
console.log(new TextDecoder().decode(result.stdout));
for (const { path, data } of result.files) console.log(path, data.byteLength);
```

`files[].data` accepts `Uint8Array`, `ArrayBuffer`, or text. Use bytes for
BED input. The files are copied into an isolated `/work` filesystem; caller
buffers remain intact. Paths must be relative POSIX paths, or `/work/…`
paths, without empty components, `.` or `..`. Each entry in the argument
array is passed literally to KING: shell expansion, pipes and redirects are
not interpreted.

`stdout`, `stderr` and each returned file's `data` are `Uint8Array` values.
`exitCode` is KING's status; `missingOutputs` lists requested files that were
not produced. Output filenames vary by command. For example, `--kinship`
can write `king.kin` and `king.kin0`, while other commands append suffixes
without a dot. Check the status and requested outputs before using results.
The runner returns any requested files even on a nonzero exit so callers can
inspect diagnostic files; the demo presents analysis results only after a
successful command.

The optional `outputDirectories` array recursively collects a result folder.
KING does not create output-prefix parent folders. Create one by supplying
an empty marker file, then exclude that marker from the returned results:

```js
const result = await runner.run(
  ['-b', 'cohort.bed', '--duplicate', '--prefix', 'results/king'],
  {
    files: [...genotypeFiles, { path: 'results/.directory', data: '' }],
    outputDirectories: ['results'],
    timeoutMs: 300000,
  },
);
const outputs = result.files.filter(file => file.path !== '/work/results/.directory');
```

Use `signal: controller.signal` with an `AbortController` to cancel a run.
Cancellation and timeouts terminate the worker and discard its filesystem
and heap. They reject with `AbortError` or `TimeoutError`. Other errors,
including a WebAssembly trap, also reject. The same runner can then execute
another command. Every invocation creates a new worker, including repeated
and concurrent calls, so KING's process-global state is never reused.

The default module is `dist/king/2.3.2/king.mjs`, resolved relative to this
runtime module. `new KingRunner({ baseUrl })` accepts a `file:`, `http:` or
`https:` directory URL ending in `/`; `moduleUrl` and `workerUrl` can override
individual assets. Keep `king.wasm` next to `king.mjs`.

Serve the checkout with ordinary static HTTP and correct MIME types for
`.mjs` (`text/javascript`) and `.wasm` (`application/wasm`). This serial build
does not require cross-origin isolation, `SharedArrayBuffer`, or a service
worker. Node 20 or later can run the same API. Browsers require HTTP module
and worker loading; opening the demo directly as a `file:` URL does not work.

The original KING CLI algorithms run inside WebAssembly. The port preserves
the 64-bit genotype-word algorithms on wasm32, enables zlib, and uses the
serial path. External R/`ps2pdf` plotting options are unavailable. Memory,
runtime and algorithm coverage are limited by the actual
[validation cases](../validation/king/README.md); a successful small example
does not establish whole-biobank capacity.
