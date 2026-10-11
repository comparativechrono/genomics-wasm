# StringTie in WebAssembly

This independent package builds upstream StringTie 3.0.3 as an ES module for
browsers and Node.js. It assembles transcripts and estimates expression from
coordinate-sorted RNA alignments using StringTie's original algorithms. Input
files stay in the caller's process or browser worker.

```sh
stringtie -p 1 -G annotation.gtf -o transcripts.gtf -A abundance.tsv reads.bam
stringtie -p 1 -e -G annotation.gtf -o estimates.gtf -B reads.bam
stringtie --merge -G annotation.gtf -o merged.gtf sample1.gtf sample2.gtf
```

The module retains the upstream command-line interface, including unguided and
guided assembly, expression-only estimation (`-e`), GTF merging, stranded
libraries, long reads (`-L`), mixed short/long reads (`--mix`), nascent RNA modes
(`-N`, `--nasc`), gene abundance tables and Ballgown output. Individual modes
are supported only to the extent covered by the published validation reports;
upstream's presence of an option is not a claim that every combination has been
tested in WebAssembly. The separate optional SuperReads_RNA pipeline is not
part of this module.

Files are read from the module's virtual filesystem. SAM, BAM and CRAM readers
and their bundled compression libraries are compiled in. For CRAM, supply any
required reference FASTA locally with `--ref`; HTTP/S3 fetching and HTSlib
plugins are not enabled. Alignments must satisfy upstream's requirements,
including coordinate sorting and the appropriate strand tags for spliced
reads. This package does not align raw FASTQ reads.

## Building and running

```sh
bash scripts/setup-emsdk.sh
source toolchain/emsdk/emsdk_env.sh
bash scripts/build-stringtie.sh all
```

The script also accepts `native`, `patched-native` or `wasm`. It needs a C/C++
compiler, GNU Make, Python 3, curl, tar and patch. The WASM variant requires
Emscripten 3.1.64. `JOBS` controls build parallelism; `STRINGTIE_BUILD_ROOT`
can relocate intermediate build directories.

The exact upstream release archive and zlib archive are verified by SHA-256.
The lock file additionally records hashes of the bundled dependency source
trees: gclib, StringTie's HTSlib, htscodecs, libdeflate, bzip2 and XZ/liblzma.
These dependencies are built for each variant from the same sources. The
upstream makefiles supply the translation-unit lists. The build script passes
the cross compiler through the dependency builds and overrides HTSlib's
x86-specific compiler flags. Compression algorithms are not replaced.

Outputs are `build/native/stringtie`, `build/patched-native/stringtie`, and
`dist/stringtie/stringtie.mjs` with its adjacent `stringtie.wasm`. The WASM
module exports the `createStringTie` factory with `FS` and `callMain`. Use the
repository's `ToolRunner` with its default worker and `waitForExit: false`.
Run every command in a fresh module/worker so global CLI state is isolated.
The package includes the original source archives, patch, licenses and a
manifest binding the compiled assets to their build inputs.

## Portability changes and limits

The normal native baseline compiles unchanged upstream sources with its usual
threaded execution path. Both patched-native and WASM use upstream's
`NOTHREADS` build option, which processes each bundle with the same
`processBundle` implementation on the current thread. `browser.patch` guards
two coverage-output mutex pairs omitted by upstream's serial configuration.
It also rejects a requested thread count above one, rather than accepting a
parallelism setting the serial build cannot honor. No transcript scoring,
assembly or expression estimation code is changed.

The WASM build requires no SharedArrayBuffer, cross-origin isolation headers or
child worker pool. It should run inside a worker to keep the page responsive.
The heap grows up to 2 GiB; input and output files consume additional browser
memory. Small regions and bounded RNA datasets are the initial use case.
Whole-sample capacity must be measured: upstream recommends substantially
more memory for regular full RNA-seq samples. Memory exhaustion terminates the
command with an error; it does not return a partial result as success.

Validation must compare unchanged native, patched-native and WASM using the
same inputs and options. Only documented command-path metadata in GTF comments
may be removed before comparing biological records. Native floating-point
differences, if any, must be reported and justified individually.

The pinned release includes upstream test data. Its `mix_short.bam` is a real
SRR4235527 RNA-seq alignment subset on hg38 chr21, with provenance recorded in
the BAM program header. `mix_guides.gff` provides annotation for that region.
Some other upstream fixtures, including `long_reads.bam`, are simulated and
must not be described as empirical samples. Upstream's shell test script does
not exit nonzero on every output mismatch, so validation must inspect records
directly.

StringTie is MIT-licensed. Dependency notices are in
`THIRD_PARTY_LICENSES.txt`; complete corresponding source archives accompany
the module.
