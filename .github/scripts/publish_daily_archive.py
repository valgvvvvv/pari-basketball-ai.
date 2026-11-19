import hashlib
import datetime as dt
import gzip
import io
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import zipfile

API = "https://api.github.com"
REPO = os.environ.get("GITHUB_REPOSITORY", "")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
           "User-Agent": "PARI-Basketball-AI-Archive"}
if TOKEN:
    HEADERS["Authorization"] = "Bearer " + TOKEN


def api_json(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(API + path, data=data,
        headers={**HEADERS, "Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(request, timeout=90) as response:
        raw = response.read()
        return json.loads(raw.decode()) if raw else {}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def download_artifact(url):
    # Do not forward the GitHub token to signed storage URLs or other hosts.
    headers = HEADERS if urllib.parse.urlparse(url).hostname == "api.github.com" else {
        "User-Agent": HEADERS["User-Agent"]}
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(urllib.request.Request(url, headers=headers), timeout=90) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code not in (301, 302, 303, 307, 308):
            raise
        signed_url = exc.headers.get("Location")
        if not signed_url or urllib.parse.urlparse(signed_url).scheme != "https":
            raise RuntimeError("No valid HTTPS artifact download URL")
    with urllib.request.urlopen(signed_url, timeout=180) as response:
        return response.read()


def pages(path, key=None):
    page = 1
    while True:
        separator = "&" if "?" in path else "?"
        data = api_json(f"{path}{separator}per_page=100&page={page}")
        batch = data.get(key, []) if key else data
        yield from batch
        if len(batch) < 100:
            return
        page += 1


def archived_artifact_ids():
    archived = set()
    for release in pages(f"/repos/{REPO}/releases"):
        if not str(release.get("tag_name", "")).startswith("pari-archive-"):
            continue
        for asset in pages(release["assets_url"].removeprefix(API)):
            if not asset.get("name", "").startswith("pari-inventory-"):
                continue
            inventory = json.loads(download_artifact(asset["browser_download_url"]))
            if inventory.get("format") != "pari-archive-inventory" or not inventory.get("verified"):
                raise ValueError("Invalid inventory; refusing to skip source snapshots")
            archived.update(int(value) for value in inventory["artifact_ids"])
    return archived


def pending_artifacts(archived):
    # No 24h cutoff: recover every retained capture, including failed-run artifacts.
    return sorted((item for item in pages(f"/repos/{REPO}/actions/artifacts", "artifacts")
        if item.get("name", "").startswith("pari-live-snapshot-")
        and not item.get("expired") and item.get("archive_download_url")
        and int(item["id"]) not in archived), key=lambda item: (item["created_at"], item["id"]))


def build_archive(artifacts, now):
    output = io.BytesIO()
    files, timestamps, events = [], [], set()
    forecasts = contracts = errors = 0
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as daily:
        for artifact in artifacts:
            with zipfile.ZipFile(io.BytesIO(download_artifact(artifact["archive_download_url"]))) as source:
                if source.testzip():
                    raise ValueError("Invalid artifact ZIP")
                members = [item for item in source.infolist() if item.filename.endswith(".json.gz")]
                if not members:
                    raise ValueError("Artifact contains no snapshots: " + str(artifact["id"]))
                for index, member in enumerate(members):
                    compressed = source.read(member)
                    payload = json.loads(gzip.decompress(compressed).decode("utf-8"))
                    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
                        raise ValueError("Snapshot has no event list")
                    stamp = payload.get("source_checked_at_utc") or payload.get("captured_at_utc") or artifact["created_at"]
                    timestamps.append(dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")))
                    events.update(str(row["id"]) for row in payload["events"] if isinstance(row, dict) and row.get("id") is not None)
                    rows = payload.get("forecasts", [])
                    forecasts += len(rows)
                    contracts += sum(len(row.get("prediction_contracts", [])) for row in rows)
                    errors += sum(len(row.get("errors", [])) for row in rows)
                    archive_path = f"snapshots/artifact-{artifact['id']}-{index:04d}.json.gz"
                    daily.writestr(archive_path, compressed)
                    files.append({"path": archive_path, "sha256": hashlib.sha256(compressed).hexdigest(),
                        "bytes": len(compressed), "artifact_id": artifact["id"], "source_checked_at_utc": stamp})
        manifest = {"format": "pari-basketball-daily-archive", "version": 3,
            "created_at_utc": now.isoformat(), "period_start_utc": min(timestamps).isoformat(),
            "period_end_utc": max(timestamps).isoformat(), "snapshot_artifacts": len(artifacts),
            "artifact_ids": [a["id"] for a in artifacts], "snapshot_files": len(files),
            "unique_event_ids_in_window": len(events), "forecast_records": forecasts,
            "immutable_prediction_contracts": contracts, "forecast_errors": errors, "files": files,
            "source": "PARI live feed collected by scheduled GitHub Actions workflow",
            "contents": "Immutable source snapshots and forecasts; outcomes remain unresolved until independently confirmed."}
        daily.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return output.getvalue(), manifest


def upload_asset(release, filename, data, content_type):
    url = release["upload_url"].split("{", 1)[0] + "?" + urllib.parse.urlencode({"name": filename})
    request = urllib.request.Request(url, data=data, headers={**HEADERS, "Content-Type": content_type}, method="POST")
    with urllib.request.urlopen(request, timeout=240) as response:
        return json.loads(response.read())


def main():
    if not REPO or not TOKEN:
        raise RuntimeError("GITHUB_REPOSITORY and GITHUB_TOKEN are required (provided by GitHub Actions)")
    now = dt.datetime.now(dt.timezone.utc)
    pending = pending_artifacts(archived_artifact_ids())
    if not pending:
        print("No unarchived retained snapshots; nothing to publish.")
        return
    artifacts = pending[:int(os.getenv("MAX_ARCHIVE_ARTIFACTS", "350"))]
    if not artifacts:
        raise ValueError("MAX_ARCHIVE_ARTIFACTS must be positive")
    zip_bytes, manifest = build_archive(artifacts, now)
    tag = "pari-archive-" + now.strftime("%Y%m%d")
    stamp = now.strftime("%Y%m%d-%H%M%S") + "-" + os.getenv("GITHUB_RUN_ID", str(int(now.timestamp())))
    filename = "pari-basketball-archive-" + stamp + ".zip"
    try:
        release = api_json(f"/repos/{REPO}/releases/tags/{tag}")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        release = api_json(f"/repos/{REPO}/releases", method="POST", body={
            "tag_name": tag, "name": "Архив матчей PARI — " + now.strftime("%Y-%m-%d"),
            "body": "LIVE-снимки, таймеры, статистика и исходные прогнозы. Охват и SHA-256 — в manifest.json. Непроверенные исходы остаются unresolved.",
            "draft": False, "prerelease": False})
    uploaded = upload_asset(release, filename, zip_bytes, "application/zip")
    expected_sha = hashlib.sha256(zip_bytes).hexdigest()
    if hashlib.sha256(download_artifact(uploaded["browser_download_url"])).hexdigest() != expected_sha:
        raise ValueError("Published ZIP failed download/SHA-256 verification; source artifacts retained")
    inventory = {"format": "pari-archive-inventory", "version": 1, "verified": True,
        "zip_name": filename, "zip_sha256": expected_sha,
        "artifact_ids": manifest["artifact_ids"], "created_at_utc": now.isoformat()}
    inventory_bytes = json.dumps(inventory, indent=2).encode()
    inventory_asset = upload_asset(release, "pari-inventory-" + stamp + ".json", inventory_bytes, "application/json")
    if download_artifact(inventory_asset["browser_download_url"]) != inventory_bytes:
        raise ValueError("Published inventory verification failed; source artifacts retained")
    # Never delete source artifacts or existing release assets; Actions manages retention.
    print(json.dumps({"published": True, "asset": uploaded["browser_download_url"],
        "sha256": expected_sha, "snapshot_artifacts": len(artifacts),
        "backlog_remaining": len(pending) - len(artifacts), "snapshot_files": manifest["snapshot_files"],
        "prediction_contracts": manifest["immutable_prediction_contracts"],
        "forecast_errors": manifest["forecast_errors"], "source_artifacts_deleted": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
