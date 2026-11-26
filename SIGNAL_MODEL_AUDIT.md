# Signal model integrity and scoring-regime diagnostics — 26 November 2026

## Changes
- Telegram research delivery requires compatible market rules, both fresh prices, and sufficient context; regulation forecasts cannot send against markets including overtime. No untrained overtime coefficient is invented.
- One successfully delivered notification per event/target/period. Changed lines and direction changes remain journal observations; they do not create repeated entries. Failed sends remain retryable.
- Diagnostic change detector compares two disjoint 90-second playing-time windows, requires a 30% rate change and a marked-scoring noise statistic of at least 1.96. Two 45-second subsegments distinguish persistent changes from a burst. These fixed thresholds are exploratory policy, not calibrated probabilities or statistical significance after repeated monitoring.
- Score corrections, clock reversals, long observation gaps, stale snapshots, period transitions and pauses reset or suppress the diagnostic. It uses normalized game clocks, never wall time as game time.
- Scoring-rate continuation and return-to-previous-window distributions enter sensitivity checks. They widen the displayed scenario envelope where applicable; their agreement is not an independent model vote. The central point forecast is not arbitrarily shifted.
- UI displays scoring dynamics and suppresses headline confidence percentages for uncalibrated signals. Raw probabilities remain diagnostic; no formal priority is enabled.
- Diagnostic regime and statisticsReady fields are retained in the immutable decision journal for subsequent evaluation.

## Reproducible checks
164 Python unit/integration tests; forecast-core, signal-quality and regime JavaScript suites. The regime suite covers rising/falling/stable/burst, score correction, reversed clocks, pause, stale observations and 2x20.
Offline replay of 97 first-target snapshots across 72 archived events: zero execution errors; 79 confirmed outcomes retain point MAE 8.63. No improvement of point accuracy is claimed. 20 exact-line decisions had stable diagnostics; 77 lacked enough valid history. All 97 lacked the required statistical context and remain informational rather than deliverable. This is a development regression set, not an independent accuracy test.

## Remaining work requiring evidence
The archived sample is selected from previously sent signals and is too narrow for honest calibration or an overtime model. Capture all eligible and rejected live states, aligned period-specific FGA/FTA/ORB/TO/fouls, final regulation score and OTs. Fit and calibrate only on earlier whole games, freeze the policy and test on later games, keeping all markets/snapshots of one game in the same split. Report coverage, MAE/bias, Brier, reliability and market-comparable virtual returns. Missing provider statistics cannot be repaired by treating absence as zero. The detector currently gets score/clock history; it labels this scoring_only rather than asserting measured possession pace.

## Possession/shot-mix repair (v8)
- Per-team observed PPP is validated separately; combined two-team PPP no longer uses the former single-team upper bound.
- Completed-period shooting counts must reconcile exactly to their scores; cumulative totals must reconcile to the live score. Missing periods, duplicate conflicts, noninteger counts, made/attempt contradictions and negative deltas block the separate estimator.
- Future shot volume uses the current period after three game minutes; future 2P/3P/FT conversion uses Jeffreys beta estimates from cumulative counts exactly once. Expected points are separate shot-mix rates times conversion, rather than cumulative score divided by time. This is an experimental within-game estimator, not trained team/opponent ratings or a calibrated probability model.
- Without complete disjoint statistics, possession data remain diagnostic and the primary score/time estimate remains active. The site does not claim a second independent possession forecast in this mode.
- The untrained universal eFG multiplier is removed. Existing endgame coefficients remain explicitly heuristic: a development ablation without them changed MAE from 8.628 to 8.644, providing no reason to replace them with that ablation. This is not a held-out comparison or proof of their benefit.
- Offline replay: 97 states, 72 events, zero execution errors, 79 confirmed targets, unchanged point MAE 8.628. None had the complete period box scores needed to activate v3; improved real-world accuracy remains unverified. The arithmetic and full adapter are covered by the new possession JavaScript suite, including 2x20 and combined-PPP regression.
- Learned prematch priors, team/opponent ratings, robust regression, lineup effects, overtime and probability calibration remain separate research work requiring sufficient independently labeled whole-game data. They are not enabled by this patch.
