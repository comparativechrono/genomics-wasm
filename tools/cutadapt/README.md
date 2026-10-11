# Cutadapt

This package runs Cutadapt 5.2 in a browser or Node.js using Pyodide 314.0.7 and CPython 3.14.2. Cutadapt and dnaio's Cython extensions are compiled to WebAssembly from their original sources. The adapter matching, quality trimming, read filtering and reporting code is upstream Cutadapt, with one change to reject multiprocessing requests.

The pinned dependencies are dnaio 1.2.4 and xopen 2.1.0. The runtime, wheels and Node.js `ws` dependency are served locally. Running a command needs no package download or external service. See `runtime/CUTADAPT.md` for the JavaScript interface and `examples/cutadapt/` for a browser example.

## Build

Use Linux x86_64 with CPython 3.14.2, Node.js, a C compiler, `patch` and `tar` installed:

```sh
python3.14 scripts/build-cutadapt.py all
```

The script verifies pinned source archives, creates an isolated Python build environment, and installs the pinned Emscripten 5.0.3 SDK under `build/cutadapt/`. It builds an unchanged native baseline, a native build with the browser restriction, and the browser wheels. The repository's other Emscripten installation is not changed.

The outputs are `dist/cutadapt/`, `build/native/cutadapt` and `build/patched-native/cutadapt`. Native launchers use `python3.14` on `PATH`, or the interpreter named by `CUTADAPT_PYTHON`. They require exactly CPython 3.14.2. Their package trees are isolated from system packages; bytecode generation is disabled so validation does not change the recorded files.

`sources.lock.json` pins all source archives, compiler downloads and Python build dependencies. The build uses the hashed wheels in `build-requirements.lock` without resolving newer dependencies. Archives can be reused from `dist/cutadapt/sources/` when rebuilding a standalone package. Host compiler binaries remain pinned build-time downloads and are not redistributed. Every source file, runtime asset and native package file is bound to `build-manifest.json` by SHA256.

## Limits

Only one processing core is supported. The browser and patched native build reject `--cores 0` and `--cores` values greater than one with exit status 2; the default and `--cores 1` work. This is a command-line restriction, not an alternate trimming implementation.

With one core, upstream Cutadapt requests zero compression threads. xopen uses its supported Python standard-library compression path; optional native ISA-L and zlib-ng accelerators are absent from both comparison environments and the browser. Gzip, bzip2, xz and zstd support comes from the pinned Python runtime. Compressed bytes may contain format metadata, so comparisons should check decoded records and reports as well as container integrity.

Inputs and outputs live in the worker's in-memory filesystem. Their size is limited by available browser memory. Each command starts a fresh worker, and cancellation terminates that worker. There is no access to the user's host filesystem or external Unix commands. This does not require SharedArrayBuffer or cross-origin isolation.

## Validation

The validation suite compares unchanged native, patched native and WebAssembly results on real human and yeast reads. It checks ordered read names, sequences, qualities and JSON statistics, with explicit tests for option effects and unsupported multiprocessing. Node.js, Chromium, Firefox and WebKit reports record the exact source and binary identities. A release requires all configured comparisons and browser checks to pass; the small fixtures do not establish whole-dataset performance or cover every possible command.

Preserve the upstream notices and source archives when redistributing. `THIRD_PARTY_LICENSES.txt` and `runtime-notices/README.md` explain their scope.
