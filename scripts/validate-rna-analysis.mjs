#!/usr/bin/env node
/** Real-data native / patched-native / WebAssembly RNA analysis validation. */
import { readFile, writeFile, mkdir, readdir, rm, lstat } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { execFileSync, spawnSync } from 'node:child_process';
import { createServer } from 'node:http';
import { dirname, resolve, join, relative, sep } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { ToolRunner, workspacePath } from '../runtime/index.mjs';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const options = { tool: 'all', engine: 'node', dataset: 'all' };
const usage = 'validate-rna-analysis.mjs [--tool featurecounts|stringtie|mosdepth|all] [--engine node|chromium|firefox|webkit] [--dataset human|yeast|all]';
for (let i = 2; i < process.argv.length; i += 2) {
  const key = process.argv[i].replace(/^--/, '');
  if (!(key in options) || !process.argv[i + 1]) throw new Error(usage);
  options[key] = process.argv[i + 1];
}
if (!['all', 'featurecounts', 'stringtie', 'mosdepth'].includes(options.tool) || !['node', 'chromium', 'firefox', 'webkit'].includes(options.engine) || !['all', 'human', 'yeast'].includes(options.dataset)) throw new Error(usage);
const plan = JSON.parse(await readFile(join(root, 'validation/rna-analysis/cases.json'), 'utf8'));
const requestedTools = options.tool === 'all' ? Object.keys(plan.tools) : [options.tool];
const datasets = options.dataset === 'all' ? plan.datasets : [options.dataset];
const reportDir = join(root, 'reports', 'rna-analysis', options.engine);
await mkdir(reportDir, { recursive: true });
const summary = { schema: 1, startedAt: new Date().toISOString(), engine: options.engine, node: process.version,
  status: 'running', builds: {}, datasets: {}, cases: [] };
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const timeoutMs = 600000;
const normalized = file => ({ ...file, path: file.path.replace(/^\/work\//, '') });
const applies = (test, dataset) => !test.datasets || test.datasets.includes(dataset);
const caseId = (tool, dataset, test) => test.oncePerTool ? `${tool}-${test.id}` : `${tool}-${dataset}-${test.id}`;
summary.expectedCases = requestedTools.flatMap(tool => datasets.flatMap((dataset, i) => plan.tools[tool].cases.filter(test => applies(test, dataset) && (!test.oncePerTool || i === 0)).map(test => caseId(tool, dataset, test))));
let browser, server, page, manifest;
function log(message) { console.log(`[${new Date().toISOString()}] ${options.engine} ${message}`); }
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
      if (url.pathname === '/') { response.setHeader('Content-Type', 'text/html'); response.end('<!doctype html><meta charset="utf-8"><title>RNA analysis validation</title>'); return; }
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
  const waitForExit = plan.tools[tool].waitForExit;
  if (options.engine === 'node') return new ToolRunner({ moduleUrl: pathToFileURL(join(root, 'dist', tool, `${tool}.mjs`)), waitForExit }).run(args, settings);
  const result = await page.evaluate(async ({ tool, args, files, outputs, outputDirectories, timeoutMs, waitForExit }) => {
    const { ToolRunner } = await import('/runtime/index.mjs');
    const encode = bytes => {
      let binary = '';
      for (let offset = 0; offset < bytes.length; offset += 32768) binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768));
      return btoa(binary);
    };
    const runner = new ToolRunner({ moduleUrl: new URL(`/dist/${tool}/${tool}.mjs`, location.href), waitForExit });
    const result = await runner.run(args, { files: files.map(f => ({ path: f.path, data: Uint8Array.from(atob(f.data), c => c.charCodeAt(0)) })), outputs, outputDirectories, timeoutMs });
    return { ...result, stdout: encode(result.stdout), stderr: encode(result.stderr), files: result.files.map(f => ({ path: f.path, data: encode(f.data) })) };
  }, { tool, args, outputs, outputDirectories, timeoutMs, waitForExit, files: files.map(f => ({ path: f.path, data: Buffer.from(f.data).toString('base64') })) });
  return { ...result, stdout: Buffer.from(result.stdout, 'base64'), stderr: Buffer.from(result.stderr, 'base64'), files: result.files.map(f => ({ path: f.path, data: Buffer.from(f.data, 'base64') })) };
}

async function native(tool, kind, args, files, outputs = [], outputDirectories = [], id = 'command', allowedSignals = []) {
  const work = join(root, 'build', 'rna-analysis-validation', options.engine, tool, id, kind);
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
  if (!Number.isInteger(processResult.status) && !allowedSignals.includes(processResult.signal)) throw new Error(`${tool} ${kind} terminated by ${processResult.signal}`);
  const result = { exitCode: processResult.status, signal: processResult.signal, stdout: processResult.stdout, stderr: processResult.stderr, files: [], missingOutputs: [] };
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
  await writeFile(join(directory, 'command-result.json'), JSON.stringify({ exitCode: result.exitCode, signal: result.signal ?? null, missingOutputs: result.missingOutputs }, null, 2) + '\n');
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

async function compare(tool, dataset, test, expectedDirectory, actualDirectory, id, kind) {
  const report = join(reportDir, id, `compare-${kind}.json`);
  const args = [join(root, 'validation/rna-analysis/compare.py'), tool, expectedDirectory, actualDirectory,
    '--manifest', join(root, 'datasets/rna-analysis/manifest.json'), '--dataset', dataset,
    '--case', test.id, '--report', report];
  try { execFileSync('python3', args, { maxBuffer: 8 * 1024 * 1024 }); }
  catch (error) { throw new Error(`RNA analysis mismatch: ${id} ${kind}; see ${relative(root, report)}\n${String(error.stdout ?? '').slice(0, 5000)}`); }
  return JSON.parse(await readFile(report, 'utf8'));
}
async function run(tool, kind, args, files, outputs, id, allowedSignals = []) {
  log(`${id} ${kind} starting`);
  const started = Date.now();
  const result = kind === 'wasm' ? await wasm(tool, args, files, outputs) : await native(tool, kind, args, files, outputs, [], id, allowedSignals);
  const directory = await saveResult(id, kind, result);
  const execution = { exitCode: result.exitCode, signal: result.signal ?? null, canonicalExitCode: result.exitCode < 0 ? (result.exitCode & 255) : result.exitCode,
    missingOutputs: result.missingOutputs, seconds: (Date.now() - started) / 1000,
    stdout: hash(result.stdout), stderr: hash(result.stderr), files: Object.fromEntries(result.files.map(normalized).map(file => [file.path, hash(file.data)])) };
  log(`${id} ${kind} exit ${result.exitCode} after ${execution.seconds.toFixed(2)} seconds`);
  return { result, directory, execution };
}
async function loadDataset(dataset) {
  const files = [], hashes = {};
  for (const [path, spec] of Object.entries(manifest.files).filter(([path]) => path.startsWith(dataset + '/')).sort()) {
    const local = path.slice(dataset.length + 1); workspacePath(local);
    const bytes = await readFile(join(root, 'datasets/rna-analysis', path));
    if (hash(bytes) !== spec.sha256 || bytes.length !== spec.bytes) throw new Error(`${dataset}: checksum or size mismatch for ${path}`);
    files.push({ path: local, data: bytes }); hashes[path] = hash(bytes);
  }
  const data = manifest.datasets[dataset];
  if (!data || files.length < 8 || !files.some(file => file.path === 'input.bam') || !files.some(file => file.path === 'input.sam')) throw new Error(`${dataset}: incomplete RNA dataset`);
  const sam = Buffer.from(files.find(file => file.path === 'input.sam').data).toString();
  const records = sam.split('\n').filter(line => line && !line.startsWith('@')).map(line => line.split('\t'));
  if (!records.length || !records.some(row => !(Number(row[1]) & 4)) || records.some(row => row.length < 11)) throw new Error(`${dataset}: no valid mapped SAM records`);
  summary.datasets[dataset] = { manifestSha256: hash(await readFile(join(root, 'datasets/rna-analysis/manifest.json'))),
    files: hashes, observations: data.observations, samRecords: records.length, mappedRecords: records.filter(row => !(Number(row[1]) & 4)).length };
  return files;
}
async function executeCase(tool, dataset, test, files, cache) {
  const id = caseId(tool, dataset, test), record = { id, tool, dataset, case: test.id, kind: test.kind,
    args: test.args, status: 'running', executions: {}, comparisons: {} };
  summary.cases.push(record);
  const results = {};
  for (const kind of test.executions) {
    const extra = (test.inputsFrom ?? []).map(input => {
      const source = cache.get(input.case)?.results[kind]?.result.files.map(normalized).find(file => file.path === input.path);
      if (!source) throw new Error(`${id}: missing ${kind} input from ${input.case}/${input.path}`);
      return { path: input.as, data: source.data };
    });
    results[kind] = await run(tool, kind, test.args, [...files, ...extra], test.outputs, id, kind === 'native' ? (test.nativeErrorPolicy?.allowedSignals ?? []) : []);
    record.executions[kind] = results[kind].execution;
    if (['failure', 'restriction'].includes(test.kind)) {
      const result = results[kind].result;
      if (result.exitCode === 0 || (!result.stderr.length && !result.stdout.length)) throw new Error(`${id} ${kind}: expected nonzero exit and a diagnostic`);
      if (test.nativeErrorPolicy && kind !== 'native' && (result.exitCode !== test.portExitCode || !Buffer.from(result.stderr).toString().includes(test.portDiagnostic))) throw new Error(`${id} ${kind}: port safety guard did not return the required clean diagnostic/exit`);
      if (test.nativeErrorPolicy && kind !== 'native') record.executions[kind].portDiagnosticMatched = true;
      if (test.kind === 'restriction' && !new RegExp(test.diagnosticRegex, 'i').test(Buffer.from(result.stderr).toString() + Buffer.from(result.stdout).toString())) throw new Error(`${id} ${kind}: missing explicit thread restriction diagnostic`);
    } else success(results[kind].result, `${id} ${kind}`);
  }
  if (['failure', 'restriction'].includes(test.kind)) {
    const normalExecutions = Object.entries(record.executions).filter(([kind]) => !(test.nativeErrorPolicy && kind === 'native')).map(([, execution]) => execution);
    if (test.nativeErrorPolicy) record.errorPolicyObserved = { reason: test.nativeErrorPolicy.reason, native: { exitCode: record.executions.native.exitCode, signal: record.executions.native.signal }, portExitCode: test.portExitCode, portDiagnostic: test.portDiagnostic };
    if (new Set(normalExecutions.map(value => value.canonicalExitCode)).size !== 1) throw new Error(`${id}: failure exit codes differ (negative C exit codes canonicalised modulo 256)`);
    record.status = 'passed'; return { record, results };
  }
  const baseline = test.kind === 'repeat' ? cache.get(test.baseline) : null;
  if (test.kind === 'repeat' && !baseline) throw new Error(`${id}: missing baseline`);
  const expectedDirectory = baseline ? baseline.results.native.directory : results.native.directory;
  for (const kind of test.comparisons) {
    const actual = kind === 'native-contract' ? results.native.directory : results[kind].directory;
    record.comparisons[kind] = await compare(tool, dataset, test, expectedDirectory, actual, id, kind);
    if (!record.comparisons[kind].equal) throw new Error(`${id}: unequal ${kind}`);
  }
  if (test.effect || test.equivalentTo) {
    const other = cache.get(test.effect ?? test.equivalentTo);
    if (!other) throw new Error(`${id}: missing option baseline`);
    const before = other.record.comparisons['native-contract'].biologicalDigest;
    const after = record.comparisons['native-contract'].biologicalDigest;
    record.optionEffect = { baseline: test.effect ?? test.equivalentTo, changed: before !== after,
      expected: test.effect ? 'changed' : 'equivalent', before, after };
    if (test.effect ? before === after : before !== after) throw new Error(`${id}: native output ${test.effect ? 'did not change under the tested option' : 'was not equivalent to its input-format/thread baseline'}`);
  }
  record.status = 'passed'; log(`PASS ${id}`);
  return { record, results };
}
try {
  execFileSync('python3', [join(root, 'scripts/fetch-rna-analysis-test-data.py'), '--verify'], { cwd: root, stdio: 'pipe' });
  manifest = JSON.parse(await readFile(join(root, 'datasets/rna-analysis/manifest.json'), 'utf8'));
  summary.manifestSha256 = hash(await readFile(join(root, 'datasets/rna-analysis/manifest.json')));
  summary.runtime = Object.fromEntries(await Promise.all(['runtime/index.mjs', 'runtime/worker.mjs', 'runtime/execute.mjs'].map(async path => [path, hash(await readFile(join(root, path)))])));
  summary.validation = Object.fromEntries(await Promise.all(['scripts/validate-rna-analysis.mjs', 'validation/rna-analysis/compare.py', 'validation/rna-analysis/cases.json'].map(async path => [path, hash(await readFile(join(root, path)))])));
  if (options.engine !== 'node') await startBrowser();
  for (const tool of requestedTools) {
    const assets = {};
    for (const name of (await readdir(join(root, 'dist', tool))).filter(name => /\.(?:mjs|js|wasm)$/.test(name)).sort()) assets[name] = hash(await readFile(join(root, 'dist', tool, name)));
    summary.builds[tool] = { native: hash(await readFile(join(root, 'build/native', tool))), 'patched-native': hash(await readFile(join(root, 'build/patched-native', tool))),
      module: assets[`${tool}.mjs`], wasm: assets[`${tool}.wasm`], assets };
    if (!summary.builds[tool].module || !summary.builds[tool].wasm) throw new Error(`${tool}: missing WASM module`);
    for (const [i, dataset] of datasets.entries()) {
      const files = await loadDataset(dataset), cache = new Map();
      for (const test of plan.tools[tool].cases.filter(test => applies(test, dataset) && (!test.oncePerTool || i === 0))) {
        cache.set(test.id, await executeCase(tool, dataset, test, files, cache));
      }

    }
  }
  const ids = summary.cases.map(record => record.id);
  if (new Set(ids).size !== ids.length || JSON.stringify([...ids].sort()) !== JSON.stringify([...summary.expectedCases].sort()) || summary.cases.some(record => record.status !== 'passed')) throw new Error('Completed cases do not match the full declared case plan');
  summary.status = 'passed';
} catch (error) {
  summary.status = 'failed'; summary.error = error.stack;
  const current = summary.cases.findLast(record => record.status === 'running');
  if (current) { current.status = 'failed'; current.error = error.message; }
  console.error(error.stack); process.exitCode = 1;
} finally {
  summary.finishedAt = new Date().toISOString();
  await writeFile(join(reportDir, `summary-${options.tool}-${options.dataset}.json`), JSON.stringify(summary, null, 2) + '\n');
  let cleanupTimer;
  try {
    if (browser) await Promise.race([browser.close(), new Promise((_, reject) => { cleanupTimer = setTimeout(() => reject(new Error('Browser cleanup exceeded 30 seconds')), 30000); })]);
  } catch (error) {
    summary.status = 'failed'; summary.cleanupError = error.message; process.exitCode = 1;
  } finally { clearTimeout(cleanupTimer); }
  if (server) { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
  await writeFile(join(reportDir, `summary-${options.tool}-${options.dataset}.json`), JSON.stringify(summary, null, 2) + '\n');
  if (summary.cleanupError) process.exit(1);
}
