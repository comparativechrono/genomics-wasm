import { ToolRunner } from '../../runtime/index.mjs';

const $ = id => document.getElementById(id);
const workspace = new Map();
const downloadUrls = [];
let controller;
let loadingFiles = false;
const examples = {
  bwa: [
    { label: 'Usage', args: [], outputs: [] },
    { label: 'Index a reference', args: ['index', 'reference.fa'], outputs: ['amb', 'ann', 'bwt', 'pac', 'sa'].map(extension => `reference.fa.${extension}`) },
    { label: 'Align paired reads', args: ['mem', 'reference.fa', 'reads_1.fq', 'reads_2.fq'], outputs: [] },
    { label: 'Align single reads', args: ['mem', 'reference.fa', 'reads.fq'], outputs: [] },
  ],
  'bwa-mem2': [
    { label: 'Usage', args: [], outputs: [] },
    { label: 'Index a reference', args: ['index', 'reference.fa'], outputs: ['0123', 'amb', 'ann', 'bwt.2bit.64', 'pac'].map(extension => `reference.fa.${extension}`) },
    { label: 'Align paired reads', args: ['mem', '-t', '1', 'reference.fa', 'reads_1.fq', 'reads_2.fq'], outputs: [] },
    { label: 'Align single reads', args: ['mem', '-t', '1', 'reference.fa', 'reads.fq'], outputs: [] },
  ],
  freebayes: [
    { label: 'Help', args: ['--help'], outputs: [] },
    { label: 'Call variants', args: ['--fasta-reference', 'reference.fa', 'alignment.bam'], outputs: [] },
    { label: 'Call haploid variants', args: ['--fasta-reference', 'reference.fa', '--ploidy', '1', 'alignment.bam'], outputs: [] },
  ],
};

function selectExample() {
  const example = examples[$('tool').value][Number($('example').value)];
  $('arguments').value = JSON.stringify(example.args, null, 2);
  $('outputs').value = example.outputs.join('\n');
}

function selectTool() {
  $('example').replaceChildren(...examples[$('tool').value].map((example, index) => {
    const option = document.createElement('option');
    option.value = index;
    option.textContent = example.label;
    return option;
  }));
  selectExample();
}

function showFiles() {
  $('files').replaceChildren(...[...workspace].map(([path, data]) => {
    const item = document.createElement('li');
    item.textContent = `${path} (${data.length.toLocaleString()} bytes)`;
    return item;
  }));
  if (!workspace.size) $('files').textContent = 'No files loaded.';
}

function download(label, filename, bytes) {
  const url = URL.createObjectURL(new Blob([bytes], { type: 'application/octet-stream' }));
  downloadUrls.push(url);
  const link = document.createElement('a');
  link.className = 'download';
  link.href = url;
  link.download = filename;
  link.textContent = label;
  $('downloads').append(link);
}

function preview(bytes) {
  if (!bytes.length) return '(empty)';
  if (bytes.subarray(0, 8192).includes(0)) return `Binary output (${bytes.length.toLocaleString()} bytes). Use the download link.`;
  const limit = 200000;
  const text = new TextDecoder().decode(bytes.subarray(0, limit));
  return bytes.length > limit ? `${text}\n\n[Preview truncated; download contains all ${bytes.length.toLocaleString()} bytes.]` : text;
}

function busy(active) {
  $('controls').disabled = active;
  $('upload').disabled = active;
  $('clear').disabled = active;
  $('cancel').disabled = !active;
}

$('tool').addEventListener('change', selectTool);
$('example').addEventListener('change', selectExample);
$('upload').addEventListener('change', async event => {
  loadingFiles = true;
  $('run').disabled = true;
  $('upload').disabled = true;
  $('clear').disabled = true;
  try {
    for (const file of event.target.files) workspace.set(`/work/${file.name}`, new Uint8Array(await file.arrayBuffer()));
    showFiles();
    $('status').textContent = 'Files loaded.';
  } catch (error) {
    $('status').textContent = `Could not read file: ${error.message}`;
  } finally {
    loadingFiles = false;
    $('run').disabled = false;
    $('upload').disabled = false;
    $('clear').disabled = false;
    $('upload').value = '';
  }
});
$('clear').addEventListener('click', () => { workspace.clear(); showFiles(); });
$('cancel').addEventListener('click', () => controller?.abort());
$('command-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (controller || loadingFiles) return;
  const tool = $('tool').value;
  let args;
  try {
    args = JSON.parse($('arguments').value);
    if (!Array.isArray(args) || args.some(arg => typeof arg !== 'string')) throw new Error('Use a JSON array of strings.');
  } catch (error) {
    $('status').textContent = `Check arguments: ${error.message}`;
    return;
  }
  const outputs = $('outputs').value.split('\n').map(path => path.trim()).filter(Boolean);
  const timeoutMs = Number($('timeout').value) * 1000;
  if (!Number.isFinite(timeoutMs) || timeoutMs < 1000) { $('status').textContent = 'Enter a time limit of at least one second.'; return; }
  controller = new AbortController();
  busy(true);
  downloadUrls.splice(0).forEach(url => URL.revokeObjectURL(url));
  $('downloads').replaceChildren();
  $('stdout').textContent = '';
  $('stderr').textContent = '';
  $('status').textContent = `Running ${tool}…`;
  const started = performance.now();
  try {
    const runner = new ToolRunner({ moduleUrl: new URL(`../../dist/${tool}/${tool}.mjs`, import.meta.url) });
    const result = await runner.run(args, {
      files: [...workspace].map(([path, data]) => ({ path, data })),
      outputs,
      signal: controller.signal,
      timeoutMs,
    });
    $('stdout').textContent = preview(result.stdout);
    $('stderr').textContent = preview(result.stderr);
    const seconds = ((performance.now() - started) / 1000).toFixed(2);
    $('status').textContent = `Exited with status ${result.exitCode} after ${seconds} seconds.${result.missingOutputs.length ? ` Missing requested files: ${result.missingOutputs.join(', ')}.` : ''}`;
    download('Download standard output', tool === 'freebayes' && !args.includes('--help') ? 'calls.vcf' : ['bwa', 'bwa-mem2'].includes(tool) && args[0] === 'mem' ? 'alignment.sam' : `${tool}-stdout.txt`, result.stdout);
    download('Download standard error', `${tool}-stderr.txt`, result.stderr);
    for (const file of result.files) {
      workspace.set(file.path, file.data);
      const name = file.path.slice(6);
      download(`Download ${name}`, name.split('/').at(-1), file.data);
    }
    showFiles();
  } catch (error) {
    $('status').textContent = error.name === 'AbortError' ? 'Command cancelled.' : `${error.name}: ${error.message}`;
    if (error.name !== 'AbortError' && error.name !== 'TimeoutError') $('stderr').textContent = 'If the tool could not load, build it first and serve the repository over HTTP. See the repository README for instructions.';
  } finally {
    controller = null;
    busy(false);
  }
});

selectTool();
