# Run only inside the pinned, disposable webR build container.
args <- commandArgs(trailingOnly = TRUE)
stopifnot(length(args) == 2L)
source <- normalizePath(args[[1L]], mustWork = TRUE)
output <- normalizePath(args[[2L]], mustWork = TRUE)
stopifnot(getRversion() == "4.6.0")
compiler <- system2("emcc", "--version", stdout = TRUE)
message(compiler[[1L]])
stopifnot(grepl("5.0.7 (263db4cffa6f9fc2ec514a70abac81362ea41849)",
                compiler[[1L]], fixed = TRUE))
# The complete toolchain is fixed by the immutable container image digest.
library(rwasm)
makevars <- system.file("webr-vars.mk", package = "rwasm", mustWork = TRUE)
# R selects CC17 when DESCRIPTION requests USE_C17. rwasm's ordinary
# CC override does not cover that compiler, causing the upstream build
# to invoke native gcc with WebAssembly flags. Set both compiler paths.
cat("\n# genomics-wasm: select the WebAssembly C17 compiler\n",
    "override CC = emcc\n",
    "override CC17 = emcc\n",
    "override C17FLAGS = $(filter-out -std=%,$(WASM_CFLAGS)) -std=gnu17\n",
    file = makevars, append = TRUE, sep = "")
writeLines(c(paste("R", getRversion()),
             paste("rwasm", packageVersion("rwasm")),
             compiler[[1L]],
             paste("Makevars", makevars)),
           file.path(output, "locfit-build-environment.txt"))
rwasm::build(source, out_dir = output, dependencies = FALSE)
stopifnot(file.exists(file.path(output, "locfit_1.5-9.12.tgz")))
