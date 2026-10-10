# The same script runs in native R and webR. No expected results are embedded.
options(warn = 1, digits = 17)
suppressPackageStartupMessages(library(DESeq2))
BiocParallel::register(BiocParallel::SerialParam())
dir.create("output", showWarnings = FALSE)

write_table <- function(value, file, ids = rownames(value), id_name = "gene_id") {
  value <- as.data.frame(value)
  stopifnot(length(ids) == nrow(value), !anyDuplicated(ids))
  value <- cbind(setNames(data.frame(ids, check.names = FALSE), id_name), value)
  write.table(value, file.path("output", file), sep = "\t", quote = FALSE,
              row.names = FALSE, col.names = TRUE, na = "NA")
}

expected <- read.delim("input/expected-packages.tsv", check.names = FALSE)
versions <- vapply(expected$package, function(x) packageDescription(x, fields = "Version"), "")
write_table(data.frame(version = c(as.character(getRversion()), unname(versions))),
            "versions.tsv", c("R", expected$package), "package")
writeLines(capture.output(sessionInfo()), "output/session-info.txt")
if (!identical(versions, setNames(expected$version, expected$package))) {
  mismatch <- versions != expected$version
  stop("Package version mismatch: ", paste(expected$package[mismatch],
       versions[mismatch], "expected", expected$version[mismatch], collapse = "; "))
}
stopifnot(as.character(getRversion()) == "4.6.0",
          as.character(packageVersion("DESeq2")) == "1.52.0")

raw_counts <- read.delim("input/counts.tsv", row.names = 1, check.names = FALSE)
samples <- read.delim("input/samples.tsv", row.names = 1, check.names = FALSE)
count_matrix <- as.matrix(raw_counts)
stopifnot(ncol(count_matrix) == 8L, nrow(count_matrix) == 63677L,
          identical(colnames(count_matrix), rownames(samples)),
          all(is.finite(count_matrix)), all(count_matrix >= 0),
          all(count_matrix == floor(count_matrix)), !anyDuplicated(rownames(count_matrix)))
storage.mode(count_matrix) <- "integer"
total <- rowSums(count_matrix)
# Keep original gene order within each group. The zeros exercise DESeq2's NA rules.
selected <- c(head(which(total >= 10), 2000L), head(which(total == 0), 100L))
count_matrix <- count_matrix[selected, , drop = FALSE]
stopifnot(nrow(count_matrix) == 2100L, sum(rowSums(count_matrix) == 0) == 100L)
samples$cell <- factor(samples$cell)
samples$dex <- factor(samples$dex, levels = c("untrt", "trt"))
stopifnot(nlevels(samples$cell) == 4L, all(table(samples$cell, samples$dex) == 1L))
write_table(count_matrix, "selected-counts.tsv")
write_table(samples[, c("cell", "dex"), drop = FALSE], "samples.tsv",
            rownames(samples), "sample_id")

dds <- DESeqDataSetFromMatrix(countData = count_matrix, colData = samples,
                              design = ~ cell + dex)
dds <- DESeq(dds, fitType = "parametric", quiet = TRUE, parallel = FALSE)
stopifnot(all(is.finite(sizeFactors(dds))), all(sizeFactors(dds) > 0),
          identical(attr(dispersionFunction(dds), "fitType"), "parametric"))
write_table(data.frame(size_factor = sizeFactors(dds)), "size-factors.tsv",
            colnames(dds), "sample_id")
write_table(counts(dds, normalized = TRUE), "normalized-counts.tsv")
dispersion_columns <- c("baseMean", "baseVar", "allZero", "dispGeneEst", "dispFit",
                        "dispersion", "dispOutlier", "betaConv")
dispersion_table <- function(object) {
  value <- as.data.frame(mcols(object))[, dispersion_columns]
  # DESeq2 1.52.0 estimateDispersionsMAP uses iter < maxit (default 100)
  # to decide whether to use its adaptive-grid fallback. Compare this decision,
  # not the exact number of optimizer iterations.
  value$dispMAPConverged <- mcols(object)$dispIter < 100L
  value
}
write_table(dispersion_table(dds), "dispersions.tsv")

wald <- results(dds, contrast = c("dex", "trt", "untrt"), alpha = 0.05)
stopifnot(sum(wald$padj < 0.05, na.rm = TRUE) > 20L)
zero <- rowSums(count_matrix) == 0
stopifnot(all(wald$baseMean[zero] == 0), all(is.na(wald$pvalue[zero])),
          all(is.na(wald$padj[zero])), all(is.na(wald$log2FoldChange[zero])))
write_table(wald, "wald.tsv")
write_table(results(dds, contrast = c("dex", "trt", "untrt"),
                    independentFiltering = FALSE, cooksCutoff = FALSE),
            "wald-unfiltered.tsv")
write_table(results(dds, contrast = c("dex", "untrt", "trt"), alpha = 0.05),
            "wald-reverse.tsv")

lrt <- nbinomLRT(dds, reduced = ~ cell, quiet = TRUE)
write_table(results(lrt, alpha = 0.05), "lrt.tsv")
write_table(assay(varianceStabilizingTransformation(dds, blind = FALSE)), "vst.tsv")
write_table(lfcShrink(dds, coef = "dex_trt_vs_untrt", type = "normal", quiet = TRUE),
            "shrink-normal.tsv")

# Exercise the separately compiled locfit dependency, not just its package load.
local_dds <- DESeqDataSetFromMatrix(countData = count_matrix, colData = samples,
                                    design = ~ cell + dex)
local_dds <- DESeq(local_dds, fitType = "local", quiet = TRUE, parallel = FALSE)
stopifnot(identical(attr(dispersionFunction(local_dds), "fitType"), "local"))
write_table(dispersion_table(local_dds), "local-dispersions.tsv")
write_table(results(local_dds, contrast = c("dex", "trt", "untrt"), alpha = 0.05),
            "local-wald.tsv")
writeLines("completed", "output/completed.txt")
