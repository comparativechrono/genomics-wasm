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
const summary = { schema: 1, engine, status: 'running', startedAt: new Date().toISOString(), phases: [], cases: [], builds: {}, browserErrors: [], browserConsole: [], failedRequests: [] };
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
  const heldRequests = new Map();
  const server = createServer(async (request, response) => {
    if (isolated) {
      response.setHeader('Cross-Origin-Opener-Policy', 'same-origin');
      response.setHeader('Cross-Origin-Embedder-Policy', 'require-corp');
    }
    response.setHeader('Cache-Control', 'no-store');
    try {
      const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
      const held = heldRequests.get(pathname);
      if (held) {
        heldRequests.delete(pathname);
        held.arrived();
        await held.unblocked;
      }
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
  return {
    server, origin: `http://127.0.0.1:${server.address().port}`,
    holdNextRequest(pathname) {
      assert.equal(heldRequests.has(pathname), false, `A request hold already exists for ${pathname}`);
      let arrived, unblock;
      const requested = new Promise(resolve => { arrived = resolve; });
      const unblocked = new Promise(resolve => { unblock = resolve; });
      const held = { arrived, unblocked };
      heldRequests.set(pathname, held);
      return {
        requested,
        release() {
          if (heldRequests.get(pathname) === held) heldRequests.delete(pathname);
          unblock();
        },
      };
    },
  };
}

function observe(page, label) {
  page.on('pageerror', error => summary.browserErrors.push(`${label}: ${error.message}`));
  page.on('console', message => summary.browserConsole.push({ page: label, type: message.type(), text: message.text() }));
  page.on('requestfailed', request => summary.failedRequests.push({ page: label, url: request.url(), error: request.failure()?.errorText }));
}

async function deadline(promise, timeoutMs, message) {
  let timer;
  try {
    return await Promise.race([
      promise,
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(message)), timeoutMs); }),
    ]);
  } finally { clearTimeout(timer); }
}

let browser, page, activePage, withHeaders, plain;
try {
  withHeaders = await serve(true);
  plain = await serve(false);
  browser = await browserType.launch({ headless: true });
  summary.browserVersion = browser.version();
  phase(`Browser ${engine} ${summary.browserVersion}`);
  const context = await browser.newContext({ viewport: { width: 1100, height: 900 } });
  context.setDefaultTimeout(30000);
  page = await context.newPage();
  activePage = page;
  observe(page, 'header-hosting');
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
    const cancelStep = async (name, action) => {
      phase(`${tool}: cancellation ${name} starting`);
      const result = await deadline(action(), 30000, `${tool}: cancellation ${name} exceeded 30 seconds`);
      phase(`${tool}: cancellation ${name} completed`);
      return result;
    };
    await cancelStep('select command', () => page.locator('#command').selectOption('help'));
    // Hold the actual HTTP response. Enabling Chromium's DevTools request
    // interception after pthread workers exit can itself stall page.route().
    const heldModule = withHeaders.holdNextRequest(`/dist/${tool}/${tool}.mjs`);
    phase(`${tool}: cancellation request hold installed`);
    try {
      await cancelStep('start command', () => page.locator('#run').click());
      await cancelStep('wait for held module request', () => heldModule.requested);
      await cancelStep('click cancel', () => page.locator('#cancel').click());
      await cancelStep('check cancelled status', () => page.waitForFunction(() => document.getElementById('status').textContent === 'Command cancelled.'));
    } finally {
      heldModule.release();
      phase(`${tool}: cancellation request hold released`);
    }
    phase(`${tool}: cancellation restart starting`);
    await run(page);
    phase(`${tool}: cancellation restart completed`);
    const restartOutput = await cancelStep('read restarted command output', () => Promise.all([page.locator('#stdout').innerText(), page.locator('#stderr').innerText()]));
    assert.match(restartOutput.join(''), new RegExp(tool, 'i'));
    summary.cases.push({ id: `${tool}-demo-cancel-loading-restart`, status: 'passed' });
  }

  if (tools.includes('salmon')) {
    phase('Salmon: static hosting service worker');
    const staticContext = await browser.newContext();
    staticContext.setDefaultTimeout(30000);
    const staticPage = await staticContext.newPage();
    activePage = staticPage;
    observe(staticPage, 'static-hosting');
    await staticPage.goto(`${plain.origin}/examples/quant/`);
    await ready(staticPage);
    assert.equal(await staticPage.evaluate(() => crossOriginIsolated), false);
    await staticPage.locator('#tool').selectOption('salmon');
    await staticPage.locator('#command').selectOption('help');
    await staticPage.locator('#run').click();
    assert.match(await staticPage.locator('#status').innerText(), /^Salmon cannot start:/);
    await Promise.all([
      staticPage.waitForEvent('load', { timeout: 45000 }),
      staticPage.locator('#enable-threads').click(),
    ]);
    await ready(staticPage);
    assert.equal(await staticPage.evaluate(() => crossOriginIsolated), true, 'The example becomes isolated on plain static hosting');
    phase('Salmon: verify service-worker asset headers and bytes');
    const assetHashes = { 'dist/salmon/salmon.mjs': summary.builds.salmon['salmon.mjs'], 'dist/salmon/salmon.wasm': summary.builds.salmon['salmon.wasm'] };
    for (const name of ['runtime/worker.mjs', 'runtime/execute.mjs']) assetHashes[name] = createHash('sha256').update(await readFile(join(root, name))).digest('hex');
    const mappedAssets = await deadline(staticPage.evaluate(async names => {
      const results = [];
      for (const name of names) {
        const response = await fetch(new URL(`__assets__/${name}`, location.href), { signal: AbortSignal.timeout(30000) });
        const bytes = await response.arrayBuffer();
        const sha256 = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(byte => byte.toString(16).padStart(2, '0')).join('');
        results.push({ name, status: response.status, coop: response.headers.get('Cross-Origin-Opener-Policy'), coep: response.headers.get('Cross-Origin-Embedder-Policy'), sha256 });
      }
      return results;
    }, Object.keys(assetHashes)), 45000, 'Service-worker asset verification exceeded 45 seconds');
    for (const asset of mappedAssets) {
      assert.equal(asset.status, 200, asset.name);
      assert.equal(asset.coop, 'same-origin', asset.name);
      assert.equal(asset.coep, 'require-corp', asset.name);
      assert.equal(asset.sha256, assetHashes[asset.name], `The service worker returns unchanged ${asset.name}`);
    }
    summary.staticAssets = mappedAssets;
    const rejected = await deadline(staticPage.evaluate(async () => {
      const names = ['not-a-tool.mjs', 'runtime/%2e%2e%2f%2e%2e%2fpackage.json'];
      return Promise.all(names.map(async name => (await fetch(new URL(`__assets__/${name}`, location.href), { signal: AbortSignal.timeout(30000) })).status));
    }), 45000, 'Service-worker path checks exceeded 45 seconds');
    assert.deepEqual(rejected, [404, 404], 'The service worker rejects unknown assets and encoded traversal');
    phase('Salmon: static-hosting version');
    await staticPage.locator('#tool').selectOption('salmon');
    await staticPage.locator('#command').selectOption('help');
    await run(staticPage);
    assert.match(`${await staticPage.locator('#stdout').innerText()}${await staticPage.locator('#stderr').innerText()}`, /salmon/i);
    // Version alone does not start the indexing/quantification worker threads.
    // Exercise the actual program through service-worker headers as well.
    await staticPage.locator('#load-data').click();
    await staticPage.waitForFunction(() => document.getElementById('status').textContent.startsWith('Example data loaded.'), null, { timeout: 60000 });
    phase('Salmon: static-hosting index');
    await staticPage.locator('#command').selectOption('index');
    await run(staticPage);
    phase('Salmon: static-hosting quantification');
    await staticPage.locator('#command').selectOption('quant');
    await run(staticPage);
    const staticResult = await downloadBytes(staticPage, 'Download salmon-out/quant.sf');
    assert.match(staticResult.bytes.toString('utf8'), /^Name\tLength\tEffectiveLength\tTPM\tNumReads\n/);
    assert.ok(staticResult.bytes.toString('utf8').trimEnd().split('\n').slice(1).some(line => Number(line.split('\t')[4]) > 0), 'Salmon assigns real reads on plain static hosting');
    await staticPage.goto(`${plain.origin}/outside-example.html`);
    assert.equal(await staticPage.evaluate(() => !!navigator.serviceWorker.controller), false, 'The isolation worker does not control other examples');
    assert.equal(await staticPage.evaluate(() => crossOriginIsolated), false);
    // Both contexts are closed by the bounded browser cleanup below.
    activePage = page;
    summary.cases.push({ id: 'salmon-demo-static-hosting', status: 'passed' });
  }
  assert.deepEqual(summary.browserErrors, [], 'No uncaught browser errors');
  summary.status = 'passed';
  phase(`Passed ${summary.cases.length} UI checks`);
} catch (error) {
  summary.status = 'failed';
  summary.error = error.stack;
  if (activePage) {
    summary.failurePage = activePage.url();
    await deadline(activePage.screenshot({ path: join(reportDir, 'failure.png'), fullPage: true }), 5000, 'Failure screenshot exceeded 5 seconds').catch(() => {});
    for (const id of ['status', 'arguments', 'outputs', 'output-directories', 'stdout', 'stderr']) {
      await writeFile(join(reportDir, `${id}.txt`), await deadline(activePage.locator(`#${id}`).evaluate(element => element.value ?? element.textContent, undefined, { timeout: 2000 }), 3000, `Failure diagnostic ${id} exceeded 3 seconds`).catch(() => '(unavailable)'));
    }
    console.error(JSON.stringify({ page: summary.failurePage, status: await deadline(activePage.locator('#status').textContent({ timeout: 2000 }), 3000, 'Failure status exceeded 3 seconds').catch(() => null), console: summary.browserConsole.slice(-15), failedRequests: summary.failedRequests.slice(-15) }, null, 2));
  }
  process.exitCode = 1;
  console.error(error);
} finally {
  summary.finishedAt = new Date().toISOString();
  // Preserve the result before asking a failed browser process to shut down.
  await writeFile(join(reportDir, 'summary.json'), `${JSON.stringify(summary, null, 2)}\n`);
  let closingDeadline;
  try {
    await Promise.race([
      browser?.close(),
      new Promise((_, reject) => { closingDeadline = setTimeout(() => reject(new Error('Browser cleanup exceeded 30 seconds')), 30000); }),
    ]);
  } catch (error) {
    summary.status = 'failed';
    summary.cleanupError = error.message;
    process.exitCode = 1;
    console.error(error);
  } finally { clearTimeout(closingDeadline); }
  for (const host of [withHeaders, plain]) if (host) {
    host.server.closeAllConnections();
    await new Promise(resolve => host.server.close(resolve));
  }
  await writeFile(join(reportDir, 'summary.json'), `${JSON.stringify(summary, null, 2)}\n`);
  // A disconnected Playwright client can otherwise keep Node alive forever.
  // This is a failed test, never a way to convert a browser failure to success.
  if (summary.cleanupError) process.exit(1);
}

async function ready(page) {
  await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Ready.'), null, { timeout: 30000 });
}

async function run(page) {
  await deadline(page.locator('#run').click(), 30000, 'Starting command exceeded 30 seconds');
  await deadline(page.waitForFunction(() => /^(Exited with status|Error:|RuntimeError:|TimeoutError:|TypeError:|RangeError:)/.test(document.getElementById('status').textContent), null, { timeout: 360000 }), 365000, 'Waiting for command status exceeded 365 seconds');
  const status = await deadline(page.locator('#status').innerText(), 30000, 'Reading command status exceeded 30 seconds');
  assert.match(status, /^Exited with status 0 /, status);
  assert.doesNotMatch(status, /Missing requested files:/);
}

async function downloadBytes(page, name) {
  const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('link', { name, exact: true }).click()]);
  const path = await download.path();
  assert.ok(path, 'A downloaded file is available');
  return { name: download.suggestedFilename(), bytes: await readFile(path) };
}
