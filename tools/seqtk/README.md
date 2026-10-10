# seqtk for WebAssembly

This package builds seqtk 1.5 for browsers and Node.js. It includes the upstream
command-line program, including `trimfq`, FASTA/FASTQ conversion, reverse
complement, sequence selection, sampling and sequence statistics. Trimming uses
seqtk's original quality trimming algorithm.

## Build

Install a C compiler, GNU Make, Python 3, curl, patch and Emscripten 3.1.64.
Activate Emscripten or set `EMSDK` to its installation directory, then run:

```sh
scripts/build-seqtk.sh all
```

The script also accepts `native`, `patched-native` or `wasm`. Native builds go to
`build/native/seqtk` and `build/patched-native/seqtk`. Browser files go to
`dist/seqtk/seqtk.mjs` and `dist/seqtk/seqtk.wasm`. `JOBS` controls dependency
compilation; `SEQTK_BUILD_ROOT` selects an absolute build directory.

The source commit, archive hashes and compiler version are pinned in
[sources.lock.json](sources.lock.json). All variants link zlib 1.3.1.
The native baseline is built from unmodified upstream sources.

## Use

The default module export is the asynchronous `createSeqtk` factory, with `FS`
and `callMain`. Create a fresh instance for each command. The shared `ToolRunner`
provides file handling, standard streams, cancellation and timeouts in a worker.

Argument arrays include the subcommand and omit the program name:

```js
["trimfq", "reads.fastq"]
["trimfq", "-q", "0.01", "reads.fastq"]
["trimfq", "-b", "5", "-e", "10", "reads.fastq"]
["seq", "-A", "reads.fastq"]
["sample", "-s", "42", "reads.fastq", "100"]
["fqchk", "reads.fastq"]
```

`trimfq` writes FASTQ to standard output. It trims each supplied read file
independently; it does not jointly evaluate mates or remove adapters. Use the
same sampling seed for corresponding paired files. Gzip input and standard
input (`-`) are supported. Provide input bytes in the module filesystem;
shell redirection and network downloads belong to the calling application.

The module uses a growing 32-bit heap capped at 2 GiB and an 8 MiB stack.
It requires no shared browser memory or cross-origin isolation. Use read sets
that fit in browser memory; input files and returned JavaScript copies also
consume memory outside the WebAssembly heap.

## Port changes

[browser.patch](browser.patch) contains two portability fixes. Kseq's buffer
capacity calculation now uses an explicit 64-bit temporary instead of shifting
a 32-bit `size_t` by 32. Integer formatting uses fixed-width format macros and
`size_t` formats, preventing truncated coordinates and counters on wasm32.
Patched native and WebAssembly builds treat format and shift-width warnings
as errors. The trimming and sampling algorithms are unchanged.

## Validation

Run the shared comparison suite with:

```sh
node scripts/validate.mjs --tool seqtk --engine node
```

Validation compares original native, patched native and WebAssembly output on
real human and yeast reads. Reports record commands, input and binary hashes,
exit statuses and output comparisons. Separate CI jobs run Chromium, Firefox
and WebKit. The tests establish equivalence for their recorded inputs and
options; inclusion of the full CLI does not imply that every command and
parameter combination has been tested.

The Node.js suite passed all 15 recorded cases on 10 October 2026.
Browser results are recorded separately by CI.

## Licence

seqtk uses the MIT licence; zlib uses the zlib licence. Packages include both
licences, complete pinned source archives, the patch, the build script and
checksums. The source archives can be reused from `build/downloads` without
network access; the native toolchain and Emscripten are still required.
