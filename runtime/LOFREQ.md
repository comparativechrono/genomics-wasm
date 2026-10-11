# LoFreq somatic workflow

`LoFreqRunner` runs the compiled LoFreq core in Node and browser module workers.
It additionally implements the serial `somatic` workflow from LoFreq 2.1.5,
commit `8fe42b04dcd9775fb618d8004649421c0632b35c`. Import it independently:

```js
import { LoFreqRunner } from './runtime/lofreq.mjs';

const runner = new LoFreqRunner({
  moduleUrl: new URL('./dist/lofreq/lofreq.mjs', import.meta.url),
});
const result = await runner.run([
  'somatic', '-n', 'normal.bam', '-t', 'tumor.bam',
  '-f', 'reference.fa', '-o', 'sample_',
], {
  files: [
    { path: 'normal.bam', data: normalBam },
    { path: 'normal.bam.bai', data: normalIndex },
    { path: 'tumor.bam', data: tumorBam },
    { path: 'tumor.bam.bai', data: tumorIndex },
    { path: 'reference.fa', data: referenceFasta },
    { path: 'reference.fa.fai', data: referenceIndex },
  ],
  outputs: [
    'sample_somatic_final.snvs.vcf.gz',
    'sample_somatic_final.snvs.vcf.gz.tbi',
    'sample_somatic_final.indels.vcf.gz',
    'sample_somatic_final.indels.vcf.gz.tbi',
  ],
  signal: abortController.signal,
  timeoutMs: 300_000,
});
```

The constructor accepts the same `moduleUrl`, optional `workerUrl` and
`waitForExit` options as `ToolRunner`; this nonthreaded build uses the default
`waitForExit: false`. Its default module URL is `../dist/lofreq/lofreq.mjs`
relative to `runtime/lofreq.mjs`. SharedArrayBuffer and cross-origin isolation
are not required. The JavaScript coordinator runs on its caller's thread;
every compute stage runs in a new disposable worker with a fresh WASM heap.

Commands other than `somatic` delegate directly to `ToolRunner`. For `somatic`,
the translation only constructs commands, manages files and reads the native
caller's reported test counts. Actual calls, multiple-testing correction,
strand filtering, VCF set operations and uniqueness testing execute in the
upstream C implementations. It does not parse or calculate variant records.

The sequence is normal relaxed calling, separate stringent normal SNV and
indel filtering, tumor relaxed calling, separate stringent tumor filtering,
normal complements, and uniqueness checks against the normal BAM. `--dbsnp`
adds two final complements and `--germline` adds the upstream intersections.
The latter are upstream's ad-hoc germline output: they use relaxed normal
record metadata with no further filtering. SNV and indel output files are
always produced, even when `--call-indels` is omitted. Calling indels requires
the BAM preparation described by upstream, including insertion/deletion
qualities; use the actual core `indelqual` and `index` commands as appropriate.

File names, option ordering, defaults and the literal-space splitting of
`--call-rlx-extra-args` follow the pinned Python script. `%f` arguments use exact
binary64, ties-to-even formatting, including values for which JavaScript
`toFixed(6)` would differ. No shell interpretation occurs. Arguments are passed
as an array, so spaces inside paths are retained.

Every stage's VCF and generated `.tbi` are kept in the run's private virtual
filesystem. Auto-generated reference `.fai`/`.gzi` files and the two relaxed
call logs are retained too. Each log follows upstream's command line and
`stderr: `/`stdout: ` line format. Result `stdout` and `stderr` aggregate the
core command diagnostics without Python logger timestamps; verbose mode also
reports each command. Diagnostics are available after a stage finishes.

The ordinary `files`, `outputs`, `outputDirectories`, `signal` and `timeoutMs`
contract applies. All paths are inside `/work`; nested directories exist when
created by supplied input files. The output prefix does not create directories.
Request intermediate files explicitly, or use an existing output directory
with `outputDirectories`. The same output selection applies to success and
ordinary command failure. Inputs and outputs never persist between runs.
Cancellation terminates the current core worker and rejects with `AbortError`;
the single timeout covers the whole workflow and rejects with `TimeoutError`.
These rejected runs do not return partial results. A core command's nonzero
status stops the workflow and returns exit code 1 with available requested
files; its original status remains in the stage ledger. Runtime traps reject.

In addition to the usual result, `result.workflow` records:

```js
{
  upstream: { version: '2.1.5', commit: '8fe42b04dcd9775fb618d8004649421c0632b35c' },
  stages: [{
    id: 'normal_relaxed',
    args: ['call', /* complete native arguments, without argv[0] */],
    exitCode: 0,
    outputs: [/* created or changed absolute workspace paths */],
    missingOutputs: [/* absent required stage outputs */],
  }],
  generatedFiles: [/* all created or changed workspace paths */],
}
```

Initial implementation limits and explicit differences:

- Only `--threads 1` is supported; Python subprocess `call-parallel` is not
  available. `--continue` is an unimplemented workflow feature. Both return a
  nonzero usage error. Fresh execution is supported.
- Nonfinite numeric parameters and integers outside JavaScript's exact integer
  range are rejected. This avoids losing numeric arguments in the adapter.
- BAM index absence returns failure. The upstream Python script mistakenly
  returns the `ValueError` class instead of raising it, then exits successfully.
  Validation must record this specific error-status difference explicitly.
- A zero-exit core stage without its required VCF is a workflow failure; it
  cannot be mistaken for an empty successful callset.
- The help and outer diagnostic text describe the browser implementation;
  Python logging timestamps and hostname/debug-object dumps are not reproduced.
- Compressed VCFs, indices and native BAM/reference parsing remain the native
  core's responsibility. Files must fit the available memory; each stage
  copies its input files to a new worker.

Correctness validation compares this workflow against the **unchanged pinned
Python script driving the unchanged native core**, with patched-native as a
separate comparison. All intermediate and final VCF records, significant
header fields, native command arguments and stage counts must agree under the
repository's documented comparison rules. Orchestration unit tests do not
establish numerical equivalence.

The orchestration translation derives from
[`src/scripts/lofreq2_somatic.py`](https://github.com/CSB5/lofreq/blob/8fe42b04dcd9775fb618d8004649421c0632b35c/src/scripts/lofreq2_somatic.py),
by Andreas Wilm, Copyright 2013,2014 Genome Institute of Singapore, distributed
under the MIT License. See the LoFreq distribution's license and source archive
for the original script and the notices of its linked dependencies.
