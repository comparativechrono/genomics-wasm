// Exercise real browser commands, file transfers and command chaining.
// Native-versus-WebAssembly numerical comparisons are a separate release gate.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { createHash, randomUUID } from 'node:crypto';
import { mkdir, readFile, readdir, stat, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { extname, join, resolve, sep } from 'node:path';
import { chromium, firefox, webkit } from 'playwright';
import { gunzipSync } from 'node:zlib';

const root = fileURLToPath(new URL('../', import.meta.url));
const engine = process.env.BROWSER || 'chromium';
const browserType = { chromium, firefox, webkit }[engine];
if (!browserType) throw new Error('BROWSER must be chromium, firefox or webkit');
const config = JSON.parse(await readFile(join(root, 'examples/plink2/config.json'), 'utf8'));
assert.deepEqual(Object.keys(config.tools), ['plink2']);
assert.ok(config.datasets.plink2);
const reportBase = resolve(process.env.PLINK2_DEMO_REPORT || join(root, 'reports/plink2', engine));
const reportDir = join(reportBase, 'browser-demo');
await mkdir(reportDir, { recursive: true });
const summary = { schema: 1, runId: randomUUID(), engine, tools: ['plink2'], status: 'running', startedAt: new Date().toISOString(), phases: [], cases: [], assets: {}, sources: {}, browserErrors: [], browserConsole: [], requests: [], failedRequests: [], externalRequests: [], workerCalls: [], downloads: [] };
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const phase = message => { summary.phases.push({ at: new Date().toISOString(), message }); console.log(`[plink2 demo] ${message}`); };
const passed = (id, details = {}) => summary.cases.push({ id, status: 'passed', ...details });
const mime = { '.html': 'text/html', '.mjs': 'text/javascript', '.js': 'text/javascript', '.css': 'text/css', '.wasm': 'application/wasm', '.json': 'application/json' };
const commands = ['freq', 'missing', 'hardy', 'make-pgen', 'glm', 'indep-pairwise'];

async function deadline(promise, timeoutMs, message) {
  let timer;
  try { return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(message)), timeoutMs); })]); }
  finally { clearTimeout(timer); }
}

async function serve(isolation = true) {
  const holds = new Map();
  const server = createServer(async (request, response) => {
    if (isolation) {
      response.setHeader('Cross-Origin-Opener-Policy', 'same-origin');
      response.setHeader('Cross-Origin-Embedder-Policy', 'require-corp');
    }
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

function table(bytes) {
  const lines = bytes.toString('utf8').trim().split(/\r?\n/).filter(line => line && !line.startsWith('##'));
  assert.ok(lines.length >= 1);
  const columns = lines[0].replace(/^#/, '').trim().split(/\s+/);
  const rows = lines.slice(1).map(line => line.trim().split(/\s+/));
  assert.ok(rows.every(row => row.length === columns.length), 'Every output row matches its table header');
  return { columns, rows };
}
function vcf(bytes) {
  const parsed = table(bytes);
  assert.deepEqual(parsed.columns.slice(0, 9), ['CHROM', 'POS', 'ID', 'REF', 'ALT', 'QUAL', 'FILTER', 'INFO', 'FORMAT']);
  return parsed;
}
function checkFrequency(bytes, variants, samples) {
  const parsed = table(bytes);
  assert.equal(parsed.rows.length, variants);
  for (const name of ['ID', 'REF', 'ALT', 'ALT_FREQS', 'OBS_CT']) assert.ok(parsed.columns.includes(name), name);
  for (const row of parsed.rows) {
    const count = Number(row[parsed.columns.indexOf('OBS_CT')]);
    assert.ok(Number.isInteger(count) && count >= 0 && count <= samples * 2);
    for (const value of row[parsed.columns.indexOf('ALT_FREQS')].split(',')) {
      assert.ok(value === 'NA' || (Number.isFinite(Number(value)) && Number(value) >= 0 && Number(value) <= 1));
    }
  }
  return parsed;
}
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
  assert.equal(await page.locator('#files li').count(), config.datasets.plink2.files.length);
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
async function workerCalls(page) { return page.evaluate(() => window.__plink2WorkerCalls); }

let browser, page, host, staticHost;
try {
  await recordTree('dist/plink2', summary.assets); await recordTree('tools/plink2', summary.sources);
  await recordTree('datasets/plink2', summary.sources);
  for (const name of ['index', 'execute', 'worker', 'plink2']) summary.sources[`runtime/${name}.mjs`] = hash(await readFile(join(root, `runtime/${name}.mjs`)));
  summary.sources['scripts/test-plink2-demo.mjs'] = hash(await readFile(join(root, 'scripts/test-plink2-demo.mjs')));
  await recordTree('examples/plink2', summary.sources);
  host = await serve(); browser = await browserType.launch({ headless: true }); summary.browserVersion = browser.version();
  const context = await browser.newContext({ viewport: { width: 1100, height: 900 } }); context.setDefaultTimeout(30000);
  context.on('request', request => summary.requests.push({ url: request.url(), type: request.resourceType() }));
  context.on('requestfailed', request => summary.failedRequests.push({ url: request.url(), error: request.failure()?.errorText }));
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (/^https?:$/.test(url.protocol) && url.origin !== host.origin && url.origin !== staticHost?.origin) { summary.externalRequests.push(url.href); await route.abort('blockedbyclient'); }
    else await route.continue();
  });
  // Observe actual workers; never substitute the native program or its output.
  await context.addInitScript(() => {
    window.__plink2WorkerCalls = [];
    const OriginalWorker = window.Worker;
    window.Worker = class extends OriginalWorker {
      constructor(url, options) { super(url, options); this.recordedUrl = String(url); }
      postMessage(message, ...rest) {
        window.__plink2WorkerCalls.push({ workerUrl: this.recordedUrl, moduleUrl: message.moduleUrl, args: [...message.args] });
        return super.postMessage(message, ...rest);
      }
    };
  });
  page = await context.newPage();
  page.on('pageerror', error => summary.browserErrors.push(error.message));
  page.on('console', message => summary.browserConsole.push({ type: message.type(), text: message.text() }));
  await page.goto(`${host.origin}/examples/plink2/`); await ready(page);
  summary.crossOriginIsolated = await page.evaluate(() => crossOriginIsolated);
  assert.equal(summary.crossOriginIsolated, true);
  assert.equal(await page.evaluate(() => typeof SharedArrayBuffer), 'function');
  assert.equal(await page.evaluate(() => !!navigator.serviceWorker?.controller), false);
  passed('threaded-hosting');

  phase('Convert authentic 1000 Genomes genotypes and inspect quality');
  await load(page); await choose(page, 'convert'); await run(page);
  const pgen = await downloadBytes(page, 'cohort.pgen');
  assert.equal(pgen.subarray(0, 2).toString('hex'), '6c1b');
  const pvar = table(await downloadBytes(page, 'cohort.pvar'));
  const psam = table(await downloadBytes(page, 'cohort.psam'));
  assert.equal(pvar.rows.length, 600); assert.equal(psam.rows.length, 2504);
  assert.ok(psam.columns.includes('IID'));
  await page.locator('#result-file').selectOption('/work/cohort.pgen');
  assert.match(await page.locator('#preview').textContent(), /^Compressed or binary file /);
  await choose(page, 'qc'); await run(page);
  const frequencyBytes = await downloadBytes(page, 'qc.afreq');
  const frequencies = checkFrequency(frequencyBytes, 600, 2504);
  assert.equal(table(await downloadBytes(page, 'qc.smiss')).rows.length, 2504);
  assert.equal(table(await downloadBytes(page, 'qc.vmiss')).rows.length, 600);
  assert.equal(table(await downloadBytes(page, 'qc.hardy')).rows.length, 600);
  await page.locator('#result-file').selectOption('/work/qc.afreq');
  assert.equal(await page.locator('#preview').textContent(), frequencyBytes.toString('utf8'));
  passed('real-conversion-qc-preview-download', { samples: 2504, variants: 600, frequencySha256: hash(frequencyBytes) });

  phase('Filter and export generated genotype files');
  await choose(page, 'filter'); await run(page);
  const filteredVariants = table(await downloadBytes(page, 'filtered.pvar')).rows.length;
  const filteredSamples = table(await downloadBytes(page, 'filtered.psam')).rows.length;
  assert.ok(filteredVariants > 0 && filteredVariants <= 600);
  assert.ok(filteredSamples > 0 && filteredSamples <= 2504);
  await choose(page, 'export'); await run(page);
  const exported = vcf(await downloadBytes(page, 'exported.vcf'));
  assert.equal(exported.rows.length, 600); assert.equal(exported.columns.length - 9, 2504);
  assert.deepEqual(exported.columns.slice(9), psam.rows.map(row => row[psam.columns.indexOf('IID')]));
  assert.deepEqual(exported.rows.map(row => row[2]), frequencies.rows.map(row => row[frequencies.columns.indexOf('ID')]));
  passed('filter-and-export', { filteredVariants, filteredSamples });

  phase('Run regression on the separately labelled synthetic control');
  await choose(page, 'linear'); await run(page);
  const regression = table(await downloadBytes(page, 'linear.QUANT.glm.linear'));
  for (const column of ['ID', 'TEST', 'OBS_CT', 'BETA', 'SE', 'P']) assert.ok(regression.columns.includes(column), column);
  const additive = regression.rows.filter(row => row[regression.columns.indexOf('TEST')] === 'ADD');
  assert.ok(additive.length > 0);
  const finite = additive.filter(row => row[regression.columns.indexOf('BETA')] !== 'NA');
  assert.ok(finite.length > 0);
  for (const row of finite) {
    assert.ok(Number.isFinite(Number(row[regression.columns.indexOf('BETA')])));
    assert.ok(Number(row[regression.columns.indexOf('SE')]) > 0);
    const p = Number(row[regression.columns.indexOf('P')]); assert.ok(p >= 0 && p <= 1);
  }
  passed('synthetic-linear-regression', { additiveTests: additive.length, finiteTests: finite.length });

  phase('Upload genuine VCF bytes and regenerate an existing output');
  const fixtureBase = fileURLToPath(new URL(config.datasets.plink2.path, new URL('../examples/plink2/app.mjs', import.meta.url)));
  const sourceVcf = await readFile(join(fixtureBase, 'real/1kg-phase3-chr21.vcf.gz'));
  assert.equal(vcf(gunzipSync(sourceVcf)).rows.length, 600);
  await page.locator('#upload').setInputFiles({ name: 'uploaded.vcf.gz', mimeType: 'application/gzip', buffer: sourceVcf });
  await page.waitForFunction(() => document.getElementById('files').textContent.includes('uploaded.vcf.gz'));
  const uploadedArgs = [...config.tools.plink2.commands.convert.args]; uploadedArgs[uploadedArgs.indexOf('--vcf') + 1] = 'uploaded.vcf.gz';
  await edit(page, uploadedArgs, config.tools.plink2.commands.convert.outputs); await run(page);
  assert.deepEqual(await downloadBytes(page, 'cohort.pgen'), pgen);
  await choose(page, 'qc'); await run(page); assert.deepEqual(await downloadBytes(page, 'qc.afreq'), frequencyBytes);
  passed('upload-and-regenerate-same-path');

  const folder = join(reportDir, 'uploaded-cohort'); await mkdir(folder, { recursive: true });
  await writeFile(join(folder, 'input.vcf.gz'), sourceVcf);
  await page.locator('#upload-folder').setInputFiles(folder);
  await page.waitForFunction(() => document.getElementById('files').textContent.includes('uploaded-cohort/input.vcf.gz'));
  await edit(page, ['--vcf', 'uploaded-cohort/input.vcf.gz', '--freq', '--out', 'uploaded-cohort/frequencies'], ['uploaded-cohort/frequencies.afreq'], ['uploaded-cohort']);
  await run(page);
  assert.deepEqual(await downloadBytes(page, 'uploaded-cohort/input.vcf.gz'), sourceVcf);
  assert.deepEqual(await downloadBytes(page, 'uploaded-cohort/frequencies.afreq'), frequencyBytes);
  passed('directory-collection-retains-source-bytes');

  phase('Reject invalid commands and hidden resource arguments; preserve prior results');
  const retained = await downloadBytes(page, 'qc.afreq');
  await page.locator('#arguments').fill('["--threads", 5]');
  const before = (await workerCalls(page)).length; await page.locator('#run').click();
  assert.match(await page.locator('#status').innerText(), /^Check command: Arguments must be a JSON array of strings/);
  assert.equal((await workerCalls(page)).length, before);
  for (const args of [['--threads', '3'], ['--memory', '4096'], ['--script', 'arguments.txt'], ['-rerun', 'cohort.log']]) {
    await edit(page, args); await page.locator('#run').click();
    assert.match(await finished(page), /^(TypeError|RangeError): /);
    assert.equal((await workerCalls(page)).length, before);
  }
  assert.deepEqual(await downloadBytes(page, 'qc.afreq'), retained);
  passed('invalid-arguments-and-resource-policy');
  await edit(page, ['--version'], ['qc.afreq']); await page.locator('#run').click();
  assert.match(await finished(page), /Missing requested files:/);
  assert.deepEqual(await downloadBytes(page, 'qc.afreq'), retained);
  passed('stale-output-not-reused');
  await edit(page, ['--not-an-option']); await page.locator('#run').click();
  assert.match(await finished(page), /^Exited with status [1-9]\d* /);
  assert.deepEqual(await downloadBytes(page, 'qc.afreq'), retained);
  passed('failed-command-preserves-files');

  phase('Cancel a held WebAssembly request and run the analysis again');
  await choose(page, 'qc');
  const held = host.holdNextRequest('/dist/plink2/plink2.wasm');
  try {
    await page.locator('#run').click(); await deadline(held.requested, 60000, 'The held WebAssembly request did not arrive');
    await page.locator('#cancel').click(); await page.waitForFunction(() => document.getElementById('status').textContent === 'Command cancelled.');
  } finally { held.release(); }
  assert.deepEqual(await downloadBytes(page, 'qc.afreq'), retained);
  await run(page); assert.deepEqual(await downloadBytes(page, 'qc.afreq'), retained);
  passed('cancel-and-rerun');
  await page.locator('#result-file').selectOption('/work/qc.afreq');
  await page.screenshot({ path: join(reportDir, 'plink2.png'), fullPage: true });

  phase('Reload clears files and upstream help remains available');
  summary.workerCalls = await workerCalls(page);
  await page.reload(); await ready(page);
  assert.equal((await page.locator('#files').innerText()).trim(), 'No files loaded.'); assert.equal(await page.locator('#downloads a').count(), 0);
  await edit(page, ['--help', ...commands]); await run(page);
  assert.match((await page.locator('#stdout').innerText()) + (await page.locator('#stderr').innerText()), /--freq/);
  await edit(page, ['--version']); await run(page); assert.match(await page.locator('#stdout').innerText(), /PLINK v2\./);
  summary.workerCalls.push(...await workerCalls(page));
  assert.equal(await page.evaluate(async () => (await navigator.serviceWorker.getRegistrations()).length), 0);
  passed('reload-clear-and-command-help', { commands });

  phase('Explain missing isolation without starting a worker');
  staticHost = await serve(false);
  const staticPage = await context.newPage(); await staticPage.goto(`${staticHost.origin}/examples/plink2/`); await ready(staticPage);
  assert.equal(await staticPage.evaluate(() => crossOriginIsolated), false);
  await staticPage.locator('#run').click();
  assert.match(await staticPage.locator('#status').innerText(), /^PLINK 2 cannot start:/);
  assert.deepEqual(await workerCalls(staticPage), []);
  await staticPage.close();
  passed('nonisolated-hosting-diagnostic');
  const paths = summary.requests.filter(request => /^https?:/.test(request.url)).map(request => new URL(request.url).pathname);
  for (const extension of ['mjs', 'wasm', 'worker.mjs']) assert.ok(paths.includes(`/dist/plink2/plink2.${extension}`), `Real local asset fetched: plink2.${extension}`);
  assert.ok(summary.workerCalls.length >= 14);
  assert.ok(summary.workerCalls.every(call => new URL(call.moduleUrl).pathname === '/dist/plink2/plink2.mjs'));
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
  for (const running of [host, staticHost].filter(Boolean)) {
    running.server.closeAllConnections();
    await deadline(new Promise(resolve => running.server.close(resolve)), 5000, 'Server cleanup exceeded 5 seconds').catch(error => { summary.status = 'failed'; summary.cleanupError = error.message; process.exitCode = 1; });
  }
  await writeFile(join(reportDir, 'summary.json'), `${JSON.stringify(summary, null, 2)}\n`);
  if (summary.cleanupError) process.exit(1);
}
