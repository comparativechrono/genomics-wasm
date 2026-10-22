# ExpansionHunter 5.0.0 for WebAssembly

This build pins the official 5.0.0 release and its upstream Boost 1.73.0,
HTSlib 1.10.2, spdlog 1.6.1 and Abseil 20210324.2 dependencies. The release's
GraphTools and JSON sources remain bundled. Compression uses pinned zlib 1.3.1,
bzip2 1.0.8 and XZ/liblzma 5.4.6. Archive bytes, sizes and SHA-256 values are
recorded in `versions.lock.json`; downloads are checked before use.

```sh
bash scripts/setup-emsdk.sh
python3 scripts/build-expansionhunter.py all
```

Python 3.11+, a native C/C++ compiler, make, CMake 3.13+, patch, curl and Node are
required. Emscripten is pinned to 3.1.64. `GENOMICS_EMSDK_DIR` can select an
existing installation, `CMAKE` can select its executable, and `JOBS` controls
compilation concurrency (default 2).
The modes `native`, `patched-native`, `wasm` and `all` select comparison targets.

The direct build compiles the same upstream application and GraphTools source
files while replacing the old CMake network-download orchestration. It does not
download or execute an upstream desktop binary. The native target retains the
original application sources and thread dispatch. Patched native and WASM use
the same synchronous dispatch adapter, invoking the original one-worker
callbacks. Statistical and graph-alignment implementations remain upstream.
Only `--threads 1` is supported by these serial targets; other counts fail with
a clear diagnostic. Seeking and streaming analysis and both upstream aligners
remain available. See `PORTABILITY.md` for the scope of the changes.

Native programs are written to `build/native/expansionhunter/ExpansionHunter`
and `build/patched-native/expansionhunter/ExpansionHunter`. The distributable
`dist/expansionhunter` contains an ES-module factory, WASM, pinned source
archives, notices and a hash-bound build manifest. `build-provenance.json`
records the exact compilation flags and source changes for each target. The
build refuses to publish a manifest if any recipe or tool input changes during
compilation.

The normal CLI accepts an aligned BAM/CRAM, local reference FASTA, variant
catalog JSON and output prefix. Seeking requires an alignment index. A reference
FASTA index is strongly recommended. Output files are `<prefix>.vcf`,
`<prefix>.json` and `<prefix>_realigned.bam`. The BAMlet is not automatically
coordinate sorted or indexed; use SAMtools before passing it to REViewer.
The output prefix's parent directory must already exist.

Execution is serial in a fresh worker, on ordinary static HTTP without
SharedArrayBuffer or cross-origin isolation. Local files and indexes must be
supplied explicitly; remote HTS protocols are disabled. WASM linear memory is
limited to 2 GiB and browser-mounted files consume additional memory. Start with
a small set of loci and the required regional inputs; this build does not claim
whole-genome browser capacity or clinical validation.

The pinned release's `LICENSE.txt`, README and application source headers specify
Apache 2.0. Its older `COPYRIGHT.txt` still contains an earlier GPL notice; the
file is retained unmodified alongside the full release sources. Current upstream
master has a different license and is not used. Third-party notices, including
the bundled GraphTools, sparsepp, CTPL and JSON notices, are preserved.
