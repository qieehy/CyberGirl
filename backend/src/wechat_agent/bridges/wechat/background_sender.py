"""Explicit BMP background adapter for the existing SocialService and Ledger.

Only the reviewed integration/restart markers are admitted in this M0 path.
All real factories are trusted program configuration. There is no default
native worker, foreground fallback, retry, clipboard or model capability.
"""

import asyncio
import hashlib
import re

from ...social.models import Receipt, ReceiptStatus, SocialError
from .background_completion import (
    project_background_result,
    record_background_completion,
    require_background_admission,
)
from .background_contract import INTEGRATED_MARKER, RESTART_MARKER
from .background_journal import BackgroundIntentJournal
from .background_text import require_background_bmp_text
from .contracts import require_send_target
from .send_state import SendState


class WeChatBackgroundAdapter:
    def __init__(self, ledger, binding, *, worker_factory, lease_factory, baseline_provider,
                 writer_lock_factory, admission_checker=require_background_admission,
                 timeout=20, preparation_timeout=10):
        if type(timeout) not in (int, float) or not 1 <= timeout <= 20:
            raise ValueError("background operation must leave cleanup time within 25 seconds")
        if type(preparation_timeout) not in (int, float) or not 0 < preparation_timeout <= min(timeout, 10):
            raise ValueError("bounded background preparation required")
        self.ledger, self.binding, self.state = ledger, binding, SendState(ledger)
        self.worker_factory, self.lease_factory = worker_factory, lease_factory
        self.baseline_provider, self.writer_lock_factory = baseline_provider, writer_lock_factory
        self.admission_checker = admission_checker
        self.timeout, self.preparation_timeout = timeout, preparation_timeout
        self._lock, self._disabled = asyncio.Lock(), False
        self._held_locks = None
        self.last_run = None

    @property
    def identity(self):
        return "wechat", self.binding.account.account_id

    def validate_action(self, action):
        if action.text not in {INTEGRATED_MARKER, RESTART_MARKER}:
            raise SocialError("background_integrated_marker_denied")

    def intent_journal(self, operation, action):
        return BackgroundIntentJournal(self.state, operation, self.binding, action)

    @property
    def capabilities(self):
        return dict(backend="window_return_bmp", target="filehelper", background=True,
                    minimized=False, single_line=True, max_utf16_units=96,
                    supplementary_characters=False, reconciliation_required=self._disabled)

    async def send_text(self, action):
        async with self._lock:
            return await self._send(action)

    async def _send(self, action):
        if self._disabled:
            return Receipt(action.action_id, action.scope, ReceiptStatus.NOT_SUBMITTED)
        operation = worker = lease = locks = None
        touched = cancelled = False
        reaped = restored = released = True
        reason = "background_precheck_rejected"
        self.last_run = dict(action_id=action.action_id, mode="integrated_background_bmp",
                             model_requests=0)
        try:
            require_send_target(self.binding, action, observed_account=self.binding.account.account_id,
                observed_username=self.binding.username, observed_window=self.binding.account.window)
            require_background_bmp_text(action.text)
            self.last_run["marker_sha256"] = hashlib.sha256(action.text.encode()).hexdigest()
            self.last_run["mode"] = "restarted_background_bmp" if action.text == RESTART_MARKER else "integrated_background_bmp"
            self.validate_action(action)
            if self.admission_checker(self.ledger, self.binding) is not True:
                raise SocialError("background_admission_required")
            async with asyncio.timeout(self.timeout):
                baselines = await self.baseline_provider(self.binding)
                locks = self.writer_lock_factory(self.binding)
                if locks is None or not callable(getattr(locks, "close", None)):
                    raise SocialError("background_writer_lock_required")
                if self.admission_checker(self.ledger, self.binding) is not True:
                    raise SocialError("background_admission_required")
                operation = self.state.begin(self.binding, action, baselines)
                self.last_run["operation_id"] = operation
                journal = self.intent_journal(operation, action)
                lease = self.lease_factory(self.binding.account.window,
                    lambda stage, original: self.state.journal(operation, stage, original=original))
                lease.open()
                worker = self.worker_factory(record=journal.record)
                async with asyncio.timeout(self.preparation_timeout):
                    await worker.start(operation, self.binding, action)
                    ready = (await worker._rpc("prepare"))["readiness"]
                    required = {"account_and_filehelper_verified", "both_draft_patterns_empty", "same_editor_and_button",
                                "standalone_identity_verified", "background_observer_active", "no_capture",
                                "button_not_pressed", "class_entry_matches_pinned_code", "covered_by_other_app"}
                    if type(ready) is not dict or set(ready) != required or any(ready[key] is not True for key in required):
                        raise SocialError("background_preparation_incomplete")
                result = project_background_result(await worker._rpc("run_background"))
                self.last_run.update(result)
                reason = result["control_result"]["reason"]
        except asyncio.CancelledError:
            cancelled, reason = True, "background_operation_cancelled"
        except TimeoutError:
            reason = "background_operation_timeout"
        except Exception as exc:
            reason = exc.code if isinstance(exc, SocialError) and re.fullmatch(r"[a-z0-9_]{1,128}", exc.code) else "background_operation_failed"
        finally:
            async def cleanup():
                nonlocal reaped, restored, released, touched
                try:
                    if worker is not None:
                        try:
                            async with asyncio.timeout(2):
                                reaped = await worker.stop() is True
                        except BaseException:
                            reaped = False
                finally:
                    try:
                        restored = lease.close(restore=reaped) is True if lease is not None else True
                    except BaseException:
                        restored = False
                    finally:
                        if locks is not None:
                            if reaped:
                                try:
                                    locks.close()
                                except BaseException:
                                    released = False
                            else:
                                self._held_locks, released = locks, False
                if operation is not None:
                    touched = self.state.finish(operation, reaped=reaped, restored=restored, reason=reason)
                    if touched:
                        self._disabled = True
                    row = self.ledger._db.execute("""SELECT input_permitted,submit_permitted FROM wechat_send_operations
                                                   WHERE operation_id=?""", (operation,)).fetchone()
                    self.last_run.update(input_permitted=row[0], submit_permitted=row[1])
                    if "control_result" in self.last_run:
                        proof = {key: self.last_run[key] for key in ("control_result", "worker_local_cleanup_verified", "observation")}
                        try:
                            self.last_run["clean_control_recorded"] = record_background_completion(
                                self.ledger, operation, proof, reaped=reaped, restored=restored, locks_released=released)
                        except SocialError:
                            self.last_run["clean_control_recorded"] = False
                if touched or not all((reaped, restored, released)):
                    self._disabled = True
                self.last_run.update(worker_reaped=reaped, provider_restored=restored,
                                     locks_released=released, reason=reason)

            task = asyncio.create_task(cleanup())
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    cancelled = True
            task.result()
        if cancelled:
            raise asyncio.CancelledError
        return Receipt(action.action_id, action.scope, ReceiptStatus.UNKNOWN if touched else ReceiptStatus.NOT_SUBMITTED)
