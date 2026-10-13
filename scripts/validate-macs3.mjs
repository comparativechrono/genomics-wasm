#!/usr/bin/env node
/** Real-data MACS3 native / patched-native / Python-WebAssembly validation. */
import { readFile, writeFile, mkdir, rm, readdir, rename } from 'node:fs/promises';
import { createHash, randomUUID } from 'node:crypto';
import { execFileSync, spawnSync } from 'node:child_process';
import { createServer } from 'node:http';
import { dirname, resolve, join, relative, sep } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { workspacePath } from '../runtime/index.mjs';
import { MACS3Runner } from '../runtime/macs3.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const options = { engine: 'node', dataset: 'all' };
const usage = 'validate-macs3.mjs [--engine node|chromium|firefox|webkit] [--dataset macs3|all]';
for (let i = 2; i < process.argv.length; i += 2) {
  const key = process.argv[i].replace(/^--/, '');
  if (!(key in options) || !process.argv[i + 1]) throw new Error(usage);
  options[key] = process.argv[i + 1];
}
if (!['node', 'chromium', 'firefox', 'webkit'].includes(options.engine) || !['all', 'macs3'].includes(options.dataset)) throw new Error(usage);
const plan = JSON.parse(await readFile(join(root, 'validation/macs3/cases.json'), 'utf8'));
const datasets = options.dataset === 'all' ? plan.datasets : [options.dataset];
const runId = randomUUID();
const publishedDir = join(root, 'reports/macs3', options.engine);
const reportDir = join(publishedDir, runId);
await mkdir(reportDir, { recursive: true });
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const applies = (test, dataset) => !test.datasets || test.datasets.includes(dataset);
const caseId = (dataset, test) => test.oncePerTool ? `macs3-${test.id}` : `macs3-${dataset}-${test.id}`;
const normalize = file => ({ ...file, path: file.path.replace(/^\/work\//, '') });
const timeoutMs = 600000;
const summary = { schema: 1, tool: 'macs3', runId, reportDirectory: runId, engine: options.engine, node: process.version,
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
  const python = JSON.parse(execFileSync(process.env.MACS3_PYTHON || 'python3.14', ['-I', '-B', '-c',
    'import json,platform; print(json.dumps({"version": platform.python_version(), "implementation": platform.python_implementation()}))'], { encoding: 'utf8' }));
  if (python.version !== plan.pythonVersion || python.implementation !== 'CPython') throw new Error('Selected native Python differs from the pinned browser Python');
  summary.nativePython = python;
  for (const [kind, environment] of Object.entries(environments)) {
    if (environment.python?.version !== python.version || environment.python?.implementation !== python.implementation) throw new Error(`${kind}: recorded Python identity differs from the selected interpreter`);
    const base = join(root, 'build', kind), actual = {};
    for (const entry of (await readdir(base, { withFileTypes: true })).filter(entry => entry.name.startsWith('macs3'))) {
      if (entry.isDirectory()) {
        for (const [path, value] of Object.entries(await treeHashes(join(base, entry.name)))) actual[`${entry.name}/${path}`] = value;
      } else if (entry.isFile()) actual[entry.name] = hash(await readFile(join(base, entry.name)));
      else throw new Error(`${kind}: native environment contains a non-regular file`);
    }
    if (!actual.macs3 || !actual['macs3-entry.py'] || !Object.keys(actual).some(path => path.startsWith('macs3-packages/')) ||
        !sameMap(actual, environment.files ?? {}) || environment.launcher !== actual.macs3) throw new Error(`${kind}: installed native package tree differs from its build manifest`);
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
        response.end('<!doctype html><meta charset="utf-8"><title>MACS3 validation</title>'); return;
      }
      const path = resolve(root, '.' + decodeURIComponent(url.pathname));
      if (!path.startsWith(root + sep) || !['runtime/', 'dist/macs3/'].some(prefix => relative(root, path).startsWith(prefix))) { response.writeHead(404).end(); return; }
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
  if (summary.crossOriginIsolated) throw new Error('MACS3 browser test must exercise ordinary hosting without cross-origin isolation');
}

async function wasm(args, files, outputs, stdin) {
  if (options.engine === 'node') return new MACS3Runner({ baseUrl: pathToFileURL(join(root, 'dist/macs3/')) }).run(args, { files, outputs, stdin, timeoutMs });
  const result = await page.evaluate(async ({ args, files, outputs, stdin, timeoutMs }) => {
    const { MACS3Runner } = await import('/runtime/macs3.mjs');
    const encode = bytes => {
      let binary = '';
      for (let offset = 0; offset < bytes.length; offset += 32768) binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768));
      return btoa(binary);
    };
    const runner = new MACS3Runner({ baseUrl: new URL('/dist/macs3/', location.href) });
    const result = await runner.run(args, { files: files.map(file => ({ path: file.path, data: Uint8Array.from(atob(file.data), char => char.charCodeAt(0)) })), outputs,
      stdin: stdin === null ? undefined : Uint8Array.from(atob(stdin), char => char.charCodeAt(0)), timeoutMs });
    return { ...result, stdout: encode(result.stdout), stderr: encode(result.stderr), files: result.files.map(file => ({ path: file.path, data: encode(file.data) })) };
  }, { args, outputs, timeoutMs, stdin: stdin === undefined ? null : Buffer.from(stdin).toString('base64'), files: files.map(file => ({ path: file.path, data: Buffer.from(file.data).toString('base64') })) });
  return { ...result, stdout: Buffer.from(result.stdout, 'base64'), stderr: Buffer.from(result.stderr, 'base64'), files: result.files.map(file => ({ path: file.path, data: Buffer.from(file.data, 'base64') })) };
}

async function native(kind, args, files, outputs, id, stdin) {
  const work = join(root, 'build/macs3-validation', options.engine, id, kind);
  await rm(work, { recursive: true, force: true });
  await mkdir(work, { recursive: true });
  for (const file of files) {
    const path = join(work, workspacePath(file.path).slice(6));
    await mkdir(dirname(path), { recursive: true });
    await writeFile(path, file.data);
  }
  const command = spawnSync(join(root, 'build', kind, 'macs3'), args, { cwd: work,
    maxBuffer: 64 * 1024 * 1024, timeout: timeoutMs, input: stdin, stdio: [stdin === undefined ? 'ignore' : 'pipe', 'pipe', 'pipe'],
    env: { ...process.env, LC_ALL: 'C', TZ: 'UTC', OPENBLAS_NUM_THREADS: '1', OMP_NUM_THREADS: '1' } });
  if (command.error) throw command.error;
  if (!Number.isInteger(command.status) || command.signal) throw new Error(`macs3 ${kind} terminated by ${command.signal}`);
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
  const outputs = test.outputs.filter(path => !(test.stderrExtract && path === 'prediction.json'));
  const result = kind === 'wasm' ? await wasm(test.args, files, outputs, stdin) : await native(kind, test.args, files, outputs, id, stdin);
  if (test.stderrExtract && result.exitCode === 0) {
    const values = {};
    for (const [key, pattern] of Object.entries(test.stderrExtract)) {
      const matches = [...Buffer.from(result.stderr).toString().matchAll(new RegExp(pattern, 'g'))];
      if (matches.length !== 1) throw new Error(`${id}: absent or ambiguous scientific diagnostic ${key}`);
      values[key] = matches[0][1];
    }
    result.files.push({ path: 'prediction.json', data: Buffer.from(JSON.stringify(values) + '\n') });
  }
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
  const execution = { inputs: Object.fromEntries(files.map(file => [file.path, hash(file.data)])), fileBytes: Object.fromEntries(result.files.map(normalize).map(file => [file.path, file.data.length])), exitCode: result.exitCode, canonicalExitCode: result.exitCode < 0 ? result.exitCode & 255 : result.exitCode,
    signal: null, missingOutputs: result.missingOutputs, seconds: (Date.now() - start) / 1000,
    stdout: hash(result.stdout), stderr: hash(result.stderr), files: Object.fromEntries(result.files.map(normalize).map(file => [file.path, hash(file.data)])) };
  log(`${id} ${kind} exit ${result.exitCode} after ${execution.seconds.toFixed(2)} seconds`);
  await writeFile(join(directory, 'command-result.json'), JSON.stringify(execution, null, 2) + '\n');
  return { result, directory, execution };
}

async function compare(dataset, test, expected, actual, id, kind) {
  const report = join(reportDir, id, `compare-${kind}.json`);
  try {
    execFileSync('python3', [join(root, 'validation/macs3/compare.py'), expected, actual,
      '--manifest', join(root, 'datasets/macs3/manifest.json'), '--dataset', dataset,
      '--case', test.id, '--actual-target', kind, '--report', report], { maxBuffer: 8 * 1024 * 1024 });
  } catch (error) { throw new Error(`MACS3 mismatch: ${id} ${kind}; see ${relative(root, report)}\n${String(error.stdout ?? '').slice(0, 5000)}`); }
  return JSON.parse(await readFile(report, 'utf8'));
}

async function loadDataset(dataset) {
  const files = [], hashes = {};
  for (const [path, spec] of Object.entries(manifest.files).filter(([path]) => path.startsWith(dataset + '/')).sort()) {
    const local = path.slice(dataset.length + 1); workspacePath(local);
    const bytes = await readFile(join(root, 'datasets/macs3', path));
    if (hash(bytes) !== spec.sha256 || bytes.length !== spec.bytes) throw new Error(`${dataset}: checksum or size mismatch for ${path}`);
    files.push({ path: local, data: bytes }); hashes[path] = hash(bytes);
  }
  summary.datasets[dataset] = { manifestSha256: summary.manifestSha256, files: hashes, observations: manifest.datasets[dataset].observations };
  return files;
}

async function executeCase(dataset, test, files, cache) {
  const id = caseId(dataset, test), results = {};
  const record = { id, tool: 'macs3', dataset, case: test.id, kind: test.kind, args: test.args, status: 'running', executions: {}, comparisons: {} };
  summary.cases.push(record);
  const fixtures = Object.entries(test.fixtures ?? {}).map(([path, text]) => ({ path, data: Buffer.from(text) }));
  const stdin = test.stdinKey ? await readFile(join(root, 'datasets/macs3', manifest.datasets[dataset][test.stdinKey])) : undefined;
  for (const kind of test.executions) {
    const inputs = files.filter(file => test.inputFiles.includes(file.path));
    if (inputs.length !== test.inputFiles.length) throw new Error(`${id}: missing planned fixture input`);
    for (const dependency of test.dependencies ?? []) {
      const source = cache.get(dependency.case)?.results[kind]?.result.files.map(normalize).find(file => file.path === dependency.path);
      if (!source) throw new Error(`${id}: missing ${kind} dependency ${dependency.case}/${dependency.path}`);
      inputs.push({ path: dependency.as, data: source.data });
    }
    results[kind] = await run(kind, test, [...inputs, ...fixtures], id, stdin);
    results[kind].execution.inputsFrom = (test.dependencies ?? []).map(dependency => ({ ...dependency, sourceTarget: kind, sha256: hash(inputs.find(file => file.path === dependency.as).data) }));
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
      for (const [side, execution] of [['expected', (baseline ? baseline.results.native : results.native).execution], ['actual', (kind === 'native-contract' ? results.native : results[kind]).execution]]) {
        const hashes = Object.fromEntries(test.artifacts.map(a => [a.path, execution.files[a.path]]));
        const sizes = Object.fromEntries(test.artifacts.map(a => [a.path, execution.fileBytes[a.path]]));
        if (!sameMap(record.comparisons[kind][`${side}Files`], hashes) || !sameMap(record.comparisons[kind][`${side}FileBytes`], sizes)) throw new Error(`${id}: comparator input bytes differ from execution`);
      }
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
  const manifestBytes = await readFile(join(root, 'datasets/macs3/manifest.json'));
  summary.manifestSha256 = hash(manifestBytes);
  manifest = JSON.parse(manifestBytes);
  for (const [path, spec] of Object.entries(manifest.files)) {
    const bytes = await readFile(join(root, 'datasets/macs3', path));
    if (hash(bytes) !== spec.sha256 || bytes.length !== spec.bytes) throw new Error(`Dataset hash/size mismatch: ${path}`);
  }
  summary.runtime = Object.fromEntries(await Promise.all(['runtime/index.mjs', 'runtime/macs3.mjs', 'runtime/macs3-worker.mjs', 'runtime/macs3-execute.mjs'].map(async path => [path, hash(await readFile(join(root, path)))])));
  summary.validation = Object.fromEntries(await Promise.all(['scripts/validate-macs3.mjs', 'validation/macs3/compare.py', 'validation/macs3/cases.json'].map(async path => [path, hash(await readFile(join(root, path)))])));
  const buildBytes = await readFile(join(root, 'dist/macs3/build-manifest.json'));
  const build = JSON.parse(buildBytes), assets = {};
  if (build.schema !== 1 || build.tool !== 'macs3' || !Object.keys(build.inputs ?? {}).length) throw new Error('Invalid or incomplete MACS3 build manifest');
  for (const [path, expected] of Object.entries(build.inputs)) {
    workspacePath(path);
    if (hash(await readFile(join(root, path))) !== expected) throw new Error(`MACS3 source input mismatch: ${path}`);
  }
  for (const [path, expected] of Object.entries(build.assets)) {
    workspacePath(path);
    const actual = hash(await readFile(join(root, 'dist/macs3', path)));
    if (actual !== (typeof expected === 'string' ? expected : expected.sha256)) throw new Error(`MACS3 build asset mismatch: ${path}`);
    assets[path] = actual;
  }
  const completeAssets = await treeHashes(join(root, 'dist/macs3'));
  delete completeAssets['build-manifest.json'];
  if (!sameMap(assets, completeAssets)) throw new Error('MACS3 build manifest does not identify the complete distribution');
  await verifyNativeEnvironments(build);
  summary.builds.macs3 = { assets, buildManifest: hash(buildBytes), manifest: hash(await readFile(join(root, 'dist/macs3/manifest.json'))),
    native: hash(await readFile(join(root, 'build/native/macs3'))), 'patched-native': hash(await readFile(join(root, 'build/patched-native/macs3'))), nativeEnvironments: build.nativeEnvironments };
  if (!build.nativeEnvironments || !Object.keys(assets).length) throw new Error('Incomplete MACS3 build/environment manifest');
  if (options.engine !== 'node') await startBrowser();
  for (const [index, dataset] of datasets.entries()) {
    const files = await loadDataset(dataset), cache = new Map();
    for (const test of plan.cases.filter(test => applies(test, dataset) && (!test.oncePerTool || index === 0))) {
      try { cache.set(test.id, await executeCase(dataset, test, files, cache)); }
      catch (error) { const record = summary.cases.findLast(record => record.status === 'running'); if (record) { record.status = 'failed'; record.error = error.stack; } console.error(error.stack); }
    }
  }
  const ids = summary.cases.map(record => record.id);
  if (new Set(ids).size !== ids.length || JSON.stringify([...ids].sort()) !== JSON.stringify([...summary.expectedCases].sort()) || summary.cases.some(record => record.status !== 'passed')) throw new Error('Completed cases do not match the complete MACS3 case plan');
  for (const [path, checksum] of Object.entries({ ...summary.runtime, ...summary.validation })) if (hash(await readFile(join(root, path))) !== checksum) throw new Error(`Runtime/validator changed during run: ${path}`);
  if (hash(await readFile(join(root, 'dist/macs3/build-manifest.json'))) !== summary.builds.macs3.buildManifest) throw new Error('Build changed during validation');
  for (const [path, expected] of Object.entries(summary.builds.macs3.assets)) if (hash(await readFile(join(root, 'dist/macs3', path))) !== expected) throw new Error('Build asset changed during validation');
  await verifyNativeEnvironments(build);
  if (hash(await readFile(join(root, 'datasets/macs3/manifest.json'))) !== summary.manifestSha256) throw new Error('Dataset manifest changed during run');
  for (const [path, spec] of Object.entries(manifest.files)) {
    const bytes = await readFile(join(root, 'datasets/macs3', path));
    if (hash(bytes) !== spec.sha256 || bytes.length !== spec.bytes) throw new Error(`Dataset changed during run: ${path}`);
  }
  for (const [path, checksum] of Object.entries(build.inputs)) if (hash(await readFile(join(root, path))) !== checksum) throw new Error(`Build input changed during run: ${path}`);
  summary.status = 'passed';
} catch (error) {
  summary.status = 'failed'; summary.error = error.stack;
  const current = summary.cases.findLast(record => record.status === 'running');
  if (current) { current.status = 'failed'; current.error = error.message; }
  console.error(error.stack); process.exitCode = 1;
} finally {
  summary.finishedAt = new Date().toISOString();
  const output = join(publishedDir, `summary-${options.dataset}.json`);
  let cleanupTimer;
  try {
    if (browser) await Promise.race([browser.close(), new Promise((_, reject) => { cleanupTimer = setTimeout(() => reject(new Error('Browser cleanup exceeded 30 seconds')), 30000); })]);
  } catch (error) { summary.status = 'failed'; summary.cleanupError = error.message; process.exitCode = 1; }
  finally { clearTimeout(cleanupTimer); }
  if (server) { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
  summary.finishedAt = new Date().toISOString();
  const temporary = join(publishedDir, `.summary-${runId}.tmp`);
  await writeFile(temporary, JSON.stringify(summary, null, 2) + '\n');
  await rename(temporary, output);
  if (summary.cleanupError) process.exit(1);
}
