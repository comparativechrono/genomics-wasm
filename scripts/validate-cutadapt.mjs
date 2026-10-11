#!/usr/bin/env node
/** Real-data Cutadapt native / patched-native / Python-WebAssembly validation. */
import { readFile, writeFile, mkdir, rm, readdir } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { execFileSync, spawnSync } from 'node:child_process';
import { createServer } from 'node:http';
import { gunzipSync } from 'node:zlib';
import { dirname, resolve, join, relative, sep } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { workspacePath } from '../runtime/index.mjs';
import { CutadaptRunner } from '../runtime/cutadapt.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const options = { engine: 'node', dataset: 'all' };
const usage = 'validate-cutadapt.mjs [--engine node|chromium|firefox|webkit] [--dataset human|yeast|all]';
for (let i = 2; i < process.argv.length; i += 2) {
  const key = process.argv[i].replace(/^--/, '');
  if (!(key in options) || !process.argv[i + 1]) throw new Error(usage);
  options[key] = process.argv[i + 1];
}
if (!['node', 'chromium', 'firefox', 'webkit'].includes(options.engine) || !['all', 'human', 'yeast'].includes(options.dataset)) throw new Error(usage);
const plan = JSON.parse(await readFile(join(root, 'validation/cutadapt/cases.json'), 'utf8'));
const datasets = options.dataset === 'all' ? plan.datasets : [options.dataset];
const reportDir = join(root, 'reports/cutadapt', options.engine);
await mkdir(reportDir, { recursive: true });
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const applies = (test, dataset) => !test.datasets || test.datasets.includes(dataset);
const caseId = (dataset, test) => test.oncePerTool ? `cutadapt-${test.id}` : `cutadapt-${dataset}-${test.id}`;
const normalize = file => ({ ...file, path: file.path.replace(/^\/work\//, '') });
const timeoutMs = 600000;
const summary = { schema: 1, tool: 'cutadapt', engine: options.engine, node: process.version,
  startedAt: new Date().toISOString(), status: 'running', builds: {}, datasets: {}, cases: [],
  expectedCases: datasets.flatMap((dataset, index) => plan.cases.filter(test => applies(test, dataset) && (!test.oncePerTool || index === 0)).map(test => caseId(dataset, test))) };
let browser, server, page, manifest;
function log(message) { console.log(`[${new Date().toISOString()}] ${options.engine} ${message}`); }
const sameMap = (left, right) => JSON.stringify(Object.entries(left).sort()) === JSON.stringify(Object.entries(right).sort());

async function treeHashes(base, prefix = '') {
  const hashes = {};
  for (const entry of (await readdir(join(base, prefix), { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
    const path = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.isDirectory()) Object.assign(hashes, await treeHashes(base, path));
    else if (entry.isFile()) hashes[path] = hash(await readFile(join(base, path)));
    else throw new Error(`Build environment contains a non-regular file: ${join(base, path)}`);
  }
  return hashes;
}

async function verifyNativeEnvironments(build) {
  const environments = build.nativeEnvironments;
  if (!environments || JSON.stringify(Object.keys(environments).sort()) !== JSON.stringify(['native', 'patched-native'])) throw new Error('Both native environments must be declared');
  const python = JSON.parse(execFileSync(process.env.CUTADAPT_PYTHON || 'python3.14', ['-I', '-B', '-c',
    'import json,platform; print(json.dumps({"version": platform.python_version(), "implementation": platform.python_implementation()}))'], { encoding: 'utf8' }));
  if (python.version !== plan.pythonVersion || python.implementation !== 'CPython') throw new Error('Selected native Python differs from the pinned browser Python');
  summary.nativePython = python;
  for (const [kind, environment] of Object.entries(environments)) {
    if (environment.python?.version !== python.version || environment.python?.implementation !== python.implementation) throw new Error(`${kind}: recorded Python identity differs from the selected interpreter`);
    const base = join(root, 'build', kind), actual = {};
    for (const entry of (await readdir(base, { withFileTypes: true })).filter(entry => entry.name.startsWith('cutadapt'))) {
      if (entry.isDirectory()) {
        for (const [path, value] of Object.entries(await treeHashes(join(base, entry.name)))) actual[`${entry.name}/${path}`] = value;
      } else if (entry.isFile()) actual[entry.name] = hash(await readFile(join(base, entry.name)));
      else throw new Error(`${kind}: native environment contains a non-regular file`);
    }
    if (!actual.cutadapt || !actual['cutadapt-entry.py'] || !Object.keys(actual).some(path => path.startsWith('cutadapt-packages/')) ||
        !sameMap(actual, environment.files ?? {}) || environment.launcher !== actual.cutadapt) throw new Error(`${kind}: installed native package tree differs from its build manifest`);
  }
}

async function startBrowser() {
  const playwright = await import('playwright');
  browser = await playwright[options.engine].launch({ headless: true });
  summary.browserVersion = browser.version();
  server = createServer(async (request, response) => {
    try {
      const url = new URL(request.url, 'http://localhost');
      if (url.pathname === '/') {
        response.setHeader('Content-Type', 'text/html');
        response.end('<!doctype html><meta charset="utf-8"><title>Cutadapt validation</title>'); return;
      }
      const path = resolve(root, '.' + decodeURIComponent(url.pathname));
      if (!path.startsWith(root + sep) || !['runtime/', 'dist/cutadapt/'].some(prefix => relative(root, path).startsWith(prefix))) { response.writeHead(404).end(); return; }
      response.setHeader('Content-Type', path.endsWith('.wasm') ? 'application/wasm' : /\.(mjs|js)$/.test(path) ? 'text/javascript' : path.endsWith('.json') ? 'application/json' : 'application/octet-stream');
      response.end(await readFile(path));
    } catch { response.writeHead(404).end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  page = await browser.newPage();
  page.on('console', message => log(`browser ${message.type()}: ${message.text()}`));
  page.on('pageerror', error => log(`browser error: ${error.message}`));
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  summary.crossOriginIsolated = await page.evaluate(() => crossOriginIsolated);
  if (summary.crossOriginIsolated) throw new Error('Cutadapt browser test must exercise ordinary hosting without cross-origin isolation');
}

async function wasm(args, files, outputs, stdin) {
  if (options.engine === 'node') return new CutadaptRunner({ baseUrl: pathToFileURL(join(root, 'dist/cutadapt/')) }).run(args, { files, outputs, stdin, timeoutMs });
  const result = await page.evaluate(async ({ args, files, outputs, stdin, timeoutMs }) => {
    const { CutadaptRunner } = await import('/runtime/cutadapt.mjs');
    const encode = bytes => {
      let binary = '';
      for (let offset = 0; offset < bytes.length; offset += 32768) binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768));
      return btoa(binary);
    };
    const runner = new CutadaptRunner({ baseUrl: new URL('/dist/cutadapt/', location.href) });
    const result = await runner.run(args, { files: files.map(file => ({ path: file.path, data: Uint8Array.from(atob(file.data), char => char.charCodeAt(0)) })), outputs,
      stdin: stdin === null ? undefined : Uint8Array.from(atob(stdin), char => char.charCodeAt(0)), timeoutMs });
    return { ...result, stdout: encode(result.stdout), stderr: encode(result.stderr), files: result.files.map(file => ({ path: file.path, data: encode(file.data) })) };
  }, { args, outputs, timeoutMs, stdin: stdin === undefined ? null : Buffer.from(stdin).toString('base64'), files: files.map(file => ({ path: file.path, data: Buffer.from(file.data).toString('base64') })) });
  return { ...result, stdout: Buffer.from(result.stdout, 'base64'), stderr: Buffer.from(result.stderr, 'base64'), files: result.files.map(file => ({ path: file.path, data: Buffer.from(file.data, 'base64') })) };
}

async function native(kind, args, files, outputs, id, stdin) {
  const work = join(root, 'build/cutadapt-validation', options.engine, id, kind);
  await rm(work, { recursive: true, force: true });
  await mkdir(work, { recursive: true });
  for (const file of files) {
    const path = join(work, workspacePath(file.path).slice(6));
    await mkdir(dirname(path), { recursive: true });
    await writeFile(path, file.data);
  }
  const command = spawnSync(join(root, 'build', kind, 'cutadapt'), args, { cwd: work,
    maxBuffer: 64 * 1024 * 1024, timeout: timeoutMs, input: stdin, stdio: [stdin === undefined ? 'ignore' : 'pipe', 'pipe', 'pipe'],
    env: { ...process.env, LC_ALL: 'C', TZ: 'UTC' } });
  if (command.error) throw command.error;
  if (!Number.isInteger(command.status) || command.signal) throw new Error(`cutadapt ${kind} terminated by ${command.signal}`);
  const result = { exitCode: command.status, signal: null, stdout: command.stdout, stderr: command.stderr, files: [], missingOutputs: [] };
  for (const path of outputs) {
    try { result.files.push({ path, data: await readFile(join(work, path)) }); }
    catch (error) { if (error.code !== 'ENOENT') throw error; result.missingOutputs.push(path); }
  }
  return result;
}

async function run(kind, test, files, id, stdin) {
  log(`${id} ${kind} starting`);
  const start = Date.now();
  const result = kind === 'wasm' ? await wasm(test.args, files, test.outputs, stdin) : await native(kind, test.args, files, test.outputs, id, stdin);
  if (!Number.isInteger(result.exitCode) || result.signal) throw new Error(`${id} ${kind}: missing normal process exit`);
  if (test.stdoutFile) result.files.push({ path: test.stdoutFile, data: result.stdout });
  const directory = join(reportDir, id, kind);
  await rm(directory, { recursive: true, force: true });
  await mkdir(directory, { recursive: true });
  await writeFile(join(directory, 'stdout.txt'), result.stdout);
  await writeFile(join(directory, 'stderr.txt'), result.stderr);
  await writeFile(join(directory, 'command-result.json'), JSON.stringify({ exitCode: result.exitCode, signal: null, missingOutputs: result.missingOutputs }, null, 2) + '\n');
  for (const file of result.files.map(normalize)) {
    const path = join(directory, workspacePath(file.path).slice(6));
    await mkdir(dirname(path), { recursive: true });
    await writeFile(path, file.data);
  }
  const execution = { exitCode: result.exitCode, canonicalExitCode: result.exitCode < 0 ? result.exitCode & 255 : result.exitCode,
    signal: null, missingOutputs: result.missingOutputs, seconds: (Date.now() - start) / 1000,
    stdout: hash(result.stdout), stderr: hash(result.stderr), files: Object.fromEntries(result.files.map(normalize).map(file => [file.path, hash(file.data)])) };
  log(`${id} ${kind} exit ${result.exitCode} after ${execution.seconds.toFixed(2)} seconds`);
  return { result, directory, execution };
}

async function compare(dataset, test, expected, actual, id, kind) {
  const report = join(reportDir, id, `compare-${kind}.json`);
  try {
    execFileSync('python3', [join(root, 'validation/cutadapt/compare.py'), expected, actual,
      '--manifest', join(root, 'datasets/cutadapt/manifest.json'), '--dataset', dataset,
      '--case', test.id, '--report', report], { maxBuffer: 8 * 1024 * 1024 });
  } catch (error) { throw new Error(`Cutadapt mismatch: ${id} ${kind}; see ${relative(root, report)}\n${String(error.stdout ?? '').slice(0, 5000)}`); }
  return JSON.parse(await readFile(report, 'utf8'));
}

async function loadDataset(dataset) {
  const files = [], hashes = {};
  for (const [path, spec] of Object.entries(manifest.files).filter(([path]) => path.startsWith(dataset + '/')).sort()) {
    const local = path.slice(dataset.length + 1); workspacePath(local);
    const bytes = await readFile(join(root, 'datasets/cutadapt', path));
    if (hash(bytes) !== spec.sha256 || bytes.length !== spec.bytes) throw new Error(`${dataset}: checksum or size mismatch for ${path}`);
    files.push({ path: local, data: bytes }); hashes[path] = hash(bytes);
  }
  const data = manifest.datasets[dataset];
  const inputBases = [];
  let readPairs;
  for (const mate of [1, 2]) {
    const raw = await readFile(join(root, 'datasets/cutadapt', data[`read${mate}`]));
    const zipped = gunzipSync(await readFile(join(root, 'datasets/cutadapt', data[`read${mate}Gzip`])));
    if (!raw.equals(zipped)) throw new Error(`${dataset}: gzip mate ${mate} differs from plain FASTQ`);
    const rows = raw.toString('ascii').trimEnd().split(/\r?\n/);
    if (rows.length % 4 || !rows.length || rows.some((line, index) => index % 4 === 0 && !line.startsWith('@'))) throw new Error(`${dataset}: malformed source FASTQ`);
    const count = rows.length / 4;
    if (readPairs !== undefined && readPairs !== count) throw new Error(`${dataset}: unequal source mate counts`);
    readPairs = count;
    inputBases.push(rows.filter((_, index) => index % 4 === 1).reduce((sum, sequence) => sum + sequence.length, 0));
  }
  summary.datasets[dataset] = { manifestSha256: summary.manifestSha256, files: hashes, observations: data.observations, readPairs, inputBases };
  return files;
}

async function executeCase(dataset, test, files, cache) {
  const id = caseId(dataset, test), results = {};
  const record = { id, tool: 'cutadapt', dataset, case: test.id, kind: test.kind, args: test.args, status: 'running', executions: {}, comparisons: {} };
  summary.cases.push(record);
  const fixtures = Object.entries(test.fixtures ?? {}).map(([path, text]) => ({ path, data: Buffer.from(text) }));
  const stdin = test.stdinKey ? await readFile(join(root, 'datasets/cutadapt', manifest.datasets[dataset][test.stdinKey])) : undefined;
  for (const kind of test.executions) {
    results[kind] = await run(kind, test, [...files, ...fixtures], id, stdin);
    record.executions[kind] = results[kind].execution;
    const result = results[kind].result;
    if (['failure', 'restriction'].includes(test.kind)) {
      const diagnostic = Buffer.from(result.stderr).toString() + Buffer.from(result.stdout).toString();
      if (result.exitCode === 0 || !new RegExp(test.diagnosticRegex, 'i').test(diagnostic)) throw new Error(`${id} ${kind}: expected nonzero exit and planned diagnostic`);
      record.executions[kind].diagnosticMatched = true;
    } else if (result.exitCode !== 0 || result.missingOutputs.length) throw new Error(`${id} ${kind}: exit ${result.exitCode}, missing ${result.missingOutputs.join(', ')}\n${Buffer.from(result.stderr).toString().slice(-4000)}`);
  }
  if (['failure', 'restriction'].includes(test.kind)) {
    if (new Set(Object.values(record.executions).map(value => value.canonicalExitCode)).size !== 1) throw new Error(`${id}: failure exit codes differ`);
  } else {
    const baseline = test.kind === 'repeat' ? cache.get(test.baseline) : null;
    if (test.kind === 'repeat' && !baseline) throw new Error(`${id}: missing repeat baseline`);
    const expected = baseline ? baseline.results.native.directory : results.native.directory;
    for (const kind of test.comparisons) {
      const actual = kind === 'native-contract' ? results.native.directory : results[kind].directory;
      record.comparisons[kind] = await compare(dataset, test, expected, actual, id, kind);
      if (!record.comparisons[kind].equal) throw new Error(`${id}: unequal ${kind}`);
    }
    if (test.effect || test.equivalentTo) {
      const other = cache.get(test.effect ?? test.equivalentTo);
      if (!other) throw new Error(`${id}: missing option baseline`);
      const before = other.record.comparisons['native-contract'].biologicalDigest;
      const after = record.comparisons['native-contract'].biologicalDigest;
      record.optionEffect = { baseline: test.effect ?? test.equivalentTo, changed: before !== after, expected: test.effect ? 'changed' : 'equivalent', before, after };
      if (test.effect ? before === after : before !== after) throw new Error(`${id}: option effect does not match plan`);
    }
  }
  record.status = 'passed'; log(`PASS ${id}`);
  return { record, results };
}

try {
  const manifestBytes = await readFile(join(root, 'datasets/cutadapt/manifest.json'));
  summary.manifestSha256 = hash(manifestBytes);
  manifest = JSON.parse(manifestBytes);
  for (const [path, spec] of Object.entries(manifest.files)) {
    const bytes = await readFile(join(root, 'datasets/cutadapt', path));
    if (hash(bytes) !== spec.sha256 || bytes.length !== spec.bytes) throw new Error(`Dataset hash/size mismatch: ${path}`);
  }
  summary.runtime = Object.fromEntries(await Promise.all(['runtime/index.mjs', 'runtime/cutadapt.mjs', 'runtime/cutadapt-worker.mjs', 'runtime/cutadapt-execute.mjs'].map(async path => [path, hash(await readFile(join(root, path)))])));
  summary.validation = Object.fromEntries(await Promise.all(['scripts/validate-cutadapt.mjs', 'validation/cutadapt/compare.py', 'validation/cutadapt/cases.json'].map(async path => [path, hash(await readFile(join(root, path)))])));
  const buildBytes = await readFile(join(root, 'dist/cutadapt/build-manifest.json'));
  const build = JSON.parse(buildBytes), assets = {};
  if (build.schema !== 1 || build.tool !== 'cutadapt' || !Object.keys(build.inputs ?? {}).length) throw new Error('Invalid or incomplete Cutadapt build manifest');
  for (const [path, expected] of Object.entries(build.inputs)) {
    workspacePath(path);
    if (hash(await readFile(join(root, path))) !== expected) throw new Error(`Cutadapt source input mismatch: ${path}`);
  }
  for (const [path, expected] of Object.entries(build.assets)) {
    workspacePath(path);
    const actual = hash(await readFile(join(root, 'dist/cutadapt', path)));
    if (actual !== (typeof expected === 'string' ? expected : expected.sha256)) throw new Error(`Cutadapt build asset mismatch: ${path}`);
    assets[path] = actual;
  }
  const completeAssets = await treeHashes(join(root, 'dist/cutadapt'));
  delete completeAssets['build-manifest.json'];
  if (!sameMap(assets, completeAssets)) throw new Error('Cutadapt build manifest does not identify the complete distribution');
  await verifyNativeEnvironments(build);
  summary.builds.cutadapt = { assets, buildManifest: hash(buildBytes), manifest: hash(await readFile(join(root, 'dist/cutadapt/manifest.json'))),
    native: hash(await readFile(join(root, 'build/native/cutadapt'))), 'patched-native': hash(await readFile(join(root, 'build/patched-native/cutadapt'))), nativeEnvironments: build.nativeEnvironments };
  if (!build.nativeEnvironments || !Object.keys(assets).length) throw new Error('Incomplete Cutadapt build/environment manifest');
  if (options.engine !== 'node') await startBrowser();
  for (const [index, dataset] of datasets.entries()) {
    const files = await loadDataset(dataset), cache = new Map();
    for (const test of plan.cases.filter(test => applies(test, dataset) && (!test.oncePerTool || index === 0))) cache.set(test.id, await executeCase(dataset, test, files, cache));
  }
  const ids = summary.cases.map(record => record.id);
  if (new Set(ids).size !== ids.length || JSON.stringify([...ids].sort()) !== JSON.stringify([...summary.expectedCases].sort()) || summary.cases.some(record => record.status !== 'passed')) throw new Error('Completed cases do not match the complete Cutadapt case plan');
  summary.status = 'passed';
} catch (error) {
  summary.status = 'failed'; summary.error = error.stack;
  const current = summary.cases.findLast(record => record.status === 'running');
  if (current) { current.status = 'failed'; current.error = error.message; }
  console.error(error.stack); process.exitCode = 1;
} finally {
  summary.finishedAt = new Date().toISOString();
  const output = join(reportDir, `summary-${options.dataset}.json`);
  await writeFile(output, JSON.stringify(summary, null, 2) + '\n');
  let cleanupTimer;
  try {
    if (browser) await Promise.race([browser.close(), new Promise((_, reject) => { cleanupTimer = setTimeout(() => reject(new Error('Browser cleanup exceeded 30 seconds')), 30000); })]);
  } catch (error) { summary.status = 'failed'; summary.cleanupError = error.message; process.exitCode = 1; }
  finally { clearTimeout(cleanupTimer); }
  if (server) { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
  await writeFile(output, JSON.stringify(summary, null, 2) + '\n');
  if (summary.cleanupError) process.exit(1);
}
