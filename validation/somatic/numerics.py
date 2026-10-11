"""Independent Decimal oracle for the LoFreq long-double probability probe.

Only this primitive probe permits a numeric accuracy bound. VCF numeric strings
remain exact. Native x87 and WebAssembly binary128 have different subnormal ranges
and significand precision, so raw subnormals are checked against each format.
"""
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def validate(path):
    value = json.loads(Path(path).read_text())
    bits, exponent = value['mantissaBits'], value['minExponent']
    if type(bits) is not int or bits not in (64, 113) or type(exponent) is not int or exponent != -16381:
        raise ValueError('unexpected long-double format')
    if [r['x'] for r in value['cases']] != ['-1','-100','-1000','-11000','-11356','-11357','-11400','-12000']:
        raise ValueError('missing numerical boundary cases')
    if [(r['a'],r['b']) for r in value['tailCases']] != [('-1','-2'),('-1','-1000'),('-1000','-11000')]:
        raise ValueError('missing intermediate tail-underflow cases')
    signature, errors = [], []
    with localcontext() as ctx:
        ctx.prec = 90; ctx.Emin = -999999; ctx.Emax = 999999
        two = Decimal(2); minimum = two ** (exponent-1)
        maximum = (two - two**(1-bits)) * two**16383
        ln10 = Decimal(10).ln()
        for index, row in enumerate(value['cases'] + value['tailCases']):
            tail = index >= 8
            expected_raw = (Decimal(row['a']).exp()+Decimal(row['b']).exp()) if tail else Decimal(row['x']).exp()
            failed = (tail and row['b'] in ('-1000','-11000')) or (not tail and Decimal(row['x']) < -11355)
            expected = minimum if failed and expected_raw < Decimal(2)**-52 else maximum if failed else expected_raw
            # Tail log-sum arithmetic is binary64 upstream; exp/log10 use long
            # double. A 3e-15 relative bound covers binary64 rounding, while
            # ordinary exponent cases use the stricter 3e-18 bound.
            tolerance = Decimal('3e-15') if tail else Decimal('3e-18')
            probability = Decimal(row['probability']); logarithm = Decimal(row['log10'])
            if not probability.is_finite() or probability <= 0 or not logarithm.is_finite(): raise ValueError('invalid numerical probability')
            error = abs(probability-expected)/expected
            expected_log = expected.ln()/ln10
            if error > tolerance or abs(logarithm-expected_log) > tolerance*max(Decimal(1),abs(expected_log)):
                raise ValueError('probability/log10 exceeds independent Decimal bound')
            if type(row['positive']) is not bool or row['positive'] is not True or type(row['rangeError']) is not bool or row['rangeError'] != failed:
                raise ValueError('incorrect range classification')
            phred = int(-10*expected_log)
            if type(row['phred']) is not int or row['phred'] != phred: raise ValueError('incorrect integer PHRED')
            raw = Decimal(row['rawProbability'])
            # Subnormal rounding uses each actual format's minimum quantum.
            quantum = minimum * two**(1-bits)
            if not raw.is_finite() or raw < 0 or abs(raw-expected_raw) > max(expected_raw*tolerance, quantum):
                raise ValueError('raw probability has lost its long-double range')
            signature.append({'inputs': [row['a'],row['b']] if tail else [row['x']], 'positive':True,'rangeError':failed,'phred':phred})
            errors.append(str(error))
    return signature, {'cases':len(signature),'mantissaBits':bits,'minExponent':exponent,'relativeErrors':errors,'oracle':'Decimal precision90; exp and ln; 3e-18 relative, 3e-15 for binary64 tail sums; subnormals one format quantum'}


def compare_numerics(expected, actual):
    left,lc = validate(expected);right,rc=validate(actual)
    return {'schema':1,'tool':'lofreq','equal':left==right,'expectedSemanticDigest':digest(left),'semanticDigest':digest(right),'biologicalDigest':digest(right),'invariants':{'numerics':rc},'nativeInvariants':{'numerics':lc},'numericPolicy':'Primitive-probe Decimal bounds only; exact flags and integer PHRED. No VCF tolerance.'}
