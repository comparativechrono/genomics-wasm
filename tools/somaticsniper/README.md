# SomaticSniper

SomaticSniper 1.0.5.0 compiled to WebAssembly. This runs the upstream C somatic
SNV caller and its original SAMtools 0.1.6 library on the files supplied to each
command. It accepts the upstream command-line arguments and produces VCF,
classic tabular or BED output.

The source is pinned to upstream commit
`136bac782dc2a761f63fa29cfd8ff1d1e162faf5`. The source archives, checksums,
Emscripten version and runtime settings are recorded in `sources.lock.json`.

## Build

Install a C compiler, make, Python 3 with `tarfile` extraction filters, curl,
patch, Node.js 20 or later and Emscripten 3.1.64. Activate Emscripten or set
`EMSDK` to its installation directory, then run from the repository root:

```sh
scripts/build-somaticsniper.sh all
```

The recipe also accepts `native`, `patched-native` and `wasm`. `JOBS` sets the
number of compiler jobs, defaulting to two. `EM_NODE_JS` can select the Node.js
executable used by Emscripten; the wrapper defaults to the host Node.js.

The outputs are:

| Build | Executable or module |
| --- | --- |
| Unchanged upstream source | `build/native/somaticsniper` |
| Port changes with a native compiler | `build/patched-native/somaticsniper` |
| WebAssembly | `dist/somaticsniper/somaticsniper.mjs` and `somaticsniper.wasm` |

All three builds use the same pinned zlib 1.3.1 and the SAMtools stdio
configuration. The native baseline does not apply either port patch. The recipe
compiles the source list from upstream's executable and library targets; it does
not build the optional test framework or the separate SAMtools command.

The distribution includes all three source archives under `sources/`, the
patches and license notices. If the download cache is absent, the build recipe
uses those archives directly after checking their hashes. The build manifest
records every tool-local input, the build wrapper and every distribution asset
except the manifest itself.

## Run

The command expects tumor BAM first, normal BAM second, and an output filename:

```sh
build/native/somaticsniper -F vcf -f reference.fa tumor.bam normal.bam calls.vcf
```

Both BAM files must be coordinate-sorted and have matching reference sequence
dictionaries, including names, lengths and order. The FASTA must contain the
matching sequence for every contig with input reads; unused BAM header entries
do not need FASTA entries, and the FASTA order may differ. BAM indexes are not
required: the caller reads both files sequentially. Supply the FASTA index as
`reference.fa.fai`, or let the caller create it in its working directory.

For the browser or Node.js, use a fresh worker for each command:

```js
import { ToolRunner } from './runtime/index.mjs';

const runner = new ToolRunner({
  moduleUrl: new URL('./dist/somaticsniper/somaticsniper.mjs', import.meta.url),
});
const result = await runner.run(
  ['-F', 'vcf', '-f', 'reference.fa', 'tumor.bam', 'normal.bam', 'calls.vcf'],
  {
    files: [
      { path: 'reference.fa', data: referenceBytes },
      { path: 'reference.fa.fai', data: referenceIndexBytes },
      { path: 'tumor.bam', data: tumorBytes },
      { path: 'normal.bam', data: normalBytes },
    ],
    outputs: ['calls.vcf'],
  },
);
```

File data and returned stdout, stderr and output files are byte arrays. Check
`result.exitCode` before using the output. An `AbortSignal` can cancel a command
by terminating its worker. Input and output files occupy the worker's memory;
they are not uploaded by the runner. The module exports `FS` and `callMain` for
applications that manage their own Emscripten instance.

This is a single-threaded program. It needs neither SharedArrayBuffer nor
cross-origin isolation headers. `waitForExit` should remain false. Its WASM heap
starts at 64 MiB and can grow to 2 GiB, subject to the browser's available memory.

Useful arguments include:

| Argument | Meaning |
| --- | --- |
| `-F vcf`, `-F classic`, `-F bed` | Output format; upstream defaults to classic |
| `-q INT` | Minimum read mapping quality |
| `-Q INT` | Minimum somatic quality, default 15 |
| `-p` | Disable the independent somatic priors |
| `-J` | Use joint priors that include the somatic mutation rate |
| `-s FLOAT` | Set the mutation-rate prior and enable `-J` |
| `-L`, `-G` | Exclude loss-of-heterozygosity or gain-of-reference calls |
| `-n STRING`, `-t STRING` | Normal and tumor sample names in the VCF |
| `-v` | Version, exit status 0 |

No arguments displays the complete upstream usage and exits with status 1.
There is no upstream `-h` option. `-t` sets a sample name, not a thread count.
VCF sample columns are normal followed by tumor.

## Port changes and checks

The calling model, pileup algorithm and output routines are unchanged. In
particular, the probability calculations retain upstream `long double` rather
than being reduced to double precision. GNU89 inline semantics are selected for
the old SAMtools C code on all targets; 64-bit file offsets remain enabled.

`browser.patch` checks the three required positional arguments, a failed FASTA
load, failed BAM opens and invalid BAM headers. `samtools.patch` checks failed
file opens in RAZF before handing the file descriptor to its reader or writer.
These checks return status 1 with a diagnostic. The unchanged upstream program
can crash when a BAM is missing and can loop on a missing reference. The checks
are also applied to the patched-native build so their effects can be compared
separately from the compiler target.

The initial native, patched-native and Node.js WebAssembly smoke check matched
the upstream integration fixture's three expected VCF records exactly. Only
the generated date and the reference file path were omitted from that comparison.
This fixture checks the port against the upstream regression output; it is not
a tumor truth set. The repository's somatic validation reports provide the
separate comparisons on the supplied real sequencing data and browser engines.

## Scope

SomaticSniper calls somatic single-nucleotide variants. This port preserves the
legacy BAM reader: it does not add CRAM, remote URL input, modern long-CIGAR
extensions or CIGAR `=`/`X` support. Use ordinary BAM CIGAR `M` operations for
aligned matches and mismatches. The optional upstream Perl filtering scripts
are present in the source archive but are not browser executables.

The memory limit makes bounded regions appropriate for browser use. Passing the
included comparisons demonstrates agreement on those files and options; it
does not establish equivalence for every dataset, guarantee valid results for
malformed inputs, or replace the biological filtering and validation required
when interpreting somatic calls.

Upstream: <https://github.com/genome/somatic-sniper>.
Documentation: <https://gmt.genome.wustl.edu/packages/somatic-sniper/documentation.html>.
See `THIRD_PARTY_LICENSES.txt` and the accompanying license files for notices.
