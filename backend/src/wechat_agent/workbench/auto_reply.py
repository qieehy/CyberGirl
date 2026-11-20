"""Fresh selected-peer inbound -> one draft -> one background send."""

import asyncio

from ..bridges.wechat.contracts import digest
from ..social.models import SocialError


class PrivateAutoReply:
    def __init__(self, workbench):
        self.workbench = workbench
        self.enabled = False
        self.epoch = self.floor = 0
        self.started_at = 0
        self.task = None
        self.last_error = self.last_action = None
        self.phase = "stopped"

    def snapshot(self):
        db = self.workbench.ledger._db
        latest = db.execute("""SELECT o.state,o.reason FROM workbench_drafts d
            JOIN outbox o USING(action_id) WHERE d.scope=? AND d.request_id LIKE 'auto:%'
            ORDER BY d.started_at DESC LIMIT 1""", (self.workbench.binding.scope.key(),)).fetchone()
        return dict(
            enabled=self.enabled,
            phase=self.phase,
            last_error=self.last_error,
            last_action=self.last_action,
            last_delivery=dict(latest) if latest else None,
        )

    def set_enabled(self, enabled):
        if type(enabled) is not bool:
            raise SocialError("invalid_request")
        wb = self.workbench
        if enabled:
            wb._open()
            if not wb.private_model or wb.private_sender is None:
                raise SocialError("auto_reply_configuration_required")
            check = getattr(wb.private_sender, "check_ready", None)
            if check:
                check()
            if wb.ledger._db.execute("SELECT paused FROM control").fetchone()[0]:
                raise SocialError("bridge_paused")
            if self.enabled:
                return self.snapshot()
            self.floor = wb.ledger._db.execute(
                "SELECT COALESCE(MAX(rowid),0) FROM bridge_records"
            ).fetchone()[0]
            self.started_at = wb.ledger.now()
            self.last_error = None
            self.phase = "waiting"
        else:
            self.phase = "stopping" if self.task and not self.task.done() else "stopped"
        self.enabled = enabled
        self.epoch += 1
        return self.snapshot()

    def tick(self):
        wb = self.workbench
        if (
            not self.enabled
            or (self.task and not self.task.done())
            or (wb.generation_task and not wb.generation_task.done())
        ):
            return
        now = wb.ledger.now()
        row = wb.ledger._db.execute(
            """SELECT p.message_id,b.rowid AS record_rowid FROM bridge_message_payloads p
            JOIN bridge_records b USING(message_id) JOIN inbox i ON i.event_id=b.event_id
            WHERE p.scope=? AND b.rowid>? AND b.disposition='OBSERVED' AND p.message_type IN ('text','image','sticker','voice')
              AND p.sender_id=? AND p.occurred_at>=? AND p.occurred_at>=? AND p.received_at>=?
              AND i.source_verified=1 AND i.is_self=0 AND i.bridge_generation=?
              AND NOT EXISTS(SELECT 1 FROM workbench_drafts d WHERE d.source_message_id=p.message_id)
            ORDER BY b.rowid DESC LIMIT 1""",
            (
                wb.binding.scope.key(),
                self.floor,
                wb.binding.username,
                int(self.started_at),
                now - 120,
                now - 120,
                wb.binding.generation,
            ),
        ).fetchone()
        if row:
            if wb.companion and wb.companion.task and not wb.companion.task.done():
                wb.companion.task.cancel()
                return
            self.floor = row["record_rowid"]
            self.task = wb._task(self._reply(row[0], self.epoch))

    async def _reply(self, source, epoch):
        wb = self.workbench
        try:
            self.phase = "generating"
            check = getattr(wb.private_sender, "check_ready", None)
            if check:
                check()
            draft = await wb.generate(
                request_id="auto:" + digest([wb.binding.scope.key(), source]),
                source_message_id=source,
                expected_message_revision=wb.state.revision(),
                expected_persona_version=wb.ledger.current_persona().version,
            )
            await wb.generation_task
            draft = wb.state.draft(draft["draft_id"])
            if not self.enabled or self.epoch != epoch or draft["state"] in {"CANCELLED", "SILENT"}:
                return
            if draft["state"] != "READY":
                raise SocialError(draft.get("reason") or "auto_generation_failed")
            if draft.get("send_error"):
                raise SocialError(draft["send_error"])
            self.phase = "sending"
            async with wb.operation_lock:
                if not self.enabled or self.epoch != epoch:
                    return
                wb._open()
                result = await wb.private_sender.send(
                    "auto-send:" + draft["draft_id"],
                    draft["text"],
                    draft_id=draft["draft_id"],
                    expected_version=draft["version"],
                )
            self.last_action = result["action_id"]
            if not result["local_echo"]:
                raise SocialError(
                    result.get("reason")
                    if result.get("state") == "FAILED"
                    else "auto_send_unconfirmed"
                )
            if wb.companion:
                wb.companion.record('reply:'+draft['draft_id'], 'assistant', draft['text'], wb.ledger.now())
        except asyncio.CancelledError:
            raise
        except SocialError as exc:
            if exc.code.startswith(("media_", "image_", "sticker_", "voice_", "invalid_media_", "private_media_")):
                # A single unsupported/missing asset must not stop later text replies.
                self.last_error = exc.code
            elif exc.code not in {"stale_draft", "version_conflict"}:
                self.last_error = exc.code
                self.enabled = False
        except Exception:
            self.last_error = "auto_reply_failed"
            self.enabled = False
        finally:
            self.phase = "waiting" if self.enabled else "stopped"
