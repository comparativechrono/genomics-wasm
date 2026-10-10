# Genomics WebAssembly tools

BWA and FreeBayes compiled for use in web browsers and Node.js. Each tool has an
independent package, ordinary command-line arguments, a small JavaScript API,
and tests against the corresponding native program.

The ports run the upstream algorithms. They do not replay saved results.
Browser commands run in workers, so they can be cancelled without blocking the
page. Each command receives a fresh WebAssembly instance.

## Tools

| Tool | Upstream version | Initial browser scope |
| --- | --- | --- |
| [BWA](tools/bwa/README.md) | 0.7.19 | `index` and `mem`, local files, one thread |
| [FreeBayes](tools/freebayes/README.md) | 1.3.6 | Local BAM/SAM variant calling, one process |

See the individual tool documentation for supported features, build changes,
licences and limitations. Whole-genome performance is not established by the
small validation fixtures.

## Build

On Linux, install a C/C++ compiler, Make, Git, curl, Python 3.12 or newer and
Node.js 20 or newer. The reproducible compiler target is Emscripten 3.1.64.

```sh
npm ci
bash scripts/setup-emsdk.sh
source toolchain/emsdk/emsdk_env.sh
bash scripts/build-bwa.sh all
bash scripts/build-freebayes.sh all
```

Each script builds the original native program, a native build with the
portability changes, and the WebAssembly program. Source revisions and archive
checksums are recorded in each tool directory. Downloaded sources stay under
`build/`; compiled browser files are written to `dist/`.

## Use a tool

```js
import { ToolRunner } from './runtime/index.mjs';

const bwa = new ToolRunner({
  moduleUrl: new URL('./dist/bwa/bwa.mjs', import.meta.url)
});
const result = await bwa.run(['index', 'reference.fa'], {
  files: [{ path: 'reference.fa', data: fastaBytes }],
  outputs: ['reference.fa.amb', 'reference.fa.ann', 'reference.fa.bwt',
            'reference.fa.pac', 'reference.fa.sa'],
  timeoutMs: 60000
});
if (result.exitCode !== 0) throw new Error(new TextDecoder().decode(result.stderr));
// Pass result.files alongside your reads to a subsequent 'mem' command.
```

Files, standard input, standard output and standard error preserve their bytes.
Arguments are an array; the API does not parse shell commands. Files are kept
inside the worker's virtual `/work` directory. Read the [runtime API](runtime/README.md)
for cancellation, output files and error handling.

For the browser example, serve the repository using `python3 -m http.server 8000`
and open `http://localhost:8000/examples/browser/`. No backend computation or
end-user installation is required. Files remain in the browser.

## Validate

```sh
npm test
python3 scripts/fetch-test-data.py --verify
node scripts/validate.mjs --engine node
npx playwright install --with-deps chromium firefox webkit
node scripts/validate.mjs --engine chromium
node scripts/validate.mjs --engine firefox
node scripts/validate.mjs --engine webkit
```

Validation uses real human NA12878 and yeast BY4741 sequencing data. Dataset
origins, transformations and checksums are documented under [datasets](datasets/README.md).
The caller receives the same fixed BAM in each implementation, independently of
the aligner tests. BWA index files are compared and cross-tested between builds.

The tests compare full SAM/VCF records and meaningful headers, preserving
secondary alignments, complex alleles and duplicate records. They also exercise
different arguments, repeated invocations and missing inputs. See
[comparison rules](validation/README.md). Native agreement establishes port
fidelity on the tested inputs; it is not a biological accuracy benchmark.

The FreeBayes comparisons allow an absolute difference of at most `1e-13` in
VCF QUAL only, provided both values have magnitude at most `1e-8`. Every use of
that allowance is reported. Native and WebAssembly builds use different
`long double` precision; the largest observed difference was `2.09496e-14`,
with all changed scores close to zero. All other fields remain exact. The
comparator's default is strict; the validation runner supplies both bounds
explicitly. See the [FreeBayes port notes](tools/freebayes/README.md).

GitHub Actions builds all three versions and runs the same comparisons in Node,
Chromium, Firefox and WebKit. Each run stores raw outputs and JSON reports as
artifacts. The `release-gate` job requires every build and browser test to pass.
There is no automatic publication of a release from an unvalidated build.

## Packages

After building and validating, run `python3 scripts/package.py`. It creates
separate archives under `releases/`, each containing one tool, its runtime,
documentation, licences and corresponding sources. GitHub Actions also retains
these files in its `tool-packages` artifact; check the complete workflow status
before using a build. That artifact is created only after all browser jobs
pass. The earlier `candidate-packages` artifact is an unvalidated build input
to that final step.

## Licence

Original runtime and build/test infrastructure use the MIT licence. Upstream
programs and dependencies retain their own licences; compiled packages are not
all MIT-licensed. See [THIRD_PARTY.md](THIRD_PARTY.md) and the notices included in
each package.
