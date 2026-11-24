"""20-minute targets must preserve time, score, market and settlement scope."""
import asyncio
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import httpx
import pari_data
import server
import server_detector
import server_signals
from evaluate_forecasts import evaluate


def fixture():
    return json.loads(Path(__file__).with_name('ncaa_halves_fixture.json').read_text(encoding='utf-8'))


def half_event(period=2, elapsed=489):
    score = [64, 49] if period == 2 else [15, 15]
    remaining = 1200 - elapsed
    now = datetime.now(timezone.utc).isoformat()
    return {'id':'halves-test', 'team1':'A','team2':'B', 'league':'NCAA test',
        'score':score,'period':period,'clock':f'{remaining//60:02d}:{remaining%60:02d}',
        'period_count':2,'period_length_seconds':1200,'format_confirmed':True,
        'timer_semantics_verified':True,'clock_integrity':{'valid':True},
        'feed_checked_at_utc':now,'match_elapsed_seconds':(period-1)*1200+elapsed,
        'period_elapsed_seconds':elapsed,
        'period_scores':[[50,34],[14,15]] if period == 2 else [[15,15]],
        'verified_total_markets':[{'eventId':'halves-test','target':'half','period':period,
            'line':76.5,'pricesVerified':True,'overOdds':1.9,'underOdds':1.9,
            'quotedAtUtc':now,'rulesConfirmed':True,'ruleSource':'fixture-half-rule',
            'includesOvertime':False,'suspended':False}]}


class HalfCalculationTests(unittest.TestCase):
    def calculate(self, event):
        result = server_detector.calculate({'events':[event],
            'checked_at_utc':datetime.now(timezone.utc).isoformat()})
        self.assertEqual(result['errors'], [])
        return result

    def test_second_half_score_and_exposure_are_not_full_game_or_quarter(self):
        result = self.calculate(half_event())
        row = next(r for r in result['evaluations'] if r['decision']['target']=='half')
        self.assertEqual(row['prediction']['current'],29)
        self.assertAlmostEqual(row['prediction']['center'],76.8,places=1)
        self.assertEqual(row['prediction']['periodCount'],2)
        self.assertEqual(row['prediction']['periodLengthSeconds'],1200)
        self.assertNotIn('window',row['decision']['reasonCodes'])
        self.assertFalse(row['decision']['priority'])
        self.assertIn('calibration',row['decision']['reasonCodes'])

    def test_first_half_has_20_minute_horizon_without_fictitious_history(self):
        row = self.calculate(half_event(1,480))['evaluations'][0]
        self.assertEqual(row['decision']['target'],'half')
        self.assertEqual(row['prediction']['current'],30)
        self.assertAlmostEqual(row['prediction']['center'],75.8,places=1)
        self.assertNotIn('preliminary',row['decision']['reasonCodes'])

    def test_half_watchlist_window_is_separate_from_quarter_window(self):
        for elapsed, blocked in [(479,True),(480,False),(960,False),(961,True)]:
            result = self.calculate(half_event(2,elapsed))
            row = result['evaluations'][0]['decision']
            self.assertEqual('window' in row['reasonCodes'],blocked)

    def test_unknown_second_half_ot_rules_remain_blocked(self):
        event=half_event()
        event['verified_total_markets'][0].update(rulesConfirmed=False,includesOvertime=None)
        row=self.calculate(event)['evaluations'][0]['decision']
        self.assertIn('rules',row['reasonCodes'])
        self.assertIsNone(row['comparison'])

    def test_overtime_and_score_conflict_do_not_generate_half_forecast(self):
        for patch_data in [{'period':3},{'score':[30,30]}]:
            result=self.calculate(dict(half_event(),**patch_data))
            self.assertEqual(result['evaluations'],[])

    def test_explicit_half_score_codes_do_not_contaminate_four_quarter_games(self):
        row={'subscores':[{'kindId':'100302','c1':'14','c2':'15'}]}
        self.assertEqual(server._pari_period_score_records(row),[])
        self.assertEqual(server._pari_period_score_records(row,2)[0]['period'],2)

    def test_half_notification_identity_does_not_merge_first_and_second(self):
        first={'event':'g','signal':{'target':'half','period':1,'line':76.5,'direction':'ТБ'}}
        second=copy.deepcopy(first);second['signal']['period']=2
        self.assertNotEqual(server_signals.key_for(first),server_signals.key_for(second))
        self.assertNotEqual(server_signals.exposure_key(first),server_signals.exposure_key(second))

    def test_independent_evaluation_keeps_both_half_targets(self):
        rows=[{'id':str(period),'event_id':'g','captured_at_utc':f'2026-11-24T0{period}:00:00Z',
            'decision':{'target':'half','period':period,'line':76.5,'reasonCodes':[]},
            'prediction':{'center':76.8}} for period in (1,2)]
        report=evaluate(rows)
        self.assertEqual(report['selected_checkpoints'],2)
        self.assertIn('half',report['partitions']['test'])


class RealNCAAPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_2x20_score_timer_and_half_market_reach_calculation(self):
        payload=fixture()
        original_client=httpx.AsyncClient
        def handle(request):
            value={'items':[]} if request.url.path.endswith('/sportscast/actrans') else payload
            return httpx.Response(200,json=value,request=request)
        def factory(**kwargs):
            return original_client(transport=httpx.MockTransport(handle))
        busy=Mock();busy.done.return_value=False
        with patch.object(server.httpx,'AsyncClient',side_effect=factory), \
             patch.object(server,'_LIVE_ENRICHMENT_TASK',busy), \
             patch.dict(pari_data._METADATA,{},clear=True),patch.dict(server._LIVE_ENRICHMENT_CACHE,{},clear=True):
            result=await server._fetch_pari_live_events()
        event=next(e for e in result['events'] if e['id']=='69999271')
        self.assertEqual(event['period_count'],2)
        self.assertEqual(event['period_length_seconds'],1200)
        self.assertEqual(event['period'],2)
        self.assertEqual(event['clock'],'11:51')
        self.assertTrue(event['timer_semantics_verified'])
        markets=event['verified_total_markets']
        half=next(m for m in markets if m['target']=='half')
        self.assertEqual(half['sourceEventId'],'70211425')
        self.assertEqual(half['period'],2)
        self.assertTrue(half['pricesVerified'])
        self.assertFalse(half['rulesConfirmed'],'catalog confirms half identity, not second-half OT settlement')
        calculated=await asyncio.to_thread(server_detector.calculate,result)
        self.assertEqual(calculated['errors'],[])
        rows=[r for r in calculated['evaluations'] if r['decision']['target']=='half']
        self.assertTrue(rows)
        self.assertEqual(rows[0]['prediction']['current'],29)
        self.assertAlmostEqual(rows[0]['prediction']['center'],76.8,places=1)
