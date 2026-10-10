// Integration smoke test for the browser UI using the actual compiled tools.
// This does not replace the separate native-output equivalence suite.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve, extname, sep } from 'node:path';
import { chromium, firefox, webkit } from 'playwright';

const root = fileURLToPath(new URL('../', import.meta.url));
const browserName = process.env.BROWSER || 'chromium';
const browserType = { chromium, firefox, webkit }[browserName];
if (!browserType) throw new Error('BROWSER must be chromium, firefox or webkit');
for (const tool of ['bwa', 'freebayes']) {
  await stat(resolve(root, `dist/${tool}/${tool}.mjs`)).catch(() => { throw new Error(`Build ${tool} before running the browser demo test`); });
}

const mime = { '.html': 'text/html', '.mjs': 'text/javascript', '.js': 'text/javascript', '.css': 'text/css', '.wasm': 'application/wasm' };
const server = createServer(async (request, response) => {
  try {
    const path = resolve(root, `.${decodeURIComponent(new URL(request.url, 'http://localhost').pathname)}`);
    if (!path.startsWith(root.endsWith(sep) ? root : `${root}${sep}`)) { response.writeHead(403).end(); return; }
    const filename = (await stat(path)).isDirectory() ? resolve(path, 'index.html') : path;
    const data = await readFile(filename);
    response.writeHead(200, { 'Content-Type': mime[extname(filename)] || 'application/octet-stream', 'Cache-Control': 'no-store' }).end(data);
  } catch {
    response.writeHead(404).end('Not found');
  }
});
await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });

let browser;
try {
  browser = await browserType.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1100, height: 900 } });
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/examples/browser/`);
  const run = async (expectedExit = 0) => {
    await page.locator('#run').click();
    await page.waitForFunction(() => /^Exited with status|Error:|RuntimeError:|TimeoutError:/.test(document.getElementById('status').textContent), null, { timeout: 120000 });
    const status = await page.locator('#status').innerText();
    assert.match(status, new RegExp(`^Exited with status ${expectedExit} `), status);
    await page.locator('#run').waitFor({ state: 'visible' });
  };
  const upload = async files => {
    await page.locator('#upload').setInputFiles(await Promise.all(files.map(async ([source, name]) => ({
      name, mimeType: 'application/octet-stream', buffer: await readFile(resolve(root, source)),
    }))));
    await page.waitForFunction(() => document.getElementById('status').textContent === 'Files loaded.');
  };

  await run(1); // BWA prints usage and returns one when no command is supplied.
  assert.match(await page.locator('#stderr').innerText(), /bwa|Usage/i);
  await upload([
    ['datasets/human/reference.fa', 'reference.fa'],
    ['datasets/human/reads_1.fastq', 'reads_1.fq'],
    ['datasets/human/reads_2.fastq', 'reads_2.fq'],
  ]);
  await page.locator('#example').selectOption('1');
  await run();
  assert.match(await page.locator('#files').innerText(), /reference\.fa\.bwt/);
  assert.equal(await page.locator('#files li').count(), 8);

  await page.locator('#example').selectOption('2');
  await run();
  assert.match(await page.locator('#stdout').innerText(), /@SQ\tSN:/);
  const firstSam = await downloadStdout(page);
  assert.match(firstSam.toString('utf8'), /^@HD|^@SQ/m);
  assert.ok(firstSam.length > 1000, 'BWA produced a nonempty alignment');
  await run();
  assert.deepEqual(await downloadStdout(page), firstSam, 'Repeated runs return identical SAM bytes');

  await page.locator('#tool').selectOption('freebayes');
  await run();
  assert.match(`${await page.locator('#stdout').innerText()}${await page.locator('#stderr').innerText()}`, /freebayes|usage/i);
  await upload([
    ['datasets/human/reference.fa.fai', 'reference.fa.fai'],
    ['datasets/human/input.bam', 'alignment.bam'],
    ['datasets/human/input.bam.bai', 'alignment.bam.bai'],
  ]);
  await page.locator('#example').selectOption('1');
  await run();
  assert.match((await downloadStdout(page)).toString('utf8'), /^##fileformat=VCF/m);
  assert.deepEqual(pageErrors, [], 'The browser reported no uncaught errors');
  console.log(`Browser demo passed with real BWA and FreeBayes in ${browserName}.`);
} finally {
  await browser?.close();
  await new Promise(resolve => server.close(resolve));
}

async function downloadStdout(page) {
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('link', { name: 'Download standard output', exact: true }).click(),
  ]);
  const path = await download.path();
  assert.ok(path, 'Download produced a local file');
  return readFile(path);
}
