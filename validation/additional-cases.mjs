/** Differential cases for trimming and spliced alignment of real sequencing reads. */
import { writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { isDeepStrictEqual } from 'node:util';

const cleanPath = path => path.replace(/^\/work\//, '');
const fileMap = result => new Map(result.files.map(file => [cleanPath(file.path), file.data]));
const readName = header => header.split(/\s/, 1)[0].replace(/\/[12]$/, '');

export function fastqRecords(data, label) {
  if (Buffer.from(data).some(byte => byte > 127)) throw new Error(`${label}: FASTQ is not ASCII`);
  const text = Buffer.from(data).toString('ascii');
  if (!text) return [];
  const lines = text.split('\n');
  if (lines.pop() !== '' || lines.length % 4) throw new Error(`${label}: incomplete four-line FASTQ`);
  const records = [];
  for (let i = 0; i < lines.length; i += 4) {
    const [header, sequence, separator, quality] = lines.slice(i, i + 4);
    if (!/^@\S/.test(header) || !separator.startsWith('+') || sequence.length !== quality.length || !/^[A-Za-z]*$/.test(sequence) || !/^[!-~]*$/.test(quality)) throw new Error(`${label}: invalid FASTQ record ${i / 4 + 1}`);
    records.push({ name: readName(header.slice(1)), header, sequence, quality });
  }
  return records;
}

export function checkPairs(first, second, label) {
  if (first.length !== second.length || first.some((read, index) => read.name !== second[index].name)) throw new Error(`${label}: paired FASTQ names or order differ`);
}

export async function runAdditionalCases(context) {
  const { options, tools, reportDir, summary, expectations, hash, wasm, native, compare, success, loadDataset } = context;
  const trimmingTools = tools.filter(tool => ['seqtk', 'adapterremoval'].includes(tool));
  const rnaTools = tools.filter(tool => ['hisat2', 'minimap2'].includes(tool));

  async function saveResult(id, kind, result) {
    await writeFile(join(reportDir, `${id}.${kind}.stdout.txt`), result.stdout);
    await writeFile(join(reportDir, `${id}.${kind}.stderr.txt`), result.stderr);
    for (const [name, bytes] of fileMap(result)) await writeFile(join(reportDir, `${id}.${kind}.${name.replaceAll('/', '_')}`), bytes);
  }

  async function exact(expected, actual, id) {
    const equal = Buffer.from(expected).equals(Buffer.from(actual));
    await writeFile(join(reportDir, `${id}.json`), JSON.stringify({
      equal, policy: 'byte for byte; no fields ignored',
      nativeBytes: expected.length, actualBytes: actual.length,
      nativeSha256: hash(expected), actualSha256: hash(actual),
    }, null, 2) + '\n');
    if (!equal) throw new Error(`${id}: output bytes differ; see reports/${options.engine}/${id}.json`);
  }

  async function triplet(tool, id, args, files, outputs = []) {
    const original = await native(tool, 'native', args, files, outputs, id);
    const patched = await native(tool, 'patched-native', args, files, outputs, id);
    const port = await wasm(tool, args, files, outputs);
    for (const [kind, result] of [['native', original], ['patched-native', patched], ['wasm', port]]) {
      await saveResult(id, kind, result);
      success(result, `${id} ${kind}`);
    }
    return { original, patched, port };
  }

  function expectedMetrics(tool, dataset, name, record) {
    const expected = expectations[tool]?.[dataset]?.[name];
    if (expected && typeof expected === 'object') for (const [key, value] of Object.entries(expected)) {
      if (!isDeepStrictEqual(record[key], value)) throw new Error(`${record.id}: ${key} differs from pinned native fixture expectation`);
    }
  }

  function passed(record) {
    record.status = 'passed';
    console.log(`PASS ${options.engine} ${record.id}`);
  }

  async function outputCase(tool, dataset, name, args, files, outputs, inspect) {
    const id = `${tool}-${dataset}-${name}`;
    const record = { id, args, status: 'running' };
    summary.cases.push(record);
    const result = await triplet(tool, id, args, files, outputs);
    for (const key of outputs.length ? outputs : ['stdout']) {
      const select = response => key === 'stdout' ? response.stdout : fileMap(response).get(key);
      const original = select(result.original);
      await exact(original, select(result.patched), `${id}-${key}-patched`);
      await exact(original, select(result.port), `${id}-${key}-wasm`);
    }
    if (inspect) await inspect(result.original, record);
    expectedMetrics(tool, dataset, name, record);
    passed(record);
    return result;
  }

  for (const dataset of trimmingTools.length ? (options.dataset === 'all' ? ['human', 'yeast'] : [options.dataset]) : []) {
    const files = (await loadDataset(dataset)).filter(file => file.path.endsWith('.fastq'));
    const source = new Map(files.map(file => [file.path, fastqRecords(file.data, `${dataset}/${file.path}`)]));
    const first = source.get('reads_1.fastq'), second = source.get('reads_2.fastq');
    checkPairs(first, second, `${dataset} input`);
    const expectedPairs = expectations.bwa[dataset]?.pairs ?? expectations.rna?.[dataset]?.pairs;
    if (first.length !== expectedPairs) throw new Error(`${dataset}: input pair count differs from expected fixture`);
    if (trimmingTools.includes('seqtk')) {
      const cases = [
        ['quality', ['trimfq', 'reads_1.fastq']],
        ['quality-threshold', ['trimfq', '-q', '0.01', '-l', '25', 'reads_1.fastq']],
        ['fixed-ends', ['trimfq', '-b', '5', '-e', '7', 'reads_1.fastq']],
        ['fixed-length', ['trimfq', '-L', '80', 'reads_1.fastq']],
      ];
      let baseline;
      for (const [name, args] of cases) {
        const result = await outputCase('seqtk', dataset, name, args, files, [], (output, record) => {
          const reads = fastqRecords(output.stdout, record.id);
          if (reads.length !== first.length) throw new Error(`${record.id}: trimming lost or added reads`);
          let shortened = 0;
          for (let i = 0; i < reads.length; i++) {
            const read = reads[i], original = first[i];
            if (read.name !== original.name) throw new Error(`${record.id}: read names/order changed`);
            const positions = [...original.sequence.matchAll(new RegExp(`(?=${read.sequence})`, 'g'))].map(match => match.index);
            if (!positions.some(start => original.quality.slice(start, start + read.sequence.length) === read.quality)) throw new Error(`${record.id}: trimmed bases and qualities are not a contiguous input segment`);
            if (name === 'fixed-ends' && read.sequence.length !== original.sequence.length - 12) throw new Error(`${record.id}: fixed end length is incorrect`);
            if (name === 'fixed-length' && read.sequence.length !== Math.min(80, original.sequence.length)) throw new Error(`${record.id}: fixed length is incorrect`);
            if (read.sequence.length < original.sequence.length) shortened++;
          }
          record.records = reads.length; record.shortenedReads = shortened;
          if (shortened === 0) throw new Error(`${record.id}: fixture did not exercise trimming`);
          const expected = expectations.seqtk?.[dataset]?.[name];
          if (expected !== undefined && shortened !== expected) throw new Error(`${record.id}: expected ${expected} shortened reads, found ${shortened}`);
        });
        if (name === 'quality') baseline = result;
      }
      // seqtk treats mates as independent files; both files must retain the same names/order.
      await outputCase('seqtk', dataset, 'quality-mate2', ['trimfq', 'reads_2.fastq'], files, [], (output, record) => {
        const reads = fastqRecords(output.stdout, record.id);
        checkPairs(fastqRecords(baseline.original.stdout, record.id), reads, record.id);
        record.records = reads.length;
      });
      await outputCase('seqtk', dataset, 'empty', ['trimfq', 'reads_1.fastq'], [{ path: 'reads_1.fastq', data: new Uint8Array() }], [], (output, record) => {
        if (output.stdout.length) throw new Error(`${record.id}: empty input generated reads`);
        record.records = 0;
      });
      const id = `seqtk-${dataset}-repeat`;
      const repeated = await wasm('seqtk', ['trimfq', 'reads_1.fastq'], files);
      await saveResult(id, 'wasm', repeated); success(repeated, id);
      await exact(baseline.original.stdout, repeated.stdout, id);
      const record = { id }; summary.cases.push(record); passed(record);
    }

    if (trimmingTools.includes('adapterremoval')) {
      const base = ['--file1', 'reads_1.fastq', '--basename', 'trimmed', '--threads', '1', '--seed', '42'];
      const paired = [...base, '--file2', 'reads_2.fastq'];
      const pairedOutputs = ['trimmed.pair1.truncated', 'trimmed.pair2.truncated', 'trimmed.singleton.truncated', 'trimmed.discarded', 'trimmed.settings'];
      const cases = [
        ['paired-adapters', paired, pairedOutputs],
        ['quality', [...paired, '--trimns', '--trimqualities', '--minquality', '20', '--minlength', '35'], pairedOutputs],
        ['fixed-ends', [...paired, '--trim5p', '5', '--trim3p', '7'], pairedOutputs],
        ['collapse', [...paired, '--collapse'], [...pairedOutputs, 'trimmed.collapsed', 'trimmed.collapsed.truncated']],
        ['single', [...base, '--trimns', '--trimqualities', '--minquality', '20'], ['trimmed.truncated', 'trimmed.discarded', 'trimmed.settings']],
      ];
      let baseline;
      for (const [name, args, outputs] of cases) {
        const result = await outputCase('adapterremoval', dataset, name, args, files, outputs, (output, record) => {
          const map = fileMap(output);
          const parsed = new Map(outputs.filter(path => !path.endsWith('.settings')).map(path => [path, fastqRecords(map.get(path), `${record.id}/${path}`)]));
          if (parsed.has('trimmed.pair1.truncated')) checkPairs(parsed.get('trimmed.pair1.truncated'), parsed.get('trimmed.pair2.truncated'), record.id);
          const names = new Set(first.map(read => read.name));
          for (const reads of parsed.values()) for (const read of reads) if (!names.has(read.name.replace(/^(M_|MT_)/, ''))) throw new Error(`${record.id}: unknown output read ${read.name}`);
          record.records = Object.fromEntries([...parsed].map(([path, reads]) => [path, reads.length]));
          record.retainedReads = [...parsed].filter(([path]) => path !== 'trimmed.discarded').reduce((sum, [, reads]) => sum + reads.length, 0);
          if (!record.retainedReads) throw new Error(`${record.id}: no reads retained`);
          const settings = Buffer.from(map.get('trimmed.settings')).toString('utf8');
          record.adapterTrimmedReads = [...settings.matchAll(/^Number of reads with adapters\[\d+\]: (\d+)$/gm)].reduce((sum, match) => sum + Number(match[1]), 0);
          record.retainedNucleotides = [...parsed].filter(([path]) => path !== 'trimmed.discarded').reduce((sum, [, reads]) => sum + reads.reduce((count, read) => count + read.sequence.length, 0), 0);
          if (name === 'paired-adapters' && !record.adapterTrimmedReads) throw new Error(`${record.id}: fixture did not exercise adapter trimming`);
          if (name === 'collapse' && !record.records['trimmed.collapsed'] && !record.records['trimmed.collapsed.truncated']) throw new Error(`${record.id}: fixture did not exercise overlapping-pair collapse`);
          if (name === 'quality' || name === 'fixed-ends') {
            const baselineBases = [...fileMap(baseline.original)].filter(([path]) => path !== 'trimmed.settings' && path !== 'trimmed.discarded').reduce((sum, [path, bytes]) => sum + fastqRecords(bytes, path).reduce((count, read) => count + read.sequence.length, 0), 0);
            if (record.retainedNucleotides >= baselineBases) throw new Error(`${record.id}: quality/end trimming did not reduce retained bases`);
          }
        });
        if (name === 'paired-adapters') baseline = result;
      }
      const id = `adapterremoval-${dataset}-repeat`;
      const repeated = await wasm('adapterremoval', paired, files, pairedOutputs);
      await saveResult(id, 'wasm', repeated); success(repeated, id);
      for (const output of pairedOutputs) await exact(fileMap(baseline.original).get(output), fileMap(repeated).get(output), `${id}-${output}`);
      const record = { id }; summary.cases.push(record); passed(record);
    }
  }

  const rnaDatasets = options.dataset === 'all' ? ['rna-human', 'rna-yeast'] : [options.dataset.startsWith('rna-') ? options.dataset : `rna-${options.dataset}`];
  for (const dataset of rnaTools.length ? rnaDatasets : []) {
    const files = await loadDataset(dataset);
    const reference = files.filter(file => file.path === 'reference.fa');
    const reads = files.filter(file => file.path.endsWith('.fastq'));
    const readCounts = Object.fromEntries(reads.map(file => [file.path, fastqRecords(file.data, `${dataset}/${file.path}`).length]));
    const expectedPairs = expectations.rna?.[dataset]?.pairs;
    if (!Number.isInteger(expectedPairs) || readCounts['reads_1.fastq'] !== expectedPairs || readCounts['reads_2.fastq'] !== expectedPairs) throw new Error(`${dataset}: RNA fixture counts missing or incorrect in validation/expected.json`);
    for (const tool of rnaTools) {
      const indexNames = tool === 'hisat2' ? Array.from({ length: 8 }, (_, index) => `reference.${index + 1}.ht2`) : ['reference.mmi'];
      const indexArgs = tool === 'hisat2' ? ['build', '-p', '1', 'reference.fa', 'reference'] : ['-t', '1', '-x', 'splice:sr', '-d', 'reference.mmi', 'reference.fa'];
      const index = await outputCase(tool, dataset, 'index', indexArgs, reference, indexNames);
      const indexes = index.original.files.map(file => ({ ...file, path: cleanPath(file.path) }));
      const portIndexes = index.port.files.map(file => ({ ...file, path: cleanPath(file.path) }));
      const input = [...files, ...indexes];
      const base = tool === 'hisat2' ? ['align', '-p', '1', '--seed', '0', '-x', 'reference'] : ['-t', '1', '-ax', 'splice:sr'];
      const pairArgs = tool === 'hisat2' ? ['-1', 'reads_1.fastq', '-2', 'reads_2.fastq'] : ['reference.mmi', 'reads_1.fastq', 'reads_2.fastq'];
      const singleArgs = tool === 'hisat2' ? ['-U', 'reads_1.fastq'] : ['reference.mmi', 'reads_1.fastq'];
      const parameterArgs = tool === 'hisat2' ? ['--min-intronlen', '25', '--max-intronlen', '100000', '--score-min', 'L,0,-0.3'] : ['-A', '3', '--MD'];
      const annotationArgs = tool === 'hisat2' ? ['--known-splicesite-infile', 'splice-sites.tsv'] : ['--junc-bed', 'junctions.bed'];
      const annotationName = annotationArgs[1];
      if (!files.some(file => file.path === annotationName)) throw new Error(`${dataset}: missing ${annotationName}`);
      const cases = [
        ['paired', [...base, ...pairArgs], input, expectedPairs * 2, true],
        ['single', [...base, ...singleArgs], input, expectedPairs, true],
        ['parameters', [...base, ...parameterArgs, ...pairArgs], input, expectedPairs * 2, true],
        ['annotation', [...base, ...annotationArgs, ...pairArgs], input, expectedPairs * 2, true],
        ['empty', [...base, ...pairArgs], input.map(file => file.path.endsWith('.fastq') ? { ...file, data: new Uint8Array() } : file), 0, false],
      ];
      if (tool === 'minimap2') cases.push(['small-batches', [...base, '-K', '10000', ...pairArgs], input, expectedPairs * 2, true]);
      let baseline;
      for (const [name, args, caseFiles, expectedPrimary, requireSpliced] of cases) {
        const id = `${tool}-${dataset}-${name}`;
        const record = { id, args, status: 'running' }; summary.cases.push(record);
        const result = await triplet(tool, id, args, caseFiles);
        const rows = Buffer.from(result.original.stdout).toString('utf8').split('\n').filter(line => line && !line.startsWith('@')).map(line => line.split('\t'));
        record.records = rows.length;
        record.primaryRecords = rows.filter(row => !(Number(row[1]) & 0x900)).length;
        record.splicedRecords = rows.filter(row => /\d+N/.test(row[5])).length;
        if (record.primaryRecords !== expectedPrimary) throw new Error(`${id}: expected ${expectedPrimary} primary records, found ${record.primaryRecords}`);
        if (requireSpliced && !record.splicedRecords) throw new Error(`${id}: real RNA fixture produced no spliced alignments`);
        if (tool === 'minimap2' && name === 'parameters') {
          for (const row of rows.filter(row => !(Number(row[1]) & 4))) {
            if (!row.some(field => field.startsWith('MD:Z:'))) throw new Error(`${id}: requested MD tag missing from mapped read`);
          }
        }
        expectedMetrics(tool, dataset, name, record);
        await compare('sam', result.original.stdout, result.patched.stdout, `${id}-patched`);
        await compare('sam', result.original.stdout, result.port.stdout, `${id}-wasm`);
        passed(record);
        if (name === 'paired') baseline = result;
      }
      const crossInput = [...files, ...portIndexes];
      const paired = [...base, ...pairArgs];
      const cross = await triplet(tool, `${tool}-${dataset}-cross-index`, paired, crossInput);
      for (const [kind, result] of [['native', cross.original], ['patched-native', cross.patched], ['wasm', cross.port]]) await compare('sam', baseline.original.stdout, result.stdout, `${tool}-${dataset}-cross-index-${kind}`);
      const crossRecord = { id: `${tool}-${dataset}-cross-index` }; summary.cases.push(crossRecord); passed(crossRecord);
      const id = `${tool}-${dataset}-repeat`;
      const repeated = await wasm(tool, paired, input);
      await saveResult(id, 'wasm', repeated); success(repeated, id);
      await compare('sam', baseline.original.stdout, repeated.stdout, id);
      const record = { id }; summary.cases.push(record); passed(record);
      if (tool === 'hisat2') await outputCase(tool, dataset, 'inspect', ['inspect', 'reference'], indexes, []);
    }
  }

  const errorArgs = {
    seqtk: ['trimfq', 'missing.fastq'],
    adapterremoval: ['--file1', 'missing.fastq', '--basename', 'trimmed', '--threads', '1', '--seed', '42'],
    hisat2: ['align', '-p', '1', '-x', 'missing', '-U', 'missing.fastq'],
    minimap2: ['-t', '1', '-ax', 'splice:sr', 'missing.fa', 'missing.fastq'],
  };
  for (const tool of [...trimmingTools, ...rnaTools]) {
    const id = `${tool}-missing-input`, args = errorArgs[tool];
    const original = await native(tool, 'native', args, [], [], id);
    const patched = await native(tool, 'patched-native', args, [], [], id);
    const port = await wasm(tool, args, []);
    for (const [kind, result] of [['native', original], ['patched-native', patched], ['wasm', port]]) {
      await saveResult(id, kind, result);
      if (!result.exitCode || result.exitCode !== original.exitCode) throw new Error(`${id}: expected native exit ${original.exitCode}, ${kind} returned ${result.exitCode}`);
    }
    const record = { id, args, exitCode: original.exitCode }; summary.cases.push(record); passed(record);
  }

  // Native upstream supports threads. These cases establish an explicit port
  // restriction separately and do not claim equivalence to native threading.
  const threadCases = {
    adapterremoval: [[['--file1', 'missing.fastq', '--threads', '2'], 1, 'supports --threads 1 only']],
    hisat2: [[['align', '-p', '2', '-x', 'missing', '-U', 'missing.fastq'], 2, 'supports only -p 1'], [['build', '-p', '2', 'missing.fa', 'reference'], 2, 'supports only -p 1']],
    minimap2: [[['-t', '2', 'missing.fa', 'missing.fastq'], 2, 'supports one thread']],
  };
  for (const tool of [...trimmingTools, ...rnaTools].filter(tool => tool in threadCases)) {
    for (const [index, [args, exitCode, diagnostic]] of threadCases[tool].entries()) {
      const id = `${tool}-unsupported-threads${index ? '-build' : ''}`;
      const results = [
        ['patched-native', await native(tool, 'patched-native', args, [], [], id)],
        ['wasm', await wasm(tool, args, [])],
      ];
      for (const [kind, result] of results) {
        await saveResult(id, kind, result);
        if (result.exitCode !== exitCode || !Buffer.from(result.stderr).toString('utf8').includes(diagnostic)) throw new Error(`${id} ${kind}: expected exit ${exitCode} and explicit single-thread diagnostic`);
      }
      const record = { id, args, exitCode, scope: 'port restriction' };
      summary.cases.push(record); passed(record);
    }
  }
}
