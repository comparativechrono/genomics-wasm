# Primer3 native equivalence validation

The case plan executes **37 cases** against the unchanged native Primer3 2.6.1
executables, a separately identified patched-native build (the port currently
needs no algorithm patches), and the actual WebAssembly programs. The same plan
runs in Node, Chromium, Firefox and WebKit. The original seven CLI programs are
covered: `primer3_core`, `oligotm`, `ntthal`, `ntdpal`, `amplicon3_core`,
`long_seq_tm_test` and `primer3_masker`.

The core suite designs primers on the complete versioned human HBB reference
transcript and checks internal oligos, target/included/excluded regions, explicit
thermodynamic tables, legacy scoring, GC/product-size constraints, artificial
mispriming libraries, multirecord input, settings files, named input/output files
and repeat invocation. Five unchanged upstream regression inputs contribute
102 additional Boulder records, including `check_primers`, overlapping/acceptable
regions, overhangs, secondary structure and annealing-temperature behavior.
Expected upstream error records are counted explicitly. Separate failing cases
verify invalid global settings and missing configuration tables and require
matching canonical exit codes and the intended diagnostic.

The ancillary programs exercise oligo melting temperatures under changed salt
conditions and legacy formulas; thermodynamic dimer/hairpin modes, explicit
configuration and interactive input; four sequence-alignment modes; amplicon
melting temperatures with/without DMSO; long-sequence melting; and actual hard
and soft masking through the upstream memory-mapped binary index reader. The
small artificial masker index and its counts are fully reproducible. A separate
masker stdin case gives native Primer3 a seekable regular-file descriptor
(equivalent to shell input redirection); the WASM adapter materializes the same
bytes as a named file because upstream masker seeks backward at FASTA headers.
The report explicitly identifies the native stdin transport.

```sh
python3 scripts/fetch-primer3-test-data.py --verify
node scripts/validate-primer3.mjs --engine node
node scripts/validate-primer3.mjs --engine chromium
node scripts/validate-primer3.mjs --engine firefox
node scripts/validate-primer3.mjs --engine webkit
python3 -m unittest discover -s tests -p test_primer3_compare.py
```

Every ordered Boulder IO field is compared, including primer sequences,
coordinates, lengths, ranking, product sizes, explanations and all printed
numerical metrics. Ancillary CLI output and alignment positions are exact.
There is no numerical tolerance; only CRLF line endings are normalized.
Nonempty records, returned counts, contiguous ranking, primer sequence indices
and valid coordinates are independently checked before comparison. Output
contracts require real primer pairs or scientific CLI results, including exact
known hard/soft-masked sequence outputs. Eighteen adversarial comparator tests
reject altered sequences, rankings, coordinates, missing fields, counts,
nonfinite values, scientific numbers and truncated records.

Each run captures every input and output hash/size, command, exit status, stdout,
stderr and comparison in an immutable run directory. Summaries bind the complete
source-built distribution, both native executable inventories, source files,
runtime, validator and dataset manifest. Files are rechecked after execution.
The release gate independently checks this recorded evidence and requires all
four engine summaries to match the frozen plan before packaging.

This establishes equivalence to the pinned implementation for these inputs. It
does not establish experimental primer performance, genome-wide specificity or
unbounded sequence/index capacity. Inputs and masker indices must fit the
WebAssembly memory and virtual filesystem. Browser demo tests additionally
exercise actual UI interactions and the extracted release package.
