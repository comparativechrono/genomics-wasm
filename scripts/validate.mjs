#!/usr/bin/env node
/** Differential tests of real upstream programs, independently of the examples. */
import { readFile, writeFile, mkdir, readdir, rm } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { execFileSync, spawnSync } from 'node:child_process';
import { createServer } from 'node:http';
import { dirname, resolve, join, relative, sep } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { ToolRunner } from '../runtime/index.mjs';
import { runAdditionalCases } from '../validation/additional-cases.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const options = { tool: 'all', engine: 'node', dataset: 'all' };
for (let i = 2; i < process.argv.length; i += 2) {
  const key = process.argv[i].replace(/^--/, '');
  if (!(key in options) || !process.argv[i + 1]) throw new Error('Usage: validate.mjs [--tool bwa|bwa-mem2|freebayes|adapterremoval|seqtk|hisat2|minimap2|all] [--engine node|chromium|firefox|webkit] [--dataset human|yeast|rna-human|rna-yeast|all]');
  options[key] = process.argv[i + 1];
}
if (!['all','bwa','bwa-mem2','freebayes','adapterremoval','seqtk','hisat2','minimap2'].includes(options.tool) || !['node','chromium','firefox','webkit'].includes(options.engine) || !['all','human','yeast','rna-human','rna-yeast'].includes(options.dataset)) throw new Error('Unknown validation option');
const requestedTools = options.tool === 'all' ? ['bwa','bwa-mem2','freebayes','adapterremoval','seqtk','hisat2','minimap2'] : [options.tool];
const tools = options.dataset.startsWith('rna-') ? requestedTools.filter(tool=>['hisat2','minimap2'].includes(tool)) : requestedTools;
if(!tools.length) throw new Error('RNA fixture selection requires hisat2, minimap2, or all');
const datasets = options.dataset === 'all' ? ['human','yeast'] : [options.dataset].filter(name=>!name.startsWith('rna-'));
const reportDir = join(root, 'reports', options.engine);
await mkdir(reportDir, { recursive: true });
const summary = { schema: 1, startedAt: new Date().toISOString(), engine: options.engine, node: process.version, status: 'running', builds: {}, datasets: {}, cases: [] };
const expectations=JSON.parse(await readFile(join(root,'validation','expected.json'),'utf8'));
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
let browser, server, page;

async function startBrowser() {
  const playwright = await import('playwright');
  browser = await playwright[options.engine].launch({ headless: true });
  summary.browserVersion = browser.version();
  server = createServer(async (request, response) => {
    try {
      const url = new URL(request.url, 'http://localhost');
      if (url.pathname === '/') { response.setHeader('Content-Type','text/html'); response.end('<!doctype html><meta charset="utf-8"><title>Validation</title>'); return; }
      const path = resolve(root, '.' + decodeURIComponent(url.pathname));
      if (!path.startsWith(root + sep) || !['runtime/','dist/'].some(prefix => relative(root,path).startsWith(prefix))) { response.writeHead(404).end(); return; }
      response.setHeader('Content-Type', path.endsWith('.wasm') ? 'application/wasm' : 'text/javascript');
      response.end(await readFile(path));
    } catch { response.writeHead(404).end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
}

async function wasm(tool, args, files, outputs = []) {
  if (options.engine === 'node') return new ToolRunner({ moduleUrl: pathToFileURL(join(root,'dist',tool,`${tool}.mjs`)) }).run(args, { files, outputs, timeoutMs: 180000 });
  const result = await page.evaluate(async ({tool,args,files,outputs}) => {
    const { ToolRunner } = await import('/runtime/index.mjs');
    const runner = new ToolRunner({ moduleUrl: new URL(`/dist/${tool}/${tool}.mjs`,location.href) });
    const result = await runner.run(args, { files: files.map(f => ({path:f.path,data:Uint8Array.from(atob(f.data),c=>c.charCodeAt(0))})), outputs, timeoutMs:180000 });
    return {...result, stdout:Array.from(result.stdout),stderr:Array.from(result.stderr),files:result.files.map(f=>({...f,data:Array.from(f.data)}))};
  }, { tool, args, outputs, files: files.map(f=>({path:f.path,data:Buffer.from(f.data).toString('base64')})) });
  return {...result, stdout:Uint8Array.from(result.stdout), stderr:Uint8Array.from(result.stderr),files:result.files.map(f=>({...f,data:Uint8Array.from(f.data)}))};
}

async function native(tool, kind, args, files, outputs = [], name = 'command') {
  const work = join(root,'build','validation',options.engine,tool,name,kind);
  await rm(work,{recursive:true,force:true});
  await mkdir(work,{recursive:true});
  for (const f of files) { const path=join(work,f.path); await mkdir(dirname(path),{recursive:true}); await writeFile(path,f.data); }
  const processResult=spawnSync(join(root,'build',kind,tool),args,{cwd:work,maxBuffer:64*1024*1024,timeout:180000,stdio:['ignore','pipe','pipe']});
  if(processResult.error) throw processResult.error;
  if(typeof processResult.status!=='number') throw new Error(`${tool} terminated by ${processResult.signal}`);
  const {stdout,stderr}=processResult;const exitCode=processResult.status;
  const resultFiles=[];
  const missingOutputs=[];
  for (const path of outputs) {
    try { resultFiles.push({path,data:await readFile(join(work,path))}); } catch(e) { if(e.code!=='ENOENT') throw e; missingOutputs.push(path); }
  }
  return {exitCode,stdout,stderr,files:resultFiles,missingOutputs};
}

async function compare(format, expected, actual, name) {
  const a=join(reportDir,`${name}.native.${format}`),b=join(reportDir,`${name}.actual.${format}`),report=join(reportDir,`${name}.json`);
  await writeFile(a,expected); await writeFile(b,actual);
  // Only FreeBayes QUAL may use this measured, absolute rounding allowance.
  // All calls, genotypes, counts and other numeric fields remain exact.
  const tolerance=format==='vcf'?['--qual-absolute-tolerance','1e-13','--qual-max-magnitude','1e-8']:[];
  try { execFileSync('python3',[join(root,'validation','compare.py'),format,a,b,'--report',report,...tolerance],{maxBuffer:1024*1024}); }
  catch(e) { throw new Error(`Output mismatch in ${name}; see ${relative(root,report)}\n${String(e.stdout??'').slice(0,1500)}`); }
}
function success(result,label) {
  if (result.exitCode !== 0 || result.missingOutputs.length) throw new Error(`${label}: exit ${result.exitCode}, missing ${result.missingOutputs.join(', ')}\n${Buffer.from(result.stderr).toString().slice(0,2000)}`);
}
async function caseRun(tool,dataset,name,args,files,format) {
  const id=`${tool}-${dataset}-${name}`;
  const record={id,args,status:'running'};summary.cases.push(record);
  const original=await native(tool,'native',args,files,[],id);
  const patched=await native(tool,'patched-native',args,files,[],id);
  const port=await wasm(tool,args,files);
  for(const [kind,result] of [['native',original],['patched-native',patched],['wasm',port]]) {
    await writeFile(join(reportDir,`${id}.${kind}.stderr.txt`),result.stderr);
    await writeFile(join(reportDir,`${id}.${kind}.${format}`),result.stdout);
  }
  success(original,id+' native');success(patched,id+' patched-native');success(port,id+' wasm');
  const lines=Buffer.from(original.stdout).toString('utf8').split('\n').filter(line=>line && !line.startsWith(format==='sam'?'@':'#'));
  if(format==='sam') {
    const primary=lines.filter(line=>(Number(line.split('\t')[1])&0x900)===0).length;
    const expected=name==='empty'?0:expectations[tool][dataset].pairs*(name==='single'?1:2);
    if(primary!==expected) throw new Error(`${id}: expected ${expected} primary records, found ${primary}`);
    record.primaryRecords=primary;
  } else {
    const expected=expectations.freebayes[dataset][name==='repeat'?'default':name];
    if(lines.length!==expected) throw new Error(`${id}: expected ${expected} variant records, found ${lines.length}`);
  }
  record.records=lines.length;
  await compare(format,original.stdout,patched.stdout,id+'-patched');
  await compare(format,original.stdout,port.stdout,id+'-wasm');
  record.status='passed';record.nativeSha256=hash(original.stdout);record.wasmSha256=hash(port.stdout);
  console.log(`PASS ${options.engine} ${id}`);
  return {original,port};
}
async function loadDataset(name) {
  const directory=join(root,'datasets',name);
  const names=(await readdir(directory)).filter(n=>/\.(fa|fai|fastq|bam|bai|tsv|bed)$/.test(n)).sort();
  const files=await Promise.all(names.map(async path=>({path,data:await readFile(join(directory,path))})));
  summary.datasets[name]=Object.fromEntries(files.map(f=>[f.path,hash(f.data)]));
  return files;
}

try {
  execFileSync('python3',[join(root,'scripts','fetch-test-data.py'),'--verify'],{cwd:root,stdio:'pipe'});
  if(tools.some(tool=>tool==='hisat2'||tool==='minimap2') || options.dataset.startsWith('rna-')) execFileSync('python3',[join(root,'scripts','fetch-rna-test-data.py'),'--verify'],{cwd:root,stdio:'pipe'});
  if(options.engine!=='node') await startBrowser();
  for(const tool of tools) summary.builds[tool]=Object.fromEntries(await Promise.all(['native','patched-native','wasm'].map(async kind=>[kind,hash(await readFile(kind==='wasm'?join(root,'dist',tool,`${tool}.wasm`):join(root,'build',kind,tool)))])));
  if(tools.includes('hisat2')) {
    summary.builds.hisat2.nativeComponents = Object.fromEntries(await Promise.all(['align','build','inspect'].map(async command=> {
      const path=`hisat2-bin/hisat2-${command}-s`;
      return [path,hash(await readFile(join(root,'build','native',path)))];
    })));
  }
  for(const dataset of datasets) {
    const files=await loadDataset(dataset);
    for(const tool of tools.filter(name=>name==='bwa'||name==='bwa-mem2')) {
      const reference=files.filter(f=>f.path==='reference.fa');
      const suffixes=tool==='bwa'?['amb','ann','bwt','pac','sa']:['0123','amb','ann','bwt.2bit.64','pac'];
      const indexNames=suffixes.map(suffix=>`reference.fa.${suffix}`);
      const indexArgs=['index','reference.fa'];
      const original=await native(tool,'native',indexArgs,reference,indexNames,`${dataset}-index`);
      const patched=await native(tool,'patched-native',indexArgs,reference,indexNames,`${dataset}-index`);
      const port=await wasm(tool,indexArgs,reference,indexNames);
      const rename=f=>({path:f.path.replace(/^\/work\//,''),data:f.data});
      for(const [kind,result] of [['native',original],['patched-native',patched],['wasm',port]]) {
        await writeFile(join(reportDir,`${tool}-${dataset}-index.${kind}.stdout.txt`),result.stdout);
        await writeFile(join(reportDir,`${tool}-${dataset}-index.${kind}.stderr.txt`),result.stderr);
        for(const f of result.files.map(rename)) await writeFile(join(reportDir,`${tool}-${dataset}-${kind}-${f.path}`),f.data);
        success(result,`${tool} ${dataset} index ${kind}`);
      }
      const nativeIndexes=original.files.map(rename),wasmIndexes=port.files.map(rename);
      for(let i=0;i<indexNames.length;i++) {
        if(hash(original.files[i].data)!==hash(patched.files[i].data)||hash(original.files[i].data)!==hash(port.files[i].data)) throw new Error(`${tool} ${dataset} index differs: ${indexNames[i]}`);
      }
      summary.cases.push({id:`${tool}-${dataset}-index`,status:'passed',sha256:Object.fromEntries(nativeIndexes.map(f=>[f.path,hash(f.data)]))});
      const input=[...files.filter(f=>f.path==='reference.fa'||f.path.endsWith('.fastq')),...nativeIndexes];
      const mem=['mem','-t','1','-K','10000000'];
      const paired=[...mem,'reference.fa','reads_1.fastq','reads_2.fastq'];
      const baseline=await caseRun(tool,dataset,'paired',paired,input,'sam');
      await caseRun(tool,dataset,'single',[...mem,'reference.fa','reads_1.fastq'],input,'sam');
      await caseRun(tool,dataset,'parameters',[...mem,'-k','17','-T','20','reference.fa','reads_1.fastq','reads_2.fastq'],input,'sam');
      if(tool==='bwa-mem2') await caseRun(tool,dataset,'high-scores',[...mem,'-A','3','reference.fa','reads_1.fastq','reads_2.fastq'],input,'sam');
      await caseRun(tool,dataset,'small-batches',['mem','-t','1','-K','10000','reference.fa','reads_1.fastq','reads_2.fastq'],input,'sam');
      if(dataset==='human'||tool==='bwa-mem2') await caseRun(tool,dataset,'empty',paired,input.map(f=>f.path.endsWith('.fastq')?{...f,data:new Uint8Array()}:f),'sam');
      const crossInput=[...input.filter(f=>!indexNames.includes(f.path)),...wasmIndexes];
      const crossNative=await native(tool,'native',paired,crossInput,[],`${dataset}-cross-index`);
      const crossWasm=await wasm(tool,paired,crossInput);
      success(crossNative,'native consuming WASM index');success(crossWasm,'WASM consuming WASM index');
      await compare('sam',baseline.original.stdout,crossNative.stdout,`${tool}-${dataset}-cross-native`);
      await compare('sam',baseline.original.stdout,crossWasm.stdout,`${tool}-${dataset}-cross-wasm`);
      summary.cases.push({id:`${tool}-${dataset}-cross-index`,status:'passed'});
      const repeated=await wasm(tool,paired,input);
      success(repeated,`repeated ${tool} command`);
      await compare('sam',baseline.original.stdout,repeated.stdout,`${tool}-${dataset}-repeat`);
      summary.cases.push({id:`${tool}-${dataset}-repeat`,status:'passed'});
    }
    if(tools.includes('freebayes')) {
      const input=files.filter(f=>!f.path.endsWith('.fastq'));
      for(const [name,args] of [
        ['default',['-f','reference.fa','input.bam']],
        ['haploid',['-f','reference.fa','-p','1','input.bam']],
        ['quality',['-f','reference.fa','-q','20','-m','20','input.bam']],
      ]) await caseRun('freebayes',dataset,name,args,input,'vcf');
      await caseRun('freebayes',dataset,'repeat',['-f','reference.fa','input.bam'],input,'vcf');
    }
  }
  await runAdditionalCases({ root, options, tools, reportDir, summary, expectations, hash, wasm, native, compare, success, loadDataset });
  for(const tool of tools.filter(name=>['bwa','bwa-mem2','freebayes'].includes(name))) {
    const args=tool==='bwa'||tool==='bwa-mem2'?['mem','missing.fa','missing.fastq']:['-f','missing.fa','missing.bam'];
    const nativeResult=await native(tool,'native',args,[],[],'missing-input');
    const port=await wasm(tool,args,[]);
    const results=[['native',nativeResult],['wasm',port]];
    if(tool==='bwa-mem2') results.push(['patched-native',await native(tool,'patched-native',args,[],[],'missing-input')]);
    for(const [kind,result] of results) {
      await writeFile(join(reportDir,`${tool}-missing-input.${kind}.stdout.txt`),result.stdout);
      await writeFile(join(reportDir,`${tool}-missing-input.${kind}.stderr.txt`),result.stderr);
      if(result.exitCode===0) throw new Error(`${tool} ${kind} accepted missing inputs`);
      if(result.exitCode!==nativeResult.exitCode) throw new Error(`${tool} missing-input exit codes differ: native ${nativeResult.exitCode} vs ${kind} ${result.exitCode}`);
    }
    summary.cases.push({id:`${tool}-missing-input`,status:'passed',exitCode:port.exitCode});
    if(tool==='bwa-mem2') {
      // The port intentionally supports one thread; native upstream permits more.
      // Check the restriction separately rather than claim native equivalence here.
      const id='bwa-mem2-unsupported-threads';
      const args=['mem','-t','2','missing.fa','missing.fastq'];
      const results=[
        ['patched-native',await native(tool,'patched-native',args,[],[],id)],
        ['wasm',await wasm(tool,args,[])],
      ];
      for(const [kind,result] of results) {
        await writeFile(join(reportDir,`${id}.${kind}.stdout.txt`),result.stdout);
        await writeFile(join(reportDir,`${id}.${kind}.stderr.txt`),result.stderr);
        if(result.exitCode!==2||!Buffer.from(result.stderr).toString('utf8').includes('supports only -t 1')) throw new Error(`${id} ${kind}: expected exit 2 and an explicit single-thread diagnostic`);
      }
      summary.cases.push({id,args,status:'passed',scope:'port restriction',exitCode:2});
    }
  }
  summary.status='passed';
} catch(error) {
  summary.status='failed';summary.error=error.stack;
  console.error(error.stack);process.exitCode=1;
} finally {
  summary.finishedAt=new Date().toISOString();
  await writeFile(join(reportDir,`summary-${options.tool}-${options.dataset}.json`),JSON.stringify(summary,null,2)+'\n');
  if(browser) await browser.close();
  if(server) await new Promise(resolve=>server.close(resolve));
}
