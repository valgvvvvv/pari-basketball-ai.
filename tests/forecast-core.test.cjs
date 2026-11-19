const assert = require("node:assert/strict");
const core = require("../static/forecast-core.js");

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
console.log("forecast core regressions: OK");
