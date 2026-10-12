#!/usr/bin/env python3
"""Verify or reproduce authentic SRR8599150 single-cell BUS fixtures.

Default/--verify checks the complete frozen inventory offline. --fetch caches
checksum-pinned original files; --regenerate reproduces every generated input
without changing the manifest. Only --regenerate --update-manifest updates it.
No biological sequences, barcode observations or tool-result goldens are invented.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import struct
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'datasets/bustools'
CACHE = ROOT / 'build/bustools-sources'
LOCK = DATA / 'provenance/sources.lock.json'
SCRIPT = 'scripts/fetch-bustools-test-data.py'
RECORD = struct.Struct('<QQiIII')
LARGE_MINIMUM = 10_000


def identity(path):
    path = Path(path)
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'bytes': path.stat().st_size, 'sha256': digest}


def matches(path, record):
    return path.is_file() and identity(path) == {key: record[key] for key in ('bytes', 'sha256')}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def sources():
    return json.loads(LOCK.read_text())


def fetch(record):
    path = ROOT / record['cachePath']
    if not path.resolve().is_relative_to(CACHE.resolve()):
        raise RuntimeError('Unsafe source cache path: ' + record['cachePath'])
    if matches(path, record):
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + '.partial')
    request = urllib.request.Request(record['url'], headers={
        'User-Agent': 'genomics-wasm-fixtures/1', 'Accept-Encoding': 'identity',
    })
    print('Downloading ' + record['url'], flush=True)
    try:
        with urllib.request.urlopen(request, timeout=180) as response, partial.open('wb') as target:
            remaining = record['bytes']
            while remaining:
                block = response.read(min(1024 * 1024, remaining))
                if not block:
                    break
                target.write(block)
                remaining -= len(block)
            if remaining or response.read(1):
                raise RuntimeError('Source size differs from pinned size')
        if not matches(partial, record):
            raise RuntimeError('Source checksum mismatch: ' + record['url'])
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)
    return path


def sequence(number, length):
    return ''.join('ACGT'[(number >> (2 * i)) & 3] for i in range(length - 1, -1, -1))


def bus_header(stream):
    raw = stream.read(20)
    magic, version, barcode, umi, text_length = struct.unpack('<4sIIII', raw)
    if (magic, version, barcode, umi) != (b'BUS\x00', 1, 16, 10):
        raise RuntimeError('Unexpected source BUS header')
    text = stream.read(text_length)
    if len(text) != text_length:
        raise RuntimeError('Truncated source BUS header')
    return raw + text


def read_bus(path):
    with path.open('rb') as stream:
        header = bus_header(stream)
        records = list(RECORD.iter_unpack(stream.read()))
    return header, records


def bus_stats(records):
    return {'records': len(records), 'barcodes': len({r[0] for r in records}),
            'umis': len({r[1] for r in records}), 'equivalenceClasses': len({r[2] for r in records}),
            'readMultiplicity': sum(r[3] for r in records),
            'duplicateBarcodeUmiEcRecords': len(records) - len({r[:3] for r in records}),
            'multiplicityHistogram': dict(sorted(Counter(r[3] for r in records).items()))}


def fastq_records(raw):
    lines = raw.decode('ascii').splitlines()
    if len(lines) % 4:
        raise RuntimeError('Truncated source FASTQ')
    records = []
    for offset in range(0, len(lines), 4):
        name, bases, plus, qualities = lines[offset:offset + 4]
        if not name.startswith('@SRR8599150.') or not plus.startswith('+') or len(bases) != len(qualities):
            raise RuntimeError('Unexpected authentic FASTQ record')
        records.append((name.split()[0], bases))
    return records


def filter_genemap(raw, transcripts, unversioned=False):
    wanted = set(transcripts)
    lines = []
    seen = set()
    for line in raw.decode('ascii').splitlines():
        fields = line.split('\t')
        if unversioned:
            fields[0] = fields[0].split('.')[0]
        if fields[0] in wanted:
            if fields[0] in seen:
                raise RuntimeError('Gene map has ambiguous duplicate transcript identifiers')
            lines.append('\t'.join(fields))
            seen.add(fields[0])
    if {line.split('\t')[0] for line in lines} != wanted:
        raise RuntimeError('Transcript-to-gene mapping is incomplete')
    return ('\n'.join(lines) + '\n').encode('ascii')


def generate(output, available):
    folder = output / 'bustools'
    folder.mkdir()
    provenance = {'accession': 'SRR8599150', 'files': {}}

    def put(name, raw, origin):
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        provenance['files'][name] = origin

    def copy(source, name):
        put(name, available[source].read_bytes(), {'source': source, 'transformation': 'Unchanged source bytes.'})

    for source, name in [('kb-output.bus', 'input.bus'), ('kb-matrix.ec', 'matrix.ec'),
                         ('kb-transcripts.txt', 'transcripts.txt'), ('kb-R1.fastq', 'R1.fastq'),
                         ('kb-R2.fastq', 'R2.fastq')]:
        copy(source, 'bustools/' + name)
    copy('kb-LICENSE', 'provenance/kb-python-LICENSE')
    header, records = read_bus(folder / 'input.bus')
    transcripts = (folder / 'transcripts.txt').read_text().splitlines()
    gene_map = filter_genemap(available['transcripts_to_genes.txt'].read_bytes(), transcripts)
    put('bustools/transcripts-to-genes.txt', gene_map, {
        'source': 'transcripts_to_genes.txt',
        'transformation': 'Retain unchanged source lines whose transcript identifiers occur in transcripts.txt, preserving source order.',
    })

    # Keeping every on-list barcode at Hamming distance 0 or 1 preserves exactly
    # the original correction candidate set for this fixture, including ambiguity.
    candidates = set()
    for barcode in {sequence(r[0], 16) for r in records}:
        candidates.add(barcode)
        for position in range(16):
            for base in 'ACGT':
                candidates.add(barcode[:position] + base + barcode[position + 1:])
    whitelist = [line for line in available['10xv2_whitelist.txt'].read_text().splitlines() if line in candidates]
    put('bustools/whitelist.txt', ('\n'.join(whitelist) + '\n').encode('ascii'), {
        'source': '10xv2_whitelist.txt',
        'transformation': 'Retain unchanged on-list lines at Hamming distance <=1 from any encoded input barcode; preserve original order. This preserves every possible default bustools correct candidate for these inputs.',
    })

    # Source flags count ambiguous bases, rather than read numbers. Create a
    # separately labelled transport fixture, replacing only flags by actual
    # zero-based FASTQ pair offsets. Original raw BUS remains byte-identical.
    reads1 = fastq_records((folder / 'R1.fastq').read_bytes())
    reads2 = fastq_records((folder / 'R2.fastq').read_bytes())
    if [name for name, _ in reads1] != [name for name, _ in reads2]:
        raise RuntimeError('FASTQ pair identifiers do not match')
    locations = defaultdict(deque)
    for offset, (_, bases) in enumerate(reads1):
        locations[bases].append(offset)
    indexed, matched = [], []
    for record in records:
        bases = sequence(record[0], 16) + sequence(record[1], 10)
        possible = [key for key, offsets in locations.items() if offsets and
                    all(left == right or left == 'N' for left, right in zip(key, bases))]
        if len(possible) != 1:
            raise RuntimeError('BUS record has no unique matching authentic read sequence')
        offset = locations[possible[0]].popleft()
        indexed.append((*record[:4], offset, record[5]))
        matched.append({'record': len(indexed) - 1, 'readPairIndex': offset,
                        'readId': reads1[offset][0][1:], 'originalFlags': record[4]})
    put('bustools/read-index.bus', header + b''.join(RECORD.pack(*record) for record in indexed), {
        'inputs': ['bustools/input.bus', 'bustools/R1.fastq', 'bustools/R2.fastq'],
        'transformation': 'Replace flags only with the verified zero-based matching FASTQ read-pair index; retain barcode, UMI, EC, count and padding. Match unchanged 26-base R1 barcode/UMI with source N wildcard and consume duplicate matches in original order.',
    })
    write_json(output / 'provenance/read-index-map.json', matched)

    # Select a prefix through a complete barcode, retaining molecule groups at
    # the boundary. The source is already corrected and sorted; it is not a
    # raw correction benchmark or representative subsample of the experiment.
    selected, previous = [], None
    with gzip.open(available['output.correct.sort.bus.gz'], 'rb') as stream:
        large_header = bus_header(stream)
        while raw := stream.read(RECORD.size):
            if len(raw) != RECORD.size:
                raise RuntimeError('Truncated released BUS record')
            record = RECORD.unpack(raw)
            if len(selected) >= LARGE_MINIMUM and record[0] != previous:
                break
            selected.append(record)
            previous = record[0]
    ec_ids = sorted({record[2] for record in selected})
    ec_wanted = set(ec_ids)
    ec_original = {}
    with gzip.open(available['matrix.ec.gz'], 'rt', encoding='ascii') as stream:
        for line in stream:
            number, values = line.rstrip('\n').split('\t')
            if int(number) in ec_wanted:
                ec_original[int(number)] = [int(value) for value in values.split(',')]
    if set(ec_original) != ec_wanted:
        raise RuntimeError('Source BUS references an absent EC')
    with gzip.open(available['transcripts.txt.gz'], 'rt', encoding='ascii') as stream:
        all_transcripts = stream.read().splitlines()
    annotated = {line.split('\t')[0].split('.')[0] for line in available['transcripts_to_genes.txt'].read_text().splitlines()}
    missing = sorted({all_transcripts[value] for values in ec_original.values() for value in values if all_transcripts[value] not in annotated})
    excluded_ecs = {number for number, values in ec_original.items() if any(all_transcripts[value] not in annotated for value in values)}
    original_records = len(selected)
    source_indices = [index for index, record in enumerate(selected) if record[2] not in excluded_ecs]
    selected = [record for record in selected if record[2] not in excluded_ecs]
    ec_ids = sorted({record[2] for record in selected})
    ec_original = {number: ec_original[number] for number in ec_ids}
    transcript_ids = sorted({value for values in ec_original.values() for value in values})
    ec_lookup = {value: number for number, value in enumerate(ec_ids)}
    tx_lookup = {value: number for number, value in enumerate(transcript_ids)}
    large_transcripts = [all_transcripts[number] for number in transcript_ids]
    remapped = [(*record[:2], ec_lookup[record[2]], *record[3:]) for record in selected]
    large = folder / 'large'
    large.mkdir()
    put('bustools/large/input.bus', large_header + b''.join(RECORD.pack(*record) for record in remapped), {
        'source': 'output.correct.sort.bus.gz', 'sourceFirstRecord': 0, 'sourcePrefixRecords': original_records,
        'retainedRecords': len(selected), 'excludedRecords': original_records - len(selected),
        'unannotatedTranscriptIds': missing, 'excludedOriginalEcIds': sorted(excluded_ecs),
        'transformation': 'Select first >=10000 records, extending through the complete last barcode; exclude records whose EC contains any transcript absent from the released gene map. Replace only EC integer with ascending-original-ID compact index. Preserve every retained barcode, UMI, count, flag, padding byte and header. Exact original source-record indices are recorded separately.',
    })
    ec_text = ''.join(str(ec_lookup[old]) + '\t' + ','.join(str(tx_lookup[value]) for value in ec_original[old]) + '\n' for old in ec_ids)
    put('bustools/large/matrix.ec', ec_text.encode('ascii'), {
        'source': 'matrix.ec.gz', 'transformation': 'Select referenced equivalence classes; losslessly reindex ECs and transcripts in ascending original integer-ID order. Transcript memberships are unchanged.',
    })
    put('bustools/large/transcripts.txt', ('\n'.join(large_transcripts) + '\n').encode('ascii'), {
        'source': 'transcripts.txt.gz', 'transformation': 'Select unchanged identifiers of every transcript referenced by retained ECs, in original integer-ID order.',
    })
    large_gene_map = filter_genemap(available['transcripts_to_genes.txt'].read_bytes(), large_transcripts, unversioned=True)
    put('bustools/large/transcripts-to-genes.txt', large_gene_map, {
        'source': 'transcripts_to_genes.txt', 'transformation': 'Select gene-map rows for retained transcripts; remove only transcript version suffix to match the unversioned released transcripts.txt identifiers. Preserve source gene identifiers, symbols and order.',
    })
    barcodes = sorted({sequence(record[0], 16) for record in selected})
    put('bustools/large/whitelist.txt', ('\n'.join(barcodes) + '\n').encode('ascii'), {
        'inputs': ['bustools/large/input.bus'],
        'transformation': 'Decode distinct observed barcodes from the already corrected source. This is an observed-barcode list, not an independent technology on-list or a cell-calling result.',
    })
    put('provenance/large-ec-map.tsv', ('compact_ec\toriginal_ec\n' + ''.join(f'{new}\t{old}\n' for old, new in ec_lookup.items())).encode(), {
        'transformation': 'Exact reversible integer EC-ID map for selected source records.',
    })
    put('provenance/large-record-map.tsv', ('fixture_record\tsource_record\n' + ''.join(f'{new}\t{old}\n' for new, old in enumerate(source_indices))).encode(), {
        'transformation': 'Exact zero-based source BUS record offsets for every retained record.',
    })
    put('provenance/large-transcript-map.tsv', ('compact_transcript\toriginal_transcript\ttranscript_id\n' + ''.join(f'{new}\t{old}\t{all_transcripts[old]}\n' for old, new in tx_lookup.items())).encode(), {
        'transformation': 'Exact reversible transcript integer-ID map; biological transcript identifiers unchanged.',
    })
    # These are explicit test/user parameters, not biological cell classes.
    parameters = {
        'capture-barcodes.txt': '\n'.join(barcodes[:len(barcodes) // 2]) + '\n',
        'barcode-map.txt': barcodes[0] + '\t' + barcodes[1] + '\n',
        'clusters.txt': ''.join(barcode + '\t' + str(index % 2) + '\n' for index, barcode in enumerate(barcodes)),
        'capture-transcripts.txt': '\n'.join(large_transcripts[:len(large_transcripts) // 2]) + '\n',
    }
    for name, text in parameters.items():
        put('bustools/large/' + name, text.encode('ascii'), {
            'transformation': 'Deterministic user parameter: ' + {
                'capture-barcodes.txt': 'capture first floor(N/2) observed barcodes in lexicographic order.',
                'barcode-map.txt': 'project first observed barcode onto the second; all other barcodes retain upstream default behavior.',
                'clusters.txt': 'assign observed barcodes alternately to groups 0 and 1; arbitrary test groups with no biological interpretation.',
                'capture-transcripts.txt': 'capture first floor(N/2) transcript identifiers in retained source order.',
            }[name], 'biologicalAnnotation': False,
        })
    write_json(output / 'provenance/fixture-sources.json', provenance)
    return {'bustools': {
        'accession': 'SRR8599150', 'species': 'Mus musculus', 'technology': '10x Chromium v2',
        'observations': {'raw': {**bus_stats(records), 'fastqReadPairs': len(reads1),
                                 'transcripts': len(transcripts), 'onlistBarcodes': len(whitelist),
                                 'genes': len({line.split('\t')[1] for line in gene_map.decode().splitlines()})},
                         'large': {**bus_stats(selected), 'sourcePrefixRecords': original_records,
                                   'recordsExcludedForMissingGeneAnnotation': original_records - len(selected),
                                   'transcripts': len(large_transcripts),
                                   'genes': len({line.split('\t')[1] for line in large_gene_map.decode().splitlines()})}},
        'requiredEffects': [
            'Raw real reads contain repeated barcode-UMI-EC observations; sorting aggregates records.',
            'Default barcode correction uses the complete candidate subset from the original upstream on-list.',
            'Count produces nonempty gene and transcript-compatibility matrices with exact native/WebAssembly agreement.',
            'Larger already corrected subset exercises 10000+ records, many barcodes/UMIs/genes, and read multiplicity versus UMI counts.',
            'Capture, projection and cluster inputs are explicit arbitrary user parameters, not inferred biological labels.',
        ],
    }}


def generated_name(name):
    return name.startswith('bustools/') or name in (
        'provenance/fixture-sources.json', 'provenance/kb-python-LICENSE',
        'provenance/read-index-map.json', 'provenance/large-ec-map.tsv',
        'provenance/large-transcript-map.tsv', 'provenance/large-record-map.tsv')


def fixture_files():
    return {str(path.relative_to(DATA)): {**identity(path), 'generated': generated_name(str(path.relative_to(DATA)))}
            for path in sorted(DATA.rglob('*')) if path.is_file() and path != DATA / 'manifest.json'}


def verify(manifest, generated=None):
    if manifest.get('schema_version') != 1 or manifest.get('sources') != sources():
        raise RuntimeError('Manifest and source lock differ')
    if set(fixture_files()) != set(manifest['files']):
        raise RuntimeError('Fixture inventory differs from frozen manifest')
    if generated is not None:
        expected = {name for name, record in manifest['files'].items() if record['generated']}
        actual = {str(path.relative_to(generated)) for path in generated.rglob('*') if path.is_file()}
        if actual != expected:
            raise RuntimeError('Regenerated inventory differs from frozen manifest')
    for name, record in manifest['files'].items():
        path = generated / name if generated is not None and record['generated'] else DATA / name
        if not matches(path, record):
            raise RuntimeError('Fixture identity mismatch: ' + name)
    for name, record in manifest['regeneration']['files'].items():
        if not matches(ROOT / name, record):
            raise RuntimeError('Regeneration recipe changed: ' + name)
    print(f"Verified {len(manifest['files'])} authentic bustools fixture files", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--verify', action='store_true')
    mode.add_argument('--fetch', action='store_true')
    mode.add_argument('--regenerate', action='store_true')
    parser.add_argument('--update-manifest', action='store_true', help='Explicitly replace fixtures and identities; requires --regenerate')
    args = parser.parse_args()
    if args.update_manifest and not args.regenerate:
        parser.error('--update-manifest requires --regenerate')
    if not (args.fetch or args.regenerate):
        verify(json.loads((DATA / 'manifest.json').read_text()))
        return
    available = {name: fetch(record) for name, record in sources().items()}
    if args.fetch:
        print(f'Verified {len(available)} pinned original source files', flush=True)
        return
    (ROOT / 'build').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='bustools-regenerate-', dir=ROOT / 'build') as temporary:
        output = Path(temporary)
        datasets = generate(output, available)
        # JSON object keys are strings; freeze that representation for subsequent
        # regeneration comparisons (multiplicity histograms use integer counts).
        datasets = json.loads(json.dumps(datasets))
        if args.update_manifest:
            for path in sorted(output.rglob('*')):
                if path.is_file():
                    destination = DATA / path.relative_to(output)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, destination)
            manifest = {'schema_version': 1, 'datasets': datasets, 'sources': sources(), 'files': fixture_files(),
                        'regeneration': {'script': SCRIPT, 'files': {SCRIPT: identity(ROOT / SCRIPT)},
                                         'requirements': ['Python 3.11+'],
                                         'method': 'Unchanged genuine input copies, lossless source-record selection and reversible integer-ID remapping; no inferred tool-result goldens.'}}
            write_json(DATA / 'manifest.json', manifest)
            verify(manifest)
        else:
            manifest = json.loads((DATA / 'manifest.json').read_text())
            if datasets != manifest['datasets']:
                raise RuntimeError('Regenerated dataset metadata differs from frozen manifest')
            verify(manifest, generated=output)


if __name__ == '__main__':
    main()
