# Genomics WebAssembly tools

Sequence trimming, DNA and RNA alignment, variant calling and differential
expression tools for web browsers and Node.js. Each tool has an independent
package, a JavaScript API, and tests against the corresponding native program.
The sequence tools accept ordinary command-line arguments. DESeq2 runs R code
using webR.

The ports run the upstream algorithms. They do not replay saved results.
Browser commands run in workers, so they can be cancelled without blocking the
page. Each sequence-tool command receives a fresh WebAssembly instance. A
DESeq2 session retains its R objects until the session is closed.

## Tools

| Tool | Upstream version | Initial browser scope |
| --- | --- | --- |
| [BWA](tools/bwa/README.md) | 0.7.19 | `index` and `mem`, local files, one thread |
| [BWA-MEM2](tools/bwa-mem2/README.md) | 2.2.1 | `index` and `mem`, local files, one thread |
| [FreeBayes](tools/freebayes/README.md) | 1.3.6 | Local BAM/SAM variant calling, one process |
| [seqtk](tools/seqtk/README.md) | 1.5 | FASTA/FASTQ processing, including quality trimming with `trimfq` |
| [AdapterRemoval](tools/adapterremoval/README.md) | 2.3.4 | Adapter and quality trimming, paired-read merging, one thread |
| [HISAT2](tools/hisat2/README.md) | 2.2.2 | Small-index building and spliced RNA alignment, one thread |
| [minimap2](tools/minimap2/README.md) | 2.31 | Indexing and alignment, including spliced RNA modes, one thread |
| [DESeq2](tools/deseq2/README.md) | 1.52.0 | Count-matrix analysis using R 4.6.0 and webR 0.6.0, one R process |

See the individual tool documentation for supported features, build changes,
licences and limitations. Whole-genome performance is not established by the
small validation fixtures.

DESeq2 has a separate build, browser example and validation workflow. It uses
existing upstream WebAssembly packages, with a compiler configuration fix for
the missing `locfit` dependency. See its [instructions](tools/deseq2/README.md).

## Build

On Linux, install GCC and Clang, Make, Git, curl, Python 3.12 or newer and
Node.js 20 or newer. BWA-MEM2 and HISAT2 use Clang for their native baselines. The reproducible
WebAssembly compiler target is Emscripten 3.1.64.

```sh
npm ci
bash scripts/setup-emsdk.sh
source toolchain/emsdk/emsdk_env.sh
bash scripts/build-bwa.sh all
bash scripts/build-bwa-mem2.sh all
bash scripts/build-freebayes.sh all
bash scripts/build-seqtk.sh all
bash scripts/build-adapterremoval.sh all
bash scripts/build-hisat2.sh all
bash scripts/build-minimap2.sh all
```

Each script builds the original native program, a native build with the
portability changes, and the WebAssembly program. Source revisions and archive
checksums are recorded in each tool directory. Downloaded sources stay under
`build/`; compiled browser files are written to `dist/`.

## Use a tool

```js
import { ToolRunner } from './runtime/index.mjs';

const bwa = new ToolRunner({
  moduleUrl: new URL('./dist/bwa/bwa.mjs', import.meta.url)
});
const result = await bwa.run(['index', 'reference.fa'], {
  files: [{ path: 'reference.fa', data: fastaBytes }],
  outputs: ['reference.fa.amb', 'reference.fa.ann', 'reference.fa.bwt',
            'reference.fa.pac', 'reference.fa.sa'],
  timeoutMs: 60000
});
if (result.exitCode !== 0) throw new Error(new TextDecoder().decode(result.stderr));
// Pass result.files alongside your reads to a subsequent 'mem' command.
```

Files, standard input, standard output and standard error preserve their bytes.
Arguments are an array; the API does not parse shell commands. Files are kept
inside the worker's virtual `/work` directory. Read the [runtime API](runtime/README.md)
for cancellation, output files and error handling.

For the browser example, serve the repository using `python3 -m http.server 8000`
and open `http://localhost:8000/examples/browser/`. No backend computation or
end-user installation is required. Files remain in the browser.

## Validate

```sh
npm test
python3 scripts/fetch-test-data.py --verify
python3 scripts/fetch-rna-test-data.py --verify
node scripts/validate.mjs --engine node
npx playwright install --with-deps chromium firefox webkit
node scripts/validate.mjs --engine chromium
node scripts/validate.mjs --engine firefox
node scripts/validate.mjs --engine webkit
```

DNA alignment and variant-calling validation uses real human NA12878 and yeast
BY4741 sequencing data. The RNA alignment tests use separate human and yeast
RNA-seq subsets containing reads that span introns. Dataset origins,
transformations and checksums are documented under [datasets](datasets/README.md).
The caller receives the same fixed BAM in each implementation, independently of
the aligner tests. BWA and BWA-MEM2 each use their own native program as the
baseline. Their index files are compared and cross-tested between native and
WebAssembly builds of the same tool; BWA and BWA-MEM2 indexes are separate formats.

The trimming tests compare FASTQ sequences, qualities, read names and order.
The alignment and calling tests compare full SAM/VCF records and meaningful headers, preserving
secondary alignments, complex alleles and duplicate records. They also exercise
different arguments, repeated invocations and missing inputs. See
[comparison rules](validation/README.md). Native agreement establishes port
fidelity on the tested inputs; it is not a biological accuracy benchmark.

The FreeBayes comparisons allow an absolute difference of at most `1e-13` in
VCF QUAL only, provided both values have magnitude at most `1e-8`. Every use of
that allowance is reported. Native and WebAssembly builds use different
`long double` precision; the largest observed difference was `2.09496e-14`,
with all changed scores close to zero. All other fields remain exact. The
comparator's default is strict; the validation runner supplies both bounds
explicitly. See the [FreeBayes port notes](tools/freebayes/README.md).

GitHub Actions builds the original native, patched native and WebAssembly
versions of each tool, and runs the same comparisons in Node,
Chromium, Firefox and WebKit. Each run stores raw outputs and JSON reports as
artifacts. The `release-gate` job requires every build and browser test to pass.
There is no automatic publication of a release from an unvalidated build.

## Packages

After building and validating, run `python3 scripts/package.py`. It creates
separate archives under `releases/`, each containing one tool, its runtime,
documentation, licences and corresponding sources. GitHub Actions also retains
these files in its `tool-packages` artifact; check the complete workflow status
before using a build. That artifact is created only after all browser jobs
pass. The earlier `candidate-packages` artifact is an unvalidated build input
to that final step.

## Licence

Original runtime and build/test infrastructure use the MIT licence. Upstream
programs and dependencies retain their own licences; compiled packages are not
all MIT-licensed. See [THIRD_PARTY.md](THIRD_PARTY.md) and the notices included in
each package.
