"""Reproducible static identity landmarks for the pinned Weixin DLL. No process IO."""

import argparse
import hashlib
import json
import re
import struct
from datetime import UTC, datetime
from pathlib import Path

from wechat_agent.bridges.wechat.qt_memory import (
    CACHE_GLOBAL_RVA,
    CACHE_INSTANCE_RVA,
    CACHE_VTABLE_RVA,
    INSTANCE_PREFIX,
)

if __package__:
    from .inspect_wechat_accessibility import EXECUTE, inspect
    from .verify_wechat_accessibility import DLL, DLL_SHA
else:
    from inspect_wechat_accessibility import EXECUTE, inspect
    from verify_wechat_accessibility import DLL, DLL_SHA


def inspect_identity(image):
    sections = inspect(image)["sections"]

    def at(rva, size):
        section = next(s for s in sections if s["rva"] <= rva < s["rva"] + s["raw_size"])
        offset = section["file_offset"] + rva - section["rva"]
        if rva + size > section["rva"] + section["raw_size"]:
            raise ValueError("static read crosses section")
        return image[offset:offset + size]

    instance = at(CACHE_INSTANCE_RVA, 0x53)
    if not instance.startswith(INSTANCE_PREFIX):
        raise ValueError("cache instance code changed")
    global_rva = CACHE_INSTANCE_RVA + 12 + struct.unpack_from("<i", instance, 8)[0]
    vtable_rva = CACHE_INSTANCE_RVA + 0x35 + struct.unpack_from("<i", instance, 0x31)[0]
    if global_rva != CACHE_GLOBAL_RVA or vtable_rva != CACHE_VTABLE_RVA:
        raise ValueError("cache landmark mismatch")
    format_rvas = []
    for section in sections:
        cursor, end = section["file_offset"], section["file_offset"] + section["raw_size"]
        while (cursor := image.find(b"session_item_%1\0", cursor, end)) >= 0:
            format_rvas.append(section["rva"] + cursor - section["file_offset"])
            cursor += 15
    references = []
    for section in sections:
        if not section["characteristics"] & EXECUTE:
            continue
        cursor, end = section["file_offset"], section["file_offset"] + section["raw_size"]
        while (cursor := image.find(b"\x8d", cursor, end)) >= 0:
            if cursor + 6 <= end and image[cursor + 1] & 0xC7 == 5:
                rva = section["rva"] + cursor - section["file_offset"]
                if rva + 6 + struct.unpack_from("<i", image, cursor + 2)[0] in format_rvas:
                    references.append(rva)
            cursor += 1
    property_hash = "04035e01ae7a05331cedb2010f9824da9b65dbcb0fb568f468bb6d6b07174a56"
    property_names = []
    for section in sections:
        if section["name"] not in {".rdata", ".data"}:
            continue
        raw = image[section["file_offset"]:section["file_offset"] + section["raw_size"]]
        for match in re.finditer(rb"(?<![A-Za-z0-9_])[A-Za-z_][A-Za-z0-9_.]{0,127}\0", raw):
            name = match.group()[:-1]
            if hashlib.sha256(name).hexdigest() == property_hash:
                property_names.append({"static_name": name.decode("ascii"), "rva": section["rva"] + match.start()})
    return {"cache_global_rva": global_rva, "cache_vtable_rva": vtable_rva,
            "cache_instance_rva": CACHE_INSTANCE_RVA, "session_format_rvas": format_rvas,
            "session_format_xref_candidates": references, "observed_property_static_matches": property_names,
            "process_memory_accessed": False, "canonical_conversation_verified": False,
            "interpretation": "Pinned static landmarks; format and interface locations are not business identity evidence."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    image = DLL.read_bytes()
    if hashlib.sha256(image).hexdigest() != DLL_SHA:
        parser.error("pinned binary changed")
    result = inspect_identity(image)
    result.update(dll_sha256=DLL_SHA, recorded_at=datetime.now(UTC).isoformat(),
                  script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
        output.write("\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
