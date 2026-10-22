import { ToolRunner } from './index.mjs';

export const REPEAT_VERSIONS = Object.freeze({ expansionhunter: '5.0.0', samtools: '1.21', reviewer: '0.2.7' });
const modules = Object.freeze({
  expansionhunter: 'expansionhunter/expansionhunter.mjs',
  samtools: 'hts-1.21/samtools.mjs',
  reviewer: 'reviewer/reviewer.mjs',
});

/** Execute one repeat-analysis command in a fresh worker and filesystem. */
export class RepeatRunner extends ToolRunner {
  constructor({ tool = 'expansionhunter', baseUrl, workerUrl } = {}) {
    if (!Object.hasOwn(modules, tool)) throw new TypeError(`Unsupported repeat-analysis program: ${tool}`);
    const base = new URL(baseUrl ?? '../dist/', import.meta.url);
    if (!base.pathname.endsWith('/')) base.pathname += '/';
    super({ moduleUrl: new URL(modules[tool], base), workerUrl });
    this.tool = tool;
    this.version = REPEAT_VERSIONS[tool];
  }
}
