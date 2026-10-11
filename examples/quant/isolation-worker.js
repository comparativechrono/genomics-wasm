// Same-origin static hosting support for this example's threaded WebAssembly.
// Registered only after the user chooses to enable it, scoped to ./examples/quant/.
// No requests or responses are cached, and other example pages are not controlled.
self.addEventListener('install', event => event.waitUntil(self.skipWaiting()));
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
const assetPrefix = new URL('__assets__/', self.registration.scope).pathname;
const assets = new Set([
  'runtime/index.mjs', 'runtime/worker.mjs', 'runtime/execute.mjs',
  'dist/kallisto/kallisto.mjs', 'dist/kallisto/kallisto.wasm',
  'dist/salmon/salmon.mjs', 'dist/salmon/salmon.wasm',
  'dist/salmon/salmon.worker.mjs', 'dist/salmon/salmon.worker.js',
]);
self.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin) return;
  // The browser can issue this request combination while checking its cache;
  // fetch() rejects it unless the mode is same-origin.
  if (request.cache === 'only-if-cached' && request.mode !== 'same-origin') return;
  event.respondWith((async () => {
    let response;
    if (url.pathname.startsWith(assetPrefix)) {
      const asset = url.pathname.slice(assetPrefix.length);
      // Exact names exclude traversal, encoded path separators and arbitrary
      // URLs. Only the existing same-origin program/runtime files are mapped.
      if (!assets.has(asset)) return new Response('Unknown tool asset', { status: 404 });
      const source = new URL(`../../${asset}`, self.registration.scope);
      source.search = url.search;
      response = await fetch(source, { mode: 'same-origin', credentials: 'same-origin', cache: request.cache });
    } else {
      response = await fetch(request);
    }
    if (response.status === 0) return response;
    const headers = new Headers(response.headers);
    headers.set('Cross-Origin-Opener-Policy', 'same-origin');
    headers.set('Cross-Origin-Embedder-Policy', 'require-corp');
    headers.set('Cross-Origin-Resource-Policy', 'same-origin');
    return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
  })());
});
