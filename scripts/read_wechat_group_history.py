"""One selected group from authenticated local snapshots; no UI or send operations."""

# ruff: noqa: E402
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from scripts.wechat_42013_profile import activate

activate()
from scripts.verify_wechat_accessibility import Page
from wechat_reader_source import ReaderSource

from wechat_agent.bridges.wechat.contracts import WindowBinding
from wechat_agent.bridges.wechat.keyprobe import verify_page_key
from wechat_agent.bridges.wechat.snapshot import open_memory_snapshot
from wechat_agent.bridges.wechat.wcdb_probe import scan_selected_page
from wechat_agent.workbench.group_history_reader import read_selected_group


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("pid", "hwnd", "created-filetime"):
        parser.add_argument("--" + name, type=int, required=True)
    parser.add_argument("--expected", required=True)
    parser.add_argument("--group", required=True)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--kind", choices=("group", "dm"), default="group")
    parser.add_argument("--expected-id")
    args = parser.parse_args()
    from scripts.verify_wechat_accessibility import DLL_SHA, EXE_SHA

    source = None
    connections, keys = [], []
    try:
        source = ReaderSource(
            WindowBinding(args.pid, args.created_filetime, args.hwnd, EXE_SHA, DLL_SHA),
            args.expected,
        )

        def open_selected(path):
            with path.open("rb") as handle:
                page = handle.read(4096)
            key = next((k for k in keys if verify_page_key(k, page)), None)
            if key is None:
                key, _, _ = scan_selected_page(source.memory, Page, source.pairs, page)
                keys.append(key)
            return open_shard(path, key, page[:16])

        def open_shard(path, key, salt):
            connection, _ = open_memory_snapshot(path, key, salt)
            connections.append(connection)
            return connection

        result = read_selected_group(source, open_selected, open_shard, args.group, args.limit,
                                     kind=args.kind, expected_id=args.expected_id)
        result["account_fingerprint"] = hashlib.sha256(source.username.encode()).hexdigest()
        print(json.dumps(result, ensure_ascii=False), flush=True)
    except Exception as exc:
        print(json.dumps({"error": getattr(exc, "code", "group_history_unavailable")}), flush=True)
    finally:
        for connection in connections:
            connection.close()
        if source:
            source.close()


if __name__ == "__main__":
    main()
