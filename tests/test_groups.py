"""Five integrated synthetic group scenarios; clocks advance without real waiting."""

import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx

from tests.test_social import Clock, RecordingModel
from wechat_agent.console import GROUP_SCOPE, ConsoleServer
from wechat_agent.context import ConversationScope
from wechat_agent.social.decision import RuntimeDecider
from wechat_agent.social.groups import GroupBuffer, configure_group
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import IncomingEvent, Receipt, ReceiptStatus, SocialError
from wechat_agent.social.sandbox import FakeAdapter
from wechat_agent.social.service import SocialService

GROUP = ConversationScope("fake", "account-a", "group", "same-name")
OTHER = ConversationScope("fake", "account-b", "group", "same-name")


class GroupAcceptance(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / ".build" / "group-tests"
        scratch.mkdir(parents=True, exist_ok=True)
        folder = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "ledger.sqlite3"
        self.clock = Clock()
        self.clock.value = datetime(2026, 10, 27, 4, tzinfo=UTC).timestamp()

    def event(self, key, *, scope=GROUP, mention=False, text="练习", reply=None):
        return IncomingEvent(scope, "member-a", key, self.clock(), self.clock(), text,
                             source_verified=True, mention_self=mention, reply_to=reply)

    def configure(self, ledger, scope=GROUP, enabled=True):
        configure_group(ledger, scope, bot_id="bot", consent_ref="synthetic-consent", enabled=enabled)

    async def flush(self, groups, seconds=3):
        self.clock.value += seconds
        tasks = await groups.flush_due()
        if tasks:
            await asyncio.gather(*tasks)

    async def test_trigger_filter_burst_bound_quote_identity_and_account_isolation(self):
        with Ledger(self.path, now=self.clock) as ledger:
            self.configure(ledger)
            self.configure(ledger, OTHER)
            model = RecordingModel()
            async with SocialService(ledger, RuntimeDecider(model), [FakeAdapter("account-a"), FakeAdapter("account-b")]) as service:
                groups = GroupBuffer(service, monotonic=self.clock)
                for event in [replace(self.event("untrusted"), source_verified=False),
                              replace(self.event("echo"), sender_id="bot"),
                              replace(self.event("self"), is_self=True),
                              self.event("unknown", scope=ConversationScope("fake", "account-a", "group", "unknown"))]:
                    self.assertEqual(groups.submit(event)["state"], "REJECTED")
                ordinary = self.event("ordinary")
                groups.submit(ordinary)
                await self.flush(groups)
                self.assertEqual(ledger.summary(ordinary.key)["state"], "IGNORED")
                self.assertEqual(model.calls, 0)
                first = self.event("burst-0", mention=True, text="最初的问题")
                groups.submit(first)
                for index in range(1, 5):
                    await self.flush(groups, 2)
                    self.assertEqual(model.calls, 0)
                    latest = self.event(f"burst-{index}", text=f"补充 {index}")
                    groups.submit(latest)
                await self.flush(groups, 2)  # 10-second deadline despite ongoing messages.
                action = ledger.summary(latest.key)["action_id"]
                self.assertEqual(model.calls, 1)
                batch = json.loads(model.inputs[0]["incoming_text"].removeprefix("GROUP_MESSAGES_JSON:"))
                self.assertEqual(len(batch), 5)
                self.assertEqual(ledger.summary(first.key)["state"], "MERGED")
                await service.dispatch(action)
                self.assertEqual(groups.submit(latest)["state"], "CONFIRMED")
                with self.assertRaises(SocialError):
                    groups.submit(replace(latest, text="changed"))
                receipt = "fake-" + action
                foreign_quote = self.event("foreign", scope=OTHER, reply=receipt)
                groups.submit(foreign_quote)
                await self.flush(groups)
                self.assertEqual(ledger.summary(foreign_quote.key)["state"], "IGNORED")
                self.clock.value += 31
                own_quote = self.event("own", reply=receipt)
                groups.submit(own_quote)
                await self.flush(groups)
                self.assertEqual(ledger.summary(own_quote.key)["state"], "QUEUED")
                self.assertEqual(len(model.inputs[-1]["confirmed_history"]), 1)
                other = self.event("other-mention", scope=OTHER, mention=True)
                groups.submit(other)
                await self.flush(groups)
                self.assertEqual(model.inputs[-1]["confirmed_history"], [])
                await groups.aclose()

    async def test_http_group_routes_bind_scope_and_restart_initialization_preserves_revoke(self):
        with Ledger(self.path, now=self.clock) as ledger:
            self.configure(ledger, GROUP_SCOPE, enabled=False)
            configure_group(ledger, GROUP_SCOPE, bot_id="bot", consent_ref="synthetic-consent", initialize=True)
            model = RecordingModel()
            async with SocialService(ledger, RuntimeDecider(model), [FakeAdapter()]) as service:
                groups = GroupBuffer(service, monotonic=self.clock)
                server = ConsoleServer(ledger, service, groups=groups, token="synthetic")
                await server.start(0)
                try:
                    async with httpx.AsyncClient(base_url=server.origin, trust_env=False) as client:
                        payload = {"message_id": "http-mention", "occurred_at": self.clock(), "sender_id": "member-a", "trigger": "mention", "text": "你好"}
                        self.assertEqual((await client.post("/api/group/messages", json=payload)).status_code, 403)
                        client.headers.update({"Authorization": "Bearer synthetic", "Origin": server.origin})
                        denied = (await client.post("/api/group/messages", json=payload)).json()
                        self.assertEqual(denied["state"], "REJECTED")
                        self.configure(ledger, GROUP_SCOPE)
                        self.assertEqual((await client.post("/api/group/messages", json=dict(payload, trigger=[]))).status_code, 400)
                        self.assertEqual((await client.post("/api/group/messages", json=dict(payload, target="other"))).status_code, 400)
                        self.assertEqual((await client.post("/api/group/messages", json=payload)).json()["state"], "BUFFERED")
                        await self.flush(groups)
                        snapshot = (await client.get("/api/state")).json()
                        action = snapshot["group_sandbox"]["events"][0]["action_id"]
                        self.assertEqual((await client.post("/api/dispatch", json={"action_id": action})).status_code, 404)
                        self.assertEqual((await client.post("/api/group/dispatch", json={"action_id": action})).json()["state"], "CONFIRMED")
                        self.assertEqual((await client.post("/api/group/messages", json=payload)).json()["state"], "CONFIRMED")
                        self.assertEqual(model.calls, 1)
                finally:
                    await server.aclose()
                    await groups.aclose()

    async def test_model_budgets_silence_window_day_rollover_and_clock_rollback(self):
        with Ledger(self.path, now=self.clock) as ledger:
            self.configure(ledger)
            model = RecordingModel()
            async with SocialService(ledger, RuntimeDecider(model), [FakeAdapter("account-a")]) as service:
                groups = GroupBuffer(service, monotonic=self.clock)

                async def silent(key):
                    event = self.event(key, mention=True, text="[silent]")
                    groups.submit(event)
                    await self.flush(groups)
                    return ledger.summary(event.key)

                self.assertEqual((await silent("one"))["state"], "SILENT")
                self.assertEqual((await silent("cooldown"))["reason"], "model_cooldown")
                for key in ["two", "three"]:
                    self.clock.value += 31
                    self.assertEqual((await silent(key))["state"], "SILENT")
                self.clock.value += 31
                self.assertEqual((await silent("window"))["reason"], "model_window_limit")
                for n in range(7):
                    self.clock.value += 601
                    self.assertEqual((await silent(f"day-{n}"))["state"], "SILENT")
                self.clock.value += 601
                self.assertEqual((await silent("daily"))["reason"], "model_daily_limit")
                self.assertEqual(model.calls, 10)
                self.clock.value = datetime(2026, 10, 27, 16, tzinfo=UTC).timestamp()
                self.assertEqual((await silent("midnight"))["state"], "SILENT")
                self.clock.value -= 30000
                self.assertEqual((await silent("rollback"))["reason"], "clock_rollback")
                self.assertEqual(model.calls, 11)
                self.assertEqual(ledger.counts()["outbox"], 0)
                await groups.aclose()

    async def test_unknown_delivery_reserves_budget_and_restart_does_not_repeat(self):
        class UnknownAdapter(FakeAdapter):
            async def send_text(self, action):
                self.sent.append(action.action_id)
                return Receipt(action.action_id, action.scope, ReceiptStatus.UNKNOWN)

        with Ledger(self.path, now=self.clock) as ledger:
            self.configure(ledger)
            model, adapter = RecordingModel(), UnknownAdapter("account-a")
            async with SocialService(ledger, RuntimeDecider(model), [adapter]) as service:
                groups = GroupBuffer(service, monotonic=self.clock)
                first = self.event("send", mention=True)
                groups.submit(first)
                await self.flush(groups)
                action = ledger.summary(first.key)["action_id"]
                self.clock.value += 90  # Submission time, not draft time, controls send cooldown.
                self.assertEqual((await service.dispatch(action))["state"], "UNCONFIRMED")
                next_event = self.event("blocked", mention=True)
                groups.submit(next_event)
                await self.flush(groups)
                self.assertEqual(ledger.summary(next_event.key)["reason"], "send_cooldown")
                self.assertEqual((model.calls, len(adapter.sent)), (1, 1))
                pending = self.event("buffered-on-exit", scope=OTHER)
                self.configure(ledger, OTHER)
                # Direct buffered row simulates a process exiting before its timer fires.
                ledger.accept(pending, buffer_group=True)
                await groups.aclose()
                with ledger._transaction() as db:
                    db.execute("UPDATE inbox SET state='BUFFERED' WHERE event_id=?", (pending.key,))
        with Ledger(self.path, now=self.clock) as ledger:
            model, adapter = RecordingModel(), FakeAdapter("account-a")
            async with SocialService(ledger, RuntimeDecider(model), [adapter]) as service:
                groups = GroupBuffer(service, monotonic=self.clock)
                self.assertEqual(groups.submit(first)["state"], "UNCONFIRMED")
                self.assertEqual((await service.dispatch(action))["state"], "UNCONFIRMED")
                self.assertEqual(ledger.summary(pending.key)["state"], "CANCELLED")
                self.assertEqual((model.calls, adapter.sent), (0, []))
                await groups.aclose()

    async def test_buffer_capacity_revoke_pause_and_new_context_fence_inflight_work(self):
        with Ledger(self.path, now=self.clock) as ledger:
            self.configure(ledger)
            model = RecordingModel()
            async with SocialService(ledger, RuntimeDecider(model), [FakeAdapter("account-a")]) as service:
                groups = GroupBuffer(service, monotonic=self.clock)
                huge = self.event("huge", text="字" * 4000, mention=True)
                self.assertEqual(groups.submit(huge)["reason"], "group_buffer_full")
                event = self.event("revoke", mention=True)
                groups.submit(event)
                self.configure(ledger, enabled=False)
                await self.flush(groups)
                self.assertEqual(ledger.summary(event.key)["state"], "CANCELLED")
                self.configure(ledger)
                event = self.event("pause", mention=True)
                groups.submit(event)
                ledger.pause(True)
                await self.flush(groups)
                self.assertEqual(ledger.summary(event.key)["state"], "CANCELLED")
                ledger.pause(False)
                model.release.clear()
                inflight = self.event("inflight", mention=True)
                groups.submit(inflight)
                self.clock.value += 3
                tasks = await groups.flush_due()
                await asyncio.wait_for(model.entered.wait(), 3)
                newer = self.event("new-context", mention=True)
                groups.submit(newer)
                model.release.set()
                await asyncio.gather(*tasks)
                self.assertEqual(ledger.summary(inflight.key)["state"], "CANCELLED")
                await self.flush(groups)
                self.assertEqual(ledger.summary(newer.key)["reason"], "model_cooldown")
                self.assertEqual((model.calls, ledger.counts()["outbox"]), (1, 0))
                await groups.aclose()
