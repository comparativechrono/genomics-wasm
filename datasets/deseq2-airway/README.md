# Airway RNA-seq counts

These are real integer gene counts from the Bioconductor **airway 1.26.0**
package. The complete matrix has **63,677 genes and eight samples**. No counts
were generated, normalized, rounded, scaled or changed for this repository.

Himes and colleagues sequenced four primary human airway smooth muscle cell
lines before and after dexamethasone treatment. Each cell line contributes one
untreated and one treated sample. This is a paired experiment, with four
biological replicates per condition.

- Study: [GEO GSE52778](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE52778),
  [SRA SRP033351](https://www.ncbi.nlm.nih.gov/sra/?term=SRP033351).
- Paper: Himes et al., PLoS ONE 9(6):e99625 (2014),
  [doi:10.1371/journal.pone.0099625](https://doi.org/10.1371/journal.pone.0099625).
- Source and count construction:
  [airway 1.26.0](https://bioconductor.org/packages/3.20/data/experiment/html/airway.html)
  and its
  [vignette](https://bioconductor.org/packages/3.20/data/experiment/vignettes/airway/inst/doc/airway.html).
  The counts use GRCh37 and Ensembl release 75.

`counts.tsv` contains `gene_id` followed by the eight sample columns in the
upstream order. `samples.tsv` contains the original sample metadata in the same
order, with an explicit `sample_id` column. The `cell` column identifies the
paired cell lines, and `dex` is `untrt` or `trt`. All eight selected samples were
untreated with albuterol (`albut=untrt`).

A paired analysis uses `~ cell + dex`, with `untrt` as the reference level of
`dex`, and `contrast=c("dex", "trt", "untrt")` to report treated versus untreated
effects. These samples are independent of the small read fixtures used to test
the alignment tools.

Automated differential tests may select the first 2,000 genes with total count
at least 10 and the first 100 all-zero genes, preserving the original order
within each selection. This is an unchanged subset of real counts and keeps
routine browser tests short. Fitting a subset changes normalization, dispersion
estimation and multiple-testing adjustment; its results are not a replacement
for analysing the complete matrix. The complete matrix is retained here for
that purpose.

The source package declares **LGPL** without a version. Its original DESCRIPTION
and CITATION are retained as `UPSTREAM_DESCRIPTION.txt` and
`UPSTREAM_CITATION.R`. Archive, RData and fixture hashes are recorded in
`../deseq2-manifest.json`.

Verify the committed fixtures without R or network access:

```sh
python3 scripts/fetch-deseq2-data.py
```

Recreate them using native R and the pinned upstream archive (no add-on R
packages are needed):

```sh
python3 scripts/fetch-deseq2-data.py --regenerate
```

The regeneration script reads the existing R object slots and writes the count
assay and sample metadata directly. It checks all output hashes before replacing
any fixture. Use `--output build/recreated-deseq2` to regenerate into another
directory, or `--r /path/to/R` to choose the R executable.
