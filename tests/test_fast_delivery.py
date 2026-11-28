import asyncio
import importlib.util
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx
import server_signals as signals


class FastDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.patches = [
            patch.dict(os.environ, {'SIGNAL_SERVER_ENABLED':'1','SIGNAL_BOT_TOKEN':'test-only',
                'SIGNAL_CHAT_ID':'123','SIGNAL_LIFECYCLE_PATH':str(Path(self.folder.name)/'receipts.sqlite3')}),
            patch.object(signals,'STATE_PATH',Path(self.folder.name)/'state.sqlite3'),
            patch.object(signals,'LOCK',asyncio.Lock()),patch.object(signals,'RESULT_LOCK',asyncio.Lock()),
            patch.object(signals,'FINAL_CHECK_LOCK',asyncio.Lock()),patch.object(signals,'PENDING',{}),
            patch.object(signals,'RETRY_AT',{}),patch.object(signals,'audit_delivery'),
            patch.object(signals,'say',AsyncMock(return_value={'message_id':42}))]
        for p in self.patches:p.start()
        self.card={'event':'new','forecast_id':'saved','teams':'Home — Away','label':'Quarter',
            'signal':{'target':'quarter','period':2,'line':40.5,'direction':'ТМ'},
            'expires':(time.time()+90)*1000,'context':'Test'}

    async def asyncTearDown(self):
        for p in reversed(self.patches):p.stop()
        self.folder.cleanup()

    async def test_slow_final_lookup_does_not_block_new_signal(self):
        entered,release=asyncio.Event(),asyncio.Event()
        async def slow():
            entered.set()
            await release.wait()
        with patch('signal_results.check_finals',AsyncMock(side_effect=slow)):
            task=asyncio.create_task(signals.results_once())
            try:
                await asyncio.wait_for(entered.wait(),1)
                await asyncio.wait_for(signals.deliver([self.card]),1)
                signals.say.assert_awaited_once()
                self.assertFalse(task.done())
            finally:
                release.set()
                await task

    async def test_replaced_card_is_not_sent_after_waiting_for_receipts(self):
        signals.PENDING[signals.key_for(self.card)]=self.card
        original=asyncio.to_thread
        async def read(func,*args,**kwargs):
            result=await original(func,*args,**kwargs)
            if func is signals.lifecycle.rows:
                signals.PENDING[signals.key_for(self.card)]={**self.card,'forecast_id':'newer'}
            return result
        with patch.object(signals.asyncio,'to_thread',side_effect=read):
            await signals.deliver([self.card],current_only=True)
        signals.say.assert_not_awaited()
        self.assertEqual(signals.STATUS['delivery_decisions'][0]['state'],'replaced')

    async def test_card_that_expires_during_receipt_read_is_not_sent(self):
        self.card['expires']=1000500
        clock={'now':1000}
        original=asyncio.to_thread
        async def read(func,*args,**kwargs):
            result=await original(func,*args,**kwargs)
            if func is signals.lifecycle.rows:clock['now']=1001
            return result
        with patch.object(signals.time,'time',side_effect=lambda:clock['now']),patch.object(signals.asyncio,'to_thread',side_effect=read):
            await signals.deliver([self.card])
        signals.say.assert_not_awaited()
        self.assertEqual(signals.STATUS['delivery_decisions'][0]['state'],'expired')

    async def test_rate_limit_uses_telegram_retry_after(self):
        with patch.object(signals,'say',AsyncMock(side_effect=signals.TelegramRetryAfter(12))):
            await signals.deliver([self.card])
        delay=signals.RETRY_AT[signals.key_for(self.card)]-time.time()
        self.assertGreater(delay,11)
        self.assertLessEqual(delay,12)

    async def test_delivery_receipt_keeps_exact_entry_and_challenger(self):
        self.card['entry_state']={'score':[40,39],'clock':'05:00','learning':{'model_version':'test-v1','center':41}}
        self.card['delivery_review']={'available':True,'allow':True,'policy':'test'}
        await signals.deliver([self.card])
        saved=signals.lifecycle.rows()[0]['payload']
        self.assertEqual(saved['entry_state'],self.card['entry_state'])
        self.assertEqual(saved['delivery_review'],self.card['delivery_review'])


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_connection_and_rate_limit_no_secret_in_errors(self):
        client=Mock(is_closed=False)
        client.post=AsyncMock(side_effect=[
            httpx.Response(429,json={'ok':False,'error_code':429,'parameters':{'retry_after':7}}),
            httpx.Response(200,json={'ok':True,'result':{'message_id':4}})])
        client.aclose=AsyncMock()
        clock={'now':1000}
        with patch.dict(os.environ,{'SIGNAL_BOT_TOKEN':'never-print-this'}),patch.object(signals,'_TELEGRAM_CLIENT',None),patch.object(signals,'_TELEGRAM_LOOP',None),patch.object(signals,'_TELEGRAM_RETRY_UNTIL',0),patch.object(signals.httpx,'AsyncClient',return_value=client) as factory,patch.object(signals.time,'time',side_effect=lambda:clock['now']):
            with self.assertRaises(signals.TelegramRetryAfter) as error:
                await signals.telegram('sendMessage',text='test')
            self.assertEqual(error.exception.retry_after,7)
            self.assertNotIn('never-print-this',str(error.exception))
            with self.assertRaises(signals.TelegramRetryAfter):
                await signals.telegram('sendMessage',text='test')
            self.assertEqual(client.post.await_count,1)
            clock['now']=1008
            self.assertEqual(await signals.telegram('sendMessage',text='test'),{'message_id':4})
            factory.assert_called_once()
            await signals.close_transport()
            client.aclose.assert_awaited_once()


class MonitorTests(unittest.TestCase):
    def test_cloud_mode_never_starts_a_second_browser_or_sender(self):
        path=Path(__file__).resolve().parents[1]/'signal-bot'/'monitor.py'
        spec=importlib.util.spec_from_file_location('signal_monitor_fast_test',path)
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(module,'check_server',return_value=True):
            module.run({},server_only=True)
        with patch.object(module,'check_server',return_value=False):
            with self.assertRaisesRegex(RuntimeError,'no second Telegram poller'):
                module.run({},server_only=True)
