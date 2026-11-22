import copy
import unittest
from unittest.mock import patch, Mock

import httpx
import pari_data
import server


def fixture():
    event = {'id': 70112200, 'team1': 'Можи-дас-Крузис', 'team2': 'Паулистано',
             'sportId': 71558, 'level': 1, 'place': 'live', 'kind': 1}
    payload = {'sports': [{'id': 1, 'name': 'Баскетбол'}, {'id': 71558, 'parentId': 1, 'name': 'Бразилия. NBB'}],
               'events': [event], 'eventMiscs': [], 'eventBlocks': [],
               'liveEventInfos': [{'eventId': 70112200, 'timerSeconds': 1470, 'timerDirection': 0,
                                   'scoreFunction': 'Basketball',
                                   'scores': [[{'c1': '45', 'c2': '40'}],
                                              [{'c1': '15', 'c2': '16', 'title': 'четверть'},
                                               {'c1': '27', 'c2': '20', 'title': 'четверть'},
                                               {'c1': '3', 'c2': '4', 'title': 'четверть'}]]}],
               'customFactors': [{'e': 70112200, 'factors': [{'f': 930, 'pt': '153.5', 'v': 1.9},
                                                           {'f': 931, 'pt': '153.5', 'v': 1.8}]}]}
    info = {'fon_code': 70112200, 'fon_team1': event['team1'], 'fon_team2': event['team2'],
            'sport_code': 11726632, 'extra': {'quarters': '4x10'}}
    return payload, info


class DataContractTests(unittest.TestCase):
    def test_sparse_duplicate_broadcast_cannot_erase_explicit_format(self):
        payload, info = fixture()
        sparse = dict(info, extra={'coverage': 'tv'})
        for rows in [[info, sparse], [sparse, info]]:
            merged = pari_data.metadata_index(rows)['70112200']
            self.assertEqual(pari_data.explicit_format(payload['events'][0], merged, 'source')['period_length_seconds'], 600)
        conflict = pari_data.metadata_index([info, dict(info, extra={'quarters': '4x12'})])['70112200']
        self.assertIsNone(pari_data.explicit_format(payload['events'][0], conflict, 'source'))

    def test_format_requires_exact_identity_and_explicit_format(self):
        payload, info = fixture()
        event = payload['events'][0]
        self.assertEqual(pari_data.explicit_format(event, info, 'source')['period_length_seconds'], 600)
        for change in [{'fon_code': 99}, {'fon_team1': event['team2']}, {'sport_code': 11726525},
                       {'extra': {}}, {'extra': {'quarters': '4x20'}}]:
            self.assertIsNone(pari_data.explicit_format(event, dict(info, **change), 'source'))

    def test_detail_subtree_replacement_is_atomic_and_rejects_foreign_data(self):
        payload, _ = fixture()
        detail = copy.deepcopy(payload)
        detail['events'].append({'id': 80000000, 'parentId': 70112200})
        self.assertTrue(pari_data.merge_detail(payload, detail, 70112200))
        before = copy.deepcopy(payload)
        detail['customFactors'].append({'e': 99, 'factors': []})
        self.assertFalse(pari_data.merge_detail(payload, detail, 70112200))
        self.assertEqual(payload, before)

    def test_prices_never_cross_lines_and_blocks_remain_blocked(self):
        payload, _ = fixture()
        event = {'id': '70112200', 'factors': payload['customFactors'][0]['factors'], 'related_factor_groups': []}
        row = pari_data.markets_for(event, '2026-11-22T20:00:00Z', {})[0]
        self.assertEqual((row['overOdds'], row['underOdds']), (1.9, 1.8))
        self.assertTrue(row['includesOvertime'])
        self.assertTrue(row['pricesVerified'])
        self.assertTrue(pari_data.markets_for(event, 'now', {'70112200': [{'state': 'blocked'}]})[0]['suspended'])
        event['factors'][1]['pt'] = '154.5'
        self.assertEqual(pari_data.markets_for(event, 'now', {}), [])

    def test_exact_quarter_only_not_three_point_stat_market(self):
        payload, _ = fixture()
        factors = payload['customFactors'][0]['factors']
        event = {'id': '70112200', 'factors': [], 'related_factor_groups': [
            {'event_id': '8', 'kind': 100403, 'factors': factors},
            {'event_id': '9', 'kind': 30400403, 'factors': factors}]}
        rows = pari_data.markets_for(event, 'now', {})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['period'], 3)
        self.assertFalse(rows[0]['includesOvertime'])


class UpstreamIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_brazil_cold_start_uses_pari_event_specific_format_and_prices(self):
        payload, info = fixture()
        real_client = httpx.AsyncClient
        def response(request):
            data = {'items': [info]} if request.url.path == '/sportscast/actrans' else payload
            return httpx.Response(200, json=data, request=request)
        def client_factory(**kwargs):
            return real_client(transport=httpx.MockTransport(response))
        task = Mock()
        task.done.return_value = False
        with patch.object(server.httpx, 'AsyncClient', side_effect=client_factory), \
             patch.object(server, '_LIVE_ENRICHMENT_TASK', task), \
             patch.object(server, 'emit_diagnostic'), patch.dict(pari_data._METADATA, clear=True):
            result = await server._fetch_pari_live_events()
        event = result['events'][0]
        self.assertEqual(event['clock'], '05:30')
        self.assertTrue(event['timer_semantics_verified'])
        self.assertIn('match-centre', event['period_format_source']['source'])
        self.assertEqual(event['verified_total_markets'][0]['overOdds'], 1.9)
        self.assertEqual(event['verified_total_markets'][0]['underOdds'], 1.8)
        self.assertTrue(event['verified_total_markets'][0]['includesOvertime'])
