#!/usr/bin/env python3
"""Validate and restore immutable snapshots from a PARI daily ZIP archive.

Never overwrites or deletes existing restored files.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import zipfile
from pathlib import Path, PurePosixPath


def restore_archive(archive_path: str | Path, output_dir: str | Path) -> dict:
    archive_path = Path(archive_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    restored = skipped = 0
    with zipfile.ZipFile(archive_path, "r") as archive:
        corrupt = archive.testzip()
        if corrupt:
            raise ValueError(f"ZIP CRC validation failed: {corrupt}")
        try:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        except Exception as exc:
            raise ValueError(f"Archive manifest is missing or invalid: {exc}") from exc
        if manifest.get("format") != "pari-basketball-daily-archive":
            raise ValueError("Unsupported archive format")
        checksums = {
            item.get("path"): item.get("sha256")
            for item in manifest.get("files", [])
            if isinstance(item, dict) and item.get("path") and item.get("sha256")
        }
        members = sorted(
            name for name in archive.namelist()
            if name.startswith("snapshots/") and name.endswith(".json.gz")
        )
        if manifest.get("snapshot_files") is not None and len(members) != int(manifest["snapshot_files"]):
            raise ValueError("Snapshot count does not match manifest")
        for member in members:
            posix = PurePosixPath(member)
            if posix.is_absolute() or ".." in posix.parts or len(posix.parts) != 2:
                raise ValueError(f"Unsafe archive member path: {member}")
            compressed = archive.read(member)
            expected_sha = checksums.get(member)
            if expected_sha and hashlib.sha256(compressed).hexdigest() != expected_sha:
                raise ValueError(f"SHA-256 mismatch: {member}")
            try:
                payload = json.loads(gzip.decompress(compressed).decode("utf-8"))
            except Exception as exc:
                raise ValueError(f"Invalid compressed snapshot {member}: {exc}") from exc
            if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
                raise ValueError(f"Snapshot has no event list: {member}")
            destination = output_dir / posix.name
            if destination.exists():
                if hashlib.sha256(destination.read_bytes()).hexdigest() == hashlib.sha256(compressed).hexdigest():
                    skipped += 1
                    continue
                raise FileExistsError(f"Refusing to overwrite different existing file: {destination}")
            destination.write_bytes(compressed)
            restored += 1
        manifest_target = output_dir / "manifest.json"
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        if manifest_target.exists() and manifest_target.read_bytes() != manifest_bytes:
            raise FileExistsError(f"Refusing to overwrite different manifest: {manifest_target}")
        if not manifest_target.exists():
            manifest_target.write_bytes(manifest_bytes)
    return {
        "archive": str(archive_path),
        "output_dir": str(output_dir),
        "snapshots_restored": restored,
        "snapshots_already_present": skipped,
        "snapshots_in_archive": restored + skipped,
        "manifest_version": manifest.get("version"),
        "integrity": "verified",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", help="Path to downloaded daily ZIP archive")
    parser.add_argument("--output", default="pari-archive-restored", help="Restore directory (never overwritten)")
    args = parser.parse_args()
    print(json.dumps(restore_archive(args.archive, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
