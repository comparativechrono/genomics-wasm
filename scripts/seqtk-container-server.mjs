#!/usr/bin/env node
// Static hosting only: no uploaded data or commands are sent to this server.
import { createServer } from 'node:http';
import { createReadStream } from 'node:fs';
import { realpath, stat } from 'node:fs/promises';
import { resolve, extname, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const mime = {
  '.html': 'text/html; charset=utf-8', '.mjs': 'text/javascript', '.js': 'text/javascript',
  '.css': 'text/css', '.wasm': 'application/wasm', '.json': 'application/json',
  '.md': 'text/plain; charset=utf-8', '.fq': 'text/plain', '.fastq': 'text/plain',
};

export function createStaticServer(directory) {
  const root = realpath(directory);
  return createServer(async (request, response) => {
    const headers = {
      'Cross-Origin-Opener-Policy': 'same-origin',
      'Cross-Origin-Embedder-Policy': 'require-corp',
      'Cross-Origin-Resource-Policy': 'same-origin',
      'X-Content-Type-Options': 'nosniff',
      'Cache-Control': 'no-cache',
    };
    try {
      if (!['GET', 'HEAD'].includes(request.method)) {
        response.writeHead(405, { ...headers, Allow: 'GET, HEAD' }).end(); return;
      }
      const base = await root;
      const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
      let filename = resolve(base, `.${pathname}`);
      const within = path => path === base || path.startsWith(base + sep);
      if (!within(filename)) { response.writeHead(403, headers).end(); return; }
      let info = await stat(filename);
      if (info.isDirectory()) filename = resolve(filename, 'index.html');
      filename = await realpath(filename);
      if (!within(filename)) { response.writeHead(403, headers).end(); return; }
      info = await stat(filename);
      if (!info.isFile()) { response.writeHead(404, headers).end(); return; }
      response.writeHead(200, { ...headers, 'Content-Type': mime[extname(filename)] || 'application/octet-stream', 'Content-Length': info.size });
      if (request.method === 'HEAD') { response.end(); return; }
      const stream = createReadStream(filename);
      stream.on('error', () => response.destroy());
      stream.pipe(response);
    } catch {
      response.writeHead(404, headers).end('Not found');
    }
  });
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const port = Number(process.argv[2] || 8000);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Port must be 1–65535');
  const root = fileURLToPath(new URL('../', import.meta.url));
  createStaticServer(root).listen(port, '127.0.0.1', () => {
    console.log(`Open http://127.0.0.1:${port}/examples/seqtk-container/`);
    console.log('Static files only. Computation and selected data remain in the browser.');
  });
}
