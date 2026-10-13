# Primer3 JavaScript API

`Primer3Runner` runs the pinned original Primer3 2.6.1 command-line programs
in a disposable worker. It shares the `ToolRunner` contract in
`runtime/README.md`; no scientific calculations are reimplemented in JavaScript.

```js
import { Primer3Runner, PRIMER3_PROGRAMS } from './runtime/primer3.mjs';

const design = new Primer3Runner(); // primer3_core
const result = await design.run([], {
  stdin: boulderInput, // records end with a line containing only "="
  timeoutMs: 60_000,
});
console.log(result.exitCode, new TextDecoder().decode(result.stdout));

const tm = new Primer3Runner({ program: 'oligotm' });
const value = await tm.run(['ACGTACGTACGTACGTACGT']);
```

`program` accepts the names in `PRIMER3_PROGRAMS`: `primer3_core`, `ntdpal`,
`ntthal`, `oligotm`, `amplicon3_core`, `long_seq_tm_test`, and `primer3_masker`.
Each module lives at `dist/primer3/<program>.mjs`, beside its corresponding
`.wasm`. `baseUrl` selects another absolute directory URL and `moduleUrl`
can override the module for integrations. Each invocation gets fresh memory
and files; keep all runtime modules beside `runtime/index.mjs` and its worker.

Pass original CLI arguments as an array. Use `stdin` for Boulder IO, or mount
inputs through `files: [{ path: 'input.txt', data }]` and name that file in the
arguments. `data` and `stdin` accept strings, `Uint8Array`, or `ArrayBuffer`.
Files are rooted at `/work`. Capture generated files using `outputs` or
`outputDirectories`; stdout and stderr are returned as bytes, including the
original final newline. `AbortSignal` and `timeoutMs` terminate the worker.

The companion programs retain their upstream calling conventions:
`amplicon3_core` accepts a DNA sequence longer than 36 bases as its final
argument, while `long_seq_tm_test` requires `SEQUENCE START LENGTH`.
The latter is an upstream test utility with no help switch and does not
validate missing positional arguments. Its valid invocation can be useful
for reproducing long-sequence Tm calculations. Native operating systems
report an upstream `exit(-1)` as 255; the worker preserves the signed exit
status returned by Emscripten, so check for zero when determining success.

Upstream embeds its default thermodynamic parameters in the code; normal
primer design and `ntthal` need no separately loaded configuration. The full
original tables are also distributed at `dist/primer3/primer3_config/` for
explicit user-supplied configurations. To use those, mount their bytes under
one workspace directory and set `PRIMER_THERMODYNAMIC_PARAMETERS_PATH` in
Boulder IO, or use `ntthal -path`, to point at that directory. Overrides are
handled by the original Primer3 parser without rewriting the input records.

The masker code and CLI are included. Genome-scale k-mer frequency indices
are not automatically provided or mounted. Supply compatible binary indices
and their paths explicitly; the original `mmap` reader runs through MEMFS
emulation and therefore adds to the memory requirement. The upstream source
archive contains its original test files, not a browser-ready genome database.

The masker's original FASTA reader also seeks backward over input headers.
For `primer3_masker`, explicitly supplied `stdin` is copied byte-for-byte into
a private seekable input file when the command has no named input. This has
the semantics of native shell input redirection (`primer3_masker ... < input.fa`),
not a pipe: the upstream reader cannot correctly seek through a pipe either.
A named CLI input takes precedence, and help or malformed commands retain
the original parser behavior. The temporary transport file is not returned
as output. A literal `-` is not an upstream masker stdin alias; omit the input
filename and supply the API's `stdin` option instead.

These modules use one worker and do not require cross-origin isolation,
SharedArrayBuffer, or pthread support. Host them with JavaScript and WASM MIME
types over HTTP(S), or run the same API under Node. Inputs and results are
copied in memory; the wasm32 heap starts at 64 MiB and may grow to 2 GiB, and
browser memory limits can be lower. Do not infer whole-genome capacity from
the tested small primer-design workloads. See `validation/primer3/README.md`
for the exact cases and numerical comparison policy.
