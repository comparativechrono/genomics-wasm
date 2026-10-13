import { workspacePath } from '../../runtime/index.mjs';

const $ = id => document.getElementById(id);
const workspace = new Map();
const generated = new Set();
const fileUrls = [], streamUrls = [];
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
  const limit = 100000;
  return decoder.decode(data.subarray(0, limit)) + (data.length > limit ? '\n\n[Preview truncated. Download the full file.]' : '');
}
function refreshFiles() {
  $('files').replaceChildren(...[...workspace].map(([path, data]) => {
    const item = document.createElement('li'); item.textContent = `${path.slice(6)} (${data.length.toLocaleString()} bytes)`; return item;
  }));
  if (!workspace.size) $('files').textContent = 'No files loaded.';
}
function refreshResults(preferred) {
  const previous = preferred || $('result-file').value;
  const paths = [...generated].filter(path => workspace.has(path)).sort();
  $('result-file').replaceChildren(...paths.map(path => new Option(path.slice(6), path)));
  $('result-file').disabled = !paths.length;
  if (paths.includes(previous)) $('result-file').value = previous;
  $('preview').textContent = paths.length ? preview(workspace.get($('result-file').value)) : 'No output yet.';
  revoke(fileUrls); $('downloads').replaceChildren();
  for (const path of paths) link('downloads', fileUrls, `Download ${path.slice(6)}`, path, workspace.get(path));
}
function stdinMode() {
  $('stdin-file-label').hidden = $('stdin-mode').value !== 'file';
  $('stdin-text-label').hidden = $('stdin-mode').value !== 'text';
}
function selectCommand() {
  const command = config.commands[$('command').value];
  $('program').value = command.program;
  $('arguments').value = JSON.stringify(command.args, null, 2);
  $('outputs').value = command.outputs.join('\n');
  $('output-directories').value = command.outputDirectories.join('\n');
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
    const files = await Promise.all(dataset.files.map(async name => {
      const response = await fetch(new URL(name, base), { signal: controller.signal });
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
$('stdin-mode').addEventListener('change', stdinMode);
$('load-data').addEventListener('click', loadExample);
$('upload').addEventListener('change', uploadFiles);
$('upload-folder').addEventListener('change', uploadFiles);
$('result-file').addEventListener('change', () => { $('preview').textContent = preview(workspace.get($('result-file').value)); });
$('clear').addEventListener('click', () => {
  workspace.clear(); generated.clear(); refreshFiles(); refreshResults(); $('status').textContent = 'Files cleared.';
});
$('cancel').addEventListener('click', () => controller?.abort());
$('command-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (controller) return;
  let args, outputs, outputDirectories, timeoutMs, stdin = new Uint8Array(), stdoutPath;
  try {
    args = JSON.parse($('arguments').value);
    if (!Array.isArray(args) || args.some(arg => typeof arg !== 'string')) throw new Error('Arguments must be a JSON array of strings.');
    outputs = [...new Set($('outputs').value.split('\n').map(path => path.trim()).filter(Boolean).map(workspacePath))];
    outputDirectories = [...new Set($('output-directories').value.split('\n').map(path => path.trim()).filter(Boolean).map(workspacePath))];
    timeoutMs = Number($('timeout').value) * 1000;
    if (!Number.isFinite(timeoutMs) || timeoutMs < 1000 || timeoutMs > 3600000) throw new Error('The time limit must be between 1 and 3600 seconds.');
    if ($('stdin-mode').value === 'file') {
      const path = workspacePath($('stdin-file').value.trim()); stdin = workspace.get(path);
      if (!stdin) throw new Error(`Standard input file is not loaded: ${path.slice(6)}`);
    } else if ($('stdin-mode').value === 'text') stdin = new TextEncoder().encode($('stdin-text').value);
    stdoutPath = $('stdout-path').value.trim() ? workspacePath($('stdout-path').value.trim()) : null;
    if (stdoutPath && outputs.includes(stdoutPath)) throw new Error('Use a different filename for standard output and a requested output file.');
  } catch (error) { $('status').textContent = `Check command: ${error.message}`; return; }
  const isOutput = path => outputs.includes(path) || outputDirectories.some(output => path === output || path.startsWith(`${output}/`));
  controller = new AbortController(); busy(true);
  revoke(streamUrls); $('stream-downloads').replaceChildren(); $('stdout').textContent = ''; $('stderr').textContent = '';
  const program = $('program').value;
  $('status').textContent = `Running ${config.label}. The first command also loads the program files…`;
  const started = performance.now();
  try {
    const runner = new (await import('../../runtime/primer3.mjs')).Primer3Runner({ program, baseUrl: new URL('../../dist/primer3/', import.meta.url) });
    const result = await runner.run(args, {
      files: [...workspace].filter(([path]) => !outputs.includes(path) && path !== stdoutPath).map(([path, data]) => ({ path, data })),
      outputs, outputDirectories, stdin, signal: controller.signal, timeoutMs,
    });
    $('stdout').textContent = preview(result.stdout); $('stderr').textContent = preview(result.stderr);
    link('stream-downloads', streamUrls, 'Download standard output', `${program}-stdout.txt`, result.stdout);
    link('stream-downloads', streamUrls, 'Download standard error', `${program}-stderr.txt`, result.stderr);
    if (result.exitCode === 0 && !result.missingOutputs.length) {
      for (const path of workspace.keys()) if (isOutput(path)) { workspace.delete(path); generated.delete(path); }
      for (const file of result.files) { workspace.set(file.path, file.data); generated.add(file.path); }
      if (stdoutPath) { workspace.set(stdoutPath, result.stdout); generated.add(stdoutPath); }
      refreshFiles(); refreshResults(stdoutPath || result.files.find(file => /\.(narrowPeak|broadPeak|bdg|out|txt)$/.test(file.path))?.path);
    }
    const seconds = ((performance.now() - started) / 1000).toFixed(2);
    $('status').textContent = `Exited with status ${result.exitCode} after ${seconds} seconds.${result.missingOutputs.length ? ` Missing requested files: ${result.missingOutputs.join(', ')}.` : ''}`;
    if (program === 'primer3_core' && /(?:^|\n)PRIMER_ERROR=/.test(decoder.decode(result.stdout))) $('status').textContent += ' Primer3 reported an input error; see the output.';
  } catch (error) { $('status').textContent = error.name === 'AbortError' ? 'Command cancelled.' : `${error.name}: ${error.message}`; }
  finally { controller = null; busy(false); }
});

try {
  const response = await fetch(new URL('config.json', import.meta.url));
  if (!response.ok) throw new Error(`Could not load config.json (HTTP ${response.status}).`);
  config = await response.json();
  for (const [id, label] of Object.entries(config.programs)) $('program').add(new Option(label, id));
  for (const [id, command] of Object.entries(config.commands)) $('command').add(new Option(command.label, id));
  for (const [id, dataset] of Object.entries(config.datasets)) $('dataset').add(new Option(dataset.label, id));
  if (!$('command').options.length || !$('dataset').options.length) throw new Error('No commands or datasets are configured.');
  $('data-description').textContent = config.datasets[$('dataset').value].description;
  selectCommand(); busy(false); $('status').textContent = 'Ready. Load files, then choose a command.';
} catch (error) { $('status').textContent = `Error: ${error.message}`; }
