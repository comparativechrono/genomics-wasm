# Experimental bustools fixtures

These fixtures use public mouse single-cell RNA sequencing data from **SRR8599150**,
prepared with 10x Chromium v2. They exercise the original bustools algorithms
and compare their actual native, patched-native and WebAssembly outputs. They
are small, deliberately selected software-validation inputs, not representative
samples for biological inference, cell calling or expression analysis.

## Sources and integrity

The raw BUS file, matching FASTQ read pairs, equivalence classes and transcript
identifiers are unchanged files from
[`pachterlab/kb_python`](https://github.com/pachterlab/kb_python/tree/188d9c7034498d83be86a461659968dd8da2db7f/tests/fixtures)
at commit `188d9c7034498d83be86a461659968dd8da2db7f`. Its BSD license is
retained in `provenance/kb-python-LICENSE`.

The larger corrected BUS file, reference mapping and complete 10x v2 barcode
on-list come from the
[BUStools getting_started release](https://github.com/BUStools/getting_started/releases/tag/getting_started).
The accession identifies the public experiment; matching `@SRR8599150.*` read
identifiers are retained in the FASTQ inputs. These are experimental data, not
the simulated upstream bustools regression fixture.

`provenance/sources.lock.json` records the exact URL, byte length and SHA-256 of
all 11 original source files. Original downloads are cached in
`build/bustools-sources/`. The dataset manifest inventories every supplied
fixture and provenance file, binds the regeneration script, records measured
coverage, and repeats the source pins. `provenance/fixture-sources.json`
describes each generated file and transformation.

## Primary raw inputs

`bustools/input.bus`, `matrix.ec`, `transcripts.txt`, `R1.fastq` and `R2.fastq`
are byte-for-byte copies of the pinned kb_python files. There are 34 BUS
records and 2,500 matched FASTQ pairs. The raw records include repeated
barcode/UMI/EC observations, barcode errors, and off-list observations so that
sorting, correction and counting have nontrivial effects. The accompanying
reference contains 865 transcripts and 2,412 equivalence classes.

`transcripts-to-genes.txt` retains unchanged rows from the complete released
gene map for those 865 transcript identifiers. The kb_python test gene map is
not used because it does not completely cover these identifiers.

`whitelist.txt` retains every barcode from the full 737,280-entry 10x v2 on-list
at Hamming distance zero or one from any observed raw barcode, preserving
source order. It therefore preserves the complete candidate set, including
ambiguities, for default barcode correction of these inputs. It is a compact
validation reference, not the complete on-list for processing other data.

`read-index.bus` is a separately labelled extraction/transport fixture. The
original raw BUS flags count ambiguous bases; they are not FASTQ indices.
For this file only, the flags field is replaced with the verified zero-based
index of the matching authentic FASTQ read pair. Matching uses the unchanged
26-base R1 barcode/UMI sequence, permits a source `N` as a wildcard, and
consumes duplicate matches in original order. Barcode, UMI, EC, multiplicity,
padding and header bytes remain unchanged. The exact read identifiers,
indices and original flags are recorded in `provenance/read-index-map.json`.
No reads or alignments are invented.

## Larger corrected subset

`bustools/large/` is selected from the released, already corrected and sorted
`output.correct.sort.bus.gz`. Selection takes a prefix of at least 10,000
records and extends through the entire last barcode, retaining the boundary
molecule groups. It is a prefix with barcode-order selection bias and must
not be interpreted as a representative experimental subsample or a raw
barcode-correction benchmark.

The release contains transcript identifiers absent from its gene map. Records
whose equivalence class includes any such identifier are excluded explicitly;
no gene annotation is guessed. The exact missing identifiers, excluded ECs,
prefix size and exclusion count are in `provenance/fixture-sources.json`.
`provenance/large-record-map.tsv` preserves the exact original source-record
index of every retained BUS record.

Retained equivalence classes and transcripts are reindexed into compact
integer ranges in ascending original-ID order. Transcript memberships and
biological identifiers remain unchanged; reversible mappings are supplied in
`provenance/large-ec-map.tsv` and `large-transcript-map.tsv`. Every retained
BUS barcode, UMI, count, flag, padding byte and header remains unchanged;
only the EC integer is replaced by its recorded compact index. The selected
gene-map rows remove only transcript version suffixes to match the released
unversioned transcript identifiers; gene identifiers, symbols and row order
are retained.

The large `whitelist.txt` is the list of observed, already corrected barcodes;
it is not an independent technology on-list or a cell-calling result. The
capture lists, one barcode-projection pair, and alternating cluster groups
are explicit deterministic user parameters. The cluster groups have no
biological interpretation. Their precise construction is recorded with each
file in the provenance JSON.

## What validation establishes

The native original-source program computes all scientific results at test
time. This fixture does not contain hand-written or Python/JavaScript-derived
bustools result goldens. Validation compares complete outputs across native,
patched-native and WebAssembly execution, checks format invariants, and
requires declared nonempty outputs and processing effects. The executable
case plan and its narrowly scoped handling of unordered container iteration
are described in `validation/bustools/README.md`.

BUSZ does not preserve the unused BUS padding field. Native and WebAssembly
compression/decompression outputs are compared to the unchanged-native
behavior, and decoded data must preserve the tested complete gene/UMI count
results. The larger source contains nonzero padding; lossless restoration of
those unused bytes is not claimed. The supplied standard BUS records do not
encode the positional intervals expected by the experimental `merge` command;
its empty-output boundary case is not scientific validation of interval
merging. Unseeded random downsampling is not claimed to be reproducible
between independent executions.

## Verification and regeneration

From the repository root:

```sh
python3 scripts/fetch-bustools-test-data.py --verify
python3 scripts/fetch-bustools-test-data.py --fetch
python3 scripts/fetch-bustools-test-data.py --regenerate
```

Verification is offline. Fetching accepts only the pinned original byte
lengths and SHA-256 hashes. Regeneration compares a fresh generated tree to
the frozen manifest and never updates expected identities. Replacing the
fixtures and manifest requires the explicit maintenance command
`--regenerate --update-manifest`; CI does not use that option. Python 3.11 or
newer is required. No biological sequences or output goldens are synthesized.
