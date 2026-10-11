# featureCounts in WebAssembly

This builds featureCounts 2.1.1 from the Subread release source for browsers and
Node.js. It runs the upstream counting code with real pthreads. The command-line
arguments and count-file formats are those of featureCounts.

The build accepts one to four counting threads (`-T 1` through `-T 4`, with one
as the default). It needs shared WebAssembly memory. Browsers must provide
cross-origin isolation; Node.js does not need HTTP headers.

## Build

Install a C compiler, make, patch, curl, Python 3, Node.js and Emscripten 3.1.64.
Activate Emscripten, or set `EMSDK` to its installation directory, then run from
the repository root:

```sh
scripts/build-featurecounts.sh all
```

The script verifies source checksums, builds zlib 1.3.1 from its pinned source,
and produces three variants:

| Variant | Command | Output |
| --- | --- | --- |
| Unmodified upstream | `scripts/build-featurecounts.sh native` | `build/native/featurecounts` |
| Port patches, native compiler | `scripts/build-featurecounts.sh patched-native` | `build/patched-native/featurecounts` |
| WebAssembly | `scripts/build-featurecounts.sh wasm` | `dist/featurecounts/featurecounts.mjs` and `featurecounts.wasm` |

The native baseline uses unchanged Subread source. The patched native build
helps separate the effects of source changes from those of the WebAssembly
compiler. `JOBS` controls build parallelism; `FEATURECOUNTS_BUILD_ROOT` can
select another absolute build directory.

The distribution includes its source lock, build manifest, patches, complete
Subread and zlib source archives, and licence notices. Keep its JavaScript and
WebAssembly assets together when deploying it, including generated worker files.

## Run

Examples of upstream command arguments are:

```sh
featureCounts -T 1 -a annotation.gtf -o counts.tsv input.bam
featureCounts -T 1 -p --countReadPairs -a annotation.gtf -o pairs.tsv input.bam
featureCounts -T 4 -f -a annotation.gtf -o exons.tsv input.sam
```

By default, featureCounts groups exons by gene. `-f` counts individual features.
For paired-end fragments, use both `-p` and `--countReadPairs`. Options such as
`-s`, `-M`, `--fraction`, `-O`, `-Q`, `-J` and `-R` retain their upstream meaning.
The usual output is the chosen count filename plus a `.summary` file. Request
additional output files when using junction counts or per-read assignments.

Use the shared [JavaScript runner](../../runtime/README.md) with `waitForExit:
true`, because the program's main function runs on a pthread:

```js
import { ToolRunner } from './runtime/index.mjs';

const featureCounts = new ToolRunner({
  moduleUrl: new URL('./dist/featurecounts/featurecounts.mjs', import.meta.url),
  waitForExit: true,
});

const result = await featureCounts.run([
  '-T', '1', '-a', 'annotation.gtf', '-o', 'counts.tsv', 'input.bam',
], {
  files: [
    { path: 'annotation.gtf', data: annotationBytes },
    { path: 'input.bam', data: bamBytes },
  ],
  outputs: ['counts.tsv', 'counts.tsv.summary'],
  timeoutMs: 120000,
});

if (result.exitCode !== 0 || result.missingOutputs.length) {
  throw new Error(new TextDecoder().decode(result.stderr));
}
// result.files contains the requested output files as Uint8Array data.
```

The inputs are bytes from browser `File.arrayBuffer()` or Node.js `readFile()`.
Each invocation has a new filesystem and module instance. Argument arrays do
not interpret shell pipes, wildcards or redirection.

For direct browser hosting, serve the page and assets with:

```text
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Embedder-Policy: require-corp
```

The [RNA analysis example](../../examples/rna-analysis/README.md) includes a
local server and a browser interface.

## Port changes and limits

- The thread limit is four instead of the upstream maximum of 64. Eight
  preloaded workers provide room for the main application thread, counting
  threads and auxiliary work. Both patched builds enforce the same limit.
- Emscripten uses standard `fopen` with its 64-bit file offsets instead of the
  Linux-specific `fopen64` entry point.
- Diagnostic format strings and BAM offset formatting use the actual integer
  widths. Gene counts, feature counts and assignment summaries retain the
  upstream 64-bit counters.
- Upstream junction and splice-site support tables encode counters in pointer
  values. In both patched builds, checked additions stop with exit status 2 if
  any individual or merged support counter would exceed 2,147,483,647. This
  prevents silent overflow on 32-bit WebAssembly. Use the unmodified native
  program for inputs beyond that limit.
- A small Emscripten mailbox compatibility override uses the SDK's existing
  notification path when the runtime itself is inside a worker.

The WebAssembly heap starts at 256 MiB and can grow to 2 GiB. Input files,
returned files and JavaScript memory also consume memory outside this heap.
Browser memory limits may be lower. These builds are intended to make bounded
datasets usable in a browser; small-region tests do not establish performance
or correctness for every whole-genome input or option combination. SAM and BAM
are featureCounts inputs; this port does not add CRAM support.

## Validation

Every patched build runs the count-guard boundary checks, including a successful
addition exactly at the limit and two failing overflow cases. The WebAssembly
checks execute the compiled 32-bit code in Node.js.

The repository's RNA validation compares unmodified native, patched native and
WebAssembly output on real human and yeast RNA-seq data. Run it after building:

```sh
node scripts/validate-rna-analysis.mjs --tool featurecounts --engine node
```

Inspect the generated reports for the tested commands and the hashes of the
particular binaries. A Node.js result does not establish a browser result; the
browser suite runs separately. Local integration checks completed gene and exon
counting on both datasets with the actual WebAssembly module. Further Node.js
checks combined four counting threads, paired-fragment counting, junction
counts with a reference FASTA, and each of the CORE, SAM and BAM read-assignment
outputs. All three builds produced matching counts, junction tables and full
read-assignment records on both datasets, allowing for threaded output order.

## Sources and licences

[sources.lock.json](sources.lock.json) pins Subread 2.1.1, zlib 1.3.1 and
Emscripten 3.1.64. Subread is distributed under GPL version 3 or later. The
build scripts and small port helpers use the repository's MIT licence; this
does not change the licence of the compiled program. See [LICENSE](LICENSE),
[LICENSE.emscripten](LICENSE.emscripten) and
[THIRD_PARTY_LICENSES.txt](THIRD_PARTY_LICENSES.txt) for the notices.
