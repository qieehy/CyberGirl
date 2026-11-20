"""Bounded reads of the pinned on-disk PE; no DLL loading or process access."""

import hashlib
import struct

if __package__:
    from .inspect_wechat_accessibility import inspect
    from .verify_wechat_accessibility import DLL_SHA
else:
    from inspect_wechat_accessibility import inspect
    from verify_wechat_accessibility import DLL_SHA


class PinnedImage:
    base = 0x180000000

    def __init__(self, image):
        if hashlib.sha256(image).hexdigest() != DLL_SHA:
            raise ValueError("pinned binary changed")
        self.image, self.sections = image, inspect(image)["sections"]

    def at(self, rva, size, *, executable=False):
        if type(rva) is not int or type(size) is not int or not 0 < size <= 4096:
            raise ValueError("static read bound")
        section = next((s for s in self.sections if s["rva"] <= rva < s["rva"] + s["raw_size"]), None)
        if (section is None or rva + size > section["rva"] + section["raw_size"]
                or (executable and not section["characteristics"] & 0x20000000)):
            raise ValueError("static section bound")
        offset = section["file_offset"] + rva - section["rva"]
        return self.image[offset:offset + size]

    def expect(self, rva, signature):
        expected = bytes.fromhex(signature)
        if self.at(rva, len(expected)) != expected:
            raise ValueError("pinned route instruction changed")

    def relative(self, rva, target, opcode=0xE8):
        code = self.at(rva, 5, executable=True)
        if code[0] != opcode or rva + 5 + struct.unpack_from("<i", code, 1)[0] != target:
            raise ValueError("pinned relative route changed")

    def rip(self, rva, target, prefix):
        code = self.at(rva, 7, executable=True)
        if code[:3] != bytes.fromhex(prefix) or rva + 7 + struct.unpack_from("<i", code, 3)[0] != target:
            raise ValueError("pinned RIP route changed")

    def methods(self, meta, class_name, count):
        _, strings, metadata, _, _, _ = struct.unpack("<6Q", self.at(meta, 48))
        strings, metadata = strings - self.base, metadata - self.base

        def string(index):
            if not 0 <= index < 256:
                raise ValueError("metadata string index bound")
            position = strings + index * 24
            raw = self.at(position, 24)
            length, offset = struct.unpack_from("<i", raw, 4)[0], struct.unpack_from("<q", raw, 16)[0]
            if not 1 <= length <= 128:
                raise ValueError("metadata string length bound")
            return self.at(position + offset, length).decode("ascii")

        header = struct.unpack("<14I", self.at(metadata, 56))
        if header[0] != 8 or string(header[1]) != class_name or header[4] != count:
            raise ValueError("class metadata changed")
        methods = []
        for index in range(count):
            name, argc, params, _, flags = struct.unpack("<5I", self.at(metadata + (header[5] + index * 5) * 4, 20))
            if argc > 8:
                raise ValueError("metadata argument bound")
            types = struct.unpack("<" + "I" * (argc + 1), self.at(metadata + params * 4, (argc + 1) * 4))
            methods.append(dict(index=index, name=string(name), argc=argc, flags=flags, types=list(types),
                                type_names=[string(t & 0x7FFFFFFF) if t & 0x80000000 else f"metatype:{t}" for t in types]))
        return methods
