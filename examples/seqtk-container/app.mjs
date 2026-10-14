import { SeqtkContainerRunner } from '../../runtime/container2wasm-seqtk.mjs';

const MAX_INPUT_BYTES = 20 * 1024 * 1024;
const MAX_PREVIEW_BYTES = 64 * 1024;
const MAX_LOG_CHARACTERS = 80 * 1024;
const encoder = new TextEncoder();
const decoder = new TextDecoder();
const DEMO_READS = '@demo_read_1\nACGTTGCAACGT\n+\nIIIIIIIIIIII\n@demo_read_2\nGATTACAGGTCA\n+\nHHHHHHHHHHHH\n@demo_read_3\nTGCATGCAATGC\n+\nJJJJJJJJJJJJ\n';
const $ = (id) => document.getElementById(id);
const runner = new SeqtkContainerRunner();
const state = { input: null, running: false, result: null, phase: 'idle', error: null };
let controller = null;
let outputURL = null;
let stderrURL = null;
let sawStreamedStderr = false;
let logEntries = 0;
let logText = '';
let readGeneration = 0;

const descriptions = {
  fasta: 'Keep read names and sequences; omit quality scores.',
  reverse: 'Reverse each sequence and complement its bases. FASTQ qualities are reversed too.',
  trim: 'Remove a fixed number of bases from each end of every read.',
  fqchk: 'Report sequence length, base composition, and per-position quality statistics.',
  custom: 'Run a seqtk command with the selected file available under /work/.',
};

function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

function formatDuration(milliseconds) {
  return milliseconds < 1000 ? `${Math.round(milliseconds)} ms` : `${(milliseconds / 1000).toFixed(2)} s`;
}

function inputNameFor(name) {
  const gzip = /\.gz$/i.test(name);
  const uncompressed = name.replace(/\.gz$/i, '');
  const extension = /\.(fa|fasta|fna)$/i.test(uncompressed) ? 'fa' : 'fq';
  return `reads.${extension}${gzip ? '.gz' : ''}`;
}

function inputPath() {
  return `/work/${state.input?.path ?? 'reads.fq'}`;
}

function integerField(id, minimum = 0, maximum = 1000000) {
  const value = Number($(id).value);
  if ($(id).value === '' || !Number.isInteger(value) || value < minimum || value > maximum) {
    throw new Error(`${id === 'timeout' ? 'Time limit' : 'Trim counts'} must be a whole number between ${minimum} and ${maximum}.`);
  }
  return value;
}

function argumentsForRun() {
  switch ($('operation').value) {
    case 'fasta': return ['seq', '-A', inputPath()];
    case 'reverse': return ['seq', '-r', inputPath()];
    case 'trim': return ['trimfq', '-b', String(integerField('trim-left')), '-e', String(integerField('trim-right')), inputPath()];
    case 'fqchk': return ['fqchk', inputPath()];
    case 'custom': {
      let args;
      try { args = JSON.parse($('arguments').value); } catch { throw new Error('Arguments must be a valid JSON array, for example ["seq", "-A", "/work/reads.fq"].'); }
      if (!Array.isArray(args) || args.length === 0 || args.some((arg) => typeof arg !== 'string' || /[\0\r\n]/.test(arg))) {
        throw new Error('Use a nonempty JSON array of strings without NUL or newline characters.');
      }
      if (args[0] === 'seqtk') throw new Error('Omit the program name: start the array with a seqtk command such as "seq".');
      return args;
    }
    default: throw new Error('Choose a supported operation.');
  }
}

function displayCommand(args) {
  return ['seqtk', ...args.map((arg) => /^[A-Za-z0-9_./:=+-]+$/.test(arg) ? arg : JSON.stringify(arg))].join(' ');
}

function updateCommand() {
  const operation = $('operation').value;
  $('operation-description').textContent = descriptions[operation];
  $('trim-options').hidden = operation !== 'trim';
  $('trim-left').disabled = operation !== 'trim';
  $('trim-right').disabled = operation !== 'trim';
  $('custom-options').hidden = operation !== 'custom';
  try {
    $('command-preview').textContent = displayCommand(argumentsForRun());
    $('command-error').hidden = true;
  } catch (error) {
    $('command-preview').textContent = 'Check the arguments below.';
    $('command-error').textContent = error.message;
    $('command-error').hidden = false;
  }
}

function setStatus(message, phase = state.phase) {
  state.phase = phase;
  $('status').textContent = message;
  const labels = { idle: 'Ready', running: 'Running', success: 'Complete', error: 'Failed', cancelled: 'Stopped' };
  $('run-indicator').textContent = labels[phase] ?? phase;
  $('run-indicator').dataset.state = phase;
}

function updateControls() {
  $('controls').disabled = state.running;
  $('run').disabled = state.running || !state.input || !globalThis.crossOriginIsolated;
  $('cancel').hidden = !state.running;
  $('activity').hidden = !state.running;
  $('results-heading').textContent = state.running ? 'Run in progress' : 'Run & results';
}

function appendLog(text, label = 'runtime') {
  if (!text) return;
  const timestamp = new Date().toLocaleTimeString([], { hour12: false });
  logText += `${logText ? '\n' : ''}[${timestamp}] ${label}: ${String(text).trimEnd()}`;
  if (logText.length > MAX_LOG_CHARACTERS) logText = `[Earlier log text truncated]\n${logText.slice(-MAX_LOG_CHARACTERS)}`;
  logEntries += 1;
  $('logs').textContent = logText;
  $('log-count').textContent = `${logEntries} ${logEntries === 1 ? 'entry' : 'entries'}`;
  $('logs').scrollTop = $('logs').scrollHeight;
}

function clearOutput() {
  if (outputURL) URL.revokeObjectURL(outputURL);
  if (stderrURL) URL.revokeObjectURL(stderrURL);
  outputURL = null;
  stderrURL = null;
  state.result = null;
  $('result').hidden = true;
  $('empty-state').hidden = false;
  $('stdout').textContent = '';
  $('metrics').replaceChildren();
  $('download').removeAttribute('href');
  $('download-stderr').removeAttribute('href');
  $('download-stderr').hidden = true;
}

function selectInput(name, data) {
  clearOutput();
  state.input = { name, path: inputNameFor(name), data };
  state.error = null;
  $('file-name').textContent = name;
  $('file-info').textContent = `${formatBytes(data.byteLength)} · ${inputPath()}`;
  $('selected-file').hidden = false;
  $('input-error').hidden = true;
  $('arguments').value = JSON.stringify(['seq', '-A', inputPath()]);
  updateCommand();
  setStatus('File ready. Run seqtk to start the container.', 'idle');
  updateControls();
}

function loadDemo() {
  if (state.running) return;
  readGeneration += 1;
  $('input-file').value = '';
  selectInput('sample-reads.fastq', encoder.encode(DEMO_READS));
}

function outputName(operation, args, sourceName) {
  const basename = sourceName.replace(/\.gz$/i, '').replace(/\.(fastq|fq|fasta|fa|fna|txt)$/i, '');
  if (operation === 'fasta') return `${basename}.fasta`;
  if (operation === 'fqchk') return `${basename}.quality.txt`;
  if (operation === 'custom') return `${basename}.${args[0].replace(/[^A-Za-z0-9_-]/g, '') || 'seqtk'}.txt`;
  const extension = /\.(fa|fasta|fna)(\.gz)?$/i.test(sourceName) ? 'fasta' : 'fastq';
  return `${basename}.${operation === 'trim' ? 'trimmed' : 'reverse-complement'}.${extension}`;
}

function addMetric(label, value, explanation) {
  const item = document.createElement('div');
  const term = document.createElement('dt');
  const description = document.createElement('dd');
  term.textContent = label;
  description.textContent = value;
  if (explanation) { item.title = explanation; term.textContent += ' ⓘ'; }
  item.append(term, description);
  $('metrics').append(item);
}

function showResult(result, context) {
  const stdout = result.stdout instanceof Uint8Array ? result.stdout : new Uint8Array(result.stdout ?? []);
  const stderr = result.stderr instanceof Uint8Array ? result.stderr : new Uint8Array(result.stderr ?? []);
  const preview = stdout.subarray(0, MAX_PREVIEW_BYTES);
  outputURL = URL.createObjectURL(new Blob([stdout], { type: 'application/octet-stream' }));
  $('download').href = outputURL;
  $('download').download = outputName(context.operation, context.args, context.inputName);
  if (stderr.byteLength) {
    stderrURL = URL.createObjectURL(new Blob([stderr], { type: 'text/plain;charset=utf-8' }));
    $('download-stderr').href = stderrURL;
    $('download-stderr').download = `${$('download').download}.stderr.txt`;
    $('download-stderr').hidden = false;
  }
  $('result-summary').textContent = `${formatBytes(stdout.byteLength)} · exit code ${result.exitCode}`;
  $('stdout').textContent = stdout.byteLength ? decoder.decode(preview) : '(Standard output is empty.)';
  $('preview-note').hidden = stdout.byteLength <= MAX_PREVIEW_BYTES;
  $('empty-state').hidden = true;
  $('result').hidden = false;
  const metrics = result.metrics ?? {};
  for (const [field, label, note] of [
    ['totalMs', 'TOTAL TIME'],
    ['loadMs', 'RUNTIME LOAD', 'Includes asset loading; browser cache state is not inferred.'],
    ['bootMs', 'LINUX STARTUP ≈', 'Observed from the guest start marker; not a precise CPU benchmark.'],
    ['executionMs', 'EXECUTION ≈', 'Includes guest work and result collection; timing resolution is approximately 100 ms.'],
  ]) {
    if (Number.isFinite(metrics[field])) addMetric(label, formatDuration(metrics[field]), note);
  }
  if (Number.isFinite(metrics.wasmMemoryBytes)) addMetric('WASM MEMORY', formatBytes(metrics.wasmMemoryBytes), 'The reported WebAssembly memory allocation. This is not peak browser or guest process memory.');
  addMetric('RUNTIME', 'Fresh VM', 'Each run starts a fresh Linux guest. Download caching is managed by the browser.');
  if (stderr.byteLength && !sawStreamedStderr) appendLog(decoder.decode(stderr.subarray(0, MAX_LOG_CHARACTERS)), 'seqtk stderr');
  appendLog(`seqtk exited with code ${result.exitCode}; ${formatBytes(stdout.byteLength)} captured from stdout.`, 'result');
}

async function run() {
  if (state.running || !state.input) return null;
  $('command-error').hidden = true;
  let args;
  let timeoutMs;
  try {
    if (!globalThis.crossOriginIsolated) throw new Error('Cross-origin isolation is required. Start the supplied local server and open its localhost URL.');
    args = argumentsForRun();
    timeoutMs = integerField('timeout', 10, 1800) * 1000;
  } catch (error) {
    $('command-error').textContent = error.message;
    $('command-error').hidden = false;
    return null;
  }

  const context = { args, operation: $('operation').value, inputName: state.input.name };
  clearOutput();
  state.running = true;
  state.error = null;
  controller = new AbortController();
  logEntries = 0;
  logText = '';
  sawStreamedStderr = false;
  $('logs').textContent = '';
  $('log-count').textContent = '0 entries';
  $('log-details').open = true;
  setStatus('Loading the container runtime…', 'running');
  appendLog(displayCommand(args), 'command');
  appendLog(`${formatBytes(state.input.data.byteLength)} input; ${timeoutMs / 1000} s overall time limit.`, 'run');
  updateControls();
  try {
    const result = await runner.run(args, {
      files: [{ path: state.input.path, data: state.input.data }],
      timeoutMs,
      signal: controller.signal,
      onLog: ({ stream, text }) => {
        if (stream === 'stderr' && text) sawStreamedStderr = true;
        appendLog(text, stream);
      },
      onProgress: ({ phase, message }) => {
        setStatus(message || ({ loading: 'Loading the container runtime…', booting: 'Starting Linux…', running: 'Running seqtk…', complete: 'Collecting output…' })[phase] || phase, 'running');
      },
    });
    state.result = result;
    showResult(result, context);
    if (result.exitCode === 0) {
      setStatus('Complete. Your output is ready to download.', 'success');
    } else {
      setStatus(`seqtk exited with code ${result.exitCode}. Check the log; captured output is still available.`, 'error');
      $('log-details').open = true;
    }
    return result;
  } catch (error) {
    state.error = { name: error.name, message: error.message };
    if (error.name === 'AbortError') {
      setStatus('Cancelled. The runtime has been stopped. You can start another run.', 'cancelled');
    } else if (error.name === 'TimeoutError') {
      setStatus('Time limit reached. The runtime has been stopped. Increase the limit or try a smaller file.', 'error');
    } else {
      setStatus(error.message || 'The container runtime could not complete this run.', 'error');
    }
    appendLog(error.message || String(error), error.name || 'error');
    $('log-details').open = true;
    return null;
  } finally {
    state.running = false;
    controller = null;
    updateControls();
  }
}

function cancel() {
  if (!state.running) return;
  controller?.abort();
  setStatus('Stopping the container runtime…', 'running');
}

$('input-file').addEventListener('change', async (event) => {
  const file = event.target.files?.[0];
  if (!file || state.running) return;
  const generation = ++readGeneration;
  state.input = null;
  clearOutput();
  $('selected-file').hidden = true;
  $('input-error').hidden = true;
  updateControls();
  if (file.size > MAX_INPUT_BYTES) {
    $('input-error').textContent = `This prototype accepts files up to 20 MiB. Selected file: ${formatBytes(file.size)}.`;
    $('input-error').hidden = false;
    $('input-file').value = '';
    setStatus('Choose a file up to 20 MiB, or try the example reads.', 'idle');
    return;
  }
  setStatus('Reading the selected file into this tab…', 'idle');
  try {
    const data = new Uint8Array(await file.arrayBuffer());
    if (generation === readGeneration && !state.running) selectInput(file.name, data);
  } catch (error) {
    if (generation !== readGeneration) return;
    $('input-error').textContent = `Could not read this file: ${error.message}`;
    $('input-error').hidden = false;
    setStatus('Choose the file again, or try the example reads.', 'idle');
  }
});
$('load-demo').addEventListener('click', loadDemo);
$('operation').addEventListener('change', updateCommand);
for (const id of ['arguments', 'trim-left', 'trim-right']) $(id).addEventListener('input', updateCommand);
$('command-form').addEventListener('submit', (event) => { event.preventDefault(); void run(); });
$('cancel').addEventListener('click', cancel);
window.addEventListener('pagehide', () => {
  controller?.abort();
  runner.dispose();
  if (outputURL) URL.revokeObjectURL(outputURL);
  if (stderrURL) URL.revokeObjectURL(stderrURL);
});

if (!globalThis.crossOriginIsolated) {
  $('environment-notice').hidden = false;
  $('environment-notice').textContent = 'This page needs cross-origin isolation to run WebAssembly threads. Use the supplied server, which sets the required COOP and COEP headers, then open the localhost URL. Opening this HTML file directly is not supported.';
}
updateCommand();
updateControls();

// A small inspection surface for browser smoke tests; no alternate execution path.
window.seqtkPrototype = {
  run,
  cancel,
  loadDemo,
  get state() {
    return {
      running: state.running,
      phase: state.phase,
      input: state.input && { name: state.input.name, path: state.input.path, bytes: state.input.data.byteLength },
      result: state.result,
      error: state.error,
    };
  },
};
