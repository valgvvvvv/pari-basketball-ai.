const assert=require('node:assert/strict');const core=require('../static/forecast-core.js');
const prior={twoMade:6,twoAtt:10,threeMade:2,threeAtt:6,ftm:3,fta:4,oreb:2,tov:4};
const live={twoMade:12,twoAtt:18,threeMade:4,threeAtt:10,ftm:4,fta:6,oreb:4,tov:6};
const input={score:[40,40],home:live,away:live,elapsedSeconds:1080,totalSeconds:2400,
 completedPeriods:[{period:1,durationSeconds:600,score:[21,21],home:prior,away:prior}]};
const f=core.possessionRemainder(input);assert.equal(f.ready,true);assert.equal(f.predictionIncludesOvertime,false);assert.equal(f.calibrated,false);
assert.ok(Math.abs(f.projection-80*2400/1080)>1,'a separate recent possession rate must not cancel into whole-game score/time');
assert.ok(f.projection>=80);
const segmentPoss=(18+10+.44*6-4+6)-(10+6+.44*4-2+4);
const expectedPpp=(2*8*12.5/19+3*4*4.5/11+2*4.5/7)/segmentPoss;
assert.ok(Math.abs(f.expectedRemaining-2*(segmentPoss/8)*22*expectedPpp)<1e-10,'independent arithmetic oracle');
for(const patch of [{score:[41,40]},{completedPeriods:[]},{elapsedSeconds:700},{elapsedSeconds:2400},
 {home:{...live,twoMade:19}},{home:{...live,fta:null}},
 {completedPeriods:[{...input.completedPeriods[0],period:2}]},
 {completedPeriods:[{...input.completedPeriods[0],score:[22,21]}]}])assert.equal(core.possessionRemainder({...input,...patch}).ready,false);
const slower=core.possessionRemainder({...input,elapsedSeconds:1140});assert.ok(slower.projection<f.projection);
const half=core.possessionRemainder({...input,elapsedSeconds:1680,completedPeriods:[{...input.completedPeriods[0],durationSeconds:1200}]});assert.equal(half.ready,true);
// Run the real adapters and UI computation boundary, not a second implementation.
const fs=require('node:fs'),vm=require('node:vm');const source=fs.readFileSync(require('node:path').join(__dirname,'../static/app.js'),'utf8');
const ctx={window:{PARIForecastCore:core},document:{getElementById:()=>null,addEventListener(){}},localStorage:{getItem:()=>null,setItem(){}},setInterval:()=>0,console};
vm.createContext(ctx);vm.runInContext(source.split('let userScrollIntentVersion = 0;')[0],ctx);
const map={twoMade:'two_pointers_made',twoAtt:'two_pointers_attempted',threeMade:'three_pointers_made',threeAtt:'three_pointers_attempted',ftm:'free_throws_made',fta:'free_throws_attempted',oreb:'offensive_rebounds',tov:'turnovers'};
const rows=box=>Object.entries(map).map(([k,metric_key])=>({metric_key,home:box[k],away:box[k],source:'PARI'}));
const event={id:'fixture',period:2,period_count:4,period_length_seconds:600,score:[40,40],clock:'02:00',timer_semantics_verified:true,
 period_scores:[[21,21],[19,19]],external_statistics:rows(live),period_statistics:rows(prior).map(r=>({...r,period:1}))};
ctx.event=event;
const adapted=vm.runInContext('possessionModelContext(event,{elapsed_seconds:1080,projection:180},2,"02:00")',ctx);
assert.equal(adapted.applied,true,'combined PPP above 2.25 is valid when individual efficiencies are valid');assert.ok(Math.abs(adapted.projectionExact-f.projection)<1e-9);
ctx.event={...event,period_statistics:[]};const missing=vm.runInContext('possessionModelContext(event,{elapsed_seconds:1080,projection:180},2,"02:00")',ctx);assert.equal(missing.applied,false);assert.equal(missing.available,true);assert.ok(missing.linearBaseline>80);
console.log('possession remainder: arithmetic oracle, non-cancellation, per-team bounds, 2x20, invalid input, full adapter OK');

ctx.historyEvent={team_history:{teams:[{name:{rus:'<script>test</script>'},summary:{sample_size:1,points_for:70,points_against:80},games:[]},
 {name:{rus:'B'},summary:{sample_size:1,points_for:90,points_against:60},games:[]}]},pari_ssis_diagnostics:{state:'score_not_current',received:[{kind:'30',metric_key:'points',home:10,away:12}]}};
const historyHtml=vm.runInContext('pariHistoryMarkup(historyEvent)',ctx);
assert.ok(historyHtml.includes('150.0'));assert.ok(!historyHtml.includes('<script>test</script>'));assert.ok(historyHtml.includes('&lt;script&gt;'));
console.log('historical reference arithmetic and escaped UI: OK');

// A stale native row must not hide the current matched provider value.
ctx.auditEvent={...event,external_statistics:rows(live).map(r=>({...r,source:'SofaScore',calculation_eligible:true,freshness_status:'fresh'}))};
ctx.auditEvent.external_statistics.unshift({metric_key:'two_pointers_attempted',home:200,away:200,source:'PARI',freshness_status:'stale',calculation_eligible:false});
let audit=vm.runInContext('possessionModelContext(auditEvent,{elapsed_seconds:1080,projection:180},2,"02:00")',ctx);
assert.equal(audit.applied,true);assert.ok(Math.abs(audit.projectionExact-f.projection)<1e-9);
assert.ok(audit.input_lineage.every(r=>r.source!=='PARI'||r.period===1));
ctx.auditEvent.period_statistics=event.period_statistics.filter(r=>!['two_pointers_made','two_pointers_attempted'].includes(r.metric_key));
ctx.auditEvent.period_statistics.push({metric_key:'field_goals_made',home:8,away:8,period:1,source:'PARI'},
 {metric_key:'field_goals_attempted',home:16,away:16,period:1,source:'PARI'});
audit=vm.runInContext('possessionModelContext(auditEvent,{elapsed_seconds:1080,projection:180},2,"02:00")',ctx);
assert.equal(audit.applied,true);assert.ok(Math.abs(audit.projectionExact-f.projection)<1e-9);
ctx.auditEvent.external_statistics=ctx.auditEvent.external_statistics.map(r=>({...r,calculation_eligible:false}));
assert.equal(vm.runInContext('possessionModelContext(auditEvent,{elapsed_seconds:1080,projection:180},2,"02:00")',ctx).applied,false);
console.log('source eligibility, actual model inputs and period algebra: OK');

assert.equal(vm.runInContext("calculationDataStatus({ready:true,kind:'live_tempo',estimate:{}},'match').requiredStatisticsReady",ctx),true);
assert.equal(vm.runInContext("calculationDataStatus({ready:true,kind:'market_reference'},'match').requiredStatisticsReady",ctx),false);
assert.equal(vm.runInContext("calculationDataStatus({ready:false,reason:'no clock'},'quarter').requiredStatisticsReady",ctx),false);
