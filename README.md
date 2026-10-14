# Genomics WebAssembly tools

An experimental [seqtk container2wasm prototype](docs/seqtk-container.md) runs
the unchanged Linux executable in an emulated container inside the browser.
It provides local file input, cancellation, logs and output downloads, with
native/Docker/direct-WASM comparisons. It is separate from the direct ports
below and requires its own build plus cross-origin-isolated hosting.

Sequence trimming, DNA and RNA alignment, gene counting, transcript assembly,
quantification, single-cell preprocessing, coverage, variant calling, metagenomics,
genotype analysis, peak calling, primer design and differential expression tools
for web browsers and Node.js. Each tool has an independent
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
| [SomaticSniper](tools/somaticsniper/README.md) | 1.0.5.0 | Somatic SNVs from matched tumour/normal BAMs, one thread |
| [LoFreq](tools/lofreq/README.md) | 2.1.5 | Variant calling, indel preparation and the serial somatic workflow |
| [Strelka2](tools/strelka2/README.md) | 2.9.10 | Serial somatic workflow, depth estimation, scoring and indexed SNV/indel VCFs |
| [Kraken2](tools/kraken2/README.md) | 2.17.2 | Taxonomic classification, database building and inspection, one thread |
| [Bracken](tools/bracken/README.md) | 3.1 | Abundance estimation and read-length distributions, one thread |
| [Prodigal](tools/prodigal/README.md) | 2.6.3 | Prokaryotic gene prediction, protein translation and saved training, one thread |
| [HMMER](tools/hmmer/README.md) | 3.4 | Thirteen offline profile-HMM, protein and nucleotide programs, one thread |
| [bustools](tools/bustools/README.md) | 0.45.1 | BUS processing, barcode correction, UMI handling and count matrices, one thread |
| [PLINK 2](tools/plink2/README.md) | 2.0.0-b.1-dev, 12 Oct 2026 | Genotype conversion, QC, LD, relationships, scoring and regression; real worker threads; no PCA |
| [MACS3](tools/macs3/README.md) | 3.0.5 | Fourteen peak, coverage, HMM and variant commands in Python/WebAssembly; serial execution |
| [Primer3](tools/primer3/README.md) | 2.6.1 | Seven original programs for primer design, thermodynamics, alignment and masking |
| [seqtk](tools/seqtk/README.md) | 1.5 | FASTA/FASTQ processing, including quality trimming with `trimfq` |
| [AdapterRemoval](tools/adapterremoval/README.md) | 2.3.4 | Adapter and quality trimming, paired-read merging, one thread |
| [Cutadapt](tools/cutadapt/README.md) | 5.2 | Adapter and quality trimming, read filtering, paired and single reads, one thread |
| [HISAT2](tools/hisat2/README.md) | 2.2.2 | Small-index building and spliced RNA alignment, one thread |
| [minimap2](tools/minimap2/README.md) | 2.31 | Indexing and alignment, including spliced RNA modes, one thread |
| [kallisto](tools/kallisto/README.md) | 0.52.0 | Transcript indexing and bulk RNA-seq quantification, one thread |
| [Salmon](tools/salmon/README.md) | 2.8.0 | Transcript indexing and bulk RNA-seq quantification, real worker threads and shared memory |
| [featureCounts](tools/featurecounts/README.md) | 2.1.1 | Gene and exon counts from SAM/BAM, real worker threads |
| [StringTie](tools/stringtie/README.md) | 3.0.3 | Transcript assembly and abundance from aligned RNA reads, one thread |
| [mosdepth](tools/mosdepth/README.md) | 0.3.14 | BAM/CRAM coverage and depth summaries, one thread |
| [DESeq2](tools/deseq2/README.md) | 1.52.0 | Count-matrix analysis using R 4.6.0 and webR 0.6.0, one R process |

See the individual tool documentation for supported features, build changes,
licences and limitations. Whole-genome performance is not established by the
small validation fixtures.

DESeq2 has a separate build, browser example and validation workflow. It uses
existing upstream WebAssembly packages, with a compiler configuration fix for
the missing `locfit` dependency. See its [instructions](tools/deseq2/README.md).

kallisto and Salmon also have a separate [browser example](examples/quant/README.md)
and validation workflow. Both use real human and yeast RNA-seq reads with matching
transcript references. Salmon needs cross-origin isolation for its worker threads;
the example includes a static server and a service worker for hosts without custom
response headers. Read the hosting instructions before deploying it.

featureCounts, StringTie and mosdepth have a separate [browser example](examples/rna-analysis/README.md)
and validation workflow. The example accepts editable command arguments, retains
output files between commands and includes real human and yeast RNA-seq alignments
with annotations. featureCounts uses shared memory and needs cross-origin isolation;
StringTie and mosdepth can use ordinary static hosting.

Cutadapt has its own [browser example](examples/cutadapt/README.md) and
[JavaScript API](runtime/CUTADAPT.md). Its Python code and compiled Cython
extensions run in the bundled Pyodide runtime. All runtime files are local;
ordinary static hosting is sufficient. The example includes real human and
yeast reads with documented selection and prior processing.

SomaticSniper and LoFreq share a [browser example](examples/somatic/README.md)
with real HCC1395 tumour and HCC1395BL normal reads. LoFreq's
[JavaScript runner](runtime/LOFREQ.md) coordinates the upstream C commands for
the somatic workflow. Both tools use ordinary static hosting. SomaticSniper is
archived upstream; LoFreq describes its somatic workflow as experimental.

Strelka2 has its own [browser example](examples/strelka2/README.md) and
[JavaScript runner](runtime/STRELKA2.md). Its serial somatic workflow includes
the upstream depth estimation, caller, scoring models and output utilities.
The real HCC1395 fixture has matching BAM and reference sequence dictionaries.
It uses ordinary static hosting.

Kraken2 and Bracken share a [browser example](examples/metagenomics/README.md)
with real paired reads from a sequenced mock community and a small reference
collection. Kraken2 classifies reads; Bracken estimates abundances from its
reports and database-specific read-length distributions. Both use ordinary
static hosting. Large production databases generally exceed the memory
available to this browser build. See the [Kraken2 API](runtime/KRAKEN2.md) and
[Bracken API](runtime/BRACKEN.md) for the supported commands.

Prodigal and HMMER share an [annotation example](examples/annotation/README.md)
with real bacterial sequence regions, protein-family alignments and sequence
targets. Prodigal predicts genes and translated proteins; HMMER builds profile
models, searches sequences and scans pressed profile collections. Files can be
carried between commands, including proteins predicted by Prodigal. Both ports
run original upstream algorithms and use ordinary static hosting. See the
[Prodigal API](runtime/PRODIGAL.md) and [HMMER API](runtime/HMMER.md).

bustools has a [single-cell browser example](examples/bustools/README.md) and
[JavaScript API](runtime/BUSTOOLS.md). It processes BUS files from kallisto and
matching barcode, equivalence-class and transcript/gene reference files. The
example carries results through sorting, barcode correction and count-matrix
generation. It uses ordinary static hosting and preserves binary files between
commands. Browser execution is serial, with a 64 MiB default sort buffer.

PLINK 2 has a [genotype-analysis example](examples/plink2/README.md) and
[JavaScript API](runtime/PLINK2.md). The original C/C++ program runs with its
upstream `NOLAPACK` matrix implementations, including linear, logistic and Firth
regression. PCA and phenotype SVD are unavailable in this build. PLINK 2 uses
real background reader threads even with one compute thread, so hosting needs
cross-origin isolation. Its minimum working arena is 640 MiB, in addition to
file copies, thread stacks and other allocations. Start with small datasets.

Primer3 has a [primer-design example](examples/primer3/README.md) and
[JavaScript API](runtime/PRIMER3.md), including the upstream thermodynamic
parameters and companion command-line programs. It runs in disposable workers
on ordinary static hosting. Files and results stay in browser memory.

## Build

On Linux, install GCC and Clang, Make, Git, curl, Python 3.12 or newer and
Node.js 20 or newer. BWA-MEM2 and HISAT2 use Clang for their native baselines. The reproducible
WebAssembly compiler target for the C/C++ tools is Emscripten 3.1.64.
Cutadapt and Bracken use Python 3.14.2 and a pinned Pyodide runtime;
see their tool READMEs before building them.

```sh
npm ci
bash scripts/setup-emsdk.sh
source toolchain/emsdk/emsdk_env.sh
bash scripts/build-bwa.sh all
bash scripts/build-bwa-mem2.sh all
bash scripts/build-freebayes.sh all
bash scripts/build-somaticsniper.sh all
bash scripts/build-lofreq.sh all
python3 scripts/setup-strelka2-python.py
bash scripts/build-strelka2.sh all
bash scripts/build-kraken2.sh all
BRACKEN_PYTHON="$(command -v python3.14)" bash scripts/build-bracken.sh all
bash scripts/build-prodigal.sh all
bash scripts/build-hmmer.sh all
bash scripts/build-bustools.sh all
bash scripts/build-plink2.sh all
bash scripts/build-primer3.sh all
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

The **Build and validate RNA quantifiers** workflow builds and checks kallisto
and Salmon separately. Run `bash scripts/build-kallisto.sh all` or
`bash scripts/build-salmon.sh all` after installing the toolchains described in
their tool directories. Then run
`node scripts/validate-quant.mjs --tool kallisto --engine node` (or `salmon`).
The same suite accepts `chromium`, `firefox` and `webkit` as its engine. It compares
transcript identities, lengths, abundance tables and read accounting with the
original native program. See its [comparison rules](validation/quant/README.md).

For browser or hosting changes, the quantifier workflow can reuse the compiled
programs pinned in `.github/quant-build-reuse.json`. It checks their source,
compiler settings and binary hashes, then reruns every Node and browser test.
Delete that file or set `enabled` to `false` when changing build inputs. Missing
or expired artifacts trigger a fresh build; mismatched provenance stops the job.
Forks ignore another repository's pinned build and compile their own programs.

The **Build and validate RNA analysis tools** workflow checks featureCounts,
StringTie and mosdepth. To build and validate one tool locally:

```sh
bash scripts/build-featurecounts.sh all
# Or scripts/build-stringtie.sh / scripts/build-mosdepth.sh
python3 scripts/fetch-rna-analysis-test-data.py --verify
node scripts/validate-rna-analysis.mjs --tool featurecounts --engine node
```

Use `--engine chromium`, `firefox` or `webkit` for browser comparisons. Each
program is checked against its unchanged native build and a native build with
the portability patches. See [comparison rules](validation/rna-analysis/README.md)
and the tool READMEs for the tested options and restrictions.

The **Build and validate Cutadapt** workflow builds the unchanged native,
patched native and WebAssembly versions from pinned inputs:

```sh
python3.14 scripts/build-cutadapt.py all
python3 scripts/fetch-cutadapt-test-data.py --verify
node scripts/validate-cutadapt.mjs --engine node
```

Repeat with `--engine chromium`, `firefox` and `webkit` for browser comparisons.
The [Cutadapt comparison rules](validation/cutadapt/README.md) cover read
sequences, qualities, pairing, output order and reported trimming statistics.
Selected options must have an observed effect on the real test reads.

The **Build and validate somatic callers** workflow checks SomaticSniper and
LoFreq against their unchanged native programs and patched native builds:

```sh
python3 scripts/fetch-somatic-test-data.py --verify
node scripts/validate-somatic.mjs --tool somaticsniper --engine node
node scripts/validate-somatic.mjs --tool lofreq --engine node
```

Repeat with `--engine chromium`, `firefox` and `webkit` for browser comparisons.
The tests compare complete variant records, LoFreq's intermediate somatic
results and indel-quality BAMs. The real paired data include published reference
calls, kept separate from the native-equivalence checks. See the
[somatic comparison rules](validation/somatic/README.md).

The **Build and validate Strelka2** workflow runs the original Python workflow
and the browser workflow against the same real paired data:

```sh
python3 scripts/fetch-strelka2-test-data.py --verify
node scripts/validate-strelka2.mjs --engine node
```

Repeat with `--engine chromium`, `firefox` and `webkit`. The
[comparison rules](validation/strelka2/README.md) describe which outputs and
workflow steps are checked. The original native scripts use the isolated
interpreter built by `scripts/setup-strelka2-python.py`.

The **Build and validate metagenomics tools** workflow checks Kraken2 and
Bracken against their unchanged native programs:

```sh
python3 scripts/fetch-metagenomics-test-data.py --verify
BRACKEN_PYTHON=python3.14 node scripts/validate-metagenomics.mjs --engine node --tool all
```

Repeat with `--engine chromium`, `firefox` and `webkit`. The tests build fresh
databases, exchange databases between native and browser programs, compare
every read classification and check Bracken's distributions and abundance
estimates. See the [comparison rules](validation/metagenomics/README.md) for
the exact checks and the two documented upstream binary-format issues.

The **Build and validate annotation tools** workflow builds unchanged native,
patched native and WebAssembly Prodigal/HMMER, then compares real biological
outputs in Node, Chromium, Firefox and WebKit:

```sh
python3 scripts/fetch-annotation-test-data.py --verify
node scripts/validate-annotation.mjs --tool all --engine node
```

Repeat with each browser engine. The case plan exercises gene coordinates,
translations, model construction, protein/domain and nucleotide searches,
effectful options, failures, repeated execution and native/browser exchange of
training files and profile databases. The [comparison rules](validation/annotation/README.md)
document exactly which metadata is normalized and how numerical results are
checked. The [fixture provenance](datasets/annotation/README.md) records original
sources and deterministic transformations. Native agreement establishes port
fidelity on these inputs, not annotation accuracy or large-database capacity.

The **Build and validate bustools** workflow builds unchanged native,
patched native and WebAssembly programs and compares their BUS records, count
matrices and supporting labels in Node, Chromium, Firefox and WebKit:

```sh
python3 scripts/fetch-bustools-test-data.py --verify
node scripts/validate-bustools.mjs --engine node
```

The [fixture provenance](datasets/bustools/README.md) identifies the experimental
sources and deterministic selection rules. The [comparison rules](validation/bustools/README.md)
define command coverage, output ordering and any metadata handling. Validation
includes command pipelines, effectful options, binary interchange, repeated
execution and error handling. These fixtures establish port fidelity on the
tested inputs, without establishing performance on full single-cell studies.

## Packages

After building and validating, run `python3 scripts/package.py`. It creates
separate archives under `releases/`, each containing one tool, its runtime,
documentation, licences and corresponding sources. GitHub Actions also retains
these files in its `tool-packages` artifact; check the complete workflow status
before using a build. That artifact is created only after all browser jobs
pass. The earlier `candidate-packages` artifact is an unvalidated build input
to that final step.

Quantifier packages are created by `scripts/package-quant.py` with the matching
validation reports from all four engines. Their final workflow artifact is
`quant-packages`. Each ZIP contains one quantifier, the shared runner, its browser
example, real test data, corresponding sources, licences and validation summaries.
Salmon's native and browser indexes have different word sizes; build the index in
the environment that will use it.

RNA analysis packages are created by `scripts/package-rna-analysis.py` after all
four engines pass. Each ZIP contains one compiled program, its runner, a browser
example configured for that program, the real input data, source archives, patches,
licences and comparison reports. The workflow's final artifact is
`rna-analysis-packages`; compiled build artifacts alone have not passed the release gate.

Cutadapt's final workflow artifact is `cutadapt-package`. Its independent ZIP
is assembled by `scripts/package-cutadapt.py` only after all four engines pass.
It contains the Python/WebAssembly runtime, compiled wheels, browser example,
real reads, comparison reports, source archives and licence notices.

Somatic caller packages are created by `scripts/package-somatic.py` after all
four engines pass. The `somatic-packages` workflow artifact contains a separate
ZIP for each caller, with its browser example, runtime, real paired data,
validation reports, corresponding sources and licences.

Strelka2's `strelka2-package` artifact is assembled by
`scripts/package-strelka2.py` after all four engines and browser examples pass.
It includes the caller, helper programs, scoring models, JavaScript workflow,
real data, sources, notices and comparison reports.

Kraken2 and Bracken packages are created by `scripts/package-metagenomics.py`
after all four engines and browser examples pass. The `metagenomics-packages`
artifact contains two independent ZIPs with examples, real data, corresponding
sources, licences and comparison reports. Bracken includes the tested Kraken2
dependency needed to prepare read-length distributions. Both packages can be
served from an ordinary static web server. Before publishing that artifact, the
workflow extracts each ZIP into a separate temporary directory, verifies its
archive and file checksums, runs its included tests, and exercises its browser
example in Chromium, Firefox and WebKit. The separate
`metagenomics-package-validation` artifact preserves those standalone browser
reports and screenshots.

Prodigal and HMMER are packaged separately by `scripts/package-annotation.py`
after all four engines pass. The `annotation-packages` workflow artifact contains
independent ZIPs with compiled programs, APIs, browser examples, real data,
original sources, notices, build recipes and matching validation reports.
Before upload, the release gate extracts each archive independently, verifies
its file checksums, runs package tests and exercises its example in all three
browsers. The `annotation-package-validation` artifact records those checks.

bustools is packaged by `scripts/package-bustools.py` after the four engines and
browser workflow pass. Its `bustools-package` artifact includes the compiled
program, API, browser example, real BUS data, original sources, licences and
validation evidence. The release gate independently extracts the ZIP, verifies
all checksums, runs its included tests and exercises the example in all three
browsers. Those checks are retained in `bustools-package-validation`.

PLINK 2 is built and validated separately:

```sh
bash scripts/build-plink2.sh all
python3 scripts/fetch-plink2-test-data.py --verify
node scripts/validate-plink2.mjs --engine node
```

Repeat validation with `--engine chromium`, `firefox` and `webkit`. The
`plink2-package` workflow artifact is assembled by `scripts/package-plink2.py`
only after the native and browser comparisons pass. It includes the threaded
program, example with a server that supplies isolation headers, authentic and
labelled synthetic fixtures, corresponding sources, notices and validation
reports. The release gate also verifies the extracted ZIP independently.

Primer3 has a separate build, validation and package workflow:

```sh
python3 scripts/fetch-primer3-test-data.py --verify
node scripts/validate-primer3.mjs --engine node
```

Repeat with `--engine chromium`, `firefox` and `webkit`. The
`primer3-package` workflow artifact is assembled only after the native
comparisons pass in all four engines. The standalone ZIP includes its browser
example, API, fixtures, validation evidence, original sources and dependency
notices. The release job verifies the extracted ZIP, runs its included tests
and exercises the example in all three browsers.

MACS3 has a separate build and validation workflow:

```sh
python3 scripts/build-macs3.py all
python3 scripts/fetch-macs3-test-data.py --verify
node scripts/validate-macs3.mjs --engine node
```

The build requires CPython 3.14.2 to match the pinned Pyodide runtime. Repeat
validation with `--engine chromium`, `firefox` and `webkit`. The
[JavaScript API](runtime/MACS3.md) runs each command in a fresh worker, and the
[browser example](examples/macs3/README.md) supports uploads, editable arguments
and downloadable results on an ordinary static server. Inputs and outputs are
held in memory; the validation fixtures do not establish whole-genome capacity.

`scripts/package-macs3.py` requires matching native comparisons from all four
engines. Its standalone ZIP includes the Python/WebAssembly runtime, scientific
dependencies, example, fixtures, pinned sources and licence notices. The release
workflow also extracts and tests that ZIP in all three browsers. A compiled build
or a source checkpoint alone is not a validated release.

## Licence

Original runtime and build/test infrastructure use the MIT licence. Upstream
programs and dependencies retain their own licences; compiled packages are not
all MIT-licensed. See [THIRD_PARTY.md](THIRD_PARTY.md) and the notices included in
each package.
