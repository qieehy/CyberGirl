"""Single-use sending orchestration with injected, trusted native implementations.

There is deliberately no default native worker: a display-name-only backend
cannot supply the canonical target observations required by this adapter.
"""

import asyncio
import hashlib
import re
from dataclasses import dataclass

from ...social.models import Receipt, ReceiptStatus, SocialError
from .contracts import WindowBinding, require_send_target
from .send_state import SendState


@dataclass(frozen=True)
class InputObservation:
    account_id: str
    username: str
    window: WindowBinding
    identity_method: str
    input_token: str
    focused: bool
    empty: bool
    draft_sha256: str

    verify_target = staticmethod(require_send_target)

    def verify(self, binding, action, *, initial=None, require_focus=True):
        self.verify_target(binding, action, observed_account=self.account_id,
                            observed_username=self.username, observed_window=self.window)
        if (self.identity_method != "native_username" or type(self.focused) is not bool or (require_focus and not self.focused)
                or type(self.empty) is not bool or not isinstance(self.input_token, str)
                or not 1 <= len(self.input_token) <= 128):
            raise SocialError("unverified_input_target")
        expected = hashlib.sha256(("" if initial is None else action.text).encode()).hexdigest()
        if (self.draft_sha256 != expected or self.empty != (initial is None)
                or (initial is not None and self.input_token != initial.input_token)):
            raise SocialError("draft_or_input_changed")


class AccessibilityLease:
    """Same-handle gate restoration; never enable system screen reading."""

    def __init__(self, memory, window, record):
        self.memory, self.window, self.record = memory, window, record
        self.original = None
        self.attempted = False

    def open(self):
        facts = self.memory.preflight()
        actual = WindowBinding(facts["pid"], int(facts["created_filetime"]), facts["hwnd"],
                               facts["exe_sha256"], facts["dll_sha256"])
        if actual != self.window:
            raise SocialError("accessibility_window_changed")
        self.original = self.memory.read()
        if self.original not in (0, 1):
            raise SocialError("unexpected_accessibility_value")
        self.record("GATE_CHECKED", self.original)
        if self.original == 0:
            self.record("GATE_ENABLE_INTENT", self.original)
            self.memory.validate()
            if self.memory.read() != self.original:
                raise SocialError("accessibility_value_changed")
            self.attempted = True  # A failed write may still have changed the byte.
            self.memory.write(1)
            if self.memory.read() != 1:
                raise SocialError("accessibility_enable_failed")
            self.record("GATE_ENABLED", self.original)

    def close(self, *, restore=True):
        verified = True
        try:
            if self.attempted:
                if not restore:
                    return False  # A still-running worker forbids releasing its provider lease.
                try:
                    self.record("GATE_RESTORE_INTENT", self.original)
                except BaseException:
                    verified = False  # Journal failure cannot prevent the actual restoration.
                self.memory.validate()
                current = self.memory.read()
                if current not in (self.original, 1):
                    raise SocialError("accessibility_value_changed")
                if current != self.original:
                    self.memory.write(self.original)
                if self.memory.read() != self.original:
                    raise SocialError("accessibility_restore_failed")
                self.record("GATE_RESTORED", self.original)
        except BaseException:
            verified = False
        finally:
            try:
                self.memory.close()
            except BaseException:
                verified = False
        return verified


class WeChatSendAdapter:
    """Parent owns the Ledger; the worker owns no database or model capability.

    Factories are program configuration, never user/model-controlled commands.
    Workers must start without UI effects, then wait for each parent permit.
    """

    def __init__(self, ledger, binding, *, worker_factory, lease_factory, baseline_provider, timeout=7, preparation_timeout=0, writer_lock_factory=None):
        if not 0 < timeout <= 7:
            raise ValueError("worker deadline must leave cleanup time within SocialService's 10 seconds")
        if not 0 <= preparation_timeout <= 15:
            raise ValueError("read-only preparation must be bounded to 15 seconds")
        self.ledger, self.binding = ledger, binding
        self.state = SendState(ledger)
        self.worker_factory, self.lease_factory = worker_factory, lease_factory
        self.baseline_provider, self.timeout = baseline_provider, timeout
        self.preparation_timeout = preparation_timeout
        self._lock = asyncio.Lock()
        self._disabled = False
        self.writer_lock_factory, self._held_locks = writer_lock_factory, None

    @property
    def identity(self):
        return "wechat", self.binding.account.account_id

    async def send_text(self, action):
        async with self._lock:
            return await self._send(action)

    async def _send(self, action):
        if self._disabled:
            return Receipt(action.action_id, action.scope, ReceiptStatus.NOT_SUBMITTED)
        operation_id = worker = lease = locks = None
        touched, cancelled, reason = False, False, "precheck_rejected"

        async def prepare():
            nonlocal operation_id, lease, worker, locks
            # Reject another account/target before starting any source/native access.
            require_send_target(self.binding, action, observed_account=self.binding.account.account_id,
                                observed_username=self.binding.username, observed_window=self.binding.account.window)
            baselines = await self.baseline_provider(self.binding)
            if self.writer_lock_factory is not None:
                locks = self.writer_lock_factory(self.binding)
                if locks is None or not callable(getattr(locks, "close", None)):
                    raise SocialError("writer_lock_required")
            operation_id = self.state.begin(self.binding, action, baselines)
            lease = self.lease_factory(self.binding.account.window,
                lambda stage, original: self.state.journal(operation_id, stage, original=original))
            lease.open()
            worker = self.worker_factory()
            bind_journal = getattr(worker, "bind_gesture_journal", None)
            if bind_journal:
                bind_journal(lambda stage: self.state.gesture_intent(operation_id, self.binding, action, stage),
                             lambda result: self.state.gesture_result(operation_id, self.binding, action, result))
            await worker.start(operation_id, self.binding, action)
            if self.preparation_timeout:
                prepared = await worker.warmup()
                if not isinstance(prepared, InputObservation):
                    raise SocialError("invalid_preparation_observation")
                prepared.verify(self.binding, action, require_focus=False)
                self.state.journal(operation_id, "NATIVE_PREPARED", original=lease.original)

        try:
            if self.preparation_timeout:
                async with asyncio.timeout(self.preparation_timeout):
                    await prepare()
            async with asyncio.timeout(self.timeout):
                if not self.preparation_timeout:
                    await prepare()
                focus_method = getattr(worker, "prepare_input_focus", None)
                if focus_method:
                    self.state.permit_focus(operation_id, self.binding, action)
                    await focus_method()
                initial = await worker.inspect()
                if not isinstance(initial, InputObservation):
                    raise SocialError("invalid_input_observation")
                initial.verify(self.binding, action)
                self.state.permit(operation_id, self.binding, action, "INPUT_PERMITTED")
                touched = True
                await worker.input_text()
                current = await worker.inspect()
                if not isinstance(current, InputObservation):
                    raise SocialError("invalid_input_observation")
                current.verify(self.binding, action, initial=initial)
                self.state.permit(operation_id, self.binding, action, "SUBMIT_PERMITTED")
                await worker.submit_once()
                reason = "submission_unknown"  # UI success is never delivery confirmation.
        except asyncio.CancelledError:
            cancelled, reason = True, "submission_cancelled"
        except TimeoutError:
            reason = "ui_operation_timeout"
        except Exception as exc:
            reason = (exc.code if isinstance(exc, SocialError) and re.fullmatch(r"[a-z_0-9]{1,128}", exc.code)
                      else "ui_operation_rejected_or_failed")
        finally:
            async def cleanup():
                reaped = True
                if worker is not None:
                    try:
                        async with asyncio.timeout(2):
                            reaped = (await worker.stop()) is True
                    except BaseException:
                        reaped = False
                try:
                    restored = (lease.close(restore=reaped) is True) if lease is not None else True
                except BaseException:
                    restored = False
                released = True
                if locks is not None:
                    if reaped:
                        try:
                            locks.close()
                        except BaseException:
                            released = False
                    else:
                        self._held_locks, released = locks, False
                if not reaped or not restored or not released or touched:
                    self._disabled = True
                if operation_id is not None:
                    self.state.finish(operation_id, reaped=reaped, restored=restored, reason=reason)

            task = asyncio.create_task(cleanup())
            # Cancellation cannot release the UI lock while the native worker is alive.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    cancelled = True
            task.result()
        if cancelled:
            raise asyncio.CancelledError
        return Receipt(action.action_id, action.scope, ReceiptStatus.UNKNOWN if touched else ReceiptStatus.NOT_SUBMITTED)
