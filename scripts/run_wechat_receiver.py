"""Explicit filehelper reception with a parent ledger and owned reader child."""

import argparse
import asyncio
import hashlib
import json
import re
from pathlib import Path

from verify_wechat_accessibility import DLL_SHA, EXE_SHA

from wechat_agent.bridges.wechat.contracts import Cursor, WindowBinding, digest
from wechat_agent.bridges.wechat.receive_process import JsonReaderWorker, ReaderSession
from wechat_agent.bridges.wechat.receive_state import fingerprint
from wechat_agent.bridges.wechat.send_process import python_worker_command
from wechat_agent.social.ledger import Ledger
from wechat_agent.social.models import SocialError


async def run(args, result):
    window = WindowBinding(args.pid, args.created_filetime, args.hwnd, EXE_SHA, DLL_SHA)
    command = python_worker_command(Path(__file__).with_name("wechat_reader_worker.py"))
    marker_ids = set()

    def committed(session, checkpoint, records, accepted):
        result["records_committed"] += sum(r["fresh"] for r in accepted["records"])
        matches = [r for r in records if args.expect_marker and r.text == args.expect_marker]
        for record in matches:
            if record.sender_id != session.binding.account.self_id:
                raise SocialError("acceptance_marker_sender_mismatch")
            marker_ids.add(record.message_id)
        if matches and args.verify_replay_once and not result["replay_verified"]:
            replay = session.catalog.store.ingest(session.binding, checkpoint["shard"],
                Cursor(**checkpoint["start"]), records)
            if any(r["fresh"] for r in replay["records"]) or replay["cursor"] != accepted["cursor"]:
                raise SocialError("acceptance_replay_failed")
            result["replay_verified"] = True
        result["marker_matches"] = len(marker_ids)

    with Ledger(args.ledger) as ledger:
        session = ReaderSession(ledger, JsonReaderWorker(command), window, args.account_fingerprint,
                                on_committed=committed)
        try:
            initial = await session.start(initialize=args.baseline_now)
            result["shards"] = len(initial)
            result["authenticated_snapshots"] = True
            result["start_checkpoint_sha256"] = digest(initial)
            epoch = session.binding.account.db_epoch
            print(json.dumps({"status": "baseline_ready", "shards": len(initial),
                              "reader_pid": session.worker.process.pid}), flush=True)
            for index in range(args.rounds):
                progress = await session.poll()
                result["polls"] += 1
                if progress["fresh"]:
                    print(json.dumps({"status": "new_records_committed", "count": progress["fresh"],
                                      "marker_matches": len(marker_ids)}), flush=True)
                if marker_ids and args.restart_after_marker and not result["restart_verified"]:
                    before = session.catalog.checkpoints()
                    old_pid = session.worker.process.pid
                    await session.close()
                    result["first_child_stopped"] = session.worker.process.returncode is not None
                    session = ReaderSession(ledger, JsonReaderWorker(command), window, args.account_fingerprint,
                                            on_committed=committed)
                    after = await session.start()
                    if (before != after or session.binding.account.db_epoch != epoch
                            or old_pid == session.worker.process.pid):
                        raise SocialError("acceptance_restart_failed")
                    result["restart_verified"] = True
                    result["restored_checkpoint_sha256"] = digest(after)
                    await session.poll()
                    if any(ledger._db.execute("SELECT COUNT(*) FROM bridge_records WHERE message_id=?", (key,)).fetchone()[0] != 1
                           for key in marker_ids):
                        raise SocialError("acceptance_marker_duplicate")
                    break
                if index + 1 < args.rounds:
                    await asyncio.sleep(args.interval)
            result["end_checkpoint_sha256"] = digest(session.catalog.checkpoints())
            result["bridge_records_total"] = ledger._db.execute("SELECT COUNT(*) FROM bridge_records").fetchone()[0]
            result["outbox_total"] = ledger._db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
            result["status"] = "completed"
            if args.expect_marker and len(marker_ids) != 1:
                raise SocialError("acceptance_marker_not_unique_or_absent")
            if args.restart_after_marker and not result["restart_verified"]:
                raise SocialError("acceptance_restart_missing")
        finally:
            result["last_child_stopped"] = await session.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--hwnd", type=int, required=True)
    parser.add_argument("--created-filetime", type=int, required=True)
    parser.add_argument("--account-fingerprint", required=True)
    parser.add_argument("--ledger", type=Path, default=Path(".state/social/wechat-receiver.sqlite3"))
    parser.add_argument("--baseline-now", action="store_true", help="仅首次建立当前尾部基线；已有游标不会重置")
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--interval", type=float, default=2)
    parser.add_argument("--expect-marker", help="只接受 W2A 测试标记，证据仅记录哈希")
    parser.add_argument("--verify-replay-once", action="store_true")
    parser.add_argument("--restart-after-marker", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.rounds <= 1000 or not 1 <= args.interval <= 60:
        parser.error("读取轮数或间隔超出范围")
    if args.expect_marker and not re.fullmatch(r"W2A-[A-Za-z0-9-]{1,64}", args.expect_marker):
        parser.error("验收标记格式不正确")
    if (args.verify_replay_once or args.restart_after_marker) and not args.expect_marker:
        parser.error("重放/重启验收需要明确的测试标记")
    fingerprint(args.account_fingerprint)
    result = {"status": "started", "account_fingerprint": args.account_fingerprint,
              "scope": "filehelper", "polls": 0, "records_committed": 0, "marker_matches": 0,
              "replay_verified": False, "restart_verified": False,
              "model_requests": 0, "external_messages": 0, "process_writes": 0,
              "key_files_written": 0, "plaintext_database_files_written": 0}
    if args.expect_marker:
        result["marker_sha256"] = hashlib.sha256(args.expect_marker.encode()).hexdigest()
    # Reserve the diagnostic destination before native reads; never overwrite evidence.
    with args.output.open("x", encoding="utf-8") as output:
        try:
            asyncio.run(run(args, result))
        except BaseException as exc:
            result["status"] = "stopped"
            result["reason"] = exc.code if isinstance(exc, SocialError) else "reader_run_failed"
        json.dump(result, output, ensure_ascii=False, indent=2)
        output.write("\n")
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
