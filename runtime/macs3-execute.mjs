/** Internal worker implementation; applications should use MACS3Runner. */
export async function executeMACS3({ moduleUrl, args, files, outputs,
  outputDirectories = [], stdin }) {
  if (!globalThis.crypto?.subtle) throw new Error('MACS3 checksum verification requires HTTPS or localhost.');
  const manifestUrl = new URL(moduleUrl);
  const manifest = JSON.parse(new TextDecoder().decode(await readAsset(manifestUrl)));
  validateMACS3Manifest(manifest);
  const base = new URL('./', manifestUrl);
  const pyBase = new URL(localPath(manifest.pyodide.path, true), base);
  const lock = JSON.parse(new TextDecoder().decode(await readAsset(new URL('pyodide-lock.json', pyBase))));
  validatePackageClosure(manifest, lock);
  // Validate every package before Pyodide can auto-load a scientific dependency.
  // Package locations are always in this local bundle, never a package index.
  for (const pkg of manifest.packages) await verifiedAsset(pkg, base);
  const entrypoint = await verifiedAsset(manifest.entrypoint, base);
  const { loadPyodide } = await import(new URL('pyodide.mjs', pyBase).href);
  const pyodide = await loadPyodide({
    indexURL: await loaderPath(pyBase),
    stdLibURL: await loaderPath(new URL('python_stdlib.zip', pyBase)),
    lockFileURL: await loaderPath(new URL('pyodide-lock.json', pyBase)),
    packageBaseUrl: await loaderPath(pyBase),
    packages: [],
  });
  if (pyodide.version !== manifest.pyodide.version) throw new Error('Pyodide version differs from the MACS3 bundle manifest');
  // WebKit can leave async side-module instantiation pending indefinitely.
  // Compile packages synchronously in this isolated worker, keeping Promise
  // and result contracts. Restore the native API before running MACS3.
  const instantiate = WebAssembly.instantiate;
  WebAssembly.instantiate = async function (source, imports, options) {
    if (source instanceof WebAssembly.Module) return new WebAssembly.Instance(source, imports);
    const module = new WebAssembly.Module(source, options);
    return { module, instance: new WebAssembly.Instance(module, imports) };
  };
  try {
    for (const pkg of manifest.packages) {
      const errors = [];
      const source = pkg.source === 'pyodide' ? pkg.name : await loaderPath(new URL(pkg.filename, base));
      await pyodide.loadPackage(source, {
        messageCallback: () => {}, errorCallback: message => errors.push(String(message)),
      });
      // loadPackage can log an installation failure while resolving its promise.
      const loaded = Object.keys(pyodide.loadedPackages).some(name => packageName(name) === packageName(pkg.name));
      if (errors.length || !loaded) throw new Error(`Could not install ${pkg.name}: ${errors.join('; ') || 'package was not loaded'}`);
    }
  } finally {
    WebAssembly.instantiate = instantiate;
  }
  const distributions = manifest.packages.filter(pkg => pkg.distribution !== null)
    .map(pkg => [pkg.distribution || pkg.name, pkg.version]);
  const expected = pyodide.toPy(distributions);
  try {
    pyodide.globals.set('_macs3_expected', expected);
    const installed = JSON.parse(pyodide.runPython(`
import importlib.metadata, json, sys
json.dumps({"python": ".".join(map(str, sys.version_info[:3])),
            "packages": {name: importlib.metadata.version(name)
                         for name, _ in _macs3_expected}})
`));
    if (installed.python !== manifest.pyodide.python ||
        distributions.some(([name, version]) => installed.packages[name] !== version)) {
      throw new Error('Installed Python or package versions differ from the MACS3 bundle manifest');
    }
  } finally { expected.destroy(); }

  const { FS } = pyodide;
  FS.mkdirTree('/opt/macs3');
  FS.writeFile('/opt/macs3/macs3', entrypoint);
  FS.mkdirTree('/work');
  FS.chdir('/work');
  for (const file of files) {
    FS.mkdirTree(file.path.slice(0, file.path.lastIndexOf('/')));
    FS.writeFile(file.path, file.data);
  }
  const stdout = new Bytes(), stderr = new Bytes();
  let inputOffset = 0;
  pyodide.setStdin({ isatty: false, read(buffer) {
    const count = Math.min(buffer.length, stdin.length - inputOffset);
    buffer.set(stdin.subarray(inputOffset, inputOffset + count));
    inputOffset += count;
    return count;
  } });
  pyodide.setStdout({ isatty: false, write: buffer => stdout.write(buffer) });
  pyodide.setStderr({ isatty: false, write: buffer => stderr.write(buffer) });
  const pythonArgs = pyodide.toPy(args);
  let exitCode;
  try {
    pyodide.globals.set('_macs3_args', pythonArgs);
    exitCode = pyodide.runPython(`
import logging, runpy, traceback
# MACS3 installs logging handlers while importing command validators. The stream
# redirection is set first so all command logs are returned to the caller.
def _run_macs3():
    sys.argv = ["macs3", *_macs3_args]
    try:
        runpy.run_path("/opt/macs3/macs3", run_name="__main__")
        return 0
    except SystemExit as error:
        if error.code is None:
            return 0
        if isinstance(error.code, int):
            return int(error.code)
        print(error.code, file=sys.stderr)
        return 1
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return 1
    finally:
        logging.shutdown()
        sys.stdout.flush()
        sys.stderr.flush()
_run_macs3()
`);
  } finally { pythonArgs.destroy(); }
  if (!Number.isInteger(exitCode)) throw new Error('MACS3 returned an invalid exit status');
  const resultFiles = [], missingOutputs = [], collected = new Set();
  const collectFile = path => {
    if (collected.has(path)) return;
    if (!FS.isFile(FS.lstat(path).mode)) throw new Error(`Output is not a regular file: ${path}`);
    resultFiles.push({ path, data: FS.readFile(path) });
    collected.add(path);
  };
  const collectDirectory = path => {
    if (!FS.isDir(FS.lstat(path).mode)) throw new Error(`Output directory is not a directory: ${path}`);
    for (const name of FS.readdir(path).filter(name => name !== '.' && name !== '..').sort()) {
      const child = `${path}/${name}`;
      if (FS.isDir(FS.lstat(child).mode)) collectDirectory(child);
      else collectFile(child);
    }
  };
  for (const path of outputs) {
    if (!FS.analyzePath(path).exists) missingOutputs.push(path);
    else collectFile(path);
  }
  for (const path of outputDirectories) {
    if (!FS.analyzePath(path).exists) missingOutputs.push(path);
    else collectDirectory(path);
  }
  return { exitCode, stdout: stdout.finish(), stderr: stderr.finish(), files: resultFiles, missingOutputs };
}

/** Validate a bundle before importing or fetching executable package code. */
export function validateMACS3Manifest(manifest) {
  if (!manifest || manifest.schema !== 1 || manifest.tool !== 'macs3' ||
      typeof manifest.version !== 'string' || !manifest.version ||
      typeof manifest.pyodide?.version !== 'string' ||
      typeof manifest.pyodide?.python !== 'string' || !Array.isArray(manifest.packages)) {
    throw new Error('Unsupported MACS3 bundle manifest');
  }
  localPath(manifest.pyodide.path, true);
  asset(manifest.entrypoint);
  const names = new Set();
  for (const pkg of manifest.packages) {
    asset(pkg);
    if (typeof pkg.name !== 'string' || !/^[a-zA-Z0-9][a-zA-Z0-9._-]*$/.test(pkg.name) ||
        typeof pkg.version !== 'string' || !pkg.version ||
        ![undefined, 'wheel', 'pyodide'].includes(pkg.source) ||
        ![undefined, null].includes(pkg.distribution) && (typeof pkg.distribution !== 'string' || !pkg.distribution)) {
      throw new Error('Invalid MACS3 package declaration');
    }
    const name = packageName(pkg.name);
    if (names.has(name)) throw new Error(`Duplicate MACS3 dependency: ${name}`);
    names.add(name);
  }
  for (const name of ['numpy', 'scipy', 'scikit-learn', 'hmmlearn', 'pandas', 'anndata', 'macs3']) {
    if (!names.has(name)) throw new Error(`Missing MACS3 dependency: ${name}`);
  }
  const main = manifest.packages.find(pkg => packageName(pkg.name) === 'macs3');
  if (main.version !== manifest.version || main.distribution === null) throw new Error('MACS3 package version differs from the bundle manifest');
  return manifest;
}

function validatePackageClosure(manifest, lock) {
  const declared = new Set(manifest.packages.filter(pkg => pkg.source === 'pyodide').map(pkg => packageName(pkg.name)));
  for (const pkg of manifest.packages.filter(pkg => pkg.source === 'pyodide')) {
    const entry = lock.packages?.[pkg.name];
    if (!entry || entry.version !== pkg.version || entry.sha256 !== pkg.sha256 ||
        localPath(pkg.filename) !== localPath(manifest.pyodide.path, true) + localPath(entry.file_name)) {
      throw new Error(`Pyodide catalogue differs from the bundle manifest for ${pkg.name}`);
    }
    for (const dependency of entry.depends || []) {
      if (!declared.has(packageName(dependency))) throw new Error(`Missing bundled Pyodide dependency: ${dependency}`);
    }
  }
}

function asset(value) {
  if (!value || !/^[a-f0-9]{64}$/.test(value.sha256)) throw new Error('Bundle assets require SHA-256 checksums');
  localPath(value.filename);
}
function packageName(name) { return name.toLowerCase().replace(/[-_.]+/g, '-'); }
function localPath(path, directory = false) {
  if (typeof path !== 'string') throw new Error('Bundle asset paths must be strings');
  const value = directory && path.endsWith('/') ? path.slice(0, -1) : path;
  if (!value || value.startsWith('/') || /[\u0000-\u0020\u007f\\:#?%]/.test(value) ||
      value.split('/').some(part => !part || part === '.' || part === '..')) {
    throw new Error(`Invalid bundle asset path: ${path}`);
  }
  return directory ? `${value}/` : value;
}
async function verifiedAsset(asset, base) {
  const data = await readAsset(new URL(asset.filename, base));
  const digest = await crypto.subtle.digest('SHA-256', data);
  const actual = [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, '0')).join('');
  if (actual !== asset.sha256) throw new Error(`Checksum mismatch for ${asset.filename}`);
  return data;
}
async function readAsset(url) {
  if (url.protocol === 'file:') {
    const { readFile } = await import('node:fs/promises');
    return new Uint8Array(await readFile(url));
  }
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Cannot load ${url.pathname}: HTTP ${response.status}`);
  return new Uint8Array(await response.arrayBuffer());
}
async function loaderPath(url) {
  if (url.protocol === 'file:') {
    const { fileURLToPath } = await import('node:url');
    return fileURLToPath(url);
  }
  return url.href;
}
class Bytes {
  chunks = [];
  length = 0;
  write(buffer) {
    // Pyodide reuses its stream buffer after this callback returns.
    this.chunks.push(buffer.slice());
    this.length += buffer.length;
    return buffer.length;
  }
  finish() {
    const result = new Uint8Array(this.length);
    let offset = 0;
    for (const chunk of this.chunks) { result.set(chunk, offset); offset += chunk.length; }
    return result;
  }
}
