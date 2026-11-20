"""Explicit, bounded WCDB configuration probe for ONE already selected DB page.

Modified from wechatauto-replica db.py at 6e603974bbac3bda93d88bbd6995510ae2547f5c,
Apache-2.0; see docs/WECHAT_DB_SOURCE_NOTICE.md. No general key export/cache.
"""

import ctypes
import re
import struct
import time

from ...social.models import SocialError
from .keyprobe import verify_page_key

CONFIG_NAME = b"com.Tencent.WCDB.Config.Cipher"
XOR_MASK = bytes.fromhex("d2c7442458020000004889442450488b450048844c2448488944254048584c24")


def config_pairs(image, sections, module_base):
    matches, start = [], 0
    while (offset := image.find(CONFIG_NAME + b"\0", start)) >= 0:
        start = offset + 1
        section = next((s for s in sections if s["file_offset"] <= offset < s["file_offset"] + s["raw_size"]), None)
        if section is not None:
            address = module_base + section["rva"] + offset - section["file_offset"]
            matches.append(struct.pack("<QQ", address, len(CONFIG_NAME)))
    if not 1 <= len(matches) <= 4:
        raise SocialError("wcdb_anchor_not_bounded")
    return tuple(matches)


def key_candidates(blob):
    if not 1 <= len(blob) <= 1024:
        raise SocialError("wcdb_blob_bound")
    decoded = bytes(v ^ XOR_MASK[i % len(XOR_MASK)] for i, v in enumerate(blob))
    for match in re.finditer(rb"[xX]'([0-9a-fA-F]{64,192})'", decoded):
        run = match[1]
        starts = {0}
        if len(run) > 96:
            starts.update(range(0, len(run) - 63, 32))
            starts.add(len(run) - 64)
        for start in sorted(starts):
            key = bytes.fromhex(run[start:start + 64].decode("ascii"))
            yield key, None
            if start + 96 <= len(run):
                yield key, bytes.fromhex(run[start + 64:start + 96].decode("ascii"))


def scan_selected_page(memory, page_type, pairs, page, *, max_bytes=512 * 1024 * 1024,
                       seconds=30, max_candidates=256):
    """Read committed private RW pages in bounded chunks; return only an HMAC match.

    Never examine another PID, account, source page or file. Fail closed at any bound.
    Memory is retained for a chunk only, not dumped or persisted.
    """
    deadline, consumed, regions, failed_reads, objects = time.monotonic() + seconds, 0, 0, 0, 0
    tested = set()
    address = 0
    while address < 0x800000000000:
        if time.monotonic() >= deadline:
            raise SocialError("wcdb_probe_time_bound")
        page_info = page_type()
        size = memory.k.VirtualQueryEx(memory.handle, address, ctypes.byref(page_info), ctypes.sizeof(page_info))
        if size == 0:
            break
        if size != ctypes.sizeof(page_info) or page_info.RegionSize <= 0:
            raise SocialError("wcdb_region_query_failed")
        base, end = page_info.BaseAddress, page_info.BaseAddress + page_info.RegionSize
        if end <= address:
            raise SocialError("wcdb_region_order_invalid")
        address = end
        regions += 1
        if regions > 100000:
            raise SocialError("wcdb_region_bound")
        if page_info.State != 0x1000 or page_info.Type != 0x20000 or page_info.Protect not in (4, 8):
            continue
        carry, cursor = b"", base
        while cursor < end:
            if time.monotonic() >= deadline:
                raise SocialError("wcdb_probe_time_bound")
            size = min(1024 * 1024, end - cursor)
            consumed += size
            if consumed > max_bytes:
                raise SocialError("wcdb_probe_byte_bound")
            try:
                chunk = memory.read_bytes(cursor, size)
            except OSError:
                failed_reads += 1
                carry, cursor = b"", cursor + size
                continue
            data, data_base = carry + chunk, cursor - len(carry)
            for pair in pairs:
                start = 0
                while (offset := data.find(pair, start)) >= 0:
                    start = offset + 1
                    location = data_base + offset
                    if location % 8:
                        continue
                    objects += 1
                    if objects > 2048:
                        raise SocialError("wcdb_object_bound")
                    try:
                        node = memory.read_bytes(location - 16, 64)
                        if node[16:32] != pair:
                            continue
                        config = struct.unpack_from("<Q", node, 40)[0]
                        if not 0x10000 <= config < 0x800000000000:
                            continue
                        obj = memory.read_bytes(config + 0x88, 40)
                        pointer, length = struct.unpack_from("<QQ", obj, 8)
                        if not 1 <= length <= 1024 or not 0x10000 <= pointer < 0x800000000000:
                            continue
                        blob = memory.read_bytes(pointer, length)
                    except OSError:
                        failed_reads += 1
                        continue
                    for candidate in key_candidates(blob):
                        if candidate in tested:
                            continue
                        tested.add(candidate)
                        if len(tested) > max_candidates:
                            raise SocialError("wcdb_candidate_bound")
                        key, salt = candidate
                        if verify_page_key(key, page, salt=salt):
                            return key, salt or page[:16], {"bytes_scanned": consumed, "regions_checked": regions,
                                "config_objects_checked": objects, "candidate_count": len(tested), "failed_reads": failed_reads}
            carry = data[-15:]
            cursor += size
    raise SocialError("selected_database_key_not_found")
