"""Reject false parity when both KING outputs violate independent truth."""
import gzip
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


spec = importlib.util.spec_from_file_location("king_compare", Path(__file__).resolve().parents[1] / "validation/king/compare.py")
compare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare)


class KingComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.a = Path(self.temp.name) / "native"
        self.b = Path(self.temp.name) / "wasm"
        self.a.mkdir(); self.b.mkdir()
        self.case = {"id": "duplicate", "contract": "duplicate", "outputs": ["result.con"]}
        self.original = ("FID1 ID1 FID2 ID2 N N_IBS0 N_IBS1 N_IBS2 Concord HomConc HetConc\n"
                         "FAM CHILD1 COPY DUP 8 0 0 8 1.00000 1.00000 1.00000\n")
        self.fixture = {"truth": {"markerCount": 8, "pairStats": {}}, "fam": {"CHILD1": ["FAM", "CHILD1"], "DUP": ["COPY", "DUP"]}}
        self.write_both(self.original)

    def write_both(self, text):
        for directory in (self.a, self.b):
            (directory / "result.con").write_text(text)
            (directory / "stdout.txt").write_text("KING 2.3.2\n")
            (directory / "stderr.txt").write_text("")
            execution = {
                "exitCode": 0, "signal": None,
                "files": {"result.con": compare.sha(text.encode())},
                "fileBytes": {"result.con": len(text.encode())},
                "stdout": compare.sha(b"KING 2.3.2\n"), "stderr": compare.sha(b""),
            }
            (directory / "execution.json").write_text(json.dumps(execution))

    def check(self):
        return compare.compare(self.a, self.b, self.case, self.fixture)

    def test_true_duplicate_binds_raw_bytes(self):
        result = self.check()
        self.assertTrue(result["equal"])
        self.assertEqual(result["actualFiles"], result["expectedFiles"])
        self.assertEqual(result["actualFileBytes"]["result.con"], len(self.original.encode()))
        self.assertTrue(result["checks"]["actual"]["duplicate"]["completeConcordance"])

    def test_identical_wrong_counts_rejected_by_truth(self):
        self.write_both(self.original.replace("8 0 0 8", "7 0 0 7"))
        with self.assertRaisesRegex(ValueError, "duplicate count mismatch"):
            self.check()

    def test_identical_wrong_relationship_rejected_by_truth(self):
        self.write_both(self.original.replace("CHILD1", "CHILD2"))
        with self.assertRaisesRegex(ValueError, "duplicate inventory"):
            self.check()

    def test_duplicate_output_rows_rejected(self):
        self.write_both(self.original + self.original.splitlines()[1] + "\n")
        with self.assertRaisesRegex(ValueError, "duplicate pair"):
            self.check()

    def test_missing_required_column_rejected(self):
        self.write_both(self.original.replace("N_IBS0 ", "").replace("8 0 0 8", "8 0 8"))
        with self.assertRaisesRegex(ValueError, "missing required columns"):
            self.check()

    def test_empty_header_only_ragged_and_nonfinite_rejected(self):
        for text, pattern in [
            ("", "empty"),
            (self.original.splitlines()[0] + "\n", "no records"),
            (self.original.replace("8 0 0 8", "8 0 8"), "ragged"),
            (self.original.replace("1.00000", "NaN", 1), "nonfinite"),
        ]:
            with self.subTest(pattern=pattern):
                self.write_both(text)
                with self.assertRaisesRegex(ValueError, pattern):
                    self.check()

    def test_wrong_family_ids_rejected(self):
        self.write_both(self.original.replace("COPY DUP", "FAM DUP"))
        with self.assertRaisesRegex(ValueError, "family identity"):
            self.check()

    def test_missing_output_and_path_escape_rejected(self):
        (self.b / "result.con").unlink()
        with self.assertRaisesRegex(ValueError, "missing regular file"):
            self.check()
        self.case["outputs"] = ["../result.con"]
        with self.assertRaisesRegex(ValueError, "unsafe"):
            self.check()

    def test_capture_identity_checked_before_semantic_comparison(self):
        self.assertTrue(self.check()["equal"])
        (self.b / "result.con").write_text(self.original.replace("\n", "\r\n"))
        with self.assertRaisesRegex(ValueError, "captured hash mismatch"):
            self.check()

    def test_missing_execution_and_incomplete_capture_rejected(self):
        path = self.b / "execution.json"
        original = path.read_text()
        path.unlink()
        with self.assertRaisesRegex(ValueError, "missing regular file.*execution.json"):
            self.check()
        execution = json.loads(original)
        execution["files"]["unexpected.txt"] = "0" * 64
        path.write_text(json.dumps(execution))
        with self.assertRaisesRegex(ValueError, "captured output inventory"):
            self.check()
        execution = json.loads(original)
        execution["exitCode"] = False
        path.write_text(json.dumps(execution))
        with self.assertRaisesRegex(ValueError, "execution exitCode"):
            self.check()

    def test_captured_stderr_and_byte_counts_verified(self):
        (self.b / "stderr.txt").write_text("uncaptured warning\n")
        with self.assertRaisesRegex(ValueError, "captured stderr hash"):
            self.check()
        self.write_both(self.original)
        path = self.b / "execution.json"
        execution = json.loads(path.read_text())
        execution["fileBytes"]["result.con"] += 1
        path.write_text(json.dumps(execution))
        with self.assertRaisesRegex(ValueError, "captured byte count"):
            self.check()

    def test_independent_kinship_and_mandatory_schema(self):
        fixture = {"truth": {"pairStats": {"CHILD1|DAD": {
            "IDs": ["DAD", "CHILD1"], "N_SNP": 8, "HetHet": 0.25,
            "IBS0": 0, "Kinship": 0.25, "KinshipWithin": 0.25,
        }}}, "fam": {"DAD": ["FAM", "DAD"], "CHILD1": ["FAM", "CHILD1"]}}
        value = b"FID ID1 ID2 N_SNP HetHet IBS0 Kinship\nFAM DAD CHILD1 8 0.2500 0.0000 0.2500\n"
        self.assertEqual(compare.check_pair_table(value, "result.kin", fixture, {"CHILD1|DAD"})["pairs"], 1)
        with self.assertRaisesRegex(ValueError, "truth mismatch"):
            compare.check_pair_table(value.replace(b"0.2500\n", b"0.5000\n"), "result.kin", fixture, {"CHILD1|DAD"})
        with self.assertRaisesRegex(ValueError, "missing required columns"):
            compare.check_pair_table(b"FID ID1 ID2\nFAM DAD CHILD1\n", "result.kin", fixture, {"CHILD1|DAD"})

    def test_pca_sign_is_aligned_but_coordinate_errors_rejected(self):
        first = b"FID IID FA MO SEX AFF PC1 PC2 PC3\nF A 0 0 1 1 0.2 -0.3 0.4\nF B 0 0 2 1 -0.2 0.3 -0.4\n"
        second = b"FID IID FA MO SEX AFF PC1 PC2 PC3\nF A 0 0 1 1 -0.2 -0.3 -0.4\nF B 0 0 2 1 0.2 0.3 0.4\n"
        self.assertEqual(compare.compare_pcs(first, second)["signAlignment"], {"PC1": -1, "PC2": 1, "PC3": -1})
        with self.assertRaisesRegex(ValueError, "PC numeric difference"):
            compare.compare_pcs(first, second.replace(b"-0.2", b"-0.22"))

    def test_compressed_segments_compare_payload_not_gzip_timestamp(self):
        first = gzip.compress(b"A B\n1 2\n", mtime=0)
        second = gzip.compress(b"A B\n1 2\n", mtime=123)
        self.assertNotEqual(first, second)
        self.assertTrue(compare.compare_artifact(first, second, "segments.gz")["decompressed"])
        with self.assertRaisesRegex(ValueError, "artifact differs"):
            compare.compare_artifact(first, gzip.compress(b"A B\n1 3\n"), "segments.gz")

    def test_integer_counts_are_exact(self):
        self.assertFalse(compare.numeric_equal("32809", "32810"))
        self.assertTrue(compare.numeric_equal("0.1234", "0.1235"))
        self.assertFalse(compare.numeric_equal("0.1234", "0.1236"))


if __name__ == "__main__":
    unittest.main()
