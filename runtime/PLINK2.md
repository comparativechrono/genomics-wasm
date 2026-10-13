# PLINK 2 JavaScript API

`Plink2Runner` runs the pinned upstream PLINK 2 CLI in a fresh worker for each
command. Its `NOLAPACK` build retains upstream conversion, QC, LD, relationship,
scoring and regression implementations. `--pca` (including approximate PCA)
and `--pheno-svd` are unavailable. Consult the [tool notes](../tools/plink2/README.md)
and [validation scope](../validation/plink2/README.md) for tested operations.

```js
import { Plink2Runner } from './runtime/plink2.mjs';

const plink2 = new Plink2Runner({
  baseUrl: new URL('./dist/plink2/', import.meta.url),
});
const result = await plink2.run([
  '--vcf', 'input.vcf.gz',
  '--double-id',
  '--freq',
  '--out', 'cohort',
], {
  files: [{ path: 'input.vcf.gz', data: vcfBytes }],
  outputs: ['cohort.afreq', 'cohort.log'],
  timeoutMs: 120000,
});
if (result.exitCode !== 0) {
  throw new Error(new TextDecoder().decode(result.stderr));
}
// result.files contains byte-preserving outputs, including the frequency table
// and PLINK log. Pass selected files as inputs to a subsequent command.
```

Create output directories through input paths or use an output prefix in the
existing virtual working directory. The runner's virtual filesystem starts
fresh for each invocation; it does not retain a cohort between commands.
For example, `--make-pgen --out cohort` creates `cohort.pgen`, `cohort.pvar`
and `cohort.psam`; supply all three to a subsequent `--pfile cohort` command.

## Resources

The helper defaults to `--threads 1 --memory 640 require`. It accepts one or two
compute threads and an integer working-arena size from 640 to 1024 MiB.
Explicit values within those bounds are preserved. Argument files (`--script`
and `--rerun`) are rejected because they could hide resource settings.
Help and version requests do not need allocation settings.

These limits describe the supported execution profile, not a guarantee that
every input fits. The WebAssembly heap limit is 2 GiB. The arena, thread stacks,
other allocations and files consume additional memory, and the host also holds
copies of inputs and returned outputs. Run commands sequentially and start with
small datasets. The fixture tests are not a whole-cohort scalability benchmark.

PLINK 2 uses genuine reader threads even with one compute thread. The helper
always sets `waitForExit: true` so output collection waits for the application
thread to finish. This pinned Emscripten build starts pthreads by loading
`plink2.mjs` again as an ES-module worker. Keep it beside `plink2.wasm`.
The packaged `plink2.worker.mjs` is an unused compatibility placeholder.

## Hosting

Serve over HTTPS or localhost with these response headers:

```text
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Embedder-Policy: require-corp
```

The browser must report `crossOriginIsolated === true` and support shared
WebAssembly memory and SIMD. The [example](../examples/plink2/README.md) includes
hosting instructions. Static hosting without cross-origin isolation cannot run
this threaded build. Node.js does not need HTTP hosting headers.

## Files, cancellation and errors

All generic [ToolRunner options](README.md) apply: byte-preserving `files`,
`outputs`, `outputDirectories`, `stdin`, `signal` and `timeoutMs`. Arguments are
literal strings, with no shell expansion. Paths are confined to the virtual
working filesystem. Every invocation has independent state.

A normal PLINK error resolves with a nonzero `exitCode`; inspect stdout,
stderr and the log for its explanation. Invalid helper arguments reject before
starting a worker. Cancellation and timeouts terminate the worker and reject
with `AbortError` and `TimeoutError`. No computation is sent to a server.

The program uses upstream numerical implementations with a documented
WebAssembly floating-point adaptation. Native/browser comparison results apply
to the recorded fixtures and commands, not to every PLINK option or dataset.
