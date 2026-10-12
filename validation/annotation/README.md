# Prodigal and HMMER numerical validation

Run `node scripts/validate-annotation.mjs` after building both tools. Select
`--tool prodigal|hmmer|all`, `--dataset prodigal|hmmer|all`, and
`--engine node|chromium|firefox|webkit`. Each tool uses its own dataset. Browser
runs use ordinary HTTP hosting without cross-origin isolation, with the same
worker runners shipped in the standalone packages.

The case plan executes the original pinned native source, the patched native
source, and the WebAssembly module. Only repeated-run cases reuse the preceding
native oracle. Every planned case must pass, and all declared outputs are
compared. Nonzero exits must match the original exit status and the specified
upstream diagnostic. The native stdin case uses a seekable input redirection,
as `prodigal < genome.fna` does in a shell: original single-genome training
rewinds the input. The browser runner mounts exactly those stdin bytes for the
same training pass.

Prodigal cases cover single-genome and metagenomic prediction on unchanged
E. coli and Salmonella reference windows, mixed contigs, closed ends, translation
table 4, SCO and start-site scores, GFF coordinates and attributes, translated
proteins and nucleotide sequences, training-file creation and restoration,
bidirectional native/WebAssembly training-file exchange, stdin, repeated
execution, missing input and invalid translation tables. Alternate options must
change native biological output; an option that merely runs is insufficient.

HMMER cases cover all 13 distributed programs: `hmmbuild`, `hmmsearch`, `hmmscan`,
`hmmpress`, `hmmfetch`, `hmmalign`, `hmmconvert`, `hmmemit`, `hmmstat`, `phmmer`,
`jackhmmer`, `nhmmer`, and `nhmmscan`. Authentic tutorial alignments span globins,
protein kinases, fibronectin domains and DNA transposon profiles. The target set
contains both family matches and unrelated sequences. Cases require dozens of
matched globins, a kinase match and multiple fibronectin domains in Sevenless,
and both-strand DNA hits. Model construction, profile emission and searches use
explicit seed 42. Thresholds, consensus-column selection and strand selection
must have observed biological effects. Default and `--max` search paths are
both tested. A frozen upstream profile allows all four pressed database files
to be compared byte for byte; scanners then consume those actual files across
native/WebAssembly boundaries in both directions. Newly built ASCII profiles
are likewise exchanged in both directions. No canned result substitutes for an
executed command.

`compare.py` retains complete output text, record ordering, all sequences,
coordinates, scores, probabilities, model parameters and alignment annotations.
It normalizes only CRLF line endings and explicitly identified HMMER generation
dates, executable paths, current-directory metadata and performance measurements.
It does not remove arbitrary comments or apply numerical tolerance. HMMER table
comments are omitted only when deriving the separate biological-effect digest,
so changing command-line metadata cannot satisfy an option-effect requirement.
The complete-output semantic digest still includes those comments after the
narrow metadata normalizations. Binary outputs are exact.

Format checks require valid FASTA records, GFF coordinates and strands, HMMER
table columns and domain coordinates, rectangular Stockholm alignments, HMM
headers and terminators, and the biological coverage declared for each case.
Mutation tests verify that changes to biological fields, statistics, order,
metadata outside the allowlist, truncated files, invalid numeric values and
path traversal are rejected.

Reports in `reports/annotation/<engine>` retain every output, stdout/stderr,
exit status, hashes and byte sizes, dependency provenance, and complete
comparison evidence. Summary identities bind the exact frozen fixture manifest,
all fixture bytes, case plan, comparator, harness, runtime modules, build inputs,
WebAssembly assets and both native executables. Dependencies identify the
actual producer target and bytes consumed. Identities are checked again at the
end to reject files modified during a validation run. Package release gates
require independently generated Node, Chromium, Firefox and WebKit reports for
the exact shipped files.
