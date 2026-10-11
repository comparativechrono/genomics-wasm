# Real Cutadapt test reads

These small fixtures contain unchanged biological read sequences and qualities.
They test trimming behavior and agreement between native and browser Cutadapt,
not contamination prevalence, sequencing quality, or biological accuracy.
No adapters, errors, Ns, or quality scores have been added to any read.

## Yeast: adapter-rich paired reads

`yeast/reads_1.fastq` and `yeast/reads_2.fastq` contain 384 real 101-base pairs
from **Saccharomyces cerevisiae SRR6357070**, wild-type total RNA-seq replicate 1
in GSE110004, generated on an Illumina HiSeq 2500. The upstream source is the
nf-core test-datasets repository at commit
`cad884e7fbe5617dcd55bdd07d9ddf65b54febe4`. Its two original compressed files
each contain 50,000 reads. nf-core first enriched for chromosome I with Kraken2
and sampled with seqtk seed 100; its original procedure, publication, and license
are preserved under `provenance/`.

Our selection takes the first 96 pairs in source order in each of four mutually
exclusive groups, evaluated in this order:

1. Both mates contain their complete 33-base adapter query below.
2. Either mate contains `AGATCGGAAGAGC`, excluding group 1.
3. Either mate has a quality below Phred 20 in its final 10 bases, excluding groups 1 and 2.
4. All remaining pairs.

The final output preserves source order. Every selected FASTQ record, including
its complete header and qualities, is copied verbatim. `yeast/selection.tsv`
records each name and group; `selection-population.json` records the source
population counts. This intentional enrichment makes adapter, quality, minimum
length, and paired-read filtering tests useful with only 384 pairs. It does not
represent the run's natural contamination rate.

The configured Illumina-compatible 3′ sequence queries are:

| Mate | Query |
| --- | --- |
| R1 | `AGATCGGAAGAGCACACGTCTGAACTCCAGTCA` |
| R2 | `AGATCGGAAGAGCGTCGTGTAGGGAAAGAGTGT` |

These exact sequences occur in the source reads; no particular commercial kit
or independently established per-read contamination truth is asserted. Short
or imperfect Cutadapt matches can also occur by chance. The real data include
both partial and imperfect matches, and two R2 records with terminal Ns.

From the `yeast/` directory:

```sh
cutadapt -j 1 \
  -a AGATCGGAAGAGCACACGTCTGAACTCCAGTCA \
  -A AGATCGGAAGAGCGTCGTGTAGGGAAAGAGTGT \
  -o trimmed_1.fastq -p trimmed_2.fastq reads_1.fastq reads_2.fastq
```

For a single-end case, use R1 alone and omit `-A`, `-p`, and the R2 input.

## Human: existing BAM-derived qualities

`human/reads_1.fastq` and `human/reads_2.fastq` contain 256 real 72-base pairs
from **HeLa ERR127306**, the HNRNPC experiment ERP001458 / E-MTAB-1147. They
are the previously prepared public Bioconductor
`RNAseqData.HNRNPC.bam.chr14` 0.50.0 subset. Complete primary pairs wholly
within hg19 chr14:102480001–102520000 were sorted by read name, and the first
256 were selected. Read orientation was restored using the SAM reverse flag;
numeric BAM qualities were preserved and emitted as Phred+33.

These are recovered alignment records, not raw sequencer FASTQ. The upstream
TopHat command used `--solexa-quals`; numeric values in these particular BAM
records range from 0 to 10. Prior processing and quality conversion remain.
Do not interpret a Phred-20 experiment on this fixture as the quality of the
original sequencing run. A mild `-q 5` threshold provides a useful nonempty
quality-trimming case; `-q 5 -m 20` also removes some reads. This fixture has no
exact `AGATCGGAAGAGC` matches and supplies a useful adapter-negative control.

Compact immutable input snapshots, the original package DESCRIPTION, metadata,
and exact archive/BAM identifiers and checksums are included in `provenance/`.
Rebuilding the Cutadapt fixture copies those snapshots; it does not redownload
the approximately 349 MB original package or repeat the earlier BAM extraction.
The original extraction recipe and source URL are in `human-source.json`.

## Additional matching examples

The observed biological R1 prefix `CTAAAACTTGCC` can exercise ordinary 5′,
anchored 5′, and linked matching on the unchanged yeast reads. For example,
`-g CTAAAACTTGCC`, `-g ^CTAAAACTTGCC`, or
`-a '^CTAAAACTTGCC...AGATCGGAAGAGCACACGTCTGAACTCCAGTCA'`.
The prefix is a user-specified sequence query, **not an asserted adapter or
primer contamination**. The linked example uses Cutadapt's normal required/
optional-end semantics; add `;required` to the 3′ query to require both ends.
An adapter query with a wildcard, `-a AGATCGGAANAGC`, exercises wildcard
matching without modifying any biological read.

`provenance/native-smoke.json` records exact commands, read/base counts, and
output hashes from native Cutadapt 5.2, dnaio 1.2.4, xopen 2.1.0 on Python
3.12.14. It is an option-effect check; the separate validation report compares
the version-matched native, patched-native, and WebAssembly runtimes.

Selected native observations:

| Case | Observed effect |
| --- | --- |
| Yeast full paired adapters | 205 R1 and 209 R2 matches; 12,061 bases removed |
| Same with `-e 0` | 200 R1 and 198 R2 matches |
| Same with `-O 20` | 154 matches per mate |
| Same with `-m 80` | 246 of 384 pairs retained |
| Same with `--discard-untrimmed` | 202 pairs retained |
| Same with `--discard-trimmed` | 172 pairs retained |
| Yeast `-q 30` | 4,376 bases quality-trimmed |
| Yeast `--trim-n` | 193 terminal N bases removed |
| Human R1 `-q 5 -m 20` | 1,882 bases quality-trimmed; 246 of 256 reads retained |

## Offline verification and exact regeneration

The manifest pins every included data/provenance file by byte size and SHA256.
Plain and gzip FASTQ contain identical records; gzip headers have zero time
and no embedded filename. Normal tests and standalone packages use these
committed compact fixtures directly.

From the repository or an independently extracted tool package:

```sh
python3 scripts/fetch-cutadapt-test-data.py --verify
python3 scripts/fetch-cutadapt-test-data.py --fetch-sources
python3 scripts/fetch-cutadapt-test-data.py --regenerate
```

All three modes require only Python 3.10+ and its standard library. The source
cache is `build/cutadapt-sources`; `--cache PATH` overrides it. Fetching downloads
only the two pinned raw yeast archives (about 4.5 MB total), and verifies both
size and SHA256. `--fetch-sources` changes no fixture bytes or manifest.
With these cached files, regeneration is offline and uses the included human
snapshots. It builds in a temporary directory and replaces fixture files only
after **all generated hashes match** the immutable manifest. No aligner,
reference, Cutadapt installation, or other dataset directory is needed.

Regeneration was prepared with Python 3.12.14 / zlib 1.2.13. Gzip generation
uses a fixed compression level 9. A different zlib implementation that emits
different compressed bytes will be rejected by the checksum gate rather than
silently changing the pinned fixture.
