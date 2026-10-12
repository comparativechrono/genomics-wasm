# HMMER JavaScript API

`HmmerRunner` executes the original HMMER 3.4 programs in fresh workers. Select
the executable as the first argument, followed by its native command options.
The serial build uses ordinary static hosting without cross-origin isolation.

```js
import { HmmerRunner } from './runtime/hmmer.mjs';
const runner = new HmmerRunner({
  baseUrl: new URL('./dist/hmmer/', import.meta.url),
});
const built = await runner.run([
  'hmmbuild', '--seed', '42', 'family.hmm', 'family.sto',
], {
  files: [{ path: 'family.sto', data: alignmentBytes }],
  outputs: ['family.hmm'],
  timeoutMs: 600000,
});
if (built.exitCode !== 0 || built.missingOutputs.length) throw new Error('Model build failed');
const searched = await runner.run([
  'hmmsearch', '--seed', '42', '--tblout', 'hits.tsv',
  '--domtblout', 'domains.tsv', 'family.hmm', 'proteins.faa',
], {
  files: [...built.files, { path: 'proteins.faa', data: proteinBytes }],
  outputs: ['hits.tsv', 'domains.tsv'],
  timeoutMs: 600000,
});
```

The supported offline commands are `hmmbuild`, `hmmsearch`, `hmmscan`,
`hmmpress`, `hmmfetch`, `hmmalign`, `hmmconvert`, `hmmemit`, `hmmstat`,
`phmmer`, `jackhmmer`, `nhmmer` and `nhmmscan`. Each command's `-h` option
prints its original help. An unknown program name is an API `RangeError`;
the selected upstream program validates its own flags and reports its own
exit status. The serial configuration does not expose `--cpu`, MPI or daemon
services. Omit `--cpu`; no threads are started by this build.

For scanning, build the profile collection, then run `hmmpress collection.hmm`
and collect `collection.hmm.h3f`, `.h3i`, `.h3m` and `.h3p`. Mount all four
files together with `collection.hmm` and your query sequence for `hmmscan`.
Files persist only when explicitly carried into the next call. Request outputs
with `outputs` or recursively collect `outputDirectories`; the wrapper does
not infer output paths from flags. Native-style `-` stdin arguments can use
the runner's `stdin` byte input when that command supports streaming.

The API returns `{ exitCode, stdout, stderr, files, missingOutputs }`.
Standard streams and file contents are byte arrays; returned paths start with
`/work/`. Inputs accept strings, `Uint8Array` or `ArrayBuffer`. Use a fixed
`--seed` on commands that accept it for reproducible comparisons; model and
search options retain their original HMMER meanings. A FASTA file of proteins
predicted by Prodigal is a valid input to appropriate protein search commands.

Pass `signal` for cancellation and `timeoutMs` for a runtime bound. Each call
has its own worker and heap. The compiled maximum heap is 2 GiB and input,
index and output copies also consume memory. Begin with individual families
or compact profile collections; validation does not establish whole-Pfam or
large-proteome performance. See [port notes](../tools/hmmer/README.md) and
[comparison rules](../validation/annotation/README.md) for build details,
file interoperability and the recorded native-equivalence scope.
