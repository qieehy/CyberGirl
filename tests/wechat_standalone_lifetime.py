"""Queue/lifetime test using ONLY hidden windows created by this process."""

import ctypes as C
import json
import os
import sys
from ctypes import wintypes

if os.name != "nt":
    raise SystemExit(2)
user, kernel = C.WinDLL("user32", use_last_error=True), C.WinDLL("kernel32", use_last_error=True)
callback_type = C.WINFUNCTYPE(C.c_ssize_t, C.c_void_p, C.c_uint, C.c_size_t, C.c_ssize_t)


class WindowClass(C.Structure):
    _fields_ = [("style", C.c_uint), ("proc", callback_type), ("class_extra", C.c_int), ("window_extra", C.c_int),
                ("instance", C.c_void_p), ("icon", C.c_void_p), ("cursor", C.c_void_p), ("background", C.c_void_p),
                ("menu", C.c_wchar_p), ("name", C.c_wchar_p)]


for name, args, result in (
    ("RegisterClassW", [C.POINTER(WindowClass)], C.c_uint16),
    ("CreateWindowExW", [C.c_uint32, C.c_wchar_p, C.c_wchar_p, C.c_uint32] + [C.c_int] * 4 + [C.c_void_p] * 4, C.c_void_p),
    ("DefWindowProcW", [C.c_void_p, C.c_uint, C.c_size_t, C.c_ssize_t], C.c_ssize_t),
    ("PostMessageW", [C.c_void_p, C.c_uint, C.c_size_t, C.c_ssize_t], C.c_int),
    ("PeekMessageW", [C.POINTER(wintypes.MSG), C.c_void_p, C.c_uint, C.c_uint, C.c_uint], C.c_int),
    ("DispatchMessageW", [C.POINTER(wintypes.MSG)], C.c_ssize_t),
    ("DestroyWindow", [C.c_void_p], C.c_int),
):
    getattr(user, name).argtypes, getattr(user, name).restype = args, result
kernel.GetModuleHandleW.argtypes, kernel.GetModuleHandleW.restype = [C.c_wchar_p], C.c_void_p
roles, received = {}, []


@callback_type
def procedure(hwnd, message, wparam, lparam):
    if message == 0x102:
        received.append([roles.get(int(hwnd), "unknown"), wparam])
        return 0
    return user.DefWindowProcW(hwnd, message, wparam, lparam)


name = "StandaloneLifetimeFixture_" + str(os.getpid())
instance = kernel.GetModuleHandleW(None)
wc = WindowClass(0, procedure, 0, 0, instance, None, None, None, None, name)
if not user.RegisterClassW(C.byref(wc)):
    raise C.WinError(C.get_last_error())


def create(role):
    hwnd = user.CreateWindowExW(0, name, "", 0, 0, 0, 64, 64, None, None, instance, None)
    if not hwnd:
        raise C.WinError(C.get_last_error())
    roles[int(hwnd)] = role
    return hwnd


main, fixed = create("main"), create("fixed-filehelper")
replacement = None
try:
    assert user.PostMessageW(main, 0x102, ord("M"), 1)
    assert user.PostMessageW(fixed, 0x102, ord("F"), 1)
    # Delay the pump. Main routing state changes without changing the fixed HWND.
    roles[int(main)] = "main-other-conversation"
    if sys.argv[1] == "destroy":
        assert user.DestroyWindow(fixed)
        roles.pop(int(fixed))
        replacement = create("replacement")
        assert user.PostMessageW(replacement, 0x102, ord("R"), 1)
    message = wintypes.MSG()
    for _ in range(64):
        if not user.PeekMessageW(C.byref(message), None, 0, 0, 1):
            break
        user.DispatchMessageW(C.byref(message))
    else:
        raise RuntimeError("fixture queue bound")
    print(json.dumps(dict(received=received, replacement_same_handle=replacement == fixed,
                          external_hwnds_used=False, windows_visible=False)))
finally:
    for hwnd in (replacement, fixed if sys.argv[1] != "destroy" else None, main):
        if hwnd:
            user.DestroyWindow(hwnd)
