"""Five end-to-end social ledger scenarios; synthetic inputs and no external service."""

import asyncio
import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from llm.models import AssistantMessage, LLMResult

from wechat_agent.context import ConversationScope
from wechat_agent.social.decision import RuntimeDecider, SyntheticModel
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import IncomingEvent, Receipt, ReceiptStatus
from wechat_agent.social.sandbox import FakeAdapter
from wechat_agent.social.service import SocialService

SCRATCH = Path(__file__).resolve().parents[1] / ".build" / "social-tests"
SCOPE = ConversationScope("fake", "account-a", "dm", "owner-dm")


class Clock:
    value = 1_800_000_000.0

    def __call__(self):
        return self.value


class RecordingModel(SyntheticModel):
    def __init__(self):
        super().__init__()
        self.inputs = []
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.release.set()
        self.injected = None

    async def __call__(self, messages, tools, **kwargs):
        self.inputs.append(json.loads(messages[-1]["content"]))
        self.entered.set()
        await self.release.wait()
        if self.injected is not None:
            self.calls += 1
            return LLMResult(AssistantMessage(self.injected))
        return await super().__call__(messages, tools, **kwargs)


class SocialAcceptance(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "ledger.sqlite3"
        self.clock = Clock()

    def event(self, key, text="hello", scope=SCOPE):
        return IncomingEvent(scope, "owner", key, self.clock(), self.clock(), text, source_verified=True)

    def open(self):
        ledger = Ledger(self.path, now=self.clock).__enter__()
        ledger.configure_owner(SCOPE, "owner")
        return ledger

    async def test_duplicate_delivery_and_reopen_preserve_one_action_and_confirmed_context(self):
        ledger = self.open()
        model, adapter = RecordingModel(), FakeAdapter("account-a")
        event = self.event("same")
        try:
            async with SocialService(ledger, RuntimeDecider(model), [adapter]) as service:
                await asyncio.gather(service.prepare(event), service.prepare(event))
                action = ledger.summary(event.key)["action_id"]
                await asyncio.gather(service.dispatch(action), service.dispatch(action))
                self.assertEqual(ledger.counts(), {"inbox": 1, "decisions": 1, "outbox": 1, "attempts": 1})
                self.assertEqual((model.calls, len(adapter.sent)), (1, 1))
                self.assertEqual(ledger.summary(event.key)["state"], "CONFIRMED")
        finally:
            ledger.close()
        with Ledger(self.path, now=self.clock) as reopened:
            fresh_model, fresh_adapter = RecordingModel(), FakeAdapter("account-a")
            async with SocialService(reopened, RuntimeDecider(fresh_model), [fresh_adapter]) as service:
                result = await service.prepare(event)
                await service.dispatch(result["action_id"])
                self.assertEqual((fresh_model.calls, fresh_adapter.sent), (0, []))
                next_result = await service.prepare(self.event("next"))
                self.assertEqual(fresh_model.inputs[0]["confirmed_history"][0]["user"], "hello")
                self.assertEqual(next_result["state"], "QUEUED")
                self.assertEqual(len(reopened.history(SCOPE)), 1, "A draft is not a sent fact")

    async def test_policy_silence_and_revocation_gate_inference_and_delivery(self):
        ledger = self.open()
        model, adapter = RecordingModel(), FakeAdapter("account-a")
        try:
            async with SocialService(ledger, RuntimeDecider(model), [adapter]) as service:
                denied = [replace(self.event("bad-source"), source_verified=False),
                          replace(self.event("echo"), is_self=True), replace(self.event("stranger"), sender_id="other"),
                          self.event("other-account", scope=replace(SCOPE, account_id="unknown")),
                          self.event("other-chat", scope=replace(SCOPE, conversation_id="unknown"))]
                for event in denied:
                    self.assertEqual((await service.prepare(event))["state"], "REJECTED")
                self.assertEqual((model.calls, ledger.counts()["inbox"]), (0, 0))
                self.assertEqual((await service.prepare(self.event("quiet", "[silent]")))["state"], "SILENT")
                self.assertEqual(ledger.counts()["outbox"], 0)
                queued = await service.prepare(self.event("queued"))
                ledger.configure_owner(SCOPE, "owner", enabled=False)
                self.assertEqual((await service.dispatch(queued["action_id"]))["state"], "CANCELLED")
                ledger.configure_owner(SCOPE, "owner")
                model.entered.clear()
                model.release.clear()
                late = self.event("in-flight")
                task = asyncio.create_task(service.prepare(late))
                await asyncio.wait_for(model.entered.wait(), 2)
                ledger.pause(True)
                ledger.pause(False)
                model.release.set()
                self.assertEqual((await task)["state"], "CANCELLED")
                self.assertEqual(adapter.sent, [])
                old = await service.prepare(self.event("superseded"))
                await service.prepare(self.event("newer"))
                self.assertEqual((await service.dispatch(old["action_id"]))["state"], "CANCELLED")
        finally:
            ledger.close()

    async def test_scopes_model_target_rejection_and_decision_outbox_atomicity(self):
        ledger = self.open()
        scope_b = replace(SCOPE, account_id="account-b")
        scope_c = replace(SCOPE, conversation_id="another-dm")
        ledger.configure_owner(scope_b, "owner")
        ledger.configure_owner(scope_c, "owner")
        model = RecordingModel()
        adapters = [FakeAdapter("account-a"), FakeAdapter("account-b")]
        try:
            async with SocialService(ledger, RuntimeDecider(model), adapters) as service:
                for scope in (SCOPE, scope_b, scope_c):
                    result = await service.prepare(self.event("same-message-id", scope.account_id + scope.conversation_id, scope))
                    self.assertEqual(model.inputs[-1]["confirmed_history"], [])
                    await service.dispatch(result["action_id"])
                self.assertEqual([len(adapter.sent) for adapter in adapters], [2, 1])
                before = ledger.counts()
                model.injected = '{"action":"REPLY","reply_text":"bad","target":"another-dm"}'
                result = await service.prepare(self.event("invalid-target"))
                self.assertEqual(result["state"], "FAILED")
                self.assertEqual(ledger.counts()["outbox"], before["outbox"])
                model.injected = None
                ledger._db.execute("CREATE TRIGGER reject_outbox BEFORE INSERT ON outbox BEGIN SELECT RAISE(ABORT,'fixture'); END")
                result = await service.prepare(self.event("atomic-failure"))
                self.assertEqual(result["state"], "FAILED")
                after = ledger.counts()
                self.assertEqual((after["decisions"], after["outbox"]), (before["decisions"], before["outbox"]))
        finally:
            ledger.close()

    async def test_process_crash_after_external_submission_recovers_unknown_without_resending(self):
        ledger = self.open()
        event = self.event("crash")
        try:
            async with SocialService(ledger, RuntimeDecider(SyntheticModel()), [FakeAdapter("account-a")]) as service:
                prepared = await service.prepare(event)
        finally:
            ledger.close()
        marker = Path(self.folder.name) / "external-synthetic-send.txt"
        code = '''import os,sys
from pathlib import Path
from wechat_agent.social.ledger import Ledger
with Ledger(Path(sys.argv[1]),now=lambda:float(sys.argv[4])) as ledger:
    action=ledger.claim(sys.argv[2])
    assert action is not None
    with Path(sys.argv[3]).open('x',encoding='utf-8') as handle:
        handle.write(action.action_id)
        handle.flush()
        os.fsync(handle.fileno())
    os._exit(37)
'''
        child = await asyncio.create_subprocess_exec(sys.executable, "-B", "-c", code, str(self.path),
                                                     prepared["action_id"], str(marker), str(self.clock()),
                                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(child.communicate(), 15)
        self.assertEqual(child.returncode, 37, (stdout, stderr))
        self.assertEqual(marker.read_text(), prepared["action_id"])
        with Ledger(self.path, now=self.clock) as recovered:
            adapter, model = FakeAdapter("account-a"), SyntheticModel()
            async with SocialService(recovered, RuntimeDecider(model), [adapter]) as service:
                self.assertEqual((await service.dispatch(prepared["action_id"]))["state"], "UNCONFIRMED")
                self.assertEqual((await service.prepare(event))["state"], "UNCONFIRMED")
                self.assertEqual((model.calls, adapter.sent, recovered.history(SCOPE)), (0, [], []))

    async def test_expiry_receipt_mismatch_and_cancelled_send_never_become_confirmed_or_retry(self):
        ledger = self.open()
        entered = asyncio.Event()

        class FaultAdapter(FakeAdapter):
            mode = "mismatch"

            async def send_text(self, action):
                self.sent.append(action.action_id)
                if self.mode == "cancel":
                    entered.set()
                    await asyncio.Event().wait()
                if self.mode == "not-submitted":
                    return Receipt(action.action_id, action.scope, ReceiptStatus.NOT_SUBMITTED)
                return Receipt("wrong-action", action.scope, ReceiptStatus.CONFIRMED, "fake-receipt")

        adapter = FaultAdapter("account-a")
        service = SocialService(ledger, RuntimeDecider(SyntheticModel()), [adapter])
        try:
            async with service:
                expired = await service.prepare(self.event("expired"))
                self.clock.value += 121
                self.assertEqual((await service.dispatch(expired["action_id"]))["state"], "EXPIRED")
                self.assertEqual(adapter.sent, [])
                mismatch = await service.prepare(self.event("mismatch"))
                self.assertEqual((await service.dispatch(mismatch["action_id"]))["state"], "UNCONFIRMED")
                await service.dispatch(mismatch["action_id"])
                self.assertEqual(len(adapter.sent), 1)
                adapter.mode = "not-submitted"
                failed = await service.prepare(self.event("failed"))
                self.assertEqual((await service.dispatch(failed["action_id"]))["state"], "FAILED")
                adapter.mode = "cancel"
                cancelled = await service.prepare(self.event("cancel"))
                sending = asyncio.create_task(service.dispatch(cancelled["action_id"]))
                await asyncio.wait_for(entered.wait(), 2)
                await service.aclose()
                with self.assertRaises(asyncio.CancelledError):
                    await sending
                self.assertEqual(ledger.action_summary(cancelled["action_id"])["state"], "UNCONFIRMED")
                self.assertEqual(ledger.history(SCOPE), [])
        finally:
            ledger.close()
