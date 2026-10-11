#!/usr/bin/env node
/** Real-data native / patched-native / WebAssembly Strelka2 validation. */
import { readFile, writeFile, mkdir, readdir, rm, lstat, readlink } from 'node:fs/promises';
import { createHash, randomUUID } from 'node:crypto';
import { execFileSync, spawnSync } from 'node:child_process';
import { createServer } from 'node:http';
import { dirname, resolve, join, relative, sep } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { ToolRunner, workspacePath } from '../runtime/index.mjs';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const options = { engine: 'node' };
for (let i = 2; i < process.argv.length; i += 2) {
  if (process.argv[i] !== '--engine' || !['node','chromium','firefox','webkit'].includes(process.argv[i+1])) throw new Error('validate-strelka2.mjs [--engine node|chromium|firefox|webkit]');
  options.engine = process.argv[i+1];
}
const plan = JSON.parse(await readFile(join(root, 'validation/strelka2/cases.json'), 'utf8'));
const datasets = plan.datasets;
const reportDir = join(root, 'reports', 'strelka2', options.engine);
await mkdir(reportDir, { recursive: true });
const summary = { schema: 1, startedAt: new Date().toISOString(), engine: options.engine, node: process.version,
  status: 'running', builds: {}, datasets: {}, cases: [] };
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const timeoutMs = 600000;
const runId = randomUUID();
summary.runId = runId;
const normalized = file => ({ ...file, path: file.path.replace(/^\/work\//, '') });
const applies = (test, dataset) => !test.datasets || test.datasets.includes(dataset);
const caseId = (tool, dataset, test) => `strelka2-${dataset}-${test.id}`;
summary.expectedCases = datasets.flatMap(dataset => plan.cases.map(test => caseId('strelka2',dataset,test)));
let browser, server, page, manifest;
function log(message) { console.log(`[${new Date().toISOString()}] ${options.engine} ${message}`); }
async function startBrowser() {
  const playwright = await import('playwright');
  browser = await playwright[options.engine].launch({ headless: true });
  summary.browserVersion = browser.version();
  server = createServer(async (request, response) => {
    try {
      const url = new URL(request.url, 'http://localhost');
      if (url.pathname === '/') { response.setHeader('Content-Type', 'text/html'); response.end('<!doctype html><meta charset="utf-8"><title>Strelka2 validation</title>'); return; }
      const path = resolve(root, '.' + decodeURIComponent(url.pathname));
      if (!path.startsWith(root + sep) || !['runtime/', 'dist/'].some(prefix => relative(root, path).startsWith(prefix))) { response.writeHead(404).end(); return; }
      response.setHeader('Content-Type', path.endsWith('.wasm') ? 'application/wasm' : 'text/javascript');
      response.end(await readFile(path));
    } catch { response.writeHead(404).end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  page = await browser.newPage();
  page.on('console', message => log(`browser ${message.type()}: ${message.text()}`));
  page.on('pageerror', error => log(`browser error: ${error.message}`));
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  summary.crossOriginIsolated = await page.evaluate(() => crossOriginIsolated);

}

async function wasm(tool, args, files, outputs = [], outputDirectories = [], test = {}) {
  const settings = { files, outputs, outputDirectories, timeoutMs };
  if (options.engine === 'node') {
    const {Strelka2Runner} = await import('../runtime/strelka2.mjs');
    const runner = test.workflow ? new Strelka2Runner({baseUrl:pathToFileURL(join(root,'dist/strelka2/'))}) : new ToolRunner({moduleUrl:pathToFileURL(join(root,'dist/strelka2',`${test.program}.mjs`))});
    return runner.run(args,settings);
  }
  const result = await page.evaluate(async ({args, files, outputs, outputDirectories, timeoutMs, workflow, program}) => {
    const {ToolRunner} = await import('/runtime/index.mjs');
    const {Strelka2Runner} = await import('/runtime/strelka2.mjs');
    const encode = bytes => { let binary=''; for(let i=0;i<bytes.length;i+=32768) binary+=String.fromCharCode(...bytes.subarray(i,i+32768)); return btoa(binary); };
    const runner=workflow ? new Strelka2Runner({baseUrl:new URL('/dist/strelka2/',location.href)}) : new ToolRunner({moduleUrl:new URL(`/dist/strelka2/${program}.mjs`,location.href)});
    const result=await runner.run(args,{files:files.map(file=>({path:file.path,data:Uint8Array.from(atob(file.data),c=>c.charCodeAt(0))})),outputs,outputDirectories,timeoutMs});
    return {...result,stdout:encode(result.stdout),stderr:encode(result.stderr),files:result.files.map(file=>({path:file.path,data:encode(file.data)}))};
  },{args,outputs,outputDirectories,timeoutMs,workflow:test.workflow,program:test.program,files:files.map(file=>({path:file.path,data:Buffer.from(file.data).toString('base64')}))});
  return {...result,stdout:Buffer.from(result.stdout,'base64'),stderr:Buffer.from(result.stderr,'base64'),files:result.files.map(file=>({path:file.path,data:Buffer.from(file.data,'base64')}))};
}
async function native(tool,kind,args,files,outputs=[],outputDirectories=[],id='command',test={}) {
  const work=join(root,'build/strelka2-validation',runId,options.engine,id,kind);
  await mkdir(work,{recursive:true});
  for(const file of files) { const path=join(work,workspacePath(file.path).slice(6)); await mkdir(dirname(path),{recursive:true}); await writeFile(path,file.data); }
  const packagePath=join(root,'build',kind,'strelka2-package');
  const python=join(root,'build/strelka2-python/bin/python2.7');
  const env={...process.env,LC_ALL:'C',TZ:'UTC',PYTHONDONTWRITEBYTECODE:'1',PATH:join(root,'build/strelka2-python/bin')+':'+process.env.PATH};
  const invoke=(command,argv)=> { const result=spawnSync(command,argv,{cwd:work,env,timeout:timeoutMs,maxBuffer:64*1024*1024,stdio:['ignore','pipe','pipe']}); if(result.error) throw result.error; if(!Number.isInteger(result.status)) throw new Error(`${kind} terminated by ${result.signal}`); return result; };
  let first,result;
  if(test.workflow) {
    first=invoke(python,['-B',join(packagePath,'bin/configureStrelkaSomaticWorkflow.py'),...args.slice(1)]);
    result=first;
    if(first.status===0) result=invoke(python,['-B','analysis/runWorkflow.py','-m','local','-j','1','-g','4']);
  } else result=invoke(join(packagePath,'libexec',test.program),args);
  const answer={exitCode:result.status,signal:result.signal,stdout:Buffer.concat(first && first!==result?[first.stdout,result.stdout]:[result.stdout]),stderr:Buffer.concat(first && first!==result?[first.stderr,result.stderr]:[result.stderr]),files:[],missingOutputs:[],work,packagePath};
  const wanted=new Set(outputs);
  const walk=async directory=> { for(const entry of await readdir(join(work,directory),{withFileTypes:true})) { const path=directory+'/'+entry.name; if(entry.isDirectory()) await walk(path); else if(entry.isFile()) wanted.add(path); } };
  for(const directory of outputDirectories) { try { await walk(directory); } catch(error) { if(error.code!=='ENOENT') throw error; } }
  for(const path of [...wanted].sort()) { try { answer.files.push({path,data:await readFile(join(work,path))}); } catch(error) { if(error.code!=='ENOENT') throw error; answer.missingOutputs.push(path); } }
  if(test.workflow && result.status===0) {
    answer.workflow={nativeCommands:await readFile(join(work,'analysis/workspace/pyflow.data/state/pyflow_tasks_info.txt'),'utf8')};
  }
  return answer;
}

async function saveResult(id, kind, result) {
  const directory = join(reportDir, id, kind);
  await mkdir(directory, { recursive: true });
  await writeFile(join(directory, 'stdout.txt'), result.stdout);
  await writeFile(join(directory, 'stderr.txt'), result.stderr);
  await writeFile(join(directory, 'command-result.json'), JSON.stringify({ exitCode: result.exitCode, signal: result.signal ?? null, missingOutputs: result.missingOutputs }, null, 2) + '\n');
  for (const file of result.files.map(normalized)) {
    const path = join(directory, workspacePath(file.path).slice(6));
    await mkdir(dirname(path), { recursive: true });
    await writeFile(path, file.data);
  }
  return directory;
}

function success(result, label) {
  if (result.exitCode !== 0 || result.missingOutputs.length) throw new Error(`${label}: exit ${result.exitCode}, missing ${result.missingOutputs.join(', ')}\n${Buffer.from(result.stderr).toString().slice(-4000)}`);
}

async function compare(tool,dataset,test,expected,actual,id,kind) {
  const report=join(reportDir,id,`compare-${kind}.json`);
  const args=[join(root,'validation/strelka2/compare.py'),expected.directory,actual.directory,'--manifest',join(root,'datasets/strelka2/manifest.json'),'--dataset',dataset,'--case',test.id,'--report',report];
  for(const prefix of [...(expected.result.provenancePrefixes??[]),expected.result.work,expected.result.packagePath?expected.result.packagePath+'/share':null, '/work/__strelka2'].filter(Boolean)) args.push('--expected-prefix',prefix);
  for(const prefix of [...(actual.result.provenancePrefixes??[]),actual.result.work,actual.result.packagePath?actual.result.packagePath+'/share':null,'/work/__strelka2'].filter(Boolean)) args.push('--actual-prefix',prefix);
  try {execFileSync('python3',args,{maxBuffer:8*1024*1024});} catch(error) {throw new Error(`Strelka2 mismatch: ${id} ${kind}; ${String(error.stdout??'').slice(0,4000)}`);}
  return JSON.parse(await readFile(report,'utf8'));
}
async function run(tool, kind, args, files, outputs, id, test = {}) {
  log(`${id} ${kind} starting`);
  const started = Date.now();
  const result = kind === 'wasm' ? await wasm(tool, args, files, outputs.filter(path=>path!==test.stdoutFile), test.workflow?['analysis/results','analysis/workspace']:[], test) : await native(tool, kind, args, files, outputs.filter(path=>path!==test.stdoutFile), test.workflow?['analysis/results','analysis/workspace']:[], id, test);
  if (test.stdoutFile) { result.files.push({path:test.stdoutFile,data:result.stdout}); result.missingOutputs = result.missingOutputs.filter(path => path.replace(/^\/work\//,'') !== test.stdoutFile); }
  const directory = await saveResult(id, kind, result);
  if(result.workflow) await writeFile(join(directory,'workflow.json'),JSON.stringify(result.workflow,null,2)+'\n');
  const execution = { exitCode: result.exitCode, signal: result.signal ?? null, canonicalExitCode: result.exitCode < 0 ? (result.exitCode & 255) : result.exitCode,
    missingOutputs: result.missingOutputs, seconds: (Date.now() - started) / 1000,
    stdout: hash(result.stdout), stderr: hash(result.stderr), files: Object.fromEntries(result.files.map(normalized).map(file => [file.path, hash(file.data)])) };
  log(`${id} ${kind} exit ${result.exitCode} after ${execution.seconds.toFixed(2)} seconds`);
  return { result, directory, execution };
}
async function loadDataset(dataset) {
  const files = [], hashes = {};
  for (const [path, spec] of Object.entries(manifest.files).filter(([path]) => path.startsWith(dataset + '/')).sort()) {
    const local = path.slice(dataset.length + 1); workspacePath(local);
    const bytes = await readFile(join(root, 'datasets/strelka2', path));
    if (hash(bytes) !== spec.sha256 || bytes.length !== spec.bytes) throw new Error(`${dataset}: checksum or size mismatch for ${path}`);
    files.push({ path: local, data: bytes }); hashes[path] = hash(bytes);
  }
  const data = manifest.datasets[dataset];
  if (!data || !files.some(file => file.path === 'tumor.bam') || !files.some(file => file.path === 'normal.bam')) throw new Error('Incomplete matched dataset');
  summary.datasets[dataset] = { manifestSha256: hash(await readFile(join(root, 'datasets/strelka2/manifest.json'))),
    files: hashes, observations: data.observations, regionMappings: data.regionMappings ?? manifest.regionMappings };
  return files;
}
async function executeCase(tool, dataset, test, files, cache) {
  const id = caseId(tool, dataset, test), record = { id, tool, dataset, case: test.id, kind: test.kind,
    args: test.args, status: 'running', executions: {}, comparisons: {} };
  await rm(join(reportDir,id),{recursive:true,force:true});
  summary.cases.push(record);
  const results = {};
  for (const kind of test.executions) {
    const extra = (test.inputsFrom ?? []).map(input => {
      const source = cache.get(input.case)?.results[kind]?.result.files.map(normalized).find(file => file.path === input.path);
      if (!source) throw new Error(`${id}: missing ${kind} input from ${input.case}/${input.path}`);
      return { path: input.as, data: source.data };
    });
    const inputs=[...files,...extra,...Object.entries(test.extraFiles??{}).map(([path,data])=>({path,data:Buffer.from(data)}))];
    if(test.statisticsBoundary) {
      const spec=test.statisticsBoundary, source=cache.get(spec.case)?.results[kind]?.result.files.map(normalized).find(file=>file.path===spec.path);
      if(!source) throw new Error('Missing actual workflow statistics for numeric boundary test');
      spec.values.forEach((values,index)=> {const text=Buffer.from(source.data).toString().replace(/<(wall|user|system)>[^<]*<\/\1>/g,'<$1>0</$1>').replace(/<candidateIndels>[^<]*<\/candidateIndels>/,`<candidateIndels>${values[0]}</candidateIndels>`).replace(/<nonCandidateIndels>[^<]*<\/nonCandidateIndels>/,`<nonCandidateIndels>${values[1]}</nonCandidateIndels>`); inputs.push({path:`stats-${index===0?'a':'b'}.xml`,data:Buffer.from(text)});});
    }
    results[kind] = await run(tool, kind, test.args, inputs, test.outputs, id, test);
    results[kind].inputs=inputs;
    results[kind].result.provenancePrefixes=(test.inputsFrom??[]).map(input=>cache.get(input.case)?.results[kind]?.result.work).filter(Boolean);
    if(test.statisticsBoundary) {
      record.statisticsBoundary??={...test.statisticsBoundary,targets:{}};
      const spec=test.statisticsBoundary, source=cache.get(spec.case).results[kind].result.files.map(normalized).find(file=>file.path===spec.path);
      record.statisticsBoundary.targets[kind]={sourceSha256:hash(source.data),inputs:Object.fromEntries(inputs.filter(file=>file.path.startsWith('stats-')).map(file=>[file.path,hash(file.data)]))};
      for(const file of inputs.filter(file=>file.path.startsWith('stats-'))) await writeFile(join(results[kind].directory,file.path),file.data);
    }
    record.executions[kind] = results[kind].execution;
    record.executions[kind].inputsFrom = (test.inputsFrom ?? []).map((input,i)=>({...input,sha256:hash(extra[i].data)}));
    for (const file of extra) { const destination=join(results[kind].directory,'inputs',workspacePath(file.path).slice(6)); await mkdir(dirname(destination),{recursive:true}); await writeFile(destination,file.data); }
    if (['failure', 'restriction'].includes(test.kind)) {
      const result = results[kind].result;
      if (result.exitCode === 0 || (!result.stderr.length && !result.stdout.length)) throw new Error(`${id} ${kind}: expected nonzero exit and a diagnostic`);
      if (test.diagnosticRegex && !new RegExp(test.diagnosticRegex, 'i').test(Buffer.from(result.stderr).toString() + Buffer.from(result.stdout).toString())) throw new Error(`${id} ${kind}: missing required failure diagnostic`);
      record.executions[kind].diagnosticMatched = Boolean(test.diagnosticRegex);
    } else success(results[kind].result, `${id} ${kind}`);
  }
  if (['failure', 'restriction'].includes(test.kind)) {
    const normalExecutions = Object.values(record.executions);
    if (new Set(normalExecutions.map(value => value.canonicalExitCode)).size !== 1) throw new Error(`${id}: failure exit codes differ (negative C exit codes canonicalised modulo 256)`);
    record.status = 'passed'; return { record, results };
  }
  const baseline = test.kind === 'repeat' ? cache.get(test.baseline) : null;
  if (test.kind === 'repeat' && !baseline) throw new Error(`${id}: missing baseline`);
  const expected = baseline ? baseline.results.native : results.native;
  if (test.workflow) await validateWorkflow(record,baseline??{results},results,id);
  for (const kind of test.comparisons) {
    const actual = kind === 'native-contract' ? results.native : results[kind];
    record.comparisons[kind] = await compare(tool,dataset,test,expected,actual,id,kind);
    if (!record.comparisons[kind].equal) throw new Error(`${id}: unequal ${kind}`);
  }
  if (test.effect || test.equivalentTo) {
    const other = cache.get(test.effect ?? test.equivalentTo);
    if (!other) throw new Error(`${id}: missing option baseline`);
    const before = other.record.comparisons['native-contract'].biologicalDigest;
    const after = record.comparisons['native-contract'].biologicalDigest;
    record.optionEffect = { baseline: test.effect ?? test.equivalentTo, changed: before !== after,
      expected: test.effect ? 'changed' : 'equivalent', before, after };
    if (test.effect ? before === after : before !== after) throw new Error(`${id}: native output ${test.effect ? 'did not change under the tested option' : 'was not equivalent to its input-format/thread baseline'}`);
  }
  record.status = 'passed'; log(`PASS ${id}`);
  return { record, results };
}
// Identity checks and the complete plan run follow below.
async function validateWorkflow(record, oracle, results, id) {
  const native=oracle.results.native;
  const nativePath=join(reportDir,id,'native-commands.tsv');
  await writeFile(nativePath,native.result.workflow.nativeCommands);
  record.workflow={oracle:'unchanged upstream Python2 workflow',nativeStages:[],targets:{}};
  for(const [kind,value] of Object.entries(results)) {
    const inputHashes=Object.fromEntries(value.inputs.map(file=>[workspacePath(file.path),hash(file.data)]));
    for(const model of ['somaticSNVScoringModels.json','somaticIndelScoringModels.json']) inputHashes[`/work/__strelka2/config/${model}`]=hash(await readFile(join(root,'dist/strelka2/config',model)));
    const outputHashes=Object.fromEntries(value.result.files.map(file=>[workspacePath(file.path),hash(file.data)]));
    const inputs=join(value.directory,'workflow-inputs.json'), outputs=join(value.directory,'workflow-outputs.json'), report=join(value.directory,'workflow-comparison.json');
    await writeFile(inputs,JSON.stringify(inputHashes)); await writeFile(outputs,JSON.stringify(outputHashes));
    const args=[join(root,'validation/strelka2/workflow.py'),nativePath,join(value.directory,'workflow.json'),'--native-work',native.result.work,'--native-package',native.result.packagePath,'--input-hashes',inputs,'--output-hashes',outputs,'--report',report];
    if(kind!=='wasm') args.push('--actual-native-work',value.result.work,'--actual-native-package',value.result.packagePath);
    try {execFileSync('python3',args,{maxBuffer:16*1024*1024});} catch(error) {throw new Error(`${id} ${kind} workflow mismatch: ${String(error.stdout??'').slice(0,5000)}`);}
    const comparison=JSON.parse(await readFile(report,'utf8'));
    if(!comparison.equal) throw new Error(`${id} ${kind} workflow comparison failed`);
    record.workflow.nativeStages=comparison.nativeStages;
    record.workflow.targets[kind]=comparison;
    record.executions[kind].workflow=value.result.workflow;
  }
}
async function scan(base,prefix='',{symlinks=false}={}) {
  const files={},links={};
  for(const entry of await readdir(join(base,prefix),{withFileTypes:true})) {
    const path=prefix+entry.name;
    if(entry.isDirectory()) {const nested=await scan(base,path+'/',{symlinks}); Object.assign(files,nested.files);Object.assign(links,nested.symlinks);}
    else if(entry.isFile()) files[path]=hash(await readFile(join(base,path)));
    else if(entry.isSymbolicLink() && symlinks) links[path]=await readlink(join(base,path));
    else throw new Error('Unexpected non-regular build file: '+path);
  }
  return {files,symlinks:links};
}
const sameMap=(a,b)=>JSON.stringify(Object.entries(a).sort())===JSON.stringify(Object.entries(b).sort());
async function identities() {
  const dist=join(root,'dist/strelka2'), raw=await readFile(join(dist,'build-manifest.json')), build=JSON.parse(raw);
  const assets=(await scan(dist)).files;delete assets['build-manifest.json'];
  if(!sameMap(assets,build.assets)) throw new Error('Strelka2 asset map differs from build manifest');
  for(const [path,checksum] of Object.entries(build.inputs)) if(hash(await readFile(join(root,path)))!==checksum) throw new Error('Changed source input: '+path);
  for(const kind of ['native','patched-native']) {
    const files=(await scan(join(root,'build',kind,'strelka2-package'))).files;
    const actual=Object.fromEntries(Object.entries(files).map(([path,checksum])=>['strelka2-package/'+path,checksum]));
    actual.strelka2=hash(await readFile(join(root,'build',kind,'strelka2')));
    if(!sameMap(actual,build.nativeFiles[kind])) throw new Error('Native helper environment differs from build manifest: '+kind);
  }
  const pyDir=join(root,'build/strelka2-python'), pyRaw=await readFile(join(pyDir,'build-manifest.json')), py=JSON.parse(pyRaw), pyTree=await scan(pyDir,'',{symlinks:true});
  delete pyTree.files['build-manifest.json'];
  if(!sameMap(pyTree.files,py.files)||!sameMap(pyTree.symlinks,py.symlinks)) throw new Error('Python2 environment differs from manifest');
  for(const [path,checksum] of Object.entries(py.inputs)) if(hash(await readFile(join(root,path)))!==checksum) throw new Error('Changed Python2 recipe input: '+path);
  const version=execFileSync(join(pyDir,'bin/python2.7'),['-B','-c','import platform; print(platform.python_version())'],{encoding:'utf8'}).trim();
  if(version!=='2.7.18') throw new Error('Wrong native workflow Python version');
  summary.builds.strelka2={native:build.nativeFiles.native.strelka2,'patched-native':build.nativeFiles['patched-native'].strelka2,module:assets['strelka2.mjs'],wasm:assets['strelka2.wasm'],assets,buildManifest:hash(raw),nativeFiles:build.nativeFiles,nativePython:{version,sha256:py.files['bin/python2.7'],buildManifest:hash(pyRaw),files:py.files,symlinks:py.symlinks}};
}
try {
  summary.python=execFileSync('python3',['--version'],{encoding:'utf8'}).trim();
  manifest=JSON.parse(await readFile(join(root,'datasets/strelka2/manifest.json'),'utf8'));
  summary.manifestSha256=hash(await readFile(join(root,'datasets/strelka2/manifest.json')));
  for(const [path,info] of Object.entries(manifest.files)) {const data=await readFile(join(root,'datasets/strelka2',path));if(data.length!==info.bytes||hash(data)!==info.sha256) throw new Error('Dataset identity mismatch: '+path);}
  summary.runtime=Object.fromEntries(await Promise.all(['runtime/index.mjs','runtime/worker.mjs','runtime/execute.mjs','runtime/strelka2.mjs','runtime/strelka2-somatic.mjs'].map(async path=>[path,hash(await readFile(join(root,path)))])));
  summary.validation=Object.fromEntries(await Promise.all(['scripts/validate-strelka2.mjs','validation/strelka2/compare.py','validation/strelka2/workflow.py','validation/strelka2/cases.json','validation/compare.py'].map(async path=>[path,hash(await readFile(join(root,path)))])));
  await identities();
  if(options.engine!=='node') await startBrowser();
  for(const dataset of datasets) {
    const files=await loadDataset(dataset),cache=new Map();
    for(const test of plan.cases) {
      try {cache.set(test.id,await executeCase('strelka2',dataset,test,files,cache));}
      catch(error) {const current=summary.cases.findLast(record=>record.status==='running');if(current){current.status='failed';current.error=error.stack;} console.error(error.stack);}
    }
  }
  const frozenBuilds=JSON.stringify(summary.builds);
  await identities();
  if(JSON.stringify(summary.builds)!==frozenBuilds) throw new Error('Build or native environment changed during validation');
  for(const [path,checksum] of Object.entries({...summary.runtime,...summary.validation})) if(hash(await readFile(join(root,path)))!==checksum) throw new Error('Validation/runtime changed during run: '+path);
  if(hash(await readFile(join(root,'datasets/strelka2/manifest.json')))!==summary.manifestSha256) throw new Error('Dataset manifest changed during run');
  const ids=summary.cases.map(record=>record.id);
  if(new Set(ids).size!==ids.length||JSON.stringify([...ids].sort())!==JSON.stringify([...summary.expectedCases].sort())||summary.cases.some(record=>record.status!=='passed')) throw new Error('Completed cases do not match the full passing case plan');
  summary.status='passed';
} catch(error) {summary.status='failed';summary.error=error.stack;console.error(error.stack);process.exitCode=1;}
finally {
  summary.finishedAt=new Date().toISOString();
  const output=join(reportDir,'summary-strelka2-all.json');
  await writeFile(output,JSON.stringify(summary,null,2)+'\n');
  let timer;
  try {if(browser) await Promise.race([browser.close(),new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('Browser cleanup exceeded 30 seconds')),30000);})]);}
  catch(error){summary.status='failed';summary.cleanupError=error.message;process.exitCode=1;}
  finally{clearTimeout(timer);}
  if(server){server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
  await writeFile(output,JSON.stringify(summary,null,2)+'\n');
  if(summary.cleanupError) process.exit(1);
}
