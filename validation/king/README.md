# KING 2.3.2 validation

Run `python3 scripts/build-king.py all`, then:

```sh
node scripts/validate-king.mjs --engine node
node scripts/validate-king.mjs --engine chromium
node scripts/validate-king.mjs --engine firefox
node scripts/validate-king.mjs --engine webkit
python3 -m unittest discover -s tests -p 'test_king_compare.py'
```

The browser commands require the repository's Playwright dependency and the
corresponding browser installations. They run the actual WebAssembly module in
workers on ordinary local HTTP, without cross-origin isolation headers. A missing
browser is a failed run, never a substitute Node result. These local builds are
subject to KING's upstream redistribution restrictions; validation does not grant
permission to distribute the compiled program.

`cases.json` defines 20 cases: 19 scientific comparisons and one missing-input
failure. Each runs pristine native KING, patched native KING, and WebAssembly.
The pristine reference is built with upstream OpenMP support and run with
`--cpus 1`; the browser profile preserves the guarded code using a single-thread
compatibility header. Reports separately identify each executed target and
browser engine. Native outputs must pass the same independent scientific
contracts before they can serve as the comparison baseline.

The fixtures contain only generated genotypes, with no donor samples or borrowed
reference panel. The main cohort has 105 samples and 32,809 SNPs spanning four
chromosomes. It contains parents, two full siblings, an exact duplicate in a
different family, unrelated founders, and controlled missing calls. The marker
count leaves 41 entries in the final 64-bit genotype word; the sample count also
exercises padding in SNP-major PLINK BED bytes. Allele A1 is the minor allele, as
required by KING's segment-analysis checks. `scripts/generate-king-fixtures.py`
uses an explicit xorshift32 generator and emits the input hashes and independent
genotype-count truth. A separate 25-sample subset exercises small-cohort behavior.

Coverage includes:

- Complete kinship and IBS counts, degree filtering, and compressed FAM input.
- Default relatedness screening on at least 100 samples, exhaustive screening,
  the degree-3 path, and pedigree/duplicate relationship classification.
- Sample and SNP QC, including exact genotype counts, missingness, and Mendelian
  consistency.
- PCA and MDS sample identities, finite coordinates, duplicate coordinates, and
  native parity with per-component sign alignment.
- Unrelated-set selection and family clustering with independent relationship
  checks.
- Nonempty IBD summaries and compressed segments, including whole-chromosome
  IBD1 for parent/child pairs and IBD2 for exact duplicates.
- Small-cohort `--related --noscreen 1`, plus a separate case verifying that the
  patched bare `--noscreen` option agrees with the pristine explicit-integer
  option. This checks the upstream option-storage type correction.

The upstream default screening path can miss cross-family relatives below 100
samples. The small-cohort scientific cases therefore explicitly select
`--noscreen 1`; they do not claim to validate the default small-cohort path. The
compressed-input case supplies only the compressed FAM file, so it cannot pass
by silently reopening a plain FAM. Compressed BIM parsing is not claimed.

Each run stores raw stdout, stderr, outputs, execution metadata, comparison
reports, and a summary under `reports/king/ENGINE/RUN_ID/`. The latest full run is
`reports/king/ENGINE/summary-all.json`. The report binds source/build manifests,
compiled assets, fixture bytes, runtime code, comparator code, arguments, exit
statuses, and captured output hashes. Those identities are checked again after
execution. A report passes only when the entire requested case plan passed; a
single-case diagnostic run is explicitly marked `completePlan: false`.

These modest synthetic fixtures test the specified functions and portability
boundaries. They do not establish large-cohort browser memory capacity, every
KING analysis mode, or clinical suitability.
