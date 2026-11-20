"""Version-pinned, read-only Qt accessible-object lookup for identity research.

Runtime IDs are transient lookup inputs, never platform identities. No remote
functions are called. No heap enumeration, key or message-content access.
"""

import hashlib
import re
import struct

from ...social.models import SocialError

CACHE_INSTANCE_RVA = 0x1BB4430
CACHE_GLOBAL_RVA = 0xBD1B310
CACHE_VTABLE_RVA = 0x941AAF8
IMAGE_SIZE = 206352384
INSTANCE_PREFIX = bytes.fromhex("564883ec20488b05d46e160a4885c07406")
MESSAGE_PAGE_META_RVA = 152345960
MESSAGE_PAGE_VTABLE_RVA = 155548280
MESSAGE_KEY_METHOD_RVA = 0x1C68940
MESSAGE_KEY_OFFSET = 0x1D0
MESSAGE_KEY_ACCESS = bytes.fromhex("488db9d00100004883b9e801000010")


class UnsupportedGetter(SocialError):
    def __init__(self, code, getter_rva):
        super().__init__(code)
        self.getter_rva = getter_rva


class Reads:
    def __init__(self, memory, *, maximum=65536):
        self.memory, self.maximum, self.used = memory, maximum, 0
        self.observed = {}

    def read(self, address, size):
        if (type(address) is not int or not 0x10000 <= address < 0x800000000000
                or type(size) is not int or not 0 < size <= 4096 or address + size >= 0x800000000000
                or self.used + size > self.maximum):
            raise SocialError("native_object_read_bound")
        data = self.memory.read_bytes(address, size)
        if len(data) != size:
            raise SocialError("native_object_partial_read")
        self.used += size
        old = self.observed.setdefault((address, size), data)
        if old != data:
            raise SocialError("native_object_changed")
        return data

    def pointer(self, address):
        value = struct.unpack("<Q", self.read(address, 8))[0]
        if not 0x10000 <= value < 0x800000000000 or value % 8:
            raise SocialError("native_object_pointer_invalid")
        return value

    def nullable_pointer(self, address):
        value = struct.unpack("<Q", self.read(address, 8))[0]
        if value and (not 0x10000 <= value < 0x800000000000 or value % 8):
            raise SocialError("native_object_pointer_invalid")
        return value

    def stable(self):
        self.memory.validate()
        for (address, size), data in self.observed.items():
            if self.memory.read_bytes(address, size) != data:
                raise SocialError("native_object_changed")


def accessible_interface(reads, base, accessible_id):
    if type(accessible_id) is not int or not 0x80000000 <= accessible_id < 0xFFFFFFFE:
        raise SocialError("qt_accessible_id_unrecognized")
    if reads.read(base + CACHE_INSTANCE_RVA, len(INSTANCE_PREFIX)) != INSTANCE_PREFIX:
        raise SocialError("qt_cache_code_mismatch")
    cache = reads.pointer(base + CACHE_GLOBAL_RVA)
    if reads.pointer(cache) != base + CACHE_VTABLE_RVA:
        raise SocialError("qt_cache_vtable_mismatch")
    data = reads.pointer(cache + 0x10)
    header = reads.read(data, 40)
    buckets = struct.unpack_from("<Q", header, 8)[0]
    size, node_size = struct.unpack_from("<ii", header, 20)
    count, seed = struct.unpack_from("<II", header, 32)
    if not 0 < size <= 10000 or node_size != 24 or not 1 <= count <= 65536:
        raise SocialError("qt_cache_layout_unrecognized")
    hashed = accessible_id ^ seed
    node = reads.pointer(buckets + (hashed % count) * 8)
    seen = set()
    for _ in range(128):
        if node == data:
            raise SocialError("qt_accessible_id_not_found")
        if node in seen:
            raise SocialError("qt_cache_cycle")
        seen.add(node)
        raw = reads.read(node, 24)
        next_node, stored_hash, key, value = struct.unpack("<QIIQ", raw)
        if stored_hash == hashed and key == accessible_id:
            if not 0x10000 <= value < 0x800000000000 or value % 8:
                raise SocialError("native_object_pointer_invalid")
            return value
        node = next_node
    raise SocialError("qt_cache_chain_bound")


def object_from_interface(reads, base, interface):
    table = reads.pointer(interface)
    if not base <= table < base + IMAGE_SIZE:
        raise SocialError("qt_interface_vtable_outside_image")
    function = reads.pointer(table + 16)  # QAccessibleInterface::object(), version-pinned MSVC ABI.
    if not base <= function < base + IMAGE_SIZE:
        raise SocialError("qt_object_getter_outside_image")
    code = reads.read(function, 24)
    # Recognize only simple audited accessors; never execute the function.
    if code.startswith(bytes.fromhex("488b4108c3")):
        return reads.pointer(interface + 8), function - base
    if code.startswith(bytes.fromhex("488b4108488b00c3")):
        return reads.pointer(reads.pointer(interface + 8)), function - base
    if code == bytes.fromhex("488b4108488b084885c9740c8b490485c97405488b4008c3"):
        private = reads.pointer(interface + 8)
        weak = reads.pointer(private)
        if struct.unpack("<i", reads.read(weak + 4, 4))[0] == 0:
            raise SocialError("qt_accessible_object_deleted")
        return reads.pointer(private + 8), function - base
    raise UnsupportedGetter("qt_object_getter_unrecognized", function - base)


def static_meta(reads, base, obj):
    table = reads.pointer(obj)
    if not base <= table < base + IMAGE_SIZE:
        raise SocialError("qt_object_vtable_outside_image")
    getter = reads.pointer(table)
    if not base <= getter < base + IMAGE_SIZE:
        raise SocialError("qt_metaobject_getter_outside_image")
    code = reads.read(getter, 32)
    # Standard Qt 5 generated metaObject() with a dynamic-metaobject guard.
    prefix = bytes.fromhex("488b490848837928000f85")
    if code.startswith(prefix) and code[15:18] == bytes.fromhex("488d05") and code[22] == 0xC3:
        private = reads.pointer(obj + 8)
        if struct.unpack("<Q", reads.read(private + 0x28, 8))[0] != 0:
            raise SocialError("qt_dynamic_metaobject_not_supported")
        meta = getter + 22 + struct.unpack_from("<i", code, 18)[0]
    elif code.startswith(bytes.fromhex("488d05")) and code[7] == 0xC3:
        meta = getter + 7 + struct.unpack_from("<i", code, 3)[0]
    else:
        raise UnsupportedGetter("qt_metaobject_getter_unrecognized", getter - base)
    if not base <= meta < base + IMAGE_SIZE:
        raise SocialError("qt_metaobject_outside_image")
    strings = reads.pointer(meta + 8)
    if not base <= strings < base + IMAGE_SIZE - 24:
        raise SocialError("qt_static_strings_outside_image")
    header = reads.read(strings, 24)
    length = struct.unpack_from("<i", header, 4)[0]
    offset = struct.unpack_from("<q", header, 16)[0]
    if not 1 <= length <= 128 or not base <= strings + offset <= base + IMAGE_SIZE - length:
        raise SocialError("qt_class_string_unrecognized")
    name = reads.read(strings + offset, length)
    # Only source-code class names from the pinned module, never heap text.
    if not re.fullmatch(rb"[A-Za-z_][A-Za-z0-9_:]{0,127}", name):
        raise SocialError("qt_static_class_name_unrecognized")
    return {"class_name": name.decode("ascii"), "meta_object_rva": meta - base,
            "meta_getter_rva": getter - base, "qobject_vtable_rva": table - base}


def object_class(reads, base, obj):
    name = static_meta(reads, base, obj)["class_name"]
    if name in {"mmui::ChatSessionCell", "mmui::ChatInputField"}:
        return name
    raise SocialError("qt_native_class_mismatch")


def session_model_hypotheses(reads, base, obj):
    """Two static call-site layouts, only on the already verified selected row.

    Inspect interface method addresses, not arbitrary object strings. Addresses
    in the report are module-relative code locations for static follow-up.
    """
    result = []
    for offset in (0x268, 0x260):
        row = {"row_model_offset": offset, "status": "not_verified"}
        try:
            model = reads.pointer(obj + offset)
            holder = reads.pointer(model + 0x3E0)
            interface = reads.pointer(holder)
            table = reads.pointer(interface)
            if not base <= table < base + IMAGE_SIZE - 0x200:
                raise SocialError("session_model_vtable_outside_image")
            slots = []
            for slot in (0x130, 0x138, 0x140, 0x148, 0x150, 0x158, 0x160, 0x168, 0x170):
                function = reads.pointer(table + slot)
                if not base <= function < base + IMAGE_SIZE:
                    raise SocialError("session_model_method_outside_image")
                slots.append({"slot": slot, "function_rva": function - base})
            row.update(status="interface_observed", method_locations=slots)
        except SocialError as exc:
            row["reason"] = exc.code
        except OSError:
            row["reason"] = "session_model_unreadable"
        result.append(row)
    return result


def qarray_bytes(reads, data, *, unit=1, maximum=128):
    header = reads.read(data, 24)
    length = struct.unpack_from("<i", header, 4)[0]
    offset = struct.unpack_from("<q", header, 16)[0]
    if not 0 <= length <= maximum or not 24 <= offset <= 4096:
        raise SocialError("qt_property_string_bound")
    return reads.read(data + offset, length * unit) if length else b""


def named_properties(reads, obj):
    """Selected QObject's named properties only; never dump names or values."""
    private = reads.pointer(obj + 8)
    if reads.pointer(private + 8) != obj:
        raise SocialError("qt_private_owner_mismatch")
    extra = reads.nullable_pointer(private + 0x30)
    if not extra:
        return {"property_count": 0, "identity_fields": [], "property_shapes": []}
    names = reads.pointer(extra + 8)  # Qt 5.15 ExtraData includes userData first.
    header = reads.read(names, 16)
    _, allocated, begin, end = struct.unpack("<iiii", header)
    if not 0 <= begin <= end <= allocated <= 4096 or end - begin > 64:
        raise SocialError("qt_property_list_bound")
    count = end - begin
    result = {"property_count": count, "identity_fields": [], "property_shapes": []}
    if count == 0:
        return result
    values = reads.pointer(extra + 16)
    values_header = reads.read(values, 24)
    value_count = struct.unpack_from("<i", values_header, 4)[0]
    value_offset = struct.unpack_from("<q", values_header, 16)[0]
    if value_count != count or not 24 <= value_offset <= 4096:
        raise SocialError("qt_property_value_layout")
    allowed = {b"username", b"user_name", b"wxid", b"session_id", b"sessionId", b"chat_id", b"conversation_id", b"peer_id", b"talker"}
    for index in range(count):
        name = qarray_bytes(reads, reads.pointer(names + 16 + (begin + index) * 8))
        at = values + value_offset + index * 16
        variant = reads.read(at, 16)
        flags = struct.unpack_from("<I", variant, 8)[0]
        kind, shared = flags & 0x3FFFFFFF, bool(flags & 0x40000000)
        result["property_shapes"].append({"name_sha256": hashlib.sha256(name).hexdigest(), "variant_type": kind})
        if name not in allowed:
            continue
        field = {"field": name.decode("ascii"), "variant_type": kind, "is_filehelper": False}
        if kind in (10, 12) and not shared:  # QString / QByteArray, no arbitrary user type traversal.
            raw = qarray_bytes(reads, reads.pointer(at), unit=2 if kind == 10 else 1)
            field["is_filehelper"] = raw == "filehelper".encode("utf-16-le" if kind == 10 else "ascii")
        result["identity_fields"].append(field)
    return result


def parent_chain(reads, base, obj, *, maximum=24):
    """Only QObject parents of a selected surface, no children or global objects."""
    if type(maximum) is not int or not 1 <= maximum <= 24:
        raise SocialError("qt_parent_bound")
    seen, entries, objects = set(), [], []
    current = obj
    for _ in range(maximum):
        if current in seen:
            raise SocialError("qt_parent_cycle")
        seen.add(current)
        info = static_meta(reads, base, current)
        private = reads.pointer(current + 8)
        if reads.pointer(private + 8) != current:
            raise SocialError("qt_private_owner_mismatch")
        objects.append(current)
        info["named_properties"] = named_properties(reads, current)
        entries.append(info)
        if info["class_name"] == "mmui::MainWindow":
            return {"complete": True, "stop": "window_root", "entries": entries}, objects
        current = reads.nullable_pointer(private + 16)
        if not current:
            return {"complete": True, "stop": "no_parent", "entries": entries}, objects
    return {"complete": False, "stop": "parent_limit", "entries": entries}, objects


def _page_session_bytes(reads, base, page, info):
    """Shared bounded field decoder; callers control disclosure and target policy."""
    if (info.get("class_name") != "mmui::ChatMessagePage" or info.get("meta_object_rva") != MESSAGE_PAGE_META_RVA
            or info.get("qobject_vtable_rva") != MESSAGE_PAGE_VTABLE_RVA):
        raise SocialError("chat_page_layout_mismatch")
    if reads.read(base + MESSAGE_KEY_METHOD_RVA + 14, len(MESSAGE_KEY_ACCESS)) != MESSAGE_KEY_ACCESS:
        raise SocialError("chat_page_key_code_mismatch")
    raw = reads.read(page + MESSAGE_KEY_OFFSET, 32)
    length, capacity = struct.unpack_from("<QQ", raw, 16)
    if not 1 <= length <= 128 or capacity < length or capacity > 1024:
        raise SocialError("chat_page_key_string_bound")
    if capacity <= 15:
        if capacity != 15:
            raise SocialError("chat_page_key_layout_mismatch")
        value = raw[:length]
    else:
        value = reads.read(reads.pointer(page + MESSAGE_KEY_OFFSET), length)
    if not re.fullmatch(rb"[A-Za-z0-9_.@-]{1,128}", value):
        raise SocialError("chat_page_key_not_canonical_identifier")
    return value


def page_session_key(reads, base, page, info):
    """Existing filehelper-only projection; never disclose a different peer."""
    value = _page_session_bytes(reads, base, page, info)
    matched = value == b"filehelper"
    return {"source": "ChatMessagePage.OnNewSessionDataLoaded.key", "field_offset": MESSAGE_KEY_OFFSET,
            "matches_filehelper": matched, "canonical_username": "filehelper" if matched else None}


def runtime_accessible_id(runtime_ids):
    if (type(runtime_ids) is not list or not 2 <= len(runtime_ids) <= 8
            or any(type(v) is not int or not -(2**31) <= v < 2**32 for v in runtime_ids)):
        raise SocialError("qt_runtime_id_shape_unrecognized")
    return runtime_ids[-1] & 0xFFFFFFFF


def selected_object(memory, runtime_ids, *, inspect_model=False, inspect_parents=False, input_runtime_ids=None,
                    inspect_session_key=False):
    accessible_id = runtime_accessible_id(runtime_ids)
    memory.validate()
    reads = Reads(memory)
    interface = accessible_interface(reads, memory.base, accessible_id)
    obj, getter_rva = object_from_interface(reads, memory.base, interface)
    name = object_class(reads, memory.base, obj)
    models = None
    properties = None
    parents = None
    business_key = None
    if inspect_model:
        if name != "mmui::ChatSessionCell":
            raise SocialError("selected_session_class_mismatch")
        models = session_model_hypotheses(reads, memory.base, obj)
        properties = named_properties(reads, obj)
    if inspect_parents:
        if name != "mmui::ChatSessionCell":
            raise SocialError("selected_session_class_mismatch")
        row_chain, row_objects = parent_chain(reads, memory.base, obj)
        parents = {"selected_row": row_chain}
        if input_runtime_ids is not None:
            input_interface = accessible_interface(reads, memory.base, runtime_accessible_id(input_runtime_ids))
            input_obj, _ = object_from_interface(reads, memory.base, input_interface)
            if object_class(reads, memory.base, input_obj) != "mmui::ChatInputField":
                raise SocialError("chat_input_class_mismatch")
            input_chain, input_objects = parent_chain(reads, memory.base, input_obj)
            shared = set(row_objects) & set(input_objects)
            parents["chat_input"] = input_chain
            parents["shared_ancestor_classes"] = [entry["class_name"] for entry, address in zip(row_chain["entries"], row_objects, strict=True)
                                                  if address in shared]
            if inspect_session_key:
                if (not row_chain["complete"] or not input_chain["complete"]
                        or "mmui::MainWindow" not in parents["shared_ancestor_classes"]):
                    raise SocialError("native_session_lineage_incomplete")
                pages = [(entry, address) for entry, address in zip(input_chain["entries"], input_objects, strict=True)
                         if entry["class_name"] == "mmui::ChatMessagePage"]
                if len(pages) != 1:
                    raise SocialError("native_current_page_not_unique")
                info, page = pages[0]
                business_key = page_session_key(reads, memory.base, page, info)
    if inspect_session_key and business_key is None:
        raise SocialError("native_session_key_requires_input_lineage")
    reads.stable()
    result = {"native_object_class": name, "lookup_bytes": reads.used,
              "object_getter_rva": getter_rva, "stable": True,
              "canonical_conversation_verified": False}
    if models is not None:
        result["session_model_hypotheses"] = models
        result["named_properties"] = properties
    if parents is not None:
        result["parent_chains"] = parents
    if business_key is not None:
        result["business_session_key"] = business_key
    return result


def verify_current_filehelper(memory, selected_runtime_ids, input_runtime_ids, *, account_reader, expected_account_fingerprint):
    """Reusable native gate; callers supply a trusted keyless account reader.

    Expected fingerprint comes from the selected account's authenticated DB
    evidence, never a UI label. Neither transient IDs nor account names escape
    into the result. This observation alone does not permit an input action.
    """
    if (not isinstance(expected_account_fingerprint, str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected_account_fingerprint)):
        raise SocialError("invalid_expected_account_fingerprint")
    memory.validate()
    account = account_reader()
    if (not isinstance(account, str) or not re.fullmatch(r"[A-Za-z0-9_.@-]{1,128}", account)
            or hashlib.sha256(account.encode("ascii")).hexdigest() != expected_account_fingerprint):
        raise SocialError("account_evidence_mismatch")
    native = selected_object(memory, selected_runtime_ids, inspect_parents=True,
                             input_runtime_ids=input_runtime_ids, inspect_session_key=True)
    if not native["stable"] or not native["business_session_key"]["matches_filehelper"]:
        raise SocialError("native_current_session_not_filehelper")
    memory.validate()
    if account_reader() != account:
        raise SocialError("account_changed_during_probe")
    native.update(account_fingerprint=expected_account_fingerprint, matched_database_account=True,
                  canonical_conversation_verified=True)
    return native


def inspect_button_dispatch(memory, runtime_ids):
    """Read bounded interface method locations, never invoke or grant actions.

    The secondary vtable offset is a research candidate. A static trace must
    identify its methods before interpreting it as QAccessibleActionInterface.
    """
    memory.validate()
    reads = Reads(memory)
    interface = accessible_interface(reads, memory.base, runtime_accessible_id(runtime_ids))
    obj, getter = object_from_interface(reads, memory.base, interface)
    info = static_meta(reads, memory.base, obj)
    if info["class_name"] != "mmui::XOutlineButton" or info["meta_object_rva"] != 152477496:
        raise SocialError("button_static_class_mismatch")
    primary = reads.pointer(interface)
    candidate = reads.pointer(interface + 16)
    if not memory.base <= candidate < memory.base + IMAGE_SIZE - 48:
        raise SocialError("button_secondary_interface_not_verified")
    methods = []
    for offset in (0, 8, 16, 24, 32, 40):
        function = reads.pointer(candidate + offset)
        if not memory.base <= function < memory.base + IMAGE_SIZE - 128:
            raise SocialError("button_secondary_method_outside_image")
        size = 128 if offset == 32 else 32
        code = reads.read(function, size)
        methods.append(dict(offset=offset, function_rva=function - memory.base,
                            code_bytes=size, prefix_sha256=hashlib.sha256(code).hexdigest()))
    cast = reads.pointer(primary + 19 * 8)
    if not memory.base <= cast < memory.base + IMAGE_SIZE - 32:
        raise SocialError("button_interface_cast_outside_image")
    cast_code = reads.read(cast, 32)
    reads.stable()
    return dict(qobject=info, primary_interface_vtable_rva=primary - memory.base,
                object_getter_rva=getter, secondary_interface_candidate_offset=16,
                secondary_vtable_rva=candidate - memory.base, methods=methods,
                interface_cast_rva=cast - memory.base, interface_cast_sha256=hashlib.sha256(cast_code).hexdigest(),
                lookup_bytes=reads.used, stable=True, methods_executed=0)
