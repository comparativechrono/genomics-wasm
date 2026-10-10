# DESeq2 comparisons

`analysis.R` is ordinary R code. The validation runner executes this same file in
native R and in the bundled webR worker, using identical count and sample files.
It checks R 4.6.0, DESeq2 1.52.0, and every bundled package version before running
the analysis. No expected numerical results are embedded in the script.

```sh
node scripts/validate-deseq2.mjs --engine node --rscript /path/to/Rscript
node scripts/validate-deseq2.mjs --engine chromium --rscript /path/to/Rscript
node scripts/validate-deseq2.mjs --engine firefox --rscript /path/to/Rscript
node scripts/validate-deseq2.mjs --engine webkit --rscript /path/to/Rscript
```

The native library must contain the package versions in the bundle manifest.
The runner preserves `R_HOME`, `R_LIBS_USER`, and other R environment variables.
Native BLAS and OpenMP thread counts are set to one. Browser tests run without
cross-origin isolation headers and reject requests to external servers; the
bundle must supply its own R runtime and packages.

After a native run, another engine can use its saved baseline:

```sh
node scripts/validate-deseq2.mjs --engine chromium \
  --native-baseline reports/deseq2/node/native
```

The baseline is accepted only when its recorded input, analysis-script, and
bundle-manifest hashes match the current run. This avoids rebuilding the native
library separately for each browser.

## Data and analysis

The fixture contains real airway RNA-seq gene counts for eight samples from four
donors, with treated and untreated cells from each donor. See
`datasets/deseq2-airway/README.md` for provenance. The comparison selects the
first 2,000 genes with total count at least 10 and the first 100 all-zero genes,
preserving the original order within each group. Selection is performed by the
same R script in both environments. These 2,100 genes keep repeated browser
checks small and retain zero-count cases. They are a software test subset, not
the full study's differential expression analysis.

The paired model is `~ cell + dex`, with untreated cells as the reference. The
tests use DESeq2's parametric dispersion fit and serial execution. The complete
outputs cover:

- Input counts, selected gene order, sample order, and model factors.
- Median-ratio size factors and all normalized counts.
- Gene-wise, fitted, and final dispersions, with convergence and outlier flags.
- Wald results, the reverse contrast, and results with independent filtering and
  Cook's cutoff disabled.
- A likelihood-ratio test against `~ cell`.
- Variance-stabilized counts with `blind = FALSE`.
- DESeq2's built-in normal-prior fold-change shrinkage.
- A separate `fitType = "local"` analysis, comparing its complete dispersion and
  Wald tables. This directly exercises the compiled `locfit` routines.

The R script requires nonzero library sizes, all four donor pairs, a parametric
fit, and more than 20 genes with adjusted Wald p-value below 0.05. It checks
zero-count genes' missing-result behavior. The JavaScript runner also checks
that R objects survive between calls, that an R error reaches JavaScript, and
that the same session can run again after that error.

The dispersion tables include `dispMAPConverged`, calculated as `dispIter < 100`.
This is the exact decision used by DESeq2 1.52.0's `estimateDispersionsMAP` with
its default `maxit = 100` to choose the adaptive-grid fallback. The decision
must agree for every gene; optimizer iteration counts need not be identical.

This does not test optional packages such as `apeglm`, `ashr`, or `glmGamPoi`,
parallel workers, all possible models, or all possible inputs. Agreement with
native DESeq2 establishes equivalence for these cases, not biological accuracy
or a complete verification of the upstream statistical method.

## Comparison rules

`compare.py` uses only Python's standard library. It requires every expected
output file, exact table schemas, exact row identifiers and order, exact package
versions, exact integer counts, and exact Boolean flags. Missing `NA` values
must occur in exactly the same positions. Duplicate identifiers, malformed
numbers, and nonfinite numerical values are rejected even if both files contain
the same invalid value. The platform-dependent `session-info.txt` is retained
as a diagnostic, but excluded from equality checks.

Floating-point comparisons are strict by default. A policy may provide a
per-column absolute and relative bound:

`abs(native - wasm) <= absolute + relative * max(abs(native), abs(wasm))`

A bound cannot excuse a sign change or zero becoming nonzero. Tiny p-values need
a relative bound without an absolute floor so that, for example, `1e-100` cannot
be treated as equivalent to zero or `2e-100`. Integer and Boolean fields never
use numerical tolerances. Every numerical column records the number of values
that changed and its largest absolute and relative differences, including when
the comparison passes under a declared bound.

For every gene with a nonmissing adjusted p-value, the decisions at thresholds
0.001, 0.01, 0.05, and 0.1 must also agree exactly. A small numerical difference
that changes one of those decisions fails even if it is within its bound.

`tolerances.json` is the policy used by the gate. Its nonzero bounds are based
on the measurements below. Each run also
writes `comparison-strict.json` with zero tolerances, preserving the distinction
between exact equality and agreement within a measured numerical bound.

Raw native and webR tables, console logs, version details, hashes, both comparison
reports, and the run summary are saved under `reports/deseq2/<engine>/`.
Mutation tests for the comparator run with the repository's normal `npm test`.
Comparator tests alone do not show that DESeq2 runs in a browser; that requires
the actual differential runs above.

## Measured differences

The first Node comparison used native R 4.6.0 on Linux x86_64 and webR 0.6.0
with R 4.6.0, both running DESeq2 1.52.0. All 43 installed package versions were
checked. Native DESeq2 was built from the unmodified source archive in
`tools/deseq2/sources.lock.json`, with Rcpp 1.1.2 and RcppArmadillo 15.6.0-1
headers matching the WebAssembly package's build information. The runtime Rcpp
package in both environments was 1.1.1-1.1. The native build's source lock and
build dependency versions are recorded in `build/deseq2-native/native-build.json`.

The measurements used bundle-manifest SHA-256
`cf7b9e05a643aefd37287773702feb59ecc5e54bb07a51880a70d8e2acac68f2`
and native `DESeq2.so` SHA-256
`5063fd67c4568f8e7427c008fb560b824fe37f359cf447bf1000664348811848`.
These identify that measurement, not a requirement for byte-identical compiler
output on another host. Each validation run records its own bundle, script,
and input hashes alongside the complete raw outputs.

Input counts, gene and sample order, all package versions, size factors, all
normalized counts, base means, base variances, missing values, and Boolean flags
agreed exactly. Their numerical tolerance remains zero. Nonzero bounds apply
only to the following columns. Relative maxima are rounded upward below; full
precision measurements are in each run's comparison reports.

| Output | Largest measured difference | Comparison bound |
| --- | --- | --- |
| Gene-wise dispersions, both fits | Relative 5.59e-11 | Relative 1e-10 |
| Parametric fitted dispersions | Relative 1.01e-14 | Relative 2e-14 |
| Parametric final dispersions | Relative 1.59e-12 | Relative 2e-12 |
| Parametric LFC, Wald statistic, p-value and adjusted p-value, including shrinkage and LRT result columns | Relative 7.84e-11 | Relative 1e-10 |
| Parametric LFC standard errors | Relative 7.73e-13 | Relative 1e-12 |
| LRT statistic | Absolute 1.56e-10 | Absolute 1e-12 plus relative 2e-12 |
| Variance-stabilized counts | Relative 1.01e-14 | Relative 2e-14 |
| Local fitted dispersions | Relative 4.55e-13 | Relative 1e-12 |
| Local final dispersions | Relative 7.68e-7 | Relative 1e-6 |
| Local LFC | Relative 2.72e-8 | Relative 5e-8 |
| Local LFC standard error and Wald statistic | Relative 3.13e-7 | Relative 5e-7 |
| Local p-value and adjusted p-value | Relative 2.57e-6 | Relative 5e-6 |

The LRT statistic is a difference of fitted log likelihoods. Near-zero
statistics show a larger relative difference despite a very small absolute
change; the mixed bound covers both small and large statistics. In this run,
the largest difference occupied 76% of that mixed bound.

The larger local-fit differences were concentrated in `ENSG00000049618` and
`ENSG00000051009`. The fitted local dispersion curve itself agreed within
4.55e-13 relative error; the increase appeared in the subsequent MAP dispersion
optimization. DESeq2 stops this iterative optimization using a log-posterior
criterion (`dispTol = 1e-6`). Small arithmetic differences affecting that
stopping point are a plausible explanation, not a proved numerical error bound.
For `ENSG00000049618`, adjusted p-values were 0.019822630136744 in native R and
0.0198225793314141 in webR. Both runs selected exactly the same 787 genes at
adjusted p-value below 0.05 with the local fit; all four tested significance
thresholds agreed gene by gene.

The bounds above are limited to these tested outputs. They do not establish an
error bound for every dataset, model, transformation, or optional DESeq2 method.
New inputs or engines that exceed a bound require investigation rather than an
automatic tolerance increase.
