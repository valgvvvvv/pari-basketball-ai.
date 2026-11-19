const { chromium } = require("/tmp/pari-playwright/node_modules/playwright");
const assert = require("node:assert/strict");

(async () => {
  const browser = await chromium.launch({headless:true});
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  let requestCount = 0;
  const fixture = (version) => {
    const score = version === 1 ? [42,42] : [43,42];
    const q3 = version === 1 ? [1,0] : [2,0];
    const line = version === 1 ? 205.5 : 206.5;
    return {
      ok:true, connected:true, checked_at_utc:new Date().toISOString(),
      total_events:1,basketball_events:1,attempts:[],
      events:[{
        id:"ui-regression-nanjing-xinjiang",team1:"Нанкин",team2:"Синьцзян",
        league:"China CBA",sport_name:"Basketball",score,period:3,clock:version===1?"08:57":"08:45",
        period_length_seconds:600,score_source:"PARI",clock_source:"PARI_explicit_countdown",
        period_scores:[[20,21],[21,21],q3],match_timeline:[],
        external_live:{source:"PARI",period_length_seconds:600,period_count:4,period:3,clock:version===1?"08:57":"08:45"},
        match_total_candidates:[
          {f:"930",pt:line,v:"1.91"},
          {f:"931",pt:line,v:"1.93"}
        ],
        external_statistics:[],
        statistics_quality:{available_metrics:[],missing_metrics:["three_pointers","two_pointers","free_throws","field_goals","rebounds","offensive_rebounds","defensive_rebounds","assists","turnovers","steals","blocks","fouls","possessions"],available_count:0,expected_count:13,coverage_percent:0,complete:false},
        feed_fetched_at_utc:new Date().toISOString(),
        event_freshness_status:"provider_timestamp_unavailable"
      }]
    };
  };
  await page.route("**/api/**", async route => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/pari/live") {
      requestCount++;
      return route.fulfill({status:200,contentType:"application/json",body:JSON.stringify(fixture(requestCount===1?1:2))});
    }
    return route.fulfill({status:200,contentType:"application/json",body:JSON.stringify({ok:true,connected:true,events:[],matches:[],count:0})});
  });
  try {
    await page.goto("http://127.0.0.1:8000/static/index.html", {waitUntil:"domcontentloaded"});
    await page.waitForSelector("#liveMatchList .live-match", {timeout:15000});
    await page.locator("#liveMatchList .live-match").first().click();
    await page.waitForFunction(() => document.querySelector("#selectedMatch")?.hidden === false);
    const matchText = await page.locator("#matchForecast").innerText();
    const quarterText = await page.locator("#quarterForecast").innerText();
    assert.match(matchText,/Единый основной прогноз/);
    assert.match(quarterText,/Единый основной прогноз/);
    assert.match(quarterText,/Gamma-Poisson/);
    assert.doesNotMatch(matchText,/AI Consensus/);
    assert.match(await page.locator("#selectedMatchInfo").innerText(),/42 : 42/);
    assert.match(await page.locator("#historyList").innerText(),/42 : 42/);

    await page.locator("#refreshLiveMatches").click();
    await page.waitForFunction(() => document.querySelector("#selectedMatchInfo")?.textContent.includes("43 : 42"));
    assert.match(await page.locator("#matchForecast").innerText(),/206\.5/);
    await page.locator("#liveMatchList .live-match").first().click();
    const historyCount = await page.locator("#historyList .history-row").count();
    assert.ok(historyCount >= 2,"new selected snapshot must append rather than replace prior history");

    await page.reload({waitUntil:"domcontentloaded"});
    await page.waitForSelector("#liveMatchList .live-match",{timeout:15000});
    const persistedHistory = await page.locator("#historyList .history-row").count();
    assert.ok(persistedHistory >= 2,"local history should survive a page reload");
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({ok:true,requestCount,historyCount,persistedHistory}));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
