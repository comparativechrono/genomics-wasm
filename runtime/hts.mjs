import { ToolRunner } from './index.mjs';

export const HTS_VERSIONS = Object.freeze(['1.16', '1.21']);
export const HTS_TOOLS = Object.freeze(['bcftools', 'samtools', 'bgzip', 'tabix', 'htsfile']);

/** Run a pinned HTS command in a fresh worker using the ordinary ToolRunner API. */
export class HTSRunner extends ToolRunner {
  constructor({ version = '1.21', tool = 'bcftools', baseUrl, workerUrl } = {}) {
    if (!HTS_VERSIONS.includes(version)) throw new TypeError(`Unsupported HTS version: ${version}`);
    if (!HTS_TOOLS.includes(tool)) throw new TypeError(`Unsupported HTS tool: ${tool}`);
    const base = new URL(baseUrl ?? `../dist/hts-${version}/`, import.meta.url);
    if (!base.pathname.endsWith('/')) base.pathname += '/';
    super({ moduleUrl: new URL(`${tool}.mjs`, base), workerUrl });
    this.version = version;
    this.tool = tool;
  }
}
