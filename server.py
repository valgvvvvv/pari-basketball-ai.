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
                        if ".js" in href.lower() or "modulepreload" in rel or ("preload" in rel and link.get("as") == "script"):
                            refs.append({"url": urljoin(str(response.url), href), "kind": "preload"})
                    for ref in refs:
                        asset_refs.append(ref)
                    for inline in parser.inline_scripts:
                        inline_hints.extend(find_hints(inline))
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

        hints = list(dict.fromkeys(inline_hints + bundle_hints))
        return {
            "ok": any(x.get("status") == 200 for x in pages),
            "diagnostic_version": 2, "pages": pages,
            "asset_refs_found": len(asset_refs), "first_party_assets_scanned": len(scanned),
            "scanned_assets": scanned, "inline_route_hints": list(dict.fromkeys(inline_hints))[:100],
            "route_candidates": hints[:200], "confirmed_api": False,
            "note": "URL-кандидаты не являются подтверждённым API. Для подтверждения нужны проверка запроса, структуры ответа и повторное получение актуального LIVE-счёта/линий."
        }
    except httpx.TimeoutException:
        return {"ok": False, "diagnostic_version": 2, "error": "PARI diagnostic timed out"}
    except httpx.HTTPError as exc:
        return {"ok": False, "diagnostic_version": 2, "error": type(exc).__name__}
