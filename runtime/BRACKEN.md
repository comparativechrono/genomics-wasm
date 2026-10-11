# Bracken runtime

`BrackenRunner` runs Bracken release 3.1's unchanged Python programs in fresh Pyodide workers and its actual C++ distribution helper in ordinary WebAssembly workers. Node 20+ and browsers with WebAssembly, ES modules and workers are supported. HTTPS or localhost hosting is sufficient; no SharedArrayBuffer or cross-origin isolation headers are required. No remote Python package service is used.

```js
import { BrackenRunner } from './runtime/bracken.mjs';
const runner = new BrackenRunner({
  baseUrl: new URL('./dist/bracken/', import.meta.url),
  kraken2BaseUrl: new URL('./dist/kraken2/', import.meta.url),
});
const result = await runner.run([
  'bracken', '-d', 'db', '-i', 'sample.report', '-o', 'abundance.tsv',
  '-w', 'redistributed.report', '-r', '151', '-l', 'S', '-t', '10',
], {
  files: [
    { path: 'sample.report', data: krakenReportBytes },
    { path: 'db/database151mers.kmer_distrib', data: distributionBytes },
  ],
  outputs: ['abundance.tsv', 'redistributed.report'],
  timeoutMs: 120000,
  signal: abortController.signal,
});
```

`files`, `outputs`, `outputDirectories`, `stdin`, cancellation and timeout follow `ToolRunner`. Paths are relative to `/work`. Results contain byte stdout/stderr, `exitCode`, `files`, and `missingOutputs`. Caller buffers are preserved. High-level commands additionally expose `workflow.stages`, recording actual commands, exit statuses, input SHA-256 hashes, output hashes and missing outputs. All successful stages run the bundled upstream algorithms. A single deadline covers an entire workflow, including intermediate transfers and hashing. Abort and timeout reject with `AbortError` and `TimeoutError`; program failures return their status and requested partial files. Only explicitly requested outputs/directories are returned.

`baseUrl` defaults to `dist/bracken/`. `kraken2BaseUrl` defaults to its sibling `dist/kraken2/`; the Kraken2 runner is imported only when generating fresh whole-reference classifications. A custom Python `workerUrl` may be supplied. Each Python invocation loads only the local Pyodide core and the selected upstream script, verifying its SHA-256. The bundle includes Node's required `ws` dependency. Keep the `pyodide/`, `python/` and `node_modules/` directories intact.

Supported entry points:

- `['bracken', ...]`, or unprefixed Bracken flags: `-d`, `-i`, `-o`, `-w`, `-r`, `-l`, `-t`, `-v`, `-h`.
- `['build', ...]` or `['bracken-build', ...]`: `-d`, `-k`, `-l`, `-t 1`, `-y kraken2`, `-v`, `-h`.
- `['est_abundance.py', ...]`, `['generate_kmer_distribution.py', ...]`: unchanged Python CLI.
- `['kmer2read_distr', ...]`: compiled helper CLI, one thread only.

To generate a distribution locally, upload a compatible Kraken2 database (`hash.k2d`, `opts.k2d`, `taxo.k2d`), `seqid2taxid.map`, `taxonomy/nodes.dmp`, and the database's original FASTA sequences beneath `library/`. Run `['build', '-d', 'db', '-k', '35', '-l', '151', '-t', '1']` and request `db/database.kraken`, `db/database151mers.kraken`, and `db/database151mers.kmer_distrib`. The workflow classifies library FASTA with actual Kraken2, invokes `kmer2read_distr`, then runs unchanged `generate_kmer_distribution.py`. As upstream, it reuses a nonempty `database.kraken` (or `database.kraken.tsv`) if supplied. Those cached assignments must match the database. FASTA files retain the caller's input order. Library downloads, legacy Kraken/KrakenUniq formats and host executable directories (`-x`) are not supported.

The estimate defaults are read length 100, rank S and threshold 10. Without `-w`, upstream creates a report beside the input using its rank label, for example `sample_bracken_species.report`; request that path to retrieve it. Upstream release 3.1 still prints shell version 3.0.1; both identities are preserved. Some upstream shell validation errors mistakenly exit zero; the JavaScript frontend reports these as status 2. Unsupported options fail explicitly.

The helper port makes OpenMP scheduling serial, fixes a diagnostic printf type mismatch, and checks a trailing-newline EOF boundary. Upstream native helper can otherwise emit one nonexistent final record with a NUL sequence ID and an undefined taxid. Native validation retains that raw output and permits only this precisely identified upstream artifact; all genuine intermediate records and final distributions must agree. Missing delimiters and unknown sequence IDs now produce an explicit helper error. Empty input yields an empty intermediate; a valid last record without a trailing newline is retained. No abundance or redistribution formula was translated or changed.

The high-level orchestration in `bracken.mjs` derives from Bracken's GPL-3.0-or-later shell workflow, copyright 2016–2023 Jennifer Lu. The unchanged upstream Python and C++ sources carry the same license. Pyodide and linked library notices and pinned source archives are included in `dist/bracken/notices/` and `dist/bracken/sources/`. The helper has a 2 GiB WebAssembly memory limit; runtime, Python and transferred files consume additional host memory. This is intended for datasets and databases that fit available memory.
