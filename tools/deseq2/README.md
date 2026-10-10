# DESeq2 in webR

This package runs DESeq2 1.52.0 in R 4.6.0 using webR 0.6.0. The R code and
compiled statistical routines come from upstream DESeq2 and its dependencies.
The JavaScript interface loads files, runs R code and returns files and console
output.

Most WebAssembly packages are existing builds from the Bioconductor R-universe
and r-wasm repositories. The additional build is `locfit`: its C17 compiler
setting is changed to Emscripten. Its statistical source is unchanged. Versions,
source URLs and checksums are locked in this directory.

## Browser example

After building or unpacking the standalone archive, serve its root over HTTP:

```sh
python3 -m http.server 8000
```

Open `http://localhost:8000/examples/deseq2/`. Load the airway example or upload
your own count and sample tables. Edit the R code to match the sample columns,
design and contrast, then run it. Results, normalised counts and diagnostic
plots can be downloaded. Computation runs in a browser worker. A static host
such as GitHub Pages is sufficient; no R server is needed.

The included airway data are real human RNA-seq counts: 63,677 genes and eight
samples from four paired cell lines. The example uses `~ cell + dex` and tests
treated against untreated samples. See the [data notes](../../datasets/deseq2-airway/README.md)
for accessions, extraction instructions and checksums.

The count table must have gene identifiers in its first column and samples in
the remaining columns. Supply non-negative integer counts, before library-size
normalisation. The sample table must have sample identifiers in its first
column, matching the count column names. Biological replication and a valid
design are still required. The example checks the input and uses the actual
DESeq2 functions to fit the model.

## JavaScript interface

```js
import { createDESeq2 } from './runtime/deseq2.mjs';

const session = await createDESeq2({ onProgress: console.log });
try {
  const result = await session.run(`
    library(DESeq2)
    countData <- as.matrix(read.delim("counts.tsv", row.names = 1,
                                    check.names = FALSE))
    colData <- read.delim("samples.tsv", row.names = 1)
    colData <- colData[colnames(countData), , drop = FALSE]
    colData$cell <- factor(colData$cell)
    colData$dex <- factor(colData$dex, levels = c("untrt", "trt"))
    dds <- DESeqDataSetFromMatrix(countData, colData, ~ cell + dex)
    dds <- DESeq(dds, parallel = FALSE)
    res <- results(dds, contrast = c("dex", "trt", "untrt"))
    write.csv(as.data.frame(res), "results.csv")
  `, { files: { 'counts.tsv': countBytes, 'samples.tsv': sampleBytes } });
  console.log(result.stdout, result.stderr);
  const csvBytes = result.files['results.csv'];
} finally {
  session.close();
}
```

`files` accepts strings or byte arrays with relative paths. Files live under
`/work` in the worker. A session retains R objects and files between calls;
`run(code, { resetFiles: true })` clears the working files before that call.
Use a new session for a fresh R environment. `readFile(path)` and
`writeFile(path, data)` provide direct file access. R errors reject the promise
and include captured output. `close()` (also named `interrupt()`) terminates the
worker; create another session to continue. An `AbortSignal` passed to
`createDESeq2` can also cancel loading or computation.

Set `baseUrl` to the directory containing the bundle's `manifest.json` to host
the runtime assets elsewhere. The default points to `dist/deseq2/` relative to
the repository layout. The bundled packages load locally without fetching
current package versions from an online repository.

## Build

```sh
bash scripts/build-deseq2-locfit.sh
python3 scripts/build-deseq2.py
```

Building `locfit` uses the pinned webR build container recorded in
`locfit.lock.json`. This is a developer build dependency; people running the
browser package do not install a container runtime. The second command fetches
and checks the pinned upstream assets and assembles `dist/deseq2/`.

## Validation

The same R script runs in native R and webR. It uses a fixed subset of real
airway counts: the first 2,000 genes with at least ten counts in total and the
first 100 all-zero genes, preserving their upstream order. This selection is
for repeatable port tests; it fits a different model from the full example.

The comparisons cover package versions, selected counts, sample order, size
factors, normalised counts, dispersions, Wald tests, reversed contrasts,
likelihood-ratio tests, variance-stabilised counts and normal-prior fold-change
shrinkage. Identifiers, table shapes and missing-value patterns must match.
Numerical comparison rules and measured differences are recorded separately
under `validation/deseq2/` and in each run's reports.

The separate **Build and validate DESeq2** workflow checks Node, Chromium,
Firefox and WebKit. Use its final package artifact only when the complete
workflow passes. Creating an archive does not establish correctness.

To run the comparison locally, install R 4.6.0 and native build dependencies
(`build-essential`, `gfortran`, `libblas-dev` and `liblapack-dev` on Ubuntu), then:

```sh
python3 scripts/setup-deseq2-native.py
node scripts/validate-deseq2.mjs --engine node --rscript build/deseq2-native/Rscript
```

The setup script builds the locked native packages from source. DESeq2 uses
the same Rcpp and RcppArmadillo headers as its upstream WebAssembly build;
these build dependencies are kept separate from the runtime R library.
See [comparison instructions](../../validation/deseq2/README.md) for browser
tests and the recorded numerical bounds.

## Scope

This is a single-process R environment. Parallel worker processes, external
executables and arbitrary native R packages are not available in the browser.
The locked dependencies support DESeq2's standard count-matrix analysis.
Optional packages such as `apeglm`, `ashr`, `glmGamPoi`, `tximport` and `tximeta`
are not included. Normal-prior shrinkage is included; other shrinkage methods
need their own compatible dependencies.

The runtime and R libraries are a substantial download and take time to load.
Memory and speed depend on the browser and dataset. Passing the small native
comparison suite does not establish performance for large experiments or
single-cell matrices.

DESeq2 is LGPL-3.0-or-later. The webR binary distribution is GPL-3.0 and
dependencies retain their individual licences. The archive includes upstream
licence notices, corresponding sources and build information. Original
JavaScript and validation infrastructure in this repository are MIT-licensed.
