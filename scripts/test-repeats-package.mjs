#!/usr/bin/env node
/** Independently exercise a relocated ZIP using its packaged WebAssembly tools. */
import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = resolve(process.env.REPEATS_PACKAGE_ROOT || fileURLToPath(new URL('../', import.meta.url)));
const reportDir = resolve(process.env.REPEATS_PACKAGE_REPORT || join(root, 'reports/repeats/package-node'));
await mkdir(reportDir, { recursive: true });
const versions = { expansionhunter: '5.0.0', reviewer: '0.2.7', samtools: '1.21' };
const units = ['expansionhunter', 'reviewer', 'hts-1.21'];
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const text = bytes => Buffer.from(bytes).toString('utf8');
const file = (path, data) => ({ path, data: typeof data === 'string' ? Buffer.from(data) : data });
const report = { schema: 1, runId: randomUUID(), engine: 'node', versions, node: process.version,
  root, status: 'running', startedAt: new Date().toISOString(), assets: {}, runtime: {}, fixtures: {}, cases: [] };
let RepeatRunner;
let called, sorted, indexed;
const source = 'datasets/repeats/upstream/expansionhunter';
const realSource = 'datasets/repeats/upstream/reviewer';
let ehInputs, syntheticInputs, realInputs;

async function tree(directory, result) {
  for (const entry of (await readdir(join(root, directory), { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
    const path = `${directory}/${entry.name}`;
    if (entry.isDirectory()) await tree(path, result);
    else { assert.ok(entry.isFile(), `Regular packaged file: ${path}`); result[path] = hash(await readFile(join(root, path))); }
  }
}

async function fixtures(directory, names) {
  return Promise.all(names.map(async entry => {
    const [original, mounted] = Array.isArray(entry) ? entry : [entry, entry];
    const path = `${directory}/${original}`;
    const data = await readFile(join(root, path));
    report.fixtures[path] = hash(data);
    return file(mounted, data);
  }));
}

async function check(id, callback) {
  const record = { id, status: 'running', commands: [] };
  report.cases.push(record);
  console.log(`[Repeats package] ${id}`);
  try { await callback(record); record.status = 'passed'; }
  catch (error) { record.status = 'failed'; record.error = error.stack; throw error; }
}

async function command(record, tool, args, files = [], outputs = [], { failure = false } = {}) {
  const result = await new RepeatRunner({ tool, baseUrl: pathToFileURL(`${join(root, 'dist')}/`) })
    .run(args, { files, outputs, timeoutMs: 300000 });
  const observed = { tool, args, exitCode: result.exitCode, missingOutputs: result.missingOutputs,
    inputs: Object.fromEntries(files.map(f => [f.path, { sha256: hash(f.data), bytes: f.data.length }])),
    stdout: { sha256: hash(result.stdout), bytes: result.stdout.length },
    stderr: { sha256: hash(result.stderr), bytes: result.stderr.length },
    outputs: Object.fromEntries(result.files.map(f => [f.path.slice('/work/'.length), { sha256: hash(f.data), bytes: f.data.length }])) };
  record.commands.push(observed);
  const directory = join(reportDir, record.id, String(record.commands.length));
  await mkdir(directory, { recursive: true });
  await writeFile(join(directory, 'stdout'), result.stdout);
  await writeFile(join(directory, 'stderr'), result.stderr);
  for (const output of result.files) {
    const path = join(directory, output.path.slice('/work/'.length));
    await mkdir(dirname(path), { recursive: true });
    await writeFile(path, output.data);
  }
  assert.ok(Number.isInteger(result.exitCode), 'The program exited normally');
  if (failure) assert.notEqual(result.exitCode, 0, 'Invalid input must fail');
  else assert.equal(result.exitCode, 0, `${tool} ${args.join(' ')}\n${text(result.stderr)}`);
  assert.deepEqual(result.missingOutputs, [], 'Every explicitly requested output exists');
  return { ...result, file: path => {
    const output = result.files.find(f => f.path === `/work/${path}`);
    assert.ok(output, `Produced ${path}`);
    return file(path, output.data);
  } };
}

function genotypeFields(vcf) {
  return Object.fromEntries(text(vcf).split(/\r?\n/).filter(line => line && !line.startsWith('#')).map(line => {
    const columns = line.split('\t');
    const info = Object.fromEntries(columns[7].split(';').map(field => field.split('=')));
    return [info.VARID, Object.fromEntries(columns[8].split(':').map((key, index) => [key, columns[9].split(':')[index]]))];
  }));
}

function table(bytes, header) {
  const rows = text(bytes).trim().split(/\r?\n/).map(line => line.split('\t'));
  assert.deepEqual(rows[0], header);
  assert.ok(rows.length > 1, 'The table contains data rows');
  return rows.slice(1);
}

function plot(bytes) {
  const svg = text(bytes);
  assert.match(svg, /^\s*<svg\b/);
  assert.match(svg, /xmlns="http:\/\/www\.w3\.org\/2000\/svg"/);
  assert.match(svg, /<\/svg>\s*$/);
  for (const dimension of ['width', 'height']) {
    const value = svg.match(new RegExp(`\\b${dimension}="([0-9.]+)"`));
    assert.ok(value && Number(value[1]) > 0, `Positive SVG ${dimension}`);
  }
  assert.ok((svg.match(/<(?:path|rect|line|text|polygon)\b/g) || []).length > 20, 'Plot contains alignment geometry');
  assert.doesNotMatch(svg, /\b(?:NaN|[+-]?inf(?:inity)?)\b/i, 'Plot has finite coordinates');
  assert.doesNotMatch(svg, /<(?:script|foreignObject)\b|\bon[a-z]+\s*=/i, 'Plot is passive SVG');
  return { bytes: bytes.length, sha256: hash(bytes) };
}

try {
  let release;
  try { release = JSON.parse(await readFile(join(root, 'release.json'), 'utf8')); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  if (release) { assert.equal(release.tool, 'repeats'); assert.deepEqual(release.versions, versions); }
  report.buildManifests = {};
  for (const unit of units) {
    const base = `dist/${unit}`;
    await tree(base, report.assets);
    const raw = await readFile(join(root, base, 'build-manifest.json'));
    const identity = JSON.parse(raw);
    const tool = unit === 'hts-1.21' ? 'hts' : unit;
    assert.equal(identity.tool, tool);
    assert.equal(identity.version, unit === 'hts-1.21' ? '1.21' : versions[unit]);
    const assets = Object.fromEntries(Object.entries(report.assets)
      .filter(([path]) => path.startsWith(`${base}/`) && path !== `${base}/build-manifest.json`)
      .map(([path, checksum]) => [path.slice(base.length + 1), checksum]));
    assert.deepEqual(assets, identity.assets, 'Shipped assets belong to the recorded build');
    report.buildManifests[unit] = hash(raw);
  }
  for (const path of ['runtime/index.mjs', 'runtime/worker.mjs', 'runtime/execute.mjs', 'runtime/repeats.mjs', 'scripts/test-repeats-package.mjs']) {
    report.runtime[path] = hash(await readFile(join(root, path)));
  }
  ({ RepeatRunner } = await import(pathToFileURL(join(root, 'runtime/repeats.mjs')).href));
  ehInputs = await fixtures(source, ['reference.fa', 'reference.fa.fai', 'variants.bam', 'variants.bam.bai', 'variants.json']);

  await check('program-versions', async record => {
    for (const [tool, version] of Object.entries(versions)) {
      const result = await command(record, tool, ['--version']);
      assert.match(text(result.stdout) + text(result.stderr), new RegExp(`(?:^|[^0-9.])${version.replaceAll('.', '\\.')}([^0-9.]|$)`));
    }
  });
  await check('call-repeat-and-flanking-snv', async record => {
    const compound = await command(record, 'expansionhunter', ['--reads', 'variants.bam', '--reference', 'reference.fa',
      '--variant-catalog', 'variants.json', '--output-prefix', 'eh', '--threads', '1'], ehInputs,
      ['eh.json', 'eh.vcf', 'eh_realigned.bam']);
    const json = JSON.parse(text(compound.file('eh.json').data));
    const locus = json.LocusResults.SNV_AND_STR;
    assert.equal(locus.AlleleCount, 2);
    assert.equal(locus.Variants['SNV_AND_STR_chr1:2000-2001'].Genotype, '0/1');
    const repeat = locus.Variants['SNV_AND_STR_chr1:2005-2008'];
    assert.equal(repeat.Genotype, '2/10');
    assert.equal(repeat.GenotypeConfidenceInterval, '2-2/10-10');
    assert.equal(repeat.RepeatUnit, 'CAG');
    const vcf = genotypeFields(compound.file('eh.vcf').data);
    assert.equal(vcf['SNV_AND_STR_chr1:2000-2001'].GT, '0/1');
    assert.equal(vcf['SNV_AND_STR_chr1:2005-2008'].REPCN, '2/10');
    assert.equal(vcf['SNV_AND_STR_chr1:2005-2008'].REPCI, '2-2/10-10');
    record.truth = { repeat: '2/10', repeatInterval: '2-2/10-10', flankingSnv: '0/1' };
  });
  await check('synthetic-diploid-haploid-and-expanded-truth', async record => {
    syntheticInputs = await fixtures('datasets/repeats/common', ['reference.fa', 'reference.fa.fai', 'reads.bam', 'reads.bam.bai', 'catalog.json']);
    called = await command(record, 'expansionhunter', ['--reads', 'reads.bam', '--reference', 'reference.fa', '--variant-catalog',
      'catalog.json', '--output-prefix', 'eh', '--threads', '1', '--sex', 'male'], syntheticInputs,
      ['eh.json', 'eh.vcf', 'eh_realigned.bam']);
    const loci = JSON.parse(text(called.file('eh.json').data)).LocusResults;
    const repeat = id => Object.values(loci[id].Variants).find(value => value.VariantType === 'Repeat');
    assert.equal(loci.NORMAL_CAG.AlleleCount, 2);
    assert.equal(repeat('NORMAL_CAG').Genotype, '12/18');
    assert.equal(loci.X_CAG.AlleleCount, 1);
    assert.equal(repeat('X_CAG').Genotype, '18');
    const expanded = repeat('EXPANDED_CAG');
    const intervals = expanded.GenotypeConfidenceInterval.split('/').map(value => value.split('-').map(Number));
    assert.equal(intervals.length, 2);
    for (const [i, count] of [12, 75].entries()) assert.ok(intervals[i][0] <= count && count <= intervals[i][1], 'Known expanded allele lies in its confidence interval');
    assert.ok(Math.max(...expanded.Genotype.split('/').map(Number)) > 24);
    record.truth = { normal: '12/18', haploid: '18', expandedAlleles: [12, 75], intervals };
  });
  await check('sort-index-and-query-generated-bamlet', async record => {
    const before = await command(record, 'samtools', ['view', '-c', 'eh_realigned.bam'], [called.file('eh_realigned.bam')]);
    const count = Number(text(before.stdout).trim());
    assert.ok(Number.isInteger(count) && count > 20, 'Realigned BAMlet contains supporting reads');
    sorted = await command(record, 'samtools', ['sort', '-o', 'eh.sorted.bam', 'eh_realigned.bam'],
      [called.file('eh_realigned.bam')], ['eh.sorted.bam']);
    indexed = await command(record, 'samtools', ['index', 'eh.sorted.bam'], [sorted.file('eh.sorted.bam')], ['eh.sorted.bam.bai']);
    await command(record, 'samtools', ['quickcheck', 'eh.sorted.bam'], [sorted.file('eh.sorted.bam')]);
    const after = await command(record, 'samtools', ['view', '-c', 'eh.sorted.bam', 'chr1', 'chr2', 'chrX'],
      [sorted.file('eh.sorted.bam'), indexed.file('eh.sorted.bam.bai')]);
    assert.equal(Number(text(after.stdout).trim()), count, 'Sorting and indexed access preserve every alignment');
    record.alignmentCount = count;
  });
  await check('render-newly-generated-repeat-evidence', async record => {
    const inputs = [...syntheticInputs.filter(f => /reference|catalog\.json/.test(f.path)), called.file('eh.vcf'),
      sorted.file('eh.sorted.bam'), indexed.file('eh.sorted.bam.bai')];
    const result = await command(record, 'reviewer', ['--reads', 'eh.sorted.bam', '--vcf', 'eh.vcf', '--reference', 'reference.fa',
      '--catalog', 'catalog.json', '--locus', 'NORMAL_CAG', '--output-prefix', 'review', '--region-extension-length', '1000'], inputs,
      ['review.NORMAL_CAG.svg', 'review.metrics.tsv', 'review.phasing.tsv']);
    record.plot = plot(result.file('review.NORMAL_CAG.svg').data);
    const metrics = table(result.file('review.metrics.tsv').data, ['VariantId', 'Genotype', 'AlleleDepth']);
    assert.ok(metrics.some(row => row[0] === 'NORMAL_CAG' && row[1] === '12/18'));
    const phasing = table(result.file('review.phasing.tsv').data, ['LocusId', 'Diplotype', 'Score']);
    assert.ok(phasing.some(row => row[0] === 'NORMAL_CAG'));
  });
  await check('real-upstream-iupac-repeat-plot', async record => {
    realInputs = await fixtures(realSource, ['reference.fa.gz', 'reference.fa.gz.fai', 'reference.fa.gz.gzi',
      'BEAN1_HG00684.bam', 'BEAN1_HG00684.bam.bai', 'HG00684.vcf', 'catalog.json']);
    const result = await command(record, 'reviewer', ['--reads', 'BEAN1_HG00684.bam', '--vcf', 'HG00684.vcf',
      '--reference', 'reference.fa.gz', '--catalog', 'catalog.json', '--locus', 'BEAN1', '--output-prefix', 'real'], realInputs,
      ['real.BEAN1.svg', 'real.metrics.tsv', 'real.phasing.tsv']);
    record.plot = plot(result.file('real.BEAN1.svg').data);
    const metrics = table(result.file('real.metrics.tsv').data, ['VariantId', 'Genotype', 'AlleleDepth']);
    assert.deepEqual(metrics.map(row => row.slice(0, 2)), [['BEAN1', '17/17']]);
    assert.ok(table(result.file('real.phasing.tsv').data, ['LocusId', 'Diplotype', 'Score']).some(row => row[0] === 'BEAN1'));
  });
  await check('invalid-locus-and-worker-recovery', async record => {
    const result = await command(record, 'reviewer', ['--reads', 'BEAN1_HG00684.bam', '--vcf', 'HG00684.vcf',
      '--reference', 'reference.fa.gz', '--catalog', 'catalog.json', '--locus', 'NOT_A_REAL_LOCUS', '--output-prefix', 'missing'],
      realInputs, [], { failure: true });
    assert.match(text(result.stderr) + text(result.stdout), /locus|catalog|not.*found/i);
    const recovered = await command(record, 'reviewer', ['--version']);
    assert.match(text(recovered.stdout) + text(recovered.stderr), /0\.2\.7/);
  });
  for (const [path, checksum] of Object.entries({ ...report.assets, ...report.runtime, ...report.fixtures })) {
    assert.equal(hash(await readFile(join(root, path))), checksum, `Packaged input remained unchanged: ${path}`);
  }
  report.status = 'passed';
  console.log(`[Repeats package] Passed ${report.cases.length} standalone Node checks`);
} catch (error) {
  report.status = 'failed'; report.error = error.stack; process.exitCode = 1; console.error(error);
} finally {
  report.finishedAt = new Date().toISOString();
  await writeFile(join(reportDir, 'summary.json'), JSON.stringify(report, null, 2) + '\n');
}
