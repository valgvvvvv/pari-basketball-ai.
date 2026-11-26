const assert=require('node:assert/strict');
const core=require('../static/forecast-core.js');
const now=Date.parse('2026-11-26T12:00:00Z');
function input(totals){return {nowMs:now,period:4,periodLengthSeconds:600,timerVerified:true,
 snapshots:totals.map((total,i)=>({period:4,captured_at:new Date(now-(totals.length-1-i)*15000).toISOString(),
 clock:`${Math.floor((600-i*15)/60).toString().padStart(2,'0')}:${((600-i*15)%60).toString().padStart(2,'0')}`,
 score:[Math.floor(total/2),Math.ceil(total/2)]}))};}
const rising=input([100,100,100,100,100,100,100,106,112,118,124,130,136]);
const rise=core.detectScoringRegime(rising);assert.equal(rise.status,'rising');assert.equal(rise.metric,'scoring_only');assert.equal(rise.calibrated,false);
const falling=core.detectScoringRegime(input([100,106,112,118,124,130,136,136,136,136,136,136,136]));assert.equal(falling.status,'falling');
assert.equal(core.detectScoringRegime(input(Array.from({length:13},(_,i)=>100+i*2))).status,'stable');
assert.equal(core.detectScoringRegime({...rising,timerVerified:false}).available,false);
assert.equal(core.detectScoringRegime({...rising,nowMs:now+91000}).available,false);
assert.equal(core.detectScoringRegime({...rising,snapshots:rising.snapshots.slice(-4)}).available,false);
const correction=structuredClone(rising);correction.snapshots[10].score=[30,30];assert.equal(core.detectScoringRegime(correction).available,false);
const reverse=structuredClone(rising);reverse.snapshots[10].clock='09:50';assert.equal(core.detectScoringRegime(reverse).available,false);
const otherPeriod=structuredClone(rising);otherPeriod.snapshots.slice(0,7).forEach(r=>r.period=3);assert.equal(core.detectScoringRegime(otherPeriod).available,false);
const pause=structuredClone(rising);pause.snapshots.push({...pause.snapshots.at(-1),captured_at:new Date(now+120000).toISOString()});pause.nowMs=now+120000;assert.equal(core.detectScoringRegime(pause).available,false);
const half={...rising,period:2,periodLengthSeconds:1200,snapshots:rising.snapshots.map(r=>({...r,period:2}))};assert.equal(core.detectScoringRegime(half).status,'rising');
const posterior=core.quarterPosterior({current:36,elapsedSeconds:180,periodLengthSeconds:600,completedQuarterTotals:[40,40,40]});
const scenarios=core.regimeSensitivity(posterior,[{line:60.5}],rise);assert.equal(scenarios.length,2);assert.ok(scenarios[0].projection>scenarios[1].projection);for(const s of scenarios)assert.ok(Math.abs(s.probabilities[0].pOver+s.probabilities[0].pUnder+s.probabilities[0].pPush-1)<1e-10);
console.log('regime detection: rising/falling/stable, corrections, pauses, stale data, formats and scenarios OK');

const burst=core.detectScoringRegime(input([100,100,100,100,100,100,100,112,124,136,136,136,136]));assert.equal(burst.status,'burst');
const prices={rulesConfirmed:true,ruleSource:'test',includesOvertime:false,pricesVerified:true,overOdds:1.9,underOdds:1.9,quotedAtUtc:new Date(now).toISOString(),suspended:false};
const ready={nowMs:now,ageSeconds:0,period:4,elapsedSeconds:240,periodLengthSeconds:600,timerVerified:true,clockValid:true,scoreValid:true,statisticsReady:true,match:{ready:true,current:100,probabilities:[{line:140.5,pOver:.7,pUnder:.3,market:prices}]}};
assert.equal(core.evaluateSignals(ready)[0].status,'research');
assert.ok(core.evaluateSignals({...ready,statisticsReady:false})[0].reasonCodes.includes('context'));
assert.ok(core.evaluateSignals({...ready,regime:rise})[0].reasonCodes.includes('regime'));
assert.equal(core.evaluateSignals({...ready,regime:rise})[0].status,'shadow');
