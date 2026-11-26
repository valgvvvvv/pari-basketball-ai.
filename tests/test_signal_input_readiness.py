import copy
import json
import unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import AsyncMock,Mock,patch
import espn_college
import pari_ssis
import server
from match_identity import match_context_compatible,_competition_key


def fixture():
    return json.loads(Path(__file__).with_name('espn_college_fixture.json').read_text(encoding='utf8'))


class ReadinessTests(unittest.TestCase):
    def test_count_formatting_does_not_create_a_false_provider_conflict(self):
        now=datetime.now(timezone.utc).isoformat()
        event={'id':'same-count','external_statistics':[
            {'name':'field_goals_made','metric_key':'field_goals_made','home':28,'away':20,'source':'PARI','observed_at_utc':now},
            {'name':'field_goals_made','metric_key':'field_goals_made','home':28.0,'away':'20.0','source':'ESPN','observed_at_utc':now,'match_status':'exact_event_id_match'}]}
        server._finalize_live_statistics([event])
        self.assertFalse(event['statistics_quality']['conflicts'])
        event['external_statistics'][1]['home']=29
        server._finalize_live_statistics([event])
        self.assertTrue(event['statistics_quality']['conflicts'])

    def test_recorded_women_boxscore_preserves_team_orientation_and_attempts(self):
        d=fixture();now=datetime.now(timezone.utc).isoformat()
        rows,diag=espn_college.parse_summary(d['summary'],d['event'],d['board'],now)
        self.assertEqual(diag['state'],'score_reconciled')
        event=d['event'];event['external_statistics']=rows
        server._finalize_live_statistics([event]);q=event['statistics_quality']
        for key in ('field_goals_attempted','free_throws_attempted','offensive_rebounds','turnovers'):
            self.assertIn(key,q['calculation_metrics'])
        self.assertNotIn('team_fouls',q['calculation_metrics'],'whole-game fouls cannot invent current-period team fouls')
        fga=next(r for r in event['external_statistics'] if r['metric_key']=='field_goals_attempted')
        self.assertEqual((fga['home'],fga['away']),(28,29))
        self.assertEqual(fga['observed_at_utc'],now)

    def test_wrong_date_score_team_and_summary_id_are_rejected(self):
        d=fixture();now=datetime.now(timezone.utc).isoformat()
        for field in ('date','score','team','summary'):
            v=copy.deepcopy(d)
            if field=='date':v['event']['start_time']-=86400
            if field=='score':v['event']['score'][0]+=1
            if field=='team':v['event']['team_history']['teams'][0]['participant_id']='wrong'
            if field=='summary':v['summary']['header']['competitions'][0]['id']='wrong'
            self.assertFalse(espn_college.parse_summary(v['summary'],v['event'],v['board'],now)[0],field)

    def test_mascot_alone_is_not_a_team_identity(self):
        self.assertNotIn('cougars',espn_college.aliases({'name':'Cougars','location':'Washington State'}))

    def test_gender_requires_both_sources_and_ncaa_homoglyphs_are_canonical(self):
        pari={'team1':'A (ж)','team2':'B (ж)','league':'США. Женщины. NCAA','start_time':1795730400,'period_count':4,'period_length_seconds':600}
        other={'home':'A','away':'B','league':'NCAA women','start_time':1795730400,'period_count':4,'period_length_seconds':600}
        self.assertTrue(match_context_compatible(pari,other))
        self.assertFalse(match_context_compatible(pari,{**other,'league':'NCAA men'}))
        self.assertEqual(_competition_key('NСАА'),'ncaa')
        self.assertEqual(server.PARI_COMPETITION_FORMATS['15100']['period_length_seconds'],600)


class FastPublicationTests(unittest.IsolatedAsyncioTestCase):
    async def test_fast_refresh_does_not_wait_for_the_slow_provider_task(self):
        d=fixture();e=d['event'];now=datetime.now(timezone.utc).isoformat()
        async def fresh(events):
            events[0]['external_statistics'],events[0]['espn_college_diagnostics']=espn_college.parse_summary(d['summary'],e,d['board'],now)
            return {'matched':1}
        with patch.object(espn_college,'enrich',side_effect=fresh),patch.object(espn_college,'FAST_TASK',None),patch.dict(espn_college.FAST_CACHE,{},clear=True):
            espn_college.apply_and_queue([e]);task=espn_college.FAST_TASK
            self.assertIsNotNone(task);await task
            busy=Mock();busy.done.return_value=False
            with patch.object(espn_college,'FAST_TASK',busy):
                espn_college.apply_and_queue([e])
                self.assertTrue(e['external_statistics'])
                self.assertEqual(e['external_statistics'][0]['observed_at_utc'],now)
                e['score'][0]+=1
                server._finalize_live_statistics([e])
                self.assertFalse(e['external_statistics'],'new score cannot reuse old counters')

    async def test_independent_data_is_published_before_slow_provider_finishes(self):
        d=fixture();event=d['event']
        async def native(events):return {'matched':0}
        async def college(events):
            rows,diag=espn_college.parse_summary(d['summary'],event,d['board'],datetime.now(timezone.utc).isoformat())
            events[0]['external_statistics']=rows;events[0]['espn_college_diagnostics']=diag
            return {'matched':1}
        async def slow(events):
            cached=server._LIVE_ENRICHMENT_CACHE[event['id']]
            self.assertTrue(cached['external_statistics'])
            self.assertEqual(cached['diagnostics']['status'],'native_ready_external_pending')
            return {'status':'completed'}
        with patch.object(pari_ssis,'enrich',side_effect=native),patch.object(espn_college,'enrich',side_effect=college), \
             patch.object(server,'_enrich_with_sofascore_live',side_effect=slow),patch.dict(server._LIVE_ENRICHMENT_CACHE,{},clear=True):
            await server._refresh_live_enrichment_background([event])

