# DESeq2 browser example

This page runs the actual DESeq2 R package using webR. It accepts count and sample tables, runs editable R code, and offers the generated files as downloads. The bundled airway example contains real human RNA-seq counts from eight samples.

Serve the repository over HTTP after preparing `dist/deseq2/`. From the repository root:

```sh
python3 -m http.server 8000
```

Open `http://localhost:8000/examples/deseq2/`. Opening `index.html` directly with a `file:` URL will not work. R and its packages load from the same server. No R server is needed.

## Input files

The count table must contain raw nonnegative integer counts, not TPM, FPKM, or log-transformed values. Rows are genes and columns are samples. The first column holds unique gene IDs; the remaining column headings are sample IDs.

The sample table has one row per sample. Its first column holds unique sample IDs matching the count-table headings. Other columns describe the experiment. Both files use tabs between columns and include a header row. Uploaded filenames can differ: R reads them as `counts.tsv` and `samples.tsv`.

The example script uses the airway columns `cell` and `dex`, with the design `~ cell + dex`. It compares dexamethasone-treated samples (`trt`) with untreated samples (`untrt`) and keeps genes with at least ten reads across all samples. Change the factors, design, and contrast for a different experiment. A positive log2 fold change means higher expression in treated samples.

## R code and downloads

The code is ordinary R, running in a worker. DESeq2 and its bundled dependencies are available. Additional packages need compatible webR binaries. The example writes:

- `results.tsv`: gene IDs, mean normalised counts, log2 fold changes, standard errors, Wald statistics, p-values, and adjusted p-values.
- `normalised-counts.tsv`: counts divided by the estimated sample size factors.
- `diagnostics.pdf`: an MA plot and a dispersion plot.

The page also offers the R script as `analysis.R`. Files written in the working directory are available to download. R output, messages, and warnings appear below the editor. Run time depends on the data and the browser; start with the supplied example before using a larger matrix.

Each run clears the working files and copies in the two input tables. R objects remain in memory between runs. Cancel stops the R session; the next run creates a new one. Clear data or reload the page to clear the uploaded files, R objects, and results.

The browser example smoke test is `scripts/test-deseq2-demo.mjs`. It runs a documented subset of the real airway counts, checks the generated TSV and PDF downloads, and exercises file uploads and cancellation. Numerical agreement with native R is tested separately.
