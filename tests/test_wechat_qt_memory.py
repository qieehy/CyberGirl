"""Transient Qt-ID lookup against synthetic memory, never a live native object."""

import struct

import pytest

from wechat_agent.bridges.wechat.qt_memory import (
    CACHE_GLOBAL_RVA,
    CACHE_INSTANCE_RVA,
    CACHE_VTABLE_RVA,
    INSTANCE_PREFIX,
    selected_object,
)
from wechat_agent.social.models import SocialError


class Memory:
    base = 0x100000000
    cache, data, buckets, node, interface, obj = 0x400000, 0x500000, 0x600000, 0x700000, 0x800000, 0x900000
    accessible_id = 0x80000001

    def __init__(self):
        self.checks = 0
        seed, count = 9, 7
        name = b"mmui::ChatSessionCell"
        self.cells = {
            (self.base + CACHE_INSTANCE_RVA, len(INSTANCE_PREFIX)): INSTANCE_PREFIX,
            (self.base + CACHE_GLOBAL_RVA, 8): struct.pack("<Q", self.cache),
            (self.cache, 8): struct.pack("<Q", self.base + CACHE_VTABLE_RVA),
            (self.cache + 16, 8): struct.pack("<Q", self.data),
            (self.data, 40): struct.pack("<QQiiiHHII", self.data, self.buckets, 1, 1, 24, 3, 3, count, seed),
            (self.buckets + ((self.accessible_id ^ seed) % count) * 8, 8): struct.pack("<Q", self.node),
            (self.node, 24): struct.pack("<QIIQ", self.data, self.accessible_id ^ seed, self.accessible_id, self.interface),
            (self.interface, 8): struct.pack("<Q", self.base + 0x1000),
            (self.base + 0x1010, 8): struct.pack("<Q", self.base + 0x2000),
            (self.base + 0x2000, 24): bytes.fromhex("488b4108c3").ljust(24, b"\xcc"),
            (self.interface + 8, 8): struct.pack("<Q", self.obj),
            (self.obj, 8): struct.pack("<Q", self.base + 0x3000),
            (self.base + 0x3000, 8): struct.pack("<Q", self.base + 0x4000),
            (self.base + 0x4000, 32): (b"\x48\x8d\x05" + struct.pack("<i", 0x5000 - 0x4007) + b"\xc3").ljust(32, b"\xcc"),
            (self.base + 0x5008, 8): struct.pack("<Q", self.base + 0x6000),
            (self.base + 0x6000, 24): struct.pack("<iii4xq", 1, len(name), 0, 24),
            (self.base + 0x6018, len(name)): name,
        }

    def validate(self):
        self.checks += 1

    def read_bytes(self, address, size):
        return self.cells[(address, size)]


def test_qt_id_maps_to_fixed_class_without_exporting_id_or_pointers_and_checks_stability():
    memory = Memory()
    result = selected_object(memory, [3, -2147483647])
    assert result["native_object_class"] == "mmui::ChatSessionCell"
    assert result["stable"] and not result["canonical_conversation_verified"]
    assert result["lookup_bytes"] < 1024 and memory.checks == 2
    assert not {"runtime_id", "pointer", "username"} & result.keys()
    modeled = Memory()
    modeled.cells[(modeled.obj + 0x268, 8)] = struct.pack("<Q", 0xA00000)
    modeled.cells[(modeled.obj + 0x260, 8)] = bytes(8)
    modeled.cells[(0xA003E0, 8)] = struct.pack("<Q", 0xB00000)
    modeled.cells[(0xB00000, 8)] = struct.pack("<Q", 0xC00000)
    modeled.cells[(0xC00000, 8)] = struct.pack("<Q", modeled.base + 0x7000)
    for slot in range(0x130, 0x178, 8):
        modeled.cells[(modeled.base + 0x7000 + slot, 8)] = struct.pack("<Q", modeled.base + 0x8000)
    modeled.cells[(modeled.obj + 8, 8)] = struct.pack("<Q", 0xD00000)
    modeled.cells[(0xD00008, 8)] = struct.pack("<Q", modeled.obj)
    modeled.cells[(0xD00030, 8)] = struct.pack("<Q", 0xE00000)
    modeled.cells[(0xE00008, 8)] = struct.pack("<Q", 0xF00000)
    modeled.cells[(0xF00000, 16)] = struct.pack("<iiii", -1, 0, 0, 0)
    model_result = selected_object(modeled, [3, -2147483647], inspect_model=True)
    assert model_result["session_model_hypotheses"][0]["status"] == "interface_observed"
    assert model_result["session_model_hypotheses"][1]["reason"] == "native_object_pointer_invalid"
    assert not model_result["canonical_conversation_verified"]
    assert model_result["named_properties"] == {"property_count": 0, "identity_fields": [], "property_shapes": []}
    modeled.cells[(0xF00000, 16)] = struct.pack("<iiii", 1, 1, 0, 1)
    modeled.cells[(0xF00010, 8)] = struct.pack("<Q", modeled.base + 0xA000)
    modeled.cells[(modeled.base + 0xA000, 24)] = struct.pack("<iii4xq", 1, 8, 0, 24)
    modeled.cells[(modeled.base + 0xA018, 8)] = b"username"
    modeled.cells[(0xE00010, 8)] = struct.pack("<Q", 0xF10000)
    modeled.cells[(0xF10000, 24)] = struct.pack("<iii4xq", 1, 1, 0, 24)
    modeled.cells[(0xF10018, 16)] = struct.pack("<QII", modeled.base + 0xB000, 10, 0)
    modeled.cells[(0xF10018, 8)] = struct.pack("<Q", modeled.base + 0xB000)
    modeled.cells[(modeled.base + 0xB000, 24)] = struct.pack("<iii4xq", 1, 10, 0, 24)
    modeled.cells[(modeled.base + 0xB018, 20)] = "filehelper".encode("utf-16-le")
    found = selected_object(modeled, [3, -2147483647], inspect_model=True)
    assert found["named_properties"]["identity_fields"] == [{"field": "username", "variant_type": 10, "is_filehelper": True}]
    assert not found["canonical_conversation_verified"]  # A research match never grants an action permit.
    meta_guarded = Memory()
    meta_guarded.cells[(meta_guarded.base + 0x4000, 32)] = (
        bytes.fromhex("488b490848837928000f85") + bytes(4) + bytes.fromhex("488d05")
        + struct.pack("<i", 0x5000 - 0x4016) + b"\xc3").ljust(32, b"\xcc")
    meta_guarded.cells[(meta_guarded.obj + 8, 8)] = struct.pack("<Q", 0xD00000)
    meta_guarded.cells[(0xD00028, 8)] = bytes(8)
    assert selected_object(meta_guarded, [3, -2147483647])["native_object_class"] == "mmui::ChatSessionCell"
    meta_guarded.cells[(0xD00028, 8)] = struct.pack("<Q", 0xD01000)
    with pytest.raises(SocialError, match="qt_dynamic_metaobject_not_supported"):
        selected_object(meta_guarded, [3, -2147483647])
    guarded = Memory()
    guarded.cells[(guarded.base + 0x2000, 24)] = bytes.fromhex("488b4108488b084885c9740c8b490485c97405488b4008c3")
    guarded.cells[(guarded.interface + 8, 8)] = struct.pack("<Q", 0xA00000)
    guarded.cells[(0xA00000, 8)] = struct.pack("<Q", 0xB00000)
    guarded.cells[(0xB00004, 4)] = struct.pack("<i", -1)
    guarded.cells[(0xA00008, 8)] = struct.pack("<Q", guarded.obj)
    assert selected_object(guarded, [3, -2147483647])["native_object_class"] == "mmui::ChatSessionCell"
    guarded.cells[(0xB00004, 4)] = struct.pack("<i", 0)
    with pytest.raises(SocialError, match="qt_accessible_object_deleted"):
        selected_object(guarded, [3, -2147483647])
    original = memory.read_bytes
    reads = [0]

    def changing(address, size):
        if address == memory.base + CACHE_GLOBAL_RVA:
            reads[0] += 1
            if reads[0] == 2:
                return struct.pack("<Q", memory.cache + 8)
        return original(address, size)

    memory.read_bytes = changing
    with pytest.raises(SocialError, match="native_object_changed"):
        selected_object(memory, [3, -2147483647])


def test_qt_lookup_rejects_wrong_code_layout_cycle_and_unknown_accessor():
    for case in ("id", "code", "vtable", "layout", "cycle", "accessor"):
        memory = Memory()
        runtime = [3, -2147483647]
        if case == "id":
            runtime[-1] = 123
        elif case == "code":
            memory.cells[(memory.base + CACHE_INSTANCE_RVA, len(INSTANCE_PREFIX))] = b"x" * len(INSTANCE_PREFIX)
        elif case == "vtable":
            memory.cells[(memory.cache, 8)] = struct.pack("<Q", memory.base + CACHE_VTABLE_RVA + 8)
        elif case == "layout":
            raw = bytearray(memory.cells[(memory.data, 40)])
            struct.pack_into("<I", raw, 32, 100000)
            memory.cells[(memory.data, 40)] = bytes(raw)
        elif case == "cycle":
            memory.cells[(memory.node, 24)] = struct.pack("<QIIQ", memory.node, 0, 1, memory.interface)
        elif case == "accessor":
            memory.cells[(memory.base + 0x2000, 24)] = b"x" * 24
        with pytest.raises(SocialError):
            selected_object(memory, runtime)
