/** Internal worker implementation; callers should use BrackenRunner. */
export async function executeBracken({ moduleUrl, args, files, outputs,
  outputDirectories = [], stdin }) {
  const manifestUrl = new URL(moduleUrl);
  const manifest = JSON.parse(new TextDecoder().decode(await readAsset(manifestUrl)));
  if (manifest.schema !== 1 || manifest.tool !== 'bracken' ||
      !manifest.pyodide?.path || !manifest.scripts) {
    throw new Error('Unsupported Bracken bundle manifest');
  }
  const [scriptName, ...scriptArgs] = args;
  if (!['est_abundance.py', 'generate_kmer_distribution.py'].includes(scriptName)) {
    throw new Error('Unsupported Bracken Python entry point');
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
  const installed = pyodide.runPython('import sys; ".".join(map(str, sys.version_info[:3]))');
  if (installed !== manifest.pyodide.python) throw new Error('Python version differs from the bundle manifest');
  const script = manifest.scripts[scriptName];
  if (!script) throw new Error('Missing Bracken Python entry point');
  const scriptBytes = await readAsset(new URL(localPath(script.path), base));
  if (!/^[a-f0-9]{64}$/.test(script.sha256) || await sha256(scriptBytes) !== script.sha256) {
    throw new Error('Bracken Python source checksum mismatch');
  }
  const { FS } = pyodide;
  FS.mkdirTree('/opt/bracken');
  FS.writeFile('/opt/bracken/' + scriptName, scriptBytes);
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
  const pythonArgs = pyodide.toPy(scriptArgs);
  let exitCode;
  try {
    pyodide.globals.set('_bracken_args', pythonArgs);
    pyodide.globals.set('_bracken_script', '/opt/bracken/' + scriptName);
    exitCode = pyodide.runPython(`
import runpy, sys, traceback
def _run_bracken():
    sys.argv = [_bracken_script, *_bracken_args]
    try:
        runpy.run_path(_bracken_script, run_name="__main__")
        return 0
    except SystemExit as error:
        if error.code is None:
            return 0
        if isinstance(error.code, int):
            return error.code
        print(error.code, file=sys.stderr)
        return 1
    except Exception:
        traceback.print_exc()
        return 1
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
_run_bracken()
`);
  } finally { pythonArgs.destroy(); }
  if (!Number.isInteger(exitCode)) throw new Error('Bracken returned an invalid exit status');
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
