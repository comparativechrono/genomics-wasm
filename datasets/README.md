# Test data

These are small subsets of public sequencing data. They test whether a
WebAssembly build reproduces the native program's results. They are not
biological truth datasets and do not establish variant-calling accuracy.

| Dataset | Reference | Paired FASTQ records | Fixed BAM records |
| --- | --- | ---: | ---: |
| Human NA12878 | 12,356 bp from GRCh37 chromosome 22 | 512 pairs | 3,333 |
| Yeast BY4741, SRR1569870 | 85,779 bp, sacCer3 chromosome M | 1,079 pairs | 2,158 |

Each directory contains `reference.fa`, its `.fai` index, `reads_1.fastq`,
`reads_2.fastq`, and a fixed `input.bam` with `.bai` index. Aligner tests use
the FASTQ files. Caller tests use the fixed BAM directly, so their input does
not depend on the aligner being tested. Combined pipeline tests are separate.

`manifest.json` records source URLs, source checksums, transformations and
checksums for every distributed data file. Verification needs only Python:

```sh
python3 scripts/fetch-test-data.py --verify
```

This command does not download anything and fails on missing or modified
files. The files are committed so normal tests do not need remote data servers.

## Human data

The source is FreeBayes's existing
[`test/tiny` fixture](https://github.com/freebayes/freebayes/tree/084dce52e54af5adbd1e2b0a67f3733dd8bfddc0/test/tiny),
pinned to commit `084dce52e54af5adbd1e2b0a67f3733dd8bfddc0`.
The reference, BAM and BAM index are byte-for-byte copies. The BAM identifies
NA12878 in its filename and read group (`NA12878D_HiSeqX_R1.fastq.gz`).
Its original SRA/ENA run accession is not recorded in that fixture; we do not
assign one. The upstream licence is retained in `human/UPSTREAM_LICENSE.txt`.

The upstream reference contig is called `q`. All 12,356 reference bases were
independently checked against UCSC hg19 chromosome 22, positions
22,200,345–22,212,700 inclusive (1-based), on 10 October 2026.
Positions in this fixture start at 1 on `q`.

The aligner input contains the first 512 complete primary read pairs after
sorting query names lexicographically. Sequences on the reverse strand are
reverse-complemented and their quality scores reversed to recover sequencing
orientation. The bases and quality values are otherwise unchanged. These
are reads recovered from an existing alignment, not a claim to reconstruct
the original raw FASTQ: any prior hard clipping or quality processing remains.

The fixed BAM contains 3,333 records, including six secondary alignments
and seven unmapped records. An upstream peculiarity is preserved: positions refer to `q`, but
mate positions (`PNEXT`) still use chromosome-scale coordinates. This is the
same small BAM used by upstream FreeBayes tests. It is not used as an expected
BWA output, and the extracted FASTQ files are independently realigned in
aligner tests.

## Yeast data

The reads are from [ENA run SRR1569870](https://www.ebi.ac.uk/ena/browser/view/SRR1569870),
Illumina HiSeq 2000 paired-end sequencing of *Saccharomyces cerevisiae* BY4741.
The associated experiment is SRX696259, study SRP046161 and sample SRS697355.
A snapshot of the public run record and ENA file listing is in `provenance/`.

The reference comes from
[UCSC sacCer3](https://hgdownload.soe.ucsc.edu/goldenPath/sacCer3/bigZips/sacCer3.fa.gz).
Only the mitochondrial contig is distributed here. Preparation used the
complete yeast reference, including nuclear chromosomes:

1. Download the first 10,000,000 compressed bytes of each ENA FASTQ using
   HTTP Range requests, then read the first 100,000 complete pairs.
2. Align those pairs to all sacCer3 chromosomes with native BWA
   0.7.19-r1273, one thread, using the command recorded in the manifest.
3. Retain the 1,079 pairs whose two primary alignments map to `chrM`.
4. Copy their original FASTQ records unchanged, in source order.
5. Save their 2,158 primary alignments in a fixed BAM with a single-contig
   header. Alignment coordinates and tags are retained; numeric reference
   IDs are adjusted for that header. Sort by position, query name and flag.

The compressed source prefixes are intentionally incomplete gzip members.
They contain the requested complete FASTQ records, but must not be read to
the gzip trailer. The regeneration script handles this explicitly and checks
each prefix's SHA256, rather than pretending to verify the full-file ENA MD5.

Mean aligned depth using bases with quality at least 20 is approximately
2.46×, with uneven coverage. The subset is useful for technical comparisons,
but is too shallow to assess sensitivity reliably. Selection enriches for
mitochondrial mappings and does not test whole-genome mapping specificity.
The reference is linearized; circular-origin-spanning reads require separate
testing. No truth calls are provided.

## Recreate the files

After building native BWA, install the regeneration dependency in a Python
environment and run:

```sh
python3 -m pip install pysam==0.23.3
python3 scripts/fetch-test-data.py --regenerate --bwa build/native/bwa
```

The script fetches only checksum-pinned source files, uses bounded Range
requests for the large ENA files, and recreates the datasets in a temporary
directory. It replaces committed data only if every generated checksum
matches. It leaves the recorded provenance snapshots and licence unchanged.
Rebuilding compressed BAM files may depend on the linked zlib implementation;
an unexplained byte difference must be investigated, not accepted by changing
the expected checksum. The distributed fixtures themselves remain fixed.
