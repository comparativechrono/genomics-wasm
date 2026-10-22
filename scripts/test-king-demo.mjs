#!/usr/bin/env node
// Real browser UI checks for the user's locally compiled KING build.
// No compiled program or upstream source is uploaded by this test.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { createHash, randomUUID } from 'node:crypto';
import { mkdir, readFile, readdir, stat, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { extname, join, resolve, sep } from 'node:path';
import { chromium, firefox, webkit } from 'playwright';
import { KING_VERSION } from '../runtime/king.mjs';

const root = resolve(fileURLToPath(new URL('../', import.meta.url)));
let engine = process.env.BROWSER || 'chromium';
if (process.argv.length > 2) {
  if (process.argv.length !== 4 || process.argv[2] !== '--engine') throw new Error('Usage: test-king-demo.mjs [--engine chromium|firefox|webkit]');
  engine = process.argv[3];
}
const browserType = { chromium, firefox, webkit }[engine];
if (!browserType) throw new Error('--engine (or BROWSER) must be chromium, firefox or webkit');
const reportDir = join(root, 'reports/king', engine, 'demo');
await mkdir(reportDir, { recursive: true });
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const summary = { schema: 1, runId: randomUUID(), engine, version: KING_VERSION, status: 'running', startedAt: new Date().toISOString(), cases: [], sources: {}, assets: {}, downloads: [], requests: [], externalRequests: [], pageErrors: [], workerCalls: [] };
const pass = (id, details = {}) => { summary.cases.push({ id, status: 'passed', ...details }); console.log(`[KING ${engine}] PASS ${id}`); };
const mime = { '.html': 'text/html', '.mjs': 'text/javascript', '.js': 'text/javascript', '.css': 'text/css', '.wasm': 'application/wasm', '.json': 'application/json' };

async function recordTree(directory, target) {
  for (const entry of await readdir(join(root, directory), { withFileTypes: true })) {
    if (entry.name === '__pycache__' || /\.py[co]$/.test(entry.name)) continue;
    const path = `${directory}/${entry.name}`;
    if (entry.isDirectory()) await recordTree(path, target);
    else { assert.ok(entry.isFile(), path); target[path] = hash(await readFile(join(root, path))); }
  }
}
async function serve() {
  const holds = new Map();
  const server = createServer(async (request, response) => {
    // Intentionally ordinary HTTP: this serial profile needs no isolation.
    response.setHeader('Cache-Control', 'no-store');
    try {
      const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
      const held = holds.get(pathname);
      if (held) { holds.delete(pathname); held.arrived(); await held.unblocked; }
      if (response.destroyed) return;
      const path = resolve(root, `.${pathname}`);
      if (!path.startsWith(`${root}${sep}`)) { response.writeHead(403).end(); return; }
      const filename = (await stat(path)).isDirectory() ? join(path, 'index.html') : path;
      response.writeHead(200, { 'Content-Type': mime[extname(filename)] || 'application/octet-stream' }).end(await readFile(filename));
    } catch { if (!response.destroyed) response.writeHead(404).end('Not found'); }
  });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  return { server, origin: `http://127.0.0.1:${server.address().port}`, hold(pathname) {
    assert.equal(holds.has(pathname), false);
    let arrived, unblock;
    const requested = new Promise(resolve => { arrived = resolve; });
    const unblocked = new Promise(resolve => { unblock = resolve; });
    const value = { arrived, unblocked }; holds.set(pathname, value);
    return { requested, release() { if (holds.get(pathname) === value) holds.delete(pathname); unblock(); } };
  } };
}
async function deadline(promise, ms, message) {
  let timer;
  try { return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(message)), ms); })]); }
  finally { clearTimeout(timer); }
}
async function finished(page) {
  await page.waitForFunction(() => !document.getElementById('controls').disabled && !document.getElementById('status').textContent.startsWith('Running '), null, { timeout: 120000 });
  return page.locator('#status').innerText();
}
async function run(page) {
  await page.locator('#run').click();
  const status = await finished(page);
  assert.match(status, /^Exited with status 0 /, `${status}\n${await page.locator('#stdout').innerText()}\n${await page.locator('#stderr').innerText()}`);
  assert.doesNotMatch(status, /Missing output folder|No files were written/);
}
async function outputBytes(page, name) {
  const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('link', { name: `Download ${name}`, exact: true }).click()]);
  assert.equal(download.suggestedFilename(), name === 'standard output' ? `king-${KING_VERSION}-stdout.txt` : name === 'standard error' ? `king-${KING_VERSION}-stderr.txt` : name.split('/').at(-1));
  const bytes = await readFile(await download.path());
  const captured = await page.evaluate(async name => {
    const result = window.__kingResults.at(-1);
    const data = name === 'standard output' ? result.stdout : name === 'standard error' ? result.stderr : result.files.find(file => file.path === `/work/${name}`)?.data;
    if (!data) throw new Error(`Worker did not return ${name}`);
    const digest = await crypto.subtle.digest('SHA-256', data);
    return { bytes: data.length, sha256: [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, '0')).join('') };
  }, name);
  assert.equal(bytes.length, captured.bytes);
  assert.equal(hash(bytes), captured.sha256, `Download bytes differ from the worker output: ${name}`);
  summary.downloads.push({ name, ...captured });
  return bytes;
}
async function assertKinship(page) {
  const paths = await page.locator('#result-file option').evaluateAll(options => options.map(option => option.textContent));
  assert.ok(paths.includes('results/king.kin'), paths.join(', '));
  assert.ok(paths.includes('results/king.kin0'), paths.join(', '));
  for (const name of ['results/king.kin', 'results/king.kin0']) {
    const bytes = await outputBytes(page, name);
    const lines = bytes.toString().trim().split(/\r?\n/);
    assert.ok(lines.length > 1, `${name} contains data rows`);
    assert.match(lines[0], /Kinship/);
  }
  assert.match((await outputBytes(page, 'standard output')).toString(), /^KING 2\.3\.2\b/);
}

let browser, page, host, held;
try {
  await recordTree('dist/king/2.3.2', summary.assets);
  await recordTree('examples/king', summary.sources);
  await recordTree('datasets/king', summary.sources);
  for (const path of ['runtime/index.mjs', 'runtime/king.mjs', 'runtime/execute.mjs', 'runtime/worker.mjs', 'scripts/test-king-demo.mjs']) summary.sources[path] = hash(await readFile(join(root, path)));
  const config = JSON.parse(await readFile(join(root, 'examples/king/config.json'), 'utf8'));
  const dataset = JSON.parse(await readFile(join(root, 'datasets/king/manifest.json'), 'utf8'));
  host = await serve(); browser = await browserType.launch({ headless: true }); summary.browserVersion = browser.version();
  const context = await browser.newContext({ viewport: { width: 1100, height: 950 } }); context.setDefaultTimeout(30000);
  context.on('request', request => { summary.requests.push(request.url()); if (/^https?:/.test(request.url()) && new URL(request.url()).origin !== host.origin) summary.externalRequests.push(request.url()); });
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (/^https?:$/.test(url.protocol) && url.origin !== host.origin) { summary.externalRequests.push(url.href); await route.abort('blockedbyclient'); }
    else await route.continue();
  });
  await context.addInitScript(() => {
    window.__kingCalls = []; window.__kingResults = [];
    const OriginalWorker = window.Worker;
    window.Worker = class extends OriginalWorker {
      constructor(url, options) { super(url, options); this.addEventListener('message', event => { if (event.data.type === 'result') window.__kingResults.push(event.data.result); }); }
      postMessage(message, ...rest) {
        if (Array.isArray(message.args)) window.__kingCalls.push({ args: message.args, moduleUrl: message.moduleUrl, inputs: message.files.map(file => file.path), outputDirectories: message.outputDirectories });
        return super.postMessage(message, ...rest);
      }
    };
  });
  page = await context.newPage(); page.on('pageerror', error => summary.pageErrors.push(error.message));
  await page.goto(`${host.origin}/examples/king/`);
  await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Ready.'));
  assert.equal(await page.evaluate(() => crossOriginIsolated), false);
  pass('ordinary-http-without-cross-origin-isolation');

  await page.locator('#load-data').click();
  await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Synthetic data loaded.'));
  assert.equal(await page.locator('#files li').count(), 3);
  assert.equal(await page.locator('#bed-file').inputValue(), 'cohort.bed');
  await run(page); await assertKinship(page);
  await page.screenshot({ path: join(reportDir, 'kinship-demo.png'), fullPage: true });
  pass('synthetic-kinship-and-exact-downloads');

  await page.locator('#command').selectOption('duplicate'); await run(page);
  const concordance = await outputBytes(page, 'results/king.con');
  assert.ok(concordance.toString().trim().split(/\r?\n/).length > 1);
  assert.equal(await page.getByRole('link', { name: 'Download results/king.kin', exact: true }).count(), 0);
  pass('duplicate-preset-replaces-prior-results');

  await page.locator('#command').selectOption('related'); await run(page);
  assert.ok((await outputBytes(page, 'results/king.kin')).toString().trim().split(/\r?\n/).length > 1);
  const relatedArgs = await page.evaluate(() => window.__kingCalls.at(-1).args);
  assert.ok(relatedArgs.includes('--noscreen'));
  assert.equal(relatedArgs[relatedArgs.indexOf('--noscreen') + 1], '1');
  pass('close-relative-preset-uses-noscreen');

  await page.locator('#command').selectOption('bysample'); await run(page);
  assert.equal((await outputBytes(page, 'results/kingbySample.txt')).toString().trim().split(/\r?\n/).length, dataset.sampleCount + 1);
  await page.locator('#command').selectOption('bysnp'); await run(page);
  const variants = await outputBytes(page, 'results/kingbySNP.txt');
  assert.equal(variants.toString().trim().split(/\r?\n/).length, dataset.markerCount + 1);
  assert.ok(variants.length > 100000);
  await page.locator('#result-file').selectOption('/work/results/kingbySNP.txt');
  assert.match(await page.locator('#preview').innerText(), /Preview truncated/);
  assert.ok((await page.locator('#preview').innerText()).length < variants.length);
  pass('qc-presets-and-complete-download-beyond-preview-limit');

  await page.locator('#arguments').fill(JSON.stringify(['-b', 'absent.bed', '--kinship', '--prefix', 'results/king']));
  await page.locator('#run').click(); const failedStatus = await finished(page);
  assert.match(failedStatus, /^Exited with status (?!0\b)-?\d+ /);
  assert.equal(await page.locator('#downloads a').count(), 0);
  assert.match(await page.locator('#stdout').innerText() + await page.locator('#stderr').innerText(), /absent|cannot|failed|unable|not found/i);
  await outputBytes(page, 'standard error');
  await page.locator('#command').selectOption('kinship'); await run(page); await assertKinship(page);
  pass('failure-clears-results-and-next-run-recovers');

  await page.locator('#arguments').fill('["--kinship", 7]');
  const before = await page.evaluate(() => window.__kingCalls.length);
  await page.locator('#run').click(); assert.match(await page.locator('#status').innerText(), /^Check command:/);
  assert.equal(await page.evaluate(() => window.__kingCalls.length), before);
  assert.equal(await page.locator('#downloads a').count(), 0);
  await page.locator('#output-directory').fill('../escape'); await page.locator('#output-directory').press('Tab');
  await page.locator('#run').click(); assert.match(await page.locator('#status').innerText(), /^Check command:/);
  assert.equal(await page.evaluate(() => window.__kingCalls.length), before);
  await page.locator('#output-directory').fill('results'); await page.locator('#output-directory').press('Tab');
  pass('invalid-arguments-and-output-paths-rejected');

  await page.locator('#clear').click();
  const fixtureFiles = await Promise.all(config.dataset.files.map(async name => ({ name, mimeType: 'application/octet-stream', buffer: await readFile(join(root, 'datasets/king', name)) })));
  await page.locator('#upload').setInputFiles(fixtureFiles);
  await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Files loaded.'));
  await run(page); await assertKinship(page); pass('uploaded-bed-bim-fam');

  const uploadFolder = join(root, 'build/king-demo', summary.runId, 'pedigree'); await mkdir(uploadFolder, { recursive: true });
  for (const file of fixtureFiles) await writeFile(join(uploadFolder, file.name), file.buffer);
  await page.locator('#clear').click(); await page.locator('#upload-folder').setInputFiles(uploadFolder);
  await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Files loaded.'));
  assert.equal(await page.locator('#bed-file').inputValue(), 'pedigree/cohort.bed');
  await run(page); await assertKinship(page);
  assert.ok((await page.evaluate(() => window.__kingCalls.at(-1).inputs)).includes('/work/pedigree/cohort.bed'));
  pass('folder-upload-preserves-paths');

  held = host.hold('/dist/king/2.3.2/king.wasm');
  await page.locator('#run').click(); await deadline(held.requested, 30000, 'WASM request did not reach cancellation hold');
  assert.equal(await page.locator('#downloads a').count(), 0);
  await page.locator('#cancel').click(); assert.equal(await finished(page), 'Command cancelled.');
  held.release(); held = null;
  await run(page); await assertKinship(page); pass('in-flight-cancellation-and-recovery');

  await page.locator('#timeout').fill('1'); held = host.hold('/dist/king/2.3.2/king.wasm');
  await page.locator('#run').click(); await deadline(held.requested, 30000, 'WASM request did not reach timeout hold');
  assert.match(await finished(page), /^TimeoutError:/);
  assert.equal(await page.locator('#downloads a').count(), 0);
  held.release(); held = null;
  await page.locator('#timeout').fill('300'); await run(page); await assertKinship(page); pass('timeout-and-recovery');

  await page.locator('#arguments').fill(JSON.stringify(['-b', 'pedigree/cohort.bed', '--kinship', '--rplot', '--prefix', 'results/king']));
  await page.locator('#run').click(); assert.match(await finished(page), /^Exited with status (?!0\b)-?\d+ /);
  assert.match(await page.locator('#stdout').innerText() + await page.locator('#stderr').innerText(), /R|ps2pdf/);
  assert.equal(await page.locator('#downloads a').count(), 0); pass('external-plotting-fails-clearly');

  await page.locator('#clear').click(); assert.equal(await page.locator('#downloads a').count(), 0);
  assert.equal(await page.locator('#stream-downloads a').count(), 0);
  assert.equal(await page.locator('#files').innerText(), 'No files loaded.');
  pass('clear-discards-inputs-and-downloads');
  assert.deepEqual(summary.externalRequests, []); assert.deepEqual(summary.pageErrors, []);
  summary.workerCalls = await page.evaluate(() => window.__kingCalls);
  assert.ok(summary.workerCalls.length >= 10);
  assert.ok(summary.workerCalls.every(call => call.moduleUrl === `${host.origin}/dist/king/2.3.2/king.mjs`));
  pass('local-only-program-and-data-requests');
  summary.status = 'passed';
} catch (error) {
  summary.status = 'failed'; summary.error = { name: error.name, message: error.message, stack: error.stack };
  if (page) { await page.screenshot({ path: join(reportDir, 'failure.png'), fullPage: true }).catch(() => {}); summary.workerCalls = await page.evaluate(() => window.__kingCalls || []).catch(() => []); }
  process.exitCode = 1; console.error(error);
} finally {
  held?.release(); await browser?.close(); host?.server.closeAllConnections(); host?.server.close();
  summary.finishedAt = new Date().toISOString();
  await writeFile(join(reportDir, 'summary.json'), JSON.stringify(summary, null, 2) + '\n');
}
