import asyncio
from copy import deepcopy
import importlib.util
import os
from pathlib import Path
import sys
import time
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("experimental_signals_test", ROOT / "server_signals.py")
signals = importlib.util.module_from_spec(spec)
spec.loader.exec_module(signals)


def card(event="1"):
    return {"event":event,"forecast_id":"saved-test-forecast","teams":"Team A — Team B","context":"50:48 · Q3 · 04:00",
            "expires":time.time()*1000+60000,
            "signal":{"target":"quarter","period":3,"line":44.5,"direction":"ТМ",
                      "probability":.72,"rankingProbability":.68,"marketReady":True,"watchlist":True,
                      "priority":False,"calibrated":False,"reasonCodes":["calibration","validation"],
                      "robustness":{"available":True,"directionStable":True,"probabilityMin":.68}}}


class ExperimentalNotifications(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env=patch.dict(os.environ,{"SIGNAL_SERVER_ENABLED":"1","SIGNAL_EXPERIMENTAL_ENABLED":"1",
                                       "SIGNAL_BOT_TOKEN":"test-token","SIGNAL_CHAT_ID":"123"})
        self.env.start()
        self.path=ROOT / ("test-signal-"+uuid.uuid4().hex+".sqlite3")
        self.state=patch.object(signals,"STATE_PATH",self.path); self.state.start()
        self.audit=patch.object(signals,"audit_delivery"); self.audit_mock=self.audit.start()
        signals.LOCK=asyncio.Lock()
        signals.STATUS.update(cards=[],error=None,sent=0)

    async def asyncTearDown(self):
        self.audit.stop(); self.state.stop(); self.env.stop(); self.path.unlink(missing_ok=True)

    def test_explicit_opt_in_and_label(self):
        row=card(); result={"researchCandidates":[row]}
        with patch.dict(os.environ,{"SIGNAL_EXPERIMENTAL_ENABLED":"0"}):
            self.assertEqual(signals.experimental_cards(result),[])
        selected=signals.experimental_cards(result)
        self.assertFalse(selected[0]["signal"]["priority"])
        text=signals.message_for(selected[0])
        self.assertIn("экспериментальный прогноз",text)
        self.assertNotIn("68.0%",text)
        self.assertIn("не проверенная вероятность",text)
        self.assertIn("не подтверждены",text)

    def test_invalid_or_mismatched_market_is_not_sent(self):
        for reason in ["data","quote","rules","overtime","suspended","preliminary","window"]:
            row=card(); row["signal"]["reasonCodes"].append(reason)
            self.assertEqual(signals.experimental_cards({"researchCandidates":[row]}),[],reason)
        row=card(); row["signal"]["marketReady"]=False
        self.assertEqual(signals.experimental_cards({"researchCandidates":[row]}),[])

    def test_unstable_or_low_probability_is_not_selected(self):
        for value in [.64,float("nan"),True,None]:
            row=card(); row["signal"]["robustness"]["probabilityMin"]=value
            self.assertEqual(signals.experimental_cards({"researchCandidates":[row]}),[])
        row=card(); row["signal"]["robustness"]["directionStable"]=False
        self.assertEqual(signals.experimental_cards({"researchCandidates":[row]}),[])

    async def test_delivery_deduplicates_but_retries_failures(self):
        await signals.deliver([])  # First-run baseline remains intact.
        row=signals.experimental_cards({"researchCandidates":[card()]})[0]
        send=AsyncMock(side_effect=[RuntimeError("temporary"),None])
        with patch.object(signals,"say",send):
            with self.assertRaises(RuntimeError): await signals.deliver([row])
            await signals.deliver([row]); await signals.deliver([row])
        self.assertEqual(send.await_count,2)
        self.assertEqual(signals.STATUS["sent"],1)

    async def test_expired_and_paused_notifications_are_not_sent(self):
        await signals.deliver([])
        row=signals.experimental_cards({"researchCandidates":[card()]})[0]
        row["expires"]=0
        with patch.object(signals,"say",AsyncMock()) as send:
            await signals.deliver([row]); send.assert_not_awaited()
            row["expires"]=time.time()*1000+60000
            db=signals.connection(); signals.put(db,"paused","1"); db.close()
            await signals.deliver([row]); send.assert_not_awaited()

    async def test_process_keeps_experimental_separate_from_priority(self):
        detector=types.SimpleNamespace(calculate=Mock(return_value={"cards":[],"researchCandidates":[card()],
                      "errors":[],"evaluations":[{"event_id":"1","decision":card()["signal"]}],"model_source_sha256":"test","diagnostics":{}}))
        journal=types.SimpleNamespace(record=Mock(return_value=["immutable-id"]))
        data={"checked_at_utc":"2026-11-23T08:00:00+00:00","events":[]}
        with patch.dict(sys.modules,{"server_detector":detector,"forecast_journal":journal}),patch.object(signals,"deliver",AsyncMock()) as deliver:
            await signals.process(data,{})
        self.assertEqual(data["priority_signals"],[])
        self.assertEqual(len(data["research_candidates"]),1)
        self.assertTrue(deliver.await_args.args[0][0]["experimental"])
        self.assertEqual(deliver.await_args.args[0][0]["forecast_id"],"immutable-id")
        self.assertEqual(signals.public_status()["notification_mode"],"experimental")

    async def test_new_lines_and_direction_flips_are_not_new_entries(self):
        await signals.deliver([])
        row=signals.experimental_cards({"researchCandidates":[card()]})[0]
        with patch.object(signals,"say",AsyncMock(return_value={"message_id":42})) as send:
            await signals.deliver([row])
            changed=deepcopy(row);changed['signal'].update(line=40.5,direction='ТБ')
            await signals.deliver([changed])
            next_period=deepcopy(row);next_period['signal']['period']=4
            await signals.deliver([next_period])
        self.assertEqual(send.await_count,2)
        self.assertEqual(self.audit_mock.call_args.args[1:],("sent",42))

    async def test_missing_immutable_forecast_blocks_delivery(self):
        await signals.deliver([])
        row=card();row.pop('forecast_id')
        with patch.object(signals,"say",AsyncMock()) as send:
            await signals.deliver([row])
            send.assert_not_awaited()

    async def test_test_command_is_owner_only_and_clears_recovered_errors(self):
        updates=[{"update_id":1,"message":{"chat":{"id":999},"text":"/test"}},
                 {"update_id":2,"message":{"chat":{"id":123},"text":"/test"}}]
        signals.STATUS["command_error"]="previous failure"
        with patch.object(signals,"telegram",AsyncMock(return_value=updates)),patch.object(signals,"say",AsyncMock()) as send:
            await signals.commands_once()
        self.assertEqual(send.await_count,1)
        self.assertIn("техническая проверка",send.await_args.args[0])
        self.assertNotIn("command_error",signals.STATUS)

if __name__ == "__main__":
    unittest.main()
