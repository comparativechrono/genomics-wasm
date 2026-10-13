# PLINK2 WebAssembly build

This compiles original PLINK2 sources at commit
`72119068234ce0acf7189e53dfe67a527bc53e2c`, version
`2.0.0-b.1-dev (12 Oct 2026)`, with upstream's `NOLAPACK` profile.
`--pca` and `--pheno-svd` are unavailable and retain upstream's explicit errors.
The upstream development version is pinned; this is not a stable PLINK2 release.

Run `scripts/build-plink2.sh all` with Emscripten 3.1.64 active, or set `EMSDK`
to its installation. Outputs are unchanged-source `build/native/plink2`,
`build/patched-native/plink2`, and the ES module/wasm/pthread assets under
`dist/plink2`. Archive checksums, sources, notices, build inputs and executable
hashes are recorded in `sources.lock.json` and `build-manifest.json`.

Both comparison executables use the same pinned zlib and NOLAPACK profile.
The compiler supplies `math.h` to C++ translation units because the pinned
NOLAPACK header uses `fabs` without declaring it; native sources stay unchanged.
The native baseline retains upstream source and floating-point setup. The
patched-native and wasm builds use `PLINK2_WASM_PORT`: the floating-point setup
preserves the host's default subnormal behavior instead of enabling flush-to-zero,
and CPU detection defaults to one compute thread instead of Linux affinity calls.
Neither change replaces statistical algorithms. Numeric validation must compare
both baselines; successful compilation alone does not establish equivalence.

WASM uses `-msimd128 -msse` for upstream's 32-bit SSE compatibility path,
`-ffp-contract=off`, real pthreads, and `PROXY_TO_PTHREAD`. It requires SIMD128
and SharedArrayBuffer; browsers must serve COOP/COEP headers. The eight-worker
pool has strict exhaustion checks. A separate compute pthread runs main and
upstream I/O can use helper threads even with `--threads 1`.

The heap starts at 64 MiB and can grow to 2 GiB. Main and pthread stacks are
8 MiB. Upstream's minimum main workspace is 640 MiB, with additional overhead
for stacks, I/O and staged files; examples use `--memory 640 require --threads 1`.
Allocation failures return through upstream error paths. This does not provide
an out-of-core browser filesystem or support arbitrarily large cohorts.

`mailbox.js` keeps the proven featureCounts workaround for browser-main-worker
`Atomics.waitAsync` notifications. The modular runner must wait for `onExit`
(`waitForExit: true`) after `callMain`, and terminate pthreads after each run.
Complete corresponding source archives and license notices accompany the binary.
