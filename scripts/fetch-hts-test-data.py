#!/usr/bin/env python3
"""Verify/reproduce explicitly synthetic and pinned upstream HTS regression inputs."""
from __future__ import annotations
import argparse, hashlib, json, shutil, tempfile, urllib.request, importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'datasets/hts'
CACHE=ROOT/'build/hts-fixture-sources'

def digest(raw): return hashlib.sha256(raw).hexdigest()
def write(path,raw):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(raw.encode() if isinstance(raw,str) else raw)
def synthetic():
    ref='ACGT'*250
    result={'reference.fa':'>chr1\n'+'\n'.join(ref[i:i+60] for i in range(0,len(ref),60))+'\n',
            'family.ped':'FAM child father mother 1 2\nFAM father 0 0 1 1\nFAM mother 0 0 2 1\nFAM unrelated 0 0 2 1\n',
            'populations.txt':'child\tFAMILY\nfather\tFAMILY\nmother\tFAMILY\nunrelated\tOTHER\n',
            'targets.bed':'chr1\t90\t190\nchr1\t250\t330\n',
            'exons.bed':'chr1\t90\t330\n'}
    header=['##fileformat=VCFv4.2','##source=genomics-wasm-explicit-synthetic-fixture','##contig=<ID=chr1,length=1000>',
      '##INFO=<ID=CSQ,Number=.,Type=String,Description="Synthetic VEP-style annotations. Format: Allele|Consequence|IMPACT|SYMBOL|Gene|Feature|CANONICAL|AF">',
      '##INFO=<ID=AF,Number=A,Type=Float,Description="Synthetic allele frequency">',
      '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
      '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Read depth">',
      '##FORMAT=<ID=AD,Number=R,Type=Integer,Description="Allelic depths">',
      '##FORMAT=<ID=PL,Number=G,Type=Integer,Description="Phred likelihoods">',
      '##FORMAT=<ID=GP,Number=G,Type=Float,Description="Synthetic genotype probabilities">',
      '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tchild\tfather\tmother\tunrelated']
    definitions=[('A','G',['0/1','0/0','0/1','./.']),('A','C',['0/1','0/0','0/0','1/1']),
      ('A','C,G',['1/2','0/1','0/2','0/0']),('ACG','A',['0/1','0/1','0/0','./1']),
      ('A','ATC',['1/1','0/1','0/1','0/0']),('A','T',['0|1','0|1','0|0','0|1']),
      ('A','G',['1','0','1','.']),('A','C',['./.','0/0','./.','0/1']),
      ('A','G',['0/0','0/0','0/0','0/0']),('A','C',['1/1','1/1','0/0','1/1']),
      ('A','T',['0|1','0|0','1|1','0|0']),('A','G',['0/1','./1','0/0','./.'])]
    rows=[]
    for i,(r,a,gts) in enumerate(definitions):
        n=1+len(a.split(',')); samples=[]
        for gt in gts:
            alleles=gt.replace('|','/').split('/'); known=[int(x) for x in alleles if x!='.']; ad=[0]*n
            for x in known: ad[x]+=10
            if len(known)!=len(alleles): pl=gp='.'
            else:
                combinations=[(x,) for x in range(n)] if len(alleles)==1 else [(x,y) for y in range(n) for x in range(y+1)]
                actual=tuple(sorted(known)); idx=combinations.index(actual)
                pl=','.join('0' if j==idx else '60' for j in range(len(combinations)))
                gp=','.join('1' if j==idx else '0' for j in range(len(combinations)))
            samples.append(f'{gt}:{sum(ad)}:'+','.join(map(str,ad))+f':{pl}:{gp}')
        allele=a.split(',')[0]; consequence='frameshift_variant' if len(r)!=len(allele) else 'missense_variant'
        annotation=f'{allele}|{consequence}|MODERATE|GENE1|ENSG000001|ENST000001|YES|0.001,{allele}|synonymous_variant|LOW|GENE1|ENSG000001|ENST000002||0.15'
        rows.append('\t'.join(['chr1',str(101+20*i),'rs'+str(1000+i),r,a,'60','PASS','AF='+','.join(['0.1']*(n-1))+';CSQ='+annotation,'GT:DP:AD:PL:GP',*samples]))
    result['trio-csq.vcf']='\n'.join(header+rows)+'\n'
    result['phased.vcf']=result['trio-csq.vcf'].replace('/','|')
    sam=['@HD\tVN:1.6\tSO:coordinate','@SQ\tSN:chr1\tLN:1000','@RG\tID:SYN\tSM:synthetic']
    for i in range(14):
        pos=81+17*i; seq=ref[pos-1:pos+49]; flag=1024 if i==13 else 0
        if i==3: seq=seq[:20]+('T' if seq[20]!='T' else 'A')+seq[21:]
        sam.append('\t'.join([f'read{i:02}',str(flag),'chr1',str(pos),'60','50M','*','0','0',seq,'I'*50,'RG:Z:SYN','NM:i:'+str(int(i==3))]))
    sam.extend(['\t'.join(['pair','99','chr1','401','60','50M','=','501','150',ref[400:450],'I'*50,'RG:Z:SYN']),
                '\t'.join(['pair','147','chr1','501','60','50M','=','401','-150',ref[500:550],'I'*50,'RG:Z:SYN']),
                '\t'.join(['unmapped','4','*','0','0','*','*','0','0','ACGT'*12+'AC','I'*50,'RG:Z:SYN'])])
    result['alignments.sam']='\n'.join(sam)+'\n'
    calling=['@HD\tVN:1.6\tSO:coordinate','@SQ\tSN:chr1\tLN:1000','@RG\tID:CALL\tSM:synthetic']
    for i in range(12):
        seq=ref[100:150]
        if i<8:seq=seq[:20]+'G'+seq[21:]
        calling.append('\t'.join([f'call{i:02}','0','chr1','101','60','50M','*','0','0',seq,'I'*50,'RG:Z:CALL']))
    result['calling.sam']='\n'.join(calling)+'\n'
    truth_header=header[:3]+[header[5],header[-1].rsplit('\tunrelated',1)[0]]
    truth_gts=[['0/1','0/0','0/0'],['0/1','0/1','0/0'],['0/0','0/1','0/0'],['0/0','0/0','0/0'],['0/1','0/0','1/1']]
    result['trio-truth.vcf']='\n'.join(truth_header+['\t'.join(['chr1',str(101+20*i),'.','A','G','60','PASS','.','GT',*gts]) for i,gts in enumerate(truth_gts)])+'\n'

    wide=['##fileformat=VCFv4.2','##contig=<ID=chr1,length=1000000>',
          '##INFO=<ID=TOKEN,Number=1,Type=String,Description="Deterministic incompressible payload for offset regression">',
          '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO']
    for i in range(6000): wide.append(f'chr1\t{10*(i+1)}\tw{i:05}\tA\tG\t60\tPASS\tTOKEN={hashlib.sha256(str(i).encode()).hexdigest()}')
    result['wide-offset.vcf']='\n'.join(wide)+'\n'
    result['wide-depth.vcf']='\n'.join(['##fileformat=VCFv4.2','##contig=<ID=chr1,length=1000>',
      '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">','##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">',
      '#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS',
      *[f'chr1\t{i}\t.\tA\tG\t60\tPASS\t.\tGT:DP\t0/1:2000000000' for i in [101,201,301]]])+'\n'
    module_spec=importlib.util.spec_from_file_location('hts_portability_fixtures',ROOT/'validation/hts/portability_fixtures.py')
    module=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(module)
    return {**{k:v.encode() for k,v in result.items()},**module.synthetic()}

def locked(): return json.loads((DATA/'provenance/sources.lock.json').read_text())
def fetch(record):
    path=CACHE/record['path']
    if path.is_file() and digest(path.read_bytes())==record['sha256']: return path
    path.parent.mkdir(parents=True,exist_ok=True)
    with urllib.request.urlopen(record['url'],timeout=90) as source: raw=source.read(record['bytes']+1)
    if len(raw)!=record['bytes'] or digest(raw)!=record['sha256']: raise RuntimeError('Pinned source mismatch: '+record['url'])
    write(path,raw); return path

def generate(destination):
    for name,raw in synthetic().items(): write(destination/'common'/name,raw)
    for record in locked()['files']:
        raw=(CACHE/record['path']).read_bytes()
        if len(raw)!=record['bytes'] or digest(raw)!=record['sha256']: raise RuntimeError('Pinned source mismatch: '+record['path'])
        write(destination/record['path'],raw)

def inventory(base):
    return {p.relative_to(base).as_posix():{'bytes':p.stat().st_size,'sha256':digest(p.read_bytes())} for p in sorted(base.rglob('*')) if p.is_file() and p.relative_to(base).parts[0] in ['common','upstream']}

def materialize_frozen(generated, manifest):
    """Restore absent frozen fixtures without accepting changed source bytes."""
    expected=manifest['files']
    if inventory(generated)!=expected:
        raise RuntimeError('Regenerated fixtures differ from frozen manifest')
    present=inventory(DATA)
    if any(name not in expected or info!=expected[name] for name,info in present.items()):
        raise RuntimeError('Existing fixtures differ from frozen manifest')
    for name in expected.keys()-present.keys():
        destination=DATA/name
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(generated/name,destination)

def main():
    p=argparse.ArgumentParser();p.add_argument('--fetch-sources',action='store_true');p.add_argument('--regenerate',action='store_true');p.add_argument('--update-manifest',action='store_true');p.add_argument('--verify',action='store_true');a=p.parse_args()
    if a.fetch_sources:
        for record in locked()['files']: fetch(record)
    if a.regenerate:
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp);generate(out)
            if a.update_manifest:
                for child in ['common','upstream']:
                    if (DATA/child).exists(): shutil.rmtree(DATA/child)
                    shutil.copytree(out/child,DATA/child)
                manifest={'schema':1,'tool':'hts','versions':['1.16','1.21'],'files':inventory(DATA),
                  'synthetic':{'trioRecords':12,'samples':['child','father','mother','unrelated'],'firstSite':{'chrom':'chr1','pos':101,'AN':6,'AC':[2],'NS':3},'samRecords':17,'mappedReads':16,'referenceBases':1000,'wideOffsetRecords':6000},
                  'sourcesLockSha256':digest((DATA/'provenance/sources.lock.json').read_bytes())}
                write(DATA/'manifest.json',json.dumps(manifest,indent=2,sort_keys=True)+'\n')
            else: materialize_frozen(out,json.loads((DATA/'manifest.json').read_text()))
    manifest=json.loads((DATA/'manifest.json').read_text())
    if inventory(DATA)!=manifest['files']: raise RuntimeError('Fixture inventory/bytes differ from manifest')
    if digest((DATA/'provenance/sources.lock.json').read_bytes())!=manifest['sourcesLockSha256']: raise RuntimeError('Source provenance changed')
    print(f"Verified {len(manifest['files'])} HTS fixture/provenance files")
if __name__=='__main__': main()
