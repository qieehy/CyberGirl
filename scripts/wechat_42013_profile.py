"""Explicit pinned 4.2.0.13 profile, installed only in a fresh owned process.

No UI/process access or input. Authenticate disk bytes and migrated immutable
Qt landmarks before changing this process's version constants. The 4.2.0.9
entry points and default constants remain unchanged in other processes.
"""
import hashlib
from pathlib import Path

VERSION = "4.2.0.13"
DLL_SHA = "afe3d02c58bada3c35009c0cd58ee1f57b2a00fe53237d0dce649107fddb9a20"
EXE_SHA = "eaaa1c6c6123ce9887e5c67b60d905d22f09fac091058f27d2d819e9ff26fc69"
IMAGE_SIZE = 206430208
CLASS_ENTRY = 40631776
_ACTIVATED = False


def activate():
    global _ACTIVATED
    if _ACTIVATED:
        return
    import sys
    forbidden = {"scripts.probe_wechat_standalone", "scripts.probe_wechat_background_service", "scripts.wechat_standalone_worker",
                 "wechat_standalone_worker", "wechat_reader_source", "scripts.wechat_reader_source"}
    if forbidden.intersection(sys.modules):
        raise ValueError("profile must be selected before loading native entry points")
    from scripts import verify_wechat_accessibility as verifier
    from scripts.research_wechat_42013_landmarks import Image
    from wechat_agent.bridges.wechat import (
        directed_native,
        qt_connections,
        qt_memory,
        standalone_identity,
    )
    dll = Path(r"C:\Program Files\Tencent\Weixin\4.2.0.13\Weixin.dll")
    exe = dll.parent.parent / "Weixin.exe"
    data = dll.read_bytes()
    if hashlib.sha256(data).hexdigest() != DLL_SHA or hashlib.sha256(exe.read_bytes()).hexdigest() != EXE_SHA:
        raise ValueError("4.2.0.13 disk fingerprints changed")
    image = Image(data)
    if image.layout["image_size"] != IMAGE_SIZE or image.layout["distinct_near_targets"] != [189836232]:
        raise ValueError("4.2.0.13 accessibility landmarks changed")
    if len(image.layout["near_core_candidates"]) != 1 or image.layout["near_core_candidates"][0]["instruction_rva"] != 8890674:
        raise ValueError("4.2.0.13 accessibility instruction changed")
    migrated = tuple((rva + (10256 if index < 11 else 62048), code)
                     for index, (rva, code) in enumerate(qt_connections.LAYOUT_PROOFS))
    for rva, code in migrated:
        if image.at(rva, len(bytes.fromhex(code))) != bytes.fromhex(code):
            raise ValueError("4.2.0.13 Qt layout proof changed")
    prefix = bytes.fromhex("564883ec20488b0534ac160a4885c07406")
    if image.at(29111952, len(prefix)) != prefix:
        raise ValueError("4.2.0.13 accessible-cache code changed")
    access = qt_memory.MESSAGE_KEY_ACCESS
    if image.at(29850528 + 14, len(access)) != access:
        raise ValueError("4.2.0.13 session key getter changed")
    if hashlib.sha256(image.at(CLASS_ENTRY,128)).hexdigest() != "9e43255df672041854e111813c5b87cf178583f0786ef53fbf55afe329c9d5d7":
        raise ValueError("4.2.0.13 native window class entry changed")
    # Apply only after the complete static proof above has succeeded.
    verifier.DLL, verifier.DLL_SHA, verifier.EXE_SHA = dll, DLL_SHA, EXE_SHA
    verifier.RVA, verifier.INSTRUCTION_RVA, verifier.IMAGE_SIZE = 189836232, 8890674, IMAGE_SIZE
    verifier._PINNED_SCAN = image.layout
    qt_memory.CACHE_INSTANCE_RVA, qt_memory.CACHE_GLOBAL_RVA, qt_memory.CACHE_VTABLE_RVA = 29111952, 198370000, 155375336
    qt_memory.INSTANCE_PREFIX, qt_memory.IMAGE_SIZE = prefix, IMAGE_SIZE
    qt_memory.MESSAGE_PAGE_META_RVA, qt_memory.MESSAGE_PAGE_VTABLE_RVA = 152412072, 155625096
    qt_memory.MESSAGE_KEY_METHOD_RVA = 29850528
    standalone_identity.SINGLE_META_RVA, standalone_identity.SINGLE_VTABLE_RVA = 152407864, 160120888
    qt_connections.BUTTON_META, qt_connections.BUTTON_VTABLE = 152543608, 155930920
    qt_connections.VIEW_META, qt_connections.VIEW_VTABLE = 152409528, 155597704
    qt_connections.CLICK_SLOT_IMPL, qt_connections.SEND_METHOD = 29721568, 29647120
    qt_connections.LAYOUT_PROOFS = migrated
    qt_connections.SIGNAL_LINEAGE = ((152543608, 6), (152540752, 0), (152054984, 4), (151803304, 3))
    directed_native.CLASS_ENTRY_RVA = CLASS_ENTRY
    # Imports below may have happened through the static dependencies above.
    # Refresh only their imported aliases, before any native object can exist.
    for name in ("scripts.wechat_pinned_image", "scripts.probe_wechat_account", "scripts.probe_wechat_database"):
        module = sys.modules.get(name)
        if module:
            for key, value in (("DLL", dll), ("DLL_SHA", DLL_SHA), ("EXE_SHA", EXE_SHA)):
                if hasattr(module, key):
                    setattr(module, key, value)
    # Reader legacy imports use the same authenticated module, never a second
    # unpatched verifier under its short script name.
    sys.modules["verify_wechat_accessibility"] = verifier
    _ACTIVATED = True


def compatible_builds(old, current):
    """Only the qualified .9 -> .13 transition, with fresh native readiness."""
    return (_ACTIVATED and (old.exe_sha256, old.dll_sha256) == (
        "5329ffc9afdeeb01db6dd5a68321277a8cf8d3f966873c070be3d04d7dfb1c0e",
        "a81c4770350a4edf3e774413da832a785d7aac50025471b30ea26d1f846b9fb4")
        and (current.exe_sha256, current.dll_sha256) == (EXE_SHA, DLL_SHA))
