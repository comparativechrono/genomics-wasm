"""Adversarial structural fixtures, not native biological validation evidence.

Output schemas are independently transcribed from Kraken2 2.17.2 and Bracken
3.1 source. Values below are deliberately small constructed examples.
"""
from decimal import Decimal, localcontext
import base64
import gzip
import hashlib
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE = importlib.util.spec_from_file_location('metagenomics_compare_tests', ROOT / 'validation/metagenomics/compare.py')
compare = importlib.util.module_from_spec(MODULE)
MODULE.loader.exec_module(compare)

CLASSIFICATIONS = 'C\tread1\t11\t100\t11:40 0:10 A:5 11:11\nU\tread2\t0\t100\t0:66\n'
PAIRED = 'C\tpair1\t11\t100|90\t11:66 |:| 11:50 A:6 \n'
REPORT = (' 50.00\t1\t1\tU\t0\tunclassified\n'
          ' 50.00\t1\t0\tR\t1\troot\n'
          ' 50.00\t1\t0\tG\t10\t  Genus\n'
          ' 50.00\t1\t1\tS\t11\t    Genus species\n')
MINIMIZERS = (' 50.00\t1\t1\t0\t0\tU\t0\tunclassified\n'
              ' 50.00\t1\t0\t42\t18\tR\t1\troot\n'
              ' 50.00\t1\t0\t42\t18\tG\t10\t  Genus\n'
              ' 50.00\t1\t1\t42\t18\tS\t11\t    Genus species\n')
ABUNDANCE = (compare.ABUNDANCE_HEADER + '\n'
             'Genus species\t11\tS\t5\t1\t6\t0.60000\n'
             'Other species\t12\tS\t3\t1\t4\t0.40000\n')
REDISTRIBUTED = ('100.00\t10\t0\tR\t1\troot\n'
                 '100.00\t10\t0\tG\t10\t  Genus\n'
                 '60.00\t6\t6\tS\t11\t    Genus species\n'
                 '40.00\t4\t4\tS\t12\t    Other species\n')
RAW_DISTRIBUTION = 'seq1\t11\t\t0:2 10:1 11:7 \nseq2\t12\t\t10:2 12:8 \n'
DISTRIBUTION = (compare.DISTRIBUTION_HEADER + '\n'
                '0\t11:2:10 \n10\t11:1:10 12:2:10 \n11\t11:7:10 \n12\t12:8:10 \n')
FASTQ = '@read1\nACGT\n+\nABCD\n@read2 note\nNNNN\n+\n!!!!\n'
OPTIONS = struct.pack('<QQQQ?7xQiii4x', 35, 31, 0xDEADBEEF, 0xFEEDBEEF, True, 0, 1, 0, 0)


class ComparatorFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def path(self, text, filename='output.txt'):
        path = self.root / filename
        path.write_bytes(text if isinstance(text, (bytes, bytearray)) else text.encode())
        return path

    def parse(self, kind, text, spec=None, context=None):
        return compare.PARSERS[kind](self.path(text), spec or {}, context or {})

    def reject(self, kind, texts, spec=None):
        for text in texts:
            with self.subTest(text=text):
                with self.assertRaises((ValueError, UnicodeError, OSError)):
                    self.parse(kind, text, spec)


class ParserTests(ComparatorFixture, unittest.TestCase):
    def test_classification_preserves_all_fields_and_order(self):
        value = self.parse('classifications', CLASSIFICATIONS)
        self.assertEqual(value[0], {'status': 'C', 'id': 'read1', 'taxid': 11, 'lengths': [100],
                                    'hits': [[11, 40], [0, 10], ['A', 5], [11, 11]]})
        self.assertEqual([row['id'] for row in value], ['read1', 'read2'])

    def test_classification_rejects_status_taxid_and_shape_corruption(self):
        self.reject('classifications', [CLASSIFICATIONS.replace('C\tread1', 'U\tread1'),
                    CLASSIFICATIONS.replace('U\tread2\t0', 'C\tread2\t0'),
                    CLASSIFICATIONS.replace('read2', 'read1'),
                    CLASSIFICATIONS.replace('\t11\t100', '\t011\t100'),
                    CLASSIFICATIONS.replace('read1', 'read 1'),
                    CLASSIFICATIONS.replace('\t100\t', '\t100|90|80\t'),
                    CLASSIFICATIONS.replace('A:5', 'A:-5'),
                    CLASSIFICATIONS.replace('0:10', '0:0'),
                    CLASSIFICATIONS.replace('0:10', 'X:10'),
                    CLASSIFICATIONS.replace('11:40 ', '11:40  '), '', '\n'])

    def test_paired_border_and_lengths_are_retained(self):
        value = self.parse('classifications', PAIRED, {'paired': True})
        self.assertEqual(value[0]['lengths'], [100, 90])
        self.assertIn('|:|', value[0]['hits'])
        self.reject('classifications', [PAIRED.replace(' |:|', ''), PAIRED.replace('|:|', '|:| |:|')], {'paired': True})
        with self.assertRaises(ValueError):
            self.parse('classifications', PAIRED, {'paired': False})

    def test_zero_length_minimizer_sentinel_is_valid(self):
        self.assertEqual(self.parse('classifications', 'U\tshort\t0\t10\t0:0\n')[0]['hits'], [[0, 0]])

    def test_quick_mode_is_explicit_and_exact(self):
        text = 'C\tread1\t11\t100|90\t11:Q\n'
        self.assertEqual(self.parse('classifications', text, {'quick': True})[0]['hits'], [[11, 'Q']])
        self.reject('classifications', [text])
        self.reject('classifications', [text.replace('11:Q', '10:Q'), text.replace('11:Q', '11:1')], {'quick': True})

    def test_translated_frame_separator_is_explicit(self):
        text = 'C\tread1\t11\t100\t11:1 -:- 11:1 -:- 11:1 -:- 11:1 -:- 11:1 -:- 11:1\n'
        self.parse('classifications', text, {'translated': True})
        self.reject('classifications', [text])

    def test_source_read_identifiers_lengths_and_order_are_checked(self):
        records = [{'id': 'read1', 'lengths': [100]}, {'id': 'read2', 'lengths': [100]}]
        self.parse('classifications', CLASSIFICATIONS, context={'readRecords': records})
        for altered in (records[::-1], records[:1], [{'id': 'read1', 'lengths': [99]}, records[1]]):
            with self.assertRaises(ValueError):
                self.parse('classifications', CLASSIFICATIONS, context={'readRecords': altered})

    def test_unknown_classification_and_hit_taxa_are_rejected(self):
        for taxa in ([0, 1, 10], [0, 1, 11]):
            altered = CLASSIFICATIONS.replace('0:10', '10:10')
            with self.assertRaises(ValueError):
                self.parse('classifications', altered, context={'knownTaxa': taxa})

    def test_report_valid_tree_and_minimizer_columns(self):
        rows = self.parse('kraken-report', REPORT)
        self.assertEqual(rows[-1]['depth'], 2)
        self.assertEqual(rows[-1]['percentage'].as_tuple().exponent, -2)
        rows = self.parse('kraken-report', MINIMIZERS, {'minimizerColumns': True})
        self.assertEqual(rows[-1]['minimizers'], [42, 18])

    def test_report_duplicate_and_corrupt_tree_rejected(self):
        self.reject('kraken-report', [REPORT + REPORT.splitlines()[-1] + '\n',
                    REPORT.replace('\t11\t', '\t10\t'), REPORT.replace('    Genus species', '   Genus species'),
                    REPORT.replace('    Genus species', '      Genus species'),
                    REPORT.replace('\tR\t1\t', '\tG\t1\t'), REPORT.replace('\tR\t1\t', '\tR\t2\t'),
                    REPORT.replace('\tU\t0\t', '\tS\t0\t'), REPORT.replace('\tS\t11\t', '\tX\t11\t'),
                    REPORT.replace('unclassified', 'wrong'), REPORT.replace('Genus species', ''),
                    REPORT.replace('Genus species', 'Genus species ')])

    def test_report_count_and_percentage_invariants(self):
        self.reject('kraken-report', [REPORT.replace('\t1\t1\tS', '\t1\t2\tS'),
                    REPORT.replace('\t1\t0\tG', '\t2\t0\tG'),
                    REPORT.replace('50.00', '50.01', 1), REPORT.replace('50.00', '101.00', 1),
                    REPORT.replace('50.00', '50.0', 1), REPORT.replace('50.00', 'NaN', 1)])
        with self.assertRaises(ValueError):
            self.parse('kraken-report', REPORT, context={'totalReads': 3})

    def test_report_minimizer_counts_are_not_discarded(self):
        self.reject('kraken-report', [MINIMIZERS.replace('\t42\t18\t', '\t42\t43\t', 1),
                    MINIMIZERS.replace('\t42\t18\t', '\t42\t', 1)])
        with self.assertRaises(ValueError):
            self.parse('kraken-report', MINIMIZERS, {'minimizerColumns': False})
        with self.assertRaises(ValueError):
            self.parse('kraken-report', REPORT, {'minimizerData': True})

    def test_report_all_unclassified_and_zero_count_taxa(self):
        self.parse('kraken-report', '100.00\t2\t2\tU\t0\tunclassified\n')
        self.parse('kraken-report', REPORT + '  0.00\t0\t0\tS\t12\t    Other species\n')
        self.reject('kraken-report', ['  0.00\t0\t0\tR\t1\troot\n'])

    def test_bracken_abundance_columns_and_precision(self):
        rows = self.parse('bracken-abundance', ABUNDANCE, {'level': 'S'})
        self.assertEqual(rows[0]['estimated'], 6)
        self.assertEqual(rows[0]['fraction'].as_tuple().exponent, -5)
        with self.assertRaises(ValueError):
            self.parse('bracken-abundance', ABUNDANCE, {'rank': 'G'})

    def test_bracken_abundance_corruption(self):
        self.reject('bracken-abundance', [ABUNDANCE.replace('new_est_reads', 'estimate'),
                    ABUNDANCE.replace('\t5\t1\t6\t', '\t5\t2\t6\t'),
                    ABUNDANCE.replace('0.60000', '0.60001'), ABUNDANCE.replace('0.60000', '0.6'),
                    ABUNDANCE.replace('0.60000', 'NaN'), ABUNDANCE.replace('0.60000', '1.00001'),
                    ABUNDANCE.replace('0.60000', '-0.60000'), ABUNDANCE.replace('\t12\t', '\t11\t'),
                    ABUNDANCE.replace('\t12\tS\t', '\t12\tG\t'),
                    ABUNDANCE.replace('\t3\t1\t4\t', '\t3\t-1\t2\t'),
                    compare.ABUNDANCE_HEADER + '\n'])

    def test_bracken_fraction_denominator_may_exceed_sum_of_printed_counts(self):
        # Two internal estimates of 6.8 and 3.8 give int(sum)=10, printed 6+3=9.
        text = ABUNDANCE.replace('\t3\t1\t4\t0.40000', '\t3\t0\t3\t0.30000')
        self.parse('bracken-abundance', text)
        self.reject('bracken-abundance', [text.replace('0.30000', '0.20000')])

    def test_redistributed_report_independent_integer_truncation(self):
        text = REDISTRIBUTED.replace('\t4\t4\tS', '\t3\t3\tS').replace('40.00', '38.00').replace('60.00', '62.00')
        self.parse('kraken-report', text, {'redistributed': True})
        self.reject('kraken-report', [text], {'redistributed': False})
        self.reject('kraken-report', [text.replace('\t3\t3\tS', '\t2\t2\tS'), REPORT], {'redistributed': True})

    def test_redistributed_terminal_genus_can_have_zero_direct_reads(self):
        # Upstream counts omitted species children when deciding direct=0.
        text = ('100.00\t10\t0\tR\t1\troot\n'
                '100.00\t10\t0\tG\t10\t  Genus\n')
        self.parse('kraken-report', text, {'redistributed': True})
        self.reject('kraken-report', [text])
        self.reject('kraken-report', [text.replace('\t10\t0\tG', '\t10\t1\tG')], {'redistributed': True})

    def test_redistributed_percentages_still_obey_unrounded_count_bounds(self):
        self.reject('kraken-report', [REDISTRIBUTED.replace('100.00', '99.99', 1),
                                     REDISTRIBUTED.replace('60.00', '90.00')], {'redistributed': True})

    def test_raw_read_distribution_preserves_mappings(self):
        rows = self.parse('bracken-read-distribution', RAW_DISTRIBUTION)
        self.assertEqual(rows[0]['hits'], [[0, 2], [10, 1], [11, 7]])

    def test_raw_read_distribution_corruptions(self):
        self.reject('bracken-read-distribution', [RAW_DISTRIBUTION.replace('\t\t', '\t0\t', 1),
                    RAW_DISTRIBUTION.replace('0:2', '0:0'), RAW_DISTRIBUTION.replace('0:2', '0:-2'),
                    RAW_DISTRIBUTION.replace('0:2 10:1', '10:1 0:2'),
                    RAW_DISTRIBUTION.replace('10:1', '0:1'), RAW_DISTRIBUTION.replace('seq2', 'seq1'),
                    RAW_DISTRIBUTION.replace('\t11\t', '\t0\t')])

    def test_raw_read_distribution_accepts_complete_last_line_without_newline(self):
        self.assertEqual(self.parse('bracken-read-distribution', RAW_DISTRIBUTION),
                         self.parse('bracken-read-distribution', RAW_DISTRIBUTION.rstrip('\n')))

    def test_empty_raw_helper_output_requires_explicit_empty_case(self):
        self.assertEqual(self.parse('bracken-read-distribution', '', {'requireNonempty': False}), [])
        self.reject('bracken-read-distribution', [''])

    def test_generic_read_distribution_parser_always_rejects_native_phantom(self):
        self.reject('bracken-read-distribution', [RAW_DISTRIBUTION.encode() + b'\x00\t-12345\t\t\n'], {'nativeEofArtifact': True})

    def test_distribution_preserves_order_and_counts(self):
        rows = self.parse('bracken-distribution', DISTRIBUTION)
        self.assertEqual(rows[1], {'mapped': 10, 'genomes': [[11, 1, 10], [12, 2, 10]]})

    def test_distribution_rejects_duplicates_and_nonconservation(self):
        self.reject('bracken-distribution', [DISTRIBUTION.replace('mapped_taxid', 'taxid'),
                    DISTRIBUTION.replace('11:2:10', '11:2:9'), DISTRIBUTION.replace('11:7:10', '11:6:10'),
                    DISTRIBUTION.replace('11:7:10', '11:11:10'), DISTRIBUTION.replace('11:7:10', '11:0:10'),
                    DISTRIBUTION.replace('11:1:10 12:2:10', '11:1:10 11:2:10'),
                    DISTRIBUTION.replace('\n12\t', '\n11\t'),
                    DISTRIBUTION.replace('11:2:10', '11:2'), compare.DISTRIBUTION_HEADER + '\n'])

    def test_fastq_preserves_sequence_quality_headers_and_read_order(self):
        rows = self.parse('fastq', FASTQ)
        self.assertEqual(rows[1], {'title': 'read2 note', 'sequence': 'NNNN', 'quality': '!!!!'})
        self.reject('fastq', [FASTQ.replace('ABCD', 'ABC'), FASTQ.replace('ACGT', 'ACGX'),
                    FASTQ.replace('read2', 'read1'), FASTQ.replace('\n+\n', '\n+wrong\n', 1),
                    FASTQ + '@incomplete\nACGT\n'])

    def test_text_rejects_unicode_separators_and_control_characters(self):
        mutations = ['\r', '\v', '\f', '\x00', '\x7f', '\x85', '\u2028', '\u2029', '\ufeff', '\u200b']
        for control in mutations:
            with self.subTest(control=repr(control)):
                self.reject('classifications', [CLASSIFICATIONS.replace('read1', 'read1' + control)])
        self.reject('classifications', [CLASSIFICATIONS.encode().replace(b'read1', b'read\xff1')])

    def test_lf_and_crlf_are_equivalent_but_blank_rows_are_rejected(self):
        self.assertEqual(self.parse('classifications', CLASSIFICATIONS), self.parse('classifications', CLASSIFICATIONS.replace('\n', '\r\n')))
        self.reject('classifications', [CLASSIFICATIONS + '\n'])

    def test_complete_gzip_crc_is_checked(self):
        raw = gzip.compress(CLASSIFICATIONS.encode())
        self.assertEqual(len(compare.parse_classifications(self.path(raw, 'out.gz'))), 2)
        for corrupt in (raw[:-1], raw[:-8] + b'\0' * 8, b'not gzip'):
            with self.assertRaises((ValueError, OSError, EOFError)):
                compare.parse_classifications(self.path(corrupt, 'out.gz'))

    def test_decimal_canonicalisation_does_not_use_active_precision(self):
        values = [Decimal('0.123456789012345678901234567890123456789'), 2 ** 65, True, '1']
        expected = compare.digest(values)
        with localcontext() as context:
            context.prec = 2
            self.assertEqual(compare.digest(values), expected)
        self.assertNotEqual(compare.digest(Decimal('0.60')), compare.digest(Decimal('0.60000')))
        self.assertNotEqual(compare.digest(1), compare.digest(True))
        self.assertNotEqual(compare.digest(1), compare.digest('1'))
        with self.assertRaises(ValueError):
            compare.digest(Decimal('NaN'))

    def test_options_retains_all_declared_fields(self):
        parsed = self.parse('kraken-options', OPTIONS)
        self.assertEqual(parsed, {'k': 35, 'l': 31, 'spacedSeedMask': 0xDEADBEEF, 'toggleMask': 0xFEEDBEEF,
                                 'dnaDatabase': True, 'minimumAcceptableHash': 0, 'reverseComplementVersion': 1,
                                 'databaseVersion': 0, 'databaseType': 0})

    def test_options_ignores_only_demonstrated_native_struct_padding(self):
        baseline = self.parse('kraken-options', OPTIONS)
        for offset in list(range(33, 40)) + list(range(60, 64)):
            changed = bytearray(OPTIONS)
            changed[offset] = 255
            self.assertEqual(self.parse('kraken-options', changed), baseline)
        for offset in (0, 8, 16, 24, 32, 40, 48, 52, 56):
            changed = bytearray(OPTIONS)
            changed[offset] ^= 1
            with self.subTest(offset=offset):
                self.assertNotEqual(self.parse('kraken-options', changed), baseline)

    def test_options_rejects_bad_abi_bool_and_lengths(self):
        changed = bytearray(OPTIONS)
        changed[32] = 2
        self.reject('kraken-options', [OPTIONS[:-1], OPTIONS + b'\0', changed,
                    struct.pack('<QQQQ?7xQiii4x', 30, 31, 0, 0, True, 0, 1, 0, 0)])

    def test_taxonomy_context_rejects_duplicate_missing_parent_and_cycle(self):
        nodes = '1\t|\t1\t|\tno rank\t|\n10\t|\t1\t|\tgenus\t|\n11\t|\t10\t|\tspecies\t|\n'
        self.assertEqual(compare.taxonomy_ids(self.path(nodes)), [1, 10, 11])
        for bad in (nodes + nodes.splitlines()[-1] + '\n', nodes.replace('11\t|\t10', '11\t|\t12'),
                    nodes.replace('10\t|\t1\t', '10\t|\t11\t'), nodes.replace('1\t|\t1\t', '1\t|\t10\t')):
            with self.assertRaises(ValueError):
                compare.taxonomy_ids(self.path(bad))

    def test_source_context_checks_input_pairing_and_preserves_single_mate_suffix(self):
        directory = self.root / 'dataset'
        (directory / 'db/taxonomy').mkdir(parents=True)
        (directory / 'db/taxonomy/nodes.dmp').write_text('1\t|\t1\t|\tno rank\t|\n')
        (directory / 'one.fastq').write_text('@pair/1 note\nACGT\n+\n!!!!\n')
        (directory / 'two.fastq').write_text('@pair/2 note\nACG\n+\n!!!\n')
        context = compare.source_context(directory, {'readInputs': ['one.fastq', 'two.fastq']})
        self.assertEqual(context['readRecords'], [{'id': 'pair', 'lengths': [4, 3]}])
        self.assertEqual(context['totalReads'], 1)
        context = compare.source_context(directory, {'readInputs': ['one.fastq']})
        self.assertEqual(context['readRecords'][0]['id'], 'pair/1')
        (directory / 'two.fastq').write_text('@other/2\nACGT\n+\n!!!!\n')
        with self.assertRaises(ValueError):
            compare.source_context(directory, {'readInputs': ['one.fastq', 'two.fastq']})


class IntegrationTests(ComparatorFixture, unittest.TestCase):
    def dirs(self, files):
        left, right = self.root / 'native', self.root / 'actual'
        left.mkdir(exist_ok=True)
        right.mkdir(exist_ok=True)
        for directory in (left, right):
            for relative, value in files.items():
                path = directory / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(value if isinstance(value, bytes) else value.encode())
        return left, right

    def test_comparison_reports_hashes_and_counts(self):
        left, right = self.dirs({'reads.tsv': CLASSIFICATIONS, 'report.tsv': REPORT})
        spec = {'tool': 'kraken2', 'artifacts': [{'path': 'reads.tsv', 'kind': 'classifications'},
                                               {'path': 'report.tsv', 'kind': 'kraken-report'}],
                'biologicalArtifacts': ['reads.tsv']}
        result = compare.compare(left, right, spec)
        self.assertTrue(result['equal'])
        self.assertEqual(result['semanticDigest'], result['expectedSemanticDigest'])
        self.assertEqual(result['invariants']['reads.tsv']['classified'], 1)
        self.assertEqual(result['invariants'], result['nativeInvariants'])

    def test_classifications_and_report_must_reconcile_even_when_both_sides_identical(self):
        left, right = self.dirs({'reads': CLASSIFICATIONS.replace('\t11\t100', '\t10\t100'), 'report': REPORT})
        spec = {'artifacts': [{'path': 'reads', 'kind': 'classifications'}, {'path': 'report', 'kind': 'kraken-report'}]}
        with self.assertRaises(ValueError):
            compare.compare(left, right, spec)

    def test_reordered_reads_are_not_normalised(self):
        left, right = self.dirs({'reads': CLASSIFICATIONS})
        (right / 'reads').write_text('\n'.join(reversed(CLASSIFICATIONS.splitlines())) + '\n')
        self.assertFalse(compare.compare(left, right, {'artifacts': [{'path': 'reads', 'kind': 'classifications'}]})['equal'])

    def test_changed_hit_order_or_any_read_field_changes_digest(self):
        left, right = self.dirs({'reads': CLASSIFICATIONS})
        spec = {'artifacts': [{'path': 'reads', 'kind': 'classifications'}]}
        original = compare.compare(left, right, spec)
        for changed in (CLASSIFICATIONS.replace('0:10 A:5', 'A:5 0:10'),
                        CLASSIFICATIONS.replace('read1', 'different'), CLASSIFICATIONS.replace('\t100\t', '\t101\t'),
                        CLASSIFICATIONS.replace('11:40', '11:41'), CLASSIFICATIONS.replace('\t11\t100', '\t10\t100')):
            (right / 'reads').write_text(changed)
            result = compare.compare(left, right, spec)
            self.assertFalse(result['equal'])
            self.assertNotEqual(result['biologicalDigest'], original['biologicalDigest'])

    def test_biological_digest_ignores_ancillary_artifacts_and_filenames(self):
        left, right = self.dirs({'reads': CLASSIFICATIONS, 'binary': b'first', 'renamed': CLASSIFICATIONS})
        spec = {'artifacts': [{'path': 'reads', 'kind': 'classifications'}, {'path': 'binary', 'kind': 'exact'}],
                'biologicalArtifacts': ['reads']}
        baseline = compare.compare(left, right, spec)
        (right / 'binary').write_bytes(b'second')
        changed = compare.compare(left, right, spec)
        self.assertFalse(changed['equal'])
        self.assertEqual(baseline['biologicalDigest'], changed['biologicalDigest'])
        renamed = compare.compare(left, right, {'artifacts': [{'path': 'renamed', 'kind': 'classifications'}]})
        self.assertEqual(baseline['biologicalDigest'], renamed['biologicalDigest'])

    def test_binary_artifacts_are_exact_not_ignored(self):
        left, right = self.dirs({'db/hash.k2d': b'\0\1\2\3'})
        spec = {'artifacts': [{'path': 'db/hash.k2d', 'kind': 'exact'}]}
        self.assertTrue(compare.compare(left, right, spec)['equal'])
        (right / 'db/hash.k2d').write_bytes(b'\0\1\2\4')
        self.assertFalse(compare.compare(left, right, spec)['equal'])
        (right / 'db/hash.k2d').write_bytes(b'')
        with self.assertRaises(ValueError):
            compare.compare(left, right, spec)

    def test_options_padding_only_is_declared_normalisation(self):
        left, right = self.dirs({'opts.k2d': OPTIONS})
        changed = bytearray(OPTIONS)
        changed[33:40] = b'garbage'
        (right / 'opts.k2d').write_bytes(changed)
        result = compare.compare(left, right, {'artifacts': [{'path': 'opts.k2d', 'kind': 'kraken-options'}]})
        self.assertTrue(result['equal'])
        self.assertIn('33..39', result['normalisation'][-1])

    def test_native_eof_exception_retains_exact_raw_evidence(self):
        phantom = b'\x00\t-12345\t\t\n'
        raw = RAW_DISTRIBUTION.encode() + phantom
        left, right = self.dirs({'raw': RAW_DISTRIBUTION})
        (left / 'raw').write_bytes(raw)
        spec = {'artifacts': [{'path': 'raw', 'kind': 'bracken-read-distribution', 'nativeEofArtifact': True}]}
        result = compare.compare(left, right, spec, {'expectedTarget': 'native', 'actualTarget': 'wasm'})
        self.assertTrue(result['equal'])
        self.assertEqual(result['nativeAnomalies']['actual'], [])
        evidence = result['nativeAnomalies']['expected'][0]
        self.assertEqual(evidence['path'], 'raw')
        self.assertEqual(evidence['offset'], len(RAW_DISTRIBUTION.encode()))
        self.assertEqual(evidence['rawSha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(evidence['fragmentSha256'], hashlib.sha256(phantom).hexdigest())
        self.assertEqual(base64.b64decode(evidence['base64']), phantom)

    def test_native_contract_can_have_different_uninitialised_phantom_integers(self):
        left, right = self.dirs({'raw': RAW_DISTRIBUTION})
        (left / 'raw').write_bytes(RAW_DISTRIBUTION.encode() + b'\x00\t-12345\t\t\n')
        (right / 'raw').write_bytes(RAW_DISTRIBUTION.encode() + b'\x00\t12345\t\t\n')
        spec = {'artifacts': [{'path': 'raw', 'kind': 'bracken-read-distribution', 'nativeEofArtifact': True}]}
        result = compare.compare(left, right, spec, {'expectedTarget': 'native', 'actualTarget': 'native'})
        self.assertTrue(result['equal'])
        self.assertNotEqual(result['nativeAnomalies']['expected'], result['nativeAnomalies']['actual'])

    def test_native_eof_exception_requires_explicit_artifact_and_target(self):
        left, right = self.dirs({'raw': RAW_DISTRIBUTION.encode() + b'\x00\t0\t\t\n'})
        for enabled, expected_target, actual_target in ((False, 'native', 'native'), (True, 'patched-native', 'native'),
                                                        (True, 'wasm', 'native'), (True, 'native', 'patched-native'),
                                                        (True, 'native', 'wasm'), (True, None, None)):
            with self.subTest(enabled=enabled, expected=expected_target, actual=actual_target):
                with self.assertRaises(ValueError):
                    compare.compare(left, right, {'artifacts': [{'path': 'raw', 'kind': 'bracken-read-distribution', 'nativeEofArtifact': enabled}]},
                                    {'expectedTarget': expected_target, 'actualTarget': actual_target})

    def test_native_eof_exception_rejects_interior_duplicate_and_unknown_tails(self):
        phantom = b'\x00\t12345\t\t\n'
        valid = RAW_DISTRIBUTION.encode()
        left, right = self.dirs({'raw': RAW_DISTRIBUTION})
        invalid = [valid + phantom + phantom, phantom + valid, valid + b'unknown\t12345\t\t\n',
                   valid + b'\x00\t12345\textra\t\n', valid + b'\x00\t12345\t\t',
                   valid + b'\x00\t12345\t\t\r\n', valid + b'\x00\t2147483648\t\t\n',
                   valid + b'\x00\t-2147483649\t\t\n', valid + b'\x00\t12.0\t\t\n',
                   valid + b'\x00\t+12\t\t\n', phantom, valid[:-1] + phantom]
        for bad in invalid:
            with self.subTest(raw=bad):
                (left / 'raw').write_bytes(bad)
                with self.assertRaises(ValueError):
                    compare.compare(left, right, {'artifacts': [{'path': 'raw', 'kind': 'bracken-read-distribution', 'nativeEofArtifact': True}]},
                                    {'expectedTarget': 'native', 'actualTarget': 'wasm'})

    def test_native_eof_exception_never_hides_genuine_record_changes(self):
        left, right = self.dirs({'raw': RAW_DISTRIBUTION})
        (left / 'raw').write_bytes(RAW_DISTRIBUTION.encode() + b'\x00\t-12345\t\t\n')
        (right / 'raw').write_text(RAW_DISTRIBUTION.replace('11:7', '11:8'))
        result = compare.compare(left, right, {'artifacts': [{'path': 'raw', 'kind': 'bracken-read-distribution', 'nativeEofArtifact': True}]},
                                 {'expectedTarget': 'native', 'actualTarget': 'wasm'})
        self.assertFalse(result['equal'])

    def test_native_no_phantom_has_no_anomaly_evidence(self):
        left, right = self.dirs({'raw': RAW_DISTRIBUTION.rstrip('\n')})
        result = compare.compare(left, right, {'artifacts': [{'path': 'raw', 'kind': 'bracken-read-distribution', 'nativeEofArtifact': True}]},
                                 {'expectedTarget': 'native', 'actualTarget': 'wasm'})
        self.assertTrue(result['equal'])
        self.assertEqual(result['nativeAnomalies'], {'expected': [], 'actual': []})

    def test_abundance_and_redistributed_report_reconcile(self):
        left, right = self.dirs({'abundance': ABUNDANCE, 'report': REDISTRIBUTED})
        spec = {'artifacts': [{'path': 'abundance', 'kind': 'bracken-abundance'},
                              {'path': 'report', 'kind': 'kraken-report', 'redistributed': True}]}
        self.assertTrue(compare.compare(left, right, spec)['equal'])
        (right / 'report').write_text(REDISTRIBUTED.replace('Genus species', 'Different species'))
        with self.assertRaises(ValueError):
            compare.compare(left, right, spec)

    def test_raw_and_final_distribution_reconcile(self):
        left, right = self.dirs({'raw': RAW_DISTRIBUTION, 'final': DISTRIBUTION})
        spec = {'artifacts': [{'path': 'raw', 'kind': 'bracken-read-distribution'},
                              {'path': 'final', 'kind': 'bracken-distribution'}]}
        self.assertTrue(compare.compare(left, right, spec)['equal'])
        # Still internally conserved, but no longer derived from the raw data.
        (right / 'final').write_text(DISTRIBUTION.replace('11:2:10', '11:3:10').replace('11:7:10', '11:6:10'))
        with self.assertRaises(ValueError):
            compare.compare(left, right, spec)

    def test_missing_empty_duplicate_and_unknown_artifacts_fail(self):
        left, right = self.dirs({'reads': CLASSIFICATIONS})
        for artifacts in ([], [{'path': 'missing', 'kind': 'exact'}], [{'path': 'reads', 'kind': 'unknown'}],
                          [{'path': 'reads', 'kind': 'classifications'}] * 2,
                          [{'path': 'reads', 'kind': 'classifications', 'minimumRecords': 3}]):
            with self.subTest(artifacts=artifacts):
                with self.assertRaises((ValueError, OSError)):
                    compare.compare(left, right, {'artifacts': artifacts})

    def test_required_classification_and_redistribution_counts_are_enforced(self):
        left, right = self.dirs({'reads': CLASSIFICATIONS, 'abundance': ABUNDANCE})
        for artifact in ({'path': 'reads', 'kind': 'classifications', 'minimumClassified': 2},
                         {'path': 'reads', 'kind': 'classifications', 'minimumUnclassified': 2},
                         {'path': 'abundance', 'kind': 'bracken-abundance', 'minimumAddedReads': 3}):
            with self.assertRaises(ValueError):
                compare.compare(left, right, {'artifacts': [artifact]})

    def test_path_traversal_and_symlink_escape_fail(self):
        left, right = self.dirs({'reads': CLASSIFICATIONS})
        for path in ('../escape', '/absolute', 'x/../reads', './reads', ''):
            with self.assertRaises(ValueError):
                compare.compare(left, right, {'artifacts': [{'path': path, 'kind': 'exact'}]})
        outside = self.root / 'outside'
        outside.write_bytes(b'outside')
        (left / 'link').symlink_to(outside)
        with self.assertRaises(ValueError):
            compare.compare(left, right, {'artifacts': [{'path': 'link', 'kind': 'exact'}]})

    def test_invalid_biological_selection_rejected(self):
        left, right = self.dirs({'reads': CLASSIFICATIONS})
        for selection in ([], ['missing'], ['reads', 'reads']):
            with self.assertRaises(ValueError):
                compare.compare(left, right, {'artifacts': [{'path': 'reads', 'kind': 'classifications'}],
                                             'biologicalArtifacts': selection})


if __name__ == '__main__':
    unittest.main()
