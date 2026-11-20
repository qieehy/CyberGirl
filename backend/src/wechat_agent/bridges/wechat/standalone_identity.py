"""Native canonical identity of an explicitly selected standalone filehelper.

No navigation, title matching, object mutation or target function invocation.
"""

import hashlib
import re

from ...social.models import SocialError
from .qt_memory import (
    Reads,
    accessible_interface,
    object_from_interface,
    page_session_key,
    parent_chain,
    runtime_accessible_id,
    static_meta,
)

SINGLE_META_RVA = 152341752
SINGLE_VTABLE_RVA = 160048360


def verify_standalone_filehelper(memory, input_runtime_ids, *, account_reader, expected_fingerprint):
    memory.validate()
    account = account_reader()
    if (not isinstance(account, str) or not re.fullmatch(r"[A-Za-z0-9_.@-]{1,128}", account)
            or hashlib.sha256(account.encode()).hexdigest() != expected_fingerprint):
        raise SocialError("standalone_account_mismatch")
    reads = Reads(memory)
    interface = accessible_interface(reads, memory.base, runtime_accessible_id(input_runtime_ids))
    editor, _ = object_from_interface(reads, memory.base, interface)
    if static_meta(reads, memory.base, editor)["class_name"] != "mmui::ChatInputField":
        raise SocialError("standalone_input_class_mismatch")
    chain, objects = parent_chain(reads, memory.base, editor)
    entries = chain["entries"]
    windows = [(e, p) for e, p in zip(entries, objects, strict=True) if e["class_name"] == "mmui::ChatSingleWindow"]
    pages = [(e, p) for e, p in zip(entries, objects, strict=True) if e["class_name"] == "mmui::ChatMessagePage"]
    if not chain["complete"] or len(windows) != 1 or len(pages) != 1:
        raise SocialError("standalone_lineage_not_unique")
    root_info, root = windows[0]
    page_info, page = pages[0]
    if objects.index(page) >= objects.index(root):
        raise SocialError("standalone_page_outside_window")
    if (root_info["meta_object_rva"] != SINGLE_META_RVA
            or root_info["qobject_vtable_rva"] != SINGLE_VTABLE_RVA):
        raise SocialError("standalone_root_layout_changed")
    business = page_session_key(reads, memory.base, page, page_info)
    if not business["matches_filehelper"]:
        raise SocialError("standalone_page_not_filehelper")
    reads.stable()
    if account_reader() != account:
        raise SocialError("standalone_account_changed")
    return {"canonical_conversation_verified": True, "native_input_verified": True,
            "root_class": root_info["class_name"], "root_meta_object_rva": root_info["meta_object_rva"],
            "root_vtable_rva": root_info["qobject_vtable_rva"], "same_window_page_lineage": True,
            "lookup_bytes": reads.used, "stable": True}


def inspect_input_value_routes(memory, input_runtime_ids, image):
    """Pinned executable method locations only, never invoke the native interface."""
    reads = Reads(memory)
    interface = accessible_interface(reads, memory.base, runtime_accessible_id(input_runtime_ids))
    editor, _ = object_from_interface(reads, memory.base, interface)
    if static_meta(reads, memory.base, editor)["class_name"] != "mmui::ChatInputField":
        raise SocialError("standalone_input_class_mismatch")
    table = reads.pointer(interface)
    if not memory.base <= table < memory.base + 206352384 - 160:
        raise SocialError("standalone_accessible_table_outside_image")
    result = []
    # Pinned Qt 5 MSVC ABI: object at slot 2, setText at 12, interface_cast at 19.
    for name, slot in (("setText", 12), ("interface_cast", 19)):
        function = reads.pointer(table + slot * 8)
        rva = function - memory.base
        expected = image.at(rva, 384, executable=True)
        observed = reads.read(function, 384)
        if observed != expected:
            raise SocialError("standalone_value_method_code_changed")
        result.append(dict(method=name, slot=slot, function_rva=rva,
                           code_sha256=hashlib.sha256(observed).hexdigest(), code_hex=observed.hex()))
    reads.stable()
    return result


def inspect_button_action_route(memory, button_runtime_ids, image):
    """Read the button's pinned interface_cast code; no native method calls."""
    from .qt_connections import BUTTON_META, BUTTON_VTABLE

    reads = Reads(memory)
    interface = accessible_interface(reads, memory.base, runtime_accessible_id(button_runtime_ids))
    button, _ = object_from_interface(reads, memory.base, interface)
    meta = static_meta(reads, memory.base, button)
    if (meta["class_name"] != "mmui::XOutlineButton" or meta["meta_object_rva"] != BUTTON_META
            or meta["qobject_vtable_rva"] != BUTTON_VTABLE):
        raise SocialError("standalone_button_class_mismatch")
    table = reads.pointer(interface)
    if not memory.base <= table < memory.base + 206352384 - 160:
        raise SocialError("standalone_accessible_table_outside_image")
    function = reads.pointer(table + 19 * 8)
    rva = function - memory.base
    expected = image.at(rva, 384, executable=True)
    observed = reads.read(function, 384)
    if observed != expected:
        raise SocialError("standalone_button_method_code_changed")
    # Exact executable sequence: ActionInterface(3) -> this+0x10, else null.
    if observed[:14] != bytes.fromhex("4883c11031c083fa03480f44c1c3"):
        raise SocialError("standalone_button_action_cast_unrecognized")
    action_table = reads.pointer(interface + 0x10)
    if not memory.base <= action_table < memory.base + 206352384 - 48:
        raise SocialError("standalone_button_action_table_outside_image")
    methods = []
    # Qt header order: destructor, actions, localized name/description, action.
    for name, slot in (("actionNames", 1), ("doAction", 4)):
        address = reads.pointer(action_table + slot * 8)
        method_rva = address - memory.base
        code = reads.read(address, 384)
        if code != image.at(method_rva, 384, executable=True):
            raise SocialError("standalone_button_action_code_changed")
        methods.append(dict(method=name, slot=slot, function_rva=method_rva,
                            code_sha256=hashlib.sha256(code).hexdigest(), code_hex=code.hex()))
    reads.stable()
    return dict(method="button_interface_cast", slot=19, function_rva=rva,
                code_sha256=hashlib.sha256(observed).hexdigest(), code_hex=observed.hex(), action_methods=methods)
