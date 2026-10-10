# BWA-MEM2 for WebAssembly

This package builds BWA-MEM2 2.2.1 for browsers and Node.js from pinned upstream
source. It is a separate program from BWA and runs the upstream BWA-MEM2
indexing and alignment algorithms. It supports `index`, single-threaded `mem`
and `version` on small references and read sets that fit in browser memory.

## Build

Install Clang, GNU Make, Python 3, curl, patch and Emscripten 3.1.64 on x86-64 Linux.
Activate Emscripten or set `EMSDK` to its installation directory, then run:

```sh
scripts/build-bwa-mem2.sh all
```

The script also accepts `native`, `patched-native` or `wasm`. `JOBS` sets the
number of compiler processes. `BWA_MEM2_BUILD_ROOT` selects an absolute build
directory, otherwise `build/bwa-mem2` is used. The source versions, archive
hashes and compiler version are recorded in [sources.lock.json](sources.lock.json).
The safestringlib revision is the submodule commit recorded by upstream 2.2.1.
All variants link the same safestringlib and zlib sources.

| Build | Output | Purpose |
| --- | --- | --- |
| Native | `build/native/bwa-mem2` | Unmodified upstream sources, native SSE4.1 |
| Patched native | `build/patched-native/bwa-mem2` | Port changes, native SSE4.1 |
| WebAssembly | `dist/bwa-mem2/bwa-mem2.mjs`, `dist/bwa-mem2/bwa-mem2.wasm` | Port changes, WebAssembly SIMD128 |

Clang avoids an upstream `__rdtsc` declaration conflict with recent GCC
versions. If Clang is absent from `PATH`, the script can use the native Clang
executable included with the selected Emscripten SDK. The C++ standard is
fixed to GNU C++11, matching the source's use of `register`. The build provides
standard `stdlib.h` and `ctype.h` declarations to safestringlib without changing
its source. Native baseline source code remains unchanged.

## Use

The default module export is the asynchronous `createBwaMem2` factory, with
`FS` and `callMain` available for integration. Use a fresh module instance for
every command. The shared `ToolRunner` interface manages this in a worker and
supports files, standard streams, cancellation and timeouts.

```js
["index", "reference.fa"]
["mem", "-t", "1", "-K", "10000000", "reference.fa", "reads_1.fastq", "reads_2.fastq"]
["version"]
```

Preserve all five index files between the indexing and alignment commands:

- `reference.fa.0123`
- `reference.fa.amb`
- `reference.fa.ann`
- `reference.fa.bwt.2bit.64`
- `reference.fa.pac`

Create indexes with BWA-MEM2 for BWA-MEM2 alignment. Do not substitute an
original BWA index. `mem` writes SAM to standard output. Gzip-compressed
FASTA and FASTQ are supported through zlib. URL and shell process inputs are
rejected; applications should fetch files and provide their bytes. Shell
redirection is handled by the application, not the argument array.

The module requires WebAssembly SIMD support. It uses a growing 32-bit heap
capped at 2 GiB and an 8 MiB stack. Upstream's scratch buffers alone reserve
several hundred megabytes, even for small inputs. Input files and JavaScript
copies use additional memory outside the heap. Whole human genomes and
memory-constrained devices are outside this package's supported scope.
No shared-memory browser setup or cross-origin isolation is required.

The WebAssembly build explicitly enables `ABORTING_MALLOC`. If the browser
cannot supply more heap memory, the command fails instead of returning a null
allocation pointer to upstream routines that do not check it. This prevents
those routines from continuing with invalid memory. It does not change the
indexing or alignment algorithms or guarantee that a particular input fits.

## Port changes

All upstream source changes are in [browser.patch](browser.patch), with a small
profiling helper in [port.h](port.h).

- The existing SSE4.1 kernels compile to WebAssembly SIMD128 using Emscripten's
  intrinsic support. AVX2 and AVX512 kernels are not selected.
- Work callbacks run sequentially in the original 512-read blocks. The three
  read, align and write stages run in order for every input batch. No reads or
  batches are skipped. The existing algorithm and buffer capacities are retained.
- `-t` accepts only `1`. The program propagates alignment command errors to
  its exit status. Native baseline behaviour is retained separately for comparison.
- CPU cycle counters and the one-second CPU-frequency calibration are replaced
  with monotonic-clock profiling. CPU topology detection is removed from the
  single-threaded port.
- FM-index population counts explicitly use 64-bit values. Upstream uses
  `unsigned long`, which is only 32 bits in wasm32. The index checkpoint layout
  is checked at compile time.
- Integer formatting uses the appropriate fixed-width types across platforms.
  SAM position formatting retains 64-bit inputs.
- Tree nodes align their keys and child pointers to each type's requirements.
  This fixes misaligned accesses found by UndefinedBehaviorSanitizer; node
  capacity, key ordering and comparisons are unchanged.
- The file opener supports local files and standard input; subprocesses and
  network access are unavailable inside the compiled module.

Timing values are profiling data and are not part of alignment comparisons.
A native speed advantage over BWA does not imply an advantage in the browser.

## Validation

From the repository checkout, run the shared three-way differential suite and
the memory-failure check:

```sh
node scripts/validate.mjs --tool bwa-mem2 --engine node
node scripts/test-bwa-mem2-memory.mjs
```

The suite compares original native, patched native and WebAssembly builds
using the same real NA12878 and yeast mitochondrial inputs. It checks every
index byte, complete SAM records and tags, paired and single reads, parameter
changes, multiple batches and exchanging native/WebAssembly indexes. A higher
match-score case exercises 16-bit score processing. Browser runs in Chromium,
Firefox and WebKit are separate CI gates. The suite writes the exact commands,
input and binary hashes, outputs and comparisons under `reports/`.

The memory test uses the actual module in a disposable worker whose heap cannot
grow. The command must reject with an out-of-memory error, and a fresh worker
must still run successfully. It checks failure handling without changing the
alignment algorithm or the numerical comparisons.

The Node.js v24.19.0 run passed all 24 cases on 10 October 2026. All five
index files were byte-identical across the three builds on both datasets.
All SAM fields and tags matched for paired and single reads, changed seed and
score parameters, higher match scores (`-A 3`), multiple batches (`-K 10000`),
repeated commands, exchanged native/WebAssembly indexes and empty reads.
Missing-input exit status matched. Both port builds rejected `-t 2` with exit
status 2, as required by the single-thread restriction. The report is
`reports/node/summary-bwa-mem2-all.json`. The SAM comparator excludes only the
command-line metadata in `@PG CL`.

These results establish equivalence for those inputs and options in Node's
WebAssembly engine. They do not claim browser execution; Chromium, Firefox
and WebKit results are recorded separately by CI. Arbitrary parameter
combinations and whole-genome workloads remain outside the measured scope.

## Sources and licences

BWA-MEM2 is MIT-licensed; [LICENSE](LICENSE) preserves the upstream licence.
Its source archive retains the additional notices in individual source files.
safestringlib carries its upstream licence and copying notices; zlib uses the
zlib licence. The helper and build infrastructure are MIT-licensed.

Each package includes the compiled module, full pinned source archives,
checksums, licences, patches, build instructions and the shared runtime. Keep
those files together when redistributing the package. The packaged archives
can rebuild without downloading upstream sources when placed in the supplied
`build/downloads` layout; Emscripten and a native toolchain are still required.
