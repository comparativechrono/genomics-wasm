import { workspacePath } from '../../runtime/index.mjs';

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
  for (const id of ['dataset', 'load-data', 'upload', 'upload-folder', 'clear']) $(id).disabled = active;
  $('cancel').disabled = !active;
}

function selectCommand() {
  const command = config.tools[$('tool').value].commands[$('command').value];
  $('arguments').value = JSON.stringify(command.args, null, 2);
  $('outputs').value = command.outputs.join('\n');
  $('output-directories').value = command.outputDirectories.join('\n');
  $('command-description').textContent = command.description;
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
  if ((bytes[0] === 0x1f && bytes[1] === 0x8b) || bytes.subarray(0, 8192).includes(0)) {
    return `Compressed or binary file (${bytes.length.toLocaleString()} bytes). Use the download link.`;
  }
  const limit = 100000;
  return text.decode(bytes.subarray(0, limit)) + (bytes.length > limit ? '\n\n[Preview truncated. Download the full file.]' : '');
}

function refreshResults(preferred) {
  const previous = preferred || $('result-file').value;
  const paths = [...generated].filter(path => workspace.has(path)).sort();
  $('result-file').replaceChildren(...paths.map(path => new Option(path.slice(6), path)));
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
    const dataset = config.datasets[$('dataset').value];
    const base = new URL(dataset.path, import.meta.url);
    const files = await Promise.all(dataset.files.map(async name => {
      const response = await fetch(new URL(name, base), { signal: controller.signal });
      if (!response.ok) throw new Error(`Could not load ${name} (HTTP ${response.status}). See README.md for preparing the example data.`);
      return [workspacePath(name), new Uint8Array(await response.arrayBuffer())];
    }));
    workspace.clear(); generated.clear();
    for (const [path, data] of files) workspace.set(path, data);
    refreshFiles(); refreshResults();
    $('status').textContent = 'Example data loaded. Choose a command and run it.';
  } catch (error) {
    $('status').textContent = error.name === 'AbortError' ? 'Loading cancelled.' : `Error: ${error.message}`;
  } finally { controller = null; busy(false); }
}

$('dataset').addEventListener('change', () => { $('data-description').textContent = config.datasets[$('dataset').value].description; });
function selectTool() {
  const tool = config.tools[$('tool').value];
  $('command').replaceChildren(...Object.entries(tool.commands).map(([id, command]) => new Option(command.label, id)));
  $('tool-description').textContent = tool.description;
  selectCommand();
}
$('tool').addEventListener('change', selectTool);
$('command').addEventListener('change', selectCommand);
$('load-data').addEventListener('click', loadExample);
$('result-file').addEventListener('change', () => { $('preview').textContent = preview(workspace.get($('result-file').value)); });
$('clear').addEventListener('click', () => {
  workspace.clear(); generated.clear(); refreshFiles(); refreshResults();
  $('status').textContent = 'Files cleared.';
});
$('cancel').addEventListener('click', () => controller?.abort());
async function uploadFiles(event) {
  if (controller) return;
  busy(true);
  $('cancel').disabled = true;
  try {
    const files = await Promise.all([...event.target.files].map(async file => [workspacePath(file.webkitRelativePath || file.name), new Uint8Array(await file.arrayBuffer())]));
    for (const [path, data] of files) { workspace.set(path, data); generated.delete(path); }
    refreshFiles(); refreshResults();
    $('status').textContent = 'Files loaded. Check the filenames in your arguments.';
  } catch (error) { $('status').textContent = `Error: ${error.message}`; }
  finally { event.target.value = ''; busy(false); }
}
$('upload').addEventListener('change', uploadFiles);
$('upload-folder').addEventListener('change', uploadFiles);

$('command-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (controller) return;
  let args, outputs, outputDirectories, timeoutMs;
  try {
    args = JSON.parse($('arguments').value);
    if (!Array.isArray(args) || args.some(arg => typeof arg !== 'string')) throw new Error('Arguments must be a JSON array of strings.');
    outputs = [...new Set($('outputs').value.split('\n').map(path => path.trim()).filter(Boolean).map(workspacePath))];
    outputDirectories = [...new Set($('output-directories').value.split('\n').map(path => path.trim()).filter(Boolean).map(workspacePath))];
    timeoutMs = Number($('timeout').value) * 1000;
    if (!Number.isFinite(timeoutMs) || timeoutMs < 1000 || timeoutMs > 3600000) throw new Error('The time limit must be between 1 and 3600 seconds.');
  } catch (error) { $('status').textContent = `Check command: ${error.message}`; return; }
  const isOutput = path => outputs.includes(path) || outputDirectories.some(output => path === output || path.startsWith(`${output}/`));
  controller = new AbortController();
  busy(true);
  revoke(streamUrls);
  $('stream-downloads').replaceChildren();
  $('stdout').textContent = '';
  $('stderr').textContent = '';
  const tool = $('tool').value;
  $('status').textContent = `Running ${config.tools[tool].label}…`;
  const started = performance.now();
  try {
    const options = { baseUrl: new URL(`../../dist/${tool}/`, import.meta.url) };
    const runner = tool === 'kraken2'
      ? new (await import('../../runtime/kraken2.mjs')).Kraken2Runner(options)
      : new (await import('../../runtime/bracken.mjs')).BrackenRunner(options);
    const result = await runner.run(args, {
      // Explicit outputs are regenerated (including the database build preset).
      // A collected directory can also contain required inputs such as the
      // reference library and taxonomy, so keep those files mounted.
      files: [...workspace].filter(([path]) => !outputs.includes(path)).map(([path, data]) => ({ path, data })),
      outputs, outputDirectories, signal: controller.signal, timeoutMs,
    });
    $('stdout').textContent = preview(result.stdout);
    $('stderr').textContent = preview(result.stderr);
    link('stream-downloads', streamUrls, 'Download standard output', `${tool}-stdout.txt`, result.stdout);
    link('stream-downloads', streamUrls, 'Download standard error', `${tool}-stderr.txt`, result.stderr);
    if (result.exitCode === 0 && !result.missingOutputs.length) {
      for (const path of workspace.keys()) if (isOutput(path)) { workspace.delete(path); generated.delete(path); }
      for (const file of result.files) { workspace.set(file.path, file.data); generated.add(file.path); }
      refreshFiles(); refreshResults(result.files.find(file => /\.(tsv|report)$/.test(file.path))?.path);
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
  $('program-summary').textContent = `Run ${Object.values(config.tools).map(tool => tool.label).join(' and ')} on sequencing reads.`;
  for (const [id, tool] of Object.entries(config.tools)) $('tool').add(new Option(tool.label, id));
  for (const [id, dataset] of Object.entries(config.datasets)) $('dataset').add(new Option(dataset.label, id));
  if (!$('tool').options.length || !$('dataset').options.length) throw new Error('No commands or datasets are configured.');
  $('data-description').textContent = config.datasets[$('dataset').value].description;
  selectTool(); busy(false);
  $('status').textContent = 'Ready. Load files, then choose a command.';
} catch (error) { $('status').textContent = `Error: ${error.message}`; }
