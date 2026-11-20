"""Pinned static Qt metadata lineage of XOutlineButton, no process access."""

import argparse
import hashlib
import json
import re
import struct
from datetime import UTC, datetime
from pathlib import Path

if __package__:
    from .inspect_wechat_accessibility import inspect
    from .verify_wechat_accessibility import DLL, DLL_SHA
else:
    from inspect_wechat_accessibility import inspect
    from verify_wechat_accessibility import DLL, DLL_SHA


def button_lineage(image):
    if hashlib.sha256(image).hexdigest() != DLL_SHA:
        raise ValueError("pinned binary changed")
    sections = inspect(image)["sections"]
    preferred = 0x180000000

    def at(rva, size):
        section = next(s for s in sections if s["rva"] <= rva < s["rva"] + s["raw_size"])
        if not 0 < size <= 4096 or rva + size > section["rva"] + section["raw_size"]:
            raise ValueError("static metadata bound")
        offset = section["file_offset"] + rva - section["rva"]
        return image[offset:offset + size]

    def info(meta):
        parent, strings, data, dispatch, _, _ = struct.unpack("<6Q", at(meta, 48))
        strings -= preferred
        header = at(strings, 24)
        length = struct.unpack_from("<i", header, 4)[0]
        relative = struct.unpack_from("<q", header, 16)[0]
        if not 1 <= length <= 128:
            raise ValueError("class metadata bound")
        name = at(strings + relative, length)
        if not re.fullmatch(rb"[A-Za-z_][A-Za-z0-9_:]{0,127}", name):
            raise ValueError("not static class metadata")
        revision, class_index, _, _, methods, _, properties = struct.unpack("<7I", at(data - preferred, 28))
        if revision != 8 or class_index != 0 or methods > 256 or properties > 256:
            raise ValueError("unsupported Qt metadata")
        return dict(class_name=name.decode("ascii"), meta_object_rva=meta,
                    static_metacall_rva=dispatch - preferred if dispatch else None,
                    own_methods=methods, parent_meta_rva=parent - preferred if parent else None)

    target = b"mmui::XOutlineButton"
    candidates = set()
    for section in sections:
        if section["name"] not in {".rdata", ".data"}:
            continue
        start, end = section["file_offset"], section["file_offset"] + section["raw_size"]
        cursor = start
        while (cursor := image.find(target + b"\0", cursor, end)) >= 0:
            rva = section["rva"] + cursor - start
            for header_rva in range((rva // 8) * 8, max(section["rva"], rva - 4096), -8):
                if header_rva + 24 > rva:
                    continue
                header = at(header_rva, 24)
                if (struct.unpack_from("<i", header, 4)[0] != len(target)
                        or header_rva + struct.unpack_from("<q", header, 16)[0] != rva):
                    continue
                pointer = struct.pack("<Q", preferred + header_rva)
                for owner in sections:
                    if owner["name"] not in {".rdata", ".data"}:
                        continue
                    location = owner["file_offset"]
                    owner_end = location + owner["raw_size"]
                    while (location := image.find(pointer, location, owner_end)) >= 0:
                        meta = owner["rva"] + location - owner["file_offset"] - 8
                        try:
                            if info(meta)["class_name"] == target.decode():
                                candidates.add(meta)
                        except (ValueError, StopIteration, UnicodeError):
                            pass
                        location += 8
            cursor += len(target)
    if len(candidates) != 1:
        raise ValueError("button metadata not unique")
    lineage, seen = [], set()
    meta = candidates.pop()
    for _ in range(12):
        if meta in seen:
            raise ValueError("static metadata cycle")
        seen.add(meta)
        row = info(meta)
        lineage.append(row)
        if row["parent_meta_rva"] is None:
            break
        meta = row["parent_meta_rva"]
    else:
        raise ValueError("static metadata depth bound")
    if lineage[-1]["class_name"] != "QObject":
        raise ValueError("static metadata root unrecognized")
    return dict(class_lineage=lineage, inherits_qabstractbutton=any(r["class_name"] == "QAbstractButton" for r in lineage),
                process_memory_accessed=False, methods_executed=0,
                interpretation="Static QObject inheritance only; does not prove the live accessibility doAction implementation.")


def verify_focus_only_dispatch(image, observed):
    """Bind the live secondary vtable and method bytes to this static branch."""
    if hashlib.sha256(image).hexdigest() != DLL_SHA:
        raise ValueError("pinned binary changed")
    if (observed.get("status") != "button_interface_locations_observed" or not observed.get("worker_reaped")
            or not observed.get("restore_verified") or not observed["button_dispatch"].get("stable")):
        raise ValueError("verified runtime interface evidence required")
    sections = inspect(image)["sections"]
    preferred = 0x180000000

    def at(rva, size):
        section = next(s for s in sections if s["rva"] <= rva < s["rva"] + s["raw_size"])
        if not 0 < size <= 4096 or rva + size > section["rva"] + section["raw_size"]:
            raise ValueError("dispatch static read bound")
        offset = section["file_offset"] + rva - section["rva"]
        return image[offset:offset + size]

    data = observed["button_dispatch"]
    for method in data["methods"]:
        size = method["code_bytes"]
        if size not in (32, 128):
            raise ValueError("method observation bound")
        target = struct.unpack("<Q", at(data["secondary_vtable_rva"] + method["offset"], 8))[0] - preferred
        if target != method["function_rva"] or hashlib.sha256(at(target, size)).hexdigest() != method["prefix_sha256"]:
            raise ValueError("live method bytes do not match disk")
    cast = data["interface_cast_rva"]
    if (struct.unpack("<Q", at(data["primary_interface_vtable_rva"] + 19 * 8, 8))[0] - preferred != cast
            or hashlib.sha256(at(cast, 32)).hexdigest() != data["interface_cast_sha256"]
            or at(cast, 14) != bytes.fromhex("4883c11031c083fa03480f44c1c3")):
        raise ValueError("ActionInterface cast changed")
    action = next(m for m in data["methods"] if m["offset"] == 32)
    if action["function_rva"] != 0x9EA590 or action["code_bytes"] != 128:
        raise ValueError("doAction implementation not the audited focus-only branch")

    def call(rva):
        code = at(rva, 5)
        if code[0] != 0xE8:
            raise ValueError("expected relative call")
        return rva + 5 + struct.unpack_from("<i", code, 1)[0]

    do_action = action["function_rva"]
    if (call(do_action + 0x24) != 0x87A690 or call(do_action + 0x2F) != 0x205F0
            or at(do_action + 0x36, 2) != bytes.fromhex("7440")
            or at(do_action + 0x78, 7) != bytes.fromhex("4883c4285f5ec3")):
        raise ValueError("focus comparison/return branch changed")
    accessor = 0x87A690

    def rip_target(rva, prefix):
        code = at(rva, 7)
        if code[:3] != bytes.fromhex(prefix):
            raise ValueError("focus accessor reference changed")
        return rva + 7 + struct.unpack_from("<i", code, 3)[0]

    if (rip_target(accessor + 0x2B, "488d05") != 0xBD068C0
            or rip_target(accessor + 0x84, "488d05") != 0xB4F7E00
            or rip_target(accessor + 0x8B, "488905") != 0xBD068C0):
        raise ValueError("focus action constant changed")

    def literal(rva):
        raw = at(rva, 24)
        length, offset = struct.unpack_from("<i", raw, 4)[0], struct.unpack_from("<q", raw, 16)[0]
        if not 1 <= length <= 32:
            raise ValueError("static action string bound")
        return at(rva + offset, length * 2).decode("utf-16-le")

    if literal(0xB4F7E00) != "SetFocus" or literal(0xB4F7D48) != "Press":
        raise ValueError("static action names changed")
    return dict(live_method_bytes_match_disk=True, action_interface_offset=16, do_action_slot=32,
                do_action_rva=do_action, only_accepted_action="SetFocus", press_action="Press",
                press_request_returns_without_click=True, methods_executed=0,
                interpretation="The observed button exposes generic focus-only doAction; Qt UIA Invoke's Press is ignored.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--interface-evidence", type=Path)
    args = parser.parse_args()
    image = DLL.read_bytes()
    result = button_lineage(image)
    if args.interface_evidence:
        result["dispatch"] = verify_focus_only_dispatch(image, json.loads(args.interface_evidence.read_text(encoding="utf-8")))
    result.update(dll_sha256=DLL_SHA, recorded_at=datetime.now(UTC).isoformat(),
                  script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(result, output, indent=2)
        output.write("\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
