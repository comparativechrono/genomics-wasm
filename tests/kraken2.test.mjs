import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdir, mkdtemp, writeFile, rm } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { Kraken2Runner } from '../runtime/kraken2.mjs';

const text = bytes => new TextDecoder().decode(bytes);
const absent = new URL('./no-compiled-kraken2/', import.meta.url);

// These are wrapper/error contracts, not stand-ins for real classifications.
// The separate metagenomics suite compares native and compiled C++ output.
test('Kraken2 keeps the pinned release source version string', async () => {
  const runner = new Kraken2Runner({ baseUrl: absent });
  const result = await runner.run(['--version']);
  assert.equal(result.exitCode, 0);
  assert.match(text(result.stdout), /^Kraken version 2\.17\.1\n/);
  for (const [prefix, expected] of [[[], /--classified-out/], [['build'], /--minimizer-spaces/], [['inspect'], /--skip-counts/]]) {
    const help = await runner.run([...prefix, '--help']);
    assert.equal(help.exitCode, 0);
    assert.match(text(help.stdout), expected);
  }
});

test('multi-stage builds snapshot input bytes and options before the first await', async () => {
  const base = new URL('../build/', import.meta.url);
  await mkdir(base, { recursive: true });
  const directory = await mkdtemp(new URL('kraken2-wrapper-test-', base));
  const worker = `${directory}/worker.mjs`;
  // This worker reports transport arguments only; it is not a classifier.
  await writeFile(worker, `import {parentPort} from 'node:worker_threads';
parentPort.once('message', m => {
 const estimate = m.moduleUrl.endsWith('/estimate_capacity.mjs');
 const stdout = new TextEncoder().encode(estimate ? '256\\n' : JSON.stringify({
   files:m.files.map(f=>[f.path,new TextDecoder().decode(f.data)]),
   outputs:m.outputs, directories:m.outputDirectories, stdin:new TextDecoder().decode(m.stdin), args:m.args
 }));
 setTimeout(()=>parentPort.postMessage({type:'result',result:{exitCode:0,stdout,stderr:new Uint8Array(),files:[],missingOutputs:[]}}), estimate?30:0);
});\n`);
  try {
    const runner = new Kraken2Runner({ baseUrl: absent, workerUrl: pathToFileURL(worker) });
    const names = ['db/library/reference.fna', 'db/seqid2taxid.map', 'db/taxonomy/nodes.dmp', 'db/taxonomy/names.dmp'];
    const files = names.map((path, i) => ({ path, data: new TextEncoder().encode(`original-${i}\n`) }));
    const outputs = ['extra.txt'], outputDirectories = ['db'];
    const args = ['build', '--db', 'db', '--no-masking'];
    const pending = runner.run(args, { files, outputs, outputDirectories });
    files.forEach(file => file.data.fill(120));
    files[0].path = 'different.fna'; files.push({ path: 'injected.txt', data: 'bad' });
    outputs[0] = 'changed.txt'; outputDirectories[0] = 'changed'; args[2] = 'changed';
    const observed = JSON.parse(text((await pending).stdout));
    assert.deepEqual(observed.files, names.map((path, i) => [`/work/${path}`, `original-${i}\n`]));
    assert.equal(observed.stdin, 'original-0\n');
    assert.ok(observed.outputs.includes('/work/extra.txt'));
    assert.ok(!observed.outputs.includes('/work/changed.txt'));
    assert.deepEqual(observed.directories, ['/work/db']);
    assert.ok(observed.args.includes('/work/db/hash.k2d'));
  } finally { await rm(directory, { recursive: true }); }
});

test('unsupported options and parallel requests fail before loading a module', async () => {
  const runner = new Kraken2Runner({ baseUrl: absent });
  for (const args of [
    ['--db', 'db', '--threads', '2', 'reads.fastq'],
    ['--db', 'db', '--threads', '1x', 'reads.fastq'],
    ['inspect', '--db', 'db', '--threads', '0'],
    ['--db', 'db', '--bzip2-compressed', 'reads.fastq'],
    ['--db', 'db', '--daemon', 'reads.fastq'],
    ['--db', 'db', '--confidence', 'NaN', 'reads.fastq'],
    ['--db', 'db', '--confidence', '1.01', 'reads.fastq'],
    ['--db', 'db', '--paired', 'reads.fastq'],
    ['--db', 'db', '--report'],
    ['--db', 'db', 'missing.fastq'],
  ]) {
    const result = await runner.run(args);
    assert.equal(result.exitCode, 64, args.join(' '));
    assert.ok(result.stderr.length);
    assert.equal(result.files.length, 0);
  }
});

test('local build requires prepared inputs and explicit no-masking', async () => {
  const runner = new Kraken2Runner({ baseUrl: absent });
  const unprepared = await runner.run(['build', '--db', 'db', '--no-masking']);
  assert.equal(unprepared.exitCode, 64);
  assert.match(text(unprepared.stderr), /seqid2taxid\.map/);
  assert.match(text((await runner.run(['build', '--db', 'db'])).stderr), /--no-masking/);
  for (const extra of [['--kmer-len', '0'], ['--minimizer-len', '32'], ['--load-factor', '0'], ['--minimizer-spaces', '8']]) {
    const result = await runner.run(['build', '--db', 'db', '--no-masking', ...extra]);
    assert.equal(result.exitCode, 64, extra.join(' '));
  }
});

test('wrapper rejects duplicate inputs and paths outside the virtual workspace', async () => {
  const runner = new Kraken2Runner({ baseUrl: absent });
  await assert.rejects(runner.run(['--db', '../db', 'reads.fastq']), /inside \/work/);
  await assert.rejects(runner.run(['--db', 'db', 'reads.fastq'], { files: [
    { path: 'reads.fastq', data: '' }, { path: '/work/reads.fastq', data: '' },
  ] }), /unique/);
  await assert.rejects(runner.run(['--db', 'db', 'bad\0name']), /NUL/);
});

test('malformed gzip fails visibly without attempting classification', async () => {
  const runner = new Kraken2Runner({ baseUrl: absent });
  const result = await runner.run(['--db', 'db', '--gzip-compressed', 'reads.gz'], {
    files: [{ path: 'reads.gz', data: new Uint8Array([0x1f, 0x8b, 0, 0]) }],
  });
  assert.equal(result.exitCode, 64);
  assert.match(text(result.stderr), /Invalid gzip input/);
});

test('pre-cancellation and timeout argument checks apply before any command', async () => {
  const runner = new Kraken2Runner({ baseUrl: absent });
  const controller = new AbortController(); controller.abort();
  await assert.rejects(runner.run(['--version'], { signal: controller.signal }), { name: 'AbortError' });
  await assert.rejects(runner.run(['--version'], { timeoutMs: -1 }), /timeoutMs/);
  assert.throws(() => new Kraken2Runner({ baseUrl: new URL('./not-directory', import.meta.url) }), /end in/);
});

test('root database paths and inspect stdout mapping preserve the complete command', async () => {
  const base = new URL('../build/', import.meta.url);
  await mkdir(base, { recursive: true });
  const directory = await mkdtemp(new URL('kraken2-path-test-', base));
  const worker = `${directory}/worker.mjs`;
  await writeFile(worker, `import {parentPort} from 'node:worker_threads';
parentPort.once('message',m=>parentPort.postMessage({type:'result',result:{exitCode:0,
stdout:new TextEncoder().encode(JSON.stringify(m.args)),stderr:new Uint8Array(),files:[],missingOutputs:[]}}));\n`);
  try {
    const runner = new Kraken2Runner({ baseUrl: absent, workerUrl: pathToFileURL(worker) });
    for (const db of ['.', '/work']) {
      const args = JSON.parse(text((await runner.run(['--db', db, 'reads.fastq'], { files: [{ path: 'reads.fastq', data: '' }] })).stdout));
      assert.deepEqual(args.slice(0, 6), ['-H', '/work/hash.k2d', '-t', '/work/taxo.k2d', '-o', '/work/opts.k2d']);
    }
    const inspect = JSON.parse(text((await runner.run(['inspect', '--db', '.', '--report-zero-counts'])).stdout));
    assert.ok(inspect.includes('-z'));
    assert.equal(inspect[inspect.indexOf('-O') + 1], '/dev/stdout');
  } finally { await rm(directory, { recursive: true }); }
});
