# Test data

The [airway count matrix](deseq2-airway/README.md) is provided separately for
DESeq2. It contains 63,677 genes and eight human samples from four paired cell
lines. Its biological replicates are independent of the read subsets below.

These are small subsets of public sequencing data. They test whether a
WebAssembly build reproduces the native program's results. The alignment and
germline-caller subsets listed below have no truth calls and do not establish
variant-calling accuracy.

| Dataset | Reference | Paired FASTQ records | Fixed BAM records |
| --- | --- | ---: | ---: |
| Human NA12878 | 12,356 bp from GRCh37 chromosome 22 | 512 pairs | 3,333 |
| Yeast BY4741, SRR1569870 | 85,779 bp, sacCer3 chromosome M | 1,079 pairs | 2,158 |
| Human HeLa RNA, ERR127306 | 40,000 bp from GRCh37 chromosome 14 | 256 pairs | — |
| Yeast RNA, SRR6357070 | 230,218 bp, R64-1-1 nuclear chromosome I | 256 pairs | — |

The `human` and `yeast` directories contain `reference.fa`, its `.fai` index, `reads_1.fastq`,
`reads_2.fastq`, and a fixed `input.bam` with `.bai` index. Aligner tests use
the FASTQ files. Caller tests use the fixed BAM directly, so their input does
not depend on the aligner being tested. Combined pipeline tests are separate.

`manifest.json` records source URLs, source checksums, transformations and
checksums for the DNA data. The RNA files have a separate `rna-manifest.json`,
described below. Verification needs only Python:

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

## RNA data

The separate `rna-human` and `rna-yeast` directories contain genuine public
RNA-seq reads and genomic references with introns. Each has `reference.fa`,
`reads_1.fastq`, `reads_2.fastq`, `splice-sites.tsv` and `junctions.bed`.
The two FASTQ files contain 256 complete pairs, in matching order. No fixed
BAM is distributed for these fixtures: each aligner creates its own output.

The human reads are from [ENA run ERR127306](https://www.ebi.ac.uk/ena/browser/view/ERR127306),
part of the HeLa HNRNPC knockdown and control experiment E-MTAB-1147.
The source is the `ERR127306_chr14.bam` file in Bioconductor's
[`RNAseqData.HNRNPC.bam.chr14` 0.50.0 package](https://bioconductor.org/packages/3.23/data/experiment/html/RNAseqData.HNRNPC.bam.chr14.html).
That package aligned real paired reads to hg19 using TopHat2 and retained
chromosome 14 alignments. We select complete primary pairs lying within
chr14:102,480,001–102,520,000 inclusive, sort their names and take the first
256. Their 72 bp sequences and numeric quality scores are recovered in
sequencing orientation. Prior processing remains; this does not reconstruct
the original sequencer FASTQ files. The matching UCSC hg19 reference window
is named `chr14_102480001_102520000`, with coordinates starting at 1.

The yeast reads are from [ENA run SRR6357070](https://www.ebi.ac.uk/ena/browser/view/SRR6357070),
wild-type total RNA-seq biological replicate 1 from study GSE110004
([Wu et al., 2018](https://doi.org/10.1016/j.molcel.2018.10.018)).
We use the existing real-data subset in
[`nf-core/test-datasets`](https://github.com/nf-core/test-datasets/tree/cad884e7fbe5617dcd55bdd07d9ddf65b54febe4),
pinned to commit `cad884e7fbe5617dcd55bdd07d9ddf65b54febe4`.
Upstream selected chromosome I reads with Kraken2 and sampled 50,000 pairs.
Its procedure is retained in `provenance/nfcore-rnaseq-README.md`.

For our smaller yeast subset, the upstream STAR BAM is used only to select
read names. We require complete primary pairs from SRR6357070 with unique
alignments on chromosome I. We select 128 pairs with a splice matching the
upstream exon annotation and 128 other pairs, using the first names in
lexicographic order in each group. Their original 101 bp FASTQ records are
copied unchanged, in source order. The upstream BAM also contains a second
run; reads from that run are excluded. The complete chromosome I reference
is copied unchanged from the same pinned source.

The optional splice hints have different origins:

- Human: 42 junctions observed in the selected TopHat2 alignments, with strand
  taken from the `XS` tag. These are not independent annotations.
- Yeast: five junctions between consecutive exons in the source GTF annotation.

`splice-sites.tsv` uses HISAT2's zero-based flanking exon coordinates.
`junctions.bed` uses BED12 with 20 bp blocks on either side of each intron,
for minimap2. Those short blocks describe junction positions, not complete
exons or transcript models. Both datasets produce spliced alignments without
hints. Hint-assisted tests check software equivalence, not biological truth.

These subsets are enriched for the selected regions and, for yeast, splice
junctions. They do not measure whole-genome mapping specificity, splice
sensitivity or gene expression accuracy. Run records, upstream notices,
source hashes, coordinates and selection details are recorded in
`rna-manifest.json` and `provenance/`.

Verify the RNA files without downloads:

```sh
python3 scripts/fetch-rna-test-data.py --verify
```

To reproduce them, install `pysam==0.23.3` and run:

```sh
python3 scripts/fetch-rna-test-data.py --regenerate
```

Regeneration downloads about 389 MB, mostly the complete Bioconductor data
package, into `build/rna-sources`. Normal validation uses only the compact
committed files. The script checks every source hash and only replaces
generated files if all resulting hashes match the manifest.

## Recreate the DNA files

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

## RNA counts, transcripts and coverage

The [RNA analysis fixtures](rna-analysis/README.md) contain alignments of real
human and yeast RNA-seq reads, matching annotations and reference sequences.
They support featureCounts, StringTie and mosdepth comparisons. The fixture
manifest records source checksums, transformations and file hashes. These
restricted subsets do not establish performance on whole samples.

## Cutadapt reads

The [Cutadapt fixtures](cutadapt/README.md) contain 384 real yeast read pairs
selected to exercise adapter and quality trimming, and 256 real human read
pairs recovered from a public BAM. The human qualities retain their prior
processing; they are not presented as raw instrument qualities. Both datasets
include plain and gzip FASTQ, exact provenance and reproducible preparation.

## Metagenomics

The [metagenomics fixture](metagenomics/README.md) contains 4,096 real paired
reads from the sequenced Zymo mock community ERR2984773, plus selected genuine
reference regions and a small Kraken2 database. Native/browser comparisons
cover classification, database exchange and Bracken abundance estimation.
This deliberately limited reference collection is for implementation testing;
it cannot measure the original community's composition accurately.

## Somatic variants

The [somatic fixtures](somatic/README.md) contain real HCC1395 tumour and
HCC1395BL normal reads from two chromosome 21 regions, with the matching
reference sequence and published SEQC2 reference calls. Coordinates are shifted
to short local contigs; the original coordinates and every transformation are
recorded. Complete mapped read pairs are retained. Native-equivalence tests and
comparison with the published calls are separate checks.

The separate [Strelka2 fixture](strelka2/README.md) contains those same read
records with a BAM sequence dictionary matching the two-contig reference.
Unused reference entries are removed and numeric reference IDs are remapped;
read bases, qualities, alignment coordinates and tags are unchanged.

## Annotation fixtures

The [Prodigal and HMMER fixture](annotation/README.md) contains unchanged real
bacterial sequence windows, protein-family alignments, protein targets and a
human nucleotide target. Original source hashes, selection rules and a frozen
regeneration manifest are included. These inputs test native/browser agreement
for gene prediction and profile-HMM analysis.

