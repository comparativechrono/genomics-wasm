"""Counterexamples for transcript quantification differential validation."""
from copy import deepcopy
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("quant_compare", Path(__file__).parents[1] / "validation/quant/compare.py")
compare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compare)


class QuantComparisonTests(unittest.TestCase):
    transcripts = {"tx1": 1000, "tx2": 2000}
    table = "target_id\tlength\teff_length\test_counts\ttpm\ntx1\t1000\t801\t4.25\t1000000\ntx2\t2000\t1801\t0\t0\n"
    metadata = {"n_targets": 2, "n_bootstraps": 0, "n_processed": 8, "n_pseudoaligned": 5,
        "n_unique": 4, "index_version": 13, "k-mer length": 31, "p_pseudoaligned": 62.5,
        "p_unique": 50.0, "kallisto_version": "0.52.0", "start_time": "time", "call": "command"}

    def check(self, table=None, metadata=None, policy=None, left_table=None, left_metadata=None):
        with tempfile.TemporaryDirectory() as directory:
            a, b = Path(directory) / "a", Path(directory) / "b"
            for path, content, meta in [(a, left_table or self.table, left_metadata or self.metadata),
                                        (b, table or self.table, metadata or self.metadata)]:
                path.mkdir()
                (path / "abundance.tsv").write_text(content)
                (path / "run_info.json").write_text(json.dumps(meta))
            return compare.compare_directories(a, b, "kallisto", self.transcripts, 8, policy)

    def policy(self, column="est_counts", absolute="0.00001", relative="0"):
        return {"schema": 1, "fields": {f"kallisto:table:{column}": {
            "absolute": Decimal(absolute), "relative": Decimal(relative),
            "reason": "unit-test allowance", "evidence": "unit-test counterexample"}}}

    def test_identical_outputs_pass(self):
        self.assertTrue(self.check()["equal"])

    def test_row_order_has_no_biological_effect_and_is_reported(self):
        lines = self.table.splitlines()
        result = self.check("\n".join([lines[0], lines[2], lines[1]]) + "\n")
        self.assertTrue(result["equal"])
        self.assertTrue(result["rowOrderChanged"])

    def test_missing_extra_duplicate_or_renamed_transcript_is_invalid(self):
        for table in [self.table.replace("tx2", "tx1"), self.table.replace("tx2", "other"),
                      "\n".join(self.table.splitlines()[:2]) + "\n",
                      self.table + "tx3\t1000\t801\t0\t0\n"]:
            with self.subTest(table=table), self.assertRaises(ValueError):
                self.check(table)

    def test_both_outputs_must_match_independent_fasta_lengths(self):
        for length in ["1001", "1000.0", "0", "-1000"]:
            changed = self.table.replace("tx1\t1000\t", f"tx1\t{length}\t")
            with self.subTest(length=length), self.assertRaises(ValueError):
                self.check(changed, left_table=changed)

    def test_nonfinite_missing_negative_and_underflow_values_are_not_hidden(self):
        for value in ["NaN", "Infinity", "1e9999", "", "-0.00001"]:
            changed = self.table.replace("\t4.25\t", f"\t{value}\t")
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.check(changed, left_table=changed)
        tiny = self.table.replace("\t4.25\t", "\t1e-999\t")
        zero = self.table.replace("\t4.25\t", "\t0\t")
        self.assertFalse(self.check(zero, left_table=tiny)["equal"])

    def test_numeric_mutation_fails_without_explicit_policy(self):
        self.assertFalse(self.check(self.table.replace("4.25", "4.250001"))["equal"])

    def test_measured_delta_is_reported_even_inside_allowance(self):
        result = self.check(self.table.replace("4.25", "4.250001"), policy=self.policy())
        self.assertTrue(result["equal"])
        self.assertEqual(result["numeric"]["table:est_counts"]["changed"], 1)
        self.assertEqual(result["numeric"]["table:est_counts"]["maxAbsoluteDelta"], Decimal("0.000001"))

    def test_large_change_fails_and_tolerance_never_changes_zero_state(self):
        self.assertFalse(self.check(self.table.replace("4.25", "4.251"), policy=self.policy())["equal"])
        self.assertFalse(self.check(self.table.replace("1801\t0\t", "1801\t0.000001\t"), policy=self.policy())["equal"])

    def test_altered_mapping_counter_fails(self):
        meta = deepcopy(self.metadata)
        meta["n_unique"] = 3
        self.assertFalse(self.check(metadata=meta)["equal"])

    def test_wrong_read_accounting_invalid_even_when_both_outputs_agree(self):
        for field, value in [("n_processed", 7), ("n_targets", 3), ("n_pseudoaligned", 9), ("n_unique", 6), ("n_processed", True)]:
            meta = {**self.metadata, field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.check(metadata=meta, left_metadata=meta)

    def test_only_named_volatile_metadata_is_excluded(self):
        meta = {**self.metadata, "start_time": "later", "call": "other path"}
        self.assertTrue(self.check(metadata=meta)["equal"])
        meta["k-mer length"] = 21
        self.assertFalse(self.check(metadata=meta)["equal"])

    def test_unknown_metadata_added_or_changed_is_not_silently_ignored(self):
        self.assertFalse(self.check(metadata={**self.metadata, "new_field": 1})["equal"])
        left = {**self.metadata, "diagnostics": [{"code": "warning", "count": 1}]}
        right = {**self.metadata, "diagnostics": [{"code": "warning", "count": 2}]}
        self.assertFalse(self.check(metadata=right, left_metadata=left)["equal"])

    def test_wrong_version_or_missing_required_metadata_is_invalid(self):
        for meta in [{**self.metadata, "kallisto_version": "0.51.1"},
                     {key: value for key, value in self.metadata.items() if key != "n_processed"}]:
            with self.assertRaises(ValueError):
                self.check(metadata=meta)

    def test_policy_cannot_loosen_exact_fields_or_omit_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            for field in ["kallisto:table:length", "kallisto:metadata:n_processed", "salmon:metadata:em_converged"]:
                path.write_text(json.dumps({"schema": 1, "fields": {field: {
                    "absolute": 1, "relative": 1, "reason": "test", "evidence": "test"}}}))
                with self.subTest(field=field), self.assertRaises(ValueError):
                    compare.load_policy(path)
            path.write_text(json.dumps({"schema": 1, "fields": {"kallisto:table:tpm": {"absolute": 1, "relative": 0}}}))
            with self.assertRaises(ValueError):
                compare.load_policy(path)

    def test_duplicate_json_keys_and_nonfinite_json_are_invalid(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metadata.json"
            for content in ['{"x":1,"x":2}', '{"x": NaN}', '{"x": Infinity}', '{"nested":[{"x":1e9999}]}']:
                path.write_text(content)
                with self.subTest(content=content), self.assertRaises(ValueError):
                    compare.load_json(path)

    def test_fasta_rejects_duplicate_empty_and_invalid_sequences(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "transcripts.fa"
            for content in [">tx1\nACG\n>tx1\nACG\n", ">tx1\n", "ACG\n", ">tx1\nACZ\n"]:
                path.write_text(content)
                with self.subTest(content=content), self.assertRaises(ValueError):
                    compare.load_transcripts(path)

    def test_salmon_schema_checks_full_table_and_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "aux_info").mkdir()
            (root / "quant.sf").write_text("Name\tLength\tEffectiveLength\tTPM\tNumReads\ntx1\t1000\t801.123\t1000000.000000\t5.000\ntx2\t2000\t1801.000\t0.000000\t0.000\n")
            meta = {"salmon_version": "2.8.0", "num_valid_targets": 2, "num_processed": 8,
                "num_mapped": 5, "num_eq_classes": 1, "num_bootstraps": 0, "num_em_iterations": 20,
                "percent_mapped": 62.5, "frag_length_mean": 200.0, "frag_length_sd": 20.0,
                "start_time": "start", "end_time": "end", "total_time_seconds": 1.2, "peak_rss_kb": 30,
                "quant_errors": [], "em_converged": True, "library_types": ["IU"]}
            (root / "aux_info/meta_info.json").write_text(json.dumps(meta))
            library = {**dict.fromkeys(compare.LIBRARY_FORMATS, 0), "IU": 5,
                "read_files": ["reads_1.fastq", "reads_2.fastq"], "expected_format": "IU",
                "compatible_fragment_ratio": 1.0, "num_compatible_fragments": 5,
                "num_incompatible_fragments": 0, "num_assigned_fragments": 5,
                "num_frags_with_concordant_consistent_mappings": 5,
                "num_frags_with_inconsistent_or_orphan_mappings": 0, "strand_mapping_bias": 0.5}
            (root / "lib_format_counts.json").write_text(json.dumps(library))
            self.assertTrue(compare.compare_directories(root, root, "salmon", self.transcripts, 8)["equal"])
            meta["quant_errors"] = ["failed"]
            (root / "aux_info/meta_info.json").write_text(json.dumps(meta))
            with self.assertRaises(ValueError):
                compare.compare_directories(root, root, "salmon", self.transcripts, 8)

    def test_salmon_strand_histogram_and_ratios_are_compared(self):
        library = {"ISR": 17, "ISF": 3, "strand_mapping_bias": Decimal("0.15"), "read_files": ["reads_1.fastq"]}
        for changed in [{**library, "ISR": 18}, {**library, "ISF": 2},
                        {**library, "strand_mapping_bias": Decimal("0.15000000001")},
                        {**library, "read_files": ["different.fastq"]}]:
            result = compare.Comparison("salmon", compare.load_policy())
            result.metadata(library, changed, section="library")
            self.assertFalse(result.report["equal"])

    def test_missing_salmon_strand_count_is_invalid(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lib_format_counts.json"
            path.write_text(json.dumps({"num_assigned_fragments": 5}))
            with self.assertRaises(ValueError):
                compare.load_library_counts(path, {"num_mapped": 5, "library_types": ["IU"]})


if __name__ == "__main__":
    unittest.main()
