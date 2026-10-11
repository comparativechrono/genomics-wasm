#!/usr/bin/env node
/** Native / patched native / WebAssembly tests of real transcript quantifiers. */
import { readFile, writeFile, mkdir, readdir, rm, lstat } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { execFileSync, spawnSync } from 'node:child_process';
import { createServer } from 'node:http';
import { dirname, resolve, join, relative, sep } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { ToolRunner, workspacePath } from '../runtime/index.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const options = { tool: 'all', engine: 'node', dataset: 'all' };
const usage = 'validate-quant.mjs [--tool kallisto|salmon|all] [--engine node|chromium|firefox|webkit] [--dataset human|yeast|all]';
for (let i = 2; i < process.argv.length; i += 2) {
  const key = process.argv[i].replace(/^--/, '');
  if (!(key in options) || !process.argv[i + 1]) throw new Error(usage);
  options[key] = process.argv[i + 1];
}
if (!['all', 'kallisto', 'salmon'].includes(options.tool) || !['node', 'chromium', 'firefox', 'webkit'].includes(options.engine) || !['all', 'human', 'yeast'].includes(options.dataset)) throw new Error(usage);
const requestedTools = options.tool === 'all' ? ['kallisto', 'salmon'] : [options.tool];
const datasets = options.dataset === 'all' ? ['human', 'yeast'] : [options.dataset];
const reportDir = join(root, 'reports', 'quant', options.engine);
await mkdir(reportDir, { recursive: true });
const summary = { schema: 1, startedAt: new Date().toISOString(), engine: options.engine,
  node: process.version, status: 'running', builds: {}, datasets: {}, cases: [] };
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const timeoutMs = 600000;
const configs = {
  kallisto: {
    indexArgs: k => ['index', '-i', 'index.idx', '-k', String(k), 'transcripts.fa'],
    indexOutputs: ['index.idx'], indexDirectories: [],
    outputs: ['out/abundance.tsv', 'out/run_info.json'],
    base: ['quant', '-i', 'index.idx', '-o', 'out', '-t', '1', '--plaintext'],
    paired: ['reads_1.fastq', 'reads_2.fastq'],
    single: ['--single', '-l', '200', '-s', '20', 'reads_1.fastq'],
    cases: [
      ['paired', []], ['single', ['--single', '-l', '200', '-s', '20'], true],
      ['fragment-prior', ['--single', '-l', '250', '-s', '30'], true],
      ['fr-stranded', ['--fr-stranded']], ['rf-stranded', ['--rf-stranded']],
      ['sequence-bias', ['--bias']],
    ],
    // MEMORY64=2 preserves upstream's 64-bit serialized C++ layout. Require
    // index interchange in both directions, not merely successful own-index runs.
    portableIndex: true,
  },
  salmon: {
    // Keep all real input records and their original lengths in the comparison
    // contract, including duplicate sequences and genuine poly-A tails.
    indexArgs: k => ['index', '-t', 'transcripts.fa', '-i', 'index', '-k', String(k), '-p', '1', '--ramLimit', '1', '--keepDuplicates', '--no-clip'],
    indexOutputs: [], indexDirectories: ['index'],
    outputs: ['out/quant.sf', 'out/aux_info/meta_info.json', 'out/lib_format_counts.json'],
    base: ['quant', '-i', 'index', '-o', 'out', '-p', '1', '--deterministic'],
    paired: ['-1', 'reads_1.fastq', '-2', 'reads_2.fastq'],
    single: ['-l', 'U', '--fldMean', '200', '--fldSD', '20', '-r', 'reads_1.fastq'],
    cases: [
      ['paired', ['-l', 'IU']], ['automatic-library', ['-l', 'A']],
      ['single', ['-l', 'U', '--fldMean', '200', '--fldSD', '20'], true],
      ['fragment-prior', ['-l', 'U', '--fldMean', '250', '--fldSD', '30'], true],
      ['fr-stranded', ['-l', 'ISF']], ['rf-stranded', ['-l', 'ISR']],
      ['em', ['-l', 'IU', '--useEM']],
      ['bias', ['-l', 'IU', '--seqBias', '--gcBias', '--posBias']],
    ],
    portableIndex: false,
  },
};
summary.expectedCases = requestedTools.flatMap(tool => {
  const planned = datasets.flatMap(dataset => [
    `${tool}-${dataset}-index-k31`,
    ...configs[tool].cases.map(([name]) => `${tool}-${dataset}-${name}`),
    `${tool}-${dataset}-index-k21`, `${tool}-${dataset}-k21`,
    `${tool}-${dataset}-repeat-1`, `${tool}-${dataset}-repeat-2`,
    ...(configs[tool].portableIndex ? [`${tool}-${dataset}-cross-index`] : []),
  ]);
  planned.push(`${tool}-missing-read`, `${tool}-invalid-fragment-prior`, `${tool}-missing-index`, `${tool}-malformed-fastq`, `${tool}-unsupported-threads`);
  return planned;
});
let browser, server, page;

function log(message) { console.log(`[${new Date().toISOString()}] ${options.engine} ${message}`); }
const normalized = file => ({ ...file, path: file.path.replace(/^\/work\//, '') });

async function startBrowser() {
  const playwright = await import('playwright');
  browser = await playwright[options.engine].launch({ headless: true });
  summary.browserVersion = browser.version();
  server = createServer(async (request, response) => {
    response.setHeader('Cross-Origin-Opener-Policy', 'same-origin');
    response.setHeader('Cross-Origin-Embedder-Policy', 'require-corp');
    response.setHeader('Cross-Origin-Resource-Policy', 'same-origin');
    try {
      const url = new URL(request.url, 'http://localhost');
      if (url.pathname === '/') { response.setHeader('Content-Type', 'text/html'); response.end('<!doctype html><meta charset="utf-8"><title>Quantifier validation</title>'); return; }
      const path = resolve(root, '.' + decodeURIComponent(url.pathname));
      if (!path.startsWith(root + sep) || !['runtime/', 'dist/'].some(prefix => relative(root, path).startsWith(prefix))) { response.writeHead(404).end(); return; }
      response.setHeader('Content-Type', path.endsWith('.wasm') ? 'application/wasm' : 'text/javascript');
      response.end(await readFile(path));
    } catch { response.writeHead(404).end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  page = await browser.newPage();
  page.on('console', message => log(`browser ${message.type()}: ${message.text()}`));
  page.on('pageerror', error => log(`browser error: ${error.message}`));
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  summary.crossOriginIsolated = await page.evaluate(() => crossOriginIsolated);
  if (!summary.crossOriginIsolated) throw new Error('Browser test server did not enable cross-origin isolation');
}

async function wasm(tool, args, files, outputs = [], outputDirectories = []) {
  const settings = { files, outputs, outputDirectories, timeoutMs };
  if (options.engine === 'node') return new ToolRunner({ moduleUrl: pathToFileURL(join(root, 'dist', tool, `${tool}.mjs`)), waitForExit: tool === 'salmon' }).run(args, settings);
  const result = await page.evaluate(async ({ tool, args, files, outputs, outputDirectories, timeoutMs }) => {
    const { ToolRunner } = await import('/runtime/index.mjs');
    const encode = bytes => {
      let binary = '';
      for (let offset = 0; offset < bytes.length; offset += 32768) binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768));
      return btoa(binary);
    };
    const runner = new ToolRunner({ moduleUrl: new URL(`/dist/${tool}/${tool}.mjs`, location.href), waitForExit: tool === 'salmon' });
    const result = await runner.run(args, { files: files.map(f => ({ path: f.path, data: Uint8Array.from(atob(f.data), c => c.charCodeAt(0)) })), outputs, outputDirectories, timeoutMs });
    return { ...result, stdout: encode(result.stdout), stderr: encode(result.stderr), files: result.files.map(f => ({ path: f.path, data: encode(f.data) })) };
  }, { tool, args, outputs, outputDirectories, timeoutMs, files: files.map(f => ({ path: f.path, data: Buffer.from(f.data).toString('base64') })) });
  return { ...result, stdout: Buffer.from(result.stdout, 'base64'), stderr: Buffer.from(result.stderr, 'base64'), files: result.files.map(f => ({ path: f.path, data: Buffer.from(f.data, 'base64') })) };
}

async function native(tool, kind, args, files, outputs = [], outputDirectories = [], id = 'command') {
  const work = join(root, 'build', 'quant-validation', options.engine, tool, id, kind);
  await rm(work, { recursive: true, force: true });
  await mkdir(work, { recursive: true });
  for (const file of files) {
    const path = join(work, workspacePath(file.path).slice(6));
    await mkdir(dirname(path), { recursive: true });
    await writeFile(path, file.data);
  }
  const processResult = spawnSync(join(root, 'build', kind, tool), args, {
    cwd: work, maxBuffer: 64 * 1024 * 1024, timeout: timeoutMs, stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, LC_ALL: 'C', TZ: 'UTC' },
  });
  if (processResult.error) throw processResult.error;
  if (!Number.isInteger(processResult.status)) throw new Error(`${tool} ${kind} terminated by ${processResult.signal}`);
  const result = { exitCode: processResult.status, stdout: processResult.stdout, stderr: processResult.stderr, files: [], missingOutputs: [] };
  const wanted = new Set(outputs);
  async function walk(directory) {
    for (const name of (await readdir(join(work, directory))).sort()) {
      const path = `${directory}/${name}`, stat = await lstat(join(work, path));
      if (stat.isDirectory()) await walk(path);
      else if (stat.isFile()) wanted.add(path);
      else throw new Error(`Unexpected non-regular output ${path}`);
    }
  }
  for (const directory of outputDirectories) {
    try { await walk(directory); } catch (error) { if (error.code !== 'ENOENT') throw error; result.missingOutputs.push(directory); }
  }
  for (const path of [...wanted].sort()) {
    try { result.files.push({ path, data: await readFile(join(work, path)) }); }
    catch (error) { if (error.code !== 'ENOENT') throw error; result.missingOutputs.push(path); }
  }
  return result;
}

async function saveResult(id, kind, result) {
  const directory = join(reportDir, id, kind);
  await mkdir(directory, { recursive: true });
  await writeFile(join(directory, 'stdout.txt'), result.stdout);
  await writeFile(join(directory, 'stderr.txt'), result.stderr);
  await writeFile(join(directory, 'command-result.json'), JSON.stringify({ exitCode: result.exitCode, missingOutputs: result.missingOutputs }, null, 2) + '\n');
  for (const file of result.files.map(normalized)) {
    const path = join(directory, workspacePath(file.path).slice(6));
    await mkdir(dirname(path), { recursive: true });
    await writeFile(path, file.data);
  }
  return directory;
}

function success(result, label) {
  if (result.exitCode !== 0 || result.missingOutputs.length) throw new Error(`${label}: exit ${result.exitCode}, missing ${result.missingOutputs.join(', ')}\n${Buffer.from(result.stderr).toString().slice(-4000)}`);
}

async function compare(tool, dataset, expectedDirectory, actualDirectory, processed, id, kind) {
  const report = join(reportDir, id, `compare-${kind}.json`);
  const args = [join(root, 'validation', 'quant', 'compare.py'), tool,
    join(expectedDirectory, 'out'), join(actualDirectory, 'out'),
    '--transcripts', join(root, 'datasets', 'rna-quant', dataset, 'transcripts.fa'),
    '--processed', String(processed), '--policy', join(root, 'validation', 'quant', 'tolerances.json'), '--report', report];
  try { execFileSync('python3', args, { maxBuffer: 4 * 1024 * 1024 }); }
  catch (error) { throw new Error(`Quantification mismatch: ${id} ${kind}; see ${relative(root, report)}\n${String(error.stdout ?? '').slice(0, 3000)}`); }
  return JSON.parse(await readFile(report, 'utf8'));
}

async function run(tool, kind, args, files, outputs, directories, id) {
  log(`${id} ${kind} starting`);
  const started = Date.now();
  const result = kind === 'wasm' ? await wasm(tool, args, files, outputs, directories) : await native(tool, kind, args, files, outputs, directories, id);
  const directory = await saveResult(id, kind, result);
  log(`${id} ${kind} exit ${result.exitCode} after ${((Date.now() - started) / 1000).toFixed(2)} seconds`);
  return { result, directory };
}

async function indexCase(tool, dataset, files, k) {
  const config = configs[tool], id = `${tool}-${dataset}-index-k${k}`, args = config.indexArgs(k);
  const record = { id, tool, args, status: 'running', indexInterchange: config.portableIndex ? 'required' : 'not established' };
  summary.cases.push(record);
  const indexes = {};
  for (const kind of ['native', 'patched-native', 'wasm']) {
    const { result } = await run(tool, kind, args, files.filter(f => f.path === 'transcripts.fa'), config.indexOutputs, config.indexDirectories, id);
    success(result, `${id} ${kind}`);
    indexes[kind] = result.files.map(normalized);
    if (!indexes[kind].length || indexes[kind].every(file => file.data.length === 0)) throw new Error(`${id} ${kind} returned no index data`);
  }
  record.files = Object.fromEntries(Object.entries(indexes).map(([kind, files]) => [kind, Object.fromEntries(files.map(file => [file.path, hash(file.data)]))]));
  record.status = 'passed';
  return indexes;
}

async function quantCase(tool, dataset, name, args, files, indexes, processed) {
  const id = `${tool}-${dataset}-${name}`, record = { id, tool, args, status: 'running', comparisons: {} };
  summary.cases.push(record);
  const results = {};
  for (const kind of ['native', 'patched-native', 'wasm']) {
    results[kind] = await run(tool, kind, args, [...files, ...indexes[kind]], configs[tool].outputs, [], id);
    success(results[kind].result, `${id} ${kind}`);
  }
  // A self-comparison validates original native output against the independent
  // transcript FASTA and actual FASTQ fragment count, before port comparisons.
  await compare(tool, dataset, results.native.directory, results.native.directory, processed, id, 'native-contract');
  for (const kind of ['patched-native', 'wasm']) record.comparisons[kind] = await compare(tool, dataset, results.native.directory, results[kind].directory, processed, id, kind);
  record.status = 'passed';
  log(`PASS ${id}`);
  return results;
}

function fastqRecords(data, label) {
  const lines = Buffer.from(data).toString('utf8').trimEnd().split('\n');
  if (lines.length % 4) throw new Error(`${label}: incomplete FASTQ record`);
  const names = [];
  for (let i = 0; i < lines.length; i += 4) {
    if (!lines[i].startsWith('@') || !/^[ACGTNacgtn]+$/.test(lines[i + 1]) || !lines[i + 2].startsWith('+') || lines[i + 1].length !== lines[i + 3].length || !/^[!-~]+$/.test(lines[i + 3])) throw new Error(`${label}: invalid FASTQ record ${i / 4 + 1}`);
    names.push(lines[i].split(/\s/)[0].replace(/\/[12]$/, ''));
  }
  if (new Set(names).size !== names.length) throw new Error(`${label}: duplicate read IDs`);
  return names;
}

async function loadDataset(dataset) {
  const directory = join(root, 'datasets', 'rna-quant', dataset);
  const files = await Promise.all(['transcripts.fa', 'reads_1.fastq', 'reads_2.fastq'].map(async path => ({ path, data: await readFile(join(directory, path)) })));
  const first = fastqRecords(files[1].data, `${dataset} mate 1`), second = fastqRecords(files[2].data, `${dataset} mate 2`);
  if (!first.length || first.length !== second.length || first.some((name, index) => name !== second[index])) throw new Error(`${dataset}: mates differ in read identities or order`);
  summary.datasets[dataset] = { pairs: first.length, files: Object.fromEntries(files.map(f => [f.path, hash(f.data)])) };
  return { files, processed: first.length };
}

async function failureCase(tool, name, args, files = [], indexes = {}, { allowFailedOutput = false } = {}) {
  const id = `${tool}-${name}`, record = { id, tool, args, status: 'running', exitCodes: {} };
  summary.cases.push(record);
  for (const kind of ['native', 'patched-native', 'wasm']) {
    const { result } = await run(tool, kind, args, [...files, ...(indexes[kind] ?? [])], configs[tool].outputs, [], id);
    if (result.exitCode === 0) throw new Error(`${id} ${kind} accepted invalid inputs`);
    if (!allowFailedOutput && result.files.some(file => /(?:abundance\.tsv|quant\.sf)$/.test(file.path))) throw new Error(`${id} ${kind} emitted an unexpected quantification table`);
    if (!result.stderr.length) throw new Error(`${id} ${kind} returned no diagnostic`);
    record.exitCodes[kind] = result.exitCode;
    if (allowFailedOutput) {
      record.failedOutputFiles ??= {};
      record.failedOutputFiles[kind] = result.files.map(normalized).map(file => file.path);
      // Kallisto upstream writes a zero-count table with NaN TPM before it
      // returns exit 1 for a malformed file with no readable records. Retain
      // this failure output; never feed it to the successful-result comparator.
      const metadata = result.files.find(file => file.path.endsWith('/run_info.json') || file.path === 'out/run_info.json');
      if (tool === 'kallisto' && (!metadata || JSON.parse(Buffer.from(metadata.data).toString()).n_processed !== 0)) throw new Error(`${id} ${kind} did not report zero readable records`);
    }
  }
  if (new Set(Object.values(record.exitCodes)).size !== 1) throw new Error(`${id}: native, patched-native and WASM failure codes differ`);
  record.status = 'passed';
}

async function threadRestrictionCase(tool, args, files, indexes) {
  const id = `${tool}-unsupported-threads`, changed = [...args];
  changed[changed.indexOf(tool === 'kallisto' ? '-t' : '-p') + 1] = '2';
  const record = { id, tool, args: changed, scope: 'port restriction', status: 'running' };
  summary.cases.push(record);
  // Upstream native permits more workers. This intentionally separate case
  // checks the browser build's contract, not native-equivalent rejection.
  const kinds = tool === 'kallisto' ? ['patched-native', 'wasm'] : ['wasm'];
  const expected = tool === 'kallisto' ? 'requires exactly one thread' : 'this WebAssembly build supports one compute worker; use --threads 1';
  for (const kind of kinds) {
    const { result } = await run(tool, kind, changed, [...files, ...indexes[kind]], configs[tool].outputs, [], id);
    if (result.exitCode !== 1 || !Buffer.from(result.stderr).toString().includes(expected) || result.files.length) throw new Error(`${id} ${kind}: expected exit1 with explicit single-worker diagnostic and no quantification output`);
  }
  record.status = 'passed';
}

try {
  // This command verifies both public-source provenance and every checked-in
  // transcript/read checksum; no downloads occur during the comparison suite.
  execFileSync('python3', [join(root, 'scripts', 'fetch-quant-test-data.py'), '--verify'], { cwd: root, stdio: 'pipe' });
  summary.runtime = Object.fromEntries(await Promise.all(['runtime/index.mjs', 'runtime/worker.mjs', 'runtime/execute.mjs'].map(async path => [path, hash(await readFile(join(root, path)))])));
  summary.validation = Object.fromEntries(await Promise.all(['scripts/validate-quant.mjs', 'validation/quant/compare.py', 'validation/quant/tolerances.json'].map(async path => [path, hash(await readFile(join(root, path)))])));
  if (options.engine !== 'node') await startBrowser();
  for (const tool of requestedTools) {
    let firstDatasetFiles;
    const assets = {};
    for (const name of (await readdir(join(root, 'dist', tool))).filter(name => /\.(?:mjs|js|wasm)$/.test(name)).sort()) assets[name] = hash(await readFile(join(root, 'dist', tool, name)));
    summary.builds[tool] = { native: hash(await readFile(join(root, 'build', 'native', tool))),
      'patched-native': hash(await readFile(join(root, 'build', 'patched-native', tool))),
      wasm: assets[`${tool}.wasm`], module: assets[`${tool}.mjs`], assets };
    if (!summary.builds[tool].wasm || !summary.builds[tool].module) throw new Error(`${tool}: missing compiled module or WASM`);
    for (const dataset of datasets) {
      const { files, processed } = await loadDataset(dataset), config = configs[tool];
      firstDatasetFiles ??= files;
      const indexes = await indexCase(tool, dataset, files, 31);
      let baseline, singleBaseline;
      for (const [name, extra, single] of config.cases) {
        const reads = single ? (tool === 'kallisto' ? ['reads_1.fastq'] : ['-r', 'reads_1.fastq']) : config.paired;
        const args = [...config.base, ...extra, ...reads];
        const result = await quantCase(tool, dataset, name, args, files, indexes, processed);
        if (name === 'paired') baseline = { args, result };
        if (name === 'single') singleBaseline = result;
        if (['fragment-prior', 'fr-stranded', 'rf-stranded', 'sequence-bias', 'em', 'bias'].includes(name)) {
          const previous = name === 'fragment-prior' ? singleBaseline : baseline.result;
          const table = tool === 'kallisto' ? 'abundance.tsv' : 'quant.sf';
          const digest = result => hash(result.files.find(file => file.path.endsWith(`/${table}`)).data);
          const changed = digest(previous.native.result) !== digest(result.native.result);
          const record = summary.cases.at(-1);
          record.nativeTableChangedByOption = changed;
          let mappingChanged = false;
          if (tool === 'salmon') {
            const meta = entry => JSON.parse(Buffer.from(entry.result.files.find(file => file.path.endsWith('/meta_info.json')).data).toString());
            const before = meta(previous.native), after = meta(result.native);
            mappingChanged = ['num_mapped', 'num_eq_classes', 'num_orphan'].some(key => before[key] !== after[key]);
            record.nativeMappingChangedByOption = mappingChanged;
            if (name === 'bias') {
              for (const kind of ['native', 'patched-native', 'wasm']) {
                const value = meta(result[kind]);
                if (!['seq_bias_correct', 'gc_bias_correct', 'pos_bias_correct'].every(key => value[key] === true) || !(value.seq_bias_context_length > 0 && value.gc_bias_bins > 0 && value.pos_bias_bins?.length_classes > 0 && value.pos_bias_bins?.bins_per_class > 0)) throw new Error(`${tool}-${dataset}-bias ${kind}: bias models were not populated`);
              }
            }
          }
          // Correct-strand Salmon yeast has unchanged rounded final estimates,
          // but the actual equivalence-class count changes from 17 to 14.
          // Require a numerical effect, not just a recorded option string.
          if (!changed && !mappingChanged) throw new Error(`${tool}-${dataset}-${name}: native option no longer changes quantification or mapping`);
        }
      }
      const smallIndexes = await indexCase(tool, dataset, files, 21);
      await quantCase(tool, dataset, 'k21', baseline.args, files, smallIndexes, processed);
      for (let repeat = 1; repeat <= 2; repeat++) {
        const id = `${tool}-${dataset}-repeat-${repeat}`, record = { id, tool, args: baseline.args, status: 'running' };
        summary.cases.push(record);
        const { result, directory } = await run(tool, 'wasm', baseline.args, [...files, ...indexes.wasm], config.outputs, [], id);
        success(result, id);
        record.comparison = await compare(tool, dataset, baseline.result.native.directory, directory, processed, id, 'wasm');
        record.status = 'passed';
      }
      if (config.portableIndex) {
        const cross = { native: indexes.wasm, 'patched-native': indexes.wasm, wasm: indexes.native };
        const results = await quantCase(tool, dataset, 'cross-index', baseline.args, files, cross, processed);
        const record = summary.cases.at(-1);
        for (const kind of ['native', 'patched-native', 'wasm']) record.comparisons[`own-index-baseline-${kind}`] = await compare(tool, dataset, baseline.result.native.directory, results[kind].directory, processed, record.id, `own-index-baseline-${kind}`);
      }
      if (dataset === datasets[0]) {
        const singleArgs = [...config.base, ...config.single];
        await failureCase(tool, 'missing-read', singleArgs, files.filter(f => f.path !== 'reads_1.fastq'), indexes);
        const bad = tool === 'kallisto' ? [...config.base, '--single', '-l', '200', '-s', '-1', 'reads_1.fastq'] : [...config.base, '-l', 'U', '--fldMean', '200', '--fldSD', 'not-a-number', '-r', 'reads_1.fastq'];
        await failureCase(tool, 'invalid-fragment-prior', bad, files, indexes);
        const firstRead = Buffer.from(files.find(file => file.path === 'reads_1.fastq').data).toString().split('\n').slice(0, 4);
        firstRead[3] = firstRead[3].slice(0, 1); // Preserve sequence, break quality length.
        const malformedFiles = files.map(file => file.path === 'reads_1.fastq' ? { path: file.path, data: Buffer.from(firstRead.join('\n') + '\n') } : file);
        await failureCase(tool, 'malformed-fastq', singleArgs, malformedFiles, indexes, { allowFailedOutput: tool === 'kallisto' });
        await threadRestrictionCase(tool, baseline.args, files, indexes);
      }
    }
    const config = configs[tool];
    await failureCase(tool, 'missing-index', [...config.base, ...config.single], firstDatasetFiles);
  }
  const actualIds = summary.cases.map(record => record.id);
  if (new Set(actualIds).size !== actualIds.length || JSON.stringify([...actualIds].sort()) !== JSON.stringify([...summary.expectedCases].sort()) || summary.cases.some(record => record.status !== 'passed')) throw new Error('Completed cases do not match the full planned case set');
  summary.status = 'passed';
} catch (error) {
  summary.status = 'failed';
  summary.error = error.stack;
  const current = summary.cases.findLast(record => record.status === 'running');
  if (current) { current.status = 'failed'; current.error = error.message; }
  console.error(error.stack);
  process.exitCode = 1;
} finally {
  summary.finishedAt = new Date().toISOString();
  await writeFile(join(reportDir, `summary-${options.tool}-${options.dataset}.json`), JSON.stringify(summary, null, 2) + '\n');
  if (browser) await browser.close();
  if (server) await new Promise(resolve => server.close(resolve));
}
