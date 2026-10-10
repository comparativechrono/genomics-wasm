#!/usr/bin/env node
/** Exercise the production allocation-failure path in a disposable worker. */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { isMainThread } from 'node:worker_threads';
import { ToolRunner } from '../runtime/index.mjs';

if (!isMainThread) {
  // This process-local override affects only this test worker. The real
  // Emscripten allocator still handles the failed growth and its retries.
  WebAssembly.Memory.prototype.grow = function () {
    throw new RangeError('Injected browser memory pressure');
  };
  await import('../runtime/worker.mjs');
} else {
  const root = new URL('../', import.meta.url);
  const moduleUrl = new URL('dist/bwa-mem2/bwa-mem2.mjs', root);
  const reportDir = new URL('reports/node/', root);
  const report = { status: 'running', fault: 'Reject heap growth in one disposable worker' };
  await mkdir(reportDir, { recursive: true });
  try {
    report.binaries = Object.fromEntries(await Promise.all(['bwa-mem2.mjs', 'bwa-mem2.wasm'].map(async name =>
      [name, createHash('sha256').update(await readFile(new URL(`dist/bwa-mem2/${name}`, root))).digest('hex')])));
    const files = await Promise.all(['reference.fa', 'reads_1.fastq', 'reads_2.fastq'].map(async path =>
      ({ path, data: await readFile(new URL(`datasets/human/${path}`, root)) })));
    const outputs = ['0123', 'amb', 'ann', 'bwt.2bit.64', 'pac'].map(suffix => `reference.fa.${suffix}`);
    const normal = new ToolRunner({ moduleUrl });
    const indexed = await normal.run(['index', 'reference.fa'], {
      files: [files[0]], outputs, timeoutMs: 120000,
    });
    assert.equal(indexed.exitCode, 0, 'The real reference must index successfully');
    assert.deepEqual(indexed.missingOutputs, []);
    assert.equal(indexed.files.length, outputs.length);

    const pressured = new ToolRunner({ moduleUrl, workerUrl: new URL(import.meta.url) });
    await assert.rejects(
      pressured.run(['mem', '-t', '1', '-K', '10000000', 'reference.fa', 'reads_1.fastq', 'reads_2.fastq'], {
        files: [...files, ...indexed.files], timeoutMs: 120000,
      }),
      error => {
        report.failure = { name: error.name, message: error.message };
        assert.equal(error.name, 'RuntimeError');
        assert.match(error.message, /Aborted\(OOM\)/);
        return true;
      },
      'Exhausted memory must reject the command instead of returning alignment output',
    );
    const fresh = await normal.run(['version'], { timeoutMs: 120000 });
    assert.equal(fresh.exitCode, 0, 'The next command must use an independent worker');
    assert.equal(new TextDecoder().decode(fresh.stdout).trim(), '2.2.1');
    report.freshWorkerExitCode = fresh.exitCode;
    report.status = 'passed';
    console.log('PASS: BWA-MEM2 rejects heap exhaustion with Aborted(OOM); a fresh worker succeeds.');
  } catch (error) {
    report.status = 'failed';
    report.error = error.stack;
    console.error(error.stack);
    process.exitCode = 1;
  } finally {
    // Inputs and indexes remain in memory; ToolRunner terminates every worker.
    await writeFile(new URL('bwa-mem2-memory.json', reportDir), JSON.stringify(report, null, 2) + '\n');
  }
}
