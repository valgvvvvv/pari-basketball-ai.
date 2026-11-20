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
        self.assertEqual(server_detector.calculate(data), json.loads(node_result.stdout))

    def test_real_frontend_functions_execute_without_browser(self):
        data = {'events': [fixture()], 'checked_at_utc': datetime.now(timezone.utc).isoformat()}
        result = server_detector.calculate(data)
        self.assertEqual(result['errors'], [])
        cards = result['cards']
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]['signal']['target'], 'match')
        self.assertEqual(cards[0]['signal']['direction'], 'ТМ')
        self.assertEqual(cards[0]['signal']['line'], 190.5)

    def test_stale_unverified_and_q3_never_emit_match_signal(self):
        for change, age in [({'timer_semantics_verified': False}, 0), ({}, 100), ({'period': 3}, 0)]:
            data = {'events': [dict(fixture(), **change)], 'checked_at_utc': (datetime.now(timezone.utc)-timedelta(seconds=age)).isoformat()}
            self.assertEqual(server_detector.calculate(data)['cards'], [])

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
        self.env = patch.dict(os.environ, {'SIGNAL_SERVER_ENABLED': '1', 'SIGNAL_BOT_TOKEN': 'test-only', 'SIGNAL_CHAT_ID': '123'})
        self.env.start()
        self.path = patch.object(server_signals, 'STATE_PATH', Path(self.folder.name)/'state.sqlite3')
        self.path.start()
        self.lock = patch.object(server_signals, 'LOCK', asyncio.Lock())
        self.lock.start()
        self.send = patch.object(server_signals, 'say', new_callable=AsyncMock)
        self.say = self.send.start()
        self.card = {'event': 'x', 'teams': 'Home — Away', 'label': 'Матч · ТМ 190.5',
                     'signal': {'target': 'match', 'period': 4, 'line': 190.5, 'direction': 'ТМ'},
                     'expires': (time.time()+90)*1000, 'context': 'Test'}

    async def asyncTearDown(self):
        self.send.stop(); self.lock.stop(); self.path.stop(); self.env.stop(); self.folder.cleanup()

    async def test_baseline_then_new_line_is_sent_once(self):
        await server_signals.deliver([self.card])
        self.say.assert_not_awaited()
        new = dict(self.card, signal=dict(self.card['signal'], line=191.5))
        await server_signals.deliver([new])
        await server_signals.deliver([new])
        self.assertEqual(self.say.await_count, 1)

    async def test_failed_send_retries_and_expired_card_is_skipped(self):
        await server_signals.deliver([])
        self.say.side_effect = RuntimeError('offline')
        with self.assertRaises(RuntimeError):
            await server_signals.deliver([self.card])
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
