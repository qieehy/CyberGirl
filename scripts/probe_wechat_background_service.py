"""Explicit, bounded background SocialService acceptance using the ORIGINAL ledger.

Default is read-only preparation. The two fixed markers need independent operator
review. No model, contact navigation, retries, foreground or hardware fallback.
"""
import argparse
import asyncio
import hashlib
import json
import os
import time
from pathlib import Path

from wechat_agent.bridges.wechat import channel_review
from wechat_agent.bridges.wechat.background_completion import require_background_admission
from wechat_agent.bridges.wechat.background_contract import (
    INTEGRATED_MARKER,
    INTEGRATED_REQUEST,
    RESTART_MARKER,
    RESTART_REQUEST,
)
from wechat_agent.bridges.wechat.background_lock import writer_locks
from wechat_agent.bridges.wechat.background_observer import BackgroundObserver
from wechat_agent.bridges.wechat.background_process import BackgroundWorker
from wechat_agent.bridges.wechat.background_readmission import revalidate_background
from wechat_agent.bridges.wechat.background_sender import WeChatBackgroundAdapter
from wechat_agent.bridges.wechat.contracts import Cursor, WindowBinding
from wechat_agent.bridges.wechat.receive_process import JsonReaderWorker, ReaderSession
from wechat_agent.bridges.wechat.send_process import python_worker_command
from wechat_agent.bridges.wechat.send_state import SendState
from wechat_agent.bridges.wechat.sender import AccessibilityLease
from wechat_agent.bridges.wechat.store import BridgeStore
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import SocialError
from wechat_agent.social.service import SocialService

if __package__:
    from .probe_wechat_background_trial import collect_echo
    from .probe_wechat_directed_trial import prior_processes_exited
    from .probe_wechat_native_sender import LEDGER
    from .probe_wechat_standalone import worker_readiness
    from .verify_wechat_accessibility import DLL, DLL_SHA, EXE_SHA, NativeMemory
else:
    from probe_wechat_background_trial import collect_echo
    from probe_wechat_directed_trial import prior_processes_exited
    from probe_wechat_native_sender import LEDGER
    from probe_wechat_standalone import worker_readiness
    from verify_wechat_accessibility import DLL, DLL_SHA, EXE_SHA, NativeMemory


STANDALONE_WORKER_SCRIPT = Path(__file__).with_name("wechat_standalone_worker.py")
READER_WORKER_SCRIPT = Path(__file__).with_name("wechat_reader_worker.py")
COMPATIBLE_BUILDS = None

class NoModel:
    async def decide(self, *args, **kwargs):
        raise SocialError("background_acceptance_model_denied")

    async def aclose(self):
        pass


def counts(ledger):
    return dict(operations=ledger._db.execute("SELECT COUNT(*) FROM wechat_send_operations").fetchone()[0],
        inputs=ledger._db.execute("SELECT COALESCE(SUM(input_permitted),0) FROM wechat_send_operations").fetchone()[0],
        submits=ledger._db.execute("SELECT COALESCE(SUM(submit_permitted),0) FROM wechat_send_operations").fetchone()[0])


def native_worker(window, fingerprint, *, record):
    return BackgroundWorker(python_worker_command(STANDALONE_WORKER_SCRIPT,
        "--pid", str(window.pid), "--hwnd", str(window.hwnd), "--created-filetime", str(window.created_filetime),
        "--account-fingerprint", fingerprint, "--allow-control", "--input-method", "utf16_bmp_integrated"), record=record)


def new_receiver(ledger, window, fingerprint):
    return ReaderSession(ledger, JsonReaderWorker(python_worker_command(READER_WORKER_SCRIPT)),
                         window, fingerprint)


async def readonly_ready(ledger, binding, fingerprint, record):
    locks = writer_locks(binding)
    lease = None
    try:
        lease = AccessibilityLease(NativeMemory(binding.account.window.pid, activate=True,
            hwnd=binding.account.window.hwnd, created_filetime=binding.account.window.created_filetime),
            binding.account.window, record)
        lease.open()
        facts = await worker_readiness(binding.account.window, binding, fingerprint, covered_mode=True)
        return facts
    finally:
        restored = lease.close() if lease is not None else True
        locks.close()
        if not restored:
            raise SocialError("background_preflight_restore_failed")


async def run(args, output, event):
    prior = json.loads(args.match_account_evidence.read_text(encoding="utf-8"))
    if (prior.get("status") != "header_key_verified" or prior.get("header_hmac_verified") is not True
            or prior.get("active_account_matched") is not True):
        raise SocialError("background_authenticated_account_required")
    fingerprint = prior["account_fingerprint"]
    window = WindowBinding(args.pid, args.created_filetime, args.hwnd, EXE_SHA, DLL_SHA)
    marker, request, kind = ((RESTART_MARKER, RESTART_REQUEST, "restarted_background_bmp") if args.restart else
                            (INTEGRATED_MARKER, INTEGRATED_REQUEST, "integrated_background_bmp"))
    output.update(mode=kind, marker_sha256=hashlib.sha256(marker.encode()).hexdigest(), service_pid=os.getpid(), model_requests=0)
    receiver = service = observer = None
    with Ledger(LEDGER) as ledger:
        try:
            receiver = new_receiver(ledger, window, fingerprint)
            initial = await receiver.start()
            binding = receiver.binding
            output.update(start_checkpoints=initial, reader_start_pid=receiver.worker.process.pid,
                          starting_counts=counts(ledger), binding_generation=binding.generation)
            facts = await readonly_ready(ledger, binding, fingerprint,
                lambda stage, original: event("readiness_gate", stage=stage, original=original))
            readmission = None
            try:
                require_background_admission(ledger, binding)
            except SocialError:
                readmission = revalidate_background(ledger, binding, facts,
                    retired_generations=lambda store, target: prior_processes_exited(store, target, except_current_generation=True),
                    compatible_builds=COMPATIBLE_BUILDS)
                output["background_revalidation"] = readmission.descriptor
            checker = readmission.check if readmission is not None else require_background_admission
            facts.update(observed_at=time.time(), binding_generation=binding.generation,
                         prior_input_context_reconciled=True, background_admission_verified=True)
            output["readiness"] = facts
            if not args.send_reviewed_once:
                output.update(status="background_service_prepared", no_input_or_submit=True)
                return
            if args.restart:
                old = json.loads(args.previous_service_evidence.read_text(encoding="utf-8"))
                if (old.get("status") != "background_service_echo_observed" or old.get("service_pid") == os.getpid()
                        or old.get("binding_generation") != binding.generation
                        or old.get("end_checkpoints") != initial):
                    raise SocialError("background_service_restart_predecessor_invalid")
                output["prior_service_pid"] = old["service_pid"]
            starts = {}
            async def baseline(_binding):
                manifest = await receiver.worker.rpc("manifest")
                registered = receiver.catalog.checkpoints()
                starts.clear()
                for name, item in manifest["shards"].items():
                    if name not in registered:
                        raise SocialError("background_baseline_shard_changed")
                    starts[name] = dict(shard=registered[name]["shard"], start=item["tail"], local_fence=item["local_fence"])
                return {v["shard"]: Cursor(**v["start"]) for v in starts.values()}
            def adapter_factory():
                return WeChatBackgroundAdapter(ledger, binding,
                    worker_factory=lambda **kw: native_worker(window, fingerprint, **kw),
                    lease_factory=lambda w, log: AccessibilityLease(NativeMemory(w.pid, activate=True,
                        hwnd=w.hwnd, created_filetime=w.created_filetime), w, log),
                    baseline_provider=baseline, writer_lock_factory=writer_locks, admission_checker=checker)
            adapter = adapter_factory()
            service = SocialService(ledger, NoModel(), [adapter], dispatch_timeout=25)
            # Explicitly attempt dispatch only of already-confirmed predecessor
            # actions. A restarted service must return their summaries untouched.
            before = counts(ledger)
            replay_ids = [r[0] for r in ledger._db.execute("""SELECT s.action_id FROM wechat_send_operations s
                JOIN outbox o USING(action_id) WHERE s.generation=? AND o.state='CONFIRMED'""", (binding.generation,))]
            for aid in replay_ids:
                await service.dispatch(aid)
            if counts(ledger) != before or adapter.last_run is not None:
                raise SocialError("background_service_replay_detected")
            output["confirmed_replay_checks"] = len(replay_ids)
            BridgeStore(ledger).bind(binding, mode="draft")
            ledger.configure_owner(binding.scope, binding.account.self_id)
            review = channel_review.authorize(ledger, binding, request, marker, facts,
                approval_ref=args.approval_reference, reviewed_unknowns=tuple(args.review_unknown_action),
                uncertainty_approval_ref=args.uncertainty_review_reference, backend_kind=kind, background_readmission=readmission)
            aid = SendState(ledger).queue_marker(binding, request, marker, review_id=review, background_readmission=readmission)
            output.update(action_id=aid, review_id=review)
            output["dispatch_state"] = (await service.dispatch(aid))["state"]
            output.update(adapter.last_run or {})
            if not output.get("clean_control_recorded"):
                raise SocialError("background_service_control_unconfirmed")
            async with asyncio.timeout(15):
                output["unique_local_echo"] = await collect_echo(receiver.worker, starts, SendState(ledger), binding, aid)
            if output["unique_local_echo"] is not True:
                raise SocialError("background_service_echo_unconfirmed")
            output["phone_confirmation_required"] = True
            event("background_send_echo_observed", action_id=aid, marker_sha256=output["marker_sha256"])
            output["receive_initial"] = await receiver.poll()
            output["post_send_checkpoints"] = receiver.catalog.checkpoints()
            output["post_send_counts"] = counts(ledger)
            if args.restart:
                observer = BackgroundObserver(window.pid)
                observer.start()
                started, restarted, polls, fresh, replay = time.monotonic(), False, 0, 0, 0
                event("continuous_receive_started", duration_seconds=1800)
                while time.monotonic() - started < 1800:
                    observer.check()
                    async with asyncio.timeout(20):
                        result = await receiver.poll()
                    if result["more"]:
                        raise SocialError("background_continuous_read_bound")
                    polls += 1
                    fresh += result["fresh"]
                    replay += result["replay"]
                    elapsed = time.monotonic() - started
                    if elapsed >= 900 and not restarted:
                        saved = receiver.catalog.checkpoints()
                        previous_pid = receiver.worker.process.pid
                        await service.aclose()
                        await receiver.close()
                        receiver = new_receiver(ledger, window, fingerprint)
                        resumed = await receiver.start()
                        if resumed != saved or receiver.worker.process.pid == previous_pid:
                            raise SocialError("background_reader_resume_mismatch")
                        service = SocialService(ledger, NoModel(), [adapter_factory()], dispatch_timeout=25)
                        replay_before = counts(ledger)
                        await service.dispatch(aid)  # UNKNOWN/CONFIRMED both must stay non-replayable.
                        if counts(ledger) != replay_before:
                            raise SocialError("background_restart_replayed_send")
                        output["midrun_restart"] = dict(at_seconds=elapsed, previous_reader_pid=previous_pid,
                            reader_pid=receiver.worker.process.pid, checkpoints_unchanged=True, no_send_replay=True)
                        event("reader_and_service_restarted", **output["midrun_restart"])
                        restarted = True
                    if polls % 15 == 0:
                        event("receive_progress", elapsed_seconds=elapsed, polls=polls, fresh=fresh, replay=replay)
                    if counts(ledger) != output["post_send_counts"]:
                        raise SocialError("background_unexpected_send_activity")
                    await asyncio.sleep(min(2, max(0, 1800 - elapsed)))
                if not restarted or replay:
                    raise SocialError("background_continuous_acceptance_incomplete")
                output.update(continuous_seconds=time.monotonic() - started, polls=polls,
                              received_fresh=fresh, received_replays=replay)
                output["status"] = "background_service_30m_completed"
            else:
                output["status"] = "background_service_echo_observed"
            output["end_checkpoints"] = receiver.catalog.checkpoints()
        finally:
            try:
                if service:
                    await service.aclose()
            finally:
                try:
                    if receiver:
                        output["reader_reaped"] = await receiver.close()
                finally:
                    if observer:
                        output["continuous_observer_stopped"] = observer.stop()
                        report = output["continuous_observation"] = observer.report()
                        if (output["continuous_observer_stopped"] is not True or report["foreground_target_seen"]
                                or report["failure"] is not None or report["maximum_sample_gap_ms"] > 250):
                            output.update(status="stopped", reason="background_continuous_observation_incomplete")
                    output["ending_counts"] = counts(ledger)
                    output["active_operations"] = ledger._db.execute("SELECT COUNT(*) FROM wechat_send_operations WHERE finished=0").fetchone()[0]
                    output["integrity_ok"] = ledger._db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("pid", "hwnd", "created-filetime"):
        parser.add_argument("--" + key, type=int, required=True)
    parser.add_argument("--match-account-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--send-reviewed-once", action="store_true")
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--previous-service-evidence", type=Path)
    parser.add_argument("--approval-reference")
    parser.add_argument("--review-unknown-action", action="append", default=[])
    parser.add_argument("--uncertainty-review-reference")
    args = parser.parse_args()
    if args.send_reviewed_once and (not args.approval_reference or not args.uncertainty_review_reference or not args.review_unknown_action):
        parser.error("exact single-action review and named uncertainties required")
    if args.restart and not args.previous_service_evidence:
        parser.error("restart requires the previous actual service evidence")
    output = dict(status="started")
    with args.output.open("x", encoding="utf-8") as destination, args.output.with_suffix(".journal.jsonl").open("x", encoding="utf-8") as journal:
        def event(kind, **fields):
            value = dict(event=kind, at=time.time(), **fields)
            journal.write(json.dumps(value, separators=(",", ":")) + "\n")
            journal.flush()
            os.fsync(journal.fileno())
            print(json.dumps(value), flush=True)
        try:
            if not DLL.is_file():
                output["supported_version"] = DLL.parent.name
                raise SocialError("background_supported_version_missing")
            asyncio.run(asyncio.wait_for(run(args, output, event), timeout=1900 if args.restart else 100))
        except BaseException as exc:
            output.update(status="stopped", reason=exc.code if isinstance(exc, SocialError) else "background_service_interrupted")
        json.dump(output, destination, indent=2)
        destination.flush()
        os.fsync(destination.fileno())
    print(json.dumps(output), flush=True)
    return 0 if output["status"] in {"background_service_prepared", "background_service_echo_observed", "background_service_30m_completed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
