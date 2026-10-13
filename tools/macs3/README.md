# MACS3 3.0.5 in WebAssembly

This port compiles the original MACS3 Cython/C and hmmlearn C++ implementations
for Pyodide. It includes all 14 upstream commands: `callpeak`, `bdgpeakcall`,
`bdgbroadcall`, `bdgcmp`, `bdgopt`, `cmbreps`, `bdgdiff`, `filterdup`, `predictd`,
`pileup`, `randsample`, `refinepeak`, `callvar`, and `hmmratac`. MACS3's existing
fermi-lite assembly, Smith–Waterman alignment, probability calculations,
peak-calling and HMM algorithms remain in the implementation. The source-level FRAG cell-by-peak matrix API retains its pandas, AnnData
and HDF5 dependencies; it is not exposed as an added browser CLI command.

## Build

On Linux x86_64, provide CPython **3.14.2**, Node, GCC/G++, make, patch, and the
usual C development headers including zlib. The build downloads only archives
listed with SHA-256 checksums in `sources.lock.json`; Python dependency
resolution is disabled. Emscripten **5.0.3** and Pyodide **314.0.7** are pinned.

```sh
python3.14 scripts/build-macs3.py all
export MACS3_PYTHON="$(command -v python3.14)"
build/native/macs3 --version
build/patched-native/macs3 --version
node scripts/validate-macs3.mjs
node scripts/test-macs3-package.mjs
python3 scripts/package-macs3.py
```

The build creates original-native and patched-native comparison environments
under `build/`, and the browser/Node bundle under `dist/macs3/`. Standalone
source archives in the distribution can repopulate an empty download cache;
compiler executables are deliberately excluded and must be downloaded again.
Native HDF5 1.12.1 and h5py 3.13.0 are compiled locally to match the browser
versions. Native launchers include their HDF5 shared libraries and do not
require packages from the user's Python environment.

The runtime dependency list is locked in `runtimePackages`. It currently
contains 18 packages: NumPy 2.4.6, SciPy 1.18.0, six 1.17.0,
python-dateutil 2.9.0.post0, pytz 2026.1.post1, tzdata 2025.3,
pandas 3.0.2, joblib 1.5.3, threadpoolctl 3.6.0, scikit-learn 1.8.0,
pkgconfig 1.6.0, h5py 3.13.0, packaging 26.1, array-api-compat 1.11.2,
natsort 8.4.0, AnnData 0.10.9, hmmlearn 0.3.3, and MACS3 3.0.5.
The `tzdata` wheel satisfies pandas' Emscripten-specific metadata requirement.
`check-wheel-dependencies.py` checks the actual bundled wheel metadata.

## Portability changes

`browser.patch` makes four changes, used by both the patched-native and browser
MACS3 builds:

- BAM/BAI virtual offsets and their counters use explicit `uint64_t`, preserving
  upstream LP64 values on wasm32. A BAI virtual offset is not a pointer and must
  retain all 64 bits even for small compressed BAM files.
- Memory logging reports `RSS unavailable` on Emscripten, where Python has no
  `resource` module. Native resource reporting is unchanged.
- Both `callvar` process-pool maps become serial maps. The upstream requested
  partition count, range boundaries, per-range function and result order remain
  unchanged. No genotype or assembly algorithm is replaced.
- The unused test-program `main` in `Signal/swalign.c` is renamed to
  `macs3_swalign_test_main`, so Emscripten can link the file into a Python
  extension. Smith–Waterman functions are unchanged.

`integer-width.patch` preserves the native LP64 width for fragment-length
sums, genome-wide q-value ranks/totals and weighted FRAG read counts. These
quantities can exceed 32 bits without requiring large in-memory arrays. It also
matches a Savitzky–Golay coefficient buffer's declared type to its existing
NumPy `i8` dtype. No formula, rounding, cutoff or array value is changed. Native
and browser validation includes compact fixtures whose accumulated lengths
and q-value weights total five billion.

`hmmlearn-wasm.patch` guards clearing the `FE_DIVBYZERO` status flag when that
flag does not exist on WebAssembly. The original logarithm calculations,
including `log(0) == -inf`, and all HMM algorithms remain unchanged. The patch
has no effect on native systems that define the flag.

The build uses the supplied wasm32 NumPy headers and compiles the pinned zlib
source as relocatable WebAssembly. MACS3's upstream SIMDe code remains intact;
fermi-lite is already configured by MACS3 to use one thread. The original-native
launcher chooses Python's real `spawn` multiprocessing backend, avoiding
forkserver socket requirements in restricted hosts while retaining upstream
process pools. Both native environments fix BLAS/OpenMP thread counts to one.

## Runtime and validation

The JavaScript runner verifies the runtime manifest, CLI file and every wheel
before loading the local Pyodide distribution. Stock Pyodide dependencies and
custom wheels are included locally. The CLI runs inside an isolated filesystem;
inputs and outputs are exchanged as bytes. The browser worker is single-threaded
and does not require `SharedArrayBuffer`, COOP or COEP headers.

WebAssembly's memory limit and per-tab browser limits still constrain dataset
size. Peak calling, local assembly, HMM training and dense intermediate arrays
can require substantially more memory than the compressed input. This is a
serial local execution port; no fixed maximum real-world dataset size is
promised.

Support and numerical-equivalence claims must be evaluated using the generated
validation reports, which bind the exact build, input fixtures, comparisons,
file sets and output contents. The validation suite covers the 14 commands,
including non-empty variant output, assembly paths, HMM inference/training,
FRAG inputs, deterministic sampling and independent output invariants.
A successful build or `--help` invocation alone does not establish scientific
or browser parity. Browser coverage is limited to engines recorded in a
passing report; missing engines remain an explicit release gate.

Licence texts and corresponding pinned sources accompany the distribution.
See `THIRD_PARTY_LICENSES.txt`, `runtime-notices/README.md`, and
`scientific-notices/README.md` for component provenance and source obligations.
