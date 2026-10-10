import { createDESeq2 } from '../../runtime/deseq2.mjs';

const exampleScript = `library(DESeq2)

counts <- as.matrix(read.delim("counts.tsv", row.names = 1,
                               check.names = FALSE))
samples <- read.delim("samples.tsv", row.names = 1,
                      check.names = FALSE)
stopifnot(is.numeric(counts), !anyNA(counts),
          all(is.finite(counts)), all(counts >= 0),
          all(counts == floor(counts)),
          !anyDuplicated(colnames(counts)),
          setequal(colnames(counts), rownames(samples)))
samples <- samples[colnames(counts), , drop = FALSE]

# Change these variables and the design for a different experiment.
samples$cell <- factor(samples$cell)
samples$dex <- factor(samples$dex, levels = c("untrt", "trt"))
stopifnot(!anyNA(samples$dex))
dds <- DESeqDataSetFromMatrix(countData = counts,
                              colData = samples,
                              design = ~ cell + dex)

# Keep genes with at least 10 reads in total across the samples.
dds <- dds[rowSums(counts(dds)) >= 10, ]
dds <- DESeq(dds, fitType = "parametric", parallel = FALSE)
res <- results(dds, contrast = c("dex", "trt", "untrt"), alpha = 0.05)
res <- res[order(res$padj), ]
print(summary(res))

write.table(data.frame(gene_id = rownames(res), as.data.frame(res)),
            "results.tsv", sep = "\\t", quote = FALSE,
            row.names = FALSE)
normalised <- counts(dds, normalized = TRUE)
write.table(data.frame(gene_id = rownames(normalised), normalised,
                       check.names = FALSE),
            "normalised-counts.tsv", sep = "\\t", quote = FALSE,
            row.names = FALSE)

pdf("diagnostics.pdf", width = 7, height = 6)
plotMA(res, alpha = 0.05, main = "Treated versus untreated")
plotDispEsts(dds)
dev.off()
sessionInfo()
`;

const byId = id => document.getElementById(id);
const controls = ['counts', 'samples', 'load-airway', 'clear', 'reset-script'];
const files = new Map();
const urls = [];
let session;
let activeRun;
let loadingData = false;

byId('script').value = exampleScript;

function updateControls() {
  const busy = Boolean(activeRun) || loadingData;
  for (const id of controls) byId(id).disabled = busy;
  byId('analysis-controls').disabled = busy;
  byId('run').disabled = busy || !files.has('counts.tsv') || !files.has('samples.tsv');
  byId('cancel').disabled = !activeRun;
}

function clearResults() {
  urls.splice(0).forEach(url => URL.revokeObjectURL(url));
  byId('downloads').replaceChildren();
  byId('stdout').textContent = 'No output yet.';
  byId('stderr').textContent = 'No messages yet.';
}

function recordFile(kind, name, bytes) {
  files.set(`${kind}.tsv`, bytes);
  byId(`${kind}-name`).textContent = `${name} (${bytes.byteLength.toLocaleString()} bytes)`;
}

for (const kind of ['counts', 'samples']) {
  byId(kind).addEventListener('change', async event => {
    const file = event.target.files[0];
    if (!file) return;
    loadingData = true;
    updateControls();
    clearResults();
    try {
      recordFile(kind, file.name, new Uint8Array(await file.arrayBuffer()));
      byId('status').textContent = files.size === 2 ? 'Files loaded. Check the R code, then run the analysis.' : 'Choose the other TSV file to continue.';
    } catch (error) {
      files.delete(`${kind}.tsv`);
      byId(`${kind}-name`).textContent = `Could not read ${file.name}.`;
      byId('status').textContent = `Error: ${error.message}`;
    } finally {
      loadingData = false;
      updateControls();
    }
  });
}

byId('load-airway').addEventListener('click', async () => {
  loadingData = true;
  updateControls();
  clearResults();
  byId('status').textContent = 'Loading the airway count and sample tables…';
  try {
    const data = await Promise.all(['counts', 'samples'].map(async kind => {
      const response = await fetch(new URL(`../../datasets/deseq2-airway/${kind}.tsv`, import.meta.url));
      if (!response.ok) throw new Error(`Could not load ${kind}.tsv (${response.status}).`);
      return [kind, new Uint8Array(await response.arrayBuffer())];
    }));
    for (const [kind, bytes] of data) {
      recordFile(kind, `airway ${kind}.tsv`, bytes);
      byId(kind).value = '';
    }
    byId('status').textContent = 'Airway data loaded. Check the R code, then run the analysis.';
  } catch (error) {
    byId('status').textContent = `Error: ${error.message}`;
  } finally {
    loadingData = false;
    updateControls();
  }
});

byId('clear').addEventListener('click', () => {
  session?.close();
  session = undefined;
  files.clear();
  for (const kind of ['counts', 'samples']) {
    byId(kind).value = '';
    byId(`${kind}-name`).textContent = `No ${kind === 'counts' ? 'count' : 'sample'} table loaded.`;
  }
  clearResults();
  byId('status').textContent = 'Load the airway example or choose two TSV files.';
  updateControls();
});

byId('reset-script').addEventListener('click', () => {
  byId('script').value = exampleScript;
  byId('status').textContent = 'Example R code restored.';
});

function addDownload(name, data) {
  const type = name.endsWith('.pdf') ? 'application/pdf' : /\.(tsv|txt|R|csv)$/i.test(name) ? 'text/plain;charset=utf-8' : 'application/octet-stream';
  const url = URL.createObjectURL(new Blob([data], { type }));
  urls.push(url);
  const link = document.createElement('a');
  link.className = 'download';
  link.href = url;
  link.download = name.split('/').at(-1);
  link.textContent = `Download ${name}`;
  byId('downloads').append(link);
}

byId('analysis-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (activeRun || files.size !== 2) return;
  const run = { cancelled: false, controller: new AbortController() };
  activeRun = run;
  const script = byId('script').value;
  clearResults();
  updateControls();
  const started = performance.now();
  try {
    if (!session) {
      byId('status').textContent = 'Loading R and DESeq2…';
      const created = await createDESeq2({
        signal: run.controller.signal,
        onProgress: message => { if (!run.cancelled) byId('status').textContent = message; },
      });
      if (run.cancelled) { await created.close(); return; }
      session = created;
    }
    byId('status').textContent = 'Running the R analysis…';
    const result = await session.run(script, { files: Object.fromEntries(files), resetFiles: true });
    if (run.cancelled) return;
    byId('stdout').textContent = result.stdout || 'No standard output.';
    byId('stderr').textContent = result.stderr || 'No messages.';
    for (const [name, bytes] of Object.entries(result.files)) {
      if (!files.has(name)) addDownload(name, bytes);
    }
    addDownload('analysis.R', script);
    byId('status').textContent = `Analysis complete (${((performance.now() - started) / 1000).toFixed(1)} seconds).`;
  } catch (error) {
    if (!run.cancelled) {
      byId('status').textContent = `Error: ${error.message}`;
      byId('stdout').textContent = error.stdout || 'No standard output.';
      byId('stderr').textContent = error.stderr || error.message;
      if (session) {
        try { session.close(); } catch { /* Preserve the analysis error. */ }
      }
      session = undefined;
    }
  } finally {
    if (activeRun === run) activeRun = undefined;
    updateControls();
  }
});

byId('cancel').addEventListener('click', async () => {
  if (!activeRun) return;
  activeRun.cancelled = true;
  activeRun.controller.abort();
  byId('cancel').disabled = true;
  byId('status').textContent = 'Analysis cancelled. The next run will start a new R session.';
  if (session) {
    const current = session;
    session = undefined;
    try { await current.interrupt(); } catch { /* The aborted session may already be closed. */ }
  }
});

window.addEventListener('pagehide', () => {
  urls.forEach(url => URL.revokeObjectURL(url));
  session?.close();
});
