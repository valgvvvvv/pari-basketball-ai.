import json
import tempfile
import unittest
from pathlib import Path

import server


class ForecastApiRegressionTests(unittest.TestCase):
    def test_clock_parser_uses_seconds(self):
        self.assertEqual(server.parse_clock("08:57"), 537)
        self.assertEqual(server.parse_clock("12:00"), 720)
        self.assertEqual(server.parse_clock("20:00"), 1200)

    def test_early_q3_uses_completed_period_prior_not_first_minute_extrapolation(self):
        req = server.QuarterAnalyzeRequest(
            quarter=3, score1=1, score2=0, clock="01:03",
            period_minutes=10, prior_quarter_totals=[41, 42],
            total_line=40.5, simulations=20000
        )
        result = server.api_analyze_quarter(req)
        self.assertTrue(result["ready"])
        self.assertEqual(result["elapsed_seconds"], 63)
        self.assertGreater(result["expected_total"], 25)
        self.assertLess(result["expected_total"], 50)
        self.assertEqual(result["distribution"], "Gamma-Poisson predictive")
        self.assertEqual(result["overtime_included"], False)
        self.assertAlmostEqual(result["p_over_mc"] + result["p_under_mc"] + result["p_push_mc"], 100, places=1)
        self.assertEqual(result["p_push_mc"], 0)

    def test_zero_zero_at_period_start_uses_prior(self):
        req = server.QuarterAnalyzeRequest(
            quarter=2, score1=0, score2=0, clock="00:00",
            period_minutes=10, prior_quarter_totals=[40],
            total_line=40, simulations=12000
        )
        result = server.api_analyze_quarter(req)
        self.assertTrue(result["ready"])
        self.assertGreater(result["expected_total"], 20)
        self.assertGreater(result["p_push_mc"], 0)

    def test_early_period_without_prior_is_not_projected(self):
        req = server.QuarterAnalyzeRequest(
            quarter=1, score1=0, score2=0, clock="00:45",
            period_minutes=10, total_line=40.5, simulations=10000
        )
        result = server.api_analyze_quarter(req)
        self.assertFalse(result["ready"])
        self.assertIn("Недостаточно данных", result["reason"])

    def test_two_halves_of_twenty_minutes_are_supported_explicitly(self):
        req = server.AnalyzeRequest(
            score1=40, score2=38, period=2, clock="19:00",
            period_minutes=20, period_count=2, total_line=150.5,
            simulations=1000
        )
        result = server.analyze(req)
        self.assertEqual(result["elapsed_minutes"], 39.0)
        self.assertEqual(result["remaining_minutes"], 1.0)

    def test_four_by_twelve_minutes_are_supported_explicitly(self):
        req = server.AnalyzeRequest(
            score1=45, score2=43, period=2, clock="11:00",
            period_minutes=12, period_count=4, total_line=180.5,
            simulations=1000
        )
        result = server.analyze(req)
        self.assertEqual(result["elapsed_minutes"], 23.0)
        self.assertEqual(result["remaining_minutes"], 25.0)
        self.assertEqual(result["remaining_minutes"], 25.0)


class ArchiveSnapshotImmutabilityTests(unittest.TestCase):
    def test_changed_score_creates_new_snapshot_and_keeps_old_payload(self):
        original_db = server._ARCHIVE_DB
        with tempfile.TemporaryDirectory() as directory:
            server._ARCHIVE_DB = Path(directory) / "archive.sqlite3"
            try:
                first = {"events": [{
                    "id": "fixture-1", "team1": "A", "team2": "B",
                    "score": [20, 18], "period": 2, "clock": "04:00",
                    "status": "LIVE"
                }]}
                second = {"events": [{
                    "id": "fixture-1", "team1": "A", "team2": "B",
                    "score": [22, 18], "period": 2, "clock": "03:20",
                    "status": "LIVE"
                }]}
                self.assertEqual(server._archive_save_events(first), (1, 1))
                self.assertEqual(server._archive_save_events(second), (1, 1))
                conn = server._archive_connect()
                try:
                    rows = conn.execute(
                        "SELECT score FROM snapshots WHERE event_id=? ORDER BY id",
                        ("fixture-1",)
                    ).fetchall()
                    self.assertEqual(len(rows), 2)
                    self.assertEqual([json.loads(row[0]) for row in rows], [[20, 18], [22, 18]])
                    count = conn.execute(
                        "SELECT snapshots FROM matches WHERE event_id=?", ("fixture-1",)
                    ).fetchone()[0]
                    self.assertEqual(count, 2)
                finally:
                    conn.close()
            finally:
                server._ARCHIVE_DB = original_db


if __name__ == "__main__":
    unittest.main()
