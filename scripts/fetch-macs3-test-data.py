#!/usr/bin/env python3
"""Verify or regenerate authentic MACS3 fixtures and explicit artificial score tracks."""
from pathlib import Path
import argparse, gzip, io, hashlib, json, shutil, tempfile, urllib.request, sys, struct
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'datasets/macs3'
LOCK = DATA / 'provenance/sources.lock.json'
def ident(raw): return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
def inventory(base):
    return {str(p.relative_to(base)): ident(p.read_bytes()) for p in sorted(base.rglob('*')) if p.is_file() and p.name != 'manifest.json'}
def bai_virtual_offsets(raw):
    if raw[:4]!=b'BAI\x01':raise ValueError('Invalid BAI magic')
    offset=4;maximum=0;above32=0
    references,=struct.unpack_from('<I',raw,offset);offset+=4
    for _ in range(references):
        bins,=struct.unpack_from('<I',raw,offset);offset+=4
        for _ in range(bins):
            number,chunks=struct.unpack_from('<II',raw,offset);offset+=8
            for _ in range(chunks):
                begin,end=struct.unpack_from('<QQ',raw,offset);offset+=16
                if number!=37450:
                    maximum=max(maximum,begin,end);above32+=int(begin>2**32)+int(end>2**32)
        intervals,=struct.unpack_from('<I',raw,offset);offset+=4+8*intervals
    if offset not in (len(raw),len(raw)-8):raise ValueError('Invalid BAI length')
    return {'maxVirtualOffset':maximum,'chunkOffsetsAboveUint32':above32}
def regenerate(fetch, out):
    lock=json.loads(LOCK.read_text()); sources={}
    for spec in lock['sources']:
        path=ROOT/spec['cachePath']
        if not path.exists() and fetch:
            raw=urllib.request.urlopen(spec['url'],timeout=120).read()
            if ident(raw)!={k:spec[k] for k in ('bytes','sha256')}: raise ValueError('Downloaded checksum mismatch: '+spec['name'])
            path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
        raw=path.read_bytes()
        if ident(raw)!={k:spec[k] for k in ('bytes','sha256')}: raise ValueError('Source checksum mismatch: '+spec['name'])
        if hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()!=spec['gitBlobSha']: raise ValueError('Git blob mismatch')
        sources[spec['name']]=raw
    origins={}
    def put(name, raw, source, transform='Unchanged upstream bytes.'):
        raw=raw.encode() if isinstance(raw,str) else raw
        path=out/'macs3'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
        origins['macs3/'+name]={'source':source,'transformation':transform}
    rename={'CTCF_SE_ChIP_chr22_50k.bed.gz':'chip.bed.gz','CTCF_SE_CTRL_chr22_50k.bed.gz':'control.bed.gz','CTCF_PE_ChIP_chr22_50k.bedpe.gz':'chip.bedpe.gz','CTCF_PE_CTRL_chr22_50k.bedpe.gz':'control.bedpe.gz','test.fragments.tsv.gz':'fragments.tsv.gz','barcodes.txt':'barcodes.txt','callvar_testing.narrowPeak':'variants.narrowPeak','tiny.bam':'tiny.bam','tiny.bam.bai':'tiny.bam.bai','tiny.sam.gz':'tiny.sam.gz','standard_results_callpeak_narrow/run_callpeak_narrow0_peaks.narrowPeak':'upstream-peaks.narrowPeak'}
    for source,name in rename.items(): put(name,sources[source],source)
    yeast=gzip.decompress(sources['yeast_500k_SRR1822137.bedpe.gz']); subset=b''.join(row+b'\n' for row in yeast.splitlines() if row.split()[0]==b'chrI')
    stream=io.BytesIO()
    with gzip.GzipFile(fileobj=stream,mode='wb',filename='',mtime=0,compresslevel=9) as zipped:zipped.write(subset)
    put('atac-chrI.bedpe.gz',stream.getvalue(),'yeast_500k_SRR1822137.bedpe.gz','Retain all records whose first tab-separated field is chrI, in source order. gzip mtime=0.')
    # Re-encoding retains complete alignments/tags and original BAM header. No bases or alleles are generated.
    try: import pysam
    except ImportError:
        sys.path.insert(0,str(ROOT/'build/macs3-fixture-tools'));import pysam
    if pysam.__version__!='0.23.3': raise ValueError('Regeneration requires pysam==0.23.3')
    regions=[r.split() for r in sources['callvar_testing.narrowPeak'].decode().splitlines()]
    intervals=[(r[0],max(0,int(r[1])-2000),int(r[2])+2000) for r in regions]
    counts={};virtual_offsets={}
    for role,label in [('chip','ChIP'),('control','CTRL')]:
        source=f'CTCF_PE_{label}_chr22_50k.bam';src=ROOT/next(s['cachePath'] for s in lock['sources'] if s['name']==source)
        name='variants-'+role+'.bam';target=out/'macs3'/name
        with pysam.AlignmentFile(src,'rb') as infile,pysam.AlignmentFile(target,'wb',header=infile.header) as outfile:
            n=0
            for record in infile:
                if not record.is_unmapped and any(record.reference_name==chrom and record.reference_start<end and record.reference_end>start for chrom,start,end in intervals):outfile.write(record);n+=1
        pysam.index(str(target));counts[role]=n
        virtual_offsets[role]=bai_virtual_offsets(Path(str(target)+'.bai').read_bytes())
        if role=='chip' and virtual_offsets[role]['chunkOffsetsAboveUint32']==0:raise ValueError('Variant fixture no longer exercises 64-bit BAM virtual offsets')
        for suffix in ('','.bai'):origins['macs3/'+name+suffix]={'source':source,'transformation':'pysam 0.23.3: retain source-order mapped alignments overlapping union of upstream callvar_testing.narrowPeak intervals padded by 2000 bases each side; preserve complete header, sequence, quality, CIGAR, flags and auxiliary tags; BGZF re-encode and BAI index.'}
    tracks={'signal.bdg':[0,10,2,20,0,8,0],'background.bdg':[2]*7,'signal2.bdg':[0,2,2,20,0,16,0],'background2.bdg':[1]*7}
    edges=[0,100,300,400,600,700,900,1000]
    for name,values in tracks.items():put(name,''.join(f'chrTest\t{edges[i]}\t{edges[i+1]}\t{v}\n' for i,v in enumerate(values)),'synthetic','Explicit artificial score track for independently calculable algebra/interval tests, not a biological measurement.')
    for name,values in [('wide-fragments.bedpe',None),('wide-signal.bdg',range(12,17)),('wide-background.bdg',[1]*5)]:
        raw=''.join(f'chrWide{i+1}\t0\t1000000000'+('' if values is None else f'\t{values[i]}')+'\n' for i in range(5))
        put(name,raw,'synthetic','Five sparse 1-gigabase intervals on distinct artificial chromosomes; aggregate 5,000,000,000 bases crosses signed and unsigned 32-bit arithmetic boundaries. No biological measurement claimed.')
    put('regions.bed','chrTest\t100\t300\tfirst\nchrTest\t400\t600\tsecond\nchrTest\t700\t900\tthird\n','synthetic','Exact regions above score 5 in signal.bdg.')
    put('training.bed','chrI\t0\t230218\n','yeast_500k_SRR1822137.bedpe.gz','Explicit whole-chrI training interval for deterministic small-model validation.')
    write_json(out/'provenance/fixture-sources.json',origins)
    return {'chipReads':len(gzip.decompress(sources['CTCF_SE_ChIP_chr22_50k.bed.gz']).splitlines()),'controlReads':len(gzip.decompress(sources['CTCF_SE_CTRL_chr22_50k.bed.gz']).splitlines()),'chipFragments':len(gzip.decompress(sources['CTCF_PE_ChIP_chr22_50k.bedpe.gz']).splitlines()),'atacChrIFragments':len(subset.splitlines()),'variantAlignments':counts,'baiVirtualOffsets':virtual_offsets,'variantRegions':len(regions),'syntheticScoreSegments':7,'syntheticWideAggregateBases':5000000000,'sourceCommit':lock['commit']}
def main():
    p=argparse.ArgumentParser(description=__doc__)
    for flag in ('verify','regenerate','fetch','update-manifest'):p.add_argument('--'+flag,action='store_true')
    p.add_argument('--fetch-sources',action='store_true')
    a=p.parse_args()
    a.fetch=a.fetch or a.fetch_sources
    if a.fetch:
        for spec in json.loads(LOCK.read_text())['sources']:
            target=ROOT/spec['cachePath']
            if not target.exists():
                raw=urllib.request.urlopen(spec['url'],timeout=120).read()
                if ident(raw)!={k:spec[k] for k in ('bytes','sha256')}:raise ValueError('Downloaded source checksum mismatch')
                target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
            if ident(target.read_bytes())!={k:spec[k] for k in ('bytes','sha256')}:raise ValueError('Source checksum mismatch')
    if a.update_manifest and not a.regenerate:p.error('--update-manifest requires --regenerate')
    if a.regenerate:
        scratch=ROOT/'build/macs3-regenerate';scratch.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as tmp:
            output=Path(tmp);observations=regenerate(a.fetch,output)
            for name in ('README.md','provenance/sources.lock.json','provenance/DATA_REUSE.md'):
                target=output/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(DATA/name,target)
            actual=inventory(output)
            if a.update_manifest:
                for name in actual:
                    target=DATA/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(output/name,target)
                script='scripts/fetch-macs3-test-data.py'
                write_json(DATA/'manifest.json',{'schema_version':1,'datasets':{'macs3':{'observations':observations}},'files':actual,'sources':json.loads(LOCK.read_text())['sources'],'regeneration':{'script':script,'files':{script:ident((ROOT/script).read_bytes())},'method':'Unchanged pinned upstream public test inputs; deterministic all-record chrI and padded BAM interval subsets; explicit artificial bedGraphs. No native/WASM result goldens.'}})
            else:
                expected=json.loads((DATA/'manifest.json').read_text())
                if actual!=expected['files'] or observations!=expected['datasets']['macs3']['observations']:raise ValueError('Regeneration differs from frozen manifest')
    m=json.loads((DATA/'manifest.json').read_text());actual=inventory(DATA)
    if actual!=m['files']:raise ValueError('Dataset inventory mismatch: '+', '.join(k for k in sorted(actual.keys()|m['files'].keys()) if actual.get(k)!=m['files'].get(k)))
    if m['sources']!=json.loads(LOCK.read_text())['sources']:raise ValueError('Source lock mismatch')
    for name,info in m['regeneration']['files'].items():
        if ident((ROOT/name).read_bytes())!=info:raise ValueError('Regeneration recipe changed: '+name)
    print(f'Verified {len(actual)} MACS3 fixture/provenance files ({sum(x["bytes"] for x in actual.values())} bytes)')
if __name__=='__main__':main()
