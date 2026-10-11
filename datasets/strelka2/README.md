# Real HCC1395 fixtures for Strelka2

These files reuse the real SEQC2 HCC1395 tumour and matched HCC1395BL normal
reads from the independently audited somatic fixture. They contain 6,565 tumour
pairs and 8,411 normal pairs. No read, quality, reference base, or variant is
synthetic or spiked into the data.

The only additional normalization is the BAM sequence dictionary: unused
original chromosome entries are removed, and numeric read/mate reference IDs
are reassigned to the two retained local contigs. The serialized SAM alignment
records before and after normalization are **identical**, including positions,
SEQ, QUAL, CIGAR, FLAG, MAPQ, TLEN, read groups, and every optional tag.
`provenance/dictionary-normalization.json` records the matching SHA256 values.

## Why a separate fixture is needed

Upstream Strelka2 2.9.10's `checkChromSet.py` requires every BAM chromosome to
exist in the reference FASTA with the same length. Input BAM dictionaries must
also agree in order. The previous somatic BAMs retain 2,779 unused original
chromosomes for provenance, in addition to the two local contigs. Their FASTA
contains only the two genuine local sequence slices, so upstream configuration
rejects those BAM headers.

This fixture makes the BAM and FASTA dictionaries equal without weakening
Strelka's checks, adding reference padding, fetching a whole genome, changing
the reads, or modifying the earlier manifested dataset. The original read-group,
program, and comment records are retained, and a deterministic program record
documents dictionary normalization.

`SA` and `XA` tags retain their original-genome annotations. These annotations
may name chromosomes outside the local dictionary; they are not local primary
or mate alignments. Upstream Strelka checks for SA presence when recognizing
non-strict supplementary records and prints it for diagnostics; the inspected
code does not resolve SA/XA coordinates against the FASTA. Removing these tags
would change the input semantics, so they are preserved. There are 11 tumour
and 15 normal SA-tagged records, and 5,356 tumour and 6,635 normal XA-tagged
records. All final primary records and mapped mates use the two local contigs.

## Biological data and coordinates

The source is NCBI SEQC2 study SRP162370, specifically the public BWA-deduplicated
whole-genome files `WGS_NS_T_1.bwa.dedup.bam` and
`WGS_NS_N_1.bwa.dedup.bam`. Complete primary mapped pairs were selected when
both alignments lay wholly inside the same reference window and their mate
fields agreed. Both read/mate positions were shifted by the same offset.
Sequences, qualities, flags, alignment CIGARs, and template lengths were not
changed. No depth subsampling was used.

| Original GRCh38 interval, 1-based inclusive | Local contig | Length | Evaluation BED interval |
| --- | --- | ---: | --- |
| chr21:10572001–10582000 | `chr21_10572001_10582000` | 10,000 | 2000–8000 |
| chr21:21404501–21423500 | `chr21_21404501_21423500` | 19,000 | 2000–17000 |

Add the top-level manifest `regionMappings.start0` to a local 1-based position
to recover its original GRCh38 1-based position. The reference is genuine
uppercase hg38 chr21 sequence, with no masked or fabricated padding.

`hcc1395/` contains `reference.fa`/`.fai`, `tumor.bam`/`.bai`,
`normal.bam`/`.bai`, `regions.bed`, `extracted-regions.bed`, `truth.vcf`,
`truth-snv.vcf`, `truth-indel.vcf`, and `confidence.bed`. The evaluation and
confidence BED files also have BGZF-compressed `.gz` files and `.gz.tbi` indexes
for upstream workflow configuration. The two reference windows include 2,000
bases of context on each side of the evaluation intervals.

The truth annotations are the official DeepSomatic mirror of SEQC2 merged
SNV/indel truth v1.2.1 and high-confidence regions v1.2, with coordinates rebased
consistently. They contain two SNVs and two deletions. The earlier integrity
audit, preserved in `provenance/source-read-integrity-audit.json`, compares all
29,952 records with the original source alignments and verifies reference bases
against their MD tags.

Observed MQ20/BQ20 support in this replicate is 17/66 tumour reads for the first
SNV, 14/57 and 14/38 for the deletions, and zero alternate observations in the
corresponding normal sample. The low-frequency truth SNV at original
chr21:21418545 has 0/51 tumour observations under these filters. SEQC2 truth
aggregates multiple replicates; a caller missing that site does not establish a
numerical port failure.

These windows were selected around known variants and are useful for executable
agreement and option tests. They are not an unbiased whole-genome or clinical
accuracy benchmark. Strelka's depth estimator accepts short contigs, but its
depth estimates here describe the selected windows, not the original whole
chromosome. Use the same fixed inputs, estimation stages, scoring models, and
options for native/WebAssembly comparisons. The separate validation report
records actual caller outputs.

## Native workflow example

With an upstream Strelka2 2.9.10 installation and its supported Python runtime,
run from `hcc1395/`:

```sh
configureStrelkaSomaticWorkflow.py \
  --normalBam normal.bam --tumorBam tumor.bam \
  --referenceFasta reference.fa --callRegions regions.bed.gz \
  --runDir strelka-run
strelka-run/runWorkflow.py -m local -j 1 -g 2
```

This keeps the upstream dictionary validation and regional workflow behavior.
The bundled BGZF BED/TBI files are ordinary input preparation, not caller output.
The explicit memory allocation avoids relying on host memory autodetection.

The native 2.9.10 workflow completed on these files with normal-sample depth
estimates of 80 and 43 for the 10 kb and 19 kb contigs. It emitted 13 SNV records
(one PASS) and two indel records (both PASS). All three passing alleles match
the published truth annotations. `provenance/native-compatibility.json` records
this fixture suitability check, including source/input identities and the
expected configuration rejection for the previous unpruned BAM dictionary.
This observation is separate from the release's native/WebAssembly validation.

## Reproducibility and standalone use

```sh
python3 scripts/fetch-strelka2-test-data.py --verify
python3 scripts/fetch-strelka2-test-data.py --fetch-sources
python3 -m pip install pysam==0.23.3
python3 scripts/fetch-strelka2-test-data.py --regenerate
```

Verification and source fetching need only Python's standard library. Exact
regeneration requires pysam 0.23.3 with bundled SAMtools/HTSlib 1.21. Source and
Linux CPython 3.12 wheel identities are recorded in `provenance/pysam-source.json`.

The 54 source files/ranges use the existing `build/somatic-sources` cache;
`--cache PATH` overrides it. They total 64,622,096 bytes. Only small, precisely
bounded BGZF ranges are downloaded from the 67/80 GB original BAMs. Every range
is pinned by URL, inclusive byte interval, byte size, and SHA256. HTTP 206 and
the exact Content-Range are required. Whole-genome BAMs are never downloaded.
The cache also includes the compressed chr21 reference, truth/mask, source
indexes, published BAM MD5 list, and optional pinned pysam source.

The exact original extraction helper and manifest are included as
`provenance/somatic-extraction.py` and `provenance/somatic-source-manifest.json`.
Regeneration imports that verified helper, reconstructs the original source
snapshots and regional somatic files in a temporary directory, and checks all
20 generated source-fixture hashes. It then normalizes only the BAM dictionaries
and prepares BED indexes. Final files are replaced only if every expected hash
matches. No other dataset directory or repository fetch script is required.

The archived source manifest describes intermediate original-coordinate BAM
snapshots that are reconstructed in temporary staging; its paths are not extra
required files in this package. The current Strelka manifest lists every actual
bundled file. All preparation helpers and source provenance are checksum-bound.

## Attribution

- Fang et al., *Nature Biotechnology* (2021), SEQC2 reference samples and calls:
  <https://doi.org/10.1038/s41587-021-00993-6>
- Original sequencing alignments and indexes:
  <https://ftp-trace.ncbi.nlm.nih.gov/ReferenceSamples/seqc/Somatic_Mutation_WG/data/WGS/>
- SEQC2 truth-set description:
  <https://sites.google.com/view/seqc2/home/data-analysis/high-confidence-somatic-snv-and-indel-v1-2>
- Official Google mirror documentation:
  <https://github.com/google/deepsomatic/blob/r1.10/docs/deepsomatic-case-study-wgs.md>
- UCSC hg38 chr21 reference:
  <https://hgdownload.soe.ucsc.edu/goldenPath/hg38/chromosomes/chr21.fa.gz>

These are publicly distributed research cell-line data. Preserve source
attribution and provider terms; the software project's license does not assign
that license to every upstream dataset.
