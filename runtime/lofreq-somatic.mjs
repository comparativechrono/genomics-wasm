// Serial orchestration translated from LoFreq v2.1.5 src/scripts/lofreq2_somatic.py.
// Upstream: Andreas Wilm, Copyright 2013,2014 Genome Institute of Singapore.
// The upstream script and this translation are distributed under the MIT License.
// Calling, filtering, set operations and uniqueness statistics remain upstream C.
import { workspacePath } from './index.mjs';

const encoder = new TextEncoder();
const decoder = new TextDecoder('utf-8', { fatal: true });
const UPSTREAM = Object.freeze({ version: '2.1.5', commit: '8fe42b04dcd9775fb618d8004649421c0632b35c' });
const SUFFIXES = [
  'normal_relaxed.vcf.gz', 'normal_relaxed.log',
  'normal_stringent.snvs.vcf.gz', 'normal_stringent.indels.vcf.gz',
  'tumor_relaxed.vcf.gz', 'tumor_relaxed.log',
  'tumor_stringent.snvs.vcf.gz', 'tumor_stringent.indels.vcf.gz',
  'somatic_raw.snvs.vcf.gz', 'somatic_raw.indels.vcf.gz',
  'somatic_final.snvs.vcf.gz', 'somatic_final.indels.vcf.gz',
  'somatic_final_minus-dbsnp.snvs.vcf.gz', 'somatic_final_minus-dbsnp.indels.vcf.gz',
  'germline.snvs.vcf.gz', 'germline.indels.vcf.gz',
];

/** Internal workflow entrypoint; runCore must execute the compiled LoFreq core. */
export async function runSomatic(args, {
  files = [], outputs = [], outputDirectories = [], stdin = new Uint8Array(), signal, timeoutMs = 0,
} = {}, runCore) {
  const started = Date.now();
  if (!Array.isArray(args) || args.some(arg => typeof arg !== 'string' || arg.includes('\0'))) {
    throw new TypeError('args must be an array of strings without NUL characters');
  }
  if (!Array.isArray(files) || !Array.isArray(outputs) || !Array.isArray(outputDirectories)) {
    throw new TypeError('files, outputs and outputDirectories must be arrays');
  }
  if (!Number.isFinite(timeoutMs) || timeoutMs < 0) throw new TypeError('timeoutMs must be a non-negative number');
  if (signal?.aborted) throw namedError('AbortError', 'Command cancelled');
  const inputFiles = files.map(file => ({ path: workspacePath(file.path), data: bytes(file.data).slice() }));
  if (new Set(inputFiles.map(file => file.path)).size !== inputFiles.length) throw new TypeError('Input file paths must be unique');
  const requestedOutputs = [...new Set(outputs.map(workspacePath))];
  const requestedDirectories = [...new Set(outputDirectories.map(workspacePath))];
  // Like subprocess.TemporaryFile in upstream, the workflow does not pipe its
  // own stdin to child commands. Validate the ordinary runner input contract.
  bytes(stdin);
  const vfs = new Map(inputFiles.map(file => [file.path, file.data]));
  const generated = new Set();
  const stages = [];
  const stdout = [];
  const stderr = [];
  const controller = new AbortController();
  let abortReason;
  const cancel = () => {
    abortReason ??= namedError('AbortError', 'Command cancelled');
    controller.abort();
  };
  const timeout = () => {
    abortReason ??= namedError('TimeoutError', `Command exceeded ${timeoutMs} ms`);
    controller.abort();
  };
  signal?.addEventListener('abort', cancel, { once: true });
  if (signal?.aborted) cancel();
  const timer = timeoutMs ? setTimeout(timeout, Math.max(0, timeoutMs - (Date.now() - started))) : undefined;
  const check = () => {
    if (timeoutMs && Date.now() - started >= timeoutMs && !abortReason) timeout();
    if (abortReason) throw abortReason;
  };
  const diagnostic = message => stderr.push(encoder.encode(`${message}\n`));
  const store = (path, data) => {
    path = workspacePath(path);
    vfs.set(path, data);
    generated.add(path);
  };
  const exists = path => vfs.has(workspacePath(path));
  const directoryExists = path => path === '/work' || [...vfs.keys()].some(file => file.startsWith(`${path}/`));
  const finish = exitCode => {
    check();
    const returned = new Map();
    const missingOutputs = [];
    for (const path of requestedOutputs) {
      if (vfs.has(path)) returned.set(path, vfs.get(path));
      else missingOutputs.push(path);
    }
    for (const path of requestedDirectories) {
      if (vfs.has(path)) throw new Error(`Output directory is not a directory: ${path}`);
      if (!directoryExists(path)) missingOutputs.push(path);
      else for (const file of [...vfs.keys()].sort()) {
        if (file.startsWith(`${path}/`)) returned.set(file, vfs.get(file));
      }
    }
    check();
    return {
      exitCode, stdout: concatenate(stdout), stderr: concatenate(stderr),
      files: [...returned].map(([path, data]) => ({ path, data })), missingOutputs,
      workflow: { upstream: { ...UPSTREAM }, stages, generatedFiles: [...generated].sort() },
    };
  };
  try {
    check();
    let options;
    try { options = parseArguments(args); }
    catch (error) {
      if (!(error instanceof UsageError)) throw error;
      diagnostic(`lofreq somatic: error: ${error.message}`);
      return finish(2);
    }
    if (options.help) { stdout.push(encoder.encode(HELP)); return finish(0); }
    const o = options;
    const path = suffix => o.outprefix + suffix;
    // Match upstream input and overwrite checks. A virtual directory exists
    // when an input file supplies it; outputs do not create directories.
    try {
      for (const input of [o.normal, o.tumor, o.ref, o.bed, o.dbsnp].filter(Boolean)) {
        if (!exists(input)) throw new WorkflowError(`File ${input} does not exist`);
      }
      for (const suffix of SUFFIXES) {
        if (exists(path(suffix))) throw new WorkflowError(`Cowardly refusing to overwrite already existing file ${path(suffix)}`);
      }
      const firstOutput = workspacePath(path(SUFFIXES[0]));
      const outdir = firstOutput.slice(0, firstOutput.lastIndexOf('/'));
      if (!directoryExists(outdir)) throw new WorkflowError(`The directory part of the output prefix does not exist: ${outdir}`);
      for (const bam of [o.normal, o.tumor]) {
        const canonical = workspacePath(bam);
        const filename = canonical.slice(canonical.lastIndexOf('/') + 1);
        const dot = filename.lastIndexOf('.');
        const alternate = dot > 0 ? canonical.slice(0, canonical.length - filename.length + dot) + '.bai' : canonical + '.bai';
        if (!vfs.has(canonical + '.bai') && !vfs.has(alternate)) {
          // Upstream accidentally returns the ValueError *class* here and exits
          // successfully. This adapter reports the missing index as failure.
          throw new WorkflowError(`BAM file ${bam} is not indexed. Provide ${bam}.bai or its basename .bai index.`);
        }
      }
    } catch (error) {
      if (!(error instanceof WorkflowError) && !(error instanceof TypeError)) throw error;
      diagnostic(`lofreq somatic: ${error.message}`);
      return finish(1);
    }
    if (!o.dbsnp) diagnostic('WARNING: No dbsnp file given. Upstream recommends dbsnp when dealing with human data.');
    else if (!exists(o.dbsnp + '.tbi')) diagnostic('WARNING: dbsnp has no .tbi index; upstream recommends bgzip and tabix.');
    const run = async (id, command, output) => {
      check();
      if (o.verbose || o.debug) diagnostic(`Executing lofreq ${command.join(' ')}`);
      // VCF close creates a tabix index. Preserve it for the following native
      // commands; fai_load can also create a reference index on its first use.
      const captured = [...new Set([output, output + '.tbi', o.ref + '.fai', o.ref + '.gzi', ...requestedOutputs])];
      let result;
      try {
        result = await runCore(command, {
          files: [...vfs].map(([filePath, data]) => ({ path: filePath, data })),
          outputs: captured, outputDirectories: requestedDirectories,
          signal: controller.signal,
        });
      } catch (error) { check(); throw error; }
      check();
      stdout.push(result.stdout);
      stderr.push(result.stderr);
      const changed = [];
      for (const file of result.files) {
        const previous = vfs.get(file.path);
        if (!previous || !equalBytes(previous, file.data)) { store(file.path, file.data); changed.push(file.path); }
      }
      const missing = exists(output) ? [] : [workspacePath(output)];
      stages.push({ id, args: command.slice(), exitCode: result.exitCode, outputs: changed, missingOutputs: missing });
      if (result.exitCode !== 0) throw new WorkflowError(`Stage ${id} failed with exit code ${result.exitCode}`);
      if (missing.length) throw new WorkflowError(`Stage ${id} did not produce ${output}`);
      return result;
    };
    const relaxed = async sample => {
      const command = ['call', '-d', '101000', '-f', o.ref, '--verbose', '--no-default-filter', '-b', '1'];
      if (o.bed) command.push('-l', o.bed);
      if (o.callIndels) command.push('--call-indels');
      if (o.extraArgs) command.push(...o.extraArgs.replaceAll('@', '-').split(' '));
      if (sample === 'normal') command.push('-a', pythonFixed(o.normalAlpha), '--use-orphan', '-B', '-N', '-A', o.normal);
      else {
        command.push('-a', pythonFixed(o.tumorAlpha));
        if (o.useOrphan) command.push('--use-orphan');
        if (o.baqOff) command.push('-B');
        command.push('-C', String(o.minCov));
        if (!o.noSrcQual) {
          command.push('-s');
          if (o.ignVcf) command.push('-S', o.ignVcf === 'normal' ? `${path('normal_stringent.snvs.vcf.gz')},${path('normal_stringent.indels.vcf.gz')}` : o.ignVcf);
        }
        command.push(o.tumor);
      }
      const output = path(`${sample}_relaxed.vcf.gz`);
      command.push('-o', output);
      const result = await run(`${sample}_relaxed`, command, output);
      const err = decoder.decode(result.stderr);
      const out = decoder.decode(result.stdout);
      // readlines() retains line endings and prefixes each physical line.
      const prefixLines = (text, prefix) => (text.match(/[^\n]*\n|[^\n]+$/g) || []).map(line => prefix + line).join('');
      store(path(`${sample}_relaxed.log`), encoder.encode(`# lofreq ${command.join(' ')}\n${prefixLines(err, 'stderr: ')}${prefixLines(out, 'stdout: ')}`));
      stages.at(-1).outputs.push(workspacePath(path(`${sample}_relaxed.log`)));
      const counts = { snvs: '-1', indels: '-1' };
      for (const line of err.split('\n')) {
        let key;
        if (line.startsWith('Number of substitution tests performed')) key = 'snvs';
        else if (line.startsWith('Number of indel tests performed')) key = 'indels';
        if (key) {
          const value = line.split(':')[1]?.trim();
          if (!/^[+-]?\d+$/.test(value || '')) throw new WorkflowError(`Cannot parse ${key} test count in ${sample} call log`);
          counts[key] = String(BigInt(value));
        }
        if (counts.snvs !== '-1' && counts.indels !== '-1') break;
      }
      if (counts.snvs === '-1' && counts.indels === '-1') throw new WorkflowError(`Cannot parse number of tests in ${sample} call log`);
      return counts;
    };
    const stringent = async (sample, counts) => {
      for (const type of ['snvs', 'indels']) {
        const qual = type === 'snvs' ? 'snvqual' : 'indelqual';
        const mtc = sample === 'normal' ? 'fdr' : type === 'snvs' ? o.tumorMtc : o.indelTumorMtc;
        const alpha = sample === 'normal' ? 0.01 : type === 'snvs' ? o.tumorMtcAlpha : o.indelTumorMtcAlpha;
        const output = path(`${sample}_stringent.${type}.vcf.gz`);
        await run(`${sample}_stringent_${type}`, [
          'filter', '-i', path(`${sample}_relaxed.vcf.gz`), '--sb-mtc', 'fdr', '--sb-alpha', '0.001000',
          '--cov-max', '100000', '--cov-min', String(o.minCov), `--only-${type}`, `--${qual}-mtc`, mtc,
          `--${qual}-alpha`, pythonFixed(alpha), `--${qual}-ntests`, counts[type], '-o', output,
        ], output);
      }
    };
    try {
      for (const sample of ['normal', 'tumor']) await stringent(sample, await relaxed(sample));
      for (const type of ['snvs', 'indels']) {
        const output = path(`somatic_raw.${type}.vcf.gz`);
        await run(`somatic_raw_${type}`, [
          'vcfset', '-a', 'complement', '-2', path('normal_relaxed.vcf.gz'), '--add-info', 'SOMATIC',
          `--only-${type}`, ...(type === 'indels' ? ['--only-pos'] : []),
          '-1', path(`tumor_stringent.${type}.vcf.gz`), '-o', output,
        ], output);
      }
      for (const type of ['snvs', 'indels']) {
        const output = path(`somatic_final.${type}.vcf.gz`);
        await run(`somatic_final_${type}`, [
          'uniq', '--uni-freq', '0.5', '--is-somatic', '-v', path(`somatic_raw.${type}.vcf.gz`),
          '--uniq-mtc', 'fdr', '--uniq-alpha', type === 'snvs' ? '0.001' : '0.0001', '-o', output, o.normal,
        ], output);
      }
      if (o.dbsnp) for (const type of ['snvs', 'indels']) {
        const output = path(`somatic_final_minus-dbsnp.${type}.vcf.gz`);
        await run(`somatic_final_minus_dbsnp_${type}`, [
          'vcfset', '-a', 'complement', '-2', o.dbsnp, '-1', path(`somatic_final.${type}.vcf.gz`),
          ...(type === 'indels' ? ['--only-pos'] : []), `--only-${type}`, '-o', output,
        ], output);
      }
      if (o.germline) for (const type of ['snvs', 'indels']) {
        const output = path(`germline.${type}.vcf.gz`);
        await run(`germline_${type}`, [
          'vcfset', '-a', 'intersect', '-1', path('normal_relaxed.vcf.gz'),
          '-2', path(`tumor_stringent.${type}.vcf.gz`), '-o', output,
        ], output);
      }
    } catch (error) {
      check();
      if (!(error instanceof WorkflowError)) throw error;
      diagnostic(`lofreq somatic: ${error.message}`);
      return finish(1);
    }
    return finish(0);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', cancel);
  }
}

const DEFINITIONS = [
  ['help', ['-h', '--help'], 'bool'], ['verbose', ['-v', '--verbose'], 'bool'],
  ['normal', ['-n', '--normal'], 'string'], ['tumor', ['-t', '--tumor'], 'string'],
  ['outprefix', ['-o', '--outprefix'], 'string'], ['ref', ['-f', '--ref'], 'string'],
  ['bed', ['-l', '--bed'], 'string'], ['dbsnp', ['-d', '--dbsnp'], 'string'],
  ['threads', ['--threads'], 'int', 1], ['tumorMtc', ['--tumor-mtc'], 'mtc', 'bonf'],
  ['tumorMtcAlpha', ['--tumor-mtc-alpha'], 'float', 1], ['indelTumorMtc', ['--indel-tumor-mtc'], 'mtc', 'bonf'],
  ['indelTumorMtcAlpha', ['--indel-tumor-mtc-alpha'], 'float', 0.01],
  ['callIndels', ['--call-indels'], 'bool'], ['minCov', ['--min-cov'], 'int', 7],
  ['germline', ['--germline'], 'bool'], ['normalAlpha', ['--normal-alpha'], 'float', 0.1],
  ['tumorAlpha', ['--tumor-alpha'], 'float', 0.01], ['ignVcf', ['-S', '--ign-vcf'], 'string', 'normal'],
  ['useOrphan', ['--use-orphan'], 'bool'], ['baqOff', ['--baq-off'], 'bool'],
  ['extraArgs', ['--call-rlx-extra-args'], 'string'], ['noSrcQual', ['--no-src-qual'], 'bool'],
  ['debug', ['--debug'], 'bool'], ['continue', ['--continue'], 'bool'],
];

function parseArguments(args) {
  const options = Object.fromEntries(DEFINITIONS.map(([key, , type, value]) => [key, value ?? (type === 'bool' ? false : undefined)]));
  const flags = new Map(DEFINITIONS.flatMap(definition => definition[1].map(flag => [flag, definition])));
  const tokens = args.slice();
  for (let index = 0; index < tokens.length; index++) {
    let flag = tokens[index];
    let value;
    if (flag.startsWith('--') && flag.includes('=')) [flag, value] = [flag.slice(0, flag.indexOf('=')), flag.slice(flag.indexOf('=') + 1)];
    if (!flags.has(flag) && flag.startsWith('--')) {
      const matches = [...flags.keys()].filter(name => name.startsWith(flag));
      if (matches.length === 1) flag = matches[0];
      else if (matches.length > 1) throw new UsageError(`ambiguous option: ${flag}`);
    }
    // argparse accepts attached short values and short boolean clusters.
    if (!flags.has(flag) && /^-[^-].+/.test(flag) && flags.has(flag.slice(0, 2))) {
      const first = flag.slice(0, 2);
      if (flags.get(first)[2] === 'bool') tokens.splice(index + 1, 0, '-' + flag.slice(2));
      else value = flag.slice(2).replace(/^=/, '');
      flag = first;
    }
    const definition = flags.get(flag);
    if (!definition) throw new UsageError(`unrecognized argument: ${flag}`);
    const [key, , type] = definition;
    if (type === 'bool') {
      if (value !== undefined) throw new UsageError(`${flag} does not take a value`);
      options[key] = true;
      if (key === 'help') return options;
      continue;
    }
    if (value === undefined) {
      value = tokens[++index];
      if (value === undefined || (value.startsWith('-') && !/^-(?:\d|\.\d)/.test(value))) throw new UsageError(`${flag} requires a value`);
    }
    if (type === 'int') {
      if (!/^[+-]?\d+$/.test(value) || !Number.isSafeInteger(Number(value))) throw new UsageError(`${flag} requires an integer in the JavaScript safe integer range`);
      value = Number(value);
    } else if (type === 'float') {
      if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(value) || !Number.isFinite(Number(value))) throw new UsageError(`${flag} requires a finite number`);
      value = Number(value);
    } else if (type === 'mtc' && !['bonf', 'holm-bonf', 'fdr'].includes(value)) throw new UsageError(`${flag} must be bonf, holm-bonf, or fdr`);
    options[key] = value;
  }
  for (const key of ['normal', 'tumor', 'ref', 'outprefix']) if (!options[key]) throw new UsageError(`--${key} is required`);
  if (options.threads !== 1) throw new UsageError('This browser workflow supports --threads 1 only; subprocess call-parallel is unavailable.');
  if (options.continue) throw new UsageError('--continue is not supported by this initial browser workflow implementation.');
  return options;
}

// Python "%f" formats the exact binary64 value, rounding halfway cases to even.
// Number.toFixed instead rounds exact halfway values upward. Use integer rational
// arithmetic so expert significance parameters reach native C unchanged.
export function pythonFixed(value, digits = 6) {
  if (!Number.isFinite(value) || !Number.isInteger(digits) || digits < 0 || digits > 100) throw new TypeError('Invalid fixed-point value or precision');
  const negative = value < 0 || Object.is(value, -0);
  const buffer = new ArrayBuffer(8);
  const view = new DataView(buffer);
  view.setFloat64(0, Math.abs(value));
  const bits = view.getBigUint64(0);
  const exponentBits = Number((bits >> 52n) & 2047n);
  const mantissa = (bits & ((1n << 52n) - 1n)) + (exponentBits ? 1n << 52n : 0n);
  const exponent = exponentBits ? exponentBits - 1023 - 52 : -1074;
  let numerator = mantissa * 10n ** BigInt(digits);
  let denominator = 1n;
  if (exponent >= 0) numerator <<= BigInt(exponent);
  else denominator <<= BigInt(-exponent);
  let rounded = numerator / denominator;
  const remainder = numerator % denominator;
  if (remainder * 2n > denominator || (remainder * 2n === denominator && (rounded & 1n))) rounded++;
  const text = rounded.toString().padStart(digits + 1, '0');
  return `${negative ? '-' : ''}${digits ? `${text.slice(0, -digits)}.${text.slice(-digits)}` : text}`;
}

function bytes(value) {
  if (typeof value === 'string') return encoder.encode(value);
  if (value instanceof Uint8Array) return value;
  if (value instanceof ArrayBuffer) return new Uint8Array(value);
  throw new TypeError('File data and stdin must be a string, Uint8Array or ArrayBuffer');
}
function equalBytes(a, b) { return a.length === b.length && a.every((value, index) => value === b[index]); }
function concatenate(chunks) {
  const result = new Uint8Array(chunks.reduce((length, chunk) => length + chunk.length, 0));
  let offset = 0;
  for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.length; }
  return result;
}
function namedError(name, message) { const error = new Error(message); error.name = name; return error; }
class UsageError extends Error {}
class WorkflowError extends Error {}

const HELP = `usage: lofreq somatic -n NORMAL -t TUMOR -f REF -o OUTPREFIX [options]

Serial LoFreq 2.1.5 somatic workflow; statistics execute in the upstream C core.
  -n, --normal FILE       Indexed normal BAM
  -t, --tumor FILE        Indexed tumor BAM
  -f, --ref FILE          Reference FASTA
  -o, --outprefix PREFIX  Prefix for all generated VCFs and logs
  -l, --bed FILE          Restrict calls to BED intervals
  -d, --dbsnp FILE        Remove known variants in this indexed VCF
  --call-indels          Also call indels (requires prepared BAM quality tags)
  --min-cov INT          Minimum somatic coverage (default 7)
  --tumor-mtc METHOD     bonf, holm-bonf, or fdr (default bonf)
  --tumor-mtc-alpha N    Tumor SNV correction alpha (default 1)
  --indel-tumor-mtc METHOD       Default bonf
  --indel-tumor-mtc-alpha N      Default 0.01
  --germline             Also emit upstream's ad-hoc germline intersection
  -S, --ign-vcf FILE     Source-quality exclusions (default: stringent normal)
  --use-orphan           Include anomalous tumor read pairs
  --baq-off              Disable tumor BAQ
  --no-src-qual          Disable tumor source quality
  --normal-alpha N      Relaxed normal significance (default 0.1)
  --tumor-alpha N       Relaxed tumor significance (default 0.01)
  --call-rlx-extra-args TEXT     Extra call options; replace - with @
  --threads 1           Serial execution only
  -v, --verbose         Include stage commands in diagnostics
  --debug               Include stage commands in diagnostics
  -h, --help            Show this help

--continue is not implemented. All file paths are inside the /work workspace.
See runtime/LOFREQ.md for the full execution and validation contract.
`;
