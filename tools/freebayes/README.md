# FreeBayes for WebAssembly

This package compiles the FreeBayes command-line caller to WebAssembly. It is an
independent port, not an official FreeBayes distribution. It runs the actual
caller on files supplied by the application.

The initial baseline is FreeBayes **1.3.6**, revision
`084dce52e54af5adbd1e2b0a67f3733dd8bfddc0`. This release has a small, explicit
dependency graph and an upstream NA12878 test dataset. It is a deliberate pinned
baseline, not a claim to ship the newest upstream release. Recursive dependency
commits and external archive checksums are recorded in `upstream.lock.json`.

## Build

Use Python 3.12 or newer, Git, curl, patch, a C/C++ compiler and Emscripten 3.1.64.
Activate Emscripten before building the WebAssembly target:

```sh
source /path/to/emsdk/emsdk_env.sh
bash scripts/build-freebayes.sh all
```

The three outputs are:

| Target | Output | Source |
| --- | --- | --- |
| `native` | `build/native/freebayes` | Pristine upstream sources |
| `patched-native` | `build/patched-native/freebayes` | The same sources with the port patches and portable TTMath implementation |
| `wasm` | `dist/freebayes/freebayes.mjs` and `freebayes.wasm` | The same patched sources, compiled by Emscripten |

Pass one target name instead of `all` to build it alone. Set `JOBS` to limit
parallel compilation. All targets use the same vendored HTSlib and vcflib
revisions, zlib 1.3.1, SIMDe 0.8.2, optimization level, and optional-codec settings.
No fast-math flags are used. `sources.json` lists the translation units selected
by upstream's Meson build, with its duplicate entry removed.

Standalone source packages include the pinned source tree and dependency
archives. Before building those packages, the script checks every source file
against `source-manifest.json`, rejects missing or extra source files, and checks
the lock-file hash. A Git checkout instead verifies the commit, recursive
submodule revisions and absence of tracked source edits. The Emscripten SDK and
host compiler are build prerequisites rather than bundled dependencies.

## Use the module

Import the module and create a fresh instance for each command. Keep the `.wasm`
file alongside the `.mjs` file, or use Emscripten's `locateFile` option. Run the
module in a Web Worker so that a call does not block the page.

```js
import createFreebayes from './freebayes.mjs';

const output = [];
const module = await createFreebayes({
  noInitialRun: true,
  print: line => output.push(line),
  printErr: line => console.error(line),
});
module.FS.writeFile('/reference.fa', referenceBytes);
module.FS.writeFile('/reference.fa.fai', indexBytes);
module.FS.writeFile('/reads.bam', bamBytes);
module.FS.writeFile('/reads.bam.bai', bamIndexBytes);
module.callMain(['-f', '/reference.fa', '/reads.bam']);
const vcf = output.join('\n') + '\n';
```

The repository's worker API handles file transfer, exit codes and resource
cleanup. The direct example above only illustrates the underlying module.

## Scope and differences

- The supported target is the single-process `freebayes` executable, using local
  reference FASTA and coordinate-sorted BAM files. SAM input can be attempted
  where the upstream reader accepts it, but indexed BAM is the tested route.
- BAM indexes are needed for region selection. Reference indexes are supplied
  as files, or may be created by the caller in its writable virtual filesystem.
- The `freebayes-parallel` shell wrapper and other external-process wrappers are
  not included. No native programs are launched from the browser.
- Remote URLs, plugins, cloud authentication and CRAM are outside the supported
  interface. HTSlib's optional lzma, bzip2 and curl features are disabled in all
  three builds. Use BAM rather than relying on partial CRAM codec availability.
- The module is single-threaded and uses WebAssembly SIMD. It needs a browser
  with SIMD support; cross-origin isolation is not required for this build.
- The heap can grow to 2 GiB. Available browser memory may be lower. There is no
  promise that whole-genome workloads will fit or finish promptly.
- The source patch suppresses native `execinfo` backtraces, which cannot run in
  WebAssembly. TTMath's existing C++ fallback replaces architecture-specific
  assembly. Neither change substitutes a different variant-calling algorithm.
- TTMath is explicitly kept on 64-bit words, matching the x86-64 native
  baseline. A small type patch uses `uint64_t` and `int64_t` instead of assuming
  that C++ `long` is 64 bits. Its exponent and mantissa bit counts are unchanged.
- The file-offset patch uses the public `off_t` type rather than glibc's private
  `__off64_t` type. Both the native and WebAssembly builds use 64-bit file offsets.
- The pinned interval-tree header omits `<limits>`. The build supplies that
  standard header to **all** targets through `-include limits`; pristine source
  files remain unchanged.
- WebAssembly and native machines have different `long double` arithmetic:
  Emscripten uses a 113-bit mantissa, while the tested x86-64 GNU build uses
  64 bits. FreeBayes uses this arithmetic for its posterior probabilities.
  Differences must be reported by validation rather than concealed through
  broad numerical rounding or variant normalization.

## Validation

The native build, patched native build and WebAssembly build must receive
identical inputs and arguments. Compare their raw VCFs after removing only
documented run metadata. Compare every record and sample field, not just variant
counts. Tests of FreeBayes use a fixed BAM so that aligner differences cannot
hide caller differences. Combined-pipeline tests are a separate check.

Passing a fixture demonstrates agreement on that fixture, not universal
equivalence or clinical validation. See the repository's validation results for
the commands, data and environments actually exercised.

The comparison policy permits an absolute difference of at most `1e-13` in
VCF `QUAL` only when **both** absolute QUAL values are at most `1e-8`. This
addresses measured rounding in posterior probabilities near one; it does not
round or change the output files. All other record fields must match exactly.
The strict comparator remains available, and reports retain the original values
and each tolerated difference. The initial human tests measured a maximum QUAL
difference of `2.09496e-14`; the tested yeast calls matched exactly.

## Attribution

FreeBayes is by Erik Garrison, Gabor Marth and its upstream contributors:
<https://github.com/freebayes/freebayes>. Its main source is MIT licensed, but the
compiled package also contains components under other licences. In particular,
vcflib's Smith-Waterman code links `libdisorder` under GPL-2.0-or-later. The
combined executable must not be described as MIT-only. Release packages include
dependency notices, and redistribution must supply the corresponding source
and build instructions under the applicable terms. The build collects notices
alongside the compiled module; upstream attribution remains intact.
