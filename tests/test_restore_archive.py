import gzip
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import importlib.util

restore_path = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "restore_daily_archive.py"
spec = importlib.util.spec_from_file_location("restore_daily_archive", restore_path)
restore_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(restore_module)
restore_archive = restore_module.restore_archive


class RestoreArchiveTests(unittest.TestCase):
    def test_valid_archive_restores_gzip_snapshots_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "daily.zip"
            output = root / "restored"
            snapshot = gzip.compress(json.dumps({"event_id":"e1","score":[50,48]}).encode())
            with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("manifest.json", json.dumps({
                    "format":"pari-basketball-daily-archive","version":1
                }))
                archive.writestr("snapshots/20261119-0001.json.gz", snapshot)
            self.assertEqual(restore_archive(archive_path, output), 1)
            self.assertEqual(json.loads(gzip.decompress((output / "20261119-0001.json.gz").read_bytes())), {
                "event_id":"e1","score":[50,48]
            })
            with self.assertRaises(FileExistsError):
                restore_archive(archive_path, output)

    def test_rejects_unsafe_archive_member(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "bad.zip"
            with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("manifest.json", json.dumps({
                    "format":"pari-basketball-daily-archive","version":1
                }))
                archive.writestr("snapshots/../outside.json.gz", gzip.compress(b"{}"))
            with self.assertRaises(ValueError):
                restore_archive(archive_path, root / "restored")


if __name__ == "__main__":
    unittest.main()
