#!/usr/bin/env node
// Run the shipped HTS programs through the actual UI on ordinary static HTTP.
// Scientific native/WASM comparisons are a separate complementary release gate.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { createHash, randomUUID } from 'node:crypto';
import { mkdir, readFile, readdir, stat, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { extname, join, resolve, sep } from 'node:path';
import { gunzipSync } from 'node:zlib';
import { chromium, firefox, webkit } from 'playwright';

const root = resolve(process.env.HTS_DEMO_ROOT || fileURLToPath(new URL('../', import.meta.url)));
const engine = process.env.BROWSER || process.env.BROWSER_ENGINE || 'chromium';
const browserType = { chromium, firefox, webkit }[engine];
if (!browserType) throw new Error('BROWSER must be chromium, firefox or webkit');
const reportDir = resolve(process.env.HTS_DEMO_REPORT || join(root, 'reports/hts', engine, 'demo'));
await mkdir(reportDir, { recursive: true });
const summary = { schema: 1, runId: randomUUID(), engine, root, versions: [], tools: ['bcftools', 'samtools', 'bgzip', 'tabix', 'htsfile'], status: 'running', startedAt: new Date().toISOString(), phases: [], cases: [], assets: {}, sources: {}, browserErrors: [], browserConsole: [], requests: [], serverRequests: [], failedRequests: [], externalRequests: [], workerCalls: [], downloads: [] };
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const phase = message => { summary.phases.push({ at: new Date().toISOString(), message }); console.log(`[HTS demo] ${message}`); };
const passed = (id, details = {}) => summary.cases.push({ id, status: 'passed', ...details });
const mime = { '.html': 'text/html', '.mjs': 'text/javascript', '.js': 'text/javascript', '.css': 'text/css', '.wasm': 'application/wasm', '.json': 'application/json' };
const records = bytes => bytes.toString('utf8').split(/\r?\n/).filter(line => line && !line.startsWith('#'));
const info = line => Object.fromEntries(line.split('\t')[7].split(';').map(field => field.split('=')));
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
async function edit(page, args, { outputs = [], directories = [], stdout = '', stdinMode = 'none', stdinFile = '', stdinText = '', tool = 'bcftools' } = {}) {
  await page.locator('#tool').selectOption(tool);
  await page.locator('#arguments').fill(JSON.stringify(args));
  await page.locator('#outputs').fill(outputs.join('\n'));
  await page.locator('#output-directories').fill(directories.join('\n'));
  await page.locator('#stdout-path').fill(stdout);
  await page.locator('#stdin-mode').selectOption(stdinMode);
  if (stdinMode === 'file') await page.locator('#stdin-file').fill(stdinFile);
  if (stdinMode === 'text') await page.locator('#stdin-text').fill(stdinText);
}
let lastDownloadClick = 0;
async function downloadBytes(page, name, version, tool = 'bcftools') {
  const delay = 250 - (performance.now() - lastDownloadClick);
  if (delay > 0) await new Promise(resolve => setTimeout(resolve, delay));
  lastDownloadClick = performance.now();
  const record = { version, name, startedAt: new Date().toISOString() }; summary.downloads.push(record);
  const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('link', { name: `Download ${name}`, exact: true }).click()]);
  const expectedName = name === 'standard output' ? `${tool}-${version}-stdout` : name === 'standard error' ? `${tool}-${version}-stderr.txt` : name.split('/').at(-1);
  assert.equal(download.suggestedFilename(), expectedName);
  const path = await download.path(); assert.ok(path);
  const bytes = await readFile(path); record.completedAt = new Date().toISOString(); record.bytes = bytes.length; record.sha256 = hash(bytes);
  return bytes;
}
async function workerCalls(page) { return page.evaluate(() => window.__htsWorkerCalls); }
function assertFillTags(bytes, input) {
  const output = records(bytes); assert.equal(output.length, input.length);
  for (let i = 0; i < input.length; ++i) {
    const fields = input[i].split('\t'), actual = info(output[i]);
    const gtIndex = fields[8].split(':').indexOf('GT');
    const calls = fields.slice(9).map(sample => sample.split(':')[gtIndex].split(/[|/]/).filter(allele => allele !== '.').map(Number));
    const alleles = calls.flat(); const ac = fields[4].split(',').map((_, j) => alleles.filter(allele => allele === j + 1).length);
    assert.equal(Number(actual.AN), alleles.length, `AN at ${fields[1]}`);
    assert.equal(Number(actual.NS), calls.filter(call => call.length).length, `NS at ${fields[1]}`);
    assert.deepEqual(actual.AC.split(',').map(Number), ac, `AC at ${fields[1]}`);
    actual.AF.split(',').map(Number).forEach((value, j) => assert.ok(Math.abs(value - ac[j] / alleles.length) < 1e-6, `AF at ${fields[1]}`));
  }
}

let browser, page, host;
try {
  const config = JSON.parse(await readFile(join(root, 'examples/hts/config.json'), 'utf8'));
  const selected = process.env.HTS_VERSION || 'all';
  summary.versions = selected === 'all' ? config.versions : [selected];
  assert.ok(summary.versions.length && summary.versions.every(version => config.versions.includes(version)), 'Requested versions must be included in this demo package');
  for (const version of summary.versions) await recordTree(`dist/hts-${version}`, summary.assets);
  await recordTree('examples/hts', summary.sources); await recordTree('datasets/hts/common', summary.sources);
  for (const path of ['runtime/index.mjs', 'runtime/hts.mjs', 'runtime/worker.mjs', 'runtime/execute.mjs', 'scripts/test-hts-demo.mjs']) summary.sources[path] = hash(await readFile(join(root, path)));
  const vcf = await readFile(join(root, 'datasets/hts/common/trio-csq.vcf'));
  const inputRecords = records(vcf);
  const sam = await readFile(join(root, 'datasets/hts/common/alignments.sam'));
  const sequence = (await readFile(join(root, 'datasets/hts/common/reference.fa'), 'utf8')).split(/\r?\n/).filter(line => !line.startsWith('>')).join('');
  const genotypeTable = inputRecords.map(line => { const f = line.split('\t'), gt = f[8].split(':').indexOf('GT'); return [f[0], f[1], f[3], f[4], ...f.slice(9).map(sample => sample.split(':')[gt])].join('\t'); }).join('\n') + '\n';
  const consequences = inputRecords.flatMap(line => { const f = line.split('\t'); return info(line).CSQ.split(',').map(csq => { const c = csq.split('|'); return [f[0], f[1], c[1], c[3]].join('\t'); }); }).join('\n') + '\n';
  host = await serve(); browser = await browserType.launch({ headless: true }); summary.browserVersion = browser.version();
  const context = await browser.newContext({ viewport: { width: 1100, height: 900 } }); context.setDefaultTimeout(30000);
  context.on('request', request => { summary.requests.push({ url: request.url(), type: request.resourceType() }); if (/^https?:/.test(request.url()) && new URL(request.url()).origin !== host.origin) summary.externalRequests.push(request.url()); });
  context.on('requestfailed', request => summary.failedRequests.push({ url: request.url(), error: request.failure()?.errorText }));
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (/^https?:$/.test(url.protocol) && url.origin !== host.origin) { summary.externalRequests.push(url.href); await route.abort('blockedbyclient'); }
    else await route.continue();
  });
  // Observe genuine worker dispatch; never substitute outputs or native tools.
  await context.addInitScript(() => {
    window.__htsWorkerCalls = [];
    const OriginalWorker = window.Worker;
    window.Worker = class extends OriginalWorker {
      constructor(url, options) { super(url, options); this.recordedUrl = String(url); }
      postMessage(message, ...rest) {
        if (Array.isArray(message.args)) window.__htsWorkerCalls.push({ workerUrl: this.recordedUrl, moduleUrl: message.moduleUrl, args: [...message.args] });
        return super.postMessage(message, ...rest);
      }
    };
  });
  page = await context.newPage();
  page.on('pageerror', error => summary.browserErrors.push(error.message));
  page.on('console', message => summary.browserConsole.push({ type: message.type(), text: message.text() }));
  await page.goto(`${host.origin}/examples/hts/`); await ready(page); await page.locator('details summary').click();
  assert.deepEqual(await page.locator('#version option').evaluateAll(options => options.map(option => option.value)), config.versions);
  assert.equal(await page.locator('#version').inputValue(), config.versions[0]);
  assert.equal(await page.evaluate(() => crossOriginIsolated), false);
  assert.equal(await page.evaluate(() => typeof SharedArrayBuffer), 'undefined');
  passed('ordinary-static-http-and-configured-versions');

  for (const version of summary.versions) {
    phase(`Run every preset with HTS ${version}`);
    await page.locator('#version').selectOption(version);
    await page.locator('#load-data').click(); await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Example data loaded.'));
    const firstCall = (await workerCalls(page)).length;
    const outputs = new Map();
    for (const [id, base] of Object.entries(config.commands)) {
      if (base.versions && !base.versions.includes(version)) continue;
      const spec = { ...base, ...(base.byVersion?.[version] || {}) };
      await page.locator('#command').selectOption(id);
      assert.deepEqual(JSON.parse(await page.locator('#arguments').inputValue()), spec.args);
      assert.equal(await page.locator('#tool').inputValue(), spec.tool);
      await run(page);
      for (const name of [...(spec.outputs || []), ...(spec.stdoutPath ? [spec.stdoutPath] : [])]) {
        const bytes = await downloadBytes(page, name, version, spec.tool); assert.ok(bytes.length, `${id}: ${name} is nonempty`); outputs.set(name, bytes);
      }
      if (id === 'split-vep') assert.equal(outputs.get('consequences.tsv').toString(), consequences);
      if (id === 'fill-tags') assertFillTags(outputs.get('filled.vcf'), inputRecords);
      if (id === 'trio-stats') {
        const row = records(outputs.get('trio-stats.txt')).find(line => /^FLT\d+\tchild\tfather\tmother\t/.test(line));
        assert.ok(row, 'Trio identity appears in actual plugin results'); const fields = row.split('\t'); assert.ok(Number(fields[4]) > 0 && Number(fields[6]) > 0, 'Trio includes tested sites and Mendelian inconsistencies');
      }
      if (id === 'mendelian') {
        const text = outputs.get('mendelian.txt').toString();
        if (version === '1.16') { const row = records(outputs.get('mendelian.txt')).find(line => /\tmother,father,child$/.test(line)); assert.ok(row); assert.ok(Number(row.split('\t')[1]) > 0); }
        else { assert.match(text, /^TRIO\t1\tchild\tfather\tmother$/m); assert.match(text, /^nmerr\t[1-9]\d*$/m); }
      }
      if (id === 'trio-dnm') { assert.equal(records(outputs.get('dnm.vcf')).length, inputRecords.length); assert.match(outputs.get('dnm.vcf').toString(), /##FORMAT=<ID=DNM,/); }
      if (id === 'trio-switch-rate') { const row = records(outputs.get('switch-rate.txt')).find(line => line.startsWith('TRIO\tfather\tmother\tchild\t')); assert.ok(row); assert.ok(Number(row.split('\t')[4]) > 0); }
      if (id === 'plugins') {
        const plugins = outputs.get('plugins.txt').toString().trim().split(/\s+/);
        for (const name of ['split-vep', 'fill-tags', 'trio-stats', 'trio-dnm2', 'trio-switch-rate', 'parental-origin', version === '1.16' ? 'mendelian' : 'mendelian2']) assert.ok(plugins.includes(name), `Built-in ${name}`);
        const manifest = JSON.parse(await readFile(join(root, `dist/hts-${version}/build-manifest.json`), 'utf8'));
        assert.deepEqual([...plugins].sort(), [...manifest.plugins].sort(), 'UI plugin list matches the actual build inventory');
      }
      if (id === 'query') assert.equal(outputs.get('genotypes.tsv').toString(), genotypeTable);
      if (id === 'bcf') assert.equal(gunzipSync(outputs.get('variants.bcf')).subarray(0, 4).toString('binary'), 'BCF\x02');
      if (id === 'samtools-sort') assert.equal(gunzipSync(outputs.get('sorted.bam')).subarray(0, 4).toString('binary'), 'BAM\x01');
      if (id === 'samtools-index') assert.equal(outputs.get('sorted.bam.bai').subarray(0, 4).toString('binary'), 'BAI\x01');
      if (id === 'samtools-count') assert.equal(outputs.get('alignment-count.txt').toString(), `${samCore(sam).length}\n`);
      if (id === 'samtools-cram') assert.equal(outputs.get('reads.cram').subarray(0, 4).toString(), 'CRAM');
      if (id === 'samtools-faidx') assert.equal(outputs.get('region.fa').toString(), `>chr1:95-135\n${sequence.slice(94, 135)}\n`);
      if (id === 'bgzip') assert.deepEqual(gunzipSync(outputs.get('variants.vcf.gz')), vcf, 'Binary stdout downloads round-trip without corruption');
      if (id === 'tabix-index') assert.equal(gunzipSync(outputs.get('variants.vcf.gz.tbi')).subarray(0, 4).toString('binary'), 'TBI\x01');
      if (id === 'tabix-query') assert.equal(outputs.get('region.vcf.txt').toString(), inputRecords.filter(line => { const position = Number(line.split('\t')[1]); return position >= 90 && position <= 150; }).join('\n') + '\n');
      if (id === 'htsfile') assert.match(outputs.get('format.txt').toString(), /SAM/);
      if (id === 'version') { assert.match(outputs.get('version.txt').toString(), new RegExp(`^bcftools ${version.replace('.', '\\.')}\\b`, 'm')); assert.match(outputs.get('version.txt').toString(), new RegExp(`^Using htslib ${version.replace('.', '\\.')}\\b`, 'm')); }
      passed(`preset-${id}`, { version, tool: spec.tool });
    }

    phase(`Check generated files, uploads and stdin with HTS ${version}`);
    await edit(page, ['query', '-f', '%CHROM\t%POS\t%REF\t%ALT[\t%GT]\n', 'variants.bcf'], { stdout: 'bcf-genotypes.tsv' }); await run(page);
    assert.equal((await downloadBytes(page, 'bcf-genotypes.tsv', version)).toString(), genotypeTable);
    await edit(page, ['view', 'sorted.bam'], { tool: 'samtools', stdout: 'decoded.sam' }); await run(page);
    assert.deepEqual(samCore(await downloadBytes(page, 'decoded.sam', version)), samCore(sam));
    await edit(page, ['view', '-T', 'reference.fa', 'reads.cram'], { tool: 'samtools', stdout: 'decoded-cram.sam' }); await run(page);
    assert.deepEqual(samCore(await downloadBytes(page, 'decoded-cram.sam', version)), samCore(sam));
    await edit(page, ['query', '-f', '%POS\n', 'filled.vcf'], { stdout: 'filled-positions.txt' }); await run(page);
    assert.equal((await downloadBytes(page, 'filled-positions.txt', version)).toString(), inputRecords.map(line => line.split('\t')[1]).join('\n') + '\n');
    passed('generated-bcf-bam-cram-and-plugin-chaining', { version });
    await edit(page, ['+split', 'trio-csq.vcf', '-Ov', '-o', 'split'], { directories: ['split'] }); await run(page);
    for (const [index, sample] of ['child', 'father', 'mother', 'unrelated'].entries()) {
      const bytes = await downloadBytes(page, `split/${sample}.vcf`, version);
      outputs.set(`split/${sample}.vcf`, bytes);
      const header = bytes.toString().split('\n').find(line => line.startsWith('#CHROM\t'));
      assert.equal(header.split('\t').length, 10); assert.equal(header.split('\t')[9], sample);
      const rows = records(bytes); assert.equal(rows.length, inputRecords.length);
      for (let i = 0; i < rows.length; ++i) {
        assert.deepEqual(rows[i].split('\t').slice(0, 5), inputRecords[i].split('\t').slice(0, 5));
        assert.equal(rows[i].split('\t')[9], inputRecords[i].split('\t')[9 + index]);
      }
    }
    passed('split-plugin-output-directory-collection', { version });
    await page.locator('#upload').setInputFiles({ name: 'uploaded.vcf', mimeType: 'text/plain', buffer: vcf });
    await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Files loaded.'));
    await edit(page, ['+split-vep', 'uploaded.vcf', '-d', '-f', '%CHROM\t%POS\t%Consequence\t%SYMBOL\n'], { stdout: 'uploaded-consequences.tsv' }); await run(page);
    assert.equal((await downloadBytes(page, 'uploaded-consequences.tsv', version)).toString(), consequences);
    assert.deepEqual(await downloadBytes(page, 'filled.vcf', version), outputs.get('filled.vcf'), 'Upload preserves prior outputs');
    for (const mode of ['file', 'text']) {
      await edit(page, ['query', '-f', '%CHROM\t%POS\t%REF\t%ALT[\t%GT]\n', '-'], { stdinMode: mode, stdinFile: 'uploaded.vcf', stdinText: vcf.toString(), stdout: `stdin-${mode}.tsv` });
      await run(page); assert.equal((await downloadBytes(page, `stdin-${mode}.tsv`, version)).toString(), genotypeTable);
    }
    passed('authentic-upload-and-file-text-stdin', { version });

    phase(`Check failure recovery and cancellation with HTS ${version}`);
    await edit(page, ['--version']);
    const before = (await workerCalls(page)).length;
    await page.locator('#arguments').fill('[5]'); await page.locator('#run').click();
    assert.match(await page.locator('#status').innerText(), /^Check command:/); assert.equal((await workerCalls(page)).length, before);
    await edit(page, ['view', 'missing.vcf']); await page.locator('#run').click(); assert.match(await finished(page), /^Exited with status -?[1-9]\d* /);
    assert.match(await page.locator('#stderr').innerText(), /missing\.vcf/);
    assert.match((await downloadBytes(page, 'standard error', version)).toString(), /missing\.vcf/);
    await edit(page, ['--version'], { outputs: ['filled.vcf'] }); await page.locator('#run').click(); assert.match(await finished(page), /Missing requested files:/);
    assert.deepEqual(await downloadBytes(page, 'filled.vcf', version), outputs.get('filled.vcf'), 'Stale output is retained but cannot satisfy a new command');
    await edit(page, ['--version'], { directories: ['split'] }); await page.locator('#run').click(); assert.match(await finished(page), /Missing requested files: \/work\/split/);
    assert.deepEqual(await downloadBytes(page, 'split/child.vcf', version), outputs.get('split/child.vcf'), 'Stale directory output is retained but cannot satisfy a new command');
    passed('invalid-form-failed-command-and-stale-file-directory-outputs', { version });
    await edit(page, ['--version']);
    for (const mode of ['cancel', 'timeout']) {
      const held = host.holdNextRequest(`/dist/hts-${version}/bcftools.mjs`);
      try {
        await page.locator('#timeout').fill(mode === 'timeout' ? '1' : '300');
        await page.locator('#run').click(); await deadline(held.requested, 30000, 'Held program request did not arrive');
        if (mode === 'cancel') await page.locator('#cancel').click();
        assert.match(await finished(page), mode === 'cancel' ? /^Command cancelled\.$/ : /^TimeoutError:/);
      } finally { held.release(); }
      assert.deepEqual(await downloadBytes(page, 'filled.vcf', version), outputs.get('filled.vcf'));
    }
    await page.locator('#timeout').fill('300'); await run(page);
    assert.match((await downloadBytes(page, 'standard output', version)).toString(), new RegExp(`^bcftools ${version.replace('.', '\\.')}\\b`, 'm'));
    passed('cancel-timeout-and-recovery', { version });
    const calls = (await workerCalls(page)).slice(firstCall);
    assert.ok(calls.length >= Object.keys(config.commands).length + 10);
    assert.ok(calls.every(call => new URL(call.moduleUrl).pathname.startsWith(`/dist/hts-${version}/`)), 'Selected version is used for every dispatch');
    for (const tool of summary.tools) assert.ok(calls.some(call => new URL(call.moduleUrl).pathname.endsWith(`/${tool}.mjs`)), `Dispatched ${tool}`);
    passed('version-isolation-and-all-five-programs', { version, workerCalls: calls.length });
    await page.screenshot({ path: join(reportDir, `hts-${version}.png`), fullPage: true });
  }
  if (summary.versions.length > 1) {
    phase('Reuse generated files across separately loaded versions');
    const version = summary.versions[0]; await page.locator('#version').selectOption(version);
    await edit(page, ['query', '-f', '%CHROM\t%POS\t%REF\t%ALT[\t%GT]\n', 'variants.bcf'], { stdout: 'cross-version.tsv' }); await run(page);
    assert.equal((await downloadBytes(page, 'cross-version.tsv', version)).toString(), genotypeTable);
    passed('cross-version-bcf-reuse');
  }
  summary.workerCalls = await workerCalls(page);
  await page.reload(); await ready(page);
  assert.equal((await page.locator('#files').innerText()).trim(), 'No files loaded.'); assert.equal(await page.locator('#downloads a').count(), 0);
  assert.equal(await page.evaluate(async () => (await navigator.serviceWorker.getRegistrations()).length), 0);
  passed('reload-clears-local-files');
  for (const version of summary.versions) for (const tool of summary.tools) for (const extension of ['mjs', 'wasm']) {
    const path = `dist/hts-${version}/${tool}.${extension}`;
    assert.ok(summary.serverRequests.some(request => request.pathname === `/${path}` && request.completed && request.status === 200 && request.bytes > 0 && request.sha256 === summary.assets[path]), `Actual local asset served with matching bytes: ${path}`);
  }
  assert.ok(summary.workerCalls.every(call => summary.versions.some(version => new URL(call.moduleUrl).pathname.startsWith(`/dist/hts-${version}/`))));
  assert.deepEqual(summary.externalRequests, []); assert.deepEqual(summary.browserErrors, []);
  passed('all-runtime-assets-local');
  for (const [path, digest] of Object.entries({ ...summary.assets, ...summary.sources })) assert.equal(hash(await readFile(join(root, path))), digest, `Inputs unchanged: ${path}`);
  summary.status = 'passed'; phase(`Passed ${summary.cases.length} UI checks`);
} catch (error) {
  summary.status = 'failed'; summary.error = error.stack; process.exitCode = 1;
  if (page) {
    summary.workerCalls = await deadline(workerCalls(page), 3000, 'Worker diagnostics timed out').catch(() => summary.workerCalls);
    await deadline(page.screenshot({ path: join(reportDir, 'failure.png'), fullPage: true }), 5000, 'Failure screenshot timed out').catch(() => {});
    for (const id of ['status', 'version', 'tool', 'arguments', 'outputs', 'stdout', 'stderr']) {
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
