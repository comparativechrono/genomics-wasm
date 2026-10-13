import { ToolRunner } from './index.mjs';

/** Run the original PLINK 2 CLI in a fresh worker with real pthreads. */
export class Plink2Runner extends ToolRunner {
  constructor({ baseUrl = new URL('../dist/plink2/', import.meta.url), moduleUrl, ...options } = {}) {
    const base = new URL(baseUrl);
    if (!['file:', 'http:', 'https:'].includes(base.protocol) || !base.pathname.endsWith('/')) {
      throw new TypeError('baseUrl must be a file, http or https directory URL ending in /');
    }
    // The application runs on a pthread. callMain() returns before it exits.
    super({ ...options, moduleUrl: moduleUrl ?? new URL('plink2.mjs', base), waitForExit: true });
  }

  async run(args = [], options = {}) {
    return super.run(preparePlink2Args(args), options);
  }
}

/** Apply browser resource limits without interpreting scientific arguments. */
export function preparePlink2Args(args) {
  if (!Array.isArray(args) || args.some(arg => typeof arg !== 'string' || arg.includes('\0'))) {
    throw new TypeError('args must be an array of strings without NUL characters');
  }
  const flag = value => /^-{1,2}[^-]/.test(value) ? value.replace(/^-{1,2}/, '').split('=')[0] : null;
  if (args.some(arg => ['script', 'rerun'].includes(flag(arg)))) {
    throw new TypeError('PLINK 2 --script and --rerun are unavailable in this runner; provide the arguments directly.');
  }
  // This upstream first-argument utility bypasses the analysis arena and rejects
  // any other flags, including --threads and --memory.
  if (['zst-decompress', 'zd'].includes(flag(args[0] ?? ''))) return [...args];
  if (args.some(arg => ['help', 'version'].includes(flag(arg)))) return [...args];
  const prepared = [];
  let hasThreads = false;
  let hasMemory = false;
  for (let index = 0; index < args.length; index++) {
    const name = flag(args[index]);
    if (['threads', 'thread-num', 'num_threads'].includes(name)) {
      if (hasThreads || args[index].includes('=')) throw new TypeError('Specify PLINK 2 --threads once, followed by 1 or 2.');
      const value = args[++index];
      if (!/^[12]$/.test(value ?? '')) throw new RangeError('PLINK 2 supports --threads 1 or --threads 2 in this runner.');
      prepared.push('--threads', value);
      hasThreads = true;
    } else if (name === 'memory') {
      if (hasMemory || args[index].includes('=')) throw new TypeError('Specify PLINK 2 --memory once, followed by 640–1024 MiB and optional require.');
      let value = args[++index];
      const requireFirst = value === 'require';
      if (requireFirst) value = args[++index];
      if (!/^\d+$/.test(value ?? '') || Number(value) < 640 || Number(value) > 1024) {
        throw new RangeError('PLINK 2 --memory must be an integer from 640 to 1024 MiB in this runner.');
      }
      prepared.push('--memory', value);
      if (requireFirst || args[index + 1] === 'require') {
        prepared.push('require');
        if (!requireFirst) index++;
      }
      hasMemory = true;
    } else {
      prepared.push(args[index]);
    }
  }
  if (!hasThreads) prepared.push('--threads', '1');
  if (!hasMemory) prepared.push('--memory', '640', 'require');
  return prepared;
}
