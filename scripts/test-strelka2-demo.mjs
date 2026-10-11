// UI integration with the actual locally bundled callers and real tumour/normal reads.
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
const tool = 'strelka2';
const browserType = { chromium, firefox, webkit }[engine];
if (!browserType) throw new Error('BROWSER must be chromium, firefox or webkit');
const reportDir = resolve(process.env.STRELKA2_DEMO_REPORT || join(root, 'reports/strelka2', engine, 'demo'));
await mkdir(reportDir, { recursive: true });
const summary = { schema: 1, engine, tool, status: 'running', startedAt: new Date().toISOString(), phases: [], cases: [], assets: {}, browserErrors: [], browserConsole: [], requests: [], failedRequests: [], externalRequests: [] };
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const phase = message => { summary.phases.push({ at: new Date().toISOString(), message }); console.log(`[Strelka2 demo] ${message}`); };
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

function variantRecords(bytes) {
  const value = bytes.toString('utf8');
  assert.match(value, /^##fileformat=VCF/m);
  assert.match(value, /^#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO/m);
  return value.split(/\r?\n/).filter(line => line && !line.startsWith('#')).map(line => {
    const fields = line.split('\t');
    assert.ok(fields.length >= 8, 'Complete VCF record');
    assert.ok(Number.isSafeInteger(Number(fields[1])) && Number(fields[1]) > 0);
    return line;
  });
}

async function ready(page) {
  await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Ready.'), null, { timeout: 30000 });
}

async function run(page) {
  await page.locator('#run').click();
  await deadline(page.waitForFunction(() => {
    const status = document.getElementById('status').textContent;
    return !status.startsWith('Running ') && !document.getElementById('controls').disabled;
  }, null, { timeout: 360000 }), 365000, 'Waiting for somatic caller exceeded 365 seconds');
  const status = await page.locator('#status').innerText();
  assert.match(status, /^Exited with status 0 /, status);
  assert.doesNotMatch(status, /Missing requested files:/);
}

async function downloadBytes(page, name) {
  const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('link', { name: `Download ${name}`, exact: true }).click()]);
  assert.equal(download.suggestedFilename(), name.split('/').at(-1));
  const path = await download.path(); assert.ok(path);
  return readFile(path);
}

let browser, page, host;
try {
  await stat(join(root, `dist/${tool}/${tool}.mjs`));
  async function recordAssets(directory, prefix = '') {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const name = prefix + entry.name, path = join(directory, entry.name);
      if (entry.isDirectory()) await recordAssets(path, `${name}/`);
      else if (/\.(m?js|wasm|whl|zip|json)$/.test(name)) summary.assets[name] = hash(await readFile(path));
    }
  }
  await recordAssets(join(root, `dist/${tool}`));
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
  await page.goto(`${host.origin}/examples/strelka2/`);
  await ready(page);
  await page.locator('#tool').selectOption(tool);
  assert.equal(await page.evaluate(() => crossOriginIsolated), false);
  assert.equal(await page.evaluate(() => typeof SharedArrayBuffer), 'undefined');
  assert.equal(await page.evaluate(() => !!navigator.serviceWorker?.controller), false);
  summary.cases.push({ id: 'ordinary-static-hosting', status: 'passed' });

  phase(`${tool}: load matched tumour/normal data`);
  await page.locator('#dataset').selectOption('hcc1395');
  await page.locator('#load-data').click();
  await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Example data loaded.'), null, { timeout: 60000 });
  assert.equal(await page.locator('#files li').count(), 9);
  await page.locator('#command').selectOption('somatic');
  await run(page);
  const name = 'strelka/results/variants/somatic.snvs.vcf.gz';
  let original = await downloadBytes(page, name);
  const decoded = name.endsWith('.gz') ? gunzipSync(original) : original;
  const records = variantRecords(decoded);
  assert.ok(records.length > 0, 'The real-data somatic result must be nonempty');
  await page.locator('#result-file').selectOption(`/work/${name}`);
  assert.match(await page.locator('#preview').innerText(), name.endsWith('.gz') ? /^Compressed or binary file/ : /^##fileformat=VCF/);
  const indels = await downloadBytes(page, 'strelka/results/variants/somatic.indels.vcf.gz');
  const indelRecords = variantRecords(gunzipSync(indels));
  assert.ok(indelRecords.length > 0, 'The real-data workflow must call an indel');
  assert.ok(indelRecords.some(record => { const fields = record.split('\t'); return fields[3].length !== fields[4].length; }));
  summary.cases.push({ id: 'somatic-preview-download', status: 'passed', variants: records.length, indels: indelRecords.length, sha256: hash(original) });

  phase('Upload real BAM and index, edit arguments, retain earlier outputs');
  const tumor = await readFile(join(root, 'datasets/strelka2/hcc1395/tumor.bam'));
  const tumorIndex = await readFile(join(root, 'datasets/strelka2/hcc1395/tumor.bam.bai'));
  await page.locator('#upload').setInputFiles([
    { name: 'uploaded.bam', mimeType: 'application/octet-stream', buffer: tumor },
    { name: 'uploaded.bam.bai', mimeType: 'application/octet-stream', buffer: tumorIndex },
  ]);
  await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Files loaded.'));
  const args = JSON.parse(await page.locator('#arguments').inputValue());
  const changed = args.map(arg => arg === 'tumor.bam' ? 'uploaded.bam' : arg === 'strelka' ? 'uploaded' : arg);
  const uploadName = 'uploaded/results/variants/somatic.snvs.vcf.gz';
  await page.locator('#arguments').fill(JSON.stringify(changed));
  await page.locator('#outputs').fill(uploadName);
  await run(page);
  const uploaded = await downloadBytes(page, uploadName);
  assert.deepEqual(variantRecords(uploadName.endsWith('.gz') ? gunzipSync(uploaded) : uploaded), records, 'Uploaded bytes give the same biological records');
  assert.deepEqual(await downloadBytes(page, name), original, 'Earlier result remains unchanged');
  summary.cases.push({ id: 'upload-edit-retain-output', status: 'passed', sha256: hash(uploaded) });

  phase('Report and download callable regions');
  await page.locator('#command').selectOption('callable');
  await run(page);
  const callable = gunzipSync(await downloadBytes(page, 'strelka/results/regions/somatic.callable.regions.bed.gz'));
  assert.ok(callable.length > 0, 'The real-data workflow reports callable regions');
  assert.match(callable.toString('utf8'), /\S+\t\d+\t\d+/);
  assert.deepEqual(variantRecords(gunzipSync(await downloadBytes(page, name))), records);
  original = await downloadBytes(page, name);
  summary.cases.push({ id: 'callable-regions', status: 'passed', bytes: callable.length });

  phase('Reject invalid arguments without starting the tool');
  await page.locator('#arguments').fill('["--help", 5]');
  await page.locator('#run').click();
  assert.match(await page.locator('#status').innerText(), /^Check command: Arguments must be a JSON array of strings/);
  assert.deepEqual(await downloadBytes(page, name), original);
  summary.cases.push({ id: 'invalid-arguments-preserve-files', status: 'passed' });

  phase('Cancel a held program request, then run caller help');
  await page.locator('#command').selectOption('help');
  await page.locator('#arguments').fill(JSON.stringify(['strelka2', '--help']));
  const held = host.holdNextRequest(`/dist/${tool}/${tool}.mjs`);
  try {
    await page.locator('#run').click();
    await deadline(held.requested, 30000, 'The program request did not arrive');
    await page.locator('#cancel').click();
    await page.waitForFunction(() => document.getElementById('status').textContent === 'Command cancelled.');
  } finally { held.release(); }
  assert.deepEqual(await downloadBytes(page, name), original);
  await run(page);
  const upstream = JSON.parse(await readFile(join(root, `tools/${tool}/sources.lock.json`), 'utf8'));
  const versionOutput = (await page.locator('#stdout').innerText()) + (await page.locator('#stderr').innerText());
  assert.ok(versionOutput.includes(upstream.version), 'Caller help reports the pinned upstream version');
  summary.cases.push({ id: 'cancel-restart-help', status: 'passed' });
  await page.screenshot({ path: join(reportDir, 'example.png'), fullPage: true });

  phase('Reload clears files and help still runs');
  await page.reload(); await ready(page);
  await page.locator('#tool').selectOption(tool);
  assert.equal((await page.locator('#files').innerText()).trim(), 'No files loaded.');
  assert.equal(await page.locator('#downloads a').count(), 0);
  await page.locator('#command').selectOption('help'); await run(page);
  assert.match((await page.locator('#stdout').innerText()) + (await page.locator('#stderr').innerText()), /strelka/i);
  assert.equal(await page.evaluate(() => crossOriginIsolated), false);
  assert.equal(await page.evaluate(async () => (await navigator.serviceWorker.getRegistrations()).length), 0);
  summary.cases.push({ id: 'reload-clear-help', status: 'passed' });

  const paths = summary.requests.filter(request => /^https?:/.test(request.url)).map(request => new URL(request.url).pathname);
  assert.ok(paths.includes(`/dist/${tool}/${tool}.mjs`), 'The real local program was fetched');
  assert.ok(paths.some(path => path.startsWith(`/dist/${tool}/`) && path.endsWith('.wasm')), 'The actual WebAssembly runtime was fetched locally');
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
