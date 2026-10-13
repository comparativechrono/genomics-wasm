# MACS3 JavaScript API

`MACS3Runner` exposes the complete MACS3 3.0.5 command-line program, compiled
with its Cython/C extensions and scientific dependencies for Pyodide. It uses
the same file-oriented API as `ToolRunner`, in Node.js and browsers.

```js
import { MACS3Runner } from './runtime/macs3.mjs';

const runner = new MACS3Runner({
  baseUrl: new URL('./dist/macs3/', import.meta.url),
});
const controller = new AbortController();
const result = await runner.run([
  'callpeak', '-t', 'chip.bed', '-c', 'control.bed', '-f', 'BED',
  '-g', '51000000', '--nomodel', '--extsize', '150',
  '-n', 'example', '--outdir', 'results',
], {
  files: [
    { path: 'chip.bed', data: chipBytes },
    { path: 'control.bed', data: controlBytes },
  ],
  outputDirectories: ['results'],
  signal: controller.signal,
  timeoutMs: 300_000,
});
console.log(result.exitCode);
console.log(new TextDecoder().decode(result.stderr));
for (const file of result.files) console.log(file.path, file.data.length);
```

The numerical settings above are only an API example. Choose parameters for
your experiment and effective genome size.

The constructor accepts `baseUrl` for the directory containing `manifest.json`
and optional `workerUrl`. Defaults resolve relative to the installed runtime
module, not the calling page. `baseUrl` can be an absolute URL or a URL string
resolved against that module. Keep `runtime/macs3.mjs`,
`runtime/macs3-worker.mjs`, `runtime/macs3-execute.mjs` and `runtime/index.mjs`
with their relative paths intact.

`run(args, options)` accepts an array of strings without `macs3`. All 14
upstream subcommands are available: `callpeak`, `bdgpeakcall`, `bdgbroadcall`,
`bdgcmp`, `bdgopt`, `cmbreps`, `randsample`, `filterdup`, `bdgdiff`,
`refinepeak`, `predictd`, `pileup`, `hmmratac` and `callvar`. Use
`run(['--help'])` or `run(['hmmratac', '--help'])` for the pinned version's full
options. No shell parsing, redirection or pipelines occur.

| Option | Meaning |
| --- | --- |
| `files` | Array of `{ path, data }`. Data is a string, `Uint8Array` or `ArrayBuffer`. |
| `outputs` | Exact output filenames to return, including existing input files if desired. |
| `outputDirectories` | Output directories to collect recursively. Files requested twice are returned once. |
| `stdin` | Standard-input bytes or text; empty by default. |
| `signal` | Optional `AbortSignal`; cancellation rejects with `AbortError`. |
| `timeoutMs` | Overall startup and execution deadline in milliseconds; `0` disables it. A timeout rejects with `TimeoutError`. |

Paths are relative to `/work`, or explicit paths below `/work/`. Empty path
components, `.`/`..`, backslashes and paths outside this directory are rejected.
Input paths must be unique. The runner creates parent directories for inputs;
use MACS3 `--outdir` to create an output directory. Requested files and
directories must be regular files and directories, not symbolic links.

The result contains `exitCode`, byte arrays `stdout` and `stderr`,
`files: [{ path, data }]` with absolute `/work/...` paths, and
`missingOutputs` listing requested paths that do not exist. MACS3 progress
messages and many estimates are emitted on stderr. Nonzero CLI exits resolve
with their status and captured streams; uncaught Python exceptions return
status 1 and a traceback. A missing requested output is reported separately,
so callers should check both `exitCode` and `missingOutputs`. Available partial
outputs are returned even when the command fails. Invalid arguments to the
JavaScript API, damaged packages and worker failures reject the promise.

Each invocation creates a fresh worker and Python environment. Completion,
cancellation and timeout terminate that worker and release its WebAssembly
heap. No virtual files persist between calls; pass previously returned `files`
as inputs to reuse them. Caller-owned input buffers are not detached. The
browser example retains successful files in page memory to implement chaining.

The bundle includes Python and the pinned NumPy, SciPy, scikit-learn,
hmmlearn, pandas, AnnData and transitive dependencies. The runner verifies all
package and entry-script SHA-256 hashes, requires the stock Pyodide dependency
closure, and checks installed package and Python versions. It loads packages
only from the bundle. Scientific libraries load in a single worker;
`callvar` uses the serial worker implementation of upstream peak processing,
including its native fermi-lite assembly and scoring. On WebAssembly a
requested process count is executed serially because process creation is not
available. It does not create shared-memory threads.

During package installation, the worker compiles and instantiates WebAssembly
side modules synchronously. This avoids an observed WebKit failure in which an
asynchronous side-module instantiation promise remains pending. The override
is limited to the serial package loader and restored before MACS3 executes,
including when installation fails. It preserves the package bytes, imports and
scientific algorithms; compilation runs in the worker so the page stays
responsive and cancellation can still terminate the worker.

Serve the runtime, bundle and your page from an ordinary HTTP(S) static server.
Use HTTPS for remote hosting; HTTP on localhost also supports the WebCrypto
checksum verification. Cross-origin isolation headers are not required. Opening a browser demo as a
`file://` page is unsupported; Node.js may load assets from `file:` URLs.
All inputs, intermediate arrays and collected outputs occupy memory. Browser
WebAssembly memory and practical device limits make whole-genome production
workloads substantially different from the small validation fixtures; there is
no automatic disk-backed streaming or performance guarantee for large inputs.

See [build instructions](../tools/macs3/README.md),
[the demo](../examples/macs3/README.md) and
[validation coverage](../validation/macs3/README.md).
