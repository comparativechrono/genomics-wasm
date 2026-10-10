/* Genuine DESeq2 in a local webR worker. No analytical code is reimplemented. */
const DEFAULT_BASE = new URL('../dist/deseq2/', import.meta.url);
const encoder = new TextEncoder();

function relativePath(path) {
  if (typeof path !== 'string' || !path || path.startsWith('/') || path.includes('\\') ||
      path.split('/').some(part => !part || part === '.' || part === '..')) {
    throw new TypeError('File paths must be relative to the R working directory.');
  }
  return `/work/${path}`;
}

async function readAsset(url, signal) {
  if (url.protocol === 'file:') {
    const { readFile } = await import('node:fs/promises');
    return new Uint8Array(await readFile(url, { signal }));
  }
  const response = await fetch(url, { signal });
  if (!response.ok) throw new Error(`Cannot load ${url.pathname}: HTTP ${response.status}`);
  return new Uint8Array(await response.arrayBuffer());
}

/** Start an independent R session using the packages shipped with this bundle. */
export async function createDESeq2({ baseUrl = DEFAULT_BASE, onProgress = () => {}, signal } = {}) {
  const base = new URL(String(baseUrl), import.meta.url);
  if (!base.pathname.endsWith('/')) base.pathname += '/';
  if (signal?.aborted) throw new DOMException('Cancelled', 'AbortError');
  onProgress('Loading R');
  const manifest = JSON.parse(new TextDecoder().decode(await readAsset(new URL('manifest.json', base), signal)));
  // webR's .mjs entry imports Node built-ins; its .js entry is for browsers.
  const isNode = typeof process !== 'undefined' && !!process.versions?.node;
  const entry = isNode ? 'webr/webr.mjs' : 'webr/webr.js';
  const { WebR, ChannelType } = await import(new URL(entry, base).href);
  const rBase = new URL('webr/', base);
  const r = new WebR({
    baseUrl: rBase.protocol === 'file:' ? decodeURIComponent(rBase.pathname) : rBase.href,
    repoUrl: new URL('packages/', base).href,
    interactive: false,
    channelType: ChannelType.PostMessage,
  });
  let closed = false;
  let busy = false;
  let cancelReject;
  const cancelled = new Promise((_, reject) => { cancelReject = reject; });
  // A listener makes cancellation safe before the first operation starts.
  cancelled.catch(() => {});
  function close() {
    if (closed) return;
    closed = true;
    r.close();
    cancelReject(new DOMException('R session closed', 'AbortError'));
    signal?.removeEventListener('abort', close);
  }
  function checkOpen() { if (closed) throw new Error('R session is closed; create a new session.'); }
  const guard = promise => Promise.race([promise, cancelled]);
  signal?.addEventListener('abort', close, { once: true });
  if (signal?.aborted) close();
  try {
    checkOpen();
    await guard(r.init());
    await guard(r.FS.mkdir('/library'));
    await guard(r.FS.mkdir('/work'));
    for (let i = 0; i < manifest.packages.length; i++) {
      checkOpen();
      const pkg = manifest.packages[i];
      onProgress(`Loading ${pkg.name} (${i + 1}/${manifest.packages.length})`);
      const bytes = await guard(readAsset(new URL(`packages/${pkg.filename}`, base), signal));
      await guard(r.FS.writeFile('/tmp/package.tgz', bytes));
      await guard(r.evalRVoid('untar("/tmp/package.tgz", exdir="/library", tar="internal")'));
      await guard(r.FS.unlink('/tmp/package.tgz'));
    }
    onProgress('Starting DESeq2');
    await guard(r.evalRVoid(`
      .libPaths(c("/library", .libPaths()))
      options(repos = c(CRAN = "file:///no-network-package-repository"))
      suppressPackageStartupMessages(library(DESeq2))
      BiocParallel::register(BiocParallel::SerialParam())
      setwd("/work")
    `));
  } catch (error) {
    close();
    throw error;
  }

  async function writeFile(path, data) {
    checkOpen();
    const absolute = relativePath(path);
    const parts = absolute.split('/').slice(1, -1);
    let directory = '';
    for (const part of parts) {
      directory += '/' + part;
      try { await guard(r.FS.lookupPath(directory)); }
      catch { await guard(r.FS.mkdir(directory)); }
    }
    await guard(r.FS.writeFile(absolute, typeof data === 'string' ? encoder.encode(data) : data));
  }
  async function readFile(path) {
    checkOpen();
    return guard(r.FS.readFile(relativePath(path)));
  }
  async function collectFiles() {
    const result = {};
    async function walk(path, relative = '') {
      const node = await guard(r.FS.lookupPath(path));
      for (const [name, child] of Object.entries(node.contents ?? {})) {
        const rel = relative ? `${relative}/${name}` : name;
        if (child.isFolder) await walk(`${path}/${name}`, rel);
        else result[rel] = await guard(r.FS.readFile(`${path}/${name}`));
      }
    }
    await walk('/work');
    return result;
  }
  async function run(script, { files = {}, resetFiles = false } = {}) {
    checkOpen();
    if (busy) throw new Error('This R session is already running.');
    if (typeof script !== 'string') throw new TypeError('R code must be a string.');
    busy = true;
    let shelter;
    try {
      if (resetFiles) await guard(r.evalRVoid('unlink(list.files("/work", all.files=TRUE, no..=TRUE, full.names=TRUE), recursive=TRUE)'));
      for (const [path, data] of Object.entries(files)) await writeFile(path, data);
      await guard(r.evalRVoid('setwd("/work")'));
      shelter = await guard(new r.Shelter());
      const captured = await guard(shelter.captureR(script, {
        captureStreams: true, captureConditions: true, captureGraphics: false,
        withAutoprint: true, throwJsException: false,
      }));
      let stdout = '', stderr = '', failed = false;
      for (const output of captured.output) {
        let data = output.data;
        if (typeof data !== 'string') {
          try { data = await (await data.get('message')).toString(); }
          catch { data = String(data); }
        }
        if (output.type === 'stdout') stdout += data + '\n';
        else if (output.type !== 'stdout') {
          stderr += data + '\n';
          if (output.type === 'error') failed = true;
        }
      }
      const result = { stdout, stderr, files: await collectFiles() };
      if (failed) throw Object.assign(new Error(stderr.trim() || 'R evaluation failed'), result);
      return result;
    } finally {
      if (shelter && !closed) await shelter.purge();
      busy = false;
    }
  }
  onProgress('Ready');
  return { run, readFile, writeFile, close, interrupt: close, manifest };
}
