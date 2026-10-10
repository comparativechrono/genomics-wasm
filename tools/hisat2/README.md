# HISAT2

HISAT2 2.2.2, compiled from pinned upstream C++ sources for WebAssembly. This
package builds reference indexes, aligns DNA or RNA reads, and inspects indexes.
RNA alignment includes splice discovery and supplied splice sites. The actual
HISAT2 alignment and indexing code runs in the browser.

## Commands

The package combines three upstream programs in one module:

| Command | Upstream program |
| --- | --- |
| `hisat2 build [options] reference.fa reference` | `hisat2-build-s` |
| `hisat2 align [options] -x reference -U reads.fastq -S output.sam` | `hisat2-align-s` |
| `hisat2 inspect [options] reference` | `hisat2-inspect-s` |

For paired reads, replace `-U reads.fastq` with `-1 reads_1.fastq -2 reads_2.fastq`.
Run `align --help`, `build --help`, or `inspect --help` for upstream options.
Arguments beginning with an option, such as `--version`, also invoke alignment.

Indexing produces eight files: `reference.1.ht2` through `reference.8.ht2`.
Keep all eight together and provide the basename to `-x`. These are HISAT2
indexes and cannot be exchanged with BWA or BWA-MEM2 indexes.

The native comparison executables and the WebAssembly module use the same
command dispatch. The untouched native baseline dispatches to the three
unmodified upstream binaries. The patched native build and WebAssembly build
link the three entrypoints into one program.

## Browser use

Import `hisat2.mjs` with the included `ToolRunner`, put input files in the
virtual filesystem, and request output files explicitly. Each invocation
creates a fresh worker and module. Index files must be supplied again for an
alignment or inspection invocation. The generic browser and Node examples in
the package demonstrate the runtime API; the repository browser example also
provides index and alignment presets.

```js
const index = await runner.run(['build', 'reference.fa', 'reference'], {
  files: [{ path: 'reference.fa', data: referenceBytes }],
  outputs: Array.from({ length: 8 }, (_, i) => `reference.${i + 1}.ht2`),
});

const aligned = await runner.run([
  'align', '-p', '1', '-x', 'reference',
  '-1', 'reads_1.fastq', '-2', 'reads_2.fastq', '-S', 'output.sam',
], {
  files: [...index.files, ...readFiles],
  outputs: ['output.sam'],
});
```

## Build

Activate Emscripten 3.1.64, then run:

```sh
bash scripts/build-hisat2.sh all
```

The script also accepts `native`, `patched-native`, or `wasm`. Native builds
use Clang; set `CXX` to override it. The script uses the compiler bundled with
the SDK when Clang is not on PATH. `JOBS` sets build parallelism.

`sources.lock.json` pins the upstream archive and its SHA-256 checksum. The
package includes that archive, the patch, dispatcher, license notices and build
script. No external source dependency is needed beyond the pinned SDK and a
native C++ build environment.

## Port changes

- The SSE2 alignment code compiles to WebAssembly SIMD128. The port replaces
  x86 CPU detection and inline population-count assembly with a 64-bit compiler
  builtin.
- The sole alignment worker runs synchronously. Index construction retains
  upstream's existing single-thread path. Alignment and indexing reject a
  thread count other than one.
- One reference-coordinate subtraction now explicitly uses a signed 64-bit
  type. Upstream's `size_t` subtraction wrapped incorrectly on wasm32, changing
  SAM template lengths. The change preserves the 64-bit native result.
- The three entrypoints share one memory-accounting object. Inspection's
  verbosity variable has internal linkage to avoid a duplicate symbol.
- C++ exceptions remain enabled, and format warnings are errors for both
  patched builds.

The algorithms, scoring, batching and index formats are otherwise unchanged.

## Limits

Use small references and read sets that fit in browser memory. The module can
grow to 2 GiB of linear memory, with additional memory used by the browser and
file copies. This package is not suitable for building or loading a whole-human
reference index in the browser.

Only single-threaded small indexes (`.ht2`) are included. Large `.ht2l` indexes,
memory mapping, shared-memory indexes, SRA input and the separate repeat builder
are not included. The Python and Perl command wrappers are not included, so
their compressed-input/output handling and helper scripts are unavailable.
Provide decompressed FASTQ/FASTA reads and local virtual filesystem paths.
Standard input is available for options supported by the underlying binary.

## Validation

The comparison suite runs original native, patched native and WebAssembly
builds against real human and yeast RNA reads. It compares complete SAM records
and tags, all eight index files, index cross-use, supplied splice sites,
different alignment options, inspection, repeated calls and invalid inputs.
Only the command-line field (`CL`) in SAM `@PG` headers is excluded from
comparison. Browser results are recorded by the
repository CI workflow alongside Node results.

HISAT2 and the port are distributed under GPL-3.0-or-later. See `LICENSE` and
`THIRD_PARTY_LICENSES.txt` for the upstream and bundled component notices.
