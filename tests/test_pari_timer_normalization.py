import unittest

from server import (PARI_COMPETITION_FORMATS, _PARI_TIMER_OBSERVATIONS, _pari_normalize_timer,
                    _pari_infer_period_format_from_timeline,
                    _pari_infer_period_format_from_match_boundary,
                    _pari_infer_period_format_from_period_timer,
                    _pari_period_from_match_timer,
                    _pari_normalize_match_timer_against_period)


class PariTimerNormalizationTests(unittest.TestCase):
    def test_euroleague_format_is_available_before_observing_a_period_boundary(self):
        rule = PARI_COMPETITION_FORMATS["37222"]
        result = _pari_normalize_match_timer_against_period(1708, 3, rule["period_length_seconds"], rule["period_count"])
        self.assertEqual(result["period_remaining_seconds"], 92)
        self.assertTrue(result["timer_semantics_verified"])

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

    def test_q3_boundary_value_alone_does_not_confirm_format(self):
        result = _pari_infer_period_format_from_match_boundary(3, 1200, 4)
        self.assertIsNone(result)

    def test_36_minutes_in_q4_must_not_fabricate_twelve_minute_quarters(self):
        result = _pari_infer_period_format_from_match_boundary(4, 2160, 4)
        self.assertIsNone(result)

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


    def test_match_clock_and_quarter_timeline_verify_up_direction(self):
        result = _pari_normalize_match_timer_against_period(1651, 3, 600, 4)
        self.assertTrue(result["timer_semantics_verified"])
        self.assertEqual(result["timer_observed_direction"], "up")
        self.assertEqual(result["period_elapsed_seconds"], 451)
        self.assertEqual(result["period_remaining_seconds"], 149)

    def test_match_clock_and_quarter_timeline_verify_down_direction(self):
        result = _pari_normalize_match_timer_against_period(1183, 3, 600, 4)
        self.assertTrue(result["timer_semantics_verified"])
        self.assertEqual(result["timer_observed_direction"], "down")
        self.assertEqual(result["period_elapsed_seconds"], 17)
        self.assertEqual(result["period_remaining_seconds"], 583)

    def test_exact_quarter_boundary_is_usable_without_guessing_direction(self):
        result = _pari_normalize_match_timer_against_period(1200, 3, 600, 4)
        self.assertTrue(result["timer_semantics_verified"])
        self.assertEqual(result["timer_observed_direction"], "boundary")
        self.assertFalse(result["timer_direction_semantics_verified"])
        self.assertEqual(result["period_remaining_seconds"], 600)

    def test_match_clock_conflicting_with_quarter_timeline_is_not_verified(self):
        result = _pari_normalize_match_timer_against_period(1149, 4, 600, 4)
        self.assertIsNone(result)


    def test_one_timer_does_not_confirm_scope_and_format(self):
        result = _pari_infer_period_format_from_period_timer(2, 667, 4)
        self.assertIsNone(result)

    def test_do_not_guess_format_when_both_period_lengths_fit(self):
        result = _pari_infer_period_format_from_period_timer(3, 1743, 4)
        self.assertIsNone(result)


    def test_overtime_clock_is_verified_after_regulation(self):
        result = _pari_normalize_match_timer_against_period(2477, 4, 600, 4)
        self.assertTrue(result["timer_semantics_verified"])
        self.assertEqual(result["timer_scope"], "overtime")
        self.assertEqual(result["overtime_period_index"], 1)
        self.assertEqual(result["period_elapsed_seconds"], 77)
        self.assertEqual(result["period_remaining_seconds"], 223)

    def test_second_overtime_period_is_normalized(self):
        result = _pari_normalize_match_timer_against_period(2720, 4, 600, 4)
        self.assertEqual(result["overtime_period_index"], 2)
        self.assertEqual(result["period_elapsed_seconds"], 20)
        self.assertEqual(result["period_remaining_seconds"], 280)


    def test_first_quarter_match_clock_is_verified(self):
        result = _pari_normalize_match_timer_against_period(693, 1, 720, 4)
        self.assertTrue(result["timer_semantics_verified"])
        self.assertEqual(result["period_remaining_seconds"], 27)

    def test_second_quarter_match_clock_is_verified(self):
        result = _pari_normalize_match_timer_against_period(781, 2, 600, 4)
        self.assertTrue(result["timer_semantics_verified"])
        self.assertEqual(result["period_remaining_seconds"], 419)

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
