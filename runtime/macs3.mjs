import { ToolRunner } from './index.mjs';

/** Run the complete MACS3 CLI in a fresh Python/WebAssembly worker per command. */
export class MACS3Runner extends ToolRunner {
  constructor({ baseUrl = new URL('../dist/macs3/', import.meta.url),
    workerUrl = new URL('./macs3-worker.mjs', import.meta.url) } = {}) {
    const base = new URL(baseUrl, import.meta.url);
    if (!base.pathname.endsWith('/')) base.pathname += '/';
    super({ moduleUrl: new URL('manifest.json', base), workerUrl });
    this.baseUrl = base.href;
  }
}
