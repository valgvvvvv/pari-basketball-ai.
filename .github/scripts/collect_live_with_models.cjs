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
      for (const ev of data.events) {
        const row = {
          event_id: String(ev.id || ""),
          teams: String((ev.team1 || "Команда 1") + " — " + (ev.team2 || "Команда 2")),
          captured_at_utc: new Date().toISOString(),
          score: ev.score ?? null,
          period: ev.period ?? null,
          clock: ev.clock ?? null,
          lines: [],
          match: null,
          quarter: null,
          ai: null,
          classic_match: null,
          classic_quarter: null,
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
        } catch (error) {
          row.errors.push(String(error && (error.stack || error.message) || error).slice(0, 1200));
        }
        forecasts.push(row);
      }

      return {
        format: "pari-live-and-model-forecast-v1",
        captured_at_utc: new Date().toISOString(),
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
