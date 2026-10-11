#!/usr/bin/env python3
"""Build Bracken's unchanged Python and serial native/WebAssembly helper."""
from pathlib import Path
import argparse, hashlib, json, os, shutil, subprocess, tarfile, urllib.request
ROOT=Path(__file__).resolve().parents[2]; TOOL=ROOT/'tools/bracken'; BUILD=ROOT/'build/bracken'; DIST=ROOT/'dist/bracken'; DOWNLOADS=ROOT/'build/downloads'
LOCK=json.loads((TOOL/'sources.lock.json').read_text())
def digest(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def hashes(base):return {str(p.relative_to(base)):digest(p) for p in sorted(base.rglob('*')) if p.is_file() and '.rsync-tmp' not in p.parts}
def inputs():return {str(p.relative_to(ROOT)):digest(p) for p in [ROOT/'scripts/build-bracken.sh',*sorted(TOOL.rglob('*'))] if p.is_file() and '.rsync-tmp' not in p.parts and '__pycache__' not in p.parts and p.suffix != '.pyc'}
def run(args,cwd=None):
    print('+ '+' '.join(map(str,args)),flush=True);subprocess.run(list(map(str,args)),cwd=cwd,check=True)
def fetch(rec):
    p=DOWNLOADS/rec['archive'];p.parent.mkdir(parents=True,exist_ok=True)
    if not p.exists():
        fallback=DIST/'sources'/rec['archive']
        if fallback.exists():shutil.copy2(fallback,p)
        else:
            tmp=p.with_suffix(p.suffix+'.partial')
            with urllib.request.urlopen(rec['url'],timeout=180) as src,tmp.open('wb') as out:shutil.copyfileobj(src,out)
            tmp.replace(p)
    if digest(p)!=rec['sha256']:raise RuntimeError('Checksum mismatch: '+str(p))
    return p
def unpack(archive,dest):
    dest.mkdir(parents=True,exist_ok=True)
    with tarfile.open(archive) as f:f.extractall(dest,filter='data')
    roots=list(dest.iterdir())
    if len(roots)!=1:raise RuntimeError('Expected one archive root')
    return roots[0]
def python_info():
    candidate=os.environ.get('BRACKEN_PYTHON') or shutil.which('python3.14')
    if not candidate:raise RuntimeError('Set BRACKEN_PYTHON to CPython '+LOCK['python'])
    p=Path(candidate).resolve()
    info=json.loads(subprocess.check_output([str(p),'-I','-S','-B','-c','import json,sys,platform;print(json.dumps({"version":platform.python_version(),"implementation":platform.python_implementation()}))'],text=True))
    if info!={'version':LOCK['python'],'implementation':'CPython'}:raise RuntimeError('Expected CPython '+LOCK['python']+': '+str(info))
    info['executableSha256']=digest(p)
    return p,info

def compile_variant(kind):
    work=BUILD/kind
    if work.exists():shutil.rmtree(work)
    source=unpack(fetch(LOCK),work/'source')
    if kind!='native':run(['patch','--batch','--fuzz=0','-p1','-i',TOOL/'browser.patch'],source)
    src=source/'src'; sources=[src/name for name in ('kmer2read_distr.cpp','ctime.cpp','taxonomy.cpp','kraken_processing.cpp')]
    if kind=='native':run(['make','-j2'],src)
    else:
        flags=['-O3','-std=c++11','-DBRACKEN_SERIAL']
        if kind=='wasm':
            flags+=['-fexceptions','-sDISABLE_EXCEPTION_CATCHING=0','-sMODULARIZE=1','-sEXPORT_ES6=1','-sEXPORT_NAME=createBrackenModule','-sENVIRONMENT=web,worker,node','-sFORCE_FILESYSTEM=1','-sINVOKE_RUN=0','-sEXIT_RUNTIME=1','-sALLOW_MEMORY_GROWTH=1','-sINITIAL_MEMORY=67108864','-sMAXIMUM_MEMORY=2147483648','-sEXPORTED_RUNTIME_METHODS=["FS","callMain"]']
        run(['em++' if kind=='wasm' else 'c++',*flags,*sources,'-o',work/('kmer2read_distr.mjs' if kind=='wasm' else 'kmer2read_distr')])
        if kind!='wasm':shutil.copy2(work/'kmer2read_distr',src/'kmer2read_distr')
    if kind!='wasm':
        base=ROOT/'build'/kind;base.mkdir(parents=True,exist_ok=True);package=base/'bracken-package'
        if package.exists():shutil.rmtree(package)
        shutil.copytree(source,package,ignore=shutil.ignore_patterns('*.o', '.rsync-tmp'))
        shutil.copy2(src/'kmer2read_distr',base/'kmer2read_distr')
        for name in ('bracken','bracken-build'):
            launcher=base/name
            launcher.write_text('#!/usr/bin/env bash\nset -euo pipefail\nroot=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)\nexec bash "$root/bracken-package/'+name+'" "$@"\n');launcher.chmod(0o755)
    return work,source

def finish(work,source,before,pyinfo):
    if inputs()!=before:raise RuntimeError('Build inputs changed; rebuild required')
    records=[LOCK,*LOCK['dependencies'].values()]
    archives=[(rec,fetch(rec)) for rec in records]
    corework=BUILD/'pyodide-runtime'
    if corework.exists():shutil.rmtree(corework)
    core=unpack(fetch(LOCK['dependencies']['pyodide-core']),corework)
    wswork=BUILD/'ws-runtime'
    if wswork.exists():shutil.rmtree(wswork)
    ws=unpack(fetch(LOCK['dependencies']['runtime-ws']),wswork)
    if DIST.exists():shutil.rmtree(DIST)
    (DIST/'pyodide').mkdir(parents=True);(DIST/'python').mkdir()
    for name in ('pyodide.mjs','pyodide.asm.mjs','pyodide.asm.wasm','python_stdlib.zip','pyodide-lock.json'):shutil.copy2(core/name,DIST/'pyodide'/name)
    shutil.copytree(ws,DIST/'node_modules/ws')
    for ext in ('.mjs','.wasm'):shutil.copy2(work/('kmer2read_distr'+ext),DIST/('kmer2read_distr'+ext))
    scripts={}
    for name in ('est_abundance.py','generate_kmer_distribution.py'):
        p=DIST/'python'/name;shutil.copy2(source/'src'/name,p);scripts[name]={'path':'python/'+name,'sha256':digest(p)}
    manifest={'schema':1,'tool':'bracken','version':LOCK['version'],'upstreamVersionText':'3.0.1','pyodide':{'version':LOCK['pyodide'],'python':LOCK['python'],'path':'pyodide/'},'scripts':scripts}
    (DIST/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    for rec,src in archives:
        p=DIST/'sources'/rec['archive'];p.parent.mkdir(exist_ok=True,parents=True);shutil.copy2(src,p)
    for name in LOCK['requiredNotices']:
        p=DIST/'notices'/name;p.parent.mkdir(exist_ok=True,parents=True);shutil.copy2(TOOL/name,p)
    native={};environments={}
    for kind in ('native','patched-native'):
        base=ROOT/'build'/kind
        paths=[base/'bracken',base/'bracken-build',base/'kmer2read_distr',*sorted((base/'bracken-package').rglob('*'))]
        native[kind]={str(p.relative_to(base)):digest(p) for p in paths if p.is_file() and '.rsync-tmp' not in p.parts}
        environments[kind]={'python':pyinfo,'scripts':{name:digest(base/'bracken-package/src'/name) for name in scripts}}
    build={'schema':1,'tool':'bracken','inputs':before,'assets':hashes(DIST),'nativeFiles':native,'nativeEnvironments':environments}
    (DIST/'build-manifest.json').write_text(json.dumps(build,indent=2,sort_keys=True)+'\n')

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('mode',nargs='?',default='all',choices=('all','native','patched-native','wasm'));args=ap.parse_args()
    before=inputs();_,info=python_info()
    os.environ['PYTHONDONTWRITEBYTECODE']='1'
    if args.mode in ('all','wasm'):
        version=subprocess.check_output(['emcc','--version'],text=True).splitlines()[0]
        if LOCK['emscripten'] not in version:raise RuntimeError('Expected Emscripten '+LOCK['emscripten'])
    for kind in ('native','patched-native','wasm'):
        if args.mode in ('all',kind):
            work,source=compile_variant(kind)
            if kind=='wasm':finish(work,source,before,info)
if __name__=='__main__':main()
