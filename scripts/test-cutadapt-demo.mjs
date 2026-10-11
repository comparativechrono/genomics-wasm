// UI integration with the actual locally bundled Cutadapt and real read subsets.
// Native-versus-WebAssembly numerical validation is a separate test.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { createHash } from 'node:crypto';
import { mkdir, readFile, readdir, stat, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { extname, join, resolve, sep } from 'node:path';
import { gunzipSync } from 'node:zlib';
import { chromium, firefox, webkit } from 'playwright';

const root = fileURLToPath(new URL('../', import.meta.url));
const engine = process.env.BROWSER || 'chromium';
const browserType = { chromium, firefox, webkit }[engine];
if (!browserType) throw new Error('BROWSER must be chromium, firefox or webkit');
const reportDir = resolve(process.env.CUTADAPT_DEMO_REPORT || join(root, 'reports/cutadapt', engine, 'demo'));
await mkdir(reportDir, { recursive: true });
const summary = { schema: 1, engine, status: 'running', startedAt: new Date().toISOString(), phases: [], cases: [], assets: {}, browserErrors: [], browserConsole: [], requests: [], failedRequests: [], externalRequests: [] };
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const phase = message => { summary.phases.push({ at: new Date().toISOString(), message }); console.log(`[Cutadapt demo] ${message}`); };
const mime = { '.html': 'text/html', '.mjs': 'text/javascript', '.js': 'text/javascript', '.css': 'text/css', '.wasm': 'application/wasm', '.json': 'application/json' };

async function deadline(promise, timeoutMs, message) {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(message)), timeoutMs); })]);
  } finally { clearTimeout(timer); }
}

async function serve() {
  const holds = new Map();
  const server = createServer(async (request, response) => {
    // Deliberately no COOP/COEP headers: this must work on ordinary static hosts.
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
      assert.equal(holds.has(pathname), false, `Request already held: ${pathname}`);
      let arrived, unblock;
      const requested = new Promise(resolve => { arrived = resolve; });
      const unblocked = new Promise(resolve => { unblock = resolve; });
      const held = { arrived, unblocked }; holds.set(pathname, held);
      return { requested, release() { if (holds.get(pathname) === held) holds.delete(pathname); unblock(); } };
    },
  };
}

function fastq(bytes) {
  const lines = bytes.toString('utf8').trimEnd().split('\n');
  assert.ok(bytes.length > 0, 'FASTQ is nonempty');
  assert.equal(lines.length % 4, 0, 'Complete four-line FASTQ records');
  const records = [];
  for (let i = 0; i < lines.length; i += 4) {
    assert.ok(lines[i].startsWith('@') && lines[i + 2].startsWith('+'));
    assert.equal(lines[i + 1].length, lines[i + 3].length, 'Sequence and quality lengths agree');
    records.push({ name: lines[i].slice(1), sequence: lines[i + 1], quality: lines[i + 3] });
  }
  return records;
}

function verifyReads(output, input) {
  const reads = fastq(output), originals = fastq(input);
  const lookup = new Map(originals.map((record, i) => [record.name, { ...record, index: i }]));
  let last = -1;
  for (const read of reads) {
    const original = lookup.get(read.name);
    assert.ok(original, 'Returned read belongs to the real input');
    assert.ok(original.index > last, 'Read order is preserved'); last = original.index;
    assert.ok(read.sequence.length >= 20, 'Preset minimum length is applied');
    assert.equal(read.sequence, original.sequence.slice(0, read.sequence.length), 'Preset trims only the 3-prime sequence');
    assert.equal(read.quality, original.quality.slice(0, read.quality.length), 'Corresponding quality scores are preserved');
  }
  return reads;
}

async function ready(page) {
  await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Ready.'), null, { timeout: 30000 });
}

async function run(page) {
  await page.locator('#run').click();
  await deadline(page.waitForFunction(() => {
    const status = document.getElementById('status').textContent;
    return !status.startsWith('Running Cutadapt') && !document.getElementById('controls').disabled;
  }, null, { timeout: 360000 }), 365000, 'Waiting for Cutadapt exceeded 365 seconds');
  const status = await page.locator('#status').innerText();
  assert.match(status, /^Exited with status 0 /, status);
  assert.doesNotMatch(status, /Missing requested files:/);
}

async function downloadBytes(page, name) {
  const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('link', { name: `Download ${name}`, exact: true }).click()]);
  assert.equal(download.suggestedFilename(), name);
  const path = await download.path(); assert.ok(path);
  return readFile(path);
}

let browser, page, host;
try {
  await stat(join(root, 'dist/cutadapt/manifest.json'));
  async function recordAssets(directory, prefix = '') {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const name = prefix + entry.name, path = join(directory, entry.name);
      if (entry.isDirectory()) await recordAssets(path, `${name}/`);
      else if (/\.(m?js|wasm|whl|zip|json)$/.test(name)) summary.assets[name] = hash(await readFile(path));
    }
  }
  await recordAssets(join(root, 'dist/cutadapt'));
  host = await serve();
  browser = await browserType.launch({ headless: true });
  summary.browserVersion = browser.version();
  const context = await browser.newContext({ viewport: { width: 1100, height: 900 } });
  context.setDefaultTimeout(30000);
  context.on('request', request => {
    summary.requests.push({ url: request.url(), type: request.resourceType() });
    if (/^https?:/.test(request.url()) && new URL(request.url()).origin !== host.origin) summary.externalRequests.push(request.url());
  });
  context.on('requestfailed', request => summary.failedRequests.push({ url: request.url(), error: request.failure()?.errorText }));
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (/^https?:$/.test(url.protocol) && url.origin !== host.origin) {
      summary.externalRequests.push(url.href);
      await route.abort('blockedbyclient');
    } else await route.continue();
  });
  page = await context.newPage();
  page.on('pageerror', error => summary.browserErrors.push(error.message));
  page.on('console', message => summary.browserConsole.push({ type: message.type(), text: message.text() }));
  await page.goto(`${host.origin}/examples/cutadapt/`);
  await ready(page);
  assert.equal(await page.evaluate(() => crossOriginIsolated), false);
  assert.equal(await page.evaluate(() => typeof SharedArrayBuffer), 'undefined');
  assert.equal(await page.evaluate(() => !!navigator.serviceWorker?.controller), false);
  summary.cases.push({ id: 'ordinary-static-hosting', status: 'passed' });

  for (const dataset of ['human', 'yeast']) {
    await page.locator('#dataset').selectOption(dataset);
    await page.locator('#load-data').click();
    await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Example data loaded.'), null, { timeout: 60000 });
    assert.equal(await page.locator('#files li').count(), 2);
    const inputs = await Promise.all([1, 2].map(read => readFile(join(root, `datasets/cutadapt/${dataset}/reads_${read}.fastq`))));
    for (const preset of ['paired', 'single']) {
      phase(`${dataset}: ${preset} preset, preview and downloads`);
      await page.locator('#command').selectOption(preset);
      await run(page);
      const names = preset === 'paired' ? ['trimmed_1.fastq', 'trimmed_2.fastq'] : ['trimmed.fastq'];
      const files = [];
      for (const name of names) files.push(await downloadBytes(page, name));
      const reads = files.map((file, i) => verifyReads(file, inputs[i]));
      if (preset === 'paired') {
        const pairName = record => record.name.split(/\s/)[0].replace(/\/[12]$/, '');
        assert.deepEqual(reads[0].map(pairName), reads[1].map(pairName), 'Paired files retain the same pairs');
      }
      const report = JSON.parse((await downloadBytes(page, 'cutadapt.json')).toString('utf8'));
      assert.equal(report.read_counts.input, fastq(inputs[0]).length);
      assert.equal(report.read_counts.output, reads[0].length);
      if (dataset === 'yeast') assert.ok(report.read_counts.read1_with_adapter > 0, 'Real yeast adapters were detected');
      if (dataset === 'human') assert.ok(report.basepair_counts.quality_trimmed > 0, 'Human quality scores cause trimming');
      await page.locator('#result-file').selectOption(`/work/${names[0]}`);
      assert.ok((await page.locator('#preview').innerText()).startsWith(`@${reads[0][0].name}\n`));
      summary.cases.push({ id: `${dataset}-${preset}-preview-download`, status: 'passed', outputReads: reads[0].length, outputs: Object.fromEntries(names.map((name, i) => [name, hash(files[i])])) });
    }
  }

  phase('Reuse retained output with edited arguments');
  const retained = await downloadBytes(page, 'trimmed.fastq');
  await page.locator('#arguments').fill(JSON.stringify(['--cores', '1', '-m', '20', '-o', 'reused.fastq', 'trimmed.fastq']));
  await page.locator('#outputs').fill('reused.fastq');
  await run(page);
  assert.deepEqual(await downloadBytes(page, 'reused.fastq'), retained);
  summary.cases.push({ id: 'edit-arguments-reuse-output', status: 'passed' });

  phase('Upload real gzip reads and download gzip output');
  const uploaded = await readFile(join(root, 'datasets/cutadapt/human/reads_1.fastq.gz'));
  await page.locator('#upload').setInputFiles({ name: 'uploaded.fastq.gz', mimeType: 'application/gzip', buffer: uploaded });
  await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Files loaded.'));
  await page.locator('#arguments').fill(JSON.stringify(['--cores', '1', '-q', '5', '-m', '20', '-o', 'uploaded-trimmed.fastq.gz', 'uploaded.fastq.gz']));
  await page.locator('#outputs').fill('uploaded-trimmed.fastq.gz');
  await run(page);
  const compressed = await downloadBytes(page, 'uploaded-trimmed.fastq.gz');
  verifyReads(gunzipSync(compressed), gunzipSync(uploaded));
  await page.locator('#result-file').selectOption('/work/uploaded-trimmed.fastq.gz');
  assert.match(await page.locator('#preview').innerText(), /^Compressed or binary file/);
  assert.deepEqual(await downloadBytes(page, 'reused.fastq'), retained, 'Upload preserves earlier output');
  summary.cases.push({ id: 'upload-gzip-preview-download', status: 'passed', sha256: hash(compressed) });

  phase('Reject invalid arguments without starting the tool');
  await page.locator('#arguments').fill('["--help", 5]');
  await page.locator('#run').click();
  assert.match(await page.locator('#status').innerText(), /^Check command: Arguments must be a JSON array of strings/);
  assert.deepEqual(await downloadBytes(page, 'reused.fastq'), retained);
  summary.cases.push({ id: 'invalid-arguments-preserve-files', status: 'passed' });

  phase('Cancel a held manifest request, then run help');
  await page.locator('#command').selectOption('help');
  const held = host.holdNextRequest('/dist/cutadapt/manifest.json');
  try {
    await page.locator('#run').click();
    await deadline(held.requested, 30000, 'The manifest request did not arrive');
    await page.locator('#cancel').click();
    await page.waitForFunction(() => document.getElementById('status').textContent === 'Command cancelled.');
  } finally { held.release(); }
  assert.deepEqual(await downloadBytes(page, 'reused.fastq'), retained);
  await run(page);
  assert.match(await page.locator('#stdout').innerText(), /cutadapt/i);
  summary.cases.push({ id: 'cancel-restart-help', status: 'passed' });
  await page.screenshot({ path: join(reportDir, 'example.png'), fullPage: true });

  phase('Reload clears files and help still runs');
  await page.reload(); await ready(page);
  assert.equal((await page.locator('#files').innerText()).trim(), 'No files loaded.');
  assert.equal(await page.locator('#downloads a').count(), 0);
  await page.locator('#command').selectOption('help'); await run(page);
  assert.match(await page.locator('#stdout').innerText(), /cutadapt/i);
  assert.equal(await page.evaluate(() => crossOriginIsolated), false);
  assert.equal(await page.evaluate(async () => (await navigator.serviceWorker.getRegistrations()).length), 0);
  summary.cases.push({ id: 'reload-clear-help', status: 'passed' });

  const paths = summary.requests.filter(request => /^https?:/.test(request.url)).map(request => new URL(request.url).pathname);
  assert.ok(paths.includes('/dist/cutadapt/manifest.json'), 'The real local manifest was fetched');
  assert.ok(paths.some(path => path.startsWith('/dist/cutadapt/') && path.endsWith('.wasm')), 'The actual WebAssembly runtime was fetched locally');
  assert.ok(paths.some(path => path.startsWith('/dist/cutadapt/') && path.endsWith('.whl')), 'The actual Python wheels were fetched locally');
  assert.deepEqual(summary.externalRequests, [], 'No packages or runtime assets came from external origins');
  assert.deepEqual(summary.browserErrors, []);
  summary.cases.push({ id: 'all-runtime-requests-local', status: 'passed' });
  summary.status = 'passed'; phase(`Passed ${summary.cases.length} UI checks`);
} catch (error) {
  summary.status = 'failed'; summary.error = error.stack; process.exitCode = 1;
  if (page) {
    await deadline(page.screenshot({ path: join(reportDir, 'failure.png'), fullPage: true }), 5000, 'Failure screenshot exceeded 5 seconds').catch(() => {});
    for (const id of ['status', 'arguments', 'outputs', 'output-directories', 'stdout', 'stderr']) {
      const content = await deadline(page.locator(`#${id}`).evaluate(element => element.value ?? element.textContent, undefined, { timeout: 2000 }), 3000, `Failure diagnostic ${id} exceeded 3 seconds`).catch(() => '(unavailable)');
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
