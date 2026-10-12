#!/usr/bin/env node
/** Compare unchanged native Prodigal/HMMER programs with their browser ports. */
import {readFile,writeFile,mkdir,readdir,rm,open,rename} from 'node:fs/promises';
import {createHash,randomUUID} from 'node:crypto';
import {spawnSync,execFileSync} from 'node:child_process';
import {createServer} from 'node:http';
import {dirname,resolve,join,relative,sep} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {workspacePath} from '../runtime/index.mjs';

const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const options={engine:'node',tool:'all',dataset:'all'};
const usage='validate-annotation.mjs [--engine node|chromium|firefox|webkit] [--tool prodigal|hmmer|all] [--dataset prodigal|hmmer|all]';
for(let i=2;i<process.argv.length;i+=2){const key=process.argv[i].replace(/^--/,'');if(!(key in options)||!process.argv[i+1])throw new Error(usage);options[key]=process.argv[i+1];}
if(!['node','chromium','firefox','webkit'].includes(options.engine)||!['all','prodigal','hmmer'].includes(options.tool))throw new Error(usage);
const plan=JSON.parse(await readFile(join(root,'validation/annotation/cases.json'),'utf8'));
if(options.dataset!=='all'&&!plan.datasets.includes(options.dataset))throw new Error(usage);
const tools=options.tool==='all'?Object.keys(plan.tools).filter(tool=>options.dataset==='all'||plan.tools[tool].datasets.includes(options.dataset)):[options.tool];
const requestedDatasets=options.dataset==='all'?plan.datasets:[options.dataset];
const runId=randomUUID(),timeoutMs=600000;
const publishedDir=join(root,'reports/annotation',options.engine),reportDir=join(publishedDir,runId);
await mkdir(reportDir,{recursive:true});
const hash=data=>createHash('sha256').update(data).digest('hex');
const normal=file=>({...file,path:workspacePath(file.path).slice(6)});
const same=(a,b)=>JSON.stringify(Object.entries(a).sort())===JSON.stringify(Object.entries(b).sort());
const applies=(test,dataset)=>!test.datasets||test.datasets.includes(dataset);
const caseId=(tool,dataset,test)=>`${tool}-${dataset}-${test.id}`;
const summaries=[];
let browser,server,page,manifest;
function log(message){console.log(`[${new Date().toISOString()}] ${options.engine} ${message}`);}

async function tree(base,prefix=''){
  const result={};
  for(const entry of (await readdir(join(base,prefix),{withFileTypes:true})).sort((a,b)=>a.name.localeCompare(b.name))){
    const path=prefix+entry.name;
    if(entry.isDirectory())Object.assign(result,await tree(base,path+'/'));
    else if(entry.isFile())result[path]=hash(await readFile(join(base,path)));
    else throw new Error(`Non-regular execution asset: ${join(base,path)}`);
  }
  return result;
}
async function startBrowser(){
  const playwright=await import('playwright');browser=await playwright[options.engine].launch({headless:true});
  server=createServer(async(request,response)=>{
    try{
      const url=new URL(request.url,'http://localhost');
      if(url.pathname==='/'){response.setHeader('Content-Type','text/html');response.end('<!doctype html><meta charset="utf-8"><title>Annotation validation</title>');return;}
      const path=resolve(root,'.'+decodeURIComponent(url.pathname));
      if(!path.startsWith(root+sep)||!['runtime/','dist/'].some(prefix=>relative(root,path).startsWith(prefix))){response.writeHead(404).end();return;}
      response.setHeader('Content-Type',path.endsWith('.wasm')?'application/wasm':/\.(mjs|js)$/.test(path)?'text/javascript':path.endsWith('.json')?'application/json':'application/octet-stream');
      response.end(await readFile(path));
    }catch{response.writeHead(404).end();}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));page=await browser.newPage();
  page.on('console',message=>log(`browser ${message.type()}: ${message.text()}`));page.on('pageerror',error=>log(`browser error: ${error.message}`));
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  if(await page.evaluate(()=>crossOriginIsolated))throw new Error('Annotation browser validation must use ordinary hosting without cross-origin isolation');
}
async function wasm(runner,args,files,outputs,stdin){
  if(options.engine==='node'){
    const module=await import(`../runtime/${runner}.mjs`),Class=module[runner==='prodigal'?'ProdigalRunner':'HmmerRunner'];
    return new Class({baseUrl:pathToFileURL(join(root,`dist/${runner}/`))}).run(args,{files,outputs,stdin,timeoutMs});
  }
  const result=await page.evaluate(async({runner,args,files,outputs,stdin,timeoutMs})=>{
    const module=await import(`/runtime/${runner}.mjs`),Class=module[runner==='prodigal'?'ProdigalRunner':'HmmerRunner'];
    const decode=value=>Uint8Array.from(atob(value),c=>c.charCodeAt(0));
    const encode=bytes=>{let value='';for(let i=0;i<bytes.length;i+=32768)value+=String.fromCharCode(...bytes.subarray(i,i+32768));return btoa(value);};
    const result=await new Class({baseUrl:new URL(`/dist/${runner}/`,location.href)}).run(args,{files:files.map(file=>({path:file.path,data:decode(file.data)})),outputs,stdin:stdin===null?undefined:decode(stdin),timeoutMs});
    return {...result,stdout:encode(result.stdout),stderr:encode(result.stderr),files:result.files.map(file=>({path:file.path,data:encode(file.data)}))};
  },{runner,args,outputs,timeoutMs,stdin:stdin===undefined?null:Buffer.from(stdin).toString('base64'),files:files.map(file=>({path:file.path,data:Buffer.from(file.data).toString('base64')}))});
  return {...result,stdout:Buffer.from(result.stdout,'base64'),stderr:Buffer.from(result.stderr,'base64'),files:result.files.map(file=>({path:file.path,data:Buffer.from(file.data,'base64')}))};
}
async function native(kind,test,files,outputs,id,stdin){
  const work=join(root,'build/annotation-validation',runId,options.engine,id,kind);await mkdir(work,{recursive:true});
  for(const file of files){const path=join(work,normal(file).path);await mkdir(dirname(path),{recursive:true});await writeFile(path,file.data);}
  const command=test.runner==='prodigal'?join(root,'build',kind,'prodigal'):join(root,'build',kind,'hmmer',test.args[0]);
  const args=test.runner==='prodigal'?test.args:test.args.slice(1);
  const env={...process.env,LC_ALL:'C',TZ:'UTC'};
  // A shell `< input.fna` redirection is seekable, which Prodigal's original
  // single-genome training pass requires. The browser wrapper materializes the
  // same explicitly supplied bytes in MEMFS for this upstream rewind contract.
  const inputFile=stdin===undefined?null:await open(join(work,test.stdinFile),'r');
  let result;
  try{result=spawnSync(command,args,{cwd:work,env,stdio:[inputFile?.fd??'ignore','pipe','pipe'],timeout:timeoutMs,maxBuffer:128*1024*1024});}
  finally{await inputFile?.close();}
  if(result.error)throw result.error;if(!Number.isInteger(result.status)||result.signal)throw new Error(`${id} ${kind} terminated by ${result.signal}`);
  const answer={exitCode:result.status,signal:null,stdout:result.stdout,stderr:result.stderr,files:[],missingOutputs:[],command:{program:relative(root,command),args},work};
  for(const path of outputs){try{answer.files.push({path,data:await readFile(join(work,path))});}catch(error){if(error.code!=='ENOENT')throw error;answer.missingOutputs.push(path);}}
  return answer;
}
async function run(kind,test,files,id,stdin){
  const started=Date.now();log(`${id} ${kind} starting`);
  const outputs=test.outputs.filter(path=>path!==test.stdoutFile);
  const result=kind==='wasm'?await wasm(test.runner,test.args,files,outputs,stdin):await native(kind,test,files,outputs,id,stdin);
  if(!Number.isInteger(result.exitCode)||result.signal)throw new Error(`${id} ${kind}: no normal exit status`);
  if(test.stdoutFile)result.files.push({path:test.stdoutFile,data:result.stdout});
  const directory=join(reportDir,id,kind);await mkdir(directory,{recursive:true});
  await writeFile(join(directory,'stdout.txt'),result.stdout);await writeFile(join(directory,'stderr.txt'),result.stderr);
  for(const file of result.files.map(normal)){const path=join(directory,file.path);await mkdir(dirname(path),{recursive:true});await writeFile(path,file.data);}
  if(result.workflow)await writeFile(join(directory,'workflow.json'),JSON.stringify(result.workflow,null,2)+'\n');
  const execution={inputs:Object.fromEntries(files.map(file=>[normal(file).path,hash(file.data)])),stdin:stdin===undefined?null:{sha256:hash(stdin),bytes:stdin.length,transport:kind==='wasm'?'mounted-input-file':'seekable-file-redirection'},exitCode:result.exitCode,canonicalExitCode:result.exitCode<0?result.exitCode&255:result.exitCode,signal:null,missingOutputs:result.missingOutputs,seconds:(Date.now()-started)/1000,stdout:hash(result.stdout),stderr:hash(result.stderr),files:Object.fromEntries(result.files.map(normal).map(file=>[file.path,hash(file.data)])),fileBytes:Object.fromEntries(result.files.map(normal).map(file=>[file.path,file.data.length]))};
  if(result.command)execution.command=result.command;
  if(result.workflow)execution.workflow=result.workflow;
  await writeFile(join(directory,'command-result.json'),JSON.stringify(execution,null,2)+'\n');
  log(`${id} ${kind} exit ${result.exitCode} after ${execution.seconds.toFixed(2)} seconds`);
  return {result,directory,execution,inputs:files};
}
async function compare(tool,dataset,test,expected,actual,id,kind){
  const path=join(reportDir,id,`compare-${kind}.json`);
  try{execFileSync('python3',['-B',join(root,'validation/annotation/compare.py'),expected.directory,actual.directory,'--tool',tool,'--dataset',dataset,'--case',test.id,'--actual-target',kind==='native-contract'?'native':kind,'--manifest',join(root,'datasets/annotation/manifest.json'),'--report',path],{env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'},maxBuffer:16*1024*1024});}
  catch(error){throw new Error(`${id} ${kind}: comparison failed\n${String(error.stdout??'').slice(0,5000)}`);}
  const comparison=JSON.parse(await readFile(path,'utf8'));
  // The comparator must have read precisely the bytes captured from execution.
  // Complete semantic equality alone cannot prove that if another process has
  // overwritten a report file, so bind both raw hashes and byte counts here.
  for(const [side,value] of [['expected',expected],['actual',actual]]){
    const hashes=Object.fromEntries(test.artifacts.map(artifact=>[artifact.path,value.execution.files[artifact.path]]));
    const sizes=Object.fromEntries(test.artifacts.map(artifact=>[artifact.path,value.execution.fileBytes[artifact.path]]));
    if(!same(comparison[`${side}Files`],hashes)||!same(comparison[`${side}FileBytes`],sizes))throw new Error(`${id} ${kind}: comparator ${side} inputs differ from captured execution bytes`);
  }
  return comparison;
}
async function executeCase(summary,tool,dataset,test,baseFiles,cache){
  const id=caseId(tool,dataset,test),record={id,tool,dataset,case:test.id,kind:test.kind,runner:test.runner,args:test.args,status:'running',executions:{},comparisons:{}};
  await rm(join(reportDir,id),{recursive:true,force:true});summary.cases.push(record);
  const results={};
  for(const kind of test.executions){
    const dependencies=(test.inputsFrom??[]).map(input=>{
      const sourceTarget=input.target??kind,source=cache.get(input.case)?.results[sourceTarget]?.result.files.map(normal).find(file=>file.path===input.path);
      if(!source)throw new Error(`${id}: missing ${sourceTarget} dependency ${input.case}/${input.path}`);
      let data=source.data;
      const provenance={sourceSha256:hash(data),sourceBytes:data.length};
      if(input.transform){
        if(input.transform!=='remove-final-newline'||data.at(-1)!==10)throw new Error('Unsupported dependency transform or missing terminal LF');
        data=data.subarray(0,data.length-1);provenance.removedFinalByte=10;
      }
      return {...input,sourceTarget,data,...provenance};
    });
    const map=new Map(baseFiles.map(file=>[file.path,file]));
    for(const input of dependencies)map.set(input.as,{path:input.as,data:input.data});
    for(const [path,text] of Object.entries(test.extraFiles??{}))map.set(path,{path,data:Buffer.from(text)});
    for(const path of test.omitInputs??[])map.delete(path);
    const inputs=[...map.values()],stdin=test.stdinFile?map.get(test.stdinFile)?.data:undefined;
    if(test.stdinFile&&stdin===undefined)throw new Error('Missing stdin fixture '+test.stdinFile);
    const value=await run(kind,test,inputs,id,stdin);results[kind]=value;record.executions[kind]=value.execution;
    value.execution.inputsFrom=dependencies.map(({data,sourceTarget,...input})=>({...input,sourceTarget,sha256:hash(data),bytes:data.length}));
    for(const input of dependencies){const path=join(value.directory,'inputs',input.as);await mkdir(dirname(path),{recursive:true});await writeFile(path,input.data);}
    if(['failure','restriction'].includes(test.kind)){
      const text=Buffer.from(value.result.stdout).toString()+Buffer.from(value.result.stderr).toString();
      if(value.result.exitCode===0||!test.diagnosticRegex||!new RegExp(test.diagnosticRegex,'i').test(text))throw new Error(`${id} ${kind}: expected clean failure and required diagnostic; exit ${value.result.exitCode}: ${text.slice(-3000)}`);
      value.execution.diagnosticMatched=true;
      if(test.expectedExitCode!==undefined&&value.execution.canonicalExitCode!==test.expectedExitCode)throw new Error(`${id}: unexpected failure status`);
    }else if(value.result.exitCode!==0||value.result.missingOutputs.length)throw new Error(`${id} ${kind}: exit ${value.result.exitCode}, missing ${value.result.missingOutputs.join(', ')}\n${Buffer.from(value.result.stderr).toString().slice(-5000)}`);
  }
  if(['failure','restriction'].includes(test.kind)){
    if(new Set(Object.values(record.executions).map(execution=>execution.canonicalExitCode)).size!==1)throw new Error(`${id}: failure exit codes differ`);
  }else{
    const baseline=test.kind==='repeat'?cache.get(test.baseline):null,expected=baseline?baseline.results.native:results.native;
    if(!expected)throw new Error(`${id}: missing unchanged native oracle`);
    for(const kind of test.comparisons){const actual=kind==='native-contract'?results.native:results[kind];record.comparisons[kind]=await compare(tool,dataset,test,expected,actual,id,kind);if(!record.comparisons[kind].equal)throw new Error(`${id}: unequal ${kind}`);}
    if(test.effect||test.equivalentTo){
      const other=cache.get(test.effect??test.equivalentTo);if(!other)throw new Error(`${id}: missing option baseline`);
      const before=other.record.comparisons['native-contract'].biologicalDigest,after=record.comparisons['native-contract'].biologicalDigest;
      record.optionEffect={baseline:test.effect??test.equivalentTo,before,after,changed:before!==after,expected:test.effect?'changed':'equivalent'};
      if(test.effect?before===after:before!==after)throw new Error(`${id}: native option effect was ${before===after?'unchanged':'changed'}`);
    }
  }
  record.status='passed';log(`PASS ${id}`);return {record,results};
}
async function identity(summary,tool){
  const dist=join(root,'dist',tool),raw=await readFile(join(dist,'build-manifest.json')),build=JSON.parse(raw),assets=await tree(dist);delete assets['build-manifest.json'];
  if(!same(assets,build.assets))throw new Error(`${tool}: dist asset inventory differs from build manifest`);
  if(!build.inputs||!Object.keys(build.inputs).length)throw new Error(`${tool}: build inputs absent`);
  for(const [path,checksum] of Object.entries(build.inputs))if(hash(await readFile(join(root,path)))!==checksum)throw new Error('Changed build input: '+path);
  for(const kind of ['native','patched-native']){
    const expected=build.nativeFiles?.[kind];
    if(!expected||!Object.keys(expected).length)throw new Error(`${tool}: native executable identity absent`);
    const actual=tool==='hmmer'?Object.fromEntries(Object.entries(await tree(join(root,'build',kind,'hmmer'))).map(([path,checksum])=>[`hmmer/${path}`,checksum])):{prodigal:hash(await readFile(join(root,'build',kind,'prodigal')))};
    if(!same(actual,expected))throw new Error(`${tool}: native execution environment changed: ${kind}`);
  }
  summary.builds[tool]={assets,buildManifest:hash(raw),nativeFiles:build.nativeFiles,inputs:build.inputs};
}
async function fileIdentity(paths){return Object.fromEntries(await Promise.all(paths.map(async path=>[path,hash(await readFile(join(root,path)))])));}
async function validateTool(tool){
  const datasets=requestedDatasets.filter(dataset=>plan.tools[tool].datasets.includes(dataset));
  if(!datasets.length)throw new Error(`${tool}: dataset not applicable`);
  const summary={schema:1,tool,runId,reportDirectory:runId,engine:options.engine,node:process.version,python:execFileSync('python3',['--version'],{encoding:'utf8'}).trim(),startedAt:new Date().toISOString(),status:'running',builds:{},datasets:{},cases:[],expectedCases:datasets.flatMap(dataset=>plan.tools[tool].cases.filter(test=>applies(test,dataset)).map(test=>caseId(tool,dataset,test)))};
  if(browser){summary.browserVersion=browser.version();summary.crossOriginIsolated=false;}summaries.push(summary);
  try{
    const raw=await readFile(join(root,'datasets/annotation/manifest.json'));manifest=JSON.parse(raw);summary.manifestSha256=hash(raw);
    for(const [path,spec] of Object.entries(manifest.files)){const data=await readFile(join(root,'datasets/annotation',path));if(data.length!==spec.bytes||hash(data)!==spec.sha256)throw new Error('Dataset identity mismatch: '+path);}
    summary.runtime=await fileIdentity(plan.tools[tool].runtime);
    summary.validation=await fileIdentity(['scripts/validate-annotation.mjs','validation/annotation/compare.py','validation/annotation/cases.json']);
    await identity(summary,tool);
    for(const dataset of datasets){
      const files=[],hashes={};
      for(const [path,spec] of Object.entries(manifest.files).filter(([path])=>path.startsWith(dataset+'/')).sort()){const local=path.slice(dataset.length+1);workspacePath(local);if(plan.tools[tool].fixtureInputs.includes(local))files.push({path:local,data:await readFile(join(root,'datasets/annotation',path))});hashes[path]=spec.sha256;}
      if(plan.tools[tool].fixtureInputs.some(path=>!files.some(file=>file.path===path)))throw new Error('Dataset lacks a planned source input');
      summary.datasets[dataset]={manifestSha256:summary.manifestSha256,files:hashes,observations:manifest.datasets[dataset].observations};
      const cache=new Map();
      for(const test of plan.tools[tool].cases.filter(test=>applies(test,dataset))){
        try{cache.set(test.id,await executeCase(summary,tool,dataset,test,files,cache));}
        catch(error){const record=summary.cases.findLast(record=>record.status==='running');if(record){record.status='failed';record.error=error.stack;}console.error(error.stack);}
      }
    }
    const old=JSON.stringify(summary.builds);await identity(summary,tool);
    if(JSON.stringify(summary.builds)!==old)throw new Error('Build changed during validation');
    for(const [path,checksum] of Object.entries({...summary.runtime,...summary.validation}))if(hash(await readFile(join(root,path)))!==checksum)throw new Error('Validator/runtime changed during run: '+path);
    if(hash(await readFile(join(root,'datasets/annotation/manifest.json')))!==summary.manifestSha256)throw new Error('Dataset manifest changed during validation');
    for(const [path,spec] of Object.entries(manifest.files)){const data=await readFile(join(root,'datasets/annotation',path));if(data.length!==spec.bytes||hash(data)!==spec.sha256)throw new Error('Dataset changed during validation: '+path);}
    const ids=summary.cases.map(record=>record.id);
    if(new Set(ids).size!==ids.length||JSON.stringify([...ids].sort())!==JSON.stringify([...summary.expectedCases].sort())||summary.cases.some(record=>record.status!=='passed'))throw new Error('Completed results do not cover the full passing case plan');
    summary.status='passed';
  }catch(error){summary.status='failed';summary.error=error.stack;console.error(error.stack);process.exitCode=1;}
  finally{summary.finishedAt=new Date().toISOString();await publishSummary(summary);}
}
async function publishSummary(summary){
  // Raw evidence belongs to an immutable run-specific directory. Publish only
  // complete summaries at the stable paths consumed by CI and the packager.
  const temporary=join(publishedDir,`.summary-${summary.tool}-${runId}.tmp`);
  await writeFile(temporary,JSON.stringify(summary,null,2)+'\n');
  await rename(temporary,join(publishedDir,`summary-${summary.tool}-${options.dataset}.json`));
}
try{if(options.engine!=='node')await startBrowser();for(const tool of tools)await validateTool(tool);}
finally{
  let timer,cleanupError;
  try{if(browser)await Promise.race([browser.close(),new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('Browser cleanup exceeded 30 seconds')),30000);})]);}
  catch(error){cleanupError=error.message;process.exitCode=1;}finally{clearTimeout(timer);}
  if(server){server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
  if(cleanupError){for(const summary of summaries){summary.status='failed';summary.cleanupError=cleanupError;await publishSummary(summary);}process.exit(1);}
}
