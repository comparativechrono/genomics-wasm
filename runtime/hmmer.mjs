import { ToolRunner } from './index.mjs';

export const HMMER_PROGRAMS = Object.freeze([
  'hmmbuild', 'hmmsearch', 'hmmscan', 'hmmpress', 'hmmfetch', 'hmmalign',
  'hmmconvert', 'hmmemit', 'hmmstat', 'phmmer', 'jackhmmer', 'nhmmer', 'nhmmscan',
]);

/** Select an original serial HMMER executable and run it in a fresh worker. */
export class HmmerRunner {
  constructor({ baseUrl = new URL('../dist/hmmer/', import.meta.url), workerUrl } = {}) {
    this.baseUrl = new URL(baseUrl);
    if (!['file:', 'http:', 'https:'].includes(this.baseUrl.protocol) || !this.baseUrl.pathname.endsWith('/')) {
      throw new TypeError('baseUrl must be a file, http or https directory URL ending in /');
    }
    this.workerUrl = workerUrl;
  }

  run(args = [], options = {}) {
    if (!Array.isArray(args) || args.some(value => typeof value !== 'string' || value.includes('\0'))) {
      return Promise.reject(new TypeError('args must be an array of strings without NUL characters'));
    }
    const [program, ...command] = args;
    if (!HMMER_PROGRAMS.includes(program)) {
      return Promise.reject(new RangeError(`Select a supported HMMER program: ${HMMER_PROGRAMS.join(', ')}`));
    }
    return new ToolRunner({
      moduleUrl: new URL(`${program}.mjs`, this.baseUrl),
      ...(this.workerUrl === undefined ? {} : { workerUrl: this.workerUrl }),
    }).run(command, options);
  }
}
