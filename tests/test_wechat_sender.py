"""Sending failure boundaries, all synthetic. No WeChat or model access."""

import asyncio
import hashlib
import os
import sqlite3
import sys
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from tests.test_wechat_bridge import BINDING, NOW, WINDOW, bind, queue, record
from wechat_agent.bridges.wechat.contracts import Cursor
from wechat_agent.bridges.wechat.send_process import JsonSendWorker, python_worker_command
from wechat_agent.bridges.wechat.send_state import SendState
from wechat_agent.bridges.wechat.send_worker import WorkerSession
from wechat_agent.bridges.wechat.sender import (
    AccessibilityLease,
    InputObservation,
    WeChatSendAdapter,
)
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import SocialError
from wechat_agent.social.service import SocialService


class Memory:
    def __init__(self, value=0, *, restore_failure=False, activation_failure=False):
        self.value, self.writes = value, []
        self.restore_failure, self.activation_failure = restore_failure, activation_failure
        self.closed = False

    def preflight(self):
        return {"pid": WINDOW.pid, "hwnd": WINDOW.hwnd, "created_filetime": str(WINDOW.created_filetime),
                "exe_sha256": WINDOW.exe_sha256, "dll_sha256": WINDOW.dll_sha256}

    def validate(self):
        pass

    def read(self):
        return self.value

    def write(self, value):
        self.writes.append(value)
        if value == 0 and self.restore_failure:
            return
        self.value = value
        if value == 1 and self.activation_failure:
            raise OSError("synthetic partial write")

    def close(self):
        self.closed = True


class Worker:
    def __init__(self, *, initial_change=None, final_change=None, after_input=None, hang=None, reaped=True):
        self.initial_change, self.final_change = initial_change or {}, final_change or {}
        self.after_input, self.hang, self.reaped = after_input, hang, reaped
        self.calls, self.entered = [], asyncio.Event()
        self.input_done = False

    async def start(self, operation_id, binding, action):
        self.calls.append("start")
        self.action = action

    async def inspect(self):
        value = InputObservation(BINDING.account.account_id, "filehelper", WINDOW, "native_username",
            "fixture-input", True, not self.input_done,
            hashlib.sha256((self.action.text if self.input_done else "").encode()).hexdigest())
        return replace(value, **(self.final_change if self.input_done else self.initial_change))

    async def input_text(self):
        self.calls.append("input")
        self.input_done = True
        if self.after_input:
            self.after_input()
        if self.hang == "input":
            self.entered.set()
            await asyncio.Event().wait()

    async def submit_once(self):
        self.calls.append("submit")
        if self.hang == "submit":
            self.entered.set()
            await asyncio.Event().wait()

    async def stop(self):
        self.calls.append("stop")
        await asyncio.sleep(0)
        return self.reaped


async def baselines(_binding):
    return {"message_0": Cursor(10, 1)}


class NoModel:
    async def aclose(self):
        pass

    async def decide(self, *args, **kwargs):
        pytest.fail("sending must not call a model")


def setup(ledger, worker, memory=None, *, timeout=7):
    store = bind(ledger, mode="draft")
    action_id = queue(ledger, store)
    memory = memory or Memory()
    adapter = WeChatSendAdapter(ledger, BINDING, worker_factory=lambda: worker,
        lease_factory=lambda window, log: AccessibilityLease(memory, window, log),
        baseline_provider=baselines, timeout=timeout)
    return action_id, adapter, memory


def test_preinput_identity_draft_focus_and_window_rejections_have_zero_input(tmp_path):
    async def scenario():
        for index, change in enumerate((
            {"identity_method": "display_name"}, {"username": "contact"},
            {"window": replace(WINDOW, created_filetime=999)}, {"focused": False},
            {"empty": False}, {"draft_sha256": "a" * 64},
        )):
            with Ledger(tmp_path / f"pre-{index}.db", now=lambda: NOW) as ledger:
                worker = Worker(initial_change=change)
                action_id, adapter, memory = setup(ledger, worker)
                async with SocialService(ledger, NoModel(), [adapter]) as service:
                    assert (await service.dispatch(action_id))["state"] == "FAILED"
                    assert (await service.dispatch(action_id))["state"] == "FAILED"
                assert worker.calls == ["start", "stop"]
                assert memory.writes == [1, 0] and memory.value == 0 and memory.closed
                row = ledger._db.execute("SELECT * FROM wechat_send_operations").fetchone()
                assert not row["input_permitted"] and row["restore_verified"] and row["worker_reaped"]
                assert ledger._db.execute("SELECT blocked_reason FROM wechat_send_control").fetchone()[0] is None
    asyncio.run(scenario())


def test_postinput_target_draft_pause_expiry_and_timeout_never_submit_or_retry(tmp_path):
    async def scenario():
        for index, change in enumerate(({"focused": False}, {"username": "contact"},
                {"input_token": "new-input"}, {"draft_sha256": "b" * 64}, {}, {}, {})):
            clock = [NOW]
            path = tmp_path / f"post-{index}.db"
            with Ledger(path, now=lambda clock=clock: clock[0]) as ledger:
                callback = ((lambda: ledger.pause(True)) if index == 4 else
                            (lambda clock=clock: clock.__setitem__(0, NOW + 121)) if index == 5 else None)
                worker = Worker(final_change=change, after_input=callback, hang="input" if index == 6 else None)
                action_id, adapter, memory = setup(ledger, worker, timeout=0.04 if index == 6 else 7)
                async with SocialService(ledger, NoModel(), [adapter]) as service:
                    assert (await service.dispatch(action_id))["state"] == "UNCONFIRMED"
                    await service.dispatch(action_id)
                assert worker.calls == ["start", "input", "stop"] and memory.value == 0
                assert ledger._db.execute("SELECT blocked_reason FROM wechat_send_control").fetchone()[0]
                assert ledger.counts()["attempts"] == 1
            with Ledger(path, now=lambda: NOW) as reopened:
                assert reopened.claim(action_id) is None
                assert reopened._db.execute("SELECT blocked_reason FROM wechat_send_control").fetchone()[0]
    asyncio.run(scenario())


def test_submit_once_echo_correlation_and_manual_reconciliation_never_resend(tmp_path):
    async def scenario():
        with Ledger(tmp_path / "echo.db", now=lambda: NOW) as ledger:
            worker = Worker()
            action_id, adapter, memory = setup(ledger, worker)
            async with SocialService(ledger, NoModel(), [adapter]) as service:
                assert (await service.dispatch(action_id))["state"] == "UNCONFIRMED"
            assert worker.calls == ["start", "input", "submit", "stop"] and memory.value == 0
            state = SendState(ledger)
            echo = record(2, sort_seq=11, sender_id=BINDING.account.self_id, text="synthetic reply")
            for invalid in ((replace(echo, local_id=1, sort_seq=10),), (replace(echo, text="synthetic rep"),),
                            (replace(echo, sender_id="other"),), (echo, replace(echo, local_id=3)),
                            (echo, replace(echo, text="conflicting source record")),
                            (replace(echo, conversation="contact"),), (replace(echo, shard="message_1"),)):
                with pytest.raises(SocialError):
                    state.local_echo(BINDING, action_id, invalid)
            assert state.local_echo(BINDING, action_id, (echo, echo))
            assert not state.local_echo(BINDING, action_id, (echo,))
            assert ledger.action_summary(action_id)["state"] == "UNCONFIRMED"
            from wechat_agent.bridges.wechat.contracts import DeliveryEvidence
            from wechat_agent.bridges.wechat.store import BridgeStore

            BridgeStore(ledger).record_evidence(DeliveryEvidence("manual", action_id, BINDING.scope,
                BINDING.generation, "manual_confirmation", "operator-checked", hashlib.sha256(b"synthetic reply").hexdigest()))
            assert ledger.action_summary(action_id)["state"] == "CONFIRMED"
            assert ledger.claim(action_id) is None
            assert ledger.counts()["attempts"] == 1
            # Confirmation does not silently clear a channel's native review requirement.
            assert ledger._db.execute("SELECT blocked_reason FROM wechat_send_control").fetchone()[0]
    asyncio.run(scenario())


def test_cancellation_reaping_and_gate_failures_are_persisted_before_unlock(tmp_path):
    async def scenario():
        with Ledger(tmp_path / "cancel.db", now=lambda: NOW) as ledger:
            worker = Worker(hang="submit")
            action_id, adapter, memory = setup(ledger, worker)
            service = SocialService(ledger, NoModel(), [adapter])
            task = asyncio.create_task(service.dispatch(action_id))
            await worker.entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await service.aclose()
            assert worker.calls == ["start", "input", "submit", "stop"] and memory.value == 0
            assert ledger.action_summary(action_id)["state"] == "UNCONFIRMED"
            assert ledger._db.execute("SELECT finished,worker_reaped,restore_verified FROM wechat_send_operations").fetchone()[:] == (1, 1, 1)
        for index, memory in enumerate((Memory(restore_failure=True), Memory(activation_failure=True), Memory(1))):
            with Ledger(tmp_path / f"gate-{index}.db", now=lambda: NOW) as ledger:
                worker = Worker()
                action_id, adapter, _ = setup(ledger, worker, memory)
                async with SocialService(ledger, NoModel(), [adapter]) as service:
                    await service.dispatch(action_id)
                assert memory.closed
                if index == 0:
                    assert ledger._db.execute("SELECT blocked_reason FROM wechat_send_control").fetchone()[0] == "accessibility_restore_failed"
                elif index == 1:
                    assert memory.value == 0 and worker.calls == []
                else:
                    assert memory.value == 1 and memory.writes == [] and "submit" in worker.calls
        with Ledger(tmp_path / "not-reaped.db", now=lambda: NOW) as ledger:
            worker = Worker(reaped=False)
            action_id, adapter, memory = setup(ledger, worker)
            async with SocialService(ledger, NoModel(), [adapter]) as service:
                await service.dispatch(action_id)
            assert memory.writes == [1] and memory.closed
            assert ledger._db.execute("SELECT blocked_reason FROM wechat_send_control").fetchone()[0] == "ui_worker_not_reaped"
        for failing_stage in ("GATE_ENABLE_INTENT", "GATE_ENABLED", "GATE_RESTORE_INTENT", "GATE_RESTORED"):
            memory = Memory()

            def log(stage, original, failing_stage=failing_stage):
                if stage == failing_stage:
                    raise OSError("synthetic journal failure")

            lease = AccessibilityLease(memory, WINDOW, log)
            try:
                lease.open()
            except OSError:
                pass
            lease.close()
            assert memory.value == 0 and memory.closed
    asyncio.run(scenario())


def test_permit_atomicity_restart_and_schema5_backup_rollback(tmp_path, monkeypatch):
    from wechat_agent.bridges.wechat import send_state

    path = tmp_path / "restart.db"
    with Ledger(path, now=lambda: NOW) as ledger:
        store = bind(ledger, mode="draft")
        action_id = queue(ledger, store)
        action = ledger.claim(action_id)
        state = SendState(ledger)
        operation = state.begin(BINDING, action, {"message_0": Cursor(10, 1)})
        ledger._db.execute("""CREATE TRIGGER fail_permission BEFORE UPDATE OF input_permitted ON wechat_send_operations
                            BEGIN SELECT RAISE(ABORT,'synthetic disk failure'); END""")
        with pytest.raises(sqlite3.IntegrityError):
            state.permit(operation, BINDING, action, "INPUT_PERMITTED")
        assert ledger._db.execute("SELECT input_permitted FROM wechat_send_operations").fetchone()[0] == 0
        ledger._db.execute("DROP TRIGGER fail_permission")
        with pytest.raises(SocialError, match="ui_operation_busy"):
            state.begin(BINDING, action, {"message_0": Cursor(10, 1)})
        state.permit(operation, BINDING, action, "INPUT_PERMITTED")
        with pytest.raises(SocialError, match="send_permit_already_used"):
            state.permit(operation, BINDING, action, "INPUT_PERMITTED")
    with Ledger(path, now=lambda: NOW) as ledger:
        assert ledger.claim(action_id) is None
        assert ledger.action_summary(action_id)["state"] == "UNCONFIRMED"
        assert ledger._db.execute("SELECT phase FROM wechat_send_operations").fetchone()[0] == "INTERRUPTED"
        assert ledger._db.execute("SELECT blocked_reason FROM wechat_send_control").fetchone()[0] == "interrupted_ui_operation"
    migration = tmp_path / "migration.db"
    with Ledger(migration, now=lambda: NOW) as ledger:
        ledger.save_persona(1, "preserved", "style", "notes")
        for table in ("reader_private_targets", "workbench_requests", "workbench_draft_revisions", "workbench_drafts", "workbench_context", "bridge_message_payloads", "wechat_channel_reviews", "reader_shards", "reader_accounts", "wechat_send_journal", "wechat_send_operations", "wechat_send_control"):
            ledger._db.execute(f"DROP TABLE {table}")
        ledger._db.execute("ALTER TABLE bridge_cursors DROP COLUMN local_fence")
        ledger._db.execute("ALTER TABLE inbox DROP COLUMN origin")
        ledger._db.execute("PRAGMA user_version=5")
    original = send_state.migrate

    def fail(db):
        original(db)
        raise RuntimeError("synthetic migration failure")

    monkeypatch.setattr(send_state, "migrate", fail)
    with pytest.raises(RuntimeError), Ledger(migration):
        pass
    with sqlite3.connect(migration) as check:
        assert check.execute("PRAGMA user_version").fetchone()[0] == 5
        assert not check.execute("SELECT 1 FROM sqlite_master WHERE name='wechat_send_control'").fetchone()
    monkeypatch.setattr(send_state, "migrate", original)
    with Ledger(migration) as ledger:
        assert ledger.current_persona().name == "preserved"
        assert ledger._db.execute("PRAGMA user_version").fetchone()[0] == 10
    assert list(tmp_path.glob("migration.db.v5-*.bak"))


def test_json_worker_real_process_bounds_single_use_and_parent_death_job(tmp_path):
    fixture = Path(__file__).with_name("wechat_sender_worker.py")

    async def scenario():
        from wechat_agent.social.models import OutgoingAction

        action = OutgoingAction("fixture-action", BINDING.scope, "fixture-source", "synthetic")
        for mode in ("normal", "wrong-operation", "duplicate", "oversized", "hang"):
            worker = JsonSendWorker((sys.executable, str(fixture), mode))
            try:
                if mode == "normal":
                    await worker.start("fixture-op", BINDING, action)
                    initial = await worker.inspect()
                    initial.verify(BINDING, action)
                    await worker.input_text()
                    (await worker.inspect()).verify(BINDING, action, initial=initial)
                    await worker.submit_once()
                    with pytest.raises(SocialError):
                        await worker.submit_once()
                else:
                    with pytest.raises((SocialError, TimeoutError, ValueError, asyncio.LimitOverrunError)):
                        async with asyncio.timeout(0.3):
                            await worker.start("fixture-op", BINDING, action)
            finally:
                assert await worker.stop()
                assert worker.process.returncode is not None
        latefile = tmp_path / "late-submit.txt"
        worker = JsonSendWorker((sys.executable, str(fixture), "late-submit", str(latefile)))
        try:
            await worker.start("late-op", BINDING, action)
            await worker.inspect()
            await worker.input_text()
            await worker.inspect()
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(0.05):
                    await worker.submit_once()
        finally:
            assert await worker.stop()
        await asyncio.sleep(0.35)
        assert not latefile.exists()
        if os.name == "nt":
            # Kill-on-close must also work when the Ledger-owning parent exits abruptly.
            pidfile = tmp_path / "child.pid"
            parent = await asyncio.create_subprocess_exec(*python_worker_command(fixture, "parent-exit", str(pidfile)))
            assert await parent.wait() == 0
            child_pid = int(pidfile.read_text())
            import ctypes as C

            kernel = C.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes, kernel.OpenProcess.restype = [C.c_uint32, C.c_int, C.c_uint32], C.c_void_p
            kernel.WaitForSingleObject.argtypes, kernel.WaitForSingleObject.restype = [C.c_void_p, C.c_uint32], C.c_uint32
            kernel.CloseHandle.argtypes = [C.c_void_p]
            handle = kernel.OpenProcess(0x100000, False, child_pid)
            if handle:
                try:
                    assert kernel.WaitForSingleObject(handle, 1500) == 0
                finally:
                    kernel.CloseHandle(handle)
    asyncio.run(scenario())


def test_worker_rechecks_native_state_after_parent_inspection_and_rejects_replayed_permits():
    from wechat_agent.social.models import OutgoingAction

    action = OutgoingAction("fixture-action", BINDING.scope, "fixture-source", "synthetic reply")

    class Backend:
        text = ""
        focused = True
        username = "filehelper"
        inputs = submits = 0

        def inspect(self):
            return InputObservation(BINDING.account.account_id, self.username, WINDOW, "native_username",
                "fixture-input", self.focused, not self.text, hashlib.sha256(self.text.encode()).hexdigest())

        def input_text(self, text, token):
            self.inputs += 1
            self.text = text

        def submit_once(self, token):
            self.submits += 1

    for failure in ("input-target", "submit-focus", "replayed-input", "normal"):
        backend = Backend()
        session = WorkerSession(backend)

        def command(name, session=session, **payload):
            return session.handle({"version": 1, "operation_id": "fixture-op", "sequence": session.sequence + 1,
                                   "command": name, **payload})

        command("init", binding=asdict(BINDING), action=asdict(action))
        command("inspect")
        if failure == "input-target":
            backend.username = "contact"
            with pytest.raises(SocialError):
                command("input_once")
            assert backend.inputs == backend.submits == 0
            backend.username = "filehelper"
            with pytest.raises(SocialError):
                command("input_once")  # Failed workers remain terminal even after the UI changes back.
            continue
        command("input_once")
        if failure == "replayed-input":
            with pytest.raises(SocialError):
                command("input_once")
            assert backend.inputs == 1 and backend.submits == 0
            continue
        command("inspect")
        if failure == "submit-focus":
            backend.focused = False
            with pytest.raises(SocialError):
                command("submit_once")
            assert backend.inputs == 1 and backend.submits == 0
        else:
            command("submit_once")
            with pytest.raises(SocialError):
                command("submit_once")
            assert backend.inputs == backend.submits == 1


def test_operator_fixed_marker_uses_no_fake_incoming_event_or_model_and_is_idempotent(tmp_path):
    async def scenario():
        with Ledger(tmp_path / "operator.db", now=lambda: NOW) as ledger:
            store = bind(ledger, mode="draft")
            state = SendState(ledger)
            with pytest.raises(SocialError, match="operator_send_not_enabled"):
                state.queue_marker(BINDING, "operator-request", "W2B-synthetic-once")
            ledger.configure_owner(BINDING.scope, BINDING.account.self_id)
            action_id = state.queue_marker(BINDING, "operator-request", "W2B-synthetic-once")
            assert state.queue_marker(BINDING, "operator-request", "W2B-synthetic-once") == action_id
            with pytest.raises(SocialError, match="operator_request_conflict"):
                state.queue_marker(BINDING, "operator-request", "W2B-other")
            assert ledger.counts() == {"inbox": 1, "decisions": 0, "outbox": 1, "attempts": 0}
            row = ledger._db.execute("SELECT origin,source_verified,sender_id FROM inbox").fetchone()
            assert row[:] == ("operator", 0, "local-operator")
            assert ledger._db.execute("SELECT COUNT(*) FROM bridge_records").fetchone()[0] == 0
            worker, memory = Worker(), Memory()
            adapter = WeChatSendAdapter(ledger, BINDING, worker_factory=lambda: worker,
                lease_factory=lambda window, log: AccessibilityLease(memory, window, log), baseline_provider=baselines)
            async with SocialService(ledger, NoModel(), [adapter]) as service:
                assert (await service.dispatch(action_id))["state"] == "UNCONFIRMED"
                await service.dispatch(action_id)
            assert worker.calls == ["start", "input", "submit", "stop"]
            assert ledger.counts()["decisions"] == 0 and ledger.counts()["attempts"] == 1
            assert state.queue_marker(BINDING, "operator-request", "W2B-synthetic-once") == action_id
            with pytest.raises(SocialError, match="ui_channel_requires_review"):
                state.queue_marker(BINDING, "new-request", "W2B-new")
            # Even after a trusted human reconciles it, the operator request is not conversation history.
            ledger._db.execute("UPDATE outbox SET state='CONFIRMED' WHERE action_id=?", (action_id,))
            assert ledger.history(BINDING.scope) == []
            store.bind(BINDING, mode="observe")
            with pytest.raises(SocialError, match="operator_send_not_enabled"):
                state.queue_marker(BINDING, "observer-request", "W2B-observer")
    asyncio.run(scenario())


@pytest.mark.parametrize("reaped", [True, False])
def test_foreground_writer_uses_shared_lock_until_its_worker_is_reaped(tmp_path, reaped):
    from types import SimpleNamespace
    async def run():
        with Ledger(tmp_path / "ledger.db", now=lambda: NOW) as ledger:
            worker = Worker(reaped=reaped)
            aid, adapter, _ = setup(ledger, worker)
            observations = []
            adapter.writer_lock_factory = lambda _: SimpleNamespace(close=lambda: observations.append(list(worker.calls)))
            async with SocialService(ledger, NoModel(), [adapter]) as service:
                await service.dispatch(aid)
            assert observations == [worker.calls] if reaped else observations == []
            assert (adapter._held_locks is not None) == (not reaped)
    asyncio.run(run())
