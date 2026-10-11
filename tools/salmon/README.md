# Salmon in WebAssembly

This package builds Salmon 2.8.0, the upstream Rust implementation, for browsers and Node.js. It runs the upstream transcriptome indexer, selective alignment, and abundance estimation. Quantification is performed on the supplied reads; no results are precomputed.

The validation suite checks 31 cases on real human and yeast data against untouched and patched native Salmon in Node.js, Chromium, Firefox and WebKit. It requires exact numerical agreement for these datasets. The release gate also requires the browser example tests to pass; see the validation reports for a particular build.

## Requirements

The browser build uses real pthread workers and shared WebAssembly memory. Run it with `new ToolRunner({ moduleUrl, waitForExit: true })`: the command runs on a pthread, and the runtime must wait for its exit before reading output files. Browser hosting must provide these response headers:

```
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Embedder-Policy: require-corp
```

Use a browser with WebAssembly threads, SIMD, and `crossOriginIsolated === true`. Ordinary GitHub Pages hosting does not provide these headers. The [browser example](../../examples/quant/README.md#hosting) includes a static server and an optional service worker that supplies them within the example's scope. Node.js does not require the headers.

The command accepts one compute worker (`--threads 1`). Omitting the thread count selects one worker. The indexer, input parser, and inference libraries still use their normal auxiliary threads. Requests for more compute workers fail with an explanation.

The maximum WebAssembly memory is 2 GiB. Input files, index files, temporary files and working memory all consume browser memory. This build is intended for manageable transcriptome subsets. It is not a promise that a complete human transcriptome and a large RNA-seq run will fit. `--ramLimit` controls an index-sort allocation budget; it is not a hard limit on total Salmon memory. Native operating-system memory diagnostics are unavailable in WebAssembly; upstream RSS logging may report zero and Emscripten may warn that `getrusage` is unsupported. These diagnostics do not describe browser memory use.

## Commands

Build an index from transcript sequences, then quantify paired reads:

```sh
salmon index -t transcripts.fa -i index -p 1 --ramLimit 1
salmon quant -i index -l A -1 reads_1.fastq -2 reads_2.fastq -o quant -p 1
```

Single-end reads need a fragment-length prior, for example:

```sh
salmon quant -i index -l U -r reads.fastq --fldMean 200 --fldSD 20 -o quant -p 1
```

The main abundance table is `quant/quant.sf`. Salmon 2.8.0 uses its upstream deterministic quantification path by default; `--useEM` selects EM instead of the default VBEM. Upstream help describes additional options. Options accepted by the upstream release but not implemented there retain their upstream warnings; this package does not implement them independently.

Build an index within the same architecture that will read it. Salmon's index dependencies record the width of `usize`: a 64-bit desktop index cannot be loaded by the 32-bit browser build. The upstream compatibility check remains in place. Browser-built indexes can be saved and loaded in later browser sessions.

## Build

The build host needs a C/C++ compiler, CMake, pkg-config, patch, and Python 3.12 or later. Install Emscripten 3.1.64 and the pinned Rust toolchain:

```sh
rustup toolchain install nightly-2026-09-25 --profile minimal --component rust-src --target wasm32-unknown-emscripten
scripts/build-salmon.sh all
```

Build modes are `native`, `patched-native`, `wasm`, and `all`. The native baseline uses unmodified upstream source and locked dependencies. The patched native build uses the same portability changes as the WebAssembly build. Rust's standard library is rebuilt with atomics for WebAssembly threads. LTO is disabled to avoid exchanging LLVM bitcode between the Rust and Emscripten compiler versions. Rust retains upstream release optimization; Emscripten's optional post-link optimization is disabled because its older Binaryen does not recognize feature metadata emitted by this Rust compiler.

The source archive, dependency archives, patches, lock files and build instructions accompany the package. Their hashes are recorded in `sources.lock.json`. `build-manifest.json` ties the distributed module to the source and build files used to produce it.

## Portability changes

The port keeps the upstream index, mapping, and statistical algorithms. Its changes concern platform support:

- Disable an unused epserde memory-mapping feature. Salmon's index readers use full deserialization; ssHash's separate file-mapping code remains enabled.
- Store packed contig entries in 64-bit words on WebAssembly, including values that combine a transcript number and position into more than 32 bits.
- Use explicit 64-bit storage for GC rank vectors, whose Rank9 representation requires 64-bit words.
- Check multiplication of index-sort memory budgets so large values cannot wrap around on 32-bit targets.
- Supply missing imports in upstream radix-sort code for 32-bit integers.
- Omit native operating-system thread-priority adjustment in browser workers.
- Build needletail as a Rust library without its unused shared-library output.
- Use the upstream system-allocator option and set a bounded browser compute-worker count.
- Use Emscripten's existing `postMessage` mailbox notifications for the browser's main runtime worker. WebKit testing exposed intermittent `Atomics.waitAsync` wakeup stalls; an unrelated message immediately resumed them. The Salmon-only `mailbox.js` linker library preserves the SDK's `waitAsync` path for Node.js and child pthreads without changing global JavaScript APIs.

See `browser.patch` and `mailbox.js` for the complete changes. Numerical agreement, input handling and index round trips are checked using real human and yeast RNA-seq reads and independent native builds. Numerical tolerances, where needed, are recorded in the validation reports.
