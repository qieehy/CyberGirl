"""Pinned, bounded read-only inspection of the current send button's one signal.

No connection mutation, Qt method invocation, heap enumeration or body reads.
Raw object pointers never leave this module; results do not enable submission.
"""

import struct

from ...social.models import SocialError
from .qt_memory import (
    Reads,
    accessible_interface,
    object_from_interface,
    parent_chain,
    runtime_accessible_id,
    static_meta,
)

BUTTON_META = 0x9169F38
BUTTON_VTABLE = 155857432
VIEW_META = 0x9149378
VIEW_VTABLE = 155520888
CLICKED_INDEX = 7  # QObject 3 + QWidget 4 + XSkiaWidget 0, then local clicked 0.
CLICK_SLOT_IMPL = 0x1C49180
SEND_METHOD = 0x1C36EB0
SIGNAL_LINEAGE = ((BUTTON_META, 6), (0x9169410, 0), (0x90F2A98, 4), (0x90B53A8, 3))

# Audited disk instructions bind each remote-layout assumption to this build.
LAYOUT_PROOFS = (
    (0x29E200, "4c8b5010488b0041035234"),  # Inherited signalCount accumulation.
    (0x2A3ACF, "488b7108"),  # QObject private.
    (0x2A3B53, "488b4e40"),  # Private connections.
    (0x2A3B60, "488b4108"),  # SignalVector.
    (0x2A3B9B, "4863ca48c1e1043b5008488d5010488d740820"),  # Header 16, lists 16, extra -1 list.
    (0x2A3C40, "4d8b6528"),  # Connection receiver.
    (0x2A3CAE, "410fb7455889c183e107"),  # Type bits.
    (0x2A3E47, "498b4d38a808"),  # Slot object pointer and flag.
    (0x2A3EC6, "498b4708"),  # QSlotObjectBase implementation pointer.
    (0x2A0665, "66c74058ffff"),  # Flags storage initially all ones; high padding is not zero.
    (0x2A06B3, "83e30783e1f001d983c1086641894c2458"),  # Preserve high bits; set type and slot-object bit.
    (0x1C491A6, "488b7210"),  # Captured view for this exact functor.
    (0x1C491B6, "4889f1ba030000004883c4205ee9e8dcfeff"),  # Send(3), no execution here.
)


def _clicked_connections(reads, base, button, view):
    private, view_private = reads.pointer(button + 8), reads.pointer(view + 8)
    if reads.pointer(private + 8) != button or reads.pointer(view_private + 8) != view:
        raise SocialError("qt_clicked_private_owner_changed")
    if reads.pointer(reads.pointer(view + 0x238) + 0x1D8) != button:
        raise SocialError("qt_clicked_view_button_changed")
    # Only this known flag byte, not the editor content or its data model.
    pressed = bool(reads.read(button + 0x140, 1)[0] & 2)
    sender_thread, receiver_thread = reads.pointer(private + 0x38), reads.pointer(view_private + 0x38)
    connections = reads.pointer(private + 0x40)
    current_id = struct.unpack("<I", reads.read(connections, 4))[0]
    vector = reads.pointer(connections + 8)
    allocated = struct.unpack("<Q", reads.read(vector + 8, 8))[0]
    if current_id == 0 or not CLICKED_INDEX < allocated <= 1024:
        raise SocialError("qt_clicked_vector_unrecognized")
    # Wildcard callbacks could add a separate route; do not enumerate them.
    if reads.nullable_pointer(vector + 16) or reads.nullable_pointer(vector + 24):
        raise SocialError("qt_clicked_wildcard_connections_unverified")
    list_at = vector + 32 + CLICKED_INDEX * 16
    node, last = reads.nullable_pointer(list_at), reads.nullable_pointer(list_at + 8)
    seen, previous, count = set(), 0, 0
    kind = None
    while node:
        if node in seen or count >= 16:
            raise SocialError("qt_clicked_connection_chain_bound")
        seen.add(node)
        following = reads.nullable_pointer(node + 16)
        if reads.nullable_pointer(node + 24) != previous or reads.pointer(node + 32) != button:
            raise SocialError("qt_clicked_connection_owner_changed")
        # Reject an unexpected receiver before reading any foreign QObject.
        if reads.pointer(node + 40) != view:
            raise SocialError("qt_clicked_receiver_unverified")
        if reads.pointer(node + 48) != receiver_thread:
            raise SocialError("qt_clicked_receiver_thread_changed")
        signal = struct.unpack("<i", reads.read(node + 84, 4))[0]
        flags = struct.unpack("<H", reads.read(node + 88, 2))[0] & 0x1F
        if signal != CLICKED_INDEX or not flags & 8 or flags & 7 not in (0, 1):
            raise SocialError("qt_clicked_connection_type_unverified")
        if flags & 7 == 0 and sender_thread != receiver_thread:
            raise SocialError("qt_clicked_auto_connection_not_same_thread")
        slot = reads.pointer(node + 56)
        if reads.pointer(slot + 8) != base + CLICK_SLOT_IMPL:
            raise SocialError("qt_clicked_functor_unverified")
        if reads.pointer(slot + 16) != view:
            raise SocialError("qt_clicked_capture_changed")
        kind = "auto_same_thread" if flags & 7 == 0 else "direct"
        previous, node, count = node, following, count + 1
    if count != 1 or previous != last:
        raise SocialError("qt_clicked_target_not_unique")
    return dict(signal="clicked", global_signal_index=CLICKED_INDEX, connections=count,
                receiver_class="mmui::ChatInputView", same_input_view=True, bound_child_button_matches=True,
                receiver_and_capture_match=True, connection_type=kind, slot_impl_rva=CLICK_SLOT_IMPL,
                target_method="ChatInputView::Send(SendActionType)", target_method_rva=SEND_METHOD, target_argument=3,
                pressed_bit_set=pressed, submission_enabled=False)


def inspect_clicked_connection(memory, button_runtime_ids, input_runtime_ids):
    reads, base = Reads(memory), memory.base
    for rva, signature in LAYOUT_PROOFS:
        expected = bytes.fromhex(signature)
        if reads.read(base + rva, len(expected)) != expected:
            raise SocialError("qt_clicked_layout_code_changed")
    lineage = SIGNAL_LINEAGE
    for index, (meta, count) in enumerate(lineage):
        expected_parent = base + lineage[index + 1][0] if index + 1 < len(lineage) else 0
        if reads.nullable_pointer(base + meta) != expected_parent:
            raise SocialError("qt_clicked_signal_lineage_changed")
        metadata = reads.pointer(base + meta + 16)
        if struct.unpack("<I", reads.read(metadata + 0x34, 4))[0] != count:
            raise SocialError("qt_clicked_signal_index_changed")
    objects = []
    for runtime in (button_runtime_ids, input_runtime_ids):
        interface = accessible_interface(reads, base, runtime_accessible_id(runtime))
        obj, _ = object_from_interface(reads, base, interface)
        objects.append(obj)
    button, input_object = objects
    button_meta = static_meta(reads, base, button)
    if (button_meta["class_name"] != "mmui::XOutlineButton" or button_meta["meta_object_rva"] != BUTTON_META
            or button_meta["qobject_vtable_rva"] != BUTTON_VTABLE
            or static_meta(reads, base, input_object)["class_name"] != "mmui::ChatInputField"):
        raise SocialError("qt_clicked_classes_changed")
    input_chain, input_objects = parent_chain(reads, base, input_object)
    button_chain, button_objects = parent_chain(reads, base, button)
    if not input_chain["complete"] or not button_chain["complete"] or input_objects[-1] != button_objects[-1]:
        raise SocialError("qt_clicked_window_lineage_changed")

    def view_in(chain, objects):
        matches = [obj for info, obj in zip(chain["entries"], objects, strict=True)
                   if info["class_name"] == "mmui::ChatInputView" and info["meta_object_rva"] == VIEW_META
                   and info["qobject_vtable_rva"] == VIEW_VTABLE]
        if len(matches) != 1:
            raise SocialError("qt_clicked_input_view_unverified")
        return matches[0]

    view = view_in(input_chain, input_objects)
    if view_in(button_chain, button_objects) != view:
        raise SocialError("qt_clicked_input_view_changed")
    result = _clicked_connections(reads, base, button, view)
    reads.stable()
    return dict(result, lookup_bytes=reads.used, stable=True, methods_executed=0)
