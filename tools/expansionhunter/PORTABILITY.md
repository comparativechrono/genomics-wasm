# ExpansionHunter portability changes

`serial.patch` changes dispatch in the seeking worker, streaming worker and locus
initialization code to use `GenomicsSerialThread`. That adapter calls the same
upstream function immediately and has a no-op `join`. The CTPL adapter similarly
invokes the original queued callback with worker index zero. Each streaming
locus queue is drained by its upstream callback before the next insertion, so it
cannot exhaust the active-queue capacity or wait for an absent worker. Queue,
read-pair, graph and genotype calculations remain in their original functions.

The serial targets reject any thread count other than one before analysis.
HTSlib's upstream reader only creates a decompression pool for counts above one,
so the accepted setting stays on its original serial path. Native reference
builds retain the original thread dispatch and are evaluated with one thread.

The C and C++ builds use `_FILE_OFFSET_BITS=64`, `_LARGEFILE_SOURCE`, disabled
floating-point contraction and the same optimization level. C++ exceptions are
enabled in WASM to preserve upstream validation and error handling. The build
preincludes the standard `limits` and `cstdint` headers in all targets because
legacy GraphTools relied on indirect includes; the native source stays unchanged.
The build does not replace graph alignment, likelihoods, confidence intervals, random
algorithms or hash containers with browser-specific implementations.

Remote HTS protocols and HTSlib dynamic plugins are disabled for both comparison
and browser targets. All test inputs must be mounted local files. Original
native, serial native and WASM output comparisons establish the recorded fixture
scope; they do not establish every possible input's behavior or capacity.

The BAMlet writer normally starts a separate consumer thread even with one worker.
The serial patch removes that thread, enqueues each completed record followed by
the existing sentinel, and calls the original queue-draining callback immediately.
Record construction, `bam_write1`, destruction, and the write-error check remain
unchanged. The native oracle retains the original asynchronous writer.

Bundled sparsepp overloads popcount on `uint32_t` and `uint64_t`. Emscripten
uses a distinct 32-bit `size_t` type, making implicit overload selection
ambiguous. The patch enables sparsepp's existing macOS width-explicit branch
for Emscripten, retaining the same bit-count formulas and hash containers.
A focused native/WASM check exercises zero, every single bit, adjacent bits,
all ones, high bits, and deterministic generated patterns against an independent
bit-by-bit count before linking the application.
