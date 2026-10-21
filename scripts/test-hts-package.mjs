#!/usr/bin/env node
/** Exercise a relocated HTS ZIP using only its packaged WebAssembly programs. */
import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = resolve(process.env.HTS_PACKAGE_ROOT || fileURLToPath(new URL('../', import.meta.url)));
const requested = process.argv.slice(2);
if (requested.length && (requested.length !== 2 || requested[0] !== '--version')) {
  throw new Error('Usage: test-hts-package.mjs [--version 1.16|1.21]');
}
let release;
try { release = JSON.parse(await readFile(join(root, 'release.json'), 'utf8')); }
catch (error) { if (error.code !== 'ENOENT') throw error; }
const version = requested[1] || process.env.HTS_VERSION || release?.upstreamVersion || '1.21';
assert.ok(['1.16', '1.21'].includes(version), 'Select one supported package version');
if (release) assert.equal(release.upstreamVersion, version, 'The requested version is the shipped release');
const reportDir = resolve(process.env.HTS_PACKAGE_REPORT || join(root, `reports/hts-${version}/package-node`));
await mkdir(reportDir, { recursive: true });
const hash = data => createHash('sha256').update(data).digest('hex');
const text = data => Buffer.from(data).toString('utf8');
const report = { schema: 1, runId: randomUUID(), engine: 'node', version, node: process.version,
  root, status: 'running', startedAt: new Date().toISOString(), assets: {}, runtime: {}, cases: [] };
const base = `dist/hts-${version}`;
const tools = ['bcftools', 'samtools', 'bgzip', 'tabix', 'htsfile'];

async function tree(directory, result) {
  for (const entry of (await readdir(join(root, directory), { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
    const path = `${directory}/${entry.name}`;
    if (entry.isDirectory()) await tree(path, result);
    else { assert.ok(entry.isFile(), `Regular packaged file: ${path}`); result[path] = hash(await readFile(join(root, path))); }
  }
}

// Deliberately artificial truth fixture: one inherited variant, one de novo
// variant, and a multiallelic site whose child genotype is missing.
const vcf = [
  '##fileformat=VCFv4.2',
  '##contig=<ID=chr1,length=1000>',
  '##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence annotations. Format: Allele|Consequence|SYMBOL|Gene|Feature|CANONICAL">',
  '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
  '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tfather\tmother\tchild',
  'chr1\t10\tinherited\tA\tG\t60\tPASS\tCSQ=G|missense_variant|GENE1|ENSG1|ENST1|YES,G|synonymous_variant|GENE1|ENSG1|ENST2|NO\tGT\t0/0\t0/1\t0/1',
  'chr1\t20\tdenovo\tC\tT\t60\tPASS\tCSQ=T|stop_gained|GENE2|ENSG2|ENST3|YES\tGT\t0/0\t0/0\t0/1',
  'chr1\t30\tmulti\tG\tA,T\t60\tPASS\tCSQ=A|missense_variant|GENE3|ENSG3|ENST4|YES,T|synonymous_variant|GENE3|ENSG3|ENST4|YES\tGT\t1/2\t0/1\t./.',
  '',
].join('\n');
const sam = [
  '@HD\tVN:1.6\tSO:coordinate', '@SQ\tSN:chr1\tLN:1000',
  'read1\t0\tchr1\t10\t60\t4M\t*\t0\t0\tACGT\tIIII',
  'read2\t0\tchr1\t100\t60\t4M\t*\t0\t0\tACGT\tIIII',
  'read3\t0\tchr1\t200\t60\t4M\t*\t0\t0\tACGT\tIIII', '',
].join('\n');
const file = (path, data) => ({ path, data: typeof data === 'string' ? Buffer.from(data) : data });
const input = file('input.vcf', vcf);
let HTSRunner;

async function check(id, callback) {
  const record = { id, status: 'running', commands: [] }; report.cases.push(record);
  console.log(`[HTS ${version} package] ${id}`);
  try { await callback(record); record.status = 'passed'; }
  catch (error) { record.status = 'failed'; record.error = error.stack; throw error; }
}

async function command(record, tool, args, files = [], outputs = []) {
  const result = await new HTSRunner({ version, tool, baseUrl: pathToFileURL(`${join(root, base)}/`) })
    .run(args, { files, outputs, timeoutMs: 180000 });
  const observed = { tool, args, exitCode: result.exitCode, missingOutputs: result.missingOutputs,
    inputs: Object.fromEntries(files.map(f => [f.path, { sha256: hash(f.data), bytes: f.data.length }])),
    stdout: { sha256: hash(result.stdout), bytes: result.stdout.length },
    stderr: { sha256: hash(result.stderr), bytes: result.stderr.length },
    outputs: Object.fromEntries(result.files.map(f => [f.path.slice('/work/'.length), { sha256: hash(f.data), bytes: f.data.length }])) };
  record.commands.push(observed);
  const destination = join(reportDir, record.id, String(record.commands.length));
  await mkdir(destination, { recursive: true });
  await writeFile(join(destination, 'stdout'), result.stdout); await writeFile(join(destination, 'stderr'), result.stderr);
  for (const f of result.files) {
    const path = join(destination, f.path.slice('/work/'.length)); await mkdir(dirname(path), { recursive: true }); await writeFile(path, f.data);
  }
  assert.equal(result.exitCode, 0, `${tool} ${args.join(' ')}\n${text(result.stderr)}`);
  assert.deepEqual(result.missingOutputs, [], 'Every explicitly requested output exists');
  return { ...result, file: path => {
    const output = result.files.find(f => f.path === `/work/${path}`); assert.ok(output, `Produced ${path}`); return file(path, output.data);
  } };
}

try {
  await tree(base, report.assets);
  for (const path of ['runtime/index.mjs', 'runtime/worker.mjs', 'runtime/execute.mjs', 'runtime/hts.mjs', 'scripts/test-hts-package.mjs']) {
    report.runtime[path] = hash(await readFile(join(root, path)));
  }
  const identityBytes = await readFile(join(root, base, 'build-manifest.json'));
  const identity = JSON.parse(identityBytes); report.buildManifestSha256 = hash(identityBytes);
  assert.equal(identity.tool, 'hts'); assert.equal(identity.version, version);
  assert.deepEqual(identity.programs, tools);
  const assets = Object.fromEntries(Object.entries(report.assets).filter(([path]) => path !== `${base}/build-manifest.json`)
    .map(([path, checksum]) => [path.slice(base.length + 1), checksum]));
  assert.deepEqual(assets, identity.assets, 'Every shipped asset belongs to the validated build');
  ({ HTSRunner } = await import(pathToFileURL(join(root, 'runtime/hts.mjs')).href));

  await check('versions-and-builtin-plugin-inventory', async record => {
    for (const tool of tools) {
      const result = await command(record, tool, ['--version']);
      assert.match(text(result.stdout) + text(result.stderr), new RegExp(`(?:^|[^0-9.])${version.replace('.', '\\.')}([^0-9.]|$)`));
    }
    const result = await command(record, 'bcftools', ['plugin', '-l']);
    const names = text(result.stdout).trim().split(/\s+/).sort();
    assert.deepEqual(names, [...identity.plugins].sort());
    for (const plugin of ['split-vep', 'fill-tags', 'trio-stats', 'trio-dnm2', 'trio-switch-rate', 'parental-origin']) assert.ok(names.includes(plugin));
    record.plugins = names;
  });
  await check('fill-tags-allele-counts', async record => {
    const fill = await command(record, 'bcftools', ['+fill-tags', 'input.vcf', '-Ov', '-o', 'filled.vcf', '--', '-t', 'AC,AN,AF,NS'], [input], ['filled.vcf']);
    const query = await command(record, 'bcftools', ['query', '-f', '%POS\t%AC\t%AN\t%NS\n', 'filled.vcf'], [fill.file('filled.vcf')]);
    assert.equal(text(query.stdout), '10\t2\t6\t3\n20\t1\t6\t3\n30\t2,1\t4\t2\n');
  });
  await check('split-vep-transcripts-and-multiallelics', async record => {
    const result = await command(record, 'bcftools', ['+split-vep', 'input.vcf', '-d', '-f', '%CHROM\t%POS\t%SYMBOL\t%Consequence\n'], [input]);
    assert.equal(text(result.stdout), 'chr1\t10\tGENE1\tmissense_variant\nchr1\t10\tGENE1\tsynonymous_variant\nchr1\t20\tGENE2\tstop_gained\nchr1\t30\tGENE3\tmissense_variant\nchr1\t30\tGENE3\tsynonymous_variant\n');
  });
  await check('trio-transmission-and-mendelian-errors', async record => {
    const result = await command(record, 'bcftools', ['+trio-stats', '-P', 'child,father,mother', 'input.vcf'], [input]);
    const rows = text(result.stdout).split('\n').filter(line => /^FLT\d+\t/.test(line)).map(line => line.split('\t'));
    assert.equal(rows.length, 1); assert.deepEqual(rows[0].slice(1, 7), ['child', 'father', 'mother', '2', '2', '1']);
  });
  await check('bcf-indexed-roundtrip', async record => {
    const converted = await command(record, 'bcftools', ['view', '-Ob', '-o', 'out.bcf', 'input.vcf'], [input], ['out.bcf']);
    const indexed = await command(record, 'bcftools', ['index', 'out.bcf'], [converted.file('out.bcf')], ['out.bcf.csi']);
    const selected = await command(record, 'bcftools', ['query', '-r', 'chr1:15-25', '-f', '%POS\t%REF\t%ALT\n', 'out.bcf'], [converted.file('out.bcf'), indexed.file('out.bcf.csi')]);
    assert.equal(text(selected.stdout), '20\tC\tT\n');
    const identified = await command(record, 'htsfile', ['out.bcf'], [converted.file('out.bcf')]);
    assert.match(text(identified.stdout), /BCF/);
  });
  await check('bam-indexed-roundtrip', async record => {
    const bam = await command(record, 'samtools', ['view', '-b', '-o', 'reads.bam', 'reads.sam'], [file('reads.sam', sam)], ['reads.bam']);
    await command(record, 'samtools', ['quickcheck', 'reads.bam'], [bam.file('reads.bam')]);
    const index = await command(record, 'samtools', ['index', 'reads.bam'], [bam.file('reads.bam')], ['reads.bam.bai']);
    const selected = await command(record, 'samtools', ['view', 'reads.bam', 'chr1:90-110'], [bam.file('reads.bam'), index.file('reads.bam.bai')]);
    assert.equal(text(selected.stdout), 'read2\t0\tchr1\t100\t60\t4M\t*\t0\t0\tACGT\tIIII\n');
  });
  await check('bgzip-tabix-indexed-query', async record => {
    const zipped = await command(record, 'bgzip', ['-c', 'input.vcf'], [input]);
    assert.ok(zipped.stdout.length > 0); const gz = file('input.vcf.gz', zipped.stdout);
    const index = await command(record, 'tabix', ['-p', 'vcf', 'input.vcf.gz'], [gz], ['input.vcf.gz.tbi']);
    const selected = await command(record, 'tabix', ['input.vcf.gz', 'chr1:15-25'], [gz, index.file('input.vcf.gz.tbi')]);
    assert.equal(text(selected.stdout), vcf.split('\n').find(line => line.startsWith('chr1\t20\t')) + '\n');
  });
  for (const [path, checksum] of Object.entries({ ...report.assets, ...report.runtime })) {
    assert.equal(hash(await readFile(join(root, path))), checksum, `Packaged input remained unchanged: ${path}`);
  }
  report.status = 'passed'; console.log(`[HTS ${version} package] Passed ${report.cases.length} standalone Node checks`);
} catch (error) {
  report.status = 'failed'; report.error = error.stack; process.exitCode = 1; console.error(error);
} finally {
  report.finishedAt = new Date().toISOString(); await writeFile(join(reportDir, 'summary.json'), JSON.stringify(report, null, 2) + '\n');
}
