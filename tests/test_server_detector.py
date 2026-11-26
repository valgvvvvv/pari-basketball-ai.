import asyncio
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import server
import server_detector
import server_signals


def fixture():
    return {'id': 'server-fixture', 'team1': 'Home', 'team2': 'Away', 'league': 'Test',
            'score': [70, 65], 'period': 4, 'clock': '05:00', 'clock_source': 'PARI',
            'period_length_seconds': 600, 'period_count': 4, 'format_confirmed': True,
            'timer_semantics_verified': True, 'clock_integrity': {'valid': True},
            'match_elapsed_seconds': 2100, 'period_elapsed_seconds': 300,
            'period_scores': [[20, 18], [22, 20], [18, 17], [10, 10]],
            'external_live': {'period_length_seconds': 600, 'period_count': 4},
            'match_total_candidates': [{'f': '930', 'pt': 190.5, 'v': 1.9}, {'f': '931', 'pt': 190.5, 'v': 1.9}]}


class DetectorTests(unittest.TestCase):
    def test_embedded_v8_matches_node_for_identical_snapshot(self):
        import shutil
        import subprocess
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is unavailable')
        data = {'events': [fixture()], 'checked_at_utc': datetime.now(timezone.utc).isoformat()}
        script = server_detector.source_bundle() + '\nconsole.log(JSON.stringify(serverPriorityCards(' + json.dumps(data) + ')));'
        node_result = subprocess.run([node, '-'], input=script, encoding='utf-8', capture_output=True, check=True)
        actual = server_detector.calculate(data)
        actual.pop('model_source_sha256')
        self.assertEqual(actual, json.loads(node_result.stdout))

    def test_outside_match_window_does_not_run_expensive_simulation(self):
        from py_mini_racer import mini_racer
        event = dict(fixture(), period=2, clock='09:00')
        data = {'events': [event], 'checked_at_utc': datetime.now(timezone.utc).isoformat()}
        with mini_racer() as engine:
            engine.eval(server_detector.source_bundle())
            engine.eval('automaticForecast=()=>{throw Error("unexpected match simulation")};')
            actual = json.loads(engine.eval('JSON.stringify(serverPriorityCards('+json.dumps(data)+'))'))
        self.assertEqual(actual['errors'], [])
        self.assertTrue(all(r['decision']['target'] != 'match' for r in actual['evaluations']))
        self.assertTrue(any(r['target'] == 'match' for r in actual['diagnostics']['modelBlockers']))

    def test_real_frontend_functions_execute_without_browser(self):
        data = {'events': [fixture()], 'checked_at_utc': datetime.now(timezone.utc).isoformat()}
        result = server_detector.calculate(data)
        self.assertEqual(result['errors'], [])
        cards = result['cards']
        self.assertEqual(cards, [])
        decision = result['evaluations'][0]['decision']
        self.assertEqual(decision['target'], 'match')
        self.assertEqual(decision['direction'], 'ТМ')
        self.assertEqual(decision['line'], 190.5)
        self.assertIn('calibration', decision['reasonCodes'])
        self.assertEqual(len(result['researchCandidates']), 1)
        self.assertFalse(result['researchCandidates'][0]['signal']['priority'])
        self.assertFalse(result['researchCandidates'][0]['signal']['marketReady'])
        # A real website research card must reach the opted-in Telegram mirror,
        # even when it is not a compatible/validated betting-market signal.
        with patch.dict(os.environ, {'SIGNAL_EXPERIMENTAL_ENABLED': '1'}):
            mirrored = server_signals.experimental_cards(result)
        self.assertEqual(len(mirrored), len(result['researchCandidates']))
        self.assertFalse(mirrored[0]['signal']['priority'])
        self.assertFalse(mirrored[0]['signal']['marketReady'])
        self.assertGreater(result['diagnostics']['evaluatedLines'], 0)

    def test_stale_unverified_and_q3_never_emit_match_signal(self):
        for change, age in [({'timer_semantics_verified': False}, 0), ({}, 100), ({'period': 3}, 0)]:
            data = {'events': [dict(fixture(), **change)], 'checked_at_utc': (datetime.now(timezone.utc)-timedelta(seconds=age)).isoformat()}
            self.assertEqual(server_detector.calculate(data)['cards'], [])
            self.assertEqual(server_detector.calculate(data)['researchCandidates'], [])

    def test_sofascore_period_array_keeps_match_and_quarter_separate(self):
        item = {'name': 'Assists', 'home': '3', 'away': '4'}
        groups = server._sofascore_stat_groups({'statistics': [
            {'period': 'ALL', 'groups': [{'groupName': 'All', 'statisticsItems': [item]}]},
            {'period': '1ST', 'groups': [{'groupName': 'Q1', 'statisticsItems': [item]}]}]})
        self.assertEqual(len(groups), 2)
        self.assertNotIn('period', groups[0])
        self.assertEqual(groups[1]['period'], 1)
        self.assertEqual(groups[0]['statistics'], [item])

    def test_sofascore_legacy_object_is_still_supported(self):
        payload = {'statistics': {'groups': [{'statistics': [{'name': 'Rebounds', 'home': 5, 'away': 6}]}]}}
        self.assertEqual(len(server._sofascore_stat_groups(payload)), 1)


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'SIGNAL_SERVER_ENABLED': '1', 'SIGNAL_BOT_TOKEN': 'test-only', 'SIGNAL_CHAT_ID': '123',
                                         'SIGNAL_LIFECYCLE_PATH': str(Path(self.folder.name)/'receipts.sqlite3')})
        self.env.start()
        self.path = patch.object(server_signals, 'STATE_PATH', Path(self.folder.name)/'state.sqlite3')
        self.path.start()
        self.lock = patch.object(server_signals, 'LOCK', asyncio.Lock())
        self.lock.start()
        server_signals.RETRY_AT.clear()
        self.send = patch.object(server_signals, 'say', new_callable=AsyncMock)
        self.say = self.send.start()
        self.say.return_value = {'message_id': 42}
        server_signals.RESULT_LOCK = asyncio.Lock()
        self.audit = patch.object(server_signals, 'audit_delivery'); self.audit.start()
        self.card = {'event': 'x', 'forecast_id': 'saved-test-forecast', 'teams': 'Home — Away', 'label': 'Матч · ТМ 190.5',
                     'signal': {'target': 'match', 'period': 4, 'line': 190.5, 'direction': 'ТМ'},
                     'expires': (time.time()+90)*1000, 'context': 'Test'}

    async def asyncTearDown(self):
        self.audit.stop(); self.send.stop(); self.lock.stop(); self.path.stop(); self.env.stop(); self.folder.cleanup()

    async def test_only_first_signal_per_game_is_sent_and_changes_are_edited(self):
        await server_signals.deliver([self.card])
        self.assertEqual(self.say.await_count, 1)
        new = dict(self.card, forecast_id='changed', signal=dict(self.card['signal'], line=191.5))
        with patch.object(server_signals, 'telegram', new_callable=AsyncMock) as edit:
            await server_signals.deliver([new])
            await server_signals.deliver([new])
        self.assertEqual(edit.await_count, 1)
        self.assertEqual(self.say.await_count, 1)
        new = dict(new, event='another-game', forecast_id='another')
        self.say.return_value = {'message_id': 43}
        await server_signals.deliver([new])
        await server_signals.deliver([new])
        self.assertEqual(self.say.await_count, 2)

    async def test_failed_send_retries_and_expired_card_is_skipped(self):
        await server_signals.deliver([])
        self.say.side_effect = RuntimeError('offline')
        await server_signals.deliver([self.card])
        server_signals.RETRY_AT.clear()
        self.say.side_effect = None
        await server_signals.deliver([self.card])
        stale = dict(self.card, signal=dict(self.card['signal'], line=192.5), expires=1)
        await server_signals.deliver([stale])
        self.assertEqual(self.say.await_count, 2)

    async def test_other_chat_cannot_pause(self):
        update = {'update_id': 4, 'message': {'chat': {'id': 999}, 'text': '/pause'}}
        with patch.object(server_signals, 'telegram', new=AsyncMock(return_value=[update])):
            await server_signals.commands_once()
        from contextlib import closing
        with closing(server_signals.connection()) as db:
            self.assertNotEqual(server_signals.get(db, 'paused'), '1')
        self.say.assert_not_awaited()
