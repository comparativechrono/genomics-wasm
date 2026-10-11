# Real RNA-analysis parity fixtures

These compact fixtures exercise featureCounts, StringTie and mosdepth on **real biological RNA-seq reads and real genome/annotation sequences**. They test native/WebAssembly implementation equivalence. They are not truth sets, expression benchmarks, accuracy rankings or representative whole-genome workloads. No read bases, qualities, gene sequences, alignments or transcript models were invented to manufacture an option effect.

| Fixture | Biological source | Reference | Fragments | Alignment records | Spliced records | Primary pairs with overlapping aligned bases |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| `human` | HeLa RNA-seq, ERR127306, HNRNPC experiment ERP001458 / E-MTAB-1147 | hg19 chr14:102,480,001–102,520,000; local 40 kb contig | 256 | 512 | 221 | 66 |
| `yeast` | Wild-type RNA-seq replicate, SRR6357070, GSE110004 / SRP126328 | R64-1-1 chromosome I, 230,218 bp | 320 | 749 | 190 | 207 |

The yeast alignments include 168 records with `NH > 1`, 109 secondary records, MAPQs 0/1/60, deletions, soft clips and unmapped reads. The human window contains genuine overlapping sense and antisense GENCODE genes. These properties give nonzero differences for multimapping, overlap, strand, pair-count and coverage options without making synthetic reads.

## Files and coordinates

Each fixture directory contains:

- `reference.fa` / `.fai`: complete reference used for these alignments. BAM/CRAM `@SQ LN` matches the compact reference; mosdepth never allocates an array for a full human chromosome.
- `reads_1.fastq`, `reads_2.fastq`: original selected biological read records, paired in matching order.
- `annotation.gtf`: real source exon annotations; `gene_id` and `transcript_id` are present.
- `input.sam`, `input.bam`, `.bam.bai`: coordinate-sorted native HISAT2 alignments with all primary/secondary/unmapped records retained.
- `input.cram`, `.cram.crai`: the same alignments encoded as CRAM 3.0. Use the supplied FASTA when decoding; `UR:reference.fa` and reference MD5 values are portable.
- `regions.bed`: annotated gene spans, including introns and genuine overlapping genes.
- `exons.bed`: the union of source exons within each gene. Different genes can overlap; intervals are not a disjoint genome partition.
- `half1.bam`, `half2.bam` and indexes: alternate query names in lexicographic order, split as complete fragments. The halves have no fragments in common and together reproduce all input records. They are technical partitions of one sample, not biological replicates. Assemble them separately and use StringTie `--merge` to exercise merge behaviour.
- `observations.json`: measured input properties. CIGAR operation codes follow SAM: 0=M, 2=D, 3=N, 4=S.

Human contig `chr14_102480001_102520000` uses local positions. Add 102,480,000 to its 1-based positions to recover hg19 chromosome 14 positions. Yeast contig `I` keeps source coordinates. BED coordinates are zero-based, half-open; GTF coordinates are one-based, inclusive.

## Read and annotation provenance

Human reads are the existing, checksum-pinned 256-pair ERR127306 subset from the Bioconductor `RNAseqData.HNRNPC.bam.chr14` 0.50.0 package. The selection and original package/BAM hashes are preserved in `provenance/rna-read-manifest.json`. These reads were recovered from an existing TopHat2 BAM. Its previous processing and numeric quality scores remain; this is not a claim that those BAM-derived FASTQs are byte-identical to raw instrument files. The 40 kb window and its observed splice hints are copied unchanged. Human splice hints were derived from these reads in the earlier fixture; annotation-assisted mapping therefore cannot establish independent splice-prediction accuracy.

Human exon models come independently from UCSC's GENCODE basic v19 genePred table and matching attributes table, not from these alignments. The original selected rows, transcript/gene identifiers and upstream file hashes are included. Exons are intersected with the 40 kb reference window and shifted to local positions. Thus some models are transcript fragments at the window boundary. No outside-window exon is relocated or fabricated. This retains the real overlap between DYNC1H1 and the antisense gene RP11-1017G21.4.

Yeast starts with the existing 256-pair SRR6357070 fixture. That earlier fixture selected 128 pairs spanning annotated introns and 128 other uniquely mapped pairs, then copied their untrimmed records from the pinned nf-core FASTQs. This RNA-analysis fixture adds the first 64 lexicographically named complete primary pairs with upstream STAR `NH > 1` for at least one mate. Those names are recorded in `yeast/additional-multimap-names.txt`; none duplicates a base-fixture name. Their unmodified original FASTQ records are appended in source order. The source BAM is used only for name selection. All 320 pairs are then newly aligned with HISAT2.

Yeast reference and exon GTF derive from nf-core/test-datasets commit `cad884e7fbe5617dcd55bdd07d9ddf65b54febe4`, using Ensembl iGenomes R64-1-1 chromosome I. The real chromosome-I GTF exon records are retained unchanged. The pinned nf-core README documents its previous chromosome enrichment and 50,000-pair sampling. That selection, our splice/multimap enrichment and the small reference affect mapping and expression distributions. The fixtures deliberately exercise software branches and cannot estimate whole-genome specificity or sensitivity.

Source accessions, sample XML, upstream notices, original read/annotation manifests, exact source URLs, sizes and SHA-256 values are included in `provenance/` and `manifest.json`. The nf-core source BAM also includes another run; the new selection explicitly requires `SRR6357070.` query names.

## Preparation commands

The native preparation aligner is unmodified HISAT2 2.2.2 at commit `99583d7536b9ee017ac07de8834017a3bf99a2fe`. The exact source archive hash and build guidance are recorded under `regeneration` and `sources` in the manifest; its licence and source lock are included in provenance. After compiling the pinned source with `make -j2 CXX=g++ hisat2-build-s hisat2-align-s`, each fixture uses:

```sh
hisat2-build-s -p 1 reference.fa reference
hisat2-align-s -p 1 --seed 0 --dta -k 10 \
  --known-splicesite-infile splice-sites.tsv \
  -x reference -1 reads_1.fastq -2 reads_2.fastq -S raw.sam
```

Preparation uses `pysam==0.23.3`, which bundles SAMtools/HTSlib 1.21, for sorting, indexing, BAM/SAM conversion and CRAM writing. No external samtools executable is required. Host-specific HISAT2 `@PG` text is replaced by a fixed record containing the actual version and argument list; alignment fields/tags are unchanged. CRAM reference URI and file ID use relative names, avoiding machine-specific paths. Both technical halves retain the parent coordinate order and every record belonging to their selected fragments.

## Offline verification and regeneration

Normal CI and packaged-release validation require only Python's standard library and perform no downloads:

```sh
python3 scripts/fetch-rna-analysis-test-data.py --verify
```

The manifest verifies sizes and hashes of every distributed fixture and provenance file. All compact seed references, read pairs, splice hints and annotation excerpts are included under `provenance/inputs`; regeneration does not depend on other RNA or quantification directories in the repository.

To fetch and verify all four pinned source archives, including the native HISAT2 source, without regenerating any fixture or requiring pysam/native programs:

```sh
python3 scripts/fetch-rna-analysis-test-data.py --fetch-sources
```

This populates the canonical `build/rna-analysis-sources` cache (`--cache` can override it), reuses already verified archives, and changes no fixture bytes. Independent release packages include these cached archives so regeneration can run offline after installing the documented prerequisites. With the pinned Python package and native HISAT2 executables installed, regenerate with:

```sh
python3 scripts/fetch-rna-analysis-test-data.py --regenerate \
  --hisat2-build /path/to/hisat2-build-s \
  --hisat2-align /path/to/hisat2-align-s
```

Regeneration verifies immutable snapshots, downloads about 11 MB of checksum-pinned nf-core BAM/FASTQ source files for the additional yeast selection if they are not cached, rebuilds outputs in a temporary directory and verifies every generated byte before replacing files. The default cache is `build/rna-analysis-sources`; `--cache` changes it. A repeated clean preparation in this environment reproduced SAM, BAM, CRAM, indexes and all text files byte for byte.

The included original manifests document the earlier compact-input extraction from full public sources. Recreating those earlier seeds independently would download the Bioconductor human package and other original sources (roughly 389 MB for the read-selection pipeline); ordinary RNA-analysis regeneration uses their included checksum-verified snapshots instead. No claim of binary stability across arbitrary compiler/HTSlib versions is made; a mismatch fails rather than silently blessing new output.

## Observed native sanity checks

These observations are evidence that the fixtures exercise options, not biological truth or an alternative to native/WASM comparisons. `provenance/native-smoke.json` records the measured totals. With native featureCounts 2.1.1, using `-p` without `--countReadPairs` counts reads:

| Setting | Human sum of assigned gene counts | Yeast sum |
| --- | ---: | ---: |
| Unstranded | 456 | 456 |
| `-s 1` | 298 | 31 |
| `-s 2` | 270 | 485 |
| `-M` | 456 | 461 |
| `-O` | 568 | 516 |
| `-M -O --fraction` | 512 | 488.33 |
| `-p --countReadPairs` | 246 | 244 |

Overlapping genes can make sums exceed the number of fragments; fractional values above are sums of featureCounts' printed decimal values. Native StringTie 3.0.3 produced 2 human/3 yeast transcripts without a guide, 2/18 with `-G annotation.gtf`, and 5/124 records with `-G annotation.gtf -e`. The last mode can emit zero-expression annotated models. Use output abundances as well as record counts when checking equivalence.

Spliced CIGARs, deletions and overlapping mates make mosdepth default versus `--fast-mode` a meaningful distinction; the smaller references also allow BAM/CRAM equivalence, minimum-MAPQ, flag-filter, region-mean, quantization and threshold tests without large memory allocations.
