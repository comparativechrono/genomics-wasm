# Cutadapt validation

Run the complete case plan with:

```sh
node scripts/validate-cutadapt.mjs --engine node --dataset all
node scripts/validate-cutadapt.mjs --engine chromium --dataset all
node scripts/validate-cutadapt.mjs --engine firefox --dataset all
node scripts/validate-cutadapt.mjs --engine webkit --dataset all
python3 -m unittest tests/test_cutadapt_compare.py
```

Each successful case runs the unmodified native Cutadapt, the native build with
the WebAssembly compatibility patches, and Cutadapt's Python and compiled Cython
code in a fresh Pyodide worker. Native and browser Python versions are pinned to
the same version. Browser tests use ordinary hosting without cross-origin
isolation. They run the requested browser engine; a missing browser or asset
fails the run. Repeated cases start new workers and compare their results with
the original native output.

`cases.json` is the required case plan, shared with the release gate. It covers
single and paired reads, gzip input and output, stdin/stdout, adapter positions
and linked adapters, wildcards, error and overlap settings, indels, quality and
fixed-length trimming, terminal Ns, length filters, and discarding reads with
or without adapters. Required option effects compare ordered read records with
a named native baseline and fail if the output does not change. Gzip and stream
cases must preserve the equivalent file-based result. Missing inputs, malformed
FASTQ, mismatched pairs and unsupported parallel execution must fail normally
with the planned diagnostic; native signals are never accepted.

The read fixtures are real human and yeast sequencing data. The yeast fixture
includes observed adapter readthrough and quality controls; no adapters were
added to the reads. Human qualities come from the source BAM and have a small
numeric range, so its quality-trimming case uses a threshold of 5. The anchored
5′ and linked-adapter cases deliberately use an observed biological prefix to
exercise matching rules. That prefix is not evidence of technical contamination.
The dataset manifest records the source selection and these limitations.

The comparator preserves full read identifiers, record order, pairing, sequence
and qualities. Both results must independently be valid FASTQ. Every output
read must be a matching sequence/quality slice of the corresponding source read,
with no invented reads or duplication. Input/output record and base totals must
agree with the JSON report, and retained plus filtered reads must account for
the input. All JSON report values, including version and command metadata,
remain exact. No floating-point tolerances are used. Only gzip wrapper metadata,
FASTQ line-ending syntax and JSON object-key order are irrelevant to comparison.
Gzip CRC errors still fail validation.

Reports are written to `reports/cutadapt/<engine>/summary-<dataset>.json` with
per-case native and WebAssembly outputs alongside them. A stream test records
stdout as a named FASTQ artifact whose hash must equal the captured stdout hash.
Summaries bind the complete case plan to the dataset, build, installed native
environments, runtime and validator hashes. Incomplete cases, errors, missing
outputs and changed assets fail validation. Passing these tests demonstrates
equivalence on the bundled fixtures and tested options; it does not establish
that any particular trimming setting is appropriate for an unknown experiment.
