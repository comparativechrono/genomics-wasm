#!/usr/bin/env python3
"""Reproduce frozen synthetic and public upstream repeat-expansion fixtures.

BAM, BAI, BGZF, FAI and GZI generation uses Python's standard library only.
The fixed-Huffman/RLE BGZF encoder deliberately avoids zlib-version-dependent
compression choices. Public reads and genotypes remain unchanged.
"""
from __future__ import annotations
import argparse
import bisect
import hashlib
import json
from pathlib import Path
import shutil
import struct
import tarfile
import tempfile
import urllib.request
import zlib

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'datasets/repeats'
CACHE = ROOT / 'build/repeat-fixture-sources'
BLOCK = 56000
# Historical diagnostic, independently rerun with the two references. These
# are observations from a pristine native release, not a replacement for the
# current native/patched-native/WASM validation gate. Keep regeneration free
# of native-tool dependencies and preserve the observed hashes verbatim.
MASK_NATIVE_EQUIVALENCE_PROOF = {
    'status': 'historical diagnostic; exact full-source versus masked-reference comparison',
    'tool': 'REViewer', 'version': '0.2.7',
    'upstreamCommit': '7cd3be55cd67566d23d9272600e484edc72edbf8',
    'nativeBinarySha256': '0b24fdb4d95bca77903598e1aeb8d188de4e7d2fd730cdcde4b9c56280a9b539',
    'locus': 'BEAN1',
    'procedure': 'Run pristine native REViewer twice with the unchanged public BAMlet, VCF and catalog, using the full pinned source FASTA then reference.fa.gz; compare all three output files byte for byte. Commands are in datasets/repeats/README.md.',
    'argv': ['--reads', 'BEAN1_HG00684.bam', '--vcf', 'HG00684.vcf', '--catalog', 'catalog.json',
             '--locus', 'BEAN1', '--reference', '{fullOrMaskedReference}', '--output-prefix', '{prefix}'],
    'outputs': {
        'BEAN1.svg': {'bytes': 609711, 'sha256': '0d3c4d9cd9095d2db38971169a02ee2fc452c830ef6e90697a1a4b77bf68ee7d'},
        'metrics.tsv': {'bytes': 54, 'sha256': '60e503bc92a23ad15c9cd524c3ac2a6d28f812c2cedcdd42310a6c1e63edec6e'},
        'phasing.tsv': {'bytes': 77, 'sha256': '645a6855fe64bad0fb1eb2e61531c0a6250e54e7489d683d7017955525a6b4a3'},
    },
}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()


def write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw.encode() if isinstance(raw, str) else raw)


def fixed_deflate(raw):
    """One RFC1951 fixed-Huffman block, literals and same-byte runs only."""
    out = bytearray()
    bits = count = 0

    def emit(value, width):
        nonlocal bits, count
        bits |= value << count
        count += width
        while count >= 8:
            out.append(bits & 255)
            bits >>= 8
            count -= 8

    def symbol(value):
        if value <= 143:
            code, width = 0x30 + value, 8
        elif value <= 255:
            code, width = 0x190 + value - 144, 9
        elif value <= 279:
            code, width = value - 256, 7
        else:
            code, width = 0xC0 + value - 280, 8
        emit(int(f'{code:0{width}b}'[::-1], 2), width)

    lengths = [3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31,
               35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258]
    extras = [0] * 8 + [1] * 4 + [2] * 4 + [3] * 4 + [4] * 4 + [5] * 4 + [0]
    emit(3, 3)  # final block, fixed Huffman codes
    pos = 0
    while pos < len(raw):
        run = 0
        if pos and raw[pos] == raw[pos - 1]:
            while run < 258 and pos + run < len(raw) and raw[pos + run] == raw[pos - 1]:
                run += 1
        if run >= 3:
            idx = bisect.bisect_right(lengths, run) - 1
            symbol(257 + idx)
            emit(run - lengths[idx], extras[idx])
            emit(0, 5)  # fixed distance code 0: one byte backwards
            pos += run
        else:
            symbol(raw[pos])
            pos += 1
    symbol(256)
    if count:
        out.append(bits & 255)
    return bytes(out)


def bgzf(raw):
    blocks, offsets = [], []
    compressed = 0
    for position in range(0, len(raw), BLOCK):
        chunk = raw[position:position + BLOCK]
        payload = fixed_deflate(chunk)
        size = 18 + len(payload) + 8
        assert size <= 65536
        header = b'\x1f\x8b\x08\x04' + bytes(4) + b'\x00\xff\x06\x00BC\x02\x00' + struct.pack('<H', size - 1)
        block = header + payload + struct.pack('<II', zlib.crc32(chunk), len(chunk))
        assert zlib.decompress(block, 31) == chunk
        offsets.append((compressed, position))
        blocks.append(block)
        compressed += len(block)
    eof = bytes.fromhex('1f8b08040000000000ff0600424302001b0003000000000000000000')
    return b''.join(blocks) + eof, offsets, compressed


def fasta(sequences):
    raw = bytearray()
    entries = []
    for name, sequence in sequences:
        raw.extend(f'>{name}\n'.encode())
        entries.append(f'{name}\t{len(sequence)}\t{len(raw)}\t60\t61\n')
        raw.extend(('\n'.join(sequence[i:i + 60] for i in range(0, len(sequence), 60)) + '\n').encode())
    return bytes(raw), ''.join(entries).encode()


def fasta_index(raw):
    entries, offset, current = [], 0, None
    for line in raw.splitlines(keepends=True):
        if line.startswith(b'>'):
            if current:
                entries.append(current)
            current = [line[1:].split()[0].decode(), 0, offset + len(line), None, None]
        elif current:
            bases = len(line.rstrip(b'\r\n'))
            if current[3] is None:
                current[3:5] = [bases, len(line)]
            current[1] += bases
        offset += len(line)
    if current:
        entries.append(current)
    return ''.join('\t'.join(map(str, item)) + '\n' for item in entries).encode()


def reg2bin(start, end):
    end = max(start + 1, end) - 1
    for shift, base in [(14, 4681), (17, 585), (20, 73), (23, 9), (26, 1)]:
        if start >> shift == end >> shift:
            return base + (start >> shift)
    return 0


def bam(sequences, records):
    """Encode coordinate-sorted records and an independent BAI index."""
    header = '@HD\tVN:1.6\tSO:coordinate\n' + ''.join(f'@SQ\tSN:{name}\tLN:{len(seq)}\n' for name, seq in sequences)
    header += '@RG\tID:SYN\tSM:SYNTHETIC_REPEATS\tPL:ILLUMINA\n@CO\tExplicitly synthetic paired reads; not a human sample\n'
    raw = bytearray(b'BAM\x01' + struct.pack('<i', len(header)) + header.encode() + struct.pack('<i', len(sequences)))
    for name, seq in sequences:
        name = name.encode() + b'\0'
        raw.extend(struct.pack('<i', len(name)) + name + struct.pack('<i', len(seq)))
    spans = [[] for _ in sequences]
    code = {'A': 1, 'C': 2, 'G': 4, 'T': 8, 'N': 15}
    operations = {'M': 0, 'I': 1, 'D': 2, 'S': 4}
    for rec in sorted(records, key=lambda r: (r['tid'], r['pos'], r['name'], r['flag'])):
        name = rec['name'].encode() + b'\0'
        seq, cigar = rec['seq'], rec['cigar']
        ref_length = sum(n for n, op in cigar if op in ['M', 'D'])
        end = rec['pos'] + max(1, ref_length)
        bin_number = reg2bin(rec['pos'], end)
        core = struct.pack('<iiIIiiii', rec['tid'], rec['pos'], (bin_number << 16) | (60 << 8) | len(name),
                           (rec['flag'] << 16) | len(cigar), len(seq), rec['tid'], rec['mate'], rec['tlen'])
        packed = bytes((code[seq[i]] << 4) | (code[seq[i + 1]] if i + 1 < len(seq) else 0) for i in range(0, len(seq), 2))
        body = core + name + b''.join(struct.pack('<I', (n << 4) | operations[op]) for n, op in cigar)
        body += packed + bytes([40]) * len(seq) + b'RGZSYN\0'
        start_offset = len(raw)
        raw.extend(struct.pack('<i', len(body)) + body)
        spans[rec['tid']].append((bin_number, rec['pos'], end, start_offset, len(raw)))
    compressed, offsets, eof_offset = bgzf(bytes(raw))

    def virtual(offset):
        if offset == len(raw):
            return eof_offset << 16
        chunk = offset // BLOCK
        return (offsets[chunk][0] << 16) | (offset % BLOCK)

    index = bytearray(b'BAI\x01' + struct.pack('<i', len(sequences)))
    for records_for_ref in spans:
        bins = {}
        for bin_number, start, end, first, last in records_for_ref:
            bins.setdefault(bin_number, []).append((first, last))
        index.extend(struct.pack('<i', len(bins) + bool(records_for_ref)))
        for bin_number, chunks in sorted(bins.items()):
            index.extend(struct.pack('<IiQQ', bin_number, 1, virtual(chunks[0][0]), virtual(chunks[-1][1])))
        if not records_for_ref:
            index.extend(struct.pack('<i', 0))
            continue
        index.extend(struct.pack('<IiQQQQ', 37450, 2, virtual(records_for_ref[0][3]),
                                 virtual(records_for_ref[-1][4]), len(records_for_ref), 0))
        linear = [0] * ((max(r[2] for r in records_for_ref) - 1) // 16384 + 1)
        for _, start, end, first, _ in records_for_ref:
            for window in range(start // 16384, (end - 1) // 16384 + 1):
                if not linear[window]:
                    linear[window] = virtual(first)
        index.extend(struct.pack('<i', len(linear)) + b''.join(struct.pack('<Q', value) for value in linear))
    index.extend(struct.pack('<Q', 0))
    return compressed, bytes(index)


def synthetic():
    definitions = [('chr1', 'NORMAL_CAG', [12, 18]), ('chr2', 'EXPANDED_CAG', [12, 75]),
                   ('chrX', 'X_CAG', [18]), ('chr3', 'TWO_STRS', [12, 18])]
    sequences, catalog, records, truths, compound, two_str = [], [], [], {}, None, None
    for tid, (chrom, locus, alleles) in enumerate(definitions):
        bases = ''.join('ACGT'[byte & 3] for counter in range(250) for byte in hashlib.sha256(f'repeats-v1/{chrom}/{counter}'.encode()).digest())
        sequence = bases[:4000] + 'CAG' * 12 + bases[4036:]
        if chrom == 'chr1':
            sequence = sequence[:3500] + 'A' + sequence[3501:]
            compound = [{'LocusId': 'COMPOUND_CAG', 'LocusStructure': '(A|T)' + sequence[3501:4000] + '(CAG)*',
                         'ReferenceRegion': ['chr1:3500-3501', 'chr1:4000-4036'], 'VariantType': ['SmallVariant', 'Repeat']}]
        if chrom == 'chr3':
            sequence = sequence[:3500] + 'GAA' * 8 + sequence[3524:]
            two_str = [{'LocusId': 'TWO_STRS', 'LocusStructure': '(GAA)*' + sequence[3524:4000] + '(CAG)*',
                        'ReferenceRegion': ['chr3:3500-3524', 'chr3:4000-4036'],
                        'VariantType': ['Repeat', 'Repeat'], 'VariantId': ['FIRST_GAA', 'SECOND_CAG']}]
        sequences.append((chrom, sequence))
        if chrom != 'chr3':
            catalog.append({'LocusId': locus, 'LocusStructure': '(CAG)*', 'ReferenceRegion': f'{chrom}:4000-4036', 'VariantType': 'Repeat'})
            truths[locus] = {'alleles': alleles, 'ploidy': len(alleles), 'assertion': 'confidenceIntervalContains' if locus == 'EXPANDED_CAG' else 'exact'}
        for allele_index, repeat_count in enumerate(alleles):
            insertions = []
            if chrom == 'chr3' and allele_index == 1:
                insertions.append((3524, 'GAA' * 6))
            if repeat_count > 12:
                insertions.append((4036, 'CAG' * (repeat_count - 12)))
            pieces, previous, added, events = [], 0, 0, []
            for coordinate, extra in insertions:
                pieces.extend([sequence[previous:coordinate], extra])
                events.append((coordinate + added, coordinate + added + len(extra), coordinate))
                previous, added = coordinate, added + len(extra)
            pieces.append(sequence[previous:])
            haplotype = ''.join(pieces)
            if chrom == 'chr1' and allele_index == 1:
                haplotype = haplotype[:3500] + 'T' + haplotype[3501:]

            def map_read(start):
                end = start + 150
                cigar, cursor, removed, pos = [], start, 0, None
                for low, high, coordinate in events:
                    if start >= high:
                        removed += high - low
                    elif start >= low:
                        pos = coordinate
                    if high <= cursor or low >= end:
                        continue
                    if cursor < low:
                        cigar.append((low - cursor, 'M'))
                        cursor = low
                    stop = min(end, high)
                    cigar.append((stop - cursor, 'I'))
                    cursor = stop
                if cursor < end:
                    cigar.append((end - cursor, 'M'))
                if pos is None:
                    pos = start - removed
                if all(op == 'I' for _, op in cigar):
                    # An in-repeat read has no unique linear placement. Retain
                    # it as a mapped, fully soft-clipped anchor at the repeat.
                    cigar = [(150, 'S')]
                    pos -= 1
                return pos, [(n, op) for n, op in cigar if n]

            for fragment, start in enumerate(range(2500, 5501, 10)):
                left, right = start, start + 200
                first_pos, first_cigar = map_read(left)
                second_pos, second_cigar = map_read(right)
                fragment_span = second_pos + sum(n for n, op in second_cigar if op in ['M', 'D']) - first_pos
                name = f'SYN_{locus}_A{allele_index}_F{fragment:04}'
                for pos, cigar, mate, flag, read_start, tlen in [(first_pos, first_cigar, second_pos, 99, left, fragment_span),
                                                               (second_pos, second_cigar, first_pos, 147, right, -fragment_span)]:
                    records.append({'tid': tid, 'pos': pos, 'name': name, 'seq': haplotype[read_start:read_start + 150],
                                    'cigar': cigar, 'mate': mate, 'flag': flag, 'tlen': tlen})
    reference, fai = fasta(sequences)
    reads, bai = bam(sequences, records)
    empty, empty_bai = bam(sequences, [])
    truth = {'schema': 1, 'origin': 'explicitly synthetic; no human-derived reads', 'loci': truths,
             'compound': {'locus': 'COMPOUND_CAG', 'snp': {'region': 'chr1:3500-3501', 'reference': 'A', 'alternate': 'T', 'genotype': '0/1'},
                          'repeat': {'region': 'chr1:4000-4036', 'alleles': [12, 18]},
                          'phase': 'No 350bp input fragment connects the SNP and STR separated by 499bp; phase is deliberately ambiguous.'},
             'twoStr': {'locus': 'TWO_STRS', 'variants': {'FIRST_GAA': {'region': 'chr3:3500-3524', 'alleles': [8, 14]},
                        'SECOND_CAG': {'region': 'chr3:4000-4036', 'alleles': [12, 18]}},
                        'phase': 'No 350bp input fragment connects the two STRs separated by 476bp; phase is deliberately ambiguous.'},
             'construction': {'referenceRepeatCount': 12, 'readLength': 150, 'fragmentLength': 350,
                              'fragmentStartInterval': [2500, 5500], 'fragmentStartStep': 10,
                              'pairsPerHaplotype': 301, 'totalReadPairs': 2107, 'mappingQuality': 60, 'baseQuality': 40,
                              'sequenceSeed': 'SHA256(repeats-v1/<chrom>/<counter>), each byte maps via ACGT[byte & 3]',
                              'linearAlignment': 'Reference-coordinate projection of inserted CAG bases; wholly inserted reads are soft-clipped at the repeat boundary.'}}
    return {'reference.fa': reference, 'reference.fa.fai': fai, 'reads.bam': reads, 'reads.bam.bai': bai,
            'empty.bam': empty, 'empty.bam.bai': empty_bai, 'catalog.json': encoded(catalog),
            'catalog.compound.json': encoded(compound),
            'catalog.twostr.json': encoded(two_str),
            'catalog.autosomal.json': encoded(catalog[:2]), 'catalog.empty.json': b'[]\n',
            'catalog.invalid.json': encoded([{'LocusId': 'INVALID', 'LocusStructure': '(CAG)*', 'ReferenceRegion': 'absent:4000-4036', 'VariantType': 'Repeat'}]),
            'truth.json': encoded(truth)}


def locked():
    return json.loads((DATA / 'provenance/sources.lock.json').read_text())


def source_path(record):
    return CACHE / record['project'] / record['sourcePath']


def source_bytes(record):
    raw = source_path(record).read_bytes()
    if len(raw) != record['bytes'] or digest(raw) != record['sha256']:
        raise RuntimeError('Pinned source mismatch: ' + record['sourcePath'])
    return raw


def fetch_sources():
    records = locked()['files']
    missing = []
    for record in records:
        try:
            source_bytes(record)
        except (OSError, RuntimeError):
            missing.append(record)
    # Standalone packages already carry these complete, hash-pinned source
    # archives. Extract only the individually verified fixture members.
    for project in {r['project'] for r in missing}:
        candidates = sorted((ROOT / 'dist' / project / 'sources').glob('*.tar.gz'))
        for archive in candidates:
            try:
                with tarfile.open(archive) as bundle:
                    for record in [r for r in missing if r['project'] == project]:
                        # Source archives have one project-root directory.
                        # Matching any suffix would also match vendored
                        # LICENSE.txt files and defeat offline restoration.
                        matches = [m for m in bundle.getmembers() if m.isfile()
                                   and (m.name == record['sourcePath']
                                        or m.name.partition('/')[2] == record['sourcePath'])]
                        if len(matches) != 1:
                            continue
                        raw = bundle.extractfile(matches[0]).read(record['bytes'] + 1)
                        if len(raw) == record['bytes'] and digest(raw) == record['sha256']:
                            write(source_path(record), raw)
            except (tarfile.TarError, OSError):
                continue
    for record in missing:
        try:
            source_bytes(record)
            continue
        except (OSError, RuntimeError):
            pass
        with urllib.request.urlopen(record['url'], timeout=120) as response:
            raw = response.read(record['bytes'] + 1)
        if len(raw) != record['bytes'] or digest(raw) != record['sha256']:
            raise RuntimeError('Downloaded source mismatch: ' + record['url'])
        write(source_path(record), raw)


def generate(destination):
    for name, raw in synthetic().items():
        write(destination / 'common' / name, raw)
    records = locked()['files']
    for record in records:
        if record.get('outputPath'):
            write(destination / record['outputPath'], source_bytes(record))
    reference = (destination / 'upstream/expansionhunter/reference.fa').read_bytes()
    write(destination / 'upstream/expansionhunter/reference.fa.fai', fasta_index(reference))
    chromosome_record = next(r for r in records if r['sourcePath'].endswith('/HG38_chr16.fa'))
    original = source_bytes(chromosome_record)
    reference_sequence = ''.join(line.decode().strip() for line in original.splitlines() if not line.startswith(b'>'))
    catalog = json.loads((destination / 'upstream/reviewer/catalog.json').read_text())
    masked = bytearray(b'N' * len(reference_sequence))
    regions = []
    for locus in catalog:
        regions_for_locus = locus['ReferenceRegion']
        if isinstance(regions_for_locus, str):
            regions_for_locus = [regions_for_locus]
        for region in regions_for_locus:
            chrom, coordinates = region.split(':')
            start, end = map(int, coordinates.split('-'))
            assert chrom == 'chr16'
            low, high = max(0, start - 2000), min(len(masked), end + 2000)
            masked[low:high] = reference_sequence[low:high].encode()
            regions.append({'locus': locus['LocusId'], 'chrom': chrom, 'start': low, 'end': high,
                            'sequenceSha256': digest(reference_sequence[low:high].encode())})
    # One long FASTA line keeps masked-N runs compact without changing any
    # sequence coordinate. FAI fields are below signed 32-bit limits here.
    raw = b'>chr16\n' + bytes(masked) + b'\n'
    fai = f'chr16\t{len(masked)}\t7\t{len(masked)}\t{len(masked) + 1}\n'.encode()
    compressed, offsets, _ = bgzf(raw)
    gzi = struct.pack('<Q', len(offsets) - 1) + b''.join(struct.pack('<QQ', *pair) for pair in offsets[1:])
    write(destination / 'upstream/reviewer/reference.fa.gz', compressed)
    write(destination / 'upstream/reviewer/reference.fa.gz.fai', fai)
    write(destination / 'upstream/reviewer/reference.fa.gz.gzi', gzi)
    write(destination / 'upstream/reviewer/reference-mask.json', encoded({'schema': 1, 'sourceSha256': chromosome_record['sha256'],
          'contig': 'chr16', 'length': len(masked), 'retainedRegions': regions,
          'derivedReferenceSha256': digest(compressed), 'nativeEquivalenceProof': MASK_NATIVE_EQUIVALENCE_PROOF,
          'transformation': 'Preserve these reference intervals exactly; replace every other base by N; preserve coordinates and contig length; deterministic BGZF encoding.'}))


def inventory(base):
    return {p.relative_to(base).as_posix(): {'bytes': p.stat().st_size, 'sha256': digest(p.read_bytes())}
            for p in sorted(base.rglob('*')) if p.is_file() and p.relative_to(base).parts[0] in ['common', 'upstream']}


def materialize_frozen(generated, manifest):
    if inventory(generated) != manifest['files']:
        raise RuntimeError('Regenerated fixtures differ from frozen manifest')
    present = inventory(DATA)
    if any(name not in manifest['files'] or spec != manifest['files'][name] for name, spec in present.items()):
        raise RuntimeError('Existing fixtures differ from frozen manifest')
    for name in manifest['files'].keys() - present.keys():
        write(DATA / name, (generated / name).read_bytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch-sources', action='store_true')
    parser.add_argument('--regenerate', action='store_true')
    parser.add_argument('--update-manifest', action='store_true')
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    if args.fetch_sources:
        fetch_sources()
    if args.regenerate:
        (ROOT / 'build').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='repeat-fixtures-', dir=ROOT / 'build') as temporary:
            generated = Path(temporary)
            generate(generated)
            if args.update_manifest:
                for child in ['common', 'upstream']:
                    if (DATA / child).exists():
                        shutil.rmtree(DATA / child)
                    shutil.copytree(generated / child, DATA / child)
                write(DATA / 'manifest.json', encoded({'schema': 1, 'tool': 'repeats', 'files': inventory(DATA),
                      'sourcesLockSha256': digest((DATA / 'provenance/sources.lock.json').read_bytes())}))
            else:
                materialize_frozen(generated, json.loads((DATA / 'manifest.json').read_text()))
    manifest = json.loads((DATA / 'manifest.json').read_text())
    if inventory(DATA) != manifest['files']:
        raise RuntimeError('Fixture inventory/bytes differ from frozen manifest')
    if digest((DATA / 'provenance/sources.lock.json').read_bytes()) != manifest['sourcesLockSha256']:
        raise RuntimeError('Source provenance changed')
    print(f"Verified {len(manifest['files'])} repeat-expansion fixtures")


if __name__ == '__main__':
    main()
