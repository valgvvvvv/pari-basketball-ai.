import datetime as dt
import io
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import zipfile

API = "https://api.github.com"
REPO = os.environ["GITHUB_REPOSITORY"]
TOKEN = os.environ["GITHUB_TOKEN"]
NOW = dt.datetime.now(dt.timezone.utc)
CUTOFF = NOW - dt.timedelta(hours=24)
HEADERS = {
    "Authorization": "Bearer " + TOKEN,
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "PARI-Basketball-AI-Archive",
}


def api_json(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, headers={**HEADERS, "Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=90) as response:
        raw = response.read()
        return json.loads(raw.decode("utf-8")) if raw else {}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def download_artifact(url):
    req = urllib.request.Request(url, headers=HEADERS)
    opener = urllib.request.build_opener(NoRedirect)
    try:
        response = opener.open(req, timeout=90)
        return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code not in (301, 302, 303, 307, 308):
            raise
        signed_url = exc.headers.get("Location")
        if not signed_url:
            raise RuntimeError("GitHub did not return a signed artifact download URL")
    # The signed URL is temporary and already authorized; do not forward the GitHub token.
    with urllib.request.urlopen(signed_url, timeout=180) as response:
        return response.read()


def main():
    since = CUTOFF.date().isoformat()
    runs = []
    page = 1
    while True:
        query = urllib.parse.urlencode({"per_page": 100, "page": page, "created": ">=" + since})
        data = api_json(f"/repos/{REPO}/actions/workflows/autonomous-archive.yml/runs?{query}")
        batch = data.get("workflow_runs", [])
        runs.extend(batch)
        if len(batch) < 100 or page >= 10:
            break
        page += 1

    artifacts = []
    for run in runs:
        created = dt.datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
        if created < CUTOFF or run.get("conclusion") != "success":
            continue
        result = api_json(f"/repos/{REPO}/actions/runs/{run['id']}/artifacts?per_page=100")
        for artifact in result.get("artifacts", []):
            if artifact.get("expired") or not artifact.get("archive_download_url"):
                continue
            artifacts.append({
                "id": artifact["id"],
                "name": artifact["name"],
                "created_at": artifact["created_at"],
                "url": artifact["archive_download_url"],
            })

    if not artifacts:
        print("No successful collector artifacts found in the last 24 hours; nothing to publish.")
        return

    tag = "pari-archive-" + NOW.strftime("%Y%m%d")
    filename = "pari-basketball-archive-" + NOW.strftime("%Y%m%d-%H%M%S") + ".zip"
    output = io.BytesIO()
    manifest = {
        "format": "pari-basketball-daily-archive",
        "version": 1,
        "created_at_utc": NOW.isoformat(),
        "period_start_utc": CUTOFF.isoformat(),
        "period_end_utc": NOW.isoformat(),
        "snapshot_artifacts": len(artifacts),
        "source": "PARI live feed collected by scheduled GitHub Actions workflow",
        "contents": "Each snapshots/*.json.gz file contains normalized LIVE events, model outputs, immutable prediction contracts, source provenance and diagnostics.",
    }

    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as daily:
        daily.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for index, artifact in enumerate(sorted(artifacts, key=lambda x: x["created_at"])):
            blob = download_artifact(artifact["url"])
            with zipfile.ZipFile(io.BytesIO(blob), "r") as source:
                candidates = [name for name in source.namelist() if name.endswith(".json.gz")]
                for member in candidates:
                    daily.writestr(f"snapshots/{artifact['created_at'].replace(':', '').replace('-', '')}-{index:04d}.json.gz",
                                   source.read(member))

    zip_bytes = output.getvalue()
    if len(zip_bytes) < 100:
        raise RuntimeError("Daily archive unexpectedly small; refusing to publish")

    try:
        release = api_json(f"/repos/{REPO}/releases/tags/{tag}")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        release = api_json(f"/repos/{REPO}/releases", method="POST", body={
            "tag_name": tag,
            "name": "Архив матчей PARI — " + NOW.strftime("%Y-%m-%d"),
            "body": "Ежедневный сжатый архив LIVE-снимков PARI. Содержит счёт, время, линии, коэффициенты, доступную статистику и диагностические поля источников. Данные публичны.",
            "draft": False,
            "prerelease": False,
        })

    # Keep every successfully published archive immutable. Re-runs create a
    # timestamped asset instead of deleting or replacing an earlier backup.
    upload_base = release["upload_url"].split("{", 1)[0]
    upload_url = upload_base + "?" + urllib.parse.urlencode({"name": filename})
    req = urllib.request.Request(upload_url, data=zip_bytes, headers={
        **HEADERS, "Content-Type": "application/zip"
    }, method="POST")
    with urllib.request.urlopen(req, timeout=240) as response:
        uploaded = json.loads(response.read().decode("utf-8"))

    # Once the permanent release asset is confirmed, remove the temporary
    # workflow artifacts so the Actions storage quota does not grow forever.
    deleted = 0
    for artifact in artifacts:
        api_json(f"/repos/{REPO}/actions/artifacts/{artifact['id']}", method="DELETE")
        deleted += 1

    print(json.dumps({
        "published": True,
        "tag": tag,
        "asset": uploaded.get("browser_download_url"),
        "snapshots": len(artifacts),
        "temporary_artifacts_deleted": deleted,
        "archive_bytes": len(zip_bytes),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
