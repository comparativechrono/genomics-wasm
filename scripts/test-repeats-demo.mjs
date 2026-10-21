#!/usr/bin/env node
// Exercise the packaged repeat-analysis programs through their real browser UI.
// Scientific native/WASM comparisons are a separate complementary release gate.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { createHash, randomUUID } from 'node:crypto';
import { mkdir, readFile, readdir, stat, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { extname, join, resolve, sep } from 'node:path';
import { gunzipSync } from 'node:zlib';
import { REPEAT_VERSIONS } from '../runtime/repeats.mjs';
import { chromium, firefox, webkit } from 'playwright';

const executingRoot = resolve(fileURLToPath(new URL('../', import.meta.url)));
const root = resolve(process.env.REPEATS_DEMO_ROOT || executingRoot);
const engine = process.env.BROWSER || process.env.BROWSER_ENGINE || 'chromium';
const browserType = { chromium, firefox, webkit }[engine];
if (!browserType) throw new Error('BROWSER must be chromium, firefox or webkit');
const reportDir = resolve(process.env.REPEATS_DEMO_REPORT || join(root, 'reports/repeats', engine, 'demo'));
await mkdir(reportDir, { recursive: true });
const summary = { schema: 1, runId: randomUUID(), engine, root, versions: REPEAT_VERSIONS, tools: ['expansionhunter', 'samtools', 'reviewer'], status: 'running', startedAt: new Date().toISOString(), phases: [], cases: [], assets: {}, sources: {}, browserErrors: [], browserConsole: [], requests: [], serverRequests: [], failedRequests: [], externalRequests: [], workerCalls: [], downloads: [] };
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const phase = message => { summary.phases.push({ at: new Date().toISOString(), message }); console.log(`[Repeats demo] ${message}`); };
const passed = (id, details = {}) => summary.cases.push({ id, status: 'passed', ...details });
const mime = { '.html': 'text/html', '.mjs': 'text/javascript', '.js': 'text/javascript', '.css': 'text/css', '.wasm': 'application/wasm', '.json': 'application/json' };
const records = bytes => bytes.toString('utf8').split(/\r?\n/).filter(line => line && !line.startsWith('#'));
const samCore = bytes => bytes.toString('utf8').split(/\r?\n/).filter(line => line && !line.startsWith('@')).map(line => line.split('\t').slice(0, 11).join('\t'));

async function deadline(promise, milliseconds, message) {
  let timer;
  try { return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(message)), milliseconds); })]); }
  finally { clearTimeout(timer); }
}
async function recordTree(directory, target) {
  for (const entry of (await readdir(join(root, directory), { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
    if (entry.name === '__pycache__' || /\.py[co]$/.test(entry.name)) continue;
    const path = `${directory}/${entry.name}`;
    if (entry.isDirectory()) await recordTree(path, target);
    else { assert.ok(entry.isFile(), `Expected regular file: ${path}`); target[path] = hash(await readFile(join(root, path))); }
  }
}
async function serve() {
  const holds = new Map();
  const server = createServer(async (request, response) => {
    // Deliberately no COOP/COEP headers or service worker.
    response.setHeader('Cache-Control', 'no-store');
    try {
      const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
      const record = { pathname, method: request.method, completed: false }; summary.serverRequests.push(record);
      response.once('finish', () => { record.completed = true; record.status = response.statusCode; });
      const held = holds.get(pathname);
      if (held) { holds.delete(pathname); held.arrived(); await held.unblocked; }
      if (response.destroyed) return;
      const path = resolve(root, `.${pathname}`);
      if (!path.startsWith(`${root}${sep}`)) { response.writeHead(403).end(); return; }
      const filename = (await stat(path)).isDirectory() ? join(path, 'index.html') : path;
      const bytes = await readFile(filename); record.bytes = bytes.length; record.sha256 = hash(bytes);
      response.writeHead(200, { 'Content-Type': mime[extname(filename)] || 'application/octet-stream' }).end(bytes);
    } catch { if (!response.destroyed) response.writeHead(404).end('Not found'); }
  });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  return { server, origin: `http://127.0.0.1:${server.address().port}`, holdNextRequest(pathname) {
    assert.equal(holds.has(pathname), false);
    let arrived, unblock;
    const requested = new Promise(resolve => { arrived = resolve; });
    const unblocked = new Promise(resolve => { unblock = resolve; });
    const held = { arrived, unblocked }; holds.set(pathname, held);
    return { requested, release() { if (holds.get(pathname) === held) holds.delete(pathname); unblock(); } };
  } };
}
async function ready(page) { await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Ready.'), null, { timeout: 30000 }); }
async function finished(page) {
  await deadline(page.waitForFunction(() => !document.getElementById('controls').disabled && !document.getElementById('status').textContent.startsWith('Running '), null, { timeout: 300000 }), 305000, 'Command exceeded 305 seconds');
  return page.locator('#status').innerText();
}
async function run(page) {
  await page.locator('#run').click(); const status = await finished(page);
  assert.match(status, /^Exited with status 0 /, `${status}\n${await page.locator('#stderr').innerText()}`);
  assert.doesNotMatch(status, /Missing requested files:/);
}
async function edit(page, args, { outputs = [], stdout = '', stdinMode = 'none', stdinFile = '', stdinText = '', tool = 'samtools' } = {}) {
  await page.locator('#tool').selectOption(tool);
  await page.locator('#arguments').fill(JSON.stringify(args));
  await page.locator('#outputs').fill(outputs.join('\n'));
  await page.locator('#stdout-path').fill(stdout);
  await page.locator('#stdin-mode').selectOption(stdinMode);
  if (stdinMode === 'file') await page.locator('#stdin-file').fill(stdinFile);
  if (stdinMode === 'text') await page.locator('#stdin-text').fill(stdinText);
}
let lastDownloadClick = 0;
async function downloadBytes(page, name, tool = 'samtools') {
  const delay = 250 - (performance.now() - lastDownloadClick);
  if (delay > 0) await new Promise(resolve => setTimeout(resolve, delay));
  lastDownloadClick = performance.now();
  const version = REPEAT_VERSIONS[tool];
  const record = { tool, version, name, startedAt: new Date().toISOString() }; summary.downloads.push(record);
  const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('link', { name: `Download ${name}`, exact: true }).click()]);
  const expectedName = name === 'standard output' ? `${tool}-${version}-stdout` : name === 'standard error' ? `${tool}-${version}-stderr.txt` : name.split('/').at(-1);
  assert.equal(download.suggestedFilename(), expectedName);
  const path = await download.path(); assert.ok(path);
  const bytes = await readFile(path); record.completedAt = new Date().toISOString(); record.bytes = bytes.length; record.sha256 = hash(bytes);
  return bytes;
}

async function workerCalls(page) { return page.evaluate(() => window.__repeatWorkerCalls); }
function table(bytes) { return bytes.toString('utf8').trim().split(/\r?\n/).map(line => line.split('\t')); }
function assertCalls(jsonBytes, vcfBytes) {
  const data = JSON.parse(jsonBytes);
  assert.deepEqual(Object.keys(data.LocusResults).sort(), ['EXPANDED_CAG', 'NORMAL_CAG']);
  const calls = {};
  for (const id of ['NORMAL_CAG', 'EXPANDED_CAG']) {
    const locus = data.LocusResults[id];
    assert.equal(locus.AlleleCount, 2);
    const variants = Object.values(locus.Variants || {});
    assert.equal(variants.length, 1); assert.equal(variants[0].RepeatUnit, 'CAG');
    calls[id] = variants[0];
  }
  assert.equal(calls.NORMAL_CAG.Genotype, '12/18', 'Spanning reads recover the constructed 12/18 alleles');
  const intervals = calls.EXPANDED_CAG.GenotypeConfidenceInterval.split('/').map(interval => interval.split('-').map(Number));
  assert.equal(intervals.length, 2);
  for (const [i, truth] of [12, 75].entries()) assert.ok(intervals[i].length === 2 && intervals[i][0] <= truth && intervals[i][1] >= truth, 'Expanded allele confidence intervals contain the constructed truth');
  const rows = records(vcfBytes).map(line => line.split('\t'));
  assert.equal(rows.length, 2);
  for (const [chrom, id] of [['chr1', 'NORMAL_CAG'], ['chr2', 'EXPANDED_CAG']]) {
    const row = rows.find(row => row[0] === chrom); assert.ok(row);
    const sample = Object.fromEntries(row[8].split(':').map((name, i) => [name, row[9].split(':')[i]]));
    assert.equal(sample.REPCN, calls[id].Genotype, 'VCF repeat alleles agree with JSON calls');
  }
  return calls;
}
function assertMetrics(metricsBytes, phasingBytes, calls) {
  const metrics = table(metricsBytes), phasing = table(phasingBytes);
  assert.deepEqual(metrics.shift(), ['VariantId', 'Genotype', 'AlleleDepth']);
  assert.equal(metrics.length, 2);
  assert.deepEqual(metrics.map(row => row[0]).sort(), ['EXPANDED_CAG', 'NORMAL_CAG']);
  for (const row of metrics) {
    assert.equal(row.length, 3); assert.equal(row[1], calls[row[0]].Genotype);
    const depths = row[2].split('/').map(Number);
    assert.equal(depths.length, 2); assert.ok(depths.every(value => Number.isFinite(value) && value > 0));
  }
  assert.deepEqual(phasing.shift(), ['LocusId', 'Diplotype', 'Score']);
  assert.ok(phasing.length >= 2);
  assert.deepEqual([...new Set(phasing.map(row => row[0]))].sort(), ['EXPANDED_CAG', 'NORMAL_CAG']);
  for (const row of phasing) assert.ok(row.length === 3 && Object.hasOwn(calls, row[0]) && row[1].length && Number.isFinite(Number(row[2])));
}

let browser, page, host;
try {
  const config = JSON.parse(await readFile(join(root, 'examples/repeats/config.json'), 'utf8'));
  for (const directory of ['dist/expansionhunter', 'dist/reviewer', 'dist/hts-1.21']) await recordTree(directory, summary.assets);
  await recordTree('examples/repeats', summary.sources); await recordTree('datasets/repeats', summary.sources);
  for (const path of ['runtime/index.mjs', 'runtime/repeats.mjs', 'runtime/worker.mjs', 'runtime/execute.mjs', 'scripts/test-repeats-demo.mjs']) summary.sources[path] = hash(await readFile(join(root, path)));
  for (const path of ['scripts/test-repeats-demo.mjs', 'runtime/repeats.mjs', 'runtime/index.mjs']) assert.equal(hash(await readFile(join(executingRoot, path))), summary.sources[path], `The executing harness and imports match the tested package: ${path}`);
  const fixtureDirectory = 'datasets/repeats/common';
  host = await serve(); browser = await browserType.launch({ headless: true }); summary.browserVersion = browser.version();
  const context = await browser.newContext({ viewport: { width: 1100, height: 950 } }); context.setDefaultTimeout(30000);
  context.on('request', request => { summary.requests.push({ url: request.url(), type: request.resourceType() }); if (/^https?:/.test(request.url()) && new URL(request.url()).origin !== host.origin) summary.externalRequests.push(request.url()); });
  context.on('requestfailed', request => summary.failedRequests.push({ url: request.url(), error: request.failure()?.errorText }));
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (/^https?:$/.test(url.protocol) && url.origin !== host.origin) { summary.externalRequests.push(url.href); await route.abort('blockedbyclient'); }
    else await route.continue();
  });
  await context.addInitScript(() => {
    window.__repeatWorkerCalls = [];
    const OriginalWorker = window.Worker;
    window.Worker = class extends OriginalWorker {
      constructor(url, options) { super(url, options); this.recordedUrl = String(url); }
      postMessage(message, ...rest) {
        if (Array.isArray(message.args)) window.__repeatWorkerCalls.push({ workerUrl: this.recordedUrl, moduleUrl: message.moduleUrl, args: [...message.args], inputs: message.files.map(file => file.path), outputs: message.outputs });
        return super.postMessage(message, ...rest);
      }
    };
  });
  page = await context.newPage();
  page.on('pageerror', error => summary.browserErrors.push(error.message));
  page.on('console', message => summary.browserConsole.push({ type: message.type(), text: message.text() }));
  await page.goto(`${host.origin}/examples/repeats/`); await ready(page); await page.locator('details summary').click();
  assert.deepEqual(await page.locator('#tool option').evaluateAll(options => options.map(option => option.value)), summary.tools);
  assert.equal(await page.evaluate(() => crossOriginIsolated), false);
  assert.equal(await page.evaluate(() => typeof SharedArrayBuffer), 'undefined');
  passed('ordinary-static-http-and-three-programs');

  phase('Run ExpansionHunter through BAMlet preparation to REViewer');
  await page.locator('#load-data').click(); await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Example data loaded.'));
  const outputs = new Map(); let genotypeCalls;
  for (const [id, spec] of Object.entries(config.commands)) {
    await page.locator('#command').selectOption(id);
    assert.deepEqual(JSON.parse(await page.locator('#arguments').inputValue()), spec.args);
    assert.equal(await page.locator('#tool').inputValue(), spec.tool);
    await run(page);
    for (const name of [...(spec.outputs || []), ...(spec.stdoutPath ? [spec.stdoutPath] : [])]) {
      const bytes = await downloadBytes(page, name, spec.tool); assert.ok(bytes.length, `${id}: ${name} is nonempty`); outputs.set(name, bytes);
    }
    if (id === 'call') {
      genotypeCalls = assertCalls(outputs.get('eh.json'), outputs.get('eh.vcf'));
      assert.equal(gunzipSync(outputs.get('eh_realigned.bam')).subarray(0, 4).toString('binary'), 'BAM\x01');
    }
    if (id === 'sort') assert.equal(gunzipSync(outputs.get('eh.sorted.bam')).subarray(0, 4).toString('binary'), 'BAM\x01');
    if (id === 'index') assert.equal(outputs.get('eh.sorted.bam.bai').subarray(0, 4).toString('binary'), 'BAI\x01');
    if (id === 'review') {
      assertMetrics(outputs.get('review.metrics.tsv'), outputs.get('review.phasing.tsv'), genotypeCalls);
      for (const locus of ['NORMAL_CAG', 'EXPANDED_CAG']) {
        const name = `review.${locus}.svg`;
        const svg = outputs.get(name).toString();
        const parsed = await page.evaluate(text => {
          const xml = new DOMParser().parseFromString(text, 'image/svg+xml');
          return { root: xml.documentElement.localName, errors: xml.querySelectorAll('parsererror').length, marks: xml.querySelectorAll('rect,path,line,polyline,polygon,text').length };
        }, svg);
        assert.equal(parsed.root, 'svg'); assert.equal(parsed.errors, 0); assert.ok(parsed.marks > 10, 'SVG contains actual read alignment geometry');
        await page.locator('#result-file').selectOption(`/work/${name}`);
        await page.waitForFunction(() => { const image = document.getElementById('plot'); return image.complete && image.naturalWidth > 0 && image.naturalHeight > 0; });
        assert.match(await page.locator('#plot').getAttribute('src'), /^blob:/);
        assert.equal(await page.locator('#plot-panel svg, #plot-panel iframe, #plot-panel object').count(), 0, 'SVG is an image, not active page markup');
        await page.locator('#plot-scale').selectOption('fit');
        assert.ok(await page.locator('#plot').evaluate(image => image.clientWidth <= image.parentElement.clientWidth));
        await page.locator('#plot-panel').screenshot({ path: join(reportDir, `reviewer-${locus}.png`) });
        await page.locator('.plot-scroll').evaluate(element => { element.scrollTop = element.scrollHeight; });
        await page.locator('#plot-panel').screenshot({ path: join(reportDir, `reviewer-${locus}-bottom.png`) });
        await page.locator('.plot-scroll').evaluate(element => { element.scrollTop = 0; });
        await writeFile(join(reportDir, `reviewer-${locus}.svg`), svg);
        await page.locator('#plot-scale').selectOption('100');
        assert.equal(await page.locator('#plot').evaluate(image => image.clientWidth), await page.locator('#plot').evaluate(image => image.naturalWidth));
      }
      await page.screenshot({ path: join(reportDir, 'repeats-workspace.png'), fullPage: true });
    }
    passed(`preset-${id}`, { tool: spec.tool });
  }
  const calls = await workerCalls(page);
  const review = calls.find(call => call.args.includes('--locus'));
  for (const name of ['eh.sorted.bam', 'eh.sorted.bam.bai', 'eh.vcf']) assert.ok(review.inputs.includes(`/work/${name}`), `REViewer consumes generated ${name}`);
  passed('generated-evidence-chains-across-programs');

  phase('Check binary streams, real file uploads and stdin');
  await edit(page, ['view', '-h', 'eh.sorted.bam'], { stdout: 'decoded.sam' }); await run(page);
  const sam = await downloadBytes(page, 'decoded.sam');
  assert.equal(Number(outputs.get('read-count.txt').toString()), samCore(sam).length);
  assert.ok(samCore(sam).length > 0);
  await page.locator('#upload').setInputFiles({ name: 'uploaded.sam', mimeType: 'text/plain', buffer: sam });
  await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Files loaded.'));
  for (const mode of ['file', 'text']) {
    await edit(page, ['view', '-c', '-'], { stdinMode: mode, stdinFile: 'uploaded.sam', stdinText: sam.toString(), stdout: `stdin-${mode}.txt` });
    await run(page); assert.equal(Number((await downloadBytes(page, `stdin-${mode}.txt`)).toString()), samCore(sam).length);
  }
  await edit(page, ['view', '-b', 'uploaded.sam'], { stdout: 'stdout.bam' }); await run(page);
  const binary = await downloadBytes(page, 'stdout.bam');
  assert.equal(gunzipSync(binary).subarray(0, 4).toString('binary'), 'BAM\x01');
  assert.deepEqual(await downloadBytes(page, 'standard output'), binary);
  await edit(page, ['view', 'stdout.bam'], { stdout: 'stdout-roundtrip.sam' }); await run(page);
  assert.deepEqual(samCore(await downloadBytes(page, 'stdout-roundtrip.sam')), samCore(sam));
  assert.deepEqual(await downloadBytes(page, 'eh.json', 'expansionhunter'), outputs.get('eh.json'), 'Uploads preserve prior calls');
  passed('binary-stdout-and-sam-roundtrip'); passed('file-upload-and-file-text-stdin');

  phase('Run a complete caller with a real folder upload');
  const folder = join(reportDir, 'uploaded-control'); await mkdir(folder, { recursive: true });
  for (const entry of config.datasets['repeat-controls'].files) {
    const source = typeof entry === 'string' ? entry : entry.source;
    const name = typeof entry === 'string' ? entry : entry.name;
    await writeFile(join(folder, name), await readFile(join(root, fixtureDirectory, source)));
  }
  await page.locator('#upload-folder').setInputFiles(folder);
  await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Files loaded.'));
  assert.match(await page.locator('#files').innerText(), /uploaded-control\/reads\.bam/);
  const folderArgs = config.commands.call.args.map(arg => ['reads.bam', 'reference.fa', 'catalog.json'].includes(arg) ? `uploaded-control/${arg}` : arg === 'eh' ? 'folder-call' : arg);
  await edit(page, folderArgs, { tool: 'expansionhunter', outputs: ['folder-call.json', 'folder-call.vcf', 'folder-call_realigned.bam'] }); await run(page);
  assertCalls(await downloadBytes(page, 'folder-call.json', 'expansionhunter'), await downloadBytes(page, 'folder-call.vcf', 'expansionhunter'));
  passed('folder-upload-preserves-paths-and-caller-truth');

  phase('Check failure handling, stale results, serial enforcement and worker cancellation');
  await edit(page, ['--version']); const before = (await workerCalls(page)).length;
  await page.locator('#arguments').fill('[5]'); await page.locator('#run').click();
  assert.match(await page.locator('#status').innerText(), /^Check command:/); assert.equal((await workerCalls(page)).length, before);
  await edit(page, ['--version'], { outputs: ['same.txt'], stdout: 'same.txt' }); await page.locator('#run').click();
  assert.match(await page.locator('#status').innerText(), /^Check command:/); assert.equal((await workerCalls(page)).length, before);
  await edit(page, ['view', 'missing.bam']); await page.locator('#run').click(); assert.match(await finished(page), /^Exited with status -?[1-9]\d* /);
  assert.match((await downloadBytes(page, 'standard error')).toString(), /missing\.bam/);
  await edit(page, ['--version'], { outputs: ['eh.json'] }); await page.locator('#run').click(); assert.match(await finished(page), /Missing requested files:/);
  assert.deepEqual(await downloadBytes(page, 'eh.json', 'expansionhunter'), outputs.get('eh.json'));
  const badThreads = [...config.commands.call.args]; badThreads[badThreads.indexOf('--threads') + 1] = '2';
  await edit(page, badThreads, { tool: 'expansionhunter' }); await page.locator('#run').click();
  assert.match(await finished(page), /^Exited with status -?[1-9]\d* /);
  // ExpansionHunter's upstream logger writes this diagnostic to standard output.
  assert.match((await downloadBytes(page, 'standard output', 'expansionhunter')).toString(), /exactly one thread/);
  passed('invalid-arguments-failure-diagnostics-and-fresh-output-requirement'); passed('parallel-thread-request-is-rejected');
  await edit(page, ['--version']);
  for (const mode of ['cancel', 'timeout']) {
    const held = host.holdNextRequest('/dist/hts-1.21/samtools.mjs');
    try {
      await page.locator('#timeout').fill(mode === 'timeout' ? '1' : '300');
      await page.locator('#run').click(); await deadline(held.requested, 30000, 'Held program request did not arrive');
      if (mode === 'cancel') await page.locator('#cancel').click();
      assert.match(await finished(page), mode === 'cancel' ? /^Command cancelled\.$/ : /^TimeoutError:/);
    } finally { held.release(); }
    assert.deepEqual(await downloadBytes(page, 'review.EXPANDED_CAG.svg', 'reviewer'), outputs.get('review.EXPANDED_CAG.svg'));
  }
  await page.locator('#timeout').fill('300'); await run(page);
  assert.match((await downloadBytes(page, 'standard output')).toString(), /^samtools 1\.21\b/m);
  passed('cancel-timeout-preserve-results-and-recover');
  summary.workerCalls = await workerCalls(page);
  const locations = { expansionhunter: 'expansionhunter/expansionhunter', samtools: 'hts-1.21/samtools', reviewer: 'reviewer/reviewer' };
  for (const [tool, location] of Object.entries(locations)) {
    assert.ok(summary.workerCalls.some(call => new URL(call.moduleUrl).pathname === `/dist/${location}.mjs`), `Dispatched ${tool}`);
    for (const extension of ['mjs', 'wasm']) {
      const path = `dist/${location}.${extension}`;
      assert.ok(summary.serverRequests.some(request => request.pathname === `/${path}` && request.completed && request.status === 200 && request.bytes > 0 && request.sha256 === summary.assets[path]), `Actual local asset served with matching bytes: ${path}`);
    }
  }
  assert.ok(summary.workerCalls.every(call => Object.values(locations).includes(new URL(call.moduleUrl).pathname.replace(/^\/dist\//, '').replace(/\.mjs$/, ''))));
  await page.locator('#clear').click(); assert.equal((await page.locator('#files').innerText()).trim(), 'No files loaded.');
  assert.equal(await page.locator('#downloads a').count(), 0); assert.equal(await page.locator('#plot').getAttribute('src'), null);
  await page.reload(); await ready(page);
  assert.equal((await page.locator('#files').innerText()).trim(), 'No files loaded.');
  assert.equal(await page.evaluate(async () => (await navigator.serviceWorker.getRegistrations()).length), 0);
  passed('clear-and-reload-discard-workspace-and-preview');
  assert.deepEqual(summary.externalRequests, []); assert.deepEqual(summary.browserErrors, []);
  passed('all-three-runtime-assets-local');
  for (const [path, digest] of Object.entries({ ...summary.assets, ...summary.sources })) assert.equal(hash(await readFile(join(root, path))), digest, `Inputs unchanged: ${path}`);
  summary.status = 'passed'; phase(`Passed ${summary.cases.length} UI checks`);
} catch (error) {
  summary.status = 'failed'; summary.error = error.stack; process.exitCode = 1;
  if (page) {
    summary.workerCalls = await deadline(workerCalls(page), 3000, 'Worker diagnostics timed out').catch(() => summary.workerCalls);
    await deadline(page.screenshot({ path: join(reportDir, 'failure.png'), fullPage: true }), 5000, 'Failure screenshot timed out').catch(() => {});
    for (const id of ['status', 'tool', 'arguments', 'outputs', 'stdout', 'stderr']) {
      const content = await deadline(page.locator(`#${id}`).evaluate(element => element.value ?? element.textContent, undefined, { timeout: 2000 }), 3000, 'Failure diagnostics timed out').catch(() => '(unavailable)');
      await writeFile(join(reportDir, `${id}.txt`), content);
    }
    console.error(JSON.stringify({ console: summary.browserConsole.slice(-15), failedRequests: summary.failedRequests.slice(-15) }, null, 2));
  }
  console.error(error);
} finally {
  summary.finishedAt = new Date().toISOString(); await writeFile(join(reportDir, 'summary.json'), JSON.stringify(summary, null, 2) + '\n');
  try { if (browser) await deadline(browser.close(), 30000, 'Browser cleanup exceeded 30 seconds'); }
  catch (error) { summary.status = 'failed'; summary.cleanupError = error.message; process.exitCode = 1; }
  if (host) { host.server.closeAllConnections(); await deadline(new Promise(resolve => host.server.close(resolve)), 5000, 'Server cleanup exceeded 5 seconds').catch(error => { summary.status = 'failed'; summary.cleanupError = error.message; process.exitCode = 1; }); }
  await writeFile(join(reportDir, 'summary.json'), JSON.stringify(summary, null, 2) + '\n');
  if (summary.cleanupError) process.exit(1);
}
