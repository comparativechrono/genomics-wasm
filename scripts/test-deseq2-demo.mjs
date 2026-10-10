// Browser UI smoke test using genuine R, DESeq2 and the real airway count table.
// Native numerical equivalence is checked by the separate DESeq2 validator.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { extname, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium, firefox, webkit } from 'playwright';

const root = fileURLToPath(new URL('../', import.meta.url));
const browserName = process.env.BROWSER || 'chromium';
const browserType = { chromium, firefox, webkit }[browserName];
if (!browserType) throw new Error('BROWSER must be chromium, firefox or webkit');
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

let browser;
try {
  browser = await browserType.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1100, height: 900 } });
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
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
  await page.locator('#script').fill(selectedScript);
  await runAnalysis(page);
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
  assert.equal((await downloadBytes(page, 'Download analysis.R')).bytes.toString('utf8'), selectedScript);

  // Uploads can use different local names; the R-facing names stay consistent.
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
  await runAnalysis(page);
  assert.equal((await downloadBytes(page, 'Download upload-check.txt')).bytes.toString('utf8'), 'Uploaded tables read by R\n');
  assert.equal(await page.getByRole('link', { name: 'Download diagnostics.pdf', exact: true }).count(), 0, 'Earlier outputs were cleared');

  await page.locator('#script').fill('Sys.sleep(60)');
  await page.locator('#run').click();
  await page.waitForFunction(() => document.getElementById('status').textContent === 'Running the R analysis…');
  await page.locator('#cancel').click();
  await page.waitForFunction(() => !document.getElementById('run').disabled);
  assert.match(await page.locator('#status').innerText(), /^Analysis cancelled\./);
  await page.locator('#script').fill('cat("R session restarted\\n")');
  await runAnalysis(page);
  assert.match(await page.locator('#stdout').innerText(), /R session restarted/);
  assert.deepEqual(pageErrors, [], 'The browser reported no uncaught errors');
  console.log(`DESeq2 browser example passed in ${browserName}: real airway analysis, TSV and R PDF downloads, uploads, cancellation and restart.`);
} finally {
  await browser?.close();
  await new Promise(resolve => server.close(resolve));
}

async function runAnalysis(page) {
  await page.locator('#run').click();
  await page.waitForFunction(() => /^(Analysis complete|Error:)/.test(document.getElementById('status').textContent), null, { timeout: 300000 });
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
