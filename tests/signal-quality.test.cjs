const assert=require('node:assert/strict');
const core=require('../static/forecast-core.js');
const at=Date.parse('2026-11-21T10:00:00Z');
const market={rulesConfirmed:true,ruleSource:'captured-official-rule',includesOvertime:false,
  pricesVerified:true,overOdds:1.8,underOdds:2.05,quotedAtUtc:new Date(at).toISOString(),suspended:false};
const row={line:160.5,pOver:.75,pUnder:.25,pPush:0,market};
const input={nowMs:at,ageSeconds:0,period:4,elapsedSeconds:120,periodLengthSeconds:600,
  timerVerified:true,scoreValid:true,clockValid:true,match:{ready:true,current:130,probabilities:[row]}};
const decision=(patch={})=>core.evaluateSignals({...input,match:{...input.match,probabilities:[{...row,market:{...market,...patch}}]}})[0];
assert.equal(decision().status,'research');
assert.equal(decision().priority,false);
assert.deepEqual(decision().reasonCodes,['calibration','validation']);
assert.ok(Math.abs(decision().comparison.modelExpectedReturn-.35)<1e-12);
assert.ok(Math.abs(decision().comparison.noVigOver+decision().comparison.noVigUnder-1)<1e-12);
assert.ok(decision({includesOvertime:true}).reasonCodes.includes('overtime'));
assert.equal(decision({includesOvertime:true}).status,'shadow');
for(const patch of [{rulesConfirmed:false},{ruleSource:''},{includesOvertime:null}])assert.ok(decision(patch).reasonCodes.includes('rules'));
for(const patch of [{pricesVerified:false},{overOdds:1},{underOdds:null}])assert.ok(decision(patch).reasonCodes.includes('prices'));
for(const patch of [{quotedAtUtc:null},{quotedAtUtc:new Date(at-91000).toISOString()},{quotedAtUtc:new Date(at+1000).toISOString()}])assert.ok(decision(patch).reasonCodes.includes('quote'));
assert.ok(decision({overOdds:1.2}).reasonCodes.includes('edge'),'high probability with a bad price is not value');
assert.equal(core.marketComparison({pOver:.5,pUnder:.4,pPush:.1},{...market,overOdds:1.8}).modelExpectedReturn,0,'push is not a loss');
for(const patch of [{elapsedSeconds:119},{period:3}])assert.ok(core.evaluateSignals({...input,...patch})[0].reasonCodes.includes('window'));
for(const patch of [{elapsedSeconds:null},{elapsedSeconds:600},{ageSeconds:91},{scoreValid:false},{timerVerified:false}])assert.ok(core.evaluateSignals({...input,...patch})[0].reasonCodes.includes('data'));
assert.equal(core.selectPrioritySignals({...input,match:{...input.match,calibrated:true,validation:{verified:true}}}).length,0,'untrusted feed flags cannot enable formal signals');
assert.equal(core.evaluateSignals({...input,match:{ready:true,probabilities:[{...row,pOver:.8,pUnder:.8}]}}).length,0);
console.log('signal quality regressions: OK');
const stableRobustness={available:true,variantCount:3,directionStable:true,probabilityMin:.68,probabilityMax:.77};
const evaluated=decision();
const watched={...evaluated,robustness:stableRobustness};
assert.equal(core.selectResearchCandidates([watched]).length,1);
assert.equal(core.selectResearchCandidates([watched])[0].priority,false);
assert.equal(core.selectResearchCandidates([{...watched,reasonCodes:[...watched.reasonCodes,'prices','rules']}])[0].marketReady,false);
for(const reason of ['data','window','preliminary'])assert.equal(core.selectResearchCandidates([{...watched,reasonCodes:[reason]}]).length,0);
for(const robustness of [null,{...stableRobustness,available:false},{...stableRobustness,directionStable:false},{...stableRobustness,probabilityMin:.649}])
  assert.equal(core.selectResearchCandidates([{...watched,robustness}]).length,0);
assert.equal(core.selectResearchCandidates([{...watched,line:140.5}, {...watched,line:160.5,robustness:{...stableRobustness,probabilityMin:.51}}, {...watched,line:180.5}]).length,0,'no cherry-picking an easier alternative line');
assert.equal(core.selectResearchCandidates([{...watched,comparison:{...watched.comparison,odds:1.4}}]).length,0,'all sensitivity cases must have positive modeled return when prices are known');
const prior=core.quarterPosterior({current:10,elapsedSeconds:300,periodLengthSeconds:600,completedQuarterTotals:[50,50]});
const base=core.simulateQuarter(prior,[{line:30.5}]);
const variants=core.quarterSensitivity(prior,[{line:30.5}]).filter(x=>x.name!=='Вся история матча');
const sensitivity=core.assessSensitivity(base.probabilities,variants)[0].robustness;
assert.equal(sensitivity.available,true);
assert.equal(sensitivity.directionStable,false,'current low tempo can disagree with the full historical prior');
assert.equal(core.selectResearchCandidates([{...watched,robustness:sensitivity}]).length,0);
assert.equal(core.summarizeSignals([watched]).reasons.find(x=>x.code==='calibration').lines,1);
console.log('research sensitivity regressions: OK');
assert.equal(decision({includesOvertime:true}).comparison,null,'regulation probabilities must not imply EV against an overtime market');
assert.ok(!decision({includesOvertime:true}).reasonCodes.includes('prices'),'a target mismatch is not missing prices');

assert.ok(decision({suspended:true}).reasonCodes.includes("suspended"));
assert.ok(!decision({suspended:true}).reasonCodes.includes("quote"));
assert.equal(core.selectResearchCandidates([{...watched,reasonCodes:["suspended"]}]).length,0);
const auditCases=require('./audited-quarter-signals.json');
for(const scoringUnit of [1,2,3]){
  const p=core.quarterPosterior({current:17,elapsedSeconds:433,periodLengthSeconds:600,completedQuarterTotals:[31]});
  const tested={...p,scoringUnit};
  const sim=core.simulateQuarter(tested,Array.from({length:400},(_,k)=>({line:p.current+k+.5})));
  let previous=0,mean=0,square=0;
  sim.probabilities.forEach((r,k)=>{const mass=r.pUnder-previous;previous=r.pUnder;mean+=k*mass;square+=k*k*mass;});
  assert.ok(Math.abs(mean-p.expectedRemaining)<1e-7,'marked scoring preserves the expected points');
  assert.ok(Math.abs(square-mean*mean-sim.variance)<1e-6,'predictive PMF and reported variance agree');
  assert.ok(Math.abs(sim.variance-(scoringUnit*p.expectedRemaining+p.meanUncertaintyVariance))<1e-10);
}
assert.equal(core.simulateQuarter({...prior,scoringUnit:4},[]),null);
for(const fixture of auditCases){
 const p=core.quarterPosterior(fixture.input),lines=[{line:fixture.line}],sim=core.simulateQuarter(p,lines);
 const variants=core.quarterSensitivity(p,lines),r=core.assessSensitivity(sim.probabilities,variants)[0].robustness;
 assert.equal(r.calibrated,false);assert.equal(r.independentModels,false);
 assert.ok(r.probabilityMin<=fixture.oldScenarioMinimum+.00051,'risk scenarios must not silently reproduce the audited extreme confidence');
 assert.ok(variants.some(v=>v.name==='Серии по 3 очка'));
 assert.ok(variants.every(v=>v.interval95[0]<=v.interval95[1]));
 assert.ok(!variants.some(v=>v.name==='Вся история матча'),'the base calculation is not an additional model vote');
 // actual_total is retained for evaluation only; never passed to any forecast.
}
console.log('marked scoring distribution and 14 audited snapshot regressions: OK');
