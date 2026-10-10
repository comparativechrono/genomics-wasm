# DESeq2 JavaScript API

`deseq2.mjs` starts a real R interpreter in a webR worker and loads DESeq2.
It works in Node and modern browsers. All runtime assets are served from the
local `dist/deseq2` folder; no R server or package repository is required.

```js
import { createDESeq2 } from './runtime/deseq2.mjs';
const session = await createDESeq2({ onProgress: console.log });
try {
  const result = await session.run(`
    counts <- as.matrix(read.delim('counts.tsv', row.names = 1))
    samples <- read.delim('samples.tsv', row.names = 1)
    samples$condition <- factor(samples$condition)
    dds <- DESeqDataSetFromMatrix(counts, samples, ~ condition)
    dds <- DESeq(dds)
    write.table(as.data.frame(results(dds)), 'results.tsv',
                sep = '\\t', quote = FALSE, col.names = NA)
  `, { files: { 'counts.tsv': countBytes, 'samples.tsv': sampleBytes } });
  console.log(result.stdout, result.stderr);
  const results = result.files['results.tsv']; // Uint8Array
} finally {
  session.close();
}
```

`createDESeq2({baseUrl, onProgress, signal})` accepts an optional URL to the
bundled assets, a progress callback receiving text, and an AbortSignal. The
base URL defaults to `../dist/deseq2/` relative to the module. Browser hosting
needs to serve ES modules and Wasm files with suitable MIME types. The runtime
uses webR's PostMessage channel and does not require cross-origin isolation.

`session.run(code, {files, resetFiles})` evaluates normal R code in the global
environment with `/work` as the working directory. `files` maps relative paths
to strings or Uint8Arrays. The result contains `stdout` and `stderr` strings,
and a `files` object with all files below `/work`, including input files.
Subdirectories are preserved. R errors reject the promise with `stdout`,
`stderr`, and `files` attached to the Error. Temporary runtime files are stored
elsewhere and are not returned.

A session keeps R variables and files between runs. Set `resetFiles: true` to
remove working files before adding that run's inputs. This does not clear R
variables. Create a new session when a completely fresh R environment is wanted.
Only one script may run at a time in a session.

`writeFile(path, bytes)` and `readFile(path)` access files relative to `/work`.
`close()` and `interrupt()` terminate the worker immediately. Pending operations
reject, and a new session is required. Closing a session discards its R objects
and files. An AbortSignal also terminates initialization or an active session.

This bundle runs sequentially. BiocParallel is registered with `SerialParam()`.
Calling subprocesses, native libraries, or parallel workers is not supported by
webR. The package set is pinned; optional DESeq2 dependencies such as apeglm,
ashr, and glmGamPoi are not included. `lfcShrink(type = 'normal')` is available.
Normal R file output such as TSV, RDS, and PDF can be downloaded through the API.
Input files, R objects, package data, and result copies all consume browser memory.
