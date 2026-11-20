"""Readiness by default; a reviewed one-use trial uses the original sender ledger."""

import argparse
import asyncio
import ctypes as C
import hashlib
import json
import os
import secrets
import time
from dataclasses import asdict
from pathlib import Path

if __package__:
    from .probe_wechat_account import read_identity
    from .probe_wechat_database import configured_roots
    from .probe_wechat_native_sender import LEDGER, NoModel
    from .verify_wechat_accessibility import DLL, DLL_SHA, EXE_SHA, NativeMemory
else:
    from probe_wechat_account import read_identity
    from probe_wechat_database import configured_roots
    from probe_wechat_native_sender import LEDGER, NoModel
    from verify_wechat_accessibility import DLL, DLL_SHA, EXE_SHA, NativeMemory

from wechat_agent.bridges.wechat import channel_review
from wechat_agent.bridges.wechat.background_lock import writer_locks
from wechat_agent.bridges.wechat.contracts import (
    AccountBinding,
    ConversationBinding,
    Cursor,
    DeliveryEvidence,
    SourceRecord,
    WindowBinding,
    digest,
)
from wechat_agent.bridges.wechat.directed_native import TRIAL_MARKER
from wechat_agent.bridges.wechat.physical_native import PHYSICAL_MARKER
from wechat_agent.bridges.wechat.posted_native import POSTED_TRIAL_MARKER
from wechat_agent.bridges.wechat.receive_process import JsonReaderWorker
from wechat_agent.bridges.wechat.receive_state import ReaderCatalog
from wechat_agent.bridges.wechat.send_process import JsonSendWorker, python_worker_command
from wechat_agent.bridges.wechat.send_state import SendState
from wechat_agent.bridges.wechat.sender import AccessibilityLease, WeChatSendAdapter
from wechat_agent.bridges.wechat.sequence_native import SEQUENCE_MARKER
from wechat_agent.bridges.wechat.store import BridgeStore
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import OutgoingAction, SocialError
from wechat_agent.social.service import SocialService

REQUEST = "w2b-directed-20261104-single"
POSTED_REQUEST = "w2b-posted-20261104-single"
SEQUENCE_REQUEST = "w2b-sequence-20261107-single"
PHYSICAL_REQUEST = "w2b-physical-20261107-single"
READY_KEYS = ("account_and_filehelper_verified", "both_draft_patterns_empty", "same_editor_and_button",
              "foreground_hit_and_focus", "no_capture_or_hardware_input", "button_not_pressed", "class_entry_matches_pinned_code")


def command(window, fingerprint, *, read_only, posted=False, sequence=False, physical=False):
    route = "--physical-fixed-trial" if physical else "--sequence-fixed-trial" if sequence else "--posted-fixed-trial" if posted else "--directed-fixed-trial"
    return python_worker_command(Path(__file__).with_name("wechat_native_worker.py"),
        "--pid", str(window.pid), "--hwnd", str(window.hwnd), "--created-filetime", str(window.created_filetime),
        "--account-fingerprint", fingerprint, route,
        *(("--read-only",) if read_only else ()))


def lease(window, journal):
    return AccessibilityLease(NativeMemory(window.pid, activate=True, hwnd=window.hwnd,
        created_filetime=window.created_filetime), window, journal)


async def await_bound_foreground(worker, memory, window, *, wait_seconds=60, clock=time.monotonic, sleep=asyncio.sleep):
    """Observe the approved window becoming ready; never activate it or infer consent from time."""
    memory.u.GetForegroundWindow.argtypes, memory.u.GetForegroundWindow.restype = [], C.c_void_p
    deadline, reads = clock() + wait_seconds, 0
    for _ in range(301):
        memory.validate()
        if memory.u.GetForegroundWindow() == window.hwnd:
            report = (await worker._rpc("readiness_diagnostics"))["readiness_diagnostics"]
            reads += 1
            if report.get("both_draft_patterns_empty") is not True:
                raise SocialError("posted_foreground_wait_existing_draft")
            if clock() <= deadline and all(report.get(k) is True for k in READY_KEYS):
                return report
            if reads >= 32:
                break
        remaining = deadline - clock()
        if remaining <= 0:
            break
        await sleep(min(0.2, remaining))
    raise SocialError("posted_foreground_ready_timeout")


async def readiness(window, fingerprint, journal, binding=None, *, prepare_focus=False, posted=False, wait_foreground=False, sequence=False, physical=False):
    if sequence or physical:
        posted = True
    if posted and prepare_focus and not (sequence or physical):
        raise SocialError("posted_readiness_focus_denied")
    if wait_foreground and not posted:
        raise SocialError("posted_foreground_wait_mode_denied")
    gate, worker = lease(window, journal), JsonSendWorker(command(window, fingerprint, read_only=not prepare_focus, posted=posted, sequence=sequence, physical=physical))
    result = {}
    try:
        async with asyncio.timeout(85 if wait_foreground else 25):
            gate.open()
            user, _, _ = read_identity(gate.memory, DLL.read_bytes(), configured_roots())
            if hashlib.sha256(user.encode()).hexdigest() != fingerprint:
                raise SocialError("directed_account_evidence_mismatch")
            if binding is None:
                binding = ConversationBinding(AccountBinding(user, "directed-readiness", user, window, "current-account-verified"),
                                               "filehelper", "dm", "native-filehelper-page")
            await worker.start("readonly-directed-readiness", binding,
                OutgoingAction("readonly-directed-trial", binding.scope, "local-operator", PHYSICAL_MARKER if physical else SEQUENCE_MARKER if sequence else POSTED_TRIAL_MARKER if posted else TRIAL_MARKER))
            result = (await worker._rpc("readiness_diagnostics"))["readiness_diagnostics"]
            if posted:
                first_snapshot_ms = result["snapshot_observation_ms"]
                if wait_foreground:
                    print(json.dumps(dict(status="reader_and_ui_ready_waiting_for_foreground", hwnd=window.hwnd)), flush=True)
                    result = await await_bound_foreground(worker, gate.memory, window)
                else:
                    result = (await worker._rpc("readiness_diagnostics"))["readiness_diagnostics"]
                result["first_snapshot_observation_ms"] = first_snapshot_ms
            if prepare_focus and not result["foreground_hit_and_focus"]:
                journal("FOCUS_PREPARE_INTENT", gate.original)
                await worker.prepare_input_focus()
                result = (await worker._rpc("readiness_diagnostics"))["readiness_diagnostics"]
            result["observed_at"] = time.time()
    finally:
        reaped = await worker.stop()
        restored = gate.close(restore=reaped)
        if not reaped or not restored:
            raise SocialError("directed_readiness_cleanup_unverified")
    return result


class TrialWorker(JsonSendWorker):
    async def warmup(self):
        observation = await super().warmup()
        data = (await self._rpc("readiness_diagnostics"))["readiness_diagnostics"]
        required = ("account_and_filehelper_verified", "both_draft_patterns_empty", "same_editor_and_button",
                    "foreground_hit_and_focus", "no_capture_or_hardware_input", "button_not_pressed", "class_entry_matches_pinned_code")
        if not all(data[k] is True for k in required):
            raise SocialError("directed_live_trial_not_ready")
        return observation


def prior_processes_exited(ledger, window, *, except_current_generation=False):
    kernel = C.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes, kernel.OpenProcess.restype = [C.c_uint32, C.c_int, C.c_uint32], C.c_void_p
    kernel.GetProcessTimes.argtypes, kernel.GetProcessTimes.restype = [C.c_void_p] + [C.POINTER(C.c_uint64)] * 4, C.c_int
    kernel.CloseHandle.argtypes = [C.c_void_p]
    for (payload,) in ledger._db.execute("SELECT window_binding FROM wechat_send_operations WHERE input_permitted=1"):
        old = WindowBinding(**json.loads(payload))
        if except_current_generation and (old.pid, old.created_filetime) == (window.pid, window.created_filetime):
            continue
        if old == window:
            return False
        C.set_last_error(0)
        handle = kernel.OpenProcess(0x1000, False, old.pid)
        if not handle:
            if C.get_last_error() != 87:  # Invalid PID is distinct from access denied / unknown.
                return False
            continue
        try:
            times = [C.c_uint64() for _ in range(4)]
            if not kernel.GetProcessTimes(handle, *(C.byref(t) for t in times)) or times[0].value == old.created_filetime:
                return False
        finally:
            kernel.CloseHandle(handle)
    return True


async def send_trial(window, fingerprint, journal, approval_ref, output, *, prepare_focus=False, wait_for_operator=False,
                     posted=False, reviewed_unknowns=(), uncertainty_approval_ref=None, wait_foreground=False, sequence=False, physical=False):
    if sequence or physical:
        posted = True
    if (posted and prepare_focus and not (sequence or physical) or not posted and (reviewed_unknowns or uncertainty_approval_ref or wait_foreground)
            or wait_for_operator and wait_foreground):
        raise SocialError("directed_trial_mode_scope_invalid")
    if posted:
        reviewed_unknowns = channel_review._reviewed_actions(reviewed_unknowns, uncertainty_approval_ref)
    marker, request = (PHYSICAL_MARKER, PHYSICAL_REQUEST) if physical else (SEQUENCE_MARKER, SEQUENCE_REQUEST) if sequence else (POSTED_TRIAL_MARKER, POSTED_REQUEST) if posted else (TRIAL_MARKER, REQUEST)
    reader = JsonReaderWorker(python_worker_command(Path(__file__).with_name("wechat_reader_worker.py")))
    review_id = None
    binding = None
    outer = lease(window, journal)
    restore_outer = True
    try:
        outer.open()
        with Ledger(LEDGER) as ledger:
            try:
                manifest = await reader.start(window, fingerprint)
                catalog = ReaderCatalog(ledger)
                binding = catalog.attach(manifest, window, fingerprint, initialize=True)
                starts = catalog.checkpoints()
                BridgeStore(ledger).bind(binding, mode="draft")
                ledger.configure_owner(binding.scope, binding.account.self_id)
                if wait_for_operator:
                    nonce = secrets.token_hex(16)
                    signal = LEDGER.parent / ("operator-ready-" + nonce + ".json")
                    print(json.dumps(dict(status="reader_ready_waiting_for_operator", nonce=nonce, request=request,
                                          signal=str(signal.resolve()))), flush=True)
                    deadline = time.monotonic() + 60
                    while not signal.exists():
                        if time.monotonic() >= deadline:
                            raise SocialError("directed_operator_ready_timeout")
                        await asyncio.sleep(0.2)
                    if signal.stat().st_size > 256 or json.loads(signal.read_text(encoding="utf-8")) != {"request": request, "nonce": nonce}:
                        raise SocialError("directed_operator_ready_signal_invalid")
                report = await readiness(window, fingerprint, journal, binding, prepare_focus=prepare_focus, posted=posted, sequence=sequence, physical=physical,
                                         wait_foreground=wait_foreground)
                keys = ("account_and_filehelper_verified", "both_draft_patterns_empty", "same_editor_and_button",
                        "foreground_hit_and_focus", "no_capture_or_hardware_input", "button_not_pressed", "class_entry_matches_pinned_code", "observed_at")
                facts = {k: report[k] for k in keys}
                facts.update(binding_generation=binding.generation, prior_process_generation_exited=prior_processes_exited(ledger, window))
                review_id = channel_review.authorize(ledger, binding, request, marker, facts, approval_ref=approval_ref,
                    reviewed_unknowns=reviewed_unknowns, uncertainty_approval_ref=uncertainty_approval_ref)
                state = SendState(ledger)
                action_id = state.queue_marker(binding, request, marker, review_id=review_id)
                output.update(action_id=action_id, review_id=review_id, prior_unknowns_reviewed=list(reviewed_unknowns))

                async def baseline(candidate):
                    if candidate != binding:
                        raise SocialError("directed_baseline_binding_changed")
                    return {v["shard"]: Cursor(**v["start"]) for v in starts.values()}

                adapter = WeChatSendAdapter(ledger, binding, writer_lock_factory=writer_locks, worker_factory=lambda: TrialWorker(command(window, fingerprint, read_only=False, posted=posted, sequence=sequence, physical=physical)),
                    lease_factory=lease, baseline_provider=baseline, preparation_timeout=15)
                async with SocialService(ledger, NoModel(), [adapter]) as service:
                    result = await service.dispatch(action_id)
                output["delivery_state"] = result["state"]
                output["echo_status"] = "not_checked"
                operation = ledger._db.execute("SELECT * FROM wechat_send_operations WHERE action_id=?", (action_id,)).fetchone()
                if operation:
                    output.update(operation_id=operation["operation_id"], input_permitted=operation["input_permitted"],
                        submit_permitted=operation["submit_permitted"], worker_reaped=bool(operation["worker_reaped"]),
                        restore_verified=bool(operation["restore_verified"]), reason=operation["reason"],
                        gesture=state.gesture_summary(operation["operation_id"]))
                    restore_outer = bool(operation["worker_reaped"]) and bool(operation["restore_verified"])
                    if operation["submit_permitted"] and operation["worker_reaped"] and operation["restore_verified"]:
                        output["echo_status"] = "not_unique_or_absent"
                        for _ in range(3):
                            records = []
                            for name, checkpoint in starts.items():
                                current = dict(checkpoint)
                                for _ in range(25):
                                    data = await reader.rpc("page", epoch=binding.account.db_epoch, name=name, checkpoint=current)
                                    batch = []
                                    for item in data["records"]:
                                        item["mentioned_ids"] = tuple(item.get("mentioned_ids", ()))
                                        batch.append(SourceRecord(**item))
                                    records.extend(batch)
                                    if len(records) > 800:
                                        raise SocialError("directed_echo_read_bound")
                                    if batch:
                                        await reader.rpc("ack", name=name, cursor=asdict(batch[-1].position))
                                        current = {**current, "start": asdict(batch[-1].position),
                                                   "local_fence": max(current["local_fence"], *(r.local_id for r in batch))}
                                    if len(batch) < 32:
                                        break
                                else:
                                    raise SocialError("directed_echo_read_incomplete")
                            try:
                                output["local_echo"] = state.local_echo(binding, action_id, tuple(records))
                                output["echo_status"] = "matched"
                                break
                            except SocialError as exc:
                                if exc.code != "local_echo_not_unique":
                                    raise
                            await asyncio.sleep(1)
                output["delivery_state"] = ledger.action_summary(action_id)["state"]
                output["status"] = ("directed_trial_echo_observed" if output["echo_status"] == "matched"
                                    else "directed_trial_unconfirmed")
                output["phone_confirmation_required"] = True
            except SocialError as exc:
                if exc.code == "directed_readiness_cleanup_unverified":
                    restore_outer = False
                if exc.code == "source_changed_during_snapshot" and output.get("echo_status") == "not_unique_or_absent" and output.get("operation_id"):
                    # Readback failure must not overwrite the actual gesture's
                    # persisted reason or trigger a second submission.
                    output.update(echo_error_reason=exc.code, status="directed_trial_unconfirmed", phone_confirmation_required=True)
                else:
                    raise
            finally:
                try:
                    if review_id is not None:
                        channel_review.close_trial(ledger, review_id)
                finally:
                    try:
                        if binding is not None:
                            BridgeStore(ledger).unbind(binding)
                    finally:
                        output["reader_stopped"] = await reader.stop()
                if output.get("action_id"):
                    final = ledger._db.execute("SELECT finished,worker_reaped,restore_verified FROM wechat_send_operations WHERE action_id=?",
                                               (output["action_id"],)).fetchone()
                    if final is not None and not all(final):
                        restore_outer = False
    finally:
        output["outer_restore_verified"] = outer.close(restore=restore_outer)
        if not output["outer_restore_verified"]:
            raise SocialError("directed_outer_restore_unverified")


async def bounded_send_trial(*args, **kwargs):
    async with asyncio.timeout(120):
        return await send_trial(*args, **kwargs)


def confirm_phone(evidence_path, output):
    """Only after the operator has explicitly checked the full marker on phone."""
    prior = json.loads(evidence_path.read_text(encoding="utf-8"))
    markers = {"send_reviewed_once": TRIAL_MARKER, "send_posted_reviewed_once": POSTED_TRIAL_MARKER,
               "send_sequence_reviewed_once": SEQUENCE_MARKER, "send_physical_reviewed_once": PHYSICAL_MARKER}
    marker = markers.get(prior.get("mode"))
    if (marker is None or prior.get("submit_permitted") != 1
            or not prior.get("worker_reaped") or not prior.get("restore_verified") or not prior.get("reader_stopped")
            or not prior.get("outer_restore_verified")):
        raise SocialError("directed_phone_evidence_incomplete")
    with Ledger(LEDGER) as ledger:
        row = ledger._db.execute("""SELECT s.*,o.text,b.payload FROM wechat_send_operations s
            JOIN outbox o USING(action_id) JOIN bridge_bindings b ON b.scope=s.scope
            WHERE s.action_id=? AND s.operation_id=?""", (prior["action_id"], prior["operation_id"])).fetchone()
        if not row or row["text"] != marker or row["content_sha256"] != prior["marker_sha256"]:
            raise SocialError("directed_phone_action_mismatch")
        if not row["finished"] or not row["worker_reaped"] or not row["restore_verified"]:
            raise SocialError("directed_phone_cleanup_unverified")
        data = json.loads(row["payload"])
        data["account"]["window"] = WindowBinding(**data["account"]["window"])
        data["account"] = AccountBinding(**data["account"])
        binding = ConversationBinding(**data)
        evidence = DeliveryEvidence(digest([row["action_id"], "phone-confirmation"]), row["action_id"], binding.scope,
            row["generation"], "manual_confirmation", "user-phone-physical-20261107" if marker == PHYSICAL_MARKER else "user-phone-sequence-20261107" if marker == SEQUENCE_MARKER else "user-phone-posted-20261104" if marker == POSTED_TRIAL_MARKER
            else "user-phone-directed-20261104", row["content_sha256"])
        output["evidence_added"] = BridgeStore(ledger).record_evidence(evidence)
        output.update(status="phone_confirmation_recorded", delivery_state=ledger.action_summary(row["action_id"])["state"],
                      action_id=row["action_id"], marker_sha256=row["content_sha256"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--hwnd", type=int, required=True)
    parser.add_argument("--created-filetime", type=int, required=True)
    parser.add_argument("--match-account-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--send-reviewed-once", action="store_true")
    parser.add_argument("--send-posted-reviewed-once", action="store_true")
    parser.add_argument("--send-sequence-reviewed-once", action="store_true")
    parser.add_argument("--sequence-readiness", action="store_true")
    parser.add_argument("--send-physical-reviewed-once", action="store_true")
    parser.add_argument("--physical-readiness", action="store_true")
    parser.add_argument("--confirm-phone", action="store_true")
    parser.add_argument("--match-send-evidence", type=Path)
    parser.add_argument("--approval-reference")
    parser.add_argument("--prepare-focus", action="store_true", help="One bound-element foreground/focus preparation after native empty-input verification")
    waits = parser.add_mutually_exclusive_group()
    waits.add_argument("--wait-for-operator", action="store_true", help="Wait at most 60s after reader authentication, before any review or action")
    waits.add_argument("--wait-for-foreground", action="store_true", help="For an approved posted trial, wait for the bound window and fresh native readiness without another UI reply")
    parser.add_argument("--posted-readiness", action="store_true", help="Observe the new queued backend only; excludes focus preparation or sending")
    parser.add_argument("--review-unknown-action", action="append", default=[], help="Explicit prior uncertain action covered by a separate operator acceptance")
    parser.add_argument("--uncertainty-review-reference")
    args = parser.parse_args()
    sending = args.send_reviewed_once or args.send_posted_reviewed_once or args.send_sequence_reviewed_once or args.send_physical_reviewed_once
    queued_sending = args.send_posted_reviewed_once or args.send_sequence_reviewed_once or args.send_physical_reviewed_once
    readonly_queued = args.posted_readiness or args.sequence_readiness or args.physical_readiness
    if sum((args.send_reviewed_once, args.send_posted_reviewed_once, args.send_sequence_reviewed_once, args.send_physical_reviewed_once)) > 1:
        parser.error("choose exactly one fixed trial route")
    if sum((args.posted_readiness, args.sequence_readiness, args.physical_readiness)) > 1:
        parser.error("choose exactly one read-only route")
    if readonly_queued and (sending or args.confirm_phone or args.prepare_focus or args.wait_for_operator or args.wait_for_foreground):
        parser.error("posted readiness is strictly observation-only and excludes focus/send/confirmation/wait modes")
    if sending != bool(args.approval_reference):
        parser.error("a concrete operator approval reference is required for the one reviewed trial")
    if queued_sending and args.prepare_focus and not (args.send_sequence_reviewed_once or args.send_physical_reviewed_once):
        parser.error("the posted trial requires manual foreground/focus preparation")
    if args.wait_for_foreground and not queued_sending:
        parser.error("foreground observation wait requires the approved fixed posted trial")
    if (bool(args.review_unknown_action) != bool(args.uncertainty_review_reference)
            or (args.review_unknown_action or args.uncertainty_review_reference) and not queued_sending):
        parser.error("uncertainty acceptance must name exact prior actions and applies only to the new posted trial")
    if args.confirm_phone and (sending or not args.match_send_evidence):
        parser.error("phone confirmation requires one existing send result and excludes submission")
    prior = json.loads(args.match_account_evidence.read_text(encoding="utf-8"))
    if prior.get("status") != "header_key_verified" or not prior.get("header_hmac_verified"):
        parser.error("authenticated prior account evidence required")
    window = WindowBinding(args.pid, args.created_filetime, args.hwnd, EXE_SHA, DLL_SHA)
    output = dict(status="started", mode="physical_readiness_only" if args.physical_readiness else "sequence_readiness_only" if args.sequence_readiness else "posted_readiness_only" if args.posted_readiness else "phone_confirmation" if args.confirm_phone
                  else "send_physical_reviewed_once" if args.send_physical_reviewed_once
                  else "send_sequence_reviewed_once" if args.send_sequence_reviewed_once
                  else "send_posted_reviewed_once" if args.send_posted_reviewed_once else "send_reviewed_once" if args.send_reviewed_once else "readiness_only",
                  marker_sha256=hashlib.sha256((PHYSICAL_MARKER if args.physical_readiness or args.send_physical_reviewed_once else SEQUENCE_MARKER if args.sequence_readiness or args.send_sequence_reviewed_once else POSTED_TRIAL_MARKER if args.posted_readiness or args.send_posted_reviewed_once else TRIAL_MARKER).encode()).hexdigest(), model_requests=0)
    with args.output.open("x", encoding="utf-8") as destination, args.output.with_suffix(".journal.jsonl").open("x", encoding="utf-8") as record:
        def journal(stage, original):
            record.write(json.dumps(dict(stage=stage, original=original, at=time.time())) + "\n")
            record.flush()
            os.fsync(record.fileno())

        try:
            if args.confirm_phone:
                confirm_phone(args.match_send_evidence, output)
            elif sending:
                asyncio.run(bounded_send_trial(window, prior["account_fingerprint"], journal, args.approval_reference, output,
                    prepare_focus=args.prepare_focus, wait_for_operator=args.wait_for_operator, posted=queued_sending, sequence=args.send_sequence_reviewed_once, physical=args.send_physical_reviewed_once,
                    reviewed_unknowns=tuple(args.review_unknown_action), uncertainty_approval_ref=args.uncertainty_review_reference,
                    wait_foreground=args.wait_for_foreground))
            else:
                output["readiness"] = asyncio.run(readiness(window, prior["account_fingerprint"], journal, prepare_focus=args.prepare_focus,
                                                          posted=readonly_queued, sequence=args.sequence_readiness, physical=args.physical_readiness))
                output["status"] = "readiness_observed"
        except BaseException as exc:
            output.update(status="stopped", reason=exc.code if isinstance(exc, SocialError) else "directed_trial_failed")
        json.dump(output, destination, indent=2)
    print(json.dumps(output))
    return 0 if output["status"] in {"readiness_observed", "directed_trial_echo_observed", "phone_confirmation_recorded"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
