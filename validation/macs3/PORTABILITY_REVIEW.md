# MACS3 portability review

Reviewed on 2026-10-13 against the checksum-pinned MACS3 3.0.5 source,
hmmlearn 0.3.3 source and the implementation in this repository. This is a
source, dependency and comparison-method review. Passing scientific runs are
recorded separately in `reports/macs3/<engine>/summary-all.json`; this document
does not substitute for those reports.

## Scientific behavior

| Area | Finding and implementation |
| --- | --- |
| CLI coverage | The upstream executable dispatches 14 commands: `callpeak`, `bdgpeakcall`, `bdgbroadcall`, `bdgcmp`, `bdgopt`, `cmbreps`, `randsample`, `filterdup`, `bdgdiff`, `refinepeak`, `predictd`, `pileup`, `hmmratac`, and `callvar`. The case plan exercises computation, including the newer `pileup_v2_cmd` implementation. |
| Variant calling | Both upstream process-pool calls become ordered serial `map` calls. The requested partition count, boundaries, per-position calling function and result order are retained. Neither allele likelihoods nor assembly/scoring formulas are replaced. The native comparison launcher uses real `spawn` processes; it does not replace the unpatched upstream pool. |
| BAM virtual offsets | Upstream `cython.ulong` is 32 bits on wasm32. The original port overflowed before shifting a BAI virtual offset, and upstream's broad exception handler silently produced a header-only VCF. The complete BAM offset path now uses explicit `uint64_t`. The authentic fixture contains seven chunk offsets above the unsigned-32-bit range, with maximum 5,630,263,296. Corrected assembly-off/on/auto executions produced 22/15/16 variants, respectively, with variant lines byte-identical to native. |
| Aggregate integer widths | `integer-width.patch` preserves LP64 widths for six parser fragment-length accumulators, three peak-normalization totals, six peak-caller q-value lengths/ranks/totals, two score-track q-value accumulators, one bedGraph total, and the weighted FRAG track count. These values can exceed 32 bits with small arrays. Ordinary coordinates, pointer-sized indices and upstream-native 32-bit fields are retained. The case plan includes compact, explicitly artificial inputs totaling five billion bases. |
| Assembly and SIMD | Upstream fermi-lite already selects one thread for MACS3 assembly. Its SIMDe implementation is retained. Renaming the unused `swalign.c` test-program entrypoint avoids a side-module linker export collision without changing alignment routines. |
| Summit smoothing | The Savitzky–Golay derivative coefficient buffer is explicitly created as NumPy `i8`. Its Cython annotation now uses `cnp.int64_t` rather than platform `long`, preserving the existing values and fitting calculation. The other smoothing buffer uses platform-default NumPy integers and retains its corresponding platform type. |
| HMM arithmetic | The hmmlearn patch omits floating-point exception-flag inspection when `FE_DIVBYZERO` is unavailable. `log(0)` remains negative infinity. Gaussian/Poisson fitting and posterior prediction are retained. Upstream logs mention Viterbi, but the actual implementation calls `predict_proba`. |
| HMM comparison | The comparator checks all model fields: starting probabilities, transitions, Gaussian means/covariances or Poisson rates, feature count, state roles, bin size and model type. Saved diagonal covariances have shape 3 by 4; full covariances have shape 3 by 4 by 4. Full covariance validation permits upstream-compatible singular positive-semidefinite matrices and rejects indefinite matrices. |
| Exact decisions | Genomic coordinates, row ordering, categorical states, genotype/sample fields and integer counts remain exact. Only declared numerical fields receive the checked-in score/HMM tolerances. Model reload uses each target's own previously generated model and records that input's checksum. |
| Random sampling | Single-end `FixWidthTrack.sample_percent` iterates an unordered chromosome set, so an explicit seed alone is not a cross-process reproducibility guarantee for multichromosome inputs. Paired-end/FRAG paths sort chromosomes. The portability patch does not silently alter this upstream behavior. |
| Subprocess use | The CLI-reachable process pools are replaced as above. Legacy `OutputWriter` shell-gzip functions are not called by these CLI paths; the CLI imports its R-script writer rather than executing R. |

The review identified and corrected an expected-file checksum binding error in
repeat comparisons: the expected bytes now come from the baseline execution.
HMM covariance checks were also strengthened without excluding singular models
accepted by upstream.

## Dependency and source audit

The actual 18 runtime wheels satisfy 26 active PEP 508 requirements for
CPython 3.14.2 on Emscripten/wasm32. This was checked with the real, checksum-
verified `packaging` wheel under `python -I -S`, using explicit target markers.
The audit found and resolved a dependency omitted from the Pyodide catalogue:
pandas requires `tzdata` on Emscripten. The closure now includes tzdata 2025.3,
as well as pandas/anndata and their dependencies.

Embedded-library inspection identified OpenBLAS **0.3.31** in SciPy and HDF5
**1.12.1** in h5py. Their versions agree with the scientific recipe release
`314-20260911`, commit `fc8587207ecbf0fb54305d5da25ab7ed70f126d3`.
The older OpenBLAS recipe in the Pyodide core source archive was not used as
the scientific wheel's version reference. Corresponding sources also include
CLAPACK/libf2c 3.2.1, Boost 1.84.0, zlib 1.3.1 and the recipe patches/settings.

The 457 scientific licence files and embedded notices were regenerated from
the pinned original archives/wheels and their output checksums verified.
The manifest preserves archive names, archive hashes and exact member paths.
The original Pyodide wheels remain intact, including NumPy's two development
archives containing WebAssembly objects. Desktop-native runtime objects are
rejected; native build wheels are retained only among rebuild-source inputs.

## Verification actually performed during this review

- `python -m unittest discover -s tests -p 'test_macs3_*.py'`: **56 passed**,
  including four tests using the real pinned PEP 508 parser.
- `node --test tests/macs3.test.mjs`: **7 passed**.
- Dependency audit against the actual built runtime wheels: **18 packages,
  26 active requirements, no missing or incompatible dependency**.
- Licence regeneration and checksum verification: **457 scientific notices**.
- Serial-pool patch application to the pinned upstream source: zero-fuzz
  dry-run succeeded; subsequent unused-entrypoint and BAM-width changes were
  reviewed. The separate integer-width patch also passed a zero-fuzz dry-run.
- The runtime test agent confirmed the original BAM failure with an actual
  virtual offset of 4,871,644,989 and confirmed corrected assembly-off/on/auto
  variant lines against native. This targeted check predates the final
  aggregate-width rebuild and does not replace the final comparison reports.

The implementation review covered the build script, runtime worker, scientific
comparator, release packager and GitHub Actions workflow. The release gate
requires complete passing Node, Chromium, Firefox and WebKit reports bound to
the same binaries, source inputs, native environments, fixtures and case plan.
The workflow additionally tests an independently extracted standalone ZIP.

Browser executables could not be launched in this constrained workspace.
An extraction audit compared all 666 regular files in the five downloaded
Playwright ZIPs with installed files by size and SHA-256; all matched. Chromium
and Firefox were intact. Fifteen WebKit library symlinks had been extracted as
text files and were restored to their archive-defined relative targets. An
ordinary Playwright WebKit launch then remained blocked by 21 missing host
libraries, including GTK4 and GStreamer. No sandbox restriction was bypassed.
This review therefore makes no browser-success claim. The browser jobs and
formal native/patched-native/WASM scientific comparisons remain required
release evidence. Small authentic fixtures do not establish whole-genome
throughput, memory capacity, every input-format edge case or universal seeded
reproducibility. Upstream prebuilt runtime/scientific wheels are redistributed
unchanged; their bit-for-bit reconstruction is not claimed.
