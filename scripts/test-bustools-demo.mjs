// Exercise real browser commands, file transfers and command chaining.
// Native-versus-WebAssembly numerical comparisons are a separate release gate.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { createHash, randomUUID } from 'node:crypto';
import { copyFile, mkdir, readFile, readdir, stat, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { extname, join, resolve, sep } from 'node:path';
import { chromium, firefox, webkit } from 'playwright';

const root = fileURLToPath(new URL('../', import.meta.url));
const engine = process.env.BROWSER || 'chromium';
const browserType = { chromium, firefox, webkit }[engine];
if (!browserType) throw new Error('BROWSER must be chromium, firefox or webkit');
const config = JSON.parse(await readFile(join(root, 'examples/bustools/config.json'), 'utf8'));
assert.deepEqual(Object.keys(config.tools), ['bustools']);
assert.ok(config.datasets.bustools);
const reportBase = resolve(process.env.BUSTOOLS_DEMO_REPORT || join(root, 'reports/bustools', engine));
const reportDir = join(reportBase, 'browser-demo');
await mkdir(reportDir, { recursive: true });
const summary = { schema: 1, runId: randomUUID(), engine, tools: ['bustools'], status: 'running', startedAt: new Date().toISOString(), phases: [], cases: [], assets: {}, sources: {}, browserErrors: [], browserConsole: [], requests: [], failedRequests: [], externalRequests: [], workerCalls: [], downloads: [] };
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const phase = message => { summary.phases.push({ at: new Date().toISOString(), message }); console.log(`[bustools demo] ${message}`); };
const passed = (id, details = {}) => summary.cases.push({ id, status: 'passed', ...details });
const mime = { '.html': 'text/html', '.mjs': 'text/javascript', '.js': 'text/javascript', '.css': 'text/css', '.wasm': 'application/wasm', '.json': 'application/json' };
const commands = ['sort', 'merge', 'mash', 'text', 'fromtext', 'correct', 'count', 'predict', 'umicorrect', 'capture', 'allowlist', 'project', 'inspect', 'linker', 'collapse', 'clusterhist', 'extract', 'compress', 'inflate', 'dump', 'whitelist', 'onlist', 'decompress'];

async function deadline(promise, timeoutMs, message) {
  let timer;
  try { return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(message)), timeoutMs); })]); }
  finally { clearTimeout(timer); }
}

async function serve() {
  const holds = new Map();
  const server = createServer(async (request, response) => {
    // Deliberately no cross-origin isolation headers or service worker.
    response.setHeader('Cache-Control', 'no-store');
    try {
      const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
      const held = holds.get(pathname);
      if (held) { holds.delete(pathname); held.arrived(); await held.unblocked; }
      if (response.destroyed) return;
      const path = resolve(root, `.${pathname}`);
      if (!path.startsWith(root.endsWith(sep) ? root : `${root}${sep}`)) { response.writeHead(403).end(); return; }
      const filename = (await stat(path)).isDirectory() ? join(path, 'index.html') : path;
      response.writeHead(200, { 'Content-Type': mime[extname(filename)] || 'application/octet-stream' }).end(await readFile(filename));
    } catch { if (!response.destroyed) response.writeHead(404).end('Not found'); }
  });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  return {
    server, origin: `http://127.0.0.1:${server.address().port}`,
    holdNextRequest(pathname) {
      assert.equal(holds.has(pathname), false);
      let arrived, unblock;
      const requested = new Promise(resolve => { arrived = resolve; });
      const unblocked = new Promise(resolve => { unblock = resolve; });
      const held = { arrived, unblocked }; holds.set(pathname, held);
      return { requested, release() { if (holds.get(pathname) === held) holds.delete(pathname); unblock(); } };
    },
  };
}

async function recordTree(directory, target) {
  for (const entry of (await readdir(join(root, directory), { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
    if (entry.name === '__pycache__' || /\.py[co]$/.test(entry.name)) continue;
    const path = `${directory}/${entry.name}`;
    if (entry.isDirectory()) await recordTree(path, target);
    else { assert.ok(entry.isFile(), `Expected a regular file: ${path}`); target[path] = hash(await readFile(join(root, path))); }
  }
}

function busRecords(bytes) {
  assert.equal(bytes.subarray(0, 4).toString('hex'), '42555300');
  assert.equal(bytes.readUInt32LE(4), 1);
  const offset = 20 + bytes.readUInt32LE(16);
  assert.equal((bytes.length - offset) % 32, 0);
  assert.ok(bytes.length > offset, 'The BUS file contains records');
  let readCount = 0;
  for (let pos = offset; pos < bytes.length; pos += 32) {
    assert.ok(bytes.readInt32LE(pos + 16) >= 0);
    readCount += bytes.readUInt32LE(pos + 20);
  }
  assert.ok(readCount > 0);
  return { records: (bytes.length - offset) / 32, readCount };
}
function matrix(bytes, barcodes, genes) {
  const lines = bytes.toString('utf8').trim().split('\n');
  assert.match(lines[0], /^%%MatrixMarket matrix coordinate (real|integer) general$/);
  const data = lines.filter(line => line && !line.startsWith('%')).map(line => line.trim().split(/\s+/).map(Number));
  const [rows, columns, entries] = data.shift();
  assert.equal(rows, barcodes.length); assert.equal(columns, genes.length); assert.equal(entries, data.length);
  assert.ok(rows > 0 && columns > 0 && entries > 0, 'Real records produce a nonempty cell-by-gene matrix');
  let molecules = 0;
  for (const [row, column, count] of data) {
    assert.ok(Number.isInteger(row) && row >= 1 && row <= rows);
    assert.ok(Number.isInteger(column) && column >= 1 && column <= columns);
    assert.ok(Number.isFinite(count) && count > 0); molecules += count;
  }
  return { cells: rows, genes: columns, entries, molecules };
}
const labels = bytes => bytes.toString('utf8').trim().split('\n').filter(Boolean);
async function ready(page) {
  await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Ready.'), null, { timeout: 30000 });
}
async function finished(page) {
  await deadline(page.waitForFunction(() => !document.getElementById('controls').disabled && !document.getElementById('status').textContent.startsWith('Running '), null, { timeout: 600000 }), 605000, 'Command exceeded 605 seconds');
  return page.locator('#status').innerText();
}
async function run(page) {
  await page.locator('#run').click();
  const status = await finished(page);
  assert.match(status, /^Exited with status 0 /, status); assert.doesNotMatch(status, /Missing requested files:/);
}
async function choose(page, command) { await page.locator('#command').selectOption(command); }
async function load(page) {
  await page.locator('#load-data').click();
  await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Example data loaded.'), null, { timeout: 60000 });
  assert.equal(await page.locator('#files li').count(), config.datasets.bustools.files.length);
}
async function edit(page, args, outputs = [], directories = []) {
  await page.locator('#arguments').fill(JSON.stringify(args));
  await page.locator('#outputs').fill(outputs.join('\n')); await page.locator('#output-directories').fill(directories.join('\n'));
}
let lastDownloadClick = 0;
async function downloadBytes(page, name) {
  // Pace real clicks to avoid the browser's automatic download flood limiter.
  const delay = 250 - (performance.now() - lastDownloadClick);
  if (delay > 0) await new Promise(resolve => setTimeout(resolve, delay));
  lastDownloadClick = performance.now();
  const record = { name, startedAt: new Date().toISOString() }; summary.downloads.push(record);
  const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('link', { name: `Download ${name}`, exact: true }).click()]);
  assert.equal(download.suggestedFilename(), name.split('/').at(-1));
  const path = await download.path(); assert.ok(path);
  const bytes = await readFile(path); record.completedAt = new Date().toISOString(); record.bytes = bytes.length; record.sha256 = hash(bytes);
  return bytes;
}
async function workerCalls(page) { return page.evaluate(() => window.__bustoolsWorkerCalls); }

let browser, page, host;
try {
  await recordTree('dist/bustools', summary.assets); await recordTree('tools/bustools', summary.sources);
  await recordTree('datasets/bustools', summary.sources);
  for (const name of ['index', 'execute', 'worker', 'bustools']) summary.sources[`runtime/${name}.mjs`] = hash(await readFile(join(root, `runtime/${name}.mjs`)));
  summary.sources['scripts/test-bustools-demo.mjs'] = hash(await readFile(join(root, 'scripts/test-bustools-demo.mjs')));
  await recordTree('examples/bustools', summary.sources);
  host = await serve(); browser = await browserType.launch({ headless: true }); summary.browserVersion = browser.version();
  const context = await browser.newContext({ viewport: { width: 1100, height: 900 } }); context.setDefaultTimeout(30000);
  context.on('request', request => summary.requests.push({ url: request.url(), type: request.resourceType() }));
  context.on('requestfailed', request => summary.failedRequests.push({ url: request.url(), error: request.failure()?.errorText }));
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (/^https?:$/.test(url.protocol) && url.origin !== host.origin) { summary.externalRequests.push(url.href); await route.abort('blockedbyclient'); }
    else await route.continue();
  });
  // Observe actual workers; never substitute the native program or its output.
  await context.addInitScript(() => {
    window.__bustoolsWorkerCalls = [];
    const OriginalWorker = window.Worker;
    window.Worker = class extends OriginalWorker {
      constructor(url, options) { super(url, options); this.recordedUrl = String(url); }
      postMessage(message, ...rest) {
        window.__bustoolsWorkerCalls.push({ workerUrl: this.recordedUrl, moduleUrl: message.moduleUrl, args: [...message.args] });
        return super.postMessage(message, ...rest);
      }
    };
  });
  page = await context.newPage();
  page.on('pageerror', error => summary.browserErrors.push(error.message));
  page.on('console', message => summary.browserConsole.push({ type: message.type(), text: message.text() }));
  await page.goto(`${host.origin}/examples/bustools/`); await ready(page);
  assert.equal(await page.evaluate(() => crossOriginIsolated), false);
  assert.equal(await page.evaluate(() => typeof SharedArrayBuffer), 'undefined');
  assert.equal(await page.evaluate(() => !!navigator.serviceWorker?.controller), false);
  passed('ordinary-static-hosting');

  phase('Sort real BUS records, correct cell barcodes and count molecules by gene');
  await load(page); await choose(page, 'sort'); await run(page);
  const sorted = await downloadBytes(page, 'sorted.bus');
  const sortedInfo = busRecords(sorted);
  await page.locator('#result-file').selectOption('/work/sorted.bus');
  assert.match(await page.locator('#preview').textContent(), /^Compressed or binary file /);
  await choose(page, 'correct'); await run(page);
  const corrected = await downloadBytes(page, 'corrected.bus'); busRecords(corrected);
  await choose(page, 'resort'); await run(page);
  const correctedSorted = await downloadBytes(page, 'corrected-sorted.bus');
  const correctedInfo = busRecords(correctedSorted);
  assert.ok(correctedInfo.readCount <= sortedInfo.readCount, 'Correction does not create reads');
  await choose(page, 'count'); await run(page);
  const counts = await downloadBytes(page, 'counts/output.mtx');
  const barcodes = labels(await downloadBytes(page, 'counts/output.barcodes.txt'));
  const genes = labels(await downloadBytes(page, 'counts/output.genes.txt'));
  const dimensions = matrix(counts, barcodes, genes);
  assert.ok(dimensions.molecules <= correctedInfo.readCount, 'Molecule counts do not exceed retained reads');
  assert.ok(barcodes.every(barcode => /^[ACGT]+$/.test(barcode)));
  await page.locator('#result-file').selectOption('/work/counts/output.mtx');
  const countText = counts.toString('utf8');
  assert.equal(await page.locator('#preview').textContent(), countText.length <= 100000 ? countText : countText.slice(0, 100000) + '\n\n[Preview truncated. Download the full file.]');
  passed('sort-correct-sort-count-preview-download', { ...dimensions, sorted: sortedInfo, corrected: correctedInfo, matrixSha256: hash(counts) });

  await choose(page, 'inspect'); await run(page);
  const inspection = JSON.parse((await downloadBytes(page, 'inspection.json')).toString('utf8'));
  assert.equal(inspection.numRecords, correctedInfo.records);
  assert.equal(inspection.numReads, correctedInfo.readCount);
  assert.ok(inspection.numBarcodes >= dimensions.cells);
  assert.equal(inspection.numBarcodesOnOnlist, inspection.numBarcodes);
  await choose(page, 'text'); await run(page);
  const rows = labels(await downloadBytes(page, 'records.txt')).map(line => line.trim().split(/\s+/));
  assert.equal(rows.length, correctedInfo.records);
  assert.ok(rows.every(row => row.length >= 4 && /^[ACGT]+$/.test(row[0]) && /^[ACGT]+$/.test(row[1]) && Number(row[3]) > 0));
  passed('inspect-and-export-real-bus-records', { records: rows.length });

  phase('Compress and restore BUS records without changing binary bytes');
  await choose(page, 'compress'); await run(page);
  const compressed = await downloadBytes(page, 'records.busz'); assert.equal(compressed.subarray(0, 4).toString('hex'), '42555301');
  await choose(page, 'inflate'); await run(page);
  assert.deepEqual(await downloadBytes(page, 'restored.bus'), correctedSorted);
  passed('lossless-binary-compress-inflate', { busSha256: hash(correctedSorted), compressedSha256: hash(compressed) });

  phase('Upload BUS bytes and regenerate an output path with a different sort order');
  const fixtureBase = fileURLToPath(new URL(config.datasets.bustools.path, new URL('../examples/bustools/app.mjs', import.meta.url)));
  const sourceBus = await readFile(join(fixtureBase, 'input.bus'));
  await page.locator('#upload').setInputFiles({ name: 'uploaded.bus', mimeType: 'application/octet-stream', buffer: sourceBus });
  await page.waitForFunction(() => document.getElementById('files').textContent.includes('uploaded.bus'));
  const sortArgs = [...config.tools.bustools.commands.sort.args]; sortArgs[sortArgs.length - 1] = 'uploaded.bus';
  await edit(page, [...sortArgs.slice(0, 1), '--umi', ...sortArgs.slice(1)], ['sorted.bus']); await run(page);
  const umiSorted = await downloadBytes(page, 'sorted.bus'); busRecords(umiSorted); assert.notDeepEqual(umiSorted, sorted);
  await edit(page, sortArgs, ['sorted.bus']); await run(page); assert.deepEqual(await downloadBytes(page, 'sorted.bus'), sorted);
  passed('upload-and-regenerate-same-path');

  const folder = join(reportDir, 'uploaded-bus'); await mkdir(folder, { recursive: true });
  await copyFile(join(fixtureBase, 'input.bus'), join(folder, 'output.bus'));
  await page.locator('#upload-folder').setInputFiles(folder);
  await page.waitForFunction(() => document.getElementById('files').textContent.includes('uploaded-bus/output.bus'));
  await edit(page, ['sort', '-t', '1', '-m', '64M', '-T', 'uploaded-sort-temp', '-o', 'uploaded-bus/sorted.bus', 'uploaded-bus/output.bus'], ['uploaded-bus/sorted.bus'], ['uploaded-bus']);
  await run(page);
  assert.deepEqual(await downloadBytes(page, 'uploaded-bus/output.bus'), sourceBus);
  assert.deepEqual(await downloadBytes(page, 'uploaded-bus/sorted.bus'), sorted);
  passed('directory-collection-retains-source-bytes');

  phase('Reject invalid commands and prevent stale output reuse');
  // Snapshot current bytes immediately before failures; outputs from earlier
  // successful commands may contain upstream metadata that changes per run.
  const retained = await downloadBytes(page, 'sorted.bus');
  await page.locator('#arguments').fill('["sort", 5]');
  const before = (await workerCalls(page)).length; await page.locator('#run').click();
  assert.match(await page.locator('#status').innerText(), /^Check command: Arguments must be a JSON array of strings/);
  assert.equal((await workerCalls(page)).length, before);
  assert.deepEqual(await downloadBytes(page, 'sorted.bus'), retained);
  passed('invalid-arguments-preserve-files');
  await edit(page, ['sort'], ['sorted.bus']); await page.locator('#run').click();
  assert.match(await finished(page), /Missing requested files:/);
  assert.deepEqual(await downloadBytes(page, 'sorted.bus'), retained);
  passed('stale-output-not-reused');
  await edit(page, ['sort', '--not-an-option']); await page.locator('#run').click();
  assert.match(await finished(page), /^Exited with status [1-9]\d* /);
  assert.ok((await page.locator('#stdout').innerText()) + (await page.locator('#stderr').innerText()));
  assert.deepEqual(await downloadBytes(page, 'sorted.bus'), retained);
  passed('failed-command-preserves-files');

  phase('Cancel a held WebAssembly request and rerun the original command');
  await choose(page, 'sort');
  const held = host.holdNextRequest('/dist/bustools/bustools.wasm');
  try {
    await page.locator('#run').click(); await deadline(held.requested, 60000, 'The held WebAssembly request did not arrive');
    await page.locator('#cancel').click(); await page.waitForFunction(() => document.getElementById('status').textContent === 'Command cancelled.');
  } finally { held.release(); }
  assert.deepEqual(await downloadBytes(page, 'sorted.bus'), retained);
  await run(page); assert.deepEqual(await downloadBytes(page, 'sorted.bus'), sorted);
  passed('cancel-and-rerun');
  await page.locator('#result-file').selectOption('/work/counts/output.mtx');
  await page.screenshot({ path: join(reportDir, 'bustools.png'), fullPage: true });

  phase('Reload clears files and all command help remains usable');
  summary.workerCalls = await workerCalls(page);
  await page.reload(); await ready(page);
  assert.equal((await page.locator('#files').innerText()).trim(), 'No files loaded.'); assert.equal(await page.locator('#downloads a').count(), 0);
  for (const command of commands) {
    await edit(page, [command]); await run(page);
    assert.match((await page.locator('#stdout').innerText()) + (await page.locator('#stderr').innerText()), /Usage: bustools /);
  }
  await edit(page, ['version']); await run(page); assert.match(await page.locator('#stdout').innerText(), /bustools, version 0\.45\.1/);
  await edit(page, ['cite']); await run(page); assert.match(await page.locator('#stdout').innerText(), /10\.1038\/s41587-021-00870-2/);
  summary.workerCalls.push(...await workerCalls(page));
  assert.equal(await page.evaluate(() => crossOriginIsolated), false);
  assert.equal(await page.evaluate(async () => (await navigator.serviceWorker.getRegistrations()).length), 0);
  passed('reload-clear-all-command-help', { commands, informationalCommands: ['version', 'cite'] });
  const paths = summary.requests.filter(request => /^https?:/.test(request.url)).map(request => new URL(request.url).pathname);
  for (const extension of ['mjs', 'wasm']) assert.ok(paths.includes(`/dist/bustools/bustools.${extension}`), `Real local asset fetched: bustools.${extension}`);
  assert.ok(summary.workerCalls.length >= commands.length + 15);
  assert.ok(summary.workerCalls.every(call => new URL(call.moduleUrl).pathname === '/dist/bustools/bustools.mjs'));
  assert.deepEqual(summary.externalRequests, []); assert.deepEqual(summary.browserErrors, []);
  passed('all-runtime-assets-local');
  summary.status = 'passed'; phase(`Passed ${summary.cases.length} UI checks`);
} catch (error) {
  summary.status = 'failed'; summary.error = error.stack; process.exitCode = 1;
  if (page) {
    summary.workerCalls = await deadline(workerCalls(page), 3000, 'Worker diagnostics timed out').catch(() => summary.workerCalls);
    await deadline(page.screenshot({ path: join(reportDir, 'failure.png'), fullPage: true }), 5000, 'Failure screenshot timed out').catch(() => {});
    for (const id of ['status', 'arguments', 'outputs', 'output-directories', 'stdout', 'stderr']) {
      const content = await deadline(page.locator(`#${id}`).evaluate(element => element.value ?? element.textContent, undefined, { timeout: 2000 }), 3000, `Failure diagnostic ${id} timed out`).catch(() => '(unavailable)');
      await writeFile(join(reportDir, `${id}.txt`), content);
    }
    console.error(JSON.stringify({ console: summary.browserConsole.slice(-15), failedRequests: summary.failedRequests.slice(-15), externalRequests: summary.externalRequests }, null, 2));
  }
  console.error(error);
} finally {
  summary.finishedAt = new Date().toISOString();
  await writeFile(join(reportDir, 'summary.json'), `${JSON.stringify(summary, null, 2)}\n`);
  try { if (browser) await deadline(browser.close(), 30000, 'Browser cleanup exceeded 30 seconds'); }
  catch (error) { summary.status = 'failed'; summary.cleanupError = error.message; process.exitCode = 1; console.error(error); }
  if (host) {
    host.server.closeAllConnections();
    await deadline(new Promise(resolve => host.server.close(resolve)), 5000, 'Server cleanup exceeded 5 seconds').catch(error => { summary.status = 'failed'; summary.cleanupError = error.message; process.exitCode = 1; });
  }
  await writeFile(join(reportDir, 'summary.json'), `${JSON.stringify(summary, null, 2)}\n`);
  if (summary.cleanupError) process.exit(1);
}
