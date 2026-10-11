/** Internal worker implementation; callers should use CutadaptRunner. */
export async function executeCutadapt({ moduleUrl, args, files, outputs,
  outputDirectories = [], stdin }) {
  const manifestUrl = new URL(moduleUrl);
  const manifest = JSON.parse(new TextDecoder().decode(await readAsset(manifestUrl)));
  if (manifest.schema !== 1 || manifest.tool !== 'cutadapt' ||
      !manifest.pyodide?.path || !Array.isArray(manifest.packages)) {
    throw new Error('Unsupported Cutadapt bundle manifest');
  }
  const base = new URL('./', manifestUrl);
  const pyBase = new URL(localPath(manifest.pyodide.path, true), base);
  const { loadPyodide } = await import(new URL('pyodide.mjs', pyBase).href);
  const pyodide = await loadPyodide({
    indexURL: await loaderPath(pyBase),
    stdLibURL: await loaderPath(new URL('python_stdlib.zip', pyBase)),
    lockFileURL: await loaderPath(new URL('pyodide-lock.json', pyBase)),
    packageBaseUrl: await loaderPath(pyBase),
    packages: [],
  });
  if (pyodide.version !== manifest.pyodide.version) throw new Error('Pyodide version differs from the bundle manifest');
  const expectedNames = ['xopen', 'dnaio', 'cutadapt'];
  if (JSON.stringify(manifest.packages.map(pkg => pkg.name)) !== JSON.stringify(expectedNames)) {
    throw new Error('Cutadapt dependencies must be installed in the declared order');
  }
  for (const pkg of manifest.packages) {
    const wheel = new URL(localPath(pkg.filename), base);
    const data = await readAsset(wheel);
    if (!/^[a-f0-9]{64}$/.test(pkg.sha256) || await sha256(data) !== pkg.sha256) {
      throw new Error(`Checksum mismatch for ${pkg.filename}`);
    }
    const errors = [];
    // Custom wheels have no dependency entries in Pyodide's catalogue. Install
    // in the pinned order, without asking a remote package index for anything.
    await pyodide.loadPackage(await loaderPath(wheel), {
      messageCallback: () => {}, errorCallback: message => errors.push(String(message)),
    });
    // loadPackage logs some failures and resolves, so awaiting it is not enough.
    if (errors.length || !Object.hasOwn(pyodide.loadedPackages, pkg.name)) {
      throw new Error(`Could not install ${pkg.name}: ${errors.join('; ') || 'package was not loaded'}`);
    }
  }
  const installed = JSON.parse(pyodide.runPython(`
import importlib.metadata, json, sys
json.dumps({"python": ".".join(map(str, sys.version_info[:3])),
            "packages": {name: importlib.metadata.version(name)
                         for name in ("xopen", "dnaio", "cutadapt")}})
`));
  if (installed.python !== manifest.pyodide.python ||
      manifest.packages.some(pkg => installed.packages[pkg.name] !== pkg.version) ||
      installed.packages.cutadapt !== manifest.version) {
    throw new Error('Installed Python or package versions differ from the bundle manifest');
  }
  const { FS } = pyodide;
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
    pyodide.globals.set('_cutadapt_args', pythonArgs);
    exitCode = pyodide.runPython(`
from cutadapt.cli import main as _cutadapt_main
def _run_cutadapt():
    sys.argv = ["cutadapt", *_cutadapt_args]
    try:
        _cutadapt_main(list(_cutadapt_args))
        return 0
    except SystemExit as error:
        if error.code is None:
            return 0
        if isinstance(error.code, int):
            return error.code
        print(error.code, file=sys.stderr)
        return 1
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
_run_cutadapt()
`);
  } finally { pythonArgs.destroy(); }
  if (!Number.isInteger(exitCode)) throw new Error('Cutadapt returned an invalid exit status');
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

function localPath(path, directory = false) {
  if (typeof path !== 'string') throw new Error('Bundle asset paths must be strings');
  const value = directory && path.endsWith('/') ? path.slice(0, -1) : path;
  if (!value || value.startsWith('/') || /[\\:#?%]/.test(value) ||
      value.split('/').some(part => !part || part === '.' || part === '..')) {
    throw new Error(`Invalid bundle asset path: ${path}`);
  }
  return directory ? `${value}/` : value;
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

async function sha256(data) {
  const digest = await crypto.subtle.digest('SHA-256', data);
  return [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, '0')).join('');
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
