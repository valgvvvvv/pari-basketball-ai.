const fs = require("fs");
const path = require("path");
const zlib = require("zlib");
const crypto = require("crypto");
const { chromium } = require("playwright");

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    page.setDefaultNavigationTimeout(240000);
    page.setDefaultTimeout(240000);
    await page.goto(process.env.SITE_URL, { waitUntil: "domcontentloaded", timeout: 240000 });
    await page.waitForFunction(
      () => typeof window.automaticForecast === "function" &&
            typeof window.automaticQuarterForecast === "function" &&
            typeof window.aiModelAnalysis === "function" &&
            typeof window.classicBasketballModelAnalysis === "function",
      null,
      { timeout: 90000 }
    );

    const result = await page.evaluate(async () => {
      let data = null;
      let lastError = null;
      let apiAttempts = 0;
      for (let attempt = 0; attempt < 7; attempt++) {
        apiAttempts = attempt + 1;
        try {
          const response = await fetch("/api/pari/live?catalog_only=false&_collector_retry=" + apiAttempts + "_" + Date.now(),
            { cache: "no-store" });
          if (!response.ok) {
            const error = new Error("PARI LIVE HTTP " + response.status);
            if (![502, 503, 504].includes(response.status)) throw error;
            lastError = error;
          } else {
            const candidate = await response.json();
            if (candidate && candidate.ok && candidate.connected && Array.isArray(candidate.events)) {
              data = candidate;
              break;
            }
            lastError = new Error("PARI LIVE response did not confirm a successful connection");
          }
        } catch (error) {
          lastError = error;
          if (error && /HTTP (?!502|503|504)/.test(error.message || "")) throw error;
        }
        if (attempt < 6) await new Promise(resolve => setTimeout(resolve, 12000));
      }
      if (!data) throw new Error("PARI LIVE unavailable after " + apiAttempts + " attempts: " +
        (lastError && lastError.message || "unknown error"));

      const capturedAt = new Date().toISOString();
      const forecasts = [];
      for (const rawEvent of data.events) {
        const ev = { ...rawEvent, feed_checked_at_utc: data.checked_at_utc || null };
        const row = {
          event_id: String(ev.id || ""),
          teams: String((ev.team1 || "Команда 1") + " — " + (ev.team2 || "Команда 2")),
          captured_at_utc: capturedAt,
          source_checked_at_utc: data.checked_at_utc || null,
          provider_event_updated_at_utc: ev.updated_at || ev.updatedAt || ev.raw?.updated_at || ev.raw?.updatedAt || null,
          score: ev.score ?? null,
          period: ev.period ?? null,
          clock: ev.clock ?? null,
          lines: [],
          match: null,
          quarter: null,
          ai: null,
          classic_match: null,
          classic_quarter: null,
          statistics_analysis: null,
          advanced_calculations: null,
          errors: []
        };
        try {
          const match = window.automaticForecast(ev);
          const quarter = window.automaticQuarterForecast(ev);
          row.lines = window.getMatchTotalLines(ev) || [];
          row.match = match;
          row.quarter = quarter;
          row.ai = window.aiModelAnalysis(ev, match, quarter);
          row.classic_match = window.classicBasketballModelAnalysis(ev, match, quarter, "match");
          row.classic_quarter = window.classicBasketballModelAnalysis(ev, match, quarter, "quarter");
          row.statistics_analysis = window.liveStatisticsAnalysis(ev);
          row.advanced_calculations = window.advancedMatchCalculations(
            ev,
            match.ready && match.kind !== "market_reference" ? match.estimate : null,
            window.findLivePeriod(ev),
            window.findLiveClock(ev),
            match.ready && match.kind !== "market_reference" ? match.estimate.possession_model : null,
            quarter
          );
          const matchEstimate = match.ready && match.kind !== "market_reference" ? match.estimate : null;
          const makeContract = (target, forecast, estimate, marketLines) => {
            if (!forecast?.ready || !estimate || !Number.isFinite(Number(estimate.projection))) return null;
            const distribution = estimate.poisson || forecast.poisson || null;
            return {
              schema_version: 1,
              event_id: row.event_id,
              snapshot_id: null,
              target,
              overtime_policy: "not_verified_from_market_rules",
              outcome: { status: "unresolved", reason: "no independently confirmed final result has been reconciled" },
              source: {
                provider: "PARI",
                response_checked_at_utc: data.checked_at_utc || null,
                provider_event_updated_at_utc: row.provider_event_updated_at_utc,
                event_score_source: ev.score_source || null,
                clock_source: ev.clock_source || null
              },
              calculated_at_utc: capturedAt,
              state: {
                score: ev.score ?? null,
                period: window.findLivePeriod(ev),
                clock: window.findLiveClock(ev),
                period_length_seconds: ev.period_length_seconds || ev.external_live?.period_length_seconds || null,
                period_count: ev.external_live?.period_count || null
              },
              model: { name: "PARI primary forecast", version: "gamma-poisson-quarter-v1 / dynamic-match-v1" },
              calibration_status: "not_calibrated_no_independent_labeled_test",
              expected_remaining_points: distribution?.mu ?? estimate.expected_remaining_points ?? null,
              center: Number(estimate.projection),
              distribution: distribution ? {
                method: distribution.distribution || distribution.method || "Monte Carlo predictive sampler",
                expected_remaining_points: distribution.mu ?? null,
                variance: distribution.variance ?? (Number.isFinite(Number(estimate.sigma)) ? Number(estimate.sigma) ** 2 : null),
                simulations: distribution.simulations ?? null,
                interval_80: [distribution.low80 ?? estimate.low ?? null, distribution.high80 ?? estimate.high ?? null],
                interval_95: [distribution.low95 ?? null, distribution.high95 ?? null]
              } : null,
              market_probability_status: "not_computed_factor_side_mapping_unverified",
              market: {
                lines: marketLines || [],
                raw_factor_records: ev.match_total_candidates || []
              },
              probabilities: distribution?.probabilities || forecast.lines || [],
              quality: {
                preliminary: !!(forecast.preliminary || estimate.preliminary),
                data_mode: estimate.data_quality?.mode || forecast.data_quality?.mode || "not_reported",
                data_label: estimate.data_quality?.label || forecast.data_quality?.label || "quality_not_reported",
                statistics: ev.statistics_quality || null,
                reasons: forecast.reason ? [forecast.reason] : []
              }
            };
          };
          row.prediction_contracts = [];
          const matchContract = makeContract({ type: "match", includes_overtime: null }, match, matchEstimate, row.lines);
          if (matchContract) row.prediction_contracts.push(matchContract);
          const quarterContract = makeContract({ type: "period", number: quarter.period || window.findLivePeriod(ev) }, quarter, quarter, quarter.lines);
          if (quarterContract) row.prediction_contracts.push(quarterContract);
        } catch (error) {
          row.errors.push(String(error && (error.stack || error.message) || error).slice(0, 1200));
        }
        forecasts.push(row);
      }

      return {
        format: "pari-live-and-model-forecast-v2",
        captured_at_utc: capturedAt,
        source_checked_at_utc: data.checked_at_utc || null,
        source: "PARI live adapter + the same browser calculation functions used by the site",
        event_count: data.events.length,
        forecast_count: forecasts.length,
        events: data.events,
        forecasts
      };
    });

    if (!result || !Array.isArray(result.events) || !Array.isArray(result.forecasts)) {
      throw new Error("Collector returned an invalid result");
    }
    for (const row of result.forecasts) {
      const basis = [row.event_id, row.captured_at_utc, JSON.stringify(row.score), row.period, row.clock].join("|");
      row.snapshot_id = crypto.createHash("sha256").update(basis).digest("hex");
      for (const contract of row.prediction_contracts || []) {
        contract.snapshot_id = crypto.createHash("sha256")
          .update(row.snapshot_id + "|" + JSON.stringify(contract.target)).digest("hex");
      }
    }
    result.prediction_contract_count = result.forecasts.reduce((sum, row) => sum + (row.prediction_contracts || []).length, 0);
    fs.mkdirSync("archive-snapshot", { recursive: true });
    const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
    const target = path.join("archive-snapshot", "live-" + stamp + ".json.gz");
    fs.writeFileSync(target, zlib.gzipSync(Buffer.from(JSON.stringify(result), "utf8"), { level: 9 }));
    fs.writeFileSync(
      path.join("archive-snapshot", "collection.txt"),
      "captured_at_utc=" + result.captured_at_utc +
      "\nevents=" + result.event_count +
      "\nforecasts=" + result.forecast_count +
      "\n"
    );
    const forecastErrorDetails = result.forecasts.flatMap(row =>
      (row.errors || []).map(error => ({
        event_id: row.event_id,
        model: error.model || null,
        reason: String(typeof error === "string" ? error : (error.reason || error.message || JSON.stringify(error) || "unknown")).slice(0, 240)
      }))
    ).slice(0, 20);
    if (forecastErrorDetails.length) {
      console.log("ARCHIVE_FORECAST_DIAGNOSTICS " + JSON.stringify(forecastErrorDetails));
    }
    console.log("ARCHIVE_COLLECTION_OK " + JSON.stringify({
      captured_at_utc: result.captured_at_utc,
      events: result.event_count,
      forecasts: result.forecast_count,
      prediction_contracts: result.prediction_contract_count,
      forecast_errors: result.forecasts.filter(x => x.errors.length).length
    }));
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error("ARCHIVE_COLLECTION_FAILED", error && (error.stack || error.message) || error);
  process.exit(1);
});
