// Exercise real browser commands, file transfers and command chaining.
// Native-versus-WebAssembly numerical comparisons are a separate release gate.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { createHash, randomUUID } from 'node:crypto';
import { mkdir, readFile, readdir, stat, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { extname, join, resolve, sep } from 'node:path';
import { chromium, firefox, webkit } from 'playwright';

const root = fileURLToPath(new URL('../', import.meta.url));
const engine = process.env.BROWSER || process.env.BROWSER_ENGINE || 'chromium';
const browserType = { chromium, firefox, webkit }[engine];
if (!browserType) throw new Error('BROWSER must be chromium, firefox or webkit');
const config = JSON.parse(await readFile(join(root, 'examples/primer3/config.json'), 'utf8'));
assert.ok(config.programs);
assert.ok(config.datasets.primer3);
const reportBase = resolve(process.env.PRIMER3_DEMO_REPORT || join(root, 'reports/primer3', engine));
const reportDir = join(reportBase, 'browser-demo');
await mkdir(reportDir, { recursive: true });
const summary = { schema: 1, runId: randomUUID(), engine, tools: ['primer3'], status: 'running', startedAt: new Date().toISOString(), phases: [], cases: [], assets: {}, sources: {}, browserErrors: [], browserConsole: [], requests: [], serverRequests: [], failedRequests: [], externalRequests: [], workerCalls: [], downloads: [] };
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const phase = message => { summary.phases.push({ at: new Date().toISOString(), message }); console.log(`[primer3 demo] ${message}`); };
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
    // Intentionally no isolation headers: an ordinary static host must work.
    response.setHeader('Cache-Control', 'no-store');
    try {
      const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
      // Independently record the actual served bytes and completed responses.
      const served = { pathname, method: request.method, completed: false };
      summary.serverRequests.push(served);
      response.once('finish', () => { served.completed = true; served.status = response.statusCode; });
      const held = holds.get(pathname);
      if (held) { holds.delete(pathname); held.arrived(); await held.unblocked; }
      if (response.destroyed) return;
      const path = resolve(root, `.${pathname}`);
      if (!path.startsWith(root.endsWith(sep) ? root : `${root}${sep}`)) { response.writeHead(403).end(); return; }
      const filename = (await stat(path)).isDirectory() ? join(path, 'index.html') : path;
      const bytes = await readFile(filename);
      served.bytes = bytes.length;
      served.sha256 = hash(bytes);
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

function servedAssetRequests(path, since = 0) {
  return summary.serverRequests.slice(since).filter(request => request.pathname === `/${path}` && request.method === 'GET' && request.completed && request.status === 200 && request.bytes > 0 && request.sha256 === summary.assets[path]);
}

function boulder(bytes) {
  const fields = Object.create(null);
  for (const line of bytes.toString('utf8').split(/\r?\n/)) {
    const split = line.indexOf('='); if (split > 0) fields[line.slice(0, split)] = line.slice(split + 1);
  }
  return fields;
}
function checkPairs(bytes, template, count) {
  const fields = boulder(bytes);
  assert.equal(fields.PRIMER_ERROR, undefined);
  assert.equal(Number(fields.PRIMER_PAIR_NUM_RETURNED), count);
  const complement = { A: 'T', C: 'G', G: 'C', T: 'A' };
  for (let i = 0; i < count; i++) {
    const [left, leftLength] = fields[`PRIMER_LEFT_${i}`].split(',').map(Number);
    const [right, rightLength] = fields[`PRIMER_RIGHT_${i}`].split(',').map(Number);
    assert.equal(fields[`PRIMER_LEFT_${i}_SEQUENCE`], template.slice(left, left + leftLength));
    const reverse = [...template.slice(right - rightLength + 1, right + 1)].reverse().map(base => complement[base]).join('');
    assert.equal(fields[`PRIMER_RIGHT_${i}_SEQUENCE`], reverse);
    const product = Number(fields[`PRIMER_PAIR_${i}_PRODUCT_SIZE`]);
    assert.equal(product, right - left + 1); assert.ok(product >= 120 && product <= 220);
    for (const side of ['LEFT', 'RIGHT']) assert.ok(Number.isFinite(Number(fields[`PRIMER_${side}_${i}_TM`])));
  }
  return fields;
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
  assert.equal(await page.locator('#files li').count(), config.datasets.primer3.files.length);
}
async function edit(page, args, outputs = [], directories = [], stdinFile = null, stdoutPath = '') {
  await page.locator('#arguments').fill(JSON.stringify(args));
  await page.locator('#outputs').fill(outputs.join('\n')); await page.locator('#output-directories').fill(directories.join('\n'));
  const advanced = page.locator('details');
  if (!await advanced.evaluate(element => element.open)) await advanced.locator('summary').click();
  await page.locator('#stdin-mode').selectOption(stdinFile ? 'file' : 'none');
  if (stdinFile) await page.locator('#stdin-file').fill(stdinFile);
  await page.locator('#stdout-path').fill(stdoutPath);
}
let lastDownloadClick = 0;
async function downloadBytes(page, name) {
  // Pace real clicks to avoid the browser's automatic download flood limiter.
  const delay = 250 - (performance.now() - lastDownloadClick);
  if (delay > 0) await new Promise(resolve => setTimeout(resolve, delay));
  lastDownloadClick = performance.now();
  const record = { name, startedAt: new Date().toISOString() }; summary.downloads.push(record);
  const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('link', { name: `Download ${name}`, exact: true }).click()]);
  if (name === 'standard output') assert.match(download.suggestedFilename(), /-stdout\.txt$/);
  else assert.equal(download.suggestedFilename(), name.split('/').at(-1));
  const path = await download.path(); assert.ok(path);
  const bytes = await readFile(path); record.completedAt = new Date().toISOString(); record.bytes = bytes.length; record.sha256 = hash(bytes);
  return bytes;
}
async function workerCalls(page) { return page.evaluate(() => window.__primer3WorkerCalls); }


let browser, page, host;
try {
  await recordTree('dist/primer3', summary.assets); await recordTree('tools/primer3', summary.sources);
  await recordTree('datasets/primer3', summary.sources); await recordTree('examples/primer3', summary.sources);
  for (const name of ['index', 'execute', 'worker', 'primer3']) summary.sources[`runtime/${name}.mjs`] = hash(await readFile(join(root, `runtime/${name}.mjs`)));
  summary.sources['scripts/test-primer3-demo.mjs'] = hash(await readFile(join(root, 'scripts/test-primer3-demo.mjs')));
  host = await serve(); browser = await browserType.launch({ headless: true }); summary.browserVersion = browser.version();
  const context = await browser.newContext({ viewport: { width: 1100, height: 900 } }); context.setDefaultTimeout(30000);
  context.on('request', request => summary.requests.push({ url: request.url(), type: request.resourceType() }));
  context.on('requestfailed', request => summary.failedRequests.push({ url: request.url(), error: request.failure()?.errorText }));
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (/^https?:$/.test(url.protocol) && url.origin !== host.origin) { summary.externalRequests.push(url.href); await route.abort('blockedbyclient'); }
    else await route.continue();
  });
  // Observe actual workers; never substitute the native program or its output.
  await context.addInitScript(() => {
    window.__primer3WorkerCalls = [];
    const OriginalWorker = window.Worker;
    window.Worker = class extends OriginalWorker {
      constructor(url, options) { super(url, options); this.recordedUrl = String(url); }
      postMessage(message, ...rest) {
        window.__primer3WorkerCalls.push({ workerUrl: this.recordedUrl, moduleUrl: message.moduleUrl, args: [...message.args] });
        return super.postMessage(message, ...rest);
      }
    };
  });
  page = await context.newPage();
  page.on('pageerror', error => summary.browserErrors.push(error.message));
  page.on('console', message => summary.browserConsole.push({ type: message.type(), text: message.text() }));
  await page.goto(`${host.origin}/examples/primer3/`); await ready(page);
  summary.crossOriginIsolated = await page.evaluate(() => crossOriginIsolated);
  assert.equal(summary.crossOriginIsolated, false);
  assert.equal(await page.evaluate(() => !!navigator.serviceWorker?.controller), false);
  passed('ordinary-static-hosting');

  const fixtureBase = fileURLToPath(new URL(config.datasets.primer3.path, new URL('../examples/primer3/app.mjs', import.meta.url)));
  const originalInput = await readFile(join(fixtureBase, 'real/hbb-pairs.boulder'));
  const template = boulder(originalInput).SEQUENCE_TEMPLATE;
  assert.equal(template.length, 628);
  assert.equal(config.commands.oligotm.args[0], template.slice(200, 221));
  assert.equal(config.commands.ntthal.args[1], template.slice(200, 221));
  assert.equal(config.commands.ntthal.args[3], template.slice(200, 221));
  phase('Design primer pairs from the authentic HBB transcript');
  await load(page); await choose(page, 'pairs'); await run(page);
  const pairsBytes = await downloadBytes(page, 'hbb-pairs.out');
  const pairs = checkPairs(pairsBytes, template, 5);
  assert.equal(await page.locator('#preview').textContent(), pairsBytes.toString('utf8'));
  assert.deepEqual(await downloadBytes(page, 'standard output'), pairsBytes);
  passed('real-primer-design-preview-download', { returned: 5, sha256: hash(pairsBytes) });

  phase('Add an internal oligo and enforce target constraints');
  await choose(page, 'internal'); await run(page);
  const internal = checkPairs(await downloadBytes(page, 'hbb-internal.out'), template, 5);
  assert.equal(Number(internal.PRIMER_INTERNAL_NUM_RETURNED), 5);
  for (let i = 0; i < 5; i++) {
    const [start, length] = internal[`PRIMER_INTERNAL_${i}`].split(',').map(Number);
    assert.equal(internal[`PRIMER_INTERNAL_${i}_SEQUENCE`], template.slice(start, start + length));
    assert.ok(start > Number(internal[`PRIMER_LEFT_${i}`].split(',')[0]));
    assert.ok(start + length <= Number(internal[`PRIMER_RIGHT_${i}`].split(',')[0]));
  }
  await choose(page, 'constrained'); await run(page);
  const constrained = checkPairs(await downloadBytes(page, 'hbb-constrained.out'), template, 5);
  for (let i = 0; i < 5; i++) {
    const [left, length] = constrained[`PRIMER_LEFT_${i}`].split(',').map(Number);
    const [right, rightLength] = constrained[`PRIMER_RIGHT_${i}`].split(',').map(Number);
    assert.ok(left >= 40 && right < 580);
    assert.ok(left + length <= 250 && right - rightLength + 1 >= 300);
    assert.ok(left + length <= 100 || left >= 120);
    assert.ok(right < 100 || right - rightLength + 1 >= 120);
  }
  passed('internal-oligo-and-target-constraints');

  phase('Edit Boulder-IO parameters and rerun generated output as input');
  await choose(page, 'pairs');
  await page.locator('#stdin-mode').selectOption('text');
  await page.locator('#stdin-text').fill(originalInput.toString('utf8').replace('PRIMER_NUM_RETURN=5', 'PRIMER_NUM_RETURN=2'));
  await page.locator('#stdout-path').fill('two-pairs.out'); await run(page);
  const two = checkPairs(await downloadBytes(page, 'two-pairs.out'), template, 2);
  assert.equal(two.PRIMER_LEFT_0_SEQUENCE, pairs.PRIMER_LEFT_0_SEQUENCE);
  assert.equal(two.PRIMER_RIGHT_0_SEQUENCE, pairs.PRIMER_RIGHT_0_SEQUENCE);
  assert.equal(two.PRIMER_LEFT_2_SEQUENCE, undefined);
  await edit(page, [], [], [], 'hbb-pairs.out', 'repeated.out'); await run(page);
  const repeated = checkPairs(await downloadBytes(page, 'repeated.out'), template, 5);
  for (let i = 0; i < 5; i++) for (const side of ['LEFT', 'RIGHT']) assert.equal(repeated[`PRIMER_${side}_${i}_SEQUENCE`], pairs[`PRIMER_${side}_${i}_SEQUENCE`]);
  passed('parameter-edit-and-output-chaining');

  phase('Run the original melting-temperature and alignment programs');
  await choose(page, 'oligotm'); await run(page);
  const tm = Number((await downloadBytes(page, 'oligotm.txt')).toString('utf8').trim());
  assert.ok(Number.isFinite(tm) && tm > 40 && tm < 90);
  await choose(page, 'ntthal'); await run(page);
  const alignment = (await downloadBytes(page, 'ntthal.txt')).toString('utf8');
  assert.match(alignment, /dS = -?\d/); assert.match(alignment, /dH = -?\d/); assert.match(alignment, /dG = -?\d/);
  passed('oligonucleotide-thermodynamics', { meltingTemperature: tm });

  phase('Exercise the remaining original Primer3 utilities');
  await choose(page, 'alignment'); await run(page);
  const aligned = (await downloadBytes(page, 'alignment.txt')).toString('utf8');
  assert.match(aligned, /score=14\.00 len=14/);
  assert.ok(aligned.includes(template.slice(200, 221)) && aligned.includes(template.slice(207, 228)));
  await choose(page, 'amplicon'); await run(page);
  const amplicon = boulder(await downloadBytes(page, 'amplicon.txt'));
  assert.equal(Number(amplicon.AMPLICON_PRODUCT_SIZE), 150);
  assert.ok(Number(amplicon.AMPLICON_MELTPOINTS) > 0);
  assert.equal(config.commands.amplicon.args[0], template.slice(200, 350));
  await choose(page, 'long-tm'); await run(page);
  const longTm = (await downloadBytes(page, 'long-tm.txt')).toString('utf8');
  assert.ok(longTm.includes(`s=${template}, start=0, length=628`));
  assert.ok(Number(longTm.match(/tm = ([\d.]+)/)?.[1]) > 0);
  await choose(page, 'masker'); await run(page);
  const masked = (await downloadBytes(page, 'masked.fasta')).toString('utf8');
  const unmasked = await readFile(join(fixtureBase, 'synthetic/masker.fasta'), 'utf8');
  assert.equal(masked.toUpperCase(), unmasked.toUpperCase());
  assert.notEqual(masked, unmasked);
  assert.match(masked.split('\n').slice(1).join(''), /[a-z]/);
  passed('all-auxiliary-programs-and-synthetic-masker');


  phase('Upload exact input bytes and collect a folder without losing its inputs');
  await page.locator('#upload').setInputFiles({ name: 'uploaded.boulder', mimeType: 'text/plain', buffer: originalInput });
  await page.waitForFunction(() => document.getElementById('files').textContent.includes('uploaded.boulder'));
  await choose(page, 'pairs'); await edit(page, [], [], [], 'uploaded.boulder', 'hbb-pairs.out'); await run(page);
  assert.deepEqual(await downloadBytes(page, 'hbb-pairs.out'), pairsBytes);
  const folder = join(reportDir, 'uploaded-sequences'); await mkdir(folder, { recursive: true });
  const binary = Buffer.from([0, 255, 128, 13, 10, 1, 42, 0]);
  await writeFile(join(folder, 'input.boulder'), originalInput); await writeFile(join(folder, 'bytes.bin'), binary);
  await page.locator('#upload-folder').setInputFiles(folder);
  await page.waitForFunction(() => document.getElementById('files').textContent.includes('uploaded-sequences/input.boulder'));
  await edit(page, [], [], ['uploaded-sequences'], 'uploaded-sequences/input.boulder', 'uploaded-sequences/primers.out'); await run(page);
  assert.deepEqual(await downloadBytes(page, 'uploaded-sequences/input.boulder'), originalInput);
  assert.deepEqual(await downloadBytes(page, 'uploaded-sequences/bytes.bin'), binary);
  assert.deepEqual(await downloadBytes(page, 'uploaded-sequences/primers.out'), pairsBytes);
  await page.locator('#result-file').selectOption('/work/uploaded-sequences/bytes.bin');
  assert.match(await page.locator('#preview').textContent(), /^Compressed or binary file /);
  passed('upload-regeneration-and-byte-preserving-folder');

  phase('Explain invalid input and preserve earlier files when commands fail');
  await page.locator('#arguments').fill('[5]');
  const before = (await workerCalls(page)).length; await page.locator('#run').click();
  assert.match(await page.locator('#status').innerText(), /^Check command: Arguments must be a JSON array of strings/);
  assert.equal((await workerCalls(page)).length, before);
  await edit(page, [], [], [], 'missing.boulder'); await page.locator('#run').click();
  assert.match(await page.locator('#status').innerText(), /^Check command: Standard input file is not loaded:/);
  assert.equal((await workerCalls(page)).length, before);
  await edit(page, [], [], [], 'real/hbb-invalid.boulder', 'invalid.out'); await page.locator('#run').click(); await finished(page);
  assert.match(await page.locator('#stdout').innerText(), /PRIMER_ERROR=/);
  assert.deepEqual(await downloadBytes(page, 'hbb-pairs.out'), pairsBytes);
  passed('invalid-form-and-biological-input-errors');
  await edit(page, ['--about'], ['hbb-pairs.out']); await page.locator('#run').click();
  assert.match(await finished(page), /Missing requested files:/);
  assert.deepEqual(await downloadBytes(page, 'hbb-pairs.out'), pairsBytes);
  passed('stale-output-not-reused');
  await edit(page, ['--not-an-option']); await page.locator('#run').click();
  assert.match(await finished(page), /^Exited with status -?[1-9]\d* /);
  assert.deepEqual(await downloadBytes(page, 'hbb-pairs.out'), pairsBytes);
  passed('failed-command-preserves-files');

  phase('Cancel and time out held program loads, then recover');
  await choose(page, 'pairs');
  for (const mode of ['cancel', 'timeout']) {
    const held = host.holdNextRequest('/dist/primer3/primer3_core.wasm');
    try {
      await page.locator('#timeout').fill(mode === 'timeout' ? '1' : '300');
      await page.locator('#run').click(); await deadline(held.requested, 60000, 'The held WebAssembly request did not arrive');
      if (mode === 'cancel') await page.locator('#cancel').click();
      assert.match(await finished(page), mode === 'cancel' ? /^Command cancelled\.$/ : /^TimeoutError:/);
    } finally { held.release(); }
    assert.deepEqual(await downloadBytes(page, 'hbb-pairs.out'), pairsBytes);
  }
  await page.locator('#timeout').fill('300'); await run(page);
  assert.deepEqual(await downloadBytes(page, 'hbb-pairs.out'), pairsBytes);
  passed('cancel-timeout-and-recovery');
  await page.screenshot({ path: join(reportDir, 'primer3.png'), fullPage: true });

  phase('Reload clears files and the original program information remains available');
  summary.workerCalls = await workerCalls(page);
  await page.reload(); await ready(page);
  assert.equal((await page.locator('#files').innerText()).trim(), 'No files loaded.'); assert.equal(await page.locator('#downloads a').count(), 0);
  await edit(page, ['--about']); await run(page);
  assert.match(await page.locator('#stdout').innerText(), /primer3|Primer3/);
  summary.workerCalls.push(...await workerCalls(page));
  passed('reload-clear-and-program-information');
  for (const program of Object.keys(config.programs)) for (const extension of ['mjs', 'wasm']) {
    const path = `dist/primer3/${program}.${extension}`;
    assert.ok(servedAssetRequests(path).length > 0, `Real local asset served with matching bytes: ${path}`);
  }
  assert.ok(summary.workerCalls.length >= 12);
  assert.ok(summary.workerCalls.every(call => Object.keys(config.programs).some(program => new URL(call.moduleUrl).pathname === `/dist/primer3/${program}.mjs`)));
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
  for (const running of [host].filter(Boolean)) {
    running.server.closeAllConnections();
    await deadline(new Promise(resolve => running.server.close(resolve)), 5000, 'Server cleanup exceeded 5 seconds').catch(error => { summary.status = 'failed'; summary.cleanupError = error.message; process.exitCode = 1; });
  }
  await writeFile(join(reportDir, 'summary.json'), `${JSON.stringify(summary, null, 2)}\n`);
  if (summary.cleanupError) process.exit(1);
}
