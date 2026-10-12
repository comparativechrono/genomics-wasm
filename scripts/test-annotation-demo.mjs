// Exercise real browser commands, file transfers and command chaining.
// Native-versus-WebAssembly numerical comparisons are a separate release gate.
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
const config = JSON.parse(await readFile(join(root, 'examples/annotation/config.json'), 'utf8'));
const requested = process.env.ANNOTATION_TOOL || 'all';
if (!['all', 'prodigal', 'hmmer'].includes(requested)) throw new Error('ANNOTATION_TOOL must be all, prodigal or hmmer');
const selectedTools = requested === 'all' ? Object.keys(config.tools) : [requested];
assert.ok(selectedTools.length > 0);
for (const tool of selectedTools) assert.ok(config.tools[tool] && config.datasets[tool], `${tool} is configured in this package`);
const reportBase = resolve(process.env.ANNOTATION_DEMO_REPORT || join(root, 'reports/annotation', engine));
const reportDir = join(reportBase, `demo-${selectedTools.length === 2 ? 'all' : selectedTools[0]}`);
await mkdir(reportDir, { recursive: true });
const summary = { schema: 1, engine, tools: selectedTools, status: 'running', startedAt: new Date().toISOString(), phases: [], cases: [], assets: {}, sources: {}, browserErrors: [], browserConsole: [], requests: [], failedRequests: [], externalRequests: [], workerCalls: [] };
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const phase = message => { summary.phases.push({ at: new Date().toISOString(), message }); console.log(`[annotation demo] ${message}`); };
const passed = (id, details = {}) => summary.cases.push({ id, status: 'passed', ...details });
const mime = { '.html': 'text/html', '.mjs': 'text/javascript', '.js': 'text/javascript', '.css': 'text/css', '.wasm': 'application/wasm', '.json': 'application/json' };
const hmmerPrograms = ['hmmbuild', 'hmmsearch', 'hmmscan', 'hmmpress', 'hmmfetch', 'hmmalign', 'hmmconvert', 'hmmemit', 'hmmstat', 'phmmer', 'jackhmmer', 'nhmmer', 'nhmmscan'];

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

const tableRows = bytes => bytes.toString('utf8').split('\n').filter(line => line.trim() && !line.startsWith('#')).map(line => line.trim().split(/\s+/));
function proteinHits(bytes) {
  const rows = tableRows(bytes);
  assert.ok(rows.length > 0, 'Protein searches return biological matches');
  for (const row of rows) {
    assert.ok(row.length >= 18); assert.ok(row[0] && row[2]);
    assert.ok(Number.isFinite(Number(row[4])) && Number(row[4]) >= 0, 'Finite E-value');
    assert.ok(Number.isFinite(Number(row[5])), 'Finite bit score');
  }
  return rows;
}
function genes(bytes) {
  const rows = bytes.toString('utf8').split('\n').filter(line => line && !line.startsWith('#')).map(line => line.split('\t'));
  assert.ok(rows.length >= 10, 'A real reference window produces multiple genes');
  for (const row of rows) {
    assert.equal(row.length, 9); assert.equal(row[2], 'CDS');
    assert.ok(Number(row[3]) >= 1 && Number(row[4]) >= Number(row[3]));
    assert.match(row[6], /^[+-]$/); assert.match(row[8], /ID=/);
  }
  return rows;
}
function fasta(bytes, alphabet) {
  const records = bytes.toString('utf8').trim().split(/^>/m).filter(Boolean).map(record => {
    const [header, ...lines] = record.split('\n');
    const sequence = lines.join(''); assert.ok(header && sequence); assert.match(sequence, alphabet);
    return { header, sequence };
  });
  assert.ok(records.length > 0); return records;
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
async function choose(page, tool, command) {
  await page.locator('#tool').selectOption(tool);
  await page.locator('#command').selectOption(command);
}
async function load(page, tool) {
  await page.locator('#dataset').selectOption(tool); await page.locator('#load-data').click();
  await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Example data loaded.'), null, { timeout: 60000 });
  assert.equal(await page.locator('#files li').count(), config.datasets[tool].files.length);
}
async function edit(page, args, outputs = [], directories = []) {
  await page.locator('#arguments').fill(JSON.stringify(args));
  await page.locator('#outputs').fill(outputs.join('\n')); await page.locator('#output-directories').fill(directories.join('\n'));
}
async function downloadBytes(page, name) {
  const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('link', { name: `Download ${name}`, exact: true }).click()]);
  assert.equal(download.suggestedFilename(), name.split('/').at(-1));
  const path = await download.path(); assert.ok(path); return readFile(path);
}
async function workerCalls(page) { return page.evaluate(() => window.__annotationWorkerCalls); }

async function failureAndCancellation(page, host, tool, command, retainedName, retainedBytes, module) {
  phase(`${tool}: reject invalid commands and prevent stale output reuse`);
  await choose(page, tool, command);
  await page.locator('#arguments').fill('["-h", 5]');
  const before = (await workerCalls(page)).length; await page.locator('#run').click();
  assert.match(await page.locator('#status').innerText(), /^Check command: Arguments must be a JSON array of strings/);
  assert.equal((await workerCalls(page)).length, before);
  assert.deepEqual(await downloadBytes(page, retainedName), retainedBytes);
  passed(`${tool}-invalid-arguments-preserve-files`);

  // A successful help command cannot pass merely because its requested output
  // exists from a preceding run. It must report that the new output is missing.
  await edit(page, tool === 'prodigal' ? ['-h'] : ['hmmsearch', '-h'], [retainedName]);
  await page.locator('#run').click();
  assert.match(await finished(page), /Missing requested files:/);
  assert.deepEqual(await downloadBytes(page, retainedName), retainedBytes);
  passed(`${tool}-stale-output-not-reused`);

  await edit(page, tool === 'prodigal' ? ['--not-an-option'] : ['hmmsearch', '--not-an-option']);
  await page.locator('#run').click();
  assert.match(await finished(page), /^Exited with status [1-9]\d* /);
  assert.ok((await page.locator('#stdout').innerText()) + (await page.locator('#stderr').innerText()));
  assert.deepEqual(await downloadBytes(page, retainedName), retainedBytes);
  passed(`${tool}-failed-command-preserves-files`);

  phase(`${tool}: cancel a held WebAssembly request and rerun`);
  await choose(page, tool, command);
  const held = host.holdNextRequest(`/dist/${tool}/${module}.wasm`);
  try {
    await page.locator('#run').click(); await deadline(held.requested, 60000, 'The held WebAssembly request did not arrive');
    await page.locator('#cancel').click(); await page.waitForFunction(() => document.getElementById('status').textContent === 'Command cancelled.');
  } finally { held.release(); }
  assert.deepEqual(await downloadBytes(page, retainedName), retainedBytes);
  await run(page);
  const rerun = await downloadBytes(page, retainedName);
  if (tool === 'hmmer') assert.deepEqual(tableRows(rerun), tableRows(retainedBytes));
  else assert.deepEqual(rerun, retainedBytes);
  passed(`${tool}-cancel-and-rerun`);
}

let browser, page, host;
try {
  for (const tool of selectedTools) {
    await recordTree(`dist/${tool}`, summary.assets); await recordTree(`tools/${tool}`, summary.sources);
    const fixture = join(root, `datasets/annotation/${tool}`);
    for (const name of config.datasets[tool].files) summary.sources[`datasets/annotation/${tool}/${name}`] = hash(await readFile(join(fixture, name)));
  }
  for (const name of ['index', 'execute', 'worker', ...selectedTools]) summary.sources[`runtime/${name}.mjs`] = hash(await readFile(join(root, `runtime/${name}.mjs`)));
  for (const name of ['scripts/test-annotation-demo.mjs', 'datasets/annotation/manifest.json']) summary.sources[name] = hash(await readFile(join(root, name)));
  await recordTree('examples/annotation', summary.sources);
  host = await serve(); browser = await browserType.launch({ headless: true }); summary.browserVersion = browser.version();
  const context = await browser.newContext({ viewport: { width: 1100, height: 900 } }); context.setDefaultTimeout(30000);
  context.on('request', request => summary.requests.push({ url: request.url(), type: request.resourceType() }));
  context.on('requestfailed', request => summary.failedRequests.push({ url: request.url(), error: request.failure()?.errorText }));
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (/^https?:$/.test(url.protocol) && url.origin !== host.origin) { summary.externalRequests.push(url.href); await route.abort('blockedbyclient'); }
    else await route.continue();
  });
  // Observe real worker calls without substituting workers or program results.
  await context.addInitScript(() => {
    window.__annotationWorkerCalls = [];
    const OriginalWorker = window.Worker;
    window.Worker = class extends OriginalWorker {
      constructor(url, options) { super(url, options); this.recordedUrl = String(url); }
      postMessage(message, ...rest) {
        window.__annotationWorkerCalls.push({ workerUrl: this.recordedUrl, moduleUrl: message.moduleUrl, args: [...message.args] });
        return super.postMessage(message, ...rest);
      }
    };
  });
  page = await context.newPage();
  page.on('pageerror', error => summary.browserErrors.push(error.message));
  page.on('console', message => summary.browserConsole.push({ type: message.type(), text: message.text() }));
  await page.goto(`${host.origin}/examples/annotation/`); await ready(page);
  assert.equal(await page.evaluate(() => crossOriginIsolated), false);
  assert.equal(await page.evaluate(() => typeof SharedArrayBuffer), 'undefined');
  assert.equal(await page.evaluate(() => !!navigator.serviceWorker?.controller), false);
  passed('ordinary-static-hosting');

  if (selectedTools.includes('prodigal')) {
    phase('Prodigal: predict genes and translate a real E. coli reference window');
    await choose(page, 'prodigal', 'single'); await load(page, 'prodigal'); await run(page);
    const originalGff = await downloadBytes(page, 'genes.gff'), originalProteins = await downloadBytes(page, 'proteins.faa');
    const coordinates = genes(originalGff), proteins = fasta(originalProteins, /^[A-Z*]+$/);
    assert.equal(proteins.length, coordinates.length); assert.equal(fasta(await downloadBytes(page, 'genes.fna'), /^[ACGTN]+$/).length, coordinates.length);
    await page.locator('#result-file').selectOption('/work/genes.gff');
    assert.equal(await page.locator('#preview').textContent(), originalGff.toString('utf8'));
    passed('prodigal-predict-preview-download', { genes: coordinates.length, sha256: hash(originalGff), proteinSha256: hash(originalProteins) });

    phase('Prodigal: upload a different real sequence and regenerate the same output paths');
    const fixture = join(root, 'datasets/annotation/prodigal');
    await page.locator('#upload').setInputFiles({ name: 'uploaded.fna', mimeType: 'application/octet-stream', buffer: await readFile(join(fixture, 'salmonella.fna')) });
    await page.waitForFunction(() => document.getElementById('files').textContent.includes('uploaded.fna'));
    const args = [...config.tools.prodigal.commands.single.args]; args[args.indexOf('-i') + 1] = 'uploaded.fna';
    await edit(page, args, config.tools.prodigal.commands.single.outputs); await run(page);
    const differentGff = await downloadBytes(page, 'genes.gff'); genes(differentGff); assert.notDeepEqual(differentGff, originalGff);
    assert.notDeepEqual(await downloadBytes(page, 'proteins.faa'), originalProteins);
    await choose(page, 'prodigal', 'single'); await run(page); assert.deepEqual(await downloadBytes(page, 'genes.gff'), originalGff);
    passed('prodigal-upload-and-regenerate-same-path');

    const folder = join(reportDir, 'uploaded-contigs'); await mkdir(folder, { recursive: true });
    await copyFile(join(fixture, 'ecoli.fna'), join(folder, 'ecoli.fna'));
    await page.locator('#upload-folder').setInputFiles(folder);
    await page.waitForFunction(() => document.getElementById('files').textContent.includes('uploaded-contigs/ecoli.fna'));
    await edit(page, ['-i', 'uploaded-contigs/ecoli.fna', '-p', 'single', '-f', 'gff', '-o', 'uploaded-contigs/genes.gff', '-a', 'uploaded-contigs/proteins.faa', '-q'], ['uploaded-contigs/genes.gff', 'uploaded-contigs/proteins.faa'], ['uploaded-contigs']);
    await run(page);
    assert.deepEqual(await downloadBytes(page, 'uploaded-contigs/ecoli.fna'), await readFile(join(fixture, 'ecoli.fna')));
    assert.deepEqual(await downloadBytes(page, 'uploaded-contigs/proteins.faa'), originalProteins);
    assert.deepEqual(genes(await downloadBytes(page, 'uploaded-contigs/genes.gff')), coordinates);
    passed('prodigal-directory-collection-retains-source');

    await choose(page, 'prodigal', 'meta'); await run(page);
    const mixedRows = genes(await downloadBytes(page, 'mixed.gff')); assert.ok(new Set(mixedRows.map(row => row[0])).size >= 2);
    assert.equal(fasta(await downloadBytes(page, 'mixed.faa'), /^[A-Z*]+$/).length, mixedRows.length);
    passed('prodigal-metagenomic-contigs', { genes: mixedRows.length });
    await failureAndCancellation(page, host, 'prodigal', 'single', 'genes.gff', originalGff, 'prodigal');

    if (selectedTools.includes('hmmer')) {
      phase('Pass Prodigal proteins directly to a real HMMER comparison');
      await choose(page, 'hmmer', 'predicted'); await run(page);
      const hits = proteinHits(await downloadBytes(page, 'predicted.tbl'));
      assert.ok(hits.some(row => row[0] === row[2]), 'Generated proteins have genuine self-matches');
      assert.deepEqual(await downloadBytes(page, 'proteins.faa'), originalProteins);
      passed('prodigal-to-hmmer-generated-proteins', { matches: hits.length });
    }
    await page.screenshot({ path: join(reportDir, 'prodigal.png'), fullPage: true });
  }

  if (selectedTools.includes('hmmer')) {
    phase('HMMER: build, press and scan a real globin profile');
    await choose(page, 'hmmer', 'build'); await load(page, 'hmmer'); await run(page);
    assert.match((await downloadBytes(page, 'globins.hmm')).toString('utf8'), /^HMMER3\/f/);
    await choose(page, 'hmmer', 'press'); await run(page);
    const indexes = new Map();
    for (const suffix of ['h3f', 'h3i', 'h3m', 'h3p']) {
      const bytes = await downloadBytes(page, `globins.hmm.${suffix}`); assert.ok(bytes.length > 0); indexes.set(suffix, bytes);
    }
    await run(page);
    for (const [suffix, bytes] of indexes) assert.deepEqual(await downloadBytes(page, `globins.hmm.${suffix}`), bytes);
    passed('hmmer-build-press-repeat', { indexes: indexes.size });
    await choose(page, 'hmmer', 'scan'); await run(page);
    const scan = await downloadBytes(page, 'scan.tbl'), scanRows = proteinHits(scan);
    assert.ok(scanRows.some(row => row[0] === 'Globin' && row[2] === 'HBB_HUMAN'));
    assert.ok(tableRows(await downloadBytes(page, 'scan.domtbl')).length > 0);
    await page.locator('#result-file').selectOption('/work/scan.tbl'); assert.equal(await page.locator('#preview').textContent(), scan.toString('utf8'));
    passed('hmmer-scan-preview-download', { matches: scanRows.length, sha256: hash(scan) });

    await choose(page, 'hmmer', 'search'); await run(page);
    assert.ok(proteinHits(await downloadBytes(page, 'search.tbl')).some(row => row[0] === 'HBB_HUMAN' && row[2] === 'Globin'));
    await choose(page, 'hmmer', 'align'); await run(page);
    const alignment = (await downloadBytes(page, 'aligned.sto')).toString('utf8');
    assert.match(alignment, /^# STOCKHOLM 1\.0/); assert.match(alignment, /HBB_HUMAN/); assert.match(alignment, /\/\//);
    await choose(page, 'hmmer', 'compare'); await run(page);
    assert.ok(proteinHits(await downloadBytes(page, 'homologs.tbl')).some(row => row[0] === 'HBB_HUMAN' && row[2] === 'HBB_HUMAN'));
    await choose(page, 'hmmer', 'stats'); await run(page); assert.match(await page.locator('#stdout').innerText(), /Globin/);
    passed('hmmer-search-align-compare-stats');

    phase('HMMER: upload source alignments, collect their directory and regenerate a profile');
    const fixture = join(root, 'datasets/annotation/hmmer'), folder = join(reportDir, 'uploaded-alignments');
    await mkdir(folder, { recursive: true }); await copyFile(join(fixture, 'globins.sto'), join(folder, 'globins.sto'));
    await page.locator('#upload-folder').setInputFiles(folder);
    await page.waitForFunction(() => document.getElementById('files').textContent.includes('uploaded-alignments/globins.sto'));
    await edit(page, ['hmmbuild', '--amino', '--seed', '42', '-n', 'UploadedGlobin', 'uploaded-alignments/globins.hmm', 'uploaded-alignments/globins.sto'], ['uploaded-alignments/globins.hmm'], ['uploaded-alignments']);
    await run(page);
    assert.deepEqual(await downloadBytes(page, 'uploaded-alignments/globins.sto'), await readFile(join(fixture, 'globins.sto')));
    assert.match((await downloadBytes(page, 'uploaded-alignments/globins.hmm')).toString('utf8'), /^NAME\s+UploadedGlobin$/m);
    passed('hmmer-upload-folder-retains-source');
    await page.locator('#upload').setInputFiles({ name: 'uploaded-protein.faa', mimeType: 'application/octet-stream', buffer: await readFile(join(fixture, 'hbb-human.faa')) });
    await page.waitForFunction(() => document.getElementById('files').textContent.includes('uploaded-protein.faa'));
    await edit(page, ['hmmscan', '--tblout', 'scan.tbl', '--domtblout', 'scan.domtbl', '-o', 'scan.txt', 'globins.hmm', 'uploaded-protein.faa'], ['scan.tbl', 'scan.domtbl', 'scan.txt']); await run(page);
    const oneProteinRows = proteinHits(await downloadBytes(page, 'scan.tbl'));
    assert.ok(oneProteinRows.every(row => row[2] === 'HBB_HUMAN')); assert.ok(oneProteinRows.length < scanRows.length);
    await choose(page, 'hmmer', 'scan'); await run(page); assert.deepEqual(tableRows(await downloadBytes(page, 'scan.tbl')), scanRows);
    passed('hmmer-upload-and-regenerate-same-path');

    await choose(page, 'hmmer', 'dna-build'); await run(page);
    await choose(page, 'hmmer', 'dna-search'); await run(page);
    const dnaRows = tableRows(await downloadBytes(page, 'dna.tbl')); assert.ok(dnaRows.length >= 4, 'Known MADE1 repeats are detected in real human DNA');
    assert.ok(dnaRows.every(row => row.length >= 15 && row[2] === 'MADE1'));
    passed('hmmer-dna-profile-and-search', { matches: dnaRows.length });
    await failureAndCancellation(page, host, 'hmmer', 'scan', 'scan.tbl', scan, 'hmmscan');
    await page.screenshot({ path: join(reportDir, 'hmmer.png'), fullPage: true });
  }

  phase('Reload clears files and all included program help remains usable');
  summary.workerCalls = await workerCalls(page);
  await page.reload(); await ready(page);
  assert.equal((await page.locator('#files').innerText()).trim(), 'No files loaded.'); assert.equal(await page.locator('#downloads a').count(), 0);
  for (const tool of selectedTools) {
    await choose(page, tool, 'help');
    const programs = tool === 'hmmer' ? hmmerPrograms : ['prodigal'];
    for (const program of programs) {
      await edit(page, tool === 'hmmer' ? [program, '-h'] : ['-h']); await run(page);
      assert.match((await page.locator('#stdout').innerText()) + (await page.locator('#stderr').innerText()), new RegExp(tool === 'hmmer' ? program : 'prodigal', 'i'));
    }
  }
  summary.workerCalls.push(...await workerCalls(page));
  assert.equal(await page.evaluate(() => crossOriginIsolated), false);
  assert.equal(await page.evaluate(async () => (await navigator.serviceWorker.getRegistrations()).length), 0);
  passed('reload-clear-all-program-help');
  const paths = summary.requests.filter(request => /^https?:/.test(request.url)).map(request => new URL(request.url).pathname);
  for (const tool of selectedTools) {
    for (const program of tool === 'hmmer' ? hmmerPrograms : ['prodigal']) {
      for (const extension of ['mjs', 'wasm']) assert.ok(paths.includes(`/dist/${tool}/${program}.${extension}`), `Real local asset fetched: ${tool}/${program}.${extension}`);
    }
  }
  for (const tool of ['prodigal', 'hmmer'].filter(tool => !selectedTools.includes(tool))) {
    assert.equal(paths.some(path => path.startsWith(`/dist/${tool}/`) || path === `/runtime/${tool}.mjs`), false, `Independent ${requested} check does not load ${tool}`);
  }
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
