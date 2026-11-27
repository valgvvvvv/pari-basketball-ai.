import copy
import gzip
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import learning_model as lm
import learning_service as service
from learning_history import parse_game
from learning_pipeline import verified_training_row, prospective_report


class LearningTests(unittest.TestCase):
    def rows(self):
        rows = []
        for i in range(100):
            start = datetime(2025, 1, 1, tzinfo=timezone.utc)+timedelta(days=i)
            state = dict(current=90+i % 21, overall_current=90+i % 21, elapsed=2100, remaining=300,
                         period_elapsed=900, period_current=40+i % 7, difference=i % 15, duration=2400,
                         previous_rate=3.4, recent_rate=None if i % 2 else 3.7, statistics={})
            rows.append(dict(game_id=str(i), cohort='ncaa_m', target='match', period=2, started_at=start.isoformat(),
                             captured_at=(start+timedelta(hours=1)).isoformat(), completed_at=(start+timedelta(hours=2)).isoformat(),
                             actual=state['current']+20+i % 4, state=state, provenance='espn_historical_replay'))
        return rows

    def test_split_whole_games_no_test_labels_in_weights(self):
        rows = self.rows(); a = lm.train(rows); m = a['models']['ncaa_m:match:core']
        parts = [set(p) for p in m['partition_game_ids']]
        self.assertFalse(parts[0] & parts[1] or parts[1] & parts[2] or parts[0] & parts[2])
        changed = copy.deepcopy(rows)
        for r in changed:
            if r['game_id'] in parts[2]:
                r['actual'] += 100
        second = lm.train(changed)['models']['ncaa_m:match:core']
        self.assertEqual(m['coefficients'], second['coefficients'])
        self.assertEqual(m['calibration_errors'], second['calibration_errors'])
        self.assertGreater(second['report']['mae'], m['report']['mae'])
        self.assertFalse(a['calibrated']); self.assertFalse(a['automatic_priority_enabled'])

    def test_insufficient_games_and_unverified_rows_do_not_train(self):
        self.assertFalse(lm.train(self.rows()[:50])['models'])
        r = self.rows()
        for x in r:
            x['provenance'] = 'last_live_score'
        self.assertFalse(lm.train(r)['models'])

    def test_feature_extractor_cannot_read_outcome_or_unknown_statistics(self):
        r = self.rows()[0]; before = lm.features(r['state'])
        r['state']['actual'] = 9999; r['state']['future_boxscore'] = {'fga':100}
        self.assertEqual(before, lm.features(r['state']))
        with self.assertRaises(ValueError):
            lm.features(r['state'], 'full')
        r['state']['elapsed'] = 0
        with self.assertRaises(ValueError):
            lm.features(r['state'])

    def test_empirical_probabilities_exact_push_and_lower_bound(self):
        p = lm.empirical(10, 9, [-100, 0, 1], 10)
        self.assertEqual(p, dict(pOver=1/3, pUnder=1/3, pPush=1/3, calibrated=False))
        self.assertEqual(lm.empirical(10, 9, [-100, 0, 1], 9.5)['pPush'], 0)

    def event(self):
        return dict(id='g', sport_id='19166', period_count=2, period_length_seconds=1200,
                    period=2, clock='05:00', score=[60, 50], period_scores=[[30, 25], [30, 25]],
                    timer_semantics_verified=True, external_statistics=[], recent_snapshots=[])

    def test_live_scope_freshness_clock_and_period_arithmetic(self):
        now = datetime.now(timezone.utc).isoformat(); e = self.event()
        group, s = service.state_from_event(e, 'match', now)
        self.assertEqual((group, s['elapsed'], s['remaining'], s['current']), ('ncaa_m', 2100, 300, 110))
        for change in [dict(clock='20:00'), dict(timer_semantics_verified=False), dict(period_count=4), dict(period_scores=[[99, 99]])]:
            with self.assertRaises(ValueError):
                service.state_from_event({**e, **change}, 'match', now)
        with self.assertRaises(ValueError):
            service.state_from_event(e, 'match', '2020-01-01T00:00:00Z')
        self.assertIsNone(lm.cohort({'league':'NBA G League'}))
        self.assertIsNone(lm.cohort({'league':'NBA 2K'}))
        self.assertEqual(lm.cohort({'sport_id':'15100'}), ('ncaa_w', 4, 600))

    def test_optional_stats_require_explicit_eligibility_not_personal_fouls(self):
        e = self.event(); e['external_statistics'] = [
            dict(metric_key='team_fouls', home=2, away=3, calculation_eligible=False),
            dict(metric_key='personal_fouls', home=9, away=10, calculation_eligible=True),
            dict(metric_key='field_goals_attempted', home=40, away=38, calculation_eligible=True, score_basis=[60,50])]
        _, s = service.state_from_event(e, 'match', datetime.now(timezone.utc).isoformat())
        self.assertEqual(s['statistics'], {'field_goals_attempted':78})

    def test_bootstrap_artifact_real_split_and_roundtrip(self):
        path = Path(__file__).resolve().parents[1]/'models'/'learning-model.json'
        a = lm.load(path)
        self.assertGreaterEqual(len(a['models']), 4)
        for m in a['models'].values():
            parts = m['partition_game_ids']
            self.assertTrue(all(len(set(p)) >= n for p, n in zip(parts, (40,10,15))))
            self.assertFalse(set(parts[0]) & set(parts[2]))
            self.assertTrue(0 <= m['report']['interval80_coverage'] <= 1)

    def test_shadow_never_becomes_priority_or_retroactive_prediction(self):
        now = datetime.now(timezone.utc).isoformat()
        with patch.object(service, 'ARTIFACT', lm.train(self.rows())):
            p = service.predict(self.event(), 'match', now)
            self.assertEqual(p['status'], 'shadow')
            self.assertFalse(p['priority']); self.assertFalse(p['calibrated'])
            e = self.event();e['sport_id']='unknown'
            self.assertEqual(service.predict(e, 'match', now)['status'], 'waiting')

    def test_frozen_observation_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'LEARNING_STATE_PATH':tmp+'/l.sqlite3'}):
            now = datetime.now(timezone.utc).isoformat()
            with patch.object(service, 'ARTIFACT', lm.train(self.rows())):
                e=self.event(); p={t:service.predict(e,t,now) for t in ('match','period')}
                service.observe(e,now,p); before=service.export()
                p['match']['center'] = 999
                service.observe(e,now,p)
                self.assertEqual(before, service.export())

    def test_final_requires_two_matching_sources_and_prior_capture(self):
        r=dict(event_id='g', period=2,target='match',captured_at='2025-01-01T01:00:00Z',
               origin={'start_time':1735689600},prediction=dict(cohort='ncaa_m',state=self.rows()[0]['state'],center=120,interval80=[110,140],model_version='v'))
        self.assertIsNone(verified_training_row(r,[60,60],[60,61],['PARI','ESPN'],'2025-01-01T02:00:00Z'))
        self.assertIsNone(verified_training_row(r,[60,60],[60,60],['PARI'],'2025-01-01T02:00:00Z'))
        self.assertIsNone(verified_training_row(r,[60,60],[60,60],['PARI','ESPN'],'2025-01-01T00:00:00Z'))
        label=verified_training_row(r,[60,60],[60,60],['PARI','ESPN'],'2025-01-01T02:00:00Z')
        self.assertEqual(label['actual'],120)
        self.assertEqual(prospective_report({'id':label})[0]['games'],1)

    def test_historical_features_ignore_future_boxscore_and_validate_final(self):
        start=datetime(2025,1,1,tzinfo=timezone.utc)
        def play(p,clock,h,a,minute):
            return dict(period={'number':p},clock={'displayValue':clock},homeScore=h,awayScore=a,
                        wallclock=(start+timedelta(minutes=minute)).isoformat())
        payload=dict(format={'regulation':{'periods':2,'clock':1200}},header={'id':'x','competitions':[
            dict(date=start.isoformat(),status={'type':{'completed':True}},competitors=[
                dict(homeAway='home',score='80',linescores=[{'displayValue':'40'},{'displayValue':'40'}]),
                dict(homeAway='away',score='70',linescores=[{'displayValue':'35'},{'displayValue':'35'}])])]},
            plays=[play(1,'20:00',0,0,0),play(1,'10:00',20,18,20),play(1,'00:00',40,35,40),
                   play(2,'10:00',60,52,70),play(2,'05:00',72,62,85),play(2,'0.0',80,70,100)],boxscore={'teams':[{'future':'secret'}]})
        rows=parse_game(payload,'ncaa_m')
        self.assertTrue(rows)
        first=next(r for r in rows if r['period']==1)
        self.assertEqual(first['state']['overall_current'],38)
        payload['boxscore']={'future_fga':9999}
        self.assertEqual(rows,parse_game(payload,'ncaa_m'))
        payload['header']['competitions'][0]['competitors'][0]['score']='81'
        self.assertEqual(parse_game(payload,'ncaa_m'),[])

    def test_universal_forecast_any_league_all_periods_and_game_start(self):
        now=datetime.now(timezone.utc).isoformat()
        for length,count in ((600,4),(720,4),(1200,2),(480,4)):
            for period in range(1,count+1):
                for elapsed in (0,60,length//2,length-1):
                    previous=[[20,19] for _ in range(period-1)]
                    current=[int(elapsed/30),int(elapsed/33)]
                    score=[sum(q[j] for q in previous)+current[j] for j in (0,1)]
                    left=length-elapsed
                    e=dict(id='foreign',league='Unseen league',sport_id='foreign',period_count=count,period_length_seconds=length,
                           period=period,clock=f'{left//60:02}:{left%60:02}',score=score,period_scores=previous+[current],timer_semantics_verified=True)
                    for target in ('match','period'):
                        p=service.predict(e,target,now)
                        self.assertEqual(p['status'],'shadow',(length,count,period,elapsed,target,p.get('reason')))
                        self.assertEqual(p['model_scope'],'universal_transfer')
                        self.assertFalse(p['priority']);self.assertFalse(p['calibrated'])
                        self.assertGreaterEqual(p['center'],p['state']['current'])
                        self.assertTrue(p['interval80'][0]<=p['interval80'][1])

    def test_known_league_early_phase_uses_universal_without_weakening_signals(self):
        e=dict(id='early',sport_id='19166',period_count=2,period_length_seconds=1200,period=1,
               clock='19:00',score=[3,2],period_scores=[[3,2]],timer_semantics_verified=True)
        p=service.predict(e,'match',datetime.now(timezone.utc).isoformat())
        self.assertEqual(p['status'],'shadow');self.assertEqual(p['profile'],'universal')
        self.assertFalse(p['priority'])

    def test_universal_does_not_fabricate_period_score_or_missing_clock(self):
        now=datetime.now(timezone.utc).isoformat();e=self.event();e.update(sport_id='foreign',period_scores=[])
        self.assertEqual(service.predict(e,'match',now)['status'],'shadow')
        self.assertEqual(service.predict(e,'period',now)['status'],'waiting')
        for change in (dict(clock=None),dict(timer_semantics_verified=False),dict(score_conflict=True),dict(score=None)):
            self.assertEqual(service.predict({**e,**change},'match',now)['status'],'waiting')
        self.assertEqual(service.predict(e,'match','2020-01-01T00:00:00Z')['status'],'waiting')

    def test_expired_clock_is_observed_score_not_new_forecast(self):
        e=self.event();e.update(sport_id='foreign',clock='00:00')
        now=datetime.now(timezone.utc).isoformat()
        for target,actual in (('match',110),('period',55)):
            p=service.predict(e,target,now)
            self.assertEqual(p['status'],'complete');self.assertEqual(p['center'],actual)
            self.assertNotIn('market_prediction',p)

    def test_learning_export_paginates_without_duplicate_residual_payloads(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'LEARNING_STATE_PATH':tmp+'/l.sqlite3'}):
            now=datetime.now(timezone.utc).isoformat()
            for i in range(3):
                e=self.event();e['id']='game'+str(i)
                service.observe(e,now,{'match':service.predict(e,'match',now)})
            first=service.export(limit=1)
            rest=service.export(after_cursor=first[0]['capture_cursor'],limit=2)
            self.assertEqual(len(first+rest),3)
            self.assertEqual(len({r['id'] for r in first+rest}),3)
            self.assertNotIn('residual_sample',first[0]['prediction'])


if __name__ == '__main__':
    unittest.main()
