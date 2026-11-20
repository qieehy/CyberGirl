"""Synthetic-only passive group admission, bounded batching and durable budgets."""

import asyncio
import json
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from .models import SocialError, identifier

SHANGHAI = timezone(timedelta(hours=8))
QUIET_SECONDS, MAX_SECONDS = 3, 10
MAX_MESSAGES, MAX_CHARACTERS, MAX_GROUPS = 20, 4000, 4


def budget_reason(db, scope, now, *, include_calls=False, exclude_action=None):
    day = datetime.fromtimestamp(now, SHANGHAI).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    # Queued replies reserve sending capacity; known non-submissions release it.
    sends = [r[0] for r in db.execute("""SELECT COALESCE(a.started_at,g.started_at) FROM outbox o
             JOIN inbox i USING(event_id) JOIN group_runs g USING(event_id)
             LEFT JOIN attempts a ON a.action_id=o.action_id WHERE i.scope=? AND o.action_id!=?
             AND (o.state IN ('SENDING','CONFIRMED','UNCONFIRMED') OR (o.state='QUEUED' AND o.expires_at>?))""",
                                    (scope, exclude_action or "", now))]
    collections = [("send", sends)]
    if include_calls:
        calls = [r[0] for r in db.execute("SELECT started_at FROM group_runs WHERE scope=?", (scope,))]
        collections.append(("model", calls))
    for name, values in collections:
        if any(t > now for t in values):
            return "clock_rollback"
        if sum(t >= day for t in values) >= 10:
            return name + "_daily_limit"
        if sum(t > now - 600 for t in values) >= 3:
            return name + "_window_limit"
        if values and now - max(values) < 30:
            return name + "_cooldown"
    return None


def group_admission(db, event, buffer_group, now):
    if not buffer_group or event.scope.platform != "fake":
        return "synthetic_group_only"
    policy = db.execute("SELECT * FROM group_policies WHERE scope=?", (event.scope.key(),)).fetchone()
    if not policy or event.sender_id == policy["bot_id"]:
        return "policy_denied"
    rows = db.execute("SELECT text,sender_id FROM inbox WHERE scope=? AND state='BUFFERED'", (event.scope.key(),)).fetchall()
    # A conservative bound includes escaped text and identity framing, without truncation.
    size = sum(len(json.dumps(dict(r), ensure_ascii=False)) + 80 for r in rows)
    size += len(json.dumps({"text": event.text, "sender_id": event.sender_id}, ensure_ascii=False)) + 80
    if len(rows) >= MAX_MESSAGES or size > MAX_CHARACTERS - 100:
        return "group_buffer_full"
    return None


def configure_group(ledger, scope, *, bot_id, consent_ref, enabled=True, initialize=False):
    identifier(bot_id)
    identifier(consent_ref)
    if scope.platform != "fake" or scope.conversation_type != "group" or type(enabled) is not bool:
        raise SocialError("synthetic_group_only")
    with ledger._transaction() as db:
        old = db.execute("""SELECT s.enabled,p.bot_id,p.consent_ref FROM scopes s
                            JOIN group_policies p USING(scope) WHERE scope=?""", (scope.key(),)).fetchone()
        if old and initialize:
            return
        if old and (old["enabled"], old["bot_id"], old["consent_ref"]) == (enabled, bot_id, consent_ref):
            return
        db.execute("""INSERT INTO scopes(scope,owner,enabled,revision) VALUES(?,?,?,1)
                    ON CONFLICT(scope) DO UPDATE SET owner=excluded.owner,enabled=excluded.enabled,revision=revision+1""",
                   (scope.key(), bot_id, int(enabled)))
        db.execute("""INSERT INTO group_policies VALUES(?,?,?) ON CONFLICT(scope)
                    DO UPDATE SET bot_id=excluded.bot_id,consent_ref=excluded.consent_ref""", (scope.key(), bot_id, consent_ref))
        ledger._cancel_queued(db, scope.key(), "policy_changed")


def seal_batch(ledger, scope):
    with ledger._transaction() as db:
        rows = db.execute("SELECT * FROM inbox WHERE scope=? AND state='BUFFERED' ORDER BY seq", (scope.key(),)).fetchall()
        if not rows:
            return None
        last = rows[-1]
        if not ledger._valid(db, last) or ledger.now() >= last["received_at"] + 120:
            db.execute("UPDATE inbox SET state='CANCELLED',reason='stale_context_or_policy' WHERE scope=? AND state='BUFFERED'", (scope.key(),))
            return None
        valid = []
        for row in rows:
            if (row["policy_revision"], row["global_revision"], row["persona_version"]) != (
                    last["policy_revision"], last["global_revision"], last["persona_version"]):
                db.execute("UPDATE inbox SET state='CANCELLED',reason='stale_context_or_policy' WHERE event_id=?", (row["event_id"],))
                continue
            quoted = row["reply_to"] is not None and db.execute("""SELECT 1 FROM outbox o JOIN inbox i USING(event_id)
                        WHERE i.scope=? AND o.state='CONFIRMED' AND o.platform_message_id=?""", (scope.key(), row["reply_to"])).fetchone() is not None
            valid.append({"sender_id": row["sender_id"], "text": row["text"], "trigger": bool(row["mention_self"] or quoted)})
        if not any(r["trigger"] for r in valid):
            db.execute("UPDATE inbox SET state='IGNORED',reason='no_group_trigger' WHERE scope=? AND state='BUFFERED'", (scope.key(),))
            return None
        content = "GROUP_MESSAGES_JSON:" + json.dumps(valid, ensure_ascii=False)
        if len(content) > MAX_CHARACTERS:
            raise SocialError("group_buffer_full")
        db.execute("UPDATE inbox SET state='MERGED',batch_id=?,reason='merged_into_batch' WHERE scope=? AND state='BUFFERED'",
                   (last["event_id"], scope.key()))
        db.execute("UPDATE inbox SET state='RECEIVED',model_text=?,reason=NULL WHERE event_id=?", (content, last["event_id"]))
        key = last["event_id"]
    return replace(ledger.recorded_event(scope, key), text=content)


class GroupBuffer:
    """Owns a single deadline scheduler and at most four flush tasks, no agent loop."""

    def __init__(self, service, *, monotonic=time.monotonic):
        from .decision import SyntheticModel

        if not isinstance(getattr(service.decider, "model", None), SyntheticModel):
            raise SocialError("synthetic_group_only")
        self.service, self.ledger, self.monotonic = service, service.ledger, monotonic
        self.pending, self.tasks = {}, set()
        self.wake = asyncio.Event()
        self.worker = None
        self.closed = False

    def submit(self, event):
        if self.closed:
            raise SocialError("closed")
        if event.scope.conversation_type != "group" or event.scope.platform != "fake":
            raise SocialError("synthetic_group_only")
        if self.service._adapter(event.scope) is None:
            return {"state": "REJECTED", "reason": "unknown_adapter_identity", "action_id": None}
        key = event.scope.key()
        if (key not in self.pending and len(self.pending) >= MAX_GROUPS) or len(self.tasks) >= MAX_GROUPS:
            raise SocialError("busy")
        result, fresh = self.ledger.accept(event, buffer_group=True)
        if fresh:
            now = self.monotonic()
            first = self.pending[key][1] if key in self.pending else now
            self.pending[key] = (event.scope, first, min(now + QUIET_SECONDS, first + MAX_SECONDS))
            self.wake.set()
        return result

    async def _flush(self, scope):
        event = seal_batch(self.ledger, scope)
        if event is not None:
            try:
                await self.service.decide_buffered(event)
            except SocialError:
                self.ledger.fail_decision(event.key)

    def _completed(self, task):
        self.tasks.discard(task)
        self.wake.set()
        if not task.cancelled():
            task.exception()  # Exceptions do not become payload-bearing background logs.

    async def flush_due(self):
        due = [key for key, (_, _, deadline) in self.pending.items() if deadline <= self.monotonic()]
        started = []
        for key in due:
            if len(self.tasks) >= MAX_GROUPS:
                break
            scope, _, _ = self.pending.pop(key)
            task = asyncio.create_task(self._flush(scope))
            self.tasks.add(task)
            task.add_done_callback(self._completed)
            started.append(task)
        return started

    async def _loop(self):
        while not self.closed:
            self.wake.clear()
            await self.flush_due()
            delay = max(.05, min((r[2] - self.monotonic() for r in self.pending.values()), default=60))
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=delay)
            except TimeoutError:
                pass

    async def __aenter__(self):
        self.worker = asyncio.create_task(self._loop())
        return self

    async def __aexit__(self, *args):
        await self.aclose()

    async def aclose(self):
        self.closed = True
        pending = list(self.tasks) + ([self.worker] if self.worker else [])
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        with self.ledger._transaction() as db:
            db.execute("UPDATE inbox SET state='CANCELLED',reason='interrupted_decision' WHERE state='BUFFERED'")
        self.pending.clear()
