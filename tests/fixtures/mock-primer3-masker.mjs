// Transport fixture only: copy the final named input and record literal argv.
// Scientific masking is independently compared with the original native CLI.
import createMockTool from './mock-tool.mjs';

export default async function createMockMasker(options) {
  const module = await createMockTool(options);
  const callMain = module.callMain.bind(module);
  module.callMain = args => {
    const input = args.at(-1);
    const path = input.startsWith('/') ? input : `/work/${input}`;
    module.FS.writeFile('/work/copied.fasta', module.FS.readFile(path));
    return callMain(['args', ...args]);
  };
  return module;
}
