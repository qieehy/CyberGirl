"""Replay the pinned Qt metadata/dispatch path for the current session key.

Static DLL bytes only. Does not load the DLL, call a client method or access a
process. This establishes the meaning of a candidate field, not a live target.
"""

import argparse
import hashlib
import json
import struct
from datetime import UTC, datetime
from pathlib import Path

from wechat_agent.bridges.wechat.qt_memory import (
    MESSAGE_KEY_ACCESS,
    MESSAGE_KEY_METHOD_RVA,
    MESSAGE_KEY_OFFSET,
    MESSAGE_PAGE_META_RVA,
    MESSAGE_PAGE_VTABLE_RVA,
)

if __package__:
    from .inspect_wechat_accessibility import inspect
    from .verify_wechat_accessibility import DLL, DLL_SHA
else:
    from inspect_wechat_accessibility import inspect
    from verify_wechat_accessibility import DLL, DLL_SHA


def inspect_session_key(image):
    if hashlib.sha256(image).hexdigest() != DLL_SHA:
        raise ValueError("pinned binary changed")
    sections = inspect(image)["sections"]
    preferred = 0x180000000

    def at(rva, size):
        section = next(s for s in sections if s["rva"] <= rva < s["rva"] + s["raw_size"])
        if not 0 < size <= 4096 or rva + size > section["rva"] + section["raw_size"]:
            raise ValueError("static read bound")
        start = section["file_offset"] + rva - section["rva"]
        return image[start:start + size]

    _, strings, metadata, dispatch, _, _ = struct.unpack("<6Q", at(MESSAGE_PAGE_META_RVA, 48))
    strings -= preferred
    metadata -= preferred

    def string(index):
        if not 0 <= index < 256:
            raise ValueError("metadata string bound")
        header_at = strings + index * 24
        header = at(header_at, 24)
        length = struct.unpack_from("<i", header, 4)[0]
        offset = struct.unpack_from("<q", header, 16)[0]
        if not 1 <= length <= 128:
            raise ValueError("metadata text bound")
        return at(header_at + offset, length).decode("ascii")

    header = struct.unpack("<14I", at(metadata, 56))
    method_count, method_offset = header[4:6]
    if string(header[1]) != "mmui::ChatMessagePage" or method_count != 14:
        raise ValueError("chat page metadata changed")
    method = struct.unpack("<5I", at(metadata + (method_offset + 8 * 5) * 4, 20))
    name, argc, parameters, _, _ = method
    signature = struct.unpack("<2I", at(metadata + parameters * 4, 8))
    if argc != 1 or not signature[1] & 0x80000000:
        raise ValueError("session argument changed")
    if string(name) != "OnNewSessionDataLoaded" or string(signature[1] & 0x7FFFFFFF) != "std::string":
        raise ValueError("session slot changed")
    dispatch_rva = dispatch - preferred
    table = 0x91643C4
    branch = table + struct.unpack("<i", at(table + 8 * 4, 4))[0]
    code = at(branch, 16)
    if code[:12] != bytes.fromhex("498b51084883c4405f5e5de9"):
        raise ValueError("session dispatch changed")
    target = branch + 16 + struct.unpack_from("<i", code, 12)[0]
    if target != MESSAGE_KEY_METHOD_RVA or at(target + 14, len(MESSAGE_KEY_ACCESS)) != MESSAGE_KEY_ACCESS:
        raise ValueError("session key field changed")
    method_bytes = at(target, 160)
    # The key at +0x1d0 is compared to the slot argument's size and bytes. The
    # equal branch passes that key to the pinned session-kind classifier.
    equality = bytes.fromhex("4c39c0757a4d85c07409e89077bf05")
    if method_bytes[0x41:0x41 + len(equality)] != equality:
        raise ValueError("session equality branch changed")
    return {"dll_sha256": DLL_SHA, "class_name": string(header[1]),
            "meta_object_rva": MESSAGE_PAGE_META_RVA, "vtable_rva": MESSAGE_PAGE_VTABLE_RVA,
            "slot": "OnNewSessionDataLoaded(std::string)", "slot_index": 8,
            "static_metacall_rva": dispatch_rva, "dispatch_branch_rva": branch,
            "method_rva": target, "session_key_offset": MESSAGE_KEY_OFFSET,
            "method_prefix_sha256": hashlib.sha256(method_bytes).hexdigest(),
            "field_access_signature_verified": True, "argument_equality_verified": True,
            "interpretation": "Current page session key; equality and session-kind classifier usage statically traced.",
            "process_memory_accessed": False, "canonical_conversation_verified": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = inspect_session_key(DLL.read_bytes())
    result.update(recorded_at=datetime.now(UTC).isoformat(),
                  script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(result, output, indent=2)
        output.write("\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
