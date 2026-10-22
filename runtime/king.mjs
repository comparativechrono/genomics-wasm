import { ToolRunner } from './index.mjs';

export const KING_VERSION = '2.3.2';

/** Run a locally compiled KING in a fresh worker and filesystem. */
export class KingRunner extends ToolRunner {
  constructor({ baseUrl = new URL('../dist/king/2.3.2/', import.meta.url), moduleUrl, workerUrl } = {}) {
    const base = new URL(baseUrl);
    if (!['file:', 'http:', 'https:'].includes(base.protocol) || !base.pathname.endsWith('/')) {
      throw new TypeError('baseUrl must be a file, http or https directory URL ending in /');
    }
    super({ moduleUrl: moduleUrl ?? new URL('king.mjs', base), workerUrl });
    this.version = KING_VERSION;
  }
}
