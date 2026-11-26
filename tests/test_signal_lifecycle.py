import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import gzip
import hmac
import json
import os
import sqlite3
from pathlib import Path
import time
import unittest
from unittest.mock import AsyncMock, patch
import uuid

import httpx
import server
import server_signals as signals
import signal_lifecycle as life
from signal_results import ResultsParser, final_evidence, primary_final_evidence

ROOT = Path(__file__).resolve().parents[1]


def card(target='quarter', line=44.5, period=2, identity='f1', direction='ТМ'):
    return {'forecast_id': identity, 'event': '42', 'teams': 'А — Б', 'experimental': True,
            'origin': {'id': '42', 'team1': 'А', 'team2': 'Б', 'period_count': 4},
            'signal': {'target': target, 'period': period, 'line': line, 'direction': direction}}


def event(period=3):
    return {'id': '42', 'team1': 'А', 'team2': 'Б', 'period': period, 'period_count': 4,
            'score': [45, 40], 'period_scores': [[20, 18], [20, 18], [5, 4]],
            'feed_checked_at_utc': datetime.now(timezone.utc).isoformat()}


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.path = ROOT/('test-lifecycle-'+uuid.uuid4().hex+'.sqlite3')
        self.env = patch.dict(os.environ, {'SIGNAL_LIFECYCLE_PATH': str(self.path),
            'SIGNAL_SERVER_ENABLED': '1', 'SIGNAL_BOT_TOKEN': 'test-key', 'SIGNAL_CHAT_ID': '123'})
        self.env.start()
        self.status = patch.dict(signals.STATUS, {'checkpoint_ready': True}, clear=True)
        self.status.start()
        signals.REPORT_LOCK, signals.RESULT_LOCK = asyncio.Lock(), asyncio.Lock()

    async def asyncTearDown(self):
        self.status.stop(); self.env.stop()
        for suffix in ('', '-wal', '-shm'):
            Path(str(self.path)+suffix).unlink(missing_ok=True)

    def save(self, row=None, number=1, at=None):
        life.register(row or card(), number, 'Исходный прогноз ТМ 44.5', at)
        return life.rows()[-1]

    def test_quarter_is_graded_only_after_that_exact_period_finished(self):
        self.save()
        life.observe([event(period=2)])
        self.assertIsNone(life.rows()[0]['result'])
        life.observe([event()])
        r = life.rows()[0]
        self.assertEqual(r['result']['total'], 38)
        self.assertEqual(r['result']['state'], 'won')
        self.assertIn('✅ ЗАШЁЛ', life.edited_text(r))
        self.assertTrue(life.edited_text(r).startswith('Исходный прогноз ТМ 44.5'))

    def test_a_disappearing_game_or_zero_clock_is_not_a_final(self):
        self.save(card(target='match'))
        life.observe([])
        e = event(4); e.update(clock='00:00', period_remaining_seconds=0)
        life.observe([e])
        self.assertIsNone(life.rows()[0]['result'])

    def test_conflicts_wrong_identity_and_unconfirmed_future_periods_are_blocked(self):
        self.save()
        for change in ({'id': 'other'}, {'team1': 'Другая'}, {'score': [99, 99]},
                       {'period_count': 2}, {'period_score_conflicts': ['conflict']},
                       {'period_scores': [[20, 18], [20, -1], [5, 4]]}):
            life.observe([{**event(), **change}])
            self.assertIsNone(life.rows()[0]['result'])

    def test_half_and_quarter_cannot_be_confused(self):
        self.save(card(target='half', period=1))
        life.observe([event()]); self.assertIsNone(life.rows()[0]['result'])
        e = {'id': '42', 'team1': 'А', 'team2': 'Б', 'period': 2, 'period_count': 2,
             'score': [45, 40], 'period_scores': [[40, 36], [5, 4]],
             'feed_checked_at_utc': datetime.now(timezone.utc).isoformat()}
        life.observe([e])
        self.assertEqual(life.rows()[0]['result']['state'], 'lost')

    def test_return_and_correction_are_not_counted_as_a_win(self):
        self.save(card(line=38))
        life.observe([event()]); self.assertEqual(life.rows()[0]['result']['state'], 'push')
        e = event(); e['score'] = [46, 40]; e['period_scores'][1][0] = 21
        life.observe([e]); r = life.rows()[0]
        self.assertEqual(r['result']['state'], 'disputed')
        previous = deepcopy(r['result'])
        life.observe([e]); self.assertEqual(life.rows()[0]['result'], previous)

    async def test_same_game_updates_and_new_targets_edit_one_message(self):
        state_path = self.path.with_suffix('.delivery.sqlite3')
        signals.STATUS['sent'] = 0
        with patch.object(signals, 'STATE_PATH', state_path), patch.object(signals, 'audit_delivery'), patch.object(signals, 'say', AsyncMock(return_value={'message_id': 90})) as send, patch.object(signals, 'telegram', AsyncMock()) as edit:
            signals.LOCK = asyncio.Lock()
            prepared = []
            for n, target in enumerate(('quarter', 'quarter', 'match')):
                c = card(target=target, line=44.5+n, identity='new-'+str(n))
                c['expires'] = (time.time()+60)*1000
                prepared.append(c)
                await signals.deliver([c])
            self.assertEqual(send.await_count, 1)
            self.assertEqual(edit.await_count, 2)
            self.assertEqual({r['message_id'] for r in life.rows()}, {90})
            self.assertEqual(len(life.rows()), 3)
            self.assertIn('44.5', edit.await_args.kwargs['text'])
            self.assertIn('45.5', edit.await_args.kwargs['text'])
            self.assertIn('Матч', edit.await_args.kwargs['text'])
            life.observe([event()])
            with patch('signal_results.check_finals', AsyncMock()):
                await signals.results_once()
            self.assertEqual(edit.await_count, 3)
            self.assertIn('✅', edit.await_args.kwargs['text'])
            self.assertIn('46.5', edit.await_args.kwargs['text'])
            # Reset the dedupe database: lifecycle receipts still suppress replay.
            state_path.unlink()
            await signals.deliver(prepared)
            self.assertEqual(send.await_count, 1)
            self.assertEqual(edit.await_count, 3)
        state_path.unlink(missing_ok=True)

    def test_legacy_unique_receipts_migrate_without_losing_originals(self):
        db = sqlite3.connect(self.path)
        db.execute('CREATE TABLE messages (id TEXT PRIMARY KEY, message_id INTEGER UNIQUE NOT NULL, sent_at REAL NOT NULL, payload TEXT NOT NULL, result TEXT, edited_hash TEXT)')
        db.commit(); db.close()
        self.save(card(identity='old'), 1)
        self.save(card(identity='new'), 1)
        checkpoint = life.export_state()
        self.assertEqual(len(checkpoint['rows']), 2)
        life.import_state(checkpoint)
        self.assertEqual(len(life.rows()), 2)

    def test_removed_footer_stays_removed_after_result_edit(self):
        original = '🔔 Сигнал\nРынок совместим; преимущество не проверено.\nИзменение линии — обновление наблюдения, а не новый вход.\n\nПредварительное направление. Вероятность и преимущество не подтверждены независимой проверкой.\nhttps://pari-basketball-ai.onrender.com/'
        life.register(card(), 1, original)
        life.observe([event()])
        text = life.group_text(life.rows())
        self.assertIn('✅', text)
        self.assertNotIn('https://pari-basketball', text)
        self.assertNotIn('преимущество не', text)

    async def test_edit_is_retried_and_success_is_not_edited_again(self):
        self.save(); life.observe([event()])
        send = AsyncMock(side_effect=[RuntimeError('temporary'), {'message_id': 1}])
        with patch('signal_results.check_finals', AsyncMock()), patch.object(signals, 'telegram', send):
            await signals.results_once(); self.assertIsNone(life.rows()[0]['edited_hash'])
            await signals.results_once(); await signals.results_once()
        self.assertEqual(send.await_count, 2)
        self.assertEqual(send.await_args.args[0], 'editMessageText')
        self.assertEqual(send.await_args.kwargs['message_id'], 1)
        self.assertEqual(send.await_args.kwargs['chat_id'], 123)
        self.assertIn('✅', send.await_args.kwargs['text'])

    async def test_pending_result_is_never_edited_as_a_loss(self):
        self.save()
        with patch('signal_results.check_finals', AsyncMock()), patch.object(signals, 'telegram', AsyncMock()) as send:
            await signals.results_once(); send.assert_not_awaited()

    def test_report_uses_sent_time_and_exact_rolling_24_hours(self):
        end = datetime(2026, 11, 26, 23, tzinfo=life.MSK)
        start = end-timedelta(hours=24)
        self.save(at=start.timestamp()-1)
        self.save(card(identity='start'), 2, start.timestamp())
        self.save(card(identity='end'), 3, end.timestamp())
        report = life.report_text(end)
        self.assertIn('Сообщений: 1', report)
        self.assertIn('25.11 23:00 — 26.11 23:00 МСК', report)
        self.assertIn('Ожидают подтверждения: 1', report)

    async def test_report_23_moscow_not_helsinki_and_chunk_retry(self):
        end = datetime.now(life.MSK).replace(hour=23, minute=0, second=0, microsecond=0)
        self.save(at=(end-timedelta(hours=1)).timestamp())
        with patch('signal_lifecycle.now_ts', return_value=(end-timedelta(hours=6)).timestamp()):
            # Establish the feature start before the simulated report cutoff.
            db = life.connect(); db.execute("UPDATE settings SET value=? WHERE key='started_at'", (str((end-timedelta(hours=6)).timestamp()),)); db.commit(); db.close()
        with patch.object(signals, 'say', AsyncMock(return_value={'message_id': 10})) as send:
            self.assertFalse(await signals.daily_report_once(end-timedelta(seconds=1)))
            self.assertTrue(await signals.daily_report_once(end))
            self.assertFalse(await signals.daily_report_once(end+timedelta(minutes=1)))
        self.assertEqual(send.await_count, 1)
        self.assertIn('МСК', send.await_args.args[0])
        state = {'chunks': ['part1', 'part2'], 'sent': [10]}
        life.report_receipt(end.timestamp(), state)
        with patch.object(signals, 'say', AsyncMock(return_value={'message_id': 11})) as send:
            await signals.daily_report_once(end)
            self.assertTrue(send.await_args.args[0].startswith('part2'))
            self.assertEqual(send.await_count, 1)

    def test_checkpoint_restores_results_receipts_and_does_not_overwrite_original(self):
        self.save(); life.observe([event()])
        r = life.rows()[0]; life.mark_edited(r['id'], life.edited_text(r))
        checkpoint = life.export_state()
        self.path.unlink()
        life.import_state(checkpoint); life.import_state(checkpoint)
        self.assertEqual(len(life.rows()), 1)
        self.assertEqual(life.rows()[0]['edited_hash'], checkpoint['rows'][0]['edited_hash'])
        wrong = deepcopy(checkpoint); wrong['rows'][0]['payload']['original_text'] = 'altered'
        with self.assertRaises(ValueError): life.import_state(wrong)
        self.assertEqual(life.rows()[0]['payload']['original_text'], 'Исходный прогноз ТМ 44.5')

    def test_finished_protocol_keeps_overtime_out_of_regulation_prediction(self):
        r = self.save(card(target='match', line=170.5))
        primary = event(); primary.update(period_count=4, period_scores=[[20, 20]]*4, score=[80, 80])
        final = {'id': '42', 'final': True, 'teams': ['А', 'Б'], 'periods': [[20, 20]]*4+[[15, 12]],
                 'score': [95, 92], 'url': 'https://scorebasketball.ru/match/test/'}
        evidence = final_evidence(r, final, primary)
        self.assertEqual(life.result_for(r, evidence)['total'], 160)
        self.assertEqual(life.result_for(r, evidence)['state'], 'won')
        for change in ({'final': False}, {'id': 'other'}, {'score': [95, 90]}, {'teams': ['Б', 'А']}):
            self.assertIsNone(final_evidence(r, {**final, **change}, primary))

    def test_parser_requires_published_final_class_and_preserves_periods(self):
        p = ResultsParser(); p.feed('<article class="mrow is-fin" data-id="42"><span class="tn">А</span><span class="tn">Б</span><div class="m-per">20:18, 20:18</div><div class="m-score"><b>40</b><b>36</b></div><a class="m-link" href="/match/a-b/"></a></article>')
        self.assertTrue(p.rows['42']['final'])
        self.assertEqual(p.rows['42']['score'], [40, 36])
        self.assertEqual(p.rows['42']['periods'], [[20, 18], [20, 18]])

    def test_primary_pari_final_flags_confirm_real_ncaa_two_half_protocol(self):
        r = self.save(card(target='match', line=154.5))
        r['payload']['origin']['period_count'] = 2
        payload = {'events': [{'id': 42, 'team1': 'А', 'team2': 'Б'}],
            'eventMiscs': [{'id': 42, 'fin': True, 'score1': 83, 'score2': 76, 'ef': '2x20'}],
            'liveEventInfos': [{'eventId': 42, 'finished': True, 'scoreFunction': 'Basketball',
                'scores': [[{'c1': '83', 'c2': '76'}], [
                    {'c1': '44', 'c2': '36', 'title': 'четверть'},
                    {'c1': '39', 'c2': '40', 'title': 'четверть'}]]}]}
        evidence = primary_final_evidence(r, payload, 'https://primary/42')
        self.assertEqual(life.result_for(r, evidence)['total'], 159)
        self.assertEqual(life.result_for(r, evidence)['state'], 'lost')
        for key, value in [('fin', False), ('score1', 82), ('ef', '4x10')]:
            broken = deepcopy(payload); broken['eventMiscs'][0][key] = value
            self.assertIsNone(primary_final_evidence(r, broken, 'https://primary/42'))
        broken = deepcopy(payload); broken['eventMiscs'][0].pop('fin'); broken['liveEventInfos'][0]['finished'] = False
        self.assertIsNone(primary_final_evidence(r, broken, 'https://primary/42'))

    def test_primary_finished_protocol_rejects_statistics_and_overtime_ambiguity(self):
        r = self.save(card(target='match'))
        payload = {'events': [{'id': 42, 'team1': 'А', 'team2': 'Б'}],
            'eventMiscs': [{'id': 42, 'fin': True, 'score1': 95, 'score2': 92}],
            'liveEventInfos': [{'eventId': 42, 'finished': True, 'scoreFunction': 'Basketball',
                'scores': [[{'c1': '95', 'c2': '92'}], [{'c1': '20', 'c2': '20', 'title': 'четверть'}]*4]}]}
        self.assertIsNone(primary_final_evidence(r, payload, 'source'))
        payload['eventMiscs'][0]['comment'] = 'ОТ ИТОГ'
        self.assertEqual(sum(primary_final_evidence(r, payload, 'source')['score']), 160)
        payload['liveEventInfos'][0]['scores'][1][0] = {'c1': '20', 'c2': '20', 'title': 'трехочковые попадания', 'kindId': '420400'}
        self.assertIsNone(primary_final_evidence(r, payload, 'source'))

    async def test_checkpoint_endpoint_rejects_unsigned_and_wrong_owner(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url='http://test') as client:
            self.assertEqual((await client.post('/internal/signals/lifecycle', json={})).status_code, 401)
            body = json.dumps({'owner': 'other', 'state': {}}).encode(); ts = str(time.time())
            sig = hmac.new(b'test-key', ts.encode()+b'.'+body, hashlib.sha256).hexdigest()
            response = await client.post('/internal/signals/lifecycle', content=body,
                headers={'X-Signal-Timestamp': ts, 'X-Signal-Signature': sig})
            self.assertEqual(response.status_code, 403)

    async def test_signed_checkpoint_recovers_state_without_publishing_or_sending_report_early(self):
        self.save(); state = life.export_state()
        body = gzip.compress(json.dumps({'owner': '123', 'state': state}).encode()); ts = str(time.time())
        sig = hmac.new(b'test-key', ts.encode()+b'.'+body, hashlib.sha256).hexdigest()
        with patch.object(signals, 'results_once', AsyncMock()), patch.object(signals, 'daily_report_once', AsyncMock()):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url='http://test') as client:
                response = await client.post('/internal/signals/lifecycle', content=body,
                    headers={'Content-Encoding': 'gzip', 'X-Signal-Timestamp': ts, 'X-Signal-Signature': sig})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['rows']), 1)

    def test_long_report_is_split_within_telegram_limit(self):
        parts = life.chunks(('А'*5000+'\n')*3)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= 3700 for p in parts))
