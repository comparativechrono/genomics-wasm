#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Regression-generation sections adapt the original PLINK2 test recipes at
# revision 72119068234ce0acf7189e53dfe67a527bc53e2c; see dataset provenance.
"""Verify/fetch/reproduce frozen authentic and synthetic PLINK2 test inputs.

Default verification is offline. Regeneration never changes expected identities
unless --update-manifest is explicitly requested. This creates inputs, not
scientific result goldens. Original upstream scripts are retained for provenance.
"""
from __future__ import annotations

import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'datasets/plink2'
CACHE = ROOT / 'build/plink2-sources'
SCRIPT = 'scripts/fetch-plink2-test-data.py'
LOCK = DATA / 'provenance/sources.lock.json'


def identity(path):
    raw = Path(path).read_bytes()
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def matches(path, record):
    return Path(path).is_file() and identity(path) == {k: record[k] for k in ('bytes', 'sha256')}


def json_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def checked_sources(do_fetch=False):
    lock = json.loads(LOCK.read_text())
    available = {}
    for rec in lock['sources']:
        path = ROOT / rec['cachePath']
        if not path.resolve().is_relative_to(CACHE.resolve()):
            raise RuntimeError('Unsafe source cache path')
        if not matches(path, rec) and do_fetch:
            path.parent.mkdir(parents=True, exist_ok=True)
            partial = path.with_suffix(path.suffix + '.partial')
            try:
                req = urllib.request.Request(rec['url'], headers={'User-Agent': 'genomics-wasm-fixtures/1', 'Accept-Encoding': 'identity'})
                with urllib.request.urlopen(req, timeout=60) as response:
                    raw = response.read(rec['bytes'] + 1)
                partial.write_bytes(raw)
                if not matches(partial, rec):
                    raise RuntimeError('Source identity mismatch: ' + rec['name'])
                partial.replace(path)
            finally:
                partial.unlink(missing_ok=True)
        if not matches(path, rec):
            raise RuntimeError('Missing or altered source: ' + rec['cachePath'] + ' (run --fetch)')
        available[rec['upstreamPath']] = path
    return available


class RandomLCG:
    """The explicit upstream TEST_MULTIALLELIC_PHASED_R generator."""
    def __init__(self, seed):
        self.seed = seed

    def draw(self):
        self.seed = (self.seed * 1103515245 + 12345) % 2147483648
        return self.seed / 2147483648.0


def vcf_header(samples, chromosomes=('1',), dosage=False, phase_dosage=False):
    lines = ['##fileformat=VCFv4.3']
    lines += ['##contig=<ID=' + c + ',length=100000000>' for c in chromosomes]
    lines += ['##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">']
    if dosage:
        lines += ['##FORMAT=<ID=DS,Number=A,Type=Float,Description="Alternate allele dosage">']
    if phase_dosage:
        lines += ['##FORMAT=<ID=HDS,Number=.,Type=Float,Description="Haplotype dosage">']
    lines += ['\t'.join(['#CHROM', 'POS', 'ID', 'REF', 'ALT', 'QUAL', 'FILTER', 'INFO', 'FORMAT', *samples])]
    return lines


def vcf_stats(raw):
    if raw[:2] == b'\x1f\x8b':
        raw = gzip.decompress(raw)
    lines = raw.decode().splitlines()
    header = next(x.split('\t') for x in lines if x.startswith('#CHROM\t'))
    rows = [x.split('\t') for x in lines if x and not x.startswith('#')]
    return {'samples': len(header) - 9, 'variants': len(rows),
            'chromosomes': sorted({x[0] for x in rows}),
            'multiallelicVariants': sum(',' in x[4] for x in rows),
            'missingGenotypes': sum('.' in x.split(':')[0] for r in rows for x in r[9:]),
            'phasedGenotypes': sum('|' in x.split(':')[0] for r in rows for x in r[9:]),
            'formats': dict(sorted(Counter(r[8] for r in rows).items()))}


def generate(output, sources):
    provenance = {'upstreamRevision': json.loads(LOCK.read_text())['revision'], 'files': {}}

    def put(name, raw, origin):
        if isinstance(raw, str):
            raw = raw.encode()
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        provenance['files'][name] = origin

    def copy(src, dest):
        put(dest, sources[src].read_bytes(), {'source': src, 'transformation': 'Unchanged original source bytes.'})

    def text(name, lines, origin):
        put('plink2/' + name, '\n'.join(lines) + '\n', origin)

    real_source = '2.0/Tests/TEST_PHASED_VCF/1kg_phase3_chr21_start.vcf.gz'
    copy(real_source, 'plink2/real/1kg-phase3-chr21.vcf.gz')
    copy('2.0/COPYING', 'provenance/PLINK2-COPYING')
    for source in sources:
        if source.endswith(('.sh', '.py')):
            copy(source, 'provenance/upstream/' + source.removeprefix('2.0/Tests/').replace('/', '__'))
    for name in ('mn.vcf.gz', 'mn_multi.vcf.gz', 'pheno.txt', 'covar.txt', 'sex.txt'):
        copy('2.0/Tests/TEST_GLM_MULTINOMIAL/' + name, 'plink2/synthetic/multinomial/' + name)

    real_lines = gzip.decompress(sources[real_source].read_bytes()).decode().splitlines()
    sample_ids = next(x.split('\t')[9:] for x in real_lines if x.startswith('#CHROM'))
    records = [x.split('\t') for x in real_lines if not x.startswith('#')]
    text('real/keep48.samples.txt', ['#IID', *sample_ids[:48]], {'source': real_source, 'transformation': 'First 48 unchanged sample identifiers, in source order; an explicit bounded test subset.'})
    ranked = sorted(enumerate(records), key=lambda t: (-sum(int(a) for cell in t[1][9:] for a in cell.split('|')), t[0]))[:20]
    text('real/scores.tsv', ['ID\tALLELE\tWEIGHT'] + [f'{r[2]}\t{r[4]}\t{(i % 7 - 3) / 8:.3f}' for i, (_, r) in enumerate(ranked)],
         {'source': real_source, 'transformation': 'Choose 20 variants with greatest ALT allele count, source order breaks ties. Assign arbitrary coefficient ((rank % 7)-3)/8. Synthetic software-test coefficients, not measured effects or a clinical score.'})

    # Byte-preserving extraction of literal fixtures from checksum-verified scripts.
    for test, name, output_name in [('TEST_VCF_MULTIALLELIC_DS_FIELDS', 'multi.vcf', 'multiallelic.vcf'), ('TEST_BCF_HAPLOID_HDS', 'haploid.vcf', 'haploid.vcf')]:
        source = '2.0/Tests/' + test + '/run_tests.sh'
        script = sources[source].read_text()
        match = re.search(r"cat > " + re.escape(name) + r" << 'EOF'\n(.*?)\nEOF", script, re.S)
        if match is None:
            raise RuntimeError('Pinned upstream fixture block not found')
        put('plink2/synthetic/' + output_name, match[1] + '\n', {'source': source, 'transformation': 'Unchanged bytes of the named literal VCF here-document; synthetic upstream regression input.'})
    text('synthetic/haploid.psam', ['#IID\tSEX', 'a\t1', 'b\t1', 'c\t1', 'd\t1'], {'source': '2.0/Tests/TEST_BCF_HAPLOID_HDS/run_tests.sh', 'transformation': 'Exact contents of upstream printf-generated synthetic sex table.'})

    # Original phased-multiallelic generator, including its explicit PRNG.
    rng = RandomLCG(12345)
    samples = ['s' + str(i) for i in range(400)]
    lines = vcf_header(samples, ('1', '2'))
    variants = {1: [('b', 2, (1, 0, 0)), ('m3', 3, (1, 2, 0)), ('m5', 5, (2, 3, 1)), ('m7', 7, (2, 6, 4)), ('m20', 20, (5, 17, 3))], 2: [('b2', 2, (1, 0, 0)), ('s3', 3, (1, 2, 0)), ('s5', 5, (1, 3, 0))]}
    for chrom, shares in [(1, (0.5, 0.25)), (2, (0.7, 0.01))]:
        draws = [rng.draw() for _ in range(800)]
        types = [0 if x < shares[0] else (1 if x < sum(shares) else 2) for x in draws]
        for pos, (vid, allele_ct, mapping) in enumerate(variants[chrom]):
            alts = ','.join('A' * (k + 1) + 'C' for k in range(allele_ct - 1))
            gts = [f'{mapping[types[2*i]]}|{mapping[types[2*i+1]]}' for i in range(400)]
            lines.append('\t'.join([str(chrom), str(10 * (pos + 1)), vid, 'G', alts, '.', '.', '.', 'GT', *gts]))
    text('synthetic/phased-multiallelic.vcf', lines, {'source': '2.0/Tests/TEST_MULTIALLELIC_PHASED_R/run_tests.sh', 'transformation': 'First upstream Python generator reproduced with identical PRNG, sample counts and genotype formulas. Contig length metadata changed from 1000 to 100000000; genotype and variant records unchanged.'})

    # Missingness regression pattern retained; explicit LCG replaces --dummy/awk.
    rng = RandomLCG(3)
    samples = ['s' + str(i) for i in range(603)]
    lines = vcf_header(samples)
    first_genotypes = None
    for v in range(30):
        freq = 0.12 + 0.36 * rng.draw()
        gt = [int(rng.draw() < freq) + int(rng.draw() < freq) for _ in samples]
        if v == 0:
            first_genotypes = gt
        blanks = 0 if v % 2 == 0 else (1, 2, 6, 9, 14)[((v - 1) // 2) % 5]
        cells = ['./.' if (s * 37 + v * 11) % 603 < blanks else ('0/0', '0/1', '1/1')[g] for s, g in enumerate(gt)]
        lines.append('\t'.join(['1', str(1000 * (v + 1)), 'v' + str(v + 1), 'A', 'G', '.', '.', '.', 'GT', *cells]))
    origin = {'sources': ['2.0/Tests/TEST_GLM_LOGISTIC_MISSING/run_tests.sh', '2.0/Tests/TEST_MULTIALLELIC_PHASED_R/run_tests.sh'], 'transformation': 'Synthetic adaptation: retain upstream 603 samples, 30 variants and exact alternating missingness formula. Replace PLINK --dummy and implementation-dependent awk rand with the explicit upstream LCG (seed 3), common-allele frequencies 0.12+0.36*U and two Bernoulli allele draws per genotype.'}
    text('synthetic/glm-missing/input.vcf', lines, origin)
    rng = RandomLCG(7)
    text('synthetic/glm-missing/covariates.tsv', ['#IID\tC1\tC2'] + [f'{s}\t{rng.draw()-0.5:.6f}\t{3*rng.draw():.6f}' for s in samples], {'source': '2.0/Tests/TEST_GLM_LOGISTIC_MISSING/run_tests.sh', 'transformation': 'Upstream two-covariate formulas and seed 7, with explicit LCG replacing awk rand; entirely synthetic.'})
    binary_rng, quant_rng = RandomLCG(11), RandomLCG(19)
    pheno = ['#IID\tBINARY\tQUANT\tFIRTH']
    for i, sample in enumerate(samples):
        pheno.append(f'{sample}\t{2 if binary_rng.draw()<0.4 else 1}\t{4*quant_rng.draw()-2:.6f}\t{2 if first_genotypes[i]>0 else 1}')
    text('synthetic/glm-missing/phenotypes.tsv', pheno, {'source': '2.0/Tests/TEST_GLM_LOGISTIC_MISSING/run_tests.sh', 'transformation': 'BINARY uses upstream 0.4 case probability, LCG seed 11 replacing awk rand. QUANT is independent 4*U-2 (seed19). FIRTH is intentionally 2 for v1 ALT carriers, 1 otherwise to force separation. All labels synthetic; no biological association claim.'})

    # Same biological-case coverage as upstream score-centering regression.
    for dosage in (False, True):
        rng = RandomLCG(17 + int(dosage))
        samples = ['s' + str(i) for i in range(1, 204)]
        lines = vcf_header(samples, ('1', 'Y', 'MT') if dosage else ('1',), dosage=dosage)
        for v in range(1, 41):
            chrom = ('MT' if v > 35 else 'Y') if dosage and v > 30 else '1'
            haploid = chrom != '1'
            freq = 0.004 + 0.002 * (v % 3) if v <= 12 else 0.1 + 0.8 * rng.draw()
            cells = []
            for _ in samples:
                if rng.draw() < 0.08:
                    cell = '.' if haploid else './.'
                    if dosage:
                        cell += ':.'
                else:
                    a, b = int(rng.draw() < freq), int(rng.draw() < freq)
                    d = a if haploid else a + b
                    cell = str(a) if haploid else f'{a}/{b}'
                    if dosage:
                        if rng.draw() < 0.3:
                            d = max(0, min(2-int(haploid), d + (rng.draw()-0.5)*0.6))
                        cell += f':{d:.3f}'
                cells.append(cell)
            lines.append('\t'.join([chrom, str(3000000 + v*10 if haploid else v*1000), 'v'+str(v), 'A', 'G', '.', '.', '.', 'GT:DS' if dosage else 'GT', *cells]))
        folder = 'synthetic/score-' + ('dosage' if dosage else 'hardcall')
        origin = {'source': '2.0/Tests/TEST_SCORE_CENTER_MISSING/run_tests.sh', 'transformation': 'Upstream sample/variant counts, rare/common allele, missingness, dosage and haploid formulas, with explicit LCG replacing implementation-dependent awk rand; same seeds 17/18. Synthetic regression data.'}
        text(folder + '/input.vcf', lines, origin)
        rng = RandomLCG(5)
        score = ['ID\tALLELE\tSCORE1\tSCORE2']
        for v in range(1, 41):
            allele = 'G' if v <= 12 or v % 2 else 'A'
            score.append(f'v{v}\t{allele}\t{2*rng.draw()-1:.4f}\t{4*rng.draw()-2:.4f}')
        text(folder + '/scores.tsv', score, {'source': '2.0/Tests/TEST_SCORE_CENTER_MISSING/run_tests.sh', 'transformation': 'Upstream score formulas and seed 5, LCG replacing awk rand; header added. Synthetic arbitrary coefficients.'})
        text(folder + '/sex.tsv', ['#IID\tSEX'] + [s+'\t1' for s in samples], {'source': '2.0/Tests/TEST_SCORE_CENTER_MISSING/run_tests.sh', 'transformation': 'All synthetic samples assigned male as upstream dosage test requires; supplied in both sets for consistent import.'})

    json_write(output / 'provenance/fixture-sources.json', provenance)
    observations = {}
    for path in sorted((output / 'plink2').rglob('*')):
        if path.name.endswith(('.vcf', '.vcf.gz')):
            observations[str(path.relative_to(output))] = vcf_stats(path.read_bytes())
    return observations


def inventory(folder):
    return {str(p.relative_to(folder)): identity(p) for p in sorted(folder.rglob('*')) if p.is_file() and p.name != 'manifest.json'}


def verify():
    manifest = json.loads((DATA / 'manifest.json').read_text())
    actual = inventory(DATA)
    expected = {p: {k: r[k] for k in ('bytes', 'sha256')} for p, r in manifest['files'].items()}
    if actual != expected:
        bad = sorted(set(actual) ^ set(expected) | {p for p in set(actual) & set(expected) if actual[p] != expected[p]})
        raise RuntimeError('Fixture inventory mismatch: ' + ', '.join(bad))
    if manifest['regeneration']['files'][SCRIPT] != identity(ROOT / SCRIPT):
        raise RuntimeError('Regeneration script identity mismatch')
    if manifest['sources'] != json.loads(LOCK.read_text())['sources']:
        raise RuntimeError('Manifest/source lock mismatch')
    print(f'Verified {len(actual)} frozen PLINK2 fixture/provenance files.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--fetch', action='store_true')
    parser.add_argument('--regenerate', action='store_true')
    parser.add_argument('--update-manifest', action='store_true')
    args = parser.parse_args()
    if args.update_manifest and not args.regenerate:
        parser.error('--update-manifest requires --regenerate')
    available = checked_sources(args.fetch) if args.fetch or args.regenerate else None
    if args.regenerate:
        scratch = ROOT / 'build/plink2-fixture-regeneration'
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as tmp:
            output = Path(tmp)
            observations = generate(output, available)
            for name in ('README.md', 'provenance/sources.lock.json', 'provenance/DATA_REUSE.md'):
                dest = output / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(DATA / name, dest)
            generated = inventory(output)
            if args.update_manifest:
                manifest = {'schema_version': 1, 'datasets': {'plink2': {'species': 'Homo sapiens (authentic input); fictional synthetic regression samples', 'observations': observations}},
                            'files': {p: {**r, 'generated': p not in ('README.md', 'provenance/sources.lock.json', 'provenance/DATA_REUSE.md')} for p, r in generated.items()},
                            'sources': json.loads(LOCK.read_text())['sources'],
                            'regeneration': {'script': SCRIPT, 'files': {SCRIPT: identity(ROOT / SCRIPT)}, 'requirements': ['Python 3.11+'], 'method': 'Unchanged authentic and upstream synthetic copies; documented deterministic synthetic regression-input adaptations. No tool-result goldens.'}}
                for name in generated:
                    dest = DATA / name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(output / name, dest)
                json_write(DATA / 'manifest.json', manifest)
            else:
                expected = json.loads((DATA / 'manifest.json').read_text())
                wanted = {p: {k: r[k] for k in ('bytes', 'sha256')} for p, r in expected['files'].items()}
                if generated != wanted or observations != expected['datasets']['plink2']['observations']:
                    raise RuntimeError('Regeneration differs from frozen manifest')
                print('Regeneration exactly matches all frozen files and observations.')
    verify()


if __name__ == '__main__':
    main()
