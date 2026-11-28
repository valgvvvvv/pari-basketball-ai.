import asyncio
from datetime import datetime, timezone, timedelta
import unittest
from unittest.mock import AsyncMock, Mock, patch

import server


class SharedRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fetch = AsyncMock(return_value={'ok': True, 'connected': True,
            'checked_at_utc': datetime.now(timezone.utc).isoformat(), 'events': [{'id': 'g'}]})

        async def process(data, history, publish=True):
            self.assertEqual(history, {'g': []})
            data['signal_diagnostics'] = {'evaluatedLines': 2}
            data['research_candidates'] = [{'event': 'g'}]

        self.process = AsyncMock(side_effect=process)
        self.patches = [patch.object(server, '_PARI_LIVE_CACHE', {}, create=True),
            patch.object(server, '_PUBLISHED_LIVE_SNAPSHOT', None, create=True),
            patch.object(server, '_LIVE_API_REFRESH_TASK', None),
            patch.object(server, '_LIVE_API_REFRESH_ATTEMPT', 0),
            patch.object(server, '_LIVE_REFRESH_LOCK', asyncio.Lock()),
            patch.object(server, '_ARCHIVE_LOCK', asyncio.Lock()),
            patch.object(server, '_fetch_pari_live_events', self.fetch),
            patch.object(server, '_archive_save_events', Mock(return_value=(1, 1))),
            patch.object(server, 'archive_recent', Mock(return_value={'events': {'g': []}})),
            patch.object(server.server_signals, 'process', self.process),
            patch.object(server.server_signals, 'queue_notifications'),
            patch.object(server, 'emit_diagnostic')]
        for p in self.patches:
            p.start()

    async def asyncTearDown(self):
        for p in reversed(self.patches):
            p.stop()

    async def test_concurrent_browser_and_collector_share_one_evaluated_snapshot(self):
        results = await asyncio.gather(server.pari_live_events(), server._refresh_live_snapshot())
        self.assertEqual(self.fetch.await_count, 1)
        self.assertEqual(self.process.await_count, 1)
        self.assertIs(results[0], results[1])
        self.assertEqual(len(results[0]['research_candidates']), 1)

    async def test_raw_diagnostics_cache_cannot_bypass_notification_processing(self):
        server._PARI_LIVE_CACHE = {'saved_at': datetime.now(timezone.utc).timestamp(),
                                  'data': {'ok': True, 'events': []}}
        await server.pari_live_events()
        self.process.assert_awaited_once()

    async def test_cache_expires_at_the_new_five_second_interval(self):
        self.assertEqual(server.server_signals.INTERVAL_SECONDS, 5)
        server._PARI_LIVE_CACHE = {'saved_at': datetime.now(timezone.utc).timestamp()-6,
                                  'data': {'signal_diagnostics': {}, 'events': []}}
        await server.pari_live_events()
        self.fetch.assert_awaited_once()
        self.assertIn('cycle_seconds', server.server_signals.STATUS['refresh_timing'])

    async def test_slow_cycle_does_not_make_completed_cache_immediately_expired(self):
        start=datetime.now(timezone.utc)
        clock={'now':start}
        original_process=self.process.side_effect
        source_timestamp=self.fetch.return_value['checked_at_utc']
        async def slow_process(data,history,publish=True):
            await original_process(data,history,publish=publish)
            clock['now']=start+timedelta(seconds=35)
        self.process.side_effect=slow_process
        with patch.object(server,'datetime',wraps=datetime) as dates:
            dates.now.side_effect=lambda *args:clock['now']
            first=await server.pari_live_events()
            second=await server.pari_live_events()
        self.assertEqual(first['events'],second['events'])
        self.fetch.assert_awaited_once()
        self.assertEqual(second['checked_at_utc'],source_timestamp)

    async def test_failed_feed_is_never_treated_as_an_evaluated_snapshot(self):
        self.fetch.return_value = {'ok': False, 'connected': False, 'events': []}
        result = await server.pari_live_events()
        self.assertFalse(result['ok'])
        self.process.assert_not_awaited()
        self.assertEqual(server._PARI_LIVE_CACHE, {})

    async def test_queue_is_published_after_challenger_attachment(self):
        def attach(data,history):
            self.assertFalse(self.process.await_args.kwargs['publish'])
            data['events'][0]['learning']={'period':{'status':'shadow'}}
        def queued(data):
            self.assertEqual(data['events'][0]['learning']['period']['status'],'shadow')
        with patch.object(server.learning_service,'attach',side_effect=attach),patch.object(server.server_signals,'queue_notifications',side_effect=queued) as publish:
            await server._refresh_live_snapshot()
            publish.assert_called_once()
