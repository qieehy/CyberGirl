"""Reproduce the pinned button connection and default Windows cancel route.

All input is disk evidence. This neither invokes a slot nor proves that the
class registration is still the actual HWND procedure (the live query failed).
"""

import argparse
import hashlib
import json
import struct
from datetime import UTC, datetime
from pathlib import Path

from wechat_agent.bridges.wechat.qt_connections import (
    CLICK_SLOT_IMPL,
    LAYOUT_PROOFS,
    SEND_METHOD,
    VIEW_META,
)

if __package__:
    from .verify_wechat_accessibility import DLL, DLL_SHA
    from .wechat_pinned_image import PinnedImage
else:
    from verify_wechat_accessibility import DLL, DLL_SHA
    from wechat_pinned_image import PinnedImage


def _delay_import(image, name, expected_iat):
    pe = struct.unpack_from("<I", image.image, 60)[0]
    directory = struct.unpack_from("<I", image.image, pe + 24 + 216)[0]
    for index in range(128):
        attrs, module_name, _, iat, names, _, _, _ = struct.unpack("<8I", image.at(directory + index * 32, 32))
        if not module_name:
            break
        module = image.at(module_name, 128).split(b"\0", 1)[0]
        if module != b"USER32.dll":
            continue
        if attrs != 1:
            raise ValueError("unsupported delay import address mode")
        for position in range(4096):
            value = struct.unpack("<Q", image.at(names + position * 8, 8))[0]
            if not value:
                break
            if not value >> 63 and image.at(value + 2, 128).split(b"\0", 1)[0] == name.encode("ascii"):
                if iat + position * 8 != expected_iat:
                    raise ValueError("USER32 import RVA changed")
                return
    raise ValueError("USER32 delay import not found")


def inspect_submit_route(raw, connection):
    if (connection.get("status") != "clicked_connection_observed" or not connection.get("worker_reaped")
            or not connection.get("restore_verified") or not connection["connection"].get("stable")
            or not connection["connection"].get("native_identity_verified")):
        raise ValueError("verified current connection evidence required")
    live = connection["connection"]
    if (live["connections"] != 1 or live["slot_impl_rva"] != CLICK_SLOT_IMPL or live["target_method_rva"] != SEND_METHOD
            or live["target_argument"] != 3 or not live["receiver_and_capture_match"] or not live["same_input_view"]):
        raise ValueError("unexpected live connection route")
    image = PinnedImage(raw)
    for rva, signature in LAYOUT_PROOFS:
        image.expect(rva, signature)
    method = image.methods(VIEW_META, "mmui::ChatInputView", 14)[8]
    if (method["name"] != "Send" or method["argc"] != 1 or method["flags"] != 10
            or method["type_names"] != ["metatype:43", "SendActionType"]):
        raise ValueError("input view Send slot metadata changed")
    table = 0x916432C
    branch = table + struct.unpack("<i", image.at(table + 8 * 4, 4))[0]
    if branch != 0x8BA83E:
        raise ValueError("Send metacall branch changed")
    image.expect(branch, "498b41088b10")
    image.relative(0x8BA860, SEND_METHOD, 0xE9)
    # Setup receives this view in RCX and captures it in the exact clicked functor.
    image.expect(0x1C2EA0D, "4889ce")
    image.expect(0x1C2EAB8, "488b8638020000488b98d8010000")
    image.rip(0x1C2EAC6, 0x9239C0, "488d05")
    image.rip(0x1C2EAE8, CLICK_SLOT_IMPL, "488d0d")
    image.expect(0x1C2EAEF, "4889480848897010")
    image.rip(0x1C2EAF7, 0x9169F38, "488d0d")
    image.expect(0x1C2EB2D, "4889da4989f84989f1")
    image.relative(0x1C2EB36, 0x2A0150)
    image.expect(CLICK_SLOT_IMPL + 5, "83f901741c")
    image.expect(0x1C491B6, "4889f1ba03000000")
    image.relative(0x1C491C3, SEND_METHOD, 0xE9)

    _delay_import(image, "RegisterClassExW", 0xBCFB288)
    _delay_import(image, "DefWindowProcW", 0xBCFAEE0)
    image.rip(0x26AFB54, 0x26B04F0, "4c8d0d")
    image.relative(0x26AFB66, 0x26AFD00)
    image.expect(0x26AFD13, "4d89cc")
    image.expect(0x26AFE49, "c78424b000000050000000")
    image.expect(0x26AFE5B, "4c89a424b8000000")
    image.expect(0x26AFF7F, "488d8c24b0000000")

    def indirect(rva, target):
        code = image.at(rva, 6, executable=True)
        if code[:2] != bytes.fromhex("ff15") or rva + 6 + struct.unpack_from("<i", code, 2)[0] != target:
            raise ValueError("USER32 indirect call changed")

    indirect(0x26AFF87, 0xBCFB288)
    image.relative(0x26B0531, 0x26B2080)  # Class procedure to message classifier.
    image.relative(0x26B05A4, 0x26B22D0)  # Class procedure to native dispatcher.
    image.expect(0x26B2080, "81f9090200007f248d41fe3d17010000")
    image.rip(0x26B2096, 0x95AD150, "4c8d0d")
    image.rip(0x26B20C2, 0x95AD5B0, "488d15")
    messages = []
    for name, message, jump_table, index in (("WM_CANCELMODE", 0x1F, 0x95AD150, 0x1F - 2),
                                           ("WM_CAPTURECHANGED", 0x215, 0x95AD5B0, 0x215 - 0x20A)):
        target = jump_table + struct.unpack("<i", image.at(jump_table + index * 4, 4))[0]
        if target != 0x26B228D:
            raise ValueError("cancel message classification changed")
        messages.append(dict(name=name, message=message, classifier_branch_rva=target, default_classification=0x21E))
    image.expect(0x26B228D, "8d9160ffffffb80100060083fa0a7224")
    image.expect(0x26B229D, "8d9100feffffb80100020083fa0f7214")
    image.expect(0x26B22AD, "8d91bffdffffb80400040883fa030f83edfeffff")
    image.expect(0x26B21AE, "81c1bbfdffff83f90bb901000008b81e0200000f42c1c3")
    image.expect(0x26B2570, "31ff")
    image.expect(0x26B2599, "4181fd1e0200000f841a110000")
    image.expect(0x26B36DB, "89f8")
    image.expect(0x26B0B6D, "84c07519")
    indirect(0x26B0B7C, 0xBCFAEE0)
    windows = (("connection_setup", 0x1C2EAB8, 0x83), ("slot_functor", CLICK_SLOT_IMPL, 0x48),
               ("send_slot_entry", SEND_METHOD, 128), ("message_classifier", 0x26B2080, 0x250),
               ("default_dispatch_branch", 0x26B2563, 0x43), ("class_procedure_entry", 0x26B04F0, 128))
    return dict(dll_sha256=DLL_SHA, current_connection_bound=True, clicked_signal_rva=0x9239C0,
                slot_functor_rva=CLICK_SLOT_IMPL, target_method="ChatInputView::Send(SendActionType)", target_argument=3,
                target_method_rva=SEND_METHOD, method_metadata=method,
                class_registration_procedure_rva=0x26B04F0, class_registration_bound_to_live_hwnd=False,
                message_classifier_rva=0x26B2080, native_dispatcher_rva=0x26B22D0, messages=messages,
                default_path_returns_unhandled=True, fallback_api="DefWindowProcW",
                native_event_filters_not_observed=True, custom_state_reset_proven=False, cancellation_verified=False,
                submission_enabled=False, process_memory_accessed=False, methods_executed=0, window_messages_sent=0,
                code_windows={name: dict(rva=rva, size=size, sha256=hashlib.sha256(image.at(rva, size)).hexdigest())
                              for name, rva, size in windows},
                interpretation="Connection reaches the named Send slot; default cancel routing has no dedicated state-reset handler. Live HWND entry and native filters remain unverified.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--match-connection-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = inspect_submit_route(DLL.read_bytes(), json.loads(args.match_connection_evidence.read_text(encoding="utf-8")))
    result.update(recorded_at=datetime.now(UTC).isoformat(), script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(result, output, indent=2)
        output.write("\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
