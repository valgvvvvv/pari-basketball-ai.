from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Optional
import math, random, os, hashlib, re
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

@app.get("/api/health")
def health():
    return {"status":"ok","version":"0.1.0","pari_live_connected":False,
            "source_status":"not_configured",
            "message":"Нужен подтверждённый официальный/лицензированный LIVE feed PARI."}

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
                        item["webpack_chunk_name_map"] = {key: chunk_name_map.get(key) for key in ("43010", "5793", "50480")}
                        # Besides bootstrap's initial chunks, inspect a small set of semantically
                        # relevant lazy chunks: live, line, event, sport, market and common API.
                        semantic_candidates = [
                            (chunk_id, filename + ".js")
                            for chunk_id, filename in chunk_name_map.items()
                            if re.search(r"live|line|event|sport|market|common|api|basket|score|match", filename, re.I)
                        ]
                        item["semantic_chunk_candidates"] = [
                            {"chunk_id": chunk_id, "filename": filename}
                            for chunk_id, filename in semantic_candidates[:12]
                        ]
                        chunk_candidates = []
                        for chunk_id in ("43010", "5793", "50480"):
                            semantic_name = chunk_name_map.get(chunk_id)
                            if semantic_name:
                                chunk_candidates.append((chunk_id, semantic_name + ".js"))
                            chunk_candidates.append((chunk_id, chunk_id + ".js"))
                        chunk_candidates.extend(semantic_candidates[:5])
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
                                    context_tokens = ("getApiState", "clientCommonAgent", "lineAgents", "commonApi",
                                                      "getServer", "sportEvents", "eventsFilter", "fetch(", "axios",
                                                      "ApiRequest", "apiUrl", "baseURL", "request(")
                                    context_windows = []
                                    for token in context_tokens:
                                        pos = 0
                                        hits = 0
                                        while hits < 3:
                                            pos = chunk_source.find(token, pos)
                                            if pos < 0:
                                                break
                                            context_windows.append({"token": token,
                                                                    "snippet": chunk_source[max(0, pos-500):min(len(chunk_source), pos+1100)]})
                                            pos += len(token)
                                            hits += 1
                                    chunk_item["api_context_windows"] = context_windows[:24]
                                    # Detect data-model vocabulary and show bounded source context.
                                    # These are code clues, not proof that the values are live.
                                    data_patterns = {
                                        "event_ids": r"\b(?:eventId|sportEventId|event_id|eventID)\\b",
                                        "team_fields": r"\b(?:homeTeam|awayTeam|teamHome|teamAway|competitor|participant)\\b",
                                        "score_fields": r"\b(?:score|homeScore|awayScore|currentScore|scoreHome|scoreAway)\\b",
                                        "clock_fields": r"\b(?:clock|timer|period|quarter|matchTime)\\b",
                                        "market_fields": r"\b(?:odds|price|marketId|outcome)\\b",
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
            "diagnostic_version": 8, "pages": pages,
            "asset_refs_found": len(asset_refs), "first_party_assets_scanned": len(scanned),
            "scanned_assets": scanned, "bootstrap_scans": bootstrap_scans, "chunk_scans": chunk_scans, "bootstrap_urls_found": len(set(bootstrap_urls)), "inline_route_hints": list(dict.fromkeys(inline_hints))[:100],
            "route_candidates": hints[:200], "confirmed_api": False,
            "note": "Версия 8 проверяет до трёх официальных CDN-зеркал PARI, извлекает webpack-карту и дополнительно сканирует до пяти lazy-чанков с именами live/line/event/sport/market/common/api. Для чанков показывает счётчики полей event/team/score/clock/market и ограниченные фрагменты кода вокруг них. Это только признаки модели данных, не доказательство LIVE-feed; API подтверждается только реальными ответами и повторным изменением счёта/времени/коэффициентов."
        }
    except httpx.TimeoutException:
        return {"ok": False, "diagnostic_version": 8, "error": "PARI diagnostic timed out", "checked_at_utc": datetime.now(timezone.utc).isoformat()}
    except httpx.HTTPError as exc:
        return {"ok": False, "diagnostic_version": 8, "error": type(exc).__name__, "checked_at_utc": datetime.now(timezone.utc).isoformat()}
    except Exception as exc:
        # Keep the diagnostic response machine-readable instead of returning an opaque 500.
        return {"ok": False, "diagnostic_version": 8, "error": type(exc).__name__, "checked_at_utc": datetime.now(timezone.utc).isoformat()}
