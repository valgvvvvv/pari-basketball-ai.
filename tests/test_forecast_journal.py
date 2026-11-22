import copy
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch

import forecast_journal as journal
from evaluate_forecasts import evaluate, load_database


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dbpath = self.tmp.name + '/journal.sqlite3'
        self.env = patch.dict(os.environ, {'FORECAST_JOURNAL_PATH': self.dbpath})
        self.env.start()
        self.row = {'event_id': 'g1', 'snapshot_id': 's1', 'captured_at_utc': '2026-01-01T10:00:00Z',
            'decision': {'target': 'match', 'period': 4, 'line': 165.5, 'direction': 'ТМ', 'status': 'research',
                'reasonCodes': ['calibration', 'validation'], 'probability': .7, 'pOver': .3, 'pUnder': .7, 'pPush': 0,
                'market': {'rulesConfirmed': True, 'ruleSource': 'rules', 'includesOvertime': False},
                'comparison': {'odds': 1.8, 'noVigOver': .5, 'noVigUnder': .5}},
            'prediction': {'center': 162, 'interval80': [150, 174], 'current': 140, 'period': 4, 'clock': '05:00', 'periodLengthSeconds': 600}}
        self.final = {'event_id': 'g1', 'final_status': 'finished', 'confirmed': True, 'identity_verified': True,
            'independent_confirmation': True, 'source': 'official+second', 'regulation_total': 160, 'total_with_overtime': 190,
            'line': 165.5, 'includes_overtime': False, 'market_rule_confirmed': True}

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def save(self):
        journal.record([self.row], 'source-hash')
        return journal.export_rows()[0]['id']

    def test_tempo_baseline_uses_actual_period_count(self):
        self.row['prediction'].update(current=120, period=2, clock='05:00', periodLengthSeconds=1200, periodCount=2)
        identity = self.save()
        journal.confirm(identity, self.final)
        report = evaluate(load_database(self.dbpath))
        self.assertAlmostEqual(report['partitions']['development']['match']['simple_tempo_mae'], abs(120*2400/2100-160))

    def test_unknown_period_count_has_no_fabricated_baseline(self):
        identity = self.save()
        journal.confirm(identity, self.final)
        report = evaluate(load_database(self.dbpath))
        self.assertIsNone(report['partitions']['development']['match']['simple_tempo_mae'])

    def test_immutable_deduplicated_snapshot_and_model_hash(self):
        self.save(); self.save()
        self.assertEqual(journal.status()['forecasts'], 1)
        with closing(journal.connect()) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('UPDATE forecasts SET event_id="changed"')
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('DELETE FROM forecasts')
        journal.record([self.row], 'different-model')
        self.assertEqual(journal.status()['forecasts'], 2)

    def test_settlement_requires_prediction_rule_identity_and_original_line(self):
        identity = self.save()
        for patching in [{'includes_overtime': True}, {'line': 166.5}]:
            with self.assertRaises(ValueError):
                journal.confirm(identity, {**self.final, **patching})
        self.assertEqual(journal.confirm(identity, {**self.final, 'independent_confirmation': False})['status'], 'unresolved')
        outcome = journal.confirm(identity, self.final)
        self.assertTrue(outcome['win'])
        self.assertAlmostEqual(outcome['virtual_unit_pnl'], .8)
        journal.confirm(identity, self.final)
        self.assertEqual(journal.status()['confirmed'], 1)
        with self.assertRaises(ValueError):
            journal.confirm(identity, {**self.final, 'regulation_total': 170})

    def test_unknown_rules_are_not_filled_in_after_the_match(self):
        self.row['decision']['market'] = {}
        identity = self.save()
        self.assertEqual(journal.confirm(identity, self.final)['market_settlement'], 'unresolved')
        self.assertEqual(journal.status()['confirmed'], 0)
        self.assertEqual(journal.status()['confirmed_games'], 1)
        self.assertEqual(evaluate(load_database(self.dbpath))['partitions']['development']['match']['regulation_mae'], 2)

    def test_model_versions_are_not_pooled(self):
        self.save()
        journal.record([self.row], 'other-version')
        report = evaluate(load_database(self.dbpath))
        self.assertEqual(len(report['by_model_source_sha256']), 2)
        for part in report['by_model_source_sha256'].values():
            self.assertEqual(part['selected_checkpoints'], 1)

    def test_first_checkpoint_and_whole_game_split_no_result_cherry_picking(self):
        identity = self.save()
        later = copy.deepcopy(self.row)
        later['captured_at_utc'] = '2026-01-01T10:05:00Z'
        later['snapshot_id'] = 's2'
        journal.record([later], 'source-hash')
        second_id = next(x['id'] for x in journal.export_rows() if x['snapshot_id'] == 's2')
        journal.confirm(second_id, self.final)
        report = evaluate(load_database(self.dbpath))
        self.assertEqual(report['selected_checkpoints'], 1)
        self.assertEqual(report['unresolved_checkpoints'], 1, 'cannot replace unlabeled first checkpoint by a later winning one')
        journal.confirm(identity, self.final)
        for number in range(2, 7):
            row = copy.deepcopy(self.row)
            row.update(event_id=f'g{number}', snapshot_id=f's{number}', captured_at_utc=f'2026-01-0{number}T10:00:00Z')
            journal.record([row], 'source-hash')
            saved = next(x['id'] for x in journal.export_rows() if x['event_id'] == f'g{number}')
            journal.confirm(saved, {**self.final, 'event_id': f'g{number}'})
        report = evaluate(load_database(self.dbpath))
        self.assertFalse(set(report['development_game_ids']) & set(report['test_game_ids']))
        self.assertEqual(report['partitions']['test']['match']['regulation_mae'], 2)
        self.assertAlmostEqual(report['partitions']['test']['match']['virtual_market_roi_by_status']['research']['roi'], .8)
        self.assertFalse(report['promotion_allowed'])
