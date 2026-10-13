# Primer3 2.6.1 original-source WebAssembly build

The seven programs in the upstream default build are compiled from release
v2.6.1, commit `7f9f17d6012f404e83bbf0f931a4d06eb4af465b`. The release
was published on 26 January 2022. `sources.lock.json` pins the source archive
and Emscripten 3.1.64. No upstream source patches are applied.

Run `scripts/build-primer3.sh all` with the pinned SDK active. The native and
`patched-native` directories contain separately compiled copies of the same
unmodified source; the latter name follows the repository's comparison
contract and does not imply a patch exists. `wasm`, `native`, and
`patched-native` modes can be built separately. Build metadata binds every
port input, runtime resource, source archive, notice, and native executable.

The source-unit and program composition follows upstream `src/Makefile`.
All targets use `-O2 -ffp-contract=off`, with upstream's `-O0` choice for
`ntdpal_main.c`. No fast-math, SIMD approximation, parameter replacement,
or algorithm port is used. The native thermodynamic units retain upstream's
`-ffloat-store`; wasm uses binary64 arithmetic directly because Clang does
not support that GCC flag. The C++ library uses C++11.

The browser build has a 64 MiB initial heap, 2 GiB maximum linear memory,
and an 8 MiB stack. It runs in one disposable worker without pthreads,
SharedArrayBuffer, cross-origin isolation, or Asyncify. Files live in MEMFS;
the masker reader's original POSIX `mmap` uses Emscripten's emulation and
therefore consumes linear-memory space rather than mapping a host file.
Native and wasm ABI checks cover the 64-bit k-mer words, 32-bit k-mer
counts, 64-bit doubles, and wasm32 pointers with 64-bit file offsets.

Default thermodynamic tables are embedded by upstream Primer3 itself.
The original `src/primer3_config/` is also shipped for explicit path-based
usage. Genome-scale masker frequency indices are not runtime resources;
users must provide compatible indices and account for their memory cost.
The original source archive retains upstream test data, but those data are
not automatically mounted as a production masking database.

See `runtime/PRIMER3.md` for the worker API and `validation/primer3/` for
the tested scientific scope. A successful compile alone is not a claim of
browser equivalence for every upstream input or workload size.
