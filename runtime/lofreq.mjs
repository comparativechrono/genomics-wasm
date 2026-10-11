import { ToolRunner } from './index.mjs';
import { runSomatic } from './lofreq-somatic.mjs';

/** LoFreq's native commands, plus its serial somatic workflow, in fresh workers. */
export class LoFreqRunner extends ToolRunner {
  constructor({ moduleUrl = new URL('../dist/lofreq/lofreq.mjs', import.meta.url), ...options } = {}) {
    super({ moduleUrl, ...options });
  }

  async run(args = [], options = {}) {
    if (Array.isArray(args) && args[0] === 'somatic') {
      return runSomatic(args.slice(1), options, (command, stageOptions) => super.run(command, stageOptions));
    }
    return super.run(args, options);
  }
}
