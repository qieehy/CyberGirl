"""One reviewed standalone-window background control in the ORIGINAL ledger.

This entry never chooses a different window, changes conversation, clears a
draft, retries input/submission, invokes a model, or falls back to foreground.
"""

import argparse
import asyncio
import hashlib
import json
import os
import time
from dataclasses import asdict
from pathlib import Path

from wechat_agent.bridges.wechat import channel_review
from wechat_agent.bridges.wechat.background_contract import (
    BMP_MARKER,
    BMP_REQUEST,
    CONTROL_MARKER,
    CONTROL_REQUEST,
    COVERED_MARKER,
    COVERED_REQUEST,
    DEFERRED_MARKER,
    DEFERRED_REQUEST,
    INTEGRATED_MARKER,
    RESTART_MARKER,
    RETURN_MARKER,
    RETURN_REQUEST,
    SOURCE_MARKER,
    SOURCE_REQUEST,
    VALUE_MARKER,
    VALUE_REQUEST,
)
from wechat_agent.bridges.wechat.background_journal import BackgroundIntentJournal
from wechat_agent.bridges.wechat.background_lock import writer_locks
from wechat_agent.bridges.wechat.background_process import BackgroundWorker
from wechat_agent.bridges.wechat.background_text import require_background_bmp_text
from wechat_agent.bridges.wechat.contracts import (
    AccountBinding,
    ConversationBinding,
    Cursor,
    DeliveryEvidence,
    SourceRecord,
    WindowBinding,
    digest,
)
from wechat_agent.bridges.wechat.receive_process import JsonReaderWorker
from wechat_agent.bridges.wechat.receive_state import ReaderCatalog
from wechat_agent.bridges.wechat.send_process import python_worker_command
from wechat_agent.bridges.wechat.send_state import SendState
from wechat_agent.bridges.wechat.sender import AccessibilityLease
from wechat_agent.bridges.wechat.store import BridgeStore
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import SocialError

if __package__:
    from .probe_wechat_directed_trial import prior_processes_exited
    from .probe_wechat_native_sender import LEDGER
    from .probe_wechat_standalone import worker_readiness
    from .verify_wechat_accessibility import DLL_SHA, EXE_SHA, NativeMemory
else:
    from probe_wechat_directed_trial import prior_processes_exited
    from probe_wechat_native_sender import LEDGER
    from probe_wechat_standalone import worker_readiness
    from verify_wechat_accessibility import DLL_SHA, EXE_SHA, NativeMemory


async def collect_echo(reader, starts, state, binding, action_id):
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
                    raise SocialError("background_echo_read_bound")
                if batch:
                    await reader.rpc("ack", name=name, cursor=asdict(batch[-1].position))
                    current = {**current, "start": asdict(batch[-1].position),
                               "local_fence": max(current["local_fence"], *(r.local_id for r in batch))}
                if len(batch) < 32:
                    break
            else:
                raise SocialError("background_echo_incomplete")
        try:
            state.local_echo(binding, action_id, tuple(records))
            return True
        except SocialError as exc:
            if exc.code != "local_echo_not_unique":
                raise
        await asyncio.sleep(1)
    return False


async def trial(window, fingerprint, record, args, output):
    diagnostic = getattr(args, "diagnose_reviewed_once", False)
    bmp_mode = getattr(args, "bmp_mode", False)
    return_mode = getattr(args, "return_mode", False)
    covered_mode = getattr(args, "covered_mode", False)
    deferred = args.deferred_focus
    value_mode = args.value_mode
    marker, request = ((COVERED_MARKER, COVERED_REQUEST) if covered_mode else (RETURN_MARKER, RETURN_REQUEST) if return_mode else (BMP_MARKER, BMP_REQUEST) if bmp_mode else (SOURCE_MARKER, SOURCE_REQUEST) if diagnostic else (DEFERRED_MARKER, DEFERRED_REQUEST) if deferred else
                       (VALUE_MARKER, VALUE_REQUEST) if value_mode else (CONTROL_MARKER, CONTROL_REQUEST))
    if bmp_mode or return_mode or covered_mode:
        require_background_bmp_text(marker)
    method = "utf16_bmp_covered" if covered_mode else "utf16_bmp_return" if return_mode else "utf16_bmp_messages" if bmp_mode else "uia_source_diagnostic" if diagnostic else "uia_deferred_focus" if deferred else "uia_value_once" if value_mode else "utf16_messages"
    kind = "standalone_background_covered" if covered_mode else "standalone_background_return" if return_mode else "standalone_background_bmp" if bmp_mode else "standalone_background_source" if diagnostic else "standalone_background_deferred" if deferred else "standalone_background_value" if value_mode else "standalone_background_control"
    reader = JsonReaderWorker(python_worker_command(Path(__file__).with_name("wechat_reader_worker.py")))
    worker = lease = locks = binding = review = operation = action = None
    reader_stopped = worker_reaped = True
    restored = True
    reason = "background_stopped_before_input"
    with Ledger(LEDGER) as ledger:
        state = SendState(ledger)
        try:
            manifest = await reader.start(window, fingerprint)
            catalog = ReaderCatalog(ledger)
            binding = catalog.attach(manifest, window, fingerprint, initialize=True)
            starts = catalog.checkpoints()
            locks = writer_locks(binding)
            lease = AccessibilityLease(NativeMemory(window.pid, activate=True, hwnd=window.hwnd,
                created_filetime=window.created_filetime), window, record)
            lease.open()
            BridgeStore(ledger).bind(binding, mode="draft")
            ledger.configure_owner(binding.scope, binding.account.self_id)
            facts = await worker_readiness(window, binding, fingerprint, value_mode=value_mode, deferred_focus=deferred,
                                           source_diagnostic=diagnostic, bmp_mode=bmp_mode, return_mode=return_mode,
                                           covered_mode=covered_mode)
            facts.update(observed_at=time.time(), binding_generation=binding.generation,
                         prior_process_generation_exited=prior_processes_exited(ledger, window))
            review = channel_review.authorize(ledger, binding, request, marker, facts,
                approval_ref=args.approval_reference, reviewed_unknowns=tuple(args.review_unknown_action),
                uncertainty_approval_ref=args.uncertainty_review_reference,
                backend_kind=kind)
            action_id = state.queue_marker(binding, request, marker, review_id=review)
            output.update(action_id=action_id, review_id=review)
            action = ledger.claim(action_id)
            if action is None:
                raise SocialError("background_action_not_claimed")
            operation = state.begin(binding, action, {v["shard"]: Cursor(**v["start"]) for v in starts.values()})
            output["operation_id"] = operation
            intents = BackgroundIntentJournal(state, operation, binding, action)
            worker = BackgroundWorker(python_worker_command(Path(__file__).with_name("wechat_standalone_worker.py"),
                "--pid", str(window.pid), "--hwnd", str(window.hwnd), "--created-filetime", str(window.created_filetime),
                "--account-fingerprint", fingerprint, "--allow-control",
                "--input-method", method), record=intents.record)
            worker_reaped = False
            async with asyncio.timeout(15):
                await worker.start(operation, binding, action)
                await worker._rpc("prepare")
            async with asyncio.timeout(25):
                result = await worker._rpc("run_background")
            output.update(result)
            control = result["control_result"]
            reason = "background_request_awaiting_echo" if control["state"] == "REQUESTED" else control["reason"]
        except BaseException as exc:
            reason = exc.code if isinstance(exc, SocialError) else "background_trial_interrupted"
            raise
        finally:
            try:
                try:
                    if worker:
                        worker_reaped = await worker.stop()
                finally:
                    output["worker_reaped"] = worker_reaped
                    restored = lease.close(restore=worker_reaped) if lease else True
                    output["provider_restored"] = restored
                if operation:
                    touched = state.finish(operation, reaped=worker_reaped, restored=restored, reason=reason)
                    ledger.finish_delivery(action.action_id, "UNCONFIRMED" if touched else "FAILED", reason)
                    row = ledger._db.execute("SELECT input_permitted,submit_permitted FROM wechat_send_operations WHERE operation_id=?",
                                             (operation,)).fetchone()
                    output.update(input_permitted=row[0], submit_permitted=row[1])
                    if row[1] and worker_reaped and restored:
                        try:
                            async with asyncio.timeout(15):
                                output["unique_local_echo"] = await collect_echo(reader, starts, state, binding, action.action_id)
                        except Exception as exc:
                            output.update(unique_local_echo=False, echo_reason=exc.code if isinstance(exc, SocialError)
                                          else "background_echo_failed")
            finally:
                try:
                    if review:
                        channel_review.close_trial(ledger, review)
                finally:
                    try:
                        if binding:
                            BridgeStore(ledger).unbind(binding)
                    finally:
                        try:
                            reader_stopped = await reader.stop()
                            output["reader_reaped"] = reader_stopped
                        finally:
                            if locks:
                                locks.close()
                                output["locks_released"] = True
            if action:
                output["delivery_state"] = ledger.action_summary(action.action_id)["state"]
    if diagnostic:
        output.update(status="background_source_diagnostic_completed" if (
            worker_reaped and restored and reader_stopped and output.get("submit_permitted") == 0
            and output.get("control_result", {}).get("reason") == "background_source_diagnostic_complete"
            and output.get("worker_local_cleanup_verified") is True) else "background_source_diagnostic_unconfirmed",
            phone_confirmation_required=False)
    elif (worker_reaped and restored and reader_stopped and output.get("unique_local_echo") is True
            and output.get("worker_local_cleanup_verified") is True
            and output.get("control_result", {}).get("focus_cleanup_verified") is True):
        output.update(status="background_control_echo_observed", phone_confirmation_required=True)
    else:
        output.update(status="background_control_unconfirmed", phone_confirmation_required=bool(output.get("submit_permitted")))


def confirm_phone(prior, output, *, delivery_only=False):
    required = ("worker_reaped", "reader_reaped", "provider_restored", "worker_local_cleanup_verified",
                "unique_local_echo", "locks_released")
    markers = {"standalone_background_control": CONTROL_MARKER, "standalone_background_value": VALUE_MARKER,
               "standalone_background_deferred": DEFERRED_MARKER, "standalone_background_bmp": BMP_MARKER,
               "standalone_background_return": RETURN_MARKER, "standalone_background_covered": COVERED_MARKER,
               "integrated_background_bmp": INTEGRATED_MARKER, "restarted_background_bmp": RESTART_MARKER}
    marker = markers.get(prior.get("mode"))
    if (marker is None
            or not all(prior.get(k) is True for k in required)
            or prior.get("submit_permitted") != 1
            or not delivery_only and prior.get("control_result", {}).get("focus_cleanup_verified") is not True):
        raise SocialError("background_phone_evidence_incomplete")
    with Ledger(LEDGER) as ledger:
        row = ledger._db.execute("""SELECT s.*,o.text,b.payload FROM wechat_send_operations s
            JOIN outbox o USING(action_id) JOIN bridge_bindings b ON b.scope=s.scope
            WHERE s.action_id=? AND s.operation_id=?""", (prior["action_id"], prior["operation_id"])).fetchone()
        if (not row or row["text"] != marker or not row["finished"]
                or not row["worker_reaped"] or not row["restore_verified"]
                or row["content_sha256"] != hashlib.sha256(marker.encode()).hexdigest()):
            raise SocialError("background_phone_action_mismatch")
        if delivery_only:
            local = [json.loads(item[0]) for item in ledger._db.execute(
                "SELECT payload FROM delivery_evidence WHERE action_id=? AND kind='local_echo'",
                (row["action_id"],))]
            if len(local) != 1 or (local[0].get("reference"), local[0].get("content_sha256"),
                                  local[0].get("binding_generation")) != (
                    row["operation_id"], row["content_sha256"], row["generation"]):
                raise SocialError("background_phone_local_echo_required")
        data = json.loads(row["payload"])
        data["account"]["window"] = WindowBinding(**data["account"]["window"])
        data["account"] = AccountBinding(**data["account"])
        binding = ConversationBinding(**data)
        evidence = DeliveryEvidence(digest([row["action_id"], "background-phone-confirmation"]), row["action_id"],
            binding.scope, row["generation"], "manual_confirmation",
            "user-phone-background-" + ("integrated" if marker == INTEGRATED_MARKER else "restart" if marker == RESTART_MARKER else "covered" if marker == COVERED_MARKER else "return" if marker == RETURN_MARKER else "bmp" if marker == BMP_MARKER else "deferred" if marker == DEFERRED_MARKER else "value" if marker == VALUE_MARKER else "control") + "-20261107",
            row["content_sha256"])
        output["evidence_added"] = BridgeStore(ledger).record_evidence(evidence)
        output.update(status="background_phone_confirmation_recorded", action_id=row["action_id"],
                      delivery_state=ledger.action_summary(row["action_id"])["state"],
                      mode=prior["mode"], marker_sha256=row["content_sha256"])
        if delivery_only:
            output.update(background_acceptance=False, original_control_result_preserved=True,
                          confirmation_scope="delivery_only")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int)
    parser.add_argument("--hwnd", type=int)
    parser.add_argument("--created-filetime", type=int)
    parser.add_argument("--match-account-evidence", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--approval-reference")
    parser.add_argument("--review-unknown-action", action="append", default=[])
    parser.add_argument("--uncertainty-review-reference")
    parser.add_argument("--send-reviewed-once", action="store_true")
    parser.add_argument("--diagnose-reviewed-once", action="store_true", help="One reviewed fixed value only; no focus, mouse messages or submission")
    parser.add_argument("--value-mode", action="store_true")
    parser.add_argument("--deferred-focus", action="store_true")
    parser.add_argument("--bmp-mode", action="store_true")
    parser.add_argument("--return-mode", action="store_true")
    parser.add_argument("--covered-mode", action="store_true")
    parser.add_argument("--confirm-phone-evidence", type=Path)
    parser.add_argument("--confirm-delivery-only", action="store_true",
                        help="Reconcile explicit handset confirmation plus stored echo without accepting background cleanup")
    args = parser.parse_args()
    if args.confirm_delivery_only and not args.confirm_phone_evidence:
        parser.error("delivery-only confirmation requires existing evidence and excludes a new send")
    if args.diagnose_reviewed_once and (args.value_mode or args.deferred_focus or args.bmp_mode or args.return_mode or args.covered_mode):
        parser.error("input-only diagnostic excludes sending methods")
    if sum((args.value_mode, args.deferred_focus, args.bmp_mode, args.return_mode, args.covered_mode)) > 1:
        parser.error("choose exactly one fixed input method")
    if sum((args.send_reviewed_once, args.diagnose_reviewed_once, bool(args.confirm_phone_evidence))) != 1:
        parser.error("choose one reviewed control, input-only diagnostic OR existing phone-confirmation result")
    if (args.send_reviewed_once or args.diagnose_reviewed_once) and (not all((args.pid, args.hwnd, args.created_filetime,
            args.match_account_evidence, args.approval_reference, args.uncertainty_review_reference))
            or not args.review_unknown_action):
        parser.error("concrete approval, original account/window and named old uncertainties required")
    if args.confirm_phone_evidence and (args.approval_reference or args.review_unknown_action):
        parser.error("phone confirmation excludes new-action approval")
    mode = "standalone_background_covered" if args.covered_mode else "standalone_background_return" if args.return_mode else "standalone_background_bmp" if args.bmp_mode else "standalone_background_source" if args.diagnose_reviewed_once else "standalone_background_deferred" if args.deferred_focus else "standalone_background_value" if args.value_mode else "standalone_background_control"
    marker = COVERED_MARKER if args.covered_mode else RETURN_MARKER if args.return_mode else BMP_MARKER if args.bmp_mode else SOURCE_MARKER if args.diagnose_reviewed_once else DEFERRED_MARKER if args.deferred_focus else VALUE_MARKER if args.value_mode else CONTROL_MARKER
    output = dict(status="started", mode=mode, model_requests=0, marker_sha256=hashlib.sha256(marker.encode()).hexdigest())
    with args.output.open("x", encoding="utf-8") as destination, args.output.with_suffix(".journal.jsonl").open("x", encoding="utf-8") as journal:
        def record(stage, original):
            journal.write(json.dumps(dict(stage=stage, original=original, at=time.time())) + "\n")
            journal.flush()
            os.fsync(journal.fileno())

        try:
            if args.confirm_phone_evidence:
                confirm_phone(json.loads(args.confirm_phone_evidence.read_text(encoding="utf-8")), output,
                              delivery_only=args.confirm_delivery_only)
            else:
                prior = json.loads(args.match_account_evidence.read_text(encoding="utf-8"))
                if (prior.get("status") != "header_key_verified" or prior.get("header_hmac_verified") is not True
                        or prior.get("active_account_matched") is not True):
                    raise SocialError("background_authenticated_account_required")
                window = WindowBinding(args.pid, args.created_filetime, args.hwnd, EXE_SHA, DLL_SHA)
                asyncio.run(asyncio.wait_for(trial(window, prior["account_fingerprint"], record, args, output), timeout=90))
        except BaseException as exc:
            output.update(status="stopped", reason=exc.code if isinstance(exc, SocialError) else "background_trial_failed")
        json.dump(output, destination, indent=2)
        destination.flush()
        os.fsync(destination.fileno())
    print(json.dumps(output))
    return 0 if output["status"] in {"background_control_echo_observed", "background_phone_confirmation_recorded",
                                     "background_source_diagnostic_completed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
