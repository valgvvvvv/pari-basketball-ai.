from copy import deepcopy
import os
import time
import unittest
from unittest.mock import patch

import signal_review as review
import server_signals as signals


def card():
    return dict(event='a',teams='A - B',forecast_id='f',expires=(time.time()+90)*1000,
        signal=dict(target='quarter',period=2,line=40.5,direction='ТМ',probability=.85,watchlist=True,
            reasonCodes=['calibration','validation'],robustness=dict(available=True,directionStable=True,probabilityMin=.75)))


def receipt(identity='first', at=1, total=45, line=40.5):
    c=card(); c['signal']['line']=line
    return dict(id=identity,sent_at=at,payload=dict(event='a',teams='A - B',signal=c['signal']),
        result=dict(state='won' if total<line else 'lost' if total>line else 'push',target='quarter',period=2,
            line=line,total=total,score=[20,total-20],evidence=dict(basis='primary_completed_period')))


class ReviewTests(unittest.TestCase):
    def prediction(self,center):
        return dict(status='shadow',center=center,state={'current':20},residual_sample=[-2,-1,0,1,2],model_version='frozen-v1')

    def test_exact_target_and_line_challenger_disagreement(self):
        event={'learning':{'period':self.prediction(48),'match':self.prediction(30)}}
        r=review.review(card(),event)
        self.assertTrue(r['available']);self.assertFalse(r['allow'])
        self.assertEqual(r['reason'],'challenger_disagrees')
        self.assertFalse(r['calibrated'])
        event['learning']['period']=self.prediction(34)
        self.assertTrue(review.review(card(),event)['allow'])

    def test_missing_model_is_not_fabricated_agreement(self):
        r=review.review(card(),{'learning':{'period':{'status':'waiting'}}})
        self.assertFalse(r['available']);self.assertTrue(r['allow'])
        self.assertEqual(r['reason'],'challenger_unavailable')

    def test_queue_omits_disagreement_but_retains_site_candidate(self):
        c=card()
        data=dict(checked_at_utc='2026-11-28T00:00:00Z',research_candidates=[c],events=[{'id':'a','learning':{'period':self.prediction(48)}}])
        with patch.dict(os.environ,{'SIGNAL_EXPERIMENTAL_ENABLED':'1'}),patch.object(signals,'PENDING',{}),patch.object(signals,'RETRY_AT',{}):
            signals.queue_notifications(data)
            self.assertEqual(signals.PENDING,{})
            self.assertEqual(signals.STATUS['challenger_disagreements'],1)
            self.assertEqual(data['research_candidates'],[c])
            data['events'][0]['learning']['period']=self.prediction(34)
            data['events'][0]['score']=[10,10]
            signals.queue_notifications(data)
            queued=next(iter(signals.PENDING.values()))
            self.assertIn('entry_state',queued)
            self.assertTrue(queued['delivery_review']['allow'])
            data['events'][0]['score'][0]=99
            self.assertEqual(queued['entry_state']['score'],[10,10])
            self.assertNotIn('delivery_review',c,'do not rewrite the frozen detector decision')

    def test_first_market_not_each_line_is_accuracy_unit(self):
        a=receipt(); update=receipt('update',2,line=46.5)
        summary=review.summarize([update,a])
        self.assertEqual(summary['all_receipts']['states'],{'lost':1,'won':1})
        self.assertEqual(summary['first_opportunities']['states'],{'lost':1})
        self.assertEqual(summary['updates'],1)

    def test_pending_and_bad_result_never_count_as_losses(self):
        a=receipt();a['result']=None
        b=receipt('bad');b['payload']['event']='b';b['result']['state']='won'
        s=review.summarize([a,b])['first_opportunities']
        self.assertEqual(s['resolved'],0);self.assertIsNone(s['win_rate'])
        self.assertEqual(s['states'],{'pending':1,'invalid_result':1})

    def test_incompatible_overtime_does_not_enter_compatible_rate(self):
        a=receipt();a['payload']['signal'].update(marketReady=True,reasonCodes=['overtime'])
        b=receipt('b');b['payload']['event']='b';b['payload']['signal'].update(marketReady=True,reasonCodes=[])
        s=review.summarize([a,b])
        self.assertEqual(s['first_opportunities']['records'],2)
        self.assertEqual(s['compatible_first_opportunities']['records'],1)

    def test_push_has_no_win_loss_and_brier_uses_frozen_probability(self):
        a=receipt(total=40,line=40)
        s=review.summarize([a])['first_opportunities']
        self.assertEqual(s['states'],{'push':1});self.assertEqual(s['resolved'],0)
        s=review.summarize([receipt()])['first_opportunities']
        self.assertEqual(s['brier'],.7225)

    def test_same_event_markets_are_not_reported_as_independent_games(self):
        a=receipt(); b=receipt('second');b['payload']['signal']['period']=3;b['result']['period']=3
        s=review.summarize([a,b])['first_opportunities']
        self.assertEqual(s['records'],2);self.assertEqual(s['events'],1)
