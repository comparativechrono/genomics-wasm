# mosdepth in WebAssembly

This port compiles mosdepth 0.3.14 with Nim 2.2.12, HTSlib 1.24 and
Emscripten 3.1.64. It runs the upstream coverage, CIGAR, overlapping-mate,
fragment and summary calculations on files in an in-memory filesystem.
There are no stored results or replacement depth algorithms.

## Build and run

On Linux x86-64, with a C compiler, make, Python 3, curl, tar, patch and
Node.js available:

```sh
bash scripts/setup-emsdk.sh
./scripts/build-mosdepth.sh all
./build/native/mosdepth -t 0 -b regions.bed output input.bam
```

The build downloads only the checksum-pinned archives in `sources.lock.json`.
The Nim distribution includes its compiler and standard-library sources;
Nimble is not used. Separate `native`, `patched-native` and `wasm` build modes
are available. The output module is `dist/mosdepth/mosdepth.mjs`, beside
`mosdepth.wasm`. The native programs are `build/native/mosdepth` and
`build/patched-native/mosdepth`.

Use `ToolRunner` from `runtime/index.mjs` with the module URL, an argument
array, input files and the output filenames to retrieve. The ordinary
runner is sufficient: this port uses one worker, does not create pthreads,
and does not require cross-origin isolation. An example command is:

```text
mosdepth -t 0 -b regions.bed output input.bam
```

Supply `input.bam.bai` alongside a sorted BAM. For CRAM, supply its `.crai`,
the local reference FASTA and its `.fai`, and add `--fasta reference.fa`.
Reference and alignment sequence names must match. Files are local to each
command; remote URLs and automatic reference downloads are unavailable.

## Options and limits

The port retains BED regions and fixed windows, per-base depth, thresholds,
quantization, mean/median summaries, flag/MAPQ/read-group filters, chromosome
selection, fast mode and fragment mode. `--threads 0` is required (also the
upstream default); this controls BAM decompression threads. Other thread
counts return status 2 with a clear diagnostic. `--d4` also returns status 2:
the optional D4 backend is not built. These restrictions apply to the patched
native comparison binary too; the unchanged native baseline retains its
upstream command handling.

HTSlib is linked statically with zlib and its bundled CRAM codecs. The optional
bzip2, LZMA, libdeflate, libcurl and plugin features are disabled in all three
builds. BAM and supported CRAM files are decoded normally; CRAM blocks that
require the omitted bzip2 or LZMA codecs cannot be read. A local FASTA does
not remove that codec limitation.

mosdepth allocates an array for a whole reference sequence. Its main coverage
array alone needs approximately four bytes per base of the longest `@SQ`
sequence. A small BAM subset retaining a full human chromosome's length can
therefore still need over a gigabyte. The module's linear memory is limited
to 2 GiB, and inputs, outputs and browser copies need additional memory.
Use genuinely small reference sequences when working with limited memory.

The compiler retains 64-bit Nim integers and the native C layout, then
Emscripten's `MEMORY64=2` pass lowers memory addresses to ordinary wasm32.
This preserves cumulative lengths above 2^31 and depth totals above 2^32;
the build runs a check against the unchanged upstream `depth_stat` type.
It does not require browsers to support native memory64.
This preserves cumulative statistics, rather than extending genomic coordinate
support: some upstream hts-nim wrappers still narrow coordinates to signed
32-bit integers, and chromosome-sized coverage arrays also impose the memory
limit described above.

## Compatibility changes and checks

The unchanged native baseline uses upstream's recommended `refc` memory
manager. The patched native and WebAssembly builds use Nim's ORC manager:
the conservative `refc` stack scanner does not correctly retain objects in
the tested optimized WebAssembly build. ORC tracks object lifetimes without
scanning WebAssembly locals. Upstream mosdepth's memory-manager recommendation
is the reason both native stages are retained in the comparison.

`hts-nim.patch` removes `noInit` from the BAM record-copy result. Under ORC,
that pragma otherwise causes destruction of an uninitialized reference before
the record is allocated. The patch also corrects four HTSlib foreign-function
signatures to the pinned C headers: `bam_endpos`, `sam_itr_queryi`,
`hts_idx_finish` and `hts_idx_save`. These are allocation and ABI fixes;
mosdepth's coverage and statistics code is unchanged. The WebAssembly linker
treats signature mismatch warnings as errors.

`browser.patch` keeps the owning BAM and reference filename strings alive
while HTSlib uses their C-string views. It also checks failed alignment and
BED opens before dereferencing their handles. The unchanged native program
can crash for those missing files; the port returns status 2 with a diagnostic.
Those error-handling checks are assessed separately from native numerical
equivalence, as are the thread and D4 restrictions.

The comparison suite runs the unchanged native program, the patched native
program and the WebAssembly module on the same real human and yeast files.
It checks decoded coverage intervals, distributions, summaries and option
effects, including spliced CIGAR strings, mate overlaps and local-reference
CRAM. Binary compression/index bytes need not be identical for decoded
coverage to be identical. Browser runs use the same comparison contracts.
Consult the generated reports for the exact commands, inputs and outcomes;
a source build alone is not a claim that every browser gate has passed.

`build-manifest.json` records the exact build-input and runtime-asset hashes.
Source archives and full third-party notices accompany the module. Rebuilding
from changed inputs is required before creating a release package.
