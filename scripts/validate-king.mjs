#!/usr/bin/env node
/** Execute the same synthetic KING cases in pristine native, patched native, and actual WebAssembly. */
import { readFile, writeFile, mkdir, readdir, rename } from 'node:fs/promises';
import { createHash, randomUUID } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { createServer } from 'node:http';
import { dirname, resolve, join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { KingRunner } from '../runtime/king.mjs';
import { workspacePath } from '../runtime/index.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const options = { engine: 'node', case: 'all' };
const usage = 'validate-king.mjs [--engine node|chromium|firefox|webkit] [--case all|CASE_ID]';
for (let i = 2; i < process.argv.length; i += 2) {
  const key = process.argv[i].replace(/^--/, '');
  if (!(key in options) || !process.argv[i + 1]) throw Error(usage);
  options[key] = process.argv[i + 1];
}
if (!['node', 'chromium', 'firefox', 'webkit'].includes(options.engine)) throw Error(usage);
const plan = JSON.parse(await readFile(join(root, 'validation/king/cases.json'), 'utf8'));
const selected = plan.cases.filter(test => options.case === 'all' || test.id === options.case);
if (!selected.length) throw Error(usage);
const targets = ['native', 'patched-native', 'wasm'];
const runId = randomUUID(), timeoutMs = 300000;
const publishedDir = join(root, 'reports/king', options.engine), reportDir = join(publishedDir, runId);
await mkdir(reportDir, { recursive: true });
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const local = path => workspacePath(path).slice(6);
const ordered = value => Array.isArray(value) ? value.map(ordered) : value && typeof value === 'object'
  ? Object.fromEntries(Object.keys(value).sort().map(key => [key, ordered(value[key])])) : value;
const canonical = value => JSON.stringify(ordered(value));
const same = (first, second) => canonical(first) === canonical(second);
const summary = {
  schema: 1, tool: 'king', version: '2.3.2', engine: options.engine,
  status: 'running', runId, reportDirectory: runId, node: process.version,
  browserVersion: null, crossOriginIsolated: false,
  startedAt: new Date().toISOString(), expectedCases: selected.map(test => test.id),
  completePlan: options.case === 'all', cases: [], browserErrors: [], externalRequests: [],
  scope: 'Synthetic fixtures only; local personal-use build; no redistribution permission established',
};
let browser, page, server, manifest;
const log = message => console.log(`[${new Date().toISOString()}] ${options.engine} ${message}`);

async function inventory(base, prefix = '') {
  const result = {};
  for (const item of (await readdir(join(base, prefix), { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
    const path = prefix + item.name;
    if (item.isDirectory()) Object.assign(result, await inventory(base, path + '/'));
    else if (item.isFile()) result[path] = hash(await readFile(join(base, path)));
    else throw Error('Non-regular execution asset: ' + path);
  }
  return result;
}

async function identities() {
  const answer = {};
  const lock = JSON.parse(await readFile(join(root, 'tools/king/versions.lock.json'), 'utf8'));
  for (const kind of targets) {
    const directory = join(root, kind === 'wasm' ? 'dist/king/2.3.2' : `build/king/${kind}`);
    const raw = await readFile(join(directory, 'build-manifest.json'));
    const build = JSON.parse(raw), assets = await inventory(directory);
    delete assets['build-manifest.json'];
    if (build.schema !== 1 || build.tool !== 'king' || build.version !== '2.3.2' || build.kind !== kind ||
        !same(build.assets, assets) || !same(build.source, lock.source)) throw Error('Build identity/asset mismatch: ' + kind);
    if (!Object.keys(build.inputs ?? {}).length || !Object.keys(build.sourceFiles ?? {}).length) throw Error('Missing source/build provenance: ' + kind);
    if (kind === 'native' && Object.keys(build.modifiedSourceFiles ?? {}).length) throw Error('Native reference must use pristine source');
    for (const [path, sha] of Object.entries(build.inputs)) {
      if (hash(await readFile(join(root, local(path)))) !== sha) throw Error('Build input changed: ' + path);
    }
    answer[kind] = { buildId: build.buildId, buildManifest: hash(raw), assets, inputs: build.inputs,
      source: build.source, sourceFiles: build.sourceFiles, modifiedSourceFiles: build.modifiedSourceFiles, profile: build.profile };
  }
  if (!same(answer.native.sourceFiles, answer.wasm.sourceFiles) ||
      !same(answer.native.sourceFiles, answer['patched-native'].sourceFiles) ||
      !same(answer.wasm.modifiedSourceFiles, answer['patched-native'].modifiedSourceFiles)) throw Error('Build source provenance differs between targets');
  return answer;
}

async function checkDataset() {
  const raw = await readFile(join(root, 'datasets/king/manifest.json'));
  if (summary.datasetManifestSha256 && hash(raw) !== summary.datasetManifestSha256) throw Error('Dataset manifest changed');
  const data = JSON.parse(raw);
  if (data.schema !== 1 || data.version !== '2.3.2' || data.sampleCount < 100 || data.markerCount < 4096) throw Error('Invalid KING fixture identity');
  for (const [path, spec] of Object.entries(data.files)) {
    const bytes = await readFile(join(root, 'datasets/king', local(path)));
    if (bytes.length !== spec.bytes || hash(bytes) !== spec.sha256) throw Error('Dataset fixture changed: ' + path);
  }
  summary.datasetManifestSha256 = hash(raw);
  return data;
}

async function codeIdentity() {
  const paths = ['runtime/index.mjs', 'runtime/execute.mjs', 'runtime/worker.mjs', 'runtime/king.mjs',
    'scripts/validate-king.mjs', 'scripts/generate-king-fixtures.py', 'validation/king/cases.json', 'validation/king/compare.py'];
  return Object.fromEntries(await Promise.all(paths.map(async path => [path, hash(await readFile(join(root, path)))])));
}

async function startBrowser() {
  const playwright = await import('playwright');
  browser = await playwright[options.engine].launch({ headless: true });
  server = createServer(async (request, response) => {
    try {
      const url = new URL(request.url, 'http://localhost');
      if (url.pathname === '/') {
        response.setHeader('Content-Type', 'text/html');
        response.end('<!doctype html><meta charset="utf-8"><title>KING validation</title>');
        return;
      }
      const path = resolve(root, '.' + decodeURIComponent(url.pathname));
      if (!path.startsWith(root + sep) || !['runtime/', 'dist/king/2.3.2/'].some(prefix => relative(root, path).startsWith(prefix))) {
        response.writeHead(404).end(); return;
      }
      response.setHeader('Content-Type', path.endsWith('.wasm') ? 'application/wasm' : /\.(mjs|js)$/.test(path) ? 'text/javascript' : 'application/octet-stream');
      response.end(await readFile(path));
    } catch { response.writeHead(404).end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  page = await browser.newPage();
  const origin = `http://127.0.0.1:${server.address().port}/`;
  page.on('pageerror', error => summary.browserErrors.push(error.message));
  page.on('request', request => { if (!request.url().startsWith(origin)) summary.externalRequests.push(request.url()); });
  await page.goto(origin);
  summary.browserVersion = browser.version();
  summary.crossOriginIsolated = await page.evaluate(() => crossOriginIsolated);
  if (summary.crossOriginIsolated) throw Error('KING validation must use ordinary HTTP without isolation headers');
}

async function wasm(args, files, outputs) {
  if (options.engine === 'node') return new KingRunner().run(args, { files, outputs, timeoutMs });
  const result = await page.evaluate(async ({ args, files, outputs, timeoutMs }) => {
    const { KingRunner } = await import('/runtime/king.mjs');
    const decode = value => Uint8Array.from(atob(value), character => character.charCodeAt(0));
    const encode = bytes => {
      let value = '';
      for (let i = 0; i < bytes.length; i += 32768) value += String.fromCharCode(...bytes.subarray(i, i + 32768));
      return btoa(value);
    };
    const result = await new KingRunner().run(args, { files: files.map(file => ({ path: file.path, data: decode(file.data) })), outputs, timeoutMs });
    return { ...result, stdout: encode(result.stdout), stderr: encode(result.stderr), files: result.files.map(file => ({ path: file.path, data: encode(file.data) })) };
  }, { args, files: files.map(file => ({ path: file.path, data: Buffer.from(file.data).toString('base64') })), outputs, timeoutMs });
  return { ...result, stdout: Buffer.from(result.stdout, 'base64'), stderr: Buffer.from(result.stderr, 'base64'),
    files: result.files.map(file => ({ path: file.path, data: Buffer.from(file.data, 'base64') })) };
}

async function native(kind, args, files, outputs, id) {
  const work = join(root, 'build/king-validation', runId, options.engine, id, kind);
  await mkdir(work, { recursive: true });
  for (const file of files) await writeFile(join(work, local(file.path)), file.data);
  const program = join(root, 'build/king', kind, 'king');
  const result = spawnSync(program, args, { cwd: work, env: { ...process.env, LC_ALL: 'C', TZ: 'UTC', OMP_NUM_THREADS: '1' },
    timeout: timeoutMs, maxBuffer: 128 * 1024 * 1024 });
  if (result.error) throw result.error;
  if (result.signal || !Number.isInteger(result.status)) throw Error('Native process did not exit normally');
  const answer = { exitCode: result.status, stdout: result.stdout, stderr: result.stderr, files: [], missingOutputs: [] };
  for (const path of outputs) {
    try { answer.files.push({ path, data: await readFile(join(work, local(path))) }); }
    catch (error) { if (error.code !== 'ENOENT') throw error; answer.missingOutputs.push(path); }
  }
  return answer;
}

async function execute(test, kind, files, args) {
  const start = Date.now(), outputs = test.outputs.map(local);
  const result = kind === 'wasm' ? await wasm(args, files, outputs) : await native(kind, args, files, outputs, test.id);
  if (!Number.isInteger(result.exitCode)) throw Error('No normal exit status');
  const directory = join(reportDir, test.id, kind);
  await mkdir(directory, { recursive: true });
  for (const stream of ['stdout', 'stderr']) await writeFile(join(directory, stream + '.txt'), result[stream]);
  const execution = { target: kind, engine: kind === 'wasm' ? options.engine : 'native', args,
    inputs: Object.fromEntries(files.map(file => [file.path, hash(file.data)])),
    inputBytes: Object.fromEntries(files.map(file => [file.path, file.data.length])),
    exitCode: result.exitCode, canonicalExitCode: result.exitCode < 0 ? result.exitCode & 255 : result.exitCode,
    missingOutputs: result.missingOutputs ?? [], stdout: hash(result.stdout), stderr: hash(result.stderr),
    stdoutBytes: result.stdout.length, stderrBytes: result.stderr.length, files: {}, fileBytes: {}, seconds: (Date.now() - start) / 1000 };
  for (const file of result.files) {
    const path = local(file.path);
    if (!outputs.includes(path) || Object.hasOwn(execution.files, path)) throw Error('Unexpected/duplicate output: ' + path);
    execution.files[path] = hash(file.data); execution.fileBytes[path] = file.data.length;
    await writeFile(join(directory, path), file.data);
  }
  await writeFile(join(directory, 'execution.json'), JSON.stringify(execution, null, 2) + '\n');
  if (!Buffer.from(result.stdout).toString().includes('KING 2.3.2 -')) throw Error(`${test.id} ${kind}: missing version banner`);
  if (test.contract === 'failure') {
    if (result.exitCode === 0 || !new RegExp(test.diagnostic, 'i').test(Buffer.from(result.stdout).toString() + Buffer.from(result.stderr).toString())) {
      throw Error(`${test.id} ${kind}: expected a nonzero exit and diagnostic`);
    }
  } else if (result.exitCode !== 0 || execution.missingOutputs.length || outputs.some(path => !(path in execution.files))) {
    throw Error(`${test.id} ${kind}: exit ${result.exitCode}; missing ${execution.missingOutputs.join(',')}; ${Buffer.from(result.stdout).toString().slice(-1500)} ${Buffer.from(result.stderr).toString().slice(-1500)}`);
  }
  return execution;
}

async function compare(test, target, executions) {
  const actual = target === 'native-contract' ? 'native' : target;
  const path = join(reportDir, test.id, `compare-${target}.json`);
  const result = spawnSync('python3', ['-B', join(root, 'validation/king/compare.py'), join(reportDir, test.id, 'native'),
    join(reportDir, test.id, actual), '--case', test.id, '--dataset', test.dataset ?? 'main', '--report', path],
  { env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' }, timeout: timeoutMs, maxBuffer: 8 * 1024 * 1024 });
  if (result.error) throw result.error;
  let comparison;
  try { comparison = JSON.parse(await readFile(path, 'utf8')); }
  catch { throw Error(`Comparator did not produce a report: ${result.stderr?.toString()}`); }
  if (result.status !== 0 || !comparison.equal) throw Error(`${test.id} ${target}: ${JSON.stringify(comparison).slice(-5000)}`);
  for (const [side, kind] of [['expected', 'native'], ['actual', actual]]) {
    if (!same(comparison[side + 'Files'], executions[kind].files) || !same(comparison[side + 'FileBytes'], executions[kind].fileBytes)) {
      throw Error('Comparator is not bound to captured output bytes');
    }
  }
  return comparison;
}

async function executeCase(test) {
  const record = { id: test.id, dataset: test.dataset ?? 'main', contract: test.contract,
    status: 'running', executions: {}, comparisons: {} };
  summary.cases.push(record);
  try {
    let names = ['cohort.bed', 'cohort.bim', 'cohort.fam'];
    if (test.id === 'kinship-gzip-fam') names = ['cohort.bed', 'cohort.bim', 'cohort.fam.gz'];
    if (test.contract === 'failure') names = [];
    const files = await Promise.all(names.map(async path => {
      const source = (test.dataset === 'small' ? 'small/' : '') + path;
      const data = await readFile(join(root, 'datasets/king', source));
      if (!manifest.files[source] || hash(data) !== manifest.files[source].sha256) throw Error('Unpinned input: ' + source);
      return { path, data };
    }));
    const args = test.command ?? ['-b', 'cohort.bed', ...test.args, '--cpus', '1', '--prefix', 'result'];
    record.args = args;
    for (const kind of targets) {
      const command = kind !== 'native' && test.patchedArgs
        ? ['-b', 'cohort.bed', ...test.patchedArgs, '--cpus', '1', '--prefix', 'result'] : args;
      record.executions[kind] = await execute(test, kind, files, command);
    }
    if (new Set(targets.map(kind => record.executions[kind].canonicalExitCode)).size !== 1) throw Error('Exit status mismatch');
    for (const target of ['native-contract', 'patched-native', 'wasm']) record.comparisons[target] = await compare(test, target, record.executions);
    record.status = 'passed'; log('PASS ' + test.id);
  } catch (error) {
    record.status = 'failed'; record.error = error.stack; log(`FAIL ${test.id}: ${error.message}`);
  }
}

try {
  manifest = await checkDataset(); summary.build = await identities(); summary.code = await codeIdentity();
  if (options.engine !== 'node') await startBrowser();
  for (const test of selected) await executeCase(test);
  if (JSON.stringify(await identities()) !== JSON.stringify(summary.build)) throw Error('Build changed during validation');
  await checkDataset();
  if (!same(await codeIdentity(), summary.code)) throw Error('Execution/validation code changed during run');
  if (summary.browserErrors.length || summary.externalRequests.length) throw Error('Unexpected browser errors or external requests');
  if (summary.cases.some(test => test.status !== 'passed') || summary.cases.length !== selected.length) throw Error('The complete requested case plan did not pass');
  summary.status = 'passed';
} catch (error) {
  summary.status = 'failed'; summary.error = error.stack; console.error(error.stack); process.exitCode = 1;
} finally {
  let timer;
  try {
    if (browser) await Promise.race([browser.close(), new Promise((_, reject) => { timer = setTimeout(() => reject(Error('Browser cleanup exceeded 30 seconds')), 30000); })]);
  } catch (error) { summary.status = 'failed'; summary.cleanupError = error.message; process.exitCode = 1; }
  finally {
    clearTimeout(timer);
    if (server) { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
  }
  summary.finishedAt = new Date().toISOString();
  const name = options.case === 'all' ? 'summary-all.json' : `summary-${options.case}.json`;
  const temporary = join(publishedDir, `.summary-${runId}.tmp`);
  await writeFile(temporary, JSON.stringify(summary, null, 2) + '\n');
  await writeFile(join(reportDir, 'summary.json'), JSON.stringify(summary, null, 2) + '\n');
  await rename(temporary, join(publishedDir, name));
  log(`${summary.status}: ${relative(root, join(publishedDir, name))}`);
}
