import copy
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch
import server
import pari_ssis
from test_pari_ssis import fixture


class CalculationAuditTests(unittest.TestCase):
    def test_completed_period_survives_new_score_but_not_a_score_correction(self):
        d=fixture(); e=d['event']; e.update(period=3,period_scores=[[22,20],[22,21],[10,7]])
        r=pari_ssis.parse_statistics(d['statistics'],e,datetime.now(timezone.utc).isoformat())
        e.update(period_statistics=r['periods'],external_statistics=r['statistics'])
        e['score']=[56,48]
        server._finalize_live_statistics([e])
        self.assertEqual({r['period'] for r in e['period_statistics']},{1,2})
        self.assertTrue(all(r['calculation_eligible'] for r in e['period_statistics']))
        e['period_scores'][0]=[21,20]
        server._finalize_live_statistics([e])
        self.assertEqual({r['period'] for r in e['period_statistics']},{2})

    def test_missing_score_is_not_reported_as_lag(self):
        d=fixture()
        for p in d['statistics']['response']['participants']:p['stats']=[{'kind':'1','value':'0'}]
        r=pari_ssis.parse_statistics(d['statistics'],d['event'],datetime.now(timezone.utc).isoformat())
        self.assertEqual(r['state'],'score_unavailable')

    def test_canonical_subset_metrics_are_not_total_turnovers_or_fouls(self):
        e={'external_statistics':[{'name':k,'metric_key':k,'home':2,'away':3,'source':'PARI'}
                                  for k in ('team_turnovers','technical_fouls')]}
        server._finalize_live_statistics([e])
        self.assertEqual({r['metric_key'] for r in e['external_statistics']},{'team_turnovers','technical_fouls'})
        self.assertIn('turnovers',e['statistics_quality']['possession_model_missing'])

    def test_period_composites_are_expanded_once_with_lineage(self):
        e={'id':'p','feed_checked_at_utc':datetime.now(timezone.utc).isoformat(),
           'period_statistics':[{'metric_key':'field_goals','name':'field goals','period':1,
                                 'home':'8/17','away':'9/20','source':'PARI'}]}
        server._finalize_live_statistics([e]);before=copy.deepcopy(e['period_statistics'])
        server._finalize_live_statistics([e])
        self.assertEqual(len(e['period_statistics']),len(before))
        r=next(r for r in before if r['metric_key']=='field_goals_attempted')
        self.assertEqual((r['home'],r['away']),(17,20))
        self.assertTrue(r['calculation_eligible'])

    def test_stale_source_is_visible_but_not_available_for_calculation(self):
        now=datetime.now(timezone.utc)
        e={'id':'s','external_statistics':[{'metric_key':'turnovers','name':'turnovers',
            'home':7,'away':8,'source':'PARI','observed_at_utc':(now-timedelta(minutes=3)).isoformat()}]}
        server._finalize_live_statistics([e])
        self.assertEqual(len(e['external_statistics']),1)
        self.assertNotIn('turnovers',e['statistics_quality']['calculation_metrics'])
        self.assertTrue(e['statistics_quality']['excluded_rows'])
        e['external_statistics'][0]['observed_at_utc']=(now+timedelta(minutes=3)).isoformat()
        server._finalize_live_statistics([e])
        self.assertEqual(e['external_statistics'][0]['freshness_status'],'future_timestamp')

    def test_same_value_with_new_observation_is_not_deduplicated_into_stale_row(self):
        now=datetime.now(timezone.utc)
        base={'metric_key':'turnovers','name':'turnovers','home':7,'away':8,'source':'PARI'}
        e={'id':'duplicate','external_statistics':[
            {**base,'observed_at_utc':(now-timedelta(minutes=3)).isoformat()},
            {**base,'observed_at_utc':now.isoformat()}]}
        server._finalize_live_statistics([e])
        self.assertIn('turnovers',e['statistics_quality']['calculation_metrics'])
        self.assertEqual(e['external_statistics'][0]['freshness_status'],'fresh')


class ProviderIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_external_exception_cannot_discard_native_history(self):
        d=fixture(); e=d['event']
        async def native(events):
            events[0]['team_history']=pari_ssis.parse_review(d['review'],e,datetime.now(timezone.utc).isoformat())
            return {'matched':1,'errors':[]}
        with patch.object(pari_ssis,'enrich',side_effect=native), \
             patch.object(server,'_enrich_with_sofascore_live',AsyncMock(side_effect=RuntimeError('offline'))), \
             patch.dict(server._LIVE_ENRICHMENT_CACHE,{},clear=True):
            await server._refresh_live_enrichment_background([e])
            self.assertTrue(server._LIVE_ENRICHMENT_CACHE[e['id']]['team_history'])
            self.assertEqual(server._LIVE_ENRICHMENT_LAST_STATUS['status'],'partial')

