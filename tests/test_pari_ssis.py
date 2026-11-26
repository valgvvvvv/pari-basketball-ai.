import copy
import json
from pathlib import Path
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pari_ssis
import pari_data
import server


def fixture():
    return json.loads(Path(__file__).with_name('pari_ssis_fixture.json').read_text(encoding='utf8'))


class StatisticsTests(unittest.TestCase):
    def setUp(self):
        self.d = fixture()
        self.now = datetime.now(timezone.utc).isoformat()

    def parse(self):
        return pari_ssis.parse_statistics(self.d['statistics'],self.d['event'],self.now)

    def test_recorded_current_score_and_quarters_not_invented_attempts(self):
        r = self.parse()
        self.assertEqual(r['state'],'score_reconciled')
        self.assertEqual([x['metric_key'] for x in r['statistics']],['points'])
        self.assertEqual(len(r['periods']),3)
        self.assertEqual(r['periods'][0]['observed_at_utc'],self.now)

    def test_identity_and_stale_score_are_rejected(self):
        self.d['event']['raw']['team1Id'] = 123
        self.assertEqual(self.parse()['state'],'identity_not_confirmed')
        self.d = fixture(); self.d['event']['score'][0] += 2
        r = self.parse()
        self.assertFalse(r['statistics']); self.assertTrue(r['received'])

    def test_exact_kind_mapping_preserves_zero_and_period_orientation(self):
        ps = self.d['statistics']['response']['participants']
        for p in ps:
            p['stats'] += [{'kind':'63','stage':'24','value':'0'},
                           {'kind':'82','value':'21'}, {'kind':'987654','value':'7'}]
        self.d['statistics']['response']['participants'].reverse()
        r = self.parse()
        self.assertEqual(next(x for x in r['periods'] if x['metric_key']=='free_throws_attempted')['home'],'0')
        self.assertEqual(next(x for x in r['statistics'] if x['metric_key']=='two_pointers_attempted')['away'],'21')
        self.assertTrue(r['unmapped'])

    def test_halves_are_not_quarters_or_overtime_aggregate(self):
        self.assertEqual(pari_ssis.stage_period('46',2),1)
        self.assertIsNone(pari_ssis.stage_period('22',2))
        self.assertIsNone(pari_ssis.stage_period('46',4))
        self.assertIsNone(pari_ssis.stage_period('26',4))

    def test_cache_signature_separates_same_teams_at_another_start(self):
        e = self.d['event']
        self.assertNotEqual(server._live_event_signature(e),
            server._live_event_signature({**e,'start_time':e['start_time']+86400}))

    def test_history_keeps_extra_statistics_and_excludes_future(self):
        r = pari_ssis.parse_review(self.d['review'],self.d['event'],self.now)
        t = r['teams'][0]
        self.assertEqual(len(t['games']),2)
        self.assertTrue(t['games'][0]['participant_statistics'])
        self.assertEqual(t['summary']['sample_size'],2)
        self.assertTrue(all(g['start_time']<self.d['event']['start_time'] for g in t['games']))
        self.d['review']['response']['participants'][0]['lastEvents'][0]['startTime'] = self.d['event']['start_time']+1
        r = pari_ssis.parse_review(self.d['review'],self.d['event'],self.now)
        self.assertEqual(len(r['teams'][0]['games']),1)

    def test_cached_statistics_cannot_follow_a_new_score(self):
        event = copy.deepcopy(self.d['event'])
        event['external_statistics'] = self.parse()['statistics']
        event['period_statistics'] = self.parse()['periods']
        event['score'][0] += 1
        server._finalize_live_statistics([event])
        self.assertFalse(event['external_statistics'])
        self.assertFalse(event['period_statistics'])


class CacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_match_centre_is_loaded_even_when_market_card_fails(self):
        d = json.loads(Path(__file__).with_name('pari_women_matchcentre_fixture.json').read_text(encoding='utf8'))
        payload = {'events':[d['event']], 'liveEventInfos':[d['live']]}
        calls = []
        async def request(client,host,path,params,timeout):
            calls.append(path)
            if path.endswith('/actrans'):
                return {'items':[d['metadata']]}
            if path.endswith('/event'):
                raise RuntimeError('market card unavailable')
            return d['response']
        with patch.object(pari_data,'request_json',side_effect=request), \
             patch.dict(pari_data._METADATA,{},clear=True):
            await pari_data.enrich_payload(None,'https://line-vk01-w.pb06e2-resources.ru',payload)
        self.assertIn('/sportscast/events',calls)
        centre = payload['pari_match_centres'][str(d['event']['id'])]
        self.assertEqual(centre['diagnostics']['state'],'score_reconciled')

    async def test_background_keeps_periods_and_history_without_refreshing_timestamp(self):
        d = fixture(); event = d['event']; now = datetime.now(timezone.utc).isoformat()
        r = pari_ssis.parse_statistics(d['statistics'],event,now)
        event.update(external_statistics=r['statistics'],period_statistics=r['periods'],
            team_history=pari_ssis.parse_review(d['review'],event,now))
        with patch.object(pari_ssis,'enrich',AsyncMock(return_value={'matched':1})), \
             patch.object(server,'_enrich_with_sofascore_live',AsyncMock(return_value={'status':'ok'})), \
             patch.dict(server._LIVE_ENRICHMENT_CACHE,{},clear=True):
            await server._refresh_live_enrichment_background([event])
            cached = server._LIVE_ENRICHMENT_CACHE[event['id']]
            self.assertEqual(cached['period_statistics'][0]['observed_at_utc'],now)
            self.assertEqual(len(cached['team_history']['teams']),2)


if __name__ == '__main__':
    unittest.main()
