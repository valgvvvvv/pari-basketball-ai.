import copy
import json
from pathlib import Path
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pari_data
import pari_matchcentre
import server
from match_identity import _gender


def fixture():
    return json.loads(Path(__file__).with_name('pari_women_matchcentre_fixture.json').read_text(encoding='utf-8'))


def parse(data):
    return pari_matchcentre.parse(data['event'], data['live'], data['metadata'], data['response'], 'https://pari.ru/source')


class StreamTests(unittest.TestCase):
    def test_component_sum_keeps_observation_time_and_provenance(self):
        event = {'id':'sum', 'feed_checked_at_utc':datetime.now(timezone.utc).isoformat(),
                 'external_statistics':[{'name':key,'metric_key':key,'home':home,'away':away,'source':'PARI'}
                    for key,home,away in [('two_pointers_attempted',20,25),('three_pointers_attempted',8,9)]]}
        server._finalize_live_statistics([event])
        row = next(r for r in event['external_statistics'] if r['metric_key']=='field_goals_attempted')
        self.assertEqual((row['home'],row['away']), (28,34))
        self.assertEqual(row['freshness_status'], 'fresh')
        self.assertTrue(row['observed_at_utc'])
        self.assertEqual(len(row['component_sources']), 2)

    def test_actual_women_stream_reconciles_score_and_quarters(self):
        result = parse(fixture())
        self.assertEqual(result['diagnostics']['state'], 'score_reconciled')
        rows = {r['metric_key']: r for r in result['statistics']}
        self.assertEqual([rows['two_pointers_made'][x] for x in ('home','away')], [9,11])
        self.assertEqual([rows['three_pointers_made'][x] for x in ('home','away')], [3,0])
        self.assertEqual([rows['free_throws_made'][x] for x in ('home','away')], [2,8])
        self.assertTrue(result['periods'])
        self.assertNotIn('field_goals_attempted', rows)
        self.assertNotIn('possessions', rows, 'possession changes are not completed possessions')
        self.assertNotIn('free_throws_attempted', rows, 'assigned free throws are not attempts')

    def test_deletions_and_identical_duplicate_ids_do_not_change_counts(self):
        data = fixture()
        expected = parse(data)['statistics']
        data['response']['events'] += [copy.deepcopy(data['response']['events'][0]),
            {'id': 9000000001, 'type': 1536, 'i1': 1, 'i2': 3, 'i3': 0, 'i4': 2},
            {'id': 9000000002, 'type': 1020, 'i2': 9000000001}]
        self.assertEqual(parse(data)['statistics'], expected)

    def test_mismatch_never_leaks_counters_into_model(self):
        for change in ('score', 'period', 'identity', 'conflicting_duplicate'):
            data = fixture()
            if change == 'score': data['live']['scores'][0][0]['c1'] = '99'
            if change == 'period': data['live']['scores'][1][0]['c1'] = '99'
            if change == 'identity': data['metadata']['fon_team1'] = 'different team'
            if change == 'conflicting_duplicate':
                row = dict(data['response']['events'][0], type=99999)
                data['response']['events'].append(row)
            self.assertEqual(parse(data)['statistics'], [], change)

    def test_reversed_broadcast_is_explicitly_swapped(self):
        data = fixture(); data['response']['extraInfo']['teamsReverse'] = True
        for group in data['live']['scores'][:2]:
            for row in group: row['c1'], row['c2'] = row['c2'], row['c1']
        result = parse(data)
        self.assertEqual(result['diagnostics']['state'], 'score_reconciled')
        row = next(x for x in result['statistics'] if x['metric_key']=='two_pointers_made')
        self.assertEqual((row['home'],row['away']), (11,9))

    def test_women_identity_markers_remain_distinct(self):
        for marker in ['WCBA', 'WKBL', 'Team (ж)', 'Team (w)', 'Женщины']:
            self.assertEqual(_gender(marker), 'women', marker)

    def test_unknown_foul_correction_does_not_leave_an_incorrect_count(self):
        data = fixture()
        data['response']['events'].append({'id':9000000000,'type':1558})
        result = parse(data)
        self.assertEqual(result['diagnostics']['state'], 'score_reconciled')
        self.assertNotIn('fouls', {r['metric_key'] for r in result['statistics']})


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_optional_provider_cache_never_replays_old_pari_counters(self):
        # A background snapshot precedes the next score. Its PARI values must
        # never be reattached as external data to a newer feed snapshot.
        event = {'id':'cache-test','team1':'A','team2':'B',
                 'feed_checked_at_utc':datetime.now(timezone.utc).isoformat(),
                 'external_statistics':[{'name':'turnovers','metric_key':'turnovers',
                    'home':1,'away':2,'source':source} for source in
                    ('PARI','PARI-PBP','PARI-derived','SofaScore')]}
        diagnostics = {'status':'no_live_events','statistics_errors':['timeout']}
        with patch.object(server,'_enrich_with_sofascore_live',AsyncMock(return_value=diagnostics)), \
             patch.dict(server._LIVE_ENRICHMENT_CACHE,{},clear=True):
            await server._refresh_live_enrichment_background([event])
            cached = server._LIVE_ENRICHMENT_CACHE['cache-test']
            self.assertEqual({r['source'] for r in cached['external_statistics']}, {'SofaScore'})
            self.assertEqual({r['source'] for r in event['external_statistics']},
                             {'PARI','PARI-PBP','PARI-derived','SofaScore'})

    async def test_women_match_is_visible_with_lineage_and_period_statistics(self):
        data = fixture(); event = data['event']; identity = event['id']
        payload = {'sports':[{'id':3,'name':'Баскетбол'},
                    {'id':event['sportId'],'parentId':3,'name':'Россия. Женщины. Суперлига'}],
                   'events':[event], 'eventMiscs':[], 'eventBlocks':[],
                   'liveEventInfos':[data['live']], 'customFactors':[]}
        original_client = httpx.AsyncClient
        def handle(request):
            if request.url.path.endswith('/sportscast/actrans'): value = {'items':[data['metadata']]}
            elif request.url.path.endswith('/sportscast/events'): value = data['response']
            else: value = payload
            return httpx.Response(200, json=value, request=request)
        def factory(**kwargs):
            return original_client(transport=httpx.MockTransport(handle))
        busy = Mock(); busy.done.return_value = False
        with patch.object(server.httpx,'AsyncClient',side_effect=factory), \
             patch.object(server,'_LIVE_ENRICHMENT_TASK',busy), \
             patch.dict(pari_data._METADATA,{},clear=True), patch.dict(pari_data._DETAILS,{},clear=True):
            result = await server._fetch_pari_live_events()
        self.assertTrue(result['ok'])
        event = next(e for e in result['events'] if e['id']==str(identity))
        self.assertEqual(event['gender'], 'women')
        self.assertTrue(event['timer_semantics_verified'])
        self.assertEqual(event['pari_match_centre']['state'], 'score_reconciled')
        stats = event['external_statistics']
        rows = [r for r in stats if r['metric_key']=='field_goals_made']
        self.assertEqual(len(rows), 1, 'do not duplicate the same made-shot derivation')
        self.assertEqual(rows[0]['match_status'], 'pari_event_id')
        self.assertEqual(rows[0]['freshness_status'], 'fresh')
        self.assertEqual(rows[0]['source'], 'PARI-PBP')
        self.assertTrue(all(r.get('observed_at_utc') for r in event['period_statistics']))
        self.assertIn('1', event['statistics_quality']['period_available_metrics'])
        self.assertIn('offensive_rebounds', event['statistics_quality']['possession_model_missing'])

