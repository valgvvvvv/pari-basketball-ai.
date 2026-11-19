const assert = require("node:assert/strict");
const { simulateQuarter } = require("../static/forecast-core.js");

const early = simulateQuarter({
  current: 1, periodSeconds: 600, elapsedSeconds: 63,
  priorQuarterTotals: [42, 40], lines: [{line: 40.5}, {line: 40}],
  seed: "nanjing-xinjiang-q3-0857", simulations: 100000
});
assert.equal(early.ready, true);
assert.ok(early.projection > 25, "one point in 63 seconds must not be extrapolated to a 9.5-point quarter");
assert.ok(early.projection < 60, "posterior mean should remain in a plausible range for this fixture");
assert.ok(early.low80 < early.projection && early.high80 > early.projection);
for (const p of early.probabilities) {
  assert.ok(Math.abs(p.pOver + p.pUnder + p.pPush - 1) < 1e-9);
}
assert.equal(early.probabilities[0].pPush, 0, "half-point lines cannot push");
assert.ok(early.probabilities[1].pPush > 0, "integer line must expose push probability");

const zeroStart = simulateQuarter({
  current: 0, periodSeconds: 600, elapsedSeconds: 0,
  priorQuarterTotals: [39, 43], lines: [{line: 40.5}],
  seed: "zero-start", simulations: 20000
});
assert.equal(zeroStart.ready, true);
assert.ok(zeroStart.projection > 0, "0:0 at period start uses same-game completed-period prior");

const noPrior = simulateQuarter({
  current: 0, periodSeconds: 600, elapsedSeconds: 45,
  priorQuarterTotals: [], lines: [{line: 40.5}], seed: "no-prior", simulations: 10000
});
assert.equal(noPrior.ready, false, "early period without a prior must not fabricate a projection");

const lateNoPrior = simulateQuarter({
  current: 18, periodSeconds: 600, elapsedSeconds: 300,
  priorQuarterTotals: [], lines: [{line: 40.5}], seed: "late-no-prior", simulations: 10000
});
assert.equal(lateNoPrior.ready, true, "after three minutes Jeffreys prior yields an explicitly preliminary estimate");
assert.equal(lateNoPrior.prior.source, "prior Jeffreys (без внешней лиги/командной истории)");

console.log("forecast-core regression tests passed");
