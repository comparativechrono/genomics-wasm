# HMMER 3.4

This port compiles the original HMMER 3.4 release, including its bundled Easel
0.49 and libdivsufsort-lite 2.0.0 libraries. The source archive, revision,
SHA-256 and compiler version are pinned in `sources.lock.json`. Upstream:
https://hmmer.org/ and https://github.com/EddyRivasLab/hmmer.

The distributed offline programs are `hmmbuild`, `hmmsearch`, `hmmscan`,
`hmmpress`, `hmmfetch`, `hmmalign`, `hmmconvert`, `hmmemit`, `hmmstat`, `phmmer`,
`jackhmmer`, `nhmmer` and `nhmmscan`. Command arguments and scientific algorithms
remain those of upstream. HMMER's daemon, MPI services, other auxiliary programs
and Easel miniapps are outside this package.

## Build

```
scripts/setup-emsdk.sh
scripts/build-hmmer.sh all
```

Modes are `native`, `patched-native`, `wasm` and `all`. The `wasm` mode requires
the two native builds to exist so its manifest can bind the comparison
executables. Native outputs are `build/{native,patched-native}/hmmer/<program>`;
each browser program is `dist/hmmer/<program>.mjs` plus `.wasm`. The distribution
contains the exact source archive, required notices and a build manifest binding
all build inputs, assets and native executable hashes.

All variants select upstream's serial SSE2 implementation with POSIX threads
and MPI disabled and without fast-math or fused floating-point contraction.
Consequently upstream's optional `--cpu` argument is unavailable in these
builds. The unchanged native oracle has no source patch. The patched native
oracle applies exactly the patch used for WebAssembly.

## Portability changes and limits

Emscripten 3.1.64 lowers upstream SSE2 intrinsics to WebAssembly SIMD128. The
source patch replaces the x86 CPUID instruction on Emscripten with the
known SIMD capability of this build and allows its target name in the older
upstream `config.sub`. A compatibility header selects Emscripten's SSE2 header
instead of Clang's catch-all x86 intrinsics header. The SSE profile conversion
also adopts upstream's existing VMX fix: convert rounded float costs through
`int` before `uint8_t`. Directly converting negative floats to unsigned bytes
is undefined C behavior; x86 wraps while WebAssembly saturates, which otherwise
corrupts the MSV filter and its calibration. The intermediate signed conversion
preserves upstream's x86 behavior without rewriting scores, filters, profile
building, random generators or alignments. It is applied to both the portable
native oracle and WebAssembly. `abi-check.c` asserts the
primitive widths, 64-bit file offsets and little-endian vector layout used by
upstream's binary profile files. The original HMM and pressed database formats
are retained. Native-to-WASM and WASM-to-native interchange is validated by
executing consumers, rather than requiring independently calibrated profiles
to be byte-identical across floating-point libraries.

The modules require a browser or Node.js runtime with WebAssembly SIMD support.
Each run executes serially in a worker with a separate virtual filesystem.
Memory starts at 64 MiB and may grow to 2 GiB. Mounted files and JavaScript
copies also occupy memory outside that heap: choose small or selected profile
and sequence databases instead of expecting an entire large public database to
fit in a tab. Daemons, external command pipelines and filesystem paths outside
the mounted virtual filesystem are unavailable. Upstream's `gzip -dc` subprocess
cannot run in a browser; provide decompressed sequence, alignment and HMM files.

For reproducible stochastic operations, use an explicit nonzero `--seed` where
the selected upstream program supports it. A zero seed requests an arbitrary
seed. HMM timestamps, run paths, elapsed time and throughput are metadata;
validation compares scientific content and does not require those fields to
match.

Copyright and incorporated-code notices are in `LICENSE*` and
`THIRD_PARTY_LICENSES.txt`. No claim of endorsement by the upstream authors is
made.
