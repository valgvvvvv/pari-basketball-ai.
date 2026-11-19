import hashlib
import datetime as dt
import io
import gzip
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
    filename = "pari-basketball-archive-" + NOW.strftime("%Y%m%d-%H%M%S") + "-" + os.getenv("GITHUB_RUN_ID", str(int(NOW.timestamp()))) + ".zip"
    output = io.BytesIO()
    manifest = {
        "format": "pari-basketball-daily-archive",
        "version": 1,
        "created_at_utc": NOW.isoformat(),
        "period_start_utc": CUTOFF.isoformat(),
        "period_end_utc": NOW.isoformat(),
        "snapshot_artifacts": len(artifacts),
        "source": "PARI live feed collected by scheduled GitHub Actions workflow",
        "contents": "Each snapshots/*.json.gz file is a compressed JSON snapshot containing the complete normalized event records returned by the project's PARI adapter.",
    }

    unique_events = set()
    forecast_count = 0
    prediction_contract_count = 0
    forecast_error_count = 0
    snapshot_file_count = 0
    file_entries = []
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as daily:
        for index, artifact in enumerate(sorted(artifacts, key=lambda x: x["created_at"])):
            blob = download_artifact(artifact["url"])
            with zipfile.ZipFile(io.BytesIO(blob), "r") as source:
                candidates = [name for name in source.namelist() if name.endswith(".json.gz")]
                if not candidates:
                    raise RuntimeError("Collector artifact contains no compressed JSON snapshots: " + artifact["name"])
                for member in candidates:
                    compressed = source.read(member)
                    try:
                        payload = json.loads(gzip.decompress(compressed).decode("utf-8"))
                    except Exception as exc:
                        raise RuntimeError("Invalid collector snapshot " + member + ": " + str(exc))
                    unique_events.update(str(row.get("id")) for row in payload.get("events", [])
                                         if isinstance(row, dict) and row.get("id") is not None)
                    forecasts = payload.get("forecasts", [])
                    forecast_count += len(forecasts)
                    prediction_contract_count += sum(
                        len(row.get("prediction_contracts", []))
                        for row in forecasts if isinstance(row, dict)
                    )
                    forecast_error_count += sum(
                        len(row.get("errors", []))
                        for row in forecasts if isinstance(row, dict)
                    )
                    archive_path = f"snapshots/{artifact['created_at'].replace(':', '').replace('-', '')}-{index:04d}-{snapshot_file_count:04d}.json.gz"
                    daily.writestr(archive_path, compressed)
                    file_entries.append({
                        "path": archive_path,
                        "sha256": hashlib.sha256(compressed).hexdigest(),
                        "bytes": len(compressed)
                    })
                    snapshot_file_count += 1
        manifest.update({
            "version": 2,
            "snapshot_files": snapshot_file_count,
            "unique_event_ids_in_window": len(unique_events),
            "forecast_records": forecast_count,
            "immutable_prediction_contracts": prediction_contract_count,
            "forecast_errors": forecast_error_count,
            "files": file_entries,
            "contents": "Each snapshots/*.json.gz file contains normalized source events, timestamped forecast snapshots and immutable versioned prediction contracts. Final outcomes remain unresolved unless independently confirmed."
        })
        daily.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

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

    # Keep every previously published archive. Each run gets a unique timestamped asset name.
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
        "snapshot_artifacts": len(artifacts),
        "snapshot_files": snapshot_file_count,
        "unique_event_ids": len(unique_events),
        "forecasts": forecast_count,
        "prediction_contracts": prediction_contract_count,
        "forecast_errors": forecast_error_count,
        "temporary_artifacts_deleted": deleted,
        "archive_bytes": len(zip_bytes),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
