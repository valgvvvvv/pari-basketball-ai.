const assert = require("node:assert/strict");
const core = require("../static/forecast-core.js");
const fs = require("node:fs");
const vm = require("node:vm");
const appSource = fs.readFileSync(require("node:path").join(__dirname, "../static/app.js"), "utf8");
const htmlSource = fs.readFileSync(require("node:path").join(__dirname, "../static/index.html"), "utf8");

const earlyQ3 = core.quarterPosterior({
  current: 1, elapsedSeconds: 63, periodLengthSeconds: 600,
  completedQuarterTotals: [42, 44]
});
assert.equal(earlyQ3.ready, true);
assert.ok(earlyQ3.projection > 30 && earlyQ3.projection < 50,
  "Q3 1:0 after 63 seconds must not extrapolate 1 point to a 9.5-point quarter");
assert.ok(earlyQ3.varianceRemaining > earlyQ3.expectedRemaining,
  "posterior predictive variance must include rate uncertainty");

const zeroAtStart = core.quarterPosterior({
  current: 0, elapsedSeconds: 0, periodLengthSeconds: 600,
  completedQuarterTotals: [40, 46]
});
assert.equal(zeroAtStart.ready, true);
assert.ok(zeroAtStart.projection >= 40 && zeroAtStart.projection <= 46);

const noPriorEarly = core.quarterPosterior({
  current: 1, elapsedSeconds: 63, periodLengthSeconds: 600,
  completedQuarterTotals: []
});
assert.equal(noPriorEarly.ready, false);

const integerLine = core.simulateQuarter(earlyQ3, [{line:40}], "integer-line-regression", 100000);
assert.ok(integerLine.probabilities[0].pPush > 0, "integer totals must have an explicit push probability");
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
assert.equal(runtimeSandbox.inferPeriodLengthForForecast({period:3,league:"Unknown league",clock:"08:57"}).seconds, 600);
assert.equal(runtimeSandbox.inferPeriodLengthForForecast({period:2,period_length_seconds:1200,external_live:{period_count:2},clock:"15:00"}).periods, 2);
assert.ok(htmlSource.indexOf("forecast-core.js") < htmlSource.indexOf("app.js"),
  "the tested forecast core must load before the UI code");
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
assert.ok(appSource.includes("(periodCount===2||explicit===1200)?2:4"),
  "20-minute halves must not be normalized as four quarters");

const periodStart = appSource.indexOf("function findLivePeriod(ev){");
const periodEnd = appSource.indexOf("function findLiveClock(ev){", periodStart);
const periodSandbox = {collectEventObjects:()=>[]};
vm.runInNewContext(appSource.slice(periodStart, periodEnd) + "; this.findLivePeriod = findLivePeriod;", periodSandbox);
assert.equal(periodSandbox.findLivePeriod({period:"OT"}), 5);

console.log("forecast core regressions: OK");
