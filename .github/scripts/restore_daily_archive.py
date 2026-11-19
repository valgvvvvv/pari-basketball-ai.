#!/usr/bin/env python3
"""Validate and extract a PARI daily archive without overwriting existing files."""
import argparse
import gzip
import json
from pathlib import Path, PurePosixPath
import zipfile


def restore_archive(archive_path: Path, output_dir: Path) -> int:
    archive_path = Path(archive_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    with zipfile.ZipFile(archive_path, "r") as archive:
        try:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Archive manifest is missing or invalid") from exc
        if manifest.get("format") != "pari-basketball-daily-archive" or manifest.get("version") != 1:
            raise ValueError("Unsupported archive format or version")
        for member in archive.infolist():
            if member.is_dir() or not member.filename.startswith("snapshots/"):
                continue
            relative = PurePosixPath(member.filename)
            if relative.is_absolute() or ".." in relative.parts or len(relative.parts) != 2:
                raise ValueError("Unsafe archive member path")
            if not relative.name.endswith(".json.gz"):
                continue
            payload = archive.read(member)
            try:
                json.loads(gzip.decompress(payload).decode("utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"Invalid compressed snapshot: {relative.name}") from exc
            destination = output_dir / relative.name
            if destination.exists():
                raise FileExistsError(f"Refusing to overwrite existing file: {destination}")
            with destination.open("xb") as stream:
                stream.write(payload)
            written += 1
    return written


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--output", type=Path, default=Path("restored-pari-archive"))
    args = parser.parse_args()
    count = restore_archive(args.archive, args.output)
    print(json.dumps({"restored_snapshots": count, "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
