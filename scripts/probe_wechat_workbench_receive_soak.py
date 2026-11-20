"""R1: thirty real minutes of workbench observation, one restart, no generator/sender."""

import argparse
import asyncio
import ctypes
import hashlib
import json
import os
import time
import traceback
from pathlib import Path

from wechat_agent.bridges.wechat.receive_observer import ReceiveObserver
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import SocialError
from wechat_agent.workbench.console import WorkbenchConsoleServer
from wechat_agent.workbench.native import open_native_receiver
from wechat_agent.workbench.service import WeChatWorkbenchService

ROOT = Path(__file__).resolve().parents[1]
SEND_TABLES = ("outbox", "attempts", "wechat_send_operations", "wechat_send_journal",
               "wechat_channel_reviews", "delivery_evidence")


def require_markers(seen, restarted):
    rows = []
    for label in "ABC":
        sha = hashlib.sha256(f"R1-20261116-{label}".encode()).hexdigest()
        matches = [item for item in seen.values() if item["sha256"] == sha]
        if len(matches) != 1:
            raise SocialError("r1_three_unique_spaced_markers_required")
        rows.append(matches[0])
    if (not restarted or rows[0]["after_restart"] or not rows[2]["after_restart"]
            or any(b["at_seconds"] - a["at_seconds"] < 30 for a, b in zip(rows, rows[1:], strict=False))):
        raise SocialError("r1_markers_must_span_restart_and_arrive_separately")


def fingerprints(db):
    result = {}
    for table in (*SEND_TABLES, "workbench_draft_revisions"):
        rows = [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
        result[table] = hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()
    result["model_traces"] = db.execute("SELECT COUNT(*) FROM workbench_requests WHERE kind='model_trace'").fetchone()[0]
    result["drafts"] = db.execute("SELECT COUNT(*) FROM workbench_drafts").fetchone()[0]
    return result


def visible_payload_hashes(state):
    # Pre-schema9 records intentionally have no stored text; never backfill them.
    return {m["message_id"]: hashlib.sha256(m["text"].encode()).hexdigest()
            for m in state["messages"] if isinstance(m["text"], str)}


async def http_state(server):
    reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
    try:
        writer.write((f"GET /api/wechat/state HTTP/1.1\r\nHost: 127.0.0.1:{server.port}\r\n"
                      f"Authorization: Bearer {server.token}\r\n\r\n").encode())
        await writer.drain()
        async with asyncio.timeout(5):
            raw = await reader.read()
        if len(raw) > 2 * 1024 * 1024:
            raise SocialError("r1_http_state_bound")
        headers, body = raw.split(b"\r\n\r\n", 1)
        if b"200 Response" not in headers:
            raise SocialError("r1_http_state_failed")
        return json.loads(body)
    finally:
        writer.close()
        await writer.wait_closed()


async def observe(ledger, factory, observer, output, event, *, duration=1800,
                  clock=time.monotonic, sleep=asyncio.sleep, window_check=lambda: None):
    """Short injected clocks are only for offline tests; the CLI always uses 1800."""
    baseline = fingerprints(ledger._db)
    receiver = wb = server = None
    reaped = []
    metrics = []
    seen = {}
    try:
        receiver, sender = await factory()
        if sender is not None:
            raise SocialError("r1_sender_must_be_absent")
        await receiver.poll()
        start_rowid = ledger._db.execute("SELECT COALESCE(MAX(rowid),0) FROM bridge_records").fetchone()[0]
        scope, generation = receiver.binding.scope.key(), receiver.binding.generation
        wb = WeChatWorkbenchService(ledger, receiver)
        server = WorkbenchConsoleServer(ledger, wb)
        await server.start(0)
        wb.start()
        observer.start()
        observer.phase = "workbench_read_only_observation"
        started, restarted, last_progress, http_checks = clock(), False, -30, 0
        output.update(started_at=time.time(), reader_start_pid=receiver.worker.process.pid,
                      start_checkpoints=receiver.catalog.checkpoints(), generation=generation)
        event("observation_started", duration_seconds=duration, restart_after_seconds=duration / 2)
        while clock() - started < duration:
            observer.check()
            window_check()
            if not wb.reading or wb.read_error:
                raise SocialError(wb.read_error or "r1_reader_stopped")
            if wb.tasks or wb.generator is not None or wb.sender is not None or fingerprints(ledger._db) != baseline:
                raise SocialError("r1_unexpected_model_or_send_activity")
            elapsed = clock() - started
            rows = ledger._db.execute("""SELECT p.message_id,p.text,b.disposition FROM bridge_message_payloads p
                JOIN bridge_records b USING(message_id) WHERE p.scope=? AND b.rowid>? AND p.message_type='text'""",
                (scope, start_rowid)).fetchall()
            for row in rows:
                if row["message_id"] not in seen:
                    seen[row["message_id"]] = dict(message_id=row["message_id"],
                        sha256=hashlib.sha256(row["text"].encode()).hexdigest(), at_seconds=elapsed,
                        after_restart=restarted, disposition=row["disposition"])
                    event("new_text_committed", **seen[row["message_id"]])
            if elapsed - last_progress >= 30:
                state = await http_state(server)
                http_checks += 1
                if state["model"] is not None or state["send_enabled"] or not state["reading"]:
                    raise SocialError("r1_http_mode_changed")
                shown = visible_payload_hashes(state)
                for item in seen.values():
                    if shown.get(item["message_id"]) != item["sha256"]:
                        raise SocialError("r1_http_payload_mismatch")
                event("observation_progress", elapsed_seconds=elapsed, new_texts=len(seen), http_checks=http_checks)
                last_progress = elapsed
            if elapsed >= duration / 2 and not restarted:
                async with wb.operation_lock:
                    saved = receiver.catalog.checkpoints()
                    metrics.append(await receiver.worker.rpc("read_metrics"))
                old_pid = receiver.worker.process.pid
                await server.aclose()
                server = None
                await wb.aclose()
                reaped.append(receiver.worker.process.returncode is not None)
                receiver, sender = await factory()
                if (sender is not None or receiver.catalog.checkpoints() != saved
                        or receiver.binding.generation != generation or receiver.worker.process.pid == old_pid):
                    raise SocialError("r1_restart_identity_or_cursor_changed")
                wb = WeChatWorkbenchService(ledger, receiver)
                server = WorkbenchConsoleServer(ledger, wb)
                await server.start(0)
                wb.start()
                restarted = True
                output["midrun_restart"] = dict(at_seconds=elapsed, prior_reader_pid=old_pid,
                    new_reader_pid=receiver.worker.process.pid, checkpoints_unchanged=True, generation_unchanged=True)
                event("midrun_restart_completed", **output["midrun_restart"])
            await sleep(min(2, max(0, duration - (clock() - started))))
        observer.check()
        output.update(continuous_seconds=clock() - started, http_checks=http_checks,
                      end_checkpoints=receiver.catalog.checkpoints(), new_texts=list(seen.values()))
        require_markers(seen, restarted)
        output["marker_checks_passed"] = True
        output["status"] = "r1_workbench_observation_completed"
    finally:
        try:
            if receiver and wb and not wb.closed:
                async with wb.operation_lock:
                    metrics.append(await receiver.worker.rpc("read_metrics"))
        finally:
            try:
                if server:
                    await server.aclose()
            finally:
                if wb and wb.receiver is receiver:
                    await wb.aclose()
                elif receiver:
                    await receiver.close()
                if receiver:
                    reaped.append(receiver.worker.process.returncode is not None)
                output["observer_stopped"] = observer.stop()
                output["observation"] = observer.report()
                output["all_readers_reaped"] = bool(reaped) and all(reaped)
                output["read_metrics"] = metrics
                output["protected_tables_and_model_counts_unchanged"] = fingerprints(ledger._db) == baseline
                output["active_bindings"] = ledger._db.execute("SELECT COUNT(*) FROM bridge_bindings WHERE active=1").fetchone()[0]
                output["integrity_ok"] = ledger._db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                if (not output["all_readers_reaped"] or not output["observer_stopped"]
                        or not output["protected_tables_and_model_counts_unchanged"] or not output["integrity_ok"]
                        or output["active_bindings"] or output["observation"]["failure"]
                        or output["observation"]["foreground_target_seen"]
                        or output["observation"]["maximum_sample_gap_ms"] > 250
                        or not metrics or any(m["snapshot_retry_exhausted"] for m in metrics)):
                    raise SocialError("r1_cleanup_or_observation_incomplete")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("pid", "hwnd", "created-filetime"):
        parser.add_argument("--" + name, type=int, required=True)
    parser.add_argument("--account-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if min(args.pid, args.hwnd, args.created_filetime) <= 0:
        parser.error("positive current binding required")
    output = dict(status="started", model_assembled=False, sender_assembled=False, target_seconds=1800)
    with args.output.open("x", encoding="utf-8") as destination, args.output.with_suffix(".journal.jsonl").open("x", encoding="utf-8") as journal:
        def event(kind, **fields):
            record = dict(event=kind, at=time.time(), **fields)
            journal.write(json.dumps(record) + "\n")
            journal.flush()
            os.fsync(journal.fileno())
            print(json.dumps(record), flush=True)

        async def run():
            with Ledger(ROOT / ".state/social/wechat-sender-poc.sqlite3") as ledger:
                async def factory():
                    return await open_native_receiver(ledger, evidence_path=args.account_evidence,
                        pid=args.pid, hwnd=args.hwnd, created_filetime=args.created_filetime, send=False)

                # Profile/native imports occur only inside this explicitly requested CLI.
                from scripts.wechat_42013_profile import activate
                activate()
                from scripts.verify_wechat_accessibility import NativeMemory
                memory = NativeMemory(args.pid, hwnd=args.hwnd, created_filetime=args.created_filetime)
                try:
                    memory.preflight()
                    memory.u.IsIconic.argtypes = [ctypes.c_void_p]
                    memory.u.IsIconic.restype = ctypes.c_int
                    def check_window():
                        memory.validate()
                        if memory.u.IsIconic(args.hwnd) or not memory.u.IsWindowVisible(args.hwnd):
                            raise SocialError("r1_target_minimized_or_hidden")
                    await observe(ledger, factory, ReceiveObserver(args.pid), output, event, window_check=check_window)
                    output["gate_after"] = memory.read()
                    if output["continuous_seconds"] < 1800 or output["gate_after"] != 0:
                        raise SocialError("r1_duration_or_gate_incomplete")
                finally:
                    memory.close()
        try:
            asyncio.run(asyncio.wait_for(run(), 1900))
        except BaseException as exc:
            output.update(status="stopped", reason=exc.code if isinstance(exc, SocialError) else type(exc).__name__)
            output["failure_location"] = [dict(file=Path(frame.filename).name, line=frame.lineno, function=frame.name)
                                          for frame in traceback.extract_tb(exc.__traceback__)[-6:]]
        output["finished_at"] = time.time()
        json.dump(output, destination, indent=2)
        destination.flush()
        os.fsync(destination.fileno())
    print(json.dumps(output), flush=True)
    return 0 if output["status"] == "r1_workbench_observation_completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
