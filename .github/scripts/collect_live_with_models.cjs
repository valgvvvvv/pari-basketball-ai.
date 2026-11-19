const fs = require("fs");
const path = require("path");
const zlib = require("zlib");
const { chromium } = require("playwright");

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    page.setDefaultNavigationTimeout(240000);
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
      const response = await fetch("/api/pari/live?catalog_only=false", { cache: "no-store" });
      if (!response.ok) throw new Error("PARI LIVE HTTP " + response.status);
      const data = await response.json();
      if (!data || !data.ok || !data.connected || !Array.isArray(data.events)) {
        throw new Error("PARI LIVE feed did not confirm a successful connection");
      }

      const forecasts = [];
      const predictionContracts = [];
      const captureTime = new Date().toISOString();
      const makeContract = (ev, target, forecast, period, clock) => {
        if (!forecast || !forecast.ready) return null;
        const estimate = target === "match" ? forecast.estimate : forecast;
        if (!estimate || !Number.isFinite(Number(estimate.projection))) return null;
        const distribution = target === "match" ? estimate.poisson : forecast.poisson;
        const contract = {
          contract_version: "pari-prediction-contract-v1",
          event_id: String(ev.id || ""),
          snapshot_id: [String(ev.id || ""), captureTime, target, period || ""].join("|"),
          target: target === "match" ? { type: "match_total" } : { type: "period_total", period: Number(period) },
          captured_at_utc: captureTime,
          source_timestamp_utc: ev.source_timestamp_utc || ev.external_live?.updated_at || null,
          source_fetched_at_utc: data.checked_at_utc || null,
          score: ev.score || null,
          period: period ?? null,
          clock: clock || null,
          period_length_seconds: ev.period_length_seconds || ev.external_live?.period_length_seconds || null,
          overtime_included: target === "match" ? null : false,
          overtime_scope_status: target === "match" ? "not_confirmed_by_market" : "not_applicable_to_period",
          model: target === "match" ? "live-tempo-possession-scenario" : (forecast.distribution || "quarter-model"),
          model_version: target === "match" ? "match-live-v1" : (forecast.model_version || "unknown"),
          current_points: estimate.current ?? null,
          expected_remaining_points: target === "match" ? distribution?.mu ?? null : forecast.expected_remaining ?? null,
          projected_total: estimate.projection,
          distribution: distribution ? {
            type: distribution.method || forecast.distribution || "not_specified",
            simulations: distribution.simulations || forecast.simulations || null,
            low80: distribution.low80 ?? forecast.low80 ?? null,
            high80: distribution.high80 ?? forecast.high80 ?? null,
            low95: distribution.low95 ?? forecast.low95 ?? null,
            high95: distribution.high95 ?? forecast.high95 ?? null,
            probabilities: distribution.probabilities || forecast.lines || [],
            posterior: forecast.posterior || null,
            prior: forecast.prior || null
          } : null,
          lines: target === "match" ? (forecast.lines || []) : (forecast.lines || []),
          data_quality: ev.statistics_quality || null,
          score_source: ev.score_source || null,
          clock_source: ev.clock_source || null,
          calibration_status: target === "match"
            ? "not_calibrated_no_verified_final_outcomes"
            : (forecast.calibration_status || "not_calibrated"),
          limitations: [
            "Source timestamp is null unless the provider supplies one.",
            "Market price side and overtime settlement scope are not inferred when absent."
          ]
        };
        return contract;
      };
      for (const ev of data.events) {
        const row = {
          event_id: String(ev.id || ""),
          teams: String((ev.team1 || "Команда 1") + " — " + (ev.team2 || "Команда 2")),
          captured_at_utc: captureTime,
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
          const matchContract = makeContract(ev, "match", match, window.findLivePeriod(ev), window.findLiveClock(ev));
          const quarterContract = makeContract(ev, "quarter", quarter, window.findLivePeriod(ev), window.findLiveClock(ev));
          row.prediction_contracts = [matchContract, quarterContract].filter(Boolean);
          predictionContracts.push(...row.prediction_contracts);
        } catch (error) {
          row.errors.push(String(error && (error.stack || error.message) || error).slice(0, 1200));
        }
        forecasts.push(row);
      }

      return {
        format: "pari-live-and-model-forecast-v1",
        captured_at_utc: captureTime,
        source_fetched_at_utc: data.checked_at_utc || null,
        source: "PARI live adapter + the same browser calculation functions used by the site",
        event_count: data.events.length,
        forecast_count: forecasts.length,
        prediction_contract_count: predictionContracts.length,
        events: data.events,
        forecasts,
        prediction_contracts: predictionContracts
      };
    });

    if (!result || !Array.isArray(result.events) || !Array.isArray(result.forecasts)) {
      throw new Error("Collector returned an invalid result");
    }
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
    console.log("ARCHIVE_COLLECTION_OK " + JSON.stringify({
      captured_at_utc: result.captured_at_utc,
      events: result.event_count,
      forecasts: result.forecast_count,
      forecast_errors: result.forecasts.filter(x => x.errors.length).length
    }));
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error("ARCHIVE_COLLECTION_FAILED", error && (error.stack || error.message) || error);
  process.exit(1);
});
