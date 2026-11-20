"""A runnable persistent synthetic loop, deliberately separate from live chat."""

import time
import uuid
from pathlib import Path

from ..context import ConversationScope
from .decision import RuntimeDecider, SyntheticModel
from .ledger import Ledger
from .models import IncomingEvent, OutgoingAction, Receipt, ReceiptStatus
from .service import SocialService


class FakeAdapter:
    def __init__(self, account_id="sandbox-account"):
        self.identity = ("fake", account_id)
        self.sent: list[str] = []

    async def send_text(self, action: OutgoingAction) -> Receipt:
        if (action.scope.platform, action.scope.account_id) != self.identity:
            return Receipt(action.action_id, action.scope, ReceiptStatus.NOT_SUBMITTED)
        self.sent.append(action.action_id)
        return Receipt(action.action_id, action.scope, ReceiptStatus.CONFIRMED, "fake-" + action.action_id)


async def run_social_demo(path: Path) -> dict:
    scope = ConversationScope("fake", "sandbox-account", "dm", "owner-dm")
    now, run_id = time.time(), uuid.uuid4().hex
    event = IncomingEvent(scope, "sandbox-owner", run_id, now, now, "你好，小禾。", source_verified=True)
    model, adapter = SyntheticModel(), FakeAdapter()
    decider = RuntimeDecider(model)
    with Ledger(path) as ledger:
        ledger.configure_owner(scope, "sandbox-owner")
        async with SocialService(ledger, decider, [adapter]) as service:
            prepared = await service.prepare(event)
            if prepared["state"] != "QUEUED":
                return {"mode": "synthetic_social", "result": prepared}
            delivered = await service.dispatch(prepared["action_id"])
            duplicate = await service.prepare(event)
            await service.dispatch(prepared["action_id"])
            silent_event = IncomingEvent(scope, "sandbox-owner", run_id + "-silent", now, now, "[silent]", source_verified=True)
            silent = await service.prepare(silent_event)
            counts = ledger.counts()
    with Ledger(path) as reopened:
        restored = reopened.summary(event.key)
    return {"mode": "synthetic_social", "delivery": delivered["state"], "duplicate": duplicate["state"],
            "silent": silent["state"], "after_reopen": restored["state"], "model_calls": model.calls,
            "adapter_sends": len(adapter.sent), "runtime": decider.last_runtime, "ledger_counts": counts,
            "real_model_requests": 0, "real_messages_sent": 0}
