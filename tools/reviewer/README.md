# REViewer 0.2.7 for WebAssembly

This build pins the official Illumina REViewer 0.2.7 release. It produces SVG
read pileups, allele-depth metrics and phasing tables from ExpansionHunter
output. The CLI remains the upstream CLI:

```sh
REViewer --reads sorted.bam --vcf result.vcf --reference reference.fa \
  --catalog catalog.json --locus LOCUS_ID --output-prefix review
```

Supply the sorted, indexed ExpansionHunter BAMlet, VCF, matching catalog,
reference FASTA and its index as local files. The BAMlet must be sorted and
indexed before use, for example with the accompanying SAMtools build. Outputs
are `review.LOCUS_ID.svg`, `review.metrics.tsv` and `review.phasing.tsv`.
Comma-separated locus IDs produce several SVGs. The default
`--region-extension-length 1000` must match the setting used in ExpansionHunter.

## Build and provenance

```sh
bash scripts/setup-emsdk.sh
source toolchain/emsdk/emsdk_env.sh
python3 scripts/build-reviewer.py all
```

Python 3.11 or newer, a C/C++ compiler, make, curl and Emscripten 3.1.64 are
required. The recipe also discovers the SDK through `GENOMICS_EMSDK_DIR`.
`JOBS` controls compilation concurrency and defaults to 2. The modes `native`,
`patched-native` and `wasm` build individual targets; `all` builds all three.
Every mode builds from freshly extracted, hash-verified source archives.

- `build/native/reviewer/REViewer`: unchanged upstream scientific code on GNU libc Linux.
- `build/patched-native/reviewer/REViewer`: the same native build with the scoped portability change.
- `dist/reviewer/reviewer.mjs` and `reviewer.wasm`: browser/Node ES module with `FS` and `callMain`.

The lock file pins REViewer, HTSlib 1.10.2, Boost 1.73.0, spdlog 1.6.1 and
compression dependencies by URL, size and SHA-256. The complete source archives
and their notices accompany the distribution. The bundled graph-tools and JSON
sources remain the versions included in the REViewer tag. Only the Boost
program-options library is needed by the linked executable; other Boost uses
are header-only. Catch2 is a test-only upstream dependency and is not linked.
The build manifest binds build inputs, emitted assets and both native oracles.
The build refuses to publish assets when an input changes during compilation.
The standard `<limits>` and `<cstdint>` headers are preincluded for compatibility
with modern compilers; the original native source files remain unchanged.

## Portability

The `Origin.cpp` fragment-assignment step uses C library `rand()` without
setting a seed. GNU libc and Emscripten's C library produce different default
sequences, which would change read assignments, allele depths and SVGs. The
patched native and WASM builds replace that one call with a scoped, fixed-width
implementation of GNU libc's default-seed, degree-31 additive sequence. A native
check compares 100,000 successive values against untouched GNU libc `rand()`.
No global RNG symbol is overridden, and the original native oracle remains
unchanged. This compatibility target is GNU libc Linux; upstream output on
other C libraries can differ. Each browser invocation uses a fresh worker.

File offsets and HTS genomic positions are checked to remain 64-bit; pointers
and `size_t` are 32-bit in WASM. The computational code is serial. C++ exceptions
are enabled so invalid inputs use the upstream error path. Remote HTS protocols
and external HTS plugins are disabled. The Emscripten filesystem holds all
working data in memory, with a 2 GiB maximum linear memory; this does not imply
that every dataset approaching that size will fit. Ordinary HTTP hosting is
sufficient; cross-origin isolation and SharedArrayBuffer are not required.

## License

The pinned REViewer 0.2.7 tag is **GPL-3.0-or-later**; its bundled graph-tools
library retains Apache-2.0 terms. REViewer's license differs in later upstream
source history. Preserve
the included GPL license, copyright notices, complete corresponding source and
build recipes when distributing this version. Boost, spdlog/fmt, HTSlib and the
compression libraries retain their own included notices. See
`versions.lock.json` and `notices/` for the exact archive and notice inventories.
