# Repeat-calling and visualization validation

`cases.json` is the executable case plan for ExpansionHunter 5.0.0, REViewer
0.2.7 and SAMtools 1.21. Every case runs against freshly built original native,
patched native and WASM programs. The same plan runs in Node, Chromium,
Firefox and WebKit. Browser validation uses ordinary HTTP without cross-origin
isolation and rejects external requests or browser errors.

```sh
python3 scripts/fetch-repeat-test-data.py --verify
node scripts/validate-repeats.mjs --engine node
node scripts/validate-repeats.mjs --engine chromium
node scripts/validate-repeats.mjs --engine firefox
node scripts/validate-repeats.mjs --engine webkit
```

## Independent checks

| Case | Scientific or failure contract |
| --- | --- |
| `upstream-seeking` | Official compound SNP/STR example: SNP 0/1, CAG 2/10 |
| `upstream-streaming` | Same known genotypes from unindexed streaming BAM |
| `upstream-path-aligner` | Alternate path aligner and KmerIndex sparsepp lookup with the same SNP/STR truth |
| `upstream-cram` | SAMtools writes/indexes CRAM; EH resolves the mounted local reference |
| `synthetic-male-chain` | Constructed 12/18 normal repeat, 12/75 expanded repeat, haploid X18; EH → SAMtools sort/index → REViewer for all three loci |
| `synthetic-female` | X ploidy changes to 18/18 with female sample sex |
| `synthetic-streaming` | All synthetic loci and both repeat evidence types through streaming dispatch |
| `compound-snv` | Unlinked heterozygous SNP 0/1 plus repeat 12/18 in one graph |
| `compound-str-chain` | Two unlinked STRs with alleles 8/14 and 12/18; exact phasing, allele depths and rendered assignment |
| `public-reviewer-bean1` | Pinned public HG00684 input VCF has BEAN1 17/17; tests homozygous read assignment |
| `empty-readset-no-call` | Empty BAM produces uncalled variants and an empty BAMlet |
| `eh-missing-index` | Seeking rejects missing BAM index |
| `eh-invalid-catalog` | EH rejects a nonexistent reference contig |
| `reviewer-unknown-locus` | REViewer rejects a requested locus absent from the catalog |
| `reviewer-missing-index` | REViewer rejects an unindexed BAMlet |

The synthetic truth is constructed before running the tools. A repeat of 75
units exceeds the 150-base read length: its independent truth requires the
confidence interval to contain 75 and the expansion to be detected. The
initial native run calls 12/77 with CI 12-12/65-94. This independent coverage
check does **not** permit different native and WASM calls or intervals: those
remain exactly equal. For deliberately unlinked compound STRs, truth specifies
the allele multiset, while the full cross-target comparison still requires
identical phasing order, scores, metrics and SVG output.

REViewer 0.2.7 does not support the SmallVariant graph nodes in the official EH
example or the synthetic SNP/STR case. Those cases validate EH alone. The
positive visualization chains use only repeat nodes; the demo uses the two
autosomal synthetic loci.

The public reference is reduced without shifting coordinates. Exact retained
window hashes and the full-source-versus-masked native-output proof are in
`datasets/repeats/upstream/reviewer/reference-mask.json`. The unchanged BAMlet,
VCF and catalog, source URLs, licenses and transformation steps are documented
in `datasets/repeats/README.md` and the source lock. Source masking is validated
against the full upstream reference, independently of cross-target parity.

## Comparison rules

The comparator retains genotype order and phase, confidence intervals, read
counts, allele depths, numerical values, graph alignments, sequence, base
qualities, flags, mate data, all tags, plot geometry, colors and labels. It
uses no floating-point tolerance and drops no scientific fields.

Only representation differences are normalized:

- JSON object order, VCF INFO/FORMAT map order and XML attribute order.
- BAM compression and record ordering. Complete decoded records form a
  multiset, preserving duplicates and every field; coordinate-sorted BAMlet
  output also has its original record order checked independently.
- BAM auxiliary-tag order. Tag names, types and values remain exact.

VCF headers, sample order and row order remain exact. TSV rows and fields,
including diplotype tie ordering, remain exact. XML element order, text and
geometry remain exact. Stdout logging timestamps are retained as raw execution
evidence but are not scientific artifacts. SAMtools `--no-PG` avoids inserting
machine-specific executable paths into BAM headers; other headers are kept.

## Evidence and release gate

Each command starts in a fresh filesystem containing explicitly captured
inputs. Outputs may not overwrite an existing mounted input, preventing stale
files from satisfying a check. Reports include each command's raw stdout,
stderr, status, output files, sizes and hashes, plus all comparison results.
The original native output is self-compared against independent truth before
patched native and WASM equivalence are accepted.

`reports/repeats/<engine>/summary-all.json` points to a unique run directory.
The manifest binds the complete datasets, all three distributions and native
build trees, build inputs, runtime files, case plan and comparator. The package
gate requires the full four-engine case inventory and replays comparisons from
raw evidence. A changed program, dataset, validator or runtime invalidates the
reports. Passing these bounded regression fixtures does not establish
whole-genome memory capacity or clinical accuracy.
