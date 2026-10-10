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
for (const tool of ['bwa', 'bwa-mem2', 'freebayes', 'adapterremoval', 'seqtk', 'hisat2', 'minimap2']) {
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

  for (const tool of ['bwa', 'bwa-mem2']) {
    await page.locator('#tool').selectOption(tool);
    await page.locator('#clear').click();
    await run(1); // Both aligners print usage and return one without a command.
    assert.match(await page.locator('#stderr').innerText(), /bwa|Usage/i);
    await upload([
      ['datasets/human/reference.fa', 'reference.fa'],
      ['datasets/human/reads_1.fastq', 'reads_1.fq'],
      ['datasets/human/reads_2.fastq', 'reads_2.fq'],
    ]);
    await page.locator('#example').selectOption('1');
    await run();
    const indexName = tool === 'bwa' ? 'reference.fa.bwt' : 'reference.fa.bwt.2bit.64';
    assert.ok((await page.locator('#files').innerText()).includes(indexName));
    assert.equal(await page.locator('#files li').count(), 8);
    const index = await downloadBytes(page, `Download ${indexName}`);
    assert.equal(index.name, indexName, 'Index download retains its filename');
    assert.ok(index.bytes.length > 1000, `${tool} produced a nonempty binary index`);

    await page.locator('#example').selectOption('2');
    await run();
    assert.match(await page.locator('#stdout').innerText(), /@SQ\tSN:/);
    const firstSam = await downloadBytes(page, 'Download standard output');
    assert.equal(firstSam.name, 'alignment.sam');
    assert.match(firstSam.bytes.toString('utf8'), /^@HD|^@SQ/m);
    assert.ok(firstSam.bytes.length > 1000, `${tool} produced a nonempty alignment`);
    await run();
    assert.deepEqual((await downloadBytes(page, 'Download standard output')).bytes, firstSam.bytes, `${tool}: repeated runs return identical SAM bytes`);
  }

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
  assert.match((await downloadBytes(page, 'Download standard output')).bytes.toString('utf8'), /^##fileformat=VCF/m);

  await page.locator('#tool').selectOption('seqtk');
  await page.locator('#clear').click();
  await run(1);
  assert.match(await page.locator('#stderr').innerText(), /seqtk|Usage/i);
  await upload([['datasets/human/reads_1.fastq', 'reads.fq']]);
  await page.locator('#example').selectOption('2');
  await run();
  const trimmed = await downloadBytes(page, 'Download standard output');
  assert.equal(trimmed.name, 'trimmed.fq');
  const originalRecords = fastqRecords(await readFile(resolve(root, 'datasets/human/reads_1.fastq')));
  const trimmedRecords = fastqRecords(trimmed.bytes);
  assert.equal(trimmedRecords.length, originalRecords.length);
  for (let index = 0; index < originalRecords.length; index++) {
    assert.equal(trimmedRecords[index][1], originalRecords[index][1].slice(5, -10));
    assert.equal(trimmedRecords[index][3], originalRecords[index][3].slice(5, -10));
  }

  await page.locator('#tool').selectOption('adapterremoval');
  await page.locator('#clear').click();
  await run();
  assert.match(`${await page.locator('#stdout').innerText()}${await page.locator('#stderr').innerText()}`, /AdapterRemoval|Usage/i);
  await upload([
    ['datasets/human/reads_1.fastq', 'reads_1.fq'],
    ['datasets/human/reads_2.fastq', 'reads_2.fq'],
  ]);
  await page.locator('#example').selectOption('1');
  await run();
  const firstTrim = await downloadBytes(page, 'Download trimmed.pair1.truncated');
  assert.equal(firstTrim.name, 'trimmed.pair1.truncated');
  assert.ok(fastqRecords(firstTrim.bytes).length > 0);
  assert.ok(fastqRecords((await downloadBytes(page, 'Download trimmed.pair2.truncated')).bytes).length > 0);
  assert.ok((await downloadBytes(page, 'Download trimmed.settings')).bytes.length > 0);
  await run();
  assert.deepEqual((await downloadBytes(page, 'Download trimmed.pair1.truncated')).bytes, firstTrim.bytes, 'Repeated trimming returns identical FASTQ bytes');

  const rnaInputs = [
    ['datasets/rna-human/reference.fa', 'reference.fa'],
    ['datasets/rna-human/reads_1.fastq', 'reads_1.fq'],
    ['datasets/rna-human/reads_2.fastq', 'reads_2.fq'],
  ];
  await page.locator('#tool').selectOption('hisat2');
  await page.locator('#clear').click();
  await run();
  assert.match(`${await page.locator('#stdout').innerText()}${await page.locator('#stderr').innerText()}`, /HISAT2|Usage/i);
  await upload(rnaInputs);
  await page.locator('#example').selectOption('1');
  await run();
  assert.equal(await page.locator('#files li').count(), 11);
  assert.ok((await downloadBytes(page, 'Download reference.1.ht2')).bytes.length > 0);
  await page.locator('#example').selectOption('2');
  await run();
  const hisatSam = await downloadBytes(page, 'Download alignment.sam');
  assert.equal(hisatSam.name, 'alignment.sam');
  assertSplicedSam(hisatSam.bytes, 'HISAT2');
  await run();
  assert.deepEqual((await downloadBytes(page, 'Download alignment.sam')).bytes, hisatSam.bytes, 'Repeated HISAT2 alignment returns identical SAM bytes');

  await page.locator('#tool').selectOption('minimap2');
  await page.locator('#clear').click();
  await run();
  assert.match(`${await page.locator('#stdout').innerText()}${await page.locator('#stderr').innerText()}`, /minimap2|Usage/i);
  await upload(rnaInputs);
  await page.locator('#example').selectOption('1');
  await run();
  const minimapSam = await downloadBytes(page, 'Download standard output');
  assert.equal(minimapSam.name, 'alignment.sam');
  assertSplicedSam(minimapSam.bytes, 'minimap2');
  await page.locator('#example').selectOption('2');
  await run();
  assert.ok((await downloadBytes(page, 'Download reference.mmi')).bytes.length > 0);
  await page.locator('#example').selectOption('3');
  await run();
  assertSplicedSam((await downloadBytes(page, 'Download standard output')).bytes, 'minimap2 with index');
  assert.deepEqual(pageErrors, [], 'The browser reported no uncaught errors');
  console.log(`Browser demo passed with all seven compiled tools in ${browserName}.`);
} finally {
  await browser?.close();
  await new Promise(resolve => server.close(resolve));
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

function fastqRecords(bytes) {
  const lines = bytes.toString('utf8').trimEnd().split('\n');
  assert.equal(lines.length % 4, 0, 'FASTQ contains complete records');
  const records = [];
  for (let index = 0; index < lines.length; index += 4) {
    const record = lines.slice(index, index + 4);
    assert.ok(record[0].startsWith('@'));
    assert.ok(record[2].startsWith('+'));
    assert.equal(record[1].length, record[3].length, 'Sequence and quality lengths match');
    records.push(record);
  }
  return records;
}

function assertSplicedSam(bytes, label) {
  const text = bytes.toString('utf8');
  assert.match(text, /^@SQ\tSN:/m, `${label} returned a SAM header`);
  const records = text.trimEnd().split('\n').filter(line => !line.startsWith('@')).map(line => line.split('\t'));
  assert.ok(records.length > 0, `${label} returned alignment records`);
  assert.ok(records.some(fields => /\d+N/.test(fields[5])), `${label} aligned a real RNA read across an intron`);
}
