#!/usr/bin/env python3
"""Verify, fetch, or regenerate the compact real Kraken2/Bracken fixture.

Run --fetch to retrieve checksum-pinned sources, --regenerate to reproduce and
compare the committed fixture, or --verify (the default) for offline checks.
Regeneration compiles unchanged upstream native tools and requires a C++
compiler, make, Perl, Bash, and Python; no WebAssembly toolchain is needed.
--update-manifest explicitly replaces the fixture and its expected identities.
"""
from __future__ import annotations

import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'datasets/metagenomics'
LOCK = DATA / 'provenance/sources.lock.json'
READ_PAIRS = 4096
READ_LENGTH = 151
WINDOW_BASES = 1_000_000
REFERENCES = (
    ('GCF_000005845.2_ASM584v2_genomic.fna.gz', 'NC_000913.3', 562, 'Escherichia coli'),
    ('GCF_000006945.2_ASM694v2_genomic.fna.gz', 'NC_003197.2', 28901, 'Salmonella enterica'),
    ('GCF_000006925.2_ASM692v2_genomic.fna.gz', 'NC_004337.2', 623, 'Shigella flexneri'),
)


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def identity(path):
    return {'bytes': path.stat().st_size, 'sha256': digest(path)}


def matches(path, expected):
    return path.is_file() and identity(path) == {key: expected[key] for key in ('bytes', 'sha256')}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def sources():
    return json.loads(LOCK.read_text())


def source_path(record):
    name = record['cachePath']
    path = ROOT / name
    if not name.startswith('build/metagenomics-sources/') or not path.resolve().is_relative_to((ROOT / 'build/metagenomics-sources').resolve()):
        raise RuntimeError('Unsafe source cache path: ' + name)
    return path


def fetch(record):
    path = source_path(record)
    if matches(path, record):
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    if record.get('snapshot'):
        original = DATA / record['snapshot']
        if not matches(original, record):
            raise RuntimeError('Pinned metadata snapshot is missing or changed: ' + str(original))
        shutil.copyfile(original, path)
        return path
    partial = path.with_name(path.name + '.partial')
    headers = {'User-Agent': 'genomics-wasm-fixtures/1', 'Accept-Encoding': 'identity'}
    if 'range' in record:
        start, end = record['range']
        headers['Range'] = f'bytes={start}-{end}'
    print('Downloading ' + record['url'], flush=True)
    try:
        request = urllib.request.Request(record['url'], headers=headers)
        with urllib.request.urlopen(request, timeout=180) as response, partial.open('wb') as target:
            if 'range' in record and (response.status != 206 or response.headers.get('Content-Range') != record['contentRange']):
                raise RuntimeError('Server did not return the pinned byte range')
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


def gzip_copy(source, target):
    with target.open('wb') as raw:
        with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0, compresslevel=9) as compressed:
            compressed.write(source.read_bytes())


def reads(output, available):
    """Read only complete selected records; the pinned gzip prefixes are truncated."""
    ids = []
    for mate in (1, 2):
        names = []
        target = output / f'reads_{mate}.fastq'
        with gzip.open(available[f'ERR2984773_{mate}.prefix-0-2097151.fastq.gz'], 'rb') as source, target.open('wb') as dest:
            for _ in range(READ_PAIRS):
                record = [source.readline() for _ in range(4)]
                title, sequence, plus, quality = record
                if (not all(line.endswith(b'\n') for line in record) or not title.startswith(b'@')
                        or plus != b'+\n' or len(sequence.rstrip(b'\n')) != READ_LENGTH
                        or len(quality.rstrip(b'\n')) != READ_LENGTH):
                    raise RuntimeError('Source prefix does not contain the expected unchanged FASTQ records')
                names.append(title.split()[0][1:].decode('ascii'))
                dest.write(b''.join(record))
        if len(set(names)) != READ_PAIRS:
            raise RuntimeError('Duplicate FASTQ IDs')
        ids.append(names)
        gzip_copy(target, target.with_suffix('.fastq.gz'))
    if ids[0] != ids[1]:
        raise RuntimeError('FASTQ mate identifiers differ')
    return {'pairs': READ_PAIRS, 'basesPerRead': READ_LENGTH, 'firstReadId': ids[0][0], 'lastReadId': ids[0][-1]}


def references(output, available):
    fasta = output / 'db/library/reference.fna'
    fasta.parent.mkdir(parents=True)
    metadata = []
    with fasta.open('w') as dest, (output / 'db/seqid2taxid.map').open('w') as mapping:
        for archive, accession, taxid, scientific_name in REFERENCES:
            with gzip.open(available[archive], 'rt', encoding='ascii') as source:
                title = source.readline().rstrip('\n')
                if not title.startswith('>' + accession + ' '):
                    raise RuntimeError('Unexpected reference accession: ' + title)
                pieces = []
                for line in source:
                    if line.startswith('>'):
                        break
                    pieces.append(line.strip())
            sequence = ''.join(pieces)
            window = sequence[:WINDOW_BASES]
            if len(window) != WINDOW_BASES or set(window) - set('ACGTRYSWKMBDHVN'):
                raise RuntimeError('Unexpected reference window')
            identifier = f'{accession}_1_{WINDOW_BASES}'
            dest.write(f'>{identifier} {scientific_name}; unchanged reference window 1-{WINDOW_BASES}\n')
            for offset in range(0, len(window), 80):
                dest.write(window[offset:offset + 80] + '\n')
            mapping.write(f'{identifier}\t{taxid}\n')
            metadata.append({'source': archive, 'accession': accession, 'taxid': taxid, 'name': scientific_name,
                             'sequenceId': identifier, 'start': 1, 'end': WINDOW_BASES, 'coordinateSystem': '1-based inclusive',
                             'chromosomeBases': len(sequence), 'windowSequenceSha256': hashlib.sha256(window.encode()).hexdigest()})
    return metadata


def taxonomy(output, available):
    records = {1: (1, 'no rank', 'root')}
    for taxon in ET.parse(available['taxonomy-562-28901-623.xml']).getroot().findall('Taxon'):
        parent = 1
        for node in [*taxon.findall('LineageEx/Taxon'), taxon]:
            taxid = int(node.findtext('TaxId'))
            value = (parent, node.findtext('Rank'), node.findtext('ScientificName'))
            if taxid in records and records[taxid] != value:
                raise RuntimeError('Inconsistent source taxonomy lineage')
            records[taxid] = value
            parent = taxid
    directory = output / 'db/taxonomy'
    directory.mkdir()
    with (directory / 'nodes.dmp').open('w') as nodes, (directory / 'names.dmp').open('w') as names:
        for taxid, (parent, rank, name) in sorted(records.items()):
            fields = [taxid, parent, rank, '', 0, 0, 1, 0, 0, 0, 0, 0, '']
            nodes.write('\t|\t'.join(map(str, fields)) + '\t|\n')
            names.write(f'{taxid}\t|\t{name}\t|\t\t|\tscientific name\t|\n')
    return len(records)


def run(command, cwd, *, env=None):
    print('+ ' + ' '.join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), cwd=cwd, env=env, check=True)


def unpack(path, directory):
    directory.mkdir(parents=True)
    with tarfile.open(path) as archive:
        archive.extractall(directory, filter='data')
    children = list(directory.iterdir())
    if len(children) != 1 or not children[0].is_dir():
        raise RuntimeError('Expected a single upstream source directory')
    return children[0]


def build_native(work, available):
    kraken = unpack(available['kraken2-2.17.2.tar.gz'], work / 'kraken')
    bracken = unpack(available['bracken-3.1.tar.gz'], work / 'bracken')
    package = work / 'kraken2-package'
    run(['bash', 'install_kraken2.sh', package], kraken)
    run(['make', '-j2'], bracken / 'src')
    return package, bracken


def generate(output, work, available):
    output.mkdir(parents=True)
    metadata = {'reads': reads(output, available), 'references': references(output, available)}
    metadata['taxonomyNodes'] = taxonomy(output, available)
    kraken, bracken = build_native(work, available)
    db = output / 'db'
    run([kraken / 'kraken2-build', '--build', '--db', db, '--threads', '1', '--no-masking'], output)
    # Upstream writes uninitialised C++ struct padding. Its nine declared fields
    # are retained byte-for-byte; only bytes documented by abi-check.cc are zeroed.
    options = bytearray((db / 'opts.k2d').read_bytes())
    if len(options) != 64:
        raise RuntimeError('Unexpected native Kraken IndexOptions ABI')
    options[33:40] = b'\0' * 7
    options[60:64] = b'\0' * 4
    (db / 'opts.k2d').write_bytes(options)
    run([kraken / 'kraken2', '--db', db, '--threads', '1', '--output', output / 'sample.kraken',
         '--report', output / 'sample.report', '--paired', output / 'reads_1.fastq', output / 'reads_2.fastq'], output)
    run([kraken / 'kraken2', '--db', db, '--threads', '1', '--output', db / 'database.kraken', db / 'library/reference.fna'], output)
    run([bracken / 'src/kmer2read_distr', '--seqid2taxid', db / 'seqid2taxid.map', '--taxonomy', db / 'taxonomy',
         '--kraken', db / 'database.kraken', '--output', db / 'database151mers.kraken', '-k', '35', '-l', '151', '-t', '1'], output)
    run([sys.executable, '-B', bracken / 'src/generate_kmer_distribution.py', '-i', db / 'database151mers.kraken',
         '-o', db / 'database151mers.kmer_distrib'], output)
    # The unchanged native helper appends an EOF artifact with uninitialised
    # taxid bytes. The unchanged Python converter ignores its empty hit list.
    # Retain the deterministic model, not this invalid temporary helper record.
    (db / 'database151mers.kraken').unlink()
    run([sys.executable, '-B', bracken / 'src/est_abundance.py', '-i', output / 'sample.report', '-o', output / 'sample.bracken',
         '-k', db / 'database151mers.kmer_distrib', '-l', 'S', '-t', '1'], output)
    calls = Counter()
    for line in (output / 'sample.kraken').read_text().splitlines():
        row = line.split('\t')
        calls[int(row[2])] += 1
    abundance = [line.split('\t') for line in (output / 'sample.bracken').read_text().splitlines()[1:]]
    metadata['observations'] = {'readPairs': sum(calls.values()), 'classifiedReadPairs': sum(value for key, value in calls.items() if key),
                                'unclassifiedReadPairs': calls[0], 'directAssignmentsByTaxid': {str(key): value for key, value in sorted(calls.items())},
                                'brackenSpecies': len(abundance), 'brackenAddedReads': sum(int(row[4]) for row in abundance)}
    # These temporary upstream build products are neither inputs nor examples.
    keep = {'hash.k2d', 'opts.k2d', 'taxo.k2d', 'seqid2taxid.map', 'database.kraken',
            'database151mers.kmer_distrib', 'library', 'taxonomy'}
    for child in db.iterdir():
        if child.name not in keep:
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    return metadata


def fixture_files():
    return {str(path.relative_to(DATA)): {**identity(path), 'generated': path.is_relative_to(DATA / 'zymo')}
            for path in sorted(DATA.rglob('*')) if path.is_file() and path != DATA / 'manifest.json'}


def verify(manifest, *, generated=None):
    if manifest.get('schema_version') != 1 or manifest.get('sources') != sources():
        raise RuntimeError('Manifest and source lock differ')
    actual = fixture_files()
    if set(actual) != set(manifest['files']):
        raise RuntimeError('Fixture file inventory differs from manifest')
    if generated is not None:
        expected_generated = {name.removeprefix('zymo/') for name, record in manifest['files'].items() if record['generated']}
        actual_generated = {str(path.relative_to(generated)) for path in generated.rglob('*') if path.is_file()}
        if actual_generated != expected_generated:
            raise RuntimeError('Regenerated file inventory differs from manifest')
    for name, record in manifest['files'].items():
        path = (generated / name.removeprefix('zymo/')) if generated is not None and record['generated'] else DATA / name
        if not matches(path, record):
            raise RuntimeError('Fixture identity mismatch: ' + name)
    for name, record in manifest['regeneration']['files'].items():
        if not matches(ROOT / name, record):
            raise RuntimeError('Regeneration recipe changed: ' + name)
    print(f"Verified {len(manifest['files'])} real metagenomics fixture files", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--verify', action='store_true')
    mode.add_argument('--fetch', action='store_true')
    mode.add_argument('--regenerate', action='store_true')
    parser.add_argument('--update-manifest', action='store_true', help='explicitly replace fixtures and their expected identities; requires --regenerate')
    args = parser.parse_args()
    if args.update_manifest and not args.regenerate:
        parser.error('--update-manifest requires --regenerate')
    if not (args.fetch or args.regenerate):
        verify(json.loads((DATA / 'manifest.json').read_text()))
        return
    available = {name: fetch(record) for name, record in sources().items()}
    if args.fetch:
        print(f'Verified {len(available)} pinned source files', flush=True)
        return
    (ROOT / 'build').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='metagenomics-regenerate-', dir=ROOT / 'build') as temporary:
        work = Path(temporary)
        output = work / 'zymo'
        metadata = generate(output, work, available)
        if args.update_manifest:
            destination = DATA / 'zymo'
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(output, destination)
            manifest = {'schema_version': 1, 'datasets': {'zymo': metadata}, 'sources': sources(), 'files': fixture_files(),
                        'regeneration': {'script': 'scripts/fetch-metagenomics-test-data.py',
                                         'files': {'scripts/fetch-metagenomics-test-data.py': identity(Path(__file__))},
                                         'requirements': ['Python 3.12+', 'C++ compiler with OpenMP', 'make', 'Perl', 'Bash'],
                                         'sourcePrograms': {'kraken2': '2.17.2', 'bracken': '3.1'},
                                         'optionsPadding': 'Zero native IndexOptions padding bytes 33..39 and 60..63; preserve every declared field.'}}
            write_json(DATA / 'manifest.json', manifest)
            verify(manifest)
        else:
            manifest = json.loads((DATA / 'manifest.json').read_text())
            if metadata != manifest['datasets']['zymo']:
                raise RuntimeError('Regenerated dataset metadata differs')
            verify(manifest, generated=output)


if __name__ == '__main__':
    main()
