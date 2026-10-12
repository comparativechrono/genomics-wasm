# Real annotation validation inputs

These inputs exercise Prodigal gene prediction and HMMER protein/DNA profile
analysis using authentic biological sequences. There are no simulated genomes,
random background sequences, invented alignments, or canned expected outputs.
Expected program outputs are calculated by the unchanged native upstream tools
during validation and compared with the WebAssembly implementations.

## Prodigal bacterial chromosomes

`prodigal/ecoli.fna` and `prodigal/salmonella.fna` contain the first 60,000 bases
of the first chromosome in these frozen NCBI RefSeq assemblies:

| File | Organism | Chromosome | Assembly |
| --- | --- | --- | --- |
| `ecoli.fna` | Escherichia coli K-12 MG1655 | NC_000913.3 | GCF_000005845.2 |
| `salmonella.fna` | Salmonella enterica Typhimurium LT2 | NC_003197.2 | GCF_000006945.2 |

The same coordinate rule, 1–60,000 inclusive, is applied to both genomes without
examining Prodigal results. All nucleotide characters are unchanged; only the
FASTA identifier, description, and line wrapping are generated. `combined.fna`
concatenates those records in the table's order. This gives individual training
inputs and a two-organism metagenomic input. A 60 kb fragment supports compact
functional testing; it is not a recommendation for production training quality.

These are the same pinned chromosome originals used by the metagenomics
fixture. Regeneration independently downloads and verifies the original NCBI
archives, so standalone annotation packages do not need the metagenomics data.

## HMMER protein families and DNA repeats

The original HMMER 3.4 release archive supplies the tutorial data. Unchanged
files are extracted from `hmmer-3.4/tutorial/`; their original member paths,
byte counts and SHA-256 identities are recorded in
`provenance/fixture-sources.json`. The source archive itself is independently
pinned in `provenance/sources.lock.json`.

| Local family files | Original source | Biological scope |
| --- | --- | --- |
| `hmmer/globins.sto`, `globins.hmm` | `globins4.sto`, `globins4.hmm` | Four structurally aligned globins, from the Bashford structural alignment cited by the upstream guide |
| `hmmer/pkinase.sto`, `pkinase.hmm` | `Pkinase.sto`, `Pkinase.hmm` | Protein kinase seed alignment PF00069.24 and older original profile PF00069.17 |
| `hmmer/fn3.sto`, `fn3.hmm` | `fn3.sto`, `fn3.hmm` | Fibronectin type III seed alignment PF00041.20 and older original profile PF00041.13 |
| `hmmer/made1.sto`, `made1.hmm` | `MADE1.sto`, `MADE1.hmm` | Dfam DF0000629.2 human MADE1 transposable-element seed alignment and profile |

The frozen original HMMs allow independent search, conversion and pressed-index
tests. Validation also builds new profiles from the authentic alignments;
the original tutorial HMMs are inputs, not expected results for `hmmbuild`.
The release deliberately retains older kinase and fibronectin profiles than
its corresponding seed alignments. Their distinct accession versions are
recorded in the table and manifest, and no rebuilt-versus-original identity is
asserted.

`hmmer/globins45.faa` contains the unchanged 45-sequence globin FASTA from the
tutorial; `hmmer/hbb-human.faa` is its unchanged human beta-hemoglobin example.
`hmmer/sevenless-drome.faa` contains all 2,554 unchanged amino acids from the
tutorial's Swiss-Prot `7LESS_DROME` record (UniProt P13368, sequence version 2,
entry version 172). The recipe parses the original `SQ` block and writes FASTA;
it does not infer, trim, translate, or mutate the protein.
`hmmer/proteins.faa` combines these 47 sequences in that order, retaining their
identifiers. The globins provide diverse homologues, while the Sevenless
receptor provides a globin negative control and positive protein-kinase and
multiple fibronectin-type-III domains. The globins are negative controls for
those two domain families. This deliberately small, enriched target collection
does not represent a proteome; its E-values must not be interpreted as if they
came from a comprehensive database search.

`hmmer/dna-target.fa` is the unchanged 330 kb human chromosome-1 tutorial
fragment. The upstream guide describes four MADE1 elements in this fragment.
The near-palindromic repeat produces paired strand hits, making `nhmmer`
strand/coordinate comparison nontrivial. The upstream fragment header does not
provide assembly coordinates; this fixture preserves that limitation and does
not invent an accession or genomic interval.

The HMMER source license is preserved as `provenance/HMMER-LICENSE`. Pfam, Dfam
and UniProt identifiers and attributions are retained in the original input
files and the archive. See the HMMER 3.4 user guide's tutorial for the published
alignment and source descriptions. These compact cases establish computational
agreement; they do not measure annotation accuracy against curated ground truth.

## Reproduce and verify

```sh
python3 scripts/fetch-annotation-test-data.py --verify
python3 scripts/fetch-annotation-test-data.py --fetch
python3 scripts/fetch-annotation-test-data.py --regenerate
```

Originals are cached under `build/annotation-sources/`. Downloads require the
exact pinned size and SHA-256; changed upstream content is rejected. Python
3.11 or later suffices; no native compiler or biological executable is needed
to regenerate these input sequences. Normal regeneration uses a temporary
directory and checks every generated byte against the frozen manifest. It
also checks all provenance, source-lock and recipe identities. It never updates
the committed data or changes expected identities silently.

After deliberately changing a fixture recipe, a developer can explicitly run
`--regenerate --update-manifest`; the resulting fixture and manifest changes
must then be reviewed together and all numerical tests rerun. The manifest's
`observations` describe verified input content. Its `requiredEffects` describe
biological behaviors that validation must establish from fresh native results,
not precomputed golden outputs.
