"""Critical continuous-chat flow with synthetic model/sender only."""

import asyncio
import hashlib

import pytest

from tests.test_workbench_private_local import PEER, incoming, service
from wechat_agent.bridges.wechat.contracts import DeliveryEvidence, digest
from wechat_agent.bridges.wechat.store import BridgeStore
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import ActionKind, Decision, SocialError
from wechat_agent.workbench.service import WeChatWorkbenchService


def build(ledger, generator, *, echo=True):
    original = service(ledger)

    class Sender:
        binding = PEER
        calls = []

        async def send(self, request_id, text, *, draft_id, expected_version):
            wb.state.require_ready(ledger._db, draft_id, expected_version)
            aid = digest([request_id])
            self.calls.append((request_id, text))
            event = ledger._db.execute(
                "SELECT b.event_id FROM bridge_records b JOIN workbench_drafts d ON d.source_message_id=b.message_id WHERE d.draft_id=?",
                (draft_id,),
            ).fetchone()[0]
            with ledger._transaction() as db:
                db.execute(
                    "INSERT INTO outbox(action_id,event_id,text,state,expires_at) VALUES(?,?,?,'UNCONFIRMED',?)",
                    (aid, event, text, ledger.now() + 60),
                )
                db.execute(
                    "INSERT INTO attempts VALUES(?,?,'UNCONFIRMED',?)",
                    (aid, ledger.now(), ledger.now()),
                )
                db.execute(
                    "UPDATE workbench_drafts SET action_id=?,state='SUBMITTED' WHERE draft_id=?",
                    (aid, draft_id),
                )
            if echo:
                BridgeStore(ledger).record_evidence(
                    DeliveryEvidence(
                        "echo:" + aid,
                        aid,
                        PEER.scope,
                        PEER.generation,
                        "local_echo",
                        "fixture",
                        hashlib.sha256(text.encode()).hexdigest(),
                        "native:" + aid,
                    )
                )
            return dict(action_id=aid, local_echo=echo, state="UNCONFIRMED", reason="sent")

    sender = Sender()
    wb = WeChatWorkbenchService(
        ledger,
        original.receiver,
        model="fixture",
        generator=generator,
        local_drafts=True,
        private_model=True,
        private_sender=sender,
    )
    wb.reading = True
    return wb, sender


def test_two_natural_messages_have_context_and_each_reply_once(tmp_path):
    async def run():
        calls = []

        async def model(text, history, *_):
            calls.append(history)
            return Decision(ActionKind.REPLY, "Cybergirl回复")

        with Ledger(tmp_path / "chat.db") as ledger:
            wb, sender = build(ledger, model)
            incoming(ledger, n=1)  # old backlog is never sent
            wb.auto_reply.set_enabled(True)
            wb.auto_reply.tick()
            assert wb.auto_reply.task is None
            incoming(ledger, n=2, sender=PEER.account.self_id)
            wb.auto_reply.tick()
            assert wb.auto_reply.task is None
            for n in (3, 4):
                incoming(ledger, n=n)
                wb.auto_reply.tick()
                await wb.auto_reply.task
                wb.auto_reply.tick()
            assert len(sender.calls) == len(calls) == 2 and wb.auto_reply.enabled
            assert len(calls[1]) == 1 and calls[1][0]["assistant"] == "Cybergirl回复"
            await wb.aclose()

    asyncio.run(run())


def test_stop_during_generation_prevents_send(tmp_path):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        async def model(*_):
            entered.set()
            await release.wait()
            return Decision(ActionKind.REPLY, "不应发送")

        with Ledger(tmp_path / "stop.db") as ledger:
            wb, sender = build(ledger, model)
            wb.auto_reply.set_enabled(True)
            incoming(ledger)
            wb.auto_reply.tick()
            await entered.wait()
            wb.auto_reply.set_enabled(False)
            release.set()
            await wb.auto_reply.task
            assert sender.calls == []
            await wb.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["model", "unknown_send"])
def test_failure_stops_auto_and_never_retries(tmp_path, failure):
    async def run():
        calls = []

        async def model(*_):
            calls.append(1)
            if failure == "model":
                raise SocialError("fixture_failure")
            return Decision(ActionKind.REPLY, "一次回复")

        with Ledger(tmp_path / "failure.db") as ledger:
            wb, sender = build(ledger, model, echo=False)
            wb.auto_reply.set_enabled(True)
            incoming(ledger)
            wb.auto_reply.tick()
            await wb.auto_reply.task
            wb.auto_reply.tick()
            assert not wb.auto_reply.enabled and len(calls) == 1
            assert len(sender.calls) == (0 if failure == "model" else 1)
            await wb.aclose()

    asyncio.run(run())
