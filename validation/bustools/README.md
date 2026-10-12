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

The comparator retains every declared output byte. It does not sort records,
drop fields, round numbers, remove comments, or apply a floating-point
tolerance. BUS comparison includes the complete header, all 32-byte records,
barcode/UMI/EC identities, counts, flags, padding, and record order. Matrix
Market comparison includes the format header, dimensions, every coordinate
and printed count, and complete ordered barcode and gene/EC identity files.
The matrix dimensions must also match their identity files. These structural
checks prevent two equally empty or malformed outputs from passing.

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
