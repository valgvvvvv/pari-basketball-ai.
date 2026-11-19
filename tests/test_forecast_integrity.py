import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server
from result_validation import settle_prediction
from match_identity import match_context_compatible


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

    def test_settlement_requires_confirmed_identity_and_overtime_rule(self):
        contract = {"event_id": "game-1", "snapshot_id": "snap-1", "target": {"type": "match"}, "market": {"lines": [{"line": 205}]}}
        base = {
            "event_id": "game-1", "final_status": "finished", "confirmed": True,
            "identity_verified": True, "independent_confirmation": True, "source": "independent-final-feed",
            "regulation_total": 200, "total_with_overtime": 210, "line": 205,
            "includes_overtime": True
        }
        self.assertEqual(settle_prediction(contract, base)["result"], "over_won")
        self.assertEqual(settle_prediction(contract, {**base, "includes_overtime": False})["result"], "under_won")
        self.assertEqual(settle_prediction(contract, {**base, "total_with_overtime": 205})["result"], "push")
        self.assertEqual(settle_prediction(contract, {**base, "includes_overtime": None})["status"], "unresolved")
        self.assertEqual(settle_prediction(contract, {**base, "identity_verified": False})["status"], "unresolved")
        self.assertEqual(settle_prediction(contract, {**base, "event_id": "different"})["status"], "disputed")

    def test_period_settlement_requires_exact_period(self):
        contract = {"event_id": "game-2", "snapshot_id": "snap-2",
                    "target": {"type": "period", "number": 3}, "market": {"lines": [{"line": 42}]}}
        final = {"event_id": "game-2", "final_status": "finished", "confirmed": True,
                 "identity_verified": True, "independent_confirmation": True, "source": "official-feed",
                 "period_number": 3, "period_total": 42, "line": 42}
        self.assertEqual(settle_prediction(contract, final)["result"], "push")
        self.assertEqual(settle_prediction(contract, {**final, "period_number": 2})["status"], "disputed")

    def test_daily_release_publisher_never_deletes_previous_release_assets(self):
        root = Path(__file__).resolve().parents[1]
        publisher = (root / ".github/scripts/publish_daily_archive.py").read_text(encoding="utf-8")
        collector = (root / ".github/scripts/collect_live_with_models.cjs").read_text(encoding="utf-8")
        self.assertIn('strftime("%Y%m%d-%H%M%S")', publisher)
        self.assertNotIn('/releases/assets/', publisher)
        self.assertIn("prediction_contracts", collector)
        self.assertIn("snapshot_id", collector)
        self.assertIn('status: "unresolved"', collector)

    def test_external_match_requires_competition_gender_date_and_format_context(self):
        pari = {
            "team1": "Lions", "team2": "Tigers", "league": "CBA",
            "start_time": "2026-11-19T10:00:00Z",
            "period_length_seconds": 600, "period_count": 4
        }
        same = {
            "home": "Lions", "away": "Tigers", "league": "CBA",
            "start_time": "2026-11-19T10:15:00Z",
            "period_length_seconds": 600, "period_count": 4
        }
        self.assertTrue(match_context_compatible(pari, same))
        self.assertFalse(match_context_compatible(pari, {**same, "league": "Women's CBA"}))
        self.assertFalse(match_context_compatible(pari, {**same, "league": "U19 CBA"}))
        self.assertFalse(match_context_compatible(pari, {**same, "period_length_seconds": 720}))
        self.assertFalse(match_context_compatible(pari, {**same, "league": "EuroLeague"}))
        self.assertFalse(match_context_compatible(pari, {**same, "start_time": "2026-11-22T10:00:00Z"}))
        self.assertFalse(match_context_compatible(
            {"team1": "Lions", "team2": "Tigers"},
            {"home": "Lions", "away": "Tigers"}
        ))

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
