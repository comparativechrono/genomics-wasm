# PLINK 2 native and WebAssembly validation

Run `node scripts/validate-plink2.mjs --engine node` after building PLINK 2 and
freezing its fixtures. Repeat with `chromium`, `firefox` and `webkit` for the
browser evidence. The harness serves COOP/COEP headers and requires
`crossOriginIsolated`, because PLINK 2 uses real reader and computation pthreads.
Commands run serially in fresh instances through the shipped `Plink2Runner`.
Every command specifies `--memory 640 require`, `--threads 1` (or explicitly 2)
and seed 42. The 640 MiB working arena does not include input/output copies or
other runtime overhead.

The plan executes 86 cases per engine against unchanged pinned native
`NOLAPACK` source, native source with the portability patch, and the actual
WebAssembly program. All three consume the same frozen source inputs. Pipeline
steps consume each target's actual preceding outputs; explicit interchange
cases give every target the unchanged-native and WASM-generated PGEN files in
turn. The reference build retains upstream floating-point handling, while the
patched native build also exercises the WASM port's lack of denormal flushing.

The authentic dataset is the upstream 1000 Genomes Phase 3 chromosome 21
fixture: 2,504 samples and 600 phased biallelic variants. It covers import,
PGEN/VCF/BED export and reimport, complete phased VCF re-export equivalence,
allele frequencies and observation counts, missingness, HWE, heterozygosity,
genotype counts, filters, LD pruning and reports, scoring and cross-runtime
fileset exchange. BED loses phase by definition; its roundtrip is checked with
`--pgen-diff include-missing` and must contain zero genotype discrepancies.
`--real-ref-alleles` explicitly restores the trusted A2/REF status after BED
reimport, since these A2 alleles came from the authentic source REF columns.
Full-sample frequency outputs must also agree after both roundtrips.

The original KING and GRM tests use the first 48 authentic sample IDs to bound browser output
size. GRM explicitly loads the frequency estimates from all 2,504 samples;
upstream rejects frequency imputation from fewer than 50 samples. KING must
return all 1,128 unordered pairs, and the GRM list all 1,176 lower-triangular
entries including the diagonal. Arbitrary scoring weights are labelled
synthetic and are not published genetic-risk estimates.

A separate original synthetic cohort covers the non-SSE2 paired-row transpose
regression. It has 105 fictional samples and 4,097 complete biallelic markers;
the first sample is a father, followed by a mother, two children, a duplicate
of the father and 100 unrelated founders. Keep files select 25, 48, 63, 64, 65,
103, 104 and 105 samples without duplicating genotype matrices. For each count,
five commands exercise `--make-king-table`, `--make-king square`,
`--king-cutoff 0.245`, `--distance square ibs flat-missing` and
`--king-table-subset`. The subset path requests every pair containing the first
sample, so it directly exercises the separate subset allocation. Five additional
105-sample WASM runs check repeatability against the original native results.
The 4,097-marker count crosses transpose batches and leaves a partial word.

These cases decode the input BED directly and independently calculate every
pair's observed-marker, heterozygote and opposite-homozygote counts, robust
kinship and IBS similarity. All pairs, matrix entries, diagonals and sample IDs
are checked, including the first sample. Cutoff selections must match the
independent relationship graph and the pinned upstream selection order. The
threshold lies between the fixture's true father–child kinship and its observed
pre-fix corrupted value, so the retained-ID regression detects the bug as well.
This oracle validates both the native reference and WASM outputs: agreement
between two incorrect outputs does not pass. These original fixtures contain
no donor genotypes or generated tool-result goldens.

The separately labelled synthetic fixtures adapted from pinned upstream test
recipes cover the 603-sample alternating dense/missing genotype regression
that previously exposed SIMD-padding errors, linear, logistic and Firth models,
one/two-thread regression equivalence, missing hardcall and dosage scoring,
haploid phased dosage, and phased multiallelic genotypes. The fixture provenance
manifest describes each recipe, the frozen bytes and their SHA-256 hashes.
Regression reports must contain 30 variants and at least 20 finite p-values.
Every error code, including the native Firth `UNFINISHED` result on the separated
first variant, is retained and compared exactly. This result is not silently
reclassified as a converged association.

The build intentionally lacks LAPACK. Exact PCA, approximate PCA and phenotype
SVD must each reject their command with native status 8 and the specific LAPACK
requirement. Missing input must return the same native status 3 and diagnostic.
These tests document unavailable functionality rather than claiming PCA support.

## Comparison policy

Binary PGEN/BED outputs are exact. Text row order, complete headers, sample and
variant IDs, chromosomes, coordinates, alleles, counts, genotypes, missing-value
markers, model tests and error codes are exact. Text parsers normalize CRLF
line endings only. VCF generation dates, and only a recognized PLINK platform
suffix if present in its source metadata, are normalized; no VCF record, sample,
phase, dosage or biological metadata is removed.

Only each artifact's explicit `floatColumns` list permits a numerical bound.
The bound follows upstream `Tests/TEST_GLM_LOGISTIC_MISSING/compare.awk`:

```
abs(a - b) <= 0.5 * (last_printed_place(a) + last_printed_place(b))
              + 1e-8 * (abs(a) + abs(b))
```

An integer-looking token on either side requires exact numerical equality,
including zero. All discrete columns always require exact strings. The
comparator checks every numerical cell separately, retains missing markers,
rejects nonfinite values, and reports the number compared and any violations.
It does not use a generic relative tolerance, alter error codes or accept a
maximum-difference summary without checking individual cells. Float allowlists
must exist in the actual header. Raw hashes still differ if accepted printed
rounding differs; equality does not imply byte identity for those tables.

Adversarial tests exercise changed biological identifiers, counts, alleles,
row order, genotype phase, dosage, error codes, nonfinite values, truncated
files, numerical shifts exceeding the allowed bound, and unsafe artifact paths.

## Evidence and release gates

Every run has an immutable UUID directory under `reports/plink2/<engine>/`.
It retains outputs, stdout/stderr, real exit statuses, byte sizes and hashes,
commands, input dependencies and comparator results. The summary is atomically
published at `summary-plink2-all.json` only after all cases have finished.

Identities bind both native binaries, every WASM execution asset, build inputs,
runtime modules, harness, comparator, case plan and the entire fixture manifest
and bytes. The same identities are checked again at the end of the run; edits
during execution invalidate the run. The strict packager independently
verifies the recorded comparison evidence and requires matching
Node, Chromium, Firefox and WebKit reports for the exact packaged files.
Passing cases are the tested scope; this does not establish equivalence of
every upstream PLINK 2 command or make arbitrary cohort sizes browser-safe.
