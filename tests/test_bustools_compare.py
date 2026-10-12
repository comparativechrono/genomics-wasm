"""Adversarial unit tests for comparison, using deliberately tiny format samples.

These are parser mutations, never biological equivalence fixtures; executable
validation uses the separately provenance-locked experimental BUS data.
"""
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / 'validation/bustools/compare.py'
spec = importlib.util.spec_from_file_location('bustools_compare', MODULE)
compare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare)


def bus(rows=((1, 2, 0, 3, 0, 0), (2, 3, 1, 2, 0, 0))):
    return b'BUS\0' + struct.pack('<4I', 1, 16, 12, 4) + b'test' + b''.join(struct.pack('<QQiIII', *r) for r in rows)


class BustoolsComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.a, self.b = Path(self.temp.name)/'a', Path(self.temp.name)/'b'
        self.a.mkdir(); self.b.mkdir()

    def files(self, values):
        for directory in (self.a, self.b):
            for name, data in values.items():
                (directory/name).write_bytes(data if isinstance(data, bytes) else data.encode())

    def compare(self, artifacts):
        return compare.compare(self.a, self.b, {'artifacts': artifacts})

    def bus_case(self):
        self.files({'data.bus': bus()})
        return [{'path': 'data.bus', 'kind': 'bus', 'minimumRecords': 2, 'minimum': {'barcodes': 2}}]

    def matrix_case(self):
        self.files({'matrix.mtx': '%%MatrixMarket matrix coordinate real general\n%\n2 2 2\n1 1 3\n2 2 2\n',
                    'barcodes.txt': 'AACCGG\nTTGGCC\n', 'genes.txt': 'GENE_A\nGENE_B\n'})
        return [{'path': 'matrix.mtx', 'kind': 'matrix', 'rowLabels': 'barcodes.txt', 'columnLabels': 'genes.txt'},
                {'path': 'barcodes.txt', 'kind': 'identifiers'}, {'path': 'genes.txt', 'kind': 'identifiers'}]

    def test_exact_bus_passes_and_binds_hashes(self):
        result = self.compare(self.bus_case())
        self.assertTrue(result['equal'])
        self.assertEqual(result['expectedFiles'], result['actualFiles'])
        self.assertEqual(result['numericTolerance'], 0)
        self.assertEqual(result['invariants']['data.bus']['totalCount'], 5)

    def test_bus_changed_count_rejected(self):
        artifacts = self.bus_case()
        (self.b/'data.bus').write_bytes(bus(((1, 2, 0, 4, 0, 0), (2, 3, 1, 2, 0, 0))))
        self.assertFalse(self.compare(artifacts)['equal'])

    def test_bus_changed_barcode_rejected(self):
        artifacts = self.bus_case()
        (self.b/'data.bus').write_bytes(bus(((3, 2, 0, 3, 0, 0), (2, 3, 1, 2, 0, 0))))
        self.assertFalse(self.compare(artifacts)['equal'])

    def test_bus_changed_ec_rejected(self):
        artifacts = self.bus_case()
        (self.b/'data.bus').write_bytes(bus(((1, 2, 9, 3, 0, 0), (2, 3, 1, 2, 0, 0))))
        self.assertFalse(self.compare(artifacts)['equal'])

    def test_bus_reorder_rejected(self):
        artifacts = self.bus_case()
        (self.b/'data.bus').write_bytes(bus(((2, 3, 1, 2, 0, 0), (1, 2, 0, 3, 0, 0))))
        self.assertFalse(self.compare(artifacts)['equal'])

    def test_bus_header_text_change_rejected(self):
        artifacts = self.bus_case()
        (self.b/'data.bus').write_bytes(bus().replace(b'test', b'TEST'))
        self.assertFalse(self.compare(artifacts)['equal'])

    def test_bus_header_lengths_change_rejected(self):
        artifacts = self.bus_case(); data = bytearray(bus()); struct.pack_into('<I', data, 8, 15)
        (self.b/'data.bus').write_bytes(data)
        self.assertFalse(self.compare(artifacts)['equal'])

    def test_bus_truncation_rejected(self):
        artifacts = self.bus_case(); (self.b/'data.bus').write_bytes(bus()[:-1])
        with self.assertRaisesRegex(ValueError, 'truncated'):
            self.compare(artifacts)

    def test_bus_invalid_magic_rejected(self):
        artifacts = self.bus_case(); (self.b/'data.bus').write_bytes(b'BUG\0'+bus()[4:])
        with self.assertRaisesRegex(ValueError, 'magic'):
            self.compare(artifacts)

    def test_bus_invalid_version_rejected(self):
        artifacts = self.bus_case(); data = bytearray(bus()); struct.pack_into('<I', data, 4, 9)
        (self.b/'data.bus').write_bytes(data)
        with self.assertRaisesRegex(ValueError, 'version'):
            self.compare(artifacts)

    def test_bus_zero_count_rejected(self):
        artifacts = self.bus_case(); (self.b/'data.bus').write_bytes(bus(((1, 2, 0, 0, 0, 0), (2, 3, 1, 2, 0, 0))))
        with self.assertRaisesRegex(ValueError, 'count'):
            self.compare(artifacts)

    def test_bus_sort_contract_rejected(self):
        artifacts = self.bus_case(); artifacts[0]['sortedBy'] = ['barcode', 'umi', 'ec', 'flags']
        (self.b/'data.bus').write_bytes(bus(((2, 3, 1, 2, 0, 0), (1, 2, 0, 3, 0, 0))))
        with self.assertRaisesRegex(ValueError, 'sort order'):
            self.compare(artifacts)

    def test_matrix_with_ids_passes(self):
        self.assertTrue(self.compare(self.matrix_case())['equal'])

    def test_matrix_changed_count_rejected(self):
        artifacts = self.matrix_case(); p = self.b/'matrix.mtx'; p.write_bytes(p.read_bytes().replace(b'1 1 3', b'1 1 4'))
        self.assertFalse(self.compare(artifacts)['equal'])

    def test_matrix_small_float_change_rejected(self):
        artifacts = self.matrix_case(); p = self.b/'matrix.mtx'; p.write_bytes(p.read_bytes().replace(b'1 1 3', b'1 1 3.000000001'))
        self.assertFalse(self.compare(artifacts)['equal'])

    def test_matrix_coordinate_reorder_rejected(self):
        artifacts = self.matrix_case(); p = self.b/'matrix.mtx'; p.write_bytes(p.read_bytes().replace(b'1 1 3\n2 2 2', b'2 2 2\n1 1 3'))
        self.assertFalse(self.compare(artifacts)['equal'])

    def test_matrix_barcode_reorder_rejected(self):
        artifacts = self.matrix_case(); (self.b/'barcodes.txt').write_text('TTGGCC\nAACCGG\n')
        self.assertFalse(self.compare(artifacts)['equal'])

    def test_matrix_gene_rename_rejected(self):
        artifacts = self.matrix_case(); (self.b/'genes.txt').write_text('GENE_A\nGENE_C\n')
        self.assertFalse(self.compare(artifacts)['equal'])

    def test_matrix_ids_dimension_mismatch_rejected(self):
        artifacts = self.matrix_case(); (self.b/'genes.txt').write_text('GENE_A\n')
        with self.assertRaisesRegex(ValueError, 'dimension'):
            self.compare(artifacts)

    def test_matrix_duplicate_ids_rejected(self):
        artifacts = self.matrix_case(); (self.b/'genes.txt').write_text('GENE_A\nGENE_A\n')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.compare(artifacts)

    def test_matrix_truncation_rejected(self):
        artifacts = self.matrix_case(); p = self.b/'matrix.mtx'; p.write_bytes(p.read_bytes().rsplit(b'2 2 2\n', 1)[0])
        with self.assertRaisesRegex(ValueError, 'nonzero count'):
            self.compare(artifacts)

    def test_matrix_duplicate_coordinate_rejected(self):
        artifacts = self.matrix_case(); p = self.b/'matrix.mtx'; p.write_bytes(p.read_bytes().replace(b'2 2 2\n', b'1 1 2\n'))
        with self.assertRaises(ValueError):
            self.compare(artifacts)

    def test_matrix_bad_header_rejected(self):
        artifacts = self.matrix_case(); p = self.b/'matrix.mtx'; p.write_bytes(p.read_bytes().replace(b'general', b'symmetric'))
        with self.assertRaisesRegex(ValueError, 'header'):
            self.compare(artifacts)

    def test_path_escape_and_duplicate_plan_rejected(self):
        self.bus_case()
        with self.assertRaises(ValueError):
            self.compare([{'path': '../data.bus', 'kind': 'bus'}])
        artifact = {'path': 'data.bus', 'kind': 'bus'}
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.compare([artifact, artifact])

    def normalized_bus_case(self):
        rows = ((1, 2, 0, 3, 0, 8), (1, 2, 1, 2, 5, 9), (2, 3, 1, 2, 0, 0))
        self.files({'data.bus': bus(rows)})
        return [{'path': 'data.bus', 'kind': 'bus', 'normalizeWithinBarcodeUmi': True}], rows

    def test_declared_within_group_reorder_preserves_full_records(self):
        artifacts, rows = self.normalized_bus_case()
        (self.b/'data.bus').write_bytes(bus((rows[1], rows[0], rows[2])))
        result = self.compare(artifacts)
        self.assertTrue(result['equal'])
        self.assertEqual(result['canonicalFiles'], result['expectedCanonicalFiles'])
        self.assertNotEqual(result['actualFiles'], result['expectedFiles'])

    def test_declared_within_group_reorder_rejects_changed_count(self):
        artifacts, rows = self.normalized_bus_case()
        (self.b/'data.bus').write_bytes(bus((rows[1], (1, 2, 0, 4, 0, 8), rows[2])))
        self.assertFalse(self.compare(artifacts)['equal'])

    def test_declared_within_group_reorder_rejects_changed_flag(self):
        artifacts, rows = self.normalized_bus_case()
        (self.b/'data.bus').write_bytes(bus((rows[1], (1, 2, 0, 3, 1, 8), rows[2])))
        self.assertFalse(self.compare(artifacts)['equal'])

    def test_declared_within_group_reorder_rejects_changed_pad(self):
        artifacts, rows = self.normalized_bus_case()
        (self.b/'data.bus').write_bytes(bus((rows[1], (1, 2, 0, 3, 0, 0), rows[2])))
        self.assertFalse(self.compare(artifacts)['equal'])

    def test_declared_within_group_reorder_rejects_duplicate_loss(self):
        artifacts, rows = self.normalized_bus_case()
        (self.a/'data.bus').write_bytes(bus(rows+(rows[2],)))
        self.assertFalse(self.compare(artifacts)['equal'])

    def test_declared_within_group_reorder_rejects_group_reorder(self):
        artifacts, rows = self.normalized_bus_case()
        (self.b/'data.bus').write_bytes(bus((rows[2], rows[1], rows[0])))
        self.assertFalse(self.compare(artifacts)['equal'])

    def normalized_ec_case(self):
        self.files({'matrix.mtx': '%%MatrixMarket matrix coordinate real general\n%\n1 4 1\n1 1 7\n',
                    'ec.txt': '0\t3,4\n1\t5\n2\t6\n3\t6\n'})
        return [{'path': 'matrix.mtx', 'kind': 'matrix', 'columnLabels': 'ec.txt'},
                {'path': 'ec.txt', 'kind': 'ec', 'normalizeUnusedOrderWith': 'matrix.mtx'}]

    def test_declared_unused_ec_permutation_passes(self):
        artifacts = self.normalized_ec_case()
        (self.b/'ec.txt').write_text('0\t3,4\n1\t6\n2\t5\n3\t6\n')
        result = self.compare(artifacts)
        self.assertTrue(result['equal'])
        self.assertEqual(result['canonicalFiles'], result['expectedCanonicalFiles'])
        self.assertNotEqual(result['actualFiles']['ec.txt'], result['expectedFiles']['ec.txt'])

    def test_declared_unused_ec_permutation_rejects_used_identity_change(self):
        artifacts = self.normalized_ec_case()
        (self.b/'ec.txt').write_text('0\t5\n1\t3,4\n2\t6\n3\t6\n')
        self.assertFalse(self.compare(artifacts)['equal'])

    def test_declared_unused_ec_permutation_rejects_unused_identity_change(self):
        artifacts = self.normalized_ec_case()
        (self.b/'ec.txt').write_text('0\t3,4\n1\t5\n2\t6\n3\t7\n')
        self.assertFalse(self.compare(artifacts)['equal'])

    def test_declared_unused_ec_permutation_rejects_duplicate_change(self):
        artifacts = self.normalized_ec_case()
        (self.b/'ec.txt').write_text('0\t3,4\n1\t5\n2\t5\n3\t6\n')
        self.assertFalse(self.compare(artifacts)['equal'])

    def test_declared_unused_ec_permutation_rejects_matrix_count_change(self):
        artifacts = self.normalized_ec_case(); p = self.b/'matrix.mtx'
        p.write_bytes(p.read_bytes().replace(b'1 1 7', b'1 1 8'))
        self.assertFalse(self.compare(artifacts)['equal'])

    def test_declared_unused_ec_permutation_rejects_missing_or_reordered_ec_id(self):
        artifacts = self.normalized_ec_case()
        (self.b/'ec.txt').write_text('0\t3,4\n2\t6\n1\t5\n3\t6\n')
        with self.assertRaisesRegex(ValueError, 'EC ID'):
            self.compare(artifacts)


if __name__ == '__main__':
    unittest.main()
