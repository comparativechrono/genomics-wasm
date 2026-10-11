"""Small independent BAM/BAI decoder for validation, using only the Python stdlib.

All compressed members must pass gzip CRC checks. Alignment order and every
auxiliary tag are retained. Numeric auxiliary storage widths remain distinct.
"""
from collections import Counter
import gzip
import io
import math
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from validation.compare import parse_sam  # noqa: E402


class ParsedBam(dict):
    pass


class Reader:
    def __init__(self, data):
        self.data, self.offset = data, 0

    def take(self, size):
        if size < 0 or self.offset + size > len(self.data):
            raise ValueError('truncated or invalid BAM/BAI structure')
        result = self.data[self.offset:self.offset + size]
        self.offset += size
        return result

    def unpack(self, format):
        return struct.unpack(format, self.take(struct.calcsize(format)))

    def integer(self):
        return self.unpack('<i')[0]

    def terminated(self):
        end = self.data.find(b'\0', self.offset)
        if end < 0:
            raise ValueError('unterminated BAM auxiliary string')
        return self.take(end - self.offset + 1)[:-1]


def ascii_text(raw, label, allow_empty=False):
    try:
        value = raw.decode('ascii')
    except UnicodeDecodeError as error:
        raise ValueError('non-ASCII ' + label) from error
    if (not value and not allow_empty) or any(ord(char) < 32 or ord(char) > 126 for char in value):
        raise ValueError('invalid ' + label)
    return value


NUMERIC = {'c': '<b', 'C': '<B', 's': '<h', 'S': '<H', 'i': '<i', 'I': '<I', 'f': '<f', 'd': '<d'}


def aux_tags(reader):
    values = {}
    while reader.offset < len(reader.data):
        tag = ascii_text(reader.take(2), 'BAM tag')
        if len(tag) != 2 or not tag[0].isalpha() or not tag[1].isalnum() or tag in values:
            raise ValueError('invalid or duplicate BAM auxiliary tag')
        kind = ascii_text(reader.take(1), 'BAM tag type')
        if kind in NUMERIC:
            value = reader.unpack(NUMERIC[kind])[0]
            if isinstance(value, float):
                if not math.isfinite(value):
                    raise ValueError('non-finite BAM float tag')
                value = value.hex()  # Preserve the exact binary floating value.
        elif kind == 'A':
            value = ascii_text(reader.take(1), 'BAM character tag')
        elif kind in ('Z', 'H'):
            value = ascii_text(reader.terminated(), 'BAM text tag', allow_empty=True)
            if kind == 'H' and (len(value) % 2 or any(char not in '0123456789ABCDEF' for char in value)):
                raise ValueError('invalid BAM hex tag')
        elif kind == 'B':
            subtype = ascii_text(reader.take(1), 'BAM array type')
            count = reader.integer()
            if subtype not in NUMERIC or subtype == 'd' or count < 0:
                raise ValueError('invalid BAM numeric array')
            items = []
            for _ in range(count):
                item = reader.unpack(NUMERIC[subtype])[0]
                if isinstance(item, float):
                    if not math.isfinite(item):
                        raise ValueError('non-finite BAM array value')
                    item = item.hex()
                items.append(item)
            value = (subtype, tuple(items))
        else:
            raise ValueError('unknown BAM auxiliary type: ' + kind)
        values[tag] = (kind, value)
    return tuple(sorted(values.items()))


def parse_bam(path):
    compressed = Path(path).read_bytes()
    if not compressed.startswith(b'\x1f\x8b'):
        raise ValueError('BAM is not gzip/BGZF compressed')
    reader = Reader(gzip.decompress(compressed))
    if reader.take(4) != b'BAM\1':
        raise ValueError('invalid BAM magic')
    header = reader.take(reader.integer()).decode('utf-8')
    if '\r' in header or any(ord(char) < 32 and char not in '\n\t' for char in header):
        raise ValueError('invalid BAM header control character')
    validated = parse_sam(io.StringIO(header))
    references = []
    count = reader.integer()
    if count < 0:
        raise ValueError('negative BAM reference count')
    for _ in range(count):
        raw = reader.take(reader.integer())
        if not raw.endswith(b'\0'):
            raise ValueError('unterminated BAM reference name')
        name, length = ascii_text(raw[:-1], 'BAM reference'), reader.integer()
        if length <= 0 or name in {ref[0] for ref in references}:
            raise ValueError('invalid BAM reference length or duplicate name')
        references.append((name, length))
    if [(dict(fields)['SN'], int(dict(fields)['LN'])) for fields in validated.ordered_header] != references:
        raise ValueError('BAM binary reference dictionary differs from SAM header')
    # Restore every header field, including @PG CL omitted by the generic SAM
    # comparator: indelqual should preserve the input BAM header unchanged.
    header_records = Counter()
    for line in header.split('\n'):
        if not line:
            continue
        fields = line.split('\t')
        if fields[0] == '@CO':
            key = ('@CO', line[4:])
        else:
            key = (fields[0], tuple(sorted(tuple(field.split(':', 1)) for field in fields[1:])))
        header_records[key] += 1
    records = []
    alphabet, cigar_ops = '=ACMGRSVTWYHKDBN', 'MIDNSHP=X'
    while reader.offset < len(reader.data):
        block = Reader(reader.take(reader.integer()))
        ref_id, position, bin_mq_nl, flag_nc, seq_len, mate_ref, mate_pos, template_len = block.unpack('<iiIIiiii')
        name_len, mapq, flag, cigar_count = bin_mq_nl & 255, (bin_mq_nl >> 8) & 255, flag_nc >> 16, flag_nc & 65535
        if seq_len < 0 or name_len < 1 or ref_id < -1 or ref_id >= len(references) or mate_ref < -1 or mate_ref >= len(references) or position < -1 or mate_pos < -1:
            raise ValueError('invalid BAM alignment core')
        raw_name = block.take(name_len)
        if not raw_name.endswith(b'\0'):
            raise ValueError('unterminated BAM read name')
        name = ascii_text(raw_name[:-1], 'BAM read name')
        cigar, query_span, reference_span = [], 0, 0
        for _ in range(cigar_count):
            value = block.unpack('<I')[0]
            length, opcode = value >> 4, value & 15
            if not length or opcode >= len(cigar_ops):
                raise ValueError('invalid BAM CIGAR')
            operation = cigar_ops[opcode]
            cigar.append((length, operation))
            if operation in 'MIS=X': query_span += length
            if operation in 'MDN=X': reference_span += length
        if cigar and query_span != seq_len:
            raise ValueError('BAM CIGAR and sequence length disagree')
        if not flag & 4 and (ref_id < 0 or position < 0 or position + reference_span > references[ref_id][1]):
            raise ValueError('mapped BAM alignment is outside its reference')
        encoded = block.take((seq_len + 1) // 2)
        sequence = ''.join(alphabet[(encoded[index // 2] >> (4 if index % 2 == 0 else 0)) & 15] for index in range(seq_len))
        qualities = block.take(seq_len)
        if qualities and qualities != b'\xff' * seq_len and any(value > 93 for value in qualities):
            raise ValueError('invalid BAM base quality')
        tags = aux_tags(block)
        records.append((name, flag, ref_id, position, mapq, tuple(cigar), mate_ref, mate_pos,
                        template_len, sequence, qualities.hex(), tags, bin_mq_nl >> 16))
    result = ParsedBam(references=references, header=sorted(header_records.items(), key=repr), records=records)
    result.compressed_size = len(compressed)
    return result


def indelqual_invariants(value, original=None):
    if not value['records']:
        raise ValueError('vacuous BAM: no alignment records')
    eligible = 0
    for record in value['records']:
        name, flag, sequence, tags = record[0], record[1], record[9], dict(record[11])
        if flag & (4 | 256 | 512 | 1024):
            continue
        eligible += 1
        for tag in ('BI', 'BD'):
            if tag not in tags or tags[tag][0] != 'Z' or len(tags[tag][1]) != len(sequence) or any(not 33 <= ord(char) <= 126 for char in tags[tag][1]):
                raise ValueError(f'{name}: missing or invalid {tag} indel qualities')
    if not eligible:
        raise ValueError('indelqual test has no eligible mapped reads')
    if original is not None:
        def unchanged(record):
            return (*record[:11], tuple(item for item in record[11] if item[0] not in ('BI', 'BD')), record[12])
        if value['references'] != original['references'] or value['header'] != original['header'] or [unchanged(record) for record in value['records']] != [unchanged(record) for record in original['records']]:
            raise ValueError('indelqual changed alignments, bases, qualities or non-indel tags')
    return {'records': len(value['records']), 'eligibleIndelQualityRecords': eligible}


def parse_bai(path, bam):
    reader = Reader(Path(path).read_bytes())
    if reader.take(4) != b'BAI\1' or reader.integer() != len(bam['references']):
        raise ValueError('invalid BAI magic or reference count')
    bins, chunks, intervals = 0, 0, 0
    for _ in bam['references']:
        count = reader.integer()
        if count < 0:
            raise ValueError('negative BAI bin count')
        seen = set()
        for _ in range(count):
            bin_id = reader.unpack('<I')[0]
            chunk_count = reader.integer()
            if bin_id > 37450 or bin_id in seen or chunk_count < 0:
                raise ValueError('invalid or duplicate BAI bin')
            seen.add(bin_id)
            for _ in range(chunk_count):
                start, end = reader.unpack('<QQ')
                if bin_id != 37450 and (start >= end or (end >> 16) > getattr(bam, 'compressed_size', 2**64)):
                    raise ValueError('reversed BAI chunk')
            bins += 1
            chunks += chunk_count
        count = reader.integer()
        if count < 0:
            raise ValueError('negative BAI linear interval count')
        offsets = [reader.unpack('<Q')[0] for _ in range(count)]
        nonzero = [value for value in offsets if value]
        if any((offset >> 16) > getattr(bam, 'compressed_size', 2**64) for offset in nonzero):
            raise ValueError('BAI virtual offset lies outside BAM file')
        if nonzero != sorted(nonzero):
            raise ValueError('BAI linear offsets are not monotonic')
        intervals += count
    if len(reader.data) - reader.offset == 8:
        reader.unpack('<Q')
    if reader.offset != len(reader.data):
        raise ValueError('trailing or truncated BAI data')
    if not bins or not chunks:
        raise ValueError('vacuous BAI: no indexed chunks')
    return {'indexedReferences': len(bam['references']), 'bins': bins, 'chunks': chunks, 'linearIntervals': intervals}
