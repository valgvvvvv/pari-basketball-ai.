import contextlib
import io
import json
import logging
import unittest
from unittest.mock import patch
from unittest.mock import Mock
import httpx

import runtime_logging as logs
import server


class FeedIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_verified_senior_competitions_have_usable_clock_on_first_snapshot(self):
        for sport_id, name in [(13822, 'Италия. Серия А'), (59996, 'Италия. Серия А2'),
                               (13721, 'Франция. Про А'), (13639, 'Польша. PLK'),
                               (14047, 'Швеция. Баскетлиган')]:
            with self.subTest(sport_id=sport_id):
                result = await self.fetch_fixture({
                    'sports': [{'id': 1, 'name': 'Баскетбол'}, {'id': sport_id, 'parentId': 1, 'name': name}],
                    'events': [{'id': 80000002, 'sportId': sport_id, 'level': 1, 'place': 'live', 'team1': 'Home', 'team2': 'Away'}],
                    'eventMiscs': [{'id': 80000002, 'score1': 65, 'score2': 65, 'timerSeconds': 2100, 'timerDirection': 0}],
                    'liveEventInfos': [{'eventId': 80000002, 'scoreFunction': 'Basketball', 'timerSeconds': 2100,
                                        'scores': [[{'c1': '65', 'c2': '65'}],
                                                   [{'c1': '20', 'c2': '20', 'title': 'четверть'}]*3+
                                                   [{'c1': '5', 'c2': '5', 'title': 'четверть'}]]}]})
                event = result['events'][0]
                self.assertEqual(event['period'], 4)
                self.assertEqual(event['clock'], '05:00')
                self.assertTrue(event['timer_semantics_verified'])
                self.assertEqual(event['period_length_seconds'], 600)

    async def test_ncaa_payload_from_render_log_is_normalized_end_to_end(self):
        payload = {"sports": [{"id": 1, "name": "Баскетбол"}, {"id": 19166, "parentId": 1, "name": "NCAA"}],
            "events": [{"id": 69999268, "sportId": 19166, "level": 1, "place": "live", "team1": "California", "team2": "East Texas"}],
            "eventMiscs": [{"id": 69999268, "score1": 84, "score2": 54, "ef": "2x20", "timerSeconds": 2317, "timerDirection": 0}],
            "liveEventInfos": [{"eventId": 69999268, "scoreFunction": "Basketball", "timerSeconds": 2317,
                "scoreCommentHead": "2x20", "scores": [[{"c1": "84", "c2": "54"}],
                    [{"c1": "44", "c2": "28", "title": "четверть"}, {"c1": "40", "c2": "26", "title": "четверть"}]]}]}
        result = await self.fetch_fixture(payload)
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["events"]), 1)
        event = result["events"][0]
        self.assertEqual(event["period_count"], 2)
        self.assertEqual(event["period_length_seconds"], 1200)
        self.assertEqual(event["period"], 2)
        self.assertEqual(event["clock"], "01:23")
        self.assertTrue(event["timer_semantics_verified"])

    async def test_womens_ncaa_format_and_timer_are_registered(self):
        result=await self.fetch_fixture({'sports':[{'id':1,'name':'Basketball'},
                {'id':15100,'parentId':1,'name':'США. Женщины. NCAA'}],
            'events':[{'id':80000003,'sportId':15100,'level':1,'place':'live','team1':'A (ж)','team2':'B (ж)'}],
            'eventMiscs':[{'id':80000003,'score1':30,'score2':39,'timerSeconds':1250,'timerDirection':0}],
            'liveEventInfos':[{'eventId':80000003,'scoreFunction':'Basketball','timerSeconds':1250,
                'scores':[[{'c1':'30','c2':'39'}],[{'c1':'15','c2':'20','title':'четверть'},
                    {'c1':'15','c2':'19','title':'четверть'},{'c1':'0','c2':'0','title':'четверть'}]]}]})
        event=result['events'][0]
        self.assertEqual((event['period_count'],event['period_length_seconds']),(4,600))
        self.assertEqual(event['period'],3)
        self.assertEqual(event['clock'],'09:10')
        self.assertTrue(event['timer_semantics_verified'])

    async def fetch_fixture(self, payload):
        original_client = httpx.AsyncClient
        def client_factory(**kwargs):
            return original_client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload, request=request)))
        busy_task = Mock()
        busy_task.done.return_value = False
        with patch.object(server.httpx, "AsyncClient", side_effect=client_factory), patch.object(server, "_LIVE_ENRICHMENT_TASK", busy_task), patch.object(server, "emit_diagnostic"):
            result = await server._fetch_pari_live_events()
        return result

    async def test_g_league_target_score_overtime_has_no_fabricated_clock(self):
        result = await self.fetch_fixture({
            "sports": [{"id": 1, "name": "Баскетбол"}, {"id": 42338, "parentId": 1, "name": "NBA. G-Лига"}],
            "events": [{"id": 80000001, "sportId": 42338, "level": 1, "place": "live", "team1": "Lakers", "team2": "Kings"}],
            "eventMiscs": [{"id": 80000001, "score1": 100, "score2": 100, "timerSeconds": 2940}],
            "liveEventInfos": [{"eventId": 80000001, "scoreFunction": "Basketball", "timerSeconds": 2940,
                "scores": [[{"c1": "100", "c2": "100"}], [{"c1": "25", "c2": "25", "title": "четверть"}] * 4]}]})
        self.assertTrue(result["ok"])
        self.assertTrue(result["events"], result.get("filter_diagnostics"))
        event = result["events"][0]
        self.assertFalse(event["timer_semantics_verified"])
        self.assertEqual(event["timer_interpretation_status"], "untimed_overtime")
        self.assertIsNone(event["clock"])


class RenderRuntimeTests(unittest.TestCase):
    def test_explicit_half_format_uses_either_source_field(self):
        for key in ("ef", "comment", "scoreCommentHead", "scoreCommentTail"):
            for text in ("2x20", "2х20 (44-28 40-26)", "2 × 20"):
                with self.subTest(key=key, text=text):
                    fmt = server._pari_explicit_text_format({key: text})
                    result = server._pari_normalize_match_timer_against_period(
                        2317, 2, fmt["period_length_seconds"], fmt["period_count"])
                    self.assertTrue(result["timer_semantics_verified"])
                    self.assertEqual(result["period_remaining_seconds"], 83)
                    self.assertEqual(result["match_elapsed_seconds"], 2317)

    def test_ambiguous_or_unrelated_numbers_do_not_define_format(self):
        for row in ({"ef": "2x20 4x12"}, {"comment": "20:20 (44-28)"}, {"ef": "12x20"}, {"ef": "4x20"}):
            self.assertIsNone(server._pari_explicit_text_format(row))
        self.assertIsNone(server._pari_explicit_text_format({"ef": "2x20"}, {"ef": "4x12"}))

    def test_half_end_and_future_half_do_not_lose_time(self):
        result = server._pari_normalize_match_timer_against_period(1200, 1, 1200, 2)
        self.assertEqual(result["period_remaining_seconds"], 0)
        self.assertEqual(server._pari_period_from_match_timer(1102, 1200, 2, "up"), 1)
        self.assertIsNone(server._pari_normalize_match_timer_against_period(2317, 2, 1200, 4))

    def test_g_league_regulation_end_is_not_an_extra_quarter(self):
        rule = server.PARI_COMPETITION_FORMATS["42338"]
        result = server._pari_normalize_match_timer_against_period(2160, 3, rule["period_length_seconds"], 4)
        self.assertEqual(result["period_remaining_seconds"], 0)
        self.assertEqual(rule["overtime_mode"], "target_score")

    def test_healthy_host_is_first_without_removing_fallbacks(self):
        original = server._pari_feed_hosts()
        healthy = original[2]
        prioritized = server._pari_feed_hosts(healthy)
        self.assertEqual(prioritized[0], healthy)
        self.assertEqual(set(original), set(prioritized))
        self.assertEqual(server._pari_feed_hosts("https://untrusted.example"), original)

    def test_successful_fallback_is_info_and_compact(self):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict("os.environ", {"PARI_DIAGNOSTICS_VERBOSE": "0"}), patch.object(logs, "_LAST_DIAGNOSTIC", {}), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            payload = {"ok": True, "error": None, "basketball_events": 7, "forecast_input_samples": ["x" * 10000],
                       "attempts": [{"error": "ConnectError"}, {"status": 200}]}
            logs.emit_diagnostic("PARI_LIVE_DIAG", payload)
            logs.emit_diagnostic("PARI_LIVE_DIAG", payload)
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(len(out.getvalue().splitlines()), 1)
        record = json.loads(out.getvalue())
        self.assertEqual(record["level"], "info")
        self.assertEqual(record["failed_attempts"], 1)
        self.assertNotIn("forecast_input_samples", record)
        self.assertNotIn("error", record)
        self.assertNotIn("null", out.getvalue())

    def test_actual_failures_are_not_hidden_or_rate_limited(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            for _ in range(2):
                logs.emit_diagnostic("PARI_LIVE_DIAG", {"ok": False, "attempts": [{"error": "ConnectError"}]})
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(len(err.getvalue().splitlines()), 2)
        self.assertEqual(json.loads(err.getvalue().splitlines()[0])["level"], "error")

    def test_uvicorn_info_and_errors_go_to_separate_streams(self):
        logger = logging.getLogger("uvicorn.error")
        saved = (logger.handlers, logger.level, logger.propagate, getattr(logger, "_pari_configured", False))
        out, err = io.StringIO(), io.StringIO()
        try:
            logger._pari_configured = False
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                logs.configure_service_logging()
                logger.setLevel(logging.INFO)
                logger.info("Application startup complete.")
                logger.error("real failure")
            self.assertIn("INFO: Application startup complete.", out.getvalue())
            self.assertNotIn("real failure", out.getvalue())
            self.assertEqual(err.getvalue().strip(), "ERROR: real failure")
            self.assertNotIn("\x1b", out.getvalue() + err.getvalue())
        finally:
            logger.handlers, logger.level, logger.propagate, logger._pari_configured = saved
