"""Pinned on-disk keyboard submission qualification. No input or process access.

The historical UI evidence binds a class/vtable only. Current input settings,
filter registration, signal connection and native HWND routing are not assumed.
"""

import argparse
import hashlib
import json
import struct
from datetime import UTC, datetime
from pathlib import Path

if __package__:
    from .verify_wechat_accessibility import DLL, DLL_SHA
    from .wechat_pinned_image import PinnedImage
else:
    from verify_wechat_accessibility import DLL, DLL_SHA
    from wechat_pinned_image import PinnedImage

INPUT_META = 0x9149178
INPUT_VTABLE = 156200152
INPUT_SEND = 0x8BA080


def _trace(image):
    if struct.unpack("<Q", image.at(INPUT_VTABLE + 5 * 8, 8))[0] - image.base != 0x21B1FB0:
        raise ValueError("input event filter vtable changed")
    method = image.methods(INPUT_META, "mmui::ChatInputField", 5)[0]
    if (method["name"] != "send" or method["flags"] != 6 or method["argc"] != 1
            or method["type_names"] != ["metatype:43", "SendActionType"]):
        raise ValueError("input send signal metadata changed")
    # QEvent::KeyPress (6) enters the helper; KeyRelease (7) has a separate path.
    image.expect(0x21B2023, "0fb7461083f807742a83f8067549")
    image.relative(0x21B2042, 0x21B0980)
    image.expect(0x21B0A47, "418d80fdfffffe83f8127726")
    image.rip(0x21B0A53, 0x94F95BC, "4c8d05")
    branches = []
    for key in (0x1000004, 0x1000005):  # Qt Return / keypad Enter.
        table = 0x94F95BC
        branch = table + struct.unpack("<i", image.at(table + (key - 0x1000003) * 4, 4))[0]
        if branch != 0x21B0A63:
            raise ValueError("Return/Enter helper route changed")
        branches.append(dict(qt_key=key, branch_rva=branch))
    image.relative(0x21B0A69, 0x21B0B90, 0xE9)
    # The return helper reads modifiers and preferences/UI state before emitting.
    image.relative(0x21B0BBD, 0x886630)
    image.expect(0x21B0CC9, "4181e70000000e7435")
    image.expect(0x21B0CD2, "4181ff0000000274624181ff000000047570")
    image.expect(0x21B0CF8, "4889f1ba02000000")
    image.relative(0x21B0D00, INPUT_SEND)
    image.expect(0x21B0D1E, "84db741b")
    image.expect(0x21B0D2E, "4889f1ba01000000")
    image.relative(0x21B0D36, INPUT_SEND)
    image.rip(0x8BA0AA, INPUT_META, "488d15")
    image.expect(0x8BA0B6, "4531c0")
    image.relative(0x8BA0B9, 0x29E1F0)

    # Static typed connection passes the SendActionType through to view::Send.
    image.expect(0x1C2DDB9, "488b9e10020000")
    image.rip(0x1C2DDC0, 0x1C36EB0, "4c8d35")
    image.rip(0x1C2DDD8, INPUT_SEND, "488d05")
    image.expect(0x1C2DDFA, "4c897010c7401800000000")
    image.rip(0x1C2DE0B, 0x1BF13F0, "488d0d")
    image.expect(0x1BF140B, "486342184901c0498b41088b004c89c14989d089c249ff6010")
    image.relative(0x1C2DE51, 0x2A0150)

    # WM_CHAR / WM_IME_CHAR creates two Qt events with key=0, not Qt::Key_Return.
    image.expect(0x26E6B63, "4181ff8602000074094181ff020100007518")
    image.expect(0x26E6C04, "ba060000004531c04189e9")
    image.relative(0x26E6C0F, 0x84F160)
    image.expect(0x26E6C3B, "ba070000004531c04189e9")
    image.relative(0x26E6C46, 0x84F160)
    # Native key receiver may be redirected to an existing Qt key grabber.
    image.expect(0x26E6A0D, "498b7424184885f64889942488000000480f44f2")
    # There is a key-down path that records a key; a lone down has no cleanup proof.
    image.expect(0x26E6EE5, "4181ff000100007416")
    image.rip(0x26E78D6, 0xBD243A0, "488d0d")
    image.relative(0x26E78E3, 0x26E7E20)
    image.expect(0x26E7E8B, "448d580144891948984c8d1c40428954d920")
    # Release looks up/removes the earlier key record; it is not an independent press.
    image.rip(0x26E6FC0, 0xBD243A0, "488d0d")
    image.expect(0x26E6FC7, "41b001")
    image.relative(0x26E6FCA, 0x26E7A30)
    image.expect(0x26E7B2D, "ffc889064883c608")
    code_windows = (("input_event_filter", 0x21B1FB0, 192), ("return_dispatch", 0x21B0980, 368),
                    ("return_helper", 0x21B0B90, 488), ("typed_connection", 0x1C2DDB9, 157),
                    ("char_key_events", 0x26E6BCC, 144), ("key_record_store", 0x26E7E20, 160))
    return dict(dll_sha256=DLL_SHA, input_class="mmui::ChatInputField", input_vtable_rva=INPUT_VTABLE,
                event_filter_rva=0x21B1FB0, return_helper_rva=0x21B0B90, return_branches=branches,
                input_send_signal=method, input_send_signal_rva=INPUT_SEND,
                view_send_method="ChatInputView::Send(SendActionType)", view_send_rva=0x1C36EB0,
                key_modifiers_and_preferences_affect_return=True, return_send_argument_values=[1, 2],
                wm_char_qt_events=[dict(type=6, key=0), dict(type=7, key=0)],
                native_receiver_can_use_key_grabber=True, keydown_has_key_record_store_path=True,
                keyup_uses_prior_key_record=True,
                candidates=[dict(message="WM_KEYDOWN", qualified=False, reason="key_record_cleanup_and_queue_unproven"),
                            dict(message="WM_CHAR", qualified=False, reason="key_zero_does_not_match_traced_return_branch"),
                            dict(message="WM_KEYUP", qualified=False, reason="prior_record_not_an_independent_press")],
                code_windows={name: dict(rva=rva, size=size, sha256=hashlib.sha256(image.at(rva, size)).hexdigest())
                              for name, rva, size in code_windows})


def inspect_keyboard_route(raw, historical):
    native = historical.get("observation", {}).get("native_object", {})
    if (historical.get("status") != "observed" or not historical.get("collector_reaped")
            or not historical.get("restore_verified") or not native.get("stable")
            or not native.get("canonical_conversation_verified") or not native.get("matched_database_account")):
        raise ValueError("verified historical input evidence required")
    first = native["parent_chains"]["chat_input"]["entries"][0]
    if (first["class_name"] != "mmui::ChatInputField" or first["meta_object_rva"] != INPUT_META
            or first["qobject_vtable_rva"] != INPUT_VTABLE):
        raise ValueError("historical input class changed")
    result = _trace(PinnedImage(raw))
    return dict(result, uses_historical_live_input_binding=True, live_process_accessed=False,
                current_filter_registration_observed=False, current_input_send_connection_observed=False,
                current_send_preference_observed=False, live_native_route_verified=False,
                submission_enabled=False, native_methods_executed=0, window_messages_sent=0,
                interpretation="No qualified single-message keyboard submission. Static input signal route is conditional; runtime policy and native event state are unobserved.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--match-input-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = inspect_keyboard_route(DLL.read_bytes(), json.loads(args.match_input_evidence.read_text(encoding="utf-8")))
    result.update(recorded_at=datetime.now(UTC).isoformat(), script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(result, output, indent=2)
        output.write("\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
