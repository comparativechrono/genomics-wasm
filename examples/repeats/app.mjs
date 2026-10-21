import { RepeatRunner, REPEAT_VERSIONS } from '../../runtime/repeats.mjs';
import { workspacePath } from '../../runtime/index.mjs';

const $ = id => document.getElementById(id);
const workspace = new Map(), generated = new Set();
const fileUrls = [], streamUrls = [], plotUrls = [];
const decoder = new TextDecoder();
let config, controller;

function busy(active) {
  $('controls').disabled = active;
  for (const id of ['dataset', 'load-data', 'upload', 'upload-folder', 'clear']) $(id).disabled = active;
  $('cancel').disabled = !active;
}
function revoke(urls) { urls.splice(0).forEach(url => URL.revokeObjectURL(url)); }
function link(container, urls, label, name, data) {
  const url = URL.createObjectURL(new Blob([data], { type: 'application/octet-stream' }));
  urls.push(url);
  const anchor = document.createElement('a');
  anchor.className = 'download'; anchor.href = url; anchor.download = name.split('/').at(-1); anchor.textContent = label;
  $(container).append(anchor);
}
function preview(data) {
  if (!data?.length) return '(empty)';
  if ((data[0] === 0x1f && data[1] === 0x8b) || data.subarray(0, 8192).includes(0)) return `Compressed or binary file (${data.length.toLocaleString()} bytes). Use the download link.`;
  return decoder.decode(data.subarray(0, 100000)) + (data.length > 100000 ? '\n\n[Preview truncated. Download the full file.]' : '');
}
function refreshFiles() {
  $('files').replaceChildren(...[...workspace].map(([path, data]) => {
    const item = document.createElement('li'); item.textContent = `${path.slice(6)} (${data.length.toLocaleString()} bytes)`; return item;
  }));
  if (!workspace.size) $('files').textContent = 'No files loaded.';
}
function showResult() {
  const path = $('result-file').value, data = workspace.get(path);
  $('preview').textContent = data ? preview(data) : 'No output yet.';
  revoke(plotUrls); $('plot').removeAttribute('src'); $('plot-panel').hidden = true;
  $('plot-status').textContent = '';
  if (data && path.toLowerCase().endsWith('.svg')) {
    const url = URL.createObjectURL(new Blob([data], { type: 'image/svg+xml' }));
    plotUrls.push(url); $('plot-panel').hidden = false;
    // SVG is loaded as an image. It never enters the page's DOM as active markup.
    $('plot').src = url;
  }
}
function scalePlot() {
  const image = $('plot');
  if (!image.naturalWidth) return;
  const width = $('plot-scale').value === 'fit'
    ? Math.min(image.naturalWidth, image.parentElement.clientWidth - 2)
    : image.naturalWidth * Number($('plot-scale').value) / 100;
  image.style.width = `${Math.max(1, width)}px`;
}
$('plot').addEventListener('load', scalePlot);
$('plot-scale').addEventListener('change', scalePlot);
window.addEventListener('resize', scalePlot);
$('plot').addEventListener('error', () => { $('plot-status').textContent = 'This SVG could not be displayed. Its original bytes remain available to download.'; });
function refreshResults(preferred) {
  const previous = preferred || $('result-file').value;
  const paths = [...generated].filter(path => workspace.has(path)).sort();
  $('result-file').replaceChildren(...paths.map(path => new Option(path.slice(6), path)));
  $('result-file').disabled = !paths.length;
  if (paths.includes(previous)) $('result-file').value = previous;
  showResult();
  revoke(fileUrls); $('downloads').replaceChildren();
  for (const path of paths) link('downloads', fileUrls, `Download ${path.slice(6)}`, path, workspace.get(path));
}
function stdinMode() {
  $('stdin-file-label').hidden = $('stdin-mode').value !== 'file';
  $('stdin-text-label').hidden = $('stdin-mode').value !== 'text';
}
function selectCommand() {
  const command = config.commands[$('command').value];
  $('tool').value = command.tool;
  $('arguments').value = JSON.stringify(command.args, null, 2);
  $('outputs').value = (command.outputs || []).join('\n');
  $('command-description').textContent = command.description;
  $('stdin-mode').value = command.stdinFile ? 'file' : command.stdin ? 'text' : 'none';
  $('stdin-file').value = command.stdinFile || ''; $('stdin-text').value = command.stdin || '';
  $('stdout-path').value = command.stdoutPath || ''; stdinMode();
}
async function loadExample() {
  if (controller) return;
  controller = new AbortController(); busy(true); $('status').textContent = 'Loading example data…';
  try {
    const dataset = config.datasets[$('dataset').value];
    const base = new URL(dataset.path, import.meta.url);
    const files = await Promise.all(dataset.files.map(async entry => {
      const source = typeof entry === 'string' ? entry : entry.source;
      const name = typeof entry === 'string' ? entry : entry.name;
      const response = await fetch(new URL(source, base), { signal: controller.signal });
      if (!response.ok) throw new Error(`Could not load ${name} (HTTP ${response.status}). See README.md for preparing the example data.`);
      return [workspacePath(name), new Uint8Array(await response.arrayBuffer())];
    }));
    workspace.clear(); generated.clear();
    for (const [path, data] of files) workspace.set(path, data);
    refreshFiles(); refreshResults(); $('status').textContent = 'Example data loaded. Choose a command and run it.';
  } catch (error) { $('status').textContent = error.name === 'AbortError' ? 'Loading cancelled.' : `Error: ${error.message}`; }
  finally { controller = null; busy(false); }
}
async function uploadFiles(event) {
  if (controller) return;
  busy(true); $('cancel').disabled = true;
  try {
    const files = await Promise.all([...event.target.files].map(async file => [workspacePath(file.webkitRelativePath || file.name), new Uint8Array(await file.arrayBuffer())]));
    for (const [path, data] of files) { workspace.set(path, data); generated.delete(path); }
    refreshFiles(); refreshResults(); $('status').textContent = 'Files loaded. Check the filenames in your arguments.';
  } catch (error) { $('status').textContent = `Error: ${error.message}`; }
  finally { event.target.value = ''; busy(false); }
}
$('dataset').addEventListener('change', () => { $('data-description').textContent = config.datasets[$('dataset').value].description; });
$('command').addEventListener('change', selectCommand);
$('tool').addEventListener('change', () => { $('command-description').textContent = 'Program changed. Review the arguments and output paths for this program.'; });
$('stdin-mode').addEventListener('change', stdinMode);
$('load-data').addEventListener('click', loadExample);
$('upload').addEventListener('change', uploadFiles);
$('upload-folder').addEventListener('change', uploadFiles);
$('result-file').addEventListener('change', showResult);
$('clear').addEventListener('click', () => { workspace.clear(); generated.clear(); refreshFiles(); refreshResults(); $('status').textContent = 'Files cleared.'; });
$('cancel').addEventListener('click', () => controller?.abort());
$('command-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (controller) return;
  let args, outputs, timeoutMs, stdin = new Uint8Array(), stdoutPath;
  try {
    args = JSON.parse($('arguments').value);
    if (!Array.isArray(args) || args.some(arg => typeof arg !== 'string' || arg.includes('\0'))) throw new Error('Arguments must be a JSON array of strings without NUL characters.');
    outputs = [...new Set($('outputs').value.split('\n').map(path => path.trim()).filter(Boolean).map(workspacePath))];
    timeoutMs = Number($('timeout').value) * 1000;
    if (!Number.isFinite(timeoutMs) || timeoutMs < 1000 || timeoutMs > 3600000) throw new Error('The time limit must be between 1 and 3600 seconds.');
    if ($('stdin-mode').value === 'file') {
      const path = workspacePath($('stdin-file').value.trim()); stdin = workspace.get(path);
      if (!stdin) throw new Error(`Standard input file is not loaded: ${path.slice(6)}`);
    } else if ($('stdin-mode').value === 'text') stdin = new TextEncoder().encode($('stdin-text').value);
    stdoutPath = $('stdout-path').value.trim() ? workspacePath($('stdout-path').value.trim()) : null;
    if (stdoutPath && outputs.includes(stdoutPath)) throw new Error('Use a different filename for saved standard output and collected output files.');
  } catch (error) { $('status').textContent = `Check command: ${error.message}`; return; }
  const isOutput = path => outputs.includes(path);
  controller = new AbortController(); busy(true);
  revoke(streamUrls); $('stream-downloads').replaceChildren(); $('stdout').textContent = ''; $('stderr').textContent = '';
  const tool = $('tool').value, version = REPEAT_VERSIONS[tool];
  $('status').textContent = `Running ${tool} ${version}. The first command also loads the program files…`;
  const started = performance.now();
  try {
    const runner = new RepeatRunner({ tool, baseUrl: new URL('../../dist/', import.meta.url) });
    const result = await runner.run(args, {
      files: [...workspace].filter(([path]) => !isOutput(path) && path !== stdoutPath).map(([path, data]) => ({ path, data })),
      outputs, stdin, signal: controller.signal, timeoutMs,
    });
    $('stdout').textContent = preview(result.stdout); $('stderr').textContent = preview(result.stderr);
    link('stream-downloads', streamUrls, 'Download standard output', `${tool}-${version}-stdout`, result.stdout);
    link('stream-downloads', streamUrls, 'Download standard error', `${tool}-${version}-stderr.txt`, result.stderr);
    if (result.exitCode === 0 && !result.missingOutputs.length) {
      for (const path of workspace.keys()) if (isOutput(path)) { workspace.delete(path); generated.delete(path); }
      for (const file of result.files) { workspace.set(file.path, file.data); generated.add(file.path); }
      if (stdoutPath) { workspace.set(stdoutPath, result.stdout); generated.add(stdoutPath); }
      refreshFiles(); refreshResults(stdoutPath || result.files[0]?.path);
    }
    const seconds = ((performance.now() - started) / 1000).toFixed(2);
    $('status').textContent = `Exited with status ${result.exitCode} after ${seconds} seconds.${result.missingOutputs.length ? ` Missing requested files: ${result.missingOutputs.join(', ')}.` : ''}`;
  } catch (error) { $('status').textContent = error.name === 'AbortError' ? 'Command cancelled.' : `${error.name}: ${error.message}`; }
  finally { controller = null; busy(false); }
});

try {
  const response = await fetch(new URL('config.json', import.meta.url));
  if (!response.ok) throw new Error(`Could not load config.json (HTTP ${response.status}).`);
  config = await response.json();
  if (Object.keys(config.tools || {}).some(tool => !Object.hasOwn(REPEAT_VERSIONS, tool))) throw new Error('An unsupported program is configured.');
  for (const [id, label] of Object.entries(config.tools)) $('tool').add(new Option(label, id));
  for (const [id, dataset] of Object.entries(config.datasets)) $('dataset').add(new Option(dataset.label, id));
  if (!$('tool').options.length || !$('dataset').options.length) throw new Error('No programs or datasets are configured.');
  $('data-description').textContent = config.datasets[$('dataset').value].description;
  for (const [id, command] of Object.entries(config.commands)) $('command').add(new Option(command.label, id));
  if (!$('command').options.length) throw new Error('No commands are configured.');
  selectCommand(); busy(false); $('status').textContent = 'Ready. Load files, then choose a command.';
} catch (error) { $('status').textContent = `Error: ${error.message}`; }
