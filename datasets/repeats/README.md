# Repeat-expansion regression inputs

These are bounded regression fixtures for ExpansionHunter 5.0.0 and REViewer
0.2.7. They combine independent synthetic controls with unchanged public
upstream example and regression inputs. They are not clinical samples supplied
by a user, and passing these cases does not establish whole-genome capacity or
clinical accuracy.

`manifest.json` records the exact size and SHA-256 of every file under `common/`
and `upstream/`. `provenance/sources.lock.json` records immutable upstream commit
URLs, source-file hashes and sizes, original notices, and derived-data steps.

## Independent synthetic controls

`common/reference.fa` has four artificial 8,000-base contigs. SHA-256-derived
bases provide deterministic flanks. Each reference has twelve CAG units at
zero-based half-open coordinates `4000-4036`. Input reads are constructed from
the explicitly specified haplotypes before any genotyper is run:

| Locus | Contig | True repeat counts | Intended check |
| --- | --- | --- | --- |
| `NORMAL_CAG` | `chr1` | 12 and 18 | Exact diploid spanning-read genotype |
| `EXPANDED_CAG` | `chr2` | 12 and 75 | A spanning allele plus an allele longer than a read; confidence interval contains the constructed size |
| `X_CAG` | `chrX` | 18 | Exact male haploid genotype; a female-mode control can call 18/18 |

The BAM contains 2,107 explicitly synthetic pairs: 150-base reads from 350-base
fragments, with starts spaced ten bases apart between 2,500 and 5,500 inclusive
on each haplotype. Base qualities are 40 and mapping qualities are 60. Inserted
CAG bases are represented in the linear alignment by insertion operations;
reads wholly inside the insertion have a mapped, fully soft-clipped anchor at
the repeat boundary. These deliberately simple alignments test graph
realignment; they are not claimed to emulate a production aligner's mapping
uncertainty or sequencing-error distribution.

The fourth contig, `chr3`, is used only by `catalog.twostr.json`: a GAA repeat
at `3500-3524` has haplotype counts 8 and 14, and the CAG repeat at `4000-4036`
has counts 12 and 18. The 476-base fixed sequence between them is longer than
every input fragment. These two STR genotypes are independently supported but
their phase cannot be established from linking fragments. `truth.json` stores
this separate `twoStr` contract. It exercises REViewer's ambiguous phasing
without requiring support for a small-variant graph node.

`catalog.json` includes all three loci; `catalog.autosomal.json` includes only
the first two. `truth.json` records the independently specified repeat counts,
ploidies, construction method and required assertion type. Native and WASM
outputs are still compared strictly, including their numerical fields; a
confidence-interval assertion for the long allele is not a tolerance between
implementations.

`catalog.compound.json` adds an A/T SNP at `chr1:3500-3501` to the first CAG
locus. The reference carries A; the haplotype with eighteen CAG units carries
T. No 350-base fragment connects this SNP and the repeat 499 bases away, so
phase is deliberately ambiguous. The expected SNP genotype is 0/1 and the STR
genotype is 12/18; the inputs do not establish which observed alleles are
phased together. This exercises tied/ambiguous phasing behavior.

`empty.bam` contains the same sequence dictionary and no records.
`catalog.empty.json` and `catalog.invalid.json` exercise empty-input and invalid
reference handling. Their names do not imply that every upstream mode accepts
them successfully.

The generator writes BAM, BAI, FASTA indexes and BGZF itself using Python's
standard library. Its fixed-Huffman/RLE deflate encoder avoids compression
choices that vary between zlib releases. No previously generated analysis
result or native tool is needed to reproduce these synthetic files.

## ExpansionHunter upstream example

`upstream/expansionhunter/` preserves the inputs from commit
`9f5db1d4ab72016b2d93330b9792842d71582622` (release 5.0.0): the 4-kb reference,
indexed BAM and `SNV_AND_STR` catalog. The original upstream result files are
included as `expected.json` and `expected.vcf`; they report an A/T heterozygous
SNP and CAG genotype 2/10. These are upstream reference results, distinct from
the independently constructed truths above. Only a FASTA index is added; the
input sequence, read bytes and catalog are unchanged.

## REViewer public regression input

`upstream/reviewer/` preserves the `BEAN1_HG00684` indexed BAMlet,
`HG00684.vcf` and the four-locus chromosome-16 catalog
from commit `7cd3be55cd67566d23d9272600e484edc72edbf8` (release 0.2.7). The sample
identifier is the upstream test label; no private sequence data was added.
The VCF supplies BEAN1 repeat genotype 17/17 with confidence intervals
17-17/17-17. The original BAMlet, its index, VCF and catalog are
byte-for-byte unchanged.

The upstream reference is the hg38 chromosome-16 sequence included in that
same source tree. Upstream's test script attributes it to UCSC's
`hgdownload.soe.ucsc.edu/goldenPath/hg38/chromosomes/chr16.fa.gz`.
For this regression fixture, `reference.fa.gz` preserves the original contig
length and every coordinate. It retains exact source bases within 2,000 bases
of each catalog locus (`BEAN1`, `JPH3`, `TNRC6A`, `XYLT1`) and replaces all other
bases by N. `reference-mask.json` records the retained intervals, each retained
sequence's SHA-256, and the full source hash. No read, genotype, graph tag or
genomic coordinate is shifted.

The masked reference uses one FASTA sequence line, deterministic BGZF encoding,
and matching FAI/GZI indexes, so only a compact compressed file needs to be
mounted in the browser. It is a targeted test reference and must not be used
for other genomic regions. The unchanged full reference remains reproducible
from its pinned upstream source archive.

As a direct check of this transformation, rebuilt native REViewer 0.2.7 was
run with both the full upstream reference and the masked BGZF reference.
The BEAN1 SVG, metrics table and phasing table were byte-for-byte identical.
The full FASTA's SHA-256 was
`3b5cb8d62fc9d7f256799e2daca37bcd4edc9f462fa607c5847043504593c551`;
the derived reference hash is frozen in `manifest.json`. The shared output
hashes from this check were:

| Output suffix | Bytes | SHA-256 |
| --- | ---: | --- |
| `BEAN1.svg` | 609711 | `0d3c4d9cd9095d2db38971169a02ee2fc452c830ef6e90697a1a4b77bf68ee7d` |
| `metrics.tsv` | 54 | `60e503bc92a23ad15c9cd524c3ac2a6d28f812c2cedcdd42310a6c1e63edec6e` |
| `phasing.tsv` | 77 | `645a6855fe64bad0fb1eb2e61531c0a6250e54e7489d683d7017955525a6b4a3` |

To repeat this transformation check after fetching sources and building the
pristine native tools, index the cached full FASTA and run the same BAMlet,
VCF, catalog and locus twice, changing only the reference and output prefix:

```sh
full=build/repeat-fixture-sources/reviewer/reviewer/tests/inputs/genomes/HG38_chr16.fa
sample=datasets/repeats/upstream/reviewer
build/native/hts-1.21/samtools faidx "$full"
mkdir -p build/repeat-reference-check
build/native/reviewer/REViewer --reads "$sample/BEAN1_HG00684.bam" \
  --vcf "$sample/HG00684.vcf" --catalog "$sample/catalog.json" --locus BEAN1 \
  --reference "$full" --output-prefix build/repeat-reference-check/full
build/native/reviewer/REViewer --reads "$sample/BEAN1_HG00684.bam" \
  --vcf "$sample/HG00684.vcf" --catalog "$sample/catalog.json" --locus BEAN1 \
  --reference "$sample/reference.fa.gz" --output-prefix build/repeat-reference-check/masked
for suffix in BEAN1.svg metrics.tsv phasing.tsv; do
  cmp "build/repeat-reference-check/full.$suffix" "build/repeat-reference-check/masked.$suffix"
done
```

This observation applies to this locus and native release. It does not claim
that masking arbitrary regions is safe. Release validation independently
compares the frozen fixture on pristine native, patched native and WASM builds.

## Reproduction

```sh
python3 scripts/fetch-repeat-test-data.py --fetch-sources --regenerate
python3 scripts/fetch-repeat-test-data.py --verify
```

`--verify` is offline. Source retrieval first checks the verified fixture cache
and then the source archives shipped under `dist/expansionhunter/sources/` and
`dist/reviewer/sources/`; only missing source files are downloaded from their
immutable URLs. Each extracted or downloaded source is independently checked
against its pinned size and SHA-256. Regeneration restores missing frozen
files but refuses altered or unexpected files. Updating the frozen manifest
requires the separate explicit `--update-manifest` development option.

Both upstream `LICENSE.txt` and `COPYRIGHT.txt` files are retained unchanged.
The ExpansionHunter release contains Apache-2.0 license text and historical
GPL wording in its copyright notice; REViewer's release has GPL-3.0-or-later
notices. See those original files and the packaged source notices.
