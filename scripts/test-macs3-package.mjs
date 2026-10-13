#!/usr/bin/env node
// Run the actual packaged Node/WebAssembly API after relocating the release.
// This smoke test has no dependency on a native MACS3 executable or build tree.
import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = resolve(process.env.MACS3_PACKAGE_ROOT || fileURLToPath(new URL('../', import.meta.url)));
const reportDir = resolve(process.env.MACS3_PACKAGE_REPORT || join(root, 'reports/macs3/package-node'));
await mkdir(reportDir, { recursive: true });
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const summary = { schema: 1, runId: randomUUID(), engine: 'node', node: process.version,
  root, status: 'running', startedAt: new Date().toISOString(), assets: {}, runtime: {}, inputs: {}, cases: [] };

async function recordTree(directory, target) {
  for (const entry of (await readdir(join(root, directory), { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
    const path = `${directory}/${entry.name}`;
    if (entry.isDirectory()) await recordTree(path, target);
    else {
      assert.ok(entry.isFile(), `Expected a regular packaged file: ${path}`);
      target[path] = hash(await readFile(join(root, path)));
    }
  }
}

function rows(bytes, columns, label) {
  assert.ok(bytes.length > 0, `${label} is nonempty`);
  const parsed = Buffer.from(bytes).toString('utf8').split(/\r?\n/)
    .filter(line => line.trim() && !/^(?:#|track\b|browser\b)/.test(line)).map(line => line.split(/\s+/));
  assert.ok(parsed.length > 0, `${label} has genomic records`);
  for (const row of parsed) {
    assert.equal(row.length, columns, `${label} has its documented column count`);
    assert.ok(Number.isSafeInteger(Number(row[1])) && Number(row[1]) >= 0);
    assert.ok(Number.isSafeInteger(Number(row[2])) && Number(row[2]) > Number(row[1]));
  }
  return parsed;
}

async function fixture(name, manifest) {
  const path = `datasets/macs3/macs3/${name}`;
  const bytes = await readFile(join(root, path));
  const expected = manifest.files[`macs3/${name}`];
  assert.ok(expected, `Fixture is declared in the source manifest: ${name}`);
  assert.equal(bytes.length, expected.bytes); assert.equal(hash(bytes), expected.sha256);
  summary.inputs[path] = { bytes: bytes.length, sha256: hash(bytes) };
  return { path: name, data: bytes };
}

async function command(runner, id, args, files, outputs) {
  console.log(`[MACS3 package] ${id}`);
  const record = { id, args, status: 'running', startedAt: new Date().toISOString(),
    inputs: Object.fromEntries(files.map(file => [file.path, { bytes: file.data.length, sha256: hash(file.data) }])) };
  summary.cases.push(record);
  try {
    const result = await runner.run(args, { files, outputs, timeoutMs: 600000 });
    record.exitCode = result.exitCode; record.missingOutputs = result.missingOutputs;
    record.stdout = { bytes: result.stdout.length, sha256: hash(result.stdout) };
    record.stderr = { bytes: result.stderr.length, sha256: hash(result.stderr) };
    record.outputs = Object.fromEntries(result.files.map(file => [file.path, { bytes: file.data.length, sha256: hash(file.data) }]));
    const directory = join(reportDir, id); await mkdir(directory, { recursive: true });
    await writeFile(join(directory, 'stdout.txt'), result.stdout); await writeFile(join(directory, 'stderr.txt'), result.stderr);
    for (const file of result.files) {
      // The runner guarantees that returned paths stay within /work.
      const destination = join(directory, file.path.slice('/work/'.length));
      await mkdir(dirname(destination), { recursive: true }); await writeFile(destination, file.data);
    }
    assert.equal(result.exitCode, 0, `${id}: ${Buffer.from(result.stderr).toString('utf8').slice(-5000)}`);
    assert.deepEqual(result.missingOutputs, [], `${id} returns every requested file`);
    for (const output of outputs) assert.ok(result.files.some(file => file.path === `/work/${output}`));
    return { result, record };
  } catch (error) { record.status = 'failed'; record.error = error.stack; throw error; }
  finally { record.finishedAt = new Date().toISOString(); }
}

try {
  for (const name of ['runtime/index.mjs', 'runtime/macs3.mjs', 'runtime/macs3-worker.mjs', 'runtime/macs3-execute.mjs', 'scripts/test-macs3-package.mjs']) {
    summary.runtime[name] = hash(await readFile(join(root, name)));
  }
  await recordTree('dist/macs3', summary.assets);
  const build = JSON.parse(await readFile(join(root, 'dist/macs3/build-manifest.json'), 'utf8'));
  assert.equal(build.tool, 'macs3');
  const compiled = Object.fromEntries(Object.entries(summary.assets).filter(([path]) => path !== 'dist/macs3/build-manifest.json')
    .map(([path, checksum]) => [path.slice('dist/macs3/'.length), checksum]));
  assert.deepEqual(compiled, build.assets, 'The complete packaged distribution matches the compiled asset manifest');
  const manifest = JSON.parse(await readFile(join(root, 'dist/macs3/manifest.json'), 'utf8'));
  const datasetBytes = await readFile(join(root, 'datasets/macs3/manifest.json'));
  summary.datasetManifestSha256 = hash(datasetBytes);
  const dataset = JSON.parse(datasetBytes);
  // Import from the selected package root, not from the test runner's checkout.
  const { MACS3Runner } = await import(pathToFileURL(join(root, 'runtime/macs3.mjs')).href);
  const runner = new MACS3Runner({ baseUrl: pathToFileURL(`${join(root, 'dist/macs3')}/`) });
  const version = await command(runner, 'version', ['--version'], [], []);
  assert.ok(Buffer.from(version.result.stdout).toString('utf8').includes(manifest.version));
  version.record.status = 'passed';

  const signal = await fixture('signal.bdg', dataset);
  const scaled = await command(runner, 'synthetic-track-arithmetic',
    ['bdgopt', '-i', signal.path, '-m', 'multiply', '-p', '2', '-o', 'scaled.bdg'], [signal], ['scaled.bdg']);
  const actual = rows(scaled.result.files.find(file => file.path === '/work/scaled.bdg').data, 4, 'Scaled bedGraph')
    .map(([chromosome, start, end, value]) => [chromosome, Number(start), Number(end), Number(value)]);
  // Independent truth for the declared artificial track, not a native output.
  assert.deepEqual(actual, [
    ['chrTest', 0, 100, 0], ['chrTest', 100, 300, 20], ['chrTest', 300, 400, 4],
    ['chrTest', 400, 600, 40], ['chrTest', 600, 700, 0], ['chrTest', 700, 900, 16], ['chrTest', 900, 1000, 0],
  ]);
  scaled.record.records = actual.length; scaled.record.status = 'passed';

  const files = await Promise.all(['chip.bed.gz', 'control.bed.gz'].map(name => fixture(name, dataset)));
  const called = await command(runner, 'authentic-chip-peaks',
    ['callpeak', '-t', 'chip.bed.gz', '-c', 'control.bed.gz', '-f', 'BED', '-g', '51000000',
      '--nomodel', '--extsize', '150', '--keep-dup', 'all', '-q', '0.05', '-n', 'package', '--outdir', 'called'],
    files, ['called/package_peaks.narrowPeak', 'called/package_summits.bed']);
  const peakRows = rows(called.result.files.find(file => file.path.endsWith('.narrowPeak')).data, 10, 'Authentic narrow peaks');
  const summits = rows(called.result.files.find(file => file.path.endsWith('_summits.bed')).data, 5, 'Peak summits');
  assert.equal(summits.length, peakRows.length);
  for (const [index, row] of peakRows.entries()) {
    assert.equal(row[0], 'chr22'); assert.equal(row[5], '.');
    for (const column of [4, 6, 7, 8]) assert.ok(Number.isFinite(Number(row[column])) && Number(row[column]) >= 0);
    assert.ok(Number.isSafeInteger(Number(row[9])) && Number(row[9]) >= 0 && Number(row[9]) < Number(row[2]) - Number(row[1]));
    assert.equal(summits[index][0], row[0]);
    assert.equal(Number(summits[index][1]), Number(row[1]) + Number(row[9]));
    assert.equal(Number(summits[index][2]), Number(summits[index][1]) + 1);
  }
  called.record.peaks = peakRows.length; called.record.status = 'passed';
  // A relocation test should identify the same bytes before and after execution.
  for (const [path, checksum] of Object.entries({ ...summary.assets, ...summary.runtime })) {
    assert.equal(hash(await readFile(join(root, path))), checksum, `Packaged file changed while running: ${path}`);
  }
  summary.status = 'passed'; console.log(`[MACS3 package] Passed ${summary.cases.length} standalone Node checks`);
} catch (error) {
  summary.status = 'failed'; summary.error = error.stack; process.exitCode = 1;
  const active = summary.cases.findLast(record => record.status === 'running');
  if (active) { active.status = 'failed'; active.error = error.stack; }
  console.error(error);
} finally {
  summary.finishedAt = new Date().toISOString();
  await writeFile(join(reportDir, 'summary.json'), `${JSON.stringify(summary, null, 2)}\n`);
}
