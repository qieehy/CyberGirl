"""Bounded orchestration around the ledger, existing Runtime and an explicit adapter."""

import asyncio
from typing import Protocol

from .ledger import Ledger
from .models import IncomingEvent, OutgoingAction, Receipt, ReceiptStatus, SocialError


class Adapter(Protocol):
    @property
    def identity(self) -> tuple[str, str]: ...
    async def send_text(self, action: OutgoingAction) -> Receipt: ...


class SocialService:
    def __init__(self, ledger: Ledger, decider, adapters: list[Adapter], *, dispatch_timeout=10):
        if type(dispatch_timeout) not in (int, float) or not 0 < dispatch_timeout <= 25:
            raise ValueError("dispatch deadline must be positive and at most 25 seconds")
        self.dispatch_timeout = dispatch_timeout
        self.ledger, self.decider = ledger, decider
        self.adapters = {adapter.identity: adapter for adapter in adapters}
        if len(self.adapters) != len(adapters):
            raise SocialError("duplicate_adapter_identity")
        self._decision_lock = asyncio.Lock()
        self._tasks = set()
        self._closed = False
        self._close_task = None

    def _adapter(self, scope):
        identity = (scope.platform, scope.account_id)
        adapter = self.adapters.get(identity)
        return adapter if adapter is not None and adapter.identity == identity else None

    async def _run(self, operation, *args):
        if self._closed:
            raise SocialError("closed")
        if len(self._tasks) >= 4:
            raise SocialError("busy")
        task = asyncio.create_task(operation(*args))
        self._tasks.add(task)
        try:
            return await task
        finally:
            self._tasks.discard(task)

    async def prepare(self, event: IncomingEvent):
        return await self._run(self._prepare, event)

    async def _prepare(self, event):
        if self._adapter(event.scope) is None:
            return {"state": "REJECTED", "reason": "unknown_adapter_identity", "action_id": None}
        result, fresh = self.ledger.accept(event)
        if not fresh:
            return result
        return await self._decide(event)

    async def decide_buffered(self, event):
        return await self._run(self._decide, event)

    async def _decide(self, event):
        try:
            # New events have already advanced context versions before waiting,
            # so an older in-flight decision cannot enqueue a stale reply.
            async with asyncio.timeout(120), self._decision_lock:
                if self.ledger.start_decision(event.key):
                    persona, history = self.ledger.decision_context(event.key)
                    decision = await self.decider.decide(event, history, persona=persona)
                    self.ledger.finish_decision(event.key, decision)
        except asyncio.CancelledError:
            self.ledger.fail_decision(event.key, cancelled=True)
            raise
        except Exception:
            self.ledger.fail_decision(event.key)
        return self.ledger.summary(event.key)

    async def dispatch(self, action_id: str):
        return await self._run(self._dispatch, action_id)

    async def _dispatch(self, action_id):
        action = self.ledger.claim(action_id)
        if action is None:
            return self.ledger.action_summary(action_id)
        adapter = self._adapter(action.scope)
        if adapter is None:
            self.ledger.finish_delivery(action_id, "FAILED", "adapter_identity_changed")
            return self.ledger.action_summary(action_id)
        try:
            async with asyncio.timeout(self.dispatch_timeout):
                receipt = await adapter.send_text(action)
            if (not isinstance(receipt, Receipt) or receipt.action_id != action_id
                    or receipt.scope != action.scope or self._adapter(action.scope) is not adapter):
                self.ledger.finish_delivery(action_id, "UNCONFIRMED", "receipt_mismatch")
            elif receipt.status == ReceiptStatus.CONFIRMED:
                self.ledger.finish_delivery(action_id, "CONFIRMED", receipt_id=receipt.receipt_id)
            elif receipt.status == ReceiptStatus.NOT_SUBMITTED:
                self.ledger.finish_delivery(action_id, "FAILED", "not_submitted")
            else:
                self.ledger.finish_delivery(action_id, "UNCONFIRMED", "submission_unknown")
        except asyncio.CancelledError:
            self.ledger.finish_delivery(action_id, "UNCONFIRMED", "submission_cancelled")
            raise
        except Exception:
            self.ledger.finish_delivery(action_id, "UNCONFIRMED", "submission_unknown")
        return self.ledger.action_summary(action_id)

    async def __aenter__(self):
        if self._closed:
            raise SocialError("closed")
        return self

    async def __aexit__(self, *args):
        await self.aclose()

    async def aclose(self):
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._finish_close())
        await asyncio.shield(self._close_task)

    async def _finish_close(self):
        pending = list(self._tasks)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await self.decider.aclose()
