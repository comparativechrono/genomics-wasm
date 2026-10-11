#!/usr/bin/env node
/** Run the same R script in native R and the bundled webR worker. */
import { readFile, writeFile, mkdir, rm, cp } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { execFileSync, spawnSync } from 'node:child_process';
import { createServer } from 'node:http';
import { dirname, resolve, join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const options = { engine: 'node', rscript: process.env.DESEQ2_RSCRIPT || 'Rscript', 'native-baseline': '', 'report-dir': '' };
for (let i = 2; i < process.argv.length; i += 2) {
  const key = process.argv[i].replace(/^--/, '');
  if (!(key in options) || !process.argv[i + 1]) throw new Error('Usage: validate-deseq2.mjs [--engine node|chromium|firefox|webkit] [--rscript PATH] [--native-baseline DIR] [--report-dir DIR]');
  options[key] = process.argv[i + 1];
}
if (!['node', 'chromium', 'firefox', 'webkit'].includes(options.engine)) throw new Error('Unknown engine');
if (options.rscript.includes('/')) options.rscript = resolve(options.rscript);
const reportDir = resolve(root, options['report-dir'] || `reports/deseq2/${options.engine}`);
await mkdir(reportDir, { recursive: true });
const hash = value => createHash('sha256').update(value).digest('hex');
const summary = { schema: 1, engine: options.engine, startedAt: new Date().toISOString(), status: 'running', cases: [] };
let browser, server, session;

async function startBrowser() {
  const playwright = await import('playwright');
  browser = await playwright[options.engine].launch({ headless: true });
  summary.browserVersion = browser.version();
  server = createServer(async (request, response) => {
    try {
      const url = new URL(request.url, 'http://localhost');
      if (url.pathname === '/') {
        response.setHeader('Content-Type', 'text/html');
        response.end('<!doctype html><meta charset="utf-8"><title>DESeq2 validation</title>');
        return;
      }
      const path = resolve(root, '.' + decodeURIComponent(url.pathname));
      if (!path.startsWith(root + sep) || !['runtime/', 'dist/'].some(prefix => relative(root, path).startsWith(prefix))) {
        response.writeHead(404).end(); return;
      }
      const mime = path.endsWith('.wasm') ? 'application/wasm' : /\.(mjs|js)$/.test(path) ? 'text/javascript' : 'application/octet-stream';
      response.setHeader('Content-Type', mime);
      response.end(await readFile(path));
    } catch { response.writeHead(404).end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const page = await browser.newPage();
  page.setDefaultTimeout(300000);
  summary.browserConsole = [];
  summary.browserErrors = [];
  page.on('console', message => {
    const text = message.text();
    summary.browserConsole.push({ at: new Date().toISOString(), type: message.type(), text });
    if (text.startsWith('[DESeq2 phase] ')) summary.browserPhase = text.slice('[DESeq2 phase] '.length);
    console.log(`[${options.engine} ${message.type()}] ${text}`);
  });
  page.on('pageerror', error => {
    summary.browserErrors.push(error.stack || error.message);
    console.error(`[${options.engine} pageerror] ${error.stack || error.message}`);
  });
  summary.externalRequests = [];
  await page.route('**/*', route => {
    if (new URL(route.request().url()).origin === origin) return route.continue();
    summary.externalRequests.push(route.request().url());
    return route.abort();
  });
  await page.goto(origin);
  return page;
}

async function storeResult(result, target) {
  await rm(target, { recursive: true, force: true });
  await mkdir(target, { recursive: true });
  await writeFile(join(reportDir, 'wasm.stdout.txt'), result.stdout || '');
  await writeFile(join(reportDir, 'wasm.stderr.txt'), result.stderr || '');
  for (const [path, bytes] of Object.entries(result.files || {})) {
    if (!path.startsWith('output/')) continue;
    const name = path.slice('output/'.length);
    if (!name || name.includes('/') || name.includes('\\') || name === '..') throw new Error('Unexpected output path');
    await writeFile(join(target, name), new Uint8Array(bytes));
  }
}

try {
  execFileSync('python3', [join(root, 'scripts/fetch-deseq2-data.py'), '--verify'], { cwd: root, stdio: 'pipe' });
  const manifestBytes = await readFile(join(root, 'dist/deseq2/manifest.json'));
  const manifest = JSON.parse(manifestBytes);
  const script = await readFile(join(root, 'validation/deseq2/analysis.R'), 'utf8');
  const packages = [...manifest.packages].sort((a, b) => a.name.localeCompare(b.name));
  const versions = 'package\tversion\n' + packages.map(pkg => `${pkg.name}\t${pkg.version}\n`).join('');
  const files = {
    'input/counts.tsv': await readFile(join(root, 'datasets/deseq2-airway/counts.tsv')),
    'input/samples.tsv': await readFile(join(root, 'datasets/deseq2-airway/samples.tsv')),
    'input/expected-packages.tsv': Buffer.from(versions),
  };
  const provenance = {
    rVersion: manifest.rVersion, deseq2Version: manifest.version, webrVersion: manifest.webrVersion,
    manifestSha256: hash(manifestBytes), scriptSha256: hash(script),
    inputSha256: Object.fromEntries(Object.entries(files).map(([name, data]) => [name, hash(data)])),
  };
  summary.provenance = provenance;
  const nativeDir = join(reportDir, 'native');
  if (options['native-baseline']) {
    const baseline = resolve(options['native-baseline']);
    const existing = JSON.parse(await readFile(join(dirname(baseline), 'native-provenance.json'), 'utf8'));
    if (JSON.stringify(existing) !== JSON.stringify(provenance)) throw new Error('Native baseline inputs, script, or package bundle differ');
    if (baseline !== nativeDir) {
      await rm(nativeDir, { recursive: true, force: true });
      await cp(baseline, nativeDir, { recursive: true });
    }
    summary.nativeBaseline = baseline;
  } else {
    const work = join(root, 'build/validation/deseq2', options.engine, 'native');
    await rm(work, { recursive: true, force: true });
    await mkdir(join(work, 'input'), { recursive: true });
    for (const [name, data] of Object.entries(files)) await writeFile(join(work, name), data);
    await writeFile(join(work, 'analysis.R'), script);
    console.log('Running native R analysis');
    const native = spawnSync(options.rscript, ['--vanilla', 'analysis.R'], {
      cwd: work, encoding: 'utf8', timeout: 300000, maxBuffer: 32 * 1024 * 1024,
      env: { ...process.env, OMP_NUM_THREADS: '1', OPENBLAS_NUM_THREADS: '1', MKL_NUM_THREADS: '1' },
    });
    await writeFile(join(reportDir, 'native.stdout.txt'), native.stdout || '');
    await writeFile(join(reportDir, 'native.stderr.txt'), native.stderr || '');
    await rm(nativeDir, { recursive: true, force: true });
    await cp(join(work, 'output'), nativeDir, { recursive: true }).catch(() => {});
    if (native.error) throw native.error;
    if (native.status !== 0) throw new Error(`Native R failed (${native.status}): ${native.stderr}`);
  }
  await writeFile(join(reportDir, 'native-provenance.json'), JSON.stringify(provenance, null, 2) + '\n');

  let result;
  console.log(`Running genuine DESeq2 with webR (${options.engine})`);
  if (options.engine === 'node') {
    const { createDESeq2 } = await import('../runtime/deseq2.mjs');
    session = await createDESeq2({ onProgress: message => console.log(message) });
    try { result = { ok: true, ...await session.run(script, { files }) }; }
    catch (error) { result = { ok: false, message: error.stack, stdout: error.stdout, stderr: error.stderr, files: error.files }; }
    if (result.ok) {
      const repeated = await session.run('stopifnot(exists("dds")); cat("session retained")');
      if (!repeated.stdout.includes('session retained')) throw new Error('R session lost analysis objects');
      let rejected = false;
      try { await session.run('stop("intentional validation error")'); }
      catch (error) { rejected = error.message.includes('intentional validation error'); }
      if (!rejected) throw new Error('R errors were not reported to JavaScript');
      await session.run('stopifnot(exists("dds"))');
    }
  } else {
    const page = await startBrowser();
    let deadline;
    const evaluation = page.evaluate(async ({ script, encoded }) => {
      const phase = name => console.log(`[DESeq2 phase] ${name}`);
      // Avoid sending millions of individual array elements through the browser
      // automation protocol. Chunking also avoids the JavaScript argument limit.
      function encodeFiles(files) {
        return Object.fromEntries(Object.entries(files || {}).filter(([name]) => name.startsWith('output/'))
          .map(([name, bytes]) => {
            const chunks = [];
            for (let offset = 0; offset < bytes.length; offset += 16384) {
              chunks.push(String.fromCharCode(...bytes.subarray(offset, offset + 16384)));
            }
            return [name, btoa(chunks.join(''))];
          }));
      }
      phase('Loading R and packages');
      const { createDESeq2 } = await import('/runtime/deseq2.mjs');
      const session = await createDESeq2({ onProgress: message => console.log(`[DESeq2 loading] ${message}`) });
      const files = Object.fromEntries(Object.entries(encoded).map(([name, value]) =>
        [name, Uint8Array.from(atob(value), character => character.charCodeAt(0))]));
      try {
        phase('Running the R analysis');
        const result = await session.run(script, { files });
        phase('Checking persistent R objects');
        const repeated = await session.run('stopifnot(exists("dds")); cat("session retained")');
        if (!repeated.stdout.includes('session retained')) throw new Error('R session lost analysis objects');
        phase('Checking R error recovery');
        let rejected = false;
        try { await session.run('stop("intentional validation error")'); }
        catch (error) { rejected = error.message.includes('intentional validation error'); }
        if (!rejected) throw new Error('R errors were not reported to JavaScript');
        await session.run('stopifnot(exists("dds"))');
        phase('Encoding output files');
        const encodedFiles = encodeFiles(result.files);
        phase('Transferring output files');
        return { ok: true, ...result, files: encodedFiles };
      } catch (error) {
        phase('Transferring R failure details');
        return { ok: false, message: error.stack, stdout: error.stdout, stderr: error.stderr,
          files: encodeFiles(error.files) };
      } finally { session.close(); }
    }, { script, encoded: Object.fromEntries(Object.entries(files).map(([name, value]) => [name, value.toString('base64')])) });
    // Allow five minutes for startup and five for analysis/transfer. Unlike
    // locator operations, Playwright page.evaluate has no default deadline.
    summary.browserDeadlineMs = 600000;
    try {
      result = await Promise.race([evaluation, new Promise((_, reject) => {
        deadline = setTimeout(() => reject(new Error(`Browser validation exceeded 10 minutes; last phase: ${summary.browserPhase || 'starting browser evaluation'}`)), summary.browserDeadlineMs);
      })]);
    } finally { clearTimeout(deadline); }
    result.files = Object.fromEntries(Object.entries(result.files || {}).map(([name, encoded]) => {
      if (typeof encoded !== 'string') {
        throw new Error(`Invalid base64 output from browser: ${name}`);
      }
      const bytes = Buffer.from(encoded, 'base64');
      if (bytes.toString('base64') !== encoded) throw new Error(`Noncanonical base64 output from browser: ${name}`);
      return [name, bytes];
    }));
    summary.browserPhase = 'Output files received';
    console.log(`[${options.engine}] Output files received`);
    if (summary.browserErrors.length) throw new Error(`The browser reported ${summary.browserErrors.length} uncaught error(s); see summary.json`);
    if (summary.externalRequests.length) throw new Error('The browser attempted to download an external dependency');
  }
  await storeResult(result, join(reportDir, 'wasm'));
  if (!result.ok) throw new Error(result.message || result.stderr || 'webR analysis failed');
  summary.cases.push({ id: 'ordinary-r-script', status: 'passed' }, { id: 'persistent-session', status: 'passed' }, { id: 'r-error-recovery', status: 'passed' });
  const comparator = join(root, 'validation/deseq2/compare.py');
  const strict = spawnSync('python3', [comparator, nativeDir, join(reportDir, 'wasm'), '--report', join(reportDir, 'comparison-strict.json')], { encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 });
  if (strict.error || ![0, 1].includes(strict.status)) throw new Error(`Strict comparison could not parse the results: ${strict.stderr || strict.stdout || strict.error}`);
  const compared = spawnSync('python3', [comparator, nativeDir, join(reportDir, 'wasm'), '--policy', join(root, 'validation/deseq2/tolerances.json'), '--report', join(reportDir, 'comparison.json')], { encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 });
  if (compared.error || compared.status !== 0) throw new Error(`Native and webR results differ; see ${relative(root, join(reportDir, 'comparison.json'))}`);
  const comparison = JSON.parse(compared.stdout);
  for (const [name, table] of Object.entries(comparison.tables)) summary.cases.push({ id: name, status: 'passed', rows: table.rows });
  summary.strictlyEqual = strict.status === 0;
  summary.status = 'passed';
  console.log(`PASS ${options.engine}: ${summary.cases.length} DESeq2 checks`);
} catch (error) {
  summary.status = 'failed';
  summary.error = error.stack;
  console.error(error.stack);
  process.exitCode = 1;
} finally {
  session?.close();
  if (browser) await browser.close();
  if (server) await new Promise(resolve => server.close(resolve));
  summary.finishedAt = new Date().toISOString();
  await writeFile(join(reportDir, 'summary.json'), JSON.stringify(summary, null, 2) + '\n');
}
