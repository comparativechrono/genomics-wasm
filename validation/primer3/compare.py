#!/usr/bin/env python3
"""Compare every Primer3 scientific field; preserve order, sequence and numbers."""
import argparse, hashlib, json, re
from pathlib import Path, PurePosixPath
NORMALISATION=['CRLF line endings become LF; no output fields, numeric values, records or alignment positions are discarded.']
RESULT=re.compile(r'PRIMER_(?:LEFT|RIGHT|INTERNAL|PAIR)_(?:\d+(?:_|$)|NUM_RETURNED$)')
POSITION=re.compile(r'PRIMER_(LEFT|RIGHT|INTERNAL)_(\d+)\Z')

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def safe_file(directory,name):
 p=PurePosixPath(name)
 if not name or p.is_absolute() or str(p)!=name or any(x in ('.','..') for x in p.parts):raise ValueError('unsafe artifact path')
 root=Path(directory).resolve();path=(root/name).resolve(strict=True)
 if not path.is_relative_to(root) or not path.is_file():raise ValueError('artifact escapes output directory')
 return path
def text(raw):
 s=raw.decode('utf-8').replace('\r\n','\n')
 if any(ord(c)<32 and c not in '\t\n' for c in s):raise ValueError('invalid output control character')
 return s

def boulder(raw,spec):
 s=text(raw);records=[];fields=[]
 for line in s.splitlines():
  if line=='=':
   if not fields:raise ValueError('empty Boulder record')
   records.append(fields);fields=[]
  elif '=' in line:
   k,v=line.split('=',1)
   if not re.fullmatch('[A-Z0-9_]+',k):raise ValueError('invalid Boulder key')
   fields.append((k,v))
  elif line.strip():raise ValueError('non-Boulder output line')
 if fields or not records:raise ValueError('unterminated or absent Boulder records')
 pairs=primers=errors=0;bio=[]
 for fields in records:
  scientific=[(k,v) for k,v in fields if RESULT.match(k)]
  keys=[k for k,v in scientific]
  if len(keys)!=len(set(keys)):raise ValueError('duplicate scientific output key')
  d=dict(fields);errors+=int('PRIMER_ERROR' in d)
  if 'PRIMER_ERROR' not in d:
   for side in ('LEFT','RIGHT','INTERNAL','PAIR'):
    key=f'PRIMER_{side}_NUM_RETURNED'
    if key not in d or not re.fullmatch(r'\d+',d[key]):raise ValueError('missing or invalid returned count')
    count=int(d[key]);pairs+=count if side=='PAIR' else 0;primers+=count if side!='PAIR' else 0
    if side!='PAIR':
     observed=[int(m.group(1)) for k in d if (m:=re.fullmatch(f'PRIMER_{side}_(\\d+)_SEQUENCE',k))]
     if sorted(observed)!=list(range(count)):raise ValueError('primer count/sequence indices inconsistent')
    else:
     observed=[int(m.group(1)) for k in d if (m:=re.fullmatch(r'PRIMER_PAIR_(\d+)_PENALTY',k))]
     if sorted(observed)!=list(range(count)):raise ValueError('pair count/rank indices inconsistent')
   for k,v in scientific:
    if v.lower() in ('nan','inf','-inf','+inf','infinity','-infinity'):raise ValueError('nonfinite scientific output')
    if k.endswith('_SEQUENCE') and not re.fullmatch('[ACGTNacgtn]+',v):raise ValueError('invalid primer sequence')
    if POSITION.fullmatch(k):
     if not re.fullmatch(r'-?\d+,\d+',v):raise ValueError('invalid primer coordinates')
     length=int(v.split(',')[1]);sequence=d.get(k+'_SEQUENCE')
     if length<=0 or sequence is None or len(sequence)<length:raise ValueError('primer length inconsistent')
  bio.append(scientific)
 inv={'kind':'boulder','records':len(records),'pairs':pairs,'primers':primers,'errors':errors}
 for key in ('records','errors'):
  if key in spec and inv[key]!=spec[key]:raise ValueError(f'expected {key}={spec[key]}, saw {inv[key]}')
 if pairs<spec.get('minPairs',0):raise ValueError('too few returned primer pairs')
 if primers<spec.get('minPrimers',0):raise ValueError('too few returned primers')
 return records,inv,bio

def parse_artifact(path,spec):
 raw=path.read_bytes();kind=spec['kind']
 if kind=='boulder':return boulder(raw,spec)
 if kind=='text':
  value=text(raw)
  if not value.strip():raise ValueError('empty scientific output')
  if 'required' in spec and any(s not in value for s in spec['required']):raise ValueError('missing required output token')
  if spec.get('numeric'):
   for token in value.split():
    if token.lower() in ('nan','inf','-inf','infinity'):raise ValueError('nonfinite scientific output')
  return value,{'kind':'text','lines':len(value.splitlines()),'bytes':len(raw)},value
 if kind=='binary':return raw.hex(),{'kind':'binary','bytes':len(raw)},raw.hex()
 raise ValueError('unknown artifact kind')

def compare(expected_dir,actual_dir,spec):
 artifacts=spec.get('artifacts',[]);paths=[a['path'] for a in artifacts]
 if not artifacts or len(paths)!=len(set(paths)):raise ValueError('empty or duplicate artifact plan')
 biological=spec.get('biologicalArtifacts',paths)
 if not biological or len(biological)!=len(set(biological)) or any(p not in paths for p in biological):raise ValueError('invalid biological artifact selection')
 values={};invariants={};hashes={};sizes={};bios={}
 for side,directory in [('expected',expected_dir),('actual',actual_dir)]:
  values[side]={};invariants[side]={};hashes[side]={};sizes[side]={};bios[side]={}
  for artifact in artifacts:
   name=artifact['path'];path=safe_file(directory,name)
   values[side][name],invariants[side][name],bios[side][name]=parse_artifact(path,artifact)
   raw=path.read_bytes();hashes[side][name]=hashlib.sha256(raw).hexdigest();sizes[side][name]=len(raw)
 expected,actual=values['expected'],values['actual'];differences=[p for p in paths if expected[p]!=actual[p]]
 return {'equal':not differences,'semanticDigest':digest(actual),'expectedSemanticDigest':digest(expected),'biologicalDigest':digest([bios['actual'][p] for p in biological]),'expectedBiologicalDigest':digest([bios['expected'][p] for p in biological]),'nativeInvariants':invariants['expected'],'invariants':invariants['actual'],'expectedFiles':hashes['expected'],'actualFiles':hashes['actual'],'expectedFileBytes':sizes['expected'],'actualFileBytes':sizes['actual'],'expectedCanonicalFiles':{p:digest(expected[p]) for p in paths},'canonicalFiles':{p:digest(actual[p]) for p in paths},'normalisation':NORMALISATION,'numericTolerance':0,'numericComparisons':{},'differences':differences}

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('expected',type=Path);p.add_argument('actual',type=Path);p.add_argument('--tool',choices=['primer3'],required=True);p.add_argument('--dataset',required=True);p.add_argument('--case',required=True);p.add_argument('--actual-target',required=True);p.add_argument('--manifest',type=Path,required=True);p.add_argument('--report',type=Path,required=True);a=p.parse_args()
 plan=json.loads((Path(__file__).parent/'cases.json').read_text());spec=next(t for t in plan['tools'][a.tool]['cases'] if t['id']==a.case)
 if a.dataset not in spec.get('datasets',plan['tools'][a.tool]['datasets']):raise ValueError('case dataset mismatch')
 raw=a.manifest.read_bytes();json.loads(raw)['datasets'][a.dataset];r=compare(a.expected,a.actual,spec)
 r.update(schema=1,tool=a.tool,dataset=a.dataset,case=a.case,actualTarget=a.actual_target,manifestSha256=hashlib.sha256(raw).hexdigest());a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r));raise SystemExit(0 if r['equal'] else 1)
if __name__=='__main__':main()
