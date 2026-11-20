"""Locked Weixin 4.2.0.9 preflight; live mutation requires --activate-once.

No model/chat access. A live experiment restores the single byte even after
failure; forced process termination/power loss cannot guarantee restoration.
"""

from __future__ import annotations

import argparse
import ctypes as C
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

if __package__:
    from .inspect_wechat_accessibility import inspect
else:
    from inspect_wechat_accessibility import inspect

DLL = Path(r"C:\Program Files\Tencent\Weixin\4.2.0.9\Weixin.dll")
EXE = DLL.parent.parent / "Weixin.exe"
DLL_SHA = "a81c4770350a4edf3e774413da832a785d7aac50025471b30ea26d1f846b9fb4"
EXE_SHA = "5329ffc9afdeeb01db6dd5a68321277a8cf8d3f966873c070be3d04d7dfb1c0e"
RVA = 189758472
INSTRUCTION_RVA = 8872546
IMAGE_SIZE = 206352384
_PINNED_SCAN = None


def transaction(memory, observe, *, activate=False, record=lambda event: None):
    """Independent state machine; memory supplies identity/page checked byte IO."""
    facts = memory.preflight()
    original = memory.read()
    if original not in (0, 1):
        raise ValueError("unexpected byte value; no write")
    result = {"preflight": facts, "original": original, "write_attempted": False,
              "restore_verified": None, "status": "preflight_ready"}
    record({"stage": "preflight", **result})
    if not activate:
        return result
    if original == 1:
        result["status"] = "already_enabled_no_write"
        return result
    # Journal durability is a prerequisite for any write attempt.
    record({"stage": "before_write", "original": original, "candidate_rva": RVA})
    attempted = False
    try:
        memory.validate()
        if memory.read() != original:
            raise ValueError("byte changed since preflight")
        attempted = True  # Set BEFORE the call: failure may still have changed memory.
        result["write_attempted"] = True
        memory.write(1)
        if memory.read() != 1:
            raise RuntimeError("activation readback mismatch")
        result["observation"] = observe(facts)
        result["status"] = "experiment_restored"
    except BaseException as exc:
        result["error_type"] = type(exc).__name__
        result["status"] = "experiment_failed_restored" if attempted else "rejected_no_write"
    finally:
        if attempted:
            try:
                # Same open process handle; never reopen a PID after exit/reuse.
                memory.validate()
                current = memory.read()
                if current not in (original, 1):
                    raise RuntimeError("concurrent unexpected value; refusing overwrite")
                if current != original:
                    memory.write(original)
                if memory.read() != original:
                    raise RuntimeError("restoration readback mismatch")
                result["restore_verified"] = True
            except BaseException as exc:
                result["restore_verified"] = False
                result["restore_error_type"] = type(exc).__name__
                result["status"] = "restore_failed"
    # Logging cannot prevent restoration: it happens only after the finally block.
    try:
        record({"stage": "completed", **result})
    except BaseException as exc:
        result["journal_error_type"] = type(exc).__name__
        if result["status"] != "restore_failed":
            result["status"] = "journal_failed_after_restore" if attempted else "journal_failed_no_write"
    return result


class Page(C.Structure):
    _fields_ = [("BaseAddress", C.c_uint64), ("AllocationBase", C.c_uint64),
                ("AllocationProtect", C.c_uint32), ("PartitionId", C.c_uint16),
                ("RegionSize", C.c_uint64), ("State", C.c_uint32),
                ("Protect", C.c_uint32), ("Type", C.c_uint32)]


class ModuleInfo(C.Structure):
    _fields_ = [("base", C.c_void_p), ("size", C.c_uint32), ("entry", C.c_void_p)]


class NativeMemory:
    def __init__(self, pid, *, activate=False, hwnd=None, created_filetime=None):
        if os.name != "nt" or C.sizeof(C.c_void_p) != 8:
            raise RuntimeError("requires 64-bit Windows Python")
        self.pid, self.activate, self.handle = pid, activate, None
        self.expected_hwnd, self.expected_creation = hwnd, created_filetime
        self.k = C.WinDLL("kernel32", use_last_error=True)
        self.u = C.WinDLL("user32", use_last_error=True)
        self.callback_type = C.WINFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)
        signatures = {
            "OpenProcess": ([C.c_uint32, C.c_int, C.c_uint32], C.c_void_p),
            "CloseHandle": ([C.c_void_p], C.c_int),
            "QueryFullProcessImageNameW": ([C.c_void_p, C.c_uint32, C.c_wchar_p, C.POINTER(C.c_uint32)], C.c_int),
            "K32EnumProcessModulesEx": ([C.c_void_p, C.POINTER(C.c_void_p), C.c_uint32, C.POINTER(C.c_uint32), C.c_uint32], C.c_int),
            "K32GetModuleFileNameExW": ([C.c_void_p, C.c_void_p, C.c_wchar_p, C.c_uint32], C.c_uint32),
            "K32GetModuleInformation": ([C.c_void_p, C.c_void_p, C.POINTER(ModuleInfo), C.c_uint32], C.c_int),
            "ReadProcessMemory": ([C.c_void_p, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)], C.c_int),
            "WriteProcessMemory": ([C.c_void_p, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)], C.c_int),
            "VirtualQueryEx": ([C.c_void_p, C.c_void_p, C.POINTER(Page), C.c_size_t], C.c_size_t),
            "GetExitCodeProcess": ([C.c_void_p, C.POINTER(C.c_uint32)], C.c_int),
            "GetProcessTimes": ([C.c_void_p] + [C.POINTER(C.c_uint64)] * 4, C.c_int),
        }
        for name, (args, returns) in signatures.items():
            fn = getattr(self.k, name)
            fn.argtypes, fn.restype = args, returns
        self.u.EnumWindows.argtypes = [self.callback_type, C.c_void_p]
        self.u.EnumWindows.restype = C.c_int
        self.u.GetWindowThreadProcessId.argtypes = [C.c_void_p, C.POINTER(C.c_uint32)]
        self.u.GetWindowThreadProcessId.restype = C.c_uint32
        self.u.IsWindowVisible.argtypes = [C.c_void_p]
        self.u.IsWindowVisible.restype = C.c_int

    @staticmethod
    def check(ok):
        if not ok:
            raise C.WinError(C.get_last_error())

    def close(self):
        if self.handle:
            self.k.CloseHandle(self.handle)
            self.handle = None

    def read_bytes(self, address, size):
        buf = C.create_string_buffer(size)
        count = C.c_size_t()
        self.check(self.k.ReadProcessMemory(self.handle, address, buf, size, C.byref(count)))
        if count.value != size:
            raise RuntimeError("partial memory read")
        return buf.raw

    def preflight(self):
        global _PINNED_SCAN
        data = DLL.read_bytes()
        if hashlib.sha256(data).hexdigest() != DLL_SHA or hashlib.sha256(EXE.read_bytes()).hexdigest() != EXE_SHA:
            raise ValueError("pinned binary fingerprint mismatch")
        # Always hash current disk bytes. Only the costly analysis of identical
        # authenticated bytes is cached within this process, never the handle.
        if _PINNED_SCAN is None:
            _PINNED_SCAN = inspect(data)
        scan = _PINNED_SCAN
        self.sections = scan["sections"]  # Pinned PE metadata, reuse without rescanning the code section.
        if scan["distinct_near_targets"] != [RVA] or scan["image_size"] != IMAGE_SIZE:
            raise ValueError("static candidate mismatch")
        sec = next(s for s in scan["sections"] if s["rva"] <= INSTRUCTION_RVA < s["rva"] + s["raw_size"])
        offset = sec["file_offset"] + INSTRUCTION_RVA - sec["rva"]
        self.expected_code = data[offset : offset + 18]
        access = 0x0400 | 0x0010  # QUERY_INFORMATION | VM_READ
        if self.activate:
            access |= 0x0008 | 0x0020  # Explicit experiment only: VM_OPERATION | VM_WRITE
        self.handle = self.k.OpenProcess(access, False, self.pid)
        self.check(self.handle)
        times = [C.c_uint64() for _ in range(4)]
        self.check(self.k.GetProcessTimes(self.handle, *(C.byref(t) for t in times)))
        self.created_filetime = times[0].value
        if self.expected_creation is not None and self.created_filetime != self.expected_creation:
            raise ValueError("process generation changed")
        path = C.create_unicode_buffer(32768)
        length = C.c_uint32(len(path))
        self.check(self.k.QueryFullProcessImageNameW(self.handle, 0, path, C.byref(length)))
        if os.path.normcase(path.value) != os.path.normcase(str(EXE)):
            raise ValueError("PID is not the pinned executable")
        modules = (C.c_void_p * 2048)()
        needed = C.c_uint32()
        self.check(self.k.K32EnumProcessModulesEx(self.handle, modules, C.sizeof(modules), C.byref(needed), 3))
        if needed.value > C.sizeof(modules):
            raise RuntimeError("module inventory exceeds bound")
        found = []
        for module in list(modules)[:needed.value // C.sizeof(C.c_void_p)]:
            n = self.k.K32GetModuleFileNameExW(self.handle, module, path, len(path))
            self.check(n)
            if os.path.normcase(path.value) == os.path.normcase(str(DLL)):
                found.append(module)
        if len(found) != 1:
            raise ValueError("expected exactly one pinned DLL module")
        info = ModuleInfo()
        self.check(self.k.K32GetModuleInformation(self.handle, found[0], C.byref(info), C.sizeof(info)))
        if info.size != IMAGE_SIZE:
            raise ValueError("loaded module size mismatch")
        self.base, self.address = info.base, info.base + RVA
        self.windows = []

        @self.callback_type
        def collect(hwnd, _):
            owner = C.c_uint32()
            self.u.GetWindowThreadProcessId(hwnd, C.byref(owner))
            if owner.value == self.pid and self.u.IsWindowVisible(hwnd):
                self.windows.append(hwnd)
            return True

        self.check(self.u.EnumWindows(collect, None))
        if self.expected_hwnd is not None:
            if self.expected_hwnd not in self.windows:
                raise ValueError("selected window is not visible in target process")
            self.windows = [self.expected_hwnd]
        if len(self.windows) != 1:
            raise ValueError(f"expected one visible window for selected PID; found {len(self.windows)}")
        self.validate()
        return {"pid": self.pid, "hwnd": self.windows[0], "dll_sha256": DLL_SHA, "exe_sha256": EXE_SHA,
                "created_filetime": str(self.created_filetime),
                "candidate_rva": RVA, "image_size": IMAGE_SIZE,
                "requested_process_access": hex(access), "page_protection": self.page.Protect,
                "page_type": self.page.Type, "instruction_matches_disk": True}

    def validate(self):
        exit_code = C.c_uint32()
        self.check(self.k.GetExitCodeProcess(self.handle, C.byref(exit_code)))
        if exit_code.value != 259:
            raise RuntimeError("target process exited")
        if getattr(self, "windows", None):
            owner = C.c_uint32()
            self.u.GetWindowThreadProcessId(self.windows[0], C.byref(owner))
            if owner.value != self.pid:
                raise ValueError("window ownership changed")
        page = Page()
        self.check(self.k.VirtualQueryEx(self.handle, self.address, C.byref(page), C.sizeof(page)) == C.sizeof(page))
        if (page.State != 0x1000 or page.Type != 0x1000000 or page.Protect not in (0x04, 0x08)
                or page.AllocationBase != self.base
                or not page.BaseAddress <= self.address < page.BaseAddress + page.RegionSize):
            raise ValueError("target is not a committed non-executable writable image page")
        if self.read_bytes(self.base + INSTRUCTION_RVA, 18) != self.expected_code:
            raise ValueError("loaded instruction mismatch")
        self.page = page

    def read(self):
        return self.read_bytes(self.address, 1)[0]

    def write(self, value):
        if not self.activate or value not in (0, 1):
            raise ValueError("write not enabled")
        self.validate()
        byte, count = C.c_ubyte(value), C.c_size_t()
        self.check(self.k.WriteProcessMemory(self.handle, self.address, C.byref(byte), 1, C.byref(count)))
        if count.value != 1:
            raise RuntimeError("partial memory write")


def summarize_controls(window):
    """A login/search window must never satisfy the chat capability check."""
    view = window["raw"]
    nodes = view["nodes"]
    root = next((n for n in nodes if n["index"] == 0 and n["parent"] is None), None)
    root_class = root["class_name"] if root else ""
    kind = {"mmui::LoginWindow": "login", "mmui::MainWindow": "main",
            "Qt51514QWindowIcon": "qt_shell"}.get(root_class, "unrecognized")
    has_mmui = any(n["class_name"].startswith("mmui::") for n in nodes)
    types = {n["type"] for n in nodes}
    roles = {n.get("role") for n in nodes}
    role_candidate = {"session_list", "chat_input"} <= roles
    legacy_candidate = "role" not in (root or {}) and {"ControlType.Edit", "ControlType.List"} <= types
    eligible = kind == "main" or (kind == "qt_shell" and role_candidate)
    candidate = eligible and (role_candidate or legacy_candidate) and not view["errors"] and not view["bounded"]
    status = ("wrong_window" if kind in {"login", "unrecognized"} else
              "provider_error" if view["errors"] else "incomplete" if view["bounded"] else
              "found" if candidate else "not_found_complete")
    return {"node_count": len(nodes), "root_class": root_class, "window_kind": kind,
            "mmui_present": has_mmui, "manual_login_or_main_window_needed": kind == "login",
            "edit_present": "ControlType.Edit" in types, "list_present": "ControlType.List" in types,
            "bounded": view["bounded"], "errors": len(view["errors"]),
            "roles_found": sorted(r for r in roles if r and r != "other"), "status": status,
            "chat_control_candidate": candidate}


def observe_controls(facts, output, *, max_depth=32, max_nodes=800, control_view=False):
    script = Path(__file__).with_name("probe_wechat_uia.ps1")
    engine = Path(os.environ["WINDIR"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    command = [str(engine), "-NoProfile", "-NonInteractive", "-File", str(script),
               "-OutputPath", str(output), "-TargetPid", str(facts["pid"]),
               "-TargetHwnd", str(facts["hwnd"]), "-CreatedFiletime", facts["created_filetime"],
               "-MaxDepth", str(max_depth), "-MaxNodes", str(max_nodes)]
    if control_view:
        command.append("-ControlView")
    with subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          creationflags=subprocess.CREATE_NO_WINDOW) as run:
        try:
            run.wait(timeout=40)
        except BaseException:
            # Terminate our collector tree, never the Weixin process, before restoring the gate.
            subprocess.run(["taskkill.exe", "/PID", str(run.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                           creationflags=subprocess.CREATE_NO_WINDOW)
            run.wait()
            raise
    if run.returncode:
        raise RuntimeError("UIA collector failed")
    data = json.loads(output.read_text(encoding="utf-8-sig"))
    if data.get("status") != "observed":
        raise RuntimeError("UIA collector did not complete")
    matches = [w for w in data["windows"] if w["pid"] == facts["pid"] and w["hwnd"] == facts["hwnd"]]
    if len(matches) != 1 or matches[0].get("created_filetime") != facts["created_filetime"]:
        raise ValueError("UIA window binding changed")
    return summarize_controls(matches[0])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--hwnd", type=int)
    parser.add_argument("--created-filetime", type=int)
    parser.add_argument("--max-depth", type=int, choices=range(1, 33), default=32)
    parser.add_argument("--max-nodes", type=int, choices=range(1, 801), default=800)
    parser.add_argument("--control-view", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--activate-once", action="store_true",
                      help="Explicit temporary write experiment; restores the byte before returning")
    mode.add_argument("--observe-only", action="store_true",
                      help="After read-only preflight, collect UIA metadata without Name/Value")
    args = parser.parse_args()
    if (args.activate_once or args.observe_only) and (not args.hwnd or not args.created_filetime):
        parser.error("observation/activation requires --hwnd and --created-filetime")
    journal_path = args.output.with_suffix(".journal.jsonl")
    uia_path = args.output.with_suffix(".uia.json")
    for path in (args.output, journal_path, uia_path, Path(str(uia_path) + ".worker")):
        if path.exists():
            parser.error("output/evidence already exists; use a new output name")
    with args.output.open("x", encoding="utf-8") as output, journal_path.open("x", encoding="utf-8") as journal:
        def record(event):
            journal.write(json.dumps({"time": datetime.now(UTC).isoformat(), **event}) + "\n")
            journal.flush()
            os.fsync(journal.fileno())

        memory = None
        selected_mode = "activate_once" if args.activate_once else "observe_only" if args.observe_only else "read_only"
        result = {"mode": selected_mode, "status": "started",
                  "model_requests": 0, "external_messages": 0, "system_settings_changed": False}
        try:
            record({"stage": "started", **result})
            memory = NativeMemory(args.pid, activate=args.activate_once, hwnd=args.hwnd,
                                  created_filetime=args.created_filetime)
            def observe(facts):
                return observe_controls(facts, uia_path, max_depth=args.max_depth,
                                        max_nodes=args.max_nodes, control_view=args.control_view)
            result.update(transaction(memory, observe,
                                      activate=args.activate_once, record=record))
            if args.observe_only:
                memory.validate()
                result["observation"] = observe(result["preflight"])
                result["status"] = "observed_no_write"
        except BaseException as exc:
            result.update(status="failed", error_type=type(exc).__name__)
            if isinstance(exc, OSError):
                result["winerror"] = exc.winerror
            elif isinstance(exc, (ValueError, RuntimeError)):
                # These are our own fixed diagnostics, never provider text or UI values.
                result["error_detail"] = str(exc)
        finally:
            if memory:
                memory.close()
            result["script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
            result["recorded_at"] = datetime.now(UTC).isoformat()
            json.dump(result, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] in {"preflight_ready", "observed_no_write", "already_enabled_no_write", "experiment_restored"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
