"""Explicit source-checkout .13 composition. Imports never touch a native process."""

import asyncio
import json
import secrets
import sys
from pathlib import Path

from ..bridges.wechat.background_completion import require_background_admission
from ..bridges.wechat.background_contract import COVERED_MARKER
from ..bridges.wechat.background_process import BackgroundWorker
from ..bridges.wechat.background_readmission import revalidate_background
from ..bridges.wechat.contracts import Cursor, WindowBinding
from ..bridges.wechat.receive_process import JsonReaderWorker, ReaderSession
from ..bridges.wechat.send_process import python_worker_command
from ..bridges.wechat.send_state import SendState
from ..bridges.wechat.sender import AccessibilityLease
from ..social.models import OutgoingAction, SocialError
from ..social.service import SocialService
from .sender import WorkbenchBackgroundAdapter


class NoModel:
    async def decide(self, *_args, **_kw):
        raise SocialError("workbench_manual_generation_required")

    async def aclose(self):
        pass


class NativeWorkbenchSender:
    def __init__(self, ledger, receiver, fingerprint, modules, *, reviewed_unknowns=(), uncertainty_reference=None):
        self.ledger, self.receiver, self.fingerprint = ledger, receiver, fingerprint
        self.binding = receiver.binding
        self.verifier, self.preparation, self.profile, self.prior_processes_exited = modules
        self.reviewed_unknowns, self.uncertainty_reference = tuple(reviewed_unknowns), uncertainty_reference
        self.checker = None
        self.service = None
        self.closed = False
        self.starts = {}
        self._held_preparation_locks = None

    async def prepare(self, draft_id):
        if self.closed:
            raise SocialError("closed")
        from ..bridges.wechat.background_lock import writer_locks

        if (await self.receiver.poll())["more"]:
            raise SocialError("background_preparation_receive_bound")
        request_id = "native-preparation:" + secrets.token_hex(16)
        with self.ledger._transaction() as db:
            prior_block = db.execute("SELECT blocked_reason FROM wechat_send_control").fetchone()[0]
            if prior_block not in {"submission_or_draft_requires_review", "reviewed_trial_requires_review"}:
                raise SocialError("workbench_preparation_cleanup_required")
            db.execute("INSERT INTO workbench_requests VALUES(?,?,?,?,'RUNNING','[]',?)",
                       (request_id, "native_preparation", request_id, draft_id, self.ledger.now()))
            db.execute("UPDATE wechat_send_control SET blocked_reason='workbench_preparation_active'")

        def journal(stage, original):
            with self.ledger._transaction() as db:
                events = json.loads(db.execute("SELECT payload FROM workbench_requests WHERE request_id=?", (request_id,)).fetchone()[0])
                events.append(dict(stage=stage, original=original, at=self.ledger.now()))
                db.execute("UPDATE workbench_requests SET payload=? WHERE request_id=?", (json.dumps(events), request_id))

        locks = lease = worker = None
        try:
            locks = writer_locks(self.binding)
            w = self.binding.account.window
            lease = AccessibilityLease(self.verifier.NativeMemory(w.pid, activate=True,
                hwnd=w.hwnd, created_filetime=w.created_filetime), w, journal)
            lease.open()
            worker = BackgroundWorker(python_worker_command(Path(self.profile.__file__).with_name("wechat_42013_worker.py"),
                "--pid", str(w.pid), "--hwnd", str(w.hwnd), "--created-filetime", str(w.created_filetime),
                "--account-fingerprint", self.fingerprint, "--input-method", "utf16_bmp_covered"))
            await worker.start("workbench-readonly-preparation", self.binding,
                OutgoingAction("workbench-readonly-preparation", self.binding.scope, "local-readonly", COVERED_MARKER))
            journal("PREPARATION_WORKER_PID", worker.process.pid)
            facts = (await worker._rpc("prepare"))["readiness"]
            try:
                require_background_admission(self.ledger, self.binding)
                checker = require_background_admission
            except SocialError:
                context = revalidate_background(self.ledger, self.binding, facts,
                    retired_generations=lambda store, target: self.prior_processes_exited(store, target, except_current_generation=True),
                    compatible_builds=self.profile.compatible_builds)
                checker = context.check
            self.checker = checker
            return facts, checker
        finally:
            async def cleanup():
                reaped = restored = True
                if worker is not None:
                    try:
                        async with asyncio.timeout(2):
                            reaped = await worker.stop() is True
                    except BaseException:
                        reaped = False
                try:
                    restored = lease.close(restore=reaped) is True if lease is not None else True
                except BaseException:
                    restored = False
                released = locks is None
                if locks is not None and reaped:
                    try:
                        locks.close()
                        released = True
                    except BaseException:
                        pass
                elif locks is not None:
                    self._held_preparation_locks = locks
                clean = reaped and restored and released
                if not clean:
                    self.closed = True
                with self.ledger._transaction() as db:
                    db.execute("UPDATE workbench_requests SET state=? WHERE request_id=?", ("DONE" if clean else "FAILED", request_id))
                    db.execute("UPDATE wechat_send_control SET blocked_reason=?",
                               (prior_block if clean else "workbench_preparation_cleanup_failed",))
                if not clean:
                    raise SocialError("background_preflight_restore_failed")

            cleanup_task = asyncio.create_task(cleanup())
            cancelled = False
            while not cleanup_task.done():
                try:
                    await asyncio.shield(cleanup_task)
                except asyncio.CancelledError:
                    cancelled = True
            cleanup_task.result()
            if cancelled:
                raise asyncio.CancelledError

    async def baseline(self, binding):
        if (await self.receiver.poll())["more"]:
            raise SocialError("background_preparation_receive_bound")
        async with self.receiver.rpc_lock:
            manifest = await self.receiver.worker.rpc("echo_baseline")
            registered = self.receiver.catalog.checkpoints()
            if set(manifest["shards"]) != set(registered):
                raise SocialError("background_baseline_shard_changed")
            for name, item in manifest["shards"].items():
                if registered[name]["shard"] != name + ":" + item["identity"]:
                    raise SocialError("background_baseline_shard_changed")
            self.starts = {name: dict(shard=registered[name]["shard"], start=item["tail"], local_fence=item["local_fence"])
                           for name, item in manifest["shards"].items()}
            return {v["shard"]: Cursor(**v["start"]) for v in self.starts.values()}

    async def dispatch(self, action_id):
        from ..bridges.wechat.background_lock import writer_locks

        w = self.binding.account.window
        script = Path(self.profile.__file__).with_name("wechat_workbench_worker.py")
        adapter = WorkbenchBackgroundAdapter(self.ledger, self.binding,
            worker_factory=lambda **kw: BackgroundWorker(python_worker_command(script,
                "--pid", str(w.pid), "--hwnd", str(w.hwnd), "--created-filetime", str(w.created_filetime),
                "--account-fingerprint", self.fingerprint, "--allow-control", "--input-method", "workbench_background_bmp"), **kw),
            lease_factory=lambda window, log: AccessibilityLease(self.verifier.NativeMemory(window.pid, activate=True,
                hwnd=window.hwnd, created_filetime=window.created_filetime), window, log),
            baseline_provider=self.baseline, writer_lock_factory=writer_locks, admission_checker=self.checker)
        self.service = SocialService(self.ledger, NoModel(), [adapter], dispatch_timeout=25)
        try:
            await self.service.dispatch(action_id)
            if not adapter.last_run or not adapter.last_run.get("clean_control_recorded"):
                return
            records = []
            prior = self.receiver.on_committed

            def committed(session, checkpoint, batch, accepted):
                records.extend(batch)
                if len(records) > 800:
                    raise SocialError("background_echo_read_bound")
                if prior:
                    prior(session, checkpoint, batch, accepted)

            self.receiver.on_committed = committed
            try:
                async with asyncio.timeout(15):
                    for _ in range(3):
                        result = await self.receiver.poll()
                        if result["more"]:
                            raise SocialError("background_echo_read_bound")
                        try:
                            SendState(self.ledger).local_echo(self.binding, action_id, tuple(records))
                            return
                        except SocialError as exc:
                            if exc.code != "local_echo_not_unique":
                                raise
                        await asyncio.sleep(1)
            finally:
                self.receiver.on_committed = prior
        finally:
            await self.service.aclose()

    async def aclose(self):
        self.closed = True
        if self.service:
            await self.service.aclose()


def native_source_root():
    for source_root in Path(__file__).resolve().parents:
        if ((source_root / "pyproject.toml").is_file()
                and (source_root / "scripts/wechat_workbench_worker.py").is_file()):
            return source_root
    raise SocialError("native_source_checkout_required")


async def open_native_receiver(ledger, *, evidence_path, pid, hwnd, created_filetime, send=False,
                               reviewed_unknowns=(), uncertainty_reference=None):
    """Explicit profile selection before importing legacy native dependencies."""
    source_root = native_source_root()
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))
    from scripts import wechat_42013_profile as profile

    profile.activate()
    from scripts import probe_wechat_standalone as preparation
    from scripts import verify_wechat_accessibility as verifier
    from scripts.probe_wechat_directed_trial import prior_processes_exited

    preparation.WORKER_SCRIPT = Path(profile.__file__).with_name("wechat_42013_worker.py")
    evidence = json.loads(await asyncio.to_thread(Path(evidence_path).read_text, encoding="utf-8"))
    if (evidence.get("status") != "header_key_verified" or evidence.get("header_hmac_verified") is not True
            or evidence.get("active_account_matched") is not True):
        raise SocialError("background_authenticated_account_required")
    fingerprint = evidence.get("account_fingerprint")
    window = WindowBinding(pid, created_filetime, hwnd, profile.EXE_SHA, profile.DLL_SHA)
    worker = JsonReaderWorker(python_worker_command(Path(profile.__file__).with_name("wechat_42013_reader_worker.py")))
    receiver = ReaderSession(ledger, worker, window, fingerprint, mode="draft" if send else "observe")
    try:
        await receiver.start()
        if send:
            ledger.configure_owner(receiver.binding.scope, receiver.binding.account.self_id)
        sender = NativeWorkbenchSender(ledger, receiver, fingerprint,
            (verifier, preparation, profile, prior_processes_exited), reviewed_unknowns=reviewed_unknowns,
            uncertainty_reference=uncertainty_reference) if send else None
        return receiver, sender
    except BaseException:
        await receiver.close()
        raise
