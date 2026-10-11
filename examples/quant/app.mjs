import { ToolRunner, workspacePath } from '../../runtime/index.mjs';

const $ = id => document.getElementById(id);
const workspace = new Map();
const generated = new Set();
const fileUrls = [];
const streamUrls = [];
const text = new TextDecoder();
let config;
let controller;

function busy(active) {
  $('controls').disabled = active;
  for (const id of ['dataset', 'load-data', 'upload', 'clear']) $(id).disabled = active;
  $('cancel').disabled = !active;
}

function selectCommand() {
  const tool = config.tools[$('tool').value];
  const command = tool.commands[$('command').value];
  $('arguments').value = JSON.stringify(command.args, null, 2);
  $('outputs').value = command.outputs.join('\n');
  $('output-directories').value = command.outputDirectories.join('\n');
  $('compatibility').textContent = tool.requiresIsolation
    ? (globalThis.crossOriginIsolated ? 'This page supports the workers required by Salmon.' : 'Salmon needs cross-origin isolation to run its worker threads. Enable it below, use the included local server, or configure your hosting headers.')
    : 'This build runs in one worker and can be served from an ordinary static website.';
  const canEnable = tool.requiresIsolation && !globalThis.crossOriginIsolated && globalThis.isSecureContext && 'serviceWorker' in navigator;
  $('enable-threads').hidden = !canEnable;
  $('isolation-hint').hidden = !canEnable;
}

function refreshFiles() {
  $('files').replaceChildren(...[...workspace].map(([path, data]) => {
    const item = document.createElement('li');
    item.textContent = `${path.slice(6)} (${data.length.toLocaleString()} bytes)`;
    return item;
  }));
  if (!workspace.size) $('files').textContent = 'No files loaded.';
}

function revoke(urls) { urls.splice(0).forEach(url => URL.revokeObjectURL(url)); }

function link(container, urls, label, name, bytes) {
  const url = URL.createObjectURL(new Blob([bytes], { type: 'application/octet-stream' }));
  urls.push(url);
  const anchor = document.createElement('a');
  anchor.className = 'download';
  anchor.href = url;
  anchor.download = name.split('/').at(-1);
  anchor.textContent = label;
  $(container).append(anchor);
}

function preview(bytes) {
  if (!bytes?.length) return '(empty)';
  if (bytes.subarray(0, 8192).includes(0)) return `Binary file (${bytes.length.toLocaleString()} bytes). Use the download link.`;
  const limit = 100000;
  return text.decode(bytes.subarray(0, limit)) + (bytes.length > limit ? '\n\n[Preview truncated. Download the full file.]' : '');
}

function refreshResults(preferred) {
  const previous = preferred || $('result-file').value;
  const paths = [...generated].filter(path => workspace.has(path)).sort();
  $('result-file').replaceChildren(...paths.map(path => {
    const option = document.createElement('option');
    option.value = path;
    option.textContent = path.slice(6);
    return option;
  }));
  $('result-file').disabled = !paths.length;
  if (paths.includes(previous)) $('result-file').value = previous;
  $('preview').textContent = paths.length ? preview(workspace.get($('result-file').value)) : 'No output yet.';
  revoke(fileUrls);
  $('downloads').replaceChildren();
  for (const path of paths) link('downloads', fileUrls, `Download ${path.slice(6)}`, path, workspace.get(path));
}

async function loadExample() {
  if (controller) return;
  controller = new AbortController();
  busy(true);
  $('status').textContent = 'Loading example data…';
  try {
    const base = new URL(config.datasets[$('dataset').value].path, import.meta.url);
    const files = await Promise.all(['transcripts.fa', 'reads_1.fastq', 'reads_2.fastq'].map(async name => {
      const response = await fetch(new URL(name, base), { signal: controller.signal });
      if (!response.ok) throw new Error(`Could not load ${name} (HTTP ${response.status}). Fetch the example data first; see README.md.`);
      return [workspacePath(name), new Uint8Array(await response.arrayBuffer())];
    }));
    workspace.clear();
    generated.clear();
    for (const [path, data] of files) workspace.set(path, data);
    refreshFiles();
    refreshResults();
    $('status').textContent = 'Example data loaded. Build an index before quantifying these reads.';
  } catch (error) {
    $('status').textContent = error.name === 'AbortError' ? 'Loading cancelled.' : `Error: ${error.message}`;
  } finally { controller = null; busy(false); }
}

$('tool').addEventListener('change', selectCommand);
$('command').addEventListener('change', selectCommand);
$('load-data').addEventListener('click', loadExample);
$('result-file').addEventListener('change', () => { $('preview').textContent = preview(workspace.get($('result-file').value)); });
$('clear').addEventListener('click', () => {
  workspace.clear(); generated.clear(); refreshFiles(); refreshResults();
  $('status').textContent = 'Files cleared.';
});
$('cancel').addEventListener('click', () => controller?.abort());
$('enable-threads').addEventListener('click', async () => {
  busy(true);
  $('cancel').disabled = true;
  $('status').textContent = 'Enabling threaded tools…';
  try {
    const scriptUrl = new URL('isolation-worker.js', import.meta.url).href;
    const scope = new URL('./', import.meta.url).href;
    let timer;
    const ready = await Promise.race([
      (async () => {
        await navigator.serviceWorker.register(scriptUrl, { scope });
        return navigator.serviceWorker.ready;
      })(),
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error('Service worker activation timed out. Use the included local server or set hosting headers.')), 30000);
      }),
    ]).finally(() => clearTimeout(timer));
    if (ready.scope !== scope || ready.active?.scriptURL !== scriptUrl) {
      throw new Error('A different service worker controls this page. Use the included local server or set hosting headers.');
    }
    // The activated worker handles this next navigation and supplies isolation
    // headers. There is deliberately no automatic reload loop if a browser
    // declines service-worker isolation.
    location.reload();
  } catch (error) {
    $('status').textContent = `Error: ${error.message}`;
    busy(false);
  }
});
$('upload').addEventListener('change', async event => {
  if (controller) return;
  busy(true);
  // File.arrayBuffer cannot be cancelled; no command can start during a read.
  $('cancel').disabled = true;
  try {
    const files = await Promise.all([...event.target.files].map(async file => [workspacePath(file.name), new Uint8Array(await file.arrayBuffer())]));
    for (const [path, data] of files) { workspace.set(path, data); generated.delete(path); }
    refreshFiles(); refreshResults();
    $('status').textContent = 'Files loaded. Check the filenames in your arguments.';
  } catch (error) { $('status').textContent = `Error: ${error.message}`; }
  finally { $('upload').value = ''; busy(false); }
});

$('command-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (controller) return;
  const tool = $('tool').value;
  if (config.tools[tool].requiresIsolation && !globalThis.crossOriginIsolated) {
    $('status').textContent = 'Salmon cannot start: this page needs cross-origin isolation. Enable threaded tools and reload, use examples/quant/serve.py, or set the hosting headers described in README.md.';
    return;
  }
  let args, outputs, outputDirectories, timeoutMs;
  try {
    args = JSON.parse($('arguments').value);
    if (!Array.isArray(args) || args.some(arg => typeof arg !== 'string')) throw new Error('Arguments must be a JSON array of strings.');
    outputs = [...new Set($('outputs').value.split('\n').map(path => path.trim()).filter(Boolean).map(workspacePath))];
    outputDirectories = [...new Set($('output-directories').value.split('\n').map(path => path.trim()).filter(Boolean).map(workspacePath))];
    timeoutMs = Number($('timeout').value) * 1000;
    if (!Number.isFinite(timeoutMs) || timeoutMs < 1000) throw new Error('The time limit must be at least one second.');
  } catch (error) { $('status').textContent = `Check command: ${error.message}`; return; }
  const isOutput = path => outputs.includes(path) || outputDirectories.some(output => path === output || path.startsWith(`${output}/`));
  controller = new AbortController();
  busy(true);
  revoke(streamUrls);
  $('stream-downloads').replaceChildren();
  $('stdout').textContent = '';
  $('stderr').textContent = '';
  $('status').textContent = `Running ${config.tools[tool].label}…`;
  const started = performance.now();
  try {
    // A worker is a separate client. Its entry URL must be inside the service
    // worker's scope to receive the isolation headers on plain static hosts.
    // Relative imports and Emscripten's pthread URLs then stay in that scope.
    const usesIsolationWorker = navigator.serviceWorker?.controller?.scriptURL === new URL('isolation-worker.js', import.meta.url).href;
    const assetBase = new URL(usesIsolationWorker ? '__assets__/' : '../../', import.meta.url);
    const runner = new ToolRunner({
      moduleUrl: new URL(`dist/${tool}/${tool}.mjs`, assetBase),
      workerUrl: new URL('runtime/worker.mjs', assetBase),
      waitForExit: config.tools[tool].waitForExit ?? false,
    });
    const result = await runner.run(args, {
      files: [...workspace].filter(([path]) => !isOutput(path)).map(([path, data]) => ({ path, data })),
      outputs, outputDirectories, signal: controller.signal, timeoutMs,
    });
    $('stdout').textContent = preview(result.stdout);
    $('stderr').textContent = preview(result.stderr);
    link('stream-downloads', streamUrls, 'Download standard output', `${tool}-stdout.txt`, result.stdout);
    link('stream-downloads', streamUrls, 'Download standard error', `${tool}-stderr.txt`, result.stderr);
    if (result.exitCode === 0 && !result.missingOutputs.length) {
      for (const path of workspace.keys()) if (isOutput(path)) { workspace.delete(path); generated.delete(path); }
      for (const file of result.files) { workspace.set(file.path, file.data); generated.add(file.path); }
      const table = result.files.find(file => /\/(abundance\.tsv|quant\.sf)$/.test(file.path));
      refreshFiles(); refreshResults(table?.path);
    }
    const seconds = ((performance.now() - started) / 1000).toFixed(2);
    $('status').textContent = `Exited with status ${result.exitCode} after ${seconds} seconds.${result.missingOutputs.length ? ` Missing requested files: ${result.missingOutputs.join(', ')}.` : ''}`;
  } catch (error) {
    $('status').textContent = error.name === 'AbortError' ? 'Command cancelled.' : `${error.name}: ${error.message}`;
  } finally { controller = null; busy(false); }
});

try {
  const response = await fetch(new URL('config.json', import.meta.url));
  if (!response.ok) throw new Error(`Could not load config.json (HTTP ${response.status}).`);
  config = await response.json();
  for (const [id, tool] of Object.entries(config.tools)) $('tool').add(new Option(tool.label, id));
  for (const [id, dataset] of Object.entries(config.datasets)) $('dataset').add(new Option(dataset.label, id));
  if (!$('tool').options.length) throw new Error('No compiled tools are configured.');
  selectCommand(); busy(false);
  $('status').textContent = 'Ready. Load files, then build an index.';
} catch (error) { $('status').textContent = `Error: ${error.message}`; }
