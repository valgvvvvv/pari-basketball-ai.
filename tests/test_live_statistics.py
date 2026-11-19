import unittest

import server


class LiveStatisticsNormalizationTests(unittest.TestCase):
    def test_explicit_sport_function_prevents_cross_sport_contamination(self):
        self.assertTrue(server._pari_score_function_is_basketball("Basketball"))
        self.assertTrue(server._pari_score_function_is_basketball("баскетбол"))
        self.assertFalse(server._pari_score_function_is_basketball(None))
        self.assertFalse(server._pari_score_function_is_basketball("Football"))
        self.assertFalse(server._pari_score_function_is_basketball("Soccer"))
        self.assertFalse(server._pari_score_function_is_basketball("Volleyball"))

    def test_common_provider_aliases(self):
        cases = {
            "3PM": "three_pointers_made", "3PA": "three_pointers_attempted",
            "2PM": "two_pointers_made", "2PA": "two_pointers_attempted",
            "FTM": "free_throws_made", "FTA": "free_throws_attempted",
            "FGM": "field_goals_made", "FGA": "field_goals_attempted",
            "OREB": "offensive_rebounds", "DREB": "defensive_rebounds",
            "REB": "rebounds", "AST": "assists", "TOV": "turnovers",
            "STL": "steals", "BLK": "blocks", "PF": "personal_fouls",
            "Possessions": "possessions",
        }
        for label, expected in cases.items():
            with self.subTest(label=label):
                self.assertEqual(server._pari_stat_metric(label), expected)

    def test_pair_value_is_split_and_aggregated(self):
        event = {"external_statistics": [
            {"name":"3 pointers","metric_key":"three_pointers","home":"8/17","away":"5/16","source":"SofaScore"},
            {"name":"2 pointers","metric_key":"two_pointers","home":"12/22","away":"10/21","source":"SofaScore"},
        ]}
        server._finalize_live_statistics([event])
        lookup = {row["metric_key"]:row for row in event["external_statistics"] if row.get("metric_key")}
        self.assertEqual(lookup["three_pointers_made"]["home"], 8.0)
        self.assertEqual(lookup["three_pointers_attempted"]["away"], 16.0)
        self.assertEqual(lookup["two_pointers_made"]["away"], 10.0)
        self.assertEqual(lookup["field_goals_made"]["home"], 20.0)
        self.assertEqual(lookup["field_goals_attempted"]["away"], 37.0)
        self.assertEqual(event["statistics_quality"]["expected_count"], 19)

    def test_quarter_shot_counts_are_separated_from_match_totals(self):
        extracted = server._extract_pari_live_statistics([{
            "statistics": [
                {"kindId": "420400", "kindName": "заб/3-х очковые", "c1": "9", "c2": "8"},
                {"kindId": "420400", "kindName": "2-я четверть заб/3-х очковые", "c1": "3", "c2": "2"},
            ]
        }])
        self.assertEqual(len(extracted["match"]), 1)
        self.assertEqual(extracted["match"][0]["home"], "9")
        self.assertEqual(len(extracted["periods"]), 1)
        self.assertEqual(extracted["periods"][0]["period"], 2)
        self.assertEqual(extracted["periods"][0]["home"], "3")

    def test_unknown_pari_codes_are_retained_but_not_used(self):
        extracted = server._extract_pari_live_statistics([{
            "statistics": [
                {"kindId": "999999", "kindName": "unknown stat", "c1": "4", "c2": "5"}
            ]
        }])
        self.assertEqual(extracted["match"], [])
        self.assertEqual(extracted["unmapped"][0]["kind_id"], "999999")
        self.assertEqual(extracted["unmapped"][0]["home"], "4")

    def test_missing_values_are_not_fabricated(self):
        event = {"external_statistics":[
            {"name":"Assists","metric_key":"assists","home":"4","away":"7","source":"PARI"}
        ]}
        server._finalize_live_statistics([event])
        self.assertEqual(len(event["external_statistics"]), 1)
        self.assertIn("possessions", event["statistics_quality"]["missing_metrics"])
        self.assertFalse(event["statistics_quality"]["complete"])


    def test_quarter_score_kind_id_is_not_overwritten_by_shot_statistics(self):
        source = {"subscores": [
            {"kindId": "100401", "kindName": "1-я четверть", "c1": "21", "c2": "17"},
            {"kindId": "420300", "kindName": "двухочковые попадания", "c1": "4", "c2": "6"},
            {"kindId": "30300401", "kindName": "двухочковые попадания за Q1", "c1": "4", "c2": "6"},
            {"kindId": "420400", "kindName": "трёхочковые попадания", "c1": "2", "c2": "1"},
            {"kindId": "30400401", "kindName": "трёхочковые попадания за Q1", "c1": "2", "c2": "1"},
        ]}
        self.assertEqual(server._pari_period_scores(source), [[21, 17]])
        records = server._pari_period_score_records(source)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["kind_id"], "100401")
        self.assertEqual(records[0]["source"], "PARI.subscores")

    def test_unknown_period_kind_is_not_guessed(self):
        source = {"subscores": [
            {"kindId": "999001", "kindName": "1-я четверть", "c1": "21", "c2": "17"}
        ]}
        self.assertEqual(server._pari_period_scores(source), [])
        self.assertEqual(server._pari_period_score_records(source), [])
        self.assertEqual(server._pari_period_score_unmapped_records(source)[0]["kind_id"], "999001")

    def test_sport_hierarchy_requires_basketball_ancestor_not_competition_name(self):
        catalogue = {
            "10": {"id": "10", "name": "UAAP", "kind": "segment"},
            "20": {"id": "20", "name": "Basketball", "kind": "sport"},
            "21": {"id": "21", "name": "UAAP", "kind": "segment", "parentId": "20"},
        }
        self.assertFalse(server._pari_sport_hierarchy_is_basketball("10", catalogue))
        self.assertTrue(server._pari_sport_hierarchy_is_basketball("21", catalogue))

    def test_explicit_football_score_function_is_not_basketball(self):
        self.assertFalse(server._pari_score_function_is_basketball("Football"))
        self.assertFalse(server._pari_score_function_is_basketball(None))
        self.assertTrue(server._pari_score_function_is_basketball("Basketball"))



    def test_all_four_period_score_kind_ids_are_mapped_independently(self):
        source = {"subscores": [
            {"kindId": "100404", "kindName": "4-я четверть", "c1": "7", "c2": "8"},
            {"kindId": "100402", "kindName": "2-я четверть", "c1": "5", "c2": "6"},
            {"kindId": "100401", "kindName": "1-я четверть", "c1": "3", "c2": "4"},
            {"kindId": "100403", "kindName": "3-я четверть", "c1": "9", "c2": "10"},
        ]}
        self.assertEqual(server._pari_period_scores(source), [[3, 4], [5, 6], [9, 10], [7, 8]])

    def test_competition_format_registry_has_rule_provenance(self):
        for competition_id in ("59836", "13791"):
            rule = server.PARI_COMPETITION_FORMATS[competition_id]
            self.assertEqual(rule["period_length_seconds"], 600)
            self.assertEqual(rule["period_count"], 4)
            self.assertEqual(rule["overtime_length_seconds"], 300)
            self.assertTrue(rule["source"])
            self.assertTrue(rule["rules_version"])
            self.assertTrue(rule["source_url"])



    def test_raw_timer_is_not_mislabelled_as_elapsed_or_remaining(self):
        unknown = server._pari_timer_metadata(405, 0, 1795086000000)
        self.assertEqual(unknown["source_timer_seconds"], 405)
        self.assertIsNone(unknown["match_elapsed_seconds"])
        self.assertIsNone(unknown["period_elapsed_seconds"])
        self.assertIsNone(unknown["period_remaining_seconds"])
        self.assertEqual(unknown["timer_state"], "source_status_unconfirmed")
        self.assertEqual(unknown["timer_interpretation_status"], "received_period_format_unconfirmed")

    def test_known_format_does_not_imply_timer_direction(self):
        rule = server.PARI_COMPETITION_FORMATS["59836"]
        metadata = server._pari_timer_metadata(420, 0, None,
            rule["period_length_seconds"], rule, False, False)
        self.assertEqual(metadata["source_timer_seconds"], 420)
        self.assertIsNone(metadata["period_remaining_seconds"])
        self.assertEqual(metadata["timer_interpretation_status"], "received_timer_semantics_unverified")
        self.assertEqual(metadata["overtime_length_seconds"], 300)



if __name__ == "__main__":
    unittest.main()
