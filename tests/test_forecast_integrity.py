import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class ForecastIntegrityTests(unittest.TestCase):
    def test_clock_is_parsed_as_exact_seconds(self):
        self.assertEqual(server.parse_clock("08:57"), 537)
        self.assertEqual(server.parse_clock("00:00"), 0)
        with self.assertRaises(ValueError):
            server.parse_clock("08:75")

    def test_manual_quarter_does_not_extrapolate_first_minute(self):
        req = server.QuarterAnalyzeRequest(
            quarter=3, score1=1, score2=0, clock="01:03",
            period_minutes=10, total_line=40.5, simulations=1000
        )
        result = server.api_analyze_quarter(req)
        self.assertFalse(result["ready"])
        self.assertEqual(result["status"], "insufficient_data")
        self.assertIn("Первые 3 минуты", result["reason"])

    def test_manual_api_supports_two_halves_of_twenty_minutes(self):
        req = server.AnalyzeRequest(
            team1="Home", team2="Away", score1=40, score2=38,
            period=2, period_count=2, clock="01:00",
            period_minutes=20, total_line=150, simulations=1000
        )
        result = server.analyze(req)
        self.assertEqual(result["elapsed_minutes"], 21.0)
        self.assertEqual(result["remaining_minutes"], 19.0)
        self.assertEqual(result["period_count"], 2)
        self.assertEqual(result["overtime_policy"], "not_modelled")
        self.assertAlmostEqual(
            result["p_over_mc"] + result["p_under_mc"] + result["p_push_mc"], 100.0, places=1
        )

    def test_raw_archive_snapshots_are_append_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "archive.sqlite3"
            with patch.object(server, "_ARCHIVE_DB", db):
                first = {"events": [{
                    "id": "fixed-match", "team1": "Nanjing", "team2": "Xinjiang",
                    "score": [51, 48], "period": 3, "clock": "08:57"
                }]}
                self.assertEqual(server._archive_save_events(first), (1, 1))
                self.assertEqual(server._archive_save_events(first), (1, 0))
                changed = {"events": [{
                    "id": "fixed-match", "team1": "Nanjing", "team2": "Xinjiang",
                    "score": [52, 48], "period": 3, "clock": "08:31"
                }]}
                self.assertEqual(server._archive_save_events(changed), (1, 1))
                conn = server._archive_connect()
                try:
                    rows = conn.execute(
                        "SELECT score,payload_sha FROM snapshots WHERE event_id=? ORDER BY id",
                        ("fixed-match",)
                    ).fetchall()
                    count = conn.execute(
                        "SELECT snapshots FROM matches WHERE event_id=?",
                        ("fixed-match",)
                    ).fetchone()[0]
                finally:
                    conn.close()
                self.assertEqual(len(rows), 2)
                self.assertEqual(count, 2)
                self.assertNotEqual(rows[0][1], rows[1][1])


if __name__ == "__main__":
    unittest.main()
