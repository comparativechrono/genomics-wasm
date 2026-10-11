# Strelka2 runtime

`Strelka2Runner` runs the Strelka2 2.9.10 somatic workflow in Node or a browser.
Calling, chromosome-depth estimation, empirical scoring, BGZF compression,
tabix indexing, and run-statistics aggregation execute the actual compiled
upstream programs. JavaScript coordinates their files and commands serially.
There are no subprocesses, remote computation, or canned variant results.
The supplied modules do not require SharedArrayBuffer or cross-origin isolation.

```js
import { Strelka2Runner } from './runtime/strelka2.mjs';

const runner = new Strelka2Runner({
  baseUrl: new URL('./dist/strelka2/', import.meta.url),
});
const result = await runner.run([
  'somatic',
  '--normalBam', 'normal.bam',
  '--tumorBam', 'tumor.bam',
  '--referenceFasta', 'reference.fa',
  '--runDir', 'strelka',
], {
  files: [
    { path: 'normal.bam', data: normalBamBytes },
    { path: 'normal.bam.bai', data: normalIndexBytes },
    { path: 'tumor.bam', data: tumorBamBytes },
    { path: 'tumor.bam.bai', data: tumorIndexBytes },
    { path: 'reference.fa', data: referenceBytes },
    { path: 'reference.fa.fai', data: referenceIndexBytes },
  ],
  outputDirectories: ['strelka/results'],
  timeoutMs: 300000,
  signal: abortController.signal,
});
```

`baseUrl` identifies a directory and must end in `/`. An optional `workerUrl`
has the same meaning as in `ToolRunner`. Default bundled scoring models are
loaded from `config/somaticSNVScoringModels.json` and
`config/somaticIndelScoringModels.json` under `baseUrl`; these files are required
even when scoring is disabled. A static HTTP server must serve the complete
`dist/strelka2` and `runtime` directories. Node also accepts a `file:` base URL.
Default assets and all computation stay local after loading; reference files
and alignment indexes must be supplied explicitly.

Input bytes are snapshotted at the start of each run. File paths are relative
to `/work`, or absolute paths below `/work`. The same path restrictions and
output-selection rules as `ToolRunner` apply. Strings, `Uint8Array`, and
`ArrayBuffer` are accepted as file data. Returned data are byte arrays.
Each compiled stage uses a new worker and module instance. The coordinator
retains only the workflow's current files between stages.

The default command estimates chromosome depth from the normal sample (or
tumor sample when no normal is supplied), applies the bundled empirical scoring
models, and writes:

- `strelka/results/variants/somatic.snvs.vcf.gz` and its `.tbi` index
- `strelka/results/variants/somatic.indels.vcf.gz` and its `.tbi` index
- `strelka/results/stats/runStats.xml` and `runStats.tsv`

`--exome` or its upstream alias `--targeted` disables the chromosome high-depth
filter. `--outputCallableRegions` adds
`strelka/results/regions/somatic.callable.regions.bed.gz` and its `.tbi` index.
Output prefixes change with `--runDir`, whose default is
`StrelkaSomaticWorkflow`. As upstream, normal input is optional and tumor input
is mandatory; at most one alignment per sample is accepted. BAM/CRAM headers
must describe exactly the reference chromosome names and lengths, and sample
headers must use the same chromosome order. BAM `.bai`/`.csi` and CRAM `.crai`
index names follow upstream checks. CRAM use requires locally available
reference sequence; remote reference resolution is not provided.

Supported configure options include repeatable `--region`, `--indelCandidates`,
`--forcedGT`, and `--noiseVcf`; `--callRegions`; `--scanSizeMb`;
`--disableEVS`; `--reportEVSFeatures`; both scoring-model overrides;
`--outputCallableRegions`; and `--retainTempFiles`. Indexed variant inputs and
call-region BED inputs require their `.tbi` files. These are consumed by the
upstream core, so candidate VCFs can be produced by other tools without adding
those tools to this runtime. Region coordinates remain one-based and inclusive,
as in the native configure command.

`--config FILE` accepts the `[StrelkaSomatic]` biological parameters from the
upstream installed INI: `maxIndelSize`, `depthFilterMultiple`,
`snvMaxFilteredBasecallFrac`, `snvMaxSpanningDeletionFrac`,
`indelMaxWindowFilteredBasecallFrac`, `ssnvPrior`, `sindelPrior`, `ssnvNoise`,
`sindelNoiseFactor`, `ssnvNoiseStrandBiasFrac`, `minTier1Mapq`, `minTier2Mapq`,
`ssnvQuality_LowerBound`, `sindelQuality_LowerBound`, `ssnvContamTolerance`, and
`indelContamTolerance`. It also accepts `indelErrorModelName` and
`extraVariantCallerArguments`. Extra arguments split on whitespace, matching
upstream Python rather than shell quoting. Numeric strings retain their exact
spelling. Recognized keys in `[DEFAULT]` are inherited by `[StrelkaSomatic]`,
whose explicit values take precedence, matching upstream ConfigParser.
Unsupported configuration keys and INI interpolation fail explicitly.
Use the dedicated CLI options for model paths and workflow switches.

`--callMemMb` is accepted as the native scheduler's memory hint and reported
in diagnostics; it does not impose a browser memory limit. The compiled WASM
memory maximum and host browser still bound each stage. Whole-genome datasets
may exceed practical browser memory: inputs, staged files, and compiled memory
can coexist, and the ordinary runner copies inputs to each fresh worker.

Workflow resume and optional realigned-BAM output (`isWriteRealignedBam=1`)
are not implemented. They fail explicitly rather than skipping their required
tasks. This adapter implements the somatic workflow only. It does not expose
the native scheduler's cluster/job/mail options or the separate germline
workflow. A run directory supplied among existing inputs is rejected.

Direct commands select the compiled helper by their first argument:

```js
await runner.run(['strelka2', '--help']);
await runner.run(['bgzip', 'calls.vcf'], {
  files: [{ path: 'calls.vcf', data: vcfBytes }],
  outputs: ['calls.vcf.gz'],
});
await runner.run(['tabix', '-p', 'vcf', 'calls.vcf.gz'], {
  files: [{ path: 'calls.vcf.gz', data: bgzfBytes }],
  outputs: ['calls.vcf.gz.tbi'],
});
```

Available executable names are `strelka2`, `GetChromDepth`, `MergeRunStats`,
`bgzip`, `bgzf_cat`, `tabix`, and `htsfile`. Unprefixed arguments go directly to
`strelka2`. The native core prints its version in `--help`; it does not implement
`--version`. High-level `['somatic', '--help']` and
`['somatic', '--version']` describe the configured workflow without loading a
compiled module.

## Translation and validation boundary

The coordinator translates command construction and file operations from
`src/python/lib/strelkaSomaticWorkflow.py`, `strelkaSharedWorkflow.py`,
`sharedWorkflow.py`, `workflowUtil.py`, the configure option modules, and
`src/python/libexec/{cat,mergeChromDepth,vcfCmdlineSwapper}.py` in release 2.9.10.
It preserves default parameter strings, chromosome grouping, balanced region
partitioning, sample selection for depth, first-segment VCF command-header
replacement, BGZF concatenation order, single-segment moves, tabix presets,
statistics merging, and temporary-file cleanup. Somatic Strelka2 does not enable
the germline workflow's dynamic sequencing-error estimation; no such stage is
added or omitted here.

The depth-file merge adds binary64 depths and formats three decimal places
using exact IEEE-754 rational ties-to-even rounding, matching Python `%f`.
Merged depth rows use input chromosome order; Python 2's unspecified dictionary
iteration order may differ. Validation must compare those rows by chromosome
without changing their values. The VCF `##cmdline=` metadata names the upstream
configure entrypoint and the user's high-level arguments. Only executable and
workspace path differences may be normalized when comparing that metadata.
VCF alleles, scores, filters, genotypes, and all other scientific fields remain
unmodified.

Each somatic result adds `workflow.upstream`, `workflow.models`,
`workflow.generatedFiles`, and `workflow.stages`. A stage records its `id`,
`kind` (`native` or `translated`), `tool`, `args` without argv[0], `exitCode`,
output paths, missing outputs, SHA-256 `inputs`, and SHA-256 `outputHashes`.
Native configure checks have `phase: 'configure'`; calling and helper stages
have `phase: 'workflow'`. Input hashes describe the actual mounted workflow
files, including both model dependencies. Model hashes remain available as
`workflow.models`. Directory transport markers are private implementation
details and never appear in returned files or file hashes.

A failed stage stops the workflow, preserves collected partial files, returns a
nonzero exit code, and records the failure. A nominally successful stage that
omits any promised output also fails the workflow. Usage/configuration errors
return 2; workflow failures return 1. Abort, timeout, and WASM runtime traps
reject the promise instead of returning a success-like object. A single
deadline and cancellation signal cover model loading, all compiled stages, and
interstitial JavaScript work. Later calls always start with fresh state.

Validation uses the unchanged upstream Python 2 configure/run workflow and
unchanged native executables as the oracle, followed by patched-native and
WASM runs on identical real biological inputs. It checks actual native stage
commands, default models, retained intermediate files, final VCFs/indexes, and
statistics. `--retainTempFiles` retains upstream intermediate directories;
upstream compression still removes its plain inputs, and single-segment final
assembly still moves the compressed segment into its final path. Unit tests
exercise orchestration failures and exact formatting independently of that
biological equivalence gate.

## License

The pinned release is commit
`1c8f1be330dc6f58568c82468a9da1c5f32fb54a`, source archive SHA-256
`45e78efec6e5272697f1d0a95851c7ae0d623dc8f93846e11fe37f15da9f1e30`.
The upstream Python workflow is Copyright (c) 2009–2018 Illumina, Inc., under
GNU GPL version 3 or later. The derived `strelka2-somatic.mjs` translation is
also GPL-3.0-or-later. The repository's general MIT license does not replace
that license. Distribution includes the upstream license, complete pinned
source, port changes, bundled models, and dependency notices.
