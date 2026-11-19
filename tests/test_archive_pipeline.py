import datetime as dt
import gzip
import hashlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '.github/scripts'))
import publish_daily_archive as publisher
from restore_daily_archive import restore_archive


def snapshot_artifact(payload=None):
    payload = payload if payload is not None else {
        'events': [{'id': 'game-1', 'clock': '04:30'}],
        'source_checked_at_utc': '2026-11-15T12:00:00+00:00',
        'forecasts': [{'prediction_contracts': [{'contract_id': 'original'}], 'errors': []}]}
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w') as archive:
        archive.writestr('capture.json.gz', gzip.compress(json.dumps(payload).encode()))
    return result.getvalue()


class ArchivePipelineTests(unittest.TestCase):
    def artifact(self, identifier=1):
        return {'id': identifier, 'name': 'pari-live-snapshot-123-1', 'expired': False,
                'created_at': '2026-11-15T12:00:00Z', 'archive_download_url': 'https://api.github.com/artifact/1'}

    def test_backfill_includes_old_and_failed_run_artifacts_and_skips_inventory(self):
        with patch.object(publisher, 'pages', return_value=iter([
            self.artifact(1), self.artifact(2), {**self.artifact(3), 'expired': True},
            {**self.artifact(4), 'name': 'unrelated'}])):
            self.assertEqual([a['id'] for a in publisher.pending_artifacts({2})], [1])

    def test_roundtrip_preserves_source_and_contracts_and_actual_time_coverage(self):
        now = dt.datetime(2026, 11, 20, tzinfo=dt.timezone.utc)
        with patch.object(publisher, 'download_artifact', return_value=snapshot_artifact()):
            blob, manifest = publisher.build_archive([self.artifact()], now)
        self.assertEqual(manifest['period_start_utc'], '2026-11-15T12:00:00+00:00')
        self.assertEqual(manifest['immutable_prediction_contracts'], 1)
        self.assertEqual(manifest['artifact_ids'], [1])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'daily.zip'
            path.write_bytes(blob)
            output = Path(directory) / 'restored'
            self.assertEqual(restore_archive(path, output)['snapshots_restored'], 1)
            self.assertEqual(restore_archive(path, output)['snapshots_already_present'], 1)
            payload = json.loads(gzip.decompress(next(output.glob('*.json.gz')).read_bytes()))
            self.assertEqual(payload['events'][0]['clock'], '04:30')
            self.assertEqual(payload['forecasts'][0]['prediction_contracts'][0]['contract_id'], 'original')

    def test_empty_live_list_is_a_valid_heartbeat(self):
        with patch.object(publisher, 'download_artifact', return_value=snapshot_artifact({'events': []})):
            _, manifest = publisher.build_archive([self.artifact()], dt.datetime.now(dt.timezone.utc))
        self.assertEqual(manifest['unique_event_ids_in_window'], 0)

    def test_invalid_artifact_never_becomes_daily_archive(self):
        with patch.object(publisher, 'download_artifact', return_value=snapshot_artifact({'error': 'upstream'})):
            with self.assertRaises(ValueError):
                publisher.build_archive([self.artifact()], dt.datetime.now(dt.timezone.utc))

    def test_corruption_in_later_file_does_not_restore_earlier_file(self):
        compressed = gzip.compress(json.dumps({'events': []}).encode())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, output = root/'bad.zip', root/'out'
            files = [{'path': 'snapshots/one.json.gz', 'sha256': hashlib.sha256(compressed).hexdigest()},
                     {'path': 'snapshots/two.json.gz', 'sha256': 'bad'}]
            with zipfile.ZipFile(path, 'w') as archive:
                for item in files:
                    archive.writestr(item['path'], compressed)
                archive.writestr('manifest.json', json.dumps({'format': 'pari-basketball-daily-archive',
                    'version': 3, 'snapshot_files': 2, 'files': files}))
            with self.assertRaises(ValueError):
                restore_archive(path, output)
            self.assertFalse(output.exists())

    def test_v3_requires_checksums_for_every_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root/'bad.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('snapshots/one.json.gz', gzip.compress(b'{"events": []}'))
                archive.writestr('manifest.json', json.dumps({'format': 'pari-basketball-daily-archive',
                    'version': 3, 'snapshot_files': 1, 'files': []}))
            with self.assertRaises(ValueError):
                restore_archive(path, root/'out')

    def test_failed_remote_verification_never_marks_artifacts_archived(self):
        with patch.dict('os.environ', {'GITHUB_RUN_ID': 'test'}), \
             patch.object(publisher, 'REPO', 'owner/repo'), patch.object(publisher, 'TOKEN', 'test'), \
             patch.object(publisher, 'archived_artifact_ids', return_value=set()), \
             patch.object(publisher, 'pending_artifacts', return_value=[self.artifact()]), \
             patch.object(publisher, 'build_archive', return_value=(b'ZIP', {'artifact_ids': [1]})), \
             patch.object(publisher, 'api_json', return_value={'upload_url': 'https://uploads.github.com/x'}), \
             patch.object(publisher, 'upload_asset', return_value={'browser_download_url': 'https://github.com/x'}) as upload, \
             patch.object(publisher, 'download_artifact', return_value=b'CORRUPT'):
            with self.assertRaises(ValueError):
                publisher.main()
            self.assertEqual(upload.call_count, 1, 'No inventory may be written after verification failure')


if __name__ == '__main__':
    unittest.main()
