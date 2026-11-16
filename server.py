from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Optional
import math, random, os, hashlib, re, asyncio, json
from datetime import datetime, timezone
from html.parser import HTMLParser
from html import unescape
import httpx

app = FastAPI(title="PARI Basketball AI", version="0.1.0")
app.mount("/static", StaticFiles(directory="static"), name="static")

class _AssetParser(HTMLParser):
    """Collect external and inline scripts, preload assets and metadata."""
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.links = []
        self.inline_scripts = []
        self.meta = []
        self.other_assets = []
        self._in_script = False
        self._script_parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        tag = tag.lower()
        if tag == "script":
            if attrs.get("src"):
                self.scripts.append(attrs["src"])
            else:
                self._in_script = True
                self._script_parts = []
        elif tag == "link" and attrs.get("href"):
            self.links.append({
                "href": attrs["href"], "rel": attrs.get("rel", ""),
                "as": attrs.get("as", ""), "type": attrs.get("type", ""),
                "crossorigin": attrs.get("crossorigin", "")
            })
        elif tag == "meta":
            self.meta.append({k: v for k, v in attrs.items() if k in ("name", "property", "content", "http-equiv")})
        else:
            for key in ("src", "href", "data-src", "data-href"):
                value = attrs.get(key)
                if value and (".js" in value.lower() or ".json" in value.lower()):
                    self.other_assets.append({"tag": tag, "attribute": key, "url": value})

    def handle_data(self, data):
        if self._in_script:
            self._script_parts.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "script" and self._in_script:
            value = "".join(self._script_parts).strip()
            if value:
                self.inline_scripts.append(value)
            self._in_script = False
            self._script_parts = []

class QuarterAnalyzeRequest(BaseModel):
    team1: str = "Команда 1"
    team2: str = "Команда 2"
    quarter: int = Field(ge=1, le=4)
    score1: int = Field(ge=0, le=100)
    score2: int = Field(ge=0, le=100)
    clock: str
    period_minutes: int = Field(default=10, ge=5, le=15)
    total_line: float
    simulations: int = Field(default=10000, ge=1000, le=20000)

class AnalyzeRequest(BaseModel):
    team1: str = "Команда 1"
    team2: str = "Команда 2"
    score1: int = Field(ge=0, le=300)
    score2: int = Field(ge=0, le=300)
    period: int = Field(ge=1, le=4)
    clock: str
    period_minutes: int = Field(ge=5, le=15)
    total_line: float
    over_odds: Optional[float] = None
    under_odds: Optional[float] = None
    fouls_total: int = Field(default=0, ge=0, le=100)
    pace_adjustment: float = Field(default=1.0, ge=0.5, le=1.5)
    simulations: int = Field(default=20000, ge=1000, le=100000)

def parse_clock(clock: str) -> int:
    try:
        m, s = clock.split(":")
        sec = int(m) * 60 + int(s)
        if int(s) > 59 or sec < 0:
            raise ValueError()
        return sec
    except Exception:
        raise ValueError("Время должно быть в формате MM:SS")

def poisson_tail(mu: float, threshold: int) -> float:
    if threshold <= 0: return 1.0
    if mu <= 0: return 0.0
    # Stable recurrence for the lower tail; basketball totals have overdispersion,
    # so this is a transparent baseline, not the sole probability estimate.
    p = math.exp(-mu)
    cdf = p
    for k in range(1, threshold):
        p *= mu / k
        cdf += p
    return max(0.0, min(1.0, 1.0-cdf))

def analyze(req: AnalyzeRequest):
    period_sec = req.period_minutes * 60
    remaining_in_period = period_sec - parse_clock(req.clock)
    if remaining_in_period < 0:
        raise ValueError("Время четверти превышает её продолжительность")
    periods_left = 4 - req.period
    remaining_sec = remaining_in_period + periods_left * period_sec
    elapsed_sec = 4 * period_sec - remaining_sec
    if elapsed_sec <= 0:
        raise ValueError("Для LIVE-анализа нужно ненулевое прошедшее время")
    current = req.score1 + req.score2
    pace = current / (elapsed_sec / 60.0)
    # Explicit transparent adjustments; do not infer a foul rate without data.
    close_game = abs(req.score1 - req.score2) <= 6
    late_game = req.period == 4 and remaining_in_period <= 300
    endgame_factor = 1.0
    if late_game and close_game:
        endgame_factor += 0.035
    elif late_game and not close_game:
        endgame_factor -= 0.025
    foul_factor = 1.0 + min(req.fouls_total, 20) * 0.0015
    mu_remaining = pace * (remaining_sec / 60.0) * req.pace_adjustment * endgame_factor * foul_factor
    expected = current + mu_remaining
    # Variance inflation relative to Poisson accounts for pace uncertainty.
    sigma = max(math.sqrt(max(mu_remaining, 1.0)) * 1.18, 1.0)
    threshold = math.floor(req.total_line - current) + 1
    p_over_pois = poisson_tail(mu_remaining, threshold)
    # Monte Carlo: Gaussian approximation to overdispersed remaining points,
    # clipped at zero. This is intentionally labelled approximate.
    rng = random.Random()
    over = 0
    totals = []
    n = req.simulations
    for _ in range(n):
        rem = max(0, rng.gauss(mu_remaining, sigma))
        total = current + rem
        totals.append(total)
        if total > req.total_line: over += 1
    p_over_mc = over / n
    p_under_mc = 1 - p_over_mc
    totals.sort()
    def quantile(q):
        return round(totals[min(n-1, max(0, int(q*(n-1))))], 1)
    # De-vig bookmaker probability when both prices are available.
    market = None
    if req.over_odds and req.under_odds and req.over_odds > 1 and req.under_odds > 1:
        po, pu = 1/req.over_odds, 1/req.under_odds
        market = round(po/(po+pu)*100, 1)
    chosen = "ТБ" if p_over_mc >= 0.5 else "ТМ"
    probability = max(p_over_mc, p_under_mc)
    # Conservative signal grading based on model probability and distance to line.
    edge = abs(expected - req.total_line)
    grade = "A" if probability >= .72 and edge >= 4 else ("B" if probability >= .62 and edge >= 2 else "C")
    return {
        "current_total": current, "elapsed_minutes": round(elapsed_sec/60, 2),
        "remaining_minutes": round(remaining_sec/60, 2), "pace_per_min": round(pace, 3),
        "mu_remaining": round(mu_remaining, 2), "expected_total": round(expected, 1),
        "line": req.total_line, "direction": chosen,
        "p_over_poisson": round(p_over_pois*100, 1),
        "p_over_mc": round(p_over_mc*100, 1), "p_under_mc": round(p_under_mc*100, 1),
        "market_over_devig": market, "range80": [quantile(.10), quantile(.90)],
        "range95": [quantile(.025), quantile(.975)], "grade": grade,
        "simulations": n, "sigma_remaining": round(sigma, 2),
        "warnings": [
            "Монте-Карло использует приближённое распределение остаточных очков; для калибровки нужны исторические данные этой лиги.",
            "Нет подтверждённого LIVE-feed: расчёт основан только на введённых пользователем данных."
        ]
    }

@app.get("/", response_class=HTMLResponse)
def index():
    with open("static/index.html", encoding="utf-8") as f:
        return f.read()

@app.head("/")
def index_head():
    # Render's readiness probe uses HEAD /. Return 200 instead of a false 405.
    return HTMLResponse(content="", status_code=200)

@app.get("/api/health")
def health():
    # This endpoint reports application/adapter readiness only. Actual upstream
    # connectivity is measured by /api/pari/live and must not be hard-coded false.
    return {
        "status": "ok",
        "version": "0.2.0",
        "pari_live_adapter_configured": True,
        "pari_live_endpoint": "/api/pari/live",
        "source_status": "configured_check_live_endpoint",
        "message": "Проверяйте фактическое соединение и число матчей через /api/pari/live."
    }

@app.post("/api/analyze")
def api_analyze(req: AnalyzeRequest):
    return analyze(req)


@app.post("/api/analyze-quarter")
def api_analyze_quarter(req: QuarterAnalyzeRequest):
    period_sec = req.period_minutes * 60
    elapsed_sec = parse_clock(req.clock)
    if elapsed_sec <= 0 or elapsed_sec > period_sec:
        raise ValueError("Время должно быть внутри текущей четверти и больше нуля")
    current = req.score1 + req.score2
    elapsed_min = elapsed_sec / 60.0
    remaining_min = (period_sec - elapsed_sec) / 60.0
    pace = current / elapsed_min
    mu = pace * remaining_min
    expected = current + mu
    sigma = max(math.sqrt(max(mu, 1.0)) * 1.18, 1.0)
    rng = random.Random()
    n = req.simulations
    totals = []
    over = 0
    for _ in range(n):
        total = current + max(0.0, rng.gauss(mu, sigma))
        totals.append(total)
        if total > req.total_line:
            over += 1
    totals.sort()
    def qtile(q):
        return round(totals[min(n - 1, max(0, int(q * (n - 1))))], 1)
    p_over = over / n
    return {
        "quarter": req.quarter, "current_total": current,
        "elapsed_minutes": round(elapsed_min, 2),
        "remaining_minutes": round(remaining_min, 2),
        "pace_per_min": round(pace, 3), "expected_total": round(expected, 1),
        "line": req.total_line, "direction": "ТБ" if p_over >= 0.5 else "ТМ",
        "p_over_mc": round(p_over * 100, 1),
        "p_under_mc": round((1 - p_over) * 100, 1),
        "range80": [qtile(.10), qtile(.90)],
        "range95": [qtile(.025), qtile(.975)],
        "grade": "A" if max(p_over, 1-p_over) >= .72 and abs(expected-req.total_line) >= 3 else ("B" if max(p_over,1-p_over) >= .62 else "C"),
        "simulations": n,
        "warnings": ["Оценка основана на темпе текущей четверти и приближённой симуляции.",
                     "Не учитываются владения, составы, фолы по командам и игровые события; модель пока не обучена на истории лиги."]
    }


@app.get("/api/pari/source-check")
async def pari_source_check():
    """Проверка прямого доступа к публичной LIVE-странице PARI без использования кэша."""
    url = "https://pari.ru/live/basketball"
    requested_at = datetime.now(timezone.utc)
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; PARI-Basketball-AI/0.1; live-source-check)",
        "Accept": "text/html,application/xhtml+xml",
        "Cache-Control": "no-cache, no-store, max-age=0",
        "Pragma": "no-cache",
    }
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=8.0),
            follow_redirects=True,
            headers=headers,
        ) as client:
            response = await client.get(url, params={"_fresh_check": str(int(requested_at.timestamp() * 1000))})
        received_at = datetime.now(timezone.utc)
        body = response.text
        title_match = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
        title = re.sub(r"\s+", " ", title_match.group(1)).strip()[:180] if title_match else None
        class _TextOnly(HTMLParser):
            def __init__(self):
                super().__init__()
                self.parts = []
                self.hidden = 0
            def handle_starttag(self, tag, attrs):
                if tag in ("script", "style", "noscript"):
                    self.hidden += 1
            def handle_endtag(self, tag):
                if tag in ("script", "style", "noscript") and self.hidden:
                    self.hidden -= 1
            def handle_data(self, data):
                if not self.hidden and data.strip():
                    self.parts.append(data.strip())
        parser = _TextOnly()
        parser.feed(body)
        visible_text = re.sub(r"\s+", " ", unescape(" ".join(parser.parts))).strip()
        asset_parser = _AssetParser()
        asset_parser.feed(body)
        script_sources = asset_parser.scripts[:20]
        link_assets = asset_parser.links[:30]
        score_time_patterns = len(re.findall(r"(?<!\d)\d{1,2}:\d{2}(?!\d)", visible_text))
        age_header = response.headers.get("age")
        age_seconds = int(age_header) if age_header and age_header.isdigit() else None
        date_header = response.headers.get("date")
        upstream_age = None
        if date_header:
            try:
                upstream_date = datetime.strptime(date_header, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)
                upstream_age = max(0, int((received_at - upstream_date).total_seconds()))
            except Exception:
                pass
        # Не называем старый ответ свежим: CDN Age или HTTP Date проверяются отдельно.
        freshness_ok = response.status_code == 200 and (age_seconds is not None or upstream_age is not None) and (age_seconds is None or age_seconds <= 60) and (upstream_age is None or upstream_age <= 180)
        return {
            "ok": response.status_code == 200,
            "source": "https://pari.ru/live/basketball",
            "http_status": response.status_code,
            "content_type": response.headers.get("content-type"),
            "content_encoding": response.headers.get("content-encoding"),
            "server_header": response.headers.get("server"),
            "cache_control": response.headers.get("cache-control"),
            "response_url": str(response.url),
            "bytes": len(response.content),
            "raw_html_preview": body[:2400],
            "body_markers": {
                "captcha": bool(re.search(r"captcha|challenge|cloudflare|access denied", body, re.I)),
                "javascript_required": bool(re.search(r"enable javascript|javascript is required|please enable javascript", body, re.I)),
                "json_payload": body.lstrip().startswith(("{", "[")),
            },
            "title": title,
            "visible_text_chars": len(visible_text),
            "score_or_clock_patterns": score_time_patterns,
            "visible_text_preview": visible_text[:2400],
            "script_sources": script_sources,
            "link_assets": link_assets,
            "fetched_at_utc": received_at.isoformat(),
            "request_started_at_utc": requested_at.isoformat(),
            "fetch_duration_ms": round((received_at - requested_at).total_seconds() * 1000),
            "http_age_seconds": age_seconds,
            "http_date_age_seconds": upstream_age,
            "etag": response.headers.get("etag"),
            "last_modified": response.headers.get("last-modified"),
            "content_sha256": hashlib.sha256(response.content).hexdigest(),
            "freshness_check": "http_response_recent_event_freshness_unverified" if freshness_ok else "stale_or_unverified",
            "note": "Свежий HTTP Date подтверждает только время ответа сервера, но не свежесть LIVE-счёта или коэффициентов. Актуальность матчей можно подтвердить только после обнаружения и проверки feed с временными метками событий.",
        }
    except httpx.TimeoutException:
        return {"ok": False, "source": url, "freshness_check": "unavailable", "error": "PARI не ответил за 15 секунд", "checked_at_utc": datetime.now(timezone.utc).isoformat()}
    except httpx.HTTPError as exc:
        return {"ok": False, "source": url, "freshness_check": "unavailable", "error": type(exc).__name__, "checked_at_utc": datetime.now(timezone.utc).isoformat()}


@app.get("/api/pari/discover")
async def pari_discover():
    """Deep read-only inspection of PARI LIVE HTML, inline scripts and public assets."""
    from urllib.parse import urljoin, urlparse
    import json

    page_urls = ["https://pari.ru/live/basketball", "https://pari.ru/live"]
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.7,en;q=0.6",
        "Cache-Control": "no-cache, no-store, max-age=0",
        "Pragma": "no-cache",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Upgrade-Insecure-Requests": "1",
    }

    def find_hints(source):
        found = []
        for match in re.finditer(r"""["']([^"']{1,260})["']""", source):
            value = match.group(1)
            low = value.lower()
            if any(word in low for word in ("api", "graphql", "live", "event", "market", "odds", "line", "sport", "basket", "socket", "ws")):
                if value.startswith(("/", "https://", "wss://")):
                    found.append(value)
        return list(dict.fromkeys(found))

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(22.0, connect=10.0), follow_redirects=True, headers=headers) as client:
            pages = []
            asset_refs = []
            inline_hints = []
            bootstrap_urls = []
            for page_url in page_urls:
                try:
                    response = await client.get(page_url, params={"_diag": str(int(datetime.now(timezone.utc).timestamp() * 1000))})
                    body = response.text
                    parser = _AssetParser()
                    parser.feed(body)
                    refs = []
                    for src in parser.scripts:
                        refs.append({"url": urljoin(str(response.url), src), "kind": "script"})
                    for link in parser.links:
                        rel = link.get("rel", "").lower()
                        href = link.get("href", "")
                        if ".js" in href.lower() or "modulepreload" in rel or "manifest" in rel or ("preload" in rel and link.get("as") == "script"):
                            refs.append({"url": urljoin(str(response.url), href), "kind": "preload"})
                    for ref in refs:
                        asset_refs.append(ref)
                    inline_analysis = []
                    # Agent hosts/config are injected into page runtime, not necessarily bootstrap.js.
                    runtime_source = body + "\n" + "\n".join(parser.inline_scripts)
                    runtime_hosts = list(dict.fromkeys(re.findall(
                        r"(?i)(?:[a-z0-9-]+\.)+pb06e2-resources\.(?:ru|com)", runtime_source
                    )))[:100]
                    runtime_windows = []
                    for token in ("urlsPreloadRaw", "_buildInfo", "commonApi", "lineQueues",
                                  "commonQueues", "clientsapi", "line-vk", "fastviewdata"):
                        pos = 0
                        hits = 0
                        while hits < 2:
                            pos = runtime_source.find(token, pos)
                            if pos < 0:
                                break
                            runtime_windows.append({"token": token,
                                "snippet": runtime_source[max(0,pos-450):min(len(runtime_source),pos+950)]})
                            pos += len(token)
                            hits += 1
                    for inline_index, inline in enumerate(parser.inline_scripts):
                        inline_hints.extend(find_hints(inline))
                        config_match = re.search(r"urlsPreloadRaw='(\{.*?\})';if\(urlsPreloadRaw\)", inline, re.S)
                        if config_match:
                            try:
                                cfg = json.loads(config_match.group(1))
                                static_hosts = [cfg.get("static", "")] + cfg.get("staticQueue", [])
                                static_path = cfg.get("staticSiteDir", "")
                                version = (cfg.get("site") or {}).get("version", "")
                                for static_host in static_hosts:
                                    if static_host and static_path and version:
                                        host_url = static_host if static_host.startswith("http") else "https:" + static_host
                                        bootstrap_urls.append(host_url.rstrip("/") + static_path + "/" + version + "/bootstrap.js")
                            except Exception:
                                pass
                        inline_analysis.append({
                            "index": inline_index,
                            "chars": len(inline),
                            "loader_markers": {
                                "create_element_script": bool(re.search(r"createElement\s*\(\s*['\"]script", inline, re.I)),
                                "script_src_assignment": bool(re.search(r"\.src\s*=", inline, re.I)),
                                "document_write": bool(re.search(r"document\.write", inline, re.I)),
                                "dynamic_import": bool(re.search(r"\bimport\s*\(", inline)),
                                "fetch": bool(re.search(r"\bfetch\s*\(", inline)),
                                "xmlhttprequest": "XMLHttpRequest" in inline,
                                "websocket": "WebSocket" in inline,
                                "service_worker": "serviceWorker" in inline,
                                "eval_or_function": bool(re.search(r"\beval\s*\(|new\s+Function", inline)),
                                "base64": "atob(" in inline or "base64" in inline.lower(),
                                "source_map": "sourceMappingURL=" in inline
                            },
                            "external_url_hints": list(dict.fromkeys(re.findall(r"https?://[^\s'\"<>]{1,260}", inline)))[:30],
                            "script_path_hints": list(dict.fromkeys(re.findall(r"['\"]([^'\"]{1,240}\.js(?:\?[^'\"]*)?)['\"]", inline, re.I)))[:30],
                            "preview": inline[:7000]
                        })
                    manifest = None
                    manifest_refs = [x["url"] for x in refs if "manifest" in x["url"].lower() and x["url"].lower().endswith(".json")]
                    for manifest_url in manifest_refs[:2]:
                        try:
                            mr = await client.get(manifest_url)
                            if mr.status_code == 200:
                                try:
                                    manifest = mr.json()
                                except Exception:
                                    manifest = {"parse_error": True, "preview": mr.text[:800]}
                        except httpx.HTTPError:
                            pass
                    title_match = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
                    pages.append({
                        "requested_url": page_url, "response_url": str(response.url),
                        "status": response.status_code, "bytes": len(response.content),
                        "content_type": response.headers.get("content-type"),
                        "title": re.sub(r"\s+", " ", title_match.group(1)).strip()[:180] if title_match else None,
                        "external_scripts": len(parser.scripts), "inline_scripts": len(parser.inline_scripts),
                        "inline_script_chars": sum(len(x) for x in parser.inline_scripts),
                        "links": parser.links[:40], "meta": parser.meta[:30],
                        "other_asset_refs": parser.other_assets[:30], "manifest": manifest,
                        "markers": {
                            "next_data": "__NEXT_DATA__" in body,
                            "nuxt": "__NUXT__" in body,
                            "service_worker": "serviceWorker" in body or "service-worker" in body.lower(),
                            "websocket": "WebSocket" in body or "socket.io" in body,
                            "captcha": bool(re.search(r"captcha|challenge|cloudflare|access denied", body, re.I))
                        },
                        "inline_route_hints": find_hints(" ".join(parser.inline_scripts))[:80],
                        "runtime_host_hints": runtime_hosts,
                        "runtime_config_context_windows": runtime_windows[:18],
                        "inline_script_analysis": inline_analysis,
                        "html_preview": body[:1200]
                    })
                except httpx.HTTPError as exc:
                    pages.append({"requested_url": page_url, "error": type(exc).__name__})

            # Fetch only HTTPS resources hosted by pari.ru or its subdomains.
            safe_assets = []
            for asset in asset_refs:
                parsed = urlparse(asset["url"])
                host = (parsed.hostname or "").lower()
                if parsed.scheme == "https" and (host == "pari.ru" or host.endswith(".pari.ru")):
                    safe_assets.append(asset)
            safe_assets = list({x["url"]: x for x in safe_assets}.values())[:18]
            scanned = []
            bundle_hints = []
            for asset in safe_assets:
                try:
                    ar = await client.get(asset["url"])
                    item = {"url": asset["url"], "kind": asset["kind"], "status": ar.status_code,
                            "content_type": ar.headers.get("content-type"), "bytes": len(ar.content)}
                    if ar.status_code == 200:
                        hints = find_hints(ar.text[:2500000])
                        item["route_hints"] = hints[:80]
                        bundle_hints.extend(hints)
                    scanned.append(item)
                except httpx.HTTPError as exc:
                    scanned.append({"url": asset["url"], "error": type(exc).__name__})

            bootstrap_scans = []
            chunk_scans = []
            # Try all three official PARI static CDN mirrors; a timeout on the primary must not stop discovery.
            for bootstrap_url in list(dict.fromkeys(bootstrap_urls))[:3]:
                parsed = urlparse(bootstrap_url)
                host = (parsed.hostname or "").lower()
                if parsed.scheme != "https" or not (host.endswith(".pb06e2-resources.ru") or host.endswith(".pb06e2-resources.com")):
                    continue
                try:
                    br = await client.get(bootstrap_url, timeout=httpx.Timeout(14.0, connect=4.0))
                    item = {"url": bootstrap_url, "status": br.status_code,
                            "content_type": br.headers.get("content-type"), "bytes": len(br.content)}
                    if br.status_code == 200:
                        source = br.text[:5000000]
                        item["route_hints"] = find_hints(source)[:160]
                        item["host_hints"] = list(dict.fromkeys(re.findall(r"(?i)(?:[a-z0-9-]+\.)+pb06e2-resources\.(?:ru|com)", source)))[:80]
                        item["api_path_hints"] = list(dict.fromkeys(re.findall(r"""['"]([^'"]{1,220}(?:api|live|event|market|odds|line|sport|basket)[^'"]{0,180})['"]""", source, re.I)))[:120]
                        item["keyword_counts"] = {word: len(re.findall(word, source, re.I)) for word in ("clientsapi", "commonApi", "line", "live", "event", "market", "odds", "graphql", "websocket")}
                        item["preview"] = source[:1800]
                        # Webpack runtime reveals the exact public chunk filenames.
                        runtime_windows = []
                        for token in ("43010", "5793", "50480", ".u=", ".u ="):
                            pos = source.find(token)
                            if pos >= 0:
                                runtime_windows.append({"token": token, "snippet": source[max(0, pos-500):min(len(source), pos+1800)]})
                        item["runtime_windows"] = runtime_windows[:12]
                        # PARI webpack maps numeric chunk IDs to semantic filenames.
                        chunk_name_map = {}
                        map_patterns = [
                            r"\.u\s*=\s*function\s*\([^)]*\)\s*\{.{0,800}?\(\{(.*?)\}\)\s*\[",
                            r"\.u\s*=\s*e\s*=>.{0,300}?\(\{(.*?)\}\)\s*\["
                        ]
                        for map_pattern in map_patterns:
                            map_match = re.search(map_pattern, source, re.S)
                            if map_match:
                                for key, value in re.findall(r"([0-9]+)\s*:\s*['\"]([^'\"]+)['\"]", map_match.group(1)):
                                    chunk_name_map[key] = value
                        # The PARI webpack runtime can put the chunk map beyond the
                        # small legacy regex window. Recover exact numeric-id/name pairs
                        # from the complete bootstrap source, then target match-center
                        # and sport-statistics lazy modules explicitly.
                        for key, value in re.findall(r"([0-9]+)\s*:\s*['\"]([^'\"]+)['\"]", source):
                            if re.search(r"matchCenter|sportstats|basketball|eventView", value, re.I):
                                chunk_name_map[key] = value
                        item["webpack_chunk_name_map"] = {
                            key: chunk_name_map.get(key) for key in ("43010", "5793", "50480")
                        }
                        item["statistics_chunk_name_map"] = {
                            key: value for key, value in chunk_name_map.items()
                            if re.search(r"matchCenter|sportstats|basketball|eventView", value, re.I)
                        }
                        # Besides bootstrap's initial chunks, inspect a small set of semantically
                        # relevant lazy chunks: live, line, event, sport, market and common API.
                        semantic_candidates = [
                            (chunk_id, filename + ".js")
                            for chunk_id, filename in chunk_name_map.items()
                            if re.search(r"live|line|event|sport|market|common|api|basket|score|match|sportstats|matchCenter|eventView", filename, re.I)
                        ]
                        item["semantic_chunk_candidates"] = [
                            {"chunk_id": chunk_id, "filename": filename}
                            for chunk_id, filename in semantic_candidates[:24]
                        ]
                        chunk_candidates = []
                        for chunk_id in ("43010", "5793", "50480"):
                            semantic_name = chunk_name_map.get(chunk_id)
                            if semantic_name:
                                chunk_candidates.append((chunk_id, semantic_name + ".js"))
                            chunk_candidates.append((chunk_id, chunk_id + ".js"))
                        semantic_candidates.sort(
                            key=lambda item: (0 if re.search(r"sportstats|matchCenter|basketball", item[1], re.I) else 1, item[0])
                        )
                        chunk_candidates.extend(semantic_candidates[:12])
                        base = bootstrap_url.rsplit("/", 1)[0] + "/"
                        seen_chunk_urls = set()
                        for chunk_id, filename in chunk_candidates:
                            chunk_url = base + filename
                            if chunk_url in seen_chunk_urls:
                                continue
                            seen_chunk_urls.add(chunk_url)
                            try:
                                cr = await client.get(chunk_url)
                                chunk_item = {"chunk_id": chunk_id, "url": chunk_url, "status": cr.status_code,
                                              "content_type": cr.headers.get("content-type"), "bytes": len(cr.content)}
                                if cr.status_code == 200 and ("javascript" in (cr.headers.get("content-type") or "").lower() or cr.text.lstrip().startswith(("!", "(", "var ", "(()=>", "webpack"))):
                                    chunk_source = cr.text[:4000000]
                                    chunk_item["host_hints"] = list(dict.fromkeys(re.findall(r"(?i)(?:[a-z0-9-]+\.)+pb06e2-resources\.(?:ru|com)", chunk_source)))[:60]
                                    chunk_item["api_path_hints"] = list(dict.fromkeys(re.findall(r"""['"]([^'"]{1,220}(?:api|live|event|market|odds|line|sport|basket)[^'"]{0,180})['"]""", chunk_source, re.I)))[:120]
                                    chunk_item["keyword_counts"] = {word: len(re.findall(word, chunk_source, re.I)) for word in ("clientsapi", "commonApi", "line", "live", "event", "market", "odds", "graphql", "websocket", "fetch")}
                                    # Surface code around request/configuration primitives so the next
                                    # diagnostic can identify actual public feed calls, not just keywords.
                                    # Prioritize service wiring and request dispatch: the previous 24-window cap
                                    # could hide getServerAgentSettings behind generic fetch/getApiState hits.
                                    context_tokens = (
                                        "getServerAgentSettings", "handleAgentsUpdate", "coreSetup",
                                        "queryCustomService", "serverAgentSettings", "additionalRequestParams",
                                        "resultsByService", "unknownServicesByGetApiState", "requestInterval",
                                        "clientCommonAgent", "lineAgents", "commonApi", "lineQueues", "commonQueues",
                                        "sportEvents", "sportCompetitionAndCompetitionGroup", "eventsFilter",
                                        "eventView", "main_sports_search", "getApiState", "ApiRequest",
                                        "apiUrl", "baseURL", "fetch(", "axios", "request("
                                    )
                                    context_windows = []
                                    targeted_windows = []
                                    for token in context_tokens:
                                        pos = 0
                                        hits = 0
                                        while hits < 6:
                                            pos = chunk_source.find(token, pos)
                                            if pos < 0:
                                                break
                                            window = {"token": token,
                                                      "snippet": chunk_source[max(0, pos-900):min(len(chunk_source), pos+2200)]}
                                            context_windows.append(window)
                                            if token in ("getServerAgentSettings", "handleAgentsUpdate", "coreSetup",
                                                         "queryCustomService", "serverAgentSettings", "additionalRequestParams",
                                                         "resultsByService", "sportEvents", "sportCompetitionAndCompetitionGroup",
                                                         "eventsFilter", "eventView", "main_sports_search"):
                                                targeted_windows.append(window)
                                            pos += len(token)
                                            hits += 1
                                    chunk_item["api_context_windows"] = context_windows[:80]
                                    chunk_item["targeted_context_windows"] = targeted_windows[:50]
                                    service_terms = ("sportEvents", "sportCompetitionAndCompetitionGroup",
                                                     "queryCustomService", "commandName", "serviceName",
                                                     "additionalRequestParams", "getServerAgentSettings",
                                                     "requestInterval", "eventView", "main_sports_search")
                                    chunk_item["service_keyword_counts"] = {
                                        term: len(re.findall(re.escape(term), chunk_source, re.I))
                                        for term in service_terms
                                    }
                                    # Detect data-model vocabulary and show bounded source context.
                                    # These are code clues, not proof that the values are live.
                                    data_patterns = {
                                        "event_ids": r"\b(?:eventId|sportEventId|event_id|eventID)\b",
                                        "team_fields": r"\b(?:homeTeam|awayTeam|teamHome|teamAway|competitor|participant)\b",
                                        "score_fields": r"\b(?:score|homeScore|awayScore|currentScore|scoreHome|scoreAway)\b",
                                        "clock_fields": r"\b(?:clock|timer|period|quarter|matchTime)\b",
                                        "market_fields": r"\b(?:odds|price|marketId|outcome)\b",
                                    }
                                    chunk_item["data_model_keyword_counts"] = {
                                        name: len(re.findall(pattern, chunk_source, re.I))
                                        for name, pattern in data_patterns.items()
                                    }
                                    data_windows = []
                                    for token in ("sportEventId", "eventId", "homeTeam", "awayTeam",
                                                  "homeScore", "awayScore", "currentScore", "scoreHome",
                                                  "scoreAway", "marketId", "matchTime", "competitor"):
                                        pos = 0
                                        hits = 0
                                        while hits < 2:
                                            pos = chunk_source.find(token, pos)
                                            if pos < 0:
                                                break
                                            data_windows.append({
                                                "token": token,
                                                "snippet": chunk_source[max(0, pos-350):min(len(chunk_source), pos+650)]
                                            })
                                            pos += len(token)
                                            hits += 1
                                    chunk_item["data_model_context_windows"] = data_windows[:18]
                                    chunk_item["request_string_hints"] = list(dict.fromkeys(
                                        re.findall(r"""['"]([^'"]{1,180})['"]""", chunk_source)
                                    ))
                                    chunk_item["request_string_hints"] = [
                                        value for value in chunk_item["request_string_hints"]
                                        if "/" in value or any(word in value.lower() for word in ("api", "event", "live", "market", "sport", "line", "odds"))
                                    ][:160]
                                    chunk_item["preview"] = chunk_source[:1200]
                                chunk_scans.append(chunk_item)
                            except httpx.HTTPError as exc:
                                chunk_scans.append({"chunk_id": chunk_id, "url": chunk_url, "error": type(exc).__name__})
                        bundle_hints.extend(item["route_hints"])
                    bootstrap_scans.append(item)
                    if br.status_code == 200:
                        # One working mirror is enough; avoid downloading identical bundles repeatedly.
                        break
                except httpx.HTTPError as exc:
                    bootstrap_scans.append({"url": bootstrap_url, "error": type(exc).__name__})

        hints = list(dict.fromkeys(inline_hints + bundle_hints))
        return {
            "ok": any(x.get("status") == 200 for x in pages),
            "diagnostic_version": 10, "pages": pages,
            "asset_refs_found": len(asset_refs), "first_party_assets_scanned": len(scanned),
            "scanned_assets": scanned, "bootstrap_scans": bootstrap_scans, "chunk_scans": chunk_scans, "bootstrap_urls_found": len(set(bootstrap_urls)), "inline_route_hints": list(dict.fromkeys(inline_hints))[:100],
            "route_candidates": hints[:200], "confirmed_api": False,
            "note": "Версия 10 приоритизирует полный контекст getServerAgentSettings/coreSetup, пользовательских service mappings и диспетчеризации запросов; это всё ещё анализ кода, а не подтверждение LIVE-feed. Feed подтверждается только реальным ответом и повторным изменением счёта/времени/коэффициентов."
        }
    except httpx.TimeoutException:
        return {"ok": False, "diagnostic_version": 10, "error": "PARI diagnostic timed out", "checked_at_utc": datetime.now(timezone.utc).isoformat()}
    except httpx.HTTPError as exc:
        return {"ok": False, "diagnostic_version": 10, "error": type(exc).__name__, "checked_at_utc": datetime.now(timezone.utc).isoformat()}
    except Exception as exc:
        # Keep the diagnostic response machine-readable instead of returning an opaque 500.
        return {"ok": False, "diagnostic_version": 10, "error": type(exc).__name__, "checked_at_utc": datetime.now(timezone.utc).isoformat()}



async def _enrich_with_sofascore_live(events):
    """Enrich PARI rows with independently sourced live score/period/clock.
    Only high-confidence, one-to-one team-name matches are accepted.
    """
    import unicodedata
    from difflib import SequenceMatcher

    def norm(value):
        value = str(value or "")
        # Repair PARI's UTF-8-as-CP1251 mojibake before transliteration.
        for _ in range(3):
            try:
                repaired = value.encode("cp1251").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                break
            bad_before = sum(value.count(ch) for ch in ("Р", "С", "Ð", "Ñ", "�"))
            bad_after = sum(repaired.count(ch) for ch in ("Р", "С", "Ð", "Ñ", "�"))
            if repaired == value or bad_after >= bad_before:
                break
            value = repaired
        value = unicodedata.normalize("NFKD", value.casefold())
        value = "".join(ch for ch in value if not unicodedata.combining(ch))
        cyrillic_to_latin = str.maketrans({
            "а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ё":"e",
            "ж":"zh","з":"z","и":"i","й":"j","к":"k","л":"l","м":"m",
            "н":"n","о":"o","п":"p","р":"r","с":"s","т":"t","у":"u",
            "ф":"f","х":"h","ц":"ts","ч":"ch","ш":"sh","щ":"shch",
            "ъ":"","ы":"y","ь":"","э":"e","ю":"yu","я":"ya"
        })
        value = value.translate(cyrillic_to_latin)
        value = re.sub(r"\b(bc|basket|basketball|club|team|bk|kk|women|w)\b", " ", value)
        return re.sub(r"[^a-z0-9]+", "", value)

    def team_score(a, b):
        def variants(value):
            base = norm(value)
            values = {base} if base else set()
            aliases = {
                "hyuston": "houston", "vashington": "washington",
                "nyuyork": "newyork", "losandzheles": "losangeles",
                "sanfrantsisko": "sanfrancisco",
                "lajons": "lions", "perpechual": "perpetual",
                "univperpechualhelpsistemd alta".replace(" ", ""): "uphsdaltas",
            }
            for source, target in aliases.items():
                if source in base:
                    values.add(base.replace(source, target))
            return values
        aa_values, bb_values = variants(a), variants(b)
        if not aa_values or not bb_values:
            return 0.0
        score = max(SequenceMatcher(None, aa, bb).ratio()
                    for aa in aa_values for bb in bb_values)
        for aa in aa_values:
            for bb in bb_values:
                shorter, longer = (aa, bb) if len(aa) <= len(bb) else (bb, aa)
                if len(shorter) >= 4 and shorter in longer:
                    score = max(score, 0.90)
        return score

    def pair_score(a, b, x, y):
        # Both teams must independently match; averaging can hide a wrong opponent.
        direct = min(team_score(a, x), team_score(b, y))
        reverse = min(team_score(a, y), team_score(b, x))
        return max(direct, reverse)

    if not events:
        return {"source": "sofascore", "status": "skipped_no_pari_events", "matched": 0}
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://www.sofascore.com/",
        "Origin": "https://www.sofascore.com",
        "X-Requested-With": "XMLHttpRequest",
        "Cache-Control": "no-cache",
    }
    urls = (
        "https://www.sofascore.com/api/v1/sport/basketball/events/live",
        "https://api.sofascore.com/api/v1/sport/basketball/events/live",
    )
    payload = None
    errors = []
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(7.0, connect=4.0),
            follow_redirects=True,
            headers=headers,
        ) as client:
            for url in urls:
                try:
                    response = await client.get(url)
                    if response.status_code == 200:
                        candidate = response.json()
                        if isinstance(candidate, dict) and isinstance(candidate.get("events"), list):
                            payload = candidate
                            break
                        errors.append("unexpected_json_shape")
                    else:
                        errors.append("HTTP_" + str(response.status_code))
                except (httpx.HTTPError, ValueError) as exc:
                    errors.append(type(exc).__name__)
            # Some lower-division matches are absent from the live index briefly.
            # Today's scheduled feed may already carry their live status and score.
            # Add today's fixtures too: smaller domestic leagues may be absent
            # from the global live index even while a match is in progress.
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            for base in ("https://www.sofascore.com/api/v1", "https://api.sofascore.com/api/v1"):
                try:
                    response = await client.get(base + "/sport/basketball/scheduled-events/" + today)
                    if response.status_code == 200:
                        candidate = response.json()
                        if isinstance(candidate, dict) and isinstance(candidate.get("events"), list):
                            if payload is None:
                                payload = candidate
                            else:
                                existing = {str(e.get("id")) for e in payload.get("events", []) if isinstance(e, dict)}
                                payload["events"].extend(e for e in candidate["events"]
                                                         if isinstance(e, dict) and str(e.get("id")) not in existing)
                            break
                    else:
                        errors.append("scheduled_HTTP_" + str(response.status_code))
                except (httpx.HTTPError, ValueError) as exc:
                    errors.append("scheduled_" + type(exc).__name__)
    except Exception as exc:
        errors.append(type(exc).__name__)

    sofascore_has_scored_events = bool(
        isinstance(payload, dict)
        and isinstance(payload.get("events"), list)
        and any(
            isinstance(item, dict)
            and isinstance(item.get("homeScore"), dict)
            and isinstance(item.get("awayScore"), dict)
            and item["homeScore"].get("current") is not None
            and item["awayScore"].get("current") is not None
            for item in payload["events"]
        )
    )
    # Query SportScore as a complementary source whenever at least one PARI
    # fixture still lacks a score. A single scored SofaScore event must not disable
    # fallback enrichment for every other live match.
    pari_needs_external_enrichment = any(
        not (isinstance(event.get("score"), (list, tuple)) and len(event.get("score")) >= 2)
        or event.get("clock") is None
        or event.get("period") is None
        or not event.get("external_statistics")
        for event in events
    )
    sportscore_diagnostics = None
    if (not sofascore_has_scored_events) or pari_needs_external_enrichment:
        # Keep SofaScore as a source too; SportScore fills gaps per fixture.
        sportscore_errors = []
        try:
            sport_payloads = []
            sportscore_raw_counts = []
            sportscore_status_samples = []
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(8.0, connect=4.0),
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            ) as client:
                # The widget feed is a live + recent list. The documented fixtures
                # endpoint with status=live is a second independent way to obtain
                # today's in-progress basketball games.
                requests = [
                    # Ask the widget feed explicitly for live rows; its default response
                    # can be a recent-results list dominated by finished matches.
                    ("widget_live", "https://sportscore.com/api/widget/matches/",
                     {"sport": "basketball", "status": "live", "live": "true",
                      "limit": 100, "src": "pari-basketball-ai"}),
                    ("widget", "https://sportscore.com/api/widget/matches/",
                     {"sport": "basketball", "limit": 50, "src": "pari-basketball-ai"}),
                    ("fixtures", "https://sportscore.com/api/v1/fixtures/",
                     {"sport": "basketball", "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                      "status": "live", "limit": 200, "src": "pari-basketball-ai"}),
                ]
                for label, endpoint, params in requests:
                    try:
                        response = await client.get(endpoint, params=params)
                        response.raise_for_status()
                        candidate = response.json()
                        if isinstance(candidate, dict):
                            sport_payloads.append(candidate)
                            rows = candidate.get("matches") or candidate.get("fixtures") or candidate.get("events") or candidate.get("data") or []
                            if isinstance(rows, dict):
                                rows = rows.get("matches") or rows.get("fixtures") or rows.get("events") or []
                            sportscore_raw_counts.append({"endpoint": label, "count": len(rows) if isinstance(rows, list) else 0,
                                                          "keys": list(candidate.keys())[:12]})
                            if isinstance(rows, list):
                                sportscore_status_samples.extend(str(row.get("status") or row.get("status_text") or "")
                                                                  for row in rows[:8] if isinstance(row, dict))
                        else:
                            sportscore_errors.append(label + "_unexpected_json")
                    except (httpx.HTTPError, ValueError) as exc:
                        sportscore_errors.append(label + "_" + type(exc).__name__)
            matches = []
            for candidate in sport_payloads:
                rows = candidate.get("matches") or candidate.get("fixtures") or candidate.get("events") or candidate.get("data") or []
                if isinstance(rows, dict):
                    rows = rows.get("matches") or rows.get("fixtures") or rows.get("events") or []
                if isinstance(rows, list):
                    matches.extend(row for row in rows if isinstance(row, dict))
            # De-duplicate records returned by both endpoints.
            unique_matches = {}
            for item in matches:
                key = str(item.get("slug") or item.get("id") or
                          (str(item.get("home") or item.get("homeTeam") or "") + "|" +
                           str(item.get("away") or item.get("awayTeam") or "")))
                unique_matches[key] = item
            matches = list(unique_matches.values())
            external_rows = []
            for item in matches:
                if not isinstance(item, dict):
                    continue
                # Widget feed contains recent finals. Accept only explicit in-play
                # states or a clearly identified active period; reject terminal states first.
                status_values = [item.get(key) for key in
                                 ("status", "status_text", "state", "match_status", "statusName")]
                status_text = " ".join(str(value) for value in status_values if value is not None).casefold().strip()
                status_compact = re.sub(r"[^a-z0-9]+", "", status_text)
                terminal_tokens = ("finished", "completed", "ended", "full time", "fulltime",
                                   "after overtime", "cancelled", "canceled", "postponed", "abandoned",
                                   "завершен", "завершён", "окончен", "отменен", "отменён")
                if any(token in status_text for token in terminal_tokens):
                    continue
                explicit_live = any(token in status_compact for token in
                                    ("live", "inprogress", "playing", "halftime", "интервал"))
                period_live = bool(re.search(
                    r"(?:\bq[1-4]\b|\b[1-4](?:st|nd|rd|th)\s+quarter\b|\bquarter\s*[1-4]\b|\bperiod\s*[1-4]\b|[1-4]\s*четверт)",
                    status_text, re.I
                ))
                if not (explicit_live or period_live):
                    continue
                hs, aws = item.get("home_score"), item.get("away_score")
                # Accept common provider score shapes without treating missing values as 0.
                def score_number(value):
                    if isinstance(value, dict):
                        for key in ("current", "score", "total", "value", "points"):
                            if value.get(key) is not None:
                                value = value.get(key)
                                break
                    if isinstance(value, (list, tuple)) and value:
                        value = value[-1]
                    try:
                        number = float(value)
                        return int(number) if math.isfinite(number) and number >= 0 else None
                    except (TypeError, ValueError, OverflowError):
                        return None
                if hs is None or aws is None:
                    score_obj = item.get("score") or item.get("scores") or {}
                    if isinstance(score_obj, dict):
                        hs = hs if hs is not None else score_obj.get("home") or score_obj.get("home_score") or score_obj.get("team1")
                        aws = aws if aws is not None else score_obj.get("away") or score_obj.get("away_score") or score_obj.get("team2")
                home_points, away_points = score_number(hs), score_number(aws)
                score_pair = ([home_points, away_points]
                              if home_points is not None and away_points is not None else None)
                # Team fields are objects in some SportScore responses and plain
                # strings in others. Normalize both without turning dicts into names.
                home_raw = item.get("home") or item.get("homeTeam") or item.get("home_team") or {}
                away_raw = item.get("away") or item.get("awayTeam") or item.get("away_team") or {}
                if score_pair is None and isinstance(home_raw, dict) and isinstance(away_raw, dict):
                    nested_home = home_raw.get("score") or home_raw.get("points") or home_raw.get("currentScore")
                    nested_away = away_raw.get("score") or away_raw.get("points") or away_raw.get("currentScore")
                    nested_hp, nested_ap = score_number(nested_home), score_number(nested_away)
                    if nested_hp is not None and nested_ap is not None:
                        score_pair = [nested_hp, nested_ap]
                home_name = str(home_raw.get("name") or home_raw.get("title") or home_raw.get("shortName") or "") if isinstance(home_raw, dict) else str(home_raw)
                away_name = str(away_raw.get("name") or away_raw.get("title") or away_raw.get("shortName") or "") if isinstance(away_raw, dict) else str(away_raw)
                if not home_name or not away_name:
                    continue
                # Women's competitions are intentionally excluded from this project.
                gender_text = " ".join((home_name, away_name, status_text,
                                        str(item.get("competition") or item.get("league") or ""),
                                        str(item.get("tournament") or ""))).casefold()
                if re.search(r"\b(women|woman|female|ladies|girls|wbb|womens|u\s*\d+\s*girls)\b|женск|девуш", gender_text):
                    continue
                status_label = " ".join(str(item.get(key) or "") for key in
                                         ("status_text", "status", "state", "period", "quarter"))
                period_match = re.search(
                    r"(?:\bq|quarter|period|четверть)\s*([1-4])|\b([1-4])(?:st|nd|rd|th)\s*quarter",
                    status_label, re.I
                )
                period = int(period_match.group(1) or period_match.group(2)) if period_match else None
                if period is None and str(item.get("period") or "").isdigit():
                    candidate_period = int(item["period"])
                    period = candidate_period if 1 <= candidate_period <= 4 else None
                # Only parse actual countdown fields. Never read HH:MM from ISO dates,
                # kickoff timestamps, or status text as a game clock.
                clock = None
                # Prefer explicit countdown strings. Some feeds encode the clock as
                # seconds or expose it under alternate countdown field names.
                clock_fields = ("clock", "timer", "time_remaining", "timeRemaining",
                                "countdown", "remaining_time", "remaining_seconds",
                                "clock_seconds", "period_clock")
                clock_values = [item.get(key) for key in clock_fields if item.get(key) is not None]
                clock_value = " ".join(str(value) for value in clock_values)
                if not clock_value:
                    clock_value = str(item.get("status_text") or "")
                clock_value = re.sub(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b", " ", clock_value)
                clock_match = re.search(r"\b(\d{1,2}):([0-5]\d)\b", clock_value)
                if clock_match:
                    candidate_clock = int(clock_match.group(1)) * 60 + int(clock_match.group(2))
                    if candidate_clock <= 15 * 60:
                        clock = f"{candidate_clock // 60:02d}:{candidate_clock % 60:02d}"
                if clock is None:
                    for key in ("remaining_seconds", "clock_seconds", "countdown_seconds"):
                        value = item.get(key)
                        try:
                            seconds = int(float(value))
                            if 0 <= seconds <= 15 * 60:
                                clock = f"{seconds // 60:02d}:{seconds % 60:02d}"
                                break
                        except (TypeError, ValueError, OverflowError):
                            pass
                # Keep quarter scores only when explicitly supplied by SportScore.
                period_scores = {}
                for n in range(1, 5):
                    hv = hs.get("period" + str(n)) if isinstance(hs, dict) else None
                    av = aws.get("period" + str(n)) if isinstance(aws, dict) else None
                    if hv is None: hv = item.get("home_period" + str(n), item.get("homePeriod" + str(n)))
                    if av is None: av = item.get("away_period" + str(n), item.get("awayPeriod" + str(n)))
                    hpn, apn = score_number(hv), score_number(av)
                    if hpn is not None and apn is not None: period_scores[str(n)] = [hpn, apn]
                # Keep the human-readable slug separate from the provider's numeric ID:
                # /api/widget/match/ requires slug, while /api/widget/tracker/ requires id.
                nested_match = item.get("match") if isinstance(item.get("match"), dict) else {}
                nested_fixture = item.get("fixture") if isinstance(item.get("fixture"), dict) else {}
                identity_sources = (item, nested_match, nested_fixture)
                def first_identity(keys):
                    for source in identity_sources:
                        for key in keys:
                            value = source.get(key) if isinstance(source, dict) else None
                            if value not in (None, ""):
                                return str(value)
                    return None
                match_slug = first_identity(("slug", "url_slug", "match_slug", "matchSlug", "seo_slug", "seoSlug"))
                provider_id = first_identity(("id", "match_id", "matchId", "fixture_id", "fixtureId", "event_id", "eventId", "_id", "game_id", "gameId"))
                if not match_slug:
                    raw_url = first_identity(("url", "href", "match_url", "matchUrl", "link"))
                    if raw_url:
                        match_slug = raw_url.rstrip("/").split("/")[-1].split("?")[0] or None
                external_rows.append({
                    "home": home_name, "away": away_name, "score": score_pair,
                    "period_scores": period_scores, "period": period, "clock": clock,
                    "status": status_label, "slug": match_slug,
                    "id": match_slug or provider_id, "provider_id": provider_id,
                    "raw_identity_keys": list(dict.fromkeys(
                        str(k) for obj in identity_sources if isinstance(obj, dict) for k in obj.keys()
                    ))[:50],
                    "period_length_seconds": None, "detail_source": None,
                })

            # ESPN's public NBA scoreboard is an independent fallback for NBA fixtures.
            # Only the NBA scoreboard is used here: NCAA uses a different period format.
            espn_diagnostics = {"status": "not_checked", "events": 0, "live_events": 0}
            try:
                espn_date = datetime.now(timezone.utc).strftime("%Y%m%d")
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(2.8, connect=1.5),
                    follow_redirects=True,
                    headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
                ) as espn_client:
                    espn_response = await espn_client.get(
                        "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
                        params={"dates": espn_date},
                    )
                espn_diagnostics["http_status"] = espn_response.status_code
                if espn_response.status_code == 200:
                    espn_payload = espn_response.json()
                    espn_events = espn_payload.get("events", []) if isinstance(espn_payload, dict) else []
                    espn_diagnostics["events"] = len(espn_events) if isinstance(espn_events, list) else 0
                    for espn_event in espn_events if isinstance(espn_events, list) else []:
                        if not isinstance(espn_event, dict):
                            continue
                        competitions = espn_event.get("competitions") or []
                        competition = competitions[0] if competitions and isinstance(competitions[0], dict) else {}
                        status = competition.get("status") or espn_event.get("status") or {}
                        status_type = status.get("type") if isinstance(status, dict) else {}
                        state = str((status_type or {}).get("state") or "").lower()
                        if state not in ("in", "live", "in_progress"):
                            continue
                        competitors = competition.get("competitors") or []
                        home_team = next((x for x in competitors if x.get("homeAway") == "home"), None)
                        away_team = next((x for x in competitors if x.get("homeAway") == "away"), None)
                        if not home_team or not away_team:
                            continue
                        try:
                            home_score, away_score = int(float(home_team.get("score"))), int(float(away_team.get("score")))
                        except (TypeError, ValueError, OverflowError):
                            continue
                        home_obj, away_obj = home_team.get("team") or {}, away_team.get("team") or {}
                        home_name = str(home_obj.get("displayName") or home_obj.get("name") or "")
                        away_name = str(away_obj.get("displayName") or away_obj.get("name") or "")
                        if not home_name or not away_name:
                            continue
                        try:
                            period = int(status.get("period"))
                        except (TypeError, ValueError):
                            period = None
                        display_clock = str(status.get("displayClock") or "").strip()
                        clock_match = re.fullmatch(r"(\d{1,2}):([0-5]\d)", display_clock)
                        clock = (clock_match.group(1).zfill(2) + ":" + clock_match.group(2)) if clock_match else None
                        # The scoreboard may omit displayClock during timeouts,
                        # quarter breaks or halftime. Keep verified score and period.
                        if period not in (1, 2, 3, 4):
                            continue
                        period_scores = {}
                        for competitor, side in ((home_team, 0), (away_team, 1)):
                            for index, line_score in enumerate(competitor.get("linescores") or [], start=1):
                                try:
                                    points = int(float(line_score.get("value") if isinstance(line_score, dict) else line_score))
                                except (TypeError, ValueError, OverflowError):
                                    continue
                                period_scores.setdefault(str(index), [None, None])[side] = points
                        period_scores = {key: value for key, value in period_scores.items()
                                         if value[0] is not None and value[1] is not None}
                        external_rows.append({
                            "home": home_name, "away": away_name,
                            "score": [home_score, away_score], "period_scores": period_scores,
                            "period": period, "clock": clock, "status": "NBA live",
                            "id": "espn:" + str(espn_event.get("id")),
                            "provider_id": str(espn_event.get("id")),
                            "period_length_seconds": 720, "period_count": 4, "detail_source": "ESPN NBA scoreboard",
                        })
                        espn_diagnostics["live_events"] += 1
                    espn_diagnostics["status"] = "ok"
                else:
                    espn_diagnostics["status"] = "http_error"
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                espn_diagnostics["status"] = type(exc).__name__
            # ESPN's public men's college basketball scoreboard supplies NCAA live
            # scores and game clocks. NCAA is two 20-minute halves, not four quarters.
            espn_ncaa_diagnostics = {"status": "not_checked", "events": 0, "live_events": 0}
            try:
                ncaa_date = datetime.now(timezone.utc).strftime("%Y%m%d")
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(3.2, connect=1.8),
                    follow_redirects=True,
                    headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
                ) as ncaa_client:
                    ncaa_response = await ncaa_client.get(
                        "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard",
                        params={"dates": ncaa_date, "limit": 500},
                    )
                espn_ncaa_diagnostics["http_status"] = ncaa_response.status_code
                if ncaa_response.status_code == 200:
                    ncaa_payload = ncaa_response.json()
                    ncaa_events = ncaa_payload.get("events", []) if isinstance(ncaa_payload, dict) else []
                    espn_ncaa_diagnostics["events"] = len(ncaa_events) if isinstance(ncaa_events, list) else 0
                    for ncaa_event in ncaa_events if isinstance(ncaa_events, list) else []:
                        if not isinstance(ncaa_event, dict): continue
                        competitions = ncaa_event.get("competitions") or []
                        competition = competitions[0] if competitions and isinstance(competitions[0], dict) else {}
                        status = competition.get("status") or ncaa_event.get("status") or {}
                        status_type = status.get("type") if isinstance(status, dict) else {}
                        state = str((status_type or {}).get("state") or "").lower()
                        if state not in ("in", "live", "in_progress"): continue
                        competitors = competition.get("competitors") or []
                        home_team = next((x for x in competitors if x.get("homeAway") == "home"), None)
                        away_team = next((x for x in competitors if x.get("homeAway") == "away"), None)
                        if not home_team or not away_team: continue
                        try:
                            home_score, away_score = int(float(home_team.get("score"))), int(float(away_team.get("score")))
                        except (TypeError, ValueError, OverflowError): continue
                        home_obj, away_obj = home_team.get("team") or {}, away_team.get("team") or {}
                        home_name = str(home_obj.get("displayName") or home_obj.get("name") or "")
                        away_name = str(away_obj.get("displayName") or away_obj.get("name") or "")
                        if not home_name or not away_name: continue
                        try: period = int(status.get("period"))
                        except (TypeError, ValueError): period = None
                        display_clock = str(status.get("displayClock") or "").strip()
                        clock_match = re.fullmatch(r"(\d{1,2}):([0-5]\d)", display_clock)
                        clock = (clock_match.group(1).zfill(2) + ":" + clock_match.group(2)) if clock_match else None
                        period_scores = {}
                        for competitor, side in ((home_team, 0), (away_team, 1)):
                            for index, line_score in enumerate(competitor.get("linescores") or [], start=1):
                                try:
                                    points = int(float(line_score.get("value") if isinstance(line_score, dict) else line_score))
                                except (TypeError, ValueError, OverflowError): continue
                                period_scores.setdefault(str(index), [None, None])[side] = points
                        period_scores = {key: value for key, value in period_scores.items()
                                         if value[0] is not None and value[1] is not None}
                        external_rows.append({
                            "home": home_name, "away": away_name,
                            "score": [home_score, away_score], "period_scores": period_scores,
                            "period": period, "clock": clock, "status": "NCAA men's live",
                            "id": "espn-ncaa:" + str(ncaa_event.get("id")),
                            "provider_id": str(ncaa_event.get("id")),
                            "period_length_seconds": 1200, "period_count": 2,
                            "detail_source": "ESPN men's college basketball scoreboard",
                        })
                        espn_ncaa_diagnostics["live_events"] += 1
                    espn_ncaa_diagnostics["status"] = "ok"
                else:
                    espn_ncaa_diagnostics["status"] = "http_error"
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                espn_ncaa_diagnostics["status"] = type(exc).__name__

            # ESPN's summary endpoint includes live team/player box-score values
            # for NBA and US men's NCAA. It is an optional enrichment only; PARI
            # remains the authoritative event/market feed.
            espn_stats_cache = globals().setdefault("_ESPN_LIVE_STATS_CACHE", {})
            async def enrich_espn_boxscore(row):
                event_ref = str(row.get("id") or "")
                if not event_ref.startswith(("espn:", "espn-ncaa:")):
                    return
                event_id = event_ref.split(":", 1)[1]
                league_slug = "nba" if event_ref.startswith("espn:") else "mens-college-basketball"
                cache_key = league_slug + ":" + event_id
                cached = espn_stats_cache.get(cache_key, {})
                if cached.get("expires", 0) > datetime.now(timezone.utc).timestamp():
                    row["external_statistics"] = cached.get("statistics", [])
                    return
                try:
                    async with espn_stats_semaphore:
                        response = await espn_client.get(
                            "https://site.api.espn.com/apis/site/v2/sports/basketball/" + league_slug + "/summary",
                            params={"event": event_id},
                        )
                    if response.status_code != 200:
                        espn_diagnostics.setdefault("boxscore_errors", []).append(
                            league_slug + ":" + str(response.status_code)
                        )
                        return
                    summary = response.json()
                    teams = ((summary.get("boxscore") or {}).get("teams") or [])
                    by_metric = {}
                    metric_aliases = {
                        "fieldgoalsmade-fieldgoalsattempted": "field_goals",
                        "threepointfieldgoalsmade-threepointfieldgoalsattempted": "three_pointers",
                        "freethrowsmade-freethrowsattempted": "free_throws",
                        "rebounds": "rebounds", "offensiverebounds": "offensive_rebounds",
                        "defensiverebounds": "defensive_rebounds", "assists": "assists",
                        "turnovers": "turnovers", "steals": "steals", "blocks": "blocks",
                        "fouls": "fouls", "personalFouls": "fouls",
                    }
                    for team_row in teams if isinstance(teams, list) else []:
                        if not isinstance(team_row, dict):
                            continue
                        team_obj = team_row.get("team") or {}
                        team_name = str(team_obj.get("displayName") or team_obj.get("name") or "")
                        side = "home" if team_score(team_name, row.get("home")) >= team_score(team_name, row.get("away")) else "away"
                        for stat in team_row.get("statistics", []) if isinstance(team_row.get("statistics"), list) else []:
                            if not isinstance(stat, dict):
                                continue
                            name = str(stat.get("name") or stat.get("abbreviation") or "")
                            key = metric_aliases.get(name.replace("_", "").replace(" ", "").casefold())
                            if key is None:
                                key = _pari_stat_metric(name)
                            if not key:
                                continue
                            value = stat.get("displayValue", stat.get("value"))
                            if value is None:
                                continue
                            by_metric.setdefault(key, {})[side] = value
                    normalized = []
                    for key, values in by_metric.items():
                        if "home" in values and "away" in values:
                            normalized.append({
                                "group": "ESPN team box score", "name": key,
                                "metric_key": key, "home": values["home"],
                                "away": values["away"], "source": "ESPN",
                            })
                    if normalized:
                        row["external_statistics"] = normalized
                        espn_stats_cache[cache_key] = {
                            "expires": datetime.now(timezone.utc).timestamp() + 30,
                            "statistics": normalized,
                        }
                except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
                    espn_diagnostics.setdefault("boxscore_errors", []).append(
                        league_slug + ":" + type(exc).__name__
                    )

            espn_stats_semaphore = asyncio.Semaphore(3)
            if any(str(row.get("id") or "").startswith(("espn:", "espn-ncaa:")) for row in external_rows):
                try:
                    async with httpx.AsyncClient(
                        timeout=httpx.Timeout(3.5, connect=2.0),
                        follow_redirects=True,
                        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
                    ) as espn_client:
                        await asyncio.gather(
                            *(enrich_espn_boxscore(row) for row in external_rows),
                            return_exceptions=True,
                        )
                except Exception as exc:
                    espn_diagnostics["boxscore_error"] = type(exc).__name__

            # Merge scored SofaScore fixtures into the candidate pool as well.
            # This prevents the SportScore fallback branch from discarding matches
            # already found by SofaScore when only some PARI events are missing data.
            if isinstance(payload, dict) and isinstance(payload.get("events"), list):
                for ss_item in payload["events"]:
                    if not isinstance(ss_item, dict):
                        continue
                    ss_home = ss_item.get("homeTeam") or {}
                    ss_away = ss_item.get("awayTeam") or {}
                    if not isinstance(ss_home, dict) or not isinstance(ss_away, dict):
                        continue
                    ss_hs, ss_as = ss_item.get("homeScore") or {}, ss_item.get("awayScore") or {}
                    ss_hp = ss_hs.get("current") if isinstance(ss_hs, dict) else None
                    ss_ap = ss_as.get("current") if isinstance(ss_as, dict) else None
                    try:
                        if ss_hp is None or ss_ap is None:
                            continue
                        ss_score = [int(ss_hp), int(ss_ap)]
                    except (TypeError, ValueError, OverflowError):
                        continue
                    ss_home_name = str(ss_home.get("name") or "")
                    ss_away_name = str(ss_away.get("name") or "")
                    if not ss_home_name or not ss_away_name:
                        continue
                    ss_status = ss_item.get("status") or {}
                    ss_time = ss_item.get("time") or {}
                    if not isinstance(ss_status, dict): ss_status = {}
                    if not isinstance(ss_time, dict): ss_time = {}
                    ss_desc = str(ss_status.get("description") or ss_status.get("code") or "")
                    ss_period_value = ss_item.get("lastPeriod") or ss_status.get("period") or ss_desc
                    ss_pm = re.search(r"(?:q|quarter|period|период|четверть)\s*([1-4])|period([1-4])", str(ss_period_value), re.I)
                    ss_period = int(ss_pm.group(1) or ss_pm.group(2)) if ss_pm else None
                    ss_clock = None
                    for ss_key in ("currentPeriodTime", "clock", "timer", "timeRemaining", "periodTime"):
                        ss_value = ss_time.get(ss_key, ss_item.get(ss_key))
                        if ss_value is not None:
                            ss_match = re.search(r"(\d{1,2}):([0-5]\d)", str(ss_value))
                            if ss_match:
                                ss_clock = ss_match.group(1).zfill(2) + ":" + ss_match.group(2)
                                break
                    ss_length = ss_time.get("max") or ss_time.get("periodLength") or ss_item.get("periodLength")
                    try:
                        ss_length = int(float(ss_length)) if ss_length is not None else None
                        if ss_length and ss_length > 100000: ss_length //= 1000
                        if ss_length is not None and not 300 <= ss_length <= 900: ss_length = None
                    except (TypeError, ValueError, OverflowError):
                        ss_length = None
                    if ss_clock is None and ss_length:
                        try:
                            ss_started = float(ss_time.get("currentPeriodStartTimestamp") or 0)
                            if ss_started > 0:
                                ss_remaining = max(0, int(ss_length - (datetime.now(timezone.utc).timestamp() - ss_started)))
                                ss_clock = f"{ss_remaining // 60:02d}:{ss_remaining % 60:02d}"
                        except (TypeError, ValueError, OverflowError):
                            pass
                    ss_period_scores = {}
                    for n in range(1, 5):
                        qh = ss_hs.get("period" + str(n)) if isinstance(ss_hs, dict) else None
                        qa = ss_as.get("period" + str(n)) if isinstance(ss_as, dict) else None
                        if qh is not None and qa is not None:
                            try: ss_period_scores[str(n)] = [int(qh), int(qa)]
                            except (TypeError, ValueError, OverflowError): pass
                    ss_candidate = {
                        "home": ss_home_name, "away": ss_away_name, "score": ss_score,
                        "period_scores": ss_period_scores, "period": ss_period,
                        "clock": ss_clock, "period_length_seconds": ss_length,
                        "status": ss_desc, "id": "sofascore:" + str(ss_item.get("id")),
                        "detail_source": "SofaScore",
                    }
                    duplicate = next((r for r in external_rows
                                      if pair_score(ss_home_name, ss_away_name, r["home"], r["away"]) >= 0.94), None)
                    if duplicate:
                        for field in ("score", "period", "clock", "period_length_seconds"):
                            if duplicate.get(field) is None and ss_candidate.get(field) is not None:
                                duplicate[field] = ss_candidate[field]
                        if not duplicate.get("period_scores") and ss_candidate.get("period_scores"):
                            duplicate["period_scores"] = ss_candidate["period_scores"]
                        if duplicate.get("detail_source") is None:
                            duplicate["detail_source"] = "SportScore + SofaScore"
                    else:
                        external_rows.append(ss_candidate)

            # The list endpoint is score/status oriented. For live rows without a
            # countdown, ask SportScore's documented single-match detail endpoint.
            # This is a bounded, read-only enrichment; only explicit clock and
            # period-duration fields are accepted (never infer 10/12-minute periods).
            detail_cache = globals().setdefault("_SPORTSCORE_DETAIL_CACHE", {})
            now_detail = datetime.now(timezone.utc).timestamp()
            detail_targets = [row for row in external_rows
                              if (row.get("slug") or row.get("provider_id"))
                              and not str(row.get("id") or "").startswith(("sofascore:", "espn:", "espn-ncaa:"))
                              and (not detail_cache.get(str(row.get("id") or row.get("provider_id")), {}).get("expires", 0)
                                   or detail_cache.get(str(row.get("id") or row.get("provider_id")), {}).get("expires", 0) <= now_detail)]
            detail_diagnostics = []
            if detail_targets:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(5.0, connect=3.0),
                    follow_redirects=True,
                    headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
                ) as detail_client:
                    semaphore = asyncio.Semaphore(8)

                    async def enrich_sportscore_detail(row):
                        async with semaphore:
                            try:
                                detail_identifier = str(row.get("slug") or row.get("provider_id") or row.get("id"))
                                payload = None
                                attempts = []
                                # Official detail route takes a slug, not an arbitrary numeric id.
                                if row.get("slug"):
                                    detail_response = await detail_client.get(
                                        "https://sportscore.com/api/widget/match/",
                                        params={"sport": "basketball", "slug": str(row["slug"]),
                                                "src": "pari-basketball-ai"}
                                    )
                                    attempts.append({"route": "match_by_slug", "status": detail_response.status_code})
                                    if detail_response.status_code == 200:
                                        payload = detail_response.json()
                                # The official tracker route takes a provider match ID.
                                # It can expose live clock/timeline even when detail has no timer.
                                if payload is None and row.get("provider_id"):
                                    tracker_response = await detail_client.get(
                                        "https://sportscore.com/api/widget/tracker/",
                                        params={"sport": "basketball", "id": str(row["provider_id"]),
                                                "src": "pari-basketball-ai"}
                                    )
                                    attempts.append({"route": "tracker_by_id", "status": tracker_response.status_code})
                                    if tracker_response.status_code == 200:
                                        payload = tracker_response.json()
                                if payload is None:
                                    detail_diagnostics.append({"id": str(row.get("id")),
                                                               "slug": row.get("slug"),
                                                               "provider_id": row.get("provider_id"),
                                                               "attempts": attempts})
                                    detail_cache[str(row.get("id") or row.get("provider_id"))] = {
                                        "expires": datetime.now(timezone.utc).timestamp() + 20,
                                        "statistics_count": 0, "status": "unavailable"
                                    }
                                    return
                                # Walk nested detail/tracker envelopes without assuming a
                                # single response wrapper/schema.
                                objects, seen = [], set()
                                def walk(value, depth=0):
                                    if depth > 7 or not isinstance(value, (dict, list)) or id(value) in seen:
                                        return
                                    seen.add(id(value))
                                    if isinstance(value, dict):
                                        objects.append(value)
                                        for child in value.values():
                                            walk(child, depth + 1)
                                    else:
                                        for child in value:
                                            walk(child, depth + 1)
                                walk(payload)
                                detail_keys = list(dict.fromkeys(
                                    str(key) for obj in objects for key in obj.keys()
                                ))[:80]

                                clock_keys = re.compile(
                                    r"(clock|timer|countdown|remaining.?time|remaining.?seconds|"
                                    r"time.?remaining|time.?left|clock.?seconds|period.?clock|"
                                    r"current.?period.?time|period.?time|game.?time|game.?clock)", re.I
                                )
                                duration_keys = re.compile(
                                    r"(period.?length|quarter.?length|period.?duration|quarter.?duration|"
                                    r"period.?seconds|quarter.?seconds)", re.I
                                )
                                def clock_seconds(value):
                                    if isinstance(value, dict):
                                        minute = value.get("minutes", value.get("minute", value.get("min")))
                                        second = value.get("seconds", value.get("second", value.get("sec")))
                                        if minute is not None and second is not None:
                                            try:
                                                total = int(float(minute))*60 + int(float(second))
                                                return total if 0 <= total <= 900 else None
                                            except (TypeError, ValueError, OverflowError):
                                                return None
                                        for nested_key in ("remaining", "remaining_seconds", "seconds_remaining", "value", "time", "clock"):
                                            if value.get(nested_key) is not None:
                                                found = clock_seconds(value[nested_key])
                                                if found is not None: return found
                                        return None
                                    candidate = str(value).strip()
                                    if re.match(r"^\d{4}-\d{2}-\d{2}[T ]", candidate): return None
                                    match = re.fullmatch(r"(\d{1,2}):([0-5]\d)", candidate)
                                    if match:
                                        total = int(match.group(1))*60 + int(match.group(2))
                                        return total if 0 <= total <= 900 else None
                                    if isinstance(value, (int, float)) or candidate.isdigit():
                                        try:
                                            total = int(float(value))
                                            return total if 0 <= total <= 900 else None
                                        except (TypeError, ValueError, OverflowError): return None
                                    return None

                                def duration_seconds(value):
                                    if isinstance(value, dict):
                                        for nested_key in ("seconds", "duration", "value", "max"):
                                            if value.get(nested_key) is not None:
                                                found = duration_seconds(value[nested_key])
                                                if found is not None: return found
                                        return None
                                    raw = str(value).strip()
                                    if re.fullmatch(r"\d{1,2}:[0-5]\d", raw):
                                        mm, ss = raw.split(":")
                                        return int(mm)*60 + int(ss)
                                    try:
                                        seconds = int(float(value))
                                        if seconds > 100000: seconds //= 1000
                                        return seconds if 300 <= seconds <= 900 else None
                                    except (TypeError, ValueError, OverflowError): return None

                                for obj in objects:
                                    for key, value in obj.items():
                                        if value is None: continue
                                        if not row.get("clock") and clock_keys.search(str(key)):
                                            seconds = clock_seconds(value)
                                            if seconds is not None:
                                                row["clock"] = f"{seconds // 60:02d}:{seconds % 60:02d}"
                                        if row.get("period_length_seconds") is None and duration_keys.search(str(key)):
                                            seconds = duration_seconds(value)
                                            if seconds is not None: row["period_length_seconds"] = seconds
                                        if isinstance(value, dict) and re.search(r"(home.?score|away.?score|period.?scores|scores)", str(key), re.I):
                                            for n in range(1, 5):
                                                hp = value.get("home" + str(n)) or value.get("home_period" + str(n)) or value.get("period" + str(n) + "_home")
                                                ap = value.get("away" + str(n)) or value.get("away_period" + str(n)) or value.get("period" + str(n) + "_away")
                                                hpn, apn = score_number(hp), score_number(ap)
                                                if hpn is not None and apn is not None:
                                                    row.setdefault("period_scores", {})[str(n)] = [hpn, apn]

                                # Preserve box-score data returned by the match-detail
                                # endpoint. Providers use several schemas (groups/statistics,
                                # stats arrays, or flat home/away key pairs), so normalize
                                # only explicit numeric values and retain original labels.
                                detail_statistics = []
                                metric_aliases = (
                                    ("three_pointers", r"3.?point|three.?point|3pt|тр[её]х.?очк"),
                                    ("two_pointers", r"2.?point|two.?point|2pt|двух.?очк"),
                                    ("free_throws", r"free.?throw|штрафн"),
                                    ("field_goals", r"field.?goal|броск.*с игры"),
                                    ("rebounds", r"rebound|подбор"),
                                    ("assists", r"assist|передач"),
                                    ("turnovers", r"turnover|потер"),
                                    ("steals", r"steal|перехват"),
                                    ("blocks", r"block|блокшот"),
                                    ("fouls", r"foul|фол"),
                                    ("possessions", r"possession|владен"),
                                )
                                def add_detail_stat(label, home_value, away_value, group="Матч"):
                                    if home_value is None or away_value is None:
                                        return
                                    searchable = (str(group) + " " + str(label)).casefold()
                                    metric = next((key for key, pattern in metric_aliases
                                                   if re.search(pattern, searchable, re.I)), None)
                                    detail_statistics.append({
                                        "group": str(group), "name": str(label),
                                        "metric_key": metric, "home": home_value,
                                        "away": away_value, "source": "SportScore",
                                    })
                                def walk_detail_stats(node, group="Матч", depth=0):
                                    if depth > 7:
                                        return
                                    if isinstance(node, list):
                                        for entry in node:
                                            walk_detail_stats(entry, group, depth + 1)
                                        return
                                    if not isinstance(node, dict):
                                        return
                                    label = node.get("name") or node.get("label") or node.get("key") or node.get("type")
                                    home_value = next((node.get(k) for k in ("home", "homeValue", "home_value", "team1", "homeTeam") if node.get(k) is not None), None)
                                    away_value = next((node.get(k) for k in ("away", "awayValue", "away_value", "team2", "awayTeam") if node.get(k) is not None), None)
                                    if isinstance(home_value, dict):
                                        home_value = home_value.get("displayValue", home_value.get("value", home_value.get("total")))
                                    if isinstance(away_value, dict):
                                        away_value = away_value.get("displayValue", away_value.get("value", away_value.get("total")))
                                    if label and home_value is not None and away_value is not None:
                                        add_detail_stat(label, home_value, away_value, node.get("groupName") or group)
                                    home_map, away_map = node.get("home"), node.get("away")
                                    if isinstance(home_map, dict) and isinstance(away_map, dict):
                                        for metric_name in set(home_map).intersection(away_map):
                                            add_detail_stat(metric_name, home_map.get(metric_name), away_map.get(metric_name), node.get("groupName") or group)
                                    for key, child in node.items():
                                        if isinstance(child, (dict, list)):
                                            walk_detail_stats(child, node.get("groupName") or node.get("name") or group, depth + 1)

                                for obj in objects:
                                    for group_key in ("statistics", "stats", "boxScore", "box_score", "teamStatistics", "team_statistics", "matchStatistics"):
                                        group_value = obj.get(group_key)
                                        if isinstance(group_value, (dict, list)):
                                            walk_detail_stats(group_value, obj.get("groupName") or obj.get("name") or "Матч")
                                if detail_statistics:
                                    unique_stats = {}
                                    for stat in detail_statistics:
                                        key = (stat["group"], stat["name"], str(stat["home"]), str(stat["away"]))
                                        unique_stats[key] = stat
                                    row["external_statistics"] = list(unique_stats.values())
                                detail_cache[str(row.get("id") or row.get("provider_id"))] = {
                                    "expires": datetime.now(timezone.utc).timestamp() + 45,
                                    "statistics_count": len(detail_statistics),
                                    "status": "ok" if detail_statistics else "no_statistics"
                                }
                                if row.get("clock") or row.get("period_length_seconds"):
                                    row["detail_source"] = "SportScore match detail"
                                detail_diagnostics.append({
                                    "id": str(row.get("id")), "http_status": response.status_code,
                                    "clock_found": bool(row.get("clock")),
                                    "period_length_found": bool(row.get("period_length_seconds")),
                                    "keys": detail_keys[:30],
                                })
                            except (httpx.HTTPError, ValueError, TypeError) as exc:
                                detail_diagnostics.append({"id": str(row.get("id")), "error": type(exc).__name__})
                                return

                    await asyncio.gather(*(enrich_sportscore_detail(row) for row in detail_targets[:40]))

            used = set()
            matched = 0
            ambiguous = 0
            diagnostics = []
            for pari in events:
                ranked = sorted(
                    ((pair_score(pari.get("team1"), pari.get("team2"), row["home"], row["away"]), row)
                     for row in external_rows),
                    key=lambda x: x[0], reverse=True,
                )
                best = ranked[0] if ranked else None
                diagnostic = {
                    "pari_match": str(pari.get("team1") or "") + " — " + str(pari.get("team2") or ""),
                    "best_external_match": (best[1]["home"] + " — " + best[1]["away"]) if best else None,
                    "best_confidence": round(best[0], 3) if best else None,
                    "external_score": best[1]["score"] if best else None,
                    "external_period": best[1]["period"] if best else None,
                    "external_clock": best[1]["clock"] if best else None,
                    "applied": False,
                    "rejection_reason": None,
                }
                diagnostics.append(diagnostic)
                if not best:
                    diagnostic["rejection_reason"] = "no_external_candidate"
                    continue
                # Team-pair confidence is the minimum of the two independent
                # team-name scores. Accept shortened/transliterated club names,
                # but keep the one-to-one and ambiguity checks below to prevent
                # assigning a score to the wrong fixture.
                if best[0] < 0.72:
                    diagnostic["rejection_reason"] = "both_team_names_not_confirmed"
                    continue
                if len(ranked) > 1 and best[0] - ranked[1][0] < 0.10:
                    diagnostic["rejection_reason"] = "ambiguous_external_candidates"
                    ambiguous += 1
                    continue
                row = best[1]
                row_key = str(row.get("id") or (row["home"] + "|" + row["away"]))
                if row_key in used:
                    diagnostic["rejection_reason"] = "external_match_already_assigned"
                    continue
                direct = team_score(pari.get("team1"), row["home"]) + team_score(pari.get("team2"), row["away"])
                reverse = team_score(pari.get("team1"), row["away"]) + team_score(pari.get("team2"), row["home"])
                ordered_score = (row["score"] if direct >= reverse else [row["score"][1], row["score"][0]]) if row.get("score") is not None else pari.get("score")
                if row.get("score") is None:
                    ordered_score = pari.get("score")
                # SportScore match-detail already contains box-score rows, but they
                # were previously discarded at the PARI matching stage. Merge them
                # here, retaining PARI values first and deduplicating exact values.
                row_statistics = row.get("external_statistics") or []
                if row_statistics:
                    merged_statistics = list(pari.get("external_statistics") or []) + list(row_statistics)
                    unique_statistics = []
                    seen_statistics = set()
                    for stat in merged_statistics:
                        marker = (stat.get("metric_key"), str(stat.get("home")), str(stat.get("away")), stat.get("source"))
                        if marker not in seen_statistics:
                            seen_statistics.add(marker)
                            unique_statistics.append(stat)
                    pari["external_statistics"] = unique_statistics
                    pari["statistics_source"] = " + ".join(dict.fromkeys(
                        str(stat.get("source") or "external") for stat in unique_statistics
                    ))
                    pari["statistics_checked_at"] = datetime.now(timezone.utc).isoformat()
                    available = {stat.get("metric_key") for stat in unique_statistics if stat.get("metric_key")}
                    expected = ["three_pointers", "two_pointers", "free_throws", "field_goals",
                                "rebounds", "offensive_rebounds", "defensive_rebounds", "assists",
                                "turnovers", "steals", "blocks", "fouls", "possessions"]
                    for prefix in ("three_pointers", "two_pointers", "free_throws", "field_goals"):
                        if any(str(metric).startswith(prefix + "_") for metric in available):
                            available.add(prefix)
                    pari["statistics_quality"] = {
                        "available_metrics": sorted(available),
                        "missing_metrics": [metric for metric in expected if metric not in available],
                        "available_count": len(set(expected).intersection(available)),
                        "expected_count": len(expected),
                        "coverage_percent": round(len(set(expected).intersection(available)) / len(expected) * 100),
                        "raw_fields_count": len(unique_statistics),
                        "complete": all(metric in available for metric in expected),
                    }
                provider_name = ("SofaScore" if str(row.get("id") or "").startswith("sofascore:") else
                                 "ESPN" if str(row.get("id") or "").startswith(("espn:", "espn-ncaa:")) else "SportScore")
                pari["external_live"] = {
                    "source": provider_name, "event_id": row.get("id"),
                    "match": row["home"] + " — " + row["away"],
                    "match_confidence": round(best[0], 3), "score": ordered_score,
                    "period": row["period"], "clock": row["clock"],
                    "period_scores": row.get("period_scores", {}),
                    "period_length_seconds": row.get("period_length_seconds"),
                    "period_count": row.get("period_count") or 4,
                    "clock_detail_source": row.get("detail_source"),
                    "status": row["status"],
                }
                if pari.get("score") is None and row.get("score") is not None:
                    pari["score"] = ordered_score
                    pari["score_source"] = provider_name
                if pari.get("period") is None and row.get("period") is not None:
                    pari["period"] = row["period"]
                    pari["period_source"] = provider_name
                if pari.get("clock") is None and row.get("clock"):
                    pari["clock"] = row["clock"]
                    pari["clock_source"] = provider_name
                used.add(row_key)
                diagnostic["applied"] = True
                diagnostic["accepted_external_match"] = row["home"] + " — " + row["away"]
                matched += 1
            sportscore_diagnostics = {
                "source": "SportScore (fallback)",
                "status": "ok" if external_rows else "no_live_events",
                "live_events": len(external_rows), "matched": matched,
                "live_events_excludes_women": True,
                "clock_policy": "Only explicit countdown fields or a valid MM:SS in status_text; timestamps are ignored; values above 15:00 are rejected.",
                "statistics_matches": 0, "statistics_errors": [],
                "unmatched_pari": max(0, len(events) - matched), "ambiguous": ambiguous,
                "match_diagnostics": diagnostics[:30], "errors": errors[:4],
                "sportscore_errors": sportscore_errors[:5],
                "sportscore_endpoint_diagnostics": sportscore_raw_counts,
                "sportscore_status_samples": sportscore_status_samples[:16],
                "sportscore_live_rows_after_filter": len(external_rows),
                "sportscore_detail_targets": len(detail_targets),
                "sportscore_rows_without_detail_id": sum(
                    1 for row in external_rows
                    if (not row.get("clock") or not row.get("period_length_seconds"))
                    and not (row.get("id") or row.get("provider_id"))
                ),
                "sportscore_detail_target_ids": [
                    {"id": row.get("id"), "slug": row.get("slug"), "provider_id": row.get("provider_id"),
                     "identity_keys": row.get("raw_identity_keys", [])}
                    for row in detail_targets[:12]
                ],
                "sportscore_detail_diagnostics": detail_diagnostics[:12],
                "espn_nba": espn_diagnostics,
                "espn_ncaa": espn_ncaa_diagnostics,
                "sportscore_live_match_samples": [
                    {"home": row["home"], "away": row["away"], "id": row.get("id"), "provider_id": row.get("provider_id"), "status": row["status"],
                     "period": row["period"], "clock": row.get("clock"),
                     "period_length_seconds": row.get("period_length_seconds"), "period_count": row.get("period_count"),
                     "detail_source": row.get("detail_source"), "score": row["score"]}
                    for row in external_rows[:12]
                ],
                "note": "SportScore дополняет PARI; результаты далее объединяются с SofaScore и его статистикой."
            }
        except Exception as exc:
            sportscore_errors.append(type(exc).__name__)
            sportscore_diagnostics = {
                "source": "SportScore (fallback)", "status": "unavailable",
                "matched": 0, "errors": errors[:4],
                "sportscore_errors": sportscore_errors[:5]
            }

    live_rows = []
    for item in (payload or {}).get("events", []):
        if not isinstance(item, dict):
            continue
        home = item.get("homeTeam") or {}
        away = item.get("awayTeam") or {}
        if not isinstance(home, dict) or not isinstance(away, dict):
            continue
        hs, aws = item.get("homeScore") or {}, item.get("awayScore") or {}
        home_score = hs.get("current") if isinstance(hs, dict) else None
        away_score = aws.get("current") if isinstance(aws, dict) else None
        if home_score is None or away_score is None:
            continue
        status = item.get("status") or {}
        time_data = item.get("time") or {}
        # Scheduled endpoint can contain non-live fixtures; keep only in-progress rows.
        status_type = status.get("type") if isinstance(status, dict) else {}
        status_code = str((status_type or {}).get("state") or (status_type or {}).get("name") or status.get("code") or "").lower()
        if status_code and not any(token in status_code for token in ("inprogress", "in_progress", "live", "period", "halftime", "1st", "2nd", "3rd", "4th")):
            continue
        if not isinstance(status, dict):
            status = {}
        if not isinstance(time_data, dict):
            time_data = {}
        description = str(status.get("description") or status.get("code") or "")
        period = None
        period_value = item.get("lastPeriod") or status.get("period") or status.get("description")
        if isinstance(period_value, dict):
            period_value = period_value.get("current") or period_value.get("name")
        pm = re.search(r"(?:q|quarter|period|период|четверть)\s*([1-4])|period([1-4])", str(period_value), re.I)
        if pm:
            period = int(pm.group(1) or pm.group(2))
        elif str(period_value).strip().isdigit() and 1 <= int(str(period_value).strip()) <= 4:
            period = int(str(period_value).strip())
        clock = None
        for key in ("currentPeriodTime", "clock", "timer", "timeRemaining", "periodTime"):
            value = time_data.get(key, item.get(key))
            if value is not None:
                match = re.search(r"(\d{1,2}):([0-5]\d)", str(value))
                if match:
                    clock = match.group(1).zfill(2) + ":" + match.group(2)
                    break
        # SofaScore commonly exposes the period start timestamp and period length
        # instead of a countdown string. Derive the countdown only when both are
        # present in the source; never assume a 10- or 12-minute quarter.
        if clock is None:
            try:
                started = float(time_data.get("currentPeriodStartTimestamp") or 0)
                period_length = float(time_data.get("max") or time_data.get("periodLength") or 0)
                if started > 0 and period_length > 0:
                    if period_length > 100000:
                        period_length /= 1000.0
                    remaining = max(0, int(period_length - (datetime.now(timezone.utc).timestamp() - started)))
                    clock = f"{remaining // 60:02d}:{remaining % 60:02d}"
            except (TypeError, ValueError, OverflowError):
                clock = None
        period_length_seconds = None
        raw_period_length = time_data.get("max") or time_data.get("periodLength") or item.get("periodLength")
        if raw_period_length is not None:
            try:
                if isinstance(raw_period_length, str) and ":" in raw_period_length:
                    mm, ss = raw_period_length.split(":", 1)
                    period_length_seconds = int(mm) * 60 + int(ss)
                else:
                    period_length_seconds = int(float(raw_period_length))
                    if period_length_seconds > 100000:
                        period_length_seconds //= 1000
            except (TypeError, ValueError, OverflowError):
                period_length_seconds = None
        if clock is None and period_length_seconds:
            try:
                started = float(time_data.get("currentPeriodStartTimestamp") or 0)
                if started > 0:
                    remaining = max(0, int(period_length_seconds - (datetime.now(timezone.utc).timestamp() - started)))
                    clock = f"{remaining // 60:02d}:{remaining % 60:02d}"
            except (TypeError, ValueError, OverflowError):
                clock = None
        period_scores = {};
        for n in range(1, 5):
            hv = hs.get("period" + str(n)) if isinstance(hs, dict) else None
            av = aws.get("period" + str(n)) if isinstance(aws, dict) else None
            if hv is not None and av is not None:
                period_scores[str(n)] = [hv, av]
        tournament = item.get("tournament") or {}
        live_rows.append({
            "home": str(home.get("name") or ""),
            "away": str(away.get("name") or ""),
            "score": [int(home_score), int(away_score)],
            "period_scores": period_scores,
            "period_length_seconds": period_length_seconds,
            "period": period,
            "clock": clock,
            "status": description,
            "league": str(tournament.get("name") or ""),
            "id": item.get("id"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })

    matched = 0
    ambiguous = 0
    matched_rows = []
    match_diagnostics = []
    for pari in events:
        ranked = sorted(
            ((pair_score(pari.get("team1"), pari.get("team2"), row["home"], row["away"]), row)
             for row in live_rows),
            key=lambda x: x[0], reverse=True
        )
        best = ranked[0] if ranked else None
        match_diagnostics.append({
            "pari_match": str(pari.get("team1") or "") + " — " + str(pari.get("team2") or ""),
            "best_external_match": (ranked[0][1]["home"] + " — " + ranked[0][1]["away"]) if ranked else None,
            "best_confidence": round(ranked[0][0], 3) if ranked else None,
            "external_score": ranked[0][1]["score"] if ranked else None,
            "external_period": ranked[0][1]["period"] if ranked else None,
            "external_clock": ranked[0][1]["clock"] if ranked else None,
        })
        if not ranked or ranked[0][0] < 0.86:
            continue
        if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 0.07:
            ambiguous += 1
            continue
        score, row = ranked[0]
        # Keep the score orientation aligned with PARI's team1/team2 order.
        direct = (SequenceMatcher(None, norm(pari.get("team1")), norm(row["home"])).ratio() +
                  SequenceMatcher(None, norm(pari.get("team2")), norm(row["away"])).ratio()) / 2
        reverse = (SequenceMatcher(None, norm(pari.get("team1")), norm(row["away"])).ratio() +
                   SequenceMatcher(None, norm(pari.get("team2")), norm(row["home"])).ratio()) / 2
        pari_order_score = row["score"] if direct >= reverse else [row["score"][1], row["score"][0]]
        # A single SofaScore event must not be assigned to multiple PARI matches.
        if row.get("_used"):
            continue
        row["_used"] = True
        pari["external_live"] = {
            "source": "SofaScore",
            "event_id": row["id"],
            "match": row["home"] + " — " + row["away"],
            "match_confidence": round(score, 3),
            "score": pari_order_score,
            "period_scores": row.get("period_scores", {}),
            "period_length_seconds": row.get("period_length_seconds"),
            "period": row["period"],
            "clock": row["clock"],
            "status": row["status"],
            "league": row["league"],
            "updated_at": row["updated_at"],
        }
        if pari.get("score") is None:
            pari["score"] = pari_order_score
            pari["score_source"] = "SofaScore"
        if pari.get("period") is None and row.get("period") is not None:
            pari["period"] = row["period"]
            pari["period_source"] = "SofaScore"
        if pari.get("clock") is None and row.get("clock"):
            pari["clock"] = row["clock"]
            pari["clock_source"] = "SofaScore"
        if row.get("external_statistics"):
            merged = list(pari.get("external_statistics") or []) + list(row["external_statistics"])
            deduped, seen = [], set()
            for stat in merged:
                key = (stat.get("metric_key"), str(stat.get("home")), str(stat.get("away")))
                if key not in seen:
                    seen.add(key)
                    deduped.append(stat)
            pari["external_statistics"] = deduped
            sources = list(dict.fromkeys(str(x.get("source") or "external") for x in deduped))
            pari["statistics_source"] = " + ".join(sources)
            pari["statistics_checked_at"] = datetime.now(timezone.utc).isoformat()
            available = sorted({x.get("metric_key") for x in deduped if x.get("metric_key")})
            expected = ["three_pointers", "two_pointers", "free_throws", "field_goals",
                        "rebounds", "offensive_rebounds", "defensive_rebounds", "assists",
                        "turnovers", "steals", "blocks", "fouls", "possessions"]
            # PARI may expose made/attempted subfields rather than aggregate totals.
            available_set = set(available)
            if any(x.startswith("three_pointers_") for x in available_set): available_set.add("three_pointers")
            if any(x.startswith("two_pointers_") for x in available_set): available_set.add("two_pointers")
            if any(x.startswith("free_throws_") for x in available_set): available_set.add("free_throws")
            missing = [key for key in expected if key not in available_set]
            pari["statistics_quality"] = {
                "available_metrics": sorted(available_set), "missing_metrics": missing,
                "available_count": len(available_set.intersection(expected)), "expected_count": len(expected),
                "coverage_percent": round(len(available_set.intersection(expected)) / len(expected) * 100),
                "raw_fields_count": len(deduped),
                "complete": not missing,
            }
        matched_rows.append((pari, row))
        matched += 1

    # Fetch detailed box-score statistics for every confidently matched LIVE fixture.
    # Bounded concurrency and a short per-event cache keep the 30-second poll economical.
    stats_matched = 0
    stats_errors = []
    stats_checked = 0
    if matched_rows:
        stats_cache = globals().setdefault("_SOFASCORE_STATS_CACHE", {})
        stats_semaphore = asyncio.Semaphore(8)

        async def fetch_event_statistics(pari, row):
            event_id = row.get("id")
            if not event_id:
                return False, "missing_event_id"
            cache_key = str(event_id)
            cached_stats = stats_cache.get(cache_key, {})
            stats_payload = cached_stats.get("payload") if cached_stats.get("expires", 0) > datetime.now(timezone.utc).timestamp() else None
            if not stats_payload:
                async with stats_semaphore:
                    last_error = "stats_unavailable"
                    for base in ("https://www.sofascore.com/api/v1", "https://api.sofascore.com/api/v1"):
                        try:
                            response = await stats_client.get(base + "/event/" + cache_key + "/statistics")
                            if response.status_code == 200:
                                candidate = response.json()
                                if isinstance(candidate, dict):
                                    stats_payload = candidate
                                    stats_cache[cache_key] = {
                                        "expires": datetime.now(timezone.utc).timestamp() + 25,
                                        "payload": candidate,
                                    }
                                    break
                            elif response.status_code not in (404, 410):
                                last_error = "stats_HTTP_" + str(response.status_code)
                        except (httpx.HTTPError, ValueError) as exc:
                            last_error = "stats_" + type(exc).__name__
                    if not stats_payload:
                        return False, last_error
            source_stats = stats_payload.get("statistics") or stats_payload
            groups = source_stats.get("groups", []) if isinstance(source_stats, dict) else []
            normalized = []
            metric_patterns = (
                ("three_pointers", r"3.?point|three.?point|3.?очк|тр[её]х.?очк|3pt|three.?pointers"),
                ("two_pointers", r"2.?point|two.?point|2.?очк|двух.?очк|2pt|two.?pointers"),
                ("free_throws", r"free.?throw|штрафн|свободн.*брос|free.?throws"),
                ("field_goals", r"field.?goal|броск.*с игры"),
                ("offensive_rebounds", r"offensive.?rebound|подбор.*атак"),
                ("defensive_rebounds", r"defensive.?rebound|подбор.*защит"),
                ("rebounds", r"rebound|подбор"),
                ("assists", r"assist|передач"),
                ("turnovers", r"turnover|потер"),
                ("steals", r"steal|перехват"),
                ("blocks", r"block|блокшот"),
                ("fouls", r"foul|фол"),
                ("possessions", r"possession|владен"),
            )
            present_metrics = set()
            for group in groups if isinstance(groups, list) else []:
                if not isinstance(group, dict):
                    continue
                group_name = str(group.get("groupName") or group.get("name") or "Статистика")
                for stat in group.get("statistics", []) if isinstance(group.get("statistics"), list) else []:
                    if not isinstance(stat, dict):
                        continue
                    name = str(stat.get("name") or stat.get("key") or "")
                    home_value, away_value = stat.get("home"), stat.get("away")
                    if home_value is None and away_value is None:
                        continue
                    searchable = (group_name + " " + name).casefold()
                    metric_key = next((key for key, pattern in metric_patterns
                                       if re.search(pattern, searchable, re.I)), None)
                    if metric_key:
                        present_metrics.add(metric_key)
                    normalized.append({
                        "group": group_name, "name": name, "metric_key": metric_key,
                        "home": home_value, "away": away_value,
                        "home_value": stat.get("homeValue"),
                        "away_value": stat.get("awayValue"),
                        "compare_code": stat.get("compareCode"),
                        "source": "SofaScore",
                    })
            if normalized:
                expected_metrics = [key for key, _ in metric_patterns]
                missing_metrics = [key for key in expected_metrics if key not in present_metrics]
                # Merge provider statistics with explicit PARI values. Keep PARI's
                # named fields first; deduplicate by metric and values.
                existing_stats = list(pari.get("external_statistics") or [])
                merged_stats = existing_stats + normalized
                deduped_stats = []
                seen_stats = set()
                for stat_row in merged_stats:
                    stat_key = (stat_row.get("metric_key"), str(stat_row.get("home")), str(stat_row.get("away")))
                    if stat_key not in seen_stats:
                        seen_stats.add(stat_key)
                        deduped_stats.append(stat_row)
                pari["external_statistics"] = deduped_stats
                sources = list(dict.fromkeys(str(x.get("source") or "unknown") for x in deduped_stats))
                pari["statistics_source"] = " + ".join(sources)
                pari["statistics_checked_at"] = datetime.now(timezone.utc).isoformat()
                combined_metrics = set(present_metrics)
                combined_metrics.update(x.get("metric_key") for x in existing_stats if x.get("metric_key"))
                combined_missing = [key for key in expected_metrics if key not in combined_metrics]
                pari["statistics_quality"] = {
                    "available_metrics": sorted(combined_metrics),
                    "missing_metrics": combined_missing,
                    "available_count": len(combined_metrics),
                    "expected_count": len(expected_metrics),
                    "coverage_percent": round(len(combined_metrics) / len(expected_metrics) * 100),
                    "raw_fields_count": len(deduped_stats),
                    "complete": not combined_missing,
                }
                return True, len(normalized)
            return False, "statistics_empty"

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.5, connect=2.0),
                                          follow_redirects=True, headers=headers) as stats_client:
                results = await asyncio.gather(
                    *(fetch_event_statistics(pari, row) for pari, row in matched_rows),
                    return_exceptions=True
                )
            for result in results:
                stats_checked += 1
                if isinstance(result, Exception):
                    stats_errors.append("stats_" + type(result).__name__)
                elif result[0]:
                    stats_matched += 1
                else:
                    stats_errors.append(str(result[1]))
        except Exception as exc:
            stats_errors.append("stats_" + type(exc).__name__)

    return {
        "source": "SofaScore + SportScore",
        "status": "ok" if (matched or (sportscore_diagnostics or {}).get("matched")) else ("no_live_events" if not live_rows else "no_confident_match"),
        "live_events": len(live_rows),
        "matched": sum(1 for event in events if event.get("external_live")),
        "sofascore_matched": matched,
        "sportscore_matched": (sportscore_diagnostics or {}).get("matched", 0),
        "statistics_matches": stats_matched,
        "statistics_checked": stats_checked,
        "statistics_errors": stats_errors[:12],
        "statistics_fields_total": sum(len(event.get("external_statistics") or []) for event in events),
        "unmatched_pari": sum(1 for event in events if not event.get("external_live")),
        "ambiguous": ambiguous,
        "match_diagnostics": match_diagnostics[:30],
        "sportscore_diagnostics": sportscore_diagnostics,
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "note": "Источники объединяются по однозначному совпадению обеих команд; статистика берётся из box-score SofaScore, SportScore служит резервом для LIVE-счёта и таймера."
    }



def _pari_stat_metric(label):
    text = str(label or "").casefold().replace("ё", "е")
    patterns = (
        ("three_pointers_made", r"3.?point.*(made|scored)|three.?point.*(made|scored)|трех.?очк.*(попад|забит)|3pm"),
        ("three_pointers_attempted", r"3.?point.*(attempt|att)|three.?point.*(attempt|att)|трех.?очк.*(брос|попыт)|3pa"),
        ("two_pointers_made", r"2.?point.*(made|scored)|two.?point.*(made|scored)|двух.?очк.*(попад|забит)|2pm"),
        ("two_pointers_attempted", r"2.?point.*(attempt|att)|two.?point.*(attempt|att)|двух.?очк.*(брос|попыт)|2pa"),
        ("free_throws_made", r"free.?throw.*(made|scored)|штрафн.*(попад|реализ)|ftm"),
        ("free_throws_attempted", r"free.?throw.*(attempt|att)|штрафн.*(брос|попыт)|fta"),
        ("field_goals_made", r"(?:field.?goal|fg).*(?:made|scored)|броск.*с игры.*(?:попад|забит)|fgm"),
        ("field_goals_attempted", r"(?:field.?goal|fg).*(?:attempt|att)|броск.*с игры.*(?:брос|попыт)|fga"),
        ("field_goals", r"field.?goal|броск.*с игры"),
        ("offensive_rebounds", r"offensive.?rebound|подбор.*атак"),
        ("defensive_rebounds", r"defensive.?rebound|подбор.*защит"),
        ("rebounds", r"rebound|подбор"),
        ("assists", r"assist|передач"),
        ("turnovers", r"turnover|потер"),
        ("steals", r"steal|перехват"),
        ("blocks", r"block|блокшот"),
        ("personal_fouls", r"personal.?foul|фол.*личн|личн.*фол"),
        ("team_fouls", r"team.?foul|командн.*фол|фол.*команд"),
        ("fouls", r"foul|фол"),
        ("possessions", r"possession|владен"),
    )
    return next((key for key, pattern in patterns if re.search(pattern, text, re.I)), None)


def _extract_pari_live_statistics(objects):
    """Normalize only explicit, named PARI box-score fields; never guess opaque S1/S2 codes."""
    found = {}
    visited = set()

    def add(label, home, away, group="PARI LIVE"):
        metric = _pari_stat_metric(label)
        if not metric or home is None or away is None:
            return
        # Preserve source representation (including made/attempted strings).
        marker = (metric, str(home), str(away))
        if marker in found:
            return
        found[marker] = {
            "group": str(group or "PARI LIVE"), "name": str(label),
            "metric_key": metric, "home": home, "away": away,
            "source": "PARI",
        }

    def walk(value, depth=0):
        if depth > 7 or not isinstance(value, (dict, list)):
            return
        ident = id(value)
        if ident in visited:
            return
        visited.add(ident)
        if isinstance(value, dict):
            label = value.get("name") or value.get("label") or value.get("key") or value.get("title")
            home = next((value.get(k) for k in ("home", "homeValue", "home_value", "team1", "team1Value", "c1") if value.get(k) is not None), None)
            away = next((value.get(k) for k in ("away", "awayValue", "away_value", "team2", "team2Value", "c2") if value.get(k) is not None), None)
            if label:
                add(label, home, away, value.get("groupName") or value.get("group") or value.get("category") or "PARI LIVE")
            # PARI may encode paired team values as flat sibling keys.
            scalar_keys = {str(k): v for k, v in value.items()
                           if isinstance(v, (str, int, float)) and v is not None}
            normalized_keys = {re.sub(r"[^a-z0-9]", "", k.casefold()): k for k in scalar_keys}
            for normalized, original in list(normalized_keys.items()):
                side_match = re.fullmatch(r"(?:team1|home)([a-z][a-z0-9]*)", normalized)
                if not side_match:
                    continue
                suffix = side_match.group(1)
                away_key = normalized_keys.get("team2" + suffix) or normalized_keys.get("away" + suffix)
                if away_key:
                    add(suffix, scalar_keys[original], scalar_keys[away_key],
                        value.get("groupName") or value.get("group") or value.get("category") or "PARI LIVE")
            for key, child in value.items():
                if isinstance(child, (dict, list)):
                    walk(child, depth + 1)
        else:
            for child in value:
                walk(child, depth + 1)

    for obj in objects:
        walk(obj)
    return list(found.values())


# PARI public LIVE events feed. The endpoint is read-only; no account or betting actions.
async def _fetch_pari_live_events(catalog_only: bool = False):
    from urllib.parse import urljoin
    # Try the host that most recently returned a valid feed first.
    hosts = [
        "https://line-lb51-w.pb06e2-resources.com",
        "https://line-lb01-w.pb06e2-resources.com",
        "https://line-vk01-w.pb06e2-resources.ru",
        "https://line-vk02-w.pb06e2-resources.ru",
    ]
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
        "Referer": "https://pari.ru/live/basketball",
        "Origin": "https://pari.ru",
        "Cache-Control": "no-cache",
    }
    attempts = []
    # Render environments can have broken/unrouted IPv6 egress while IPv4 works.
    # Bind the outbound transport explicitly to IPv4; this does not alter the PARI URL.
    ipv4_transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=1)
    async with httpx.AsyncClient(
        transport=ipv4_transport,
        timeout=httpx.Timeout(6.0, connect=3.0),
        follow_redirects=True,
        headers=headers,
    ) as client:
        for host in hosts:
            url = host + "/events/list"
            try:
                response = None
                # Retry the primary host once on transient transport failure.
                retries = 1
                for attempt_no in range(retries):
                    try:
                        response = await client.get(
                            url,
                            params={"lang": "ru", "scopeMarket": "2300"},
                        )
                        break
                    except httpx.HTTPError:
                        if attempt_no + 1 >= retries:
                            raise
                item = {
                    "host": host,
                    "status": response.status_code,
                    "final_url": str(response.url),
                    "content_type": response.headers.get("content-type", ""),
                    "bytes": len(response.content or b""),
                }
                if response.status_code != 200:
                    item["error"] = "HTTP response is not 200"
                    item["body_preview"] = (response.text or "")[:240]
                    attempts.append(item)
                    continue
                try:
                    raw_json = response.content or b""
                    try:
                        payload = json.loads(raw_json.decode("utf-8-sig"))
                    except UnicodeDecodeError:
                        # A few legacy line hosts may return CP1251 bytes despite JSON content type.
                        payload = json.loads(raw_json.decode("cp1251"))
                except Exception as exc:
                    item["error"] = "Response is not valid UTF-8/CP1251 JSON: " + type(exc).__name__
                    item["body_preview"] = (response.text or "")[:320]
                    attempts.append(item)
                    continue
                if not isinstance(payload, dict):
                    item["error"] = "JSON root is not an object"
                    item["json_type"] = type(payload).__name__
                    item["body_preview"] = (response.text or "")[:240]
                    attempts.append(item)
                    continue
                if not isinstance(payload.get("events"), list):
                    item["error"] = "JSON object has no events array"
                    item["json_keys"] = list(payload.keys())[:40]
                    item["body_preview"] = (response.text or "")[:240]
                    attempts.append(item)
                    continue
                all_events = payload["events"]
                # PARI/Fonbet-style feeds keep live scoreboard data in top-level
                # eventMiscs/liveEventInfos arrays, keyed by event id, not inside
                # events[]. A parser that reads only events[] silently loses score
                # and timer even while the bookmaker page displays them.
                def index_live_rows(rows):
                    indexed = {}
                    if not isinstance(rows, list):
                        return indexed
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        row_id = next((row.get(k) for k in
                                       ("id", "eventId", "event_id", "e", "event")
                                       if row.get(k) is not None), None)
                        if isinstance(row_id, dict):
                            row_id = row_id.get("id")
                        if row_id is not None:
                            indexed.setdefault(str(row_id), {}).update(row)
                    return indexed
                event_misc_by_id = index_live_rows(payload.get("eventMiscs", []))
                live_info_by_id = index_live_rows(payload.get("liveEventInfos", []))
                factors = payload.get("customFactors", []) if isinstance(payload, dict) else []
                factor_map = {}
                for row in factors if isinstance(factors, list) else []:
                    if isinstance(row, dict) and row.get("e") is not None:
                        factor_map[str(row.get("e"))] = row.get("factors", [])
                sport_ids = sorted({
                    str(event.get("sportId"))
                    for event in all_events
                    if isinstance(event, dict) and event.get("sportId") is not None
                })

                # Keep the complete event hierarchy. PARI often places period,
                # score/statistics and market rows under the root match event.
                events_by_parent = {}
                for child in all_events:
                    if not isinstance(child, dict):
                        continue
                    parent_id = child.get("parentId", child.get("parent_id"))
                    if parent_id is not None:
                        events_by_parent.setdefault(str(parent_id), []).append(child)

                def collect_related_event_rows(root_id, limit=250):
                    related, queue, seen = [], [str(root_id)], set()
                    while queue and len(related) < limit:
                        parent = queue.pop(0)
                        if parent in seen:
                            continue
                        seen.add(parent)
                        for child in events_by_parent.get(parent, []):
                            child_id = child.get("id")
                            if child_id is not None and str(child_id) not in seen:
                                related.append(child)
                                queue.append(str(child_id))
                                if len(related) >= limit:
                                    break
                    return related

                # Compact structural diagnostics: current PARI responses may use
                # different sport identifiers or nested sport metadata.
                def compact_event(event):
                    if not isinstance(event, dict):
                        return event
                    scalar = {}
                    nested = {}
                    for key, value in event.items():
                        if value is None or isinstance(value, (str, int, float, bool)):
                            scalar[key] = value
                        elif isinstance(value, dict):
                            nested[key] = list(value.keys())[:20]
                        elif isinstance(value, list):
                            nested[key] = {"type": "list", "length": len(value),
                                           "first_item_keys": list(value[0].keys())[:20]
                                           if value and isinstance(value[0], dict) else []}
                    return {"scalar_fields": scalar, "nested_fields": nested}
                sport_id_counts = {}
                events_by_sport = {}
                for event in all_events:
                    if isinstance(event, dict) and event.get("sportId") is not None:
                        sid = str(event.get("sportId"))
                        sport_id_counts[sid] = sport_id_counts.get(sid, 0) + 1
                        events_by_sport.setdefault(sid, []).append(event)
                # Resolve sportId dynamically from PARI's own sports catalogue.
                # The live feed may omit it, so use listBase as a cached metadata source.
                sports_meta = payload.get("sports", []) if isinstance(payload, dict) else []
                if not isinstance(sports_meta, list) or not sports_meta:
                    cached = globals().get("_PARI_SPORTS_CACHE", {})
                    if cached.get("expires", 0) > datetime.now(timezone.utc).timestamp():
                        sports_meta = cached.get("sports", [])
                    else:
                        try:
                            base_response = await client.get(
                                host + "/events/listBase",
                                params={"lang": "ru", "scopeMarket": "2300"},
                            )
                            if base_response.status_code == 200:
                                base_payload = base_response.json()
                                candidate_sports = base_payload.get("sports", []) if isinstance(base_payload, dict) else []
                                if isinstance(candidate_sports, list) and candidate_sports:
                                    sports_meta = candidate_sports
                                    globals()["_PARI_SPORTS_CACHE"] = {
                                        "expires": datetime.now(timezone.utc).timestamp() + 600,
                                        "sports": sports_meta,
                                    }
                        except (httpx.HTTPError, ValueError):
                            sports_meta = []
                sports_by_id = {
                    str(row.get("id")): row for row in sports_meta
                    if isinstance(row, dict) and row.get("id") is not None
                }
                def sport_parent_id(row):
                    for key in ("parentId", "parent_id", "parentSportId", "parent"):
                        value = row.get(key)
                        if isinstance(value, dict):
                            value = value.get("id")
                        if value is not None:
                            return str(value)
                    return None
                def sport_chain_text(row):
                    names = []
                    seen = set()
                    current = row
                    for _ in range(12):
                        if not isinstance(current, dict):
                            break
                        current_id = str(current.get("id", ""))
                        if current_id in seen:
                            break
                        seen.add(current_id)
                        names.append(str(current.get("name", "")))
                        parent_id = sport_parent_id(current)
                        current = sports_by_id.get(parent_id) if parent_id else None
                    return " / ".join(names).lower()
                basketball_ids = set()
                sport_names = {}
                basketball_markers = ("баскетбол", "basketball")
                known_basketball_markers = (
                    "nba", "wnba", "ncaa", "euroleague", "eurocup", "acb", "bbl",
                    "kbl", "wkbl", "nbl", "cba", "lkl", "vtb", "gbl", "serie a",
                    "pro a", "pro b", "liga nacional", "pba", "uaap", "tpbl",
                    "basketliga", "plk", "superliga", "adriatic", "aba league",
                    "fiba", "korisliiga", "b.league", "b one", "bnxt"
                )
                for sid, row in sports_by_id.items():
                    chain = sport_chain_text(row)
                    direct_name = str(row.get("name", "")).lower()
                    if any(marker in chain for marker in basketball_markers) or (
                        str(row.get("kind", "")).lower() == "segment"
                        and any(marker in direct_name for marker in known_basketball_markers)
                    ):
                        basketball_ids.add(sid)
                        sport_names[sid] = str(row.get("name", ""))
                # Retain the two IDs already verified from actual basketball root events.
                basketball_ids.update({"70206", "12071"})
                excluded_markers = (
                    "жен", "women", "female", "wnba", "wkbl", "esports",
                    "esportsbattle", "кибер", "cyber", "nba 2k", "2k26", "2k27",
                    "h2h", "ipbl", "виртуал", "3x3", "3х3"
                )
                basketball = []
                filter_counts = {
                    "all_event_rows": len(all_events),
                    "with_sport_id": 0,
                    "sport_id_in_basketball_catalogue": 0,
                    "excluded_by_name": 0,
                    "excluded_by_hierarchy": 0,
                    "not_live": 0,
                    "accepted_live_roots": 0,
                }
                candidate_live_examples = []
                def repair_team_encoding(value):
                    value = str(value or "")
                    # Recover UTF-8 Cyrillic that was decoded as CP1251/Latin-1 upstream.
                    # Only accept a conversion when typical mojibake markers decrease.
                    markers = ("Р", "С", "Ð", "Ñ", "вЂ", "â€", "�")
                    for _ in range(3):
                        try:
                            repaired = value.encode("cp1251").decode("utf-8")
                        except (UnicodeEncodeError, UnicodeDecodeError):
                            try:
                                repaired = value.encode("latin1").decode("utf-8")
                            except (UnicodeEncodeError, UnicodeDecodeError):
                                break
                        before = sum(value.count(marker) for marker in markers)
                        after = sum(repaired.count(marker) for marker in markers)
                        if repaired == value or after >= before:
                            break
                        value = repaired
                    return value

                def repair_nested_encoding(value):
                    if isinstance(value, str):
                        return repair_team_encoding(value)
                    if isinstance(value, list):
                        return [repair_nested_encoding(item) for item in value]
                    if isinstance(value, dict):
                        return {key: repair_nested_encoding(item) for key, item in value.items()}
                    return value

                def _extract_pari_score(source):
                    # Read only explicit PARI score fields; never turn missing values into 0.
                    if not isinstance(source, dict):
                        return None
                    nested = source.get("score") or source.get("scoreInfo") or source.get("scores")
                    if isinstance(nested, dict):
                        home = next((nested.get(k) for k in ("home", "homeScore", "home_score", "team1", "team1Score", "score1") if nested.get(k) is not None), None)
                        away = next((nested.get(k) for k in ("away", "awayScore", "away_score", "team2", "team2Score", "score2") if nested.get(k) is not None), None)
                    else:
                        home = away = None
                    if home is None:
                        home = next((source.get(k) for k in ("team1Score", "team1_score", "homeScore", "home_score", "score1", "scoreHome", "homePoints") if source.get(k) is not None), None)
                    if away is None:
                        away = next((source.get(k) for k in ("team2Score", "team2_score", "awayScore", "away_score", "score2", "scoreAway", "awayPoints") if source.get(k) is not None), None)
                    def numeric(v):
                        if isinstance(v, dict):
                            v = next((v.get(k) for k in ("current", "total", "value", "points", "score") if v.get(k) is not None), None)
                        if isinstance(v, (list, tuple)) and v:
                            v = v[-1]
                        try:
                            n = float(v)
                            return int(n) if math.isfinite(n) and n >= 0 else None
                        except (TypeError, ValueError, OverflowError):
                            return None
                    h, a = numeric(home), numeric(away)
                    return [h, a] if h is not None and a is not None else None

                for event in all_events:
                    if not isinstance(event, dict):
                        continue
                    sid = str(event.get("sportId", ""))
                    if sid:
                        filter_counts["with_sport_id"] += 1
                    if sid not in basketball_ids:
                        if str(event.get("place", "")).lower() == "live" and len(candidate_live_examples) < 12:
                            candidate_live_examples.append({
                                "sport_id": sid,
                                "name": repair_team_encoding(event.get("name") or event.get("competitionName") or ""),
                                "team1": repair_team_encoding(event.get("team1") or event.get("team1Name") or ""),
                                "team2": repair_team_encoding(event.get("team2") or event.get("team2Name") or ""),
                                "level": event.get("level"),
                                "parent_id": event.get("parentId"),
                            })
                        continue
                    filter_counts["sport_id_in_basketball_catalogue"] += 1
                    team_text = " ".join(repair_team_encoding(event.get(k, "")) for k in ("team1", "team2")).lower()
                    competition_text = " ".join([
                        repair_team_encoding(event.get("name", "")),
                        repair_team_encoding(event.get("competitionName", "")),
                        repair_team_encoding(sport_names.get(sid, ""))
                    ]).lower()
                    if any(marker in competition_text or marker in team_text for marker in excluded_markers):
                        filter_counts["excluded_by_name"] += 1
                        continue
                    # Only root match rows; children are periods/markets, not matches.
                    if event.get("level") != 1 and event.get("parentId") is not None:
                        filter_counts["excluded_by_hierarchy"] += 1
                        continue
                    # Do not mix prematch events into the LIVE list.
                    if str(event.get("place", "")).lower() != "live":
                        filter_counts["not_live"] += 1
                        continue
                    event_id = event.get("id")
                    event_misc = event_misc_by_id.get(str(event_id), {})
                    live_info = live_info_by_id.get(str(event_id), {})
                    # Some PARI rows are placed in the LIVE tree before kickoff.
                    # Explicit notStarted status/comment overrides place=live.
                    pari_state = " ".join(str(row.get(key) or "") for row in
                                          (event, event_misc, live_info)
                                          for key in ("st1", "status", "state", "comment",
                                                      "scoreComment", "scoreCommentTail")).casefold()
                    if any(marker in pari_state for marker in
                           ("notstarted", "не начался", "не началась", "ожидает начала")):
                        filter_counts["not_live"] += 1
                        continue
                    pari_gender_text = " ".join(
                        repair_team_encoding(event.get(key) or "")
                        for key in ("team1", "team2", "name", "competitionName", "sportName")
                    ).casefold()
                    if re.search(r"\b(women|woman|female|ladies|girls|wbb|womens)\b|женск|девуш", pari_gender_text):
                        filter_counts["excluded_by_name"] += 1
                        continue
                    filter_counts["accepted_live_roots"] += 1
                    # Merge only source-provided fields; never infer score or clock
                    # from kickoff timestamps or odds.
                    score_source = dict(event)
                    for source_row in (event_misc, live_info):
                        for key, value in source_row.items():
                            if value is not None and (score_source.get(key) is None or key not in score_source):
                                score_source[key] = value
                    related_rows = collect_related_event_rows(event_id)
                    repaired_raw = repair_nested_encoding(event)
                    repaired_related_rows = repair_nested_encoding(related_rows)
                    period_value = next((row.get(key) for row in
                                          (event, live_info, event_misc)
                                          for key in ("period", "currentPeriod", "periodNumber",
                                                      "quarter", "currentQuarter", "periodName")
                                          if row.get(key) is not None), None)
                    if period_value is None:
                        for child_row in repaired_related_rows:
                            child_name = str(child_row.get("name", ""))
                            quarter_match = re.search(r"(?:^|\D)([1-4])\s*[-–.]?\s*(?:я|ая)?\s*(?:четверть|четверт)", child_name.lower())
                            if quarter_match:
                                period_value = int(quarter_match.group(1))
                                break
                    def event_factors(source_row):
                        combined = []
                        for candidate_list in (
                            factor_map.get(str(source_row.get("id")), []),
                            source_row.get("factors", []),
                            source_row.get("markets", []),
                        ):
                            if isinstance(candidate_list, list):
                                combined.extend(item for item in candidate_list if isinstance(item, dict))
                        unique, seen = [], set()
                        for factor in combined:
                            marker = (str(factor.get("f")), str(factor.get("pt")),
                                      str(factor.get("p")), str(factor.get("v")))
                            if marker not in seen:
                                seen.add(marker)
                                unique.append(factor)
                        return unique

                    clock_value = next((row.get(key) for row in
                                        (event, live_info, event_misc)
                                        for key in ("clock", "timer", "time", "countdown",
                                                    "timeRemaining", "remainingTime", "periodClock")
                                        if row.get(key) is not None), None)
                    if isinstance(clock_value, dict):
                        clock_value = next((clock_value.get(key) for key in
                                            ("clock", "timer", "time", "remaining",
                                             "remainingTime", "seconds", "value")
                                            if clock_value.get(key) is not None), None)
                    # PARI timerSeconds/timer may represent elapsed match time, not
                    # the countdown of the current quarter. Convert only when the
                    # competition format is known; never feed a 31:14/38:26 match
                    # timer into a quarter-clock field.
                    timer_raw = next((row.get("timerSeconds") for row in
                                      (live_info, event_misc, event)
                                      if row.get("timerSeconds") is not None), None)
                    if timer_raw is None and clock_value is not None:
                        timer_text = str(clock_value).strip()
                        timer_match = re.fullmatch(r"(\d{1,3}):(\d{2})", timer_text)
                        if timer_match:
                            timer_raw = int(timer_match.group(1)) * 60 + int(timer_match.group(2))
                        elif re.fullmatch(r"\d{1,5}", timer_text):
                            timer_raw = int(timer_text)
                    try:
                        timer_seconds = int(float(timer_raw)) if timer_raw is not None else None
                    except (TypeError, ValueError, OverflowError):
                        timer_seconds = None

                    # Use PARI's resolved sport catalogue name, not only the often-empty
                    # event.name/competitionName fields. Some catalogue labels reverse
                    # the order ("Кубок BSKT"), so match both forms explicitly.
                    competition_text = repair_team_encoding(" ".join(str(x or "") for x in (
                        event.get("name"), event.get("competitionName"),
                        sport_names.get(str(event.get("sportId")), "")
                    ))).casefold()
                    period_length_seconds = None
                    if re.search(r"\bcdb[l1]\b|chinese basketball development league", competition_text):
                        period_length_seconds = 720
                    elif re.search(r"bskt\s*cup|cup\s*bskt|кубок\s*bskt", competition_text):
                        period_length_seconds = 600
                    elif re.search(r"philippines.*ncaa|ncaa.*philippines|филиппин.*ncaa", competition_text):
                        # Philippine NCAA follows the four-quarter FIBA format;
                        # do not confuse it with US men's NCAA two-half rules.
                        period_length_seconds = 600

                    # PARI's observed timerSeconds are cumulative match elapsed
                    # seconds (e.g. 38:26 in CDBL, 31:14 in BSKT Cup).
                    # Derive the quarter and its remaining clock from that value.
                    if timer_seconds is not None and period_length_seconds and timer_seconds >= period_length_seconds:
                        elapsed_periods = timer_seconds // period_length_seconds
                        if 0 <= elapsed_periods < 4:
                            period_value = elapsed_periods + 1
                            remaining = period_length_seconds - (timer_seconds % period_length_seconds)
                            clock_value = f"{remaining // 60:02d}:{remaining % 60:02d}"
                        else:
                            clock_value = None
                    elif timer_seconds is not None and 0 <= timer_seconds <= 1200:
                        clock_value = f"{timer_seconds // 60:02d}:{timer_seconds % 60:02d}"
                    elif clock_value is not None:
                        clock_text = str(clock_value).strip()
                        clock_match = re.fullmatch(r"(\d{1,2}):([0-5]\d)", clock_text)
                        if clock_match and int(clock_match.group(1)) * 60 + int(clock_match.group(2)) <= 1200:
                            clock_value = clock_match.group(1).zfill(2) + ":" + clock_match.group(2)
                        else:
                            clock_value = None

                    period_scores = []
                    score_rows = live_info.get("scores")
                    if isinstance(score_rows, list) and len(score_rows) > 1 and isinstance(score_rows[1], list):
                        for score_row in score_rows[1]:
                            if not isinstance(score_row, dict):
                                continue
                            try:
                                q1, q2 = int(score_row.get("c1")), int(score_row.get("c2"))
                                period_scores.append([q1, q2])
                            except (TypeError, ValueError, OverflowError):
                                continue
                    # PARI can leave a completed fixture in the LIVE tree for a short
                    # period. When the verified cumulative timer reaches full regulation
                    # and all four quarter scores add up exactly to the final score,
                    # suppress this stale row instead of presenting it as a live game.
                    if (timer_seconds is not None and period_length_seconds
                            and timer_seconds >= 4 * period_length_seconds
                            and len(period_scores) == 4):
                        pari_score_check = (_extract_pari_score(event)
                                            or _extract_pari_score(event_misc)
                                            or _extract_pari_score(live_info))
                        quarter_totals = [sum(pair[side] for pair in period_scores)
                                          for side in (0, 1)]
                        if pari_score_check == quarter_totals:
                            filter_counts["accepted_live_roots"] = max(
                                0, filter_counts["accepted_live_roots"] - 1
                            )
                            filter_counts["not_live"] += 1
                            continue

                    root_factors = event_factors(event)
                    related_factor_rows = [
                        (child, event_factors(child))
                        for child in collect_related_event_rows(event_id)
                    ]
                    basketball.append({
                        "id": str(event_id) if event_id is not None else "",
                        "team1": repair_team_encoding(event.get("team1") or event.get("team1Name") or ""),
                        "team2": repair_team_encoding(event.get("team2") or event.get("team2Name") or ""),
                        "sport_id": str(event.get("sportId")),
                        "classification": "catalogue_resolved",
                        "sport_name": repair_team_encoding(sport_names.get(str(event.get("sportId")), "")),
                        "status": "live",
                        "league": repair_team_encoding(event.get("name") or event.get("competitionName") or ""),
                        "start_time": event.get("startTime"),
                        "score": (_extract_pari_score(event)
                                  or _extract_pari_score(live_info)
                                  or _extract_pari_score(event_misc)
                                  or _extract_pari_score(score_source)),
                        "score_source": "PARI" if (
                            _extract_pari_score(event) or _extract_pari_score(live_info)
                            or _extract_pari_score(event_misc) or _extract_pari_score(score_source)
                        ) else None,
                        "period": period_value,
                        "period_source": "PARI_timer_and_catalogue" if (
                            timer_seconds is not None and period_length_seconds
                            and timer_seconds >= period_length_seconds
                        ) else ("PARI_event" if period_value is not None else None),
                        "clock": clock_value,
                        "clock_source": "PARI_timer_and_catalogue" if (
                            timer_seconds is not None and period_length_seconds
                            and timer_seconds >= period_length_seconds
                            and timer_seconds < 4 * period_length_seconds
                        ) else ("PARI_clock_field" if clock_value is not None else None),
                        "period_length_seconds": period_length_seconds,
                        "period_count": 4 if period_length_seconds else None,
                        "match_elapsed_seconds": timer_seconds,
                        "period_scores": period_scores,
                        "match_timeline": [
                            {"period": index + 1, "home": pair[0], "away": pair[1],
                             "total": pair[0] + pair[1],
                             "cumulative_home": sum(x[0] for x in period_scores[:index + 1]),
                             "cumulative_away": sum(x[1] for x in period_scores[:index + 1])}
                            for index, pair in enumerate(period_scores)
                            if isinstance(pair, (list, tuple)) and len(pair) >= 2
                            and all(isinstance(x, (int, float)) for x in pair[:2])
                        ],
                        "timeline_source": "PARI liveEventInfos.scores",
                        "statistics_types": event.get("statisticsTypes"),
                        "live_fields": repair_nested_encoding({
                            key: value
                            for source_row in (event, event_misc, live_info)
                            for key, value in source_row.items()
                            if any(token in key.lower() for token in (
                                "score", "period", "quarter", "clock", "timer", "time",
                                "team1s", "team2s", "team1m", "team2m", "foul", "stat",
                                "point", "rebound", "assist", "attempt", "throw", "possession",
                                "percent", "three", "two", "free"
                            ))
                        }),
                        # Keep source hierarchy, repairing only text encoding; numeric source values remain untouched.
                        "raw": repaired_raw,
                        "related_events": repaired_related_rows,
                        "factors": root_factors,
                        "related_factors": [
                            factor for _child, child_factors in related_factor_rows
                            for factor in child_factors
                        ],
                        "related_factor_groups": [
                            {
                                "event_id": str(child.get("id")),
                                "name": repair_team_encoding(child.get("name") or child.get("kindName") or ""),
                                "level": child.get("level"),
                                "kind": child.get("kind"),
                                "root_kind": child.get("rootKind"),
                                "factors": child_factors,
                            }
                            for child, child_factors in related_factor_rows
                            if child_factors
                        ],
                        "match_total_candidates": [
                            factor for factor in root_factors
                            if str(factor.get("f")) in ("930", "931")
                        ],
                    })
                # Extract explicit named team statistics if PARI includes them in
                # the live event, misc, info, or related-event payload. Opaque
                # statisticsTypes/S1 codes are retained for diagnostics, not guessed.
                for pari_row in basketball:
                    pari_id = str(pari_row.get("id") or "")
                    pari_stats = _extract_pari_live_statistics([
                        pari_row.get("raw", {}),
                        pari_row.get("live_fields", {}),
                        event_misc_by_id.get(pari_id, {}),
                        live_info_by_id.get(pari_id, {}),
                        pari_row.get("related_events", []),
                    ])
                    if pari_stats:
                        pari_row["external_statistics"] = pari_stats
                        pari_row["statistics_source"] = "PARI"
                    keys = sorted({x["metric_key"] for x in pari_stats})
                    expected = ["field_goals", "field_goals_made", "field_goals_attempted",
                                "three_pointers_made", "three_pointers_attempted",
                                "two_pointers_made", "two_pointers_attempted",
                                "free_throws_made", "free_throws_attempted",
                                "rebounds", "offensive_rebounds", "defensive_rebounds",
                                "assists", "turnovers", "steals", "blocks",
                                "personal_fouls", "team_fouls", "fouls", "possessions"]
                    pari_row["statistics_quality"] = {
                        "available_metrics": keys,
                        "missing_metrics": [key for key in expected if key not in keys],
                        "available_count": len(set(keys).intersection(expected)),
                        "expected_count": len(expected),
                        "coverage_percent": round(len(set(keys).intersection(expected)) / len(expected) * 100),
                        "raw_fields_count": len(pari_stats), "complete": all(key in keys for key in expected),
                    }
                attempts.append({
                    "host": host,
                    "status": 200,
                    "total_events": len(all_events),
                    "basketball_events": len(basketball),
                    "basketball_sport_ids_detected": sorted(basketball_ids.intersection(sport_ids)),
                    "sports_catalog_entries": len(sports_meta),
                    "basketball_catalog_ids": len(basketball_ids),
                    "live_only": True,
                    "sport_ids": sport_ids,
                })
                # The regular LIVE endpoint must stay lightweight. Full per-sport
                # catalogue diagnostics are only needed by the explicit catalog_only
                # diagnostic request, never by every phone refresh.
                if not catalog_only:
                    # PARI is authoritative for markets/odds. Fill missing score,
                    # period and clock only from a separately verified live event.
                    # External score providers must never prevent PARI matches and
                    # markets from reaching the browser. Give enrichment a short
                    # budget; return the authoritative PARI feed even on timeout.
                    try:
                        live_enrichment = await asyncio.wait_for(
                            _enrich_with_sofascore_live(basketball), timeout=12.0
                        )
                    except asyncio.TimeoutError:
                        live_enrichment = {
                            "source": "sofascore+sportscore",
                            "status": "timeout",
                            "matched": 0,
                            "errors": ["Внешнее обогащение превысило лимит 7 секунд; матчи PARI возвращены без него."]
                        }
                    except Exception as exc:
                        live_enrichment = {
                            "source": "sofascore+sportscore",
                            "status": "error",
                            "matched": 0,
                            "errors": [type(exc).__name__ + ": " + str(exc)[:180]]
                        }
                    print("PARI_LIVE_DIAG " + json.dumps({
                        "ok": True,
                        "host": host,
                        "total_events": len(all_events),
                        "payload_keys": list(payload.keys())[:60],
                        "event_miscs_count": len(payload.get("eventMiscs", [])) if isinstance(payload.get("eventMiscs"), list) else None,
                        "live_event_infos_count": len(payload.get("liveEventInfos", [])) if isinstance(payload.get("liveEventInfos"), list) else None,
                        "event_misc_sample_keys": list((payload.get("eventMiscs") or [{}])[0].keys())[:40] if isinstance(payload.get("eventMiscs"), list) and payload.get("eventMiscs") and isinstance(payload.get("eventMiscs")[0], dict) else [],
                        "live_info_sample_keys": list((payload.get("liveEventInfos") or [{}])[0].keys())[:40] if isinstance(payload.get("liveEventInfos"), list) and payload.get("liveEventInfos") and isinstance(payload.get("liveEventInfos")[0], dict) else [],
                        "basketball_events": len(basketball),
                        "filter_diagnostics": filter_counts,
                        "live_score_source": live_enrichment,
                        "forecast_input_samples": [
                            {
                                "id": event.get("id"),
                                "teams": str(event.get("team1") or "") + " — " + str(event.get("team2") or ""),
                                "competition_context": {
                                    "league": event.get("league"),
                                    "sport_name": event.get("sport_name"),
                                    "sport_id": event.get("sport_id"),
                                    "raw_name": (event.get("raw") or {}).get("name"),
                                    "competitionName": (event.get("raw") or {}).get("competitionName"),
                                    "tournamentId": (event.get("raw") or {}).get("tournamentId"),
                                    "tournament": (event.get("raw") or {}).get("tournament"),
                                    "raw_keys": list((event.get("raw") or {}).keys())[:80],
                                },
                                "score_present": event.get("score") is not None,
                                "pari_score_debug": {
                                    "event": {key: value for key, value in (event.get("raw") or {}).items()
                                              if any(token in str(key).lower() for token in ("score", "point", "team1s", "team2s"))},
                                    "event_misc": event_misc_by_id.get(str(event.get("id")), {}),
                                    "live_info": live_info_by_id.get(str(event.get("id")), {}),
                                },
                                "pari_clock_debug": {
                                    key: value for key, value in (event.get("raw") or {}).items()
                                    if any(token in str(key).lower() for token in ("clock", "timer", "period", "quarter", "time"))
                                },
                                "period": event.get("period") or (event.get("external_live") or {}).get("period"),
                                "clock": event.get("clock") or (event.get("external_live") or {}).get("clock"),
                                "period_length_seconds": (event.get("external_live") or {}).get("period_length_seconds"),
                                "total_market_count": len(event.get("match_total_candidates") or []),
                                "score_source": event.get("score_source") or (event.get("external_live") or {}).get("source"),
                                "statistics_source": event.get("statistics_source"),
                                "statistics_fields": len(event.get("external_statistics") or []),
                                "statistics_missing": (event.get("statistics_quality") or {}).get("missing_metrics", []),
                                "match_timeline": event.get("match_timeline", []),
                            }
                            for event in basketball[:20]
                        ],
                        "attempts": [
                            {k: item.get(k) for k in ("host", "status", "error") if item.get(k) is not None}
                            for item in attempts
                        ],
                    }, ensure_ascii=False, default=str), flush=True)
                    return {
                        "ok": True,
                        "connected": True,
                        "source": url + "?lang=ru&scopeMarket=2300",
                        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
                        "total_events": len(all_events),
                        "basketball_events": len(basketball),
                        "events": basketball,
                        "live_score_source": live_enrichment,
                        "filter_diagnostics": filter_counts,
                        "basketball_catalogue_ids": len(basketball_ids),
                        "sports_catalogue_entries": len(sports_meta),
                        "unclassified_live_examples": candidate_live_examples if not basketball else [],
                        "attempts": attempts,
                        "note": "PARI supplies live events and betting markets; missing live score/period/clock are enriched from SofaScore only on high-confidence team matches.",
                    }

                # Sample root match records across the most common sport IDs.
                # The feed is hierarchical: level 1 rows are match/event roots;
                # lower levels are periods and markets, not separate matches.
                # Build a complete compact catalogue, not just the top 30 sport IDs.
                # This exposes the IDs that the allowlist would otherwise silently drop.
                top_sport_ids = [
                    row["sport_id"] for row in sorted(
                        [{"sport_id": k, "events": v} for k, v in sport_id_counts.items()],
                        key=lambda x: x["events"], reverse=True
                    )
                ]
                root_samples = []
                sport_catalog = []
                for sid in top_sport_ids:
                    roots = [
                        e for e in events_by_sport.get(sid, [])
                        if (e.get("level") == 1 or e.get("parentId") is None)
                        and (e.get("team1") or e.get("team2"))
                    ]
                    live_roots = [e for e in roots if str(e.get("place", "")).lower() == "live"]
                    line_roots = [e for e in roots if str(e.get("place", "")).lower() == "line"]
                    def root_preview(e):
                        return {
                            "id": e.get("id"),
                            "level": e.get("level"),
                            "kind": e.get("kind"),
                            "root_kind": e.get("rootKind"),
                            "team1": repair_team_encoding(e.get("team1") or e.get("team1Name") or ""),
                            "team2": repair_team_encoding(e.get("team2") or e.get("team2Name") or ""),
                            "name": repair_team_encoding(e.get("name") or e.get("competitionName") or ""),
                            "place": e.get("place"),
                            "start_time": e.get("startTime"),
                            "fields": sorted(e.keys()),
                        }
                    sport_catalog.append({
                        "sport_id": sid,
                        "event_rows": sport_id_counts.get(sid, 0),
                        "root_matches": len(roots),
                        "live_roots": len(live_roots),
                        "prematch_roots": len(line_roots),
                        "examples": [root_preview(e) for e in (live_roots[:2] or roots[:2])],
                    })
                    for e in roots[:2]:
                        root_samples.append({"sport_id": sid, **root_preview(e)})
                if catalog_only:
                    live_catalog = [row for row in sport_catalog if row["live_roots"] > 0]
                    return {
                        "ok": True,
                        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
                        "total_events": len(all_events),
                        "sport_id_count": len(sport_ids),
                        "live_sport_id_count": len(live_catalog),
                        "live_sport_catalog": live_catalog,
                        "note": "Compact audit: every sportId with at least one live root match, including team examples."
                    }
                return {
                    "ok": True,
                    "connected": True,
                    "source": url + "?lang=ru&scopeMarket=2300",
                    "checked_at_utc": datetime.now(timezone.utc).isoformat(),
                    "total_events": len(all_events),
                    "basketball_events": len(basketball),
                    "sport_ids_sample": sport_ids[:40],
                    "sport_id_count": len(sport_ids),
                    "sport_id_counts_top": sorted(
                        [{"sport_id": k, "events": v} for k, v in sport_id_counts.items()],
                        key=lambda x: x["events"], reverse=True
                    )[:30],
                    "sample_event_shapes": [compact_event(e) for e in all_events[:5]],
                    "sample_root_events_by_sport": root_samples[:80],
                    "sport_catalog": sport_catalog,
                    "sample_factor_shape": compact_event(factors[0]) if isinstance(factors, list) and factors else None,
                    "events": basketball,
                    "attempts": attempts,
                    "note": "Basketball sportIds are resolved dynamically from PARI sports hierarchy or cached listBase metadata. Female, esports, virtual and 3x3 competitions are excluded.",
                }
            except httpx.HTTPError as exc:
                attempts.append({"host": host, "error": type(exc).__name__, "detail": str(exc)[:240]})
            except Exception as exc:
                attempts.append({"host": host, "error": type(exc).__name__, "detail": str(exc)[:240]})
    failure_summary = "; ".join(
        str(item.get("host", "")) + ": " + str(item.get("error", "unknown error"))
        + ((" (HTTP " + str(item.get("status")) + ")") if item.get("status") is not None else "")
        for item in attempts
    )
    print("PARI_LIVE_DIAG " + json.dumps({
        "ok": False,
        "attempts": [
            {k: item.get(k) for k in ("host", "status", "error") if item.get(k) is not None}
            for item in attempts
        ],
    }, ensure_ascii=False, default=str), flush=True)
    return {
        "ok": False,
        "connected": False,
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "attempts": attempts,
        "error": "PARI events/list недоступен на проверенных узлах. " + (failure_summary or "Нет диагностических ответов."),
    }

@app.get("/api/pari/match-debug")
async def pari_match_debug(team: str = "Luxol"):
    """Inspect LIVE matches; tolerate PARI Cyrillic mojibake and Latin transliterations."""
    import json
    import unicodedata

    data = await _fetch_pari_live_events(catalog_only=False)
    if not data.get("ok"):
        return {"ok": False, "error": data.get("error"), "attempts": data.get("attempts", [])}

    def repair_text(value):
        text = str(value or "")
        # Repeatedly undo UTF-8 bytes that were decoded as Windows-1251.
        for _ in range(2):
            try:
                candidate = text.encode("cp1251").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                break
            bad_before = sum(text.count(ch) for ch in ("Р", "С", "Ð", "Ñ", "�"))
            bad_after = sum(candidate.count(ch) for ch in ("Р", "С", "Ð", "Ñ", "�"))
            if bad_after >= bad_before or candidate == text:
                break
            text = candidate
        return text

    def repair_tree(value):
        if isinstance(value, str):
            return repair_text(value)
        if isinstance(value, list):
            return [repair_tree(item) for item in value]
        if isinstance(value, dict):
            return {key: repair_tree(item) for key, item in value.items()}
        return value

    def compact(row):
        if not isinstance(row, dict):
            return row
        scalar, nested = {}, {}
        for key, value in row.items():
            if value is None or isinstance(value, (str, int, float, bool)):
                scalar[key] = repair_text(value) if isinstance(value, str) else value
            elif isinstance(value, dict):
                nested[key] = {"keys": list(value.keys())[:60]}
            elif isinstance(value, list):
                nested[key] = {
                    "length": len(value),
                    "first_keys": list(value[0].keys())[:60] if value and isinstance(value[0], dict) else [],
                    "first_scalar": repair_text(value[0]) if value and isinstance(value[0], str) else (value[0] if value and isinstance(value[0], (int, float, bool)) else None)
                }
        return {"scalar_fields": scalar, "nested_fields": nested}

    needle = unicodedata.normalize("NFKC", str(team or "")).casefold().strip()
    aliases = {needle}
    if needle in ("luxol", "luxol basketball"):
        aliases.update(("луксол", "люксол"))
    matches, samples = [], []
    for event in data.get("events", []):
        repaired_event = repair_tree(event)
        raw_text = json.dumps(repaired_event, ensure_ascii=False, default=str).casefold()
        found = any(alias and alias in raw_text for alias in aliases)
        summary = {
            "id": event.get("id"),
            "team1": repair_text(event.get("team1")),
            "team2": repair_text(event.get("team2")),
            "league": repair_text(event.get("league")),
            "sport_name": repair_text(event.get("sport_name")),
            "root_fields": sorted((event.get("raw") or {}).keys()) if isinstance(event.get("raw"), dict) else [],
            "related_count": len(event.get("related_events", []) or []),
        }
        if not found:
            if len(samples) < 30:
                samples.append(summary)
            continue
        matches.append({
            **summary,
            "root": compact(repaired_event.get("raw", {})),
            "related_rows": [compact(row) for row in (repaired_event.get("related_events", []) or [])[:80]],
            "factor_count": len(repaired_event.get("factors", []) or []),
            "factor_examples": (repaired_event.get("factors", []) or [])[:12],
            "related_factor_count": len(repaired_event.get("related_factors", []) or []),
            "related_factor_examples": (repaired_event.get("related_factors", []) or [])[:20],
        })
    return {
        "ok": True,
        "checked_at_utc": data.get("checked_at_utc"),
        "total_events": data.get("total_events"),
        "basketball_events": data.get("basketball_events"),
        "search": team,
        "matches": matches,
        "live_match_samples": samples,
        "filter_diagnostics": data.get("filter_diagnostics"),
        "note": "Search includes repaired Cyrillic text and common Latin transliterations; all actual source fields are preserved."
    }

@app.get("/api/pari/diagnostics")
async def pari_live_diagnostics():
    data = await _fetch_pari_live_events(catalog_only=False)
    # The diagnostics request already fetched the complete LIVE payload.
    # Prime the same in-memory cache used by the homepage so opening diagnostics
    # does not force the phone browser to repeat the upstream PARI request.
    if data.get("ok") and data.get("connected"):
        globals()["_PARI_LIVE_CACHE"] = {
            "saved_at": datetime.now(timezone.utc).timestamp(),
            "data": data,
        }
    return {
        "ok": data.get("ok"),
        "connected": data.get("connected"),
        "total_events": data.get("total_events"),
        "basketball_events": data.get("basketball_events"),
        "filter_diagnostics": data.get("filter_diagnostics"),
        "basketball_catalogue_ids": data.get("basketball_catalogue_ids"),
        "sports_catalogue_entries": data.get("sports_catalogue_entries"),
        "unclassified_live_examples": data.get("unclassified_live_examples", []),
        "attempts": data.get("attempts", []),
        "error": data.get("error"),
    }


# Return the real PARI response on a cold start. The browser must not be left
# polling an empty loading response while a detached task runs in the background.
@app.get("/api/pari/live")
async def pari_live_events(catalog_only: bool = False):
    if catalog_only:
        return await _fetch_pari_live_events(catalog_only=True)

    now = datetime.now(timezone.utc).timestamp()
    cached = globals().get("_PARI_LIVE_CACHE")
    if cached and now - cached.get("saved_at", 0) <= 35:
        return cached["data"]

    data = await _fetch_pari_live_events(catalog_only=False)
    if data.get("ok") and data.get("connected"):
        globals()["_PARI_LIVE_CACHE"] = {
            "saved_at": datetime.now(timezone.utc).timestamp(),
            "data": data,
        }
    return data


async def _pari_startup_stats_discovery():
    """Run one bounded PARI frontend bundle inspection per deploy and log likely stats endpoints."""
    try:
        result = await pari_discover()
        pattern = re.compile(r"statistic|box.?score|possession|foul|rebound|assist|turnover|steal|block|three.?point|two.?point|free.?throw|field.?goal|play.?by.?play|timeline|tracker", re.I)
        candidates = []
        for chunk in result.get("chunk_scans", []) if isinstance(result, dict) else []:
            if not isinstance(chunk, dict):
                continue
            url = str(chunk.get("url") or "")
            if pattern.search(url):
                candidates.append({"kind": "chunk", "url": url, "status": chunk.get("status")})
            for key in ("targeted_context_windows", "api_context_windows", "data_model_context_windows"):
                windows = chunk.get(key, [])
                for window in windows if isinstance(windows, list) else []:
                    token = str(window.get("token") or "")
                    snippet = str(window.get("snippet") or "")
                    if pattern.search(token + " " + snippet):
                        candidates.append({"kind": "code", "chunk": url, "token": token, "snippet": snippet[:900]})
            for value in chunk.get("request_string_hints", []):
                if pattern.search(str(value)):
                    candidates.append({"kind": "request", "chunk": url, "value": str(value)[:200]})
        unique = []
        seen = set()
        for item in candidates:
            marker = json.dumps(item, ensure_ascii=False, sort_keys=True)
            if marker not in seen:
                seen.add(marker)
                unique.append(item)
        summary = {"ok": bool(result.get("ok")) if isinstance(result, dict) else False,
                   "diagnostic_version": result.get("diagnostic_version") if isinstance(result, dict) else None,
                   "pages": [{"url": page.get("requested_url"), "status": page.get("status"),
                              "hosts": (page.get("runtime_host_hints") or [])[:8]}
                             for page in result.get("pages", []) if isinstance(page, dict)],
                   "chunks_scanned": len(result.get("chunk_scans", [])) if isinstance(result, dict) else 0,
                   "bootstrap": [{"url": item.get("url"), "status": item.get("status"),
                                  "chunk_map": item.get("webpack_chunk_name_map"),
                                  "statistics_chunk_map": item.get("statistics_chunk_name_map"),
                                  "api_paths": (item.get("api_path_hints") or [])[:50],
                                  "runtime_windows": (item.get("runtime_windows") or [])[:8],
                                  "semantic_chunks": (item.get("semantic_chunk_candidates") or [])[:40]}
                                 for item in result.get("bootstrap_scans", []) if isinstance(item, dict)],
                   "chunk_summaries": [{"url": item.get("url"), "status": item.get("status"),
                                        "bytes": item.get("bytes"), "paths": (item.get("api_path_hints") or [])[:30],
                                        "service_terms": item.get("service_keyword_counts"),
                                        "stat_terms": item.get("data_model_keyword_counts"),
                                        "runtime_windows": (item.get("runtime_windows") or [])[:6]}
                                       for item in result.get("chunk_scans", []) if isinstance(item, dict)],
                   "candidate_count": len(unique),
                   "candidates": [{**item, "snippet": str(item.get("snippet") or "")[:260]}
                                  for item in unique[:18]]}
        print("PARI_STATS_DISCOVERY " + json.dumps(summary, ensure_ascii=False, default=str), flush=True)
    except Exception as exc:
        print("PARI_STATS_DISCOVERY_ERROR " + type(exc).__name__ + ": " + str(exc)[:240], flush=True)


@app.on_event("startup")
async def _schedule_pari_stats_discovery():
    # Do not delay app readiness; one diagnostic runs in the background after startup.
    asyncio.create_task(_pari_startup_stats_discovery())
