import unittest
import os
import tempfile
from pathlib import Path
from unittest.mock import patch
from monitor import Store, deliver, signal_key, commands, load_config


def card(**changes):
    return dict(event='42', teams='A — B',
                label='Четверть Q3 · ТМ 45.5 · 70.0% по модели', expires=200000, **changes)


class FakeTelegram:
    def __init__(self, fail=False, updates=None):
        self.messages = []
        self.fail = fail
        self.updates = updates or []

    def send(self, chat, text):
        if self.fail:
            raise RuntimeError('offline')
        self.messages.append((chat, text))

    def call(self, *args, **kwargs):
        return self.updates


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.tg = FakeTelegram()

    def tearDown(self):
        self.store.db.close()

    def test_probability_change_does_not_repeat(self):
        first = card()
        second = dict(first, label=first['label'].replace('70.0', '80.0'))
        self.assertEqual(deliver([first, second], self.store, self.tg, 1, now=100), 1)

    def test_line_direction_and_quarter_are_distinct(self):
        first = card()
        keys = [signal_key(dict(first, label=first['label'].replace(a, b)))
                for a, b in [('45.5', '46.5'), ('ТМ', 'ТБ'), ('Q3', 'Q4')]]
        self.assertEqual(len(set([signal_key(first), *keys])), 4)

    def test_stale_and_incomplete_skipped(self):
        self.assertEqual(deliver([dict(card(), expires=99000), dict(card(), event='')],
                                 self.store, self.tg, 1, now=100), 0)

    def test_send_failure_is_retryable(self):
        with self.assertRaises(RuntimeError):
            deliver([card()], self.store, FakeTelegram(fail=True), 1, now=100)
        self.assertFalse(self.store.seen(signal_key(card())))
        self.assertEqual(deliver([card()], self.store, self.tg, 1, now=100), 1)

    def test_other_chat_cannot_pause(self):
        self.tg.updates = [{'update_id': 2, 'message': {'chat': {'id': 99}, 'text': '/pause'}}]
        commands(self.tg, {'chat_id': 1}, self.store, 'ok')
        self.assertNotEqual(self.store.get('paused'), '1')
        self.assertEqual(self.store.get('offset'), '3')

    def test_owner_can_pause(self):
        self.tg.updates = [{'update_id': 3, 'message': {'chat': {'id': 1}, 'text': '/pause'}}]
        commands(self.tg, {'chat_id': 1}, self.store, 'ok')
        self.assertEqual(self.store.get('paused'), '1')

    def test_cloud_uses_environment_without_local_secret_file(self):
        with patch.dict(os.environ, {'SIGNAL_BOT_TOKEN': 'test-token', 'SIGNAL_CHAT_ID': '123'}):
            self.assertEqual(load_config(), {'token': 'test-token', 'chat_id': 123})

    def test_history_survives_restart(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            path = Path(folder) / 'state.sqlite3'
            first = Store(path)
            self.assertEqual(deliver([card()], first, self.tg, 1, now=100), 1)
            first.db.close()
            second = Store(path)
            self.assertEqual(deliver([card()], second, self.tg, 1, now=101), 0)
            second.db.close()

    def test_cloud_bootstrap_does_not_rewind_commands(self):
        import json
        key = signal_key(card())
        seed = json.dumps({'sent': [[key, 100]], 'meta': {'offset': '5', 'paused': '0'}})
        self.store.bootstrap(seed)
        self.assertTrue(self.store.seen(key))
        self.store.put('offset', '10')
        self.store.put('paused', '1')
        self.store.bootstrap(seed)
        self.assertEqual(self.store.get('offset'), '10')
        self.assertEqual(self.store.get('paused'), '1')


if __name__ == '__main__':
    unittest.main()
