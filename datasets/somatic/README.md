# Real matched tumour/normal somatic fixtures

This fixture uses biological whole-genome reads from the SEQC2 breast-cancer
cell line **HCC1395** and the matched B-lymphocyte cell line **HCC1395BL**.
No read bases, quality scores, variants, or reference bases were simulated or
spiked into the data. Two real chr21 windows are rebased to short local contigs
so the browser can use a complete 29,000-base reference without masked padding.

The source files are NCBI's `WGS_NS_T_1.bwa.dedup.bam` and
`WGS_NS_N_1.bwa.dedup.bam`, from the public study SRP162370. Original read
groups, alignment flags, CIGARs, sequences, qualities, mapping qualities,
template lengths, and optional tags remain unchanged in retained records.
Only the primary reference names/IDs and read/mate positions are transformed.

## Regions and files

| Original GRCh38 interval, 1-based inclusive | Local contig | Reference bases | Evaluation interval, BED coordinates |
| --- | --- | ---: | --- |
| chr21:10572001–10582000 | `chr21_10572001_10582000` | 10,000 | 2000–8000 |
| chr21:21404501–21423500 | `chr21_21404501_21423500` | 19,000 | 2000–17000 |

For a local 1-based position, add the manifest's `start0` to obtain its original
GRCh38 1-based position. The top-level `regionMappings` object is authoritative.

`hcc1395/` contains the following ready-to-use files:

- `reference.fa` and `.fai`: genuine uppercase hg38 chr21 sequence slices.
- `tumor.bam` and `.bai`: 13,130 alignment records from 6,565 complete pairs.
- `normal.bam` and `.bai`: 16,822 alignment records from 8,411 complete pairs.
- `regions.bed`: evaluation windows with 2,000 bases of context on each side.
- `extracted-regions.bed`: the full local reference intervals.
- `truth.vcf`, `truth-snv.vcf`, and `truth-indel.vcf`: the four SEQC2 truth
  records in the evaluation windows, with coordinates rebased and alleles and
  other fields preserved.
- `confidence.bed`: the original SEQC2 high-confidence mask intersected with
  the evaluation windows and rebased consistently.

The VCFs are ordinary text VCF, not BGZF/tabix-indexed exclusion databases.
They are somatic truth annotations, not dbSNP.

## Selection and coordinate transformation

The source BAMs are approximately 80 and 67 GB. We fetched only their original
indexes, headers, and bounded BGZF byte ranges for the two regions. Every
downloaded range has an exact URL, inclusive byte interval, byte count, and
SHA256 in `manifest.json`. The downloader requires HTTP 206 with the exact
`Content-Range`, so a server ignoring range requests cannot trigger a whole-BAM
download. Published full-file MD5s are preserved in `provenance/WGS.md5` and the
manifest; those full-file MD5s were not independently recomputed.

The original-coordinate snapshots under `provenance/original/` preserve the
original BAM headers and records. The bounded source extraction includes
overlapping alignments whose start is no more than 1,000 bases before a window;
the final selection is stricter and keeps only complete primary mapped pairs
whose two alignments lie wholly inside the same window and whose mate fields
agree. Secondary, supplementary, unmapped, incomplete, and boundary-crossing
pairs are excluded. Duplicate and QC-failure flags are retained where present,
so each caller applies its own filters. No depth subsampling is performed.

Both mates move by the same coordinate offset, preserving insert length and
proper-pair semantics. The final BAM headers retain the original sequence
dictionary and read groups, append the two local sequence entries, and add a
deterministic provenance program/comment record. Original optional tags that
describe other alignments, such as `SA` or `XA`, retain their original
coordinate system; those original reference names remain in the dictionary.
There are no final primary records or mates on those unused original entries.

`provenance/read-integrity-audit.json` records comparison of every retained
alignment against its original record. All 29,952 records preserve SEQ, QUAL,
CIGAR, TLEN, MAPQ, FLAG, and optional tags, and both positions shift correctly.
Reference sequence reconstructed from each record's MD tag agrees with the
real uppercase hg38 reference. An independent HTSlib remote query agreed
exactly with all 7,051 original tumour records in the first window.

## What the truth annotations establish

The VCF comes from the official Google DeepSomatic mirror of SEQC2's merged
high-confidence SNV/indel truth v1.2.1. The mask is SEQC2 high-confidence regions
v1.2. These truth records aggregate evidence across many sequencing replicates;
they do not guarantee alternate-read support in this particular NS_T_1 file.

Observed support below uses base quality at least 20, mapping quality at least
20, excludes flags `0x704`, and avoids counting overlapping mates twice. These
counts are descriptive, not a separate truth set or calibrated sensitivity test.

| Original position and allele | Tumour alternate/depth | Normal alternate/depth |
| --- | ---: | ---: |
| chr21:10576679 A>G | 17/66 | 0/68 |
| chr21:10576939 CA>C | 14/57 | 0/72 |
| chr21:21409525 GA>G | 14/38 | 0/50 |
| chr21:21418545 C>T | 0/51 | 0/45 |

The final SNV has a low aggregate VAF in the published truth set but no observed
alternate support under these filters in this replicate. A caller missing it
does not establish a numerical port defect. SomaticSniper's default native run
produces nonempty output including the first true SNV; its default output also
contains other candidates and loss-of-heterozygosity calls. The separate
validation report assesses native-versus-WebAssembly agreement.

The windows were deliberately chosen around known variants. Pair selection,
window boundaries, and the reduced number of evaluated bases affect coverage,
context, and multiple-testing correction. Use the same fixed inputs and options
for native/WASM comparisons. Do not interpret these small biased windows as a
whole-genome accuracy, contamination, tumour purity, or clinical benchmark.
The cell lines can have copy-number changes; a diploid caller setting is a
model choice, not a biological assertion about every locus.

## Running and rebuilding

From `hcc1395/`, the baseline SomaticSniper command is:

```sh
somaticsniper -F vcf -f reference.fa tumor.bam normal.bam calls.vcf
```

For LoFreq indel calling, prepare separate working BAMs with its real upstream
`indelqual --dindel` command and index them before running the somatic workflow.
The bundled input BAMs contain the original alignment tags; they are not
preprocessed by LoFreq. Native LoFreq's standalone default `call` creates a
temporary file under `/tmp`, which must be writable on the validation host.

The fetcher is independently usable from an extracted standalone tool package:

```sh
python3 scripts/fetch-somatic-test-data.py --verify
python3 scripts/fetch-somatic-test-data.py --fetch-sources
python3 -m pip install pysam==0.23.3
python3 scripts/fetch-somatic-test-data.py --regenerate
```

Verification and source fetching use Python's standard library only. Exact
regeneration requires **pysam 0.23.3 with bundled SAMtools/HTSlib 1.21**; it uses
no external samtools binary or unrelated dataset directory. PyPI source and
Linux CPython 3.12 wheel identifiers and hashes are recorded in
`provenance/pysam-source.json`; the pinned source distribution is included in
the source cache. Install an appropriate wheel for your interpreter/platform.

The canonical source cache is `build/somatic-sources`; use `--cache PATH` to
override it. Its 54 pinned files/ranges total 64,622,096 bytes, including the
whole compressed chr21 reference, truth/mask, original indexes, optional pysam
source, and small raw BAM byte ranges. No whole-genome BAM is downloaded.
With this cache, regeneration is offline. It reconstructs the original BAM
snapshots, selects and rebases pairs, creates indexes, and transforms the
truth/mask in a temporary directory. It replaces files only after all generated
sizes and hashes agree with the immutable manifest. Exact regeneration passed
with Python 3.12.14 and pysam 0.23.3.

## Source attribution

- Fang et al. (2021), *Establishing community reference samples, data and call
  sets for benchmarking cancer mutation detection using whole-genome
  sequencing*, Nature Biotechnology 39, 1151–1160.
  <https://doi.org/10.1038/s41587-021-00993-6>
- Original SEQC2 BAMs and indexes:
  <https://ftp-trace.ncbi.nlm.nih.gov/ReferenceSamples/seqc/Somatic_Mutation_WG/data/WGS/>
- Official SEQC2 truth-set description:
  <https://sites.google.com/view/seqc2/home/data-analysis/high-confidence-somatic-snv-and-indel-v1-2>
- Official Google DeepSomatic data/truth mirror documentation:
  <https://github.com/google/deepsomatic/blob/r1.10/docs/deepsomatic-case-study-wgs.md>
- UCSC GRCh38/hg38 chr21 reference:
  <https://hgdownload.soe.ucsc.edu/goldenPath/hg38/chromosomes/chr21.fa.gz>

These are publicly distributed research cell-line data. Preserve their source
attribution and provider terms. The software project's license is not an
assertion that every upstream dataset has that same license.
