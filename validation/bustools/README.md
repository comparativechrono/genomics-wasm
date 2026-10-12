# bustools equivalence validation

`node scripts/validate-bustools.mjs --engine node --tool all` executes each
planned case against the unchanged upstream native program, the native program
with the portability patch, and the original-source WebAssembly program.
Replace `node` with `chromium`, `firefox`, or `webkit` for the same comparison
through a real browser worker on ordinary hosting without cross-origin
isolation. The compiler, source, native executables, WASM assets, runtime,
validator, case plan, and experimental input files are all identified by
SHA-256 in the report.

The inputs are provenance-locked experimental single-cell RNA sequencing BUS
records, mappings, and matching reads under `datasets/bustools`. They are not
outputs manufactured by a JavaScript or Python replacement for bustools.
`cases.json` is the executable source of truth for required workflows,
artifacts, effects, structural invariants, and cross-target dependencies.
Biological calculations are performed only by the original C++ program.

The comparator retains every declared output byte except two explicitly named
unordered-map iteration differences observed between native libstdc++ and
WASM libc++. There is no numerical tolerance. BUS comparison includes the
complete header and all six fields of each 32-byte record. Matrix comparison
includes the header, dimensions, every coordinate and printed count, and the
complete barcode and gene/EC identities. Matrix dimensions must match their
identity files. These structural checks prevent two equally empty or malformed
outputs from passing.

Only `project-transcripts/projected.bus` permits record permutations within a
consecutive barcode/UMI group. Its complete header, group boundaries and order,
all six record fields, and every duplicate remain exact. Only
`count-transcripts/counts.ec.txt` permits different assignment of unused EC
column identities: every matrix-referenced EC ID and transcript list remains
exact, and the full multiset of unused transcript lists, including duplicates,
must agree. Its complete matrix and ordered barcode list remain byte-exact.
The observed experimental case differed only in two records within one BUS
group and 7,150 unused EC assignments. All gene-count modes, predictions,
other outputs and numeric precision compared byte for byte. Per-artifact
canonical hashes accompany the original raw hashes and sizes.

Upstream BUSZ does not encode the unused BUS padding field. The experimental
input contains nonzero padding, so decompression is checked byte for byte
against actual unchanged-native decompression, rather than falsely claiming
that upstream restores those padding bytes. Native/WASM interchange cases
then count the decompressed records and require the complete gene/UMI matrix,
barcodes and genes to match the original uncompressed calculation exactly.

The experimental `merge` command expects interval-encoded BUS records with
start/end positions in the padding field. The supplied real standard BUS
input does not carry such intervals. `merge-no-intervals` checks the original
program's empty-output boundary behavior and full EC mapping; it does not
claim scientific validation of interval merging. `mash` exercises the same
real read-indexed input twice as an explicit repeated-input operation, without
inventing additional read alignments.

Cases declaring `effect` must change the selected complete native biological
outputs from their baseline; `equivalentTo` cases must preserve them. Repeat
cases compare a new module invocation with an earlier unchanged native result.
Dependency records identify the source execution, original and mounted input
hashes, and byte lengths. Cross-target cases consume actual native-produced
files in WASM and actual WASM-produced files in native code.

Explicit stdin cases send the same bytes to native stdin and the shared WASM
stdin API. Binary stdout is captured as bytes and compared as a BUS artifact.
Every invocation starts in a fresh workspace. Source inputs and requested
outputs have explicit names; missing outputs and nonzero successful-case exit
statuses fail validation.

Upstream random downsampling has no user-settable seed. Deterministic boundary
behavior can be compared exactly, but this suite does not claim reproducible
stochastic draws across independent executions. User-supplied barcode capture,
mapping, and cluster labels are documented fixture parameters, not inferred
cell classifications.

Each run writes raw commands, stdout, stderr, artifacts, and comparison reports
under a new UUID directory. Only completed summaries are atomically published
to `reports/bustools/<engine>/summary-bustools-all.json`. The summaries bind the
comparator's raw hashes and byte lengths to the bytes captured at execution,
and recheck all build, runtime, validator, and dataset identities at the end.
The release packager requires complete successful reports from all four
engines before producing a standalone package.

`tests/test_bustools_compare.py` deliberately mutates small format-only unit
samples to demonstrate rejection of changed UMI counts, barcodes, ECs, BUS
headers, record order, truncated records, matrix counts, coordinates, labels,
dimensions, duplicates, and invalid paths. These parser tests are distinct
from the experimental inputs used for executable equivalence validation.
