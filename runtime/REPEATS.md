# ExpansionHunter and REViewer JavaScript API

`RepeatRunner` runs ExpansionHunter 5.0.0, REViewer 0.2.7 and the supporting
SAMtools 1.21 build in Node.js or a browser. Each command uses the upstream
command-line arguments in a fresh worker and filesystem.

```js
import { RepeatRunner } from './runtime/repeats.mjs';

const caller = new RepeatRunner({ tool: 'expansionhunter' });
const called = await caller.run([
  '--reads', 'sample.bam', '--reference', 'reference.fa',
  '--variant-catalog', 'catalog.json', '--output-prefix', 'sample',
  '--threads', '1',
], {
  files: inputs, // [{ path, data }], including the BAM/CRAM and reference indexes
  outputs: ['sample.json', 'sample.vcf', 'sample_realigned.bam'],
  timeoutMs: 300_000,
});
if (called.exitCode !== 0 || called.missingOutputs.length) {
  throw new Error(new TextDecoder().decode(called.stderr));
}
```

Use `tool: 'samtools'` to sort and index `sample_realigned.bam` before using it
with `tool: 'reviewer'`. The command sequence is:

```js
['sort', '-o', 'sample.sorted.bam', 'sample_realigned.bam']
['index', 'sample.sorted.bam']
['--reads', 'sample.sorted.bam', '--vcf', 'sample.vcf',
 '--reference', 'reference.fa', '--catalog', 'catalog.json',
 '--locus', 'LOCUS_ID', '--output-prefix', 'plot']
```

For each command, mount its required inputs through `files` and retain its
named outputs. REViewer produces `plot.LOCUS_ID.svg`, `plot.metrics.tsv` and
`plot.phasing.tsv`. Use the same reference, catalog and region extension
length as the ExpansionHunter call. A BAMlet is a subset of realigned reads;
it still needs coordinate sorting and an index. Starting a new worker does
not preserve any files automatically—carry the returned bytes into the next
command's `files` array.

REViewer 0.2.7 supports repeat plots but has upstream limitations for catalogs
that combine repeats with small variants. The demo uses repeat-only loci;
ExpansionHunter's broader catalog support does not imply that every call can
be plotted by this REViewer release.

The constructor accepts an optional `baseUrl` for the directory containing
`expansionhunter/`, `reviewer/` and `hts-1.21/`, and an optional `workerUrl`.
`REPEAT_VERSIONS` exports the fixed version assigned to each program. This
runner does not select alternative upstream versions.

`run(args, options)` uses the standard `ToolRunner` contract:

| Option | Meaning |
| --- | --- |
| `files` | Inputs as `{ path, data }`, where data is a string, `Uint8Array` or `ArrayBuffer`. |
| `outputs` | Exact output paths to collect. |
| `outputDirectories` | Existing directories whose files are collected recursively. This does not create directories. |
| `stdin` | Text or bytes for a command that accepts standard input. |
| `signal` | An `AbortSignal` that terminates the command's worker. |
| `timeoutMs` | Deadline covering loading and execution; `0` disables it. |

The result contains `exitCode`, `stdout`, `stderr`, `files` and
`missingOutputs`. Streams and files retain their exact bytes. Check both the
exit status and `missingOutputs`. Inputs remain usable by the caller.

Paths are local to `/work`; shell pipelines, redirection and quoting are not
interpreted. Output-prefix parent directories must already exist. Use a flat
prefix unless you have supplied inputs that create the required directory.
References must be local, including those needed for CRAM decoding.

These builds use serial execution; ExpansionHunter requires `--threads 1`.
They support ordinary static HTTP hosting, without
SharedArrayBuffer or cross-origin isolation. Browser input copies, outputs
and the WASM heap all consume memory. The small validation fixtures establish
correctness for their recorded cases, not whole-genome capacity or throughput.
See the individual build documents for upstream limitations and the scoped
portability changes.

For browser display, load an SVG output through an `<img>` with a Blob URL.
Do not insert uploaded or generated SVG text into the page with `innerHTML`.
The [demo](../examples/repeats/README.md) provides file uploads, chaining,
plots, downloads, cancellation and timeouts.
