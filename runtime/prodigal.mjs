import { ToolRunner, workspacePath } from './index.mjs';

/** Original Prodigal in a fresh worker, with seekable input for its training pass. */
export class ProdigalRunner extends ToolRunner {
  constructor({ baseUrl = new URL('../dist/prodigal/', import.meta.url), moduleUrl, ...options } = {}) {
    const base = new URL(baseUrl);
    if (!base.pathname.endsWith('/')) throw new TypeError('baseUrl must end in /');
    super({ ...options, moduleUrl: moduleUrl ?? new URL('prodigal.mjs', base) });
  }

  async run(args = [], options = {}) {
    if (!Array.isArray(args) || args.some(value => typeof value !== 'string' || value.includes('\0'))) {
      throw new TypeError('args must be an array of strings without NUL characters');
    }
    // Prodigal's original single-genome path rewinds input after learning its
    // model. Emscripten stdin is a character device rather than a seekable file.
    // Preserve CLI input precedence and materialize only an explicitly supplied
    // stdin stream when there is no -i option. No sequence bytes are changed.
    if (options.stdin === undefined || hasInput(args)) return super.run(args, options);
    const files = options.files ?? [], outputs = options.outputs ?? [];
    if (!Array.isArray(files) || !Array.isArray(outputs)) throw new TypeError('files and outputs must be arrays');
    const directories = options.outputDirectories ?? [];
    if (!Array.isArray(directories)) throw new TypeError('outputDirectories must be an array');
    const occupied = new Set([...files.map(file => workspacePath(file.path)), ...outputs.map(workspacePath), ...directories.map(workspacePath)]);
    for (const name of [...occupied]) {
      for (let parent = name.slice(0, name.lastIndexOf('/')); parent !== '/work'; parent = parent.slice(0, parent.lastIndexOf('/'))) occupied.add(parent);
    }
    let path = '/work/__prodigal_stdin.fna', suffix = 0;
    while (occupied.has(path)) path = `/work/__prodigal_stdin_${++suffix}.fna`;
    const result = await super.run([...args, '-i', path], {
      ...options, files: [...files, { path, data: options.stdin }], stdin: new Uint8Array(),
    });
    // Internal transport files are not caller outputs, including when collecting
    // an enclosing output directory.
    return { ...result, files: result.files.filter(file => file.path !== path) };
  }
}

function hasInput(args) {
  const values = new Set(['-a', '-A', '-d', '-f', '-F', '-g', '-G', '-o', '-O', '-p', '-P', '-s', '-S', '-t', '-T']);
  for (let index = 0; index < args.length; index++) {
    if (args[index] === '-i' || args[index] === '-I') return true;
    if (values.has(args[index])) index++;
  }
  return false;
}
