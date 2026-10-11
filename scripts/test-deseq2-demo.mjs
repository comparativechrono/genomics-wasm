// Browser UI smoke test using genuine R, DESeq2 and the real airway count table.
// Native numerical equivalence is checked by the separate DESeq2 validator.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdir, readFile, stat, writeFile } from 'node:fs/promises';
import { extname, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium, firefox, webkit } from 'playwright';

const root = fileURLToPath(new URL('../', import.meta.url));
const browserName = process.env.BROWSER || 'chromium';
const browserType = { chromium, firefox, webkit }[browserName];
if (!browserType) throw new Error('BROWSER must be chromium, firefox or webkit');
const diagnostics = process.env.DESEQ2_DIAGNOSTICS === '1';
const reportDir = resolve(root, process.env.DESEQ2_REPORT_DIR || `reports/deseq2/${browserName}/demo`);
await mkdir(reportDir, { recursive: true });
const summary = { browser: browserName, diagnostics, startedAt: new Date().toISOString(), status: 'running', phases: [], console: [], pageErrors: [] };
function phase(name) {
  summary.phase = name;
  summary.phases.push({ at: new Date().toISOString(), name });
  console.log(`[DESeq2 UI ${browserName}] ${name}`);
}

function diagnosticScript(script) {
  if (!diagnostics) return script;
  // A calling handler prints the live R stack before unwinding. It does not
  // catch, retry or replace the original error, and the analysis is unchanged.
  return `withCallingHandlers({\n${script}\n}, error = function(e) {
  cat("\\nDESeq2 diagnostic error: ", conditionMessage(e), "\\n", sep = "", file = stderr())
  calls <- sys.calls()
  cat("R call stack:\\n", file = stderr())
  for (i in seq_along(calls)) {
    cat(i, ": ", paste(deparse(calls[[i]], width.cutoff = 500L), collapse = " "), "\\n",
        sep = "", file = stderr())
  }
})\n`;
}
await stat(resolve(root, 'dist/deseq2/manifest.json')).catch(() => {
  throw new Error('Prepare dist/deseq2 before testing the browser example');
});

const mime = { '.html': 'text/html', '.mjs': 'text/javascript', '.js': 'text/javascript', '.css': 'text/css', '.wasm': 'application/wasm', '.json': 'application/json' };
const server = createServer(async (request, response) => {
  try {
    const path = resolve(root, `.${decodeURIComponent(new URL(request.url, 'http://localhost').pathname)}`);
    if (!path.startsWith(root.endsWith(sep) ? root : `${root}${sep}`)) {
      response.writeHead(403).end();
      return;
    }
    const filename = (await stat(path)).isDirectory() ? resolve(path, 'index.html') : path;
    const data = await readFile(filename);
    response.writeHead(200, { 'Content-Type': mime[extname(filename)] || 'application/octet-stream', 'Cache-Control': 'no-store' }).end(data);
  } catch {
    response.writeHead(404).end('Not found');
  }
});
await new Promise((resolve, reject) => {
  server.once('error', reject);
  server.listen(0, '127.0.0.1', resolve);
});

let browser, page;
try {
  phase('Launching browser');
  browser = await browserType.launch({ headless: true });
  summary.browserVersion = browser.version();
  console.log(`[DESeq2 UI ${browserName}] Browser version ${summary.browserVersion}`);
  page = await browser.newPage({ viewport: { width: 1100, height: 900 } });
  const pageErrors = summary.pageErrors;
  page.on('pageerror', error => pageErrors.push(error.message));
  page.on('console', message => {
    summary.console.push({ at: new Date().toISOString(), type: message.type(), text: message.text() });
    if (diagnostics) console.log(`[Browser ${message.type()}] ${message.text()}`);
  });
  phase('Loading the real airway tables');
  await page.goto(`http://127.0.0.1:${server.address().port}/examples/deseq2/`);
  await page.locator('#load-airway').click();
  await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Airway data loaded.'));

  // Use the same deterministic real-data subset as the numerical validator.
  // The full 63,677-gene matrix remains the browser example's normal default.
  const fullScript = await page.locator('#script').inputValue();
  const selectedScript = fullScript.replace('samples <- read.delim', `
selection <- c(head(which(rowSums(counts) >= 10), 2000),
               head(which(rowSums(counts) == 0), 100))
counts <- counts[selection, , drop = FALSE]
samples <- read.delim`);
  assert.notEqual(selectedScript, fullScript, 'Smoke test inserted the documented subset');
  const analysisScript = diagnosticScript(selectedScript);
  await page.locator('#script').fill(analysisScript);
  await runAnalysis(page, 'Airway DESeq2 analysis');
  phase('Checking analysis downloads');
  assert.match(await page.locator('#stdout').innerText(), /DESeq2/);
  const results = await downloadBytes(page, 'Download results.tsv');
  const rows = results.bytes.toString('utf8').trimEnd().split('\n');
  assert.equal(results.name, 'results.tsv');
  assert.equal(rows.length, 2001, 'The example prefilter kept all 2,000 selected expressed genes');
  assert.equal(rows[0], 'gene_id\tbaseMean\tlog2FoldChange\tlfcSE\tstat\tpvalue\tpadj');
  assert.ok(rows.slice(1).some(row => Number.isFinite(Number(row.split('\t')[6]))), 'DESeq2 returned adjusted p-values');
  const normalised = await downloadBytes(page, 'Download normalised-counts.tsv');
  assert.equal(normalised.bytes.toString('utf8').trimEnd().split('\n').length, 2001);
  const pdf = await downloadBytes(page, 'Download diagnostics.pdf');
  assert.ok(pdf.bytes.length > 1000, 'R produced a nonempty PDF');
  assert.equal(pdf.bytes.subarray(0, 5).toString('ascii'), '%PDF-');
  assert.equal((await downloadBytes(page, 'Download analysis.R')).bytes.toString('utf8'), analysisScript);

  // Uploads can use different local names; the R-facing names stay consistent.
  phase('Uploading count and sample tables');
  for (const kind of ['counts', 'samples']) {
    await page.locator(`#${kind}`).setInputFiles({
      name: `my-${kind}.txt`, mimeType: 'text/plain',
      buffer: await readFile(resolve(root, `datasets/deseq2-airway/${kind}.tsv`)),
    });
    await page.waitForFunction(kind => document.getElementById(`${kind}-name`).textContent.startsWith(`my-${kind}.txt`), kind);
  }
  await page.locator('#script').fill(`
counts <- read.delim("counts.tsv", row.names = 1, check.names = FALSE)
samples <- read.delim("samples.tsv", row.names = 1, check.names = FALSE)
stopifnot(nrow(counts) == 63677, ncol(counts) == 8,
          identical(colnames(counts), rownames(samples)))
writeLines("Uploaded tables read by R", "upload-check.txt")
`);
  await runAnalysis(page, 'Reading uploaded tables');
  assert.equal((await downloadBytes(page, 'Download upload-check.txt')).bytes.toString('utf8'), 'Uploaded tables read by R\n');
  assert.equal(await page.getByRole('link', { name: 'Download diagnostics.pdf', exact: true }).count(), 0, 'Earlier outputs were cleared');

  phase('Checking cancellation');
  await page.locator('#script').fill('Sys.sleep(60)');
  await page.locator('#run').click();
  await page.waitForFunction(() => document.getElementById('status').textContent === 'Running the R analysis…');
  await page.locator('#cancel').click();
  await page.waitForFunction(() => !document.getElementById('run').disabled);
  assert.match(await page.locator('#status').innerText(), /^Analysis cancelled\./);
  await page.locator('#script').fill('cat("R session restarted\\n")');
  await runAnalysis(page, 'Restarting R after cancellation');
  assert.match(await page.locator('#stdout').innerText(), /R session restarted/);
  assert.deepEqual(pageErrors, [], 'The browser reported no uncaught errors');
  summary.status = 'passed';
  phase('All UI checks passed');
  console.log(`DESeq2 browser example passed in ${browserName}: real airway analysis, TSV and R PDF downloads, uploads, cancellation and restart.`);
} catch (error) {
  summary.status = 'failed';
  summary.error = error.stack || error.message;
  throw error;
} finally {
  summary.finishedAt = new Date().toISOString();
  if (page && !page.isClosed()) {
    for (const [id, filename] of [['stdout', 'stdout.txt'], ['stderr', 'stderr.txt'], ['status', 'status.txt']]) {
      try {
        await writeFile(resolve(reportDir, filename), await page.locator(`#${id}`).innerText({ timeout: 2000 }));
      } catch (error) { summary.captureError = error.message; }
    }
    try { await writeFile(resolve(reportDir, 'analysis.R'), await page.locator('#script').inputValue({ timeout: 2000 })); }
    catch (error) { summary.captureError = error.message; }
  }
  await writeFile(resolve(reportDir, 'summary.json'), JSON.stringify(summary, null, 2) + '\n');
  await browser?.close();
  await new Promise(resolve => server.close(resolve));
}

async function runAnalysis(page, label) {
  phase(label);
  await page.locator('#run').click();
  // Firefox exceeded five minutes in CI; keep a bounded ten-minute allowance.
  await page.waitForFunction(() => /^(Analysis complete|Error:)/.test(document.getElementById('status').textContent), null, { timeout: 600000 });
  const status = await page.locator('#status').innerText();
  assert.match(status, /^Analysis complete/, `${status}\n${await page.locator('#stderr').innerText()}`);
  await page.waitForFunction(() => !document.getElementById('run').disabled);
}

async function downloadBytes(page, label) {
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('link', { name: label, exact: true }).click(),
  ]);
  const path = await download.path();
  assert.ok(path, 'Download produced a local file');
  return { name: download.suggestedFilename(), bytes: await readFile(path) };
}
