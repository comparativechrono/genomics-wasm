#!/usr/bin/env python3
"""Build pinned Strelka2 somatic programs for native and WebAssembly execution."""
from pathlib import Path
import argparse, hashlib, json, os, shutil, subprocess, tarfile, urllib.request, uuid
ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / 'tools/strelka2'
BUILD = ROOT / 'build/strelka2'
DIST = ROOT / 'dist/strelka2'
DOWNLOADS = ROOT / 'build/downloads'
LOCK = json.loads((TOOL / 'sources.lock.json').read_text())
PROGRAMS = ('strelka2','GetChromDepth','MergeRunStats','bgzip','bgzf_cat','tabix','htsfile')
HTS = 'htslib-1.7-6-g6d2bfb7'

def digest(p):
    with p.open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()

def inputs():
    files = [ROOT/'scripts/build-strelka2.sh', ROOT/'scripts/setup-strelka2-python.py']
    files += sorted(TOOL.rglob('*')) + sorted((ROOT/'tools/strelka2-python').rglob('*'))
    return {str(p.relative_to(ROOT)):digest(p) for p in files if p.is_file()}

def run(args, cwd, env):
    print('+ ' + ' '.join(map(str,args)), flush=True)
    subprocess.run(list(map(str,args)),cwd=cwd,env=env,check=True)

def fetch(record):
    p=DOWNLOADS/record['archive']; p.parent.mkdir(parents=True,exist_ok=True)
    if not p.is_file():
        fallback=DIST/'sources'/record['archive']
        if fallback.is_file(): shutil.copyfile(fallback,p)
        else:
            tmp=p.with_suffix(p.suffix+'.partial')
            with urllib.request.urlopen(record['url'],timeout=180) as r, tmp.open('wb') as f: shutil.copyfileobj(r,f)
            tmp.replace(p)
    if digest(p)!=record['sha256']: raise RuntimeError('Archive checksum mismatch: '+str(p))
    return p

def unpack(archive,dest):
    dest.mkdir(parents=True,exist_ok=True)
    with tarfile.open(archive) as t:
        names={Path(m.name).parts[0] for m in t.getmembers() if m.name and m.name!='.'}
        t.extractall(dest,filter='data')
    if len(names)!=1: raise RuntimeError('Archive must contain one root: '+str(archive))
    return dest/next(iter(names))

def copyglob(src,dest,patterns):
    dest.mkdir(parents=True,exist_ok=True)
    for pattern in patterns:
        for f in sorted(src.glob(pattern)): shutil.copy2(f,dest/f.name)

def copy_python_package(source,cmake,package):
    copyglob(cmake/'src/python/bin',package/'bin',('*.py','*.ini'))
    copyglob(cmake/'src/python/lib/pystage',package/'lib/python',('*.py',))
    copyglob(cmake/'src/python/libexec',package/'libexec',('*.py','*.ini'))
    copyglob(cmake/'redist/pyflow-1.1.20/src',package/'lib/python/pyflow',('*.py',))
    for directory in ('empiricalVariantScoring/models','indelErrorModel/models'):
        copyglob(source/'src/config'/directory,package/'share/config',('*.json',))
    for directory in ('bin','libexec'):
        for f in (package/directory).glob('*.py'): f.chmod(0o755)

def compile_variant(kind,cmake,base_env):
    work=BUILD/kind
    if work.exists(): shutil.rmtree(work)
    work.mkdir(parents=True)
    source=unpack(fetch(LOCK),work/'source')
    for record in LOCK['bundledDependencies']:
        if digest(source/record['bundledPath']) != record['sha256']:
            raise RuntimeError('Bundled dependency checksum mismatch: '+record['archive'])
    if kind != 'native':
        run(['patch','--batch','--fuzz=0','-p1','-i',TOOL/'browser.patch'],source,base_env)
    wasm=kind=='wasm'
    env={**base_env,'CC':'emcc' if wasm else 'cc','CXX':'em++' if wasm else 'c++',
         'AR':'emar' if wasm else 'ar','RANLIB':'emranlib' if wasm else 'ranlib'}
    zsource=unpack(fetch(LOCK['dependencies']['zlib']),work/'zlib')
    prefix=work/'zprefix'
    run(['./configure','--static',f'--prefix={prefix}'],zsource,env)
    run(['make','-j2','install'],zsource,env)
    env.update(CPPFLAGS=f'-I{prefix}/include',LDFLAGS=f'-L{prefix}/lib',LIBRARY_PATH=str(prefix/'lib'))
    build=work/'cmake'
    if kind=='native':
        run([cmake,'-S',source,'-B',build,'-DCMAKE_BUILD_TYPE=Release','-DCMAKE_PARALLEL=2',
             f'-DCMAKE_INSTALL_PREFIX={work}/install','-DCMAKE_DISABLE_FIND_PACKAGE_PythonInterp=TRUE',
             '-DCMAKE_POLICY_VERSION_MINIMUM=3.5',f'-DCMAKE_EXE_LINKER_FLAGS=-L{prefix}/lib',
             f'-DCMAKE_CXX_FLAGS=-I{prefix}/include',f'-DZLIB_LIBRARY={prefix}/lib/libz.a',
             f'-DZLIB_INCLUDE_DIR={prefix}/include'],work,env)
        run([cmake,'--build',build,'--target','strelka2','GetChromDepth','MergeRunStats',
             'strelka_python_buildtime_config','--parallel','2'],work,env)
        package=work/'install-package'
        if package.exists(): shutil.rmtree(package)
        (package/'libexec').mkdir(parents=True)
        copy_python_package(source,build,package)
        for name in PROGRAMS:
            if name in PROGRAMS[:3]: src=build/'src/c++/bin'/name
            elif name=='bgzf_cat': src=build/'redist/bgzf_extras-v0.1'/name
            else: src=build/'redist'/HTS/name
            shutil.copy2(src,package/'libexec'/name)
        # The unchanged Python configurator checks that these upstream tools exist,
        # even though the browser workflow does not expose realigned BAM output.
        for src in (build/'redist/samtools-1.6/samtools',build/'redist/bgzf_extras-v0.1/bgzip9'):
            shutil.copy2(src,package/'libexec'/src.name)
    else:
        deps=work/'deps'; deps.mkdir()
        for record in LOCK['bundledDependencies']:
            if record['archive'].startswith(('boost_','htslib-','rapidjson-','CodeMin-','bgzf_')):
                unpack(source/record['bundledPath'],deps)
        run(['patch','--batch','--fuzz=0','-p1','-i',TOOL/'rapidjson.patch'],deps/'rapidjson-1.1.0',env)
        hts=deps/HTS
        run(['patch','--batch','--fuzz=0','-p1','-i',TOOL/'htslib.patch'],hts,env)
        configure=['emconfigure','./configure','--host=wasm32-unknown-emscripten'] if wasm else ['./configure']
        run([*configure,'--disable-libcurl','--disable-bz2','--disable-lzma'],hts,env)
        run(['make','-j2',f'AR={env["AR"]}',f'RANLIB={env["RANLIB"]}','lib-static'],hts,env)
        command=['emcmake',cmake] if wasm else [cmake]
        run([*command,'-S',TOOL,'-B',build,'-DCMAKE_BUILD_TYPE=Release',f'-DSTRELKA={source}',
             f'-DDEPS={deps}',f'-DZPREFIX={prefix}'],work,env)
        run([cmake,'--build',build,'--parallel','2'],work,env)
        if not wasm:
            package=work/'install-package'
            if package.exists(): shutil.rmtree(package)
            baseline=BUILD/'native/install-package'
            if not baseline.is_dir(): baseline=ROOT/'build/native/strelka2-package'
            shutil.copytree(baseline,package)
            for name in PROGRAMS: shutil.copy2(build/name,package/'libexec'/name)
    if not wasm:
        for f in (package/'libexec').iterdir(): f.chmod(0o755)
        shutil.copy2(package/'libexec/strelka2',work/'strelka2')
    return work

def publish_native(kind):
    base=ROOT/'build'/kind; base.mkdir(parents=True,exist_ok=True)
    staged=base/'strelka2-package.new'
    if staged.exists(): shutil.rmtree(staged)
    shutil.copytree(BUILD/kind/'install-package',staged)
    target=base/'strelka2-package'
    if target.exists(): shutil.rmtree(target)
    staged.rename(target)
    shutil.copy2(BUILD/kind/'strelka2',base/'strelka2')

def finish(work,before):
    if inputs()!=before: raise RuntimeError('Build inputs changed during compilation; rebuild required')
    for kind in ('native','patched-native'):
        if (BUILD/kind/'install-package').is_dir(): publish_native(kind)
    if DIST.exists(): shutil.rmtree(DIST)
    DIST.mkdir(parents=True)
    for name in PROGRAMS:
        for extension in ('.mjs','.wasm'): shutil.copy2(work/'cmake'/(name+extension),DIST/(name+extension))
    source=next((work/'source').iterdir())
    copyglob(source/'src/config/empiricalVariantScoring/models',DIST/'config',('somatic*ScoringModels.json',))
    for record in [LOCK,*LOCK['dependencies'].values()]:
        dest=DIST/'sources'/record['archive'];dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(fetch(record),dest)
    for name in LOCK['requiredNotices']:
        dest=DIST/'notices'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(TOOL/name,dest)
    manifest={'schema':1,'tool':'strelka2','inputs':before,
              'assets':{str(p.relative_to(DIST)):digest(p) for p in sorted(DIST.rglob('*')) if p.is_file()},
              'nativeFiles':{}}
    for kind in ('native','patched-native'):
        base=ROOT/'build'/kind
        paths=[base/'strelka2',*sorted((base/'strelka2-package').rglob('*'))]
        manifest['nativeFiles'][kind]={str(p.relative_to(base)):digest(p) for p in paths if p.is_file()}
    (DIST/'build-manifest.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')

def main():
    global BUILD
    # Directory outputs in upstream CMake can appear complete after interruption.
    # A new work tree prevents interrupted extraction/build state being reused.
    BUILD = BUILD / uuid.uuid4().hex
    print('Build directory: '+str(BUILD), flush=True)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',nargs='?',default='all',choices=('all','native','patched-native','wasm'))
    args=parser.parse_args();before=inputs()
    cmake=shutil.which('cmake')
    if not cmake: raise RuntimeError('CMake 3.15 or newer is required on PATH')
    (BUILD/'tmp').mkdir(parents=True,exist_ok=True)
    env={**os.environ,'TMPDIR':str(BUILD/'tmp'),'SOURCE_DATE_EPOCH':'1767225600','PYTHONDONTWRITEBYTECODE':'1'}
    if args.mode in ('all','wasm'):
        version=subprocess.check_output(['emcc','--version'],env=env,text=True).splitlines()[0]
        if LOCK['emscripten'] not in version: raise RuntimeError('Expected Emscripten '+LOCK['emscripten'])
    for kind in ('native','patched-native','wasm'):
        if args.mode in ('all',kind):
            work=compile_variant(kind,cmake,env)
            if kind=='wasm': finish(work,before)
            elif args.mode!='all': publish_native(kind)
if __name__=='__main__':main()
