"""Current-button connection metadata only; synthetic memory, never invoke."""

import json
import struct
from dataclasses import asdict

import pytest

from tests.test_wechat_native_sender import ACTION, BINDING, setup
from tests.test_wechat_qt_memory import Memory
from wechat_agent.bridges.wechat import native_sender, qt_connections
from wechat_agent.bridges.wechat.qt_connections import CLICK_SLOT_IMPL, _clicked_connections
from wechat_agent.bridges.wechat.qt_memory import Reads
from wechat_agent.bridges.wechat.send_worker import WorkerSession
from wechat_agent.social.models import SocialError

BUTTON, VIEW, BP, VP, FORM = 0xC00000, 0xC01000, 0xD00000, 0xD01000, 0xD10000
THREAD, CD, VECTOR, NODE, SLOT = 0xD20000, 0xE00000, 0xE01000, 0xF00000, 0xF01000


def memory():
    value = Memory()
    for address, pointer in ((BUTTON + 8, BP), (VIEW + 8, VP), (BP + 8, BUTTON), (VP + 8, VIEW),
            (VIEW + 0x238, FORM), (FORM + 0x1D8, BUTTON), (BP + 0x38, THREAD), (VP + 0x38, THREAD),
            (BP + 0x40, CD), (CD + 8, VECTOR), (VECTOR + 8, 16), (VECTOR + 16, 0), (VECTOR + 24, 0),
            (VECTOR + 32 + 7 * 16, NODE), (VECTOR + 40 + 7 * 16, NODE), (NODE + 16, 0), (NODE + 24, 0),
            (NODE + 32, BUTTON), (NODE + 40, VIEW), (NODE + 48, THREAD), (NODE + 56, SLOT),
            (SLOT + 8, value.base + CLICK_SLOT_IMPL), (SLOT + 16, VIEW)):
        value.cells[(address, 8)] = struct.pack("<Q", pointer)
    value.cells[(BUTTON + 0x140, 1)] = b"\0"
    value.cells[(CD, 4)] = struct.pack("<I", 4)
    value.cells[(NODE + 84, 4)] = struct.pack("<i", 7)
    value.cells[(NODE + 88, 2)] = struct.pack("<H", 8)
    return value


def test_only_one_current_view_functor_is_reported_without_pointers_or_body():
    value = memory()
    reads = Reads(value)
    result = _clicked_connections(reads, value.base, BUTTON, VIEW)
    reads.stable()
    assert result["target_argument"] == 3 and result["connection_type"] == "auto_same_thread"
    assert result["connections"] == 1 and result["receiver_and_capture_match"] and not result["submission_enabled"]
    assert reads.used < 512
    encoded = json.dumps(result)
    for private in (BUTTON, VIEW, BP, VP, THREAD, CD, VECTOR, NODE, SLOT):
        assert str(private) not in encoded and hex(private) not in encoded


@pytest.mark.parametrize("address,size,replacement", [
    (BP + 8, 8, VIEW), (FORM + 0x1D8, 8, VIEW), (CD, 4, 0), (VECTOR + 8, 8, 2048),
    (VECTOR + 16, 8, NODE), (NODE + 16, 8, NODE), (NODE + 24, 8, NODE), (NODE + 32, 8, VIEW),
    (NODE + 40, 8, BUTTON), (NODE + 48, 8, VP), (NODE + 84, 4, 8), (NODE + 88, 2, 10),
    (NODE + 88, 2, 0), (SLOT + 8, 8, CLICK_SLOT_IMPL), (SLOT + 16, 8, BUTTON), (BP + 0x38, 8, VP),
    (VECTOR + 40 + 7 * 16, 8, VIEW),
])
def test_owner_signal_type_chain_and_capture_changes_cannot_become_a_verified_route(address, size, replacement):
    value = memory()
    value.cells[(address, size)] = replacement.to_bytes(size, "little")
    with pytest.raises(SocialError):
        _clicked_connections(Reads(value), value.base, BUTTON, VIEW)


def test_direct_connection_is_explicit_and_stability_detects_live_change():
    value = memory()
    value.cells[(NODE + 88, 2)] = struct.pack("<H", 9)
    value.cells[(BUTTON + 0x140, 1)] = b"\x02"
    reads = Reads(value)
    result = _clicked_connections(reads, value.base, BUTTON, VIEW)
    assert result["connection_type"] == "direct" and result["pressed_bit_set"]
    value.cells[(SLOT + 16, 8)] = struct.pack("<Q", BUTTON)
    with pytest.raises(SocialError, match="native_object_changed"):
        reads.stable()


@pytest.mark.parametrize("stored", [0xFFF8, 0xFFF9])
def test_pinned_constructor_padding_is_ignored_without_relaxing_type_or_slot_checks(stored):
    value = memory()
    value.cells[(NODE + 88, 2)] = struct.pack("<H", stored)
    result = _clicked_connections(Reads(value), value.base, BUTTON, VIEW)
    assert result["connection_type"] == ("auto_same_thread" if stored & 7 == 0 else "direct")


def test_native_connection_diagnostic_rechecks_identity_and_preserves_existing_draft(monkeypatch):
    backend, ui, checks = setup(monkeypatch)
    ui.text = "SYNTHETIC-PRIVATE-DRAFT"
    monkeypatch.setattr(native_sender, "inspect_clicked_connection", lambda *args: {"stable": True, "submission_enabled": False})
    result = backend.connection_diagnostics()
    assert result["native_identity_verified"] and result["input_preserved"] and not result["input_empty"]
    assert len(checks) == 2 and not ui.calls and backend.initial is None
    assert ui.text not in str(result)

    def changed(*args):
        ui.text += "changed"
        return {"stable": True, "submission_enabled": False}

    monkeypatch.setattr(native_sender, "inspect_clicked_connection", changed)
    with pytest.raises(SocialError, match="surface_changed_during_connection_diagnostic"):
        backend.connection_diagnostics()
    assert not ui.calls


def test_connection_rpc_does_not_grant_an_input_or_submit_permit(monkeypatch):
    backend, ui, _ = setup(monkeypatch)
    backend.binding = backend.action = None
    ui.text = "SYNTHETIC-PRIVATE-DRAFT"
    monkeypatch.setattr(native_sender, "inspect_clicked_connection", lambda *args: {"stable": True, "submission_enabled": False})
    session = WorkerSession(backend)
    request = dict(version=1, operation_id="readonly", sequence=1, command="init", binding=asdict(BINDING), action=asdict(ACTION))
    session.handle(request)
    response = session.handle(dict(version=1, operation_id="readonly", sequence=2, command="connection_diagnostics"))
    assert response["result"]["connection_diagnostics"]["input_preserved"]
    assert session.initial is None and not session.focus_used and not session.input_used and not session.ready_to_submit
    with pytest.raises(SocialError, match="worker_permit_order_denied"):
        session.handle(dict(version=1, operation_id="readonly", sequence=3, command="submit_once"))
    assert not ui.calls


@pytest.mark.parametrize("change", [None, "instructions", "signal_count", "button_class", "root", "view"])
def test_outer_observer_requires_pinned_layout_signal_index_classes_and_shared_ancestors(monkeypatch, change):
    value = memory()
    for rva, signature in qt_connections.LAYOUT_PROOFS:
        value.cells[(value.base + rva, len(bytes.fromhex(signature)))] = bytes.fromhex(signature)
    lineage = ((qt_connections.BUTTON_META, 6), (0x9169410, 0), (0x90F2A98, 4), (0x90B53A8, 3))
    for index, (meta, count) in enumerate(lineage):
        parent = value.base + lineage[index + 1][0] if index + 1 < len(lineage) else 0
        metadata = value.base + 0x10000 + index * 0x100
        value.cells[(value.base + meta, 8)] = struct.pack("<Q", parent)
        value.cells[(value.base + meta + 16, 8)] = struct.pack("<Q", metadata)
        value.cells[(metadata + 0x34, 4)] = struct.pack("<I", count)
    input_object, root = 0xC03000, 0xC04000
    monkeypatch.setattr(qt_connections, "accessible_interface", lambda reads, base, key: key)
    monkeypatch.setattr(qt_connections, "object_from_interface", lambda reads, base, key: (BUTTON if key == 0x80000001 else input_object, 0))

    def meta(reads, base, obj):
        return dict(class_name="mmui::XOutlineButton" if obj == BUTTON else "mmui::ChatInputField",
                    meta_object_rva=qt_connections.BUTTON_META, qobject_vtable_rva=qt_connections.BUTTON_VTABLE)

    monkeypatch.setattr(qt_connections, "static_meta", meta)

    def parents(reads, base, obj):
        information = dict(class_name="mmui::ChatInputView", meta_object_rva=qt_connections.VIEW_META,
                           qobject_vtable_rva=qt_connections.VIEW_VTABLE)
        own_root = root + 8 if change == "root" and obj == BUTTON else root
        own_view = VIEW + 8 if change == "view" and obj == BUTTON else VIEW
        return dict(complete=True, entries=[{"class_name": "own"}, information, {"class_name": "mmui::MainWindow"}]), [obj, own_view, own_root]

    monkeypatch.setattr(qt_connections, "parent_chain", parents)
    if change == "instructions":
        rva, signature = qt_connections.LAYOUT_PROOFS[0]
        value.cells[(value.base + rva, len(bytes.fromhex(signature)))] = b"\0" * len(bytes.fromhex(signature))
    elif change == "signal_count":
        value.cells[(value.base + 0x10000 + 0x34, 4)] = struct.pack("<I", 5)
    elif change == "button_class":
        monkeypatch.setattr(qt_connections, "static_meta", lambda *args: dict(meta(*args), class_name="other"))
    if change is None:
        result = qt_connections.inspect_clicked_connection(value, [3, 0x80000001], [3, 0x80000002])
        assert result["stable"] and result["lookup_bytes"] < 65536 and result["methods_executed"] == 0
    else:
        with pytest.raises(SocialError):
            qt_connections.inspect_clicked_connection(value, [3, 0x80000001], [3, 0x80000002])
