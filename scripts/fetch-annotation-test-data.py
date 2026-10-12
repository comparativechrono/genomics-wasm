#!/usr/bin/env python3
"""Verify or reproduce real Prodigal and HMMER biological fixtures.

Default/--verify checks the complete committed inventory offline. --fetch caches
checksum-pinned originals. --regenerate reproduces inputs from those originals
and checks the frozen manifest without changing it. Only an explicit developer
--regenerate --update-manifest replaces inputs and their expected identities.
No biological sequences or expected tool results are synthesized.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'datasets/annotation'
LOCK = DATA / 'provenance/sources.lock.json'
CACHE = ROOT / 'build/annotation-sources'
SCRIPT = 'scripts/fetch-annotation-test-data.py'
WINDOW = 60_000
REFERENCES = (
    ('ecoli', 'GCF_000005845.2_ASM584v2_genomic.fna.gz', 'NC_000913.3', 562, 'Escherichia coli K-12 MG1655'),
    ('salmonella', 'GCF_000006945.2_ASM694v2_genomic.fna.gz', 'NC_003197.2', 28901, 'Salmonella enterica Typhimurium LT2'),
)
TUTORIAL_FILES = {
    'globins.sto': 'globins4.sto', 'globins.hmm': 'globins4.hmm',
    'pkinase.sto': 'Pkinase.sto', 'pkinase.hmm': 'Pkinase.hmm',
    'fn3.sto': 'fn3.sto', 'fn3.hmm': 'fn3.hmm',
    'made1.sto': 'MADE1.sto', 'made1.hmm': 'MADE1.hmm',
    'hbb-human.faa': 'HBB_HUMAN', 'globins45.faa': 'globins45.fa',
    'dna-target.fa': 'dna_target.fa',
}


def identity(path):
    path = Path(path)
    with path.open('rb') as stream:
        value = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'bytes': path.stat().st_size, 'sha256': value}


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
    print('Downloading ' + record['url'], flush=True)
    request = urllib.request.Request(record['url'], headers={
        'User-Agent': 'genomics-wasm-fixtures/1', 'Accept-Encoding': 'identity',
    })
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


def fasta_records(text):
    records = []
    for line in text.splitlines():
        if line.startswith('>'):
            records.append([line[1:], ''])
        elif line.strip():
            if not records:
                raise RuntimeError('FASTA has sequence before its first header')
            records[-1][1] += line.strip()
    ids = [title.split()[0] for title, _ in records]
    if not records or len(ids) != len(set(ids)):
        raise RuntimeError('FASTA is empty or has duplicate identifiers')
    return records


def fasta_text(records):
    return ''.join('>' + title + '\n' + ''.join(sequence[i:i+80] + '\n'
                   for i in range(0, len(sequence), 80)) for title, sequence in records)


def alignment_metadata(raw):
    sequences = {}
    for line in raw.decode('ascii').splitlines():
        if line.strip() and not line.startswith('#') and line != '//':
            name, part = line.split()
            sequences[name] = sequences.get(name, '') + part
    lengths = {len(sequence) for sequence in sequences.values()}
    if not sequences or len(lengths) != 1:
        raise RuntimeError('Invalid source Stockholm alignment')
    return {'sequences': len(sequences), 'alignmentColumns': lengths.pop()}


def generate(output, available):
    provenance = {'coordinateSystem': '1-based inclusive', 'files': {}}
    prodigal = {'references': [], 'observations': {}, 'requiredEffects': [
        'Nonempty gene, protein, nucleotide and score outputs from each real bacterial window.',
        'Native and WebAssembly single-genome and metagenomic gene coordinates, strands and translations agree.',
        'Output formats and closed-end / translation-table options exercise distinct supported behavior.',
    ]}
    records = []
    for label, archive, accession, taxid, name in REFERENCES:
        with gzip.open(available[archive], 'rt', encoding='ascii') as stream:
            title = stream.readline().rstrip('\n')
            if not title.startswith('>' + accession + ' '):
                raise RuntimeError('Unexpected reference chromosome: ' + title)
            pieces = []
            for line in stream:
                if line.startswith('>'):
                    break
                pieces.append(line.strip())
        chromosome = ''.join(pieces)
        sequence = chromosome[:WINDOW]
        if len(sequence) != WINDOW or set(sequence) - set('ACGTRYSWKMBDHVN'):
            raise RuntimeError('Unexpected reference alphabet or length')
        identifier = f'{accession}_1_{WINDOW}'
        record = [f'{identifier} {name}; unchanged chromosome window 1-{WINDOW}', sequence]
        records.append(record)
        path = f'prodigal/{label}.fna'
        (output / path).parent.mkdir(parents=True, exist_ok=True)
        (output / path).write_text(fasta_text([record]), encoding='ascii')
        metadata = {'source': archive, 'accession': accession, 'taxid': taxid, 'name': name,
                    'sequenceId': identifier, 'start': 1, 'end': WINDOW,
                    'chromosomeBases': len(chromosome),
                    'windowSequenceSha256': hashlib.sha256(sequence.encode('ascii')).hexdigest()}
        prodigal['references'].append(metadata)
        provenance['files'][path] = {**metadata, 'transformation': 'First 60000 chromosome bases unchanged; descriptive FASTA header and 80-column wrapping.'}
    (output / 'prodigal/combined.fna').write_text(fasta_text(records), encoding='ascii')
    provenance['files']['prodigal/combined.fna'] = {
        'inputs': ['prodigal/ecoli.fna', 'prodigal/salmonella.fna'],
        'transformation': 'Concatenate the two complete FASTA inputs in the listed order.',
    }
    prodigal['observations'] = {'chromosomes': 2, 'basesPerChromosome': WINDOW, 'combinedBases': 2 * WINDOW}
    hmmer = {'alignments': {}, 'profiles': {}, 'observations': {}, 'requiredEffects': [
        'Globin searches recover diverse authentic globins and reject the Sevenless receptor negative control.',
        'Sevenless has protein-kinase and repeated fibronectin-type-III domains; globins are negative controls for these profiles.',
        'Search thresholds change reported hits; native and WebAssembly scores and domain coordinates agree within documented tolerances.',
        'MADE1 nucleotide searches recover repeat hits on both strands of the real human chromosome-1 fragment.',
    ]}
    (output / 'hmmer').mkdir()
    with tarfile.open(available['hmmer-3.4.tar.gz'], 'r:gz') as archive:
        def member(name):
            full = 'hmmer-3.4/' + name
            item = archive.getmember(full)
            if not item.isfile():
                raise RuntimeError('Source member is not a regular file: ' + full)
            raw = archive.extractfile(item).read()
            return raw, {'source': 'hmmer-3.4.tar.gz', 'member': full,
                         'sourceMemberBytes': len(raw), 'sourceMemberSha256': hashlib.sha256(raw).hexdigest()}
        for name, original in TUTORIAL_FILES.items():
            raw, origin = member('tutorial/' + original)
            path = 'hmmer/' + name
            (output / path).write_bytes(raw)
            provenance['files'][path] = {**origin, 'transformation': 'Unchanged upstream tutorial file; filename only renamed.'}
            if name.endswith('.sto'):
                hmmer['alignments'][name] = alignment_metadata(raw)
            elif name.endswith('.hmm'):
                fields = {}
                for line in raw.decode('ascii').splitlines():
                    if line.startswith('HMM '):
                        break
                    parts = line.split(maxsplit=1)
                    if len(parts) == 2 and parts[0] in ('NAME', 'ACC', 'LENG', 'ALPH'):
                        fields[parts[0]] = parts[1]
                hmmer['profiles'][name] = fields
        raw, origin = member('tutorial/7LESS_DROME')
        swissprot = raw.decode('ascii')
        match = re.search(r'^SQ   SEQUENCE\s+(\d+) AA;[^\n]*\n(.*?)^//', swissprot, re.M | re.S)
        if not match:
            raise RuntimeError('Cannot parse the Sevenless Swiss-Prot sequence record')
        sequence = re.sub(r'\s+', '', match.group(2))
        if len(sequence) != int(match.group(1)) or len(sequence) != 2554 or set(sequence) - set('ACDEFGHIKLMNPQRSTVWY'):
            raise RuntimeError('Unexpected Sevenless protein sequence')
        sevenless = fasta_text([['7LESS_DROME P13368 Protein sevenless; Drosophila melanogaster; unchanged UniProt sequence version 2', sequence]])
        (output / 'hmmer/sevenless-drome.faa').write_text(sevenless, encoding='ascii')
        provenance['files']['hmmer/sevenless-drome.faa'] = {**origin,
            'transformation': 'Extract unchanged SQ amino-acid sequence from Swiss-Prot; descriptive FASTA header and 80-column wrapping.',
            'accession': 'P13368', 'sequenceVersion': 2, 'entryVersion': 172, 'aminoAcids': len(sequence)}
        proteins = fasta_records((output / 'hmmer/globins45.faa').read_text())
        proteins += fasta_records((output / 'hmmer/hbb-human.faa').read_text())
        proteins += fasta_records(sevenless)
        (output / 'hmmer/proteins.faa').write_text(fasta_text(proteins), encoding='ascii')
        provenance['files']['hmmer/proteins.faa'] = {
            'inputs': ['hmmer/globins45.faa', 'hmmer/hbb-human.faa', 'hmmer/sevenless-drome.faa'],
            'transformation': 'Concatenate unchanged protein sequences, retaining original identifiers and order; 80-column wrapping.',
        }
        raw, origin = member('LICENSE')
        (output / 'provenance').mkdir(exist_ok=True)
        (output / 'provenance/HMMER-LICENSE').write_bytes(raw)
        provenance['files']['provenance/HMMER-LICENSE'] = {**origin, 'transformation': 'Unchanged upstream license.'}
    dna = fasta_records((output / 'hmmer/dna-target.fa').read_text())
    hmmer['observations'] = {'proteinTargets': len(proteins), 'globinTargets': 46,
        'sevenlessAminoAcids': 2554, 'aminoAcids': sum(len(sequence) for _, sequence in proteins),
        'nucleotideTargets': len(dna), 'nucleotideBases': sum(len(sequence) for _, sequence in dna),
        'targetIds': [title.split()[0] for title, _ in proteins]}
    write_json(output / 'provenance/fixture-sources.json', provenance)
    return {'prodigal': prodigal, 'hmmer': hmmer}


def generated_name(name):
    return name.startswith(('prodigal/', 'hmmer/')) or name in (
        'provenance/fixture-sources.json', 'provenance/HMMER-LICENSE')


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
    print(f"Verified {len(manifest['files'])} real annotation fixture files", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--verify', action='store_true')
    mode.add_argument('--fetch', action='store_true')
    mode.add_argument('--regenerate', action='store_true')
    parser.add_argument('--update-manifest', action='store_true', help='Explicitly replace fixtures and expected identities; requires --regenerate')
    args = parser.parse_args()
    if args.update_manifest and not args.regenerate:
        parser.error('--update-manifest requires --regenerate')
    if not (args.fetch or args.regenerate):
        verify(json.loads((DATA / 'manifest.json').read_text()))
        return
    available = {name: fetch(record) for name, record in sources().items()}
    if args.fetch:
        print(f'Verified {len(available)} pinned source archives', flush=True)
        return
    (ROOT / 'build').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='annotation-regenerate-', dir=ROOT / 'build') as temporary:
        output = Path(temporary)
        datasets = generate(output, available)
        if args.update_manifest:
            for path in sorted(output.rglob('*')):
                if path.is_file():
                    destination = DATA / path.relative_to(output)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, destination)
            manifest = {'schema_version': 1, 'datasets': datasets, 'sources': sources(), 'files': fixture_files(),
                        'regeneration': {'script': SCRIPT, 'files': {SCRIPT: identity(ROOT / SCRIPT)},
                                         'requirements': ['Python 3.11+'],
                                         'sourcePrograms': {'hmmer': '3.4'},
                                         'method': 'Archive extraction and lossless sequence selection; no tool-result files are generated or frozen.'}}
            write_json(DATA / 'manifest.json', manifest)
            verify(manifest)
        else:
            manifest = json.loads((DATA / 'manifest.json').read_text())
            if datasets != manifest['datasets']:
                raise RuntimeError('Regenerated dataset metadata differs from frozen manifest')
            verify(manifest, generated=output)


if __name__ == '__main__':
    main()
