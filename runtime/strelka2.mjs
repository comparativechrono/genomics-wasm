import { ToolRunner } from './index.mjs';
import { runSomatic } from './strelka2-somatic.mjs';

const TOOLS = new Set(['strelka2', 'GetChromDepth', 'MergeRunStats', 'bgzip', 'bgzf_cat', 'tabix', 'htsfile']);

/** Strelka2 native executables and its serial somatic workflow. */
export class Strelka2Runner {
  constructor({ baseUrl = new URL('../dist/strelka2/', import.meta.url), workerUrl } = {}) {
    this.baseUrl = new URL(baseUrl);
    if (!this.baseUrl.pathname.endsWith('/')) throw new TypeError('baseUrl must identify a directory ending in /');
    this.workerUrl = workerUrl;
  }

  async run(args = [], options = {}) {
    if (!Array.isArray(args) || args.some(arg => typeof arg !== 'string' || arg.includes('\0'))) {
      throw new TypeError('args must be an array of strings without NUL characters');
    }
    const execute = (tool, command, stageOptions) => new ToolRunner({
      moduleUrl: new URL(`${tool}.mjs`, this.baseUrl),
      ...(this.workerUrl === undefined ? {} : { workerUrl: this.workerUrl }),
    }).run(command, stageOptions);
    if (args[0] === 'somatic') return runSomatic(args.slice(1), options, execute, this.baseUrl);
    const tool = TOOLS.has(args[0]) ? args[0] : 'strelka2';
    return execute(tool, TOOLS.has(args[0]) ? args.slice(1) : args, options);
  }
}
