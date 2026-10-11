// Serial orchestration translated from Strelka 2.9.10 src/python/{lib,libexec}.
// Strelka - Small Variant Caller. Copyright (c) 2009-2018 Illumina, Inc.
// SPDX-License-Identifier: GPL-3.0-or-later
// Calling, depth estimation, model scoring, compression, indexing and statistics
// remain upstream compiled code. See STRELKA2.md for translation boundaries.
import { workspacePath } from './index.mjs';

const encoder = new TextEncoder();
const decoder = new TextDecoder('utf-8', { fatal: true });
const UPSTREAM = Object.freeze({ version: '2.9.10', commit: '1c8f1be330dc6f58568c82468a9da1c5f32fb54a', archiveSha256: '45e78efec6e5272697f1d0a95851c7ae0d623dc8f93846e11fe37f15da9f1e30' });
const RESERVED = '/work/__strelka2';
const MARKER = '.strelka2-directory';
const DEFAULTS = Object.freeze({
  maxIndelSize: '49', depthFilterMultiple: '3.0', snvMaxFilteredBasecallFrac: '0.4',
  snvMaxSpanningDeletionFrac: '0.75', indelMaxWindowFilteredBasecallFrac: '0.3',
  ssnvPrior: '0.0001', sindelPrior: '0.000001', ssnvNoise: '0.0000000005',
  sindelNoiseFactor: '2.2', ssnvNoiseStrandBiasFrac: '0.0', minTier1Mapq: '20', minTier2Mapq: '0',
  ssnvQuality_LowerBound: '15', sindelQuality_LowerBound: '40', ssnvContamTolerance: '0.15',
  indelContamTolerance: '0.15', isWriteRealignedBam: '0', extraVariantCallerArguments: null,
  indelErrorModelName: null,
});
const SOMATIC_OPTIONS = [
  ['--min-mapping-quality', 'minTier1Mapq'], ['--somatic-snv-rate', 'ssnvPrior'],
  ['--shared-site-error-rate', 'ssnvNoise'], ['--shared-site-error-strand-bias-fraction', 'ssnvNoiseStrandBiasFrac'],
  ['--somatic-indel-rate', 'sindelPrior'], ['--shared-indel-error-factor', 'sindelNoiseFactor'],
  ['--tier2-min-mapping-quality', 'minTier2Mapq'], ['--strelka-snv-max-filtered-basecall-frac', 'snvMaxFilteredBasecallFrac'],
  ['--strelka-snv-max-spanning-deletion-frac', 'snvMaxSpanningDeletionFrac'], ['--strelka-snv-min-qss-ref', 'ssnvQuality_LowerBound'],
  ['--strelka-indel-max-window-filtered-basecall-frac', 'indelMaxWindowFilteredBasecallFrac'], ['--strelka-indel-min-qsi-ref', 'sindelQuality_LowerBound'],
  ['--ssnv-contam-tolerance', 'ssnvContamTolerance'], ['--indel-contam-tolerance', 'indelContamTolerance'],
];

/** Internal workflow entrypoint. execute runs a real compiled helper in a fresh worker. */
export async function runSomatic(args, {
  files = [], outputs = [], outputDirectories = [], stdin = new Uint8Array(), signal, timeoutMs = 0,
} = {}, execute, baseUrl) {
  const started = Date.now();
  if (!Array.isArray(args) || args.some(arg => typeof arg !== 'string' || arg.includes('\0'))) throw new TypeError('args must be strings without NUL');
  if (![files, outputs, outputDirectories].every(Array.isArray)) throw new TypeError('files, outputs and outputDirectories must be arrays');
  if (!Number.isFinite(timeoutMs) || timeoutMs < 0) throw new TypeError('timeoutMs must be a non-negative number');
  if (signal?.aborted) throw namedError('AbortError', 'Command cancelled');
  bytes(stdin); // Native workflow children do not inherit workflow stdin.
  const vfs = new Map();
  for (const file of files) {
    const path = workspacePath(file.path);
    if (vfs.has(path)) throw new TypeError(`Duplicate input path: ${path}`);
    if (internal(path)) throw new TypeError(`Reserved workflow path: ${path}`);
    // new Uint8Array copies Buffer inputs too; Buffer.slice() would alias them.
    vfs.set(path, new Uint8Array(bytes(file.data)));
  }
  const requestedOutputs = [...new Set(outputs.map(workspacePath))];
  const requestedDirectories = [...new Set(outputDirectories.map(workspacePath))];
  const directories = new Set(['/work']);
  const generated = new Set(), stages = [], models = {}, stdout = [], stderr = [];
  const hashes = new Map();
  const controller = new AbortController();
  let abortReason;
  const cancel = () => { abortReason ??= namedError('AbortError', 'Command cancelled'); controller.abort(); };
  const timeout = () => { abortReason ??= namedError('TimeoutError', `Command exceeded ${timeoutMs} ms`); controller.abort(); };
  signal?.addEventListener('abort', cancel, { once: true });
  if (signal?.aborted) cancel();
  const timer = timeoutMs ? setTimeout(timeout, Math.max(0, timeoutMs - (Date.now() - started))) : undefined;
  const check = () => {
    if (timeoutMs && Date.now() - started >= timeoutMs && !abortReason) timeout();
    if (abortReason) throw abortReason;
  };
  const diagnostic = message => stderr.push(encoder.encode(`${message}\n`));
  const isDirectory = path => directories.has(path) || [...vfs.keys()].some(file => file.startsWith(`${path}/`));
  const requireFile = path => {
    if (!vfs.has(path)) throw new WorkflowError(`File does not exist: ${path}`);
    return vfs.get(path);
  };
  const requireInputFile = path => {
    if (!vfs.has(path)) throw new UsageError(`Input file does not exist: ${path}`);
    return vfs.get(path);
  };
  const store = (path, data) => { vfs.set(path, data); hashes.delete(path); generated.add(path); };
  const digest = async path => {
    if (!hashes.has(path)) hashes.set(path, sha256(requireFile(path)));
    const value = await hashes.get(path); check(); return value;
  };
  const hashFiles = async paths => {
    const result = {};
    for (const path of paths) if (vfs.has(path)) result[path] = await digest(path);
    return result;
  };
  const finish = exitCode => {
    check();
    const returned = new Map(), missingOutputs = [];
    for (const path of requestedOutputs) {
      if (vfs.has(path) && !internal(path)) returned.set(path, vfs.get(path)); else missingOutputs.push(path);
    }
    for (const path of requestedDirectories) {
      if (vfs.has(path)) throw new Error(`Output directory is not a directory: ${path}`);
      if (!isDirectory(path)) missingOutputs.push(path);
      else for (const file of [...vfs.keys()].sort()) if (file.startsWith(`${path}/`) && !internal(file)) returned.set(file, vfs.get(file));
    }
    return { exitCode, stdout: concatenate(stdout), stderr: concatenate(stderr), files: [...returned].map(([path, data]) => ({ path, data })), missingOutputs,
      workflow: { upstream: { ...UPSTREAM }, stages, models, generatedFiles: [...generated].filter(path => !internal(path)).sort() } };
  };
  const translated = async (id, tool, command, inputs, outputPaths, action) => {
    check();
    const inputHashes = await hashFiles(inputs);
    action(); check();
    const missingOutputs = outputPaths.filter(path => !vfs.has(path));
    stages.push({ id, kind: 'translated', tool, args: command.slice(), exitCode: 0, inputs: inputHashes,
      outputs: outputPaths.slice(), missingOutputs, outputHashes: await hashFiles(outputPaths) });
    if (missingOutputs.length) throw new WorkflowError(`Stage ${id} did not produce ${missingOutputs.join(', ')}`);
  };
  const mkdir = async (id, path) => translated(id, 'mkdir', ['-p', path], [], [], () => {
    if (vfs.has(path)) throw new WorkflowError(`Cannot create directory over file: ${path}`);
    directories.add(path);
  });
  const removeDirectory = async (id, path) => translated(id, 'rm', ['-rf', path], [...vfs.keys()].filter(file => file.startsWith(`${path}/`)), [], () => {
    for (const file of vfs.keys()) if (file.startsWith(`${path}/`)) { vfs.delete(file); hashes.delete(file); }
    for (const dir of directories) if (dir === path || dir.startsWith(`${path}/`)) directories.delete(dir);
  });
  const run = async (id, tool, command, expected = [], { phase = 'workflow', quiet = false } = {}) => {
    check();
    const inputHashes = await hashFiles([...vfs.keys()]);
    const mounted = [...vfs].map(([path, data]) => ({ path, data }));
    // ToolRunner materializes parent directories for files. These transport-only
    // empty markers represent upstream mkdir tasks; they never enter outputs.
    for (const path of directories) if (path !== '/work') mounted.push({ path: `${path}/${MARKER}`, data: new Uint8Array() });
    let result;
    try {
      result = await execute(tool, command, { files: mounted, outputs: [...new Set([...expected, ...requestedOutputs])],
        outputDirectories: requestedDirectories.filter(path => isDirectory(path)), signal: controller.signal });
    } catch (error) { check(); throw error; }
    check();
    if (!quiet) stdout.push(result.stdout);
    stderr.push(result.stderr);
    const changed = [];
    for (const file of result.files) {
      if (internal(file.path)) continue;
      if (!vfs.has(file.path) || !equalBytes(vfs.get(file.path), file.data)) { store(file.path, file.data); changed.push(file.path); }
    }
    const missingOutputs = expected.filter(path => !vfs.has(path));
    stages.push({ id, kind: 'native', phase, tool, args: command.slice(), exitCode: result.exitCode,
      inputs: inputHashes, outputs: changed, missingOutputs, outputHashes: await hashFiles([...new Set([...expected, ...changed])]) });
    if (result.exitCode !== 0) throw new WorkflowError(`Stage ${id} (${tool}) failed with exit code ${result.exitCode}`);
    if (missingOutputs.length) throw new WorkflowError(`Stage ${id} did not produce ${missingOutputs.join(', ')}`);
    return result;
  };
  try {
    check();
    let o;
    try { o = parseArguments(args); }
    catch (error) { if (!(error instanceof UsageError)) throw error; diagnostic(`strelka2 somatic: ${error.message}`); return finish(2); }
    if (o.help) { stdout.push(encoder.encode(HELP)); return finish(o.allHelp ? 2 : 0); }
    if (o.version) { stdout.push(encoder.encode(`${UPSTREAM.version}\n`)); return finish(0); }
    try {
      if (o.config) Object.assign(o.parameters, parseConfig(decoder.decode(requireInputFile(o.config))));
      if (truthy(o.parameters.isWriteRealignedBam)) throw new UsageError('isWriteRealignedBam is not implemented by this workflow adapter; no realigned-BAM stage is skipped silently');
      if (isDirectory(o.runDir) || vfs.has(o.runDir)) throw new UsageError(`Run directory already exists: ${o.runDir}; workflow resume is not implemented`);
      requireInputFile(o.referenceFasta);
      const chromosomes = parseFai(decoder.decode(requireInputFile(o.referenceFasta + '.fai')));
      for (const path of [...o.normalBams, ...o.tumorBams]) {
        requireInputFile(path);
        if (!/\.(bam|cram)$/.test(path)) throw new UsageError(`Alignment must have .bam or .cram extension: ${path}`);
        if (![path + '.bai', path + '.csi', path + '.crai', ...(path.endsWith('.bam') ? [path.slice(0, -4) + '.bai'] : [])].some(index => vfs.has(index))) throw new UsageError(`Alignment index is missing: ${path}`);
      }
      for (const path of [...o.indelCandidates, ...o.forcedGT, ...o.noiseVcfs, ...(o.callRegions ? [o.callRegions] : [])]) {
        requireInputFile(path); requireInputFile(path + '.tbi');
      }
      // Default model files are part of the installed tool, including when EVS
      // is disabled. Load their exact bytes and expose hashes for provenance.
      for (const key of ['snvScoringModelFile', 'indelScoringModelFile']) {
        if (!o[key]) {
          const filename = key === 'snvScoringModelFile' ? 'somaticSNVScoringModels.json' : 'somaticIndelScoringModels.json';
          o[key] = `${RESERVED}/config/${filename}`;
          let data;
          try { data = await loadAsset(new URL(`config/${filename}`, baseUrl), controller.signal); }
          catch (error) { check(); throw new WorkflowError(`Cannot load scoring model ${filename}: ${error.message}`); }
          check(); vfs.set(o[key], data);
        }
        requireInputFile(o[key]); models[o[key]] = await digest(o[key]);
      }
      let firstSQ;
      for (const [index, bam] of [...o.normalBams, ...o.tumorBams].entries()) {
        const result = await run(`checkAlignmentHeader_${index}`, 'htsfile', ['-h', bam], [], { phase: 'configure', quiet: true });
        const sq = parseSq(decoder.decode(result.stdout));
        if (sq.length !== chromosomes.length || sq.some(item => !chromosomes.some(c => c.name === item.name && c.size === item.size))) throw new UsageError(`Alignment and reference must contain the same chromosome names and lengths: ${bam}`);
        if (firstSQ && (sq.length !== firstSQ.length || sq.some((item, i) => item.name !== firstSQ[i].name || item.size !== firstSQ[i].size))) throw new UsageError(`Alignment chromosome order differs between samples: ${bam}`);
        firstSQ ??= sq;
      }
      const regions = o.regions.flatMap(region => region.split('+')).map(region => parseRegion(region, chromosomes));
      let bedChromosomes;
      if (o.callRegions) {
        const result = await run('listCallRegionChromosomes', 'tabix', ['-l', o.callRegions], [], { phase: 'configure', quiet: true });
        bedChromosomes = new Set(decoder.decode(result.stdout).trim().split('\n').filter(Boolean));
        for (const name of bedChromosomes) if (!chromosomes.some(c => c.name === name)) throw new UsageError(`Call-regions chromosome is absent from reference: ${name}`);
      }
      const skipped = new Set(chromosomes.filter(c => (regions.length && !regions.some(r => r.name === c.name)) || (bedChromosomes && !bedChromosomes.has(c.name))).map(c => c.name));
      const groups = segmentGroups(chromosomes, regions, o.scanSizeMb, bedChromosomes);
      if (!groups.length) throw new UsageError('No genome regions to analyze');
      const root = o.runDir, workspace = `${root}/workspace`, segmentDir = `${workspace}/genomeSegment.tmpdir`;
      for (const path of [root, workspace, `${root}/results`, `${root}/results/variants`, `${root}/results/stats`, ...(o.outputCallableRegions ? [`${root}/results/regions`] : [])]) await mkdir(`mkdir_${path.slice(root.length) || 'run'}`, path);
      if (o.callMemMb !== null) diagnostic('--callMemMb is recorded as an upstream scheduler hint; browser memory remains limited by the compiled WASM memory and host browser.');
      const depth = `${workspace}/chromDepth.tsv`;
      if (!o.exome) {
        const depthDir = depth + '.tmpdir', sample = `${depthDir}/chromDepth.tsv.000.txt`, sampleDir = sample + '.tmpdir';
        await mkdir('getChromDepth_makeTmpDir', depthDir);
        await mkdir('getChromDepth_sample000_makeTmpDir', sampleDir);
        const depthFiles = [];
        for (const group of groupSmall(chromosomes.filter(c => !skipped.has(c.name)))) {
          const label = groupLabel(group, c => robustChromId(c));
          const output = `${sampleDir}/chromDepth.tsv.000.txt_${label}`;
          await run(`getChromDepth_sample000_estimateChromDepth_${label}`, 'GetChromDepth', ['--ref', o.referenceFasta, '--align-file', o.normalBams[0] || o.tumorBams[0], '--output', output, ...group.flatMap(c => ['--chrom', c.name])], [output]);
          depthFiles.push(output);
        }
        await translated('getChromDepth_sample000_catChromDepth', 'cat.py', ['--output', sample, ...depthFiles], depthFiles, [sample], () => store(sample, concatenate(depthFiles.map(requireFile))));
        await translated('getChromDepth_mergeChromDepth', 'mergeChromDepth.py', ['--out', depth, '--in', sample], [sample], [depth], () => store(depth, encoder.encode(mergeDepth([decoder.decode(requireFile(sample))]))));
        if (!o.retainTempFiles) await removeDirectory('getChromDepth_removeTmpDir', depthDir);
      }
      await mkdir('makeTmpDir', segmentDir);
      const segments = { snv: [], indel: [], callable: [], stats: [] };
      for (const [index, group] of groups.entries()) {
        const label = groupLabel(group, segment => segment.id);
        const snv = `${segmentDir}/somatic.snvs.unfiltered.${label}.vcf`, indel = `${segmentDir}/somatic.indels.unfiltered.${label}.vcf`;
        const callable = `${segmentDir}/somatic.callable.regions.${label}.bed`, stats = `${segmentDir}/runStats.${label}.xml`;
        const command = group.flatMap(segment => ['--region', segment.bamRegion]);
        command.push('--ref', o.referenceFasta, '--max-indel-size', o.parameters.maxIndelSize);
        if (o.parameters.indelErrorModelName) command.push('--indel-error-model-name', o.parameters.indelErrorModelName);
        if (o.reportEVSFeatures) command.push('--report-evs-features');
        for (const path of o.indelCandidates) command.push('--candidate-indel-input-vcf', path);
        for (const path of o.forcedGT) command.push('--force-output-vcf', path);
        if (o.callRegions) command.push('--call-regions-bed', o.callRegions);
        if (o.parameters.extraVariantCallerArguments) command.push(...o.parameters.extraVariantCallerArguments.trim().split(/\s+/));
        for (const [flag, key] of SOMATIC_OPTIONS) command.push(flag, o.parameters[key]);
        if (!o.disableEVS) command.push('--somatic-snv-scoring-model-file', o.snvScoringModelFile, '--somatic-indel-scoring-model-file', o.indelScoringModelFile);
        for (const path of o.normalBams) command.push('--normal-align-file', path);
        for (const path of o.tumorBams) command.push('--tumor-align-file', path);
        command.push('--somatic-snv-file', snv, '--somatic-indel-file', indel);
        if (o.outputCallableRegions) command.push('--somatic-callable-regions-file', callable);
        for (const path of o.noiseVcfs) command.push('--noise-vcf', path);
        command.push('--stats-file', stats);
        if (index) command.push('--strelka-skip-header');
        if (!o.exome) command.push('--strelka-chrom-depth-file', depth, '--strelka-max-depth-factor', o.parameters.depthFilterMultiple);
        await run(`callGenomeSegment_${label}`, 'strelka2', command, [snv, indel, stats, ...(o.outputCallableRegions ? [callable] : [])]);
        if (!index) for (const path of [snv, indel]) await translated(`fixVcfHeader_${label}_${path === snv ? 'snv' : 'indel'}`, 'vcfCmdlineSwapper.py', [['configureStrelkaSomaticWorkflow.py', ...args].join(' ')], [path], [path], () => store(path, rewriteHeader(requireFile(path), ['configureStrelkaSomaticWorkflow.py', ...args].join(' '))));
        for (const path of [snv, indel, ...(o.outputCallableRegions ? [callable] : [])]) {
          await run(`compressSegmentOutput_${label}_${path === snv ? 'snv' : path === indel ? 'indel' : 'callable'}`, 'bgzip', [path], [path + '.gz']);
          vfs.delete(path); hashes.delete(path); // Upstream bgzip removes its input on success.
        }
        segments.snv.push(snv + '.gz'); segments.indel.push(indel + '.gz'); segments.stats.push(stats);
        if (o.outputCallableRegions) segments.callable.push(callable + '.gz');
      }
      const finishFile = async (label, inputs, output, type) => {
        if (inputs.length > 1) await run(`${label}_concat_${type}`, 'bgzf_cat', ['-o', output, ...inputs], [output]);
        else await translated(`${label}_concat_${type}`, 'mv', [inputs[0], output], inputs, [output], () => { store(output, requireFile(inputs[0])); vfs.delete(inputs[0]); hashes.delete(inputs[0]); });
        await run(`${label}_index_${type}`, 'tabix', ['-p', type, output], [output + '.tbi']);
      };
      await finishFile('SNV', segments.snv, `${root}/results/variants/somatic.snvs.vcf.gz`, 'vcf');
      await finishFile('Indel', segments.indel, `${root}/results/variants/somatic.indels.vcf.gz`, 'vcf');
      const statsXml = `${root}/results/stats/runStats.xml`, statsTsv = `${root}/results/stats/runStats.tsv`;
      await run('mergeRunStats', 'MergeRunStats', [...segments.stats.flatMap(path => ['--stats-file', path]), '--output-file', statsXml, '--report-file', statsTsv], [statsXml, statsTsv]);
      if (o.outputCallableRegions) await finishFile('callableRegions', segments.callable, `${root}/results/regions/somatic.callable.regions.bed.gz`, 'bed');
      if (!o.retainTempFiles) await removeDirectory('removeTmpDir', segmentDir);
      return finish(0);
    } catch (error) {
      check();
      if (!(error instanceof UsageError || error instanceof WorkflowError)) throw error;
      diagnostic(`strelka2 somatic: ${error.message}`);
      return finish(error instanceof UsageError ? 2 : 1);
    }
  } finally { if (timer !== undefined) clearTimeout(timer); signal?.removeEventListener('abort', cancel); }
}

export function parseArguments(args) {
  const o = { normalBams: [], tumorBams: [], referenceFasta: null, runDir: '/work/StrelkaSomaticWorkflow',
    indelCandidates: [], forcedGT: [], noiseVcfs: [], regions: [], callRegions: null,
    exome: false, disableEVS: false, reportEVSFeatures: false, outputCallableRegions: false, retainTempFiles: false,
    scanSizeMb: 12, callMemMb: null, snvScoringModelFile: null, indelScoringModelFile: null, config: null, parameters: { ...DEFAULTS } };
  const flags = { '--exome': 'exome', '--targeted': 'exome', '--disableEVS': 'disableEVS', '--reportEVSFeatures': 'reportEVSFeatures', '--outputCallableRegions': 'outputCallableRegions', '--retainTempFiles': 'retainTempFiles', '--help': 'help', '-h': 'help', '--allHelp': 'allHelp', '--version': 'version' };
  const scalar = { '--referenceFasta': 'referenceFasta', '--runDir': 'runDir', '--callRegions': 'callRegions', '--snvScoringModelFile': 'snvScoringModelFile', '--indelScoringModelFile': 'indelScoringModelFile', '--config': 'config', '--scanSizeMb': 'scanSizeMb', '--callMemMb': 'callMemMb' };
  const repeated = { '--normalBam': 'normalBams', '--tumorBam': 'tumorBams', '--tumourBam': 'tumorBams', '--indelCandidates': 'indelCandidates', '--forcedGT': 'forcedGT', '--noiseVcf': 'noiseVcfs', '--region': 'regions' };
  for (let i = 0; i < args.length; i++) {
    const equal = args[i].indexOf('='), flag = equal < 0 ? args[i] : args[i].slice(0, equal);
    if (flags[flag]) {
      if (equal >= 0) throw new UsageError(`${flag} does not take a value`);
      o[flags[flag]] = true; if (flag === '--allHelp') o.help = true;
      continue;
    }
    const key = scalar[flag] || repeated[flag];
    if (!key) throw new UsageError(`Unknown or unsupported option: ${flag}`);
    const value = equal >= 0 ? args[i].slice(equal + 1) : args[++i];
    if (value === undefined || value === '') throw new UsageError(`${flag} requires a value`);
    if (repeated[flag]) o[key].push(value); else o[key] = value;
  }
  if (o.help || o.version) return o;
  if (!o.referenceFasta) throw new UsageError('--referenceFasta is required');
  if (!o.tumorBams.length) throw new UsageError('--tumorBam is required');
  if (o.tumorBams.length > 1 || o.normalBams.length > 1) throw new UsageError('At most one normal and one tumor alignment are supported by upstream Strelka2');
  for (const key of ['referenceFasta', 'runDir', 'callRegions', 'snvScoringModelFile', 'indelScoringModelFile', 'config']) if (o[key]) o[key] = checkedPath(o[key]);
  for (const key of ['normalBams', 'tumorBams', 'indelCandidates', 'forcedGT', 'noiseVcfs']) o[key] = o[key].map(checkedPath);
  if (new Set([...o.normalBams, ...o.tumorBams]).size !== o.normalBams.length + o.tumorBams.length) throw new UsageError('Normal and tumor alignment paths must differ');
  for (const key of ['scanSizeMb', 'callMemMb']) if (o[key] !== null) {
    if (!/^\d+$/.test(String(o[key])) || !Number.isSafeInteger(Number(o[key])) || Number(o[key]) < 1) throw new UsageError(`${key} must be a positive integer`);
    o[key] = Number(o[key]);
  }
  return o;
}

export function parseConfig(text) {
  let section = null, previous = null, hasSomaticSection = false;
  const defaults = {}, specific = {};
  for (const line of text.split(/\r?\n/)) {
    if (!line.trim() || /^[#;]/.test(line.trim())) continue;
    const header = line.trim().match(/^\[([^\]]+)\]$/);
    if (header) { section = header[1]; previous = null; if (section === 'StrelkaSomatic') hasSomaticSection = true; continue; }
    if (!['StrelkaSomatic', 'DEFAULT'].includes(section)) continue;
    const values = section === 'DEFAULT' ? defaults : specific;
    if (/^\s/.test(line) && previous) { values[previous] = `${values[previous] || ''}\n${line.trim()}`; continue; }
    const entry = line.match(/^([^:=]+)\s*[:=]\s*(.*)$/);
    if (!entry) throw new UsageError(`Malformed configuration line: ${line}`);
    const key = entry[1].trim();
    if (!Object.hasOwn(DEFAULTS, key)) throw new UsageError(`Unsupported [${section}] configuration key: ${key}`);
    previous = key;
    // ConfigParser strips an inline semicolon only when preceded by whitespace.
    values[key] = entry[2].replace(/\s;.*$/, '').trim() || null;
  }
  if (section === null) throw new UsageError('Configuration requires an INI section');
  // SafeConfigParser.items(section) includes DEFAULT values, with section
  // values taking precedence irrespective of where DEFAULT appeared in file.
  // A DEFAULT-only custom file has no section for getIniSections to merge.
  const values = hasSomaticSection ? { ...defaults, ...specific } : {};
  for (const [key, value] of Object.entries(values)) if (value?.includes('%')) throw new UsageError(`INI interpolation is not implemented: ${key}`);
  for (const [key, value] of Object.entries(values)) if (value === null && !['extraVariantCallerArguments', 'indelErrorModelName'].includes(key)) throw new UsageError(`Configuration value cannot be empty: ${key}`);
  return values;
}

export function parseFai(text) {
  const result = [], names = new Set();
  for (const line of text.trimEnd().split('\n')) {
    const columns = line.replace(/\r$/, '').split('\t');
    if (columns.length !== 5 || !columns[0] || columns.slice(1).some(value => !/^\d+$/.test(value))) throw new UsageError('Reference FASTA index must contain five valid tab-separated columns');
    const size = Number(columns[1]);
    if (!Number.isSafeInteger(size) || size < 1 || names.has(columns[0])) throw new UsageError('Reference index has an invalid length or duplicate chromosome');
    names.add(columns[0]); result.push({ name: columns[0], size, index: result.length });
  }
  return result;
}
function parseSq(text) {
  const result = [], names = new Set();
  for (const line of text.split('\n')) if (line.startsWith('@SQ\t')) {
    const fields = Object.fromEntries(line.split('\t').slice(1).map(field => [field.slice(0, 2), field.slice(3)]));
    const size = Number(fields.LN);
    if (!fields.SN || !Number.isSafeInteger(size) || size < 1 || names.has(fields.SN)) throw new UsageError('Invalid or duplicate @SQ alignment header');
    names.add(fields.SN); result.push({ name: fields.SN, size });
  }
  if (!result.length) throw new UsageError('Alignment has no @SQ chromosome header');
  return result;
}
export function parseRegion(text, chromosomes) {
  text = text.trim();
  let name = text, start, end;
  const colon = text.lastIndexOf(':');
  if (colon >= 0) {
    const fields = text.slice(colon + 1).split('-');
    if (fields.length === 2) {
      name = text.slice(0, colon); start = Number(fields[0]); end = Number(fields[1]);
      if (fields.some(value => !/^\d+$/.test(value)) || !Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start < 1 || end < start) throw new UsageError(`Invalid region: ${text}`);
    }
  }
  if (!chromosomes.some(c => c.name === name)) throw new UsageError(`Region chromosome is absent from reference: ${name}`);
  return { name, start, end };
}
export function segmentGroups(chromosomes, regions, scanSizeMb, bedChromosomes) {
  const segments = [];
  for (const region of regions.length ? regions : [null]) for (const chrom of chromosomes) {
    if (region && chrom.name !== region.name || bedChromosomes && !bedChromosomes.has(chrom.name)) continue;
    const first = region?.start ?? 1, last = region?.end ?? chrom.size, length = last - first + 1;
    const count = 1 + Math.floor((length - 1) / (scanSizeMb * 1e6)), base = Math.floor(length / count), remainder = length % count;
    let start = first;
    for (let index = 0; index < count; index++) {
      const size = base + (index < remainder ? 1 : 0), end = start + size - 1;
      const regionId = robustChromId(chrom) + (region?.start === undefined ? '' : `-${first}-${last}`);
      segments.push({ id: `chromId_${regionId}_${String(index).padStart(4, '0')}`, name: chrom.name, size, bamRegion: `${chrom.name}:${start}-${end}` });
      start = end + 1;
    }
  }
  return groupSmall(segments);
}
function groupSmall(items) {
  const groups = []; let group = [], size = 0;
  for (const item of items) {
    if (size + item.size > 200000 && group.length) { groups.push(group); group = []; size = 0; }
    group.push(item); size += item.size;
  }
  if (group.length) groups.push(group);
  return groups;
}
function robustChromId(chrom) {
  // Upstream Python2 cleanId operates on UTF-8 bytes, not Unicode codepoints.
  const clean = [...encoder.encode(chrom.name)].map(byte => byte >= 65 && byte <= 90 || byte >= 97 && byte <= 122 || byte >= 48 && byte <= 57 || byte === 95 || byte === 45 ? String.fromCharCode(byte) : '_').join('');
  return `${String(chrom.index).padStart(3, '0')}_${clean}`;
}
function groupLabel(group, label) { return label(group[0]) + (group.length > 1 ? `_to_${label(group.at(-1))}` : ''); }

export function mergeDepth(inputs) {
  const depths = new Map();
  for (const [index, text] of inputs.entries()) {
    const current = new Map();
    for (const line of text ? text.replace(/\n$/, '').split('\n') : []) {
      const fields = line.trim().split('\t');
      if (fields.length !== 2 || !fields[0] || !fields[1].trim()) throw new WorkflowError('Malformed chromosome-depth row');
      if (current.has(fields[0])) throw new WorkflowError(`Duplicate chromosome-depth row: ${fields[0]}`);
      const depth = Number(fields[1]);
      if (!Number.isFinite(depth)) throw new WorkflowError('Non-finite chromosome depth');
      if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(fields[1])) throw new WorkflowError('Malformed chromosome depth');
      current.set(fields[0], depth);
    }
    if (index && (current.size !== depths.size || [...current.keys()].some(name => !depths.has(name)))) throw new WorkflowError('Chromosome sets differ between depth inputs');
    for (const [name, depth] of current) depths.set(name, index ? depths.get(name) + depth : depth);
  }
  // Python2 dict traversal order is unspecified; insertion order changes no
  // depth value or core behavior. Comparisons match rows by chromosome.
  return [...depths].map(([name, depth]) => `${name}\t${pythonFixed(depth, 3)}\n`).join('');
}
export function rewriteHeader(data, command) {
  const lines = decoder.decode(data).match(/[^\n]*\n|[^\n]+$/g) || [];
  let found = false;
  return encoder.encode(lines.map(line => {
    if (line.startsWith('##cmdline=')) { found = true; return `##cmdline=${command}\n`; }
    if (!line.startsWith('##') && !found) { found = true; return `##cmdline=${command}\n` + line; }
    return line;
  }).join(''));
}
// IEEE-754 rational rounding matches Python/C %f, including halfway ties-even.
export function pythonFixed(value, digits) {
  const view = new DataView(new ArrayBuffer(8)); view.setFloat64(0, value);
  const bits = view.getBigUint64(0), negative = (bits >> 63n) !== 0n;
  const exponent = Number((bits >> 52n) & 0x7ffn), fraction = bits & ((1n << 52n) - 1n);
  if (exponent === 0x7ff) throw new WorkflowError('Cannot format a non-finite depth');
  let numerator = exponent === 0 ? fraction : fraction + (1n << 52n);
  const binaryExponent = (exponent === 0 ? -1022 : exponent - 1023) - 52;
  let denominator = 1n;
  if (binaryExponent >= 0) numerator <<= BigInt(binaryExponent); else denominator <<= BigInt(-binaryExponent);
  numerator *= 10n ** BigInt(digits);
  let rounded = numerator / denominator;
  const remainder = numerator % denominator;
  if (remainder * 2n > denominator || remainder * 2n === denominator && rounded % 2n) rounded++;
  const text = rounded.toString().padStart(digits + 1, '0');
  return `${negative ? '-' : ''}${digits ? text.slice(0, -digits) + '.' + text.slice(-digits) : text}`;
}
function checkedPath(path) { try { const value = workspacePath(path); if (internal(value)) throw new Error('reserved internal path'); return value; } catch (error) { throw new UsageError(`Invalid path ${path}: ${error.message}`); } }
function internal(path) { return path === RESERVED || path.startsWith(RESERVED + '/') || path.split('/').includes(MARKER); }
function truthy(value) { return !['', '0', 'false', 'f', 'no', 'n', 'off'].includes(String(value).toLowerCase()); }
function bytes(data) { if (typeof data === 'string') return encoder.encode(data); if (data instanceof Uint8Array) return data; if (data instanceof ArrayBuffer) return new Uint8Array(data); throw new TypeError('file data and stdin must be strings, Uint8Array or ArrayBuffer'); }
function concatenate(parts) { const output = new Uint8Array(parts.reduce((sum, part) => sum + part.length, 0)); let offset = 0; for (const part of parts) { output.set(part, offset); offset += part.length; } return output; }
function equalBytes(a, b) { return a.length === b.length && a.every((value, i) => value === b[i]); }
async function sha256(data) { const digest = await crypto.subtle.digest('SHA-256', data); return [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, '0')).join(''); }
async function loadAsset(url, signal) {
  if (url.protocol === 'file:' && typeof process !== 'undefined' && process.versions?.node) {
    const { readFile } = await import('node:fs/promises'); return new Uint8Array(await readFile(url, { signal }));
  }
  const response = await fetch(url, { signal });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return new Uint8Array(await response.arrayBuffer());
}
function namedError(name, message) { const error = new Error(message); error.name = name; return error; }
class UsageError extends Error {}
class WorkflowError extends Error {}

const HELP = `Usage: somatic --tumorBam TUMOR --referenceFasta REF [options]

Strelka2 2.9.10 somatic workflow, executing upstream compiled tools serially.
  --normalBam FILE              Indexed matched-normal BAM/CRAM
  --tumorBam, --tumourBam FILE  Indexed tumor BAM/CRAM (required)
  --referenceFasta FILE         Reference FASTA with .fai (required)
  --runDir DIR                  Output directory (default StrelkaSomaticWorkflow)
  --exome, --targeted           Disable chromosome high-depth filtering
  --region CHROM[:START-END]    Restrict calling; repeatable, supports + lists
  --callRegions FILE            BGZF/tabix-indexed BED call intervals
  --indelCandidates FILE        Indexed candidate-indel VCF; repeatable
  --forcedGT FILE               Indexed forced-output VCF; repeatable
  --noiseVcf FILE               Indexed site-noise VCF; repeatable
  --outputCallableRegions       Emit indexed callable-region BED
  --disableEVS                  Disable empirical variant scoring
  --reportEVSFeatures           Include empirical scoring features
  --snvScoringModelFile FILE    Override bundled SNV model
  --indelScoringModelFile FILE  Override bundled indel model
  --config FILE                 [StrelkaSomatic] biological parameter overrides
  --scanSizeMb INT              Maximum scan segment size in Mb (default 12)
  --callMemMb INT               Upstream scheduler hint; not a browser memory cap
  --retainTempFiles             Retain upstream intermediate directories
  --version                    Print upstream version
  -h, --help                    Show this help

All inputs and outputs reside in /work. No server, subprocess or shared memory
is required. Resume and optional realigned-BAM output are not implemented.
See runtime/STRELKA2.md for limits, provenance and validation requirements.
`;
