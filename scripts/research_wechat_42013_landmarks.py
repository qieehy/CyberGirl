"""Static-only 4.2.0.13 migration hypotheses. No process IO or profile activation."""
import hashlib
import json
import re
import struct
from pathlib import Path

from scripts.inspect_wechat_accessibility import inspect

SHA = "afe3d02c58bada3c35009c0cd58ee1f57b2a00fe53237d0dce649107fddb9a20"
BASE = 0x180000000


class Image:
    def __init__(self, data):
        if hashlib.sha256(data).hexdigest() != SHA:
            raise ValueError("unrecognized research image")
        self.data, self.layout = data, inspect(data)
        self.sections = self.layout["sections"]
    def rva(self, offset):
        sec = next(s for s in self.sections if s["file_offset"] <= offset < s["file_offset"] + s["raw_size"])
        return sec["rva"] + offset - sec["file_offset"]
    def offset(self, rva):
        sec = next(s for s in self.sections if s["rva"] <= rva < s["rva"] + s["raw_size"])
        return sec["file_offset"] + rva - sec["rva"]
    def at(self, rva, size):
        offset = self.offset(rva)
        if self.rva(offset + size - 1) != rva + size - 1:
            raise ValueError("read crossed a section")
        return self.data[offset:offset+size]
    def find(self, pattern, *, executable=None):
        rows = []
        for sec in self.sections:
            if executable is not None and bool(sec["characteristics"] & 0x20000000) != executable:
                continue
            start, end = sec["file_offset"], sec["file_offset"] + sec["raw_size"]
            cursor = start
            while (cursor := self.data.find(pattern, cursor, end)) >= 0:
                rows.append(self.rva(cursor))
                cursor += 1
        return rows
    def type_name(self, vtable):
        try:
            locator = struct.unpack("<Q", self.at(vtable - 8, 8))[0] - BASE
            signature, offset, cd_offset, desc, hierarchy, self_rva = struct.unpack("<6I", self.at(locator, 24))
            if signature != 1 or offset != 0 or locator != self_rva:
                return None
            return self.at(desc + 16, 128).split(b"\0")[0].decode("ascii")
        except (ValueError, StopIteration, UnicodeError):
            return None
    def qt_meta(self, name):
        results = []
        for header in self.find(struct.pack("<ii", -1, len(name)), executable=False):
            displacement = struct.unpack("<q", self.at(header + 16, 8))[0]
            try:
                if self.at(header + displacement, len(name) + 1) != name.encode() + b"\0":
                    continue
            except StopIteration:
                continue
            for pointer in self.find(struct.pack("<Q", BASE + header), executable=False):
                meta = pointer - 8
                values = struct.unpack("<6Q", self.at(meta, 48))
                metadata = values[2] - BASE
                try:
                    md = struct.unpack("<14I", self.at(metadata, 56))
                except StopIteration:
                    continue
                if md[0] != 8 or md[1] != 0 or md[4] > 256:
                    continue
                getters = []
                prefix = bytes.fromhex("488b490848837928000f85")
                for sec in self.sections:
                    if not sec["characteristics"] & 0x20000000:
                        continue
                    for match in re.finditer(re.escape(prefix) + b"....\x48\x8d\x05....\xc3", self.data[sec["file_offset"]:sec["file_offset"]+sec["raw_size"]], re.S):
                        getter = sec["rva"] + match.start()
                        code = match.group()
                        target = getter + 22 + struct.unpack_from("<i", code, 18)[0]
                        if target == meta:
                            getters.append(getter)
                tables = []
                for getter in getters:
                    for table in self.find(struct.pack("<Q", BASE + getter), executable=False):
                        typ = self.type_name(table)
                        entries = struct.unpack("<6Q", self.at(table, 48))
                        if all(BASE < address < BASE + self.layout["image_size"] for address in entries):
                            tables.append(dict(vtable=table, getter=getter, rtti=typ))
                results.append(dict(meta=meta, strings=header, metadata=metadata, methods=md[4], dispatch=values[3]-BASE, tables=tables))
        return results


def catalog(data):
    image = Image(data)
    classes = {name: image.qt_meta(name) for name in ["mmui::ChatSingleWindow", "mmui::ChatMessagePage", "mmui::XOutlineButton", "mmui::ChatInputView", "mmui::ChatInputField"]}
    caches = []
    for sec in image.sections:
        if not sec["characteristics"] & 0x20000000:
            continue
        raw = data[sec["file_offset"]:sec["file_offset"]+sec["raw_size"]]
        for match in re.finditer(bytes.fromhex("564883ec20488b05") + b"...." + bytes.fromhex("4885c07406"), raw, re.S):
            rva = sec["rva"] + match.start()
            code = image.at(rva, 0x53)
            global_rva = rva + 12 + struct.unpack_from("<i", code, 8)[0]
            if code[0x2e:0x31] != bytes.fromhex("488d05"):
                continue
            table = rva + 0x35 + struct.unpack_from("<i", code, 0x31)[0]
            typ = image.type_name(table)
            if code[23:28] == bytes.fromhex("b928000000"):
                caches.append(dict(instance=rva, global_rva=global_rva, vtable=table, prefix=code[:17].hex(), rtti=typ))
    return dict(status="static_hypotheses_only", sha256=SHA, classes=classes, cache_candidates=caches,
                process_memory_accessed=False, profile_enabled=False)


if __name__ == "__main__":
    import sys
    result = catalog(Path(r"C:\Program Files\Tencent\Weixin\4.2.0.13\Weixin.dll").read_bytes())
    with Path(sys.argv[1]).open("x", encoding="utf-8") as output:
        json.dump(result, output, indent=2)
    print(json.dumps(result))
