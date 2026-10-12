# bustools JavaScript API

`BustoolsRunner` runs the original bustools 0.45.1 command-line program in an
isolated worker. BUS processing, barcode correction, UMI handling and matrix
generation use the upstream C++ implementation. Ordinary static hosting is
sufficient; no computation service or cross-origin isolation is needed.

```js
import { BustoolsRunner } from './runtime/bustools.mjs';

const runner = new BustoolsRunner({
  baseUrl: new URL('./dist/bustools/', import.meta.url),
});
const sorted = await runner.run([
  'sort', '-t', '1', '-m', '64M', '-o', 'sorted.bus', 'input.bus',
], {
  files: [{ path: 'input.bus', data: busBytes }],
  outputs: ['sorted.bus'],
  timeoutMs: 600000,
});
if (sorted.exitCode !== 0 || sorted.missingOutputs.length) {
  throw new Error(new TextDecoder().decode(sorted.stderr));
}
```

Arguments are passed literally, without shell expansion. Use a subcommand as
the first argument, such as `sort`, `correct`, `count`, `text`, `inspect` or
`compress`. Running a recognized subcommand without arguments prints its native
usage; `version` prints the source version. Upstream's unknown-subcommand path
can return status zero while printing an error, so check the expected outputs
as well as `exitCode` when running a workflow.

The default API paths are relative to this runtime module. An explicit
`baseUrl` must be an absolute file/HTTP/HTTPS directory URL ending in `/`.
`moduleUrl` and `workerUrl` overrides support custom asset layouts.

## Files and streams

Inputs accept strings, `Uint8Array` and `ArrayBuffer`. Use byte arrays for BUS,
BUSZ and compressed files; decoding binary data as text corrupts it. Each
invocation receives its own virtual `/work` directory. Relative paths and
`/work/`-prefixed paths identify the same files; parent traversal and paths
outside the workspace are rejected when mounting or requesting files.

The result is `{ exitCode, stdout, stderr, files, missingOutputs }`. Streams
and returned file data are byte arrays. Explicitly list desired files in
`outputs`, or collect all files beneath named directories with
`outputDirectories`. Output prefixes must have any required parent directories
available to the command; the upstream program controls which files it creates.
No output filename is inferred by the runner.

For commands that support streaming, pass the input bytes as `stdin`, use `-`
as the native input filename and use `-p` for native standard output. `stdout`
may then be a binary BUS file. A JavaScript pipeline carries these bytes into
the next invocation rather than using shell pipes. Sorting with `-p` also
requires the upstream `-T` temporary-file prefix; choose a workspace path.

## Barcode and count workflow

A typical gene-count workflow is:

1. Sort the input BUS records.
2. Run `correct -w allowlist.txt -o corrected.bus sorted.bus` with a suitable
   technology-specific barcode allowlist.
3. Sort the corrected BUS records again.
4. Run `count --genecounts -g transcripts_to_genes.txt -e matrix.ec
   -t transcripts.txt -o counts corrected-sorted.bus`.

Mount the BUS file returned by each stage into the following stage, along
with that stage's supporting files. The final command creates `counts.mtx`,
`counts.barcodes.txt` and `counts.genes.txt`. Keep the labels together with the
matrix: they define its barcode rows and gene columns. Equivalence-class IDs,
transcript names and the gene map must describe the same kallisto reference
that produced the input BUS file. The runner does not fetch references or
generate a replacement mapping.

An output file from one invocation can be mounted directly in the next:

```js
const corrected = await runner.run([
  'correct', '-w', 'allowlist.txt', '-o', 'corrected.bus', 'sorted.bus',
], {
  files: [...sorted.files, { path: 'allowlist.txt', data: allowlistBytes }],
  outputs: ['corrected.bus'],
});
```

Check each stage's status and missing outputs before continuing. Caller-owned
input buffers remain intact; retain returned files explicitly because worker
memory and its filesystem are discarded at the end of each command.

## Resource limits and cancellation

This port runs serially. The sort buffer defaults to 64 MiB, the upstream
minimum, instead of the native default of 4 GiB. Use an explicit suitable
`-m` value up to 1 GiB when needed and request one sort thread. Larger buffers
and requests for multiple sort threads are rejected. Temporary sort runs use
the in-memory filesystem; a smaller sort buffer does not make arbitrarily
large input files fit in a browser. The compiled heap can grow to 2 GiB, and
JavaScript input/output copies consume additional memory.

Pass an `AbortSignal` as `signal` to cancel a command. `timeoutMs` bounds an
invocation. Cancellation and timeout terminate the worker, including synchronous
native code, and later invocations start with fresh state. Runtime traps and
module-load errors reject the promise; ordinary upstream failures return their
exit status and diagnostics.

See the [port notes](../tools/bustools/README.md),
[browser example](../examples/bustools/README.md) and
[comparison rules](../validation/bustools/README.md) for supported commands,
portability changes, fixture provenance and exact validation coverage.
