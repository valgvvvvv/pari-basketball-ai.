const assert = require("node:assert/strict");
const core = require("../static/forecast-core.js");
const priorityFixture={ageSeconds:20,period:4,elapsedSeconds:240,periodLengthSeconds:600,timerVerified:true,scoreValid:true,clockValid:true,
  quarter:{ready:true,preliminary:false,probabilities:[{line:42.5,pOver:.72,pUnder:.28,pPush:0}]},
  match:{ready:true,preliminary:false,probabilities:[{line:180.5,pOver:.25,pUnder:.75,pPush:0}]}};
assert.equal(core.selectPrioritySignals(priorityFixture).length,0,"uncalibrated forecasts must not become confident signals");
assert.equal(core.evaluateSignals(priorityFixture)[0].direction,"ТМ");
for(const patch of [{ageSeconds:null},{ageSeconds:91},{ageSeconds:-1},{timerVerified:false},{scoreValid:false},{clockValid:false},{period:5}])
  assert.equal(core.selectPrioritySignals({...priorityFixture,...patch}).length,0);
for(const elapsedSeconds of [239,481])
  assert.equal(core.selectPrioritySignals({...priorityFixture,elapsedSeconds}).filter(x=>x.target==="quarter").length,0);
assert.equal(core.evaluateSignals({...priorityFixture,elapsedSeconds:480}).filter(x=>!x.reasonCodes.includes('window')).length,2);
assert.equal(core.selectPrioritySignals({...priorityFixture,period:2}).filter(x=>x.target==="match").length,0);
for(const period of [1,2,3])
  assert.equal(core.selectPrioritySignals({...priorityFixture,period}).filter(x=>x.target==="match").length,0);
for(const elapsedSeconds of [null,undefined,0,60,119,600,720])
  assert.equal(core.selectPrioritySignals({...priorityFixture,elapsedSeconds}).filter(x=>x.target==="match").length,0);
for(const elapsedSeconds of [120,121,481,599])
  assert.equal(core.evaluateSignals({...priorityFixture,elapsedSeconds}).filter(x=>x.target==="match"&&!x.reasonCodes.includes('window')).length,1);
assert.equal(core.evaluateSignals({...priorityFixture,periodLengthSeconds:720,elapsedSeconds:719}).filter(x=>x.target==="match"&&!x.reasonCodes.includes('data')).length,1);
for(const probabilities of [[{line:42.5,pOver:.8,pUnder:.8}],[{line:null,pOver:.9,pUnder:.1}],[{line:42.5,pOver:.64,pUnder:.36}],[{line:42.5,pOver:.9,pUnder:.1,stale:true}]])
  assert.equal(core.selectPrioritySignals({...priorityFixture,quarter:{ready:true,probabilities}}).filter(x=>x.target==="quarter").length,0);
const fs = require("node:fs");
const vm = require("node:vm");
const appSource = fs.readFileSync(require("node:path").join(__dirname, "../static/app.js"), "utf8");
const htmlSource = fs.readFileSync(require("node:path").join(__dirname, "../static/index.html"), "utf8");
const archivePublisherSource = fs.readFileSync(require("node:path").join(__dirname, "../.github/scripts/publish_daily_archive.py"), "utf8");
const archiveCollectorSource = fs.readFileSync(require("node:path").join(__dirname, "../.github/scripts/collect_live_with_models.cjs"), "utf8");

const earlyQ3 = core.quarterPosterior({
  current: 1, elapsedSeconds: 63, periodLengthSeconds: 600,
  completedQuarterTotals: [42, 44]
});
assert.equal(earlyQ3.ready, true);
assert.ok(earlyQ3.projection > 30 && earlyQ3.projection < 50,
  "Q3 1:0 after 63 seconds must not extrapolate 1 point to a 9.5-point quarter");
assert.ok(earlyQ3.varianceRemaining > earlyQ3.expectedRemaining,
  "posterior predictive variance must include rate uncertainty");
assert.ok(earlyQ3.meanUncertaintyVariance > 0 && earlyQ3.meanUncertaintySd > 0);
assert.ok(Math.abs(earlyQ3.varianceRemaining - earlyQ3.expectedRemaining - earlyQ3.meanUncertaintyVariance) < 1e-9,
  "predictive variance must separate point-process variance from posterior mean uncertainty");

const zeroAtStart = core.quarterPosterior({
  current: 0, elapsedSeconds: 0, periodLengthSeconds: 600,
  completedQuarterTotals: [40, 46]
});
assert.equal(zeroAtStart.ready, true);
assert.ok(zeroAtStart.projection >= 40 && zeroAtStart.projection <= 46);

const riverportQ2 = core.quarterPosterior({
  current: 4, elapsedSeconds: 37, periodLengthSeconds: 600,
  completedQuarterTotals: [44]
});
assert.equal(riverportQ2.ready, true);
assert.equal(riverportQ2.alpha, 48.5);
assert.ok(Math.abs(riverportQ2.beta - (10 + 37/60)) < 1e-9);
assert.ok(Math.abs(riverportQ2.expectedRemaining - 42.85) < 0.03);
assert.ok(Math.abs(riverportQ2.projection - 46.85) < 0.03,
  "Riverport Q2 must use the Q1 prior, not current tempo × remaining time");

const nanjingLiveScore = [38, 53];
const nanjingCurrentQuarterScore = [1, 0];
const nanjingPriorPoints = nanjingLiveScore[0] + nanjingLiveScore[1] -
  nanjingCurrentQuarterScore[0] - nanjingCurrentQuarterScore[1];
const nanjingOldSnapshot = core.quarterPosterior({
  current: nanjingCurrentQuarterScore[0] + nanjingCurrentQuarterScore[1],
  elapsedSeconds: 63, periodLengthSeconds: 600,
  priorPoints: nanjingPriorPoints, priorExposureSeconds: 1200, priorQuarterCount: 2
});
assert.equal(nanjingOldSnapshot.ready, true);
assert.equal(nanjingOldSnapshot.priorQuarterCount, 2);
assert.equal(nanjingOldSnapshot.priorIsAggregate, true);
assert.equal(nanjingOldSnapshot.completedPoints, 90);
assert.ok(Math.abs(nanjingOldSnapshot.projection - 39.90380047505938) < 1e-8,
  "fixed Nanjing-Xinjiang live score 38:53 includes Q3 1:0; prior Q1+Q2 total is 90 over 20 minutes");
assert.equal(nanjingOldSnapshot.elapsedSeconds, 63);
assert.ok(Math.abs(nanjingOldSnapshot.beta - 21.05) < 1e-9);
const nanjingDistribution = core.simulateQuarter(nanjingOldSnapshot, [{line:40.5},{line:40}], "nanjing-xinjiang-fixed-snapshot", 100000);
assert.ok(Math.abs(nanjingDistribution.mu - nanjingOldSnapshot.expectedRemaining) < 1e-9,
  "predictive distribution mean must equal posterior expected remaining points");
// Exact negative-binomial quantile; the previous normal-approximation sampler returned 30.
assert.equal(nanjingDistribution.low80, 31);
assert.equal(nanjingDistribution.high80, 50);
assert.ok(Math.abs(nanjingDistribution.variance - nanjingDistribution.mu - nanjingDistribution.meanUncertaintyVariance) < 1e-8,
  "predictive variance must equal Poisson variance plus posterior mean uncertainty");
const nanjingHalfLine = nanjingDistribution.probabilities.find(x => x.line === 40.5);
const nanjingIntegerLine = nanjingDistribution.probabilities.find(x => x.line === 40);
assert.ok(nanjingHalfLine.pUnder < 1, "fixed Nanjing snapshot must not claim TM 100% from one early point");
assert.equal(nanjingHalfLine.pPush, 0);
assert.equal(nanjingHalfLine.direction, "без выраженного преимущества");
assert.ok(nanjingOldSnapshot.projection < 40.5, "numeric center must remain separately below the 40.5 line");
assert.ok(nanjingIntegerLine.pPush > 0, "integer market line must retain push probability");
for (const row of [nanjingHalfLine,nanjingIntegerLine]) {
  assert.ok(Math.abs(row.pOver + row.pUnder + row.pPush - 1) < 1e-9,
    "over/under/push probabilities must sum to one for the fixed snapshot");
}

const illawarraQ3 = core.quarterPosterior({
  current: 11, elapsedSeconds: 149, periodLengthSeconds: 600,
  completedQuarterTotals: [49, 46]
});
assert.equal(illawarraQ3.ready, true);
assert.equal(illawarraQ3.priorQuarterCount, 2);
assert.equal(illawarraQ3.completedPoints, 95);
assert.equal(illawarraQ3.priorAlpha, 95.5);
assert.ok(illawarraQ3.beta > 22.48 && illawarraQ3.beta < 22.49);
assert.ok(illawarraQ3.expectedRemaining > 35 && illawarraQ3.expectedRemaining < 36,
  "Illawarra Q3 must include Q1 and Q2 history in its posterior");

const noPriorEarly = core.quarterPosterior({
  current: 1, elapsedSeconds: 63, periodLengthSeconds: 600,
  completedQuarterTotals: []
});
assert.equal(noPriorEarly.ready, false);

const integerLine = core.simulateQuarter(earlyQ3, [{line:40}], "integer-line-regression", 100000);
assert.ok(integerLine.probabilities[0].pPush > 0, "integer totals must have an explicit push probability");
assert.ok(integerLine.meanUncertaintySd > 0, "scenario/parameter mean uncertainty must be exposed separately");
const halfLine = core.simulateQuarter(earlyQ3, [{line:40.5}], "half-line-regression", 100000);
assert.equal(halfLine.probabilities[0].pPush, 0, "half-point lines cannot push");
for (const row of [...integerLine.probabilities, ...halfLine.probabilities]) {
  const sum = row.pOver + row.pUnder + row.pPush;
  assert.ok(Math.abs(sum - 1) < 1e-9, "over/under/push probabilities must sum to one");
}

const aiStart = appSource.indexOf("function aiModelAnalysis(ev,matchForecast,quarterForecast){");
const aiEnd = appSource.indexOf("function classicBasketballModelAnalysis(", aiStart);
assert.ok(aiStart >= 0 && aiEnd > aiStart);
const aiSandbox = {};
vm.runInNewContext(appSource.slice(aiStart, aiEnd) + "; this.aiModelAnalysis = aiModelAnalysis;", aiSandbox);
const matchFixture = {
  ready: true, kind: "live_tempo",
  estimate: { current: 128, projection: 217.2, poisson: { mu: 89.2, probabilities: [{line:206.5,pOver:0.71,pUnder:0.29,pPush:0}] } }
};
const quarterFixture = {
  ready: true, current: 1, projection: 40.8,
  poisson: { mu: 39.8, probabilities: [{line:40,pOver:0.48,pUnder:0.47,pPush:0.05}] },
  lines: [{line:40,pOver:0.48,pUnder:0.47,pPush:0.05}]
};
const ai = aiSandbox.aiModelAnalysis({}, matchFixture, quarterFixture);
assert.equal(ai.match.projection, matchFixture.estimate.projection);
assert.equal(ai.match.probabilities[0].pOver, matchFixture.estimate.poisson.probabilities[0].pOver);
assert.equal(ai.quarter.projection, quarterFixture.projection);
assert.equal(ai.quarter.probabilities[0].pPush, quarterFixture.poisson.probabilities[0].pPush);

const marketStart = appSource.indexOf("function getMatchTotalLines(ev) {");
const marketEnd = appSource.indexOf("function inferPeriodLengthForForecast(ev)", marketStart);
assert.ok(marketStart >= 0 && marketEnd > marketStart);
const marketSandbox = {localStorage:{setItem(){}}};
vm.runInNewContext(appSource.slice(marketStart, marketEnd) + "; this.getMatchTotalLines = getMatchTotalLines;", marketSandbox);
const market = marketSandbox.getMatchTotalLines({id:"market-fixture",match_total_candidates:[
  {f:"930",pt:"206.5",v:"1.91"},{f:"931",pt:"206.5",v:"1.89"}
]});
assert.equal(market.length, 1);
assert.equal(market[0].prices.length, 2, "both raw market prices must be retained");
assert.deepEqual(Array.from(market[0].prices, x=>x.factorId), ["930","931"]);

assert.ok(!appSource.includes('const bayes=linear*0.68+prior*0.32'),
  "legacy Bayesian calculation that mixed full-game and quarter totals must stay removed");
assert.ok(appSource.includes("One canonical forecast drives every headline block"));
assert.ok(appSource.includes("Date.now()-feedChecked>120000"), "stale API snapshots must be rejected");
assert.ok(appSource.includes("validateScoreTimeline"), "period sums must be checked against the match score");
assert.ok(appSource.includes("providerEventTimestampAvailable"), "provider event freshness must be distinguished from API response age");
assert.ok(appSource.includes("full_play_by_play") && appSource.includes("score_clock_period_results") && appSource.includes("score_clock_only"), "data coverage modes must be explicit");
assert.ok(appSource.includes("/api/archive/recent?minutes=10"), "live refresh must retrieve timestamped rolling snapshots");
assert.ok(appSource.includes("const renderedMatchCards = count ? window.pariLiveEvents.map"), "live list cards must use events enriched with recent snapshots");
assert.ok(appSource.includes("scoreSource") && appSource.includes("clockSource") && appSource.includes("statisticsSource"), "source provenance must be tracked by layer");
assert.ok(appSource.includes("probText=p=>p?"), "headline probabilities must come from the forecast distribution only");
assert.ok(appSource.includes("Вероятности показаны только из собственного распределения"), "tool registry must distinguish model probabilities from bookmaker odds");
assert.ok(appSource.includes("unifiedToolsPanelHtml") && appSource.includes("Недоступны / отключены:"), "all tools must be available in one shared registry");
assert.ok(appSource.includes("formatElapsedClock(posterior.elapsedSeconds)"), "quarter elapsed time must be formatted as mm:ss");
assert.ok(!appSource.includes('forecastHtml+=\'<span class="live-match-meta forecast-live">ИИ · матч:'), "live cards must not repeat the primary match forecast as an AI forecast");
assert.ok(appSource.includes("Веса сценариев заданы эвристическим правилом, не обучены"), "scenario weights must be explicitly disclosed as heuristic and untrained");
assert.ok(!appSource.includes('forecastHtml+=\'<span class="live-match-meta forecast-live">ИИ · Q'), "live cards must not repeat the primary quarter forecast as an AI forecast");
const applyPoissonStart=appSource.indexOf("function applyPoissonModel(");
const applyPoissonEnd=appSource.indexOf("function normalCdf(",applyPoissonStart);
const applyPoissonSource=appSource.slice(applyPoissonStart,applyPoissonEnd);
assert.ok(!applyPoissonSource.includes("dataQuality.rolling_snapshot_count="), "data quality fields must not be written before dataQuality is initialized");
assert.ok(appSource.includes("priorExposureSeconds:aggregatePriorExposureSeconds"), "missing period splits must use aggregate historical score and exact exposure");
assert.ok(!appSource.slice(appSource.indexOf("function saveAnalysisHistory("),appSource.indexOf("function clearAnalysisHistory(")).includes("if(!estimate&&!quarter?.ready)return"), "raw snapshot history must persist even when no forecast is available");
assert.ok(appSource.includes('forecastStatus:estimate||quarter?.ready?"calculated":"not_available"'), "history must distinguish snapshot from forecast availability");
assert.ok(htmlSource.includes("20261127-match-order-v16"), "readiness fix must bump frontend cache");
assert.ok(htmlSource.includes("confirmedIndependentMatches") && htmlSource.includes("Финалы ещё не сверяются автоматически"), "quality panel must show zero confirmed independent finals and not claim accuracy without settlement");
assert.ok(archivePublisherSource.startsWith("import hashlib"), "permanent archive publisher must import hashlib before calculating checksums");
assert.ok(archiveCollectorSource.includes("contract.snapshot_id = row.snapshot_id") && archiveCollectorSource.includes("contract.contract_id = crypto.createHash"), "all prediction blocks from one source update must share snapshot_id; target identity uses contract_id");
assert.ok(archiveCollectorSource.includes('registryText.includes("Недоступны / отключены:")'), "browser acceptance must verify disabled tools remain accessible in the shared registry");
assert.ok(!archiveCollectorSource.includes("window.automaticForecast") && archiveCollectorSource.includes("typeof automaticForecast === \"function\""), "browser collector must call classic-script lexical functions rather than assume they are window properties");
assert.ok(archiveCollectorSource.includes("result.source_checked_at_utc || row.captured_at_utc") && archiveCollectorSource.includes("String(row.score || \"\"),"), "archive snapshot IDs must use the same source timestamp, score, period and clock key as the UI");
assert.ok(archiveCollectorSource.includes("source_event_snapshot: JSON.parse(JSON.stringify(rawEvent))") && archiveCollectorSource.includes("model_versions:"), "archive must preserve immutable raw source event and method versions separately from forecasts");
assert.ok(appSource.includes("Применимо: ") && appSource.includes("рассчитано сейчас:"), "registry must show factual tool counts");
assert.ok(htmlSource.includes("20261127-match-order-v16"), "frontend cache must be bumped after display fixes");
assert.ok(appSource.includes("const qLines=") && !appSource.includes("qLines+="), "quarter forecast text must be immutable after declaration");
const timerFnStart = appSource.indexOf("function findQuarterClockFromElapsed(");
const timerFnEnd = appSource.indexOf("function automaticQuarterForecast(", timerFnStart);
assert.ok(timerFnStart >= 0 && timerFnEnd > timerFnStart, "quarter clock helper must be available for behavior tests");
const timerSandbox = {};
vm.runInNewContext(appSource.slice(timerFnStart, timerFnEnd) +
  "; this.findQuarterClockFromElapsed=findQuarterClockFromElapsed;", timerSandbox);
assert.equal(timerSandbox.findQuarterClockFromElapsed({
  timer_seconds:405, period_elapsed_seconds:405, timer_semantics_verified:false
},1,600), null, "raw timer values without verified semantics must not become a countdown");
assert.equal(timerSandbox.findQuarterClockFromElapsed({
  period_elapsed_seconds:5, timer_semantics_verified:true
},1,600), "09:55", "verified period elapsed time must produce a quarter countdown");

assert.ok(appSource.includes("Ансамбль сценариев") && appSource.includes("не независимый голос"), "scenario mixture must be shown without counting it as an independent model");
assert.ok(!appSource.includes('add("Динамика четверти"'), "a duplicate of the primary quarter forecast must not appear as a separate method");
assert.ok(!appSource.includes("Единый прогноз · повтор основного расчёта"), "duplicate primary forecast cards must stay removed");
assert.ok(appSource.includes("Модель без ОТ; тотал матча PARI с ОТ (правило 8.1)"), "list forecast must not imply overtime is included");
// Missing clocks must not produce a model forecast that could enter the archive as a prediction.
const autoStart = appSource.indexOf("function automaticForecast(ev)");
const autoEnd = appSource.indexOf("function quarterHighlightInfo(ev)", autoStart);
const noClockSandbox = {
  findLiveScore: () => [51, 57], findLivePeriod: () => 3,
  findLiveClock: () => null, validateScoreTimeline: () => ({valid:true}),
  getMatchTotalLines: () => [{line:206.5}],
  inferPeriodLengthForForecast: () => ({seconds:600, periods:4}),
  marketReferenceForecast: lines => ({ready:true,kind:"market_reference",lines}),
  estimateLiveTotal: () => { throw new Error("No model may run without a clock"); }
};
vm.runInNewContext(appSource.slice(autoStart, autoEnd) + ";this.run=automaticForecast;", noClockSandbox);
assert.equal(noClockSandbox.run({}).kind, "market_reference");
assert.ok(noClockSandbox.run({}).reason.includes("таймер"));
assert.ok(appSource.includes("const mu=possMu*secondaryAdjustment,mc=dynamicScenarioMonteCarlo"), "tempo momentum must not shift the mean and the scenario distribution twice");
assert.ok(!appSource.includes("possMu*secondaryAdjustment*tempoFactor"), "duplicate tempo multiplier must stay out of the primary mean");
assert.ok(appSource.includes("Импульс MC"), "tempo momentum must be visible as a distribution-scenario effect");
assert.ok(appSource.includes("research_scheme:true") && appSource.includes("calibration_status:\"not_calibrated\""), "scenario mixture must be explicitly labeled experimental and uncalibrated");
assert.ok(appSource.includes("momentum_rule") && appSource.includes("scenario_weight_rule"), "experimental momentum and scenario weights must be inspectable");
const clockStart = appSource.indexOf("function findLiveClock(ev){");
const clockEnd = appSource.indexOf("function estimateLiveTotal(ev", clockStart);
assert.ok(clockStart >= 0 && clockEnd > clockStart);
assert.ok(!appSource.slice(clockStart, clockEnd).includes("inferPeriodLengthForForecast("),
  "clock parsing must not recursively call format inference");
const periodSourceStart = appSource.indexOf("function findLivePeriod(ev){");
const periodSourceEnd = appSource.indexOf("function estimateLiveTotal(ev", periodSourceStart);
const formatSourceStart = appSource.indexOf("function findPeriodLengthSeconds(ev)");
const formatSourceEnd = appSource.indexOf("function estimateLiveTotalFromPeriod(", formatSourceStart);
const runtimeSource = appSource.slice(periodSourceStart, periodSourceEnd) +
  appSource.slice(formatSourceStart, formatSourceEnd) +
  "; this.findLiveClock=findLiveClock; this.inferPeriodLengthForForecast=inferPeriodLengthForForecast;";
const runtimeSandbox = {collectEventObjects:()=>[], localStorage:{setItem(){}}};
vm.runInNewContext(runtimeSource, runtimeSandbox);
assert.equal(runtimeSandbox.findLiveClock({period:3,clock:"08:57"}), "08:57");
assert.equal(runtimeSandbox.findLiveClock({period:1,match_elapsed_seconds:null,period_length_seconds:600}),null,"null is not a zero-second timer");
assert.equal(runtimeSandbox.findLiveClock({period:3,clock:"11:00",period_length_seconds:600}),null,"clock cannot exceed confirmed quarter duration");
const stoppedTimer={period:3,period_length_seconds:600,match_elapsed_seconds:1500,
  clock_source:"PARI_timer_and_period_consistency",timer_timestamp_msec:Date.now()-30000,timer_direction:0};
assert.equal(runtimeSandbox.findLiveClock(stoppedTimer),"05:00","wall time must not advance a source snapshot");
assert.equal(runtimeSandbox.findLiveClock({...stoppedTimer,clock_integrity:{valid:false}}),null,"integrity gates apply before all fallback branches");
const highlightSource=appSource.slice(appSource.indexOf("function quarterHighlightInfo("),appSource.indexOf("function prioritySignals("));
vm.runInNewContext(highlightSource+";this.highlight=quarterHighlightInfo",runtimeSandbox);
assert.equal(runtimeSandbox.highlight({period:3,clock:"06:00",period_length_seconds:600}).elapsed,240);
assert.equal(runtimeSandbox.highlight({period:3,clock:"02:00",period_length_seconds:600}).elapsed,480);
assert.equal(runtimeSandbox.highlight({period:3,clock:"06:01",period_length_seconds:600}),null);
assert.equal(runtimeSandbox.highlight({period:2,clock:"15:00",period_length_seconds:1200,period_count:2}),null,"a half is not a quarter-window candidate");
const prioritySandbox={window:{PARIForecastCore:core},isTwentyMinuteHalves:()=>false,findLiveClock:e=>e.clock,
  inferPeriodLengthForForecast:()=>({seconds:600}),findLivePeriod:e=>e.period,
  findLiveScore:()=>[60,60],validateScoreTimeline:()=>({valid:true})};
vm.runInNewContext(appSource.slice(appSource.indexOf("function scoringRegime("),appSource.indexOf("function priorityLabel("))+";this.select=prioritySignals;this.evaluate=signalEvaluations;",prioritySandbox);
const matchForPriority={ready:true,kind:"live_tempo",estimate:{current:120,poisson:{probabilities:priorityFixture.match.probabilities}}};
const signalEvent={period:4,clock:"08:00",feed_checked_at_utc:new Date().toISOString(),timer_semantics_verified:true};
assert.equal(prioritySandbox.evaluate(signalEvent,matchForPriority,{ready:false}).filter(x=>!x.reasonCodes.includes('window')).length,1,"Q4 2:00 elapsed must not depend on the separate 4–8-minute quarter window");
assert.equal(prioritySandbox.select({...signalEvent,clock:"08:01"},matchForPriority,{ready:false}).length,0);
assert.equal(prioritySandbox.select({...signalEvent,clock:"01:00"},matchForPriority,{ready:false}).length,0,"missing calibration cannot be bypassed late in Q4");
assert.equal(prioritySandbox.select({...signalEvent,period:3},matchForPriority,{ready:false}).length,0);
assert.equal(runtimeSandbox.inferPeriodLengthForForecast({period:3,league:"Unknown league",clock:"08:57"}), null, "unknown format must not be guessed from period or clock");
assert.equal(runtimeSandbox.inferPeriodLengthForForecast({period:2,period_length_seconds:1200,external_live:{period_count:2},clock:"15:00"}).periods, 2);
assert.equal(runtimeSandbox.inferPeriodLengthForForecast({
  period:3,live_fields:{period_length_seconds:600,period_count:4,period_format_source:{source:"KBL official rules"}}
}).seconds,600,"PARI live_fields must supply the verified competition format");
assert.equal(runtimeSandbox.inferPeriodLengthForForecast({
  period:3,live_fields:{period_length_seconds:600,period_count:4}
}).periods,4);
const clockSourceStart=appSource.indexOf("function eventClock(ev)");
const clockSourceEnd=appSource.indexOf("function findPeriodLengthSeconds(ev)",clockSourceStart);
const clockSandbox={findLiveClock:()=>null,findPeriodLengthSeconds:()=>null};
vm.runInNewContext(appSource.slice(clockSourceStart,clockSourceEnd)+"; this.eventClock=eventClock;",clockSandbox);
assert.match(clockSandbox.eventClock({live_fields:{source_timer_seconds:405}}),/Время получено, длительность периода не подтверждена/);
assert.match(clockSandbox.eventClock({live_fields:{source_timer_seconds:405}}),/06:45 \(405 с\)/);
assert.ok(appSource.includes("ev?.live_fields?.timer_semantics_verified"),"timer semantics must be read from PARI live_fields");
const fallbackStart=appSource.indexOf("function liveMatchRenderFallback(ev)");
const fallbackEnd=appSource.indexOf("async function refreshLiveMatches()",fallbackStart);
const fallbackSandbox={
  escapeHtml:value=>String(value??""),
  eventScore:()=> "21:17",eventPeriod:()=> "Q1",
  eventClock:()=> "Время получено, длительность периода не подтверждена",
  console:{error:()=>{}}
};
vm.runInNewContext(appSource.slice(fallbackStart,fallbackEnd)+"; this.renderFallback=liveMatchRenderFallback;",fallbackSandbox);
const fallbackHtml=fallbackSandbox.renderFallback({
  id:"x",team1:"A",team2:"B",source_timer_seconds:405,
  external_statistics:[{name:"2PT",home:4,away:6}],
  period_statistics:[{period:1,name:"3PT",home:2,away:1}],
  match_total_candidates:[{pt:165,v:1.9}]
});
assert.match(fallbackHtml,/2PT 4:6/,"fallback card must show available match statistics");
assert.match(fallbackHtml,/Q1 3PT 2:1/,"fallback card must show available quarter statistics");
assert.match(fallbackHtml,/Тотал 165/,"fallback card must preserve available markets");


const recentStart = appSource.indexOf("function recentTempoFromSnapshots");
const recentEnd = appSource.indexOf("function dynamicScenarioMonteCarlo", recentStart);
const recentSource = appSource.slice(recentStart, recentEnd) +
  "; this.recentTempoFromSnapshots=recentTempoFromSnapshots;";
const recentSandbox = {
  inferPeriodLengthForForecast:()=>({method:"verified",seconds:600}),
  findLivePeriod:()=>3,
  findLiveScore:event=>event.score
};
vm.runInNewContext(recentSource, recentSandbox);
const now = Date.now();
const rollingEvent = {
  period_length_seconds:600, score:[52,43],
  feed_checked_at_utc:new Date(now).toISOString(),
  recent_snapshots:[{period:3,captured_at:new Date(now-240000).toISOString(),clock:"05:00",score:[40,35]}]
};
const rolling = recentSandbox.recentTempoFromSnapshots(rollingEvent,3,"01:00",600);
assert.equal(rolling.applied,true);
assert.equal(rolling.score_delta,20);
assert.equal(rolling.home_delta,12);
assert.equal(rolling.away_delta,8);
assert.ok(rolling.rawChange>60);
const corrected = recentSandbox.recentTempoFromSnapshots({...rollingEvent,score:[39,35]},3,"01:00",600);
assert.equal(corrected.applied,false, "score corrections must invalidate rolling tempo");
const insufficient = recentSandbox.recentTempoFromSnapshots({...rollingEvent,recent_snapshots:[
  {period:3,captured_at:new Date(now-90000).toISOString(),clock:"02:30",score:[45,40]}
]},3,"01:00",600);
assert.equal(insufficient.applied,false, "less than three minutes of snapshots is not a rolling signal");
const mcStart = appSource.indexOf("function dynamicScenarioMonteCarlo");
const mcEnd = appSource.indexOf("function poissonMonteCarlo", mcStart);
const mcSource = appSource.slice(mcStart, mcEnd) +
  "; this.dynamicScenarioMonteCarlo=dynamicScenarioMonteCarlo;";
const mcSandbox = {
  inferPeriodLengthForForecast:()=>({seconds:600,method:"verified"}),
  readPeriodScore:()=>40,
  findLiveScore:()=>[50,50],
  currentQuarterScore:()=>({pair:[10,10]}),
  recentTempoFromSnapshots:()=>({applied:true,rawChange:20,source:"rolling_3_5_min"}),
  clampForecast:(value,min,max)=>Math.max(min,Math.min(max,value))
};
vm.runInNewContext(mcSource, mcSandbox);
const mcResult = mcSandbox.dynamicScenarioMonteCarlo(100,80,[],"rolling-test",
  {id:"fixture",period_length_seconds:600,period_count:4,external_live:{period_length_seconds:600}},
  3,"01:00","match");
assert.equal(mcResult.momentum_source,"rolling_3_5_min");
assert.equal(mcResult.momentum,20);
assert.equal(mcResult.research_scheme,true);
assert.ok(htmlSource.indexOf("forecast-core.js") < htmlSource.indexOf("app.js"),
  "the tested forecast core must load before the UI code");
assert.ok(htmlSource.includes("20261127-match-order-v16"), "forecast core cache must be refreshed with model changes");
assert.ok(appSource.includes("market.prices.push"), "quarter market prices must preserve both factor IDs");
assert.ok(appSource.includes('outcomeStatus:"unresolved"'), "unconfirmed history outcomes must not be shown as settled");
assert.ok(appSource.includes('overtime_policy:"not_included_market_rule_unverified"'), "regulation-only forecasts must not imply overtime inclusion");
assert.ok(!appSource.includes('add("Smooth Game Q"'), "a diagnostic must not be counted as a forecast model");
assert.ok(appSource.includes("эвристика концовки"), "endgame adjustment must be labeled as a heuristic");


const scoreStart = appSource.indexOf("function validateScoreTimeline(ev,matchScore,period){");
const scoreEnd = appSource.indexOf("function currentQuarterScore(", scoreStart);
assert.ok(scoreStart >= 0 && scoreEnd > scoreStart);
const scoreSandbox = {readPeriodScorePair:(_ev,index)=>[[25,22],[24,26],[1,0]][index] || null};
vm.runInNewContext(appSource.slice(scoreStart, scoreEnd) + "; this.validateScoreTimeline = validateScoreTimeline;", scoreSandbox);
assert.equal(scoreSandbox.validateScoreTimeline({}, [50,48], 3).valid, true);
assert.equal(scoreSandbox.validateScoreTimeline({}, [51,48], 3).valid, false,
  "period sum mismatch must block a forecast");
assert.ok(appSource.includes("if(!explicit||![2,4].includes(periodCount))return null") && appSource.includes("seconds:explicit,periods:periodCount"),
  "period duration and period count must both be confirmed independently");

const periodStart = appSource.indexOf("function findLivePeriod(ev){");
const periodEnd = appSource.indexOf("function findLiveClock(ev){", periodStart);
const periodSandbox = {collectEventObjects:()=>[]};
vm.runInNewContext(appSource.slice(periodStart, periodEnd) + "; this.findLivePeriod = findLivePeriod;", periodSandbox);
assert.equal(periodSandbox.findLivePeriod({period:"OT"}), 5);
assert.equal(periodSandbox.findLivePeriod({period:"Quarter 3"}),3);
assert.equal(periodSandbox.findLivePeriod({period:"Q4"}),4);
assert.equal(periodSandbox.findLivePeriod({status:"playing overtime"}),5);

const cardHelpersStart = appSource.indexOf("function quarterForecastMarkup(");
const cardHelpersEnd = appSource.indexOf("async function refreshLiveMatches()", cardHelpersStart);
assert.ok(cardHelpersStart >= 0 && cardHelpersEnd > cardHelpersStart,
  "safe match-card helpers must exist outside the LIVE refresh callback");
const cardSandbox = {
  escapeHtml: value => String(value ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;"),
  findLivePeriod: () => 2,
  isTwentyMinuteHalves: () => false,
  livePeriodLabel: (_ev,period) => "Q"+(period||2),
  eventScore: () => "50:47",
  eventPeriod: () => "Q2",
  eventClock: () => "06:45",
  console
};
vm.runInNewContext(appSource.slice(cardHelpersStart,cardHelpersEnd) +
  "; this.quarterForecastMarkup=quarterForecastMarkup; this.liveMatchRenderFallback=liveMatchRenderFallback;",cardSandbox);
const readyQuarterMarkup = cardSandbox.quarterForecastMarkup({
  ready:true,period:2,projection:42.6,lines:[{direction:"ТБ",line:41.5,pOver:.71,pUnder:.29}]
},{id:"fixture"});
assert.match(readyQuarterMarkup,/Прогноз Q2/);
assert.match(readyQuarterMarkup,/ТБ 41.5/);
assert.doesNotThrow(() => cardSandbox.quarterForecastMarkup({
  ready:false,reason:"ошибка модели"
},{id:"fixture"}));
const safeFallback = cardSandbox.liveMatchRenderFallback({
  id:"fixture",team1:"Home",team2:"Away",timer_seconds:405,
  external_statistics:[{metric_key:"assists",home:2,away:3}],
  statistics_quality:{available_count:1,expected_count:19},
  match_total_candidates:[{pt:164.5,v:"1.91"}]
});
for (const visibleFact of ["50:47","Q2","06:45","assists 2:3","Тотал 164.5"]) {
  assert.ok(safeFallback.includes(visibleFact), "fallback card must retain " + visibleFact);
}
assert.ok(appSource.includes("data.successful_source || data.source_url"),
  "UI must show the actually successful PARI host");
assert.ok(!appSource.includes("data.attempts?.[0]?.host"),
  "UI must not display the first failed host as the successful source");
assert.ok(appSource.includes('source_no_live_events:"у внешнего источника нет LIVE-событий"'),
  "provider empty-live-feed status must be visible and distinct from no exact match");
assert.ok(appSource.includes("statusLabels[missingStatuses[0]]"),
  "statistics gaps must show a specific reason rather than a generic no-statistics label");

console.log("forecast core regressions: OK");
const malformedBase={current:20,elapsedSeconds:300,periodLengthSeconds:600,completedQuarterTotals:[40,42]};
for(const patch of [{current:null},{current:''},{current:true},{current:2.5},{current:-1},
  {elapsedSeconds:null},{periodLengthSeconds:''},{completedQuarterTotals:[40,null]},
  {completedQuarterTotals:[-1,40]},{completedQuarterTotals:[40.5]},
  {priorPoints:80,priorExposureSeconds:1200},{completedQuarterTotals:[],priorPoints:null,priorExposureSeconds:1200}])
  assert.equal(core.quarterPosterior({...malformedBase,...patch}).ready,false,'invalid counts/exposure must not silently become zero or disappear');
const parsingSandbox={};
vm.runInNewContext(appSource.slice(0,appSource.indexOf('function findLivePeriod('))+';this.parse=parseScore;this.liveScore=findLiveScore;',parsingSandbox);
for(const value of [[null,null],['',10],[true,10],[-1,10],[2.5,10],'-2:10','2.5:10',{score1:null,score2:10}])
  assert.equal(parsingSandbox.parse(value),null);
assert.equal(parsingSandbox.parse([0,0]).join(':'),'0:0');
assert.equal(parsingSandbox.liveScore({score:null,related_events:[{score:[30,20]}]}),null,'a child statistic or quarter is not the root score');
assert.equal(parsingSandbox.liveScore({score:[50,45],related_events:[{score:[30,20]}]}).join(':'),'50:45');
console.log('input validation regressions: OK');

// Delayed transport exercises the real single-flight refresh entry point.
(async function refreshCadenceRegression(){
  let now=0,sequence=0,calls=0,rejectRequest;
  const timers=new Map(),node={children:[],querySelectorAll:()=>[]};
  const sandbox={Date:{now:()=>now},window:{scrollY:0},userScrollIntentVersion:0,
    $:()=>node,liveMessage:node,liveList:node,liveBadge:node,liveUpdated:node,
    AbortController,fetch:()=>{calls++;return new Promise((_resolve,reject)=>{rejectRequest=reject;});},
    setTimeout:(callback,delay)=>{const id=++sequence;timers.set(id,{callback,delay});return id;},
    clearTimeout:id=>timers.delete(id)};
  const start=appSource.indexOf('async function refreshLiveMatches()');
  const end=appSource.indexOf('if ($("refreshLiveMatches"))',start);
  vm.runInNewContext('let liveRefreshInFlight=false;const LIVE_REFRESH_INTERVAL_MS=10000;'+
    'let nextLiveRefreshDelay=10000;let liveRefreshTimer=null;'+appSource.slice(start,end)+
    ';this.refresh=refreshLiveMatches;',sandbox);
  const first=sandbox.refresh();await sandbox.refresh();
  assert.equal(calls,1,'concurrent triggers must not stack requests');
  now=3500;rejectRequest(new Error('transport unavailable'));await first;
  assert.equal(timers.size,1,'only one automatic refresh must remain');
  assert.equal([...timers.values()][0].delay,6500,'response time counts toward the ten-second cadence');
  const manual=sandbox.refresh();
  assert.equal(timers.size,1,'manual refresh cancels pending poll, leaving only its watchdog');
  now=20000;rejectRequest(new Error('slow transport'));await manual;
  assert.equal(timers.size,1);
  assert.equal([...timers.values()][0].delay,1000,'slow responses must not cause a tight retry loop');
  console.log('ten-second refresh cadence regressions: OK');
})().catch(error=>{console.error(error);process.exitCode=1;});
const optimizedMC=mcSource.replace(/\r\n/g,"\n");
const legacyMC=optimizedMC.replace(/  \/\/ Preserve the identical seeded sample,[\s\S]*?  const quantile=/,
 '  outcomes.sort((a,b)=>a-b);\n  const quantile=')
 .replace('const line=Number(x.line),under=bound(line,false),upper=bound(line,true);\n    const over=n-upper,push=upper-under;',
 'const line=Number(x.line);let over=0,under=0,push=0; for(const total of outcomes){if(total>line)over++;else if(total<line)under++;else push++;}');
assert.notEqual(legacyMC,optimizedMC);
const makeMC=source=>{const ctx={...mcSandbox};vm.runInNewContext(source,ctx);return ctx.dynamicScenarioMonteCarlo;};
const fastMC=makeMC(optimizedMC),referenceMC=makeMC(legacyMC);
const testLines=Array.from({length:301},(_,i)=>({line:80+i/2}));
let fastMs=0,referenceMs=0;
for(const remaining of [0,12,80]){
 let start=performance.now();const ref=referenceMC(100,remaining,testLines,'cdf-oracle',{},4,'04:00','match');referenceMs+=performance.now()-start;
 start=performance.now();const actual=fastMC(100,remaining,testLines,'cdf-oracle',{},4,'04:00','match');fastMs+=performance.now()-start;
 assert.equal(JSON.stringify(actual),JSON.stringify(ref),'optimization must preserve every probability, push, quantile, moment and scenario exactly');
}
console.log('Monte Carlo exact-output regression: OK; reference '+Math.round(referenceMs)+'ms, optimized '+Math.round(fastMs)+'ms');
