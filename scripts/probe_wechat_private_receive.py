"""Explicit private-target baseline or one inbound acceptance; no generator/sender."""

import argparse
import asyncio
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

from scripts.probe_wechat_workbench_receive_soak import http_state
from wechat_agent.bridges.wechat.contracts import Cursor
from wechat_agent.bridges.wechat.private_receive import private_scope
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import SocialError
from wechat_agent.workbench.console import WorkbenchConsoleServer
from wechat_agent.workbench.private_native import open_private_receiver
from wechat_agent.workbench.service import WeChatWorkbenchService

ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / ".state/social/wechat-sender-poc.sqlite3"
PROTECTED = ("outbox", "attempts", "wechat_send_operations", "wechat_send_journal", "wechat_channel_reviews", "delivery_evidence",
             "workbench_drafts", "workbench_draft_revisions")


def audit(db):
    values = {name: hashlib.sha256(json.dumps([tuple(r) for r in db.execute(f"SELECT * FROM {name} ORDER BY rowid")],
                                            ensure_ascii=False).encode()).hexdigest() for name in PROTECTED}
    values["filehelper_cursors"] = [tuple(r) for r in db.execute("SELECT * FROM bridge_cursors ORDER BY source_key")
                                    if json.loads(r["scope"])[3] == "filehelper"]
    values["model_traces"] = db.execute("SELECT COUNT(*) FROM workbench_requests WHERE kind='model_trace'").fetchone()[0]
    return values


async def run(args, result, event):
    with sqlite3.connect(DATABASE.as_uri() + "?mode=ro", uri=True) as before_db:
        before_db.row_factory = sqlite3.Row
        before = audit(before_db)
        result["starting_schema"] = before_db.execute("PRAGMA user_version").fetchone()[0]
    scope_raw = await asyncio.to_thread(args.private_scope_file.read_bytes)
    selected = private_scope(json.loads(scope_raw))
    result["target_sha256"] = hashlib.sha256(selected["username"].encode()).hexdigest()
    with Ledger(DATABASE) as ledger:
        receiver = resumed = wb = server = None
        try:
            options = dict(scope_file=args.private_scope_file, pid=args.pid, hwnd=args.hwnd, created_filetime=args.created_filetime)
            receiver = await open_private_receiver(ledger, **options, initialize=args.initialize_only)
            result["reader_pid"] = receiver.worker.process.pid
            result["baseline_checkpoints"] = receiver.catalog.checkpoints()
            if args.initialize_only:
                result["status"] = "private_readonly_baseline_ready"
                return
            event("private_observer_ready", wait_seconds=300, target_sha256=result["target_sha256"])
            captured = []

            def committed(session, checkpoint, batch, accepted):
                captured.append((checkpoint, batch))
                if len(captured) > 100:
                    raise SocialError("private_acceptance_page_bound")

            receiver.on_committed = committed
            deadline = time.monotonic() + 300
            selected_row = None
            while time.monotonic() < deadline:
                page = await receiver.poll()
                if page["more"]:
                    raise SocialError("private_acceptance_page_bound")
                rows = ledger._db.execute("""SELECT p.message_id,p.sender_id,p.text,b.disposition FROM bridge_message_payloads p
                    JOIN bridge_records b USING(message_id) WHERE p.scope=? AND p.text=?""",
                    (receiver.binding.scope.key(), args.expected_text)).fetchall()
                if len(rows) > 1:
                    raise SocialError("private_acceptance_marker_not_unique")
                if rows:
                    selected_row = rows[0]
                    break
                await asyncio.sleep(2)
            if selected_row is None:
                raise SocialError("private_acceptance_new_marker_timeout")
            if selected_row["sender_id"] != selected["username"] or selected_row["disposition"] != "OBSERVED":
                raise SocialError("private_acceptance_selected_peer_inbound_required")
            selected_batch = next(((point, batch) for point, batch in captured
                                   if any(record.message_id == selected_row["message_id"] for record in batch)), None)
            if not selected_batch:
                raise SocialError("private_acceptance_marker_was_not_new")
            point, batch = selected_batch
            replay = receiver.catalog.store.ingest(receiver.binding, point["shard"], Cursor(**point["start"]), batch)
            result["same_batch_replay_all_not_fresh"] = all(not item["fresh"] for item in replay["records"])
            result["source_message_id"] = selected_row["message_id"]
            result["source_text_sha256"] = hashlib.sha256(selected_row["text"].encode()).hexdigest()
            result["sender_matches_selected_peer"] = True
            result["disposition"] = selected_row["disposition"]
            saved = receiver.catalog.checkpoints()
            await receiver.close()
            result["first_reader_reaped"] = receiver.worker.process.returncode is not None
            resumed = await open_private_receiver(ledger, **options)
            result["resumed_reader_pid"] = resumed.worker.process.pid
            result["checkpoints_unchanged"] = resumed.catalog.checkpoints() == saved
            result["after_restart_poll"] = await resumed.poll()
            wb = WeChatWorkbenchService(ledger, resumed)
            server = WorkbenchConsoleServer(ledger, wb)
            await server.start(0)
            wb.start()
            state = await http_state(server)
            shown = next(m for m in state["messages"] if m["message_id"] == selected_row["message_id"])
            result["http_target_and_source_verified"] = (state["target"] == selected["username"] and state["model"] is None
                and state["send_enabled"] is False and hashlib.sha256(shown["text"].encode()).hexdigest() == result["source_text_sha256"])
            result["source_rows"] = ledger._db.execute("SELECT COUNT(*) FROM bridge_message_payloads WHERE message_id=?", (selected_row["message_id"],)).fetchone()[0]
            if (not result["same_batch_replay_all_not_fresh"] or not result["checkpoints_unchanged"]
                    or not result["http_target_and_source_verified"] or result["source_rows"] != 1
                    or result["after_restart_poll"]["fresh"] != 0):
                raise SocialError("private_acceptance_recovery_incomplete")
            result["status"] = "private_inbound_readonly_restart_http_verified"
        finally:
            if server:
                await server.aclose()
            if wb:
                await wb.aclose()
            else:
                if resumed:
                    await resumed.close()
                if receiver:
                    await receiver.close()
            result["all_readers_reaped"] = all(r.worker.process.returncode is not None for r in (receiver, resumed) if r and r.worker.process)
            result["protected_send_model_and_filehelper_cursors_unchanged"] = audit(ledger._db) == before
            result["active_bindings"] = ledger._db.execute("SELECT COUNT(*) FROM bridge_bindings WHERE active=1").fetchone()[0]
            result["ending_schema"] = ledger._db.execute("PRAGMA user_version").fetchone()[0]
            result["integrity_ok"] = ledger._db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            if not result["protected_send_model_and_filehelper_cursors_unchanged"] or not result["all_readers_reaped"] or result["active_bindings"] or not result["integrity_ok"]:
                raise SocialError("private_acceptance_cleanup_failed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("pid", "hwnd", "created-filetime"):
        parser.add_argument("--" + name, type=int, required=True)
    parser.add_argument("--private-scope-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--initialize-only", action="store_true")
    mode.add_argument("--expected-text")
    args = parser.parse_args()
    if min(args.pid, args.hwnd, args.created_filetime) <= 0 or args.expected_text is not None and not args.expected_text.strip():
        parser.error("current binding and nonempty selected marker required")
    result = dict(status="started", model_calls=0, send_calls=0, started_at=time.time())
    with args.output.open("x", encoding="utf-8") as output:
        def event(kind, **fields):
            print(json.dumps(dict(event=kind, **fields)), flush=True)

        try:
            asyncio.run(asyncio.wait_for(run(args, result, event), 400))
        except BaseException as exc:
            result.update(status="stopped", reason=exc.code if isinstance(exc, SocialError) else type(exc).__name__)
        result["finished_at"] = time.time()
        json.dump(result, output, ensure_ascii=False, indent=2)
        output.flush()
        os.fsync(output.fileno())
    print(json.dumps(result), flush=True)
    return 0 if result["status"] in {"private_readonly_baseline_ready", "private_inbound_readonly_restart_http_verified"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
