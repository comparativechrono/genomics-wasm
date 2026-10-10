"""Mutation tests: compare full output, not just alignment/variant counts."""

import io
import json
from decimal import Decimal
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from validation.compare import FormatError, compare_files, compare_sam, compare_vcf, parse_sam, parse_vcf


SAM_HEADER = (
    "@HD\tVN:1.6\tSO:unsorted\n"
    "@SQ\tSN:chr1\tLN:1000\n"
    "@SQ\tSN:chr2\tLN:2000\n"
    "@RG\tID:reads\tSM:sample\n"
    "@PG\tID:bwa\tPN:bwa\tVN:0.7.18\tCL:bwa mem /native/ref.fa reads.fq\n"
)
SAM_READ = "read1\t0\tchr1\t20\t60\t4M\t*\t0\t0\tACGT\tIIII\tNM:i:0\tAS:i:4\tRG:Z:reads\n"
SAM = SAM_HEADER + SAM_READ
VCF_HEADER = (
    "##fileformat=VCFv4.2\n"
    "##fileDate=20261001\n"
    "##commandline=freebayes -f /native/ref.fa sample.bam\n"
    '##source=freeBayes v1.3.8\n'
    '##contig=<ID=chr1,length=1000>\n'
    '##contig=<ID=chr2,length=2000>\n'
    '##INFO=<ID=DP,Number=1,Type=Integer,Description="Total depth">\n'
    '##INFO=<ID=AF,Number=A,Type=Float,Description="Allele frequency">\n'
    '##INFO=<ID=FLAG,Number=0,Type=Flag,Description="Flag">\n'
    '##FILTER=<ID=LowQ,Description="Low quality">\n'
    '##FILTER=<ID=LowDP,Description="Low depth">\n'
    '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
    '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">\n'
    '##FORMAT=<ID=GQ,Number=1,Type=Float,Description="Genotype quality">\n'
    '##FORMAT=<ID=PL,Number=G,Type=Integer,Description="Likelihoods">\n'
    '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tsample\n'
)
VCF_CALL = "chr1\t20\t.\tA\tC\t40.5\tLowQ;LowDP\tDP=12;AF=0.5;FLAG\tGT:DP:GQ\t0/1:12:40\n"
VCF = VCF_HEADER + VCF_CALL


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def compare(self, kind, expected, actual, max_differences=20, qual_absolute_tolerance=0,
                qual_max_magnitude=None):
        left = self.directory / f"expected.{kind}"
        right = self.directory / f"actual.{kind}"
        left.write_text(expected, encoding="utf-8")
        right.write_text(actual, encoding="utf-8")
        return compare_files(kind, left, right, max_differences, qual_absolute_tolerance,
                             qual_max_magnitude)

    def assertDifferent(self, kind, expected, actual):
        report = self.compare(kind, expected, actual)
        self.assertFalse(report["equal"], report)
        self.assertFalse(report["errors"], report)
        self.assertGreater(report["differences_total"], 0)
        return report

    def assertMalformed(self, kind, value):
        # Two identical malformed outputs must never pass the release gate.
        report = self.compare(kind, value, value)
        self.assertFalse(report["equal"], report)
        self.assertEqual({error["side"] for error in report["errors"]}, {"expected", "actual"})

    def test_identical_sam_and_vcf_pass(self):
        self.assertTrue(self.compare("sam", SAM, SAM)["equal"])
        self.assertTrue(self.compare("vcf", VCF, VCF)["equal"])

    def test_sam_each_mandatory_field_is_compared(self):
        mutations = [("read1", "read2"), ("\t0\tchr1", "\t16\tchr1"),
                     ("\tchr1\t20", "\tchr2\t20"), ("\t20\t60", "\t21\t60"),
                     ("\t60\t4M", "\t59\t4M"), ("\t4M\t", "\t2M1I1M\t"),
                     ("\t*\t0\t0\t", "\t=\t0\t0\t"), ("\t*\t0\t0\t", "\t*\t1\t0\t"),
                     ("\t*\t0\t0\t", "\t*\t0\t-1\t"), ("ACGT", "AGGT"), ("IIII", "IIIH")]
        for old, new in mutations:
            with self.subTest(field=old):
                self.assertDifferent("sam", SAM, SAM_HEADER + SAM_READ.replace(old, new))

    def test_sam_tag_values_types_presence_are_compared(self):
        for old, new in [("NM:i:0", "NM:i:1"), ("NM:i:0", "NM:f:0"),
                         ("\tNM:i:0", ""), ("RG:Z:reads", "RG:Z:other")]:
            with self.subTest(change=new):
                self.assertDifferent("sam", SAM, SAM.replace(old, new))

    def test_sam_secondary_and_supplementary_records_are_retained(self):
        for flag in (256, 2048):
            with self.subTest(flag=flag):
                extra = SAM_READ.replace("\t0\tchr1", f"\t{flag}\tchr1")
                report = self.assertDifferent("sam", SAM, SAM + extra)
                self.assertEqual(report["counts"]["extra_records"], 1)

    def test_sam_only_pg_command_line_ignored(self):
        self.assertTrue(self.compare("sam", SAM, SAM.replace("/native/", "/wasm/"))["equal"])
        self.assertTrue(self.compare("sam", SAM, SAM.replace("\tCL:bwa mem /native/ref.fa reads.fq", ""))["equal"])
        self.assertDifferent("sam", SAM, SAM.replace("VN:0.7.18", "VN:0.7.17"))
        self.assertDifferent("sam", SAM, SAM.replace("SM:sample", "SM:other"))
        self.assertDifferent("sam", SAM, SAM.replace("LN:1000", "LN:1001"))
        self.assertDifferent("sam", SAM, SAM_HEADER + "@CO\tnew comment\n" + SAM_READ)

    def test_sam_unordered_fields_and_records_pass(self):
        second = SAM_READ.replace("read1", "read2")
        reordered = SAM_HEADER.replace("SN:chr1\tLN:1000", "LN:1000\tSN:chr1")
        reordered += second + SAM_READ.replace("NM:i:0\tAS:i:4\tRG:Z:reads", "RG:Z:reads\tAS:i:4\tNM:i:0")
        self.assertTrue(self.compare("sam", SAM + second, reordered)["equal"])

    def test_sam_reference_order_is_preserved(self):
        changed = SAM.replace("@SQ\tSN:chr1\tLN:1000\n@SQ\tSN:chr2\tLN:2000", "@SQ\tSN:chr2\tLN:2000\n@SQ\tSN:chr1\tLN:1000")
        self.assertDifferent("sam", SAM, changed)

    def test_sam_record_multiplicity_is_preserved(self):
        report = self.assertDifferent("sam", SAM + SAM_READ, SAM)
        self.assertEqual(report["counts"]["missing_records"], 1)
        # Equal totals still fail if one duplicate replaces a different record.
        self.assertDifferent("sam", SAM + SAM_READ.replace("read1", "read2"), SAM + SAM_READ)

    def test_sam_malformed_input_never_passes(self):
        malformed = ["", "\n", SAM.replace("\t60\t", "\t256\t"), SAM.replace("\t4M\t", "\t3M\t"),
                     SAM.replace("IIII", "III"), SAM.replace("NM:i:0", "NM:i:no"),
                     SAM.replace("NM:i:0", "NM:i:0\tNM:i:0"), SAM.replace("AS:i:4", "AS:x:4"),
                     SAM.replace("SN:chr1\tLN:1000", "SN:chr1\tSN:chr1\tLN:1000"),
                     SAM.replace("LN:1000", "LN:0"), SAM + "@CO\tmisplaced\n",
                     SAM_HEADER + "only\tthree\tcolumns\n", SAM.replace("AS:i:4", "AS:B:i,1,no"),
                     SAM.replace("AS:i:4", "AS:f:nan"), SAM.replace("\tchr1\t20", "\tmissing\t20")]
        for value in malformed:
            with self.subTest(value=value[-70:]):
                self.assertMalformed("sam", value)

    def test_vcf_all_fixed_fields_and_values_are_compared(self):
        mutations = [("chr1\t20", "chr2\t20"), ("\t20\t.", "\t21\t."),
                     ("\t.\tA\t", "\trs1\tA\t"), ("\tA\tC\t", "\tG\tC\t"),
                     ("\tA\tC\t", "\tA\tG\t"), ("40.5", "40.6"),
                     ("LowQ;LowDP", "PASS"), ("DP=12", "DP=13"), ("AF=0.5", "AF=0.6"),
                     (";FLAG", ""), ("0/1:12:40", "1/1:12:40"),
                     ("0/1:12:40", "0/1:13:40"), ("0/1:12:40", "0/1:12:39")]
        for old, new in mutations:
            with self.subTest(field=old):
                self.assertDifferent("vcf", VCF, VCF_HEADER + VCF_CALL.replace(old, new))

    def test_vcf_genotype_phase_and_allele_order_preserved(self):
        for genotype in ("0|1", "1/0", "1|0", "./1", "1"):
            with self.subTest(genotype=genotype):
                self.assertDifferent("vcf", VCF, VCF.replace("0/1:12:40", f"{genotype}:12:40"))

    def test_vcf_raw_complex_alleles_not_normalized(self):
        first = VCF_HEADER + VCF_CALL.replace("\tA\tC\t", "\tAC\tAT\t")
        second = VCF_HEADER + VCF_CALL.replace("\t20\t.\tA\tC\t", "\t21\t.\tC\tT\t")
        self.assertDifferent("vcf", first, second)

    def test_vcf_numeric_format_is_exact(self):
        self.assertDifferent("vcf", VCF, VCF.replace("AF=0.5", "AF=0.500000"))
        self.assertDifferent("vcf", VCF, VCF.replace("\t40.5\t", "\t4.05e1\t"))

    def test_vcf_qual_tolerance_is_explicit_and_default_is_strict(self):
        left = VCF.replace("40.5", "9.69323e-12")
        right = VCF.replace("40.5", "9.69324e-12")
        for tolerance in (0, "0", Decimal(0)):
            report = self.compare("vcf", left, right, qual_absolute_tolerance=tolerance)
            self.assertFalse(report["equal"])
            self.assertEqual(report["tolerated_differences"]["count"], 0)
        report = self.compare("vcf", left, right, qual_absolute_tolerance="1e-15")
        self.assertTrue(report["equal"], report)
        self.assertEqual(Decimal(report["qual_absolute_tolerance"]), Decimal("1e-15"))
        self.assertEqual(report["tolerated_differences"]["count"], 1)
        self.assertEqual(Decimal(report["tolerated_differences"]["max_qual_absolute_delta"]), Decimal("1e-17"))
        self.assertEqual(report["differences_total"], 0)
        self.assertEqual(report["counts"]["missing_records"], 0)
        self.assertEqual(report["counts"]["extra_records"], 0)

    def test_vcf_qual_tolerance_exact_decimal_boundary(self):
        left = VCF.replace("40.5", "9.69323e-12")
        right = VCF.replace("40.5", "9.69324e-12")
        self.assertTrue(self.compare("vcf", left, right, qual_absolute_tolerance="0.00000000000000001")["equal"])
        self.assertFalse(self.compare("vcf", left, right, qual_absolute_tolerance="9.999999999999999999999999999e-18")["equal"])
        precise_left = VCF.replace("40.5", "1.00000000000000000000000000000000000000001")
        precise_right = VCF.replace("40.5", "1.00000000000000000000000000000000000000002")
        report = self.compare("vcf", precise_left, precise_right, qual_absolute_tolerance="1e-41")
        self.assertTrue(report["equal"], report)
        self.assertEqual(Decimal(report["tolerated_differences"]["max_qual_absolute_delta"]), Decimal("1e-41"))

    def test_vcf_qual_tolerance_cannot_hide_other_field_changes(self):
        left = VCF.replace("40.5", "9.69323e-12")
        right = VCF.replace("40.5", "9.69324e-12")
        changes = [("0/1:12:40", "1/1:12:40"), ("DP=12", "DP=13"),
                   ("AF=0.5", "AF=0.500000000001"), ("0/1:12:40", "0/1:12:40.000000000001"),
                   ("\tA\tC\t", "\tA\tG\t"), ("LowQ;LowDP", "PASS")]
        for old, new in changes:
            with self.subTest(field=old):
                report = self.compare("vcf", left, right.replace(old, new), qual_absolute_tolerance="1")
                self.assertFalse(report["equal"], report)
                self.assertEqual(report["tolerated_differences"]["count"], 0)
        report = self.compare("vcf", left, right.replace("freeBayes v1.3.8", "freeBayes v1.3.7"), qual_absolute_tolerance="1e-15")
        self.assertFalse(report["equal"])
        self.assertEqual(report["tolerated_differences"]["count"], 1)

    def test_vcf_qual_tolerance_rejects_missing_nonfinite_and_sign_changes(self):
        left = VCF.replace("40.5", "0")
        for value in (".", "-0", "-0.0", "-1", "NaN", "Inf"):
            with self.subTest(value=value):
                report = self.compare("vcf", left, VCF.replace("40.5", value), qual_absolute_tolerance="100")
                self.assertFalse(report["equal"], report)
                self.assertEqual(report["tolerated_differences"]["count"], 0)
        missing = VCF.replace("40.5", ".")
        self.assertTrue(self.compare("vcf", missing, missing, qual_absolute_tolerance="1e-15")["equal"])

    def test_vcf_qual_tolerance_preserves_multiplicity_and_bounds_reports(self):
        left_call = VCF_CALL.replace("40.5", "9.69323e-12")
        right_call = VCF_CALL.replace("40.5", "9.69324e-12")
        report = self.compare("vcf", VCF_HEADER + left_call * 2, VCF_HEADER + right_call, qual_absolute_tolerance="1e-15")
        self.assertFalse(report["equal"])
        self.assertEqual(report["tolerated_differences"]["count"], 1)
        self.assertEqual(report["counts"]["missing_records"], 1)
        left = VCF_HEADER + "".join(left_call.replace("chr1\t20", f"chr1\t{position}") for position in range(20, 24))
        right = VCF_HEADER + "".join(right_call.replace("chr1\t20", f"chr1\t{position}") for position in range(20, 24))
        report = self.compare("vcf", left, right, max_differences=2, qual_absolute_tolerance="1e-15")
        self.assertTrue(report["equal"])
        self.assertEqual(report["tolerated_differences"]["count"], 4)
        self.assertEqual(len(report["tolerated_differences"]["examples"]), 2)
        self.assertEqual(report["tolerated_differences"]["omitted_examples"], 2)

    def test_vcf_qual_tolerance_matches_duplicate_records_in_order(self):
        left = VCF_HEADER + VCF_CALL.replace("40.5", "0") + VCF_CALL.replace("40.5", "1")
        right = VCF_HEADER + VCF_CALL.replace("40.5", "2") + VCF_CALL.replace("40.5", "1")
        report = self.compare("vcf", left, right, qual_absolute_tolerance="1")
        self.assertTrue(report["equal"], report)
        self.assertEqual(report["tolerated_differences"]["count"], 2)

    def test_invalid_qual_tolerances_are_rejected(self):
        for value in ("-1", "NaN", "Inf", "not-a-number"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.compare("vcf", VCF, VCF, qual_absolute_tolerance=value)
        with self.assertRaises(ValueError):
            self.compare("sam", SAM, SAM, qual_absolute_tolerance="1e-15")

    def test_vcf_qual_magnitude_bound_is_inclusive(self):
        left = VCF.replace("40.5", "9.99e-11")
        right = VCF.replace("40.5", "1e-10")
        for expected, actual in ((left, right), (right, left)):
            report = self.compare("vcf", expected, actual, qual_absolute_tolerance="1e-13",
                                  qual_max_magnitude="1e-10")
            self.assertTrue(report["equal"], report)
            self.assertEqual(Decimal(report["qual_max_magnitude"]), Decimal("1e-10"))
            self.assertEqual(report["tolerated_differences"]["count"], 1)
            self.assertEqual(Decimal(report["tolerated_differences"]["max_qual_absolute_delta"]), Decimal("1e-13"))

    def test_vcf_qual_magnitude_rejects_either_value_above_bound(self):
        left = VCF.replace("40.5", "1e-10")
        right = VCF.replace("40.5", "1.000001e-10")
        for expected, actual in ((left, right), (right, left)):
            report = self.compare("vcf", expected, actual, qual_absolute_tolerance="1e-13",
                                  qual_max_magnitude="1e-10")
            self.assertFalse(report["equal"], report)
            self.assertEqual(report["tolerated_differences"]["count"], 0)
        # Omitting the magnitude option retains the explicitly unbounded policy.
        report = self.compare("vcf", left, right, qual_absolute_tolerance="1e-13")
        self.assertTrue(report["equal"])
        self.assertIsNone(report["qual_max_magnitude"])

    def test_vcf_qual_magnitude_uses_exact_decimal_comparison(self):
        left = VCF.replace("40.5", "1e-10")
        right = VCF.replace("40.5", "1.00000000000000000000000000000000000000001e-10")
        report = self.compare("vcf", left, right, qual_absolute_tolerance="1e-13",
                              qual_max_magnitude="1e-10")
        self.assertFalse(report["equal"], report)
        self.assertEqual(report["tolerated_differences"]["count"], 0)

    def test_vcf_qual_magnitude_does_not_reject_exact_values_above_bound(self):
        report = self.compare("vcf", VCF, VCF, qual_absolute_tolerance="1e-13",
                              qual_max_magnitude="1e-10")
        self.assertTrue(report["equal"])
        self.assertEqual(report["tolerated_differences"]["count"], 0)
        report = self.compare("vcf", VCF, VCF.replace("\t40.5\t", "\t40.50\t"),
                              qual_absolute_tolerance="1e-13", qual_max_magnitude="1e-10")
        self.assertFalse(report["equal"])
        self.assertEqual(report["tolerated_differences"]["count"], 0)

    def test_vcf_magnitude_bound_does_not_enable_tolerance(self):
        left = VCF.replace("40.5", "9.69323e-12")
        right = VCF.replace("40.5", "9.69324e-12")
        report = self.compare("vcf", left, right, qual_max_magnitude="1e-10")
        self.assertFalse(report["equal"])
        self.assertEqual(report["qual_absolute_tolerance"], "0")

    def test_invalid_qual_magnitudes_are_rejected(self):
        for value in ("-1", "NaN", "Inf", "not-a-number"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.compare("vcf", VCF, VCF, qual_max_magnitude=value)
        with self.assertRaises(ValueError):
            self.compare("sam", SAM, SAM, qual_max_magnitude="1e-10")

    def test_vcf_qual_tolerance_cli_and_wrapper(self):
        left = VCF.replace("40.5", "9.69323e-12")
        right = VCF.replace("40.5", "9.69324e-12")
        self.compare("vcf", left, right)
        expected, actual = self.directory / "expected.vcf", self.directory / "actual.vcf"
        self.assertTrue(compare_vcf(expected, actual, qual_absolute_tolerance="1e-15",
                                    qual_max_magnitude="1e-10")["equal"])
        script = Path(__file__).resolve().parents[1] / "validation" / "compare.py"
        result = subprocess.run([sys.executable, str(script), "vcf", str(expected), str(actual),
                                 "--qual-absolute-tolerance", "1e-15", "--qual-max-magnitude", "1e-10"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["tolerated_differences"]["count"], 1)
        self.assertEqual(Decimal(json.loads(result.stdout)["qual_max_magnitude"]), Decimal("1e-10"))

    def test_vcf_only_documented_metadata_ignored(self):
        changed = VCF.replace("20261001", "20261002").replace("/native/", "/wasm/")
        self.assertTrue(self.compare("vcf", VCF, changed)["equal"])
        self.assertDifferent("vcf", VCF, VCF.replace("freeBayes v1.3.8", "freeBayes v1.3.7"))
        self.assertDifferent("vcf", VCF, VCF.replace('Description="Total depth"', 'Description="Changed meaning"'))
        self.assertDifferent("vcf", VCF, VCF.replace("length=1000", "length=1001"))

    def test_vcf_unordered_fields_and_records_pass(self):
        second = VCF_CALL.replace("chr1\t20", "chr1\t30")
        changed = VCF_HEADER.replace("ID=chr1,length=1000", "length=1000,ID=chr1")
        changed += second + VCF_CALL.replace("DP=12;AF=0.5;FLAG", "FLAG;AF=0.5;DP=12").replace("LowQ;LowDP", "LowDP;LowQ").replace("GT:DP:GQ\t0/1:12:40", "GT:GQ:DP\t0/1:40:12")
        self.assertTrue(self.compare("vcf", VCF + second, changed)["equal"])

    def test_vcf_sample_order_is_preserved(self):
        left = VCF_HEADER.replace("\tsample\n", "\tsample1\tsample2\n") + VCF_CALL.rstrip() + "\t0/0:11:41\n"
        right = left.replace("\tsample1\tsample2\n", "\tsample2\tsample1\n").replace("\t0/1:12:40\t0/0:11:41", "\t0/0:11:41\t0/1:12:40")
        self.assertDifferent("vcf", left, right)

    def test_vcf_contig_order_is_preserved(self):
        changed = VCF.replace("##contig=<ID=chr1,length=1000>\n##contig=<ID=chr2,length=2000>", "##contig=<ID=chr2,length=2000>\n##contig=<ID=chr1,length=1000>")
        self.assertDifferent("vcf", VCF, changed)

    def test_vcf_record_multiplicity_is_preserved(self):
        report = self.assertDifferent("vcf", VCF, VCF + VCF_CALL)
        self.assertEqual(report["counts"]["extra_records"], 1)
        self.assertDifferent("vcf", VCF + VCF_CALL.replace("chr1\t20", "chr1\t21"), VCF + VCF_CALL)

    def test_vcf_trailing_missing_sample_values_are_equivalent(self):
        left = VCF.replace("0/1:12:40", "0/1:12:.")
        self.assertTrue(self.compare("vcf", left, left.replace("0/1:12:.", "0/1:12"))["equal"])

    def test_vcf_malformed_input_never_passes(self):
        malformed = ["", VCF_CALL, VCF.replace("##fileformat=VCFv4.2\n", ""),
                     VCF.replace("DP=12", "DP=12;DP=12"), VCF.replace("GT:DP:GQ", "GT:DP:DP"),
                     VCF.replace("GT:DP:GQ\t0/1:12:40", "DP:GT:GQ\t12:0/1:40"),
                     VCF.replace("0/1:12:40", "0/2:12:40"), VCF.replace("0/1:12:40", "0//1:12:40"),
                     VCF.replace("0/1:12:40", "0/1::40"), VCF.replace("0/1:12:40", "0/1:12:40:50"),
                     VCF.replace("\t20\t.\t", "\t0\t.\t"), VCF.replace("40.5", "NaN"),
                     VCF.replace("40.5", "-1"), VCF.replace("DP=12", "DP=twelve"),
                     VCF.replace("AF=0.5", "AF=0.5,0.6"), VCF.replace(";FLAG", ";FLAG=true"),
                     VCF.replace("\tA\tC\t", "\tA\tC,C\t"), VCF.replace("LowQ;LowDP", "PASS;LowQ"),
                     VCF.replace("LowQ;LowDP", "LowQ;LowQ"), VCF.replace("ID=chr1,length", "ID=chr1,ID=chr1,length"),
                     VCF_HEADER + VCF_CALL.replace("\t0/1:12:40", ""),
                     VCF + "##source=late\n", VCF.replace('Description="Depth"', 'Description="Depth'),
                     VCF.replace("##fileDate=20261001", "##fileDate=20261001\n##fileDate=20261001")]
        for value in malformed:
            with self.subTest(value=value[-90:]):
                self.assertMalformed("vcf", value)

    def test_vcf_number_g_depends_on_ploidy(self):
        diploid = VCF.replace("GT:DP:GQ\t0/1:12:40", "GT:PL\t0/1:0,10,20")
        haploid = VCF.replace("GT:DP:GQ\t0/1:12:40", "GT:PL\t1:0,10")
        self.assertTrue(self.compare("vcf", diploid, diploid)["equal"])
        self.assertTrue(self.compare("vcf", haploid, haploid)["equal"])
        self.assertMalformed("vcf", haploid.replace("1:0,10", "1:0,10,20"))

    def test_quoted_metadata_commas_are_not_split(self):
        value = VCF.replace('Description="Depth"', 'Description="Depth, including \\"quoted\\" text"')
        self.assertTrue(self.compare("vcf", value, value)["equal"])

    def test_header_only_output_is_valid(self):
        self.assertTrue(self.compare("sam", SAM_HEADER, SAM_HEADER)["equal"])
        self.assertTrue(self.compare("vcf", VCF_HEADER, VCF_HEADER)["equal"])

    def test_missing_files_fail_with_actionable_error(self):
        report = compare_sam(self.directory / "missing.sam", self.directory / "also-missing.sam")
        self.assertFalse(report["equal"])
        self.assertEqual(len(report["errors"]), 2)

    def test_bounded_reports_keep_complete_counts(self):
        first = SAM_HEADER + "".join(SAM_READ.replace("read1", f"read{i}") for i in range(12))
        report = self.compare("sam", first, SAM_HEADER, max_differences=3)
        self.assertEqual(len(report["differences"]), 3)
        self.assertEqual(report["differences_total"], 12)
        self.assertEqual(report["omitted_differences"], 9)
        self.assertEqual(report["counts"]["missing_records"], 12)

    def test_parser_and_wrapper_api(self):
        self.assertEqual(sum(parse_sam(io.StringIO(SAM)).records.values()), 1)
        self.assertEqual(sum(parse_vcf(io.StringIO(VCF)).records.values()), 1)
        with self.assertRaises(FormatError):
            parse_vcf(io.StringIO("broken"))
        self.compare("vcf", VCF, VCF)
        self.assertTrue(compare_vcf(self.directory / "expected.vcf", self.directory / "actual.vcf")["equal"])
        with self.assertRaises(ValueError):
            compare_files("bam", "a", "b")

    def test_cli_exit_codes_and_report_file(self):
        script = Path(__file__).resolve().parents[1] / "validation" / "compare.py"
        expected = self.directory / "expected.sam"
        actual = self.directory / "actual.sam"
        report = self.directory / "reports" / "comparison.json"
        expected.write_text(SAM, encoding="utf-8")
        for content, code in ((SAM, 0), (SAM.replace("\t60\t4M", "\t59\t4M"), 1), ("bad", 2)):
            with self.subTest(exit=code):
                actual.write_text(content, encoding="utf-8")
                result = subprocess.run([sys.executable, str(script), "sam", str(expected), str(actual), "--report", str(report)], capture_output=True, text=True)
                self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                self.assertEqual(json.loads(result.stdout), json.loads(report.read_text()))


class FastqChecks(unittest.TestCase):
    def test_fastq_validation_and_mate_order(self):
        # The differential runner uses byte equality, then independently checks
        # retained read counts, valid records, and mate pairing/order.
        root = Path(__file__).resolve().parents[1]
        script = r"""
import assert from 'node:assert/strict';
import { fastqRecords, checkPairs } from './validation/additional-cases.mjs';
const read = '@read1/1\nACGT\n+\nIIII\n';
const first = fastqRecords(Buffer.from(read), 'first');
const second = fastqRecords(Buffer.from(read.replace('/1', '/2')), 'second');
assert.equal(first.length, 1);
assert.equal(first[0].sequence, 'ACGT');
assert.equal(first[0].quality, 'IIII');
checkPairs(first, second, 'pair');
assert.deepEqual(fastqRecords(Buffer.alloc(0), 'empty'), []);
for (const changed of [read.slice(0, -1), read.replace('IIII', 'III'), read.replace('@read', 'read'), read.replace('\n+\n', '\n-\n'), read.replace('ACGT', 'ACG1')]) {
  assert.throws(() => fastqRecords(Buffer.from(changed), 'broken'));
}
assert.throws(() => fastqRecords(Buffer.from([255, 10]), 'nonascii'));
assert.throws(() => checkPairs(first, [], 'missing mate'));
assert.throws(() => checkPairs(first, fastqRecords(Buffer.from(read.replace('read1', 'read2')), 'other'), 'wrong mate'));
const two = fastqRecords(Buffer.from(read + read.replace('read1', 'read2')), 'two');
assert.throws(() => checkPairs(two, two.slice().reverse(), 'reordered mates'));
"""
        result = subprocess.run(['node', '--input-type=module', '-e', script], cwd=root,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
