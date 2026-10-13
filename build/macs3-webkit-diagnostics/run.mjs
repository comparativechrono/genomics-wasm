import {createServer} from 'node:http';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {resolve,extname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {webkit,chromium,firefox} from 'playwright';
const root=fileURLToPath(new URL('../../',import.meta.url));
// Always inherit wheel hashes from the artifact being diagnosed. The only
// substituted executable is the small, logged diagnostic entry script.
const manifest=JSON.parse(await readFile(new URL('../../dist/macs3/manifest.json',import.meta.url),'utf8'));
const entry=await readFile(new URL('entry.py',import.meta.url));
manifest.entrypoint={filename:'entry.py',sha256:createHash('sha256').update(entry).digest('hex')};
await writeFile(new URL('manifest.json',import.meta.url),JSON.stringify(manifest,null,2)+'\n');
const engine=process.env.BROWSER||'webkit';
const out=new URL(`logs-${engine}-${Date.now()}/`,import.meta.url);await mkdir(out);
const logs=[],results=[];
function log(...args){const line=[new Date().toISOString(),...args].join(' ');logs.push(line);console.log(line);}
const mime={'.mjs':'application/javascript','.js':'application/javascript','.json':'application/json','.wasm':'application/wasm','.html':'text/html'};
const server=createServer(async(req,res)=>{
 try{const path=new URL(req.url,'http://localhost').pathname;if(path==='/__probe__'){res.writeHead(200,{'Content-Type':'text/html'}).end('<!doctype html><title>MACS3 diagnostic</title>');return;}
 const servedPath=path.replace(/^\/build\/macs3-webkit-diagnostics\/(pyodide|wheels)\//,'/dist/macs3/$1/');
 const file=resolve(root,'.'+decodeURIComponent(servedPath));if(!file.startsWith(root)){res.writeHead(403).end();return;}
 const bytes=await readFile(file);res.writeHead(200,{'Content-Type':mime[extname(file)]||'application/octet-stream'}).end(bytes);
 }catch(error){res.writeHead(404).end(String(error));}
});
await new Promise((yes,no)=>{server.once('error',no);server.listen(0,'127.0.0.1',yes);});
const origin=`http://127.0.0.1:${server.address().port}`;
let browser;
try{
 browser=await({webkit,chromium,firefox}[engine]).launch({headless:true});log('BROWSER',engine,browser.version());
 const page=await browser.newPage();page.on('console',m=>log('CONSOLE',m.type(),m.text()));page.on('pageerror',error=>log('PAGEERROR',error.stack));
 await page.goto(origin+'/__probe__');
 const plan=JSON.parse(await readFile(new URL('../../validation/macs3/cases.json',import.meta.url),'utf8'));
 const selected=(process.env.DIAG_CASES||'callpeak-model,callpeak-shift,predictd-bed').split(',');
 const ids=Array.from({length:Number(process.env.DIAG_REPEAT||1)},()=>selected).flat();
 for(const id of ids){
  let args,inputFiles;
  if(id==='probe'){args=['--probe',...(process.env.DIAG_PROBE_ARGS?JSON.parse(process.env.DIAG_PROBE_ARGS):[])];inputFiles=['chip.bed.gz'];}
  else{const spec=plan.cases.find(c=>c.id===id);args=[...spec.args,'--verbose','3'];inputFiles=spec.inputFiles;}
  log('CASE',id,JSON.stringify(args));
  const result=await page.evaluate(async({origin,args,inputFiles,timeoutMs,id})=>{
   const {MACS3Runner}=await import(origin+'/runtime/macs3.mjs');
   const files=await Promise.all(inputFiles.map(async path=>({path,data:new Uint8Array(await(await fetch(origin+'/datasets/macs3/macs3/'+path)).arrayBuffer())})));
   const script=id==='probe'?'build/macs3-webkit-diagnostics/probe-pileup.py':'dist/macs3/macs3.py';
   files.push({path:id==='probe'?'probe.py':'upstream-macs3.py',data:new Uint8Array(await(await fetch(origin+'/'+script)).arrayBuffer())});
   try{const result=await new MACS3Runner({baseUrl:origin+'/build/macs3-webkit-diagnostics/',workerUrl:origin+'/build/macs3-webkit-diagnostics/worker.mjs'}).run(args,{files,timeoutMs});
    return{exitCode:result.exitCode,stdout:new TextDecoder().decode(result.stdout),stderr:new TextDecoder().decode(result.stderr)};
   }catch(error){return{error:String(error),stack:error.stack};}
  },{origin,args,inputFiles,id,timeoutMs:Number(process.env.DIAG_TIMEOUT||60000)});
  results.push({id,...result});log('RESULT',id,JSON.stringify(result));
  await writeFile(new URL('results.json',out),JSON.stringify(results,null,2));await writeFile(new URL('console.log',out),logs.join('\n')+'\n');
 }
}catch(error){log('ERROR',error.stack);throw error;}
finally{await writeFile(new URL('console.log',out),logs.join('\n')+'\n');await browser?.close();server.close();console.log('LOGS',fileURLToPath(out));}
