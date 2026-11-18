import unittest

import server


class LiveStatisticsNormalizationTests(unittest.TestCase):
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

    def test_missing_values_are_not_fabricated(self):
        event = {"external_statistics":[
            {"name":"Assists","metric_key":"assists","home":"4","away":"7","source":"PARI"}
        ]}
        server._finalize_live_statistics([event])
        self.assertEqual(len(event["external_statistics"]), 1)
        self.assertIn("possessions", event["statistics_quality"]["missing_metrics"])
        self.assertFalse(event["statistics_quality"]["complete"])


if __name__ == "__main__":
    unittest.main()
