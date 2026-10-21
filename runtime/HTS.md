# BCFtools, SAMtools and HTSlib JavaScript API

`HTSRunner` runs the pinned 1.16 or 1.21 toolchain in Node.js or a browser.
Each version has its own BCFtools, SAMtools and HTSlib build. BCFtools plugins
are linked into the executable and retain the upstream `+name` command syntax.

```js
import { HTSRunner } from './runtime/hts.mjs';

const bcftools = new HTSRunner({ version: '1.21', tool: 'bcftools' });
const result = await bcftools.run([
  '+fill-tags', 'input.vcf', '-Ov', '-o', 'filled.vcf', '--', '-t', 'AC,AN,AF',
], {
  files: [{ path: 'input.vcf', data: inputBytes }],
  outputs: ['filled.vcf'],
  timeoutMs: 300_000,
});
if (result.exitCode !== 0 || result.missingOutputs.length) {
  throw new Error(new TextDecoder().decode(result.stderr));
}
const filledVcf = result.files[0].data;
```

Use `tool: 'samtools'`, `'bgzip'`, `'tabix'` or `'htsfile'` for the other
executables. `version` is either `'1.16'` or `'1.21'` and defaults to `'1.21'`.
The constructor also accepts `baseUrl` for the directory containing that
version's compiled modules, and an optional `workerUrl`. Both version bundles
can coexist; the default asset URL resolves relative to this runtime module.
Keep the runtime modules and each tool's `.mjs`/`.wasm` files together.

`run(args, options)` uses the same options and result as `ToolRunner`:

| Option | Meaning |
| --- | --- |
| `files` | Input files as `{ path, data }`; data may be text, `Uint8Array` or `ArrayBuffer`. |
| `outputs` | Exact filenames to collect. |
| `outputDirectories` | Directories whose output files should be collected recursively. |
| `stdin` | Input bytes or text. Standard output is returned as bytes, including binary BCF or BGZF. |
| `signal` | An `AbortSignal` for cancellation. |
| `timeoutMs` | Startup and execution deadline; `0` disables the deadline. |

Paths belong to the worker's `/work` directory. Supply inputs, reference
sequences and indexes explicitly. Each invocation starts with a fresh
filesystem and program state. Pass output bytes into the next invocation to
chain commands; shell pipelines and shell quoting are not interpreted.
Input buffers remain usable by the caller. Nonzero command exits return their
status and captured streams; JavaScript argument and worker errors reject the
promise. Check `missingOutputs` as well as `exitCode`.

List compiled plugins with `run(['plugin', '-l'])`. Run
`run(['+split-vep', '-h'])` or `run(['+fill-tags', '--', '-h'])` for that
version's plugin help. Plugin availability and behavior are version-specific;
see the build documentation and the generated plugin inventory in each bundle.
External shared-library plugins cannot be loaded into these static builds.

These bundles process local files with serial execution. HTSlib remote URL
protocols, interactive curses displays and the optional GSL-based `polysomy`
command are not included. Download remote inputs in your application and pass
their bytes through `files`. Supply local reference sequences explicitly for
CRAM workflows.

Random sampling follows the bundled C runtime. For `samtools view`, seed `0`
(for example `-s 0.5`) selects the same reads as the Linux native reference.
Nonzero seeds use upstream `srand`/`rand` and can select different reads in
WebAssembly than in a glibc-based native build, while remaining repeatable
within this build. The experimental `bcftools som` command similarly uses
platform-dependent `random`. These are not promises of identical selected
records across operating systems. Explicitly seeded HTSlib POSIX random
functions, including `+setGT` random genotype selection, use the same sequence
in the tested native and WebAssembly builds.

Serve the demo and assets from HTTP(S), such as a local static HTTP server.
Cross-origin isolation is not required. Browser input, working data and output
occupy memory; fixture validation does not establish whole-genome capacity or
throughput. The source archives, build recipes, headers and static HTSlib
library are included for developers rebuilding or linking other programs.

See [build instructions](../tools/hts/README.md),
[the browser demo](../examples/hts/README.md), and
[scientific validation](../validation/hts/README.md).
