"""Pinned on-disk event/signal trace; never execute a client method or message.

The vtable binding is historical B6 evidence, not a fresh runtime event trace.
This identifies a cancellation gap; it does not enable the candidate sender.
"""

import argparse
import hashlib
import json
import struct
from datetime import UTC, datetime
from pathlib import Path

if __package__:
    from .inspect_wechat_accessibility import inspect
    from .verify_wechat_accessibility import DLL, DLL_SHA
else:
    from inspect_wechat_accessibility import inspect
    from verify_wechat_accessibility import DLL, DLL_SHA


def inspect_mouse_route(image, observed):
    if hashlib.sha256(image).hexdigest() != DLL_SHA:
        raise ValueError("pinned binary changed")
    if (observed.get("status") != "button_interface_locations_observed" or not observed.get("worker_reaped")
            or not observed.get("restore_verified") or not observed["button_dispatch"].get("stable")):
        raise ValueError("verified historical button binding required")
    binding = observed["button_dispatch"]["qobject"]
    if binding != dict(class_name="mmui::XOutlineButton", meta_object_rva=152477496,
                       meta_getter_rva=9583312, qobject_vtable_rva=155857432):
        raise ValueError("historical button class changed")
    sections, preferred = inspect(image)["sections"], 0x180000000

    def at(rva, size):
        section = next(s for s in sections if s["rva"] <= rva < s["rva"] + s["raw_size"])
        if not 0 < size <= 4096 or rva + size > section["rva"] + section["raw_size"]:
            raise ValueError("static event trace bound")
        offset = section["file_offset"] + rva - section["rva"]
        return image[offset:offset + size]

    def expect(rva, hex_bytes):
        expected = bytes.fromhex(hex_bytes)
        if at(rva, len(expected)) != expected:
            raise ValueError("event route bytes changed")

    def relative_call(rva, target, opcode=0xE8):
        code = at(rva, 5)
        if code[0] != opcode or rva + 5 + struct.unpack_from("<i", code, 1)[0] != target:
            raise ValueError("event signal call changed")

    meta, vtable = binding["meta_object_rva"], binding["qobject_vtable_rva"]
    if struct.unpack("<Q", at(vtable, 8))[0] - preferred != binding["meta_getter_rva"]:
        raise ValueError("button meta getter changed")
    getter = at(binding["meta_getter_rva"], 23)
    if (getter[:11] != bytes.fromhex("488b490848837928000f85")
            or getter[15:18] != bytes.fromhex("488d05") or getter[22] != 0xC3):
        raise ValueError("button meta getter shape changed")
    if binding["meta_getter_rva"] + 22 + struct.unpack_from("<i", getter, 18)[0] != meta:
        raise ValueError("button meta getter target changed")
    _, strings, metadata, _, _, _ = struct.unpack("<6Q", at(meta, 48))
    strings, metadata = strings - preferred, metadata - preferred

    def string(index):
        if not 0 <= index < 128:
            raise ValueError("button string index bound")
        position = strings + index * 24
        header = at(position, 24)
        length, offset = struct.unpack_from("<i", header, 4)[0], struct.unpack_from("<q", header, 16)[0]
        if not 1 <= length <= 128:
            raise ValueError("button string length bound")
        return at(position + offset, length).decode("ascii")

    header = struct.unpack("<14I", at(metadata, 56))
    if header[0] != 8 or string(header[1]) != binding["class_name"] or header[4] != 6 or header[13] != 6:
        raise ValueError("button signal metadata changed")
    signals = [string(struct.unpack("<5I", at(metadata + (header[5] + i * 5) * 4, 20))[0]) for i in range(6)]
    if signals != ["clicked", "stateChanged", "pressed", "released", "entered", "leaved"]:
        raise ValueError("button signals changed")
    functions = {"event": (4, 0x5D1580, 0xDEF), "mouse_press": (18, 0x1EA2A50, 0x160),
                 "mouse_release": (19, 0x1EA2BB0, 0x180)}
    for slot, rva, _ in functions.values():
        if struct.unpack("<Q", at(vtable + slot * 8, 8))[0] - preferred != rva:
            raise ValueError("button event vtable changed")
    expect(0x5D15AC, "440fb75210")  # QEvent type from +0x10.
    expect(0x5D15B7, "4183c2fe4181fad9000000")  # Event type minus 2, bounded jump table.
    reference = at(0x5D15CA, 7)
    if (reference[:3] != bytes.fromhex("4c8d1d")
            or 0x5D15D1 + struct.unpack_from("<i", reference, 3)[0] != 0x90F4478):
        raise ValueError("widget event jump table changed")
    branches = []
    for event_type, slot, branch in ((2, 18, 0x5D1BB4), (3, 19, 0x5D1BC4)):
        table = 0x90F4478
        if table + struct.unpack("<i", at(table + (event_type - 2) * 4, 4))[0] != branch:
            raise ValueError("mouse event dispatch changed")
        expect(branch, "488b01ff90" + struct.pack("<I", slot * 8).hex())
        branches.append(dict(qevent_type=event_type, branch_rva=branch, virtual_slot=slot))
    expect(0x1EA2A68, "f6814001000004")
    expect(0x1EA2B4A, "837e5001752b")
    expect(0x1EA2B54, "808f4001000002")
    relative_call(0x1EA2B5E, 0x923A50)
    expect(0x1EA2BC8, "f6814001000004")
    expect(0x1EA2CB3, "84c07431837f5001753b80a640010000fd")
    relative_call(0x1EA2CC7, 0x923A70)
    relative_call(0x1EA2CD5, 0x9239E0)
    expect(0x1EA2CDA, "804f12044889f1")
    relative_call(0x1EA2CE1, 0x9239C0)
    for rva, signal_index in ((0x9239C0, 0), (0x923A50, 2), (0x923A70, 3)):
        code = at(rva, 7)
        if code[:3] != bytes.fromhex("488d15") or rva + 7 + struct.unpack_from("<i", code, 3)[0] != meta:
            raise ValueError("signal meta target changed")
        if signal_index == 0:
            expect(rva + 7, "4531c04531c9")
            relative_call(rva + 13, 0x29E1F0, 0xE9)
        else:
            expect(rva + 7, "41b8" + struct.pack("<I", signal_index).hex() + "4531c9")
            relative_call(rva + 16, 0x29E1F0, 0xE9)
    return dict(dll_sha256=DLL_SHA, class_name=binding["class_name"], vtable_rva=vtable, signals=signals,
                event_branches=branches, functions={name: dict(virtual_slot=slot, function_rva=rva, code_bytes=size,
                        sha256=hashlib.sha256(at(rva, size)).hexdigest()) for name, (slot, rva, size) in functions.items()},
                state_offset=0x140, press_sets_mask=2, release_clears_mask=2,
                press_signal="pressed", release_signals=["released", "stateChanged", "clicked"],
                in_bounds_left_release_has_no_prior_pressed_bit_guard=True,
                uses_historical_live_vtable_binding=True, live_button_event_bytes_rechecked=False,
                cancellation_verified=False, native_window_to_widget_route_verified=False,
                submission_enabled=False, process_memory_accessed=False, methods_executed=0,
                interpretation="Pinned widget event-to-signal branch only; no proof that WM_CANCELMODE clears custom state or queued Qt events.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--match-button-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = inspect_mouse_route(DLL.read_bytes(), json.loads(args.match_button_evidence.read_text(encoding="utf-8")))
    result.update(recorded_at=datetime.now(UTC).isoformat(), script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(result, output, indent=2)
        output.write("\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
