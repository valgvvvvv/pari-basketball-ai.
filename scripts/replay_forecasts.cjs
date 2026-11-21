// Offline replay: labels are never exposed to the model VM. No network or browser.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto');
const [inputFile,outputFile]=process.argv.slice(2);
if(!inputFile||!outputFile)throw Error('Usage: node scripts/replay_forecasts.cjs input.json output.json');
const root=path.resolve(__dirname,'..'),source=fs.readFileSync(path.join(root,'static/app.js'),'utf8');
const core=fs.readFileSync(path.join(root,'static/forecast-core.js'),'utf8');
const boundary='let userScrollIntentVersion = 0;';
if(source.split(boundary).length!==2)throw Error('Pure model boundary changed');
let now=0;const NativeDate=Date;
class ReplayDate extends NativeDate{constructor(...args){super(...(args.length?args:[now]));}static now(){return now;}}
const context={Date:ReplayDate,document:{getElementById:()=>null,addEventListener:()=>{}},
  localStorage:{getItem:()=>null,setItem:()=>{},removeItem:()=>{}},setInterval:()=>0,console};
context.window=context;vm.createContext(context);
vm.runInContext(core+'\n'+source.split(boundary)[0],context,{timeout:10000});
const inputs=JSON.parse(fs.readFileSync(inputFile,'utf8'));
const seen=new Set(),rows=[];
for(const item of inputs.slice().sort((a,b)=>Date.parse(a.now)-Date.parse(b.now))){
  const target=item.label?.target==='match'?'match':'quarter';
  const key=item.group||[item.event.id,target,target==='quarter'?item.event.period:0].join(':');
  if(seen.has(key))continue;
  now=Date.parse(item.now);
  if(!Number.isFinite(now))throw Error('Missing historical snapshot timestamp');
  context.input=JSON.parse(JSON.stringify(item.event));
  let result;
  try{
    result=vm.runInContext(`(()=>{const m=${target==='match'?'automaticForecast(input)':'({ready:false})'};
      const q=${target==='quarter'?'automaticQuarterForecast(input)':'({ready:false})'};
      return {prediction:${target==='match'?'m':'q'},evaluations:signalEvaluations(input,m,q)};})()`,context,{timeout:15000});
  }catch(error){rows.push({group:key,event_id:item.event.id,error:String(error)});seen.add(key);continue;}
  seen.add(key);
  rows.push({group:key,event_id:item.event.id,now:item.now,target,...JSON.parse(JSON.stringify(result)),label:item.label||null});
  if(rows.length%20===0)console.log('replayed',rows.length);
}
const output={model_source_sha256:crypto.createHash('sha256').update(core+'\n'+source.split(boundary)[0]).digest('hex'),
  protocol:'first chronological snapshot per supplied game/target group; labels outside VM; frozen clock; no later high-probability search',
  games:new Set(rows.map(x=>String(x.event_id))).size,checks:rows.length,
  priority_signals:rows.flatMap(x=>x.evaluations||[]).filter(x=>x.priority).length,errors:rows.filter(x=>x.error).length,rows};
fs.writeFileSync(outputFile,JSON.stringify(output,null,2));
console.log(JSON.stringify({games:output.games,checks:output.checks,priority:output.priority_signals,errors:output.errors}));
if(output.errors)process.exitCode=1;
