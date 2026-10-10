"""Mutations that a native/webR numerical comparison must detect."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("deseq2_compare", Path(__file__).parents[1] / "validation/deseq2/compare.py")
compare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compare)


class DESeq2ComparisonTests(unittest.TestCase):
    def check(self, left, right, policy=None, schema=None):
        schema = schema or ("gene_id", 2, {"effect": "number", "count": "integer", "flag": "boolean"})
        with tempfile.TemporaryDirectory() as directory:
            a, b = Path(directory) / "a.tsv", Path(directory) / "b.tsv"
            a.write_text(left)
            b.write_text(right)
            return compare.compare_tables(a, b, schema, "fixture.tsv", policy or compare.load_policy())

    text = "gene_id\teffect\tcount\tflag\ngene1\t1.2\t4\tTRUE\ngene2\tNA\t0\tFALSE\n"

    def test_identical_tables_pass(self):
        self.assertTrue(self.check(self.text, self.text)["equal"])

    def test_row_order_and_identifiers_are_exact(self):
        changed = "gene_id\teffect\tcount\tflag\ngene2\tNA\t0\tFALSE\ngene1\t1.2\t4\tTRUE\n"
        self.assertFalse(self.check(self.text, changed)["equal"])

    def test_na_is_not_zero(self):
        self.assertFalse(self.check(self.text, self.text.replace("\tNA\t", "\t0\t"))["equal"])

    def test_count_and_boolean_mutations_fail(self):
        for changed in [self.text.replace("\t4\t", "\t5\t"), self.text.replace("TRUE", "FALSE")]:
            self.assertFalse(self.check(self.text, changed)["equal"])

    def test_duplicate_genes_and_nonfinite_values_are_invalid_even_if_identical(self):
        for changed in [self.text.replace("gene2", "gene1"), self.text.replace("1.2", "NaN"), self.text.replace("1.2", "Inf")]:
            with self.assertRaises(ValueError):
                self.check(changed, changed)

    def test_numeric_change_is_reported_under_explicit_tolerance(self):
        policy = {"default": {"absolute": 1e-8, "relative": 0}, "columns": {}}
        changed = self.text.replace("1.2", "1.200000001")
        self.assertFalse(self.check(self.text, changed)["equal"])
        result = self.check(self.text, changed, policy)
        self.assertTrue(result["equal"])
        self.assertEqual(result["numeric"]["effect"]["changed"], 1)
        self.assertGreater(result["numeric"]["effect"]["maxAbsoluteDelta"], 0)

    def test_large_change_fails_even_with_tolerance(self):
        policy = {"default": {"absolute": 1e-8, "relative": 1e-8}, "columns": {}}
        self.assertFalse(self.check(self.text, self.text.replace("1.2", "1.3"), policy)["equal"])

    def test_sign_and_zero_changes_are_never_tolerated(self):
        policy = {"default": {"absolute": 100, "relative": 100}, "columns": {}}
        for changed in ["-1.2", "0"]:
            self.assertFalse(self.check(self.text, self.text.replace("1.2", changed), policy)["equal"])

    def test_small_pvalues_use_relative_error(self):
        schema = ("gene_id", 1, {"pvalue": "number"})
        a = "gene_id\tpvalue\ngene1\t1e-100\n"
        policy = {"default": {"absolute": 0, "relative": 1e-6}, "columns": {}}
        self.assertTrue(self.check(a, a.replace("1e-100", "1.0000001e-100"), policy, schema)["equal"])
        self.assertFalse(self.check(a, a.replace("1e-100", "2e-100"), policy, schema)["equal"])

    def test_tolerance_cannot_change_significance_decisions(self):
        schema = ("gene_id", 1, {"padj": "number"})
        a = "gene_id\tpadj\ngene1\t0.0499999999\n"
        b = "gene_id\tpadj\ngene1\t0.0500000001\n"
        policy = {"default": {"absolute": 1e-8, "relative": 1e-8}, "columns": {}}
        result = self.check(a, b, policy, schema)
        self.assertFalse(result["equal"])
        self.assertEqual(result["differences"][0]["column"], "padj<0.05")

    def test_schema_changes_are_invalid(self):
        for changed in [self.text.replace("effect", "pvalue"), self.text.replace("\t4\t", "\t4.0\t"), self.text.splitlines()[0] + "\n"]:
            with self.assertRaises(ValueError):
                self.check(changed, changed)


if __name__ == "__main__":
    unittest.main()
