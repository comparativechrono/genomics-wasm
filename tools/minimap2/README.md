# minimap2 WebAssembly

This package builds minimap2 2.31 (r1302) for browsers and Node. It runs the
upstream alignment code, including `splice:sr` for short RNA-seq reads and
`splice` or `splice:hq` for long RNA-seq reads. It also supports the genomic
alignment presets and PAF output.

The exact source revision and archive checksums are in `sources.lock.json`.
The source and zlib archives accompany the downloadable package. minimap2 is
MIT licensed; zlib's license is included separately.

## Build

Install Python 3, curl, make, patch, a native C compiler and Emscripten 3.1.64.
From the repository root:

```sh
source toolchain/emsdk/emsdk_env.sh
bash scripts/build-minimap2.sh all
```

The script accepts `native`, `patched-native` and `wasm` to build one variant.
It verifies downloaded archives and starts each build from fresh source.

- `build/native/minimap2`: unmodified upstream source with native CPU dispatch.
- `build/patched-native/minimap2`: the browser patch compiled natively.
- `dist/minimap2/minimap2.mjs` and `minimap2.wasm`: the WebAssembly module.

The native builds target x86-64. The port uses upstream SSE4.1 alignment
kernels; Emscripten translates these to WebAssembly SIMD128. It does not
require shared memory or browser cross-origin isolation headers.

## Commands

Use one thread. This is the port's default; requests for other thread counts
fail with exit code 2.

```sh
# Short paired RNA-seq reads
minimap2 -ax splice:sr -t 1 reference.fa reads_1.fastq reads_2.fastq > reads.sam

# Long RNA reads
minimap2 -ax splice -t 1 reference.fa reads.fastq > reads.sam

# Build and use a reusable index
minimap2 -x splice:sr -t 1 -d reference.mmi reference.fa
minimap2 -ax splice:sr -t 1 reference.mmi reads_1.fastq reads_2.fastq > reads.sam

# Include known splice junctions in BED format
minimap2 -ax splice:sr -t 1 -j annotation.bed reference.fa reads_1.fastq reads_2.fastq > reads.sam
```

Index settings are stored in `.mmi` files and override the corresponding
command-line settings when the index is loaded. Build an index with the
preset you intend to use. These are minimap2 indexes; BWA indexes cannot be
used by minimap2.

`ToolRunner` accepts an argument array and files, and returns stdout, stderr,
the exit code and requested output files. Shell redirection in the examples
means saving the returned stdout. Run each command through a fresh worker,
as the supplied runtime does, so that globals and allocated memory are
released between commands.

## Browser limits

The build uses a growing heap capped at 2 GiB and an 8 MiB stack. It is meant
for references and read sets that fit within the browser's available memory;
a full human reference is not a supported target for this wasm32 package.
`-K` controls mapping batch size, but does not reduce the size of a loaded
reference index. There are no subprocesses or remote-file readers.

`browser.patch` changes thread dispatch and the thread option, and corrects
`size_t` formats in diagnostic output. Alignment, seeding, chaining, splice
scoring and index serialization remain upstream code. The build selects the
non-dispatch SIMD objects and explicitly enables SSE4.1, preserving the same
alignment kernels in patched native and WebAssembly builds.

## Validation

The repository compares the original native build, patched native build and
WebAssembly on pinned real sequencing data. It checks full SAM records,
index bytes and index exchange between builds. Program command lines and
runtime timing diagnostics are not biological results and are handled
separately. See `validation/README.md` and the workflow reports for the exact
cases and results; compatibility is limited to the cases that have passed.

Local Node comparisons passed on the included human and yeast RNA subsets.
With `-ax splice:sr -t 1`, the human subset produced 512 mapped records, 147
with an intron (`N`) in the CIGAR; the yeast subset produced 505 mapped
records, 119 with an intron. The native and WebAssembly results matched.
The RNA fixtures contain short paired reads; no long-read RNA fixture is
currently included.
