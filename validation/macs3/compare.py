#!/usr/bin/env python3
"""Compare MACS3 scientific outputs without relaxing coordinates, counts or decisions."""
import argparse, hashlib, json, math, re
from pathlib import Path, PurePosixPath
POLICY={'score':{'absolute':1e-5,'relative':1e-6},'hmm':{'absolute':1e-7,'relative':1e-5}}
NORMALISATION=['CRLF to LF.', 'UCSC track display lines omitted; data rows retain original order.', 'VCF fileDate and Program_Args omitted; all other headers and every call field retained.', 'XLS generation timestamp and invocation comments omitted; scientific counts, parameters, headers and rows retained.']
INTEGER=re.compile(r'-?\d+\Z')
FLOAT=re.compile(r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?\Z')
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def safe_file(directory,name):
    p=PurePosixPath(name)
    if not name or p.is_absolute() or str(p)!=name or any(s in ('.','..') for s in p.parts):raise ValueError('unsafe artifact path')
    root=Path(directory).resolve();out=(root/name).resolve(strict=True)
    if not out.is_relative_to(root) or not out.is_file():raise ValueError('artifact escapes output directory')
    return out
def integer(s):
    if not INTEGER.fullmatch(s):raise ValueError('invalid integer: '+s)
    return int(s)
def number(s):
    x=float(s)
    if not math.isfinite(x):raise ValueError('nonfinite scientific value')
    return 0.0 if x == 0 else x
def text(raw):
    s=raw.decode('utf-8').replace('\r\n','\n')
    if any(ord(c)<32 and c not in '\t\n' for c in s):raise ValueError('invalid output control character')
    return s

def model(raw,spec):
    def pairs(items):
        out={}
        for k,v in items:
            if k in out:raise ValueError('duplicate JSON field')
            out[k]=v
        return out
    m=json.loads(text(raw),object_pairs_hook=pairs,parse_constant=lambda s: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
    typ=spec['hmmType'];base={'startprob','transmat','n_features','i_open_region','i_background_region','i_nucleosomal_region','hmm_binsize','hmm_type'}
    required=base|({'means','covars','covariance_type'} if typ=='gaussian' else {'lambdas'})
    if set(m)!=required or m['hmm_type']!=typ:raise ValueError('HMM schema/type mismatch')
    if type(m['n_features']) is not int or m['n_features']!=4 or type(m['hmm_binsize']) is not int or m['hmm_binsize']<=0:raise ValueError('HMM dimensions invalid')
    roles=[m[x] for x in ('i_open_region','i_background_region','i_nucleosomal_region')]
    if any(type(x) is not int for x in roles) or sorted(roles)!=[0,1,2]:raise ValueError('HMM states not a permutation')
    def array(v,shape):
        if not shape:
            if type(v) not in (int,float) or not math.isfinite(v):raise ValueError('invalid HMM parameter')
            return
        if not isinstance(v,list) or len(v)!=shape[0]:raise ValueError('HMM array dimensions invalid')
        for x in v:array(x,shape[1:])
    array(m['startprob'],[3]);array(m['transmat'],[3,3])
    for row in [m['startprob']]+m['transmat']:
        if any(x<0 or x>1 for x in row) or not math.isclose(sum(row),1,abs_tol=1e-8):raise ValueError('HMM probabilities not normalized')
    if typ=='poisson':
        array(m['lambdas'],[3,4])
        if any(v<0 for row in m['lambdas'] for v in row):raise ValueError('negative Poisson rate')
    else:
        array(m['means'],[3,4]);cov=m['covariance_type']
        if cov not in ('diag','full'):raise ValueError('unsupported covariance schema')
        array(m['covars'],[3,4] if cov=='diag' else [3,4,4])
        for c in m['covars']:
            if cov=='diag':
                if any(x<=0 for x in c):raise ValueError('non-positive HMM variance')
            else:
                if any(c[i][i]<=0 for i in range(4)) or any(not math.isclose(c[i][j],c[j][i],abs_tol=1e-9) for i in range(4) for j in range(4)):raise ValueError('invalid covariance matrix')
                # hmmlearn permits singular PSD matrices and adds jitter at decode;
                # reject negative eigenvalues without rejecting that upstream case.
                lower=[[0.0]*4 for _ in range(4)]
                epsilon=max(c[i][i] for i in range(4))*1e-10
                for i in range(4):
                    for j in range(i+1):
                        value=c[i][j]-sum(lower[i][k]*lower[j][k] for k in range(j))
                        if i==j:
                            if value < -epsilon:raise ValueError('HMM covariance not positive semidefinite')
                            lower[i][j]=math.sqrt(max(0.0,value)) if value>epsilon else 0.0
                        elif lower[j][j]:lower[i][j]=value/lower[j][j]
                        elif abs(value)>epsilon:raise ValueError('HMM covariance not positive semidefinite')
    return m,{'kind':'hmm-model','hmmType':typ,'states':3,'features':4},m

def parse(raw,spec):
    kind=spec['kind'];s=text(raw)
    if kind=='hmm-model':return model(raw,spec)
    if kind=='json-exact':
        v=json.loads(s)
        if set(v)!=set(spec['requiredKeys']) or any(not str(x) for x in v.values()):raise ValueError('invalid extracted scientific diagnostic')
        if 'equals' in spec and v!=spec['equals']:raise ValueError('independent expected diagnostic differs')
        return v,{'kind':kind,'fields':len(v)},v
    if kind=='vcf':
        headers=[];rows=[]
        for line in s.splitlines():
            if line.startswith(('##fileDate=','##Program_Args=')):continue
            if line.startswith('#'):headers.append(line);continue
            if not line:continue
            r=line.split('\t')
            if len(r)!=10:raise ValueError('invalid VCF field count')
            r[1]=integer(r[1]);r[5]=integer(r[5])
            if r[1]<=0 or not re.fullmatch('[ACGTN]+',r[3]) or not re.fullmatch('[ACGTN,]+',r[4]):raise ValueError('invalid VCF variant')
            info=[]
            for item in r[7].split(';'):
                k,sep,v=item.partition('=')
                if not sep:raise ValueError('invalid VCF INFO')
                info.append([k,number(v) if k in ('DBIC','AR') or k.startswith('BIC') else v])
            if len(set(k for k,v in info))!=len(info):raise ValueError('duplicate VCF INFO')
            r[7]=info
            if len(r[8].split(':'))!=len(r[9].split(':')) or not re.fullmatch(r'[01][|/][01]',r[9].split(':')[0]):raise ValueError('invalid VCF genotype')
            rows.append(r)
        if not headers or not headers[0].startswith('##fileformat=VCF') or not headers[-1].startswith('#CHROM\tPOS'):raise ValueError('missing VCF headers')
        for v in spec.get('requiredVariants',[]):
            if not any([r[0],r[1],r[3],r[4]]==v for r in rows):raise ValueError('required authentic upstream variant absent')
        value={'headers':headers,'rows':rows};bio=rows
    elif kind=='numeric-text':
        if any(token not in s for token in spec.get('required',[])):raise ValueError('missing numeric text content')
        # Only scientific R vectors are numeric-tolerant; the rest remains exact, including alternatives and plot labels.
        value=[]
        for line in s.splitlines():
            m=re.fullmatch(r'(p|m|ycorr) <- c\((.*)\)',line)
            value.append([m[1],[number(x) for x in m[2].split(',')]] if m else line)
        rows=value;bio=value
    else:
        rows=[];metadata=[]
        widths={'bedgraph':4,'narrowPeak':10,'broadPeak':9,'gappedPeak':15,'bed12':12,'bed5':5,'bed6':6,'bed3':3,'states':4,'numeric-table':6,'integers':1,'xls':spec.get('columns',10)}
        if kind not in widths:raise ValueError('unknown artifact kind '+kind)
        floats={'bedgraph':{3},'narrowPeak':{6,7,8},'broadPeak':{6,7,8},'gappedPeak':{12,13,14},'bed12':set(),'bed5':{4},'bed6':set(),'bed3':set(),'states':set(),'numeric-table':{2,3,4,5},'integers':set(),'xls':({4,5,6,7} if spec.get('columns')==9 else {5,6,7,8})}[kind]
        for line in s.splitlines():
            if not line or line.startswith('track '):continue
            if kind=='xls' and line.startswith('#'):
                if line.startswith(('# This file is generated by','\n# command line:','# command line:','# Command line:')):continue
                metadata.append(line);continue
            r=line.split('\t')
            if kind=='xls' and r[0]=='chr':metadata.append(line);continue
            if len(r)!=widths[kind]:raise ValueError(f'{kind}: invalid field count {len(r)}')
            r=[number(v) if i in floats else integer(v) if (kind=='integers' or i in ({1} if kind=='numeric-table' else {1,2})) else v for i,v in enumerate(r)]
            if kind not in ('integers','numeric-table'):
                if r[1]<0 or r[2]<=r[1]:raise ValueError('invalid genomic interval')
                if kind in ('narrowPeak','broadPeak','gappedPeak','bed12'):
                    integer(r[4])
                    if kind=='narrowPeak':
                        offset=integer(r[9])
                        if offset!=-1 and not 0<=offset<r[2]-r[1]:raise ValueError('invalid summit offset')
                    if kind in ('gappedPeak','bed12'):
                        integer(r[6]);integer(r[7]);count=integer(r[9]);sizes=[integer(x) for x in r[10].rstrip(',').split(',')];starts=[integer(x) for x in r[11].rstrip(',').split(',')]
                        if len(sizes)!=count or len(starts)!=count or any(a<0 or b<=0 or a+b>r[2]-r[1] for a,b in zip(starts,sizes)):raise ValueError('invalid BED blocks')
            if kind=='states' and r[3] not in ('open','nuc','bg'):raise ValueError('invalid HMM state label')
            if spec.get('probability') and not 0<=r[3]<=1:raise ValueError('invalid state posterior probability')
            rows.append(r)
        value={'metadata':metadata,'rows':rows} if kind=='xls' else rows;bio=rows
    if len(rows)<spec.get('minRows',1) or ('rows' in spec and len(rows)!=spec['rows']):raise ValueError(f'{kind}: wrong or vacuous row count {len(rows)}')
    if 'coveredBases' in spec and sum(r[2]-r[1] for r in rows)!=spec['coveredBases']:raise ValueError('aggregate interval length differs')
    if 'minPositiveScores' in spec and sum(r[3]>0 for r in rows) < spec['minPositiveScores']:raise ValueError('too few positive scientific scores')
    if spec.get('positiveScores') and any(r[3]<=0 for r in rows):raise ValueError('expected positive scientific scores')
    if 'integral' in spec and sum((r[2]-r[1])*r[3] for r in rows)!=spec['integral']:raise ValueError('independent fragment coverage integral differs')
    if 'coordinates' in spec and [r[:3] for r in rows]!=spec['coordinates']:raise ValueError('independent expected coordinates differ')
    return value,{'kind':kind,'rows':len(rows)},bio

def equal_values(a,b,policy,stats,path=''):
    if type(a)!=type(b):return False
    if isinstance(a,float):
        stats['fields']+=1;delta=abs(a-b);scale=max(abs(a),abs(b));relative=delta/scale if scale else 0
        stats['maxAbsoluteError']=max(stats['maxAbsoluteError'],delta);stats['maxRelativeError']=max(stats['maxRelativeError'],relative)
        ok=delta<=policy['absolute']+policy['relative']*scale
        if ok and delta:stats['toleratedDifferences']+=1
        return ok
    if isinstance(a,list):return len(a)==len(b) and all(equal_values(x,y,policy,stats,path+f'/{i}') for i,(x,y) in enumerate(zip(a,b)))
    if isinstance(a,dict):return a.keys()==b.keys() and all(equal_values(a[k],b[k],policy,stats,path+'/'+k) for k in a)
    return a==b

def truth(values,spec):
    if not spec.get('truth'):return {}
    t=spec['truth'];operation=t['operation']
    if operation=='wide-p2q':
        # Pinned upstream BedGraph.p2q keeps rank k=1; preserve that behavior.
        expected=[score-math.log10(5000000000) for score in range(12,17)]
        if any(not math.isclose(r[3],v,abs_tol=1e-5) for r,v in zip(values['result.bdg'],expected)):raise ValueError('5-Gbp p2q arithmetic truth failed')
        return {'operation':operation,'aggregateBases':5000000000,'checkedScores':5,'upstreamRank':1}
    edges=[0,100,300,400,600,700,900,1000];x=[0,10,2,20,0,8,0];y=[0,2,2,20,0,16,0]
    expected=[v*t['parameter'] for v in x] if operation=='multiply' else [max(v,t['parameter']) for v in x] if operation=='max' else [(a+b)/2 for a,b in zip(x,y)] if operation=='mean' else None
    if expected is None:return {'operation':operation,'independentTruth':'not claimed; native equivalence and non-vacuous structure required'}
    rows=values['result.bdg']
    if any(r[0]!='chrTest' for r in rows) or any(rows[i][2]!=rows[i+1][1] for i in range(len(rows)-1)):raise ValueError('truth chromosome/coverage topology differs')
    # Tracks may merge adjacent equal segments; expand at original boundaries before checking.
    for i,v in enumerate(expected):
        covering=[r for r in rows if r[0]=='chrTest' and r[1]<=edges[i] and r[2]>=edges[i+1]]
        if len(covering)!=1 or not math.isclose(covering[0][3],v,abs_tol=1e-5):raise ValueError('independent arithmetic truth failed')
    if rows[0][1]!=0 or rows[-1][2]!=1000:raise ValueError('truth coverage differs')
    return {'operation':operation,'checkedSegments':len(expected)}

def compare(expected_dir,actual_dir,spec):
    artifacts=spec['artifacts'];paths=[a['path'] for a in artifacts]
    if not paths or len(paths)!=len(set(paths)):raise ValueError('empty or duplicate artifact plan')
    biology=spec.get('biologicalArtifacts',paths)
    if not biology or any(p not in paths for p in biology) or len(set(biology))!=len(biology):raise ValueError('invalid biological selection')
    values={};invariants={};hashes={};sizes={};bios={};truths={}
    for side,directory in [('expected',expected_dir),('actual',actual_dir)]:
        values[side]={};invariants[side]={};hashes[side]={};sizes[side]={};bios[side]={}
        for a in artifacts:
            raw=safe_file(directory,a['path']).read_bytes();v,inv,bio=parse(raw,a)
            values[side][a['path']]=v;invariants[side][a['path']]=inv;bios[side][a['path']]=bio;hashes[side][a['path']]=hashlib.sha256(raw).hexdigest();sizes[side][a['path']]=len(raw)
        truths[side]=truth(values[side],spec)
    differences=[];numeric={}
    for a in artifacts:
        p=a['path'];stats={'fields':0,'maxAbsoluteError':0,'maxRelativeError':0,'toleratedDifferences':0}
        tolerance={'absolute':0,'relative':0} if a.get('exactFloats') else POLICY['hmm' if a['kind']=='hmm-model' else 'score']
        if not equal_values(values['expected'][p],values['actual'][p],tolerance,stats):differences.append(p)
        if stats['fields']:numeric[p]=stats|{'tolerance':tolerance}
    return {'equal':not differences,'semanticDigest':digest(values['actual']),'expectedSemanticDigest':digest(values['expected']),'biologicalDigest':digest([bios['actual'][p] for p in biology]),'expectedBiologicalDigest':digest([bios['expected'][p] for p in biology]),'nativeInvariants':invariants['expected'],'invariants':invariants['actual'],'truthChecks':truths,'expectedFiles':hashes['expected'],'actualFiles':hashes['actual'],'expectedFileBytes':sizes['expected'],'actualFileBytes':sizes['actual'],'expectedCanonicalFiles':{p:digest(values['expected'][p]) for p in paths},'canonicalFiles':{p:digest(values['actual'][p]) for p in paths},'normalisation':NORMALISATION,'numericTolerance':POLICY,'numericComparisons':numeric,'differences':differences}
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('expected',type=Path);p.add_argument('actual',type=Path);p.add_argument('--dataset',required=True);p.add_argument('--case',required=True);p.add_argument('--actual-target');p.add_argument('--manifest',type=Path,required=True);p.add_argument('--report',type=Path,required=True);a=p.parse_args()
    plan=json.loads((Path(__file__).parent/'cases.json').read_text());spec=next(c for c in plan['cases'] if c['id']==a.case)
    if plan['numericPolicy']!=POLICY:raise ValueError('Numeric policy differs from plan')
    raw=a.manifest.read_bytes();json.loads(raw)['datasets'][a.dataset];r=compare(a.expected,a.actual,spec)
    r.update(schema=1,tool='macs3',dataset=a.dataset,case=a.case,actualTarget=a.actual_target,manifestSha256=hashlib.sha256(raw).hexdigest());a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r));raise SystemExit(0 if r['equal'] else 1)
if __name__=='__main__':main()
