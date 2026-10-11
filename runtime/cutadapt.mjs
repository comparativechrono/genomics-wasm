import { ToolRunner } from './index.mjs';

/** Run the bundled Cutadapt in a fresh Python/WebAssembly worker per command. */
export class CutadaptRunner extends ToolRunner {
  constructor({ baseUrl = new URL('../dist/cutadapt/', import.meta.url),
    workerUrl = new URL('./cutadapt-worker.mjs', import.meta.url) } = {}) {
    const base = new URL(baseUrl, import.meta.url);
    if (!base.pathname.endsWith('/')) base.pathname += '/';
    super({ moduleUrl: new URL('manifest.json', base), workerUrl });
    this.baseUrl = base.href;
  }
}
