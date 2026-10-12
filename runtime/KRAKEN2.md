# Kraken2 in a browser or Node

`Kraken2Runner` runs the original compiled Kraken2 programs in fresh workers. Serve the package over HTTP or HTTPS, or import it from Node. No cross-origin isolation is required. Release tag 2.17.2 retains upstream's version string 2.17.1.

```js
import { Kraken2Runner } from './runtime/kraken2.mjs';
const kraken2 = new Kraken2Runner();
const result = await kraken2.run([
  '--db', 'db', '--paired', '--report', 'report.tsv',
  'reads_1.fastq', 'reads_2.fastq',
], { files });
```

`files` is an array of `{ path, data }` records containing `db/hash.k2d`, `db/taxo.k2d`, `db/opts.k2d` and the reads. Data can be a string, Uint8Array or ArrayBuffer. Input paths are relative to the command's virtual workspace. Use `--db .` or `--db /work` when the three database files are mounted directly at the workspace root. A filename with spaces is one argument; shell commands, pipes and globs are not parsed.

The result has `exitCode`, byte arrays `stdout` and `stderr`, `files`, and `missingOutputs`, as in `ToolRunner`. Named classification, report and sequence outputs are collected automatically. Additional `outputs` and `outputDirectories` may be requested. `signal` cancels the command; `timeoutMs` limits the entire operation, including decompression and multiple database-building stages. Caller-owned input bytes are preserved.

Classification supports the ordinary upstream options `--db`, `--threads 1`, `--paired`, `--quick`, `--confidence`, `--minimum-hit-groups`, `--minimum-base-quality`, `--use-names`, `--report`, `--report-zero-counts`, `--report-minimizer-data`, `--use-mpa-style`, `--output`, `--classified-out`, `--unclassified-out`, `--memory-mapping` and `--gzip-compressed`. Gzip is detected from the first input's magic bytes as in the native wrapper. Paired sequence-output filenames require exactly one `#`, replaced by `_1` and `_2`. `--output -` suppresses classification text; a dash supplied to other output options names an ordinary file. An input filename `-` reads the `stdin` option, including gzip bytes. Unsupported options fail visibly with status 64.

Inspect a database with `['inspect', '--db', 'db']`. Inspection also accepts `--threads 1`, `--skip-counts`, `--use-mpa-style` and `--report-zero-counts`. The wrapper directs the original helper's report to `/dev/stdout`, the browser filesystem equivalent of upstream's `/dev/fd/1`, so both summary and taxon rows reach stdout.

For a local database, supply `db/library/reference.fna`, `db/seqid2taxid.map`, `db/taxonomy/nodes.dmp` and `db/taxonomy/names.dmp`, then run:

```js
const built = await kraken2.run([
  'build', '--db', 'db', '--threads', '1', '--no-masking',
], { files: preparedReferenceFiles });
```

The result includes the three database files and a `stages` ledger identifying the actual `estimate_capacity` and `build_db` calls. Their algorithms are unchanged. The wrapper reproduces upstream's capacity adjustment of 8192 minimizers and its default load factor of 0.7. Existing `hash.k2d` skips rebuilding as in the native script, after estimating capacity. This path still collects requested files and directories and lists absent database files in `missingOutputs`; check that list before using a previously interrupted build. To build another database, mount prepared inputs under a different prefix, such as `built-db/`.

Build options include `--kmer-len`, `--minimizer-len`, `--minimizer-spaces`, `--load-factor`, `--max-db-size`, `--block-size`, `--subblock-size`, `--minimum-bits-for-taxid`, `--protein` and `--fast-build`. Use one combined FASTA for reproducible library order across host filesystems; multiple supplied library FASTAs are concatenated in input order. Files ending in `.fna` or `.faa` are included. No network download, accession-map generation or masking workflow is implied; prepare the map and taxonomy explicitly.

Direct helpers use `['classify', ...nativeShortOptions]`, `['build_db', ...]`, `['estimate_capacity', ...]`, `['dump_table', ...]` or `['lookup_accession_numbers', ...]`, with their original CLI. Gzip decompression is a high-level-wrapper feature; direct helpers receive uncompressed bytes. Raw helper output collection follows `ToolRunner` and requires requested `outputs`/`outputDirectories`.

The package supports one processing thread and one database per classification. It does not expose daemon mode, multi-database merging, low-complexity masking, remote database downloads or bzip2 input. The high-level wrapper rejects missing input reads with status 64; the pinned original helper can otherwise silently return an empty successful result for an unreadable read file. Direct helpers retain that upstream input-error behavior. The 2 GiB compiled heap also contains the database and intermediate arrays; large desktop Kraken2 databases will not fit. Memory mapping still copies data into the browser's virtual filesystem and is not disk-backed storage.
