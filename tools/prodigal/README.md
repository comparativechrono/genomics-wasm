# Prodigal

This port compiles the original Prodigal C gene finder, official release `v2.6.3`,
commit `004218fbeb2cf54fe12d88ec2eede6a27d70ebf7`. The full upstream source,
its 50 built-in metagenomic training models and original GPL-3.0-or-later licence
are retained. `sources.lock.json` pins the release archive's SHA-256.

Run `bash scripts/setup-emsdk.sh` followed by `bash scripts/build-prodigal.sh all`.
The build needs Python 3.12+, a C compiler, patch and Emscripten 3.1.64. It creates
the unchanged native executable `build/native/prodigal`, a patched native
executable `build/patched-native/prodigal`, and `dist/prodigal/prodigal.mjs` plus
`prodigal.wasm`. The build manifest binds every port input, every distribution
asset and both native executable hashes. Complete sources are under
`dist/prodigal/sources/` and are reusable as an offline source cache.

The recorded portability patch replaces fourteen overlapping `sprintf` calls
with non-overlapping string appends. The upstream calls use their destination
buffer as a `%s` argument, which is undefined in C and differs between native
and browser C libraries. Gene selection, training, scoring, genetic codes,
output field values and metagenomic models are unchanged. A separate compile
unit asserts the training-file structure layout without adding executable code.

All prediction units use `-O2` without fast-math. The 25 MiB `training.c` unit
contains literal model initializers and training-file I/O; it uses `-O0` on all
targets to avoid spending most of the build optimizing constant assignments.
Native builds use `-U_FORTIFY_SOURCE`: fortifying libc's `sprintf` loses the
overlapping prefixes in the unmodified baseline, whereas ordinary glibc retains
the upstream output. The patched source avoids that undefined behavior outright.

The command supports single-genome training and prediction (`-p single`) and
metagenomic prediction (`-p meta`), GFF/GenBank/SCO/gene-coordinate output,
protein and nucleotide FASTA, start scores, closed ends, N masking, alternate
translation tables and saved training files. The runtime guide describes
mounting input files and collecting output paths. Single-genome training needs
at least 20,000 input bases; upstream recommends at least 100,000.

The raw binary `-t` training files retain upstream's little-endian layout with
32-bit integers and 64-bit doubles. Compile-time assertions require a 558,392
byte training structure and matching member offsets on the native and browser
targets. This supports exchange with ordinary little-endian x86-64 builds;
upstream does not define a universal architecture-independent training format.

Browser execution is serial and needs neither SharedArrayBuffer nor cross-origin
isolation. The maximum compiled heap is 2 GiB; sequence copies, working memory
and output files also consume memory. Upstream limits individual sequences to
32 million bases. This is intended for microbial genomes and modest contig
collections. Raw FASTA input is used; compressed files must be decompressed
before mounting.
