// Exercise the actual locally bundled MACS3 through its browser interface.
// Scientific native/WASM equivalence is a separate, complementary release gate.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { createHash, randomUUID } from 'node:crypto';
import { mkdir, readFile, readdir, stat, writeFile } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { extname, join, resolve, sep } from 'node:path';
import { chromium, firefox, webkit } from 'playwright';

// An extracted release can be served and tested without copying its files into
// the checkout or inadvertently using the checkout's runtime or fixtures.
const root = resolve(process.env.MACS3_DEMO_ROOT || fileURLToPath(new URL('../', import.meta.url)));
const engine = process.env.BROWSER || process.env.BROWSER_ENGINE || 'chromium';
const browserType = { chromium, firefox, webkit }[engine];
if (!browserType) throw new Error('BROWSER must be chromium, firefox or webkit');
const reportDir = resolve(process.env.MACS3_DEMO_REPORT || join(root, 'reports/macs3', engine, 'demo'));
await mkdir(reportDir, { recursive: true });
const summary = { schema: 1, runId: randomUUID(), engine, tools: ['macs3'], root, status: 'running', startedAt: new Date().toISOString(), phases: [], cases: [], assets: {}, sources: {}, browserErrors: [], browserConsole: [], requests: [], serverRequests: [], failedRequests: [], externalRequests: [], workerCalls: [], downloads: [] };
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const phase = message => { summary.phases.push({ at: new Date().toISOString(), message }); console.log(`[MACS3 demo] ${message}`); };
const passed = (id, details = {}) => summary.cases.push({ id, status: 'passed', ...details });
const mime = { '.html': 'text/html', '.mjs': 'text/javascript', '.js': 'text/javascript', '.css': 'text/css', '.wasm': 'application/wasm', '.json': 'application/json' };

async function deadline(promise, timeoutMs, message) {
  let timer;
  try { return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(message)), timeoutMs); })]); }
  finally { clearTimeout(timer); }
}

async function serve() {
  const holds = new Map();
  const server = createServer(async (request, response) => {
    // Intentionally no COOP/COEP headers: ordinary static hosting must work.
    response.setHeader('Cache-Control', 'no-store');
    try {
      const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
      const served = { pathname, method: request.method, completed: false }; summary.serverRequests.push(served);
      response.once('finish', () => { served.completed = true; served.status = response.statusCode; });
      const held = holds.get(pathname);
      if (held) { holds.delete(pathname); held.arrived(); await held.unblocked; }
      if (response.destroyed) return;
      const path = resolve(root, `.${pathname}`);
      if (!path.startsWith(`${root}${sep}`)) { response.writeHead(403).end(); return; }
      const filename = (await stat(path)).isDirectory() ? join(path, 'index.html') : path;
      const bytes = await readFile(filename); served.bytes = bytes.length; served.sha256 = hash(bytes);
      response.writeHead(200, { 'Content-Type': mime[extname(filename)] || 'application/octet-stream' }).end(bytes);
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

function servedAssetRequests(path) {
  return summary.serverRequests.filter(request => request.pathname === `/${path}` && request.method === 'GET' && request.completed && request.status === 200 && request.bytes > 0 && request.sha256 === summary.assets[path]);
}

function intervals(bytes, columns, label) {
  assert.ok(bytes.length > 0, `${label} is nonempty`);
  const rows = bytes.toString('utf8').split(/\r?\n/).filter(line => line.trim() && !/^(?:#|track\b|browser\b)/.test(line)).map(line => line.split(/\s+/));
  assert.ok(rows.length > 0, `${label} contains genomic records`);
  for (const row of rows) {
    if (columns === null) assert.ok(row.length >= 3, `${label} has at least three columns`);
    else assert.equal(row.length, columns, `${label} column count`);
    assert.match(row[0], /^\S+$/, `${label} chromosome`);
    assert.ok(Number.isSafeInteger(Number(row[1])) && Number(row[1]) >= 0, `${label} start coordinate`);
    assert.ok(Number.isSafeInteger(Number(row[2])) && Number(row[2]) > Number(row[1]), `${label} end coordinate`);
  }
  return rows;
}

function peaks(bytes, broad = false) {
  const rows = intervals(bytes, broad ? 9 : 10, broad ? 'broadPeak' : 'narrowPeak');
  for (const row of rows) {
    assert.ok(Number.isFinite(Number(row[4])) && Number(row[4]) >= 0, 'Peak score is finite and nonnegative');
    assert.equal(row[5], '.', 'Peak calls are unstranded');
    for (const index of [6, 7]) assert.ok(Number.isFinite(Number(row[index])) && Number(row[index]) >= 0, 'Signal and p-value score are finite and nonnegative');
    assert.ok(Number.isFinite(Number(row[8])) && (Number(row[8]) >= 0 || Number(row[8]) === -1), 'q-value score is nonnegative or the upstream unavailable sentinel');
    if (!broad) assert.ok(Number.isInteger(Number(row[9])) && Number(row[9]) >= 0 && Number(row[9]) < Number(row[2]) - Number(row[1]), 'Summit lies within its peak');
  }
  return rows;
}

function bedgraph(bytes) {
  const rows = intervals(bytes, 4, 'bedGraph');
  for (const row of rows) assert.ok(Number.isFinite(Number(row[3])), 'BedGraph signal is finite');
  const ends = new Map();
  for (const row of rows) {
    assert.ok(Number(row[1]) >= (ends.get(row[0]) ?? 0), 'BedGraph intervals are sorted and nonoverlapping per chromosome');
    ends.set(row[0], Number(row[2]));
  }
  return rows;
}
function bedgraphValues(bytes) {
  return bedgraph(bytes).map(([chromosome, start, end, score]) => [chromosome, Number(start), Number(end), Number(score)]);
}
function hmmModel(bytes) {
  const model = JSON.parse(bytes.toString('utf8'));
  assert.ok(['gaussian', 'poisson'].includes(model.hmm_type));
  assert.equal(model.startprob.length, 3); assert.equal(model.transmat.length, 3);
  for (const distribution of [model.startprob, ...model.transmat]) {
    assert.equal(distribution.length, 3);
    assert.ok(distribution.every(value => Number.isFinite(value) && value >= 0 && value <= 1));
    assert.ok(Math.abs(distribution.reduce((sum, value) => sum + value, 0) - 1) < 1e-6, 'HMM state probabilities sum to one');
  }
  assert.deepEqual([model.i_open_region, model.i_background_region, model.i_nucleosomal_region].sort(), [0, 1, 2]);
  assert.ok(Number.isSafeInteger(model.hmm_binsize) && model.hmm_binsize > 0);
  for (const name of model.hmm_type === 'gaussian' ? ['means', 'covars'] : ['lambdas']) {
    assert.equal(model[name].length, 3);
    assert.ok(model[name].flat(Infinity).every(Number.isFinite), `HMM ${name} are finite`);
  }
  return model;
}

async function ready(page) {
  await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Ready.'), null, { timeout: 30000 });
}
async function finished(page) {
  await deadline(page.waitForFunction(() => !document.getElementById('controls').disabled && !document.getElementById('status').textContent.startsWith('Running '), null, { timeout: 600000 }), 605000, 'Command exceeded 605 seconds');
  return page.locator('#status').innerText();
}
async function run(page) {
  await page.locator('#run').click(); const status = await finished(page);
  assert.match(status, /^Exited with status 0 /, status); assert.doesNotMatch(status, /Missing requested files:/);
}
async function choose(page, command) { await page.locator('#command').selectOption(command); }
async function edit(page, args, outputs = [], directories = []) {
  await page.locator('#arguments').fill(JSON.stringify(args));
  await page.locator('#outputs').fill(outputs.join('\n'));
  await page.locator('#output-directories').fill(directories.join('\n'));
}
let lastDownloadClick = 0;
async function downloadBytes(page, name) {
  // Pace genuine clicks to avoid the browser's automatic download limiter.
  const delay = 250 - (performance.now() - lastDownloadClick);
  if (delay > 0) await new Promise(resolve => setTimeout(resolve, delay));
  lastDownloadClick = performance.now();
  const record = { name, startedAt: new Date().toISOString() }; summary.downloads.push(record);
  const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('link', { name: `Download ${name}`, exact: true }).click()]);
  assert.equal(download.suggestedFilename(), name === 'standard output' ? 'macs3-stdout.txt' : name.split('/').at(-1));
  const path = await download.path(); assert.ok(path);
  const bytes = await readFile(path); record.completedAt = new Date().toISOString(); record.bytes = bytes.length; record.sha256 = hash(bytes);
  return bytes;
}
async function workerCalls(page) { return page.evaluate(() => window.__macs3WorkerCalls); }

let browser, page, host;
try {
  await recordTree('dist/macs3', summary.assets); await recordTree('tools/macs3', summary.sources);
  await recordTree('datasets/macs3', summary.sources); await recordTree('examples/macs3', summary.sources);
  for (const name of ['index', 'macs3', 'macs3-worker', 'macs3-execute']) summary.sources[`runtime/${name}.mjs`] = hash(await readFile(join(root, `runtime/${name}.mjs`)));
  summary.sources['scripts/test-macs3-demo.mjs'] = hash(await readFile(join(root, 'scripts/test-macs3-demo.mjs')));
  const config = JSON.parse(await readFile(join(root, 'examples/macs3/config.json'), 'utf8'));
  host = await serve(); browser = await browserType.launch({ headless: true }); summary.browserVersion = browser.version();
  const context = await browser.newContext({ viewport: { width: 1100, height: 900 } }); context.setDefaultTimeout(30000);
  context.on('request', request => {
    summary.requests.push({ url: request.url(), type: request.resourceType() });
    if (/^https?:/.test(request.url()) && new URL(request.url()).origin !== host.origin) summary.externalRequests.push(request.url());
  });
  context.on('requestfailed', request => summary.failedRequests.push({ url: request.url(), error: request.failure()?.errorText }));
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (/^https?:$/.test(url.protocol) && url.origin !== host.origin) { summary.externalRequests.push(url.href); await route.abort('blockedbyclient'); }
    else await route.continue();
  });
  // Only observe worker requests. No native program or fabricated output is injected.
  await context.addInitScript(() => {
    window.__macs3WorkerCalls = [];
    const OriginalWorker = window.Worker;
    window.Worker = class extends OriginalWorker {
      constructor(url, options) { super(url, options); this.recordedUrl = String(url); }
      postMessage(message, ...rest) {
        if (Array.isArray(message.args)) window.__macs3WorkerCalls.push({ workerUrl: this.recordedUrl, moduleUrl: message.moduleUrl, args: [...message.args] });
        return super.postMessage(message, ...rest);
      }
    };
  });
  page = await context.newPage();
  page.on('pageerror', error => summary.browserErrors.push(error.message));
  page.on('console', message => summary.browserConsole.push({ type: message.type(), text: message.text() }));
  await page.goto(`${host.origin}/examples/macs3/`); await ready(page);
  summary.crossOriginIsolated = await page.evaluate(() => crossOriginIsolated);
  assert.equal(summary.crossOriginIsolated, false);
  assert.equal(await page.evaluate(() => typeof SharedArrayBuffer), 'undefined');
  assert.equal(await page.evaluate(() => !!navigator.serviceWorker?.controller), false);
  passed('ordinary-static-hosting');

  const datasetId = Object.keys(config.datasets)[0];
  const dataset = config.datasets[datasetId];
  const fixtureBase = fileURLToPath(new URL(dataset.path, pathToFileURL(join(root, 'examples/macs3/app.mjs'))));
  assert.ok(dataset.files.includes('chip.bed.gz') && dataset.files.includes('signal.bdg'));
  phase('Load the authentic ChIP/ATAC inputs and labeled synthetic utility fixtures');
  await page.locator('#dataset').selectOption(datasetId); await page.locator('#load-data').click();
  await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Example data loaded.'), null, { timeout: 60000 });
  assert.equal(await page.locator('#files li').count(), dataset.files.length);
  const requiredCommands = ['callpeak', 'bdgpeakcall', 'bdgbroadcall', 'bdgcmp', 'bdgopt', 'bdgdiff', 'cmbreps', 'filterdup', 'predictd', 'pileup', 'randsample', 'refinepeak', 'callvar', 'hmmratac'];
  const presets = Object.entries(config.commands).filter(([, command]) => !command.args[0].startsWith('-'));
  for (const command of requiredCommands) assert.ok(presets.some(([, spec]) => spec.args[0] === command), `The demo exposes ${command}`);
  const results = new Map();
  for (const [id, spec] of presets) {
    phase(`Run ${id}, inspect scientific output, preview and download`);
    await choose(page, id); await run(page);
    const outputPaths = new Set(spec.outputs);
    for (const directory of spec.outputDirectories) {
      const options = await page.locator('#result-file option').evaluateAll(elements => elements.map(element => element.value.slice(6)));
      const collected = options.filter(path => path.startsWith(`${directory}/`));
      assert.ok(collected.length > 0, `Output directory ${directory} is collected`);
      for (const path of collected) outputPaths.add(path);
    }
    const artifacts = {}, observed = new Map();
    for (const name of outputPaths) {
      const bytes = await downloadBytes(page, name); observed.set(name, bytes);
      artifacts[name] = { bytes: bytes.length, sha256: hash(bytes) };
      assert.ok(bytes.length > 0, `${id} produces a nonempty ${name}`);
      if (/\.(?:bdg|bedgraph)$/i.test(name)) artifacts[name].records = bedgraph(bytes).length;
      else if (spec.args[0] === 'callpeak' && /\.(?:narrowPeak|broadPeak)$/.test(name)) {
        const rows = peaks(bytes, name.endsWith('.broadPeak'));
        assert.ok(rows.every(row => row[0] === 'chr22'), 'Authentic human CTCF peak calls remain on chr22');
        artifacts[name].records = rows.length;
      } else if (/\.(?:narrowPeak|broadPeak|gappedPeak|bed|bed12)$/.test(name)) {
        const columns = name.endsWith('.narrowPeak') ? 10 : name.endsWith('.broadPeak') ? 9 : null;
        artifacts[name].records = intervals(bytes, columns, name).length;
      } else if (name.endsWith('.vcf')) {
        const lines = bytes.toString('utf8').split(/\r?\n/);
        assert.ok(lines.some(line => /^##fileformat=VCFv4\./.test(line)));
        assert.ok(lines.some(line => /^#CHROM\tPOS\t/.test(line)));
        const records = lines.filter(line => line && !line.startsWith('#')).map(line => line.split('\t'));
        assert.ok(records.length > 0, 'Callvar emits variant records');
        for (const row of records) {
          assert.ok(row.length >= 10 && Number.isSafeInteger(Number(row[1])) && Number(row[1]) > 0);
          assert.match(row[3], /^[ACGTN]+$/i); assert.match(row[4], /^[ACGTN]+(?:,[ACGTN]+)*$/i);
          assert.ok(Number.isFinite(Number(row[5]))); assert.match(row[8], /(?:^|:)GT(?::|$)/);
        }
        assert.ok(records.some(row => row[0] === 'chr22' && row[1] === '17255752' && row[3] === 'A' && row[4] === 'G'), 'The authentic BAM fixture yields its known A-to-G variant');
        artifacts[name].records = records.length;
      } else if (spec.args[0] === 'hmmratac' && name.endsWith('_model.json')) artifacts[name].hmmType = hmmModel(bytes).hmm_type;
      else if (name.endsWith('.json')) JSON.parse(bytes.toString('utf8'));
      await page.locator('#result-file').selectOption(`/work/${name}`);
      const preview = await page.locator('#preview').textContent();
      if ((bytes[0] === 0x1f && bytes[1] === 0x8b) || bytes.subarray(0, 8192).includes(0)) assert.match(preview, /^Compressed or binary file /);
      else {
        const expected = bytes.subarray(0, 100000).toString('utf8');
        assert.ok(preview.startsWith(expected), `${name} preview matches the exact downloaded bytes`);
      }
    }
    if (spec.args[0] === 'predictd') assert.match(await page.locator('#stderr').innerText(), /(?:predicted fragment length|Average insertion length of all pairs) is \d+ bps/);
    if (id === 'bdgopt') {
      const before = bedgraph(await readFile(join(fixtureBase, 'signal.bdg')));
      const after = bedgraph(observed.get(spec.outputs[0]));
      assert.deepEqual(after.map(row => row.slice(0, 3)), before.map(row => row.slice(0, 3)));
      assert.deepEqual(after.map(row => Number(row[3])), before.map(row => 2 * Number(row[3])));
    }
    if (id === 'bdgpeakcall') {
      const called = intervals(observed.get(spec.outputs[0]), 10, 'Synthetic peaks');
      assert.deepEqual(called.map(row => row.slice(0, 3)), [['chrTest', '100', '300'], ['chrTest', '400', '600'], ['chrTest', '700', '900']]);
    }
    results.set(id, observed); passed(`preset-${id}-science-preview-download`, { command: spec.args[0], outputs: artifacts });
  }

  const narrowEntry = presets.find(([id, spec]) => spec.args[0] === 'callpeak' && spec.args.includes('chip.bed.gz') && [...results.get(id).keys()].some(name => name.endsWith('.narrowPeak')));
  const broadEntry = presets.find(([id, spec]) => spec.args[0] === 'callpeak' && [...results.get(id).keys()].some(name => name.endsWith('.broadPeak')));
  assert.ok(narrowEntry && broadEntry, 'Real-data narrow and broad peak calling are both demonstrated');
  const [narrowId, narrowSpec] = narrowEntry;
  const narrowPath = [...results.get(narrowId).keys()].find(name => name.endsWith('.narrowPeak'));
  const retained = results.get(narrowId).get(narrowPath);

  phase('Edit arguments and reuse generated bedGraph output');
  const doubledPath = config.commands.bdgopt.outputs[0];
  const originalSignal = bedgraphValues(await readFile(join(fixtureBase, 'signal.bdg')));
  await edit(page, ['bdgopt', '-i', doubledPath, '-m', 'multiply', '-p', '0.5', '-o', 'reused.bdg'], ['reused.bdg']);
  await run(page);
  const restored = await downloadBytes(page, 'reused.bdg');
  assert.deepEqual(bedgraphValues(restored), originalSignal, 'Chaining the generated doubled signal through multiply 0.5 recovers the input values');
  assert.deepEqual(await downloadBytes(page, narrowPath), retained, 'Chaining preserves earlier peak outputs');
  passed('edited-arguments-output-chaining', { sha256: hash(restored) });

  phase('Upload authentic compressed ChIP reads and reproduce the peak calls');
  const input = await readFile(join(fixtureBase, 'chip.bed.gz'));
  await page.locator('#upload').setInputFiles({ name: 'uploaded-chip.bed.gz', mimeType: 'application/gzip', buffer: input });
  await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Files loaded.'));
  await choose(page, narrowId);
  await edit(page, narrowSpec.args.map(arg => arg === 'chip.bed.gz' ? 'uploaded-chip.bed.gz' : arg), narrowSpec.outputs, narrowSpec.outputDirectories);
  await run(page);
  assert.deepEqual(await downloadBytes(page, narrowPath), retained, 'Uploaded input bytes reproduce the original scientific output');
  assert.deepEqual(await downloadBytes(page, 'reused.bdg'), restored, 'Upload preserves unrelated generated output');
  passed('authentic-upload-and-peak-reproduction', { inputSha256: hash(input), outputSha256: hash(retained) });

  phase('Reject invalid forms, report failed commands and prevent stale outputs');
  const before = (await workerCalls(page)).length;
  await page.locator('#arguments').fill('[5]'); await page.locator('#run').click();
  assert.match(await page.locator('#status').innerText(), /^Check command: Arguments must be a JSON array of strings/);
  assert.equal((await workerCalls(page)).length, before, 'Invalid forms do not start a worker');
  await edit(page, narrowSpec.args.map(arg => arg === 'chip.bed.gz' ? 'missing.bed.gz' : arg), narrowSpec.outputs, narrowSpec.outputDirectories);
  await page.locator('#run').click(); assert.match(await finished(page), /^Exited with status -?[1-9]\d* /);
  assert.match(await page.locator('#stderr').innerText(), /(?:No such file|not exist|cannot find|missing\.bed\.gz)/i);
  assert.deepEqual(await downloadBytes(page, narrowPath), retained);
  await edit(page, ['--help'], [narrowPath]); await page.locator('#run').click();
  assert.match(await finished(page), /Missing requested files:/);
  assert.deepEqual(await downloadBytes(page, narrowPath), retained, 'Stale output is retained for the user but does not satisfy a new command');
  passed('invalid-form-failed-command-and-stale-output');

  phase('Cancel and time out program loading, then recover');
  await edit(page, ['--help']);
  for (const mode of ['cancel', 'timeout']) {
    const held = host.holdNextRequest('/dist/macs3/manifest.json');
    try {
      await page.locator('#timeout').fill(mode === 'timeout' ? '1' : '600');
      await page.locator('#run').click(); await deadline(held.requested, 30000, 'The held manifest request did not arrive');
      if (mode === 'cancel') await page.locator('#cancel').click();
      assert.match(await finished(page), mode === 'cancel' ? /^Command cancelled\.$/ : /^TimeoutError:/);
    } finally { held.release(); }
    assert.deepEqual(await downloadBytes(page, narrowPath), retained);
  }
  await page.locator('#timeout').fill('600'); await run(page);
  assert.match(await page.locator('#stdout').innerText(), /callpeak/);
  const helpBytes = await downloadBytes(page, 'standard output');
  assert.match(helpBytes.toString('utf8'), /hmmratac/);
  passed('cancel-timeout-and-recovery');
  await page.screenshot({ path: join(reportDir, 'macs3.png'), fullPage: true });

  phase('Reload clears local files and the bundled program remains available');
  summary.workerCalls = await workerCalls(page); await page.reload(); await ready(page);
  assert.equal((await page.locator('#files').innerText()).trim(), 'No files loaded.'); assert.equal(await page.locator('#downloads a').count(), 0);
  await edit(page, ['--version']); await run(page);
  const manifest = JSON.parse(await readFile(join(root, 'dist/macs3/manifest.json'), 'utf8'));
  assert.ok((await page.locator('#stdout').innerText()).includes(manifest.version));
  summary.workerCalls.push(...await workerCalls(page));
  assert.equal(await page.evaluate(async () => (await navigator.serviceWorker.getRegistrations()).length), 0);
  passed('reload-clear-and-version');

  assert.ok(servedAssetRequests('dist/macs3/manifest.json').length > 0, 'The actual local manifest was served');
  const wasm = Object.keys(summary.assets).filter(path => path.endsWith('.wasm'));
  assert.ok(wasm.some(path => servedAssetRequests(path).length > 0), 'The actual local WebAssembly runtime was served');
  for (const pkg of manifest.packages) assert.ok(servedAssetRequests(`dist/macs3/${pkg.filename}`).length > 0, `Bundled dependency served with matching bytes: ${pkg.name}`);
  assert.ok(summary.workerCalls.length >= presets.length + 5);
  assert.ok(summary.workerCalls.every(call => new URL(call.moduleUrl).pathname === '/dist/macs3/manifest.json'));
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
