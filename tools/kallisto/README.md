# kallisto in WebAssembly

This package builds kallisto 0.52.0 for browsers and Node.js from the pinned
upstream source. It runs kallisto's transcriptome indexing, pseudoalignment and
expectation-maximisation quantification code. It does not use stored answers or
send reads to a server.

Supported commands are `index`, `quant`, `inspect`, `version` and `cite`. The
build runs on one worker and requires `-t 1` when a thread count is supplied.
FASTQ files can be plain text or gzip compressed. Quantification supports paired
reads, single reads with a fragment length distribution, strand selection,
sequence bias correction, and plaintext bootstrap output.

```sh
kallisto index -i transcripts.idx transcripts.fa
kallisto quant -i transcripts.idx -o sample --plaintext -t 1 reads_1.fastq reads_2.fastq
kallisto quant -i transcripts.idx -o single --plaintext --single -l 200 -s 20 reads.fastq
```

The main outputs are `abundance.tsv` and `run_info.json`. Bootstrap runs with
`--plaintext -b N` also produce `bs_abundance_0.tsv` through
`bs_abundance_N-1.tsv`. HDF5 and HTSlib are disabled using upstream build options,
so HDF5 files and BAM output are not available. BUS and single-cell workflows
are outside this package's supported commands.

Bootstrap commands run, but a fixed seed does not reproduce the same bootstrap
tables across the native and WebAssembly builds. In two-replicate checks on
both included datasets, the main abundance table agreed exactly while the
bootstrap tables differed. Their random-sampling equivalence has not been
validated. Use the same build when reproducible bootstrap draws are required;
the native-comparison release checks cover the main quantification results.

Build indexes using this package or the pinned 64-bit kallisto 0.52.0 native
build. Loading the human test index in both directions produces the same
quantification table. This does not establish compatibility with other kallisto
versions or unrelated builds. Plain FASTA, FASTQ and result tables can be
transferred between builds.

The WebAssembly heap can grow to 2 GiB. Inputs, indexes and output files share
browser memory. Small transcript panels are suitable for the included example;
whole-transcriptome capacity and performance must be measured for the user's
reference and browser. Memory exhaustion stops the worker with an error.
SharedArrayBuffer and cross-origin isolation headers are not required.

## Building

```sh
scripts/setup-emsdk.sh
source toolchain/emsdk/emsdk_env.sh
scripts/build-kallisto.sh all
```

The build script accepts `native`, `patched-native` and `wasm` separately. It
checks the kallisto and zlib source archives against `sources.lock.json` and
requires Emscripten 3.1.64 for WebAssembly. GCC with C++17 support, Make,
Python 3, curl, tar and patch are also needed for the native baseline. `JOBS`
controls compile jobs. The unchanged upstream source is compiled with GCC;
Clang needs the Bifrost typo correction included in the patched builds.

`native` compiles unchanged upstream source. `patched-native` compiles the same
source with the browser patch on the host. `wasm` uses that patch with Emscripten.
The three builds use the upstream CMake source lists through a small generated
makefile so that Bifrost inherits the selected compiler. No CMake download or
system library discovery is needed. Zlib is built from the same pinned source
for all three variants.

Native binaries are written to `build/native/kallisto` and
`build/patched-native/kallisto`. The browser module is
`dist/kallisto/kallisto.mjs`, alongside `kallisto.wasm` and its source and licence
files. Use the shared `ToolRunner` to run each command in a fresh worker and
transfer files between commands.

## Changes for the browser

`browser.patch` replaces the worker launch points in kallisto and Bifrost with
`SerialThread` and two sequential BooPHF launch functions in `serial-thread.h`.
They execute the original callback on the current worker. This works because
the supported commands use one processing thread and join completed work;
these functions are not a general replacement for concurrent C++ code.
The command checks require one processing thread.

Bifrost uses 64-bit sizes to pack minimizer positions and bitmap values. This
build uses Emscripten `MEMORY64=2` for kallisto and zlib: the compiler retains
64-bit C/C++ values, then Binaryen lowers the memory operations to ordinary
32-bit WebAssembly. Browsers do not need native memory64 support. This mode is
marked experimental by Emscripten; release validation must exercise actual
browser engines as well as Node. The patch also corrects a misspelled member name in an
unused Bifrost copy constructor so Clang can parse it.

The index and quantification algorithms remain upstream code. The validation
suite compares each build using the same real human and yeast reads, and checks
the portability patch separately from the WebAssembly compilation. See the
repository's validation instructions and generated reports for tested cases and
measured numerical differences.

kallisto and Bifrost use the BSD 2-Clause licence. Dependency notices are in
`THIRD_PARTY_LICENSES.txt`; the exact source archives accompany each package.
