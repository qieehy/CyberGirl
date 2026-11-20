"""Explicit keyless active-account check; read-only process and directory metadata."""

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from wechat_agent.bridges.wechat.keyprobe import account_directory, config_identity, remote_bytes
from wechat_agent.social.models import SocialError

if __package__:
    from .inspect_wechat_accessibility import inspect
    from .probe_wechat_database import configured_roots
    from .verify_wechat_accessibility import DLL, NativeMemory
else:
    from inspect_wechat_accessibility import inspect
    from probe_wechat_database import configured_roots
    from verify_wechat_accessibility import DLL, NativeMemory


def read_identity(memory, image, roots):
    sections = memory.sections if hasattr(memory, "sections") else inspect(image)["sections"]
    section = next(s for s in sections if s["name"] == ".data")
    if not 0 < section["virtual_size"] <= 16 * 1024 * 1024:
        raise SocialError("module_data_bound")
    memory.validate()
    data = memory.read_bytes(memory.base + section["rva"], section["virtual_size"])
    username, config = config_identity(memory.read_bytes, memory.base + section["rva"], data)
    del data
    account = account_directory(roots, username)
    memory.validate()
    if remote_bytes(memory.read_bytes, config + 0x48, maximum=128).decode("ascii") != username:
        raise SocialError("account_changed_during_probe")
    return username, account, config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--hwnd", type=int, required=True)
    parser.add_argument("--created-filetime", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--match-account-evidence", type=Path)
    args = parser.parse_args()
    result = {"status": "started", "pid": args.pid, "hwnd": args.hwnd,
              "created_filetime": str(args.created_filetime), "process_writes": 0,
              "keys_read": False, "databases_opened": 0, "model_requests": 0, "external_messages": 0}
    with args.output.open("x", encoding="utf-8") as output:
        memory = NativeMemory(args.pid, hwnd=args.hwnd, created_filetime=args.created_filetime)
        try:
            result["preflight"] = memory.preflight()
            username, _, _ = read_identity(memory, DLL.read_bytes(), configured_roots())
            fingerprint = hashlib.sha256(username.encode()).hexdigest()
            if args.match_account_evidence:
                previous = json.loads(args.match_account_evidence.read_text(encoding="utf-8"))
                if (previous.get("status") != "header_key_verified" or not previous.get("active_account_matched")
                        or previous.get("account_fingerprint") != fingerprint):
                    raise SocialError("account_evidence_mismatch")
                result["matched_previous_database_account"] = True
            result.update(status="active_account_verified", account_fingerprint=fingerprint,
                          unique_account_directory=True, canonical_conversation_verified=False)
        except Exception as exc:
            result.update(status="not_verified", error_type=type(exc).__name__)
            if isinstance(exc, SocialError):
                result["reason"] = exc.code
        finally:
            memory.close()
            result["recorded_at"] = datetime.now(UTC).isoformat()
            root = Path(__file__).resolve().parent.parent
            paths = ["scripts/probe_wechat_account.py", "scripts/probe_wechat_database.py",
                     "scripts/verify_wechat_accessibility.py", "backend/src/wechat_agent/bridges/wechat/keyprobe.py"]
            result["implementation_sha256"] = {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in paths}
            json.dump(result, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
    print(json.dumps(result))
    return 0 if result["status"] == "active_account_verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
