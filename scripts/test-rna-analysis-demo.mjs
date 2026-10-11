// Browser integration using actual RNA analysis tools and real RNA read subsets.
// Numerical agreement against native programs is checked by validate-rna-analysis.mjs.
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
const selected = process.env.RNA_ANALYSIS_TOOLS || process.env.RNA_ANALYSIS_TOOL || 'featurecounts,stringtie,mosdepth';
const tools = selected.split(',');
if (!tools.length || tools.some(tool => !['featurecounts', 'stringtie', 'mosdepth'].includes(tool))) throw new Error('RNA_ANALYSIS_TOOLS must contain featurecounts, stringtie and/or mosdepth, separated by commas');
const reportDir = resolve(process.env.RNA_ANALYSIS_DEMO_REPORT || join(root, 'reports/rna-analysis', engine, `demo-${tools.join('-')}`));
await mkdir(reportDir, { recursive: true });
const summary = { schema: 1, engine, status: 'running', startedAt: new Date().toISOString(), phases: [], cases: [], builds: {}, browserErrors: [], browserConsole: [], failedRequests: [] };
const phase = message => { summary.phases.push({ at: new Date().toISOString(), message }); console.log(`[RNA analysis demo] ${message}`); };
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
        response.writeHead(200, { 'Content-Type': 'text/html' }).end('<!doctype html><title>Outside RNA analysis example</title>');
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
const command = { featurecounts: 'count', stringtie: 'assemble', mosdepth: 'coverage' };
const output = { featurecounts: 'counts.tsv', stringtie: 'transcripts.gtf', mosdepth: 'coverage.mosdepth.summary.txt' };
function checkOutput(tool, bytes) {
  const lines = bytes.toString('utf8').trimEnd().split('\n').filter(line => line && !line.startsWith('#'));
  assert.ok(lines.length > 1, `${tool}: output contains data rows`);
  if (tool === 'featurecounts') {
    assert.match(lines[0], /^Geneid\tChr\tStart\tEnd\tStrand\tLength\t/);
    assert.ok(lines.slice(1).some(line => Number(line.split('\t').at(-1)) > 0), 'Real reads assigned to genes');
  } else if (tool === 'stringtie') {
    assert.ok(lines.some(line => line.split('\t')[2] === 'exon'), 'Real alignments produce exon structures');
    assert.ok(lines.some(line => Number(line.match(/cov "([0-9.eE+-]+)"/)?.[1]) > 0), 'Transcripts have nonzero coverage');
  } else {
    assert.match(lines[0], /^chrom\tlength\tbases\tmean\tmin\tmax$/);
    assert.ok(lines.slice(1).some(line => Number(line.split('\t')[2]) > 0), 'Coverage contains aligned bases');
  }
}
async function analyse(page, tool, dataset, hosting = 'headers') {
  await page.locator('#tool').selectOption(tool);
  await page.locator('#dataset').selectOption(dataset);
  await page.locator('#load-data').click();
  await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Example data loaded.'), null, { timeout: 60000 });
  assert.equal(await page.locator('#files li').count(), 6);
  await page.locator('#command').selectOption(command[tool]);
  await run(page);
  const downloaded = await downloadBytes(page, `Download ${output[tool]}`);
  assert.equal(downloaded.name, output[tool]);
  checkOutput(tool, downloaded.bytes);
  assert.equal(await page.locator('#result-file').inputValue(), `/work/${output[tool]}`);
  summary.cases.push({ id: `${tool}-${dataset}-${hosting}-analyse-download`, status: 'passed', sha256: createHash('sha256').update(downloaded.bytes).digest('hex') });
}
try {
  withHeaders = await serve(true);
  plain = await serve(false);
  browser = await browserType.launch({ headless: true });
  summary.browserVersion = browser.version();
  const context = await browser.newContext({ viewport: { width: 1100, height: 900 } });
  context.setDefaultTimeout(30000);
  page = await context.newPage(); activePage = page;
  observe(page, 'header-hosting');
  await page.goto(`${withHeaders.origin}/examples/rna-analysis/`);
  await ready(page);
  assert.equal(await page.evaluate(() => crossOriginIsolated), true);
  for (const tool of tools) {
    for (const dataset of ['human', 'yeast']) {
      phase(`${tool}: ${dataset} analysis and download`);
      await analyse(page, tool, dataset);
    }
    // Exercise user-file upload with a real alignment, then rerun.
    await page.locator('#upload').setInputFiles({ name: 'input.bam', mimeType: 'application/octet-stream', buffer: await readFile(join(root, 'datasets/rna-analysis/yeast/input.bam')) });
    await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Files loaded.'));
    await run(page);
    checkOutput(tool, (await downloadBytes(page, `Download ${output[tool]}`)).bytes);
    summary.cases.push({ id: `${tool}-upload-rerun`, status: 'passed' });
    phase(`${tool}: cancellation and restart`);
    await page.locator('#command').selectOption('help');
    const held = withHeaders.holdNextRequest(`/dist/${tool}/${tool}.mjs`);
    try {
      await page.locator('#run').click();
      await deadline(held.requested, 30000, 'No module request to cancel');
      await page.locator('#cancel').click();
      await page.waitForFunction(() => document.getElementById('status').textContent === 'Command cancelled.');
    } finally { held.release(); }
    await run(page);
    summary.cases.push({ id: `${tool}-cancel-restart`, status: 'passed' });
  }
  await page.screenshot({ path: join(reportDir, 'example.png'), fullPage: true });
  const staticContext = await browser.newContext();
  staticContext.setDefaultTimeout(30000);
  const staticPage = await staticContext.newPage(); activePage = staticPage;
  observe(staticPage, 'static-hosting');
  await staticPage.goto(`${plain.origin}/examples/rna-analysis/`);
  await ready(staticPage);
  assert.equal(await staticPage.evaluate(() => crossOriginIsolated), false);
  for (const tool of tools.filter(tool => tool !== 'featurecounts')) {
    phase(`${tool}: ordinary static hosting`);
    await analyse(staticPage, tool, 'human', 'static');
    summary.cases.push({ id: `${tool}-static-hosting`, status: 'passed' });
  }
  if (tools.includes('featurecounts')) {
    phase('featureCounts: service-worker static hosting');
    await staticPage.locator('#tool').selectOption('featurecounts');
    await staticPage.locator('#command').selectOption('help');
    await staticPage.locator('#run').click();
    assert.match(await staticPage.locator('#status').innerText(), /^featureCounts cannot start:/);
    await Promise.all([staticPage.waitForEvent('load', { timeout: 45000 }), staticPage.locator('#enable-threads').click()]);
    await ready(staticPage);
    assert.equal(await staticPage.evaluate(() => crossOriginIsolated), true);
    const names = [...Object.keys(summary.builds.featurecounts).map(name => `dist/featurecounts/${name}`), 'runtime/worker.mjs', 'runtime/execute.mjs'];
    const assets = await staticPage.evaluate(async names => Promise.all(names.map(async name => {
      const response = await fetch(new URL(`__assets__/${name}`, location.href));
      const data = await response.arrayBuffer();
      return {name, status: response.status, coep: response.headers.get('Cross-Origin-Embedder-Policy'), sha256: [...new Uint8Array(await crypto.subtle.digest('SHA-256', data))].map(x => x.toString(16).padStart(2,'0')).join('')};
    })), names);
    for (const asset of assets) {
      assert.equal(asset.status, 200); assert.equal(asset.coep, 'require-corp');
      assert.equal(asset.sha256, createHash('sha256').update(await readFile(join(root, asset.name))).digest('hex'));
    }
    summary.staticAssets = assets;
    const rejected = await staticPage.evaluate(async () => Promise.all(['unknown.mjs','runtime/%2e%2e%2f%2e%2e%2fpackage.json'].map(async name => (await fetch(new URL(`__assets__/${name}`, location.href))).status)));
    assert.deepEqual(rejected, [404,404]);
    await analyse(staticPage, 'featurecounts', 'human', 'static');
    await staticPage.goto(`${plain.origin}/outside-example.html`);
    assert.equal(await staticPage.evaluate(() => !!navigator.serviceWorker.controller), false);
    assert.equal(await staticPage.evaluate(() => crossOriginIsolated), false);
    summary.cases.push({ id: 'featurecounts-static-hosting', status: 'passed' });
  }
  assert.deepEqual(summary.browserErrors, []);
  summary.status = 'passed'; phase(`Passed ${summary.cases.length} UI checks`);
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
