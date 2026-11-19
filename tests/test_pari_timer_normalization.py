import unittest

from server import _PARI_TIMER_OBSERVATIONS, _pari_normalize_timer


class PariTimerNormalizationTests(unittest.TestCase):
    def setUp(self):
        _PARI_TIMER_OBSERVATIONS.clear()

    def normalize(self, event, raw, period, at, paused=False):
        return _pari_normalize_timer(
            event, raw, None, period, 600, 4,
            timer_paused=paused, now_seconds=at,
        )

    def test_count_up_verified_after_two_intervals(self):
        self.assertFalse(self.normalize("up", 100, 1, 1000)["timer_semantics_verified"])
        self.assertFalse(self.normalize("up", 130, 1, 1030)["timer_semantics_verified"])
        result = self.normalize("up", 160, 1, 1060)
        self.assertTrue(result["timer_semantics_verified"])
        self.assertEqual(result["period_elapsed_seconds"], 160)
        self.assertEqual(result["period_remaining_seconds"], 440)

    def test_direction_survives_quarter_reset_and_stoppage(self):
        self.normalize("transition", 100, 1, 1000)
        self.normalize("transition", 130, 1, 1030)
        self.assertTrue(self.normalize("transition", 160, 1, 1060)["timer_semantics_verified"])
        q2 = self.normalize("transition", 8, 2, 1090)
        self.assertTrue(q2["timer_semantics_verified"])
        self.assertEqual(q2["timer_observed_direction"], "up")
        self.assertEqual(q2["match_elapsed_seconds"], 608)
        stopped = self.normalize("transition", 8, 2, 1120, paused=True)
        self.assertTrue(stopped["timer_semantics_verified"])
        self.assertEqual(stopped["timer_state"], "stoppage")

    def test_count_down_direction_and_period_reset(self):
        self.normalize("down", 590, 1, 1000)
        self.normalize("down", 560, 1, 1030)
        result = self.normalize("down", 530, 1, 1060)
        self.assertTrue(result["timer_semantics_verified"])
        q2 = self.normalize("down", 600, 2, 1090)
        self.assertTrue(q2["timer_semantics_verified"])
        self.assertEqual(q2["period_elapsed_seconds"], 0)
        self.assertEqual(q2["period_remaining_seconds"], 600)

    def test_missing_format_never_verifies(self):
        result = _pari_normalize_timer("unknown", 120, None, 1, None, None, now_seconds=1000)
        self.assertFalse(result["timer_semantics_verified"])
        self.assertEqual(result["timer_interpretation_status"], "received_period_format_unconfirmed")

    def test_bad_period_time_is_not_verified(self):
        self.normalize("bad", 100, 1, 1000)
        self.normalize("bad", 130, 1, 1030)
        self.normalize("bad", 160, 1, 1060)
        result = self.normalize("bad", 1500, 2, 1090)
        self.assertFalse(result["timer_semantics_verified"])
        self.assertEqual(result["timer_interpretation_status"], "timer_period_mismatch")


if __name__ == "__main__":
    unittest.main()
