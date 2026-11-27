import asyncio
import unittest
import httpx
from unittest.mock import AsyncMock, patch
import server


class FastLiveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patches=[patch.object(server,'_PUBLISHED_LIVE_SNAPSHOT',None,create=True),
                      patch.object(server,'_LIVE_API_REFRESH_TASK',None),patch.object(server,'_LIVE_API_REFRESH_ATTEMPT',0)]
        for p in self.patches:p.start()

    async def asyncTearDown(self):
        task=server._LIVE_API_REFRESH_TASK
        if task is not None and not task.done():
            task.cancel()
            try:await task
            except asyncio.CancelledError:pass
        for p in reversed(self.patches):p.stop()

    async def test_cold_start_awaits_one_real_snapshot(self):
        data={'ok':True,'checked_at_utc':'1970-01-01T00:16:40Z','events':[]}
        with patch.object(server,'_refresh_live_snapshot',new=AsyncMock(return_value=data)) as load:
            self.assertEqual(await server.pari_live_events(),data)
            load.assert_awaited_once()

    async def test_fresh_published_read_never_calls_slow_pipeline(self):
        data={'ok':True,'checked_at_utc':'1970-01-01T00:16:35Z','events':[{'id':'a'}]}
        server._PUBLISHED_LIVE_SNAPSHOT={'saved_at':995,'data':data}
        with patch.object(server.time,'time',return_value=1000),patch.object(server,'_refresh_live_snapshot',new=AsyncMock()) as load:
            r=await server.pari_live_events()
            self.assertEqual(r['checked_at_utc'],data['checked_at_utc']);self.assertEqual(r['snapshot_age_seconds'],5)
            self.assertFalse(r['snapshot_stale']);load.assert_not_called()
            self.assertNotIn('refresh_pending',data,'read metadata must not mutate the frozen snapshot')

    async def test_http_compression_preserves_complete_snapshot(self):
        data={'ok':True,'checked_at_utc':'1970-01-01T00:16:35Z',
              'events':[{'id':str(i),'statistics':{'source':'PARI','values':list(range(200))}}
                        for i in range(13)],'recent_snapshots_by_event':{'a':[{'score':[40,41]}]}}
        server._PUBLISHED_LIVE_SNAPSHOT={'saved_at':995,'data':data}
        with patch.object(server.time,'time',return_value=1000):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://test') as client:
                plain=await client.get('/api/pari/live',headers={'Accept-Encoding':'identity'})
                compressed=await client.get('/api/pari/live',headers={'Accept-Encoding':'gzip'})
        self.assertEqual(plain.status_code,200)
        self.assertEqual(compressed.headers.get('content-encoding'),'gzip')
        self.assertEqual(compressed.json(),plain.json())
        self.assertEqual(compressed.json()['events'],data['events'])
        self.assertLess(int(compressed.headers['content-length']),len(plain.content)//2)
        self.assertIn('Accept-Encoding',compressed.headers['vary'])

    async def test_many_readers_share_one_background_refresh_and_keep_source_age(self):
        data={'ok':True,'checked_at_utc':'1970-01-01T00:15:00Z','events':[]}
        server._PUBLISHED_LIVE_SNAPSHOT={'saved_at':900,'data':data}
        hold=asyncio.Event()
        async def slow():await hold.wait()
        with patch.object(server.time,'time',return_value=1000),patch.object(server,'_refresh_live_snapshot',new=AsyncMock(side_effect=slow)) as load:
            results=await asyncio.gather(*(server.pari_live_events() for _ in range(20)))
            await asyncio.sleep(0)
            load.assert_awaited_once()
            self.assertTrue(all(r['snapshot_stale'] and r['snapshot_age_seconds']==100 for r in results))
            self.assertTrue(all(r['checked_at_utc']==data['checked_at_utc'] for r in results))
            hold.set();await server._LIVE_API_REFRESH_TASK
