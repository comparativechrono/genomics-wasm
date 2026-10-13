import { ToolRunner, workspacePath } from './index.mjs';

export const PRIMER3_PROGRAMS = Object.freeze([
  'primer3_core', 'ntdpal', 'ntthal', 'oligotm',
  'amplicon3_core', 'long_seq_tm_test', 'primer3_masker',
]);

/** Run an original Primer3 CLI in a fresh worker and virtual filesystem. */
export class Primer3Runner extends ToolRunner {
  constructor({ program = 'primer3_core', baseUrl = new URL('../dist/primer3/', import.meta.url), moduleUrl, ...options } = {}) {
    if (!PRIMER3_PROGRAMS.includes(program)) throw new TypeError(`Unsupported Primer3 program: ${program}`);
    const base = new URL(baseUrl);
    if (!['file:', 'http:', 'https:'].includes(base.protocol) || !base.pathname.endsWith('/')) {
      throw new TypeError('baseUrl must be a file, http or https directory URL ending in /');
    }
    super({ ...options, moduleUrl: moduleUrl ?? new URL(`${program}.mjs`, base) });
    this.program = program;
  }

  async run(args = [], options = {}) {
    if (!Array.isArray(args) || args.some(value => typeof value !== 'string' || value.includes('\0'))) {
      throw new TypeError('args must be an array of strings without NUL characters');
    }
    // The original masker FASTA reader seeks backward to copy each header.
    // Emscripten's stdin character device is not seekable. Materialize only
    // explicitly supplied stdin when the CLI would read from stdin, preserving
    // named-input precedence and leaving help/errors to the original parser.
    if (this.program !== 'primer3_masker' || options.stdin === undefined || !maskerReadsStdin(args)) {
      return super.run(args, options);
    }
    const files = options.files ?? [], outputs = options.outputs ?? [], directories = options.outputDirectories ?? [];
    if (!Array.isArray(files) || !Array.isArray(outputs) || !Array.isArray(directories)) {
      throw new TypeError('files, outputs and outputDirectories must be arrays');
    }
    const occupied = new Set([...files.map(file => workspacePath(file.path)), ...outputs.map(workspacePath), ...directories.map(workspacePath)]);
    for (const name of [...occupied]) {
      for (let parent = name.slice(0, name.lastIndexOf('/')); parent !== '/work'; parent = parent.slice(0, parent.lastIndexOf('/'))) occupied.add(parent);
    }
    let path = '/work/__primer3_masker_stdin.fasta', suffix = 0;
    while (occupied.has(path)) path = `/work/__primer3_masker_stdin_${++suffix}.fasta`;
    const result = await super.run([...args, path], {
      ...options, files: [...files, { path, data: options.stdin }], stdin: new Uint8Array(),
    });
    return { ...result, files: result.files.filter(file => file.path !== path) };
  }
}

/** Identify upstream masker stdin use, without interpreting scientific options. */
export function maskerReadsStdin(args) {
  if (!Array.isArray(args) || args.some(value => typeof value !== 'string' || value.includes('\0'))) {
    throw new TypeError('args must be an array of strings without NUL characters');
  }
  // With no arguments upstream prints help. A literal '-' is not an upstream
  // stdin alias, and malformed/unknown options must retain their normal error.
  if (!args.length) return false;
  const values = new Set(['-lf', '--lists_file', '-lp', '--list_prefix', '-lh', '--kmer_lists_path',
    '-p', '--probability_cutoff', '-a', '--absolute_value_cutoff', '-m5', '--mask_5p',
    '-m3', '--mask_3p', '-c', '--masking_char', '-d', '--masking_direction']);
  const switches = new Set(['-s', '--soft_mask', '-D']);
  for (let index = 0; index < args.length; index++) {
    const arg = args[index];
    if (index === args.length - 1 && !arg.startsWith('-')) return false;
    if (values.has(arg)) {
      if (index + 1 >= args.length) return false;
      index++;
    } else if (arg === '-l' || arg === '--list') {
      // The original option accepts a list followed by coefficient, mismatch
      // count and optional 'sq', or one numeric intercept. Preserve its token
      // boundary rules, including negative coefficients and long aliases.
      const start = index;
      while (index + 1 < args.length) {
        const value = args[index + 1], position = index + 1 - start;
        if (value.startsWith('-')) {
          if (value.length === 1 || !isCNumber(value.slice(1))) break;
        } else if (position !== 1 && position !== 4) {
          if (!isCNumber(value)) break;
        } else if (position === 4 && value !== 'sq') break;
        index++;
      }
      if (index === start || index - start > 4) return false;
    } else if (!switches.has(arg)) return false;
  }
  return true;
}

function isCNumber(value) {
  // Match a fully consumed C strtod token, as masker_main.c does when finding
  // the end of -l values. No number is calculated or reformatted here.
  return value === '' || /^[\t\n\v\f\r ]*[+-]?(?:(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?|0[xX](?:[\da-fA-F]+\.?[\da-fA-F]*|\.[\da-fA-F]+)(?:[pP][+-]?\d+)?|inf(?:inity)?|nan(?:\([\w]*\))?)$/i.test(value);
}
