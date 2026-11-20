"""Static PE inspection only; never import upstream code or open a process.

The signature and ranking criterion are documented in wechatauto-replica's
Apache-2.0 accessibility.py at 6e603974bbac3bda93d88bbd6995510ae2547f5c.
This independent reader reports hypotheses, not verified runtime addresses.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from datetime import UTC, datetime
from pathlib import Path

SIGNATURE = re.compile(rb"\x48\x85\xc9\x0f\x84....\x80\x3d(?P<disp>.{4})\x00\x0f\x84", re.S)
EXECUTE = 0x20000000
WRITE = 0x80000000


def inspect(data: bytes) -> dict:
    if len(data) < 64 or data[:2] != b"MZ":
        raise ValueError("not a DOS/PE image")
    pe = struct.unpack_from("<I", data, 60)[0]
    if pe + 24 > len(data) or data[pe : pe + 4] != b"PE\0\0":
        raise ValueError("invalid PE header")
    machine, count = struct.unpack_from("<HH", data, pe + 4)
    optional_size = struct.unpack_from("<H", data, pe + 20)[0]
    table = pe + 24 + optional_size
    if machine != 0x8664 or optional_size < 112 or not 1 <= count <= 96:
        raise ValueError("expected bounded x64 PE32+ image")
    if table + count * 40 > len(data) or struct.unpack_from("<H", data, pe + 24)[0] != 0x20B:
        raise ValueError("invalid section table/optional header")
    image_size = struct.unpack_from("<I", data, pe + 24 + 56)[0]
    sections = []
    for i in range(count):
        off = table + 40 * i
        vsize, rva, raw_size, raw_ptr = struct.unpack_from("<IIII", data, off + 8)
        chars = struct.unpack_from("<I", data, off + 36)[0]
        if raw_size and raw_ptr + raw_size > len(data):
            raise ValueError("section outside file")
        if rva + vsize > image_size:
            raise ValueError("section outside image")
        sections.append({"name": data[off : off + 8].rstrip(b"\0").decode("ascii"),
                         "rva": rva, "virtual_size": vsize, "raw_size": raw_size,
                         "file_offset": raw_ptr, "characteristics": chars})

    def file_rva(offset):
        for sec in sections:
            delta = offset - sec["file_offset"]
            if 0 <= delta < sec["raw_size"]:
                return sec["rva"] + delta
        return None

    core_rvas = set()
    cursor = 0
    while (cursor := data.find(b"qt.accessibility.core\0", cursor)) >= 0:
        rva = file_rva(cursor)
        if rva is not None:
            core_rvas.add(rva)
        cursor += 1

    xrefs = set()
    matches = []
    for sec in sections:
        if not sec["characteristics"] & EXECUTE:
            continue
        start = sec["file_offset"]
        end = start + sec["raw_size"]
        cursor = start
        while (cursor := data.find(b"\x8d", cursor, end)) >= 0:
            if cursor + 6 <= end and data[cursor + 1] & 0xC7 == 0x05:
                lea_rva = sec["rva"] + cursor - start
                target = lea_rva + 6 + struct.unpack_from("<i", data, cursor + 2)[0]
                if target in core_rvas:
                    xrefs.add(lea_rva)
                    if cursor > start and 0x40 <= data[cursor - 1] <= 0x4F:
                        xrefs.add(lea_rva - 1)
            cursor += 1
        for match in SIGNATURE.finditer(data, start, end):
            # CMP byte [RIP+disp32], 0 ends one byte after its displacement.
            insn_rva = sec["rva"] + match.start() - start
            target = sec["rva"] + match.start("disp") - start + 5
            target += struct.unpack("<i", match.group("disp"))[0]
            targets = [s for s in sections if s["rva"] <= target < s["rva"] + s["virtual_size"]]
            if len(targets) != 1 or not 0 <= target < image_size:
                continue
            dest = targets[0]
            if not dest["characteristics"] & WRITE or dest["characteristics"] & EXECUTE:
                continue
            matches.append({"instruction_rva": insn_rva, "candidate_rva": target,
                            "target_section": dest["name"]})
    for match in matches:
        match["nearest_core_xref_distance"] = min(
            (abs(match["instruction_rva"] - x) for x in xrefs), default=None
        )
    near = [m for m in matches if m["nearest_core_xref_distance"] is not None
            and m["nearest_core_xref_distance"] <= 0x20000]
    return {"machine": "x64", "sections": sections, "image_size": image_size,
            "core_string_rvas": sorted(core_rvas), "core_reference_rvas": sorted(xrefs),
            "signature_candidates": matches, "near_core_candidates": near,
            "distinct_near_targets": sorted({m["candidate_rva"] for m in near}),
            "runtime_verified": False, "process_memory_accessed": False,
            "interpretation": "Static candidates only; do not use as a write authorization or proof of compatibility."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dll", type=Path)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists")
    if args.dll.stat().st_size > 512 * 1024 * 1024:
        parser.error("file exceeds inspection size bound")
    data = args.dll.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != args.expected_sha256.lower():
        parser.error("DLL fingerprint changed; refresh environment evidence before inspecting")
    result = inspect(data)
    result.update(recorded_at=datetime.now(UTC).isoformat(), dll=str(args.dll), sha256=digest,
                  bytes=len(data), script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    with args.output.open("x", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(json.dumps({"output": str(args.output), "core_strings": len(result["core_string_rvas"]),
                      "core_references": len(result["core_reference_rvas"]),
                      "signature_candidates": len(result["signature_candidates"]),
                      "distinct_near_targets": len(result["distinct_near_targets"]),
                      "runtime_verified": False}))


if __name__ == "__main__":
    main()
