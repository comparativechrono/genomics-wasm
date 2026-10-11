# RNA quantification test data

These are real public RNA-seq reads with matching annotated transcript sequences.
They test whether the browser builds reproduce the native tools. The selected
reads and restricted references are not suitable for estimating expression in the
original samples or comparing the biological accuracy of the two tools.

| Dataset | Run | Read pairs | Transcripts | Transcript bases |
| --- | --- | ---: | ---: | ---: |
| Human HeLa cells | ERR127306 | 256 | 5 | 16,735 |
| Yeast, wild-type replicate 1 | SRR6357070 | 256 | 124 | 150,264 |

Each directory contains `transcripts.fa`, `reads_1.fastq`, `reads_2.fastq`,
`tx2gene.tsv` and `transcripts.tsv`. FASTA identifiers match the first columns of
the two tables. Transcript and gene IDs retain their source versions where
present. `tx2gene.tsv` has a header and two columns; `transcripts.tsv` also records
gene names, lengths, strands and exon counts.

## Human

The reads come from [ERR127306](https://www.ebi.ac.uk/ena/browser/view/ERR127306),
part of the HNRNPC knockdown and control experiment
[E-MTAB-1147](https://www.ebi.ac.uk/biostudies/arrayexpress/studies/E-MTAB-1147).
They are the same 256 pairs as `datasets/rna-human`: complete primary pairs
inside hg19 chr14:102480001–102520000, recovered from the pinned
`RNAseqData.HNRNPC.bam.chr14` Bioconductor data package. Sequence orientation is
restored from the BAM flags; numeric qualities are written as Phred+33. Previous
alignment processing remains. No simulated reads, altered sequences or repeated
read pairs have been added.

Transcript models come from the UCSC hg19 `wgEncodeGencodeBasicV19` table. The
corresponding `wgEncodeGencodeAttrsV19` table supplies stable gene IDs. We select
genes with a Basic transcript whose span overlaps the read selection window, then
include all of their Basic transcripts on chromosome 14. This gives two DYNC1H1
transcripts and three transcripts of the overlapping antisense gene
RP11-1017G21.4. All annotated exons are extracted from the complete hg19 chromosome
14 sequence, including exons outside the read selection window. Negative-strand
transcripts are reverse-complemented. We do not add poly(A), padding or artificial
isoforms.

The 14,333-base and 938-base DYNC1H1 transcripts share sequence, so some fragments
are compatible with both. An initial native kallisto 0.52.0 paired-end run with
default k=31 processed all 256 fragments, pseudoaligned all 256, and classified
230 as unique. Its EM assigned nonzero, fractional counts to both DYNC1H1
transcripts. These are checks that the fixture exercises ambiguous allocation,
not known expression values. Reads cover only part of the larger transcript, and
GENCODE Basic omits models from the comprehensive annotation.

## Yeast

The reads come from [SRR6357070](https://www.ebi.ac.uk/ena/browser/view/SRR6357070),
wild-type total RNA-seq replicate 1 in
[GSE110004](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE110004).
They are the same 256 pairs as `datasets/rna-yeast`, copied byte for byte from the
existing fixture. That fixture selects 128 pairs with an annotated splice
junction and 128 other pairs from the real nf-core reads. The original untrimmed
FASTQ sequences and quality strings are retained.

The reference and annotation are pinned to
[nf-core/test-datasets commit cad884e](https://github.com/nf-core/test-datasets/tree/cad884e7fbe5617dcd55bdd07d9ddf65b54febe4),
which documents an Ensembl iGenomes R64-1-1 reference. We group all exon features
on chromosome I by transcript ID and extract them in transcript orientation.
Artificial annotation features with the contig name `not_in_genome` are ignored.
This yields 124 transcripts, including coding and noncoding genes. Transcript
names and sequence lengths remain as annotated. The annotation generally has one
transcript per gene, but some different transcripts share sequence.

The initial native kallisto paired-end run pseudoaligned 244 of 256 fragments,
with 230 unique fragments and fractional allocations for several transcripts.
Unmapped reads remain in the fixture. Chromosome I and the chosen read subset
omit the rest of the transcriptome, so they cannot establish whole-transcriptome
mapping specificity or unbiased abundance estimates.

## Verify and recreate

From the repository or unpacked source bundle root:

```sh
python3 scripts/fetch-quant-test-data.py --verify
python3 scripts/fetch-quant-test-data.py --regenerate
```

Verification is offline and needs only Python's standard library. Regeneration
uses about 39.3 MB of checksum-pinned UCSC and nf-core reference sources. It reuses
matching files in `build/quant-sources` and otherwise downloads them. It then
copies the verified reads from `datasets/rna-human` and `datasets/rna-yeast`.
Every generated file must match its recorded size and SHA-256 before the
committed files are replaced.

To recreate the read subsets from their original sources first:

```sh
python3 scripts/fetch-rna-test-data.py --regenerate
python3 scripts/fetch-quant-test-data.py --regenerate
```

The first command additionally requires `pysam==0.23.3` and downloads about
389 MB, mostly the original Bioconductor data package. Its complete source pins,
selection rules and caveats are copied into `provenance/rna-read-manifest.json`.

`manifest.json` records every reference source URL, byte count and SHA-256, each
transcript's ID and length, and checksums for the distributed data. The selected
original annotation rows are preserved in `provenance/`. That directory also
preserves ENA run metadata, the nf-core preparation notes and MIT notice, and the
Bioconductor package DESCRIPTION with its upstream LGPL declaration. Reference
sequences and annotations retain their original source attribution.
