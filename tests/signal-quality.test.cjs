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
for(const patch of [{quotedAtUtc:null},{quotedAtUtc:new Date(at-91000).toISOString()},{quotedAtUtc:new Date(at+1000).toISOString()},{suspended:true}])assert.ok(decision(patch).reasonCodes.includes('quote'));
assert.ok(decision({overOdds:1.2}).reasonCodes.includes('edge'),'high probability with a bad price is not value');
assert.equal(core.marketComparison({pOver:.5,pUnder:.4,pPush:.1},{...market,overOdds:1.8}).modelExpectedReturn,0,'push is not a loss');
for(const patch of [{elapsedSeconds:119},{period:3}])assert.ok(core.evaluateSignals({...input,...patch})[0].reasonCodes.includes('window'));
for(const patch of [{elapsedSeconds:null},{elapsedSeconds:600},{ageSeconds:91},{scoreValid:false},{timerVerified:false}])assert.ok(core.evaluateSignals({...input,...patch})[0].reasonCodes.includes('data'));
assert.equal(core.selectPrioritySignals({...input,match:{...input.match,calibrated:true,validation:{verified:true}}}).length,0,'untrusted feed flags cannot enable formal signals');
assert.equal(core.evaluateSignals({...input,match:{ready:true,probabilities:[{...row,pOver:.8,pUnder:.8}]}}).length,0);
console.log('signal quality regressions: OK');
