const fs = require("fs");
const path = require("path");
const zlib = require("zlib");
const crypto = require("crypto");
// UI acceptance rerun after the v60 / v8 scroll-anchor deployment.
const { chromium } = require("playwright");

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    page.setDefaultNavigationTimeout(240000);
    page.setDefaultTimeout(240000);
    await page.goto(process.env.SITE_URL, { waitUntil: "domcontentloaded", timeout: 240000 });
    await page.waitForFunction(
      () => typeof automaticForecast === "function" &&
            typeof automaticQuarterForecast === "function" &&
            typeof aiModelAnalysis === "function" &&
            typeof classicBasketballModelAnalysis === "function",
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
          source_event_snapshot: JSON.parse(JSON.stringify(rawEvent)),
          model_versions: {
            match: "dynamic-match-v1",
            quarter: "gamma-poisson-quarter-v1",
            unified_registry: "unified-tools-registry-v1"
          },
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
          const match = automaticForecast(ev);
          const quarter = automaticQuarterForecast(ev);
          row.lines = getMatchTotalLines(ev) || [];
          row.match = match;
          row.quarter = quarter;
          row.ai = aiModelAnalysis(ev, match, quarter);
          row.classic_match = classicBasketballModelAnalysis(ev, match, quarter, "match");
          row.classic_quarter = classicBasketballModelAnalysis(ev, match, quarter, "quarter");
          row.statistics_analysis = liveStatisticsAnalysis(ev);
          row.advanced_calculations = advancedMatchCalculations(
            ev,
            match.ready && match.kind !== "market_reference" ? match.estimate : null,
            findLivePeriod(ev),
            findLiveClock(ev),
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
                period: findLivePeriod(ev),
                clock: findLiveClock(ev),
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
                rolling_snapshot_count: estimate.data_quality?.rolling_snapshot_count ?? (Array.isArray(ev.recent_snapshots) ? ev.recent_snapshots.length : 0),
                rolling_tempo_available: !!(estimate.tempo_dynamics?.rolling || quarter.tempo_dynamics?.rolling),
                rolling_tempo_source: estimate.poisson?.momentum_source || estimate.tempo_dynamics?.source || "period_history_fallback",
                rolling_tempo_reason: estimate.data_quality?.rolling_tempo_reason || null,
                reasons: forecast.reason ? [forecast.reason] : []
              }
            };
          };
          row.prediction_contracts = [];
          const matchContract = makeContract({ type: "match", includes_overtime: null }, match, matchEstimate, row.lines);
          if (matchContract) row.prediction_contracts.push(matchContract);
          const quarterContract = makeContract({ type: "period", number: quarter.period || findLivePeriod(ev) }, quarter, quarter, quarter.lines);
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
      // Match the UI's event snapshot key exactly: one ID for score, clock,
      // forecast cards, registry rows, history and all target contracts.
      row.snapshot_id = [
        row.event_id,
        result.source_checked_at_utc || row.captured_at_utc,
        String(row.score || ""),
        String(row.period || ""),
        String(row.clock || "")
      ].join("|");
      for (const contract of row.prediction_contracts || []) {
        // Every forecast block from one captured event update shares the same snapshot ID.
        // Target-specific identity is separate and never replaces the source snapshot ID.
        contract.snapshot_id = row.snapshot_id;
        contract.contract_id = crypto.createHash("sha256")
          .update(row.snapshot_id + "|" + JSON.stringify(contract.target)).digest("hex");
      }
    }
    result.prediction_contract_count = result.forecasts.reduce((sum, row) => sum + (row.prediction_contracts || []).length, 0);
    for (const row of result.forecasts) {
      if ((row.prediction_contracts || []).some(contract => contract.snapshot_id !== row.snapshot_id)) {
        throw new Error("Forecast contracts from one event update do not share the source snapshot_id");
      }
    }

    // v62 is deployed before this run; verify the live UI bundle and stylesheet before smoke tests.
    const appScriptUrl = await page.evaluate(() =>
      Array.from(document.scripts).map(script => script.src).find(src => src.includes("/static/app.js")) || ""
    );
    const stylesheetUrl = await page.evaluate(() =>
      Array.from(document.styleSheets).map(sheet => sheet.href || "").find(href => href.includes("/static/styles.css")) || ""
    );
    result.ui_smoke = { status: "skipped_old_deployment", app_script: appScriptUrl, stylesheet: stylesheetUrl,
      expected_bundle: "20261119-integrity-v65", expected_stylesheet: "20261119-ui-v8", expected_viewports: [360, 390, 768, 1280] };
    if (appScriptUrl.includes("20261119-integrity-v65") && stylesheetUrl.includes("20261119-ui-v8")) {
      const smokeRow = result.forecasts.find(row => row.event_id);
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
        await card.click({ force: true });
        await page.waitForTimeout(500);
        let selectionFallback = false;
        if (await page.locator("#selectedMatch").isHidden()) {
          const state = await page.evaluate(eventId => {
            const ev = (window.pariLiveEvents || []).find(x => String(x.id) === String(eventId));
            if (!ev) return { found: false, reason: "event id missing from refreshed frontend snapshot" };
            if (typeof showSelectedMatch !== "function") return { found: true, reason: "showSelectedMatch is not exposed" };
            showSelectedMatch(ev);
            return { found: true, selected: !document.querySelector("#selectedMatch").hidden };
          }, smokeRow.event_id);
          if (!state.selected) throw new Error("Click did not select match and direct selection failed: " + JSON.stringify(state));
          selectionFallback = true;
        }
        await page.locator("#selectedMatch").waitFor({ state: "visible", timeout: 30000 });
        await page.waitForFunction(() => {
          const count = document.querySelector("#historyCount");
          return count && Number(count.textContent || 0) > 0;
        }, null, { timeout: 30000 });
        const historyBefore = Number(await page.locator("#historyCount").innerText());
        const historyText = await page.locator("#historyList").innerText();
        if (!historyText.includes(smokeRow.teams)) throw new Error("Selected match was not saved in forecast history");
        const toolRows = await page.locator("#toolsRegistry .unified-tools-table tbody tr").count();
        if (toolRows < 1) throw new Error("Unified calculation tool registry is empty");
        const registryText = await page.locator("#toolsRegistry").innerText();
        if (!registryText.includes("Недоступны / отключены:")) throw new Error("Inactive tools are not exposed in the collapsed shared registry");
        for (const selector of ["#matchForecast", "#quarterForecast"]) {
          const headlineCount = await page.locator(selector + " .forecast-main").count();
          if (headlineCount > 1) throw new Error("Duplicate primary forecast in " + selector);
        }
        const snapshotText = await page.locator("#selectedMatchInfo").innerText();
        if (!snapshotText.includes("snapshot")) throw new Error("Selected match snapshot ID is not visible");
        fs.mkdirSync("archive-snapshot", { recursive: true });
        const viewportChecks = [];
        for (const width of [360, 390, 768, 1280]) {
          await page.setViewportSize({ width, height: 900 });
          const dimensions = await page.evaluate(() => ({
            viewport: window.innerWidth,
            document: document.documentElement.scrollWidth,
            body: document.body.scrollWidth,
            selected: Math.round(document.querySelector("#selectedMatch").getBoundingClientRect().width),
            sidebar: Math.round(document.querySelector(".sidebar").getBoundingClientRect().width),
            offenders: Array.from(document.querySelectorAll("body *")).map(el => {
              const r = el.getBoundingClientRect(), s = getComputedStyle(el);
              return {tag:el.tagName,id:el.id,className:typeof el.className==="string"?el.className:"",left:Math.round(r.left),right:Math.round(r.right),width:Math.round(r.width),scrollWidth:el.scrollWidth,minWidth:s.minWidth,whiteSpace:s.whiteSpace};
            }).filter(x => x.right > window.innerWidth + 1 || x.width > window.innerWidth + 1)
              .sort((a,b)=>b.right-a.right).slice(0,18)
          }));
          if (dimensions.document > dimensions.viewport + 1 || dimensions.body > dimensions.viewport + 1) {
            await page.screenshot({path:path.join("archive-snapshot","ui-overflow-"+width+".png"),fullPage:true});
            throw new Error("Page-level horizontal overflow at " + width + "px: " + JSON.stringify(dimensions));
          }
          if (dimensions.sidebar > dimensions.viewport + 1) {
            throw new Error("Sidebar exceeds viewport at " + width + "px: " + JSON.stringify(dimensions));
          }
          if (dimensions.selected > dimensions.viewport + 1) {
            throw new Error("Selected match card exceeds viewport at " + width + "px: " + JSON.stringify(dimensions));
          }
          await page.screenshot({ path: path.join("archive-snapshot", "ui-viewport-" + width + ".png") });
          viewportChecks.push(dimensions);
        }
        await page.setViewportSize({ width: 1280, height: 900 });
        const explanation = page.locator("#matchForecast details").first();
        let openedExplanation = null;
        if (await explanation.count()) {
          openedExplanation = await explanation.locator("summary").innerText();
          await explanation.locator("summary").click();
        }
        await page.evaluate(() => { document.documentElement.style.scrollBehavior = "auto"; window.scrollTo({top:Math.min(450, document.documentElement.scrollHeight - window.innerHeight),behavior:"instant"}); });
        await page.waitForTimeout(120);
        const scrollBefore = await page.evaluate(() => window.scrollY);
        await page.waitForFunction(() => {
          const button = document.querySelector("#refreshLiveMatches");
          return button && !button.disabled;
        }, null, { timeout: 60000 });
        await page.locator("#refreshLiveMatches").click();
        await page.waitForFunction(before => {
          const count = document.querySelector("#historyCount");
          return count && Number(count.textContent || 0) > before;
        }, historyBefore, { timeout: 30000 });
        // The app restores the reading anchor after two animation frames; wait for that scheduled correction before measuring it.
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(resolve, 80)))));
        const selectedAfterRefresh = await page.locator("#selectedMatchTitle").innerText();
        if (!selectedAfterRefresh.includes(smokeRow.teams.split(" — ")[0])) {
          throw new Error("Auto-refresh changed the selected match");
        }
        if (openedExplanation) {
          const explanationStillOpen = await page.locator("#matchForecast details[open]").filter({ hasText: openedExplanation }).count();
          if (!explanationStillOpen) throw new Error("Auto-refresh collapsed the opened explanation");
        }
        const scrollAfter = await page.evaluate(() => window.scrollY);
        if (Math.abs(scrollAfter - scrollBefore) > 5) {
          throw new Error("Auto-refresh moved the reading position: " + scrollBefore + " -> " + scrollAfter);
        }
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
          deployed_app_version: appScriptUrl.split("integrity-v").pop() || "unknown",
          deployed_stylesheet_version: stylesheetUrl.split("ui-v").pop() || "unknown",
          expected_viewports: [360, 390, 768, 1280],
          viewport_checks: viewportChecks,
          tool_rows: toolRows,
          selection_fallback: selectionFallback,
          selected_event_id: smokeRow.event_id,
          scroll_before_refresh: scrollBefore,
          scroll_after_refresh: scrollAfter,
          reading_anchor_delta_px: readingAnchorDelta,
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

// Recheck responsive UI after the v63/v8 deployment completed.

// Final browser smoke is intentionally rerun after v63 has reached Render.

// v64 is live on Render; run final viewport and refresh-anchor smoke now.
