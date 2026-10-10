# AdapterRemoval for WebAssembly

This package builds AdapterRemoval 2.3.4 for browsers and Node.js. It runs the
upstream adapter alignment, quality trimming, read filtering and paired-read
merging algorithms. Single and paired FASTQ files are supported.

Version 2.3.4 is an established release with a true single-thread execution
path. Version 3 uses additional I/O threads and different compression
libraries; it is not the version supplied here.

## Build

On x86-64 Linux, install a C/C++ compiler, GNU Make, Python 3, curl, patch
and Emscripten 3.1.64.
Activate Emscripten or set `EMSDK` to its installation directory, then run:

```sh
scripts/build-adapterremoval.sh all
```

The script also accepts `native`, `patched-native` or `wasm`. Native builds go
to `build/native/adapterremoval` and `build/patched-native/adapterremoval`.
Browser files are `dist/adapterremoval/adapterremoval.mjs` and
`dist/adapterremoval/adapterremoval.wasm`. `ADAPTERREMOVAL_BUILD_ROOT` selects
an absolute build directory; `JOBS` controls dependency compilation.

[sources.lock.json](sources.lock.json) records the source commit, archive
hashes and Emscripten version. All variants link the same zlib 1.3.1 and
bzip2 1.0.8 sources. The original native baseline has no source modifications.

## Use

The default module export is the asynchronous `createAdapterRemoval` factory,
with `FS` and `callMain`. Use a fresh instance for every command. The shared
`ToolRunner` handles this in a worker and provides files, standard streams,
cancellation and timeouts.

For paired reads, pass an argument array such as:

```js
["--file1", "reads_1.fastq", "--file2", "reads_2.fastq",
 "--basename", "trimmed", "--threads", "1", "--seed", "42",
 "--trimns", "--trimqualities", "--minquality", "20"]
```

For single reads, omit `--file2`. Add `--collapse` to merge overlapping pairs;
`--collapse-deterministic` uses the upstream deterministic merging mode.
`--adapter1` and `--adapter2` select adapter sequences. `--help` lists upstream
options. A fixed `--seed` makes ordinary merging reproducible when equally
supported bases need a random choice.

Paired trimming writes `trimmed.pair1.truncated`, `trimmed.pair2.truncated`,
`trimmed.singleton.truncated`, `trimmed.discarded` and `trimmed.settings`.
Merging adds collapsed-read outputs. The calling application should collect
these files from the module filesystem. Gzip and bzip2 input and output use
the pinned compression libraries.

Only `--threads 1` is supported, and it is the default. Other thread counts
are rejected before processing. The module uses WebAssembly SIMD, a growing
32-bit heap capped at 2 GiB and an 8 MiB stack. Shared browser memory and
cross-origin isolation are not required. Use inputs that fit in browser
memory; file storage and JavaScript copies also consume memory.

## Port changes

[browser.patch](browser.patch) removes worker creation and runs the existing
single-thread scheduler. It retains every input, processing and output step,
and rejects thread counts other than one. The SSE2 header is included explicitly.
The SSE2 alignment kernel compiles
to WebAssembly SIMD through Emscripten. Native and WebAssembly use the same
upstream adapter alignment, trimming, merging and statistics code.

## Validation

Run the shared comparison suite with:

```sh
node scripts/validate.mjs --tool adapterremoval --engine node
```

The suite compares original native, patched native and WebAssembly results
on real human and yeast reads. FASTQ records, their order and their qualities
are compared exactly. Separate CI jobs cover Chromium, Firefox and WebKit.
Reports record the commands, hashes and detailed comparisons. Validation
applies to those inputs and options rather than all possible combinations.

The Node.js suite passed all 14 recorded cases on 10 October 2026. FASTQ
outputs and complete settings reports matched byte for byte, including paired
adapter trimming, quality trimming, fixed end trimming, merging, single reads,
repeated commands and error cases. Browser results are recorded separately by CI.

## Licence

AdapterRemoval is GPL-3.0-or-later; its licence and full source archive are
included. zlib and bzip2 retain their own licences. Every package includes the
source archives, patch, build script and checksums. Archives in the supplied
`build/downloads` layout permit rebuilding without downloading sources;
a native toolchain and Emscripten are still required.
