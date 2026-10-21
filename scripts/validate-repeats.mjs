#!/usr/bin/env node
/** Native, patched native and WebAssembly repeat calling and visualization. */
import {readFile,writeFile,mkdir,readdir,rename} from 'node:fs/promises';
import {createHash,randomUUID} from 'node:crypto';
import {spawnSync,execFileSync} from 'node:child_process';
import {createServer} from 'node:http';
import {dirname,resolve,join,relative,sep} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {workspacePath} from '../runtime/index.mjs';
import {ToolRunner} from '../runtime/index.mjs';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const options={engine:'node'};
for(let i=2;i<process.argv.length;i+=2){const key=process.argv[i].replace(/^--/,'');if(!(key in options)||!process.argv[i+1])throw Error('Usage: validate-repeats.mjs --engine node|chromium|firefox|webkit');options[key]=process.argv[i+1];}
if(!['node','chromium','firefox','webkit'].includes(options.engine))throw Error('Invalid engine');
const plan=JSON.parse(await readFile(join(root,'validation/repeats/cases.json'),'utf8'));
const selected=plan.cases;
const programs={expansionhunter:{unit:'expansionhunter',executable:'ExpansionHunter',module:'expansionhunter.mjs'},reviewer:{unit:'reviewer',executable:'REViewer',module:'reviewer.mjs'},samtools:{unit:'hts-1.21',executable:'samtools',module:'samtools.mjs'}};
const buildVersions={expansionhunter:{tool:'expansionhunter',version:'5.0.0'},reviewer:{tool:'reviewer',version:'0.2.7'},'hts-1.21':{tool:'hts',version:'1.21'}};
const runId=randomUUID(),timeoutMs=300000,publishedDir=join(root,'reports/repeats',options.engine),reportDir=join(publishedDir,runId);
await mkdir(reportDir,{recursive:true});
const hash=raw=>createHash('sha256').update(raw).digest('hex');
const local=path=>workspacePath(path).slice(6);
const same=(a,b)=>JSON.stringify(Object.entries(a).sort())===JSON.stringify(Object.entries(b).sort());
const targets=['native','patched-native','wasm'];
const summary={schema:1,tool:'repeats',versions:{expansionhunter:'5.0.0',reviewer:'0.2.7',samtools:'1.21'},engine:options.engine,status:'running',runId,reportDirectory:runId,node:process.version,browserVersion:null,crossOriginIsolated:false,startedAt:new Date().toISOString(),programCoverage:[...new Set(selected.filter(c=>c.kind!=='failure').flatMap(c=>c.steps.map(s=>s.tool)))].sort(),expectedCases:selected.map(c=>c.id),cases:[],browserErrors:[],externalRequests:[]};
let browser,page,server,manifest;
function log(s){console.log(`[${new Date().toISOString()}] ${options.engine} ${s}`);}
async function tree(base,prefix=''){
 const result={};for(const item of (await readdir(join(base,prefix),{withFileTypes:true})).sort((a,b)=>a.name.localeCompare(b.name))){const path=prefix+item.name;if(item.isDirectory())Object.assign(result,await tree(base,path+'/'));else if(item.isFile())result[path]=hash(await readFile(join(base,path)));else throw Error('Nonregular asset '+path);}return result;
}
async function identities(){
 const identities={};
 for(const [unit,expected] of Object.entries(buildVersions)){
  const dist=join(root,'dist',unit),raw=await readFile(join(dist,'build-manifest.json')),build=JSON.parse(raw),assets=await tree(dist);delete assets['build-manifest.json'];
  if(build.schema!==1||build.version!==expected.version||build.tool!==expected.tool||!same(build.assets,assets))throw Error('Build identity/asset mismatch '+unit);
  for(const [path,value] of Object.entries(build.inputs))if(hash(await readFile(join(root,path)))!==value)throw Error('Changed build input '+path);
  for(const target of targets.slice(0,2)){const actual=await tree(join(root,'build',target,unit));if(!same(actual,build.nativeFiles[target]))throw Error('Native build identity mismatch '+unit+'/'+target);}
  identities[unit]={tool:build.tool,version:build.version,assets,buildManifest:hash(raw),inputs:build.inputs,nativeFiles:build.nativeFiles};
 }
 return identities;
}
async function fileIdentities(paths){return Object.fromEntries(await Promise.all(paths.map(async path=>[path,hash(await readFile(join(root,path)))])));}
async function checkDataset(){
 const raw=await readFile(join(root,'datasets/repeats/manifest.json')),data=JSON.parse(raw);if(summary.datasetManifestSha256&&hash(raw)!==summary.datasetManifestSha256)throw Error('Dataset manifest changed');
 const inventory=await tree(join(root,'datasets/repeats'));const expected=Object.keys(data.files).sort(),actual=Object.keys(inventory).filter(p=>p.startsWith('common/')||p.startsWith('upstream/')).sort();if(JSON.stringify(actual)!==JSON.stringify(expected))throw Error('Dataset inventory mismatch');
 for(const [path,spec] of Object.entries(data.files)){const raw=await readFile(join(root,'datasets/repeats',path));if(raw.length!==spec.bytes||hash(raw)!==spec.sha256)throw Error('Dataset fixture changed '+path);}
 if(hash(await readFile(join(root,'datasets/repeats/provenance/sources.lock.json')))!==data.sourcesLockSha256)throw Error('Source provenance lock changed');summary.datasetManifestSha256=hash(raw);return data;
}
async function startBrowser(){
 const playwright=await import('playwright');browser=await playwright[options.engine].launch({headless:true});
 server=createServer(async(req,res)=>{try{const url=new URL(req.url,'http://localhost');if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end('<!doctype html><meta charset="utf-8"><title>Repeat validation</title>');return;}const path=resolve(root,'.'+decodeURIComponent(url.pathname));if(!path.startsWith(root+sep)||!['runtime/','dist/'].some(prefix=>relative(root,path).startsWith(prefix))){res.writeHead(404).end();return;}res.setHeader('Content-Type',path.endsWith('.wasm')?'application/wasm':/\.(mjs|js)$/.test(path)?'text/javascript':'application/octet-stream');res.end(await readFile(path));}catch{res.writeHead(404).end();}});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));page=await browser.newPage();page.on('pageerror',e=>{summary.browserErrors.push(e.message);log('browser error '+e.message);});page.on('request',request=>{if(!request.url().startsWith(`http://127.0.0.1:${server.address().port}/`))summary.externalRequests.push(request.url());});await page.goto(`http://127.0.0.1:${server.address().port}/`);summary.browserVersion=browser.version();summary.crossOriginIsolated=await page.evaluate(()=>crossOriginIsolated);if(summary.crossOriginIsolated)throw Error('Browser validation must use ordinary hosting');
}
async function wasm(step,files,outputs){
 const config=programs[step.tool];if(!config)throw Error('Unknown program '+step.tool);
 if(options.engine==='node')return new ToolRunner({moduleUrl:pathToFileURL(join(root,'dist',config.unit,config.module))}).run(step.args,{files,outputs,timeoutMs});
 const result=await page.evaluate(async({config,step,files,outputs,timeoutMs})=>{
  const {ToolRunner}=await import('/runtime/index.mjs');const decode=value=>Uint8Array.from(atob(value),c=>c.charCodeAt(0));const encode=bytes=>{let result='';for(let i=0;i<bytes.length;i+=32768)result+=String.fromCharCode(...bytes.subarray(i,i+32768));return btoa(result);};
  const result=await new ToolRunner({moduleUrl:new URL(`/dist/${config.unit}/${config.module}`,location.href)}).run(step.args,{files:files.map(f=>({path:f.path,data:decode(f.data)})),outputs,timeoutMs});return {...result,stdout:encode(result.stdout),stderr:encode(result.stderr),files:result.files.map(f=>({path:f.path,data:encode(f.data)}))};
 },{config,step,files:files.map(f=>({path:f.path,data:Buffer.from(f.data).toString('base64')})),outputs,timeoutMs});
 return {...result,stdout:Buffer.from(result.stdout,'base64'),stderr:Buffer.from(result.stderr,'base64'),files:result.files.map(f=>({path:f.path,data:Buffer.from(f.data,'base64')}))};
}
async function native(target,step,files,outputs,work){
 await mkdir(work,{recursive:true});for(const file of files){const path=join(work,local(file.path));await mkdir(dirname(path),{recursive:true});await writeFile(path,file.data);}
 const source=step.oracle?'native':target,config=programs[step.tool],program=join(root,'build',source,config.unit,config.executable),env={...process.env,LC_ALL:'C',TZ:'UTC'};
 const result=spawnSync(program,step.args,{cwd:work,env,timeout:timeoutMs,maxBuffer:128*1024*1024});if(result.error)throw result.error;if(result.signal||!Number.isInteger(result.status))throw Error('Native command did not exit normally');
 const answer={exitCode:result.status,signal:null,stdout:result.stdout,stderr:result.stderr,files:[],missingOutputs:[],command:{program:relative(root,program),args:step.args}};
 for(const path of outputs){try{answer.files.push({path,data:await readFile(join(work,path))});}catch(error){if(error.code!=='ENOENT')throw error;answer.missingOutputs.push(path);}}return answer;
}
async function executeStep(test,target,step,index,workspace,directory){
 const files=[...workspace].map(([path,data])=>({path,data})),outputs=step.outputs.map(local),start=Date.now();
 for(const output of outputs)if(workspace.has(output))throw Error('Output would reuse an input: '+output);
 const result=target==='wasm'&&!step.oracle?await wasm(step,files,outputs):await native(target,step,files,outputs,join(root,'build/repeats-validation',runId,test.id,target,String(index)));
 if(!Number.isInteger(result.exitCode)||result.signal)throw Error('No normal exit status');
 for(const stream of ['stdout','stderr'])if(step[stream+'File'])result.files.push({path:step[stream+'File'],data:result[stream]});
 const evidence=join(directory,'steps',String(index));await mkdir(evidence,{recursive:true});await writeFile(join(evidence,'stdout.txt'),result.stdout);await writeFile(join(evidence,'stderr.txt'),result.stderr);
 const execution={step:index,tool:step.tool,args:step.args,oracle:!!step.oracle,inputs:Object.fromEntries(files.map(f=>[f.path,hash(f.data)])),inputBytes:Object.fromEntries(files.map(f=>[f.path,f.data.length])),exitCode:result.exitCode,canonicalExitCode:result.exitCode<0?result.exitCode&255:result.exitCode,signal:null,missingOutputs:result.missingOutputs??[],stdout:hash(result.stdout),stderr:hash(result.stderr),stdoutBytes:result.stdout.length,stderrBytes:result.stderr.length,files:{},fileBytes:{},seconds:(Date.now()-start)/1000};
 if(result.command)execution.command=result.command;
 for(const file of result.files){const path=local(file.path);execution.files[path]=hash(file.data);execution.fileBytes[path]=file.data.length;workspace.set(path,file.data);for(const destination of [join(directory,path),join(evidence,'files',path)]){await mkdir(dirname(destination),{recursive:true});await writeFile(destination,file.data);}}
 if(test.kind==='failure'){execution.diagnosticMatched=result.exitCode!==0&&new RegExp(test.diagnosticRegex,'i').test(Buffer.from(result.stdout).toString()+Buffer.from(result.stderr).toString());}
 await writeFile(join(evidence,'command-result.json'),JSON.stringify(execution,null,2)+'\n');
 if(test.kind==='failure'){if(!execution.diagnosticMatched||(test.expectedExitCode!==undefined&&execution.canonicalExitCode!==test.expectedExitCode))throw Error(`${test.id} ${target} expected diagnostic failure, got ${result.exitCode}`);}
 else if(result.exitCode!==0||execution.missingOutputs.length)throw Error(`${test.id} ${target} step${index} exit${result.exitCode}, missing ${execution.missingOutputs.join(',')}: ${Buffer.from(result.stderr).toString().slice(-5000)}`);
 return execution;
}
async function compare(test,expected,actual,target,executions){
 const report=join(reportDir,test.id,`compare-${target}.json`);const result=spawnSync('python3',['-B',join(root,'validation/repeats/compare.py'),expected,actual,'--case',test.id,'--report',report],{timeout:timeoutMs,maxBuffer:8*1024*1024,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}});if(result.error)throw result.error;
 const comparison=JSON.parse(await readFile(report,'utf8'));if(!comparison.equal)throw Error(`${test.id} ${target}: ${JSON.stringify(comparison).slice(-5000)}`);
 for(const [side,kind] of [['expected','native'],['actual',target==='native-contract'?'native':target]]){const hashes=Object.assign({},...executions[kind].map(x=>x.files)),sizes=Object.assign({},...executions[kind].map(x=>x.fileBytes));const selectedHashes=Object.fromEntries(test.artifacts.map(x=>[x.path,hashes[x.path]])),selectedSizes=Object.fromEntries(test.artifacts.map(x=>[x.path,sizes[x.path]]));if(!same(comparison[side+'Files'],selectedHashes)||!same(comparison[side+'FileBytes'],selectedSizes))throw Error('Comparator was not bound to captured execution bytes');}
 return comparison;
}
async function executeCase(test){
 const record={id:test.id,kind:test.kind,programs:[...new Set(test.steps.map(s=>s.tool))],steps:test.steps,status:'running',executions:{},comparisons:{}};summary.cases.push(record);
 try{
  const inputs=await Promise.all(test.inputs.map(async input=>{const source=input.source,data=await readFile(join(root,'datasets/repeats',source));if(!manifest.files[source]||hash(data)!==manifest.files[source].sha256)throw Error('Unpinned fixture '+source);return {path:local(input.as),data};}));
  for(const target of targets){const directory=join(reportDir,test.id,target),workspace=new Map(inputs.map(f=>[f.path,f.data]));record.executions[target]=[];for(const file of inputs){const path=join(directory,'inputs',file.path);await mkdir(dirname(path),{recursive:true});await writeFile(path,file.data);}await mkdir(directory,{recursive:true});for(let i=0;i<test.steps.length;i++)record.executions[target].push(await executeStep(test,target,test.steps[i],i,workspace,directory));}
  if(test.kind==='failure'){if(new Set(targets.map(t=>record.executions[t].at(-1).canonicalExitCode)).size!==1)throw Error('Failure exit statuses differ');}
  else for(const target of ['native-contract','patched-native','wasm'])record.comparisons[target]=await compare(test,join(reportDir,test.id,'native'),join(reportDir,test.id,target==='native-contract'?'native':target),target,record.executions);
  record.status='passed';log(`PASS ${test.id}`);
 }catch(error){record.status='failed';record.error=error.stack;log(`FAIL ${test.id}: ${error.message}`);}
}
async function publish(){const temporary=join(publishedDir,`.summary-${runId}.tmp`);await writeFile(temporary,JSON.stringify(summary,null,2)+'\n');await rename(temporary,join(publishedDir,'summary-all.json'));}
try{
 manifest=await checkDataset();summary.build=await identities();summary.runtime=await fileIdentities(plan.runtime);summary.validation=await fileIdentities(['scripts/validate-repeats.mjs','scripts/fetch-repeat-test-data.py','validation/repeats/cases.json','validation/repeats/compare.py','datasets/repeats/provenance/sources.lock.json']);
 if(options.engine!=='node')await startBrowser();for(const test of selected)await executeCase(test);
 if(!same(await identities(),summary.build))throw Error('Build changed during run');await checkDataset();for(const [path,value] of Object.entries({...summary.runtime,...summary.validation}))if(hash(await readFile(join(root,path)))!==value)throw Error('Execution code changed during validation '+path);
 if(summary.browserErrors.length||summary.externalRequests.length)throw Error('Browser errors or unexpected external requests');
 if(summary.cases.some(c=>c.status!=='passed')||JSON.stringify(summary.cases.map(c=>c.id))!==JSON.stringify(summary.expectedCases))throw Error('The complete case plan did not pass');summary.status='passed';
}catch(error){summary.status='failed';summary.error=error.stack;console.error(error.stack);process.exitCode=1;}
finally{
 let timer;try{if(browser)await Promise.race([browser.close(),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Browser cleanup exceeded30seconds')),30000);})]);}catch(error){summary.status='failed';summary.cleanupError=error.message;process.exitCode=1;}finally{clearTimeout(timer);if(server){server.closeAllConnections();await new Promise(r=>server.close(r));}}
 summary.finishedAt=new Date().toISOString();await publish();log(`${summary.status}: ${relative(root,join(publishedDir,'summary-all.json'))}`);
}
