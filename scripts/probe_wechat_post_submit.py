"""Read-only diagnostics. Never focus, type, submit, clear or unblock.

UI patterns expose hashes/counts only; database mode reads the original two
selected filehelper shards and lists other shard file metadata without opening
their contents. The sender ledger is opened in SQLite read-only mode.
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from wechat_agent.bridges.wechat.contracts import (
    AccountBinding,
    ConversationBinding,
    Cursor,
    WindowBinding,
)
from wechat_agent.bridges.wechat.reader import conversation_table
from wechat_agent.bridges.wechat.send_process import JsonSendWorker
from wechat_agent.bridges.wechat.sender import AccessibilityLease
from wechat_agent.social.models import OutgoingAction, SocialError

if __package__:
    from .probe_wechat_account import read_identity
    from .probe_wechat_database import configured_roots
    from .probe_wechat_native_sender import LEDGER, MARKER, worker_command
    from .verify_wechat_accessibility import DLL, DLL_SHA, EXE_SHA, NativeMemory
    from .wechat_filehelper_source import FilehelperSource
else:
    from probe_wechat_account import read_identity
    from probe_wechat_database import configured_roots
    from probe_wechat_native_sender import LEDGER, MARKER, worker_command
    from verify_wechat_accessibility import DLL, DLL_SHA, EXE_SHA, NativeMemory
    from wechat_filehelper_source import FilehelperSource


async def ui_diagnostic(window, fingerprint, record, *, kind="ui"):
    memory = NativeMemory(window.pid, activate=True, hwnd=window.hwnd, created_filetime=window.created_filetime)
    lease = AccessibilityLease(memory, window, record)
    worker = JsonSendWorker(worker_command(window, fingerprint) + ("--read-only",))
    result = {}
    try:
        async with asyncio.timeout(25):
            lease.open()
            username, _, _ = read_identity(memory, DLL.read_bytes(), configured_roots())
            if hashlib.sha256(username.encode()).hexdigest() != fingerprint:
                raise SocialError("diagnostic_account_mismatch")
            binding = ConversationBinding(AccountBinding(username, "readonly-no-database", username, window, "w2a-account-hmac"),
                                          "filehelper", "dm", "w2b4-native-page")
            action = OutgoingAction("readonly-draft-diagnostic", binding.scope, "local-readonly", MARKER)
            await worker.start("readonly-draft-diagnostic", binding, action)
            if kind == "connection":
                data = await worker._rpc("connection_diagnostics")
                result.update(status="clicked_connection_observed", connection=data["connection_diagnostics"])
            elif kind == "geometry":
                data = await worker._rpc("geometry_diagnostics")
                result.update(status="geometry_observed", geometry=data["geometry_diagnostics"])
            elif kind == "button":
                data = await worker._rpc("button_diagnostics")
                result.update(status="button_interface_locations_observed", button_dispatch=data["button_diagnostics"])
            else:
                data = await worker._rpc("draft_diagnostics")
                result.update(status="draft_patterns_observed", **data["draft_diagnostics"])
    except Exception as exc:
        result.update(status="not_verified", error_type=type(exc).__name__)
        if isinstance(exc, SocialError):
            result["reason"] = exc.code
    finally:
        try:
            reaped = await worker.stop()
        except Exception:
            reaped = False
        result.update(worker_reaped=reaped, restore_verified=lease.close(restore=reaped))
        if not reaped or not result["restore_verified"]:
            result.update(status="not_verified", reason="diagnostic_cleanup_unverified")
    return result


def shard_inventory(folder):
    """Directory metadata only, fixed account; never open new shard contents."""
    entries = list(folder.iterdir())
    if len(entries) > 128:
        raise SocialError("shard_directory_metadata_bound")
    result = []
    for path in sorted(entries):
        if not re.fullmatch(r"message_\d{1,3}\.db", path.name):
            continue
        if not path.resolve().is_relative_to(folder.resolve()) or not path.is_file():
            raise SocialError("shard_metadata_path_changed")
        row = dict(shard=path.name, size=path.stat().st_size, modified_ns=path.stat().st_mtime_ns)
        wal = path.with_name(path.name + "-wal")
        if not wal.resolve().is_relative_to(folder.resolve()):
            raise SocialError("shard_wal_metadata_path_changed")
        row["wal_present"] = wal.is_file()
        if row["wal_present"]:
            row.update(wal_size=wal.stat().st_size, wal_modified_ns=wal.stat().st_mtime_ns)
        result.append(row)
    return result


def audit_filehelper_cursor(connection, start):
    """Only local row IDs/order metadata in filehelper; never select bodies."""
    table = conversation_table(connection, "filehelper")
    maximum = connection.execute(f'SELECT MAX(local_id) FROM "{table}"').fetchone()[0] or 0
    rows = connection.execute(f'SELECT local_id,sort_seq FROM "{table}" WHERE local_id>? ORDER BY local_id LIMIT 101',
                              (start.local_id,)).fetchall()
    if len(rows) > 100:
        raise SocialError("cursor_metadata_audit_bound")
    positions = [Cursor(sequence, local_id) for local_id, sequence in rows]
    return dict(max_local_id=maximum, new_local_ids_count=len(rows),
                new_local_ids_below_sort_watermark=sum(position < start for position in positions),
                new_local_id_positions=[asdict(position) for position in positions])


def database_diagnostic(window, fingerprint):
    # No Ledger constructor/migration or mutations in this diagnostic.
    with sqlite3.connect(LEDGER.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("""SELECT s.*,b.payload FROM wechat_send_operations s
            JOIN outbox o USING(action_id) JOIN bridge_bindings b ON b.scope=s.scope
            WHERE s.input_permitted=1 AND s.submit_permitted=1 AND s.finished=1 AND o.text=?""", (MARKER,)).fetchall()
        if len(rows) != 1:
            raise SocialError("diagnostic_original_send_not_unique")
        original = rows[0]
        starts = {name: Cursor(*position) for name, position in json.loads(original["baselines"]).items()}
        generation = original["generation"]
    source = FilehelperSource(window, fingerprint)
    try:
        if source.binding.generation != generation:
            raise SocialError("diagnostic_original_source_generation_changed")
        positions = source.baselines()
        records = source.records(starts)
        audit = source._query(lambda connection, name: audit_filehelper_cursor(connection, starts[name]))
        matches = [row for row in records if row.text == MARKER and row.sender_id == source.binding.account.self_id]
        folder = next(iter(source.shards.values()))[0].parent
        inventory = shard_inventory(folder)
        source.validate()
        return dict(status="selected_echo_diagnostic_complete", selected_shards=list(source.shards),
                    original_baselines={name: asdict(cursor) for name, cursor in starts.items()},
                    current_positions={name: asdict(cursor) for name, cursor in positions.items()},
                    new_records_read=len(records), exact_self_marker_matches=len(matches),
                    unique_self_echo_observed=len({row.message_id for row in matches}) == 1,
                    cursor_metadata_audit=audit, snapshots=source.latest_snapshot_stats,
                    shard_file_metadata=inventory, unselected_shard_contents_opened=0,
                    sender_ledger_writes=0)
    finally:
        source.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--hwnd", type=int, required=True)
    parser.add_argument("--created-filetime", type=int, required=True)
    parser.add_argument("--match-account-evidence", type=Path, required=True)
    parser.add_argument("--mode", choices=("ui", "button", "geometry", "connection", "database"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    previous = json.loads(args.match_account_evidence.read_text(encoding="utf-8"))
    if (previous.get("status") != "header_key_verified" or not previous.get("active_account_matched")
            or not previous.get("header_hmac_verified")):
        parser.error("authenticated account evidence required")
    journal_path = args.output.with_suffix(".journal.jsonl")
    if args.output.exists() or journal_path.exists():
        parser.error("evidence exists")
    window = WindowBinding(args.pid, args.created_filetime, args.hwnd, EXE_SHA, DLL_SHA)
    result = dict(status="started", mode=args.mode, input_actions=0, submit_actions=0, focus_actions=0,
                  model_requests=0, sender_ledger_writes=0, keys_written=0, plaintext_database_files_written=0)
    with args.output.open("x", encoding="utf-8") as output, journal_path.open("x", encoding="utf-8") as journal:
        def record(stage, original):
            journal.write(json.dumps(dict(stage=stage, original=original, at=datetime.now(UTC).isoformat())) + "\n")
            journal.flush()
            os.fsync(journal.fileno())

        try:
            fingerprint = previous["account_fingerprint"]
            result.update(asyncio.run(ui_diagnostic(window, fingerprint, record, kind=args.mode)) if args.mode != "database"
                          else database_diagnostic(window, fingerprint))
        except Exception as exc:
            result.update(status="not_verified", error_type=type(exc).__name__)
            if isinstance(exc, SocialError):
                result["reason"] = exc.code
        finally:
            result["recorded_at"] = datetime.now(UTC).isoformat()
            root = Path(__file__).resolve().parent.parent
            files = ["scripts/probe_wechat_post_submit.py", "scripts/wechat_native_worker.py", "scripts/wechat_native_ui.ps1",
                     "scripts/wechat_input_metadata.ps1", "scripts/wechat_click_geometry.ps1", "scripts/wechat_filehelper_source.py",
                     "backend/src/wechat_agent/bridges/wechat/click_geometry.py", "backend/src/wechat_agent/bridges/wechat/directed_click.py",
                     "backend/src/wechat_agent/bridges/wechat/qt_connections.py",
                     "backend/src/wechat_agent/bridges/wechat/native_sender.py", "backend/src/wechat_agent/bridges/wechat/send_worker.py",
                     "backend/src/wechat_agent/bridges/wechat/qt_memory.py", "scripts/verify_wechat_accessibility.py"]
            result["implementation_sha256"] = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in files}
            json.dump(result, output, indent=2)
            output.write("\n")
    print(json.dumps(result))
    return 0 if result["status"] in {"draft_patterns_observed", "selected_echo_diagnostic_complete", "geometry_observed",
                                   "clicked_connection_observed", "button_interface_locations_observed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
