#!/usr/bin/env python3
"""Exact Kraken2/Bracken comparison, with independent format/count checks.

Schemas follow Kraken2 2.17.2 src/{classify,reports}.cc and Bracken 3.1
src/{kraken_processing.cpp,generate_kmer_distribution.py,est_abundance.py}.
No classification or abundance algorithm is reproduced here. Decimal tokens
retain their precision; all numerical comparison is exact and context-free.
"""
import argparse
import base64
from collections import Counter, defaultdict
from decimal import Decimal
from fractions import Fraction
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import struct
import unicodedata


ABUNDANCE_HEADER = 'name\ttaxonomy_id\ttaxonomy_lvl\tkraken_assigned_reads\tadded_reads\tnew_est_reads\tfraction_total_reads'
DISTRIBUTION_HEADER = 'mapped_taxid\tgenome_taxids:kmers_mapped:total_genome_kmers'
RANK = re.compile(r'[URDKPCOFGS](?:[1-9][0-9]*)?')


def canonical(value):
    """Typed representation, without Decimal.normalize()/context rounding."""
    if value is None:
        return ['null']
    if isinstance(value, bool):
        return ['boolean', value]
    if isinstance(value, int):
        return ['integer', str(value)]
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError('non-finite decimal')
        parts = value.as_tuple()
        return ['decimal', parts.sign, ''.join(map(str, parts.digits)), parts.exponent]
    if isinstance(value, str):
        return ['string', value]
    if isinstance(value, dict):
        return ['object', [[key, canonical(item)] for key, item in sorted(value.items())]]
    if isinstance(value, (list, tuple)):
        return ['array', [canonical(item) for item in value]]
    raise ValueError('unsupported comparison type: ' + type(value).__name__)


def digest(value):
    return hashlib.sha256(json.dumps(canonical(value), separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def read_text(path):
    path = Path(path)
    if not path.is_file():
        raise ValueError('missing required artifact: ' + str(path))
    raw = path.read_bytes()
    if path.suffix == '.gz':
        if not raw.startswith(b'\x1f\x8b'):
            raise ValueError('expected gzip artifact: ' + str(path))
        raw = gzip.decompress(raw)  # Validate complete members and CRC.
    return decode_text(raw, str(path))


def decode_text(raw, label):
    text = raw.decode('utf-8', errors='strict').replace('\r\n', '\n')
    if any((unicodedata.category(char) in ('Cc', 'Cf', 'Zl', 'Zp') and char not in '\n\t') for char in text):
        raise ValueError('invalid control character or line separator: ' + label)
    return text


def lines(path, require_nonempty=True):
    return text_lines(read_text(path), require_nonempty, str(path))


def text_lines(text, require_nonempty=True, label='artifact'):
    rows = text.split('\n') if text else []
    if rows and rows[-1] == '':
        rows.pop()
    if any(not row for row in rows):
        raise ValueError('unexpected blank record: ' + label)
    if require_nonempty and not rows:
        raise ValueError('empty required artifact: ' + label)
    return rows


def uint(token, label, positive=False):
    if not re.fullmatch(r'0|[1-9][0-9]*', token):
        raise ValueError(label + ': expected canonical non-negative integer')
    value = int(token)
    if positive and not value:
        raise ValueError(label + ': expected positive integer')
    return value


def decimal(token, label, places):
    if not re.fullmatch(r'(?:0|[1-9][0-9]*)\.[0-9]{' + str(places) + '}', token):
        raise ValueError(label + ': invalid fixed-precision decimal')
    value = Decimal(token)
    if not value.is_finite():
        raise ValueError(label + ': non-finite decimal')
    return value


def name(token, label):
    if not token or token != token.strip() or '\t' in token:
        raise ValueError(label + ': empty name or surrounding whitespace')
    return token


def taxon(value, context, allow_zero=True):
    known = context.get('knownTaxa')
    if value == 0 and allow_zero:
        return value
    if value <= 0 or (known is not None and value not in {int(item) for item in known}):
        raise ValueError('undeclared or invalid taxonomy ID: ' + str(value))
    return value


def parse_classifications(path, spec=None, context=None):
    spec, context = spec or {}, context or {}
    records, seen = [], set()
    for line in lines(path, spec.get('requireNonempty', True)):
        fields = line.split('\t')
        if len(fields) != 5:
            raise ValueError('Kraken classification requires five tab-separated columns')
        status, identifier, called, lengths, hits = fields
        if status not in ('C', 'U') or not identifier or any(char.isspace() for char in identifier):
            raise ValueError('invalid classification status or read identifier')
        if identifier in seen:
            raise ValueError('duplicate classification read identifier')
        seen.add(identifier)
        called = taxon(uint(called, 'classification taxid'), context)
        if (status == 'U') != (called == 0):
            raise ValueError('classification status and taxonomy ID disagree')
        sizes = [uint(item, 'read length') for item in lengths.split('|')]
        if len(sizes) not in (1, 2):
            raise ValueError('classification requires one length or two mate lengths')
        if 'paired' in spec and (len(sizes) == 2) != spec['paired']:
            raise ValueError('classification pairing differs from case plan')
        # Upstream emits a trailing space when the final run is ambiguous (A).
        tokens = hits.rstrip(' ').split(' ')
        if not tokens or any(not token for token in tokens):
            raise ValueError('empty or malformed minimizer hit list')
        parsed, quick, mates, frames = [], False, 0, 0
        for token in tokens:
            if token in ('|:|', '-:-'):
                mates += token == '|:|'
                frames += token == '-:-'
                parsed.append(token)
                continue
            match = re.fullmatch(r'(A|0|[1-9][0-9]*):(Q|0|[1-9][0-9]*)', token)
            if not match:
                raise ValueError('invalid minimizer hit token')
            code, count = match.groups()
            code = code if code == 'A' else taxon(uint(code, 'hit taxid'), context)
            if count == 'Q':
                if len(tokens) != 1 or code != called or not spec.get('quick', False):
                    raise ValueError('invalid or unexpected quick-mode classification')
                quick = True
            else:
                count = uint(count, 'hit count')
                if count == 0 and (token != '0:0' or len(tokens) != 1):
                    raise ValueError('zero hit count is only valid for the empty 0:0 sentinel')
            parsed.append([code, count])
        if not quick and mates != len(sizes) - 1:
            raise ValueError('mate separator count differs from read lengths')
        if frames and not spec.get('translated', False):
            raise ValueError('unexpected translated-frame separator')
        if spec.get('translated', False) and not quick and frames != 5 * len(sizes):
            raise ValueError('incorrect translated-frame separator count')
        if spec.get('quick', False) and not quick:
            raise ValueError('quick-mode case omitted Q marker')
        records.append({'status': status, 'id': identifier, 'taxid': called, 'lengths': sizes, 'hits': parsed})
    expected = context.get('readRecords')
    if expected is not None:
        observed = [{'id': item['id'], 'lengths': item['lengths']} for item in records]
        if observed != expected:
            raise ValueError('classification identifiers, order or lengths differ from input reads')
    return records


def rounded_interval(value, places):
    """Interval implied by a rounded display value; not an equality tolerance."""
    half_unit = Fraction(1, 2 * 10 ** places)
    return Fraction(value) - half_unit, Fraction(value) + half_unit


def parse_report(path, spec=None, context=None):
    spec, context = spec or {}, context or {}
    redistributed = spec.get('redistributed', False)
    records, seen, stack, children = [], set(), [], defaultdict(list)
    width = None
    for line in lines(path, spec.get('requireNonempty', True)):
        fields = line.split('\t')
        if len(fields) not in (6, 8) or (width is not None and len(fields) != width):
            raise ValueError('taxonomy report requires consistently six or eight columns')
        width = len(fields)
        minimizer_columns = spec.get('minimizerData', spec.get('minimizerColumns'))
        if minimizer_columns is not None and (width == 8) != minimizer_columns:
            raise ValueError('taxonomy report minimizer columns differ from case plan')
        percentage = decimal(fields[0].lstrip(' '), 'report percentage', 2)
        if not 0 <= percentage <= 100:
            raise ValueError('report percentage outside [0, 100]')
        clade, direct = uint(fields[1], 'clade reads'), uint(fields[2], 'direct reads')
        if direct > clade:
            raise ValueError('direct reads exceed clade reads')
        rank, taxid_text, indented = fields[-3:]
        if not RANK.fullmatch(rank):
            raise ValueError('invalid report rank')
        taxid = taxon(uint(taxid_text, 'report taxid'), context)
        if taxid in seen:
            raise ValueError('duplicate report taxonomy ID')
        seen.add(taxid)
        spaces = len(indented) - len(indented.lstrip(' '))
        if spaces % 2:
            raise ValueError('taxonomy report indentation is not a multiple of two')
        depth = spaces // 2
        scientific_name = name(indented[spaces:], 'scientific name')
        record = {'percentage': percentage, 'clade': clade, 'direct': direct,
                  'rank': rank, 'taxid': taxid, 'depth': depth, 'name': scientific_name}
        if width == 8:
            minimizers = [uint(fields[3], 'minimizer count'), uint(fields[4], 'distinct minimizer count')]
            if minimizers[1] > minimizers[0]:
                raise ValueError('distinct minimizers exceed total minimizers')
            record['minimizers'] = minimizers
        if taxid == 0:
            if rank != 'U' or depth or clade != direct or records or scientific_name != 'unclassified':
                raise ValueError('invalid unclassified report row')
            if redistributed:
                raise ValueError('Bracken redistributed report unexpectedly contains unclassified reads')
        else:
            if rank == 'U' or (taxid == 1 and (rank != 'R' or depth)):
                raise ValueError('invalid root or classified report rank')
            if depth == 0:
                if taxid != 1 or stack:
                    raise ValueError('taxonomy tree must start at root taxid 1')
            elif depth > len(stack):
                raise ValueError('taxonomy report skipped an indentation level')
            if depth:
                parent = stack[depth - 1]
                children[parent['taxid']].append(record)
            stack = stack[:depth] + [record]
        records.append(record)
    by_taxid = {record['taxid']: record for record in records}
    classified = by_taxid.get(1, {}).get('clade', 0)
    total = classified + by_taxid.get(0, {}).get('clade', 0)
    if records and not total and spec.get('requireNonempty', True):
        raise ValueError('vacuous taxonomy report has no reads')
    if context.get('totalReads') is not None and not redistributed and total != context['totalReads']:
        raise ValueError('report total differs from input read count')
    for row in records:
        subtrees = children[row['taxid']]
        accounted = row['direct'] + sum(child['clade'] for child in subtrees)
        if redistributed:
            if total:
                lower, upper = rounded_interval(row['percentage'], 2)
                # The percentage is computed before either clade is truncated.
                # These are exact bounds implied by the printed integer parts.
                possible_lower = Fraction(100 * row['clade'], total + 1)
                possible_upper = Fraction(100 * (row['clade'] + 1), total)
                if upper < possible_lower or lower > possible_upper or (row['taxid'] == 1 and row['percentage'] != 100):
                    raise ValueError('redistributed percentage violates pre-truncation count bounds')
            # est_abundance.py increments its `children` counter even for
            # lower-rank nodes absent from new_reads and therefore omitted
            # from the report. A terminal emitted genus/family can have
            # direct=0 despite having no emitted descendants.
            if not subtrees:
                if row['direct'] not in (0, row['clade']):
                    raise ValueError('redistributed leaf has an invalid direct count')
                continue
            # Bracken prints int(each fractional clade), independently. The
            # parent therefore loses at most (number of children - 1) reads.
            gap = row['clade'] - accounted
            if gap < 0 or gap > max(0, len(subtrees) - 1):
                raise ValueError('redistributed clade counts violate truncation bounds')
        elif accounted != row['clade']:
            raise ValueError('report clade is not direct reads plus child clades')
        if total and not redistributed:
            lower, upper = rounded_interval(row['percentage'], 2)
            exact_percentage = Fraction(100 * row['clade'], total)
            if not lower <= exact_percentage <= upper:
                raise ValueError('report percentage disagrees with counts at its display precision')
    return records


def parse_abundance(path, spec=None, context=None):
    spec, context = spec or {}, context or {}
    rows = lines(path)
    if rows[0] != ABUNDANCE_HEADER:
        raise ValueError('invalid Bracken abundance header')
    records, seen = [], set()
    for line in rows[1:]:
        fields = line.split('\t')
        if len(fields) != 7:
            raise ValueError('Bracken abundance requires seven columns')
        scientific_name, taxid_text, rank = fields[:3]
        taxid = taxon(uint(taxid_text, 'abundance taxid', positive=True), context, False)
        if taxid in seen:
            raise ValueError('duplicate abundance taxonomy ID')
        seen.add(taxid)
        expected_rank = spec.get('rank', spec.get('level'))
        if not re.fullmatch(r'[DKPCOFGS](?:[1-9][0-9]*)?', rank) or (expected_rank is not None and rank != expected_rank):
            raise ValueError('invalid or unexpected abundance rank')
        assigned, added, estimated = [uint(value, 'abundance read count') for value in fields[3:6]]
        if assigned + added != estimated:
            raise ValueError('assigned plus added reads differ from estimated reads')
        fraction = decimal(fields[6], 'abundance fraction', 5)
        if not 0 <= fraction <= 1:
            raise ValueError('abundance fraction outside [0, 1]')
        records.append({'name': name(scientific_name, 'abundance name'), 'taxid': taxid, 'rank': rank,
                        'assigned': assigned, 'added': added, 'estimated': estimated, 'fraction': fraction})
    if spec.get('requireNonempty', True) and (not records or not sum(row['estimated'] for row in records)):
        raise ValueError('empty or vacuous Bracken abundance')
    if len({row['rank'] for row in records}) > 1:
        raise ValueError('abundance rows have different estimation ranks')
    if records:
        # Upstream divides int(per-taxon estimate) by int(sum of unrounded
        # estimates). Its denominator can exceed sum(printed counts) by at
        # most N-1. Intersect exact rational display intervals; do not assume
        # the printed fractions sum to 1 or rerun redistribution.
        total = sum(row['estimated'] for row in records)
        lower_d, upper_d = Fraction(total), Fraction(total + len(records) - 1)
        for row in records:
            lower_f, upper_f = rounded_interval(row['fraction'], 5)
            if upper_f <= 0 or (row['estimated'] == 0 and lower_f > 0):
                raise ValueError('abundance fraction is inconsistent with its count')
            lower_d = max(lower_d, Fraction(row['estimated']) / upper_f)
            if lower_f > 0:
                upper_d = min(upper_d, Fraction(row['estimated']) / lower_f)
        if -(-lower_d.numerator // lower_d.denominator) > upper_d.numerator // upper_d.denominator:
            raise ValueError('abundance fractions have no count-consistent integer denominator')
    return records


def parse_read_distribution(path, spec=None, context=None):
    spec, context = spec or {}, context or {}
    return read_distribution_records(lines(path, spec.get('requireNonempty', True)), context)


def read_distribution_records(rows, context):
    records, seen = [], set()
    for line in rows:
        fields = line.split('\t')
        if len(fields) != 4 or fields[2] != '':
            raise ValueError('Bracken read distribution requires four columns and an empty third column')
        identifier = name(fields[0], 'distribution sequence ID')
        if any(char.isspace() for char in identifier) or identifier in seen:
            raise ValueError('invalid or duplicate distribution sequence ID')
        seen.add(identifier)
        genome = taxon(uint(fields[1], 'genome taxid', positive=True), context, False)
        hits, mapped_ids = [], set()
        for token in fields[3].rstrip(' ').split(' '):
            parts = token.split(':')
            if len(parts) != 2:
                raise ValueError('invalid read-distribution mapping')
            mapped, count = uint(parts[0], 'mapped taxid'), uint(parts[1], 'mapped count', positive=True)
            taxon(mapped, context)
            if mapped in mapped_ids:
                raise ValueError('duplicate mapped taxid within a read distribution')
            mapped_ids.add(mapped)
            hits.append([mapped, count])
        if [item[0] for item in hits] != sorted(mapped_ids):
            raise ValueError('read-distribution mappings violate upstream std::map ordering')
        records.append({'id': identifier, 'genome': genome, 'hits': hits})
    return records


def native_read_distribution(path, spec, context):
    """Isolate one source-proven native mmap EOF phantom, preserving evidence.

    Bracken 3.1 kraken_processing.cpp read loop (lines 259..293) processes
    the byte following a terminal newline without checking dataSize first.
    The unchanged native helper can append NUL, an indeterminate signed int,
    then empty columns. The portable helper fixes the bound. This exception
    is never available to patched-native/WASM or the ordinary text parser.
    """
    raw = Path(path).read_bytes()
    match = re.search(rb'\x00\t-?[0-9]+\t\t\n\Z', raw)
    if match is None:
        return parse_read_distribution(path, spec, context), None
    prefix, fragment = raw[:match.start()], match.group()
    if not prefix or not prefix.endswith(b'\n'):
        raise ValueError('native EOF artifact must follow complete genuine records')
    integer_value = int(fragment.split(b'\t')[1])
    if not -(2 ** 31) <= integer_value < 2 ** 31:
        raise ValueError('native EOF artifact integer does not fit the upstream int field')
    # Strict decoding and parsing of every preceding byte also rejects a
    # second phantom, an interior artifact, or any additional corruption.
    records = read_distribution_records(text_lines(decode_text(prefix, str(path)), True, str(path)), context)
    return records, {'offset': match.start(), 'length': len(fragment),
                     'rawSha256': hashlib.sha256(raw).hexdigest(),
                     'fragmentSha256': hashlib.sha256(fragment).hexdigest(),
                     'base64': base64.b64encode(fragment).decode('ascii'),
                     'reason': 'Bracken 3.1 unchanged native kraken_processing.cpp reads beyond the terminal newline; one non-biological EOF phantom excluded'}


def parse_distribution(path, spec=None, context=None):
    spec, context = spec or {}, context or {}
    rows = lines(path)
    if rows[0] != DISTRIBUTION_HEADER:
        raise ValueError('invalid Bracken kmer-distribution header')
    records, mapped_seen, totals, sums = [], set(), {}, Counter()
    for line in rows[1:]:
        fields = line.split('\t')
        if len(fields) != 2:
            raise ValueError('Bracken kmer distribution requires two columns')
        mapped = taxon(uint(fields[0], 'mapped taxid'), context)
        if mapped in mapped_seen:
            raise ValueError('duplicate mapped distribution taxonomy ID')
        mapped_seen.add(mapped)
        genomes, seen = [], set()
        for token in fields[1].rstrip(' ').split(' '):
            parts = token.split(':')
            if len(parts) != 3:
                raise ValueError('distribution requires genome:mapped:total triples')
            genome = taxon(uint(parts[0], 'genome taxid', positive=True), context, False)
            count, total = uint(parts[1], 'mapped count', positive=True), uint(parts[2], 'genome total', positive=True)
            if genome in seen or count > total:
                raise ValueError('duplicate genome or mapped count exceeds total')
            seen.add(genome)
            if genome in totals and totals[genome] != total:
                raise ValueError('inconsistent total count for genome')
            totals[genome] = total
            sums[genome] += count
            genomes.append([genome, count, total])
        records.append({'mapped': mapped, 'genomes': genomes})
    if spec.get('requireNonempty', True) and not records:
        raise ValueError('empty Bracken kmer distribution')
    if dict(sums) != totals:
        raise ValueError('mapped counts do not sum to each genome total')
    return records


def parse_fastq(path, spec=None, context=None):
    spec, context = spec or {}, context or {}
    rows = lines(path, spec.get('requireNonempty', True))
    if len(rows) % 4:
        raise ValueError('incomplete FASTQ record')
    records, seen = [], set()
    for offset in range(0, len(rows), 4):
        title, sequence, plus, quality = rows[offset:offset + 4]
        if not title.startswith('@') or len(title) == 1 or not plus.startswith('+'):
            raise ValueError('invalid FASTQ header')
        if plus[1:] and plus[1:] != title[1:]:
            raise ValueError('FASTQ repeated identifier differs')
        identifier = title[1:].split()[0]
        if identifier in seen:
            raise ValueError('duplicate FASTQ identifier')
        seen.add(identifier)
        if not re.fullmatch('[ACGTRYSWKMBDHVNacgtryswkmbdhvn]+', sequence):
            raise ValueError('invalid FASTQ sequence')
        if len(sequence) != len(quality) or any(ord(char) < 33 or ord(char) > 126 for char in quality):
            raise ValueError('invalid FASTQ quality')
        records.append({'title': title[1:], 'sequence': sequence, 'quality': quality})
    return records


def parse_options(path, spec=None, context=None):
    """Read the pinned 64-bit IndexOptions disk ABI, excluding only padding.

    kraken2_data.h declares nine fields; build_db.cc writes sizeof(struct),
    including uninitialised native padding at bytes 33..39 and 60..63.
    The portable build preserves this disk ABI with deterministic padding.
    """
    raw = Path(path).read_bytes()
    if len(raw) != 64 or raw[32] not in (0, 1):
        raise ValueError('invalid Kraken IndexOptions size or bool encoding')
    values = struct.unpack('<QQQQ?7xQiii4x', raw)
    keys = ['k', 'l', 'spacedSeedMask', 'toggleMask', 'dnaDatabase',
            'minimumAcceptableHash', 'reverseComplementVersion', 'databaseVersion', 'databaseType']
    result = dict(zip(keys, values))
    if not result['l'] or result['k'] < result['l']:
        raise ValueError('invalid Kraken kmer/minimizer lengths')
    return result


PARSERS = {'classifications': parse_classifications, 'kraken-report': parse_report,
           'bracken-abundance': parse_abundance, 'bracken-distribution': parse_distribution,
           'bracken-read-distribution': parse_read_distribution, 'fastq': parse_fastq,
           'kraken-options': parse_options}


def artifact_path(directory, relative):
    path = PurePosixPath(relative)
    if not relative or path.is_absolute() or '..' in path.parts or str(path) != relative:
        raise ValueError('artifact path must be a canonical relative path')
    base = Path(directory).resolve()
    result = base.joinpath(*path.parts)
    if not result.resolve().is_relative_to(base):
        raise ValueError('artifact path escapes output directory')
    return result


def reconcile(payloads, specs):
    """Independent relations between emitted files, without caller inference."""
    classes = [payloads[item['path']] for item in specs if item['kind'] == 'classifications']
    reports = [payloads[item['path']] for item in specs if item['kind'] == 'kraken-report' and not item.get('redistributed', False)]
    if len(classes) == 1:
        counts = Counter(row['taxid'] for row in classes[0])
        for report in reports:
            emitted = {row['taxid']: row['direct'] for row in report if row['direct']}
            if emitted != dict(counts):
                raise ValueError('taxonomy report direct counts disagree with classifications')
    abundance = [payloads[item['path']] for item in specs if item['kind'] == 'bracken-abundance']
    redistributed = [payloads[item['path']] for item in specs if item['kind'] == 'kraken-report' and item.get('redistributed', False)]
    if len(abundance) == 1:
        for report in redistributed:
            by_id = {row['taxid']: row for row in report}
            for row in abundance[0]:
                match = by_id.get(row['taxid'])
                if not match or (match['clade'], match['name'], match['rank']) != (row['estimated'], row['name'], row['rank']):
                    raise ValueError('redistributed report differs from abundance estimates')
    raw = [payloads[item['path']] for item in specs if item['kind'] == 'bracken-read-distribution']
    final = [payloads[item['path']] for item in specs if item['kind'] == 'bracken-distribution']
    if len(raw) == len(final) == 1:
        expected = Counter()
        for row in raw[0]:
            for mapped, count in row['hits']:
                expected[(mapped, row['genome'])] += count
        observed = {(row['mapped'], genome): count for row in final[0] for genome, count, _ in row['genomes']}
        if dict(expected) != observed:
            raise ValueError('kmer distribution does not conserve raw read-distribution counts')


def load_output(directory, spec, context=None, *, target=None, anomalies=None):
    context = context or {}
    artifacts = spec.get('artifacts')
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError('case must declare nonempty artifacts')
    payloads, invariants = {}, {}
    for artifact in artifacts:
        relative, kind = artifact['path'], artifact['kind']
        if relative in payloads:
            raise ValueError('duplicate artifact path')
        path = artifact_path(directory, relative)
        if kind == 'exact':
            raw = path.read_bytes()
            if artifact.get('requireNonempty', True) and not raw:
                raise ValueError('empty required binary artifact')
            value = {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
            checks = {'bytes': len(raw)}
        else:
            if kind not in PARSERS:
                raise ValueError('unknown artifact kind: ' + kind)
            if artifact.get('nativeEofArtifact') and kind != 'bracken-read-distribution':
                raise ValueError('native EOF exception only applies to Bracken read distributions')
            if artifact.get('nativeEofArtifact') and target == 'native':
                value, anomaly = native_read_distribution(path, artifact, context)
                if anomaly is not None and anomalies is not None:
                    anomalies.append({'path': relative, **anomaly})
            else:
                value = PARSERS[kind](path, artifact, context)
            checks = {'fields': len(value), 'bytes': 64} if kind == 'kraken-options' else {'records': len(value)}
            if len(value) < artifact.get('minimumRecords', 0):
                raise ValueError('artifact has fewer than required records')
            if kind == 'classifications':
                checks.update({'classified': sum(row['status'] == 'C' for row in value),
                               'unclassified': sum(row['status'] == 'U' for row in value)})
                if checks['classified'] < artifact.get('minimumClassified', 0) or checks['unclassified'] < artifact.get('minimumUnclassified', 0):
                    raise ValueError('classification does not meet required classified/unclassified counts')
            elif kind == 'bracken-abundance':
                checks.update({'estimatedReads': sum(row['estimated'] for row in value),
                               'addedReads': sum(row['added'] for row in value)})
                if checks['addedReads'] < artifact.get('minimumAddedReads', 0):
                    raise ValueError('Bracken case did not redistribute the required number of reads')
        payloads[relative], invariants[relative] = value, checks
    reconcile(payloads, artifacts)
    return payloads, invariants


def compare(expected_dir, actual_dir, spec, context=None):
    context = context or {}
    native_anomalies = {'expected': [], 'actual': []}
    left, left_checks = load_output(expected_dir, spec, context, target=context.get('expectedTarget'), anomalies=native_anomalies['expected'])
    right, right_checks = load_output(actual_dir, spec, context, target=context.get('actualTarget'), anomalies=native_anomalies['actual'])
    selected = spec.get('biologicalArtifacts', [item['path'] for item in spec['artifacts']])
    if not isinstance(selected, list) or not selected or len(set(selected)) != len(selected) or any(path not in right for path in selected):
        raise ValueError('biologicalArtifacts must select unique declared artifacts')
    kinds = {item['path']: item['kind'] for item in spec['artifacts']}
    biological = [[kinds[path], right[path]] for path in selected]
    return {'schema': 1, 'tool': spec.get('tool', (context or {}).get('tool')), 'equal': canonical(left) == canonical(right),
            'expectedSemanticDigest': digest(left), 'semanticDigest': digest(right),
            'biologicalDigest': digest(biological), 'invariants': right_checks, 'nativeInvariants': left_checks,
            'nativeAnomalies': native_anomalies,
            'normalisation': ['LF/CRLF line ending syntax', 'report percentage field padding',
                              'upstream trailing spaces in hit/distribution lists', 'gzip wrapper metadata'] +
                             (['Kraken IndexOptions ABI padding bytes 33..39 and 60..63; all nine declared fields remain exact']
                              if 'kraken-options' in kinds.values() else []),
            'numericPolicy': 'Exact typed integers and precision-preserving decimals; no numerical tolerances.'}


def unique_object(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate JSON key: ' + key)
        result[key] = value
    return result


def read_json(path):
    return json.loads(read_text(path), object_pairs_hook=unique_object,
                      parse_float=Decimal, parse_constant=lambda value: (_ for _ in ()).throw(ValueError('non-finite JSON number')))


def taxonomy_ids(path):
    parents = {}
    for line in lines(path):
        fields = [field.strip(' \t') for field in line.split('|')]
        if len(fields) < 4 or fields[-1] != '':
            raise ValueError('invalid NCBI taxonomy nodes record')
        identifier = uint(fields[0], 'taxonomy node ID', positive=True)
        parent = uint(fields[1], 'taxonomy parent ID', positive=True)
        if identifier in parents:
            raise ValueError('duplicate taxonomy node')
        parents[identifier] = parent
    if parents.get(1) != 1 or any(parent not in parents for parent in parents.values()):
        raise ValueError('taxonomy nodes lack root or a declared parent')
    for identifier in parents:
        visited = set()
        while identifier != 1:
            if identifier in visited:
                raise ValueError('taxonomy nodes contain a cycle')
            visited.add(identifier)
            identifier = parents[identifier]
    return sorted(parents)


def source_context(dataset_directory, spec):
    context = {'knownTaxa': taxonomy_ids(artifact_path(dataset_directory, 'db/taxonomy/nodes.dmp'))}
    read_inputs = spec.get('readInputs')
    if read_inputs is not None:
        if not isinstance(read_inputs, list) or len(read_inputs) not in (1, 2):
            raise ValueError('readInputs must specify one FASTQ or a mate pair')
        groups = [parse_fastq(artifact_path(dataset_directory, path)) for path in read_inputs]
        if len(groups) == 2 and len(groups[0]) != len(groups[1]):
            raise ValueError('source FASTQ mate counts differ')
        records = []
        def read_id(record):
            identifier = record['title'].split()[0]
            return identifier[:-2] if len(groups) == 2 and len(identifier) > 2 and identifier.endswith(('/1', '/2')) else identifier
        for mates in zip(*groups):
            identifiers = [read_id(record) for record in mates]
            if len(set(identifiers)) != 1:
                raise ValueError('source FASTQ mate identifiers disagree')
            records.append({'id': identifiers[0], 'lengths': [len(record['sequence']) for record in mates]})
        if len({row['id'] for row in records}) != len(records):
            raise ValueError('source reads have duplicate classified identifiers')
        context.update({'readRecords': records, 'totalReads': len(records), 'sourceReads': groups})
    return context


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('expected')
    parser.add_argument('actual')
    parser.add_argument('--tool', required=True, choices=['kraken2', 'bracken'])
    parser.add_argument('--case', required=True)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--report', required=True)
    parser.add_argument('--actual-target', required=True, choices=['native', 'patched-native', 'wasm'])
    args = parser.parse_args()
    try:
        manifest = read_json(args.manifest)
        if args.dataset not in manifest['datasets']:
            raise ValueError('unknown dataset')
        plan = read_json(Path(__file__).with_name('cases.json'))
        matches = [case for case in plan['tools'][args.tool]['cases'] if case['id'] == args.case]
        if len(matches) != 1:
            raise ValueError('case must identify one unique planned case')
        context = source_context(artifact_path(Path(args.manifest).parent, args.dataset), matches[0])
        context['tool'] = args.tool
        context.update({'expectedTarget': 'native', 'actualTarget': args.actual_target})
        result = compare(args.expected, args.actual, matches[0], context)
    except Exception as error:
        result = {'schema': 1, 'tool': args.tool, 'equal': False, 'error': str(error)}
    Path(args.report).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    return 0 if result['equal'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
