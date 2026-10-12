// SPDX-License-Identifier: GPL-3.0-or-later
// Shell workflow derived from Bracken, copyright (C) 2016-2023 Jennifer Lu.
import { ToolRunner, workspacePath } from './index.mjs';
const encoder = new TextEncoder();
const PYTHON = new Set(['est_abundance.py', 'generate_kmer_distribution.py']);
const HELP = `Usage: bracken -d DB -i REPORT -o OUTPUT [options]
  -d DB          Directory containing database<READ_LEN>mers.kmer_distrib
  -i REPORT      Kraken report to re-estimate
  -o OUTPUT      Abundance table output
  -w OUTREPORT   Redistributed Kraken report (default: derived from input name)
  -r READ_LEN    Distribution read length (default: 100)
  -l LEVEL       Taxonomic rank: D,P,C,O,F,G,S,S1,... (default: S)
  -t THRESHOLD   Minimum assigned reads before re-estimation (default: 10)
  -v             Print the upstream shell's version text
  -h, --help     Show this help
`;
const BUILD_HELP = `Usage: bracken-build -d DB [options]
  -d DB          Local Kraken2 database directory
  -k KMER_LEN    Database k-mer length (default: 35)
  -l READ_LEN    Read length for the distribution (default: 100)
  -t 1           One execution thread (only supported value)
  -y kraken2     Database format (only supported value)
  -v             Print the upstream shell's version text
  -h, --help     Show this help
Requires hash.k2d, opts.k2d, taxo.k2d, seqid2taxid.map,
taxonomy/nodes.dmp and original library/*.fna, *.fa or *.fasta.
Reuses nonempty database.kraken or database.kraken.tsv when supplied.
Produces database.kraken, database<READ_LEN>mers.kraken and
  database<READ_LEN>mers.kmer_distrib in DB.
`;

/** Bracken's unchanged Python algorithms and serial local distribution workflow. */
export class BrackenRunner {
  constructor({ baseUrl = new URL('../dist/bracken/', import.meta.url), kraken2BaseUrl,
    workerUrl = new URL('./bracken-worker.mjs', import.meta.url) } = {}) {
    this.baseUrl = new URL(baseUrl);
    if (!this.baseUrl.pathname.endsWith('/')) throw new TypeError('baseUrl must end in /');
    this.kraken2BaseUrl = new URL(kraken2BaseUrl || '../kraken2/', this.baseUrl);
    this.workerUrl = workerUrl;
  }
  async run(args = [], options = {}) {
    if (!Array.isArray(args) || args.some(arg => typeof arg !== 'string' || arg.includes('\0'))) {
      throw new TypeError('args must be an array of strings without NUL characters');
    }
    args = [...args];
    options = { ...options,
      files: (options.files ?? []).map(file => ({ path: file.path, data: copyBytes(file.data) })),
      outputs: [...(options.outputs ?? [])], outputDirectories: [...(options.outputDirectories ?? [])],
      stdin: copyBytes(options.stdin ?? new Uint8Array()),
    };
    const execute = async (tool, command, opts) => {
      if (tool === 'kraken2') {
        const { Kraken2Runner } = await import('./kraken2.mjs');
        return new Kraken2Runner({ baseUrl: this.kraken2BaseUrl }).run(command, opts);
      }
      return new ToolRunner(tool === 'kmer2read_distr'
        ? { moduleUrl: new URL('kmer2read_distr.mjs', this.baseUrl) }
        : { moduleUrl: new URL('manifest.json', this.baseUrl), workerUrl: this.workerUrl })
        .run(PYTHON.has(tool) ? [tool, ...command] : command, opts);
    };
    if (PYTHON.has(args[0]) || args[0] === 'kmer2read_distr') return execute(args[0], args.slice(1), options);
    const build = ['build', 'bracken-build'].includes(args[0]);
    return runWorkflow(build, ['bracken', 'build', 'bracken-build'].includes(args[0]) ? args.slice(1) : args, options, execute);
  }
}

/** Internal scheduler, exported for isolated lifecycle tests; no algorithms live here. */
export async function runWorkflow(build, args, options, execute) {
  const { files = [], outputs = [], outputDirectories = [], stdin = new Uint8Array(), signal, timeoutMs = 0 } = options;
  if (![files, outputs, outputDirectories].every(Array.isArray)) throw new TypeError('files, outputs and outputDirectories must be arrays');
  if (!Number.isFinite(timeoutMs) || timeoutMs < 0) throw new TypeError('timeoutMs must be a non-negative number');
  const workspace = new Map();
  for (const file of files) {
    const path = workspacePath(file.path);
    if (workspace.has(path)) throw new TypeError('Input file paths must be unique');
    workspace.set(path, copyBytes(file.data));
  }
  const requested = [...new Set(outputs.map(workspacePath))];
  const directories = [...new Set(outputDirectories.map(workspacePath))];
  const input = copyBytes(stdin);
  const controller = new AbortController();
  let timedOut = false;
  const onAbort = () => controller.abort();
  signal?.addEventListener('abort', onAbort, { once: true });
  if (signal?.aborted) controller.abort();
  const timer = timeoutMs ? setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs) : null;
  const stages = [], out = [], err = [];
  const check = () => {
    if (controller.signal.aborted) {
      const error = new Error(timedOut ? `Command exceeded ${timeoutMs} ms` : 'Command cancelled');
      error.name = timedOut ? 'TimeoutError' : 'AbortError'; throw error;
    }
  };
  const requireFile = name => {
    const p = workspacePath(name);
    if (!workspace.has(p)) throw new UsageError(`File does not exist: ${name}`);
    return p;
  };
  const stage = async (tool, command, resultPaths = [], captureStdout) => {
    check();
    const inputs = await hashFiles(workspace); check();
    const result = await execute(tool, command, {
      files: [...workspace].map(([path, data]) => ({ path, data })),
      outputs: [...new Set([...workspace.keys(), ...resultPaths])], stdin: input, signal: controller.signal,
    });
    check();
    for (const file of result.files) workspace.set(workspacePath(file.path), copyBytes(file.data));
    if (captureStdout) workspace.set(workspacePath(captureStdout), copyBytes(result.stdout));
    out.push(result.stdout); err.push(result.stderr);
    const stageOutputs = new Map(result.files.map(file => [workspacePath(file.path), file.data]));
    if (captureStdout) stageOutputs.set(workspacePath(captureStdout), result.stdout);
    stages.push({ tool, args: [...command], exitCode: result.exitCode, inputs,
      outputHashes: await hashFiles(stageOutputs), missingOutputs: result.missingOutputs });
    check();
    if (result.exitCode !== 0) throw new StageError(result.exitCode);
    if (result.missingOutputs.length) throw new Error(`Stage ${tool} omitted required outputs: ${result.missingOutputs.join(', ')}`);
  };
  const finish = exitCode => {
    const selected = new Set(), missingOutputs = [];
    for (const path of requested) {
      if (workspace.has(path)) selected.add(path); else missingOutputs.push(path);
    }
    for (const directory of directories) {
      const found = [...workspace.keys()].filter(path => path.startsWith(`${directory}/`));
      if (!found.length) missingOutputs.push(directory); else found.forEach(path => selected.add(path));
    }
    return { exitCode, stdout: concat(out), stderr: concat(err),
      files: [...selected].map(path => ({ path, data: workspace.get(path) })), missingOutputs,
      workflow: { tool: 'bracken', kind: build ? 'build' : 'estimate', stages } };
  };
  try {
    check();
    const flags = parseOptions(args, build);
    if (flags.help) { out.push(encoder.encode(build ? BUILD_HELP : HELP)); return finish(0); }
    if (flags.v) { out.push(encoder.encode(build ? 'bracken-build.sh v3.0.1\n' : 'Bracken v3.0.1\n')); return finish(0); }
    if (!flags.d) throw new UsageError('A database directory is required (-d)');
    const dbName = flags.d.replace(/\/+$/, '');
    const db = dbName === '.' || dbName === '/work' ? '/work' : workspacePath(dbName);
    if (build) {
      if (flags.y && flags.y !== 'kraken2') throw new UsageError('Only Kraken2 local databases are supported');
      if (flags.x) throw new UsageError('-x selects a host executable directory; use kraken2BaseUrl in the browser API');
      if (flags.t !== undefined && flags.t !== '1') throw new UsageError('This build supports exactly one thread (-t 1)');
      const length = flags.l ?? '100', kmer = flags.k ?? '35';
      if (!/^[0-9]+$/.test(length) || Number(length) <= 1 || !/^[0-9]+$/.test(kmer) || Number(kmer) <= 1) throw new UsageError('Read and k-mer lengths must be integers greater than one');
      requireFile(`${db}/taxonomy/nodes.dmp`); requireFile(`${db}/seqid2taxid.map`); requireFile(`${db}/hash.k2d`);
      const genomes = [...workspace.keys()].filter(path => path.startsWith(`${db}/library/`) && /\.(fna|fa|fasta)$/.test(path));
      if (![...workspace.keys()].some(path => path.startsWith(`${db}/library/`))) throw new UsageError('Database library directory is required');
      const classifications = `${db}/database.kraken`;
      if (!workspace.get(classifications)?.length && workspace.get(`${db}/database.kraken.tsv`)?.length) {
        workspace.set(classifications, copyBytes(workspace.get(`${db}/database.kraken.tsv`)));
      }
      if (!workspace.get(classifications)?.length) {
        if (!genomes.length) throw new UsageError('Database library has no FASTA files');
        await stage('kraken2', ['--db', db, '--threads', '1', ...genomes], [], classifications);
      }
      const kmers = `${db}/database${length}mers.kraken`, distribution = `${db}/database${length}mers.kmer_distrib`;
      await stage('kmer2read_distr', ['--seqid2taxid', `${db}/seqid2taxid.map`, '--taxonomy', `${db}/taxonomy/`,
        '--kraken', classifications, '--output', kmers, '-k', kmer, '-l', length, '-t', '1'], [kmers]);
      await stage('generate_kmer_distribution.py', ['-i', kmers, '-o', distribution], [distribution]);
    } else {
      if (!flags.i) throw new UsageError('An input report is required (-i)');
      const input = requireFile(flags.i);
      if (!flags.o) throw new UsageError('An abundance output file is required (-o)');
      const output = workspacePath(flags.o);
      const distribution = requireFile(`${db}/database${flags.r ?? '100'}mers.kmer_distrib`);
      const command = ['-i', input, '-o', output];
      const required = [output];
      if (flags.w) { const path = workspacePath(flags.w); command.push('--out-report', path); required.push(path); }
      else required.push(defaultReport(input, flags.l ?? 'S'));
      command.push('-k', distribution, '-l', flags.l ?? 'S', '-t', flags.t ?? '10');
      await stage('est_abundance.py', command, required);
    }
    check(); return finish(0);
  } catch (error) {
    if (controller.signal.aborted) check();
    if (error instanceof StageError) return finish(error.exitCode);
    if (error instanceof UsageError) { err.push(encoder.encode(`bracken: ${error.message}\n`)); return finish(2); }
    throw error;
  } finally {
    clearTimeout(timer); signal?.removeEventListener('abort', onAbort);
  }
}

function defaultReport(input, level) {
  const labels = { D: 'domains', P: 'phylums', C: 'classes', O: 'orders', F: 'families', G: 'genuses', S: 'species' };
  const slash = input.lastIndexOf('/'), dot = input.lastIndexOf('.');
  const split = dot > slash + 1 && !/^\.+$/.test(input.slice(slash + 1, dot)) ? dot : input.length;
  return `${input.slice(0, split)}_bracken_${labels[level] ?? level}${input.slice(split)}`;
}
class UsageError extends Error {}
class StageError extends Error { constructor(exitCode) { super('Stage failed'); this.exitCode = exitCode; } }
function parseOptions(args, build) {
  const allowed = build ? 'kldxty' : 'tior dwl'.replace(/ /g, '');
  const result = {};
  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (arg === '-h' || arg === '--help') { result.help = true; continue; }
    if (arg === '-v') { result.v = true; continue; }
    if (!/^-[^-]/.test(arg) || !allowed.includes(arg[1])) throw new UsageError(`Unsupported option: ${arg}`);
    const value = arg.length > 2 ? arg.slice(2) : args[++i];
    if (value === undefined) throw new UsageError(`Missing value for ${arg}`);
    result[arg[1]] = value;
  }
  return result;
}
function copyBytes(data) {
  if (typeof data === 'string') return encoder.encode(data);
  if (data instanceof Uint8Array) return new Uint8Array(data);
  if (data instanceof ArrayBuffer) return new Uint8Array(data.slice(0));
  throw new TypeError('File data must be a string, Uint8Array or ArrayBuffer');
}
function concat(chunks) {
  const result = new Uint8Array(chunks.reduce((n, chunk) => n + chunk.length, 0));
  let offset = 0; for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.length; } return result;
}
async function hashFiles(files) {
  const result = {};
  for (const [path, data] of files) {
    const hash = await crypto.subtle.digest('SHA-256', data);
    result[path] = [...new Uint8Array(hash)].map(value => value.toString(16).padStart(2, '0')).join('');
  }
  return result;
}
