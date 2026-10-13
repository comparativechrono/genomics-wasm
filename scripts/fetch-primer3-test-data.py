#!/usr/bin/env python3
"""Verify or reproducibly regenerate checksum-pinned Primer3 input fixtures."""
from pathlib import Path
import argparse, hashlib, json, struct, tarfile, urllib.request, tempfile, shutil
ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'datasets/primer3'
LOCK=DATA/'provenance/sources.lock.json'

def ident(raw): return {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
def write_json(p,x): p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,indent=2,sort_keys=True)+'\n')
def regenerate(fetch,output):
 lock=json.loads(LOCK.read_text()); sources={}
 for spec in lock['sources']:
  name=spec['name']
  path=ROOT/spec['cachePath']
  if not path.is_file() and fetch:
   path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(urllib.request.urlopen(spec['url'],timeout=120).read())
  raw=path.read_bytes()
  if ident(raw)!={k:spec[k] for k in ('bytes','sha256')}: raise ValueError('Source identity mismatch: '+name)
  sources[name]=raw
 tar=tarfile.open(ROOT/next(s['cachePath'] for s in lock['sources'] if s['name']=='primer3'))
 def upstream(name):return tar.extractfile('primer3-2.6.1/'+name).read()
 outputs={};origins={}
 def put(name,raw,origin):
  outputs[name]=raw.encode() if isinstance(raw,str) else raw;origins[name]=origin
 for source,target in lock['upstreamFiles'].items():
  raw=upstream(source)
  if ident(raw)!=lock['upstreamIdentities'][source]:raise ValueError('Upstream member identity mismatch')
  put(target,raw,{'source':'primer3','path':source,'transformation':'Unchanged bytes.'})
 fasta=sources['hbb'];put('primer3/real/NM_000518.5.fasta',fasta,{'source':'hbb','transformation':'Unchanged versioned NCBI RefSeq FASTA response.'})
 seq=''.join(fasta.decode().splitlines()[1:]);assert len(seq)==628 and set(seq)<=set('ACGT')
 def boulder(name,extra=(),template=seq):
  tags=[('SEQUENCE_ID','NM_000518.5-HBB'),('SEQUENCE_TEMPLATE',template),('PRIMER_TASK','generic'),('PRIMER_NUM_RETURN','5'),('PRIMER_PRODUCT_SIZE_RANGE','120-220'),('PRIMER_EXPLAIN_FLAG','1')]
  changes=dict(extra);tags=[(k,v) for k,v in tags if k not in changes]+list(extra)
  value=''.join(f'{k}={v}\n' for k,v in tags)+'=\n'
  put('primer3/real/'+name,value,{'source':'hbb','transformation':'Remove FASTA header/newlines; retain all 628 transcript nucleotides. Add explicit software-test Boulder IO parameters: '+json.dumps(dict(tags)|{'SEQUENCE_TEMPLATE':'[complete source sequence]'})})
  return value
 base=boulder('hbb-pairs.boulder')
 boulder('hbb-internal.boulder',[('PRIMER_PICK_INTERNAL_OLIGO','1')])
 boulder('hbb-constrained.boulder',[('SEQUENCE_TARGET','250,50'),('SEQUENCE_EXCLUDED_REGION','100,20'),('SEQUENCE_INCLUDED_REGION','40,540')])
 boulder('hbb-thermodynamic.boulder',[('PRIMER_THERMODYNAMIC_OLIGO_ALIGNMENT','1'),('PRIMER_THERMODYNAMIC_TEMPLATE_ALIGNMENT','1'),('PRIMER_THERMODYNAMIC_PARAMETERS_PATH','config/')])
 boulder('hbb-legacy.boulder',[('PRIMER_THERMODYNAMIC_OLIGO_ALIGNMENT','0'),('PRIMER_TM_FORMULA','0'),('PRIMER_SALT_CORRECTIONS','0')])
 boulder('hbb-high-gc.boulder',[('PRIMER_GC_CLAMP','2'),('PRIMER_PRODUCT_SIZE_RANGE','200-300')])
 boulder('hbb-invalid.boulder',[('PRIMER_MIN_SIZE','30'),('PRIMER_OPT_SIZE','20')])
 boulder('hbb-missing-config.boulder',[('PRIMER_THERMODYNAMIC_PARAMETERS_PATH','absent-config/')])
 boulder('hbb-mispriming.boulder',[('PRIMER_MISPRIMING_LIBRARY','synthetic/repeat-library.fasta'),('PRIMER_MAX_LIBRARY_MISPRIMING','8'),('PRIMER_PAIR_MAX_LIBRARY_MISPRIMING','16')])
 put('primer3/real/hbb-multi.boulder',base+base.replace('NM_000518.5-HBB','NM_000518.5-HBB-second').replace('120-220','220-320'),{'source':'hbb','transformation':'Two complete HBB Boulder records; second record changes product-size range to 220-320.'})
 put('primer3/synthetic/repeat-library.fasta','>hbb-fragment-100-180\n'+seq[100:180]+'\n>hbb-fragment-300-380\n'+seq[300:380]+'\n',{'source':'hbb','transformation':'Artificial mispriming library consisting of HBB bases [100:180] and [300:380], zero-based half-open; not a genome-wide specificity screen.'})
 put('primer3/synthetic/ntthal-interactive.txt','ccgcagtaagctgcgg,ccgcagtaagctgcgg\nAAAACCCGCTTTGCTAGCTACG,AACCCGCTTGCTAGCAAAAAAAA\n',{'source':'primer3','path':'test/thal_batch_input','transformation':'First two complete comma-separated upstream pair records, unchanged, for ntthal -i.'})
 header=bytearray(40);struct.pack_into('<I',header,0,0x47543443);struct.pack_into('<I',header,12,4);struct.pack_into('<I',header,16,256);struct.pack_into('<Q',header,32,40)
 put('primer3/synthetic/masker-4mer.list',bytes(header)+b''.join(struct.pack('<QI',w,20 if w%3==0 else 1) for w in range(256)),{'source':'primer3','path':'src/masker.c','transformation':'Synthetic complete 4-mer index in upstream little-endian glistmaker format: all 256 sorted two-bit words, frequency20 for word divisible by3 else1; 40byte header. Exercises mmap, binary search and masking; not measured genome frequencies.'})
 put('primer3/synthetic/masker.fasta','>synthetic\nAACGTGATCCAGTTCAGGAACCGTATCGTACGTTGCCGATCATG\n',{'source':'synthetic','transformation':'Explicit artificial 44-base test oligo; no biological provenance claimed.'})
 write_json(output/'provenance/fixture-sources.json',origins)
 for path,raw in outputs.items():
  p=output/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
 return {'reference':{'accession':'NM_000518.5','organism':'Homo sapiens','gene':'HBB','bases':len(seq)},'upstreamFiles':len(lock['upstreamFiles']),'fixtureFiles':len(outputs)}

def inventory(base):return {str(p.relative_to(base)):ident(p.read_bytes()) for p in sorted(base.rglob('*')) if p.is_file() and p.name!='manifest.json'}
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--verify',action='store_true');p.add_argument('--regenerate',action='store_true');p.add_argument('--fetch',action='store_true');p.add_argument('--update-manifest',action='store_true');a=p.parse_args()
 if a.update_manifest and not a.regenerate:p.error('--update-manifest requires --regenerate')
 if a.fetch and not a.regenerate:
  for spec in json.loads(LOCK.read_text())['sources']:
   path=ROOT/spec['cachePath']
   if not path.is_file():
    raw=urllib.request.urlopen(spec['url'],timeout=120).read()
    if ident(raw)!={k:spec[k] for k in ('bytes','sha256')}:raise ValueError('Downloaded source checksum mismatch')
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
   if ident(path.read_bytes())!={k:spec[k] for k in ('bytes','sha256')}:raise ValueError('Source checksum mismatch')
 if a.regenerate:
  scratch=ROOT/'build/primer3-regenerate';scratch.mkdir(exist_ok=True)
  with tempfile.TemporaryDirectory(dir=scratch) as tmp:
   output=Path(tmp);observations=regenerate(a.fetch,output)
   for name in ('README.md','provenance/sources.lock.json','provenance/DATA_REUSE.md'):
    target=output/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(DATA/name,target)
   generated=inventory(output)
   if a.update_manifest:
    for name in generated:
     target=DATA/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(output/name,target)
    script='scripts/fetch-primer3-test-data.py'
    write_json(DATA/'manifest.json',{'schema_version':1,'datasets':{'primer3':{'observations':observations}},'files':generated,'sources':json.loads(LOCK.read_text())['sources'],'regeneration':{'script':script,'files':{script:ident((ROOT/script).read_bytes())},'method':'Unchanged versioned RefSeq and pinned upstream test inputs; explicit deterministic synthetic recipes. No result goldens.'}})
   else:
    expected=json.loads((DATA/'manifest.json').read_text())
    if generated!=expected['files'] or observations!=expected['datasets']['primer3']['observations']:raise ValueError('Regeneration differs from frozen manifest')
 m=json.loads((DATA/'manifest.json').read_text());actual=inventory(DATA)
 if actual!=m['files']:
  bad=[p for p in sorted(actual.keys()|m['files'].keys()) if actual.get(p)!=m['files'].get(p)];raise ValueError('Dataset inventory mismatch: '+', '.join(bad))
 if m['sources']!=json.loads(LOCK.read_text())['sources']:raise ValueError('Source lock/manifest mismatch')
 for name,info in m['regeneration']['files'].items():
  if ident((ROOT/name).read_bytes())!=info:raise ValueError('Regeneration recipe changed: '+name)
 print(f'Verified {len(actual)} Primer3 fixture/provenance files ({sum(x["bytes"] for x in actual.values())} bytes)')
if __name__=='__main__':main()
