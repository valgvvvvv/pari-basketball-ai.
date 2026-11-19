import unittest

from server import (_PARI_TIMER_OBSERVATIONS, _pari_normalize_timer,
                    _pari_infer_period_format_from_timeline,
                    _pari_infer_period_format_from_match_boundary,
                    _pari_period_from_match_timer)


class PariTimerNormalizationTests(unittest.TestCase):
    def setUp(self):
        _PARI_TIMER_OBSERVATIONS.clear()

    def normalize(self, event, raw, period, at, paused=False):
        return _pari_normalize_timer(
            event, raw, None, period, 600, 4,
            timer_paused=paused, now_seconds=at,
        )


    def test_infer_ten_minute_period_from_cumulative_match_timer(self):
        # Q3: 22:00 elapsed in match and 8:00 left in Q3 => 10-minute quarters.
        result = _pari_infer_period_format_from_timeline(3, 1320, 480, 4)
        self.assertEqual(result["period_length_seconds"], 600)
        self.assertEqual(result["period_count"], 4)

    def test_infer_ten_minute_period_from_match_countdown(self):
        # Q3: 18:00 remain in match, 8:00 remain in Q3 => 10-minute quarters.
        result = _pari_infer_period_format_from_timeline(3, 1080, 480, 4)
        self.assertEqual(result["period_length_seconds"], 600)

    def test_infer_twelve_minute_period_from_match_timer(self):
        # Q3: 26:00 elapsed in match and 10:00 left in Q3 => 12-minute quarters.
        result = _pari_infer_period_format_from_timeline(3, 1560, 600, 4)
        self.assertEqual(result["period_length_seconds"], 720)

    def test_do_not_infer_when_timer_and_quarter_clock_conflict(self):
        result = _pari_infer_period_format_from_timeline(3, 900, 480, 4)
        self.assertIsNone(result)


    def test_do_not_guess_format_from_ambiguous_quarter_clock_alone(self):
        result = _pari_infer_period_format_from_timeline(2, None, 595, 4)
        self.assertIsNone(result)

    def test_infer_ten_minute_format_at_q3_boundary(self):
        result = _pari_infer_period_format_from_match_boundary(3, 1200, 4)
        self.assertEqual(result["period_length_seconds"], 600)

    def test_infer_twelve_minute_format_at_q4_boundary(self):
        result = _pari_infer_period_format_from_match_boundary(4, 2160, 4)
        self.assertEqual(result["period_length_seconds"], 720)

    def test_infer_q2_countdown_uses_only_future_quarters(self):
        # 28:00 remain in the match and 8:00 remain in Q2 => 10-minute quarters.
        result = _pari_infer_period_format_from_timeline(2, 1680, 480, 4)
        self.assertEqual(result["period_length_seconds"], 600)


    def test_match_timer_resolves_quarter_from_verified_count_up(self):
        self.assertEqual(_pari_period_from_match_timer(1183, 600, 4, "up"), 2)
        self.assertEqual(_pari_period_from_match_timer(1569, 600, 4, "up"), 3)
        self.assertEqual(_pari_period_from_match_timer(2263, 600, 4, "up"), 4)

    def test_match_timer_resolves_quarter_from_verified_count_down(self):
        self.assertEqual(_pari_period_from_match_timer(1183, 600, 4, "down"), 3)

    def test_count_up_verified_after_one_clean_interval(self):
        self.assertFalse(self.normalize("up", 100, 1, 1000)["timer_semantics_verified"])
        result = self.normalize("up", 130, 1, 1030)
        self.assertTrue(result["timer_semantics_verified"])
        self.assertEqual(result["period_elapsed_seconds"], 130)
        self.assertEqual(result["period_remaining_seconds"], 470)

    def test_direction_survives_quarter_reset_and_stoppage(self):
        self.normalize("transition", 100, 1, 1000)
        self.assertTrue(self.normalize("transition", 130, 1, 1030)["timer_semantics_verified"])
        q2 = self.normalize("transition", 8, 2, 1090)
        self.assertTrue(q2["timer_semantics_verified"])
        self.assertEqual(q2["timer_observed_direction"], "up")
        self.assertEqual(q2["match_elapsed_seconds"], 608)
        stopped = self.normalize("transition", 8, 2, 1120, paused=True)
        self.assertTrue(stopped["timer_semantics_verified"])
        self.assertEqual(stopped["timer_state"], "stoppage")

    def test_count_down_direction_and_period_reset(self):
        self.normalize("down", 590, 1, 1000)
        result = self.normalize("down", 560, 1, 1030)
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
        result = self.normalize("bad", 1500, 2, 1060)
        self.assertFalse(result["timer_semantics_verified"])
        self.assertEqual(result["timer_interpretation_status"], "timer_period_mismatch")


if __name__ == "__main__":
    unittest.main()
