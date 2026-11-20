"""Bounded active-account/filehelper probe. Never log keys, paths or message bodies."""

import argparse
import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from wechat_agent.bridges.wechat.keyprobe import (
    account_directory,
    config_identity_and_key,
    derive_page_key,
    message_database,
    remote_bytes,
    xor_material,
)
from wechat_agent.bridges.wechat.wcdb_probe import config_pairs, scan_selected_page
from wechat_agent.social.models import SocialError

if __package__:
    from .inspect_wechat_accessibility import inspect
    from .verify_wechat_accessibility import DLL, DLL_SHA, EXE_SHA, NativeMemory, Page
else:
    from inspect_wechat_accessibility import inspect
    from verify_wechat_accessibility import DLL, DLL_SHA, EXE_SHA, NativeMemory, Page


def configured_roots():
    """Read only path configuration, never key caches or account history."""
    roots = {Path(os.environ["USERPROFILE"]) / "Documents/xwechat_files"}
    folder = Path(os.environ["APPDATA"]) / "Tencent/xwechat/config"
    files = sorted(folder.glob("*.ini"))
    if len(files) > 20:
        raise SocialError("config_inventory_bound")
    for path in files:
        if path.stat().st_size > 8192:
            continue
        data = path.read_bytes()
        for encoding in ("utf-8-sig", "utf-16", "gbk"):
            try:
                content = data.decode(encoding).strip("\x00\r\n \t")
            except UnicodeError:
                continue
            match = re.fullmatch(r"[A-Za-z]:[\\/][^\x00\r\n]+", content)
            if match:
                root = Path(match[0])
                roots.update((root, root / "xwechat_files"))
                break
    return roots


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--hwnd", type=int, required=True)
    parser.add_argument("--created-filetime", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--key-source", choices=("cfg", "wcdb"), default="cfg")
    parser.add_argument("--shard", default="message_0.db")
    parser.add_argument("--filehelper-baseline", action="store_true",
                        help="Authenticate an in-memory shard snapshot and query only the filehelper watermark")
    parser.add_argument("--after", type=Path, help="Read filehelper records after this verified baseline evidence")
    parser.add_argument("--expect-marker", help="Exact synthetic marker; never written into diagnostic output")
    args = parser.parse_args()
    if bool(args.after) != bool(args.expect_marker) or (args.after and args.filehelper_baseline):
        parser.error("--after and --expect-marker are required together and exclude --filehelper-baseline")
    if args.expect_marker and not re.fullmatch(r"W2A-[A-Za-z0-9-]{1,64}", args.expect_marker):
        parser.error("expected marker must be a bounded W2A test marker")
    result = {"status": "started", "stage": "preflight", "pid": args.pid, "hwnd": args.hwnd,
              "created_filetime": str(args.created_filetime), "process_write_attempts": 0,
              "key_files_written": 0, "plaintext_database_files_written": 0,
              "chat_rows_read": 0, "external_messages": 0, "model_requests": 0}
    result["key_source"] = args.key_source
    with args.output.open("x", encoding="utf-8") as output:
        memory = NativeMemory(args.pid, hwnd=args.hwnd, created_filetime=args.created_filetime)
        try:
            result["preflight"] = memory.preflight()
            image = DLL.read_bytes()
            result["stage"] = "static_master_pattern"
            material = xor_material(image)
            sections = inspect(image)["sections"]
            section = next(s for s in sections if s["name"] == ".data")
            if section["virtual_size"] > 16 * 1024 * 1024:
                raise SocialError("module_data_bound")
            result["stage"] = "bound_config_read"
            memory.validate()
            data = memory.read_bytes(memory.base + section["rva"], section["virtual_size"])
            username, master, config = config_identity_and_key(memory.read_bytes, memory.base + section["rva"], data, material)
            del data, material
            result["stage"] = "active_account_match"
            account = account_directory(configured_roots(), username)
            path = message_database(account, args.shard)
            result.update(active_account_matched=True, account_fingerprint=hashlib.sha256(username.encode()).hexdigest())
            result["stage"] = "selected_shard_header"
            with path.open("rb") as handle:
                page = handle.read(4096)
            if args.key_source == "cfg":
                key = derive_page_key(master, page)
                salt = page[:16]
            else:
                result["stage"] = "selected_wcdb_config"
                pairs = config_pairs(image, sections, memory.base)
                key, salt, stats = scan_selected_page(memory, Page, pairs, page)
                result["scan"] = stats
            del image
            memory.validate()
            if remote_bytes(memory.read_bytes, config + 0x48, maximum=128).decode("ascii") != username:
                raise SocialError("account_changed_during_probe")
            with path.open("rb") as handle:
                if handle.read(4096) != page:
                    raise SocialError("database_header_changed_during_probe")
            if args.filehelper_baseline or args.after:
                from wechat_agent.bridges.wechat.reader import baseline, read_new
                from wechat_agent.bridges.wechat.snapshot import open_memory_snapshot

                result["stage"] = "memory_snapshot"
                connection, stats = open_memory_snapshot(path, key, salt)
                try:
                    result["snapshot"] = stats
                    result["selected_conversation"] = "filehelper"
                    if args.filehelper_baseline:
                        result["stage"] = "filehelper_baseline"
                        position = baseline(connection, "filehelper")
                        result["filehelper_baseline"] = {"sort_seq": position.sort_seq, "local_id": position.local_id}
                        result["message_bodies_queried"] = 0
                    else:
                        from wechat_agent.bridges.wechat.contracts import (
                            AccountBinding,
                            ConversationBinding,
                            Cursor,
                            WindowBinding,
                        )
                        from wechat_agent.bridges.wechat.store import BridgeStore
                        from wechat_agent.social.ledger import Ledger

                        result["stage"] = "filehelper_new_records"
                        prior = json.loads(args.after.read_text(encoding="utf-8"))
                        account_fp = hashlib.sha256(username.encode()).hexdigest()
                        epoch = hashlib.sha256(page[:16]).hexdigest()
                        if (prior.get("status") != "header_key_verified" or prior.get("selected_conversation") != "filehelper"
                                or prior.get("account_fingerprint") != account_fp or prior.get("db_epoch_fingerprint") != epoch
                                or prior.get("shard") != args.shard or "filehelper_baseline" not in prior):
                            raise SocialError("baseline_binding_mismatch")
                        start = Cursor(**prior["filehelper_baseline"])
                        window = WindowBinding(args.pid, args.created_filetime, args.hwnd, EXE_SHA, DLL_SHA)
                        binding = ConversationBinding(AccountBinding(username, epoch, username, window, "w2a-selected-shard-hmac"),
                                                      "filehelper", "dm", "w2a-canonical-table")
                        records = read_new(connection, binding, args.shard, start, datetime.now(UTC).timestamp())
                        result["chat_rows_read"] = len(records)
                        result["message_bodies_queried"] = len(records)
                        matches = [record for record in records if record.text == args.expect_marker]
                        if len(matches) != 1:
                            raise SocialError("test_marker_not_unique_in_new_batch")
                        memory.validate()
                        if remote_bytes(memory.read_bytes, config + 0x48, maximum=128).decode("ascii") != username:
                            raise SocialError("account_changed_during_probe")
                        ledger_path = Path(".state/social/wechat-reader-poc.sqlite3")
                        with Ledger(ledger_path) as ledger:
                            store = BridgeStore(ledger)
                            store.bind(binding)
                            current = store.baseline(binding, args.shard, start)
                            if current not in (start, records[-1].position):
                                raise SocialError("poc_cursor_changed")
                            ingested = store.ingest(binding, args.shard, start, records)
                            replay = store.ingest(binding, args.shard, start, records)
                            result["ledger_counts"] = ledger.counts()
                            result["duplicate_replay_fresh_records"] = sum(r["fresh"] for r in replay["records"])
                            result["source_dispositions"] = [r["disposition"] for r in ingested["records"]]
                        with Ledger(ledger_path) as ledger:
                            store = BridgeStore(ledger)
                            store.bind(binding)
                            result["cursor_survived_reopen"] = store.cursor(binding, args.shard) == records[-1].position
                            result["reopen_counts"] = ledger.counts()
                        result["marker_verified"] = True
                        result["marker_sender_is_self"] = matches[0].sender_id == username
                finally:
                    connection.close()
                memory.validate()
                if remote_bytes(memory.read_bytes, config + 0x48, maximum=128).decode("ascii") != username:
                    raise SocialError("account_changed_during_probe")
            # Hashes identify evidence without logging account names, paths or keys.
            result.update(status="header_key_verified", stage="complete", active_account_matched=True,
                          account_fingerprint=hashlib.sha256(username.encode()).hexdigest(),
                          db_epoch_fingerprint=hashlib.sha256(page[:16]).hexdigest(),
                          shard=args.shard, shard_size_bytes=path.stat().st_size,
                          wal_present=Path(str(path) + "-wal").exists(), header_hmac_verified=True)
            del master, key
        except Exception as exc:
            result.update(status="not_verified", error_type=type(exc).__name__)
            if isinstance(exc, SocialError):
                result["reason"] = exc.code
            # Never output arbitrary exceptions, remote values, paths or keys.
        finally:
            memory.close()
            result["recorded_at"] = datetime.now(UTC).isoformat()
            result["script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
            root = Path(__file__).resolve().parent.parent
            components = ["scripts/verify_wechat_accessibility.py", "scripts/inspect_wechat_accessibility.py",
                          "backend/src/wechat_agent/bridges/wechat/keyprobe.py", "backend/src/wechat_agent/bridges/wechat/wcdb_probe.py",
                          "backend/src/wechat_agent/bridges/wechat/snapshot.py", "backend/src/wechat_agent/bridges/wechat/reader.py"]
            result["implementation_sha256"] = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in components}
            output.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            output.flush()
            os.fsync(output.fileno())
        print(json.dumps(result))
    return 0 if result["status"] == "header_key_verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
