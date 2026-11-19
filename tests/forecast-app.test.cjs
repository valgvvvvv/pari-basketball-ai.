const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const core = require("../static/forecast-core.js");
const app = fs.readFileSync(require("node:path").join(__dirname, "../static/app.js"), "utf8");

const start = app.indexOf("function automaticQuarterForecast(ev) {");
const end = app.indexOf("\nlet liveRefreshInFlight", start);
assert.ok(start >= 0 && end > start, "quarter forecast function must exist");
const context = {
  window: { PARIForecastCore: core },
  Number, Math, Array, Map, Set, String, RegExp, Object,
  findLivePeriod: ev => ev.period,
  inferPeriodLengthForForecast: ev => ({seconds:ev.period_length_seconds, periods:ev.periods || 4, method:ev.format_method || "duration from fixture"}),
  findPeriodLengthSeconds: ev => ev.period_length_seconds,
  findLiveClock: ev => ev.clock,
  findQuarterClockFromElapsed: () => null,
  findLiveScore: ev => ev.score,
  readPeriodScore: (ev, index) => {
    const row = ev.period_scores?.[index];
    return Array.isArray(row) ? Number(row[0]) + Number(row[1]) : null;
  },
  currentQuarterScore: (ev, period, score) => {
    const row = ev.period_scores?.[period - 1];
    if (Array.isArray(row)) return {pair:row,source:"fixture quarter score"};
    if (period === 1 && Array.isArray(score)) return {pair:score,source:"fixture match score"};
    if (Array.isArray(score) && Array.isArray(ev.period_scores)) {
      const previous=ev.period_scores.slice(0,period-1);
      if(previous.length!==period-1||previous.some(x=>!Array.isArray(x)))return null;
      return {pair:[score[0]-previous.reduce((a,x)=>a+x[0],0),score[1]-previous.reduce((a,x)=>a+x[1],0)],source:"derived fixture"};
    }
    return null;
  }
};
vm.runInNewContext(app.slice(start,end) + "\nthis.runQuarter=automaticQuarterForecast;", context);
const forecast=context.runQuarter({
  id:"nanjing-xinjiang-fixed",period:3,clock:"08:57",period_length_seconds:600,periods:4,
  score:[42,42],period_scores:[[20,21],[21,21],[1,0]],match_total_candidates:[]
});
assert.equal(forecast.ready,true);
assert.ok(forecast.projection>25 && forecast.projection<50,
  "Q3 1:0 at 08:57 must use prior completed periods, not linear extrapolation to 9.5");
assert.equal(forecast.model_version,"quarter-gp-v1");
assert.equal(forecast.overtime_included,false);

const zero = context.runQuarter({
  id:"zero-q2",period:2,clock:"10:00",period_length_seconds:600,periods:4,
  score:[20,20],period_scores:[[20,20],[0,0]],match_total_candidates:[]
});
assert.equal(zero.ready,true);
assert.ok(zero.projection>20,"0:0 at the start of Q2 should use the completed Q1 prior");

const noPrior = context.runQuarter({
  id:"no-prior-q1",period:1,clock:"09:15",period_length_seconds:600,periods:4,
  score:[0,0],period_scores:[[0,0]],match_total_candidates:[]
});
assert.equal(noPrior.ready,false,"early Q1 without a league/team prior must be unavailable");

const unverifiedFormat = context.runQuarter({
  id:"unknown-format-q1",period:1,clock:"08:00",period_length_seconds:600,periods:4,
  format_method:"предварительно угаданный формат",score:[10,8],period_scores:[[10,8]],match_total_candidates:[]
});
assert.equal(unverifiedFormat.ready,false,"a guessed period format must not produce an ordinary quarter forecast");

const autoStart=app.indexOf("function automaticForecast(ev) {");
const autoEnd=app.indexOf("\nfunction quarterHighlightInfo",autoStart);
assert.ok(autoStart>=0&&autoEnd>autoStart);
let estimateCalls=0;
const autoContext={
  Number,Math,Array,String,Object,
  getMatchTotalLines:()=>[],
  findLiveScore:ev=>ev.score,
  findLivePeriod:ev=>ev.period,
  findLiveClock:ev=>ev.clock,
  inferPeriodLengthForForecast:ev=>({seconds:ev.period_length_seconds,periods:ev.external_live.period_count,method:ev.format_method||"confirmed"}),
  marketReferenceForecast:()=>null,
  estimateLiveTotal:()=>{estimateCalls++;return {current:80,projection:160,elapsed_seconds:1260,remaining_seconds:1140,elapsed_minutes:21,remaining_minutes:19,sigma:12};},
  applyPoissonModel:(estimate)=>({...estimate,poisson:{simulations:100000,probabilities:[],low80:140,high80:180},possession_model:{applied:false}}),
  estimateLiveTotalFromPeriod:()=>null,
  readPeriodScore:()=>null
};
vm.runInNewContext(app.slice(autoStart,autoEnd)+"\nthis.runAuto=automaticForecast;",autoContext);
const firstHalf=autoContext.runAuto({id:"q2-before-halftime",score:[40,40],period:2,clock:"05:00",
  period_length_seconds:600,external_live:{period_count:4}});
assert.equal(firstHalf.ready,false,"4-period full-game forecast must wait until Q3");
assert.equal(estimateCalls,0);
const secondHalf=autoContext.runAuto({id:"half2-start",score:[40,40],period:2,clock:"19:00",
  period_length_seconds:1200,external_live:{period_count:2}});
assert.equal(secondHalf.ready,true,"explicit 2x20 format may forecast in the second half");
assert.equal(estimateCalls,1);

const mcStart=app.indexOf("function dynamicScenarioMonteCarlo(");
const mcEnd=app.indexOf("\nfunction clampForecast",mcStart);
assert.ok(mcStart>=0&&mcEnd>mcStart);
const mcContext={
  Number,Math,Array,String,Object,
  clampForecast:(x,lo,hi)=>Math.max(lo,Math.min(hi,x)),
  inferPeriodLengthForForecast:()=>({seconds:600}),
  readPeriodScore:()=>null,
  findLiveScore:()=>[50,50],
  currentQuarterScore:()=>null
};
vm.runInNewContext(app.slice(mcStart,mcEnd)+"\nthis.runMonteCarlo=dynamicScenarioMonteCarlo;",mcContext);
const largeRate=mcContext.runMonteCarlo(100,80,[{line:180.5}],"ptrs-large-rate",{},3,null,"match");
assert.ok(Math.abs(largeRate.mean_exact-180)<0.8,"large-rate Poisson sampler should preserve the expected center");
assert.ok(largeRate.stddev_exact>8,"scenario mixture should retain predictive spread");
assert.ok(Math.abs(largeRate.probabilities[0].pOver+largeRate.probabilities[0].pUnder+largeRate.probabilities[0].pPush-1)<1e-9);

assert.ok(!app.includes('add("Байесовская",bayes'),
  "the old full-match/quarter-unit Bayesian blend must not re-enter the model list");
assert.ok(!app.includes('history.splice(oldIndex,1)'),
  "new snapshots must not replace earlier snapshots");
assert.ok(!app.includes('history.slice(-100)'),
  "local history must not silently discard earlier saved entries");

const classicStart = app.indexOf("function classicBasketballModelAnalysis(");
const classicEnd = app.indexOf("\nfunction classicModelPanelHtml", classicStart);
assert.ok(classicStart >= 0 && classicEnd > classicStart);
const classicContext = {
  Number, Math, Array, String, Object, Set,
  findLivePeriod: ev => ev.period,
  findLiveClock: ev => ev.clock,
  inferPeriodLengthForForecast: () => ({seconds:600,periods:4,method:"confirmed"}),
  readPeriodScore: (_ev,index) => [40,42][index] ?? null,
  currentQuarterScore: () => ({pair:[1,0]}),
  findLiveScore: ev => ev.score,
  clampForecast: (x,lo,hi) => Math.max(lo,Math.min(hi,x))
};
vm.runInNewContext(app.slice(classicStart,classicEnd) +
  "\nthis.runClassic=classicBasketballModelAnalysis;", classicContext);
for (const projection of [172.4,215.7,219.6,212.1]) {
  const ev = {period:3,clock:"06:33",score:[59,69],period_length_seconds:600,
    external_live:{period_count:4,period_length_seconds:600}};
  const forecast = {ready:true,kind:"live_tempo",estimate:{
    current:128,projection,sigma:12,elapsed_seconds:1407,remaining_seconds:993,
    remaining_minutes:16.55,poisson:{simulations:100000,low80:projection-12,high80:projection+12,
      low95:projection-20,high95:projection+20,probabilities:[]},
    possession_model:{applied:false}
  },lines:[{line:206.5}]};
  const models = classicContext.runClassic(ev,forecast,{ready:false},"match");
  assert.equal(models.consensus,null,"correlated alternatives must not form an unvalidated consensus");
  assert.equal(models.groups.length,0,"model coincidence must not be presented as independent evidence");
  assert.ok(!models.models.some(x => /Байесовская/.test(x.name)),
    "the unit-mismatched Bayesian full-match model must be excluded");
  assert.ok(!models.models.some(x => Math.abs(x.projection-132)<0.2 || Math.abs(x.projection-166.4)<0.2),
    "the observed Bayesian outlier values must not reappear as alternatives");
}
console.log("application forecast regressions passed");
