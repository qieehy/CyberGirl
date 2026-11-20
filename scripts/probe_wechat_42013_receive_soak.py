"""Read-only 30 minute recovery acceptance. Never queues or sends a message."""
import argparse
import asyncio
import hashlib
import json
import os
import time
from pathlib import Path

from scripts.wechat_42013_profile import activate

# Select the explicit build before importing any native entry points.
if __name__ == "__main__":
    activate()

from wechat_agent.bridges.wechat.background_completion import CLEAN_STAGE
from wechat_agent.bridges.wechat.contracts import WindowBinding
from wechat_agent.bridges.wechat.receive_observer import ReceiveObserver
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import SocialError
from wechat_agent.social.service import SocialService


class NoSendAdapter:
    def __init__(self, binding):
        self.identity = ("wechat", binding.account.account_id)
        self.called = False
    async def send_text(self, action):
        self.called = True
        raise SocialError("receive_only_send_denied")


def totals(ledger):
    row = ledger._db.execute("SELECT COUNT(*),COALESCE(SUM(input_permitted),0),COALESCE(SUM(submit_permitted),0) FROM wechat_send_operations").fetchone()
    return dict(operations=row[0], inputs=row[1], submits=row[2],
        outbox=ledger._db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0],
        attempts=ledger._db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0])


def predecessors(ledger, paths, binding):
    ids = []
    markers = {"integrated_background_bmp": "W2B-20261107-background-integrated-中文",
               "restarted_background_bmp": "W2B-20261107-background-restart-中文"}
    for path in paths:
        proof = json.loads(path.read_text(encoding="utf-8"))
        marker = markers.get(proof.get("mode"))
        if not marker or proof.get("binding_generation") != binding.generation:
            raise SocialError("receive_only_predecessor_changed")
        row = ledger._db.execute("""SELECT s.*,o.state,o.text FROM wechat_send_operations s JOIN outbox o USING(action_id)
            WHERE s.action_id=? AND s.operation_id=?""", (proof["action_id"], proof["operation_id"])).fetchone()
        if (not row or row["state"] != "CONFIRMED" or row["text"] != marker
                or row["content_sha256"] != hashlib.sha256(marker.encode()).hexdigest()
                or any(row[key] != 1 for key in ("finished", "worker_reaped", "restore_verified", "input_permitted", "submit_permitted"))
                or not ledger._db.execute("SELECT 1 FROM wechat_send_journal WHERE operation_id=? AND stage=?", (row["operation_id"], CLEAN_STAGE)).fetchone()):
            raise SocialError("receive_only_predecessor_unreconciled")
        if not ledger._db.execute("SELECT 1 FROM delivery_evidence WHERE action_id=? AND kind='manual_confirmation'", (row["action_id"],)).fetchone():
            raise SocialError("receive_only_phone_confirmation_required")
        ids.append(row["action_id"])
    if len(set(ids)) != 2:
        raise SocialError("receive_only_exact_two_predecessors_required")
    return ids


async def receive_window(ledger, receiver, observer, replay_ids, output, event, *, receiver_factory,
                         no_model_factory, duration=1800, clock=time.monotonic, sleep=asyncio.sleep):
    """Real CLI uses 1800 seconds. Injected short clocks are only for unit tests."""
    service = None
    snapshot_retries = snapshot_exhausted = 0
    initial = totals(ledger)
    original_generation = receiver.binding.generation
    adapter = NoSendAdapter(receiver.binding)
    try:
        service = SocialService(ledger, no_model_factory(), [adapter])
        for aid in replay_ids:
            await service.dispatch(aid)
        if totals(ledger) != initial or adapter.called:
            raise SocialError("receive_only_replay_detected")
        output.update(confirmed_replay_checks=len(replay_ids), start_checkpoints=receiver.catalog.checkpoints(),
                      starting_totals=initial, reader_start_pid=receiver.worker.process.pid)
        observer.start()
        observer.phase = "read_only_receive"
        observer.check()
        started, restarted, polls, fresh, replay = clock(), False, 0, 0, 0
        event("read_only_receive_started", duration_seconds=duration)
        while clock() - started < duration:
            observer.check()
            async with asyncio.timeout(20):
                result = await receiver.poll()
            if result["more"]:
                raise SocialError("receive_only_page_bound")
            polls += 1
            fresh += result["fresh"]
            replay += result["replay"]
            elapsed = clock() - started
            if elapsed >= duration / 2 and not restarted:
                saved, previous_pid = receiver.catalog.checkpoints(), receiver.worker.process.pid
                metrics = await receiver.worker.rpc("read_metrics")
                snapshot_retries += metrics["snapshot_retries"]
                snapshot_exhausted += metrics["snapshot_retry_exhausted"]
                await service.aclose()
                await receiver.close()
                receiver = receiver_factory()
                resumed = await receiver.start()
                if resumed != saved or receiver.worker.process.pid == previous_pid or receiver.binding.generation != original_generation:
                    raise SocialError("receive_only_restart_cursor_changed")
                adapter = NoSendAdapter(receiver.binding)
                service = SocialService(ledger, no_model_factory(), [adapter])
                for aid in replay_ids:
                    await service.dispatch(aid)
                if totals(ledger) != initial or adapter.called:
                    raise SocialError("receive_only_restart_replay_detected")
                output["midrun_restart"] = dict(at_seconds=elapsed, previous_reader_pid=previous_pid,
                    reader_pid=receiver.worker.process.pid, checkpoints_unchanged=True, no_send_replay=True)
                event("reader_and_service_restarted", **output["midrun_restart"])
                restarted = True
            if totals(ledger) != initial or adapter.called:
                raise SocialError("receive_only_unexpected_send_activity")
            if polls % 15 == 0:
                event("receive_progress", elapsed_seconds=elapsed, polls=polls, fresh=fresh, replay=replay)
            await sleep(min(2, max(0, duration - elapsed)))
        observer.check()
        if not restarted or replay:
            raise SocialError("receive_only_acceptance_incomplete")
        output.update(status="read_only_receive_restart_completed", continuous_seconds=clock()-started,
                      target_seconds=duration, polls=polls, received_fresh=fresh, received_replays=replay,
                      end_checkpoints=receiver.catalog.checkpoints())
    finally:
        try:
            if service:
                await service.aclose()
        finally:
            try:
                try:
                    async with asyncio.timeout(2):
                        metrics = await receiver.worker.rpc("read_metrics")
                    snapshot_retries += metrics["snapshot_retries"]
                    snapshot_exhausted += metrics["snapshot_retry_exhausted"]
                    output.update(snapshot_retries=snapshot_retries, snapshot_retry_exhausted=snapshot_exhausted)
                except Exception:
                    output["snapshot_metrics_unavailable"] = True
                finally:
                    output["reader_reaped"] = await receiver.close()
            finally:
                output["observer_stopped"] = observer.stop()
                report = output["observation"] = observer.report()
                output.update(ending_totals=totals(ledger), model_requests=0, new_sends=0)
                if (output["observer_stopped"] is not True or report["foreground_target_seen"]
                        or report["failure"] is not None or report["maximum_sample_gap_ms"] > 250):
                    output.update(status="stopped", reason="receive_only_observation_incomplete")
                if output["ending_totals"] != initial:
                    output.update(status="stopped", reason="receive_only_unexpected_send_activity")
                if output.get("status") == "read_only_receive_restart_completed" and (
                        output.get("snapshot_metrics_unavailable") or output.get("snapshot_retry_exhausted") != 0):
                    output.update(status="stopped", reason="receive_only_snapshot_recovery_incomplete")


async def run(args, output, event):
    # All real imports follow the explicit profile selection in the CLI.
    from scripts import probe_wechat_42013_service as selected
    host = selected.entry
    reference = json.loads(args.account_evidence.read_text(encoding="utf-8"))
    if reference.get("status") != "header_key_verified" or not reference.get("header_hmac_verified") or not reference.get("active_account_matched"):
        raise SocialError("receive_only_original_account_required")
    fingerprint = reference["account_fingerprint"]
    window = WindowBinding(args.pid, args.created_filetime, args.hwnd, host.EXE_SHA, host.DLL_SHA)
    receiver = None
    with Ledger(host.LEDGER) as ledger:
        try:
            receiver = host.new_receiver(ledger, window, fingerprint)
            await receiver.start()
            binding = receiver.binding
            ids = predecessors(ledger, [args.integrated_evidence, args.restart_evidence], binding)
            output.update(service_pid=os.getpid(), binding_generation=binding.generation, predecessor_action_ids=ids,
                          resumed_after_interrupted_run=True, sends_disabled=True)
            output["readiness"] = await host.readonly_ready(ledger, binding, fingerprint,
                lambda stage, original: event("readiness_gate", stage=stage, original=original))
            observer = ReceiveObserver(window.pid)
            owned_receiver, receiver = receiver, None
            await receive_window(ledger, owned_receiver, observer, ids, output, event,
                receiver_factory=lambda: host.new_receiver(ledger, window, fingerprint), no_model_factory=host.NoModel)
            if output["status"] == "read_only_receive_restart_completed" and output["continuous_seconds"] < 1800:
                raise SocialError("receive_only_real_duration_incomplete")
        finally:
            if receiver:
                output["reader_reaped"] = await receiver.close()
            output.update(integrity_ok=ledger._db.execute("PRAGMA integrity_check").fetchone()[0] == "ok",
                active_operations=ledger._db.execute("SELECT COUNT(*) FROM wechat_send_operations WHERE finished=0").fetchone()[0])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("pid", "hwnd", "created-filetime"):
        parser.add_argument("--"+key, type=int, required=True)
    for key in ("account-evidence", "integrated-evidence", "restart-evidence", "output"):
        parser.add_argument("--"+key, type=Path, required=True)
    args = parser.parse_args()
    output = dict(status="started", sends_disabled=True)
    with args.output.open("x", encoding="utf-8") as destination, args.output.with_suffix(".journal.jsonl").open("x", encoding="utf-8") as journal:
        def event(kind, **fields):
            entry = dict(event=kind, at=time.time(), **fields)
            journal.write(json.dumps(entry)+"\n")
            journal.flush()
            os.fsync(journal.fileno())
            print(json.dumps(entry),flush=True)
        try:
            asyncio.run(asyncio.wait_for(run(args,output,event),timeout=1900))
        except BaseException as exc:
            output.update(status="stopped",reason=exc.code if isinstance(exc,SocialError) else "receive_only_interrupted")
        json.dump(output,destination,indent=2)
        destination.flush()
        os.fsync(destination.fileno())
    print(json.dumps(output),flush=True)
    return 0 if output["status"]=="read_only_receive_restart_completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
