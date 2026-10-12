// Tests only byte transport into a seekable file; native equivalence uses Prodigal.
import createMockTool from './mock-tool.mjs';
export default async function createTransportProbe(options) {
  const module = await createMockTool(options);
  module.callMain = args => {
    const index = args.findIndex(value => value === '-i' || value === '-I');
    options.print(JSON.stringify(args));
    const path = args[index + 1];
    module.FS.writeFile('/work/copied.bin', module.FS.readFile(path.startsWith('/') ? path : '/work/' + path));
    return 0;
  };
  return module;
}
