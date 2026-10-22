import { KingRunner, KING_VERSION } from '../../runtime/king.mjs';
import { workspacePath } from '../../runtime/index.mjs';

const $ = id => document.getElementById(id);
const inputs = new Map(), results = new Map(), urls = [];
const decoder = new TextDecoder();
let config, controller;

function busy(active) {
  $('controls').disabled = active;
  for (const id of ['load-data', 'upload', 'upload-folder', 'clear']) $(id).disabled = active;
  $('cancel').disabled = !active;
}
function resetResults() {
  results.clear(); urls.splice(0).forEach(url => URL.revokeObjectURL(url));
  for (const id of ['downloads', 'stream-downloads', 'result-file']) $(id).replaceChildren();
  $('result-file').disabled = true;
  for (const id of ['preview', 'stdout', 'stderr']) $(id).textContent = 'No output yet.';
}
function download(container, label, path, data) {
  const url = URL.createObjectURL(new Blob([data], { type: 'application/octet-stream' }));
  urls.push(url);
  const anchor = document.createElement('a');
  anchor.className = 'download'; anchor.href = url; anchor.download = path.split('/').at(-1); anchor.textContent = label;
  $(container).append(anchor);
}
function preview(bytes) {
  if (!bytes?.length) return '(empty)';
  if (bytes.subarray(0, 8192).includes(0) || (bytes[0] === 0x1f && bytes[1] === 0x8b)) return `Binary or compressed data (${bytes.length.toLocaleString()} bytes). Download the original file.`;
  return decoder.decode(bytes.subarray(0, 100000)) + (bytes.length > 100000 ? '\n[Preview truncated. Download the complete file.]' : '');
}
function refreshFiles() {
  $('files').replaceChildren(...[...inputs].map(([path, bytes]) => {
    const item = document.createElement('li'); item.textContent = `${path.slice(6)} (${bytes.length.toLocaleString()} bytes)`; return item;
  }));
  if (!inputs.size) { const item = document.createElement('li'); item.textContent = 'No files loaded.'; $('files').append(item); }
}
function showResult() { $('preview').textContent = results.has($('result-file').value) ? preview(results.get($('result-file').value)) : 'No output yet.'; }
function fillPreset() {
  const command = config.commands[$('command').value];
  const directory = $('output-directory').value.trim();
  $('arguments').value = JSON.stringify(['-b', $('bed-file').value.trim(), ...command.flags, '--prefix', `${directory}/king`], null, 2);
  $('command-description').textContent = command.description;
}
function chooseBed() {
  const paths = [...inputs.keys()].filter(path => path.toLowerCase().endsWith('.bed'));
  if (paths.length === 1) $('bed-file').value = paths[0].slice(6);
  fillPreset();
}
async function loadExample() {
  if (controller) return;
  const current = new AbortController(); controller = current; busy(true); resetResults();
  $('status').textContent = 'Loading synthetic data…';
  try {
    const base = new URL(config.dataset.path, import.meta.url);
    const files = await Promise.all(config.dataset.files.map(async name => {
      const response = await fetch(new URL(name, base), { signal: current.signal });
      if (!response.ok) throw new Error(`Could not load ${name} (HTTP ${response.status}). See the setup instructions.`);
      return [workspacePath(name), new Uint8Array(await response.arrayBuffer())];
    }));
    if (current.signal.aborted) throw new DOMException('Loading cancelled', 'AbortError');
    inputs.clear(); for (const [path, bytes] of files) inputs.set(path, bytes);
    refreshFiles(); chooseBed(); $('status').textContent = 'Synthetic data loaded. Choose an analysis and run it.';
  } catch (error) { $('status').textContent = error.name === 'AbortError' ? 'Loading cancelled.' : `Error: ${error.message}`; }
  finally { controller = null; busy(false); }
}
async function upload(event) {
  if (controller) return;
  busy(true); $('cancel').disabled = true;
  try {
    const files = await Promise.all([...event.target.files].map(async file => [workspacePath(file.webkitRelativePath || file.name), new Uint8Array(await file.arrayBuffer())]));
    for (const [path, bytes] of files) inputs.set(path, bytes);
    resetResults(); refreshFiles(); chooseBed(); $('status').textContent = 'Files loaded. Check the BED filename before running.';
  } catch (error) { $('status').textContent = `Error: ${error.message}`; }
  finally { event.target.value = ''; busy(false); }
}
$('load-data').addEventListener('click', loadExample);
$('upload').addEventListener('change', upload);
$('upload-folder').addEventListener('change', upload);
$('command').addEventListener('change', fillPreset);
$('bed-file').addEventListener('change', fillPreset);
$('output-directory').addEventListener('change', fillPreset);
$('result-file').addEventListener('change', showResult);
$('cancel').addEventListener('click', () => controller?.abort());
$('clear').addEventListener('click', () => { inputs.clear(); resetResults(); refreshFiles(); $('status').textContent = 'Files cleared.'; });
$('command-form').addEventListener('submit', async event => {
  event.preventDefault(); if (controller) return;
  let args, directory, timeoutMs;
  try {
    args = JSON.parse($('arguments').value);
    if (!Array.isArray(args) || args.some(arg => typeof arg !== 'string' || arg.includes('\0'))) throw new Error('Arguments must be a JSON array of strings without NUL characters.');
    directory = workspacePath($('output-directory').value.trim());
    if ([...inputs.keys()].some(path => path === directory || path.startsWith(`${directory}/`) || directory.startsWith(`${path}/`))) throw new Error('Choose an output folder separate from the loaded input files.');
    timeoutMs = Number($('timeout').value) * 1000;
    if (!Number.isInteger(timeoutMs) || timeoutMs < 1000 || timeoutMs > 3600000) throw new Error('The time limit must be between 1 and 3600 seconds.');
  } catch (error) { resetResults(); $('status').textContent = `Check command: ${error.message}`; return; }
  const marker = `${directory}/.king-output-directory`;
  const current = new AbortController(); controller = current; busy(true); resetResults();
  $('status').textContent = `Running KING ${KING_VERSION}… The first command also loads the program.`;
  const started = performance.now();
  try {
    // A zero-byte marker creates the chosen directory before KING opens output
    // files. It is private to this command and never appears as an analysis output.
    const result = await new KingRunner().run(args, {
      files: [...inputs].map(([path, data]) => ({ path, data })).concat({ path: marker, data: new Uint8Array() }),
      outputDirectories: [directory], signal: current.signal, timeoutMs,
    });
    $('stdout').textContent = preview(result.stdout); $('stderr').textContent = preview(result.stderr);
    download('stream-downloads', 'Download standard output', `king-${KING_VERSION}-stdout.txt`, result.stdout);
    download('stream-downloads', 'Download standard error', `king-${KING_VERSION}-stderr.txt`, result.stderr);
    if (result.exitCode === 0 && !result.missingOutputs.length) {
      for (const file of result.files) if (file.path !== marker) results.set(file.path, file.data);
      for (const [path, bytes] of results) {
        $('result-file').add(new Option(path.slice(6), path));
        download('downloads', `Download ${path.slice(6)}`, path, bytes);
      }
      $('result-file').disabled = !results.size; showResult();
    }
    const seconds = ((performance.now() - started) / 1000).toFixed(2);
    const suffix = result.missingOutputs.length ? ` Missing output folder: ${result.missingOutputs.join(', ')}.` : result.exitCode === 0 && !results.size ? ' No files were written to the selected output folder.' : '';
    $('status').textContent = `Exited with status ${result.exitCode} after ${seconds} seconds.${suffix}`;
  } catch (error) { $('status').textContent = error.name === 'AbortError' ? 'Command cancelled.' : `${error.name}: ${error.message}`; }
  finally { controller = null; busy(false); }
});

try {
  const response = await fetch(new URL('config.json', import.meta.url));
  if (!response.ok) throw new Error(`Could not load config.json (HTTP ${response.status}).`);
  config = await response.json();
  for (const [id, command] of Object.entries(config.commands)) $('command').add(new Option(command.label, id));
  if (!$('command').options.length || !config.dataset.files.length) throw new Error('No commands or example files are configured.');
  fillPreset(); busy(false); $('status').textContent = 'Ready. Load files, then choose an analysis.';
} catch (error) { $('status').textContent = `Error: ${error.message}`; }
