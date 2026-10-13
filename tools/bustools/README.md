# bustools 0.45.1

This port compiles the original C/C++ source from pinned upstream commit
`d4d7a283dbe9d34d2106e9ba31e2566413be6a70` with Emscripten 3.1.64.
All 19 processing commands and upstream aliases are linked into one
`bustools.mjs` / `bustools.wasm` pair. The source lock identifies the complete
upstream archive and zlib 1.3.1, including their SHA-256 checksums. Numerical
dependencies and CRoaring are retained from the original source archive.

```sh
bash scripts/build-bustools.sh all
```

The modes `native`, `patched-native`, and `wasm` are also available. The
unchanged-source reference is `build/native/bustools`; the same portability
patch and serial configuration are exercised by `build/patched-native/bustools`.
Release assets, original sources, notices, and their build manifest are placed
in `dist/bustools`. All targets compile the same pinned zlib sources.

`browser.patch` makes these portability changes:

- The port's default sort memory is 64 MiB. The upstream 4 GiB default exceeds
  wasm32's address space. The native reference retains its original default;
  comparison cases pass an explicit identical `-m` value. Invalid and oversized
  budgets are rejected before 32-bit conversion; the port caps sorting buffers
  at 1 GiB to leave space for runtime and filesystem storage.
- The port accepts one sorting thread and rejects `-t` values above one with a
  clear CLI error. The upstream serial sort implementation remains unchanged.
- Prediction invokes its original per-gene worker synchronously, preserving
  the model fitting and accumulation code. The native reference retains its
  upstream thread scheduler.
- The size-rounding helper omits a shift by 32 on 32-bit `size_t` targets.
- BUSZ block lengths are widened to `uint64_t` before shifting by 30, preserving
  the original native file format on wasm32.
- For per-cell histogram summaries with zero counted reads and zero UMIs, the
  undefined counts-per-UMI mean is written as the native Linux reference's
  `-nan` token. WebAssembly engines may choose either sign for the original
  `0/0` NaN. This formatting guard makes that spelling deterministic; all
  counts and every finite ratio calculation remain unchanged.

The ABI check asserts 32-byte BUS records, all six field offsets, little-endian
serialization, and 64-bit BUSZ index fields. Native/WASM validation additionally
exercises data and compressed-file interchange. Build flags disable floating
point contraction and do not enable fast-math. C++ exception handling remains
enabled, including the upstream predictor's optimizer fallback. `EIGEN_MPL2_ONLY` verifies that
the compiled Eigen code does not require its optional LGPL components.

The browser build uses a single worker and a maximum 2 GiB linear-memory heap.
SharedArrayBuffer, cross-origin isolation, and WebAssembly SIMD are not required.
Input files, sort spill files, and outputs reside in the worker's memory-backed
filesystem; external sorting therefore does not provide disk-scale capacity.
Pass `-m 64M` (or a suitable larger budget) and use modest BUS inputs. Ordinary
CLI arguments, binary stdin/stdout, and command-specific output files are
transported by the shared JavaScript runner without biological rewrites.

Licenses and original attribution notices are listed in
`THIRD_PARTY_LICENSES.txt` and copied into every distribution. See
`runtime/BUSTOOLS.md` for the JavaScript API and
`validation/bustools/README.md` for native-equivalence coverage.
