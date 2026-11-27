const assert=require('node:assert/strict'),core=require('../static/forecast-core.js');
const now=Date.parse('2026-11-27T12:00:00Z');
const market=(target='quarter',line=50)=>({eventId:'a',target,period:4,line,suspended:false,pricesVerified:true,
  overOdds:1.9,underOdds:1.9,quotedAtUtc:new Date(now).toISOString(),rulesConfirmed:true,ruleSource:'PARI',includesOvertime:false});
const estimate=(target='quarter',line=50,p=.75)=>({target,line,pOver:p,pUnder:1-p,pPush:0,calculationAvailable:true,reasonCodes:['calibration','validation'],source:'primary'});
const input={eventId:'a',nowMs:now,ageSeconds:10,timerVerified:true,scoreValid:true,paused:false,period:4,periodCount:4,
  periodLengthSeconds:600,remainingSeconds:240,currentMatch:100,currentPeriod:20,markets:[market()],estimates:[estimate()]};
const rank=patch=>core.rankLiveMatch({...input,...patch});
assert.equal(rank({}).tier,0);assert.equal(rank({}).probability,.75);assert.equal(rank({}).priority,false);
assert.equal(rank({paused:true}).tier,2);assert.equal(rank({remainingSeconds:60}).tier,2);
assert.equal(rank({remainingSeconds:600}).tier,2);assert.equal(rank({ageSeconds:91}).tier,2);
assert.equal(rank({timerVerified:false}).tier,2);assert.equal(rank({scoreValid:false}).tier,2);
for(const change of [{suspended:true},{suspended:null},{pricesVerified:false},{underOdds:1},{eventId:'other'},
  {period:3},{quotedAtUtc:new Date(now-91000).toISOString()},{quotedAtUtc:new Date(now+1000).toISOString()},{line:19}]){
  assert.equal(rank({markets:[{...market(),...change}]}).tier,2,JSON.stringify(change));
}
assert.equal(rank({markets:[{...market(),includesOvertime:true}]}).tier,1,'open OT line cannot use regulation probability');
assert.equal(rank({markets:[{...market(),rulesConfirmed:false}]}).tier,1);
assert.equal(rank({estimates:[]}).tier,1);
assert.equal(rank({estimates:[{...estimate(),reasonCodes:['preliminary']}]}).tier,1);
const central=rank({markets:[market('quarter',40),market('quarter',50),market('quarter',60)],
  estimates:[estimate('quarter',40,.99),estimate('quarter',50,.6),estimate('quarter',60,.98)]});
assert.equal(central.line,50);assert.equal(central.probability,.6,'central line is chosen before looking at probabilities');
const mixed=rank({markets:[market(),market('match',150)],estimates:[estimate(),estimate('match',150,.82)]});
assert.equal(mixed.target,'match');assert.equal(mixed.probability,.82);
assert.equal(rank({period:1,remainingSeconds:30,markets:[{...market('match',150),period:1}],estimates:[estimate('match',150,.7)]}).tier,0,
  'a late Q1 must not hide an available whole-game market');
assert.equal(rank({markets:[{...market(),target:'half',period:2}],period:2,periodCount:2,periodLengthSeconds:1200,
  estimates:[estimate('half',50,.72)]}).target,'half');
const rows=[{id:'closed',tier:2,probability:.99},{id:'weak',tier:0,probability:.65},{id:'strong',tier:0,probability:.85},{id:'unknown',tier:1,probability:null}];
assert.deepEqual(rows.sort(core.compareLiveMatches).map(x=>x.id),['strong','weak','unknown','closed']);
assert.equal(core.compareLiveMatches({id:'a',tier:0,probability:.7},{id:'b',tier:0,probability:.7}),-1,'stable ID tie prevents random order');
console.log('match order: open markets, pause, late periods, freshness, central line, OT and stable probability ranking OK');
const ranks=(pa,pb,tb=0)=>[{id:'a',tier:0,probability:pa},{id:'b',tier:tb,probability:pb}];
let state=core.stableLiveQueue(ranks(.7,.69),null,0);
assert.deepEqual(state.ids,['a','b']);
state=core.stableLiveQueue(ranks(.69,.7),state,45000);
assert.deepEqual(state.ids,['a','b'],'one-point jitter must not move cards');
state=core.stableLiveQueue(ranks(.6,.9),state,50000);
assert.deepEqual(state.ids,['a','b'],'even large probability moves wait for the 45-second reorder window');
state=core.stableLiveQueue(ranks(.6,.9),state,90000);
assert.deepEqual(state.ids,['b','a']);
state=core.stableLiveQueue(ranks(.6,.9,2),state,91000);
assert.deepEqual(state.ids,['a','b'],'closed market demotes immediately');
state=core.stableLiveQueue(ranks(.6,.9),state,92000);
assert.equal(state.items.b.tier,2,'reopening is debounced');
state=core.stableLiveQueue(ranks(.6,.9),state,111000);
assert.equal(state.items.b.tier,2);
state=core.stableLiveQueue(ranks(.6,.9),state,112000);
assert.equal(state.items.b.tier,0,'only a continuously open market returns after 20 seconds');
state=core.stableLiveQueue([{id:'a',tier:0,probability:.6}],state,120000);
assert.deepEqual(state.ids,['a']);assert.equal(state.items.b,undefined,'removed events leave the queue state');
console.log('stable queue: probability hysteresis, cooldown, reopening debounce and removed IDs OK');
