// Browser integration using actual quantifiers and real RNA read subsets.
// Numerical agreement against native programs is checked by validate-quant.mjs.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { createHash } from 'node:crypto';
import { mkdir, readFile, readdir, stat, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { extname, join, resolve, sep } from 'node:path';
import { chromium, firefox, webkit } from 'playwright';

const root = fileURLToPath(new URL('../', import.meta.url));
const engine = process.env.BROWSER || 'chromium';
const browserType = { chromium, firefox, webkit }[engine];
if (!browserType) throw new Error('BROWSER must be chromium, firefox or webkit');
const selected = process.env.QUANT_TOOLS || process.env.QUANT_TOOL || 'kallisto,salmon';
const tools = selected.split(',');
if (!tools.length || tools.some(tool => !['kallisto', 'salmon'].includes(tool))) throw new Error('QUANT_TOOLS must contain kallisto and/or salmon, separated by commas');
const reportDir = resolve(process.env.QUANT_DEMO_REPORT || join(root, 'reports/quant', engine, 'demo'));
await mkdir(reportDir, { recursive: true });
const summary = { schema: 1, engine, status: 'running', startedAt: new Date().toISOString(), phases: [], cases: [], builds: {}, browserErrors: [] };
const phase = message => { summary.phases.push({ at: new Date().toISOString(), message }); console.log(`[quant demo] ${message}`); };
for (const tool of tools) {
  await stat(join(root, 'dist', tool, `${tool}.wasm`));
  summary.builds[tool] = {};
  for (const file of (await readdir(join(root, 'dist', tool))).filter(file => /\.(wasm|m?js)$/.test(file))) {
    summary.builds[tool][file] = createHash('sha256').update(await readFile(join(root, 'dist', tool, file))).digest('hex');
  }
}

const mime = { '.html': 'text/html', '.mjs': 'text/javascript', '.js': 'text/javascript', '.css': 'text/css', '.wasm': 'application/wasm', '.json': 'application/json' };
async function serve(isolated) {
  const server = createServer(async (request, response) => {
    if (isolated) {
      response.setHeader('Cross-Origin-Opener-Policy', 'same-origin');
      response.setHeader('Cross-Origin-Embedder-Policy', 'require-corp');
    }
    response.setHeader('Cache-Control', 'no-store');
    try {
      const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
      if (pathname === '/outside-example.html') {
        response.writeHead(200, { 'Content-Type': 'text/html' }).end('<!doctype html><title>Outside quant example</title>');
        return;
      }
      const path = resolve(root, `.${pathname}`);
      if (!path.startsWith(root.endsWith(sep) ? root : `${root}${sep}`)) { response.writeHead(403).end(); return; }
      const filename = (await stat(path)).isDirectory() ? join(path, 'index.html') : path;
      response.writeHead(200, { 'Content-Type': mime[extname(filename)] || 'application/octet-stream' }).end(await readFile(filename));
    } catch { response.writeHead(404).end('Not found'); }
  });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  return { server, origin: `http://127.0.0.1:${server.address().port}` };
}

let browser, page, withHeaders, plain;
try {
  withHeaders = await serve(true);
  plain = await serve(false);
  browser = await browserType.launch({ headless: true });
  summary.browserVersion = browser.version();
  phase(`Browser ${engine} ${summary.browserVersion}`);
  const context = await browser.newContext({ viewport: { width: 1100, height: 900 } });
  page = await context.newPage();
  page.on('pageerror', error => summary.browserErrors.push(error.message));
  await page.goto(`${withHeaders.origin}/examples/quant/`);
  await ready(page);
  assert.equal(await page.evaluate(() => crossOriginIsolated), true);

  for (const tool of tools) {
    phase(`${tool}: load real human inputs`);
    await page.locator('#tool').selectOption(tool);
    await page.locator('#dataset').selectOption('human');
    await page.locator('#load-data').click();
    await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Example data loaded.'), null, { timeout: 60000 });
    assert.equal(await page.locator('#files li').count(), 3);

    // Upload one real read file again to exercise the user-file path.
    await page.locator('#upload').setInputFiles({
      name: 'reads_1.fastq', mimeType: 'application/octet-stream',
      buffer: await readFile(join(root, 'datasets/rna-quant/human/reads_1.fastq')),
    });
    await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Files loaded.'));
    assert.equal(await page.locator('#files li').count(), 3);

    phase(`${tool}: index`);
    await page.locator('#command').selectOption('index');
    await run(page);
    const fileNames = await page.locator('#files').innerText();
    assert.match(fileNames, tool === 'kallisto' ? /kallisto\.idx/ : /salmon-index\//);
    summary.cases.push({ id: `${tool}-demo-index`, status: 'passed' });

    phase(`${tool}: quantify and download`);
    await page.locator('#command').selectOption('quant');
    await run(page);
    const output = tool === 'kallisto' ? 'kallisto-out/abundance.tsv' : 'salmon-out/quant.sf';
    const downloaded = await downloadBytes(page, `Download ${output}`);
    assert.equal(downloaded.name, output.split('/').at(-1));
    const lines = downloaded.bytes.toString('utf8').trimEnd().split('\n');
    const expectedColumns = tool === 'kallisto'
      ? ['target_id', 'length', 'eff_length', 'est_counts', 'tpm']
      : ['Name', 'Length', 'EffectiveLength', 'TPM', 'NumReads'];
    assert.deepEqual(lines[0].split('\t'), expectedColumns);
    assert.ok(lines.length > 1, `${tool} returned transcript rows`);
    const countColumn = tool === 'kallisto' ? 3 : 4;
    const rows = lines.slice(1).map(line => line.split('\t'));
    assert.ok(rows.some(row => Number(row[countColumn]) > 0), `${tool} assigned real reads`);
    for (const row of rows) {
      assert.equal(row.length, expectedColumns.length);
      for (const value of row.slice(1)) assert.ok(Number.isFinite(Number(value)) && Number(value) >= 0, `${tool} has a valid numeric output`);
    }
    assert.equal(await page.locator('#result-file').inputValue(), `/work/${output}`);
    assert.ok((await page.locator('#preview').innerText()).startsWith(lines[0]));
    summary.cases.push({ id: `${tool}-demo-quant-download`, status: 'passed', transcriptRows: rows.length, outputSha256: createHash('sha256').update(downloaded.bytes).digest('hex') });

    phase(`${tool}: cancel during worker loading and restart`);
    await page.locator('#command').selectOption('help');
    let releaseRequest, moduleRequested;
    const pendingRequest = new Promise(resolve => { releaseRequest = resolve; });
    const requestEntered = new Promise(resolve => { moduleRequested = resolve; });
    const moduleRoute = `**/dist/${tool}/${tool}.mjs`;
    await page.route(moduleRoute, async route => { moduleRequested(); await pendingRequest; await route.continue().catch(() => {}); });
    let loadingDeadline;
    try {
      await page.locator('#run').click();
      await Promise.race([requestEntered, new Promise((_, reject) => { loadingDeadline = setTimeout(() => reject(new Error('The tool worker did not request its module')), 30000); })]);
      await page.locator('#cancel').click();
      await page.waitForFunction(() => document.getElementById('status').textContent === 'Command cancelled.');
    } finally {
      clearTimeout(loadingDeadline);
      releaseRequest();
      await page.unroute(moduleRoute);
    }
    await run(page);
    assert.match(`${await page.locator('#stdout').innerText()}${await page.locator('#stderr').innerText()}`, new RegExp(tool, 'i'));
    summary.cases.push({ id: `${tool}-demo-cancel-loading-restart`, status: 'passed' });
  }

  if (tools.includes('salmon')) {
    phase('Salmon: static hosting service worker');
    const staticContext = await browser.newContext();
    const staticPage = await staticContext.newPage();
    staticPage.on('pageerror', error => summary.browserErrors.push(error.message));
    await staticPage.goto(`${plain.origin}/examples/quant/`);
    await ready(staticPage);
    assert.equal(await staticPage.evaluate(() => crossOriginIsolated), false);
    await staticPage.locator('#tool').selectOption('salmon');
    await staticPage.locator('#command').selectOption('help');
    await staticPage.locator('#run').click();
    assert.match(await staticPage.locator('#status').innerText(), /^Salmon cannot start:/);
    await Promise.all([
      staticPage.waitForEvent('load'),
      staticPage.locator('#enable-threads').click(),
    ]);
    await ready(staticPage);
    assert.equal(await staticPage.evaluate(() => crossOriginIsolated), true, 'The example becomes isolated on plain static hosting');
    await staticPage.locator('#tool').selectOption('salmon');
    await staticPage.locator('#command').selectOption('help');
    await run(staticPage);
    assert.match(`${await staticPage.locator('#stdout').innerText()}${await staticPage.locator('#stderr').innerText()}`, /salmon/i);
    // Version alone does not start the indexing/quantification worker threads.
    // Exercise the actual program through service-worker headers as well.
    await staticPage.locator('#load-data').click();
    await staticPage.waitForFunction(() => document.getElementById('status').textContent.startsWith('Example data loaded.'), null, { timeout: 60000 });
    await staticPage.locator('#command').selectOption('index');
    await run(staticPage);
    await staticPage.locator('#command').selectOption('quant');
    await run(staticPage);
    const staticResult = await downloadBytes(staticPage, 'Download salmon-out/quant.sf');
    assert.match(staticResult.bytes.toString('utf8'), /^Name\tLength\tEffectiveLength\tTPM\tNumReads\n/);
    assert.ok(staticResult.bytes.toString('utf8').trimEnd().split('\n').slice(1).some(line => Number(line.split('\t')[4]) > 0), 'Salmon assigns real reads on plain static hosting');
    await staticPage.goto(`${plain.origin}/outside-example.html`);
    assert.equal(await staticPage.evaluate(() => !!navigator.serviceWorker.controller), false, 'The isolation worker does not control other examples');
    assert.equal(await staticPage.evaluate(() => crossOriginIsolated), false);
    await staticContext.close();
    summary.cases.push({ id: 'salmon-demo-static-hosting', status: 'passed' });
  }
  assert.deepEqual(summary.browserErrors, [], 'No uncaught browser errors');
  summary.status = 'passed';
  phase(`Passed ${summary.cases.length} UI checks`);
} catch (error) {
  summary.status = 'failed';
  summary.error = error.stack;
  if (page) {
    await page.screenshot({ path: join(reportDir, 'failure.png'), fullPage: true }).catch(() => {});
    for (const id of ['status', 'arguments', 'outputs', 'output-directories', 'stdout', 'stderr']) {
      await writeFile(join(reportDir, `${id}.txt`), await page.locator(`#${id}`).evaluate(element => element.value ?? element.textContent).catch(() => '(unavailable)'));
    }
  }
  process.exitCode = 1;
  console.error(error);
} finally {
  await browser?.close();
  for (const host of [withHeaders, plain]) if (host) await new Promise(resolve => host.server.close(resolve));
  summary.finishedAt = new Date().toISOString();
  await writeFile(join(reportDir, 'summary.json'), `${JSON.stringify(summary, null, 2)}\n`);
}

async function ready(page) {
  await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Ready.'), null, { timeout: 30000 });
}

async function run(page) {
  await page.locator('#run').click();
  await page.waitForFunction(() => /^(Exited with status|Error:|RuntimeError:|TimeoutError:|TypeError:|RangeError:)/.test(document.getElementById('status').textContent), null, { timeout: 360000 });
  const status = await page.locator('#status').innerText();
  assert.match(status, /^Exited with status 0 /, status);
  assert.doesNotMatch(status, /Missing requested files:/);
}

async function downloadBytes(page, name) {
  const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('link', { name, exact: true }).click()]);
  const path = await download.path();
  assert.ok(path, 'A downloaded file is available');
  return { name: download.suggestedFilename(), bytes: await readFile(path) };
}
