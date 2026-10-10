# Output comparison

`compare.py` compares native and WebAssembly results. It uses only the Python
standard library. A comparison passes only when both inputs parse and their
headers and complete records match under the rules below. The default is exact;
an explicitly enabled exception for VCF QUAL is described below.

```sh
python3 validation/compare.py sam native.sam browser.sam --report reports/bwa.json
python3 validation/compare.py vcf native.vcf browser.vcf --report reports/freebayes.json
python3 -m unittest discover -s tests -p test_compare.py -v
```

Exit status is `0` for a match, `1` for a mismatch, and `2` for an invalid input,
unreadable file, or failure to write the report. JSON is printed to standard
output and, when requested, also written to `--report`. Diagnostics show at most
20 differences by default; use `--max-differences N` to change this. Each preview
is limited to 1,000 characters. Counts always cover the complete inputs, including
differences omitted from the preview.

The Python API returns the same report:

```python
from validation.compare import compare_sam, compare_vcf

report = compare_sam("native.sam", "browser.sam")
if not report["equal"]:
    raise RuntimeError(report)
```

## What is compared

Records are compared as multisets: their order may change, but the number of
copies of every record must match. Equal record counts alone cannot pass a
comparison. Secondary and supplementary alignments are retained. All numbers
are compared as their original text: `0.5`, `0.500000`, and `5e-1` differ unless
the specific QUAL exception below has been enabled. No other numeric field has
a tolerance.

| Format | Compared exactly | Order that may change |
| --- | --- | --- |
| SAM | All 11 mandatory fields; all optional tag names, types, and values; header fields, reference dictionary, read groups, program identity and version, and comments | Alignment records; optional tags within a record; fields within a header record; non-`@SQ` header records |
| VCF | CHROM, POS, ID, REF, ALT, QUAL; FILTER membership; every INFO key and value; every FORMAT key and sample value; sample names and order; header declarations and contig dictionary | Variant records; FILTER IDs; INFO keys; FORMAT keys with their associated values; structured header attributes; non-contig metadata lines |

`@SQ` order and VCF `##contig` order are retained. Metadata multiplicity is also
retained. A VCF's FORMAT `GT`, when present, must remain first as required by the
format. Subsequent FORMAT keys may change order if their values move with them.
Omitted trailing sample values are treated as missing (`.`), as allowed by VCF.
Unix and Windows line endings are accepted.

The comparator does **not** normalize or decompose variants, trim alleles, change
their case, reorder alternate alleles, reverse-complement alignments, or
reinterpret genotypes. `0/1`, `1/0`, `0|1`, and `1|0` remain different. This keeps
differences in raw representations visible even when they might describe the
same biological event. SAM array element order, VCF comma-separated value order,
and VCF ID text are preserved.

## Metadata exceptions

The complete allowlist is:

* SAM: the `CL` field on `@PG` records. Native and browser invocations necessarily
  have different executable names and may have different paths. Every other
  field on the program record is compared, including `ID`, `PN`, `VN`, and `PP`.
* VCF: the complete `##fileDate=` and `##commandline=` metadata lines, using those
  exact, case-sensitive keys. These are the invocation/date metadata emitted by
  FreeBayes. A line may be absent on either side; a duplicate of either key is
  rejected.

There are no wildcard exceptions. Other command-related keys are **not** silently
removed. `##source`, descriptions, reference paths, assembly identifiers, and
program versions are compared. If input paths appear in meaningful metadata,
give native and browser runs matching input basenames or investigate the
difference. Do not broaden the allowlist merely to obtain a passing comparison.

## Optional VCF QUAL tolerance

`--qual-absolute-tolerance DECIMAL` allows a specified absolute difference in the
VCF QUAL column only. Its default, `0`, retains the strict text comparison. For
example, after establishing and documenting a numerical cause:

```sh
python3 validation/compare.py vcf native.vcf browser.vcf \
  --qual-absolute-tolerance 1e-15 --report reports/freebayes.json
```

The Python equivalent is
`compare_vcf("native.vcf", "browser.vcf", qual_absolute_tolerance="1e-15")`.
Pass decimal values as strings to preserve the intended threshold exactly.
Both subtraction and comparison use decimal arithmetic without binary
floating-point rounding. The bound is inclusive. A positive tolerance also
accepts different numeric representations of the same QUAL, with zero delta.

Every other part of the record must match under the ordinary policy, including
raw alleles, genotype and phase, INFO, FORMAT, filters, and sample values.
Genotype qualities and INFO values are **not** covered. Missing QUAL (`.`) never
matches a numeric QUAL. Nonfinite values remain invalid. Sign changes, including
`0` versus `-0`, cannot be excused by the tolerance. Metadata differences still
fail. A nonzero QUAL tolerance is rejected for SAM.

Records with the same non-QUAL content are paired in ascending decimal QUAL
order, separately by sign, while preserving multiplicity. Every changed pair
accepted under this rule is reported in `tolerated_differences`: `count` includes
duplicate records, `max_qual_absolute_delta` gives the largest accepted delta,
and bounded `examples` retain both original values and their exact delta. The
report's `qual_absolute_tolerance` records the threshold even when no changes
were tolerated. An `equal: true` result with a nonzero tolerated count therefore
means agreement under the declared tolerance, not exact equality.

During native baseline testing of FreeBayes 1.3.6, selecting the upstream portable
TTMath implementation produced one QUAL change at fixture position `q:10515`:
`9.69323e-12` to `9.69324e-12`, an absolute difference of `1e-17`. The other
record fields matched. This finding motivated the optional QUAL-only policy;
it does not by itself establish WebAssembly equivalence or justify a tolerance
on any other field. Keep strict reports as well as reports using an approved
tolerance, and investigate newly observed numerical differences.

## Input checks and limits

The parser checks column counts and required headers, duplicate tags and named
header definitions, numeric syntax and ranges, CIGAR syntax and query length,
sequence/quality lengths, allele and genotype syntax, genotype allele indices,
and typed VCF values where definitions are available. VCF fixed, `A`, and `R`
cardinalities are checked. FORMAT `Number=G` is checked when GT gives a ploidy.
Malformed input on both sides is still a failure.

This is a comparison parser, not a complete SAM/VCF conformance validator. In
particular it does not prove alignment correctness against a reference, validate
all flag relationships or mate relationships, require declarations for every
VCF field, infer INFO `Number=G` ploidy, or validate every VCF structural-variant
convention. It accepts finite decimal floating-point values, not NaN or infinity.
It reads uncompressed UTF-8 SAM and VCF text and holds canonical records in
memory; decompress files first and use bounded validation datasets. BAM, CRAM,
and BCF require conversion with a separately pinned standard implementation.

An empty SAM file is rejected. A valid header with zero records is accepted in
either format: no alignments or no calls can be a legitimate result. The test
runner must additionally assert expected record counts or required example
results so a fixture that should produce output cannot pass by producing only
headers. It must also check process exit status and test the browser runtime;
the comparator cannot establish that a tool ran successfully.

## Validation scope

Native and WebAssembly runs should use identical inputs, options, source
revisions, and dependency versions. If the port changes upstream source, compare
an unmodified native build with a native build containing the same patch as well
as with WebAssembly. Test callers on an identical fixed alignment input before
testing the combined pipeline. Keep raw outputs and reports when a comparison
fails.

The mutation tests deliberately alter positions, CIGAR strings, mapping
qualities, tags, alleles, genotypes, phasing, quality values, headers, record
counts, and duplicate multiplicities. They also show that supported harmless
order changes pass and identical malformed inputs fail. These tests validate the
comparison code; they are not evidence that a WebAssembly tool reproduces a
native tool. That evidence requires actual differential runs on the recorded
datasets. Agreement with a native program establishes port equivalence within
the tested scope, not biological accuracy.

## BWA-MEM2 differential checks

Run the pinned BWA-MEM2 build against the real human and yeast fixtures:

```sh
node scripts/validate.mjs --tool bwa-mem2 --engine node
node scripts/validate.mjs --tool bwa-mem2 --engine chromium
node scripts/validate.mjs --tool bwa-mem2 --engine firefox
node scripts/validate.mjs --tool bwa-mem2 --engine webkit
```

These compare the unmodified native **BWA-MEM2** build with both the patched
native build and the WebAssembly build. BWA 0.7.19 is a separate tool and is not
used as the BWA-MEM2 baseline. All SAM comparisons use the exact policy above;
there is no numeric tolerance for alignment fields or tags.

For each dataset, the runner compares all five BWA-MEM2 index files byte for
byte: `.0123`, `.amb`, `.ann`, `.bwt.2bit.64`, and `.pac`. It runs paired-end and
single-end alignment, changed seed length and score threshold, increased match
scores (`-A 3`), multiple small read batches (`-K 10000`), empty FASTQ files, and a repeated command. Native and
WebAssembly builds each read both native-generated and WebAssembly-generated
indexes. Primary alignment counts must agree with the read counts recorded in
`expected.json`, in addition to complete SAM comparisons. Missing input files
must fail with the same nonzero exit status in all three builds.

A separate port restriction check requires `-t 2` to fail with exit status 2 and
an explicit single-thread diagnostic in the patched native and WebAssembly
builds. The unmodified native tool supports multiple threads, so this case does
not claim native equivalence.

Raw SAM, index bytes, process diagnostics, comparison reports, and the summary
are written under `reports/<engine>/`. The summary records input and binary
SHA-256 hashes. A successful Node run exercises the WebAssembly module and its
worker API; browser compatibility requires the separate browser runs above.
