import { ToolRunner, workspacePath } from './index.mjs';

const CORE = new Set(['classify', 'build_db', 'estimate_capacity', 'dump_table', 'lookup_accession_numbers']);
const encoder = new TextEncoder(), decoder = new TextDecoder();
const CLASSIFY_VALUES = new Map(Object.entries({ db: null, threads: '-p', confidence: '-T',
  'minimum-base-quality': '-Q', 'minimum-hit-groups': '-g', report: '-R', output: '-O',
  'classified-out': '-C', 'unclassified-out': '-U' }));
const CLASSIFY_FLAGS = new Map(Object.entries({ quick: '-q', paired: '-P', 'use-names': '-n',
  'use-mpa-style': '-m', 'report-zero-counts': '-z', 'report-minimizer-data': '-K',
  'memory-mapping': '-M', 'gzip-compressed': null, 'bzip2-compressed': null }));
const BUILD_VALUES = new Set(['db', 'threads', 'kmer-len', 'minimizer-len', 'minimizer-spaces',
  'load-factor', 'max-db-size', 'block-size', 'subblock-size', 'minimum-bits-for-taxid']);
const VERSION = 'Kraken version 2.17.1\nCopyright 2013-2023, Derrick Wood (dwood@cs.jhu.edu)\n';
const HELP = {
  classify: `Usage: kraken2 [options] FASTA/FASTQ [more files]
  --db DIR                    Prepared Kraken2 database (required)
  --threads 1                 One processing thread
  --paired                    Consecutive files are mate pairs
  --quick                     Stop classification after the first hit
  --confidence FLOAT          Required confidence in [0,1] (default 0)
  --minimum-hit-groups INT    Minimum minimizer-hit groups (default 2)
  --minimum-base-quality INT  Mask lower-quality FASTQ bases (default 0)
  --use-names                 Include scientific names in classifications
  --output FILE              Classification text (default stdout; - suppresses)
  --report FILE              Taxonomic abundance report
  --report-zero-counts        Include taxa with zero counts in the report
  --report-minimizer-data     Include total/distinct minimizer counts
  --use-mpa-style             Use MPA-style report format
  --classified-out FILE      Classified sequences; paired filenames need one #
  --unclassified-out FILE    Unclassified sequences; # becomes _1 and _2
  --gzip-compressed          Decompress inputs (also detected from first file)
  --memory-mapping           Use virtual-filesystem mapping; still held in RAM
  --help                     Show this help
  --version                  Show upstream version (tag 2.17.2 says 2.17.1)

An input filename - reads the runner's stdin. Supply files through the API.
One database and one thread per command. Bzip2, daemon mode and database
downloads are unsupported. Databases and working data must fit in a 2 GiB heap.
Other commands: inspect, build; see runtime/KRAKEN2.md for direct C++ helpers.
`,
  inspect: `Usage: kraken2 inspect --db DIR [options]
  --db DIR                Prepared Kraken2 database (required)
  --threads 1             One processing thread
  --skip-counts           Print database summary without minimizer counts
  --use-mpa-style         Use MPA-style output
  --report-zero-counts    Include all taxa
  --help                 Show this help
  --version              Show upstream version (tag 2.17.2 says 2.17.1)
The report is returned in stdout. Inputs are db/hash.k2d, taxo.k2d, opts.k2d.
`,
  build: `Usage: kraken2 build --db DIR --no-masking [options]
  --db DIR                      Prepared library/taxonomy directory (required)
  --no-masking                  Use prepared sequences without new masking
  --threads 1                   One processing thread
  --kmer-len INT                K-mer length (DNA 35; protein 15)
  --minimizer-len INT           Minimizer length (DNA 31; protein 12; at most 31)
  --minimizer-spaces INT        Spaced-seed positions (DNA 7; protein 0)
  --load-factor FLOAT          Hash table load factor in (0,1] (default 0.7)
  --max-db-size BYTES           Downsample to a smaller hash table
  --block-size INT             Input block size (default 16384)
  --subblock-size INT          Deterministic subblock size (default block size)
  --minimum-bits-for-taxid INT Reserve taxon-ID bits (default 0)
  --protein                   Build a protein database for translated searches
  --fast-build                Use upstream's non-deterministic build mode
  --help                      Show this help
  --version                   Show upstream version (tag 2.17.2 says 2.17.1)
Supply DIR/library/*.fna or *.faa, DIR/seqid2taxid.map, and
DIR/taxonomy/nodes.dmp plus names.dmp. One combined FASTA gives reproducible
library order. The three .k2d outputs are returned; existing hash.k2d skips
rebuilding. No downloads, automatic map generation or masking are performed.
`,
};

class UsageError extends Error {}

/** Original Kraken2 executables, plus local command-line orchestration. */
export class Kraken2Runner {
  constructor({ baseUrl = new URL('../dist/kraken2/', import.meta.url), workerUrl } = {}) {
    this.baseUrl = new URL(baseUrl);
    if (!this.baseUrl.pathname.endsWith('/')) throw new TypeError('baseUrl must end in /');
    this.workerUrl = workerUrl;
  }

  async run(args = [], options = {}) {
    if (!Array.isArray(args) || args.some(x => typeof x !== 'string' || x.includes('\0'))) {
      throw new TypeError('args must be an array of strings without NUL characters');
    }
    if (!Number.isFinite(options.timeoutMs ?? 0) || (options.timeoutMs ?? 0) < 0) throw new TypeError('Invalid timeoutMs');
    if (!Array.isArray(options.files ?? []) || !Array.isArray(options.outputs ?? []) || !Array.isArray(options.outputDirectories ?? [])) {
      throw new TypeError('files, outputs and outputDirectories must be arrays');
    }
    // Capture caller-owned values before decompression or the first helper await.
    args = args.slice();
    options = { ...options,
      files: (options.files ?? []).map(file => ({ path: workspacePath(file.path), data: bytes(file.data).slice() })),
      outputs: (options.outputs ?? []).map(workspacePath),
      outputDirectories: (options.outputDirectories ?? []).map(workspacePath),
      stdin: bytes(options.stdin ?? new Uint8Array()).slice(),
    };
    const controller = new AbortController();
    const abort = () => controller.abort();
    if (options.signal?.aborted) controller.abort();
    options.signal?.addEventListener('abort', abort, { once: true });
    let timedOut = false;
    const timer = options.timeoutMs ? setTimeout(() => { timedOut = true; controller.abort(); }, options.timeoutMs) : undefined;
    const stageOptions = { ...options, signal: controller.signal, timeoutMs: 0 };
    const execute = (program, command, extra = {}) => new ToolRunner({
      moduleUrl: new URL(`${program}.mjs`, this.baseUrl),
      ...(this.workerUrl === undefined ? {} : { workerUrl: this.workerUrl }),
    }).run(command, { ...stageOptions, ...extra });
    try {
      checkAbort(controller.signal);
      if (CORE.has(args[0])) return await execute(args[0], args.slice(1));
      const context = ['build', 'inspect'].includes(args[0]) ? args[0] : 'classify';
      if (args.includes('--version')) return emptyResult(0, '', VERSION);
      if (args.includes('--help')) return emptyResult(0, '', HELP[context]);
      if (args[0] === 'build') return await buildDatabase(args.slice(1), stageOptions, execute);
      if (args[0] === 'inspect') return await inspectDatabase(args.slice(1), stageOptions, execute);
      return await classifyReads(args[0] === 'kraken2' ? args.slice(1) : args, stageOptions, execute);
    } catch (error) {
      if (timedOut) { const timeout = new Error(`Command exceeded ${options.timeoutMs} ms`); timeout.name = 'TimeoutError'; throw timeout; }
      if (error instanceof UsageError) return emptyResult(64, error.message + '\n');
      throw error;
    } finally {
      clearTimeout(timer);
      options.signal?.removeEventListener('abort', abort);
    }
  }
}

function parse(args, values, flags) {
  const found = new Map(), positional = [];
  for (let i = 0; i < args.length; i++) {
    const token = args[i];
    if (token === '--') { positional.push(...args.slice(i + 1)); break; }
    if (!token.startsWith('--')) {
      if (token.startsWith('-') && token !== '-') throw new UsageError(`Unsupported option: ${token}`);
      positional.push(token); continue;
    }
    const equals = token.indexOf('='), name = token.slice(2, equals < 0 ? undefined : equals);
    if (values.has(name)) {
      const value = equals < 0 ? args[++i] : token.slice(equals + 1);
      if (value === undefined || !value.length) throw new UsageError(`--${name} requires a value`);
      found.set(name, value);
    } else if (flags.has(name) && equals < 0) found.set(name, true);
    else throw new UsageError(`Unsupported option: --${name}`);
  }
  return { found, positional };
}

function dbPaths(found) {
  if (!found.has('db')) throw new UsageError('Must specify --db');
  const name = found.get('db');
  const db = name === '.' || name === '/work' ? '/work' : workspacePath(name);
  return { db, flags: ['-H', `${db}/hash.k2d`, '-t', `${db}/taxo.k2d`, '-o', `${db}/opts.k2d`] };
}

function validateThreads(found) {
  if (found.has('threads') && (!/^\+?0*1$/.test(found.get('threads')))) {
    throw new UsageError('Kraken2 browser package requires exactly one thread');
  }
}

function number(found, key, fallback, integer = false, minimum = 0, maximum = Number.MAX_SAFE_INTEGER) {
  const text = found.get(key);
  const value = text === undefined ? fallback : Number(text);
  if (!Number.isFinite(value) || value < minimum || value > maximum || (integer && !Number.isSafeInteger(value))) {
    throw new UsageError(`Invalid --${key}`);
  }
  return value;
}

function fileMap(files = []) {
  if (!Array.isArray(files)) throw new TypeError('files must be an array');
  const map = new Map();
  for (const file of files) {
    const path = workspacePath(file.path);
    if (map.has(path)) throw new TypeError('Input file paths must be unique');
    map.set(path, bytes(file.data));
  }
  return map;
}

async function classifyReads(args, options, execute) {
  const { found, positional } = parse(args, CLASSIFY_VALUES, CLASSIFY_FLAGS);
  const { flags } = dbPaths(found);
  validateThreads(found);
  if (!positional.length) throw new UsageError('Need to specify input filenames');
  if (found.has('paired') && positional.length % 2) throw new UsageError('--paired requires an even number of input files');
  if (found.has('bzip2-compressed')) throw new UsageError('bzip2 input is not supported; provide uncompressed or gzip files');
  const quality = number(found, 'minimum-base-quality', 0, true);
  const groups = number(found, 'minimum-hit-groups', 2, true);
  const confidence = number(found, 'confidence', 0, false, 0, 1);
  flags.push('-p', '1', '-T', String(confidence), '-Q', String(quality), '-g', String(groups));
  const outputs = [...(options.outputs ?? [])];
  for (const [key, flag] of CLASSIFY_VALUES) {
    if (flag && found.has(key) && !['threads', 'confidence', 'minimum-base-quality', 'minimum-hit-groups'].includes(key)) {
      const value = found.get(key);
      flags.push(flag, value === '-' && key === 'output' ? value : workspacePath(value));
      if (value !== '-' || key !== 'output') {
        if (['classified-out', 'unclassified-out'].includes(key) && found.has('paired')) {
          if (value.split('#').length !== 2) throw new UsageError('Paired sequence output requires exactly one # character');
          outputs.push(value.replace('#', '_1'), value.replace('#', '_2'));
        } else outputs.push(value);
      }
    }
  }
  for (const [key, flag] of CLASSIFY_FLAGS) if (flag && found.has(key)) flags.push(flag);
  const mounted = fileMap(options.files);
  // The native Perl wrapper decompresses files before passing bytes to classify.
  // DecompressionStream performs that transport step; all classification stays C++.
  let stdin = options.stdin;
  const first = positional[0] === '-' ? stdin : mounted.get(workspacePath(positional[0]));
  const compressed = found.has('gzip-compressed') || (first?.[0] === 0x1f && first?.[1] === 0x8b);
  if (first?.[0] === 66 && first?.[1] === 90 && first?.[2] === 104) throw new UsageError('bzip2 input is not supported; provide uncompressed or gzip files');
  const decompressed = new Set();
  for (const name of positional) {
    const path = name === '-' ? '/dev/stdin' : workspacePath(name);
    if (name !== '-' && !mounted.has(path)) throw new UsageError(`Input file is not mounted: ${path}`);
    if (compressed && !decompressed.has(path)) {
      try {
        const data = await inflate(name === '-' ? stdin : mounted.get(path), options.signal);
        if (name === '-') stdin = data; else mounted.set(path, data);
        decompressed.add(path);
      }
      catch (error) { if (error.name === 'AbortError') throw error; throw new UsageError(`Invalid gzip input: ${name}`); }
    }
    flags.push(path);
  }
  const result = await execute('classify', flags, { files: [...mounted].map(([path, data]) => ({ path, data })), stdin, outputs });
  return { ...result, stages: [{ program: 'classify', args: flags, exitCode: result.exitCode }] };
}

async function inspectDatabase(args, options, execute) {
  const { found, positional } = parse(args, new Set(['db', 'threads']), new Set(['skip-counts', 'use-mpa-style', 'report-zero-counts']));
  if (positional.length) throw new UsageError('inspect does not accept input filenames');
  validateThreads(found);
  const { flags } = dbPaths(found);
  // Upstream defaults to /dev/fd/1, which MEMFS does not provide. Its normal
  // stdout device preserves the same stream without losing the taxon rows.
  flags.push('-p', '1', '-O', '/dev/stdout');
  for (const [key, flag] of [['skip-counts', '-s'], ['use-mpa-style', '-m'], ['report-zero-counts', '-z']]) if (found.has(key)) flags.push(flag);
  const result = await execute('dump_table', flags);
  return { ...result, stages: [{ program: 'dump_table', args: flags, exitCode: result.exitCode }] };
}

async function buildDatabase(args, options, execute) {
  const { found, positional } = parse(args, BUILD_VALUES, new Set(['build', 'no-masking', 'protein', 'fast-build']));
  if (positional.length) throw new UsageError('build does not accept positional arguments');
  const { db } = dbPaths(found);
  validateThreads(found);
  if (!found.has('no-masking')) throw new UsageError('Local browser builds require --no-masking; supply already prepared library sequences');
  const protein = found.has('protein');
  const k = number(found, 'kmer-len', protein ? 15 : 35, true, 1);
  const l = number(found, 'minimizer-len', protein ? 12 : 31, true, 1, Math.min(k, 31));
  const spaces = number(found, 'minimizer-spaces', protein ? 0 : 7, true, 0, Math.floor(l / 4));
  const load = number(found, 'load-factor', 0.7, false, Number.MIN_VALUE, 1);
  const block = number(found, 'block-size', 16384, true, 1);
  const subblock = number(found, 'subblock-size', block, true, 0) || block;
  const bits = number(found, 'minimum-bits-for-taxid', 0, true, 0, 31);
  const maxBytes = number(found, 'max-db-size', 0, true, 0);
  const files = fileMap(options.files);
  for (const path of ['seqid2taxid.map', 'taxonomy/nodes.dmp', 'taxonomy/names.dmp']) {
    if (!files.has(`${db}/${path}`)) throw new UsageError(`Local build requires ${db}/${path}`);
  }
  const libraries = [...files].filter(([path]) => path.startsWith(`${db}/library/`) && /\.(fna|faa)$/.test(path));
  if (!libraries.length) throw new UsageError('Local build requires library/*.fna or *.faa');
  // Input order is the supplied file order, matching concatenation of an explicitly
  // ordered library list. Supply one combined FASTA for filesystem-independent builds.
  const stdin = concatenate(libraries.map(([, data]) => data));
  const common = ['-k', String(k), '-l', String(l), '-S', '1'.repeat(l - 2 * spaces) + '01'.repeat(spaces), ...(protein ? ['-X'] : [])];
  const stages = [], stderr = [];
  const estimateArgs = [...common, '-p', '1'];
  const estimate = await execute('estimate_capacity', estimateArgs, { files: [], stdin, outputs: [], outputDirectories: [] });
  stages.push({ program: 'estimate_capacity', args: estimateArgs, exitCode: estimate.exitCode });
  stderr.push(estimate.stderr);
  if (estimate.exitCode !== 0) return { ...estimate, stages };
  const text = decoder.decode(estimate.stdout).trim();
  if (!/^\d+$/.test(text)) throw new Error('Capacity estimator did not return an integer');
  const capacity = Math.floor((Number(text) + 8192) / load);
  const actualBytes = maxBytes && maxBytes < capacity * 4 ? Math.floor(maxBytes / 4) * 4 : capacity * 4;
  if (maxBytes > 0 && maxBytes < 4) throw new UsageError('--max-db-size must allow at least one hash-table cell');
  if (!Number.isSafeInteger(capacity) || actualBytes >= 2147483648) throw new UsageError('Estimated database exceeds the 2 GiB browser heap limit');
  const command = [...common, '-H', `${db}/hash.k2d`, '-t', `${db}/taxo.k2d`, '-o', `${db}/opts.k2d`,
    '-n', `${db}/taxonomy/`, '-m', `${db}/seqid2taxid.map`, '-c', String(capacity), '-p', '1',
    ...(maxBytes && maxBytes < capacity * 4 ? ['-M', String(Math.floor(maxBytes / 4))] : []),
    '-B', String(block), '-b', String(subblock), '-r', String(bits), ...(found.has('fast-build') ? ['-F'] : [])];
  const generated = ['hash.k2d', 'taxo.k2d', 'opts.k2d'].map(name => `${db}/${name}`);
  let result;
  if (files.has(`${db}/hash.k2d`)) {
    // Native build skips construction when hash.k2d exists. Keep the usual
    // output contract even if this is an incomplete, previously interrupted DB.
    const selected = new Set(), missingOutputs = [];
    for (const path of new Set([...generated, ...options.outputs])) {
      if (files.has(path)) selected.add(path); else missingOutputs.push(path);
    }
    for (const directory of new Set(options.outputDirectories)) {
      if (files.has(directory)) throw new Error(`Output directory is not a directory: ${directory}`);
      const found = [...files.keys()].filter(path => path.startsWith(`${directory}/`)).sort();
      if (!found.length) missingOutputs.push(directory); else found.forEach(path => selected.add(path));
    }
    result = { ...emptyResult(0), files: [...selected].map(path => ({ path, data: files.get(path) })), missingOutputs };
  } else {
    result = await execute('build_db', command, { files: [...files].map(([path, data]) => ({ path, data })), stdin,
      outputs: [...new Set([...generated, ...(options.outputs ?? [])])], outputDirectories: options.outputDirectories ?? [] });
    stages.push({ program: 'build_db', args: command, exitCode: result.exitCode });
  }
  stderr.push(result.stderr);
  return { ...result, stderr: concatenate(stderr), stages, capacity, libraryFiles: libraries.map(([path]) => path) };
}

async function inflate(data, signal) {
  checkAbort(signal);
  const reader = new Blob([data]).stream().pipeThrough(new DecompressionStream('gzip')).getReader();
  const chunks = [];
  const abort = () => { reader.cancel().catch(() => {}); };
  signal?.addEventListener('abort', abort, { once: true });
  try {
    while (true) {
      checkAbort(signal);
      const item = await reader.read();
      checkAbort(signal);
      if (item.done) break;
      chunks.push(item.value);
    }
    return concatenate(chunks);
  } finally { signal?.removeEventListener('abort', abort); reader.releaseLock(); }
}

function bytes(value) {
  if (typeof value === 'string') return encoder.encode(value);
  if (value instanceof Uint8Array) return value;
  if (value instanceof ArrayBuffer) return new Uint8Array(value);
  throw new TypeError('File data must be string, Uint8Array or ArrayBuffer');
}
function concatenate(chunks) {
  const result = new Uint8Array(chunks.reduce((sum, x) => sum + x.length, 0));
  let offset = 0;
  for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.length; }
  return result;
}
function checkAbort(signal) {
  if (signal?.aborted) { const error = new Error('Command cancelled'); error.name = 'AbortError'; throw error; }
}
function emptyResult(exitCode, stderr = '', stdout = '') {
  return { exitCode, stdout: encoder.encode(stdout), stderr: encoder.encode(stderr), files: [], missingOutputs: [] };
}
