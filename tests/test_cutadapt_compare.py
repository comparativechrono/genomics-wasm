import gzip
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('cutadapt_compare', Path(__file__).parents[1] / 'validation/cutadapt/compare.py')
COMPARE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COMPARE)


class CutadaptComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.left, self.right = self.root / 'native', self.root / 'wasm'
        self.left.mkdir()
        self.right.mkdir()
        self.inputs = [[('read1/1 annotation', 'AACCGGTT', 'ABCDEFGH'), ('read2/1', 'TTAACCGG', 'HIJKLMNO')],
                       [('read1/2 annotation', 'CCAATTGG', 'PQRSTUVW'), ('read2/2', 'GGTTCCAA', 'QRSTUVWX')]]
        self.output = [[('read1/1 annotation', 'CCGG', 'CDEF'), ('read2/1', 'TAACC', 'IJKLM')],
                       [('read1/2 annotation', 'AATT', 'RSTU'), ('read2/2', 'TTCC', 'STUV')]]
        self.context = {'inputs': self.inputs, 'readGroups': [['out1.fastq', 'out2.fastq']], 'version': '5.2'}
        for directory in (self.left, self.right):
            self.write(directory)

    @staticmethod
    def encode(records):
        return ''.join(f'@{title}\n{sequence}\n+\n{quality}\n' for title, sequence, quality in records).encode()

    def report(self, output=None):
        output = self.output if output is None else output
        basecounts = [sum(len(record[1]) for record in mate) for mate in output]
        return {'tag': 'Cutadapt report', 'schema_version': [0, 3], 'cutadapt_version': '5.2',
                'python_version': '3.14.2', 'command_line_arguments': ['-j', '1'], 'cores': 1,
                'input': {'path1': 'reads_1.fastq', 'path2': 'reads_2.fastq', 'paired': True},
                'read_counts': {'input': 2, 'output': len(output[0]), 'read1_with_adapter': 2,
                                'read2_with_adapter': 2, 'filtered': {'too_short': 2 - len(output[0]), 'too_long': None}},
                'basepair_counts': {'input': 32, 'input_read1': 16, 'input_read2': 16,
                                   'output': sum(basecounts), 'output_read1': basecounts[0], 'output_read2': basecounts[1]},
                'adapters_read1': [{'three_prime_end': {'trimmed_lengths': [{'len': 4, 'expect': 0.125, 'counts': [2, 0]}]}}],
                'adapters_read2': []}

    def write(self, directory, output=None, report=None):
        output = self.output if output is None else output
        for index, records in enumerate(output, 1):
            (directory / f'out{index}.fastq').write_bytes(self.encode(records))
        (directory / 'report.json').write_text(json.dumps(self.report(output) if report is None else report))

    def compare(self):
        return COMPARE.compare_directories(self.left, self.right, self.context)

    def test_exact_records_and_statistics(self):
        result = self.compare()
        self.assertTrue(result['equal'])
        self.assertEqual(result['invariants']['outputBases'], [9, 8])

    def test_gzip_metadata_is_ignored_but_crc_is_checked(self):
        for directory, timestamp in ((self.left, 1), (self.right, 999)):
            for index in (1, 2):
                raw = (directory / f'out{index}.fastq').read_bytes()
                (directory / f'out{index}.fastq.gz').write_bytes(gzip.compress(raw, mtime=timestamp))
        self.context['readGroups'] = [['out1.fastq.gz', 'out2.fastq.gz']]
        self.assertTrue(self.compare()['equal'])
        target = self.right / 'out1.fastq.gz'
        raw = bytearray(target.read_bytes())
        raw[-8] ^= 1
        target.write_bytes(raw)
        with self.assertRaises((ValueError, gzip.BadGzipFile)):
            self.compare()

    def test_full_identifier_preserved(self):
        target = self.right / 'out1.fastq'
        target.write_bytes(target.read_bytes().replace(b' annotation', b' changed'))
        with self.assertRaisesRegex(ValueError, 'identifier'):
            self.compare()

    def test_read_order_and_pairing_preserved(self):
        self.write(self.right, [list(reversed(mate)) for mate in self.output])
        with self.assertRaisesRegex(ValueError, 'order'):
            self.compare()
        self.write(self.right, [self.output[0], list(reversed(self.output[1]))])
        with self.assertRaisesRegex(ValueError, 'pairing'):
            self.compare()

    def test_bases_qualities_stay_corresponding_source_slice(self):
        altered = [[*mate] for mate in self.output]
        altered[0][0] = ('read1/1 annotation', 'CCGG', 'ABCD')
        self.write(self.right, altered)
        with self.assertRaisesRegex(ValueError, 'matched slice'):
            self.compare()

    def test_valid_but_different_trimming_is_unequal(self):
        altered = [[*mate] for mate in self.output]
        altered[0][0] = ('read1/1 annotation', 'ACCG', 'BCDE')
        self.write(self.right, altered)
        self.assertFalse(self.compare()['equal'])

    def test_output_count_and_base_invariants(self):
        for key, value in [('output', 1), ('input', 3)]:
            report = self.report()
            report['read_counts'][key] = value
            self.write(self.right, report=report)
            with self.assertRaisesRegex(ValueError, 'read count'):
                self.compare()
        report = self.report()
        report['basepair_counts']['output_read1'] += 1
        self.write(self.right, report=report)
        with self.assertRaisesRegex(ValueError, 'per-mate output'):
            self.compare()

    def test_all_statistics_remain_exact_and_python_is_pinned(self):
        report = self.report()
        report['adapters_read1'][0]['three_prime_end']['trimmed_lengths'][0]['expect'] = 0.12500000001
        self.write(self.right, report=report)
        self.assertFalse(self.compare()['equal'])
        report = self.report()
        report['python_version'] = '3.14.3'
        self.write(self.right, report=report)
        with self.assertRaisesRegex(ValueError, 'Python version'):
            self.compare()

    def test_duplicate_json_and_nonfinite_rejected(self):
        path = self.right / 'report.json'
        path.write_text('{"cores":1,"cores":1}')
        with self.assertRaisesRegex(ValueError, 'duplicate JSON'):
            self.compare()
        path.write_text('{"value":NaN}')
        with self.assertRaisesRegex(ValueError, 'non-finite'):
            self.compare()

    def test_boolean_cannot_replace_a_numeric_statistic(self):
        report = self.report()
        report['adapters_read1'][0]['three_prime_end']['trimmed_lengths'][0]['counts'][1] = False
        self.write(self.right, report=report)
        self.assertFalse(self.compare()['equal'])
        report['schema_version'][0] = False
        self.write(self.right, report=report)
        with self.assertRaisesRegex(ValueError, 'schema'):
            self.compare()

    def test_fastq_requires_real_line_endings(self):
        path = self.right / 'out1.fastq'
        original = path.read_bytes()
        path.write_bytes(original.replace(b'\n', b'\r\n'))
        self.assertTrue(self.compare()['equal'])
        for separator in (b'\v', b'\f', b'\r'):
            path.write_bytes(original.replace(b'\n', separator))
            with self.assertRaisesRegex(ValueError, 'control character|line ending'):
                self.compare()

    def test_long_decimal_statistics_are_not_rounded(self):
        path = self.right / 'report.json'
        path.write_text(path.read_text().replace('0.125', '0.125000000000000000000000000001'))
        self.assertFalse(self.compare()['equal'])

    def test_empty_output_only_when_explicitly_allowed(self):
        for directory in (self.left, self.right):
            self.write(directory, [[], []])
        with self.assertRaisesRegex(ValueError, 'vacuous'):
            self.compare()
        self.context['requireNonzero'] = False
        self.assertTrue(self.compare()['equal'])

    def test_incomplete_fastq_and_invalid_quality_rejected(self):
        for content in (b'@read1\nAC\n+\n', b'@read1\nAC\n+\n!\n'):
            (self.right / 'out1.fastq').write_bytes(content)
            with self.assertRaises(ValueError):
                self.compare()

    def test_json_arguments_bound_to_case(self):
        self.context['args'] = ['-j', '2']
        with self.assertRaisesRegex(ValueError, 'command arguments'):
            self.compare()


if __name__ == '__main__':
    unittest.main()
