# KING 2.3.2: local personal-use build

This integration compiles the authentic September 8, 2023 KING 2.3.2 C++
source for personal use. It is not a redistributable release. The source
archive's `Main.cpp`, `KingCore.cpp`, and other files require prior written
permission for redistribution and allow modifications only for personal
use. The bundled MERLIN-derived files contain their own restrictions.
The download page's research-use statement does not establish permission
to distribute a modified WebAssembly build. No source, binary, or release
ZIP is committed or published by this recipe. Obtain clarification and
permission covering KING and the bundled code before redistributing them.

Upstream sources:

- https://www.kingrelatedness.com/Download.shtml
- https://www.kingrelatedness.com/history.shtml
- https://www.kingrelatedness.com/executables/KING2.3.2code.tar.gz

`versions.lock.json` pins the source bytes and version, zlib 1.3.1, and
Emscripten 3.1.64. The recipe checks the source version and SHA-256, safely
extracts it into ignored local build directories, and retains every
upstream copyright notice. No upstream source files are stored here.

```sh
bash scripts/setup-emsdk.sh
JOBS=2 python3 scripts/build-king.py all
```

Individual modes are `native`, `patched-native`, and `wasm`. Native binaries
are written to `build/king/native/king` and
`build/king/patched-native/king`; the local browser module is written to
`dist/king/2.3.2/king.mjs` and `king.wasm`. Build manifests bind source
hashes, build inputs, compiler, and output hashes. The pristine native
reference uses upstream's OpenMP compilation profile and is validated
with `--cpus 1`. The patched-native and WebAssembly profiles use a small
single-thread compatibility header and ignore parallel pragmas. They
still execute the initialization, output headers, and cleanup inside
upstream's OpenMP guards. Disabling those guards entirely omits output
headers in upstream KING. Requests for `--cpus` values other than 0
(automatic) or 1 fail explicitly in the browser profile. All profiles
use bundled matrix routines without external LAPACK.

The port uses a dedicated worker, ordinary HTTP, and a fresh module per
command. It does not require cross-origin isolation. Its wasm32 memory
ceiling is 2 GiB; practical dataset size is lower because input, output,
and analysis buffers share that space. Browser support does not imply
biobank-scale performance or validation of every upstream analysis.

Narrow portability transformations are applied only to the
patched-native and WebAssembly copies. The 64-bit genotype algorithms
are selected from the width of `unsigned long long`, independently of
pointer width. The decimal-conversion accumulator is widened so unsigned
32-bit values above one billion do not overflow on wasm32. The local
`--noscreen` and `--invnorm` option storage types are corrected to match
the parser's integer and boolean access respectively. This avoids an
integer write through a pointer to a one-byte boolean. Both patched
profiles reject `--rplot`, `--pngplot`, and `--rpath` explicitly because
they require external R and PDF conversion processes. Plotting is not
silently reported as successful. Compression is enabled explicitly in
all profiles; no upstream operating-system detection is relied upon.
Every build runs the real KING string conversion code on native or Node
WebAssembly for seven unsigned integer boundary values, including one
billion and `UINT_MAX`. The unmodified wasm32 conversion fails this
regression even though the original LP64 native code passes it.

The browser profile uses the bundled non-LAPACK matrix implementations.
Only analyses exercised by the validation reports should be treated as
verified. The original native executable may offer plotting through
locally installed R, unlike the browser-equivalent patched profile.

The upstream 2.3.2 default screening path can miss between-family
relationships in cohorts with fewer than 100 genotyped samples. This
port retains that upstream algorithm rather than silently changing it.
Use `--related --noscreen 1` for small cohorts; the demo and small-cohort
validation use that path. Default screening must be evaluated using the
separate larger synthetic cohort. Do not treat small-cohort default
`--related`, `--unrelated`, or `--cluster` results as validated simply
because the command exits successfully.
