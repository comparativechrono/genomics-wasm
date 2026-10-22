# HTS tools with built-in bcftools plugins

The two independent bundles use matching bcftools, SAMtools and HTSlib releases:
1.16 and 1.21. Each includes `bcftools`, `samtools`, `bgzip`, `tabix`, `htsfile`,
and a linkable wasm32 HTSlib SDK. All 39 stock plugins in each release are built
into bcftools, including `split-vep`, `fill-tags`, `trio-dnm2`, `trio-stats`,
`trio-switch-rate` and `parental-origin`. Version 1.16 includes `mendelian`;
version 1.21 includes its upstream replacement `mendelian2`.

## Build

Use Python 3.11 or newer, a native C compiler, make, patch, curl, Node.js and
Emscripten 3.1.64. The source release archives and compression dependencies are
locked by SHA-256 and size in `versions.lock.json`. The repository SDK setup is:

```sh
bash scripts/setup-emsdk.sh
source toolchain/emsdk/emsdk_env.sh
python3 scripts/build-hts.py all --version all
```

On filesystems that cannot preserve archive ownership, run setup with
`TAR_OPTIONS='--no-same-owner --no-same-permissions'`. The build also discovers
an SDK already installed at `toolchain/emsdk`, or `GENOMICS_EMSDK_DIR`.
`JOBS` controls compilation concurrency (default 2). Each build uses a fresh
source tree; previous compiled objects are never reused. Downloaded archives
are verified again before reuse. No build requires autoconf.

`--version 1.16` and `--version 1.21` select one bundle. The modes `native`,
`patched-native` and `wasm` build individual comparison targets. `all` builds
all three targets and records both native inventories in the WASM manifest.

* `build/native/hts-V`: pristine upstream executables and all stock `.so`
  plugins. Set `BCFTOOLS_PLUGINS` to its absolute `plugins` directory when
  running the upstream oracle.
* `build/patched-native/hts-V`: native executables with the same static plugin
  loader and portability changes as WASM.
* `dist/hts-V`: five ES modules and WASM files, source archives, required
  notices, built-in plugin inventory, SDK and a hash-bound build manifest.

The build refuses to publish a manifest if its input files changed while
compiling. Validation and packaging should consume the completed matching
version bundle, not files copied between versions.

## Plugin implementation

`prepare-plugins.py` reads each upstream plugin's actual Makefile recipe.
It compiles the plugin, its private helper sources and its own version object,
enumerates all defined global symbols, and recompiles with a unique prefix for
every such symbol. This preserves the per-plugin state and helper isolation
normally supplied by a separate shared library. A generated registry replaces
only the dynamic loader boundary; upstream argument parsing, filters, plugin
lifecycle and scientific code remain in use. The inventory and source hashes
are recorded in `builtin-plugins.json`. No `.so` files, runtime downloads or
`BCFTOOLS_PLUGINS` path are needed in WASM. External plugins cannot be loaded.

## Portability and supported features

Both bundles use 32-bit pointers with 64-bit `off_t` and `hts_pos_t`; the build
checks those widths at compile time. Reviewed changes preserve native LP64
behavior for cumulative depth and PL counters, BGZF seek offsets, optical
duplicate coordinates and estimated library size. GTisec allocates its
64-bit table entries using their actual type size. These changes apply to
patched-native and WASM, leaving the original native oracle untouched.
SAMtools subsampling seed parsing also retains the native 32-bit unsigned
seed range. Commands that use the C library random-number generator can
choose different records on Emscripten and a native host despite identical
nonzero seeds; this build does not replace upstream random-number algorithms.

SAM, BAM, CRAM, VCF, BCF, BGZF, tabix and local indexed access are supported.
The builds include pinned zlib 1.3.1, bzip2 1.0.8 and liblzma from XZ 5.4.6,
plus the upstream release's bundled htscodecs. Browser execution is serial,
requires no SharedArrayBuffer or cross-origin isolation, and has a 2 GiB
maximum WASM linear memory. Use default thread counts; parallel worker
requests are unsupported. Input and output files also consume browser memory.

Explicit thread requests preserve upstream error handling: tested bcftools
`view --threads 2` and SAMtools `view/sort -@ 2` fail with thread-pool errors.
`bgzip -@ 2` prints a thread-pool error and completes serially, as its upstream
caller does not treat pool creation failure as fatal. No command should be
assumed to run in parallel in these bundles.

SAMtools sorting invokes its original sorting worker directly for a single
worker. Upstream otherwise starts a pthread even for that case, which cannot
run in these builds. The serial dispatch preserves the original sort keys,
algorithms, temporary-file handling and output records; the unsorted-read
regression verifies ordering and successful indexing in both pinned versions.

Remote HTTP/FTP/S3/GCS access, HTSlib external protocol plugins, libdeflate,
interactive curses display, optional GSL-dependent `polysomy`, and optional
Perl filtering are disabled or not enabled. SAMtools text/HTML `tview` remains
available. The core command implementations are otherwise retained. The
browser runner mounts user-supplied local files and returns outputs; it does
not make data-service requests.

The SDK's `htslib/README.md` gives include and link arguments. Its static
archives contain WebAssembly objects and require the pinned Emscripten
toolchain; they are not host-native libraries.

## Licensing

bcftools and SAMtools use MIT/Expat terms, HTSlib uses MIT/BSD terms, and
htscodecs uses BSD terms. Source release archives and their complete notices
are included per version. Compression dependencies carry their upstream
zlib, bzip2 and XZ/liblzma notices. See `versions.lock.json` and `notices/` for
the exact files required in every bundle. Emscripten runtime notice is also
included. No GSL code is linked.
