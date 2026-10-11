"""Adversarial structural VCF fixtures, never biological integration evidence.

The SNV/indel fields and audit-header names below follow Strelka 2.9.10's
strelka_streams.cpp, position_somatic_snv_strand_grid_vcf.cpp,
SomaticIndelVcfWriter.cpp and starling_streams_base.cpp.  This deliberately
combined tiny VCF is constructed to exercise the comparator; Strelka itself
writes separate SNV and indel files.  AF is an explicitly labelled structural
extension: the upstream indel AF FORMAT declaration is disabled by #if 0.
"""
import copy
import gzip
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE = importlib.util.spec_from_file_location(
    'strelka2_compare_tests', ROOT / 'validation/strelka2/compare.py')
compare = importlib.util.module_from_spec(MODULE)
MODULE.loader.exec_module(compare)
WORKFLOW_MODULE = importlib.util.spec_from_file_location(
    'strelka2_workflow_tests', ROOT / 'validation/strelka2/workflow.py')
workflow_compare = importlib.util.module_from_spec(WORKFLOW_MODULE)
WORKFLOW_MODULE.loader.exec_module(workflow_compare)

HEADER = '''##fileformat=VCFv4.1
##fileDate=20261011
##source=strelka
##source_version=2.9.10
##startTime=Sun Oct 11 03:04:05 2026
##cmdline=strelka2 --ref reference.fa --normal-bam normal.bam --tumor-bam tumor.bam --somatic-snv-scoring-model-file models/somaticSNVScoringModels.json --somatic-indel-scoring-model-file models/somaticIndelScoringModels.json --somatic-snv-file out.vcf
##reference=file://reference.fa
##contig=<ID=chr1,length=20>
##content=structural comparator fixture, not native biological output
##priorSomaticSnvRate=1e-06
##priorSomaticIndelRate=1e-06
##snv_scoring_features=SomaticSNVQualityAndHomRefGermlineGenotype,NormalSampleRelativeTotalLocusDepth
##indel_scoring_features=SomaticIndelQualityAndHomRefGermlineGenotype,NormalSampleRelativeTotalLocusDepth
##INFO=<ID=SOMATIC,Number=0,Type=Flag,Description="Somatic mutation">
##INFO=<ID=QSS,Number=1,Type=Integer,Description="Quality score for any somatic snv">
##INFO=<ID=TQSS,Number=1,Type=Integer,Description="Data tier used to compute QSS">
##INFO=<ID=QSS_NT,Number=1,Type=Integer,Description="Quality score reflecting the joint probability of a somatic variant and NT">
##INFO=<ID=TQSS_NT,Number=1,Type=Integer,Description="Data tier used to compute QSS_NT">
##INFO=<ID=QSI,Number=1,Type=Integer,Description="Quality score for any somatic variant">
##INFO=<ID=TQSI,Number=1,Type=Integer,Description="Data tier used to compute QSI">
##INFO=<ID=QSI_NT,Number=1,Type=Integer,Description="Quality score reflecting the joint probability of a somatic variant and NT">
##INFO=<ID=TQSI_NT,Number=1,Type=Integer,Description="Data tier used to compute QSI_NT">
##INFO=<ID=NT,Number=1,Type=String,Description="Genotype of the normal in all data tiers">
##INFO=<ID=SGT,Number=1,Type=String,Description="Most likely somatic genotype excluding normal noise states">
##INFO=<ID=DP,Number=1,Type=Integer,Description="Combined depth across samples">
##INFO=<ID=MQ,Number=1,Type=Float,Description="RMS Mapping Quality">
##INFO=<ID=MQ0,Number=1,Type=Integer,Description="Total Mapping Quality Zero Reads">
##INFO=<ID=ReadPosRankSum,Number=1,Type=Float,Description="Z-score from Wilcoxon rank sum test of Alt Vs. Ref read-position in the tumor">
##INFO=<ID=SNVSB,Number=1,Type=Float,Description="Somatic SNV site strand bias">
##INFO=<ID=SomaticEVS,Number=1,Type=Float,Description="Somatic Empirical Variant Score (EVS)">
##INFO=<ID=EVSF,Number=.,Type=Float,Description="Empirical variant scoring features.">
##INFO=<ID=RU,Number=1,Type=String,Description="Smallest repeating sequence unit in inserted or deleted sequence">
##INFO=<ID=RC,Number=1,Type=Integer,Description="Number of times RU repeats in the reference allele">
##INFO=<ID=IC,Number=1,Type=Integer,Description="Number of times RU repeats in the indel allele">
##INFO=<ID=IHP,Number=1,Type=Integer,Description="Largest reference interrupted homopolymer length intersecting with the indel">
##INFO=<ID=OVERLAP,Number=0,Type=Flag,Description="Somatic indel possibly overlaps a second indel.">
##INFO=<ID=AF,Number=A,Type=Float,Description="Structural extension for exact frequency comparison; not asserted upstream output">
##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Read depth for tier1">
##FORMAT=<ID=FDP,Number=1,Type=Integer,Description="Number of basecalls filtered from original read depth for tier1">
##FORMAT=<ID=SDP,Number=1,Type=Integer,Description="Number of reads with deletions spanning this site at tier1">
##FORMAT=<ID=SUBDP,Number=1,Type=Integer,Description="Number of reads below tier1 mapping quality threshold aligned across this site">
##FORMAT=<ID=AU,Number=2,Type=Integer,Description="Number of 'A' alleles used in tiers 1,2">
##FORMAT=<ID=CU,Number=2,Type=Integer,Description="Number of 'C' alleles used in tiers 1,2">
##FORMAT=<ID=GU,Number=2,Type=Integer,Description="Number of 'G' alleles used in tiers 1,2">
##FORMAT=<ID=TU,Number=2,Type=Integer,Description="Number of 'T' alleles used in tiers 1,2">
##FORMAT=<ID=DP2,Number=1,Type=Integer,Description="Read depth for tier2">
##FORMAT=<ID=TAR,Number=2,Type=Integer,Description="Reads strongly supporting alternate allele for tiers 1,2">
##FORMAT=<ID=TIR,Number=2,Type=Integer,Description="Reads strongly supporting indel allele for tiers 1,2">
##FORMAT=<ID=TOR,Number=2,Type=Integer,Description="Other reads (weak support or insufficient indel breakpoint overlap) for tiers 1,2">
##FORMAT=<ID=DP50,Number=1,Type=Float,Description="Average tier1 read depth within 50 bases">
##FORMAT=<ID=FDP50,Number=1,Type=Float,Description="Average tier1 number of basecalls filtered from original read depth within 50 bases">
##FORMAT=<ID=SUBDP50,Number=1,Type=Float,Description="Average number of reads below tier1 mapping quality threshold aligned across sites within 50 bases">
##FORMAT=<ID=BCN50,Number=1,Type=Float,Description="Fraction of filtered reads within 50 bases of the indel.">
##FILTER=<ID=LowEVS,Description="Somatic Empirical Variant Score is below threshold">
##FILTER=<ID=LowDepth,Description="Tumor or normal sample read depth is below threshold">
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tNORMAL\tTUMOR
'''
ROWS = [
    'chr1\t2\t.\tC\tT\t.\tPASS\tSOMATIC;QSS=42;TQSS=1;NT=ref;QSS_NT=40;TQSS_NT=1;SGT=CC->CT;DP=60;MQ=55.25;MQ0=0;ReadPosRankSum=-0.12;SNVSB=0.45;SomaticEVS=18.25;EVSF=40,0.875,-0.12;AF=0.25\tDP:FDP:SDP:SUBDP:AU:CU:GU:TU\t30:1:0:2:1,2:26,28:0,1:2,3\t30:0:0:3:0,1:22,24:0,1:8,10',
    'chr1\t9\t.\tAA\tA\t.\tPASS\tSOMATIC;QSI=35;TQSI=2;NT=ref;QSI_NT=32;TQSI_NT=2;SGT=ref->het;MQ=54.75;MQ0=1;RU=A;RC=4;IC=3;IHP=4;SomaticEVS=15.50;EVSF=32,0.8125,4;AF=0.30\tDP:DP2:TAR:TIR:TOR:DP50:FDP50:SUBDP50:BCN50\t31:34:28,30:1,2:2,2:30.25:1.50:0.25:0.047\t30:33:20,22:8,9:2,2:28.75:1.25:0.50:0.041',
]
VCF = HEADER + '\n'.join(ROWS) + '\n'
CONTEXT = {'tool': 'strelka2', 'references': {'chr1': 'ACGTACGTAAAACGTACGTA'},
           'executableAliases': ['strelka2', 'strelka2.mjs', 'this.program', 'configureStrelkaSomaticWorkflow.py']}
SPEC = {'path': 'out.vcf.gz', 'minSnvs': 1, 'minIndels': 1, 'minSomatic': 2,
        'expectedSamples': ['NORMAL', 'TUMOR']}


def with_row(index, row, rows=ROWS):
    changed = list(rows)
    changed[index] = row
    return HEADER + '\n'.join(changed) + '\n'


def change_info(row_index, tag, value):
    columns = ROWS[row_index].split('\t')
    items = columns[7].split(';')
    for index, item in enumerate(items):
        if item.split('=', 1)[0] == tag:
            items[index] = tag if value is None else f'{tag}={value}'
            break
    else:
        raise AssertionError(f'fixture has no INFO/{tag}')
    columns[7] = ';'.join(items)
    return with_row(row_index, '\t'.join(columns))


def change_sample(row_index, sample, tag, value):
    columns = ROWS[row_index].split('\t')
    index = columns[8].split(':').index(tag)
    values = columns[9 + sample].split(':')
    values[index] = value
    columns[9 + sample] = ':'.join(values)
    return with_row(row_index, '\t'.join(columns))


class Strelka2VCFTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def parse(self, text=VCF, *, spec=None, context=None, name='out.vcf.gz', prefixes=('/work',)):
        path = self.root / name
        raw = text if isinstance(text, bytes) else text.encode()
        if not isinstance(text, bytes) and name.endswith('.gz'):
            raw = gzip.compress(raw)
        path.write_bytes(raw)
        return compare.parse_output(path, CONTEXT if context is None else context,
                                    SPEC if spec is None else spec, prefixes)

    def assert_distinct_or_invalid(self, original, mutated, **kwargs):
        try:
            result = self.parse(mutated, **kwargs)[0]
        except ValueError:
            return
        self.assertNotEqual(original, result)

    def test_structural_fixture_has_nonempty_snv_indel_somatic_reference_checks(self):
        _, counts = self.parse()
        self.assertEqual(counts['records'], 2)
        self.assertEqual(counts['snvRecords'], 1)
        self.assertEqual(counts['indelRecords'], 1)
        self.assertEqual(counts['somaticRecords'], 2)
        self.assertEqual(counts['referenceCheckedRecords'], 2)
        self.assertEqual(counts['samples'], ['NORMAL', 'TUMOR'])

    def test_only_declared_run_clock_executable_and_working_paths_are_normalized(self):
        original, counts = self.parse()
        changed = VCF.replace('20261011', '20261012').replace(
            'Sun Oct 11 03:04:05 2026', 'Mon Oct 12 23:59:58 2026').replace(
            '##cmdline=strelka2 ', '##cmdline=/runtime/strelka2.mjs ').replace(
            'reference.fa', '/work/reference.fa').replace(
            'normal.bam', '/work/normal.bam').replace('tumor.bam', '/work/tumor.bam').replace(
            'models/', '/work/models/').replace('--somatic-snv-file out.vcf', '--somatic-snv-file /work/out.vcf')
        actual, other = self.parse(gzip.compress(changed.replace('\n', '\r\n').encode(), mtime=123))
        self.assertEqual(original, actual)
        self.assertEqual(counts, other)

    def test_clock_provenance_is_required_to_be_well_formed_and_unique(self):
        mutants = [VCF.replace('20261011', '20260230'),
                   VCF.replace('Sun Oct 11 03:04:05 2026', 'not a date'),
                   VCF.replace('Sun Oct 11 03:04:05 2026', 'Sun Oct 32 03:04:05 2026'),
                   VCF.replace('Sun Oct 11 03:04:05 2026', 'Sun Oct 11 25:04:05 2026'),
                   VCF.replace('##startTime=', '##startTime=Sun Oct 11 03:04:05 2026\n##startTime='),
                   VCF.replace('##fileDate=', '##fileDate=20261011\n##fileDate=')]
        for mutated in mutants:
            with self.subTest(header=mutated[:170]), self.assertRaises(ValueError):
                self.parse(mutated)

    def test_native_c_locale_space_padded_calendar_day_is_accepted(self):
        changed = VCF.replace('20261011', '20180105').replace(
            'Sun Oct 11 03:04:05 2026', 'Fri Jan  5 12:40:08 2018')
        self.assertEqual(self.parse()[0], self.parse(changed)[0])

    def test_clock_and_command_header_presence_cannot_be_dropped(self):
        original = self.parse()[0]
        for prefix in ('##fileDate=', '##startTime=', '##cmdline='):
            changed = '\n'.join(line for line in VCF.split('\n') if not line.startswith(prefix))
            with self.subTest(prefix=prefix):
                self.assert_distinct_or_invalid(original, changed)

    def test_generic_commandline_metadata_is_preserved_or_explicitly_rejected(self):
        original = self.parse()[0]
        first = VCF.replace('#CHROM', '##commandline=another-tool --threshold 5\n#CHROM')
        second = first.replace('another-tool --threshold 5', 'another-tool --threshold 6')
        try:
            parsed = self.parse(first)[0]
        except ValueError:
            return
        self.assertNotEqual(original, parsed)
        self.assert_distinct_or_invalid(parsed, second)

    def test_program_aliases_do_not_hide_options_model_identity_or_argument_order(self):
        original = self.parse()[0]
        mutants = [VCF.replace('##cmdline=strelka2 ', '##cmdline=configureStrelkaSomaticWorkflow.py '),
                   VCF.replace('##cmdline=strelka2 ', '##cmdline=different-program '),
                   VCF.replace('--normal-bam normal.bam', '--normal-bam another.bam'),
                   VCF.replace('models/somaticSNVScoringModels.json', 'models/changed-model.json'),
                   VCF.replace('--normal-bam normal.bam --tumor-bam tumor.bam',
                               '--tumor-bam tumor.bam --normal-bam normal.bam'),
                   VCF.replace('--somatic-snv-file out.vcf', '--somatic-snv-file out.vcf --exome'),
                   VCF.replace('##cmdline=strelka2 ', '##cmdline=strelka2 --normal-bam normal.bam ')]
        for mutated in mutants:
            with self.subTest(command=mutated.splitlines()[5]):
                self.assertNotEqual(original, self.parse(mutated)[0])

    def test_unknown_path_prefixes_and_component_collisions_are_not_normalized(self):
        original = self.parse()[0]
        for prefix in ('/elsewhere/', '/workspace/', '/workish/'):
            with self.subTest(prefix=prefix):
                self.assertNotEqual(original, self.parse(VCF.replace('reference.fa', prefix + 'reference.fa'))[0])

    def test_equals_form_option_paths_only_use_declared_working_prefix(self):
        original_text = VCF.replace('--ref reference.fa', '--ref=reference.fa')
        original = self.parse(original_text)[0]
        self.assertEqual(original, self.parse(original_text.replace('--ref=reference.fa', '--ref=/work/reference.fa'))[0])
        self.assertNotEqual(original, self.parse(original_text.replace('--ref=reference.fa', '--ref=/elsewhere/reference.fa'))[0])

    def test_source_version_prior_and_scoring_feature_identity_remain_exact(self):
        original = self.parse()[0]
        mutants = [VCF.replace('##source=strelka', '##source=other'),
                   VCF.replace('##source_version=2.9.10', '##source_version=2.9.11'),
                   VCF.replace('##priorSomaticSnvRate=1e-06', '##priorSomaticSnvRate=2e-06'),
                   VCF.replace('##priorSomaticIndelRate=1e-06', '##priorSomaticIndelRate=2e-06'),
                   VCF.replace('NormalSampleRelativeTotalLocusDepth', 'ChangedFeature')]
        for mutated in mutants:
            with self.subTest(header=mutated[:300]):
                self.assertNotEqual(original, self.parse(mutated)[0])

    def test_quality_somatic_genotype_and_tier_selection_values_remain_exact(self):
        original = self.parse()[0]
        mutations = [(0, 'QSS', '43'), (0, 'QSS_NT', '41'), (0, 'TQSS', '2'),
                     (0, 'TQSS_NT', '2'), (0, 'SGT', 'CC->TT'), (0, 'NT', 'het'),
                     (1, 'QSI', '36'), (1, 'QSI_NT', '33'), (1, 'TQSI', '1'),
                     (1, 'TQSI_NT', '1'), (1, 'SGT', 'ref->hom'), (1, 'NT', 'conflict')]
        for row, tag, value in mutations:
            with self.subTest(row=row, tag=tag):
                self.assertNotEqual(original, self.parse(change_info(row, tag, value))[0])

    def test_exact_floating_strings_and_scoring_feature_arrays_are_preserved(self):
        original = self.parse()[0]
        mutations = [(0, 'SomaticEVS', '18.2500000000000001'), (1, 'SomaticEVS', '15.500'),
                     (0, 'AF', '0.25000000000000001'), (1, 'AF', '3e-1'),
                     (0, 'MQ', '55.250'), (0, 'ReadPosRankSum', '-0.1200000000000001'),
                     (0, 'SNVSB', '0.4500000000000001'), (0, 'EVSF', '40,-0.12,0.875'),
                     (1, 'EVSF', '32,0.81250000000000001,4')]
        for row, tag, value in mutations:
            with self.subTest(row=row, tag=tag, value=value):
                self.assertNotEqual(original, self.parse(change_info(row, tag, value))[0])

    def test_each_normal_and_tumor_nucleotide_tier_count_is_compared(self):
        original = self.parse()[0]
        for sample in (0, 1):
            for tag in ('AU', 'CU', 'GU', 'TU'):
                values = ROWS[0].split('\t')[9 + sample].split(':')
                index = ROWS[0].split('\t')[8].split(':').index(tag)
                pair = values[index].split(',')
                for tier in (0, 1):
                    changed = pair.copy()
                    changed[tier] = str(int(changed[tier]) + 1)
                    with self.subTest(sample=sample, tag=tag, tier=tier):
                        self.assertNotEqual(original, self.parse(change_sample(0, sample, tag, ','.join(changed)))[0])

    def test_indel_support_arrays_depths_and_local_context_are_compared(self):
        original = self.parse()[0]
        for sample in (0, 1):
            for tag, value in [('TAR', '27,31'), ('TIR', '4,5'), ('TOR', '3,4'),
                               ('DP', '35'), ('DP2', '36'), ('DP50', '31.250'),
                               ('FDP50', '1.75'), ('SUBDP50', '0.75'), ('BCN50', '0.049')]:
                with self.subTest(sample=sample, tag=tag):
                    self.assertNotEqual(original, self.parse(change_sample(1, sample, tag, value))[0])
        for tag, value in [('RU', 'AA'), ('RC', '5'), ('IC', '2'), ('IHP', '5')]:
            with self.subTest(tag=tag):
                self.assertNotEqual(original, self.parse(change_info(1, tag, value))[0])

    def test_filter_status_including_missing_pass_and_multiple_filters_is_preserved(self):
        original = self.parse()[0]
        for replacement in ('.', 'LowEVS', 'LowDepth', 'LowEVS;LowDepth'):
            with self.subTest(filter=replacement):
                self.assertNotEqual(original, self.parse(VCF.replace('\tPASS\t', '\t' + replacement + '\t', 1))[0])

    def test_fixed_fields_and_equivalent_indel_placement_are_not_normalized(self):
        original = self.parse()[0]
        mutants = [VCF.replace('chr1\t2\t.\tC\tT', 'chr1\t6\t.\tC\tT'),
                   VCF.replace('chr1\t2\t.\tC\tT', 'chr1\t2\tvariant1\tC\tT'),
                   VCF.replace('chr1\t2\t.\tC\tT', 'chr1\t2\t.\tC\tG'),
                   VCF.replace('chr1\t9\t.\tAA\tA', 'chr1\t10\t.\tAA\tA'),
                   VCF.replace('\tT\t.\tPASS', '\tT\t42.125\tPASS')]
        for mutated in mutants:
            with self.subTest(record=mutated.splitlines()[-2:]):
                self.assertNotEqual(original, self.parse(mutated)[0])

    def test_sample_headers_order_and_values_cannot_be_interchanged(self):
        with self.assertRaises(ValueError):
            self.parse(VCF.replace('\tNORMAL\tTUMOR', '\tTUMOR\tNORMAL'))
        original = self.parse()[0]
        columns = ROWS[0].split('\t')
        columns[9], columns[10] = columns[10], columns[9]
        self.assertNotEqual(original, self.parse(with_row(0, '\t'.join(columns)))[0])
        without_sample_requirement = {key: value for key, value in SPEC.items() if key != 'expectedSamples'}
        self.assertNotEqual(original, self.parse(VCF.replace('\tNORMAL\tTUMOR', '\tTUMOR\tNORMAL'),
                                                spec=without_sample_requirement)[0])

    def test_missing_duplicate_and_extra_records_cannot_compare_equal(self):
        original = self.parse()[0]
        loose_spec = {'path': 'out.vcf.gz', 'expectedSamples': ['NORMAL', 'TUMOR']}
        for mutated in (HEADER + ROWS[0] + '\n', VCF + ROWS[0] + '\n',
                        VCF + ROWS[0].replace('chr1\t2', 'chr1\t6') + '\n'):
            with self.subTest(record_count=len(mutated.splitlines())):
                self.assertNotEqual(original, self.parse(mutated, spec=loose_spec)[0])

    def test_record_info_and_filter_set_order_do_not_change_semantics(self):
        columns = ROWS[0].split('\t')
        columns[7] = ';'.join(reversed(columns[7].split(';')))
        changed = HEADER + ROWS[1] + '\n' + '\t'.join(columns) + '\n'
        self.assertEqual(self.parse()[0], self.parse(changed)[0])
        left = VCF.replace('\tPASS\t', '\tLowEVS;LowDepth\t', 1)
        right = left.replace('\tLowEVS;LowDepth\t', '\tLowDepth;LowEVS\t', 1)
        self.assertEqual(self.parse(left)[0], self.parse(right)[0])

    def test_header_definitions_descriptions_types_and_unknown_metadata_are_retained(self):
        original = self.parse()[0]
        mutants = [VCF.replace('Description="Number of \'A\' alleles used in tiers 1,2"', 'Description="Changed interpretation"'),
                   VCF.replace('ID=QSS,Number=1,Type=Integer', 'ID=QSS,Number=1,Type=Float'),
                   VCF.replace('ID=EVSF,Number=.,Type=Float', 'ID=EVSF,Number=3,Type=Float'),
                   VCF.replace('##content=', '##modelChecksum=abc123\n##content=')]
        for mutated in mutants:
            with self.subTest(header=mutated[:200]):
                self.assert_distinct_or_invalid(original, mutated)

    def test_reference_identity_coordinates_and_alleles_are_independently_checked(self):
        mutants = [VCF.replace('chr1\t2\t.\tC', 'chr1\t2\t.\tA'),
                   VCF.replace('chr1\t9\t.\tAA', 'chr1\t20\t.\tAA'),
                   VCF.replace('chr1\t2', 'chr1\t0'), VCF.replace('chr1\t2', 'chrMissing\t2'),
                   VCF.replace('length=20', 'length=21'), VCF.replace('\tC\tT\t', '\tC\t<DEL>\t')]
        for mutated in mutants:
            with self.subTest(record=mutated.splitlines()[-2:]), self.assertRaises(ValueError):
                self.parse(mutated)

    def test_unknown_info_format_filter_and_duplicate_fields_are_rejected(self):
        mutants = [VCF.replace(';QSS=42', ';UNDECLARED=42'),
                   VCF.replace('DP:FDP:SDP:SUBDP:AU', 'DP:UNDECLARED:SDP:SUBDP:AU'),
                   VCF.replace('\tPASS\t', '\tUNDECLARED\t', 1),
                   VCF.replace(';QSS=42', ';QSS=42;QSS=43'),
                   VCF.replace('DP:FDP:SDP:SUBDP:AU', 'DP:DP:SDP:SUBDP:AU'),
                   VCF.replace('##FORMAT=<ID=AU,', '##FORMAT=<ID=CU,')]
        for mutated in mutants:
            with self.subTest(record=mutated.splitlines()[-2:]), self.assertRaises(ValueError):
                self.parse(mutated)

    def test_malformed_negative_nonfinite_and_short_tier_arrays_are_rejected(self):
        mutants = [change_sample(0, 0, 'AU', '1'), change_sample(0, 0, 'CU', '1,2,3'),
                   change_sample(0, 0, 'GU', '-1,1'), change_sample(0, 0, 'TU', '1,NaN'),
                   change_sample(1, 1, 'TAR', '1,'), change_sample(1, 1, 'TIR', '1.5,2'),
                   change_sample(1, 1, 'TOR', '1,-2'), change_info(0, 'QSS', '-1'),
                   change_info(1, 'QSI', '-1'), change_info(0, 'SomaticEVS', 'NaN'),
                   change_info(1, 'SomaticEVS', 'Infinity'), change_info(0, 'EVSF', '1,nan,2')]
        for mutated in mutants:
            with self.subTest(record=mutated.splitlines()[-2:]), self.assertRaises(ValueError):
                self.parse(mutated)

    def test_tiny_negative_scores_and_out_of_range_frequencies_do_not_underflow_to_valid_zero(self):
        mutants = [change_info(0, 'SomaticEVS', '-1e-10000'),
                   change_info(0, 'MQ', '-1e-10000'),
                   change_info(0, 'AF', '-1e-10000'),
                   change_info(0, 'AF', '1.00000000000000001'),
                   change_sample(1, 0, 'DP50', '-1e-10000')]
        for mutated in mutants:
            with self.subTest(record=mutated.splitlines()[-2:]), self.assertRaises(ValueError):
                self.parse(mutated)

    def test_empty_success_missing_columns_and_misplaced_metadata_are_rejected(self):
        mutants = [HEADER, '', VCF.replace('\tNORMAL\tTUMOR', '\tNORMAL\tNORMAL'),
                   HEADER + '\t'.join(ROWS[0].split('\t')[:-1]) + '\n',
                   VCF + '##startTime=Sun Oct 11 03:04:05 2026\n',
                   VCF + '##cmdline=strelka2 --ref reference.fa\n']
        for mutated in mutants:
            with self.subTest(length=len(mutated)), self.assertRaises(ValueError):
                self.parse(mutated)

    def test_gzip_crc_truncation_wrong_content_and_invalid_utf8_are_rejected(self):
        valid = gzip.compress(VCF.encode())
        damaged = bytearray(valid)
        damaged[-8] ^= 1
        mutants = [valid[:-4], bytes(damaged), VCF.encode(), gzip.compress(VCF.encode() + b'\xff')]
        for mutated in mutants:
            with self.subTest(length=len(mutated)), self.assertRaises((ValueError, OSError, EOFError)):
                self.parse(mutated)

    def test_control_line_separators_cannot_hide_records_or_metadata(self):
        for character in ('\0', '\v', '\f', '\r', '\x1c', '\x1d', '\x1e', '\x85', '\u2028', '\u2029'):
            mutated = VCF.replace('\nchr1\t2', character + 'chr1\t2')
            with self.subTest(character=repr(character)), self.assertRaises(ValueError):
                self.parse(mutated)

    def test_comparison_checks_each_output_and_requires_every_declared_file(self):
        native, wasm = self.root / 'native', self.root / 'wasm'
        native.mkdir()
        wasm.mkdir()
        for directory in (native, wasm):
            (directory / 'snvs.vcf').write_text(VCF)
            (directory / 'indels.vcf').write_text(VCF)
        context = copy.deepcopy(CONTEXT)
        context['vcfs'] = [{**SPEC, 'path': 'snvs.vcf'}, {**SPEC, 'path': 'indels.vcf'}]
        self.assertTrue(compare.compare_directories(native, wasm, context)['equal'])
        (wasm / 'indels.vcf').write_text(change_info(1, 'QSI', '36'))
        self.assertFalse(compare.compare_directories(native, wasm, context)['equal'])
        (wasm / 'indels.vcf').unlink()
        with self.assertRaises(OSError):
            compare.compare_directories(native, wasm, context)


STATS_XML = '''<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<!DOCTYPE boost_serialization>
<boost_serialization signature="serialization::archive" version="12">
<runStatsData>
  <lifeTime><wall>1000000000</wall><user>900000000</user><system>100000000</system></lifeTime>
  <candidateIndels>4</candidateIndels>
  <nonCandidateIndels>7</nonCandidateIndels>
</runStatsData>
</boost_serialization>
'''
STATS_TSV = '''StrelkaRunStatsReport
TotalHours\t0.001h wall, 0.001h user + 0.000h system = 0.001h CPU (100.0%)

CallRegionCandidateIndels\t4
CallRegionNonCandidateIndels\t7
'''


class Strelka2AuxiliaryTests(unittest.TestCase):
    """Structural fixtures from RunStats serialization/report field names."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def parse(self, text, kind, *, spec=None):
        path = self.root / ('output.' + {'stats-xml': 'xml', 'stats-tsv': 'tsv', 'depth': 'tsv', 'bed': 'bed'}[kind])
        path.write_text(text)
        return compare.parse_auxiliary(path, {'kind': kind, **(spec or {})}, CONTEXT)

    def test_xml_normalizes_only_declared_run_times_and_retains_counts(self):
        original, counts = self.parse(STATS_XML, 'stats-xml')
        changed = STATS_XML.replace('1000000000', '5000000000').replace(
            '900000000', '4000000000').replace('100000000</system>', '1000000000</system>')
        actual, other = self.parse(changed, 'stats-xml')
        self.assertEqual(original, actual)
        self.assertEqual(counts, {'candidateIndels': 4, 'nonCandidateIndels': 7})
        self.assertEqual(counts, other)
        for mutated in [STATS_XML.replace('<candidateIndels>4', '<candidateIndels>5'),
                        STATS_XML.replace('<nonCandidateIndels>7', '<nonCandidateIndels>8'),
                        STATS_XML.replace('version="12"', 'version="13"')]:
            with self.subTest(mutated=mutated[-180:]):
                self.assertNotEqual(original, self.parse(mutated, 'stats-xml')[0])

    def test_xml_rejects_missing_reordered_malformed_and_negative_statistics(self):
        mutants = [STATS_XML.replace('<candidateIndels>4</candidateIndels>', ''),
                   STATS_XML.replace('<wall>1000000000</wall>', ''),
                   STATS_XML.replace('<candidateIndels>4</candidateIndels>', '<candidateIndels>4</candidateIndels><candidateIndels>4</candidateIndels>'),
                   STATS_XML.replace('<candidateIndels>4', '<candidateIndels>-1'),
                   STATS_XML.replace('<nonCandidateIndels>7', '<nonCandidateIndels>2.5'),
                   STATS_XML.replace('<wall>1000000000', '<wall>NaN'),
                   STATS_XML.replace('<user>900000000', '<user>-1e-10000'),
                   STATS_XML.replace('<system>100000000', '<system>Infinity'),
                   STATS_XML.replace('</candidateIndels>', '</candidateIndels>unexpected-statistics'),
                   STATS_XML.replace('boost_serialization', 'wrong_root')]
        for mutated in mutants:
            with self.subTest(mutated=mutated[-180:]), self.assertRaises(ValueError):
                self.parse(mutated, 'stats-xml')

    def test_tsv_ignores_measured_hours_but_preserves_both_biological_counters(self):
        original, counts = self.parse(STATS_TSV, 'stats-tsv')
        changed = STATS_TSV.replace('0.001h', '0.100h').replace('0.000h', '0.010h').replace('100.0%', '110.0%')
        self.assertEqual(original, self.parse(changed, 'stats-tsv')[0])
        self.assertEqual(counts, {'CallRegionCandidateIndels': 4, 'CallRegionNonCandidateIndels': 7})
        for mutated in [STATS_TSV.replace('CandidateIndels\t4', 'CandidateIndels\t5'),
                        STATS_TSV.replace('NonCandidateIndels\t7', 'NonCandidateIndels\t8')]:
            self.assertNotEqual(original, self.parse(mutated, 'stats-tsv')[0])

    def test_tsv_allows_native_zero_wall_percentage_nan_but_not_malformed_hour_values(self):
        for percentage in ('nan', '-nan', 'inf', '-inf'):
            with self.subTest(percentage=percentage):
                self.assertEqual(self.parse(STATS_TSV, 'stats-tsv')[0],
                                 self.parse(STATS_TSV.replace('100.0%', percentage + '%'), 'stats-tsv')[0])
        for value in ('.', '1..2', '-1', 'nan', 'inf'):
            with self.subTest(hours=value), self.assertRaises(ValueError):
                self.parse(STATS_TSV.replace('0.001h wall', value + 'h wall'), 'stats-tsv')
        for percentage in ('.', '1..2'):
            with self.subTest(percentage=percentage), self.assertRaises(ValueError):
                self.parse(STATS_TSV.replace('100.0%', percentage + '%'), 'stats-tsv')

    def test_tsv_rejects_missing_duplicate_extra_cells_and_invalid_counters(self):
        mutants = [STATS_TSV.replace('CallRegionCandidateIndels\t4\n', ''),
                   STATS_TSV.replace('CallRegionCandidateIndels\t4', 'CallRegionCandidateIndels\t4\textra'),
                   STATS_TSV.replace('CallRegionCandidateIndels\t4', 'CallRegionCandidateIndels\t-1'),
                   STATS_TSV.replace('CallRegionNonCandidateIndels\t7', 'CallRegionNonCandidateIndels\tNaN'),
                   STATS_TSV + 'CallRegionCandidateIndels\t4\n',
                   STATS_TSV.replace('TotalHours\t', 'TotalHours\t0.0h wall, 0.0h user + 0.0h system = 0.0h CPU (nan%)\nTotalHours\t')]
        for mutated in mutants:
            with self.subTest(mutated=mutated[-180:]), self.assertRaises(ValueError):
                self.parse(mutated, 'stats-tsv')

    def test_depth_values_are_exact_and_contigs_are_validated(self):
        original, counts = self.parse('chr1\t12.50\n', 'depth')
        self.assertEqual(counts, {'contigs': 1, 'positiveContigs': 1})
        for changed in ('chr1\t12.51\n', 'chr1\t12.500\n', 'chr1\t0\n'):
            self.assertNotEqual(original, self.parse(changed, 'depth')[0])
        for malformed in ('', 'chr1\t12\nchr1\t12\n', 'missing\t12\n', 'chr1\t12\textra\n',
                          'chr1\tNaN\n', 'chr1\t-inf\n', 'chr1\t-1e-10000\n'):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                self.parse(malformed, 'depth')

    def test_depth_dictionary_row_order_only_is_normalized(self):
        context = {**CONTEXT, 'references': {**CONTEXT['references'], 'chr2': 'ACGT'}}
        path = self.root / 'depth.tsv'
        path.write_text('chr1\t80.000\nchr2\t43.000\n')
        first = compare.parse_auxiliary(path, {'kind': 'depth'}, context)
        path.write_text('chr2\t43.000\nchr1\t80.000\n')
        self.assertEqual(first, compare.parse_auxiliary(path, {'kind': 'depth'}, context))
        path.write_text('chr2\t80.000\nchr1\t43.000\n')
        self.assertNotEqual(first, compare.parse_auxiliary(path, {'kind': 'depth'}, context))

    def test_bed_keeps_intervals_exact_and_rejects_overlap_bounds_and_empty_required_output(self):
        original, counts = self.parse('chr1\t0\t5\nchr1\t10\t20\n', 'bed', spec={'minRecords': 1})
        self.assertEqual(counts, {'records': 2, 'bases': 15})
        self.assertNotEqual(original, self.parse('chr1\t1\t6\nchr1\t10\t20\n', 'bed')[0])
        for malformed in ('', 'chr1\t0\t5\nchr1\t4\t10\n', 'chr1\t-1\t5\n',
                          'chr1\t5\t5\n', 'chr1\t0\t21\n', 'missing\t0\t5\n', 'chr1\t0\t5\textra\n'):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                self.parse(malformed, 'bed', spec={'minRecords': 1})

    def test_every_declared_auxiliary_output_participates_in_directory_comparison(self):
        native, wasm = self.root / 'native', self.root / 'wasm'
        native.mkdir()
        wasm.mkdir()
        for directory in (native, wasm):
            (directory / 'stats.xml').write_text(STATS_XML)
            (directory / 'stats.tsv').write_text(STATS_TSV)
            (directory / 'depth.tsv').write_text('chr1\t12.5\n')
            (directory / 'callable.bed').write_text('chr1\t0\t10\n')
        context = {**CONTEXT, 'auxiliary': [
            {'path': 'stats.xml', 'kind': 'stats-xml'}, {'path': 'stats.tsv', 'kind': 'stats-tsv'},
            {'path': 'depth.tsv', 'kind': 'depth'}, {'path': 'callable.bed', 'kind': 'bed', 'minRecords': 1}]}
        self.assertTrue(compare.compare_directories(native, wasm, context)['equal'])
        (wasm / 'stats.xml').write_text(STATS_XML.replace('<candidateIndels>4', '<candidateIndels>5'))
        self.assertFalse(compare.compare_directories(native, wasm, context)['equal'])
        (wasm / 'stats.xml').write_text(STATS_XML)
        (wasm / 'callable.bed').unlink()
        with self.assertRaises(OSError):
            compare.compare_directories(native, wasm, context)


LEDGER_HEADER = '#taskLabel\ttaskNamespace\ttaskType\tnCores\tmemMb\tpriority\tisForceLocal\tdependencies\tcwd\tcommand\n'


def command_ledger(commands):
    """Constructed scheduler records, not native execution evidence."""
    return LEDGER_HEADER + ''.join(f'structural_{index}\t\tcommand\t1\t100\t0\t0\t\t\t{command}\n'
                                   for index, command in enumerate(commands))


def workflow_fixture():
    """Small structural hash graph with native, translated and configure nodes."""
    sha = lambda value: hashlib.sha256(value.encode()).hexdigest()
    model = '/work/__strelka2/config/somaticSNVScoringModels.json'
    initial = {'/work/reference.fa': sha('structural reference'), '/work/normal.bam': sha('structural alignment'), model: sha('structural model')}
    current = dict(initial)
    stages, generated = [], set()

    def add(tool, args, reads, writes, *, kind='translated', phase=None, remove=()):
        inputs = dict(current) if reads is None else {path: current[path] for path in reads}
        stage = {'id': f'structural_{len(stages)}', 'kind': kind, 'tool': tool, 'args': args,
                 'inputs': inputs, 'outputs': list(writes), 'outputHashes': writes, 'exitCode': 0, 'missingOutputs': []}
        if phase is not None:
            stage['phase'] = phase
        stages.append(stage)
        current.update(writes)
        generated.update(writes)
        for path in remove:
            del current[path]

    add('htsfile', ['-h', '/work/normal.bam'], None, {}, kind='native', phase='configure')
    depth = '/work/analysis/temp/depth.txt'
    joined = '/work/analysis/temp/joined.txt'
    final = '/work/analysis/workspace/chromDepth.tsv'
    moved = '/work/analysis/workspace/merged.txt'
    add('GetChromDepth', ['--ref', '/work/reference.fa', '--align-file', '/work/normal.bam', '--output', depth, '--chrom', 'chr1'],
        None, {depth: sha('chr1\t12.500\n')}, kind='native', phase='workflow')
    add('cat.py', ['--output', joined, depth], [depth], {joined: current[depth]})
    add('mergeChromDepth.py', ['--out', final, '--in', joined], [joined], {final: sha('chr1\t12.500\n')})
    add('mv', [joined, moved], [joined], {moved: current[joined]}, remove=[joined])
    add('rm', ['-rf', '/work/analysis/temp'], [depth], {}, remove=[depth])
    commands = [
        '/native/package/libexec/GetChromDepth --ref /native/work/reference.fa --align-file /native/work/normal.bam --output /native/work/analysis/temp/depth.txt --chrom chr1',
        '/native/package/libexec/cat.py --output /native/work/analysis/temp/joined.txt /native/work/analysis/temp/depth.txt',
        '/native/package/libexec/mergeChromDepth.py --out /native/work/analysis/workspace/chromDepth.tsv --in /native/work/analysis/temp/joined.txt',
        'mv /native/work/analysis/temp/joined.txt /native/work/analysis/workspace/merged.txt',
        'rm -rf /native/work/analysis/temp',
    ]
    return command_ledger(commands), {'stages': stages, 'models': {model: initial[model]}, 'generatedFiles': sorted(generated)}, initial, {final: current[final], moved: current[moved]}


class Strelka2WorkflowTests(unittest.TestCase):
    def compare(self, ledger, actual, initial=None, retained=None, **kwargs):
        return workflow_compare.compare_workflow(ledger, actual, native_work='/native/work', native_package='/native/package',
                                                  input_hashes=initial, output_hashes=retained, **kwargs)

    def test_observed_commands_and_independent_hash_graph_match(self):
        ledger, actual, initial, retained = workflow_fixture()
        result = self.compare(ledger, actual, initial, retained)
        self.assertTrue(result['equal'])
        self.assertEqual(result['nativeStageCount'], 4)
        self.assertEqual(result['successfulStages'], 6)
        self.assertEqual(result['dependencyEdges'], 10)
        self.assertEqual(result['retainedOutputs'], 2)

    def test_command_multiset_ignores_scheduler_order_and_labels_but_keeps_multiplicity(self):
        ledger, actual, initial, retained = workflow_fixture()
        rows = ledger.splitlines()
        reordered = rows[0] + '\n' + '\n'.join(reversed(rows[1:])) + '\n'
        self.assertTrue(self.compare(reordered, actual, initial, retained)['equal'])
        changed = ledger.replace('--chrom chr1', '--chrom chr2')
        result = self.compare(changed, actual, initial, retained)
        self.assertFalse(result['equal'])
        self.assertEqual(len(result['missingCommands']), 1)
        duplicate = ledger + rows[1].replace('structural_0', 'extra_structural') + '\n'
        self.assertFalse(self.compare(duplicate, actual, initial, retained)['equal'])

    def test_native_vs_patched_native_uses_actual_command_inventory_and_package_paths(self):
        ledger, _, _, _ = workflow_fixture()
        patched = ledger.replace('/native/work', '/patched/work').replace('/native/package', '/patched/package')
        report = self.compare(ledger, {'nativeCommands': patched}, actual_native_work='/patched/work', actual_native_package='/patched/package')
        self.assertTrue(report['equal'])
        self.assertEqual(report['mode'], 'native-command-ledger')
        patched = patched.replace('--chrom chr1', '--chrom chr2')
        self.assertFalse(self.compare(ledger, {'nativeCommands': patched}, actual_native_work='/patched/work', actual_native_package='/patched/package')['equal'])

    def test_native_reheader_redirect_and_temporary_move_are_one_semantic_operation(self):
        command = '/python/bin/python2.7 -E /native/package/libexec/vcfCmdlineSwapper.py "' + \
            '/native/package/bin/configureStrelkaSomaticWorkflow.py --referenceFasta /native/work/reference.fa --runDir /native/work/analysis"' + \
            ' < /native/work/analysis/a.vcf > /native/work/analysis/a.vcf.reheader.tmp && mv /native/work/analysis/a.vcf.reheader.tmp /native/work/analysis/a.vcf'
        stages = workflow_compare.native_stages(command_ledger([command]), '/native/work', '/native/package')
        self.assertEqual(stages, [{'tool': 'vcfCmdlineSwapper.py', 'args': [
            'configureStrelkaSomaticWorkflow.py --referenceFasta /work/reference.fa --runDir /work/analysis', '/work/analysis/a.vcf', '/work/analysis/a.vcf']}])
        with self.assertRaises(ValueError):
            workflow_compare.native_stages(command_ledger([command.replace('.tmp /native/work/analysis/a.vcf', '.tmp /native/work/analysis/wrong.vcf')]), '/native/work', '/native/package')

    def test_native_model_paths_normalize_only_known_package_config_root(self):
        command = '/native/package/libexec/strelka2 --somatic-snv-scoring-model-file /native/package/share/config/model.json --region chr1:1-20'
        stages = workflow_compare.native_stages(command_ledger([command]), '/native/work', '/native/package')
        self.assertEqual(stages[0]['args'], ['--somatic-snv-scoring-model-file', '/work/__strelka2/config/model.json', '--region', 'chr1:1-20'])
        changed = workflow_compare.native_stages(command_ledger([command.replace('/native/package/share/config/', '/other/config/')]), '/native/work', '/native/package')
        self.assertNotEqual(stages, changed)

    def test_initial_intermediate_unknown_and_missing_input_edges_are_rejected(self):
        ledger, actual, initial, retained = workflow_fixture()
        mutations = [lambda w: w['stages'][0]['inputs'].update({'/work/normal.bam': '0' * 64}),
                     lambda w: w['stages'][2]['inputs'].update({'/work/analysis/temp/depth.txt': '1' * 64}),
                     lambda w: w['stages'][0]['inputs'].update({'/work/unknown': '2' * 64}),
                     lambda w: w['stages'][0]['inputs'].pop('/work/normal.bam')]
        for mutation in mutations:
            changed = copy.deepcopy(actual)
            mutation(changed)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.compare(ledger, changed, initial, retained)

    def test_models_cannot_self_attest_and_move_cannot_change_bytes(self):
        ledger, actual, initial, retained = workflow_fixture()
        changed = copy.deepcopy(actual)
        changed['models'][next(iter(changed['models']))] = '0' * 64
        with self.assertRaises(ValueError):
            self.compare(ledger, changed, initial, retained)
        changed = copy.deepcopy(actual)
        changed['stages'][4]['outputHashes']['/work/analysis/workspace/merged.txt'] = '1' * 64
        with self.assertRaises(ValueError):
            self.compare(ledger, changed, initial, retained)

    def test_retained_output_bytes_missing_outputs_and_generated_inventory_are_checked(self):
        ledger, actual, initial, retained = workflow_fixture()
        for changed in ({**retained, '/work/analysis/workspace/chromDepth.tsv': '0' * 64}, {},
                        {**retained, '/work/unknown': '1' * 64}):
            with self.subTest(retained=changed), self.assertRaises(ValueError):
                self.compare(ledger, actual, initial, changed)
        changed = copy.deepcopy(actual)
        changed['generatedFiles'].pop()
        with self.assertRaises(ValueError):
            self.compare(ledger, changed, initial, retained)

    def test_failed_duplicate_missing_and_disguised_configure_stages_fail(self):
        ledger, actual, initial, retained = workflow_fixture()
        mutations = [lambda w: w['stages'][0].update(exitCode=1),
                     lambda w: w['stages'][0].update(exitCode=False),
                     lambda w: w['stages'][0].update(missingOutputs=['/work/missing']),
                     lambda w: w['stages'][1].update(id=w['stages'][0]['id']),
                     lambda w: w['stages'][1].update(phase='configure')]
        for mutation in mutations:
            changed = copy.deepcopy(actual)
            mutation(changed)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.compare(ledger, changed, initial, retained)

    def test_unknown_shell_operators_duplicate_tasks_and_empty_native_inventory_fail(self):
        for ledger in (command_ledger(['echo one | cat']), command_ledger(['echo one; echo two']),
                       LEDGER_HEADER, command_ledger(['mkdir -p /native/work/analysis']),
                       command_ledger(['GetChromDepth']) + 'structural_0\t\tcommand\t1\t100\t0\t0\t\t\tGetChromDepth\n'):
            with self.subTest(ledger=ledger[-100:]), self.assertRaises(ValueError):
                workflow_compare.native_stages(ledger, '/native/work', '/native/package')


if __name__ == '__main__':
    unittest.main()
