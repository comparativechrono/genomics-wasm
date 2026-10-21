#!/usr/bin/env python3
"""Strict semantic comparison of HTS results, with independently stated fixture truths.

Only command provenance headers and SAM @SQ UR paths are canonicalized. Scientific
fields, record order, sample order, genotype phasing/missingness and numeric text
remain exact. BGZF block packing may differ, but decompressed bytes must match.
"""
from __future__ import annotations
import argparse, gzip, hashlib, json, re, struct
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]

def sha(raw):return hashlib.sha256(raw).hexdigest()
def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
def require(condition,message):
    if not condition:raise AssertionError(message)
def info_map(text):
    if text=='.':return {}
    result={}
    for part in text.split(';'):
        key,sep,value=part.partition('=');require(key not in result,'Duplicate INFO key '+key);result[key]=value if sep else True
    return result

def parse_vcf(raw):
    headers=[];records=[];samples=None
    for line in raw.decode('utf-8').splitlines():
        if line.startswith('##'):
            if re.match(r'^##bcftools_[A-Za-z0-9_-]*(?:Version|Command)=',line):continue
            headers.append(line);continue
        if line.startswith('#CHROM\t'):
            require(samples is None,'Multiple VCF column headers');columns=line.split('\t');samples=columns[9:];continue
        if not line:continue
        require(samples is not None,'VCF record before header');cols=line.split('\t')
        require(len(cols)==(9+len(samples) if samples else 8),'Wrong VCF column count')
        require(int(cols[1])>0,'Invalid VCF coordinate')
        record={'chrom':cols[0],'pos':int(cols[1]),'id':cols[2],'ref':cols[3],'alt':cols[4].split(','),'qual':cols[5],'filter':cols[6],'info':info_map(cols[7])}
        if samples:
            fmt=cols[8].split(':');require(len(set(fmt))==len(fmt),'Duplicate FORMAT field')
            record['samples']=[]
            for text in cols[9:]:
                fields=text.split(':');require(len(fields)<=len(fmt),'Extra FORMAT fields')
                fields+=['.']*(len(fmt)-len(fields));record['samples'].append(dict(zip(fmt,fields)))
        records.append(record)
    require(samples is not None,'Missing VCF column header')
    return {'headers':headers,'samples':samples,'records':records}

def parse_sam(raw):
    headers=[];records=[]
    for line in raw.decode('utf-8').splitlines():
        if line.startswith('@'):
            cols=line.split('\t')
            # The path identifies this same reference, not an alignment property.
            if cols[0]=='@SQ':cols=[('UR:'+Path(x[3:]).name) if x.startswith('UR:') else x for x in cols]
            headers.append(cols);continue
        if not line:continue
        cols=line.split('\t');require(len(cols)>=11,'Malformed SAM record')
        records.append(cols[:11]+sorted(cols[11:]))
    require(headers,'Missing SAM header')
    return {'headers':headers,'records':records}

def bgzf(raw):
    offsets=[];pos=0;uncompressed=bytearray()
    while pos<len(raw):
        require(raw[pos:pos+4]==b'\x1f\x8b\x08\x04','Not a BGZF gzip member')
        require(pos+18<=len(raw),'Truncated BGZF header');xlen=struct.unpack_from('<H',raw,pos+10)[0];extra=raw[pos+12:pos+12+xlen];i=0;size=None
        while i+4<=len(extra):
            key=extra[i:i+2];n=struct.unpack_from('<H',extra,i+2)[0];require(i+4+n<=len(extra),'Invalid gzip extra field')
            if key==b'BC':require(n==2,'Invalid BGZF BC field');size=struct.unpack_from('<H',extra,i+4)[0]+1
            i+=4+n
        require(size is not None and size>=26 and pos+size<=len(raw),'Invalid BGZF size')
        block=gzip.decompress(raw[pos:pos+size]);require(len(block)<=65536,'Oversized BGZF payload')
        if block:offsets.append(pos)
        uncompressed.extend(block);pos+=size
    return bytes(uncompressed),offsets

def canonical(raw,kind):
    if kind=='vcf':return parse_vcf(raw)
    if kind=='sam':return parse_sam(raw)
    if kind=='bgzf':return {'decompressedSha256':sha(bgzf(raw)[0]),'decompressedBytes':len(bgzf(raw)[0])}
    if kind=='text':
        # bcftools stats prints argv[0], which differs by runtime. Preserve all
        # command arguments, and replace precisely its executable prefix only.
        text=raw.decode('utf-8')
        text=re.sub(r'(?m)^(#\s+The command line was:\s+)(?:\S*/)?bcftools(?=\s)',r'\1bcftools',text)
        text=re.sub(r'(?m)^(COMMAND: )(?:\S*/)?(?:samtools|hts)(?= markdup )',r'\1samtools',text)
        return text
    raise ValueError('Unknown artifact kind: '+kind)

def contract(raw,kind,spec,inputs):
    value=canonical(raw,kind);checks={}
    def check(name,condition,observed):
        require(condition,f'{name}: observed {observed!r}');checks[name]=observed
    for name,expected in spec.items():
        if name=='nonempty':check(name,bool(raw) and (bool(value['records']) if kind in ['vcf','sam'] else True),len(raw))
        elif name=='exact':check(name,raw.decode()==expected,raw.decode())
        elif name=='contains':
            for needle in expected:require(needle in raw.decode(),f'Missing required output {needle!r}')
            checks[name]=expected
        elif name in ['lines','minDataLines']:
            n=len([x for x in raw.decode().splitlines() if x and not x.startswith('#')]);check(name,n==expected if name=='lines' else n>=expected,n)
        elif name in ['records','minRecords']:
            n=len(value['records']);check(name,n==expected if name=='records' else n>=expected,n)
        elif name=='alleleCountsFrom':
            source=parse_vcf((inputs/expected).read_bytes());require(len(source['records'])==len(value['records']),'Allele count record count changed');truth=[]
            for original,observed in zip(source['records'],value['records']):
                require((original['chrom'],original['pos'],original['ref'],original['alt'])==(observed['chrom'],observed['pos'],observed['ref'],observed['alt']),'Allele count variant identity changed')
                ac=[0]*len(original['alt']);an=ns=0
                for sample in original['samples']:
                    alleles=[int(x) for x in re.split(r'[/|]',sample['GT']) if x!='.'];ns+=bool(alleles);an+=len(alleles)
                    for allele in alleles:
                        if allele:ac[allele-1]+=1
                expected_info={'AN':str(an),'AC':','.join(map(str,ac)),'NS':str(ns),'AF':','.join(format(a/an,'.6g') if an else '.' for a in ac)}
                require(all(observed['info'].get(k)==v for k,v in expected_info.items()),f'Incorrect counts at {original["pos"]}: {observed["info"]} expected {expected_info}');truth.append({'pos':original['pos'],**expected_info})
            checks[name]=truth
        elif name=='calledAlleles':
            actual={str(x['pos']):{'ref':x['ref'],'alt':x['alt'],'GT':[s.get('GT') for s in x.get('samples',[])]} for x in value['records']};check(name,actual==expected,actual)
        elif name=='trioTruth':
            rows=[x.split('\t') for x in raw.decode().splitlines() if x.startswith('FLT0\t')];require(len(rows)==1,'Expected exactly one trio summary');counts=list(map(int,rows[0][4:10]));check(name,rows[0][1:4]==['child','father','mother'] and counts==[5,4,1,1,1,1],counts)
        elif name=='samples':check(name,value['samples']==expected,value['samples'])
        elif name=='readNames':check(name,[row[0] for row in value['records']]==expected,[row[0] for row in value['records']])
        elif name=='positions':check(name,[x['pos'] for x in value['records']]==expected,[x['pos'] for x in value['records']])
        elif name=='firstInfo':
            actual={k:value['records'][0]['info'].get(k) for k in expected};check(name,actual==expected,actual)
        elif name=='info':
            actual=sorted({k for x in value['records'] for k in x['info']});check(name,set(expected)<=set(actual),actual)
        elif name=='format':
            actual=sorted({k for x in value['records'] for sample in x.get('samples',[]) for k in sample});check(name,set(expected)<=set(actual),actual)
        elif name=='biallelic':check(name,all(len(x['alt'])==1 for x in value['records']),[len(x['alt']) for x in value['records']])
        elif name in ['noMissingGT','hasMissingGT']:
            missing=sum('.' in sample.get('GT','') for x in value['records'] for sample in x.get('samples',[]));check(name,missing==0 if name=='noMissingGT' else missing>0,missing)
        elif name=='mendelianErrors':
            errors=[row['pos'] for row in value['records'] if row['info'].get('MERR')=='1'];check(name,errors==expected and all(row['info'].get('MERR') in ['0','1'] for row in value['records']),errors)
        elif name=='sameAlignmentFieldsAs':
            source=parse_sam((inputs/expected).read_bytes());fields=[row[:11] for row in value['records']];original=[row[:11] for row in source['records']];check(name,fields==original,{'source':expected,'sourceCanonicalSha256':sha(encoded(original))})
        elif name=='sameRecordsAs':
            source=canonical((inputs/expected).read_bytes(),kind);check(name,value['records']==source['records'],{'source':expected,'sourceCanonicalSha256':sha(encoded(source['records']))})
        elif name=='sameBytesAs':
            data=bgzf(raw)[0] if kind=='bgzf' else raw;source=(inputs/expected).read_bytes();check(name,data==source,{'source':expected,'sourceSha256':sha(source),'bytes':len(source)})
        elif name=='minMaxBlockOffset':
            offsets=bgzf(raw)[1];maximum=max(offsets,default=0);check(name,maximum>=expected,maximum)
        elif name=='opticalDuplicates':
            records=value['records'];duplicates=[row for row in records if int(row[1])&1024];check(name,len(duplicates)==2 and any(':2147483900:' in row[0] and 'dt:Z:LB' in row[11:] for row in duplicates) and any(':2147483698:' in row[0] and 'dt:Z:SQ' in row[11:] for row in duplicates),[{'name':row[0],'tags':row[11:]} for row in duplicates])
        elif name=='pscMeanDepth':
            rows=[x.split('\t') for x in raw.decode().splitlines() if x.startswith('PSC\t')];require(len(rows)==1,'Expected one PSC sample');depth=float(rows[0][9]);check(name,depth==expected,depth)
        else:raise ValueError('Unknown contract '+name)
    return checks

def compare(expected,actual,test,version):
    result={'schema':1,'case':test['id'],'version':version,'equal':True,'expectedFiles':{},'actualFiles':{},'expectedFileBytes':{},'actualFileBytes':{},'artifacts':[]}
    digests={}
    for spec in test['artifacts']:
        path,kind=spec['path'],spec['kind'];a=(expected/path).read_bytes();b=(actual/path).read_bytes()
        for side,raw in [('expected',a),('actual',b)]:result[side+'Files'][path]=sha(raw);result[side+'FileBytes'][path]=len(raw)
        av=canonical(a,kind);bv=canonical(b,kind);ac=contract(a,kind,spec.get('contract',{}),expected/'inputs');bc=contract(b,kind,spec.get('contract',{}),actual/'inputs')
        ah=sha(encoded(av));bh=sha(encoded(bv));equal=av==bv
        result['artifacts'].append({'path':path,'kind':kind,'equal':equal,'expectedCanonicalSha256':ah,'actualCanonicalSha256':bh,'contract':bc,'expectedContract':ac})
        result['equal']&=equal;digests[path]=bh
        if not equal:
            result['artifacts'][-1]['difference']=first_difference(av,bv)
    result['biologicalDigest']=sha(encoded(digests));return result

def first_difference(a,b,path='$'):
    if type(a)!=type(b):return {'path':path,'expected':str(a)[:500],'actual':str(b)[:500]}
    if isinstance(a,dict):
        if a.keys()!=b.keys():return {'path':path,'expectedKeys':sorted(a),'actualKeys':sorted(b)}
        for key in a:
            if a[key]!=b[key]:return first_difference(a[key],b[key],path+'.'+key)
    elif isinstance(a,list):
        if len(a)!=len(b):return {'path':path,'expectedLength':len(a),'actualLength':len(b)}
        for i,(x,y) in enumerate(zip(a,b)):
            if x!=y:return first_difference(x,y,path+f'[{i}]')
    return {'path':path,'expected':str(a)[:1000],'actual':str(b)[:1000]}

def main():
    p=argparse.ArgumentParser();p.add_argument('expected',type=Path);p.add_argument('actual',type=Path);p.add_argument('--case',required=True);p.add_argument('--version',required=True,choices=['1.16','1.21']);p.add_argument('--report',type=Path,required=True);a=p.parse_args()
    tests=json.loads((ROOT/'validation/hts/cases.json').read_text())['cases'];test=next(x for x in tests if x['id']==a.case and a.version in x.get('versions',['1.16','1.21']))
    try:result=compare(a.expected,a.actual,test,a.version)
    except Exception as e:result={'schema':1,'case':a.case,'version':a.version,'equal':False,'error':str(e)}
    a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    print(json.dumps(result));return 0 if result['equal'] else 1
if __name__=='__main__':raise SystemExit(main())
