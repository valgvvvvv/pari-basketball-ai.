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
                research_scheme: distribution.research_scheme ?? false,
                calibration_status: distribution.calibration_status || "not_calibrated",
                momentum: distribution.momentum ?? null,
                momentum_source: distribution.momentum_source || null,
                recent_tempo: distribution.recent_tempo || null,
                momentum_rule: distribution.momentum_rule || null,
                scenario_weight_rule: distribution.scenario_weight_rule || null,
                scenarios: distribution.scenarios || [],
                expected_remaining_points: distribution.mu ?? null,
                variance: distribution.variance ?? (Number.isFinite(Number(estimate.sigma)) ? Number(estimate.sigma) ** 2 : null),
                mean_uncertainty_variance: distribution.meanUncertaintyVariance ?? null,
                mean_uncertainty_sd: distribution.meanUncertaintySd ?? null,
                simulations: distribution.simulations ?? null,
                interval_type: "predictive_total_interval",
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
        total_events: data.total_events || data.events.length,
        live_score_source: data.live_score_source || { status: "not_reported", matched: 0, errors: [] },
        attempts: data.attempts || [],
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

    const appScriptUrl = await page.evaluate(() =>
      Array.from(document.scripts).map(script => script.src).find(src => src.includes("/static/app.js")) || ""
    );
    result.ui_smoke = { status: "skipped_old_deployment", app_script: appScriptUrl };
    if (appScriptUrl.includes("20261119-integrity-v40")) {
      const smokeRow = result.forecasts.find(row =>
        row.quarter || (row.match && row.match.kind !== "market_reference")
      );
      if (smokeRow) {
        const uiPayload = {
          ok: true, connected: true, loading: false,
          events: result.events, total_events: result.total_events,
          checked_at_utc: result.source_checked_at_utc || new Date().toISOString(),
          live_score_source: result.live_score_source, attempts: result.attempts
        };
        await page.route("**/api/pari/live*", route => route.fulfill({
          status: 200, contentType: "application/json", body: JSON.stringify(uiPayload)
        }));
        await page.waitForFunction(() => {
          const button = document.querySelector("#refreshLiveMatches");
          return button && !button.disabled;
        }, null, { timeout: 60000 });
        await page.locator("#refreshLiveMatches").click();
        await page.waitForSelector(".live-match", { timeout: 60000 });
        const card = page.locator(".live-match").filter({ hasText: smokeRow.teams }).first();
        await card.click();
        await page.locator("#selectedMatch").waitFor({ state: "visible", timeout: 30000 });
        await page.waitForFunction(() => {
          const count = document.querySelector("#historyCount");
          return count && Number(count.textContent || 0) > 0;
        }, null, { timeout: 30000 });
        const historyBefore = Number(await page.locator("#historyCount").innerText());
        const historyText = await page.locator("#historyList").innerText();
        if (!historyText.includes(smokeRow.teams)) throw new Error("Selected match was not saved in forecast history");
        await page.waitForFunction(() => {
          const button = document.querySelector("#refreshLiveMatches");
          return button && !button.disabled;
        }, null, { timeout: 60000 });
        await page.locator("#refreshLiveMatches").click();
        await page.waitForFunction(before => {
          const count = document.querySelector("#historyCount");
          return count && Number(count.textContent || 0) > before;
        }, historyBefore, { timeout: 30000 });
        const settlementContract = {
          event_id: "ui-smoke-fixture", snapshot_id: "ui-smoke-snapshot",
          target: { type: "match" }, market: { lines: [{ line: 200 }] }
        };
        const settlementResponse = await page.request.post(
          new URL("/api/archive/settle-preview", "https://pari-basketball-ai.onrender.com").toString(),
          { data: { contract: settlementContract, final_record: {
            event_id: "ui-smoke-fixture", final_status: "finished",
            confirmed: true, identity_verified: true, independent_confirmation: true,
            source: "automated-ui-smoke-fixture", market_rule_confirmed: true,
            includes_overtime: false, regulation_total: 201, line: 200
          } } }
        );
        const settlement = await settlementResponse.json();
        if (!settlementResponse.ok || settlement.status !== "settled" || settlement.result !== "over_won") {
          throw new Error("Result settlement preview UI/API smoke failed: " + JSON.stringify(settlement));
        }
        result.ui_smoke = {
          status: "passed", app_script: appScriptUrl,
          selected_event_id: smokeRow.event_id,
          history_before_refresh: historyBefore,
          history_after_refresh: Number(await page.locator("#historyCount").innerText()),
          result_preview: settlement.status + "/" + settlement.result
        };
      } else {
        result.ui_smoke = { status: "skipped_no_forecastable_match", app_script: appScriptUrl };
      }
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
      forecast_errors: result.forecasts.filter(x => x.errors.length).length,
      ui_smoke: result.ui_smoke || { status: "not_run" }
    }));
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error("ARCHIVE_COLLECTION_FAILED", error && (error.stack || error.message) || error);
  process.exit(1);
});
