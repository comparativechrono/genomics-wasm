import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { MACS3Runner } from '../runtime/macs3.mjs';
import { validateMACS3Manifest } from '../runtime/macs3-execute.mjs';

const checksum = '0'.repeat(64);
function manifest() {
  return { schema: 1, tool: 'macs3', version: '3.0.5',
    pyodide: { version: '0.29.3', python: '3.14.2', path: 'pyodide/' },
    entrypoint: { filename: 'macs3.py', sha256: checksum },
    packages: ['numpy', 'scipy', 'scikit-learn', 'hmmlearn', 'pandas', 'anndata', 'macs3'].map(name => ({
      name, version: name === 'macs3' ? '3.0.5' : '1.0', filename: `wheels/${name}.whl`, sha256: checksum, source: 'wheel',
    })),
  };
}

// These checks cover bundle integrity and worker transport. Biological
// equivalence is tested separately against actual native and WASM programs.
test('MACS3 resolves bundle and worker URLs independently of caller cwd', () => {
  const runner = new MACS3Runner({ baseUrl: new URL('https://example.org/tools/macs3') });
  assert.equal(runner.moduleUrl, 'https://example.org/tools/macs3/manifest.json');
  assert.equal(runner.baseUrl, 'https://example.org/tools/macs3/');
  assert.match(runner.workerUrl, /\/runtime\/macs3-worker\.mjs$/);
});

test('MACS3 rejects workspace traversal, duplicate files and invalid arguments before startup', async () => {
  const runner = new MACS3Runner({ baseUrl: new URL('./nonexistent/', import.meta.url) });
  await assert.rejects(runner.run(['--help'], { files: [{ path: '../bad', data: '' }] }), /inside \/work/);
  await assert.rejects(runner.run(['--help'], { files: [{ path: 'a', data: '' }, { path: '/work/a', data: '' }] }), /unique/);
  await assert.rejects(runner.run(['callpeak', 10]), /array of strings/);
  await assert.rejects(runner.run(['--help'], { outputDirectories: ['/etc'] }), /inside \/work/);
});

test('a cancelled MACS3 request starts no worker or bundle download', async () => {
  const controller = new AbortController(); controller.abort();
  const runner = new MACS3Runner({ baseUrl: new URL('./nonexistent/', import.meta.url) });
  await assert.rejects(runner.run(['--help'], { signal: controller.signal }), { name: 'AbortError' });
});

test('manifest validates the scientific closure and rejects ambiguous dependencies', () => {
  assert.equal(validateMACS3Manifest(manifest()).version, '3.0.5');
  const incomplete = manifest(); incomplete.packages = incomplete.packages.filter(pkg => pkg.name !== 'hmmlearn');
  assert.throws(() => validateMACS3Manifest(incomplete), /Missing MACS3 dependency: hmmlearn/);
  const duplicate = manifest(); duplicate.packages.push({ ...duplicate.packages[2], name: 'scikit_learn' });
  assert.throws(() => validateMACS3Manifest(duplicate), /Duplicate MACS3 dependency/);
  const wrongVersion = manifest(); wrongVersion.packages.at(-1).version = '0.0';
  assert.throws(() => validateMACS3Manifest(wrongVersion), /version differs/);
});

test('manifest cannot redirect package or CLI assets outside the bundle', () => {
  for (const path of ['../foreign.whl', '/foreign.whl', 'https://example.org/foreign.whl', 'wheels/%2e%2e/foreign.whl', 'wheels/foreign.whl?q=1', 'wheels\\foreign.whl', '\t../foreign.whl', 'wheels/\n../foreign.whl']) {
    const value = manifest(); value.packages[0].filename = path;
    assert.throws(() => validateMACS3Manifest(value), /Invalid bundle asset path/);
  }
  const value = manifest(); value.pyodide.path = '../pyodide/';
  assert.throws(() => validateMACS3Manifest(value), /Invalid bundle asset path/);
  const missingHash = manifest(); delete missingHash.entrypoint.sha256;
  assert.throws(() => validateMACS3Manifest(missingHash), /SHA-256/);
});

test('worker reports damaged package bytes before importing a Python runtime', async () => {
  const directory = await mkdtemp(new URL('../.macs3-test-', import.meta.url));
  try {
    await mkdir(`${directory}/pyodide`); await mkdir(`${directory}/wheels`);
    await writeFile(`${directory}/manifest.json`, JSON.stringify(manifest()));
    await writeFile(`${directory}/pyodide/pyodide-lock.json`, JSON.stringify({ packages: {} }));
    await writeFile(`${directory}/wheels/numpy.whl`, 'damaged wheel');
    const runner = new MACS3Runner({ baseUrl: pathToFileURL(`${directory}/`) });
    await assert.rejects(runner.run(['--help']), /Checksum mismatch for wheels\/numpy.whl/);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('worker refuses undeclared transitive dependencies before Pyodide can fetch them', async () => {
  const directory = await mkdtemp(new URL('../.macs3-test-', import.meta.url));
  try {
    await mkdir(`${directory}/pyodide`);
    const value = manifest(); Object.assign(value.packages[0], { source: 'pyodide', filename: 'pyodide/numpy.whl' });
    await writeFile(`${directory}/manifest.json`, JSON.stringify(value));
    await writeFile(`${directory}/pyodide/pyodide-lock.json`, JSON.stringify({ packages: {
      numpy: { version: '1.0', file_name: 'numpy.whl', sha256: checksum, depends: ['unbundled-library'] },
    } }));
    const runner = new MACS3Runner({ baseUrl: pathToFileURL(`${directory}/`) });
    await assert.rejects(runner.run(['--help']), /Missing bundled Pyodide dependency: unbundled-library/);
  } finally { await rm(directory, { recursive: true, force: true }); }
});
