"""Selected QObject lineage and binding research, using synthetic memory only."""

import hashlib
import struct

import pytest

from tests.test_wechat_qt_memory import Memory
from wechat_agent.bridges.wechat.qt_memory import (
    MESSAGE_KEY_ACCESS,
    MESSAGE_KEY_METHOD_RVA,
    MESSAGE_KEY_OFFSET,
    MESSAGE_PAGE_META_RVA,
    MESSAGE_PAGE_VTABLE_RVA,
    Reads,
    page_session_key,
    parent_chain,
    selected_object,
    verify_current_filehelper,
)
from wechat_agent.social.models import SocialError


def add_parent(memory, obj, private, parent, name, index):
    at = memory.base + 0x10000 + index * 0x1000
    data = name.encode("ascii")
    memory.cells[(obj, 8)] = struct.pack("<Q", at)
    memory.cells[(at, 8)] = struct.pack("<Q", at + 0x100)
    memory.cells[(at + 0x100, 32)] = (bytes.fromhex("488d05") + struct.pack("<i", 0x200 - 0x107) + b"\xc3").ljust(32, b"\xcc")
    memory.cells[(at + 0x208, 8)] = struct.pack("<Q", at + 0x300)
    memory.cells[(at + 0x300, 24)] = struct.pack("<iii4xq", 1, len(data), 0, 24)
    memory.cells[(at + 0x318, len(data))] = data
    memory.cells[(obj + 8, 8)] = struct.pack("<Q", private)
    memory.cells[(private + 8, 8)] = struct.pack("<Q", obj)
    memory.cells[(private + 16, 8)] = struct.pack("<Q", parent)
    memory.cells[(private + 0x30, 8)] = bytes(8)


def lineage():
    memory = Memory()
    add_parent(memory, memory.obj, 0xD00000, 0xE00000, "mmui::ChatSessionCell", 0)
    add_parent(memory, 0xE00000, 0xD10000, 0xE10000, "mmui::ChatSessionList", 1)
    add_parent(memory, 0xE10000, 0xD20000, 0, "mmui::MainWindow", 2)
    return memory


def test_selected_lineage_checks_owners_stability_and_returns_only_static_metadata():
    memory = lineage()
    reads = Reads(memory)
    result, objects = parent_chain(reads, memory.base, memory.obj)
    assert result["complete"] and result["stop"] == "window_root"
    assert [r["class_name"] for r in result["entries"]] == ["mmui::ChatSessionCell", "mmui::ChatSessionList", "mmui::MainWindow"]
    assert len(objects) == 3 and reads.used < 2048
    assert all(r["named_properties"]["property_count"] == 0 for r in result["entries"])
    reads.stable()
    memory.cells[(0xD10010, 8)] = bytes(8)
    with pytest.raises(SocialError, match="native_object_changed"):
        reads.stable()


def test_lineage_does_not_report_truncated_or_corrupt_graph_as_complete():
    memory = lineage()
    limited, _ = parent_chain(Reads(memory), memory.base, memory.obj, maximum=2)
    assert not limited["complete"] and limited["stop"] == "parent_limit"
    memory.cells[(0xD10010, 8)] = struct.pack("<Q", memory.obj)
    with pytest.raises(SocialError, match="qt_parent_cycle"):
        parent_chain(Reads(memory), memory.base, memory.obj)
    memory = lineage()
    memory.cells[(0xD10008, 8)] = struct.pack("<Q", memory.obj)
    with pytest.raises(SocialError, match="qt_private_owner_mismatch"):
        parent_chain(Reads(memory), memory.base, memory.obj)


def page_memory(value=b"filehelper", *, heap=False):
    memory = Memory()
    memory.cells[(memory.base + MESSAGE_KEY_METHOD_RVA + 14, len(MESSAGE_KEY_ACCESS))] = MESSAGE_KEY_ACCESS
    at = memory.obj + MESSAGE_KEY_OFFSET
    prefix = struct.pack("<Q", 0xD00000).ljust(16, b"\x00") if heap else value.ljust(16, b"\x00")
    memory.cells[(at, 32)] = prefix + struct.pack("<QQ", len(value), 31 if heap else 15)
    if heap:
        memory.cells[(at, 8)] = struct.pack("<Q", 0xD00000)
        memory.cells[(0xD00000, len(value))] = value
    info = dict(class_name="mmui::ChatMessagePage", meta_object_rva=MESSAGE_PAGE_META_RVA,
                qobject_vtable_rva=MESSAGE_PAGE_VTABLE_RVA)
    return memory, info


@pytest.mark.parametrize("heap", [False, True])
def test_business_key_requires_pinned_page_code_and_stable_string(heap):
    memory, info = page_memory(heap=heap)
    reads = Reads(memory)
    result = page_session_key(reads, memory.base, memory.obj, info)
    assert result["matches_filehelper"] and result["canonical_username"] == "filehelper"
    reads.stable()
    memory.cells[(memory.obj + MESSAGE_KEY_OFFSET, 32)] = bytes(32)
    with pytest.raises(SocialError, match="native_object_changed"):
        reads.stable()
    memory, info = page_memory(b"other_canonical_id", heap=True)
    result = page_session_key(Reads(memory), memory.base, memory.obj, info)
    assert not result["matches_filehelper"] and result["canonical_username"] is None
    assert "other_canonical_id" not in str(result)


@pytest.mark.parametrize("case", ["class", "meta", "vtable", "code", "length", "capacity", "text"])
def test_business_key_rejects_unknown_page_layout_and_noncanonical_text(case):
    memory, info = page_memory()
    if case in {"class", "meta", "vtable"}:
        field = {"class": "class_name", "meta": "meta_object_rva", "vtable": "qobject_vtable_rva"}[case]
        info[field] = "other" if case == "class" else 0
    elif case == "code":
        memory.cells[(memory.base + MESSAGE_KEY_METHOD_RVA + 14, len(MESSAGE_KEY_ACCESS))] = bytes(len(MESSAGE_KEY_ACCESS))
    else:
        raw = bytearray(memory.cells[(memory.obj + MESSAGE_KEY_OFFSET, 32)])
        if case == "length":
            struct.pack_into("<Q", raw, 16, 129)
        elif case == "capacity":
            struct.pack_into("<Q", raw, 24, 14)
        else:
            raw[0] = 0xFF
        memory.cells[(memory.obj + MESSAGE_KEY_OFFSET, 32)] = bytes(raw)
    with pytest.raises(SocialError):
        page_session_key(Reads(memory), memory.base, memory.obj, info)


def complete_surface():
    memory, _ = page_memory()
    root, page, chat_input = 0xE10000, memory.obj, 0xE00000
    row = 0xF00000
    memory.cells[(memory.interface + 8, 8)] = struct.pack("<Q", row)
    add_parent(memory, row, 0xD10000, root, "mmui::ChatSessionCell", 0)
    add_parent(memory, root, 0xD20000, 0, "mmui::MainWindow", 1)
    add_parent(memory, chat_input, 0xD30000, page, "mmui::ChatInputField", 2)
    add_parent(memory, page, 0xD40000, root, "mmui::ChatMessagePage", 3)
    # The page's static layout must match the real pinned metadata/vtable.
    old_table = memory.base + 0x13000
    table = memory.base + MESSAGE_PAGE_VTABLE_RVA
    getter = old_table + 0x100
    memory.cells[(page, 8)] = struct.pack("<Q", table)
    memory.cells[(table, 8)] = struct.pack("<Q", getter)
    memory.cells[(getter, 32)] = (bytes.fromhex("488d05") + struct.pack("<i", memory.base + MESSAGE_PAGE_META_RVA - getter - 7)
                                + b"\xc3").ljust(32, b"\xcc")
    memory.cells[(memory.base + MESSAGE_PAGE_META_RVA + 8, 8)] = struct.pack("<Q", old_table + 0x300)
    # A second accessible node points at the input. Synthetic IDs remain RAM-only.
    second_id, second_node, second_interface = memory.accessible_id + 1, 0x710000, 0x810000
    memory.cells[(memory.data, 40)] = struct.pack("<QQiiiHHII", memory.data, memory.buckets, 1, 2, 24, 3, 3, 7, 9)
    memory.cells[(memory.buckets + ((second_id ^ 9) % 7) * 8, 8)] = struct.pack("<Q", second_node)
    memory.cells[(second_node, 24)] = struct.pack("<QIIQ", memory.data, second_id ^ 9, second_id, second_interface)
    memory.cells[(second_interface, 8)] = struct.pack("<Q", memory.base + 0x1000)
    memory.cells[(second_interface + 8, 8)] = struct.pack("<Q", chat_input)
    return memory, [3, memory.accessible_id], [3, second_id]


def test_current_page_key_requires_unique_input_page_and_shared_window():
    memory, selected, chat_input = complete_surface()
    found = selected_object(memory, selected, inspect_parents=True, input_runtime_ids=chat_input, inspect_session_key=True)
    assert found["business_session_key"]["matches_filehelper"] and found["stable"]
    assert not found["canonical_conversation_verified"]  # Account validation is a separate mandatory gate.
    memory.cells[(0xD40010, 8)] = bytes(8)
    with pytest.raises(SocialError, match="native_session_lineage_incomplete"):
        selected_object(memory, selected, inspect_parents=True, input_runtime_ids=chat_input, inspect_session_key=True)
    memory, selected, chat_input = complete_surface()
    with pytest.raises(SocialError, match="native_session_key_requires_input_lineage"):
        selected_object(memory, selected, inspect_parents=True, inspect_session_key=True)


def test_account_gate_runs_before_and_after_native_read_and_refuses_changed_target():
    memory, selected, chat_input = complete_surface()
    account = "synthetic_account"
    fingerprint = hashlib.sha256(account.encode()).hexdigest()
    observed = []

    def account_reader():
        observed.append(memory.checks)
        return account

    found = verify_current_filehelper(memory, selected, chat_input, account_reader=account_reader,
                                     expected_account_fingerprint=fingerprint)
    assert found["canonical_conversation_verified"] and found["matched_database_account"]
    assert found["account_fingerprint"] == fingerprint and observed[0] < observed[1]
    assert account not in str(found)
    for values, expected, reason in (([account, "changed"], fingerprint, "account_changed"),
                                    (["wrong"], fingerprint, "account_evidence_mismatch"),
                                    ([account], "invalid", "invalid_expected_account")):
        sequence = iter(values)
        with pytest.raises(SocialError, match=reason):
            verify_current_filehelper(memory, selected, chat_input, account_reader=lambda sequence=sequence: next(sequence),
                                      expected_account_fingerprint=expected)
    memory.cells[(memory.obj + MESSAGE_KEY_OFFSET, 32)] = b"other_peer".ljust(16, b"\x00") + struct.pack("<QQ", 10, 15)
    with pytest.raises(SocialError, match="native_current_session_not_filehelper"):
        verify_current_filehelper(memory, selected, chat_input, account_reader=lambda: account,
                                  expected_account_fingerprint=fingerprint)
