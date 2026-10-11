# Salmon thread notifications in WebKit

Salmon uses Emscripten pthreads. The browser's main runtime worker handles
filesystem requests from those threads while the command runs. With Emscripten
3.1.64, that worker sometimes stalled in the Linux WebKit 27.2 build supplied
with Playwright 1.64.0. Chromium, Firefox and Node.js completed the same commands.

The tests below used the same compiled Salmon 2.8.0 files, real human transcript
reference and index arguments. Every attempt created a fresh runtime worker.
They ran in [GitHub Actions run 38133276465](https://github.com/comparativechrono/genomics-wasm/actions/runs/38133276465).

| Diagnostic condition | Result |
| --- | --- |
| Unmodified runtime | Two starts passed; the third timed out after 60 seconds. |
| One ignored message after 10 seconds | All 100 passed. Nine stalled until the message arrived, then finished within 143 milliseconds. |
| Full logging with a five-second timer | All 100 passed, but 12 took about five or ten seconds. |
| Disable `Atomics.waitAsync` only in the main runtime worker | All 100 passed, taking 592–708 milliseconds each. |

This identifies a notification wakeup problem: an unrelated event resumed the
stalled command. The port therefore uses Emscripten's existing `postMessage`
mailbox notification path for Salmon's browser main runtime. It leaves Node.js
and child pthreads on the SDK's original path. The implementation is in
[`mailbox.js`](../../tools/salmon/mailbox.js), loaded as an Emscripten linker
library. It does not change the global `Atomics` object or Salmon's algorithms.

The diagnostic script retains the original experiments, including modes
`direct`, `message-probe`, `full` and `no-wait-async`:

```sh
node scripts/diagnose-salmon-browser.mjs --engine webkit --case index --repeat 100 --mode direct
```

The release gate requires this 100-start check with the normal runtime, the
native comparisons, and the browser interface tests. Diagnostic overrides are
not used for those release checks.
