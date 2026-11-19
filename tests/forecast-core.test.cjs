const assert = require("node:assert/strict");
const core = require("../static/forecast-core.js");
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
assert.equal(nanjingDistribution.low80, 30);
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
assert.ok(appSource.includes("liveList.innerHTML = count ? window.pariLiveEvents.map"), "live list cards must use events enriched with recent snapshots");
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
assert.ok(htmlSource.includes("20261119-integrity-v69"), "history fix must bump frontend cache");
assert.ok(htmlSource.includes("confirmedIndependentMatches") && htmlSource.includes("Финалы ещё не сверяются автоматически"), "quality panel must show zero confirmed independent finals and not claim accuracy without settlement");
assert.ok(archivePublisherSource.startsWith("import hashlib"), "permanent archive publisher must import hashlib before calculating checksums");
assert.ok(archiveCollectorSource.includes("contract.snapshot_id = row.snapshot_id") && archiveCollectorSource.includes("contract.contract_id = crypto.createHash"), "all prediction blocks from one source update must share snapshot_id; target identity uses contract_id");
assert.ok(archiveCollectorSource.includes('registryText.includes("Недоступны / отключены:")'), "browser acceptance must verify disabled tools remain accessible in the shared registry");
assert.ok(!archiveCollectorSource.includes("window.automaticForecast") && archiveCollectorSource.includes("typeof automaticForecast === \"function\""), "browser collector must call classic-script lexical functions rather than assume they are window properties");
assert.ok(archiveCollectorSource.includes("result.source_checked_at_utc || row.captured_at_utc") && archiveCollectorSource.includes("String(row.score || \"\"),"), "archive snapshot IDs must use the same source timestamp, score, period and clock key as the UI");
assert.ok(archiveCollectorSource.includes("source_event_snapshot: JSON.parse(JSON.stringify(rawEvent))") && archiveCollectorSource.includes("model_versions:"), "archive must preserve immutable raw source event and method versions separately from forecasts");
assert.ok(appSource.includes("Применимо: ") && appSource.includes("рассчитано сейчас:"), "registry must show factual tool counts");
assert.ok(htmlSource.includes("20261119-integrity-v69"), "frontend cache must be bumped after display fixes");
assert.ok(appSource.includes("const qLines=") && !appSource.includes("qLines+="), "quarter forecast text must be immutable after declaration");
assert.ok(appSource.includes("timer_semantics_verified!==true"), "unverified PARI timer values must not be converted into a quarter countdown");

assert.ok(appSource.includes("Ансамбль сценариев") && appSource.includes("не независимый голос"), "scenario mixture must be shown without counting it as an independent model");
assert.ok(!appSource.includes('add("Динамика четверти"'), "a duplicate of the primary quarter forecast must not appear as a separate method");
assert.ok(!appSource.includes("Единый прогноз · повтор основного расчёта"), "duplicate primary forecast cards must stay removed");
assert.ok(appSource.includes("ОТ не включён, правило PARI не подтверждено"), "list forecast must not imply overtime is included");
assert.ok(appSource.includes('kind:"period_range",preliminary:true'), "timerless range forecasts must be explicitly preliminary");
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
assert.equal(runtimeSandbox.inferPeriodLengthForForecast({period:3,league:"Unknown league",clock:"08:57"}), null, "unknown format must not be guessed from period or clock");
assert.equal(runtimeSandbox.inferPeriodLengthForForecast({period:2,period_length_seconds:1200,external_live:{period_count:2},clock:"15:00"}).periods, 2);
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
assert.ok(htmlSource.includes("20261119-forecast-core-v3"), "forecast core cache must be refreshed with model changes");
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


const cardHelpersStart = appSource.indexOf("function quarterForecastMarkup(");
const cardHelpersEnd = appSource.indexOf("async function refreshLiveMatches()", cardHelpersStart);
assert.ok(cardHelpersStart >= 0 && cardHelpersEnd > cardHelpersStart,
  "safe match-card helpers must exist outside the LIVE refresh callback");
const cardSandbox = {
  escapeHtml: value => String(value ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;"),
  findLivePeriod: () => 2,
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
for (const visibleFact of ["50:47","Q2","06:45","Статистика: 1 показателей","Тотал 164.5"]) {
  assert.ok(safeFallback.includes(visibleFact), "fallback card must retain " + visibleFact);
}
assert.ok(appSource.includes("data.successful_source || data.source_url"),
  "UI must show the actually successful PARI host");
assert.ok(!appSource.includes("data.attempts?.[0]?.host"),
  "UI must not display the first failed host as the successful source");
assert.ok(appSource.includes("statusLabels[missingStatuses[0]]"),
  "statistics gaps must show a specific reason rather than a generic no-statistics label");

console.log("forecast core regressions: OK");
