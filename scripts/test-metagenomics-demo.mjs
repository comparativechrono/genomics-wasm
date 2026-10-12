// Exercise the browser example with real reads and the actual bundled programs.
// Native-versus-WebAssembly numerical comparison is a separate release gate.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { createHash } from 'node:crypto';
import { copyFile, mkdir, readFile, readdir, stat, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { extname, join, resolve, sep } from 'node:path';
import { chromium, firefox, webkit } from 'playwright';

const root = fileURLToPath(new URL('../', import.meta.url));
const engine = process.env.BROWSER || 'chromium';
const browserType = { chromium, firefox, webkit }[engine];
if (!browserType) throw new Error('BROWSER must be chromium, firefox or webkit');
const config = JSON.parse(await readFile(join(root, 'examples/metagenomics/config.json'), 'utf8'));
const requested = process.env.METAGENOMICS_TOOL || 'all';
if (!['all', 'kraken2', 'bracken'].includes(requested)) throw new Error('METAGENOMICS_TOOL must be all, kraken2 or bracken');
if (requested !== 'all' && !config.tools[requested]) throw new Error(`${requested} is not configured in this package`);
const withBracken = requested !== 'kraken2' && !!config.tools.bracken;
assert.ok(config.tools.kraken2, 'The example requires its Kraken2 dependency');
const selectedTools = withBracken ? ['kraken2', 'bracken'] : ['kraken2'];
const reportBase = resolve(process.env.METAGENOMICS_DEMO_REPORT || join(root, 'reports/metagenomics', engine));
const reportDir = join(reportBase, `demo-${withBracken ? 'all' : 'kraken2'}`);
await mkdir(reportDir, { recursive: true });
const summary = { schema: 1, engine, tools: selectedTools, status: 'running', startedAt: new Date().toISOString(), phases: [], cases: [], assets: {}, sources: {}, browserErrors: [], browserConsole: [], requests: [], failedRequests: [], externalRequests: [], workerCalls: [] };
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const phase = message => { summary.phases.push({ at: new Date().toISOString(), message }); console.log(`[metagenomics demo] ${message}`); };
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
    // Intentionally no COOP/COEP headers or service worker.
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

async function recordTree(directory, target) {
  for (const entry of (await readdir(join(root, directory), { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
    if (entry.name === '__pycache__' || /\.py[co]$/.test(entry.name)) continue;
    const path = `${directory}/${entry.name}`;
    if (entry.isDirectory()) await recordTree(path, target);
    else { assert.ok(entry.isFile(), `Expected a regular file: ${path}`); target[path] = hash(await readFile(join(root, path))); }
  }
}

function classifications(bytes) {
  const rows = bytes.toString('utf8').trimEnd().split('\n').map(line => line.split('\t'));
  assert.equal(rows.length, 4096, 'Every real read pair receives one classification');
  assert.equal(new Set(rows.map(row => row[1])).size, rows.length, 'Read names are unique');
  for (const row of rows) {
    assert.equal(row.length, 5); assert.match(row[0], /^[CU]$/); assert.ok(row[1]);
    assert.match(row[2], /^\d+$/); assert.equal(row[3], '151|151'); assert.ok(row[4].length);
    assert.equal(row[0] === 'U', row[2] === '0');
  }
  return rows;
}

function reportRows(bytes) {
  const rows = bytes.toString('utf8').trimEnd().split('\n').map(line => line.split('\t'));
  assert.ok(rows.length > 2);
  for (const row of rows) {
    assert.equal(row.length, 6); assert.ok(Number.isFinite(Number(row[0])));
    assert.match(row[1], /^\d+$/); assert.match(row[2], /^\d+$/);
    assert.match(row[3], /^[URDKPCOFGS]\d*$/); assert.match(row[4], /^\d+$/); assert.ok(row[5].trim());
  }
  return rows;
}

function abundance(bytes, rank) {
  const [header, ...lines] = bytes.toString('utf8').trimEnd().split('\n');
  assert.equal(header, 'name\ttaxonomy_id\ttaxonomy_lvl\tkraken_assigned_reads\tadded_reads\tnew_est_reads\tfraction_total_reads');
  assert.ok(lines.length > 0);
  return lines.map(line => {
    const row = line.split('\t'); assert.equal(row.length, 7); assert.ok(row[0]); assert.equal(row[2], rank);
    for (const index of [1, 3, 4, 5]) assert.match(row[index], /^\d+$/);
    assert.equal(Number(row[3]) + Number(row[4]), Number(row[5]));
    assert.ok(Number(row[6]) >= 0 && Number(row[6]) <= 1);
    return row;
  });
}

function canonicalOptions(bytes) {
  assert.equal(bytes.length, 64, 'Native 64-bit IndexOptions layout');
  const copy = Buffer.from(bytes);
  // Only the proven C++ padding bytes are unspecified, never option fields.
  copy.fill(0, 33, 40); copy.fill(0, 60, 64);
  return copy;
}

async function ready(page) {
  await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Ready.'), null, { timeout: 30000 });
}
async function run(page) {
  await page.locator('#run').click();
  await deadline(page.waitForFunction(() => !document.getElementById('controls').disabled && !document.getElementById('status').textContent.startsWith('Running '), null, { timeout: 600000 }), 605000, 'Command exceeded 605 seconds');
  const status = await page.locator('#status').innerText();
  assert.match(status, /^Exited with status 0 /, status); assert.doesNotMatch(status, /Missing requested files:/);
}
async function downloadBytes(page, name) {
  const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('link', { name: `Download ${name}`, exact: true }).click()]);
  assert.equal(download.suggestedFilename(), name.split('/').at(-1));
  const path = await download.path(); assert.ok(path); return readFile(path);
}
async function workerCalls(page) { return page.evaluate(() => window.__metagenomicsWorkerCalls); }
async function stageModules(page, start) { return (await workerCalls(page)).slice(start).map(call => new URL(call.moduleUrl).pathname); }

let browser, page, host;
try {
  for (const tool of selectedTools) {
    await recordTree(`dist/${tool}`, summary.assets);
    await recordTree(`tools/${tool}`, summary.sources);
  }
  const runtimeFiles = ['index', 'execute', 'worker', 'kraken2', ...(withBracken ? ['bracken', 'bracken-worker', 'bracken-execute'] : [])];
  for (const path of [...runtimeFiles.map(name => `runtime/${name}.mjs`), 'scripts/test-metagenomics-demo.mjs', 'datasets/metagenomics/manifest.json']) summary.sources[path] = hash(await readFile(join(root, path)));
  await recordTree('examples/metagenomics', summary.sources);
  const fixture = join(root, 'datasets/metagenomics/zymo');
  for (const path of config.datasets.zymo.files) summary.sources[`datasets/metagenomics/zymo/${path}`] = hash(await readFile(join(fixture, path)));
  host = await serve();
  browser = await browserType.launch({ headless: true }); summary.browserVersion = browser.version();
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
  // Observe actual worker inputs without replacing any worker or program result.
  await context.addInitScript(() => {
    window.__metagenomicsWorkerCalls = [];
    const OriginalWorker = window.Worker;
    window.Worker = class extends OriginalWorker {
      constructor(url, options) { super(url, options); this.recordedUrl = String(url); }
      postMessage(message, ...rest) {
        window.__metagenomicsWorkerCalls.push({ workerUrl: this.recordedUrl, moduleUrl: message.moduleUrl, args: [...message.args] });
        return super.postMessage(message, ...rest);
      }
    };
  });
  page = await context.newPage();
  page.on('pageerror', error => summary.browserErrors.push(error.message));
  page.on('console', message => summary.browserConsole.push({ type: message.type(), text: message.text() }));
  await page.goto(`${host.origin}/examples/metagenomics/`); await ready(page);
  assert.equal(await page.evaluate(() => crossOriginIsolated), false);
  assert.equal(await page.evaluate(() => typeof SharedArrayBuffer), 'undefined');
  assert.equal(await page.evaluate(() => !!navigator.serviceWorker?.controller), false);
  passed('ordinary-static-hosting');

  phase('Load the real paired-read fixture');
  await page.locator('#tool').selectOption('kraken2'); await page.locator('#dataset').selectOption('zymo'); await page.locator('#load-data').click();
  await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Example data loaded.'), null, { timeout: 60000 });
  assert.equal(config.datasets.zymo.files.length, 12); assert.equal(await page.locator('#files li').count(), 12);
  passed('load-real-fixture', { files: 12 });

  phase('Classify paired reads and download complete results');
  await page.locator('#command').selectOption('classify'); await run(page);
  const original = await downloadBytes(page, 'sample.kraken'), originalReport = await downloadBytes(page, 'sample.report');
  const rows = classifications(original), classified = rows.filter(row => row[0] === 'C').length;
  assert.ok(classified > 0 && classified < rows.length); reportRows(originalReport);
  await page.locator('#result-file').selectOption('/work/sample.report');
  assert.equal(await page.locator('#preview').textContent(), originalReport.toString('utf8'));
  passed('paired-preview-download', { pairs: rows.length, classified, sha256: hash(original), reportSha256: hash(originalReport) });

  phase('Check confidence changes classifications and gzip preserves them');
  await page.locator('#command').selectOption('confidence'); await run(page);
  const confident = await downloadBytes(page, 'confidence.kraken'), confidentRows = classifications(confident);
  assert.notDeepEqual(confident, original); assert.ok(confidentRows.filter(row => row[0] === 'C').length < classified);
  reportRows(await downloadBytes(page, 'confidence.report')); assert.deepEqual(await downloadBytes(page, 'sample.kraken'), original);
  passed('confidence-changes-assignments', { classified: confidentRows.filter(row => row[0] === 'C').length });
  await page.locator('#command').selectOption('gzip'); await run(page);
  assert.deepEqual(await downloadBytes(page, 'gzip.kraken'), original); assert.deepEqual(await downloadBytes(page, 'gzip.report'), originalReport);
  passed('gzip-parity');

  phase('Build and inspect the reference database in browser workers');
  let before = (await workerCalls(page)).length;
  await page.locator('#command').selectOption('build');
  // Collecting the whole database directory must not hide its source inputs.
  await page.locator('#output-directories').fill('db'); await run(page);
  assert.deepEqual(await stageModules(page, before), ['/dist/kraken2/estimate_capacity.mjs', '/dist/kraken2/build_db.mjs']);
  for (const name of ['library/reference.fna', 'seqid2taxid.map', 'taxonomy/nodes.dmp', 'taxonomy/names.dmp']) {
    assert.deepEqual(await downloadBytes(page, `db/${name}`), await readFile(join(fixture, 'db', name)), `Directory collection retains ${name}`);
  }
  for (const name of ['hash.k2d', 'taxo.k2d', 'opts.k2d']) {
    const built = await downloadBytes(page, `db/${name}`), reference = await readFile(join(fixture, 'db', name));
    assert.deepEqual(name === 'opts.k2d' ? canonicalOptions(built) : built, reference, `Rebuilt ${name} matches the native fixture`);
  }
  await page.locator('#command').selectOption('classify'); await run(page);
  assert.deepEqual(await downloadBytes(page, 'sample.kraken'), original); assert.deepEqual(await downloadBytes(page, 'sample.report'), originalReport);
  passed('build-database-and-reclassify');
  await page.locator('#command').selectOption('inspect'); await run(page);
  const inspection = await page.locator('#stdout').innerText();
  const inspectionRows = inspection.split('\n').filter(line => /^\s*\d+\.\d+\t/.test(line));
  assert.ok(inspectionRows.length >= 10, 'Inspect includes taxon rows, not only a summary');
  assert.match(inspection, /Escherichia/); assert.match(inspection, /Salmonella/); assert.match(inspection, /Shigella/);
  passed('inspect-taxonomic-content', { taxa: inspectionRows.length });

  phase('Upload read files and a database folder, then edit the command');
  await page.locator('#upload').setInputFiles(await Promise.all([1, 2].map(async mate => ({ name: `uploaded_${mate}.fastq`, mimeType: 'application/octet-stream', buffer: await readFile(join(fixture, `reads_${mate}.fastq`)) }))));
  await page.waitForFunction(() => document.getElementById('files').textContent.includes('uploaded_2.fastq'));
  const uploadDirectory = join(reportDir, 'uploaded-db'); await mkdir(uploadDirectory, { recursive: true });
  for (const name of ['hash.k2d', 'taxo.k2d', 'opts.k2d']) await copyFile(join(fixture, 'db', name), join(uploadDirectory, name));
  await page.locator('#upload-folder').setInputFiles(uploadDirectory);
  await page.waitForFunction(() => document.getElementById('files').textContent.includes('uploaded-db/opts.k2d'));
  await page.locator('#command').selectOption('classify');
  await page.locator('#arguments').fill(JSON.stringify(['--db', 'uploaded-db', '--paired', '--report', 'uploaded.report', '--output', 'uploaded.kraken', 'uploaded_1.fastq', 'uploaded_2.fastq']));
  await page.locator('#outputs').fill('uploaded.report\nuploaded.kraken'); await run(page);
  assert.deepEqual(await downloadBytes(page, 'uploaded.kraken'), original); assert.deepEqual(await downloadBytes(page, 'uploaded.report'), originalReport);
  assert.deepEqual(await downloadBytes(page, 'sample.kraken'), original);
  passed('upload-reads-folder-edit-retain-output');

  let speciesBytes;
  if (withBracken) {
    phase('Build the Bracken distribution through all three actual stages');
    await page.locator('#tool').selectOption('bracken'); await page.locator('#command').selectOption('build');
    before = (await workerCalls(page)).length; await run(page);
    assert.deepEqual(await stageModules(page, before), ['/dist/kraken2/classify.mjs', '/dist/bracken/kmer2read_distr.mjs', '/dist/bracken/manifest.json']);
    const referenceCalls = await downloadBytes(page, 'db/database.kraken');
    assert.equal(referenceCalls.toString('utf8').trimEnd().split('\n').length, 3);
    const kmers = await downloadBytes(page, 'db/database151mers.kraken');
    assert.equal(kmers.includes(0), false); assert.equal(kmers.toString('utf8').trimEnd().split('\n').length, 3);
    const distribution = await downloadBytes(page, 'db/database151mers.kmer_distrib');
    assert.deepEqual(distribution, await readFile(join(fixture, 'db/database151mers.kmer_distrib')));
    passed('bracken-build-three-stages', { classificationSha256: hash(referenceCalls), kmerSha256: hash(kmers), distributionSha256: hash(distribution) });
    phase('Estimate species and genus abundances from the generated report');
    await page.locator('#command').selectOption('species'); await run(page);
    speciesBytes = await downloadBytes(page, 'species.bracken'); const species = abundance(speciesBytes, 'S');
    assert.ok(species.some(row => Number(row[4]) > 0), 'Real shared assignments cause positive redistribution');
    reportRows(await downloadBytes(page, 'species.report'));
    passed('bracken-species-redistribution', { taxa: species.length, sha256: hash(speciesBytes) });
    await page.locator('#command').selectOption('genus'); await run(page);
    const genusBytes = await downloadBytes(page, 'genus.bracken'), genera = abundance(genusBytes, 'G');
    assert.notDeepEqual(genusBytes, speciesBytes); assert.notDeepEqual(genera.map(row => row[1]), species.map(row => row[1]));
    reportRows(await downloadBytes(page, 'genus.report')); assert.deepEqual(await downloadBytes(page, 'species.bracken'), speciesBytes);
    passed('bracken-genus-rank', { taxa: genera.length, sha256: hash(genusBytes) });
  }

  phase('Reject invalid arguments while retaining earlier files');
  await page.locator('#arguments').fill('["--help", 5]'); before = (await workerCalls(page)).length; await page.locator('#run').click();
  assert.match(await page.locator('#status').innerText(), /^Check command: Arguments must be a JSON array of strings/);
  assert.equal((await workerCalls(page)).length, before); assert.deepEqual(await downloadBytes(page, 'sample.kraken'), original);
  if (withBracken) assert.deepEqual(await downloadBytes(page, 'species.bracken'), speciesBytes);
  passed('invalid-arguments-preserve-files');

  phase('Cancel a held runtime request and rerun a real calculation');
  await page.locator('#tool').selectOption(withBracken ? 'bracken' : 'kraken2');
  await page.locator('#command').selectOption(withBracken ? 'species' : 'classify');
  const held = host.holdNextRequest(withBracken ? '/dist/bracken/pyodide/pyodide.asm.wasm' : '/dist/kraken2/classify.wasm');
  try {
    await page.locator('#run').click(); await deadline(held.requested, 60000, 'The held WebAssembly request did not arrive');
    await page.locator('#cancel').click(); await page.waitForFunction(() => document.getElementById('status').textContent === 'Command cancelled.');
  } finally { held.release(); }
  assert.deepEqual(await downloadBytes(page, 'sample.kraken'), original);
  if (withBracken) assert.deepEqual(await downloadBytes(page, 'species.bracken'), speciesBytes);
  await run(page);
  assert.deepEqual(await downloadBytes(page, withBracken ? 'species.bracken' : 'sample.kraken'), withBracken ? speciesBytes : original);
  passed('cancel-runtime-and-rerun-calculation');
  await page.screenshot({ path: join(reportDir, 'example.png'), fullPage: true });
  summary.workerCalls = await workerCalls(page);

  phase('Reload clears files and command help still works');
  await page.reload(); await ready(page);
  assert.equal((await page.locator('#files').innerText()).trim(), 'No files loaded.'); assert.equal(await page.locator('#downloads a').count(), 0);
  for (const tool of selectedTools) {
    await page.locator('#tool').selectOption(tool); await page.locator('#command').selectOption('help'); await run(page);
    assert.match((await page.locator('#stdout').innerText()) + (await page.locator('#stderr').innerText()), new RegExp(tool, 'i'));
  }
  assert.equal(await page.evaluate(() => crossOriginIsolated), false);
  assert.equal(await page.evaluate(async () => (await navigator.serviceWorker.getRegistrations()).length), 0);
  passed('reload-clear-help');

  const paths = summary.requests.filter(request => /^https?:/.test(request.url)).map(request => new URL(request.url).pathname);
  const requiredAssets = ['classify', 'estimate_capacity', 'build_db', 'dump_table'].flatMap(name => [`/dist/kraken2/${name}.mjs`, `/dist/kraken2/${name}.wasm`]);
  if (withBracken) requiredAssets.push('/dist/bracken/kmer2read_distr.mjs', '/dist/bracken/kmer2read_distr.wasm', '/dist/bracken/manifest.json', '/dist/bracken/pyodide/pyodide.mjs', '/dist/bracken/pyodide/pyodide.asm.wasm', '/dist/bracken/pyodide/python_stdlib.zip', '/dist/bracken/python/est_abundance.py', '/dist/bracken/python/generate_kmer_distribution.py');
  for (const asset of requiredAssets) assert.ok(paths.includes(asset), `Actual local runtime asset was fetched: ${asset}`);
  if (!withBracken) assert.equal(paths.some(path => path.startsWith('/dist/bracken/') || /\/runtime\/bracken/.test(path)), false, 'Kraken-only checks do not import Bracken');
  assert.deepEqual(summary.externalRequests, [], 'No runtime assets came from an external origin'); assert.deepEqual(summary.browserErrors, []);
  passed('all-runtime-requests-local');
  assert.equal(summary.cases.length, withBracken ? 15 : 12);
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
