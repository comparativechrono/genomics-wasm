# Prodigal JavaScript API

`ProdigalRunner` executes the original Prodigal 2.6.3 program in a fresh worker.
It accepts the native command-line arguments and uses the shared file/byte API.
No shell, external server computation or cross-origin isolation is required.

```js
import { ProdigalRunner } from './runtime/prodigal.mjs';
const runner = new ProdigalRunner({
  baseUrl: new URL('./dist/prodigal/', import.meta.url),
});
const result = await runner.run([
  '-i', 'genome.fna', '-p', 'single', '-f', 'gff',
  '-o', 'genes.gff', '-a', 'proteins.faa', '-d', 'genes.fna',
], {
  files: [{ path: 'genome.fna', data: genomeBytes }],
  outputs: ['genes.gff', 'proteins.faa', 'genes.fna'],
  timeoutMs: 600000,
});
if (result.exitCode !== 0 || result.missingOutputs.length) {
  throw new Error(new TextDecoder().decode(result.stderr));
}
```

The `single` mode trains on the supplied genome; upstream requires at least
20,000 bases and recommends substantially more. Use `-p meta` for metagenomic
contigs. Native options include closed ends (`-c`), masking (`-m`), translation
tables (`-g`), start-site output (`-s`), saved training (`-t`) and output format
(`-f`). `-h` prints the original upstream help. A `-t` path can be an input or
an output depending on whether that file already exists; explicitly mount or
request it as appropriate.

Inputs may be strings, `Uint8Array` or `ArrayBuffer`. An explicitly supplied
`stdin` is materialized as an internal, seekable file when `-i`/`-I` is absent,
because Prodigal rewinds sequence input for prediction after training. Input
bytes are preserved, explicit `-i` takes precedence, and the transport file is
not returned as an output. Gzip decoding is not added by this wrapper: provide
the uncompressed FASTA, GenBank or EMBL input accepted by the upstream program.

Return values are `{ exitCode, stdout, stderr, files, missingOutputs }` with
byte arrays and `/work/`-prefixed file paths. Request each generated file via
`outputs`, or recursively collect a directory with `outputDirectories`.
Returned files can be mounted in a later invocation, including in HMMER.
Every call receives fresh memory; retain desired files in your application.

Pass an `AbortSignal` as `signal` to cancel a command. `timeoutMs` bounds the
entire invocation; cancellation and timeout terminate the worker. The maximum
compiled heap is 2 GiB; mounted inputs and outputs consume additional memory.
The real fixture checks native equivalence on small bacterial sequence regions,
not performance on entire large metagenomes. See [port notes](../tools/prodigal/README.md).
