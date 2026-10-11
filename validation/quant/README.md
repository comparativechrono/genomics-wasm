# Transcript quantification tests

Run `node scripts/validate-quant.mjs --tool kallisto --engine node` or substitute
`salmon`, `chromium`, `firefox` or `webkit`. The programs must first be built in
`build/native/`, `build/patched-native/` and `dist/`. Browser tests use Playwright
and a local server with cross-origin isolation headers. These headers support
real WebAssembly threads where a tool requires them.

Each numerical case runs the unchanged upstream program, the patched program
compiled for the host, and the WebAssembly build with the same options and input
files. The suite builds indexes itself. It does not substitute prerecorded
results. Reports, command diagnostics and output files are retained in
`reports/quant/<engine>/`; summary filenames identify the selected tool and data.
The summary records the native binaries, JavaScript, WebAssembly, runtime and
input SHA-256 hashes.

## Data and cases

The human and yeast inputs are small, real RNA-seq subsets with 256 read pairs
each. Their sources, selection and transcript reconstruction are documented in
`datasets/rna-quant/README.md`. The human reference contains five complete
transcripts, including overlapping DYNC1H1 isoforms; the yeast reference contains
124 transcripts. The suite verifies the source manifest before running and
independently checks FASTQ records and mate identities.

Cases cover index construction at k=31 and k=21, paired and single reads,
explicit single-read fragment distributions, a changed fragment distribution,
both strand settings, and two fresh-worker repeats. Kallisto also tests sequence
bias correction; Salmon tests automatic library detection, EM alongside its default optimizer and combined
sequence, GC and position bias correction. The Salmon bias case requires the
reported model shapes to be populated and the native abundance table to change.
Missing files
and an invalid fragment-length parameter must fail with the same exit status as
the native program. Both tools also test a real FASTQ record whose quality line
has deliberately been truncated. A separate port-restriction case requires a
clear error for more than one compute worker; this is intentionally distinct
from upstream native behavior, which permits more workers.

Kallisto index interchange is required in both directions: native quantification
reads the WebAssembly index and WebAssembly quantification reads the native
index. Its build preserves the upstream 64-bit C++ data layout before lowering
to a regular wasm32 module. Salmon's epserde index records platform integer
width, so 64-bit native and wasm32 builds use independently constructed indexes;
cross-platform index interchange is not claimed.

The initial Kallisto native checks mapped 256 human fragments, including 26 with
ambiguous transcript assignments, and 244 yeast fragments, including 14 with
ambiguous assignments. Both references produce fractional abundance estimates.
Changing strand, fragment-length and sequence-bias options changes the native
outputs, so these cases exercise quantification rather than just option parsing.
Salmon's yeast reverse-strand case retains the same rounded abundance table as
unstranded quantification but changes the equivalence-class count from 17 to 14.
The suite requires an actual change in estimates or mapping counts for every
option-change case; a changed option string alone is insufficient.
The small subsets test implementation fidelity. They do not establish accuracy
of abundance estimation or bias models on a complete biological experiment.
Bootstrap, gene aggregation, decoy references and whole-transcriptome scale are
outside the current validation scope.

An exploratory Kallisto run with two bootstraps and seed 42 produced identical
primary abundance tables but different bootstrap tables between native and
WebAssembly builds on both datasets. Seeded bootstrap output is therefore not
claimed to reproduce across these builds.

## Comparison rules

`compare.py` checks every transcript in `abundance.tsv` or `quant.sf`:

- Identifiers must be unique and the complete identifier/length set must match
  the independent input FASTA. Row order can differ and is reported.
- Effective lengths, estimated counts and TPM must be finite and nonnegative;
  effective lengths must be positive. No low-abundance rows are discarded.
- Zero and nonzero states must match, even if a numerical allowance is present.
- Input-fragment accounting and all integer metadata are exact. The reported
  processed count must equal the FASTQ count; mapped and unique counts cannot
  exceed the applicable totals.
- All metadata keys and values are compared, including unknown additional
  fields. Only explicitly named wall-clock times, command paths and peak memory
  measurements are excluded. Tool versions are pinned and validated.
- Salmon's complete `lib_format_counts.json` is also required and compared,
  including compatible/incompatible fragment counts, every strand histogram bin
  and strand ratios. Its assigned count must agree with `meta_info.json`.
  Read-file provenance is retained and compared without path rewriting.

The default numerical policy is exact. `tolerances.json` contains no allowances
unless a measured platform difference has been investigated and documented.
Allowances can apply only to named floating-point columns, never identifiers,
lengths, input counts or convergence flags. Each allowance requires its reason
and evidence. Reports retain the largest absolute and relative difference and
the number of changed values, including differences within an allowance.
Decimal parsing preserves tiny nonzero values rather than rounding them to zero.

An unsuccessful command is never evaluated as successful quantification.
Kallisto 0.52.0 writes a zero-count table with NaN TPM before returning exit 1 when
a malformed FASTQ contains no readable records. The negative case checks the
nonzero exit status and zero processed count, retains the partial output, and
does not admit NaN to the successful-result comparator.

`tests/test_quant_compare.py` checks counterexamples including missing or
duplicate transcripts, incorrect lengths, NaN, altered read accounting, changed
metadata, tiny nonzero values, and attempts to apply tolerance to exact fields.
