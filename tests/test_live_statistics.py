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



    def test_related_rows_are_initialized_before_sport_function_filter(self):
        with open(server.__file__, encoding="utf-8") as source_file:
            source = source_file.read()
        self.assertLess(
            source.index("related_rows = collect_related_event_rows(event_id)"),
            source.index("score_function_rows = (event, live_info, event_misc, *event_block_rows, *related_rows)"),
        )



    def test_generic_scores_group_maps_quarters_by_position_only_when_all_titles_are_quarter(self):
        source = {"scores": [
            [{"c1": "56", "c2": "42"}],
            [
                {"c1": "26", "c2": "23", "title": "четверть"},
                {"c1": "24", "c2": "14", "title": "четверть"},
                {"c1": "6", "c2": "5", "title": "четверть"},
            ],
        ]}
        self.assertEqual(server._pari_period_scores(source), [[26, 23], [24, 14], [6, 5]])

    def test_generic_scores_group_does_not_accept_mixed_stat_labels(self):
        source = {"scores": [
            [{"c1": "10", "c2": "9"}],
            [
                {"c1": "6", "c2": "5", "title": "четверть"},
                {"c1": "4", "c2": "3", "title": "двухочковые"},
            ],
        ]}
        self.assertEqual(server._pari_period_scores(source), [])



    def test_future_zero_quarters_are_kept_as_unstarted_not_match_scores(self):
        records = [
            {"period": 1, "score": [26, 23], "kind_id": "100401"},
            {"period": 2, "score": [24, 14], "kind_id": "100402"},
            {"period": 3, "score": [11, 7], "kind_id": "100403"},
            {"period": 4, "score": [0, 0], "kind_id": "100404"},
        ]
        accepted, unstarted = server._pari_filter_unstarted_zero_periods(records, 3)
        self.assertEqual([row["period"] for row in accepted], [1, 2, 3])
        self.assertEqual(unstarted[0]["period"], 4)
        self.assertIn("ещё не начался", unstarted[0]["reason"])

    def test_unknown_current_period_does_not_discard_source_zero_scores(self):
        records = [{"period": 4, "score": [0, 0], "kind_id": "100404"}]
        accepted, unstarted = server._pari_filter_unstarted_zero_periods(records, None)
        self.assertEqual(accepted, records)
        self.assertEqual(unstarted, [])


    def test_null_stat_values_are_not_converted_to_zero(self):
        event = {"id": "fixture-null", "external_statistics": [
            {"name": "Assists", "metric_key": "assists", "home": None, "away": "0", "source": "SofaScore"}
        ]}
        server._finalize_live_statistics([event])
        self.assertEqual(event["external_statistics"], [])
        self.assertNotIn("assists", event["statistics_quality"]["available_metrics"])



    def test_statistics_rows_keep_source_and_event_provenance(self):
        event = {"id": "pari-123", "feed_checked_at_utc": "2026-11-19T10:00:00+00:00",
                 "external_statistics": [
                     {"name": "Assists", "metric_key": "assists", "home": 4, "away": 7,
                      "source": "SofaScore", "source_event_id": "sofa-987",
                      "target": "match", "match_status": "exact_event_id_match"}
                 ]}
        server._finalize_live_statistics([event])
        row = event["external_statistics"][0]
        self.assertEqual(row["event_id"], "pari-123")
        self.assertEqual(row["source_event_id"], "sofa-987")
        self.assertEqual(row["target"], "match")
        self.assertIsNone(row["target_period"])
        self.assertEqual(row["match_status"], "exact_event_id_match")
        self.assertEqual(row["observed_at_utc"], "2026-11-19T10:00:00+00:00")
        self.assertEqual(row["observation_basis"], "pari_feed_snapshot_proxy")
        self.assertEqual(row["freshness_status"], "source_timestamp_unavailable")
        self.assertTrue(row["received_at_utc"])



    def test_background_enrichment_preserves_partial_success_when_other_metric_times_out(self):
        import asyncio
        from unittest.mock import patch

        events = [{
            "id": "pari-partial", "team1": "Home", "team2": "Away",
            "external_statistics": [
                {"name": "Assists", "metric_key": "assists", "home": 3, "away": 4, "source": "PARI"}
            ],
        }]

        async def fake_provider(snapshot):
            snapshot[0]["external_statistics"].append({
                "name": "Steals", "metric_key": "steals", "home": 2, "away": 1,
                "source": "SofaScore", "source_event_id": "sofa-partial",
                "target": "match", "match_status": "exact_event_id_match"
            })
            return {"status": "partial", "matched": 1, "unmatched_pari": 0,
                    "statistics_errors": ["stats_TimeoutException"],
                    "checked_at_utc": "2026-11-19T10:00:00+00:00"}

        with patch.object(server, "_enrich_with_sofascore_live", side_effect=fake_provider):
            asyncio.run(server._refresh_live_enrichment_background(events))
        cached = server._LIVE_ENRICHMENT_CACHE["pari-partial"]
        self.assertEqual(len(cached["external_statistics"]), 1)
        self.assertEqual(cached["external_statistics"][0]["metric_key"], "steals")
        self.assertTrue(cached["statistics_source_diagnostics"]["timeout"])
        self.assertEqual(cached["signature"], ("home", "away"))



    def test_unmatched_external_source_is_diagnosed_separately_from_missing_field(self):
        event = {"id": "pari-no-match", "external_statistics": [],
                 "statistics_source_diagnostics": {"match_status": "no_exact_match"}}
        server._finalize_live_statistics([event])
        self.assertEqual(event["statistics_quality"]["missing_status"]["assists"], "no_exact_match")





    def test_confirmed_pari_value_wins_and_conflicting_external_value_is_preserved(self):
        event = {"id": "conflict-fixture", "external_statistics": [
            {"name": "Assists", "metric_key": "assists", "home": 5, "away": 6, "source": "PARI"},
            {"name": "Assists", "metric_key": "assists", "home": 7, "away": 6, "source": "SofaScore"}
        ]}
        server._finalize_live_statistics([event])
        assists = next(row for row in event["external_statistics"] if row.get("metric_key") == "assists")
        self.assertEqual(assists["home"], 5)
        conflict = next(row for row in event["statistics_quality"]["conflicts"] if row["metric"] == "assists")
        self.assertEqual({item["source"] for item in conflict["values"]}, {"PARI", "SofaScore"})



if __name__ == "__main__":
    unittest.main()
