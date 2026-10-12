import { ToolRunner } from './index.mjs';

/** Run the original bustools CLI in a fresh worker and virtual filesystem. */
export class BustoolsRunner extends ToolRunner {
  constructor({ baseUrl = new URL('../dist/bustools/', import.meta.url), moduleUrl, ...options } = {}) {
    const base = new URL(baseUrl);
    if (!['file:', 'http:', 'https:'].includes(base.protocol) || !base.pathname.endsWith('/')) {
      throw new TypeError('baseUrl must be a file, http or https directory URL ending in /');
    }
    super({ ...options, moduleUrl: moduleUrl ?? new URL('bustools.mjs', base) });
  }
}
