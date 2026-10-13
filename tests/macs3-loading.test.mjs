import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { executeMACS3 } from '../runtime/macs3-execute.mjs';

const names = ['numpy', 'scipy', 'scikit-learn', 'hmmlearn', 'pandas', 'anndata', 'macs3'];
const digest = bytes => createHash('sha256').update(bytes).digest('hex');
const stateKey = '__macs3_package_loading_test__';

// Real, small Wasm module that re-exports its imported memory and table.
// Offset views below contain invalid surrounding bytes, detecting lost bounds.
const name = text => [text.length, ...new TextEncoder().encode(text)];
const imports = [2, ...name('env'), ...name('memory'), 2, 0, 1,
  ...name('env'), ...name('table'), 1, 0x70, 0, 1];
const exports = [2, ...name('memory'), 2, 0, ...name('table'), 1, 0];
const wasm = Uint8Array.from([0, 97, 115, 109, 1, 0, 0, 0,
  2, imports.length, ...imports, 7, exports.length, ...exports]);

async function fakeBundle(t, state) {
  const directory = await mkdtemp(new URL('../.macs3-loading-test-', import.meta.url));
  t.after(() => rm(directory, { recursive: true, force: true }));
  await mkdir(`${directory}/pyodide`); await mkdir(`${directory}/wheels`);
  const bytes = new TextEncoder().encode('checksum-verified package fixture');
  const manifest = { schema: 1, tool: 'macs3', version: '3.0.5',
    pyodide: { version: '314.0.7', python: '3.14.2', path: 'pyodide/' },
    entrypoint: { filename: 'macs3.py', sha256: digest(bytes) },
    packages: names.map(name => ({ name, version: name === 'macs3' ? '3.0.5' : '1.0',
      filename: `wheels/${name}.whl`, sha256: digest(bytes), source: 'wheel' })),
  };
  await Promise.all([
    writeFile(`${directory}/manifest.json`, JSON.stringify(manifest)),
    writeFile(`${directory}/macs3.py`, bytes),
    writeFile(`${directory}/pyodide/pyodide-lock.json`, '{"packages":{}}'),
    ...manifest.packages.map(pkg => writeFile(`${directory}/${pkg.filename}`, bytes)),
  ]);
  state.manifest = manifest;
  globalThis[stateKey] = state;
  t.after(() => { delete globalThis[stateKey]; });
  // This stub exercises the real executor's package scope and failure paths.
  // Scientific equivalence is covered separately with the actual bundle.
  await writeFile(`${directory}/pyodide/pyodide.mjs`, `
export async function loadPyodide() {
  const state = globalThis[${JSON.stringify(stateKey)}];
  state.coreInstantiate = WebAssembly.instantiate;
  const loadedPackages = {};
  return {
    version: state.manifest.pyodide.version, loadedPackages,
    async loadPackage(source, options) {
      const name = source.split('/').at(-1).replace(/\\.whl$/, '');
      await state.loadPackage(name, options);
      loadedPackages[name] = source;
    },
    toPy() { return { destroy() {} }; },
    globals: { set() {} },
    runPython(source) {
      state.pythonInstantiate = WebAssembly.instantiate;
      return source.includes('importlib.metadata') ? JSON.stringify({
        python: state.manifest.pyodide.python,
        packages: Object.fromEntries(state.manifest.packages.map(pkg => [pkg.name, pkg.version])),
      }) : 0;
    },
    FS: { mkdirTree() {}, writeFile() {}, chdir() {} },
    setStdin() {}, setStdout() {}, setStderr() {},
  };
}
`);
  return { moduleUrl: pathToFileURL(`${directory}/manifest.json`).href,
    args: ['--version'], files: [], outputs: [], stdin: new Uint8Array() };
}

test('MACS3 side modules bypass async instantiation only during package installation', async t => {
  const original = WebAssembly.instantiate;
  const unavailableAsync = () => { throw new Error('asynchronous side-module loader was used'); };
  t.after(() => { WebAssembly.instantiate = original; });
  WebAssembly.instantiate = unavailableAsync;
  const memory = new WebAssembly.Memory({ initial: 1 });
  const table = new WebAssembly.Table({ initial: 1, element: 'anyfunc' });
  const importObject = { env: { memory, table } };
  const padded = new Uint8Array(wasm.length + 9).fill(255); padded.set(wasm, 4);
  const sources = [wasm.buffer, wasm, padded.subarray(4, 4 + wasm.length)];
  const visited = [];
  const state = { async loadPackage(name) {
    visited.push(name);
    assert.notEqual(WebAssembly.instantiate, unavailableAsync);
    if (name !== 'numpy') return;
    for (const source of sources) {
      const pending = WebAssembly.instantiate(source, importObject);
      assert.ok(pending instanceof Promise);
      const { module, instance } = await pending;
      assert.ok(module instanceof WebAssembly.Module);
      assert.equal(instance.exports.memory, memory);
      assert.equal(instance.exports.table, table);
      const fromModule = await WebAssembly.instantiate(module, importObject);
      assert.ok(fromModule instanceof WebAssembly.Instance);
      assert.equal(fromModule.exports.memory, memory);
      assert.equal(fromModule.exports.table, table);
    }
    let invalid;
    assert.doesNotThrow(() => { invalid = WebAssembly.instantiate(new Uint8Array([255])); });
    await assert.rejects(invalid, WebAssembly.CompileError);
    await assert.rejects(WebAssembly.instantiate(wasm, { env: { memory: {}, table } }), WebAssembly.LinkError);
  } };
  const result = await executeMACS3(await fakeBundle(t, state));
  assert.equal(result.exitCode, 0);
  assert.deepEqual(visited, names);
  assert.equal(state.coreInstantiate, unavailableAsync);
  assert.equal(state.pythonInstantiate, unavailableAsync);
  assert.equal(WebAssembly.instantiate, unavailableAsync);
});

test('MACS3 restores the original instantiator after package rejection or logged failure', async t => {
  for (const mode of ['reject', 'logged']) {
    await t.test(mode, async t => {
      const original = WebAssembly.instantiate;
      const sentinel = () => { throw new Error('original instantiator called'); };
      t.after(() => { WebAssembly.instantiate = original; });
      WebAssembly.instantiate = sentinel;
      const state = { async loadPackage(name, options) {
        assert.notEqual(WebAssembly.instantiate, sentinel);
        if (mode === 'reject') throw new Error('failed package fixture');
        options.errorCallback('failed package fixture');
      } };
      await assert.rejects(executeMACS3(await fakeBundle(t, state)), /failed package fixture/);
      assert.equal(state.coreInstantiate, sentinel);
      assert.equal(state.pythonInstantiate, undefined);
      assert.equal(WebAssembly.instantiate, sentinel);
    });
  }
});
